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
    expect(page.locator('#accounts')).to_contain_text('Сначала импортируйте')
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
    page.locator('#accounts select').select_option(label='Демо · VND')
    page.get_by_role('button',name='Сопоставить',exact=True).click()
    expect(page.locator('#status')).to_have_text('Данные обновлены.')
    page.locator('nav a[href="#review"]').click()
    page.locator('#review select').first.select_option(label='Еда')
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


@pytest.mark.parametrize('failure',['wrong_account','wrong_period','changing_total','cancel','logout','empty'])
def test_automatic_history_failures_are_persisted(browser,failure):
    context, identifier=browser
    account='88889999000'+str(2+['wrong_account','wrong_period','changing_total','cancel','logout','empty'].index(failure))
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
