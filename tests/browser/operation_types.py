"""Installed extension + actual HTTP backend, synthetic SDK remote only."""
from pathlib import Path
import json
import pytest
from playwright.sync_api import expect
from extension_flow import browser,open_app,connect,fixture_bank,TOKEN

BASE='http://127.0.0.1:8001'
AUTH={'Authorization':'Bearer '+TOKEN}
CASES=[('expense',False),('income',True),('refund',True),('transfer',False),('transfer',True),('lend',False),('borrow',True),('repay',False),('collect',True)]

def reset(context,existing=False,positive=False):
    response=context.request.post(BASE+'/api/test/reset',headers=AUTH,data={'existing':existing,'positive':positive})
    assert response.ok,response.text()

def remote(context):return context.request.get(BASE+'/api/test/remote',headers=AUTH).json()

def choose(editor,kind):
    editor.get_by_label('Тип операции',exact=True).select_option(kind)
    if kind=='transfer':editor.get_by_label('Второй счёт').select_option('second')
    elif kind in ('lend','borrow','repay','collect'):editor.get_by_label('Человек для долга').fill('Test person')
    else:
        editor.get_by_role('combobox',name='Категория для типа операции',exact=True).click()
        editor.get_by_label('Поиск категории типа операции').fill('Зар' if kind=='income' else 'Прод')
        editor.get_by_role('option',name='Зарплата' if kind=='income' else 'Продукты',exact=True).click()
    editor.get_by_role('button',name='Сохранить параметры',exact=True).click()

@pytest.mark.parametrize('kind,positive',CASES)
@pytest.mark.parametrize('existing',[False,True])
def test_bank_workspace_save_review_cancel_confirm(browser,kind,positive,existing):
    context,_=browser;reset(context,existing,positive)
    page=open_app(browser);connect(page,BASE)
    expect(page.locator('#status')).to_have_text('Данные обновлены.')
    page.goto(page.url.split('#')[0]+'#banks/vietinbank')
    expect(page.locator('#review .operation-editor')).to_have_count(2)
    expect(page.locator('#refresh')).to_be_enabled()
    editor=page.locator('#review .operation-editor').nth(1 if positive else 0)
    editor.locator('summary').click();choose(editor,kind)
    expect(page.locator('#status')).to_have_text('Данные обновлены.')
    expect(page.locator('#review .operation-editor').nth(1 if positive else 0)).to_contain_text('сохранён локально')
    assert not remote(context)['writes']
    page.reload();expect(page.locator('#review .operation-editor').nth(1 if positive else 0)).to_contain_text('сохранён локально')
    page.locator('#operation-send').click()
    dialog=page.get_by_role('dialog',name='Общая отправка в ZenMoney')
    expect(dialog).to_be_visible();expect(dialog).to_contain_text('Other VND' if kind=='transfer' else 'Test person' if kind in ('lend','borrow','repay','collect') else 'Зарплата' if kind=='income' else 'Продукты')
    check=dialog.locator('input[type=checkbox]').first.bounding_box();label=dialog.locator('label').first.bounding_box()
    assert check['width']<=20 and check['x']-label['x']<2
    assert not remote(context)['writes']
    dialog.get_by_role('button',name='Отмена',exact=True).click();assert not remote(context)['writes']
    page.locator('#operation-send').click();dialog.get_by_role('button',name='Отправить в ZenMoney · 1',exact=True).click()
    expect(page.locator('#status')).to_contain_text('1 успешно, 0 требуют внимания')
    for _ in range(60):
        if remote(context)['writes']:break
        bank.wait_for_timeout(100)
    result=remote(context);assert len(result['writes'])==1 and len(result['writes'][0])==1
    p=result['writes'][0][0]
    if existing:assert p['id']=='existing' and p['created']==10
    assert ((p['income'] if p['incomeAccount']=='cash' else 0)-(p['outcome'] if p['outcomeAccount']=='cash' else 0))==(100000 if positive else -65000)
    if kind=='transfer':assert p['outcomeAccount' if positive else 'incomeAccount']=='second'
    if kind in ('lend','borrow','repay','collect'):assert p['payee']=='Test person' and 'debt' in (p['incomeAccount'],p['outcomeAccount'])
    page.close()

