import os
import json
import shutil
import tempfile
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright, expect

BASE = 'http://127.0.0.1:8000'
TOKEN = 'docker-test-token-not-for-real-data'

@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as p, tempfile.TemporaryDirectory() as profile:
        # Headless tests open popup.html directly, which does not grant activeTab.
        # Grant only the synthetic bank host in a temporary test copy; production
        # manifest retains activeTab and does not request persistent bank access.
        extension = str(Path(profile) / 'extension')
        shutil.copytree('/app/extension', extension)
        manifest_path = Path(extension) / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['host_permissions'].append('https://ipay.vietinbank.vn/*')
        manifest_path.write_text(json.dumps(manifest))
        context = p.chromium.launch_persistent_context(profile, channel='chromium', headless=True,
            args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}', '--no-sandbox'],
            viewport={'width': 1440, 'height': 1000})
        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
        identifier = worker.url.split('/')[2]
        yield context, identifier
        context.close()


def open_app(browser):
    context, identifier = browser
    page = context.new_page()
    page.goto(f'chrome-extension://{identifier}/app.html')
    return page


def connect(page, url=BASE, token=TOKEN):
    page.locator('nav a[href="#settings"]').click()
    page.locator('#url').fill(url)
    page.locator('#token').fill(token)
    page.locator('#save').click()


