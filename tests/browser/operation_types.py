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

@pytest.mark.parametrize('existing',[False,True])
def test_bank_inline_transfer_local_only_and_popup_confirmation(browser,existing):
    context,identifier=browser;reset(context,existing)
    page=open_app(browser);connect(page,BASE);expect(page.locator('#status')).to_have_text('Данные обновлены.')
    popup=context.new_page();popup.goto(f'chrome-extension://{identifier}/popup.html');popup.locator('#inline-enable').click()
    expect(popup.locator('#inline-status')).to_contain_text('Включено');bank=fixture_bank(browser)
    expect(bank.locator('zenimport-operation .operation-editor')).to_have_count(2)
    editor=bank.locator('zenimport-operation .operation-editor').first;editor.locator('summary').click()
    editor.get_by_label('Тип операции',exact=True).select_option('transfer');editor.get_by_label('Второй счёт').select_option('foreign')
    editor.get_by_label('Сумма на втором счёте').fill('2.500000000001');editor.get_by_role('button',name='Сохранить параметры').click()
    expect(bank.locator('zenimport-operation .operation-editor').first).to_contain_text('сохранён локально')
    assert not remote(context)['writes']
    bank.screenshot(path='/results/operation-inline-transfer.png',full_page=True)
    # The installed content world cannot ask for a remote write through a forged
    # extension-page message. Saving the bank row only creates a local draft.
    forged=popup.evaluate("chrome.runtime.sendMessage({type:'inline-operation',account:'111122223333',id:'existing',edit:{}})")
    assert not forged['ok'] and not remote(context)['writes']
    bank.bring_to_front()
    with context.expect_page() as opened:popup.locator('#batch-open').click()
    batch=opened.value;dialog=batch.get_by_role('dialog',name='Общая отправка в ZenMoney')
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text('Other USD')
    expect(dialog).to_contain_text('2,500000000001')
    batch.screenshot(path='/results/operation-popup-preview.png',full_page=True)
    assert not remote(context)['writes']
    dialog.get_by_role('button',name='Отмена',exact=True).click();batch.close()
    context.service_workers[0].evaluate("chrome.storage.local.set({inlineEnabled:false})")
    bank.close();popup.close();page.close()

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
