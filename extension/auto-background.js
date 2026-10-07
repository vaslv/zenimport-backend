import {api} from './api.js';
import {parseVietinBankJson} from './vietinbank-json.js';
import {planHistory,activeJob,jobLabels} from './auto-plan.js';
const origin='https://ipay.vietinbank.vn';
let queue=Promise.resolve();
const get=async()=> (await chrome.storage.local.get('autoJob')).autoJob;
const put=j=>chrome.storage.local.set({autoJob:j});
function log(j,stage,details={}){
  if(!jobLabels[stage])throw Error('Неизвестный этап загрузчика.');
  const safe={};for(const k of ['part','page','total','rows','added','duplicates','ms','polls','status','bytes','from','to','action']){
    const v=details[k];if(typeof v==='number'&&Number.isFinite(v))safe[k]=v;
    else if(typeof v==='string'&&/^[a-zA-Z0-9_ .:/-]{1,90}$/.test(v))safe[k]=v;
  }
  if(j.events.length>=4000&&!['error','cancel'].includes(stage))throw Error('Достигнут предел журнала; уменьшите период.');
  j.events.push({at:Date.now(),stage,label:jobLabels[stage],...safe});j.updatedAt=Date.now();
}
async function stop(j,error,status='error'){await chrome.scripting.unregisterContentScripts({ids:['auto-history-observer']}).catch(()=>{});j.status=status;j.error=error;log(j,status==='cancelled'?'cancel':'error');await put(j);await chrome.storage.session.remove('autoSeen');}
async function attach(j){
  if(!activeJob(j)||j.status!=='navigating')return;
  const tab=await chrome.tabs.get(j.tabId);
  if(tab.url?.startsWith(origin+'/login')){await stop(j,'Банк требует повторного входа. Войдите и нажмите «Загрузить историю» ещё раз.','needs_login');return;}
  if(tab.url!==j.url){await stop(j,'Открыта другая банковская страница. Загрузка остановлена.');return;}
  j.status='running';log(j,'attached');await put(j);
  await chrome.scripting.executeScript({target:{tabId:j.tabId},func:config=>{globalThis.__zenImportAutoConfig=config;},args:[{id:j.id,account:j.account,parts:j.parts}]});
  await chrome.scripting.executeScript({target:{tabId:j.tabId},files:['auto-content.js']});
}
async function start(months){
  const previous=await get();if(activeJob(previous))throw Error('Загрузка уже выполняется. Сначала остановите её.');
  // Preserve the preceding diagnostic run without accumulating financial payloads.
  if(previous)await chrome.storage.local.set({previousAutoJob:previous});
  const plan=planHistory(months),j={id:crypto.randomUUID(),status:'starting',startedAt:Date.now(),updatedAt:Date.now(),...plan,part:0,added:0,duplicates:0,completed:[],events:[]};
  log(j,'start');await put(j);await chrome.storage.session.remove('autoSeen');
  try{
    const [active]=await chrome.tabs.query({active:true,currentWindow:true});
    const banks=await chrome.tabs.query({url:origin+'/*'});
    let tab=active?.url?.startsWith(origin+'/')?active:banks[0];
    let account=tab?.url?.startsWith(origin+'/payment-history')?new URL(tab.url).searchParams.get('accountNo'):null;
    if(!account){const state=await api('/api/state');const accounts=state.bank_accounts.filter(a=>a.bank==='vietinbank');if(accounts.length!==1)throw Error('Откройте историю нужного счёта в банке для первого запуска.');account=accounts[0].account;}
    if(!/^\d{6,30}$/.test(account))throw Error('Неверный номер банковского счёта.');
    j.account=account;j.url=origin+'/payment-history?accountNo='+encodeURIComponent(account);
    const registered=await chrome.scripting.getRegisteredContentScripts({ids:['auto-history-observer']});
    if(registered.length)await chrome.scripting.unregisterContentScripts({ids:['auto-history-observer']});
    await chrome.scripting.registerContentScripts([{id:'auto-history-observer',matches:[origin+'/payment-history*'],js:['auto-observer-init.js','bank-observer.js'],world:'MAIN',runAt:'document_start',persistAcrossSessions:false}]);
    // Create a blank tab first so the job exists before navigation completes.
    if(!tab)tab=await chrome.tabs.create({url:'about:blank',active:true});
    j.tabId=tab.id;j.status='navigating';log(j,'navigation');await put(j);
    await chrome.tabs.update(tab.id,{url:j.url,active:true});
    return {ok:true};
  }catch(e){await stop(j,e.message);throw e;}
}
async function handle(message,sender){
  const trusted=sender.id===chrome.runtime.id&&sender.url?.startsWith(chrome.runtime.getURL(''));
  if(message.type==='auto-start'){if(!trusted)throw Error('Недоступно');return start(message.months);}
  let j=await get();
  if(message.type==='auto-status'){if(!trusted)throw Error('Недоступно');if(activeJob(j)&&Date.now()-j.updatedAt>90000)await stop(j,'Более 90 секунд нет прогресса. Запустите загрузку снова; сохранённые строки не продублируются.');return {ok:true,job:j};}
  if(message.type==='auto-cancel'){if(!trusted)throw Error('Недоступно');if(activeJob(j))await stop(j,'Остановлено пользователем. Уже сохранённые страницы остаются в базе.','cancelled');return {ok:true};}
  if(!j||!activeJob(j))throw Error('Загрузка остановлена.');
  if(message.type==='auto-bank-response'){
    if(sender.url!==chrome.runtime.getURL('devtools.html')||message.tabId!==j.tabId)return {ok:false};
    log(j,'network');await put(j);
    await chrome.tabs.sendMessage(j.tabId,{type:'auto-bank-data',id:j.id,data:message.data,error:message.error,startedAt:message.startedAt});return {ok:true};
  }
  if(sender.id!==chrome.runtime.id||sender.tab?.id!==j.tabId||sender.frameId!==0||sender.url!==j.url||message.id!==j.id)throw Error('Контекст загрузки изменился.');
  if(Date.now()-j.startedAt>20*60000){await stop(j,'Превышено время запуска. Повторите загрузку меньшего периода.');throw Error(j.error);}
  if(message.type==='auto-check')return {ok:true};
  if(message.type==='auto-event'){log(j,message.stage,message.details);await put(j);return {ok:true};}
  if(message.type==='auto-error'){await stop(j,typeof message.error==='string'?message.error.slice(0,250):'Ошибка страницы банка.');return {ok:true};}
  if(message.type==='auto-begin'){
    if(message.part!==j.part)throw Error('Нарушен порядок интервалов.');
    j.current={rows:0,total:null,page:0};await chrome.storage.session.set({autoSeen:{id:j.id,ids:[]}});await put(j);return {ok:true};
  }
  if(message.type==='auto-page'){
    if(!j.current||message.part!==j.part)throw Error('Неожиданный интервал.');
    const d=message.data,period=j.parts[j.part];
    if(d?.accountNo!==j.account||d.error!==false||d.currentPage!==j.current.page)throw Error('Ответ другого счёта, страницы или ошибка банка.');
    if(j.current.total!==null&&d.totalRecords!==j.current.total)throw Error('Число операций изменилось во время загрузки. Повторите интервал.');
    if(d.totalRecords===0){
      if(d.currentPage!==0||!Array.isArray(d.transactions)||d.transactions.length!==0)throw Error('Некорректный пустой ответ.');
      j.current.total=0;log(j,'response',{part:j.part,total:0,rows:0});await put(j);return {ok:true,rows:0,total:0};
    }
    const payload=await parseVietinBankJson(d);
    if(payload.capture.row_start!==j.current.rows+1)throw Error('Пропуск или повтор страниц API.');
    if(payload.transactions.some(t=>t.date<period.from||t.date>period.to))throw Error('Ответ не соответствует выбранному периоду.');
    const seen=(await chrome.storage.session.get('autoSeen')).autoSeen;
    if(seen?.id!==j.id)throw Error('Прогресс был сброшен; начните загрузку заново.');
    const ids=new Set(seen.ids);for(const t of payload.transactions){if(ids.has(t.external_id))throw Error('Операция повторилась на разных страницах.');ids.add(t.external_id);}
    payload.capture.start_date=period.from;payload.capture.end_date=period.to;
    log(j,'response',{part:j.part,page:d.currentPage,total:d.totalRecords,rows:d.transactions.length});j.status='saving';await put(j);
    const saveStart=Date.now(),result=await api('/api/imports',payload);
    j.status='running';j.added+=result.added;j.duplicates+=result.duplicates;j.current={rows:payload.capture.row_end,total:d.totalRecords,page:j.current.page+1};
    log(j,'save',{part:j.part,page:d.currentPage,ms:Date.now()-saveStart,added:result.added,duplicates:result.duplicates});
    await chrome.storage.session.set({autoSeen:{id:j.id,ids:[...ids]}});await put(j);
    return {ok:true,rows:j.current.rows,total:j.current.total};
  }
  if(message.type==='auto-end'){
    if(!j.current||j.current.total===null||j.current.rows!==j.current.total||message.part!==j.part)throw Error('Интервал загружен не полностью.');
    j.completed.push({...j.parts[j.part],rows:j.current.rows});log(j,'segment',{part:j.part,rows:j.current.rows});j.part++;delete j.current;
    if(j.part===j.parts.length){await chrome.scripting.unregisterContentScripts({ids:['auto-history-observer']});j.status='done';log(j,'done');await chrome.storage.session.remove('autoSeen');}
    await put(j);return {ok:true};
  }
  throw Error('Неизвестное действие загрузчика.');
}
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if(!message?.type?.startsWith('auto-'))return;
  queue=queue.catch(()=>{}).then(()=>handle(message,sender)).then(reply,async error=>{
    // Validation/import errors must leave a persisted partial result.
    const j=await get();if(j&&activeJob(j)&&message.id===j.id)await stop(j,error.message);
    reply({ok:false,error:error.message});
  });return true;
});
chrome.tabs.onUpdated.addListener((tabId,change,tab)=>{
  queue=queue.catch(()=>{}).then(async()=>{
    const j=await get();if(!activeJob(j)||j.tabId!==tabId)return;
    if(j.status==='navigating'&&change.status==='complete')return attach(j);
    if(j.status!=='navigating'&&(change.status==='loading'||(change.url&&change.url!==j.url)))await stop(j,'Банковская вкладка перезагружена или сессия завершилась. Войдите и запустите загрузку заново.');
  }).catch(async e=>{const j=await get();if(activeJob(j))await stop(j,e.message);});
});
chrome.tabs.onRemoved.addListener(tabId=>{queue=queue.catch(()=>{}).then(async()=>{const j=await get();if(activeJob(j)&&j.tabId===tabId)await stop(j,'Банковская вкладка закрыта.');});});
// After a browser restart, never claim a suspended import is still running.
chrome.runtime.onStartup.addListener(()=>{queue=queue.catch(()=>{}).then(async()=>{const j=await get();if(activeJob(j))await stop(j,'Браузер перезапущен. Запустите загрузку снова; повторы не добавятся.');});});

chrome.runtime.onInstalled.addListener(()=>{queue=queue.catch(()=>{}).then(async()=>{const j=await get();if(activeJob(j))await stop(j,'Расширение обновлено. Запустите загрузку снова; повторы не добавятся.');});});
