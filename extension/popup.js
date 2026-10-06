import {api} from './api.js';
import {parseVietinBankJson} from './vietinbank-json.js';
let payload;
const $ = id => document.getElementById(id);
$('open').onclick = () => chrome.tabs.create({url: chrome.runtime.getURL('app.html')});
async function extract(collectAll=false) {
  $('extract').disabled = $('collect').disabled = true;
  $('status').textContent=collectAll?'Собираю страницы. Держите окно расширения открытым…':'Читаю страницу…';
  $('import').hidden = $('debug').hidden = true;
  payload = undefined;
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    await chrome.scripting.executeScript({target:{tabId:tab.id},func:value=>{globalThis.__zenImportAllPages=value;},args:[collectAll]});
    const [result] = await chrome.scripting.executeScript({target: {tabId: tab.id}, files: ['extract.js']});
    if (!result.result?.ok) throw new Error(result.result?.error || 'Страница недоступна');
    payload = result.result.data;
    $('status').textContent = `${payload.bank} · ${payload.account} · ${payload.transactions.length} операций. HTML не собирается.${payload.capture?` Строки ${payload.capture.row_start}–${payload.capture.row_end} из ${payload.capture.total_rows}. ${payload.capture.complete?'Все строки выбранного поиска собраны.':'Это только часть истории.'}`:''}`;
    $('import').hidden = $('debug').hidden = false;
  } catch (error) { $('status').textContent = error.message; }
  finally { $('extract').disabled = $('collect').disabled = false; }
}
$('extract').onclick=()=>extract(false);
$('collect').onclick=()=>extract(true);
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

function showJson(data) {
  payload=data;
  const c=data.capture;
  $('status').textContent=`VietinBank · ${data.account} · ${data.transactions.length} операций. Строки ${c.row_start}–${c.row_end} из ${c.total_rows}. ${c.complete?'Ответ охватывает весь результат поиска.':'Это часть истории; оставшиеся страницы нужно получить отдельно.'} Проверьте счёт перед импортом.`;
  $('import').hidden=$('debug').hidden=false;
}
async function restoreCapture() {
  const {bankCapture:state}=await chrome.storage.session.get('bankCapture');
  if(!state)return;
  $('clear-capture').hidden=false;
  payload=undefined;$('import').hidden=$('debug').hidden=true;
  if(state.expires<Date.now()) {await chrome.storage.session.remove('bankCapture');$('status').textContent='Захват устарел. Начните заново.';return;}
  if(state.status==='ready')showJson(state.payload);
  else $('status').textContent=state.status==='error'?state.error:'Ожидаю новый ответ истории. Закройте это окно, нажмите Search в банке, затем снова откройте расширение. При переходе/перезагрузке страницы начните захват заново.';
}
$('capture').onclick=async()=>{
  $('capture').disabled=true;payload=undefined;$('import').hidden=$('debug').hidden=true;
  try {
    const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
    if(!tab?.url?.startsWith('https://ipay.vietinbank.vn/payment-history'))throw Error('Откройте историю счёта VietinBank.');
    await chrome.storage.session.set({bankCapture:{tabId:tab.id,status:'waiting',expires:Date.now()+600000}});
    await chrome.scripting.executeScript({target:{tabId:tab.id},files:['bank-bridge.js']});
    await chrome.scripting.executeScript({target:{tabId:tab.id},world:'MAIN',files:['bank-observer.js']});
    await restoreCapture();
  }catch(error){await chrome.storage.session.remove('bankCapture');$('status').textContent=error.message;}
  finally{$('capture').disabled=false;}
};
$('clear-capture').onclick=async()=>{
  const {bankCapture:state}=await chrome.storage.session.get('bankCapture');
  await chrome.storage.session.remove('bankCapture');
  if(state)try{await chrome.scripting.executeScript({target:{tabId:state.tabId},world:'MAIN',func:()=>window.__zenImportStopObserver?.()});}catch{}
  payload=undefined;$('import').hidden=$('debug').hidden=$('clear-capture').hidden=true;$('status').textContent='Захват очищен.';
};
$('json-file').onchange=async event=>{
  payload=undefined;$('import').hidden=$('debug').hidden=true;
  try {
    const file=event.target.files[0];if(!file)return;
    if(file.size>1500000)throw Error('Файл больше 1,5 МБ. Разбейте историю на страницы.');
    const data=await parseVietinBankJson(JSON.parse(await file.text()));
    await chrome.storage.session.remove('bankCapture');
    showJson(data);
  }catch(error){$('status').textContent=error instanceof SyntaxError?'Файл не содержит корректный JSON.':error.message;}
  finally{event.target.value='';}
};
chrome.storage.onChanged.addListener((changes,area)=>{if(area==='session'&&changes.bankCapture?.newValue)restoreCapture();});
restoreCapture();