def test_installed_extension_end_to_end(browser):
    context, identifier = browser
    page = open_app(browser)
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    expect(page.locator('#view-settings')).to_be_visible()
    connect(page)
    expect(page.locator('#status')).to_have_text('Данные обновлены.')
    expect(page.locator('#connection')).to_have_text('Сервер подключён')
    page.locator('nav a[href="#banks"]').click()
    expect(page.locator('#bank-list')).to_contain_text('Сначала импортируйте')
    page.locator('#sync').click()
    expect(page.locator('#status')).to_have_text('Данные обновлены.')
    page.locator('nav a[href="#zenmoney"]').click()
    expect(page.locator('#zen-accounts')).to_contain_text('Демо · VND')
    expect(page.locator('#categories')).to_contain_text('Еда')
    bank = context.new_page()
    bank.goto(BASE + '/demo-bank')
    popup = context.new_page()
    popup.goto(f'chrome-extension://{identifier}/popup.html')
    bank.bring_to_front()
    popup.locator('#diagnostics > summary').click();popup.locator('#legacy-tools > summary').click();popup.locator('#extract').click()
    expect(popup.locator('#status')).to_contain_text('3 операций')
    popup.locator('#import').click()
    expect(popup.locator('#status')).to_contain_text('Добавлено: 3')
    popup.locator('#import').click()
    expect(popup.locator('#status')).to_contain_text('Уже были: 3')
    page.bring_to_front()
    page.locator('#refresh').click()
    expect(page.locator('#status')).to_have_text('Данные обновлены.')
    page.locator('nav a[href="#banks"]').click()
    page.locator('#bank-list a[href="#banks/demo"]').click()
    page.locator('#bank-settings-link').click()
    page.locator('#accounts select').select_option(label='Демо · VND')
    page.get_by_role('button',name='Сопоставить',exact=True).click()
    expect(page.locator('#status')).to_have_text('Данные обновлены.')
    page.locator('nav a[href="#review"]').click()
    page.locator('#review .ledger-summary').first.click()
    page.locator('#review .ledger-detail select').first.select_option(label='Еда')
    page.get_by_role('button',name='Подтвердить создание',exact=True).first.click()
    expect(page.locator('#outbox')).to_contain_text('Подтверждена')
    page.on('dialog',lambda dialog:dialog.accept())
    page.locator('#send').click()
    expect(page.locator('#status')).to_contain_text('1 успешно')
    expect(page.locator('#outbox')).to_contain_text('Отправлена')
    page.reload()
    expect(page.locator('#connection')).to_have_text('Сервер подключён')
    page.locator('nav a[href="#overview"]').click()
    Path('/results').mkdir(exist_ok=True)
    page.screenshot(path='/results/extension-overview.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path='/results/extension-mobile.png',full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Horizontal overflow'
    assert not errors
    popup.close();bank.close();page.close()


def test_connection_errors_validation_and_recovery(browser):
    page = open_app(browser)
    expect(page.locator('#connection')).to_have_text('Сервер подключён')
    connect(page, token='incorrect-token-with-enough-characters')
    expect(page.locator('#status')).to_contain_text('Неверный API-токен')
    expect(page.locator('#sync')).to_be_disabled()
    connect(page, url='http://127.0.0.1:1')
    expect(page.locator('#status')).to_contain_text('Сервер недоступен')
    connect(page, url='http://example.com')
    expect(page.locator('#status')).to_contain_text('HTTPS')
    connect(page)
    expect(page.locator('#connection')).to_have_text('Сервер подключён')
    page.close()


def test_popup_unknown_page_and_storage_boundary(browser):
    context, identifier = browser
    unknown = context.new_page();unknown.goto(BASE + '/docs')
    popup = context.new_page();popup.goto(f'chrome-extension://{identifier}/popup.html')
    unknown.bring_to_front();popup.locator('#diagnostics > summary').click();popup.locator('#legacy-tools > summary').click();popup.locator('#extract').click()
    expect(popup.locator('#status')).to_contain_text('Банк пока не поддерживается')
    expect(popup.locator('#import')).to_be_hidden()
    worker=context.service_workers[0]
    result=worker.evaluate('''async () => {
      const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
      const [result]=await chrome.scripting.executeScript({target:{tabId:tab.id},func:async()=>{
        try { const data=await chrome.storage.local.get('token');return !!data.token; } catch { return false; }
      }});return result.result;
    }''')
    assert result is False, 'Content script can read the backend token'
    popup.close();unknown.close()


def test_history_filter_and_paging(browser):
    from urllib.parse import urlparse, parse_qs
    context, identifier = browser
    page = context.new_page()
    records = [{'id': f'h{i}', 'date': '2020-01-01', 'income': 0, 'outcome': i, 'comment': f'История {i}'} for i in range(105)]
    def history_route(route):
        query = parse_qs(urlparse(route.request.url).query)
        offset = int(query.get('offset', ['0'])[0])
        data = [] if 'start' in query else records
        route.fulfill(json={'items': data[offset:offset+100], 'total': len(data), 'offset': offset,
                            'limit': 100, 'has_more': offset+100 < len(data)})
    page.route('**/api/zenmoney/history?*', history_route)
    page.goto(f'chrome-extension://{identifier}/app.html#zenmoney/transactions')
    expect(page.locator('#history-page')).to_have_text('Найдено: 105. Показано: 1–100.')
    expect(page.locator('#zen article')).to_have_count(100)
    page.locator('#history-next').click()
    expect(page.locator('#history-page')).to_have_text('Найдено: 105. Показано: 101–105.')
    expect(page.locator('#history-next')).to_be_disabled()
    page.locator('#history-prev').click()
    expect(page.locator('#history-page')).to_contain_text('1–100')
    page.locator('#history-start').fill('2021-01-01')
    page.get_by_role('button', name='Применить период').click()
    expect(page.locator('#history-page')).to_have_text('Найдено: 0. Показано: 0–0.')
    expect(page.locator('#zen')).to_contain_text('Операций по выбранным условиям нет')
    expect(page.locator('#history-prev')).to_be_disabled()
    expect(page.locator('#history-next')).to_be_disabled()
    page.close()


def fixture_bank(browser, html=None):
    context, identifier = browser
    bank = context.new_page()
    source = html or Path('/app/tests/browser/fixtures/vietinbank.html').read_text()
    bank.route('https://ipay.vietinbank.vn/**', lambda route: route.fulfill(body=source, content_type='text/html'))
    bank.goto('https://ipay.vietinbank.vn/payment-history?accountNo=111122223333')
    return bank


def test_vietinbank_period_popup_and_import(browser):
    context, identifier = browser
    bank = fixture_bank(browser)
    popup = context.new_page()
    popup.goto(f'chrome-extension://{identifier}/popup.html')
    bank.bring_to_front()
    popup.locator('#diagnostics > summary').click();popup.locator('#legacy-tools > summary').click();popup.locator('#collect').click()
    expect(popup.locator('#status')).to_contain_text('3 операций')
    expect(popup.locator('#status')).to_contain_text('Все строки выбранного поиска собраны')
    popup.locator('#import').click()
    expect(popup.locator('#status')).to_contain_text('Добавлено: 3')
    popup.locator('#import').click()
    expect(popup.locator('#status')).to_contain_text('Уже были: 3')
    app = open_app(browser)
    app.locator('nav a[href="#banks"]').click()
    app.locator('#bank-list a[href="#banks/vietinbank"]').click()
    app.locator('#bank-settings-link').click()
    expect(app.locator('#accounts')).to_contain_text('vietinbank / 111122223333')
    expect(app.locator('#accounts')).to_contain_text('строки 1–3 из 3')
    expect(app.locator('#bank-rows')).to_contain_text('Fee without a reference')
    app.close();popup.close();bank.close()


@pytest.mark.parametrize('mutation,expected', [
    ("document.querySelector('thead td').textContent='NGÀY'", 'английская'),
    ("document.querySelector('.cus-option').textContent='999988887777 - TEST PERSON - Current account'", 'счёт'),
    ("document.querySelector('tbody tr td:nth-child(3)').textContent='-65.000 VND'", 'сумма'),
    ("document.querySelector('tbody tr td:first-child').textContent='31/02/2026 12:00:00'", 'дата'),
    ("document.querySelector('.table-pagination-total-range').textContent='1-3'", 'счётчик'),
    ("document.querySelector('tbody tr td:first-child').textContent='31/08/2026 12:00:00'", 'периода'),
])
def test_vietinbank_rejects_ambiguous_dom(browser, mutation, expected):
    bank = fixture_bank(browser)
    bank.evaluate(mutation)
    # This isolated container executes only our read-only parser over synthetic DOM.
    result = bank.evaluate(Path('/app/extension/extract.js').read_text())
    assert not result['ok']
    assert expected in result['error']
    bank.close()


def test_vietinbank_fingerprint_stability_and_partial_capture(browser):
    bank = fixture_bank(browser)
    source = Path('/app/extension/extract.js').read_text()
    first = bank.evaluate(source)
    second = bank.evaluate(source)
    assert first == second and first['ok']
    assert first['data']['capture']['complete'] is False
    assert first['data']['transactions'][0]['raw']['reference_kind'] == 'fingerprint'
    assert first['data']['transactions'][0]['amount_minor'] == -65000
    assert first['data']['transactions'][1]['amount_minor'] == 100000
    bank.close()


def test_vietinbank_collection_rejects_changed_context(browser):
    bank = fixture_bank(browser)
    bank.evaluate("document.querySelector('[aria-label=\"Page 2\"]').onclick=()=>{document.querySelector('.table-pagination-total-total').textContent='4';render(1);};globalThis.__zenImportAllPages=true")
    result = bank.evaluate(Path('/app/extension/extract.js').read_text())
    assert not result['ok'] and 'изменились' in result['error']
    bank.close()


def test_zenmoney_tabs_accounts_archive_and_transaction_details(browser):
    from urllib.parse import urlparse, parse_qs
    context, identifier = browser
    page = context.new_page()
    account_data = [
        {'id':'vnd','title':'VietinBank · основной','instrument':1,'archive':False},
        {'id':'usd','title':'Долларовый счёт','instrument':2,'archive':False},
        {'id':'old','title':'Закрытая карта','instrument':2,'archive':True},
    ]
    records = [
        {'id':'coffee','date':'2026-10-06','income':0,'outcome':65000,'incomeAccount':'vnd','outcomeAccount':'vnd',
         'incomeInstrument':1,'outcomeInstrument':1,'payee':'Кофейня','comment':'Завтрак и кофе','tag':['food'],'_amounts':{'income':'0','outcome':'65000'}},
        {'id':'transfer','date':'2026-10-06','income':500,'outcome':13000000,'incomeAccount':'usd','outcomeAccount':'vnd',
         'incomeInstrument':2,'outcomeInstrument':1,'comment':'Пополнение валютного счёта','tag':None},
        {'id':'salary','date':'2026-10-05','income':20000000,'outcome':0,'incomeAccount':'vnd','outcomeAccount':'vnd',
         'incomeInstrument':1,'outcomeInstrument':1,'payee':'Работодатель','tag':['salary']},
        {'id':'archived','date':'2026-10-04','income':0,'outcome':3.1,'incomeAccount':'old','outcomeAccount':'old',
         'incomeInstrument':2,'outcomeInstrument':2,'comment':'Подписка','tag':None},
    ]
    def state_route(route):
        data = route.fetch().json()
        data.update(zenmoney_accounts=account_data,zenmoney_instruments=[{'id':1,'shortTitle':'VND'},{'id':2,'shortTitle':'USD'}],
                    categories=[{'id':'food','title':'Кафе и рестораны'},{'id':'salary','title':'Зарплата'}],zenmoney_merchants=[],
                    zenmoney_summary={'transactions':{'count':4,'first_date':'2026-10-04','last_date':'2026-10-06'},'coverage_verified':False})
        route.fulfill(json=data)
    requests = []
    def history_route(route):
        query = parse_qs(urlparse(route.request.url).query)
        requests.append(query)
        account = query.get('account',[''])[0]
        rows = [t for t in records if not account or account in [t['incomeAccount'],t['outcomeAccount']]]
        start = query.get('start',[''])[0]
        end = query.get('end',[''])[0]
        rows = [t for t in rows if (not start or t['date']>=start) and (not end or t['date']<=end)]
        route.fulfill(json={'items':rows,'total':len(rows),'offset':0,'limit':100,'has_more':False})
    page.route('**/api/state',state_route)
    page.route('**/api/zenmoney/history?*',history_route)
    page.goto(f'chrome-extension://{identifier}/app.html#zenmoney')
    expect(page.locator('#zen-panel-accounts')).to_be_visible()
    expect(page.locator('#zen-panel-transactions')).to_be_hidden()
    expect(page.locator('#zen-panel-categories')).to_be_hidden()
    expect(page.locator('#zen-accounts button')).to_have_count(2)
    expect(page.locator('#zen-accounts')).not_to_contain_text('Закрытая карта')
    page.locator('#show-archived').check()
    expect(page.locator('#zen-accounts button')).to_have_count(3)
    page.locator('#show-archived').uncheck()
    expect(page.locator('#zen-accounts button')).to_have_count(2)
    page.get_by_role('button',name='Операции по счёту VietinBank · основной',exact=True).click()
    expect(page.locator('#zen-panel-transactions')).to_be_visible()
    expect(page.locator('#zen-panel-accounts')).to_be_hidden()
    expect(page.locator('#history-account')).to_have_value('vnd')
    expect(page.locator('#zen article')).to_have_count(3)
    assert requests[-1]['account']==['vnd'] and requests[-1]['offset']==['0']
    expect(page.locator('#zen .transaction-day')).to_have_count(2)
    expense=page.locator('[data-transaction-id="coffee"]')
    expect(expense).to_contain_text('Кафе и рестораны')
    expect(expense).to_contain_text('Завтрак и кофе')
    expect(expense.locator('.money')).to_have_text('−65 000 VND')
    transfer=page.locator('[data-transaction-id="transfer"]')
    expect(transfer.locator('.transaction-accounts')).to_have_text('VietinBank · основной → Долларовый счёт')
    expect(transfer.locator('.transaction-sums')).to_contain_text('−13 000 000 VND')
    expect(transfer.locator('.transaction-sums')).not_to_contain_text('+500')
    page.locator('#history-account').select_option('usd')
    expect(page.locator('#zen article')).to_have_count(1)
    expect(page.locator('#zen .money')).to_have_text('+500,00 USD')
    page.locator('#zen summary').click()
    expect(page.locator('#zen dd')).to_contain_text(['transfer','13 000 000 VND','500,00 USD'])
    page.locator('#history-clear-account').click()
    expect(page.locator('#zen article')).to_have_count(4)
    expect(page.locator('#zen .transaction-day')).to_have_count(3)
    expect(page.locator('[data-transaction-id="transfer"] .money')).to_have_count(2)
    # Archive toggle controls the accounts list, never silently removes historical transactions.
    expect(page.locator('[data-transaction-id="archived"] .money')).to_have_text('−3,10 USD')
    page.screenshot(path='/results/zenmoney-transactions-desktop.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path='/results/zenmoney-transactions-mobile.png',full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.set_viewport_size({'width':1440,'height':1000})
    page.get_by_role('tab',name='Счета',exact=True).click()
    page.screenshot(path='/results/zenmoney-accounts.png',full_page=True)
    page.locator('#show-archived').check()
    page.get_by_role('button',name='Операции по счёту Закрытая карта',exact=True).click()
    expect(page.locator('#zen article')).to_have_count(1)
    expect(page.locator('#history-account')).to_have_value('old')
    page.get_by_role('tab',name='Категории',exact=True).click()
    expect(page.locator('#zen-panel-categories')).to_be_visible()
    expect(page.locator('#zen-panel-transactions')).to_be_hidden()
    page.get_by_role('tab',name='Категории',exact=True).press('Home')
    expect(page.get_by_role('tab',name='Счета',exact=True)).to_be_focused()
    expect(page.locator('#zen-panel-accounts')).to_be_visible()
    # Exact decimal formatting and textual rendering, without executing supplied markup.
    result=page.evaluate('''async () => {
      const {formatMoney}=await import('./zenmoney-view.js');
      return [formatMoney('9007199254740993.12','USD'),formatMoney('1e-8','BTC'),formatMoney('12.2300','USD')];
    }''')
    assert result==['9 007 199 254 740 993,12 USD','0,00000001 BTC','12,23 USD']
    page.close()


@pytest.mark.parametrize('transport',['fetch','xhr','cached-open'])
def test_vietinbank_json_response_capture(browser,transport):
    context,identifier=browser
    account='888822223333' if transport=='fetch' else ('888822224444' if transport=='xhr' else '888822225555')
    endpoint='https://api-ipay.vietinbank.vn/ipay/wa/getHistTransactions'
    source={'error':False,'accountNo':account,'currentPage':0,'nextPage':1,'pageSize':1,'totalRecords':2,
        'sessionId':'PRIVATE-SESSION','requestId':'PRIVATE-REQUEST','checksum':'PRIVATE-CHECKSUM',
        'transactions':[{'currency':'VND','remark':'JSON synthetic shop','amount':'42000','balance':'958000',
            'trxId':'SYNTHETIC-JSON-1','processDate':'06-10-2026 10:00:00','dorC':'D',
            'corresponsiveName':'Example shop','corresponsiveAccount':'1234567890'}]}
    bank=context.new_page()
    bank.route('https://ipay.vietinbank.vn/**',lambda route:route.fulfill(content_type='text/html',body='<h1>History</h1>'))
    bank.route(endpoint,lambda route:route.fulfill(content_type='application/json',headers={'Access-Control-Allow-Origin':'*'},body=json.dumps(source)))
    bank.goto('https://ipay.vietinbank.vn/payment-history')
    bank.evaluate('()=>{window.savedOpen=XMLHttpRequest.prototype.open;}')
    popup=context.new_page();popup.goto(f'chrome-extension://{identifier}/popup.html')
    bank.bring_to_front();popup.locator('#diagnostics').evaluate('(el)=>el.open=true');popup.locator('#manual-capture').click()
    expect(popup.locator('#status')).to_contain_text('Ожидаю новый ответ')
    popup.close()
    if transport=='fetch':
        returned=bank.evaluate('(url)=>fetch(url).then(r=>r.json())',endpoint)
    elif transport=='cached-open':
        returned=bank.evaluate('''url=>new Promise((resolve,reject)=>{const x=new XMLHttpRequest();window.savedOpen.call(x,'GET',url);x.onload=()=>resolve(JSON.parse(x.responseText));x.onerror=reject;x.send();})''',endpoint)
    else:
        returned=bank.evaluate('''url=>new Promise((resolve,reject)=>{const x=new XMLHttpRequest();x.open('GET',url);x.onload=()=>resolve(JSON.parse(x.responseText));x.onerror=reject;x.send();})''',endpoint)
    assert returned['sessionId']=='PRIVATE-SESSION', 'Bank response must remain intact'
    popup=context.new_page();popup.goto(f'chrome-extension://{identifier}/popup.html')
    expect(popup.locator('#status')).to_contain_text('Строки 1–1 из 2')
    expect(popup.locator('#status')).to_contain_text('Это часть истории')
    stored=popup.evaluate('()=>chrome.storage.session.get("bankCapture")')
    assert 'PRIVATE-' not in json.dumps(stored)
    assert stored['bankCapture']['payload']['transactions'][0]['raw']['details']['corresponsiveName']=='Example shop'
    popup.locator('#import').click();expect(popup.locator('#status')).to_contain_text('Добавлено: 1')
    popup.locator('#import').click();expect(popup.locator('#status')).to_contain_text('Уже были: 1')
    popup.locator('#diagnostics').evaluate('(el)=>el.open=true');popup.locator('#clear-capture').click();expect(popup.locator('#import')).to_be_hidden()
    # Disarmed / unsolicited messages cannot recreate a capture.
    bank.evaluate("()=>window.postMessage({source:'zenimport-history',data:{error:false}},location.origin)")
    assert popup.evaluate('()=>chrome.storage.session.get("bankCapture")')=={}
    popup.close();bank.close()


def test_json_file_preview_and_invalid_file_clears_import(browser):
    context,identifier=browser
    popup=context.new_page();popup.goto(f'chrome-extension://{identifier}/popup.html')
    data={'error':False,'accountNo':'777722223333','currentPage':0,'nextPage':0,'pageSize':100,'totalRecords':1,
          'transactions':[{'currency':'VND','remark':'Synthetic file','amount':'100','balance':'1000','trxId':'',
                           'processDate':'01-10-2026 00:00:00','dorC':'C'}]}
    popup.locator('#diagnostics > summary').click()
    popup.locator('#json-file').set_input_files({'name':'history.json','mimeType':'application/json','buffer':json.dumps(data).encode()})
    expect(popup.locator('#status')).to_contain_text('Ответ охватывает весь результат')
    expect(popup.locator('#import')).to_be_visible()
    popup.locator('#json-file').set_input_files({'name':'bad.json','mimeType':'application/json','buffer':b'{}'})
    expect(popup.locator('#import')).to_be_hidden()
    popup.close()


def test_compact_popup_period_choices_and_review_destination(browser):
    context,identifier=browser
    popup=context.new_page();popup.goto(f'chrome-extension://{identifier}/popup.html')
    expect(popup.locator('#history-period')).to_have_value('1')
    expect(popup.locator('#capture')).to_have_text('Получить историю')
    expect(popup.locator('#extract')).to_be_hidden()
    expect(popup.locator('#collect')).to_be_hidden()
    expect(popup.locator('#json-file')).to_be_hidden()
    expect(popup.locator('#period-hint')).to_contain_text('Расширение выберет даты')
    popup.locator('#history-period').select_option('6')
    expect(popup.locator('#period-hint')).to_contain_text('частями до 30 дней')
    popup.reload();expect(popup.locator('#history-period')).to_have_value('6')
    Path('/results').mkdir(exist_ok=True)
    popup.set_viewport_size({'width':370,'height':650})
    popup.screenshot(path='/results/history-popup.png',full_page=True)
    assert popup.evaluate('document.documentElement.scrollWidth <= innerWidth')
    with context.expect_page() as opened:
        popup.locator('#open').click()
    review_page=opened.value
    review_page.wait_for_url('**/app.html#review')
    review_page.close();popup.close()


def test_live_review_shows_candidates_without_enabling_writes(browser):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state['mode']='live';state['capabilities']={'write':False}
    state['review']=[{'id':'review-example','state':'pending','date':'2026-10-01','amount_minor':-100,'currency':'VND','description':'Example payment','raw':{},'mapped':True,'bank_account':'example',
                     'candidates':[{'id':'zen-example','date':'2026-10-01','payee':'Example recipient','reason':'Сумма и счёт совпадают; дата ±2 дня'}]}]
    page=context.new_page()
    page.route('**/api/state',lambda route:route.fulfill(content_type='application/json',body=json.dumps(state)))
    page.goto(f'chrome-extension://{identifier}/app.html#review')
    expect(page.locator('#review')).to_contain_text('Example recipient')
    expect(page.locator('#review')).to_contain_text('Запись отключена')
    assert page.locator('#review button').count()==0
    page.close()


def test_capture_failure_keeps_visible_diagnostic_steps(browser):
    context,identifier=browser
    other=context.new_page();other.goto(BASE+'/health')
    popup=context.new_page();popup.goto(f'chrome-extension://{identifier}/popup.html')
    other.bring_to_front();popup.locator('#diagnostics').evaluate('(el)=>el.open=true');popup.locator('#manual-capture').click()
    expect(popup.locator('#status')).to_contain_text('Откройте историю счёта')
    expect(popup.locator('#capture-steps')).to_contain_text('Запуск принят')
    expect(popup.locator('#capture-steps')).to_contain_text('Этап завершился ошибкой')
    assert popup.evaluate('()=>chrome.storage.session.get("bankCapture")')['bankCapture']['status']=='error'
    popup.reload();expect(popup.locator('#status')).to_contain_text('Откройте историю счёта')
    expect(popup.locator('#capture-progress')).to_be_visible()
    popup.locator('#diagnostics').evaluate('(el)=>el.open=true');popup.locator('#clear-capture').click()
    expect(popup.locator('#capture-progress')).to_be_hidden()
    popup.close();other.close()


@pytest.mark.parametrize('continuation_zero',[False,True])
def test_automatic_history_background_pagination_and_dedup(browser,continuation_zero):
    context, identifier = browser
    account = '888899990011' if continuation_zero else '888899990001'
    requests = []
    fixture = Path(__file__).with_name('auto-bank.html').read_text()
    context.route('https://ipay.vietinbank.vn/payment-history?accountNo='+account,
                  lambda route: route.fulfill(content_type='text/html', body=fixture))
    def response(route):
        body = json.loads(route.request.post_data)
        requests.append(body)
        # Deliberately invented fixture parameters: only the synthetic bank sends them.
        start, page = body['from'], body['page']
        date = '-'.join(reversed(start.split('-')))
        rows = [{'currency':'VND','remark':'Synthetic auto payment','amount':'1000','balance':'9000',
                 'trxId':f'auto-{start}-{n}','processDate':date+' 12:00:00','dorC':'D'}
                for n in range(page*100, min(page*100+100,103))]
        route.fulfill(content_type='application/json', headers={'Access-Control-Allow-Origin':'*'},
                      body=json.dumps({'error':False,'accountNo':account,'currentPage':page,'nextPage':page+1,
                                       'pageSize':100,'totalRecords':0 if continuation_zero and page else 103,'transactions':rows,'sessionId':'DO-NOT-LOG'}))
    context.route('https://api-ipay.vietinbank.vn/ipay/wa/getHistTransactions', response)
    bank = context.new_page();bank.goto('https://ipay.vietinbank.vn/payment-history?accountNo='+account)
    popup = context.new_page();popup.goto(f'chrome-extension://{identifier}/popup.html')
    worker = context.service_workers[0]
    previous=worker.evaluate("async()=>(await chrome.storage.local.get('autoJob')).autoJob?.id")
    popup.locator('#history-period').select_option('6');bank.bring_to_front();popup.locator('#capture').click()
    for _ in range(100):
        started=worker.evaluate("async()=>(await chrome.storage.local.get('autoJob')).autoJob")
        if started and started['id']!=previous:break
        bank.wait_for_timeout(100)
    assert started['id']!=previous
    popup.close()  # Work must continue without the popup.
    for _ in range(180):
        job = worker.evaluate("async()=> (await chrome.storage.local.get('autoJob')).autoJob")
        if job['status'] not in ['starting','navigating','running','saving']:break
        bank.wait_for_timeout(500)
    assert job['status']=='done', str(job.get('error'))+' '+json.dumps(job,ensure_ascii=False)[-2500:]
    expected=len(job['parts'])*103
    assert job['added']==expected and job['duplicates']==0
    assert len(requests)==len(job['parts'])*2
    assert all(x['rows']==103 for x in job['completed'])
    if continuation_zero:
        assert job['responses'][-1]['counters']['totalRecords']['value']==0
        assert job['responses'][-1]['rowCount']==3
    assert 'DO-NOT-LOG' not in json.dumps(job)
    popup=context.new_page();popup.goto(f'chrome-extension://{identifier}/popup.html')
    expect(popup.locator('#auto-status')).to_contain_text('Весь период сохранён')
    bank.bring_to_front();popup.locator('#capture').click()
    expect(popup.locator('#auto-status')).to_contain_text(f'уже были: {expected}',timeout=90000)
    expect(popup.locator('#auto-status')).to_contain_text('Весь период сохранён',timeout=10000)
    with popup.expect_download() as downloading:popup.locator('#auto-export').click()
    report=json.loads(Path(downloading.value.path()).read_text())
    assert report['current']['status']=='done'
    assert account not in json.dumps(report) and TOKEN not in json.dumps(report)
    popup.screenshot(path='/results/automatic-history.png',full_page=True)
    popup.close();bank.close()
    context.unroute('https://api-ipay.vietinbank.vn/ipay/wa/getHistTransactions',response)


@pytest.mark.parametrize('failure',['wrong_account','wrong_period','changing_total','cancel','logout','empty','money_format'])
def test_automatic_history_failures_are_persisted(browser,failure):
    context, identifier=browser
    account='88889999000'+str(2+['wrong_account','wrong_period','changing_total','cancel','logout','empty','money_format'].index(failure))
    fixture=Path(__file__).with_name('auto-bank.html').read_text()
    if failure=='logout':fixture=fixture.replace("tablePage=1;fetchPage(0);","location.href='/login';")
    url='https://ipay.vietinbank.vn/payment-history?accountNo='+account
    context.route(url,lambda route:route.fulfill(content_type='text/html',body=fixture))
    context.route('https://ipay.vietinbank.vn/login',lambda route:route.fulfill(content_type='text/html',body='Login'))
    def respond(route):
        body=json.loads(route.request.post_data)
        if failure=='cancel':route.fulfill(status=200,headers={'Access-Control-Allow-Origin':'*'},body=json.dumps({'transactions':[], 'currentPage':999}));return
        total=0 if failure=='empty' else (104 if body['page'] else 103)
        date='01-01-2000' if failure=='wrong_period' else '-'.join(reversed(body['from'].split('-')))
        rows=[{'currency':'VND','remark':'Failure fixture','amount':'2000','balance':'8000',
               'trxId':f"{account}-{body['from']}-{n}",'processDate':date+' 10:00:00','dorC':'D'}
              for n in range(body['page']*100,min(body['page']*100+100,total))]
        if failure=='money_format':rows[0]['balance']='-1234.00'
        route.fulfill(content_type='application/json',headers={'Access-Control-Allow-Origin':'*'},body=json.dumps({
            'error':False,'accountNo':'99999999' if failure=='wrong_account' else account,
            'currentPage':body['page'],'pageSize':100,'totalRecords':total,'transactions':rows}))
    endpoint='https://api-ipay.vietinbank.vn/ipay/wa/getHistTransactions'
    context.route(endpoint,respond)
    bank=context.new_page();bank.goto(url)
    popup=context.new_page();popup.goto(f'chrome-extension://{identifier}/popup.html')
    previous=popup.evaluate("async()=>(await chrome.storage.local.get('autoJob')).autoJob?.id")
    popup.locator('#history-period').select_option('1');bank.bring_to_front();popup.locator('#capture').click()
    for _ in range(100):
        started=popup.evaluate("async()=>(await chrome.storage.local.get('autoJob')).autoJob")
        if started and started['id']!=previous:break
        popup.wait_for_timeout(100)
    assert started['id']!=previous
    if failure=='cancel':
        expect(popup.locator('#auto-cancel')).to_be_visible();popup.locator('#auto-cancel').click()
    wanted='done' if failure=='empty' else ('cancelled' if failure=='cancel' else 'error')
    for _ in range(100):
        job=popup.evaluate("async()=>(await chrome.storage.local.get('autoJob')).autoJob")
        if job['status']==wanted:break
        popup.wait_for_timeout(300)
    assert job['status']==wanted, str(job.get('error'))+' '+json.dumps(job,ensure_ascii=False)[-2000:]
    assert job['added']==(100 if failure=='changing_total' else 0), str(job.get('error'))+' '+json.dumps(job,ensure_ascii=False)[-2000:]
    if failure=='money_format':
        assert 'balance' in job['error'] and 'Строка 1' in job['error']
        issue=job['responses'][-1]['moneyIssues'][0]
        assert issue['field']=='balance' and issue['shape']=='-####.##'
        assert issue['fractionAllZero'] is True
        assert '-1234' not in json.dumps(job)
    if failure=='changing_total':
        assert not job['completed'] and job['current']['rows']==100
        assert job['responses'][-1]['expected']['total']==103
        assert job['responses'][-1]['counters']['totalRecords']['value']==104
        assert job['responses'][-1]['counters']['currentPage']['value']==1
        assert job['responses'][-1]['rowCount']==4
        with popup.expect_download() as downloaded:popup.locator('#auto-export').click()
        report=json.loads(Path(downloaded.value.path()).read_text())
        assert report['current']['responses'][-1]['rowCount']==4
        assert account not in json.dumps(report)
    popup.reload();expect(popup.locator('#auto-status')).to_contain_text('Добавлено: '+str(job['added']))
    popup.close();bank.close();context.unroute(endpoint,respond)


def test_unified_ledger_filters_local_links_and_category_evidence(browser):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True},outbox=[],bank_imports=[])
    state['bank_accounts']=[{'id':'ba','bank':'vietinbank','account':'11112222','currency':'VND','zenmoney_account':'za'},
                            {'id':'bb','bank':'other','account':'33334444','currency':'VND','zenmoney_account':'zb'}]
    state['zenmoney_accounts']=[{'id':'za','title':'Andrei VND · VietinBank','instrument':1}, {'id':'zb','title':'Другой счёт','instrument':1}]
    state['categories']=[{'id':'food','title':'Рестораны и доставка'}]
    candidate={'id':'z1','date':'2026-10-05','payee':'Coffee shop','categories':['Рестораны и доставка'],'reason':'Сумма и счёт совпадают; дата ±2 дня'}
    common={'state':'pending','mapped':True,'bank_account':'ba','currency':'VND','amount_minor':-65000,'raw':{},'candidates':[],'linked':None,'category_suggestion':None}
    state['review']=[{**common,'id':'b1','date':'2026-10-05','description':'Coffee payment','reconciliation_status':'possible','candidates':[candidate]},
                     {**common,'id':'b2','date':'2026-10-04','description':'Dinner payment <img src=x onerror=alert(1)>','reconciliation_status':'new',
                      'category_suggestion':{'title':'Рестораны и доставка','category':'food','count':3,'conflict':False,'reason':'Тот же получатель',
                       'distribution':[{'id':'food','title':'Рестораны и доставка','count':3}], 'examples':[{'id':'e1','date':'2026-09-01','payee':'Dinner','title':'Рестораны и доставка'}]}},
                     {**common,'id':'b3','date':'2026-10-03','bank_account':'bb','description':'Refund','amount_minor':1000,'reconciliation_status':'new'}]
    page=context.new_page();page.route('**/api/state',lambda route:route.fulfill(content_type='application/json',body=json.dumps(state)))
    def link(route):
        tx=state['review'][0]
        if route.request.method=='POST':
            body=json.loads(route.request.post_data);assert body['match_id']=='z1' and body['action']=='link'
            tx.update(state='linked',linked_id='z1',linked=candidate,reconciliation_status='synced')
        else:tx.update(state='pending',linked_id=None,linked=None,reconciliation_status='possible')
        route.fulfill(content_type='application/json',body=json.dumps({'state':tx['state']}))
    page.route('**/api/review/b1/link',link)
    page.goto(f'chrome-extension://{identifier}/app.html#review')
    expect(page.locator('#review .ledger-entry')).to_have_count(3)
    assert page.locator('#review img').count()==0
    assert page.locator('#review .ledger-day').evaluate_all('(els)=>els.map(e=>e.dataset.date)')==['2026-10-05','2026-10-04','2026-10-03']
    expect(page.locator('[data-review-status="synced"]')).to_contain_text('0')
    page.locator('#review-account').select_option('ba');expect(page.locator('#review .ledger-entry')).to_have_count(2)
    page.locator('[data-review-status="new"]').click();expect(page.locator('#review .ledger-entry')).to_have_count(1)
    page.locator('#review .ledger-summary').click();expect(page.locator('.category-evidence')).to_contain_text('1 сентября 2026')
    page.locator('[data-review-status="all"]').click();page.locator('[data-transaction="b1"] .ledger-summary').click()
    page.get_by_role('button',name='Это та же операция',exact=True).click()
    expect(page.locator('[data-transaction="b1"]')).to_have_attribute('data-state','synced')
    page.locator('[data-review-status="synced"]').click();expect(page.locator('#review .ledger-entry')).to_have_count(1)
    page.locator('[data-transaction="b1"] .ledger-summary').click();page.get_by_role('button',name='Отменить связь').click()
    expect(page.locator('#review .ledger-entry')).to_have_count(0)
    page.locator('[data-review-status="all"]').click()
    page.locator('#review-search').fill('Dinner');expect(page.locator('#review .ledger-entry')).to_have_count(1)
    page.locator('#review-search').fill('');page.locator('#review-start').fill('2026-10-05');expect(page.locator('#review .ledger-entry')).to_have_count(1)
    page.locator('#review-start').fill('')
    page.screenshot(path='/results/ledger-desktop.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844});page.screenshot(path='/results/ledger-mobile.png',full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert not page.locator('#review button').filter(has_text='Подтвердить создание').count()
    page.close()


def test_day_proposal_requires_explicit_confirmation(browser):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True},outbox=[],recipient_profiles=[])
    state['bank_accounts']=[{'id':'ba','bank':'vietinbank','account':'1234','currency':'VND','zenmoney_account':'za'}]
    state['zenmoney_accounts']=[{'id':'za','title':'Bank','instrument':1}]
    c={'id':'z','date':'2026-10-01','payee':'Shop','categories':['Food'],'reason':'Exact day'}
    state['review']=[{'id':'b','bank_account':'ba','date':'2026-10-01','amount_minor':-100,'currency':'VND','description':'Bank Shop',
       'raw':{},'mapped':True,'state':'pending','reconciliation_status':'possible','candidates':[c],'category_suggestion':None}]
    state['day_review']=[{'bank_account':'ba','date':'2026-10-01','equal':True,'bank_count':1,'zen_count':1,'pairs':[{'bank_id':'b','zen_id':'z'}],'ambiguous':0,'snapshot':'a'*64}]
    page=context.new_page();calls=[]
    page.route('**/api/state',lambda route:route.fulfill(content_type='application/json',body=json.dumps(state)))
    def confirm_day(route):
        calls.append(json.loads(route.request.post_data))
        state['review'][0].update(state='linked',linked_id='z',linked=c,reconciliation_status='synced')
        state['day_review'][0]['pairs']=[]
        route.fulfill(content_type='application/json',body='{"linked":1}')
    page.route('**/api/review-days/ba/2026-10-01/confirm',confirm_day)
    page.goto(f'chrome-extension://{identifier}/app.html#review')
    expect(page.locator('details.day-proposal')).to_contain_text('Суммы дня совпадают')
    assert not calls
    page.locator('details.day-proposal summary').click()
    expect(page.locator('details.day-proposal')).to_contain_text('Bank Shop → Shop · Food')
    page.get_by_role('button',name='Подтвердить пары дня · 1',exact=True).click()
    expect(page.locator('[data-transaction="b"]')).to_have_attribute('data-state','synced')
    assert calls==[{'snapshot':'a'*64}]
    page.close()