def inline_pages(browser,existing=False,uncategorized=False,payee=None,candidate=False,positive=False,spa=False):
    context,identifier=browser
    response=context.request.post(BASE+'/api/test/reset',headers=AUTH,data={'existing':existing,'uncategorized':uncategorized,'payee':payee,'candidate':candidate,'positive':positive});assert response.ok
    worker=context.service_workers[0];worker.evaluate("chrome.storage.local.set({inlineEnabled:false})")
    page=open_app(browser);connect(page,BASE);expect(page.locator('#status')).to_have_text('Данные обновлены.')
    popup=context.new_page();popup.goto(f'chrome-extension://{identifier}/popup.html')
    popup.locator('#inline-enable').check();expect(popup.locator('#inline-enable')).to_be_checked()
    # The full-sync button now uses the real calendar collector. These tests start
    # with two stored operations and an empty synthetic historical response.
    html=Path('/app/tests/browser/fixtures/vietinbank.html').read_text()
    html=html.replace('<input readonly placeholder="Choose time range" value="01/09/2026 - 06/10/2026">','')
    html+=Path('/app/tests/browser/auto-bank.html').read_text()
    bank=fixture_bank(browser,html,url='https://ipay.vietinbank.vn/home?accountNo=111122223333' if spa else None)
    if spa:bank.evaluate("history.pushState({},'', '/payment-history?accountNo=111122223333')")
    bank.route('https://api-ipay.vietinbank.vn/ipay/wa/getHistTransactions',lambda r:r.fulfill(
        content_type='application/json',headers={'Access-Control-Allow-Origin':'*'},
        body=json.dumps({'error':False,'accountNo':'111122223333','currentPage':0,'pageSize':100,'totalRecords':0,'transactions':[]})))
    expect(bank.locator('zenimport-operation .operation-editor')).to_have_count(2)
    expect(bank.locator('#zenimport-status')).to_contain_text('Данные ZenMoney получены')
    return context,page,popup,bank

@pytest.mark.parametrize('existing',[False,True])
def test_bank_inline_transfer_new_staged_existing_immediate(browser,existing):
    context,page,popup,bank=inline_pages(browser,existing)
    editor=bank.locator('zenimport-operation .operation-editor').first
    editor.get_by_role('radio',name='Трансфер',exact=True).click();editor.get_by_label('Второй счёт').select_option('foreign')
    editor.get_by_label('Сумма на втором счёте').fill('2.500000000001');editor.get_by_label('Сумма на втором счёте').press('Tab')
    if existing:
        expect(bank.locator('zenimport-operation').first.locator('.status-icon')).to_have_class('status-icon good')
        for _ in range(60):
            if remote(context)['writes']:break
            bank.wait_for_timeout(100)
        writes=remote(context)['writes'];assert len(writes)==1 and writes[0][0]['id']=='existing'
        assert writes[0][0]['incomeAccount']=='foreign' and str(writes[0][0]['income'])=='2.500000000001'
    else:
        expect(bank.locator('zenimport-operation .operation-editor').first).to_have_attribute('data-operation-draft','true')
        assert not remote(context)['writes']
        bank.bring_to_front();popup.locator('#send-new').click()
        try:expect(popup.locator('#send-status')).to_contain_text('Добавлено в ZenMoney: 2.',timeout=45000)
        except AssertionError as exc:
            diagnostic=popup.evaluate("async()=>{const {autoJob:j,fullSyncJob:s}=await chrome.storage.local.get(['autoJob','fullSyncJob']);return {sync:s,capture:j&&{status:j.status,error:j.error,events:j.events.slice(-8)}}}")
            raise AssertionError(str(exc)+'; '+str(diagnostic)) from exc
        result=remote(context);assert len(result['writes'])==1 and len(result['writes'][0])==2
    bank.screenshot(path='/results/minimal-inline-transfer.png',full_page=True)
    popup.close();bank.close();page.close()


