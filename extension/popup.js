import {api} from './api.js';
let payload;
const $ = id => document.getElementById(id);
$('open').onclick = () => chrome.tabs.create({url: chrome.runtime.getURL('app.html')});
$('extract').onclick = async () => {
  $('extract').disabled = true;
  $('import').hidden = $('debug').hidden = true;
  payload = undefined;
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    const [result] = await chrome.scripting.executeScript({target: {tabId: tab.id}, files: ['extract.js']});
    if (!result.result?.ok) throw new Error(result.result?.error || 'Страница недоступна');
    payload = result.result.data;
    $('status').textContent = `${payload.bank} · ${payload.account} · ${payload.transactions.length} операций. HTML не собирается.`;
    $('import').hidden = $('debug').hidden = false;
  } catch (error) { $('status').textContent = error.message; }
  finally { $('extract').disabled = false; }
};
$('import').onclick = async () => {
  $('import').disabled = true;
  try {
    const result = await api('/api/imports', payload);
    $('status').textContent = `Добавлено: ${result.added}. Уже были: ${result.duplicates}. Откройте интерфейс для проверки.`;
  } catch (error) { $('status').textContent = error.message; }
  finally { $('import').disabled = false; }
};
$('debug').onclick = () => {
  if (!confirm('Скачать исходные поля операций в локальный JSON? Он содержит финансовые данные. На сервер файл не отправляется.')) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = 'debug-bank-rows.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