def test_bulk_day_button_confirms_only_eligible_days_in_selected_scope(browser):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True},outbox=[],recipient_profiles=[])
    state['bank_accounts']=[{'id':'ba','bank':'vietinbank','account':'1234','currency':'VND','zenmoney_account':'za'},
                            {'id':'bb','bank':'other','account':'5678','currency':'VND','zenmoney_account':'zb'}]
    state['zenmoney_accounts']=[{'id':'za','title':'Bank','instrument':1},{'id':'zb','title':'Other','instrument':1}]
    state['review']=[];state['day_review']=[]
    for i,(day,account,eligible) in enumerate([('2026-10-01','ba',True),('2026-10-02','ba',True),('2026-10-03','ba',False),('2026-10-04','bb',True)]):
        c={'id':f'z{i}','date':day,'payee':'Shop','categories':['Food'],'reason':'Exact day'}
        state['review'].append({'id':f'b{i}','bank_account':account,'date':day,'amount_minor':-100,'currency':'VND','description':'Bank Shop',
          'raw':{},'mapped':True,'state':'pending','reconciliation_status':'possible','candidates':[c],'category_suggestion':None})
        state['day_review'].append({'bank_account':account,'date':day,'equal':True,'bank_count':1,'zen_count':1,
          'pairs':[{'bank_id':f'b{i}','zen_id':f'z{i}'}],'ambiguous':0 if eligible else 2,'bulk_eligible':eligible,'snapshot':str(i)*64})
    page=context.new_page();calls=[]
    page.route('**/api/state',lambda route:route.fulfill(content_type='application/json',body=json.dumps(state)))
    def confirm_bulk(route):
        body=json.loads(route.request.post_data);calls.append(body)
        assert [d['date'] for d in body['days']]==['2026-10-01','2026-10-02']
        for d in state['day_review']:
            if d['date'] in ['2026-10-01','2026-10-02']:
                d.update(bulk_eligible=False,pairs=[])
        for row in state['review'][:2]:row.update(state='linked',linked=row['candidates'][0],reconciliation_status='synced')
        route.fulfill(content_type='application/json',body='{"days":2,"linked":2}')
    page.route('**/api/review-days/confirm',confirm_bulk)
    page.goto(f'chrome-extension://{identifier}/app.html#review')
    page.locator('#review-account').select_option('ba')
    page.locator('#review-start').fill('2026-10-02')
    expect(page.locator('.bulk-day-review')).to_contain_text('Полностью однозначных дней: 1.')
    page.locator('#review-start').fill('')
    page.locator('#review-search').fill('no matching rows')
    expect(page.locator('.ledger-entry')).to_have_count(0)
    expect(page.locator('.bulk-day-review')).to_contain_text('Полностью однозначных дней: 2.')
    assert not calls
    page.get_by_role('button',name='Сопоставить все однозначные дни · 2 операций',exact=True).click()
    expect(page.get_by_role('dialog')).to_be_visible()
    expect(page.locator('.pairs-review-pair')).to_have_count(2)
    assert not calls
    page.get_by_role('button',name='Подтвердить и сохранить · 2',exact=True).click()
    expect(page.locator('.bulk-day-review')).to_contain_text('Нет полностью однозначных дней')
    assert len(calls)==1
    page.locator('#review-search').fill('')
    expect(page.locator('[data-transaction="b2"]')).to_have_attribute('data-state','possible')
    expect(page.locator('[data-transaction="b0"]')).to_have_attribute('data-state','synced')
    page.close()