def test_minimal_popup_toggle_and_full_account_default_creation(browser):
    context,page,popup,bank=inline_pages(browser)
    assert popup.locator('button').count()==2
    assert not remote(context)['writes']
    original=bank.url
    bank.bring_to_front();popup.locator('#inline-enable').uncheck()
    expect(bank.locator('zenimport-operation')).to_have_count(0)
    bank.bring_to_front();popup.locator('#inline-enable').check()
    expect(bank.locator('tbody zenimport-operation')).to_have_count(2)
    expect(bank.locator('#zenimport-status')).to_contain_text('Данные ZenMoney получены')
    assert bank.url==original and not remote(context)['writes']
    # Ordinary new debits/credits are created without inventing a category.
    bank.bring_to_front();popup.locator('#send-new').click()
    expect(popup.locator('#send-status')).to_contain_text('Добавлено в ZenMoney: 2.',timeout=90000)
    result=remote(context);assert len(result['writes'])==1 and len(result['writes'][0])==2
    assert sorted(p['income']-p['outcome'] for p in result['writes'][0])==[-65000,100000]
    assert all(not p['tag'] for p in result['writes'][0])
    expect(bank.locator('tbody zenimport-operation').first.get_by_role('combobox',name='Категория для типа операции')).to_have_text('Выберите категорию')
    popup.reload();bank.bring_to_front();expect(popup.locator('#send-new')).to_be_enabled()
    assert len(remote(context)['writes'])==1
    popup.screenshot(path='/results/minimal-popup.png')
    popup.close();bank.close();page.close()


def test_existing_inline_category_is_sent_once_and_forgery_denied(browser):
    context,page,popup,bank=inline_pages(browser,True,True)
    card=bank.locator('tbody zenimport-operation').first
    card.get_by_role('combobox',name='Категория для типа операции',exact=True).click()
    card.get_by_role('option',name='Продукты',exact=True).click()
    expect(card.locator('.status-icon')).to_have_class('status-icon good')
    for _ in range(60):
        if remote(context)['writes']:break
        bank.wait_for_timeout(100)
    result=remote(context);assert len(result['writes'])==1 and len(result['writes'][0])==1
    assert result['writes'][0][0]['id']=='existing' and result['writes'][0][0]['tag']==['food']
    forged=popup.evaluate("chrome.runtime.sendMessage({type:'inline-category',account:'111122223333',id:'existing',category:'food'})")
    assert not forged['ok'] and len(remote(context)['writes'])==1
    popup.close();bank.close();page.close()


def test_inline_lost_reply_blocks_blind_repeat(browser):
    context,page,popup,bank=inline_pages(browser,True,True)
    assert context.request.post(BASE+'/api/test/behavior',headers=AUTH,data={'behavior':'lost_read'}).ok
    card=bank.locator('tbody zenimport-operation').first
    card.get_by_role('combobox',name='Категория для типа операции',exact=True).click();card.get_by_role('option',name='Продукты',exact=True).click()
    expect(bank.locator('#zenimport-status')).to_contain_text('не подтверждено')
    expect(card.locator('.status-icon')).to_have_attribute('title','Проверьте результат отправки')
    assert len(remote(context)['writes'])==1
    bank.reload();expect(card.locator('.status-icon')).to_have_attribute('title','Проверьте результат отправки')
    assert len(remote(context)['writes'])==1
    popup.close();bank.close();page.close()


@pytest.mark.parametrize('behavior,confirmed',[('partial',1),('before',0),('lost_response',2)])
def test_new_popup_verifies_partial_or_lost_creation_without_retry(browser,behavior,confirmed):
    context,page,popup,bank=inline_pages(browser)
    assert context.request.post(BASE+'/api/test/behavior',headers=AUTH,data={'behavior':behavior}).ok
    bank.bring_to_front();popup.locator('#send-new').click()
    expect(popup.locator('#send-status')).to_contain_text(f'Добавлено в ZenMoney: {confirmed}.',timeout=90000)
    result=remote(context);assert len(result['writes'])==1 and len(result['transactions'])==confirmed
    if confirmed<2:
        expect(popup.locator('#send-status')).to_contain_text('повтор заблокирован')
    popup.reload();bank.bring_to_front()
    expect(popup.locator('#send-new')).to_be_enabled()
    assert len(remote(context)['writes'])==1
    popup.close();bank.close();page.close()


