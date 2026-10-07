import {progress} from './capture-progress.js';
import {api} from './api.js';
import {historyPeriod} from './history-period.js';
import {parseVietinBankJson} from './vietinbank-json.js';
let payload, restoreGeneration=0;
const $ = id => document.getElementById(id);
$('extension-version').textContent=chrome.runtime.getManifest().version;
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
  $('status').textContent='Сохраняю операции в локальную базу…';
  const {bankCapture:importState}=await chrome.storage.session.get('bankCapture');
  const tracked=importState?.payload?.transactions?.[0]?.external_id===payload?.transactions?.[0]?.external_id&&importState?.payload?.account===payload?.account;
  if(tracked)await progress('Popup',importState.expires,'saving');
  try {
    const result = await api('/api/imports', payload);
    if(tracked)await progress('Popup',importState.expires,'saved');
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
  const generation=++restoreGeneration;
  const {bankCapture:state}=await chrome.storage.session.get('bankCapture');
  if(!state)return;
  $('clear-capture').hidden=false;
  payload=undefined;$('import').hidden=$('debug').hidden=true;
  if(state.expires<Date.now()&&['waiting','starting'].includes(state.status)) {await progress('Popup',state.expires,'timeout');await chrome.storage.session.set({bankCapture:{...state,status:'error',error:'Время ожидания истекло. Запустите получение заново.'}});return;}
  if(state.status==='starting'){$('status').textContent='Запускаю получение истории…';return;}
  if(state.status==='ready')showJson(state.payload);
  else if(state.status==='error') $('status').textContent=state.error;
  else {
    const tools=await chrome.runtime.sendMessage({type:'devtools-status',tabId:state.tabId}).catch(()=>null);
    if(generation!==restoreGeneration)return;
    $('status').textContent='Ожидаю новый ответ истории. '+(tools?.connected?'Захват через Network подключён. ':'Для резервного захвата откройте DevTools (Network) в банковской вкладке. ')+(Date.now()-(state.startedAt||0)>15000?'Ответ ещё не получен: измените период и нажмите Search; проверьте, появился ли новый getHistTransactions в Network. ':'Закройте это окно, нажмите Search в банке, затем снова откройте расширение. ');
  }
}
async function startCapture(diagnoseRequest=false){
  $('manual-capture').disabled=$('diagnose-request').disabled=true;payload=undefined;$('import').hidden=$('debug').hidden=true;
  $('status').textContent='Запуск принят. Проверяю банковскую вкладку…';
  const startedAt=Date.now(),id=startedAt+600000;
  await chrome.storage.session.set({bankCapture:{diagnoseRequest,status:'starting',startedAt,expires:id}});
  await progress('Popup',id,'start');
  $('capture-progress').open=true;
  try {
    const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
    if(!tab?.url?.startsWith('https://ipay.vietinbank.vn/payment-history'))throw Error('Откройте историю счёта VietinBank.');
    await progress('Popup',id,'tab');
    if(diagnoseRequest){
      const tools=await chrome.runtime.sendMessage({type:'devtools-status',tabId:tab.id}).catch(()=>null);
      if(!tools?.connected)throw Error('Для схемы запроса откройте DevTools → Network в банковской вкладке. После обновления расширения переоткройте DevTools.');
    }
    if(diagnoseRequest)await progress('Popup',id,'network');
    await chrome.storage.local.set({historyPeriodMonths:Number($('history-period').value)});
    await chrome.storage.session.set({bankCapture:{tabId:tab.id,diagnoseRequest,status:'starting',startedAt,expires:id}});
    if(diagnoseRequest){
      await chrome.scripting.executeScript({target:{tabId:tab.id},world:'MAIN',func:()=>window.__zenImportStopObserver?.()});
    }else{
      await chrome.scripting.executeScript({target:{tabId:tab.id},files:['bank-bridge.js']});
      await chrome.scripting.executeScript({target:{tabId:tab.id},world:'MAIN',files:['bank-observer.js']});
    }
    await chrome.storage.session.set({bankCapture:{tabId:tab.id,diagnoseRequest,status:'waiting',startedAt,expires:id}});
    await progress('Popup',id,diagnoseRequest?'waiting':'observer');
    if(!diagnoseRequest)await progress('Popup',id,'waiting');
    await restoreCapture();
  }catch(error){await progress('Popup',id,'error');await chrome.storage.session.set({bankCapture:{diagnoseRequest,status:'error',startedAt,expires:id,error:error.message}});$('status').textContent=error.message;}
  finally{$('manual-capture').disabled=$('diagnose-request').disabled=false;}
};
$('manual-capture').onclick=()=>startCapture();
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
  $('period-hint').textContent=`Период: ${historyPeriod(months)}. Расширение выберет даты частями до 30 дней, соберёт страницы и сохранит операции локально. Меню можно закрыть; банковскую вкладку оставьте открытой.`;
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
  $('status').textContent='Скачивание схемы запущено. Файл: vietinbank-request-schema.json';
  await progress('Popup',state.expires,'download');
  setTimeout(()=>URL.revokeObjectURL(url),1000);
};
chrome.storage.onChanged.addListener((changes,area)=>{if(area==='session'&&(changes.bankContract||changes.bankCapture))showContract();});
showContract();