def test_readonly_full_day_comparison_shows_both_sources(browser):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True},recipient_profiles=[],outbox=[])
    state['bank_accounts']=[{'id':'ba','bank':'bank','account':'1234','currency':'VND','zenmoney_account':'za'}]
    state['zenmoney_accounts']=[{'id':'za','title':'Bank','instrument':1}]
    state['review']=[{'id':'b','bank_account':'ba','date':'2026-10-01','amount_minor':-100,'currency':'VND','description':'Bank purchase',
      'raw':{},'state':'pending','mapped':True,'candidates':[],'category_suggestion':None,'reconciliation_status':'new'}]
    z={'id':'z','date':'2026-10-01','amount_minor':'-200','currency':'VND','payee':'Only Zen <img src=x>','comment':'Comment',
       'categories':['Food'],'transfer':False,'linked_bank_id':None,'same_day_bank_ids':[]}
    state['day_review']=[{'bank_account':'ba','date':'2026-10-01','bank_count':1,'zen_count':1,'equal':False,'pairs':[],'ambiguous':0,
      'bulk_eligible':False,'snapshot':'a'*64,'bank_ids':['b'],'zen_rows':[z]},
      {'bank_account':'ba','date':'2026-10-02','bank_count':0,'zen_count':1,'equal':False,'pairs':[],'ambiguous':0,
      'bulk_eligible':False,'snapshot':'b'*64,'bank_ids':[],'zen_rows':[{**z,'id':'z2','date':'2026-10-02'}]}]
    page=context.new_page();page.route('**/api/state',lambda route:route.fulfill(content_type='application/json',body=json.dumps(state)))
    page.goto(f'chrome-extension://{identifier}/app.html#review')
    page.locator('.full-day-review > summary').click()
    first=page.locator('.full-day-review .comparison-day[data-date="2026-10-01"]')
    first.locator(':scope > summary').click()
    expect(first).to_contain_text('Bank purchase');expect(first).to_contain_text('Only Zen <img src=x>')
    expect(first).to_contain_text('Нет пары по сумме в этот день')
    expect(first).to_contain_text('Нет пары по сумме в банке в этот день')
    assert first.locator('img').count()==0 and first.locator('button').count()==0
    second=page.locator('.full-day-review .comparison-day[data-date="2026-10-02"]')
    second.locator('summary').click();expect(second).to_contain_text('Банковских операций за этот день не загружено')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.close()


def test_work_days_filter_keeps_all_rows_of_problem_days(browser):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True},recipient_profiles=[],outbox=[])
    state['bank_accounts']=[{'id':'ba','bank':'bank','account':'1234','currency':'VND','zenmoney_account':'za'}]
    state['zenmoney_accounts']=[{'id':'za','title':'Bank','instrument':1}]
    state['review']=[];state['day_review']=[]
    for i,(day,pending,categories,transfer) in enumerate([(1,False,['Food'],False),(2,True,['Food'],False),(2,False,['Food'],False),(3,False,[],False),(4,False,[],True)]):
        date=f'2026-10-0{day}'
        z={'id':f'z{i}','date':date,'amount_minor':'-100','currency':'VND','payee':'Shop','comment':'','categories':categories,
           'transfer':transfer,'linked_bank_id':None if pending else f'b{i}','same_day_bank_ids':[f'b{i}']}
        row={'id':f'b{i}','bank_account':'ba','date':date,'amount_minor':-100,'currency':'VND','description':'Bank purchase',
             'raw':{},'state':'pending' if pending else 'linked','mapped':True,'candidates':[z],'category_suggestion':None,
             'linked':None if pending else z,'linked_id':None if pending else z['id'],'reconciliation_status':'possible' if pending else 'synced'}
        state['review'].append(row)
        d=next((d for d in state['day_review'] if d['date']==date),None)
        if d is None:
            d={'bank_account':'ba','date':date,'bank_count':0,'zen_count':0,'equal':True,'pairs':[],'ambiguous':0,'bulk_eligible':False,'snapshot':'a'*64,'bank_ids':[],'zen_rows':[]}
            state['day_review'].append(d)
        d['bank_count']+=1;d['zen_count']+=1;d['bank_ids'].append(row['id']);d['zen_rows'].append(z)
    state['day_review'].append({'bank_account':'ba','date':'2026-10-05','bank_count':0,'zen_count':1,'equal':False,'pairs':[],
       'ambiguous':0,'bulk_eligible':False,'snapshot':'b'*64,'bank_ids':[],'zen_rows':[{**z,'id':'only','date':'2026-10-05','linked_bank_id':None,'same_day_bank_ids':[]}]})
    page=context.new_page();page.route('**/api/state',lambda route:route.fulfill(content_type='application/json',body=json.dumps(state)))
    page.goto(f'chrome-extension://{identifier}/app.html#review')
    expect(page.locator('.ledger-entry')).to_have_count(5)
    page.locator('[data-review-status="synced"]').click()
    page.evaluate("location.hash='attention'")
    expect(page.locator('#title')).to_have_text('Требуют внимания')
    expect(page.locator('.attention-day')).to_have_count(3)
    expect(page.locator('.attention-day[data-date="2026-10-02"]')).to_contain_text('Банк + ZenMoney')
    expect(page.locator('.attention-day[data-date="2026-10-03"]')).to_be_visible()
    expect(page.locator('.attention-day[data-date="2026-10-05"]')).to_contain_text('Только ZenMoney')
    expect(page.locator('.ledger-entry')).to_have_count(0)
    expect(page.locator('.full-day-review')).to_have_count(0)
    expect(page.locator('#review-search')).to_be_hidden()
    calls=[]
    def link_pair(route):
        body=json.loads(route.request.post_data);calls.append(body)
        assert body=={'action':'link','match_id':'z1','reviewed_candidates':['z1']}
        row=state['review'][1];z=row['candidates'][0];z['linked_bank_id']='b1'
        row.update(state='linked',linked=z,linked_id='z1',reconciliation_status='synced')
        route.fulfill(content_type='application/json',body='{"state":"linked"}')
    page.route('**/api/review/b1/link',link_pair)
    pending=page.locator('.attention-day[data-date="2026-10-02"] .attention-operation').filter(has_text='связь не подтверждена')
    pending.locator(':scope > summary').click()
    pending.locator('select').select_option('z1')
    assert not calls
    pending.get_by_role('button',name='Подтвердить выбранную пару').click()
    expect(page.locator('.attention-day')).to_have_count(2)
    assert len(calls)==1
    page.reload()
    expect(page.locator('.attention-day')).to_have_count(2)
    page.locator('#review-start').fill('2026-10-04');page.locator('#review-end').fill('2026-10-04')
    expect(page.locator('#review')).to_contain_text('Нет дней, требующих внимания')
    page.locator('#review-start').fill('');page.locator('#review-end').fill('')
    page.locator('nav a[href="#review"]').click()
    page.locator('[data-review-status="all"]').click()
    expect(page.locator('.ledger-entry')).to_have_count(5)
    expect(page.locator('#review-search')).to_be_enabled()
    page.close()


