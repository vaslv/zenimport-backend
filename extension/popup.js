import {api} from './api.js';
import {historyPeriod} from './history-period.js';
import {parseVietinBankJson} from './vietinbank-json.js';
let payload;
const $ = id => document.getElementById(id);
$('open').onclick = () => chrome.tabs.create({url: chrome.runtime.getURL('app.html#review')});
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
    $('status').textContent = `Добавлено: ${result.added}. Уже были: ${result.duplicates}. Нажмите «Проверить операции». В ZenMoney ничего не отправлено.`;
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
  $('status').textContent=`VietinBank · ${data.account} · ${data.transactions.length} операций. Строки ${c.row_start}–${c.row_end} из ${c.total_rows}. ${c.complete?'Ответ охватывает весь результат поиска.':'Это часть истории; оставшиеся страницы нужно получить отдельно.'} Проверьте счёт и сохраните операции для проверки.`;
  $('import').hidden=$('debug').hidden=false;
}
async function restoreCapture() {
  const {bankCapture:state}=await chrome.storage.session.get('bankCapture');
  if(!state)return;
  $('clear-capture').hidden=false;
  payload=undefined;$('import').hidden=$('debug').hidden=true;
  if(state.expires<Date.now()) {await chrome.storage.session.remove('bankCapture');$('status').textContent='Захват устарел. Начните заново.';return;}
  if(state.status==='ready')showJson(state.payload);
  else if(state.status==='error') $('status').textContent=state.error;
  else {
    const tools=await chrome.runtime.sendMessage({type:'devtools-status',tabId:state.tabId}).catch(()=>null);
    $('status').textContent='Ожидаю новый ответ истории. '+(tools?.connected?'Захват через Network подключён. ':'Для резервного захвата откройте DevTools (Network) в банковской вкладке. ')+(Date.now()-(state.startedAt||0)>15000?'Ответ ещё не получен: измените период и нажмите Search; проверьте, появился ли новый getHistTransactions в Network. ':'Закройте это окно, нажмите Search в банке, затем снова откройте расширение. ');
  }
}
async function startCapture(diagnoseRequest=false){
  $('capture').disabled=true;payload=undefined;$('import').hidden=$('debug').hidden=true;
  try {
    const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
    if(!tab?.url?.startsWith('https://ipay.vietinbank.vn/payment-history'))throw Error('Откройте историю счёта VietinBank.');
    if(diagnoseRequest){
      const tools=await chrome.runtime.sendMessage({type:'devtools-status',tabId:tab.id}).catch(()=>null);
      if(!tools?.connected)throw Error('Для схемы запроса откройте DevTools → Network в банковской вкладке. После обновления расширения переоткройте DevTools.');
    }
    await chrome.storage.local.set({historyPeriodMonths:Number($('history-period').value)});
    await chrome.storage.session.set({bankCapture:{tabId:tab.id,diagnoseRequest,status:'waiting',startedAt:Date.now(),expires:Date.now()+600000}});
    if(diagnoseRequest){
      await chrome.scripting.executeScript({target:{tabId:tab.id},world:'MAIN',func:()=>window.__zenImportStopObserver?.()});
    }else{
      await chrome.scripting.executeScript({target:{tabId:tab.id},files:['bank-bridge.js']});
      await chrome.scripting.executeScript({target:{tabId:tab.id},world:'MAIN',files:['bank-observer.js']});
    }
    await restoreCapture();
  }catch(error){await chrome.storage.session.remove('bankCapture');$('status').textContent=error.message;}
  finally{$('capture').disabled=false;}
};
$('capture').onclick=()=>startCapture();
$('diagnose-request').onclick=()=>startCapture(true);
$('clear-capture').onclick=async()=>{
  const {bankCapture:state}=await chrome.storage.session.get('bankCapture');
  await chrome.storage.session.remove(['bankCapture','bankContract']);
  $('export-contract').hidden=true;
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

function updatePeriodHint(){
  const months=Number($('history-period').value);
  $('period-hint').textContent=`Выберите в банке ${historyPeriod(months)}. Здесь показана подсказка дат: фильтр банка автоматически не меняется.${months===6?' Если банк ограничивает период, загружайте его частями.':''}`;
}
$('history-period').onchange=()=>{updatePeriodHint();chrome.storage.local.set({historyPeriodMonths:Number($('history-period').value)});};
chrome.storage.local.get('historyPeriodMonths').then(settings=>{
  if([1,6].includes(settings.historyPeriodMonths))$('history-period').value=String(settings.historyPeriodMonths);
  updatePeriodHint();
});

async function showContract(){
  const {bankContract:contract,bankCapture:state}=await chrome.storage.session.get(['bankContract','bankCapture']);
  $('export-contract').hidden=!(contract&&state&&contract.captureId===state.expires&&state.expires>Date.now());
}
$('export-contract').onclick=async()=>{
  const {bankContract:contract,bankCapture:state}=await chrome.storage.session.get(['bankContract','bankCapture']);
  if(!contract||!state||contract.captureId!==state.expires||state.expires<Date.now())return;
  const url=URL.createObjectURL(new Blob([JSON.stringify(contract.report,null,2)],{type:'application/json'}));
  const link=document.createElement('a');link.href=url;link.download='vietinbank-request-schema.json';link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
};
chrome.storage.onChanged.addListener((changes,area)=>{if(area==='session'&&(changes.bankContract||changes.bankCapture))showContract();});
showContract();
