"""Bank SPA/context regressions against the disposable operation backend only."""
import pytest
from playwright.sync_api import expect

from extension_flow import browser, TOKEN
from operation_types import AUTH, BASE, inline_pages, remote


ACCOUNT = '111122223333'
HISTORY = 'https://ipay.vietinbank.vn/payment-history?accountNo=' + ACCOUNT


def close_pages(context, page, popup, bank):
    """Prevent an enabled, closed bank fixture from leaking into another test."""
    bank.close()
    popup.close()
    page.close()
    context.service_workers[0].evaluate(
        "chrome.storage.local.set({inlineEnabled:false})"
    )


def wait_writes(context, bank, count):
    for _ in range(60):
        result = remote(context)
        if len(result['writes']) >= count:
            return result['writes']
        bank.wait_for_timeout(100)
    raise AssertionError('Synthetic remote did not receive the expected update')


@pytest.mark.parametrize('existing', [False, True])
def test_spa_history_entry_edit_and_popup_do_not_create_records(browser, existing):
    context, page, popup, bank = inline_pages(
        browser, existing=existing, uncategorized=True, spa=True
    )
    try:
        bank.bring_to_front()
        expect(popup.locator('#send-new')).to_be_enabled()
        card = bank.locator('tbody zenimport-operation').first
        expect(card.get_by_label('Тип операции', exact=True)).to_be_enabled()
        card.get_by_role('combobox', name='Категория для типа операции', exact=True).click()
        card.get_by_role('option', name='Продукты', exact=True).click()
        if existing:
            writes = wait_writes(context, bank, 1)
            assert len(writes) == 1 and len(writes[0]) == 1
            assert writes[0][0]['id'] == 'existing'
            assert writes[0][0]['tag'] == ['food']
            expect(card.locator('.status-icon')).to_have_text('✓')
        else:
            expect(card.locator('.operation-editor')).to_have_attribute(
                'data-operation-draft', 'true'
            )
            assert not remote(context)['writes']

        # Changes are local for a new row; an existing row keeps its remote UUID.
        card.get_by_label('Тип операции', exact=True).select_option('debt')
        card.get_by_label('Человек для долга').fill('Synthetic SPA person')
        card.get_by_label('Человек для долга').press('Tab')
        if existing:
            writes = wait_writes(context, bank, 2)
            assert len(writes) == 2
            assert all(len(batch) == 1 and batch[0]['id'] == 'existing' for batch in writes)
            assert writes[-1][0]['payee'] == 'Synthetic SPA person'
            assert writes[-1][0]['incomeAccount'] == 'debt'
        else:
            expect(card.get_by_label('Действие с долгом')).to_have_value('lend')
            expect(card.locator('.operation-editor')).to_have_attribute(
                'data-operation-draft', 'true'
            )
            # Allow the debounced request to finish, not merely its local select.
            bank.wait_for_timeout(600)
            expect(card.get_by_label('Тип операции', exact=True)).to_be_enabled()
            assert not remote(context)['writes']
        assert bank.url == HISTORY
        expect(bank.locator('#zenimport-status')).not_to_contain_text(
            'Банковский счёт или страница изменились'
        )
        bank.bring_to_front()
        expect(popup.locator('#send-new')).to_be_enabled()
    finally:
        close_pages(context, page, popup, bank)


@pytest.mark.parametrize('destination', [
    '/home?accountNo=' + ACCOUNT,
    '/payment-history?accountNo=999900001111',
])
def test_spa_navigation_rejects_stale_bank_mutation_and_recovers(browser, destination):
    context, page, popup, bank = inline_pages(browser, spa=True)
    worker = context.service_workers[0]
    try:
        state = context.request.get(BASE + '/api/state', headers=AUTH).json()
        row = next(r for r in state['review'] if r['amount_minor'] == -65000)
        definition = row['operation_edit']['definition']
        bank.evaluate('(path)=>history.pushState({}, "", path)', destination)
        bank.bring_to_front()
        expect(popup.locator('#send-new')).to_be_disabled()
        # Execute in the actual isolated content world. A page cannot manufacture
        # Chrome's sender metadata, and the stale account must fail before a write.
        result = worker.evaluate('''async ({url,account,id})=>{
          const tabs=await chrome.tabs.query({url:'https://ipay.vietinbank.vn/*'});
          const tab=tabs.find(t=>t.url===url);
          if(!tab)throw Error('Synthetic bank tab not found');
          const replies=await chrome.scripting.executeScript({
            target:{tabId:tab.id},
            func:selection=>chrome.runtime.sendMessage({type:'inline-category',...selection,category:'food'}),
            args:[{account,id}]
          });
          return replies[0].result;
        }''', {'url': bank.url, 'account': ACCOUNT, 'id': row['id']})
        assert result['ok'] is False and 'изменил' in result.get('error', '')
        after = context.request.get(BASE + '/api/state', headers=AUTH).json()
        assert next(r for r in after['review'] if r['id'] == row['id'])['operation_edit']['definition'] == definition
        assert not remote(context)['writes']

        bank.evaluate('(url)=>history.pushState({}, "", url)', HISTORY)
        expect(bank.locator('tbody zenimport-operation .operation-editor')).to_have_count(2)
        expect(bank.locator('#zenimport-status')).to_contain_text('Данные ZenMoney получены')
        bank.bring_to_front()
        expect(popup.locator('#send-new')).to_be_enabled()
        assert not remote(context)['writes']
    finally:
        close_pages(context, page, popup, bank)