@pytest.mark.parametrize('route',['attention','banks/bank'])
@pytest.mark.parametrize('scope',['day','all'])
def test_attention_preview_pairs_save_explicitly(browser,scope,route):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True},recipient_profiles=[],outbox=[])
    state['bank_accounts']=[{'id':'ba','bank':'bank','account':'1234','currency':'VND','zenmoney_account':'za'}]
    state['zenmoney_accounts']=[{'id':'za','title':'Bank','instrument':1}]
    state['review']=[];state['day_review']=[]
    for i in [1,2]:
        date=f'2026-10-0{i}'
        z={'id':f'z{i}','date':date,'amount_minor':'-100','currency':'VND','payee':'Shop','categories':['Food'],
           'transfer':False,'linked_bank_id':None,'same_day_bank_ids':[f'b{i}']}
        state['review'].append({'id':f'b{i}','bank_account':'ba','date':date,'amount_minor':-100,'currency':'VND','description':'Bank purchase',
             'raw':{},'state':'pending','mapped':True,'candidates':[z],'category_suggestion':None,'linked':None,'reconciliation_status':'possible'})
        state['day_review'].append({'bank_account':'ba','date':date,'bank_count':1,'zen_count':1,'equal':True,'pairs':[],
             'preview_pairs':[{'bank_id':f'b{i}','zen_id':f'z{i}'}],'ambiguous':0,'bulk_eligible':False,'snapshot':str(i)*64,'bank_ids':[f'b{i}'],'zen_rows':[z]})
    page=context.new_page();calls=[]
    page.route('**/api/state',lambda route:route.fulfill(content_type='application/json',body=json.dumps(state)))
    def save(route):
        body=json.loads(route.request.post_data);calls.append(body)
        expected=['2026-10-01'] if scope=='day' else ['2026-10-01','2026-10-02']
        assert sorted(d['date'] for d in body['days'])==expected
        for d in state['day_review']:
            if d['date'] not in expected:continue
            d['preview_pairs']=[];z=d['zen_rows'][0];z['linked_bank_id']=d['bank_ids'][0]
            row=next(r for r in state['review'] if r['id']==z['linked_bank_id'])
            row.update(state='linked',linked=z,linked_id=z['id'],reconciliation_status='synced')
        route.fulfill(content_type='application/json',body=json.dumps({'days':len(expected),'linked':len(expected)}))
    page.route('**/api/review-pairs/confirm',save)
    page.goto(f'chrome-extension://{identifier}/app.html#{route}')
    connect(page)
    expect(page.locator('#status')).to_have_text('Данные обновлены.')
    page.goto(f'chrome-extension://{identifier}/app.html#{route}')
    if route.startswith('banks/'):
        page.locator('#bank-attention-only').check()
    expect(page.locator('.attention-operation')).to_have_count(2)
    expect(page.locator('.attention-operation').first).to_contain_text('Предварительная пара · не сохранена')
    assert not calls
    if scope=='day':page.locator('.attention-day[data-date="2026-10-01"]').get_by_role('button',name='Сохранить пары дня · 1',exact=True).click()
    else:
        launch=page.get_by_role('button',name='Сохранить пары всех показанных дней · 2',exact=True)
        launch.click()
        expect(page.get_by_role('dialog',name='Проверка пар перед сохранением')).to_be_visible()
        expect(page.locator('.pairs-review-pair')).to_have_count(2)
        expect(page.locator('.pairs-review-list')).to_contain_text('Bank purchase')
        expect(page.locator('.pairs-review-list')).to_contain_text('Food')
        assert not calls
        page.get_by_role('button',name='Отмена',exact=True).click()
        expect(page.get_by_role('dialog')).to_have_count(0)
        launch.click();page.keyboard.press('Escape')
        expect(page.get_by_role('dialog')).to_have_count(0)
        assert not calls
        launch.click()
        page.get_by_role('button',name='Подтвердить и сохранить · 2',exact=True).click()
    expect(page.locator('.attention-day')).to_have_count(1 if scope=='day' else 0)
    assert len(calls)==1
    page.close()


def test_split_proposal_shows_categories_saves_and_unlinks(browser):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True},recipient_profiles=[],outbox=[])
    state['bank_accounts']=[{'id':'ba','bank':'bank','account':'1234','currency':'VND','zenmoney_account':'za'}]
    state['zenmoney_accounts']=[{'id':'za','title':'Bank','instrument':1}]
    base={'bank_account':'ba','date':'2026-10-01','currency':'VND','raw':{},'state':'pending','mapped':True,
          'candidates':[],'category_suggestion':None,'linked':None,'reconciliation_status':'new','split_ids':[]}
    state['review']=[{**base,'id':'b','amount_minor':-100,'description':'Supermarket'}, {**base,'id':'extra','amount_minor':-50,'description':'Other'}]
    zs=[{'id':id,'date':'2026-10-01','amount_minor':str(-amount),'currency':'VND','payee':'Shop','categories':[cat],
         'transfer':False,'linked_bank_id':None,'same_day_bank_ids':[]} for id,amount,cat in [('food',40,'Food'),('toys',60,'Toys')]]
    option={'bank_id':'b','zen_ids':['food','toys'],'unique':True}
    d={'bank_account':'ba','date':'2026-10-01','bank_count':2,'zen_count':2,'equal':False,'resolved':False,'pairs':[],
       'preview_pairs':[],'ambiguous':0,'bulk_eligible':False,'snapshot':'a'*64,'bank_ids':['b','extra'],'zen_rows':zs,
       'split_candidates':[option],'split_search_limited':False,'bank_total':'-150','zen_total':'-100'}
    state['day_review']=[d]
    page=context.new_page();calls=[]
    page.route('**/api/state',lambda route:route.fulfill(content_type='application/json',body=json.dumps(state)))
    def save(route):
        calls.append('save');assert json.loads(route.request.post_data)=={'snapshot':'a'*64,'zen_ids':['food','toys']}
        state['review'][0].update(state='linked',split_ids=['food','toys'],linked={'split':True,'date':d['date'],'categories':['Food','Toys'],'transfer':False},reconciliation_status='synced')
        for z in zs:z['linked_bank_id']='b'
        d['split_candidates']=[]
        route.fulfill(content_type='application/json',body='{"state":"linked","parts":2}')
    def undo(route):
        calls.append('undo');state['review'][0].update(state='pending',split_ids=[],linked=None,reconciliation_status='new')
        for z in zs:z['linked_bank_id']=None
        d['split_candidates']=[option]
        route.fulfill(content_type='application/json',body='{"state":"pending"}')
    page.route('**/api/review/b/split-link',save);page.route('**/api/review/b/link',undo)
    page.goto(f'chrome-extension://{identifier}/app.html#attention')
    entry=page.locator('.attention-operation').filter(has_text='Возможная разбивка по категориям')
    entry.locator('summary').first.click();entry.locator('.split-option summary').click()
    expect(entry.locator('.split-option')).to_contain_text('Food');expect(entry.locator('.split-option')).to_contain_text('Toys')
    assert not calls
    entry.get_by_role('button',name='Сохранить составную связь').click()
    expect(page.locator('.attention-operation')).to_have_count(2)
    paired=page.locator('.attention-operation').filter(has_text='Связь подтверждена')
    paired.locator('summary').click();expect(paired).to_contain_text('Банк: 1 · ZenMoney: 2')
    paired.get_by_role('button',name='Отменить составную связь').click()
    expect(page.locator('.split-option')).to_have_count(1)
    assert calls==['save','undo']
    page.close()


def test_hide_reconciled_preserves_unresolved_and_missing_categories(browser):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True},recipient_profiles=[],outbox=[])
    state['bank_accounts']=[{'id':'ba','bank':'bank','account':'1234','currency':'VND','zenmoney_account':'za'}]
    state['zenmoney_accounts']=[{'id':'za','title':'Bank','instrument':1}]
    state['review']=[];zs=[]
    for i,(pending,categories,transfer) in enumerate([(False,['Food'],False),(True,['Food'],False),(False,[],False),(False,[],True)]):
        z={'id':f'z{i}','date':'2026-10-01','amount_minor':str(-100-i),'currency':'VND','payee':'Shop','categories':categories,
           'transfer':transfer,'linked_bank_id':None if pending else f'b{i}','same_day_bank_ids':[f'b{i}']}
        zs.append(z)
        state['review'].append({'id':f'b{i}','bank_account':'ba','date':'2026-10-01','amount_minor':-100-i,'currency':'VND',
             'description':f'Payment {i}','raw':{},'state':'pending' if pending else 'linked','mapped':True,'candidates':[z],
             'category_suggestion':None,'linked':None if pending else z,'linked_id':None if pending else z['id'],
             'reconciliation_status':'possible' if pending else 'synced'})
    state['day_review']=[{'bank_account':'ba','date':'2026-10-01','bank_count':4,'zen_count':4,'equal':True,'pairs':[],
             'preview_pairs':[],'ambiguous':0,'bulk_eligible':False,'snapshot':'a'*64,'bank_ids':['b0','b1','b2','b3'],'zen_rows':zs}]
    page=context.new_page();page.route('**/api/state',lambda route:route.fulfill(content_type='application/json',body=json.dumps(state)))
    page.goto(f'chrome-extension://{identifier}/app.html#attention')
    expect(page.locator('.attention-operation')).to_have_count(4)
    page.locator('#hide-reconciled').check()
    expect(page.locator('.attention-day')).to_have_count(1)
    expect(page.locator('.attention-operation')).to_have_count(2)
    expect(page.locator('#review')).to_contain_text('Payment 1')
    expect(page.locator('#review')).to_contain_text('Payment 2')
    page.get_by_role('button',name='Обновить данные',exact=True).click()
    expect(page.locator('.attention-operation')).to_have_count(2)
    page.locator('#hide-reconciled').uncheck()
    expect(page.locator('.attention-operation')).to_have_count(4)
    page.locator('nav a[href="#review"]').click()
    expect(page.locator('#hide-reconciled')).to_be_hidden()
    page.close()


def test_settlement_manual_selection_group_display_and_undo(browser):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True},recipient_profiles=[],outbox=[])
    state['bank_accounts']=[{'id':'ba','bank':'bank','account':'1234','currency':'VND','zenmoney_account':'za'}]
    state['zenmoney_accounts']=[{'id':'za','title':'Bank','instrument':1}]
    base={'bank_account':'ba','date':'2026-04-11','currency':'VND','raw':{},'state':'pending','mapped':True,
          'candidates':[],'category_suggestion':None,'linked':None,'reconciliation_status':'new','split_ids':[]}
    state['review']=[{**base,'id':id,'amount_minor':amount,'description':name} for id,amount,name in [('expense',-961440,'For friend'),('refund',1000000,'Refund'),('other',-10,'Other')]]
    z={'id':'net','date':base['date'],'amount_minor':'38560','currency':'VND','payee':'Rounding','categories':['Income'],
       'transfer':False,'linked_bank_id':None,'same_day_bank_ids':[]}
    d={'bank_account':'ba','date':base['date'],'bank_count':3,'zen_count':1,'equal':False,'resolved':False,'pairs':[],
       'preview_pairs':[],'ambiguous':0,'bulk_eligible':False,'snapshot':'a'*64,'bank_ids':['expense','refund','other'],'zen_rows':[z],
       'split_candidates':[],'bank_total':'38550','zen_total':'38560'}
    state['day_review']=[d];calls=[]
    page=context.new_page()
    page.route('**/api/state',lambda route:route.fulfill(content_type='application/json',body=json.dumps(state)))
    def save(route):
        body=json.loads(route.request.post_data)
        assert body=={'bank_ids':['expense','refund'],'zen_ids':['net'],'snapshot':'a'*64}
        calls.append('save')
        group={'id':'group','kind':'settlement','bank_ids':['expense','refund'],'zen_ids':['net']}
        for b in state['review'][:2]:b.update(state='linked',group=group,split_ids=['net'],linked={'settlement':True,'categories':['Income'],'date':base['date']},reconciliation_status='synced')
        z['linked_bank_id']='refund'
        route.fulfill(content_type='application/json',body='{"state":"linked"}')
    def undo(route):
        assert route.request.method=='DELETE';calls.append('undo')
        for b in state['review'][:2]:b.update(state='pending',group=None,split_ids=[],linked=None,reconciliation_status='new')
        z['linked_bank_id']=None
        route.fulfill(content_type='application/json',body='{"state":"pending"}')
    page.route('**/api/reconciliation-groups',save);page.route('**/api/review/expense/link',undo)
    page.goto(f'chrome-extension://{identifier}/app.html#attention')
    form=page.locator('.settlement-form');form.locator('summary').click()
    form.get_by_role('button',name='Сохранить группу',exact=True).click()
    expect(form).to_contain_text('Выберите связанные записи');assert not calls
    for id in ['expense','refund','net']:form.locator(f'[data-member="{id}"]').check()
    expect(form).to_contain_text('Разница: 0')
    form.get_by_role('button',name='Сохранить группу',exact=True).click()
    expect(page.locator('.attention-operation')).to_have_count(2)
    paired=page.locator('.attention-operation').filter(has_text='Взаиморасчёт · связь подтверждена')
    expect(paired).to_contain_text('Банк: 2 · ZenMoney: 1')
    page.locator('#hide-reconciled').check();expect(page.locator('.attention-operation')).to_have_count(1)
    page.locator('#hide-reconciled').uncheck();paired.locator('summary').click()
    paired.get_by_role('button',name='Отменить весь взаиморасчёт').click()
    expect(page.locator('.attention-operation')).to_have_count(4)
    assert calls==['save','undo'];page.close()