def test_inline_debt_requires_explicit_person_not_previous_shop(browser):
    context,page,popup,bank=inline_pages(browser,True,payee='Previous shop')
    editor=bank.locator('zenimport-operation .operation-editor').first
    type_select=editor.get_by_role('radio',name='Расход',exact=True);type_select.focus();type_select.press('ArrowRight');editor.get_by_role('radio',name='Трансфер',exact=True).press('ArrowRight');expect(editor.get_by_role('radio',name='Долг',exact=True)).to_have_attribute('aria-checked','true')
    expect(editor.get_by_label('Человек для долга')).to_have_value('')
    expect(editor).to_contain_text('Укажите человека для долга.')
    assert not remote(context)['writes']
    editor.get_by_label('Человек для долга').fill('Test person');editor.get_by_label('Человек для долга').press('Tab')
    expect(bank.locator('tbody zenimport-operation').first.locator('.status-icon')).to_have_class('status-icon good')
    expect(bank.locator('tbody zenimport-operation').first.get_by_label('Действие с долгом')).to_have_value('lend')
    for _ in range(60):
        if remote(context)['writes']:break
        bank.wait_for_timeout(100)
    p=remote(context)['writes'][0][0];assert p['id']=='existing' and p['payee']=='Test person' and p['incomeAccount']=='debt'
    popup.close();bank.close();page.close()


def test_new_popup_stops_if_account_changes_during_preview(browser):
    context,page,popup,bank=inline_pages(browser)
    worker=context.service_workers[0]
    worker.evaluate("""()=>{globalThis.originalPreviewFetch=fetch;globalThis.fetch=async(...args)=>{
      const result=await originalPreviewFetch(...args);
      if(new URL(args[0]).pathname==='/api/batch/preview')await new Promise(r=>globalThis.releasePreview=r);
      return result;
    };}""")
    try:
        bank.bring_to_front();popup.locator('#send-new').click()
        for _ in range(900):
            if worker.evaluate('typeof releasePreview')=='function':break
            bank.wait_for_timeout(100)
        assert worker.evaluate('typeof releasePreview')=='function'
        bank.evaluate("history.pushState({},'', '/home')")
        worker.evaluate('releasePreview()')
        expect(popup.locator('#send-status')).to_contain_text('Отправка остановлена')
        assert not remote(context)['writes']
    finally:
        worker.evaluate('fetch=originalPreviewFetch;delete globalThis.originalPreviewFetch;delete globalThis.releasePreview')
        popup.close();bank.close();page.close()


def test_zenmoney_history_type_editor_and_foreign_currency_review(browser):
    context,_=browser;reset(context,True)
    page=open_app(browser);connect(page,BASE);expect(page.locator('#status')).to_have_text('Данные обновлены.')
    page.goto(page.url.split('#')[0]+'#zenmoney/transactions');editor=page.locator('#zen .operation-editor')
    expect(editor).to_have_count(1);editor.locator('summary').click();editor.get_by_label('Тип операции',exact=True).select_option('transfer')
    editor.get_by_label('Второй счёт').select_option('foreign');editor.get_by_label('Сумма на втором счёте').fill('2.500000000001')
    editor.get_by_role('button',name='Сохранить параметры').click();expect(page.locator('#status')).to_have_text('Данные обновлены.')
    page.locator('#operation-send').click();dialog=page.get_by_role('dialog',name='Общая отправка в ZenMoney')
    expect(dialog).to_contain_text('Other USD');expect(dialog).to_contain_text('2,500000000001')
    page.screenshot(path='/results/operation-zenmoney-review.png',full_page=True)
    assert not remote(context)['writes'];dialog.get_by_role('button',name='Отправить в ZenMoney · 1').click()
    expect(page.locator('#status')).to_contain_text('1 успешно, 0 требуют внимания')
    assert remote(context)['writes'][0][0]['id']=='existing'
    page.close()


