"""Installed extension + actual HTTP backend, synthetic SDK remote only."""
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
    result=remote(context);assert len(result['writes'])==1 and len(result['writes'][0])==1
    p=result['writes'][0][0]
    if existing:assert p['id']=='existing' and p['created']==10
    assert ((p['income'] if p['incomeAccount']=='cash' else 0)-(p['outcome'] if p['outcomeAccount']=='cash' else 0))==(100000 if positive else -65000)
    if kind=='transfer':assert p['outcomeAccount' if positive else 'incomeAccount']=='second'
    if kind in ('lend','borrow','repay','collect'):assert p['payee']=='Test person' and 'debt' in (p['incomeAccount'],p['outcomeAccount'])
    page.close()

def inline_pages(browser,existing=False,uncategorized=False,payee=None):
    context,identifier=browser
    response=context.request.post(BASE+'/api/test/reset',headers=AUTH,data={'existing':existing,'uncategorized':uncategorized,'payee':payee});assert response.ok
    worker=context.service_workers[0];worker.evaluate("chrome.storage.local.set({inlineEnabled:false})")
    page=open_app(browser);connect(page,BASE);expect(page.locator('#status')).to_have_text('Данные обновлены.')
    popup=context.new_page();popup.goto(f'chrome-extension://{identifier}/popup.html')
    popup.locator('#inline-enable').check();expect(popup.locator('#inline-enable')).to_be_checked()
    bank=fixture_bank(browser);expect(bank.locator('zenimport-operation .operation-editor')).to_have_count(2)
    expect(bank.locator('#zenimport-status')).to_contain_text('Данные ZenMoney получены')
    return context,page,popup,bank

@pytest.mark.parametrize('existing',[False,True])
def test_bank_inline_transfer_new_staged_existing_immediate(browser,existing):
    context,page,popup,bank=inline_pages(browser,existing)
    editor=bank.locator('zenimport-operation .operation-editor').first;editor.locator('summary').click()
    editor.get_by_label('Тип операции',exact=True).select_option('transfer');editor.get_by_label('Второй счёт').select_option('foreign')
    editor.get_by_label('Сумма на втором счёте').fill('2.500000000001');editor.get_by_label('Сумма на втором счёте').press('Tab')
    if existing:
        expect(bank.locator('zenimport-operation').first.locator('strong').first).to_have_class('good')
        for _ in range(60):
            if remote(context)['writes']:break
            bank.wait_for_timeout(100)
        writes=remote(context)['writes'];assert len(writes)==1 and writes[0][0]['id']=='existing'
        assert writes[0][0]['incomeAccount']=='foreign' and str(writes[0][0]['income'])=='2.500000000001'
    else:
        expect(bank.locator('zenimport-operation .operation-editor').first).to_contain_text('сохранён локально')
        assert not remote(context)['writes']
        bank.bring_to_front();popup.locator('#send-new').click()
        expect(popup.locator('#send-status')).to_contain_text('Добавлено в ZenMoney: 2.')
        result=remote(context);assert len(result['writes'])==1 and len(result['writes'][0])==2
    bank.screenshot(path='/results/minimal-inline-transfer.png',full_page=True)
    popup.close();bank.close();page.close()


def test_minimal_popup_toggle_and_visible_default_creation(browser):
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
    expect(popup.locator('#send-status')).to_contain_text('Добавлено в ZenMoney: 2.')
    result=remote(context);assert len(result['writes'])==1 and len(result['writes'][0])==2
    assert sorted(p['income']-p['outcome'] for p in result['writes'][0])==[-65000,100000]
    assert all(not p['tag'] for p in result['writes'][0])
    expect(bank.locator('tbody zenimport-operation').first).to_contain_text('Нужна категория')
    popup.reload();expect(popup.locator('#send-new')).to_be_disabled()
    assert len(remote(context)['writes'])==1
    popup.screenshot(path='/results/minimal-popup.png')
    popup.close();bank.close();page.close()


def test_existing_inline_category_is_sent_once_and_forgery_denied(browser):
    context,page,popup,bank=inline_pages(browser,True,True)
    card=bank.locator('tbody zenimport-operation').first
    card.get_by_role('combobox',name='Категория',exact=True).click()
    card.get_by_role('option',name='Продукты',exact=True).click()
    expect(card.locator('strong').first).to_have_class('good')
    result=remote(context);assert len(result['writes'])==1 and len(result['writes'][0])==1
    assert result['writes'][0][0]['id']=='existing' and result['writes'][0][0]['tag']==['food']
    forged=popup.evaluate("chrome.runtime.sendMessage({type:'inline-category',account:'111122223333',id:'existing',category:'food'})")
    assert not forged['ok'] and len(remote(context)['writes'])==1
    popup.close();bank.close();page.close()


def test_inline_lost_reply_blocks_blind_repeat(browser):
    context,page,popup,bank=inline_pages(browser,True,True)
    assert context.request.post(BASE+'/api/test/behavior',headers=AUTH,data={'behavior':'lost_read'}).ok
    card=bank.locator('tbody zenimport-operation').first
    card.get_by_role('combobox',name='Категория',exact=True).click();card.get_by_role('option',name='Продукты',exact=True).click()
    expect(bank.locator('#zenimport-status')).to_contain_text('не подтверждено')
    expect(card).to_contain_text('Нужно проверить результат отправки')
    assert len(remote(context)['writes'])==1
    bank.reload();expect(card).to_contain_text('Нужно проверить результат отправки')
    assert len(remote(context)['writes'])==1
    popup.close();bank.close();page.close()


@pytest.mark.parametrize('behavior,confirmed',[('partial',1),('before',0),('lost_response',2)])
def test_new_popup_verifies_partial_or_lost_creation_without_retry(browser,behavior,confirmed):
    context,page,popup,bank=inline_pages(browser)
    assert context.request.post(BASE+'/api/test/behavior',headers=AUTH,data={'behavior':behavior}).ok
    bank.bring_to_front();popup.locator('#send-new').click()
    expect(popup.locator('#send-status')).to_contain_text(f'Добавлено в ZenMoney: {confirmed}.')
    result=remote(context);assert len(result['writes'])==1 and len(result['transactions'])==confirmed
    if confirmed<2:
        expect(popup.locator('#send-status')).to_contain_text('Повторная отправка')
    popup.reload();bank.bring_to_front()
    expect(popup.locator('#send-new')).to_be_disabled()
    assert len(remote(context)['writes'])==1
    popup.close();bank.close();page.close()


def test_inline_debt_requires_explicit_person_not_previous_shop(browser):
    context,page,popup,bank=inline_pages(browser,True,payee='Previous shop')
    editor=bank.locator('zenimport-operation .operation-editor').first;editor.locator('summary').click()
    type_select=editor.get_by_label('Тип операции',exact=True);type_select.focus();type_select.press('ArrowDown');type_select.press('ArrowDown');expect(type_select).to_have_value('lend')
    expect(editor.get_by_label('Человек для долга')).to_have_value('')
    expect(editor).to_contain_text('Укажите человека для долга.')
    assert not remote(context)['writes']
    editor.get_by_label('Человек для долга').fill('Test person');editor.get_by_label('Человек для долга').press('Tab')
    expect(bank.locator('tbody zenimport-operation').first.locator('strong').first).to_have_class('good')
    expect(bank.locator('tbody zenimport-operation').first.locator('summary')).to_contain_text('Тип операции · Дать в долг')
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
        for _ in range(60):
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