@pytest.mark.parametrize('source',['bank','zenmoney'])
def test_exclude_last_unmatched_record_and_restore_from_registry(browser,source):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True},recipient_profiles=[],outbox=[],excluded_zen_ids=[])
    state['bank_accounts']=[{'id':'ba','bank':'bank','account':'1234','currency':'VND','zenmoney_account':'za'}]
    state['zenmoney_accounts']=[{'id':'za','title':'Bank','instrument':1}]
    bank={'id':'b','bank_account':'ba','date':'2026-10-01','amount_minor':-100,'currency':'VND','description':'Correction',
          'raw':{},'state':'pending','mapped':True,'candidates':[],'category_suggestion':None,'linked':None,'reconciliation_status':'new'}
    z={'id':'z','date':'2026-10-01','amount_minor':'-100','currency':'VND','payee':'Debt','categories':[],
       'transfer':False,'linked_bank_id':None,'same_day_bank_ids':[]}
    state['review']=[bank] if source=='bank' else []
    day={'bank_account':'ba','date':'2026-10-01','bank_count':1 if source=='bank' else 0,'zen_count':1 if source=='zenmoney' else 0,
         'equal':False,'resolved':False,'pairs':[],'preview_pairs':[],'bulk_eligible':False,'snapshot':'a'*64,
         'bank_ids':['b'] if source=='bank' else [],'zen_rows':[z] if source=='zenmoney' else []}
    state['day_review']=[day];calls=[]
    page=context.new_page();page.route('**/api/state',lambda route:route.fulfill(content_type='application/json',body=json.dumps(state)))
    def change(route):
        excluded=route.request.method=='PUT';calls.append(excluded)
        if source=='bank':bank.update(state='ignored' if excluded else 'pending',reconciliation_status='ignored' if excluded else 'new')
        else:z['excluded']=excluded;state['excluded_zen_ids']=['z'] if excluded else []
        day.update(resolved=excluded,equal=excluded)
        route.fulfill(content_type='application/json',body=json.dumps({'excluded':excluded}))
    page.route('**/api/exclusions/**',change)
    page.goto(f'chrome-extension://{identifier}/app.html#attention')
    expect(page.locator('.attention-day')).to_have_count(1)
    page.locator('.attention-operation > summary').click()
    page.get_by_role('button',name='Игнорировать',exact=True).click()
    expect(page.locator('.attention-day')).to_have_count(0)
    expect(page.locator('.excluded-records')).to_contain_text('Исключены из сведения · 1')
    page.reload();expect(page.locator('.attention-day')).to_have_count(0)
    page.locator('.excluded-records summary').click()
    page.locator('.excluded-records').get_by_role('button',name='Вернуть в проверку').click()
    expect(page.locator('.attention-day')).to_have_count(1)
    expect(page.locator('.excluded-records')).to_have_count(0)
    assert calls==[True,False];page.close()


def test_exclude_from_zenmoney_history_without_bank_mapping(browser):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True},review=[],day_review=[],bank_accounts=[],outbox=[],excluded_zen_ids=[])
    tx={'id':'adjustment','date':'2026-10-01','income':10,'outcome':0,'comment':'Adjustment','tag':[]}
    page=context.new_page();calls=[]
    page.route('**/api/state',lambda route:route.fulfill(json=state))
    page.route('**/api/zenmoney/history?*',lambda route:route.fulfill(json={'items':[tx],'total':1,'offset':0,'limit':100,'has_more':False}))
    def change(route):
        excluded=route.request.method=='PUT';calls.append(excluded);state['excluded_zen_ids']=['adjustment'] if excluded else []
        route.fulfill(json={'excluded':excluded})
    page.route('**/api/exclusions/zenmoney/adjustment',change)
    page.goto(f'chrome-extension://{identifier}/app.html#zenmoney/transactions')
    row=page.locator('[data-transaction-id="adjustment"]')
    row.locator('summary').click();row.get_by_role('button',name='Игнорировать',exact=True).click()
    expect(row.locator('.transaction-labels')).to_contain_text('Не требует сведения')
    row.locator('summary').click();row.get_by_role('button',name='Вернуть в проверку').click()
    expect(row.locator('.transaction-labels')).not_to_contain_text('Не требует сведения')
    assert calls==[True,False];page.close()


@pytest.mark.parametrize('scope',['day','all'])
def test_repeated_amount_pairs_are_separate_and_saved_together(browser,scope):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True},review=[],day_review=[],recipient_profiles=[],outbox=[])
    state['bank_accounts']=[{'id':'ba','bank':'bank','account':'1234','currency':'VND','zenmoney_account':'za'}]
    state['zenmoney_accounts']=[{'id':'za','title':'Bank','instrument':1}]
    for day in ['2026-10-01','2026-10-02']:
        banks=[];zs=[];pairs=[]
        for i in range(2):
            bid=f'{day}-b{i}';zid=f'{day}-z{i}'
            z={'id':zid,'date':day,'amount_minor':'-100','currency':'VND','payee':f'Shop {i}','categories':['Food'],
               'created':1700000000+i*100,'transfer':False,'linked_bank_id':None,'same_day_bank_ids':[]};zs.append(z)
            b={'id':bid,'bank_account':'ba','date':day,'amount_minor':-100,'currency':'VND','description':f'Payment {i}',
               'raw':{'date':f'{day} 0{i+8}:00:00'},'state':'pending','mapped':True,'candidates':[],'category_suggestion':None,'linked':None,'reconciliation_status':'possible'}
            banks.append(b);pairs.append({'bank_id':bid,'zen_id':zid,'order':'chronological','order_tied':True})
        for b in banks:b['candidates']=zs
        state['review'].extend(banks)
        state['day_review'].append({'bank_account':'ba','date':day,'bank_count':2,'zen_count':2,'equal':True,'resolved':False,'pairs':pairs,
            'preview_pairs':pairs,'ambiguous':0,'bulk_eligible':True,'snapshot':'a'*64,'bank_ids':[b['id'] for b in banks],'zen_rows':zs})
    page=context.new_page();calls=[]
    page.route('**/api/state',lambda route:route.fulfill(json=state))
    def save(route):
        data=json.loads(route.request.post_data);calls.append(data)
        chosen=[d['date'] for d in data['days']]
        assert sorted(chosen)==(['2026-10-01'] if scope=='day' else ['2026-10-01','2026-10-02'])
        for d in state['day_review']:
            if d['date'] not in chosen:continue
            for pair in d['preview_pairs']:
                b=next(b for b in state['review'] if b['id']==pair['bank_id']);z=next(z for z in d['zen_rows'] if z['id']==pair['zen_id'])
                z['linked_bank_id']=b['id'];b.update(state='linked',linked=z,linked_id=z['id'],reconciliation_status='synced')
            d.update(preview_pairs=[],resolved=True)
        route.fulfill(json={'days':len(chosen),'linked':2*len(chosen)})
    page.route('**/api/review-pairs/confirm',save)
    page.goto(f'chrome-extension://{identifier}/app.html#attention')
    expect(page.locator('.attention-operation')).to_have_count(4)
    expect(page.locator('.attention-operation').first).to_contain_text('Пара по порядку · не сохранена')
    first=page.locator('.attention-operation').first;first.locator(':scope > summary').click()
    expect(first).to_contain_text('Запись ZenMoney создана:');expect(first).to_contain_text('стабильный порядок идентификаторов')
    assert not calls
    if scope=='all':
        page.get_by_role('button',name='Сохранить пары всех показанных дней · 4',exact=True).click()
        expect(page.locator('.pairs-review-pair')).to_have_count(4)
        expect(page.locator('.pairs-review-list')).to_contain_text('Одинаковые отметки времени')
        assert not calls
        page.get_by_role('button',name='Подтвердить и сохранить · 4',exact=True).click()
    else:page.locator('[data-date="2026-10-01"].attention-day').get_by_role('button',name='Сохранить пары дня · 2',exact=True).click()
    expect(page.locator('.attention-operation')).to_have_count(0 if scope=='all' else 2)
    assert len(calls)==1;page.close()


def test_mass_preview_scrolls_all_pairs_and_stale_save_is_not_retried(browser):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True},review=[],day_review=[],recipient_profiles=[],outbox=[])
    state['bank_accounts']=[{'id':'ba','bank':'bank','account':'1234','currency':'VND','zenmoney_account':'za'}]
    state['zenmoney_accounts']=[{'id':'za','title':'Bank','instrument':1}]
    for i in range(25):
        day=f'2026-10-{i+1:02}'
        z={'id':f'z{i}','date':day,'amount_minor':'-100','currency':'VND','payee':f'Shop {i}','categories':['Food'],
           'transfer':False,'linked_bank_id':None,'same_day_bank_ids':[]}
        b={'id':f'b{i}','bank_account':'ba','date':day,'amount_minor':-100,'currency':'VND','description':f'Payment {i}',
           'raw':{},'state':'pending','mapped':True,'candidates':[z],'category_suggestion':None,'linked':None,'reconciliation_status':'possible'}
        state['review'].append(b);state['day_review'].append({'bank_account':'ba','date':day,'bank_count':1,'zen_count':1,'equal':True,'resolved':False,
            'pairs':[],'preview_pairs':[{'bank_id':b['id'],'zen_id':z['id']}],'snapshot':'a'*64,'bank_ids':[b['id']],'zen_rows':[z]})
    page=context.new_page();page.set_viewport_size({'width':480,'height':700});calls=[]
    page.route('**/api/state',lambda route:route.fulfill(json=state))
    def stale(route):
        data=json.loads(route.request.post_data);calls.append(data)
        assert len(data['days'])==25 and all(d['snapshot']=='a'*64 for d in data['days'])
        route.fulfill(status=409,json={'detail':'Данные дня изменились; обновите список'})
    page.route('**/api/review-pairs/confirm',stale)
    page.goto(f'chrome-extension://{identifier}/app.html#attention')
    page.get_by_role('button',name='Сохранить пары всех показанных дней · 25',exact=True).click()
    expect(page.locator('.pairs-review-pair')).to_have_count(25)
    scroller=page.locator('.pairs-review-list')
    assert scroller.evaluate('(e)=>e.scrollHeight>e.clientHeight')
    page.locator('.pairs-review-pair').last.scroll_into_view_if_needed()
    expect(page.locator('.pairs-review-pair').last).to_be_visible()
    assert page.locator('.pairs-review-dialog').evaluate('(e)=>e.scrollWidth<=e.clientWidth')
    assert not calls
    for d in state['day_review']:d['snapshot']='b'*64
    page.get_by_role('button',name='Подтвердить и сохранить · 25',exact=True).click()
    expect(page.locator('#feedback')).to_contain_text('Данные дня изменились')
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert len(calls)==1 and all(b['state']=='pending' for b in state['review'])
    page.close()


@pytest.mark.parametrize('view',['attention','review'])
def test_category_picker_prioritizes_history_and_saves_local_choice(browser,view):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True},recipient_profiles=[],outbox=[])
    state['bank_accounts']=[{'id':'ba','bank':'bank','account':'1234','currency':'VND','zenmoney_account':'za'}]
    state['zenmoney_accounts']=[{'id':'za','title':'Bank','instrument':1}]
    state['categories']=[{'id':id,'title':title} for id,title in [('food','Продукты'),('taxi','Такси'),('sport','Спорт'),('auto','Автомобиль')]]+[{'id':'deleted','title':'Удалена','deleted':True}]
    b={'id':'b','bank_account':'ba','date':'2026-10-01','amount_minor':-100,'currency':'VND','description':'Shop','raw':{},
       'state':'pending','mapped':True,'candidates':[],'category_suggestion':None,'category_draft':None,'linked':None,'reconciliation_status':'new',
       'category_ranking':[{'id':'taxi','title':'Такси','count':1},{'id':'food','title':'Продукты','count':5}]}
    z={'id':'z','date':b['date'],'amount_minor':'-100','currency':'VND','payee':'Shop','categories':[],'tag_ids':[],'transfer':False,'linked_bank_id':'b','category_draft':None,'category_ranking':b['category_ranking'],'same_day_bank_ids':['b']}
    b.update(state='linked',linked_id='z',linked=z,reconciliation_status='synced')
    state['review']=[b];state['day_review']=[{'bank_account':'ba','date':b['date'],'bank_count':1,'zen_count':1,'equal':True,'resolved':True,
        'pairs':[],'preview_pairs':[],'snapshot':'a'*64,'bank_ids':['b'],'zen_rows':[z]}]
    page=context.new_page();calls=[]
    page.route('**/api/state',lambda route:route.fulfill(json=state))
    def save(route):
        body=json.loads(route.request.post_data);calls.append(body)
        assert route.request.method=='PUT'
        id=body['category_id'];z['category_draft']={'id':id,'title':next(c['title'] for c in state['categories'] if c['id']==id),'valid':True} if id else None
        route.fulfill(json=body)
    page.route('**/api/zenmoney/transactions/z/category',save)
    page.goto(f'chrome-extension://{identifier}/app.html#{view}')
    def open_row():
        if page.url.endswith('#review'):page.locator('.ledger-summary').first.click()
        else:expect(page.locator('.attention-operation').first).not_to_have_attribute('open','')
    open_row();picker=page.locator('.category-picker');expect(picker).to_be_visible()
    expect(picker.locator('.category-priorities')).to_be_visible()
    expect(picker.locator('.category-priorities')).to_contain_text('Предполагаемые категории: Продукты · 5; Такси · 1')
    expect(picker.locator('.category-priorities strong')).to_have_count(2)
    picker.locator('.ts-control').click()
    expect(picker.locator('.category-option')).to_have_count(5)
    assert picker.locator('.category-option').evaluate_all('(xs)=>xs.map(x=>x.dataset.categoryId)')==['food','taxi','auto','sport','']
    expect(picker.locator('.category-option strong')).to_have_count(2)
    expect(picker.locator('[data-category-id="food"]')).to_contain_text('В истории: 5')
    expect(picker.locator('[data-category-id="taxi"]')).to_contain_text('один пример')
    assert picker.locator('[data-category-id="food"] strong').evaluate('(e)=>Number(getComputedStyle(e).fontWeight)')>=700
    search=picker.get_by_label('Поиск по категориям',exact=True)
    search.fill('спо')
    expect(picker.locator('.category-option:visible')).to_have_count(1)
    expect(picker.locator('.category-option:visible')).to_have_text('Спорт')
    search.fill('НетТакойКатегории');expect(picker.locator('.no-results')).to_be_visible()
    search.fill('');search.press('Escape');expect(picker.locator('.ts-dropdown')).not_to_be_visible()
    picker.locator('.ts-control').click();page.locator('#title').click()
    expect(picker.locator('.ts-dropdown')).not_to_be_visible()
    picker.locator('.ts-control').click();picker.locator('[data-category-id="sport"]').click();assert not calls
    with page.expect_response(lambda r:r.url.endswith('/api/zenmoney/transactions/z/category') and r.request.method=='PUT'):
        picker.get_by_role('button',name='Сохранить категорию',exact=True).click()
    if view=='attention':
        expect(page.locator('.attention-day')).to_have_count(0)
        expect(page.locator('.pending-categories')).to_contain_text('Категории выбраны локально: 1')
        page.locator('.pending-categories a').click()
    page.reload();open_row();expect(picker.locator('.ts-control .item')).to_have_text('Спорт')
    expect(picker).to_contain_text('Категория сохранена локально')
    picker.locator('.ts-control').click();picker.locator('[data-category-id=""]').click()
    with page.expect_response(lambda r:r.url.endswith('/api/zenmoney/transactions/z/category') and r.request.method=='PUT'):
        picker.get_by_role('button',name='Сохранить категорию',exact=True).click()
    assert calls==[{'category_id':'sport','expected_tags':[]},{'category_id':None,'expected_tags':[]}]
    page.goto(f'chrome-extension://{identifier}/app.html#attention')
    expect(page.locator('.attention-day')).to_have_count(1)
    page.close()