def test_inline_reports_actual_poll_error_and_recovers(browser):
    context,page,popup,bank=inline_pages(browser)
    worker=context.service_workers[0]
    worker.evaluate("""()=>{globalThis.originalPollFetch=fetch;globalThis.fetch=async(...args)=>{
      if(new URL(args[0]).pathname==='/api/state')return new Response(JSON.stringify({detail:'Synthetic expired token'}),{status:401});
      return originalPollFetch(...args);
    };}""")
    def advance():
        worker.evaluate("""async()=>{const tabs=await chrome.tabs.query({url:'https://ipay.vietinbank.vn/*'});
          await chrome.scripting.executeScript({target:{tabId:tabs.at(-1).id},func:()=>{const previous=Date.now;Date.now=()=>previous()+20000;}});
        }""")
    try:
        advance()
        expect(bank.locator('#zenimport-status')).to_contain_text('Неверный API-токен')
        expect(bank.locator('#zenimport-status')).not_to_contain_text('Нет связи')
        assert context.request.get(BASE+'/health').ok
        worker.evaluate('()=>{fetch=originalPollFetch;}')
        advance()
        expect(bank.locator('#zenimport-status')).to_contain_text('Данные ZenMoney получены')
        assert not remote(context)['writes']
    finally:
        worker.evaluate('fetch=originalPollFetch;delete globalThis.originalPollFetch')
        popup.close();bank.close();page.close()


def test_invalidated_content_context_requests_bank_reload(browser):
    context,page,popup,bank=inline_pages(browser)
    worker=context.service_workers[0]
    worker.evaluate("""async()=>{const tabs=await chrome.tabs.query({url:'https://ipay.vietinbank.vn/*'});
      await chrome.scripting.executeScript({target:{tabId:tabs.at(-1).id},func:()=>{
        chrome.runtime.sendMessage=async()=>{throw Error('Extension context invalidated.');};
        const previous=Date.now;Date.now=()=>previous()+20000;
      }});
    }""")
    expect(bank.locator('#zenimport-status')).to_contain_text('Расширение обновлено. Обновите страницу банка.')
    expect(bank.locator('#zenimport-status')).not_to_contain_text('Нет связи')
    assert not remote(context)['writes']
    popup.close();bank.close();page.close()


def test_compact_layout_and_candidate_can_be_resolved_inline(browser):
    context,page,popup,bank=inline_pages(browser,candidate=True)
    bank.add_style_tag(content='table{width:100%;border-collapse:collapse}td{padding:12px}body{font-family:system-ui}')
    card=bank.locator('tbody zenimport-operation').first
    expect(card.locator('summary')).to_have_count(0)
    expect(card.get_by_role('radiogroup',name='Тип операции').get_by_role('radio')).to_have_count(4)
    expect(card.get_by_role('radio',name='Расход',exact=True)).to_be_enabled()
    expect(card.locator('.status-icon')).to_have_text('?')
    bounds=card.bounding_box();kind=card.get_by_role('radiogroup',name='Тип операции').bounding_box();category=card.get_by_role('combobox',name='Категория для типа операции').bounding_box()
    assert bounds['height']<=50 and abs(kind['y']-category['y'])<=2
    card.get_by_role('combobox',name='Категория для типа операции').click();card.get_by_role('option',name='Продукты',exact=True).click()
    expect(card.locator('.operation-editor')).to_have_attribute('data-operation-draft','true')
    assert not remote(context)['writes']
    card.locator('.status-icon').click()
    card.get_by_role('button',name='2026-10-06 · CT DI:TEST0001 Coffee · Продукты',exact=True).click()
    expect(card.locator('.status-icon')).to_have_text('✓')
    assert not remote(context)['writes']
    bank.screenshot(path='/results/compact-bank-rows.png',full_page=True)
    popup.close();bank.close();page.close()


