import os
import tempfile
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright, expect

BASE = 'http://127.0.0.1:8000'
TOKEN = 'docker-test-token-not-for-real-data'

@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as p, tempfile.TemporaryDirectory() as profile:
        extension = '/app/extension'
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
    popup.locator('#extract').click()
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
    unknown.bring_to_front();popup.locator('#extract').click()
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
    page.goto(f'chrome-extension://{identifier}/app.html#zenmoney')
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
    expect(page.locator('#zen')).to_contain_text('Операций пока нет')
    expect(page.locator('#history-prev')).to_be_disabled()
    expect(page.locator('#history-next')).to_be_disabled()
    page.close()