def test_attention_inline_category_and_pair_confirmation_share_one_day(browser):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True},recipient_profiles=[],outbox=[])
    state['bank_accounts']=[{'id':'ba','bank':'bank','account':'1234','currency':'VND','zenmoney_account':'za'}]
    state['zenmoney_accounts']=[{'id':'za','title':'Bank','instrument':1}];state['categories']=[{'id':'food','title':'Еда'}]
    common={'bank_account':'ba','date':'2026-10-01','currency':'VND','raw':{},'state':'pending','mapped':True,'linked':None,'category_suggestion':None,'category_draft':None}
    z={'id':'z','date':common['date'],'amount_minor':'-100','currency':'VND','payee':'Known shop','categories':[],'tag_ids':[],'transfer':False,'linked_bank_id':None}
    pair={**common,'id':'paired','description':'Known shop','amount_minor':-100,'candidates':[z],'reconciliation_status':'possible'}
    new={**common,'id':'new','description':'New payment','amount_minor':-200,'candidates':[],'reconciliation_status':'new'}
    state['review']=[pair,new]
    day={'bank_account':'ba','date':common['date'],'bank_count':2,'zen_count':1,'equal':False,'resolved':False,'pairs':[],
         'preview_pairs':[{'bank_id':'paired','zen_id':'z'}],'snapshot':'a'*64,'bank_ids':['paired','new'],'zen_rows':[z]};state['day_review']=[day]
    page=context.new_page();calls=[]
    page.route('**/api/state',lambda route:route.fulfill(json=state))
    def save_category(route):
        assert json.loads(route.request.post_data)=={'category_id':'food','expected_tags':[]};calls.append('category')
        z['category_draft']={'id':'food','title':'Еда','valid':True};route.fulfill(json={'category_id':'food'})
    def save_pair(route):
        calls.append('pair');pair.update(state='linked',linked_id='z',linked=z,reconciliation_status='synced');z['linked_bank_id']='paired';day['preview_pairs']=[]
        route.fulfill(json={'days':1,'linked':1})
    page.route('**/api/zenmoney/transactions/z/category',save_category);page.route('**/api/review-pairs/confirm',save_pair)
    page.goto(f'chrome-extension://{identifier}/app.html#attention')
    picker=page.locator('.inline-category-picker[data-zen-id="z"]')
    expect(page.locator('.category-picker')).to_have_count(1)
    expect(picker).to_be_visible();expect(page.locator('.attention-operation[open]')).to_have_count(0)
    picker.locator('.ts-control').click();picker.locator('[data-category-id="food"]').click()
    with page.expect_response(lambda r:r.url.endswith('/api/zenmoney/transactions/z/category')):picker.get_by_role('button',name='Сохранить категорию').click()
    expect(picker).to_contain_text('Категория сохранена локально')
    page.get_by_role('button',name='Сохранить пары дня · 1',exact=True).click()
    expect(page.locator('.inline-category-picker')).to_have_count(1)
    expect(picker.locator('.ts-control .item')).to_have_text('Еда')
    assert page.url.endswith('#attention') and calls==['category','pair']
    page.set_viewport_size({'width':480,'height':700})
    expect(picker.get_by_role('button',name='Сохранить категорию')).to_be_visible()
    assert picker.evaluate('(e)=>e.scrollWidth<=e.clientWidth');page.close()


def test_attention_split_categories_target_each_zen_part(browser):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True},recipient_profiles=[],outbox=[])
    state['bank_accounts']=[{'id':'ba','bank':'bank','account':'1234','currency':'VND','zenmoney_account':'za'}]
    state['zenmoney_accounts']=[{'id':'za','title':'Bank','instrument':1}]
    state['categories']=[{'id':'food','title':'Еда'}]
    zs=[{'id':id,'date':'2026-10-01','amount_minor':amount,'currency':'VND','payee':'Shop','categories':[],
         'tag_ids':[],'transfer':False,'linked_bank_id':'b','category_draft':None} for id,amount in [('z1','-40'),('z2','-60')]]
    b={'id':'b','bank_account':'ba','date':'2026-10-01','amount_minor':-100,'currency':'VND','description':'Shop','raw':{},
       'state':'linked','mapped':True,'candidates':[],'linked_id':None,'split_ids':['z1','z2'],'reconciliation_status':'synced',
       'linked':{'categories':[],'missing_category':True},'group':{'kind':'split','zen_ids':['z1','z2'],'bank_ids':['b']}}
    state['review']=[b]
    state['day_review']=[{'bank_account':'ba','date':b['date'],'bank_ids':['b'],'zen_rows':zs,'resolved':True,'equal':False,'preview_pairs':[]}]
    page=context.new_page();calls=[]
    page.route('**/api/state',lambda route:route.fulfill(json=state))
    def save(route):
        calls.append(route.request.url)
        assert json.loads(route.request.post_data)=={'category_id':'food','expected_tags':[]}
        zs[0]['category_draft']={'id':'food','title':'Еда','valid':True}
        route.fulfill(json={'category_id':'food'})
    page.route('**/api/zenmoney/transactions/z1/category',save)
    page.goto(f'chrome-extension://{identifier}/app.html#attention')
    expect(page.locator('.category-picker')).to_have_count(2)
    page.locator('[data-zen-id="z1"] select').evaluate('(s)=>{document.body.dataset.destroyedPickers="0";s.tomselect.on("destroy",()=>document.body.dataset.destroyedPickers=String(Number(document.body.dataset.destroyedPickers)+1));}')
    page.locator('#refresh').click()
    expect(page.locator('body')).to_have_attribute('data-destroyed-pickers','1')
    expect(page.locator('.category-picker')).to_have_count(2)
    expect(page.locator('.category-priorities').first).to_have_text('Нет подходящих подтверждённых примеров для подсказки категории.')
    picker=page.locator('[data-zen-id="z1"]')
    picker.locator('.ts-control').click()
    page.locator('[data-zen-id="z2"] .ts-control').click()
    expect(picker.locator('.ts-dropdown')).not_to_be_visible()
    expect(page.locator('[data-zen-id="z2"] .ts-dropdown')).to_be_visible()
    picker.locator('.ts-control').click();picker.locator('[data-category-id="food"]').click()
    picker.get_by_role('button',name='Сохранить категорию').click()
    expect(picker).to_contain_text('Категория сохранена локально')
    expect(page.locator('[data-zen-id="z2"] .ts-control .item')).to_have_count(0)
    assert len(calls)==1
    page.close()


def test_category_send_preview_cancel_confirm_and_verify(browser):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True,'category_write':True},category_draft_count=2,category_sends=[])
    items=[{'zen_id':id,'snapshot':letter*64,'date':'2026-05-26','income':0,'outcome':6000,'account':'Bank',
            'instrument':1,'payee':'Shop','before_categories':[],'category':'Услуги','error':None} for id,letter in [('z1','a'),('z2','b')]]
    state['zenmoney_instruments']=[{'id':1,'shortTitle':'VND'}]
    page=context.new_page();calls=[]
    page.route('**/api/state',lambda route:route.fulfill(json=state))
    page.route('**/api/category-updates/preview',lambda route:route.fulfill(json={'items':items}))
    def send(route):
        calls.append(json.loads(route.request.post_data))
        state['category_sends']=[{'zen_id':'z1','state':'uncertain','error':'Verify'}]
        route.fulfill(json={'results':state['category_sends']})
    page.route('**/api/category-updates/send',send)
    def verify(route):
        state.update(category_draft_count=0,category_sends=[{'zen_id':'z1','state':'sent','error':None}]);route.fulfill(json={'results':state['category_sends']})
    page.route('**/api/category-updates/verify',verify)
    page.goto(f'chrome-extension://{identifier}/app.html#attention')
    page.locator('#category-send').click();dialog=page.locator('.category-send-dialog')
    expect(dialog).to_be_visible();expect(dialog.locator('.category-send-item')).to_have_count(2)
    expect(dialog).to_contain_text('Без категории → Услуги')
    dialog.get_by_role('button',name='Отмена',exact=True).click();assert not calls
    page.locator('#category-send').click();dialog.locator('input[type=checkbox]').nth(1).uncheck()
    dialog.get_by_role('button',name='Подтвердить отправку · 1',exact=True).click()
    expect(page.locator('#category-verify')).to_be_visible()
    assert calls==[{'items':[{'zen_id':'z1','snapshot':'a'*64}]}]
    page.locator('#category-verify').click()
    expect(page.locator('#category-verify')).to_be_hidden();expect(page.locator('#category-send')).to_be_disabled()
    page.close()


@pytest.mark.parametrize('case,expected',[
    ('unknown','adjust'),('ranked',''),('saved','food'),('conflict',''),
    ('deleted',''),('missing',''),('duplicate',''),('categorized',''),('transfer',''),
])
def test_adjustment_default_is_unsaved_and_never_replaces_existing_choice(browser,case,expected):
    context,identifier=browser
    page=context.new_page()
    page.goto(f'chrome-extension://{identifier}/app.html')
    connect(page)
    expect(page.locator('#feedback')).to_have_text('Данные обновлены.')
    page.evaluate("""async ({case:mode})=>{
      const {categoryPicker}=await import('./category-picker.js');
      const row={id:'default-test',categories:[],tag_ids:[],transfer:false,category_ranking:[]};
      const categories=[{id:'adjust',title:'Корректировка'},{id:'food',title:'Еда'}];
      if(mode==='ranked')row.category_ranking=[{id:'food',count:2}];
      if(mode==='saved'||mode==='conflict')row.category_draft={id:'food',valid:mode==='saved'};
      if(mode==='deleted')categories[0].deleted=true;
      if(mode==='missing')categories.shift();
      if(mode==='duplicate')categories.push({id:'adjust2',title:'Корректировка'});
      if(mode==='categorized'){row.categories=['Еда'];row.tag_ids=['food'];}
      if(mode==='transfer')row.transfer=true;
      window.categoryRequests=[];
      const host=document.createElement('div');host.id='default-test-host';
      document.body.append(host);
      host.append(categoryPicker(row,{categories},action=>window.categoryRequests.push(action())));
    }""",{'case':case})
    picker=page.locator('#default-test-host .category-picker')
    assert picker.locator('select').input_value()==expected
    assert page.evaluate('window.categoryRequests')==[]
    if case=='unknown':
        expect(picker).to_contain_text('По умолчанию выбрана «Корректировка»')
        expect(picker).to_contain_text('Выбор ещё не сохранён')
        picker.get_by_role('button',name='Сохранить категорию',exact=True).click()
        assert page.evaluate('window.categoryRequests')==[{'path':'/api/zenmoney/transactions/default-test/category','method':'PUT','body':{'category_id':'adjust','expected_tags':[]}}]
        picker.locator('.ts-control').click()
        picker.locator('[data-category-id="food"]').click()
        picker.get_by_role('button',name='Сохранить категорию',exact=True).click()
        assert page.evaluate('window.categoryRequests.at(-1).body.category_id')=='food'
    page.close()