@pytest.mark.parametrize('positive,action',[(False,'lend'),(False,'repay'),(True,'borrow'),(True,'collect')])
def test_compact_debt_action_preserves_direction(browser,positive,action):
    context,page,popup,bank=inline_pages(browser,positive=positive)
    editor=bank.locator('tbody zenimport-operation').nth(1 if positive else 0).locator('.operation-editor')
    editor.get_by_role('radio',name='Долг',exact=True).click()
    editor.get_by_label('Действие с долгом').select_option(action)
    expect(editor.get_by_label('Действие с долгом').locator('option')).to_have_count(2)
    editor.get_by_label('Человек для долга').fill('Test person');editor.get_by_label('Человек для долга').press('Tab')
    expect(editor).to_have_attribute('data-operation-draft','true')
    state=context.request.get(BASE+'/api/state',headers=AUTH).json()
    row=next(r for r in state['review'] if r['amount_minor']==(100000 if positive else -65000))
    assert row['operation_edit']['definition']['kind']==action
    assert not remote(context)['writes']
    popup.close();bank.close();page.close()


def test_compact_income_refund_keeps_expense_category(browser):
    context,page,popup,bank=inline_pages(browser,positive=True)
    editor=bank.locator('tbody zenimport-operation').nth(1).locator('.operation-editor')
    expect(editor.get_by_role('radio',name='Приход',exact=True)).to_have_attribute('aria-checked','true')
    editor.get_by_label('Вид поступления').select_option('refund')
    editor.get_by_role('combobox',name='Категория для типа операции').click();editor.get_by_role('option',name='Продукты',exact=True).click()
    expect(editor).to_have_attribute('data-operation-draft','true')
    state=context.request.get(BASE+'/api/state',headers=AUTH).json()
    row=next(r for r in state['review'] if r['amount_minor']==100000)
    assert row['operation_edit']['definition']['kind']=='refund' and row['operation_edit']['definition']['category_id']=='food'
    assert not remote(context)['writes']
    popup.close();bank.close();page.close()


def test_circle_types_and_category_hints_below_picker(browser):
    context,page,popup,bank=inline_pages(browser)
    worker=context.service_workers[0]
    try:
        # Existing backend models with a long history ranking, no fake writes.
        worker.evaluate('''()=>{
          globalThis.layoutFetch=fetch;
          globalThis.fetch=async (...args)=>{
            const response=await layoutFetch(...args);
            if(new URL(args[0]).pathname!=='/api/state')return response;
            const state=await response.json();
            for(const [i,title] of ['Техника','Дом','Продукты, бытовая химия','Одежда, обувь','Рестораны и доставка'].entries())state.categories.push({id:'layout-'+i,title,showOutcome:true,showIncome:false});
            for(const row of state.review)row.creation_ranking=state.categories.map((c,i)=>({id:c.id,count:20-i}));
            return new Response(JSON.stringify(state),{headers:{'Content-Type':'application/json'}});
          };
        }''')
        bank.reload()
        card=bank.locator('tbody zenimport-operation').first
        expect(card.locator('.operation-hints button')).to_have_count(6)
        expect(bank.locator('#zenimport-status')).to_contain_text('Данные ZenMoney получены')
        expect(card.get_by_role('radiogroup',name='Тип операции')).to_be_visible()
        bank.add_style_tag(content='table{width:100%;table-layout:fixed;border-collapse:collapse}td{padding:12px;overflow-wrap:anywhere}body{font-family:system-ui;margin:8px}')
        for width in [1440,760,480]:
            bank.set_viewport_size({'width':width,'height':900})
            types=card.get_by_role('radiogroup',name='Тип операции')
            picker=card.get_by_role('combobox',name='Категория для типа операции')
            hints=card.locator('.operation-hints')
            geometry=card.evaluate('''h=>{
              const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
              const root=h.shadowRoot;return {bounds:rect(h),tb:rect(root.querySelector('.operation-types')),pb:rect(root.querySelector('.operation-category-trigger')),hb:rect(root.querySelector('.operation-hints'))};
            }''')
            bounds,tb,pb,hb=(geometry[k] for k in ['bounds','tb','pb','hb'])
            assert abs(hb['x']-pb['x'])<2 and hb['y']>=pb['y']+pb['height']
            assert pb['x']>=tb['x']+tb['width'] and hb['x']+hb['width']<=bounds['x']+bounds['width']
            assert card.locator('.operation-fields').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
            assert hints.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
            radios=types.get_by_role('radio');assert radios.count()==4
            for b in radios.all():
                box=b.bounding_box();assert abs(box['width']-box['height'])<1
                expect(b).to_have_css('border-radius','50%')
            expect(types.get_by_role('radio',name='Расход',exact=True)).to_have_css('background-color','rgb(239, 102, 92)')
            expect(types.get_by_role('radio',name='Долг',exact=True)).to_have_css('background-color','rgba(0, 0, 0, 0)')
            bank.screenshot(path=f'/results/bank-circle-types-{width}.png',full_page=True)
        # A suggested category remains a one-click action and doesn't create a remote record.
        card.locator('.operation-hints').get_by_role('button',name='Продукты',exact=True).click()
        expect(card.locator('.operation-editor')).to_have_attribute('data-operation-draft','true')
        expect(card.get_by_role('radio',name='Приход',exact=True)).to_be_disabled()
        assert not remote(context)['writes']
    finally:
        worker.evaluate('fetch=layoutFetch;delete globalThis.layoutFetch')
        bank.close();popup.close();page.close()
        worker.evaluate('chrome.storage.local.set({inlineEnabled:false})')