async function renderProgress(){
  const data=await chrome.storage.session.get(['bankCapture','bankProgressPopup','bankProgressNetwork','bankProgressWorker']);
  const state=data.bankCapture;
  const events=state?['Popup','Network','Worker'].flatMap(source=>data['bankProgress'+source]?.id===state.expires?data['bankProgress'+source].events:[]):[];
  $('capture-progress').hidden=!events.length;
  $('capture-steps').replaceChildren(...events.sort((a,b)=>a.at-b.at).map(e=>{
    const li=document.createElement('li');li.textContent=`${new Date(e.at).toLocaleTimeString('ru-RU')} · ${e.label}`;return li;
  }));
  const waiting=state&&['starting','waiting'].includes(state.status);
  $('capture-age').hidden=!waiting;
  if(waiting)$('capture-age').textContent=`Ожидание: ${Math.floor((Date.now()-state.startedAt)/1000)} с. ${events.some(e=>e.stage==='response')?'Ответ замечен, обрабатываю данные.':'Получение ответа ещё не подтверждено.'}`;
  $('status').dataset.phase=state?.status||'idle';
}
chrome.storage.onChanged.addListener((changes,area)=>{if(area==='session'){renderProgress();}});
setInterval(async()=>{
  const {bankCapture:state}=await chrome.storage.session.get('bankCapture');
  if(state&&['starting','waiting'].includes(state.status)){await restoreCapture();await renderProgress();}
},2000);
renderProgress();

// Persisted automatic job survives popup closure; only metadata goes into its journal.
import {activeJob,publicJob} from './auto-plan.js';
async function renderAuto(){
  const reply=await chrome.runtime.sendMessage({type:'auto-status'}).catch(()=>null);
  const j=reply?.job;
  $('auto-progress').hidden=!j;if(!j)return;
  const running=activeJob(j);
  $('capture').disabled=running;$('history-period').disabled=running;
  $('manual-capture').disabled=$('diagnose-request').disabled=running;
  $('auto-cancel').hidden=!running;
  $('auto-meter').max=j.parts.length;$('auto-meter').value=j.completed.length;
  const names={starting:'Запуск',navigating:'Открываю историю',running:'Собираю историю',saving:'Сохраняю страницу',done:'Весь период сохранён',error:'Остановлено: период загружен не полностью',cancelled:'Отменено',needs_login:'Требуется вход в банк'};
  $('auto-status').textContent=`${names[j.status]||j.status}. Интервалы: ${j.completed.length}/${j.parts.length}. Добавлено: ${j.added}, уже были: ${j.duplicates}. ${j.current?`Текущий интервал: ${j.current.rows}/${j.current.total??'?'} операций. `:''}${j.error||''}`;
  $('auto-events').replaceChildren(...j.events.slice(-80).map(e=>{const li=document.createElement('li');const {at,label,stage,...details}=e;li.textContent=`${new Date(at).toLocaleTimeString('ru-RU')} · ${label} ${Object.entries(details).map(([k,v])=>k+'='+v).join(' ')}`;return li;}));
}
$('capture').onclick=async()=>{
  $('capture').disabled=true;$('status').textContent='Запуск принят. Проверяю доступ к банковской вкладке…';
  try{
    // Request in the click handler, before asynchronous work loses the user gesture.
    const granted=await chrome.permissions.request({origins:['https://ipay.vietinbank.vn/*']});
    if(!granted)throw Error('Для автоматического перехода нужен доступ к вкладке VietinBank.');
    ++restoreGeneration;await chrome.storage.session.remove(['bankCapture','bankContract']);payload=undefined;$('import').hidden=$('debug').hidden=true;await renderProgress();
    const r=await chrome.runtime.sendMessage({type:'auto-start',months:Number($('history-period').value)});
    if(!r?.ok)throw Error(r?.error||'Загрузчик не ответил.');
    $('status').textContent='Задача запущена. Ход выполнения и результат показаны ниже. Меню можно закрыть.';
  }catch(e){$('status').textContent=e.message;}
  finally{$('capture').disabled=false;await renderAuto();}
};
$('auto-cancel').onclick=async()=>{await chrome.runtime.sendMessage({type:'auto-cancel'});await renderAuto();};
$('auto-export').onclick=async()=>{
  const {autoJob,previousAutoJob}=await chrome.storage.local.get(['autoJob','previousAutoJob']);
  const report={version:chrome.runtime.getManifest().version,current:publicJob(autoJob),previous:publicJob(previousAutoJob)};
  const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download='zenimport-actions.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
chrome.storage.onChanged.addListener((changes,area)=>{if(area==='local'&&changes.autoJob)renderAuto();});
renderAuto();

setInterval(renderAuto,5000);