def test_bank_workspace_scopes_whole_days_and_keeps_drafts_until_sent(browser):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True,'category_write':True},
                 review=[],day_review=[],outbox=[],recipient_profiles=[],category_sends=[],category_draft_count=2)
    state['bank_accounts']=[{'id':id,'bank':bank,'account':id,'currency':'VND','zenmoney_account':'zen-'+id}
        for id,bank in [('vt1','vietinbank'),('vt2','vietinbank'),('other','other-bank'),('unmapped','vietinbank')]]
    state['bank_accounts'][-1]['zenmoney_account']=None
    state['zenmoney_accounts']=[{'id':'zen-'+id,'title':id,'instrument':1} for id in ['vt1','vt2','other','unrelated']]
    state['categories']=[{'id':'food','title':'Еда'}]
    def pair(id,account,date,draft=False):
        z={'id':'z-'+id,'date':date,'amount_minor':'-100','currency':'VND','payee':id,'categories':[] if draft else ['Еда'],
           'tag_ids':[] if draft else ['food'],'transfer':False,'linked_bank_id':id,'same_day_bank_ids':[id],
           'category_draft':{'id':'food','title':'Еда','valid':True} if draft else None}
        b={'id':id,'bank_account':account,'date':date,'amount_minor':-100,'currency':'VND','description':id,'raw':{},
           'state':'linked','mapped':True,'linked':z,'linked_id':z['id'],'split_ids':[],'candidates':[],'reconciliation_status':'synced'}
        state['review'].append(b)
        day=next((d for d in state['day_review'] if d['bank_account']==account and d['date']==date),None)
        if day is None:
            day={'bank_account':account,'date':date,'bank_ids':[],'zen_rows':[],'equal':True,'resolved':True,'pairs':[],'preview_pairs':[]}
            state['day_review'].append(day)
        day['bank_ids'].append(id);day['zen_rows'].append(z)
        return z
    draft=pair('waiting','vt1','2026-10-01',True)
    pair('same-day-complete','vt1','2026-10-01')
    pair('complete-day','vt1','2026-10-02')
    pair('second-account','vt2','2026-10-03')
    pair('foreign-draft','other','2026-10-01',True)
    state['review'].append({'id':'no-mapping','bank_account':'unmapped','date':'2026-10-04','amount_minor':-100,'currency':'VND',
        'description':'No mapping','raw':{},'state':'pending','mapped':False,'linked':None,'candidates':[],'reconciliation_status':'new'})
    page=context.new_page();calls=[]
    page.route('**/api/state',lambda route:route.fulfill(json=state))
    items=[{'zen_id':id,'snapshot':'a'*64,'date':'2026-10-01','account':'Account','income':0,'outcome':100,'instrument':1,
            'payee':id,'before_categories':[],'category':'Еда','error':None} for id in ['z-waiting','z-foreign-draft']]
    page.route('**/api/category-updates/preview',lambda route:route.fulfill(json={'items':items}))
    def send(route):
        calls.append(json.loads(route.request.post_data))
        draft.update(category_draft=None,categories=['Еда'],tag_ids=['food'])
        state['category_draft_count']=1
        route.fulfill(json={'results':[{'zen_id':'z-waiting','state':'sent'}]})
    page.route('**/api/category-updates/send',send)
    page.goto(f'chrome-extension://{identifier}/app.html#banks')
    connect(page)
    expect(page.locator('#status')).to_have_text('Данные обновлены.')
    page.locator('nav a[href="#banks"]').click()
    expect(page.locator('nav a[href="#attention"]')).to_have_count(0)
    page.locator('#bank-list a[href="#banks/vietinbank"]').click()
    expect(page.locator('#title')).to_have_text('VietinBank')
    expect(page.locator('.attention-day')).to_have_count(4)
    expect(page.locator('#review')).not_to_contain_text('foreign-draft')
    assert page.locator('#review-account option').evaluate_all('(xs)=>xs.map(x=>x.value)')==['','vt1','vt2','unmapped']
    page.locator('#bank-attention-only').check()
    expect(page.locator('.attention-day')).to_have_count(2)
    expect(page.locator('.attention-day[data-date="2026-10-01"]')).to_contain_text('same-day-complete')
    page.screenshot(path='/results/bank-workspace.png',full_page=True)
    page.locator('#hide-reconciled').check()
    expect(page.locator('#review')).to_contain_text('waiting')
    expect(page.locator('#review')).not_to_contain_text('same-day-complete')
    page.locator('#review-account').select_option('vt1')
    expect(page.locator('.attention-day')).to_have_count(1)
    expect(page.locator('#category-send')).to_have_text('Отправить категории в ZenMoney · 1')
    assert page.locator('#feedback').inner_text()=='Данные обновлены.'
    page.locator('#category-send').click()
    expect(page.locator('.category-send-item')).to_have_count(1)
    expect(page.locator('.category-send-dialog')).not_to_contain_text('z-foreign-draft')
    page.get_by_role('button',name='Подтвердить отправку · 1',exact=True).click()
    expect(page.locator('.attention-day')).to_have_count(0)
    assert calls==[{'items':[{'zen_id':'z-waiting','snapshot':'a'*64}]}]
    state['category_sends']=[{'zen_id':'z-waiting','state':'uncertain'}]
    page.locator('#refresh').click()
    expect(page.locator('.attention-day')).to_have_count(1)
    expect(page.locator('#review')).to_contain_text('ожидает отправки или проверки результата')
    state['category_sends'][0]['state']='sent'
    page.locator('#refresh').click()
    expect(page.locator('.attention-day')).to_have_count(0)
    page.locator('#bank-attention-only').uncheck()
    page.locator('#hide-reconciled').uncheck()
    expect(page.locator('.attention-day')).to_have_count(2)
    page.locator('nav a[href="#banks"]').click()
    page.locator('#bank-list a[href="#banks/other-bank"]').click()
    expect(page.locator('.attention-day')).to_have_count(1)
    expect(page.locator('#review')).to_contain_text('foreign-draft')
    expect(page.locator('#review')).not_to_contain_text('same-day-complete')
    page.close()


def test_new_operations_categories_and_scoped_creation_confirmation(browser):
    context,identifier=browser
    state=context.request.get(BASE+'/api/state',headers={'Authorization':'Bearer '+TOKEN}).json()
    state.update(mode='live',capabilities={'write':False,'local_review':True,'category_write':True,'create':True},
                 review=[],day_review=[],outbox=[],recipient_profiles=[],category_sends=[],category_draft_count=0)
    state['bank_accounts']=[{'id':'ba','bank':'vietinbank','account':'1234','currency':'VND','zenmoney_account':'za'}]
    state['zenmoney_accounts']=[{'id':'za','title':'VietinBank account','instrument':1}]
    state['categories']=[{'id':'food','title':'Еда'},{'id':'toys','title':'Игрушки'},{'id':'adjust','title':'Корректировка'}]
    for id in ['known','mixed','unknown']:
        state['review'].append({'id':id,'bank_account':'ba','date':'2026-10-11','amount_minor':-100,'currency':'VND','description':id,
            'raw':{},'state':'pending','mapped':True,'candidates':[],'linked':None,'reconciliation_status':'new','creation_job':None,
            'creation_draft':{'category_id':'food','title':'Еда','source':'auto','valid':True} if id=='known' else None,
            'creation_ranking':[{'id':'food','count':3},{'id':'toys','count':2}] if id=='mixed' else [{'id':'food','count':3}] if id=='known' else []})
    state['review'][1]['raw']={'details':{'corresponsiveAccount':'VQR123','corresponsiveName':'Shop <img src=x>'}}
    state['day_review']=[{'bank_account':'ba','date':'2026-10-11','bank_ids':['known','mixed','unknown'],'zen_rows':[],
                         'bank_count':3,'zen_count':0,'equal':False,'resolved':False,'pairs':[],'preview_pairs':[]}]
    page=context.new_page();writes=[];saved=[];created=[]
    page.route('**/api/state',lambda route:route.fulfill(json=state))
    page.route('**/api/new-operations/prepare',lambda route:route.fulfill(json={'status':'prepared'}))
    def save(route):
        id=route.request.url.split('/')[-2];body=json.loads(route.request.post_data);saved.append((id,body))
        row=next(r for r in state['review'] if r['id']==id)
        category=body['category_id'];row['creation_draft']={'category_id':category,'title':'Игрушки','valid':True,'source':'manual'}
        route.fulfill(json=body)
    page.route('**/api/new-operations/*/category',save)
    def create(route):
        assert json.loads(route.request.post_data)['bank']=='vietinbank'
        ids=[]
        for row in state['review']:
            if row['state']=='pending' and row['creation_draft']:
                row['state']='queued';row['creation_job']={'state':'prepared'};ids.append(row['id'])
        created.extend(ids);route.fulfill(json={'created':ids,'skipped':[]})
    page.route('**/api/new-operations/create',create)
    def preview(route):
        assert json.loads(route.request.post_data)['bank']=='vietinbank'
        route.fulfill(json={'items':[{'bank_id':r['id'],'date':r['date'],'amount_minor':r['amount_minor'],'currency':'VND','description':r['description'],
            'account':'VietinBank account','category':(r['creation_draft'] or {}).get('title'),'source':(r['creation_draft'] or {}).get('source'),
            'error':None if r['creation_draft'] else 'Выберите категорию','snapshot':'a'*64} for r in state['review'] if r['creation_job']]})
    page.route('**/api/new-operations/preview',preview)
    def send(route):
        writes.append(json.loads(route.request.post_data));route.fulfill(json={'results':[{'state':'sent'}]})
    page.route('**/api/new-operations/send',send)
    page.goto(f'chrome-extension://{identifier}/app.html#banks/vietinbank')
    connect(page);page.goto(f'chrome-extension://{identifier}/app.html#banks/vietinbank')
    expect(page.locator('#creation-create')).to_be_enabled()
    expect(page.locator('.category-picker')).to_have_count(3)
    expect(page.locator('.attention-day h2')).to_have_text('11 октября 2026 · VietinBank account')
    expect(page.locator('.attention-day summary .beneficiary-account')).to_have_count(1)
    expect(page.locator('.attention-day summary .beneficiary-account')).to_have_text('Beneficiary account: VQR123 · Shop <img src=x>')
    expect(page.locator('.attention-day summary img')).to_have_count(0)
    known=page.locator('[data-zen-id="known"]')
    expect(known.locator('.ts-control .item')).to_have_text('Еда')
    expect(known).to_contain_text('Назначена автоматически по точному описанию платежа')
    unknown=page.locator('[data-zen-id="unknown"]')
    expect(unknown.locator('.ts-control .item')).to_have_count(0)
    expect(page.locator('#creation-send')).to_be_disabled()
    page.locator('#creation-create').click()
    expect(page.locator('#status')).to_contain_text('Создано локальных записей: 1')
    assert created==['known'] and not writes
    page.locator('#creation-send').click()
    expect(page.get_by_role('dialog',name='Создание новых операций')).to_be_visible()
    expect(page.get_by_role('button',name='Отправить в ZenMoney · 1',exact=True)).to_be_enabled()
    expect(page.locator('.creation-item input:disabled')).to_have_count(0)
    page.get_by_role('button',name='Отмена',exact=True).click();assert not writes
    for id in ['mixed','unknown']:
        picker=page.locator(f'[data-zen-id="{id}"]');picker.locator('.ts-control').click()
        if id=='mixed':expect(picker.locator('.category-option strong')).to_have_count(2)
        picker.locator('[data-category-id="toys"]').click()
        if id=='mixed':picker.get_by_label('Всегда использовать выбранную категорию',exact=False).check()
        with page.expect_response(lambda r:'/category' in r.url and '/new-operations/' in r.url):
            picker.get_by_role('button',name='Сохранить категорию',exact=True).click()
        expect(page.locator('#creation-create')).to_be_enabled()
    assert len(saved)==2 and not writes
    assert saved[0][1]['remember_recipient'] is True
    assert 'remember_recipient' not in saved[1][1]
    page.locator('#creation-create').click()
    expect(page.locator('#status')).to_contain_text('Создано локальных записей: 2')
    assert set(created)=={'known','mixed','unknown'} and not writes
    page.locator('#creation-send').click()
    page.get_by_role('button',name='Отправить в ZenMoney · 3',exact=True).click()
    expect(page.locator('#status')).to_contain_text('1 успешно')
    assert len(writes)==1 and {i['bank_id'] for i in writes[0]['items']}=={'known','mixed','unknown'}
    page.close()