@pytest.mark.parametrize('failure,reason', [
    ('token', 'Сохраните API-токен'),
    ('http500', 'Synthetic state failure'),
    ('unmapped', 'Свяжите банковский счёт'),
    ('page', 'Откройте историю'),
])
def test_popup_disabled_reason_and_automatic_recovery(browser, failure, reason):
    context, page, popup, bank = inline_pages(browser, spa=True)
    worker = context.service_workers[0]
    intercepted = False
    try:
        # A prior run's message must not conceal the current account's failure.
        worker.evaluate('''async()=>{
          await chrome.storage.local.set({fullSyncJob:null});
          await chrome.storage.session.remove('inlineSendNotice');
        }''')
        bank.bring_to_front()
        expect(popup.locator('#send-new')).to_be_enabled()
        if failure == 'token':
            worker.evaluate("chrome.storage.local.set({token:''})")
        elif failure == 'page':
            bank.evaluate("history.pushState({},'', '/home?accountNo=111122223333')")
        else:
            worker.evaluate('''mode=>{
              globalThis.contextRegressionFetch=fetch;
              globalThis.fetch=async (...args)=>{
                if(new URL(args[0]).pathname!=='/api/state')return contextRegressionFetch(...args);
                if(mode==='http500')return new Response(JSON.stringify({detail:'Synthetic state failure'}),{
                  status:500,headers:{'Content-Type':'application/json'}
                });
                const response=await contextRegressionFetch(...args);
                const state=await response.json();
                for(const account of state.bank_accounts)account.zenmoney_account=null;
                return new Response(JSON.stringify(state),{headers:{'Content-Type':'application/json'}});
              };
            }''', failure)
            intercepted = True
        expect(popup.locator('#send-new')).to_be_disabled()
        expect(popup.locator('#send-status')).to_contain_text(reason)
        assert not remote(context)['writes']

        if failure == 'token':
            worker.evaluate('(token)=>chrome.storage.local.set({token})', TOKEN)
        elif failure == 'page':
            bank.evaluate('(url)=>history.pushState({}, "", url)', HISTORY)
        else:
            worker.evaluate('fetch=contextRegressionFetch;delete globalThis.contextRegressionFetch')
            intercepted = False
        bank.bring_to_front()
        expect(popup.locator('#send-new')).to_be_enabled()
        expect(popup.locator('#send-status')).not_to_contain_text(reason)
        assert not remote(context)['writes']
    finally:
        if intercepted:
            worker.evaluate('fetch=contextRegressionFetch;delete globalThis.contextRegressionFetch')
        worker.evaluate('(token)=>chrome.storage.local.set({token})', TOKEN)
        close_pages(context, page, popup, bank)


def test_account_changes_while_operation_state_is_read_blocks_write(browser):
    context, page, popup, bank = inline_pages(
        browser, existing=True, uncategorized=True, spa=True
    )
    worker = context.service_workers[0]
    # Stop popup polling so the held /api/state is deterministically the edit's
    # scoped read. A valid edit is sent from the actual content world.
    popup.close()
    state = context.request.get(BASE + '/api/state', headers=AUTH).json()
    row = next(r for r in state['review'] if r['amount_minor'] == -65000)
    definition = row['operation_edit']['definition']
    worker.evaluate('''()=>{
      globalThis.contextRace={original:fetch,armed:true,writes:[],paths:[]};
      globalThis.fetch=async (...args)=>{
        const path=new URL(args[0]).pathname;contextRace.paths.push(path);
        if(args[1]?.method==='PUT'||path.endsWith('/send'))contextRace.writes.push(path);
        const response=await contextRace.original.apply(globalThis,args);
        if(path==='/api/state'&&contextRace.armed){
          contextRace.armed=false;
          await new Promise(resolve=>{contextRace.release=resolve;});
        }
        return response;
      };
    }''')
    try:
        edit={**definition,'category_id':'food'}
        worker.evaluate("""async ({url,id,zen_id,edit,snapshot})=>{
          const tab=(await chrome.tabs.query({url:'https://ipay.vietinbank.vn/*'})).find(t=>t.url===url);
          contextRace.request=chrome.scripting.executeScript({target:{tabId:tab.id},
            func:payload=>chrome.runtime.sendMessage(payload),
            args:[{type:'inline-operation',account:'111122223333',id,zen_id,edit:{definition:edit,expected_snapshot:snapshot}}]
          }).then(result=>{contextRace.result=result;return result;});
        }""",{'url':bank.url,'id':row['id'],'zen_id':row['operation_edit']['id'],
               'edit':edit,'snapshot':row['operation_edit']['snapshot']})
        for _ in range(60):
            if worker.evaluate('typeof contextRace.release') == 'function':
                break
            bank.wait_for_timeout(100)
        assert worker.evaluate('typeof contextRace.release') == 'function',worker.evaluate('({result:contextRace.result,paths:contextRace.paths})')
        bank.evaluate("history.pushState({},'', '/payment-history?accountNo=999900001111')")
        worker.evaluate('contextRace.release()')
        result=worker.evaluate('async()=> (await contextRace.request)[0].result')
        assert not result['ok'] and 'изменил' in result['error']
        assert worker.evaluate('contextRace.writes') == []
        assert not remote(context)['writes']
        after = context.request.get(BASE + '/api/state', headers=AUTH).json()
        assert next(r for r in after['review'] if r['id'] == row['id'])['operation_edit']['definition'] == definition
    finally:
        worker.evaluate('''()=>{
          contextRace.release?.();
          fetch=contextRace.original;
          delete globalThis.contextRace;
        }''')
        bank.close()
        page.close()
        worker.evaluate('chrome.storage.local.set({inlineEnabled:false})')