@pytest.mark.parametrize('retry_import',[False,True])
def test_early_bank_response_replayed_for_missing_newest_row(browser,retry_import):
    context,page,popup,bank=inline_pages(browser)
    worker=context.service_workers[0]
    endpoint='https://api-ipay.vietinbank.vn/ipay/wa/getHistTransactions'
    response={'error':False,'accountNo':'111122223333','currentPage':0,'pageSize':100,'totalRecords':1,
              'sessionId':'private-must-not-leave-bank','transactions':[{'currency':'VND','remark':'Newest synthetic operation',
              'amount':'325000','balance':'610000','trxId':'NEWEST-TEST','processDate':'14-10-2026 19:46:17','dorC':'D'}]}
    bank.route(endpoint,lambda r:r.fulfill(json=response,headers={'Access-Control-Allow-Origin':'*'}))
    worker.evaluate('''retry=>{
      globalThis.replayTest={original:fetch,imports:[],fail:retry};
      globalThis.fetch=async (...args)=>{
        if(new URL(args[0]).pathname==='/api/imports'){
          replayTest.imports.push(JSON.parse(args[1].body));
          if(replayTest.fail){replayTest.fail=false;return new Response(JSON.stringify({detail:'Synthetic temporary import error'}),{status:503});}
        }
        return replayTest.original.apply(globalThis,args);
      };
    }''',retry_import)
    try:
        # The bank fetches history before its SPA moves onto the history route.
        bank.evaluate("history.pushState({},'', '/home')")
        expect(bank.locator('tbody zenimport-operation')).to_have_count(0)
        bank.evaluate('(url)=>fetch(url).then(r=>r.json())',endpoint)
        bank.evaluate('''()=>{
          const row=document.createElement('tr');
          for(const text of ['14/10/2026 19:46:17','Newest synthetic operation','-325,000 VND','610,000 VND']){
            const td=document.createElement('td');td.textContent=text;row.append(td);
          }
          document.querySelector('tbody').prepend(row);
          history.pushState({},'', '/payment-history?accountNo=111122223333');
        }''')
        expect(bank.locator('tbody .operation-editor')).to_have_count(3,timeout=25000)
        card=bank.locator('tbody zenimport-operation').first
        expect(card.get_by_role('radio',name='Расход',exact=True)).to_be_enabled()
        expect(card.locator('.status-icon')).to_have_text('+')
        state=context.request.get(BASE+'/api/state',headers=AUTH).json()
        assert len([r for r in state['review'] if r['amount_minor']==-325000])==1
        imports=worker.evaluate('replayTest.imports')
        assert len(imports)==(2 if retry_import else 1)
        assert 'private-must-not-leave-bank' not in json.dumps(imports)
        bank.evaluate("window.postMessage({source:'zenimport-inline-replay',account:'111122223333'},location.origin)")
        bank.wait_for_timeout(500)
        assert len(worker.evaluate('replayTest.imports'))==len(imports)
        assert not remote(context)['writes']
    finally:
        bank.close();popup.close();page.close()
        worker.evaluate('fetch=replayTest.original;delete globalThis.replayTest;chrome.storage.local.set({inlineEnabled:false})')
