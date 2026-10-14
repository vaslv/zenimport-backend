// A popup click authorizes one account, a six-month collection, then all stored missing rows.
// Bank messages never authorize creation. Persist progress before every write.
import {api} from './api.js';
import {startHistory,stopHistory} from './auto-background.js';
import {activeJob} from './auto-plan.js';
const key='fullSyncJob',alarm='zenimport-full-sync',origin='https://ipay.vietinbank.vn';
const get=async()=> (await chrome.storage.local.get(key))[key];
export const syncActive=j=>j&&['collecting','sending'].includes(j.stage);
let schedule=run=>run(),queued=false;
async function put(j){await chrome.storage.local.set({[key]:j});await chrome.storage.session.set({inlineSendNotice:j.notice});try{await chrome.tabs.sendMessage(j.tabId,{type:'inline-sync-progress',account:j.account,message:j.notice,error:j.stage==='error'});}catch{}}
async function finish(j,stage,notice){j.stage=stage;j.notice=notice;await put(j);await chrome.alarms.clear(alarm);try{await chrome.tabs.sendMessage(j.tabId,{type:'inline-refresh'});}catch{}}
async function context(){
  if(!(await chrome.storage.local.get('inlineEnabled')).inlineEnabled)throw Error('Включите интерфейс на странице банка.');
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  const u=new URL(tab?.url||'about:blank'),account=u.searchParams.get('accountNo');
  if(u.origin!==origin||u.pathname!=='/payment-history'||!/^\d{6,30}$/.test(account||''))throw Error('Откройте историю нужного счёта VietinBank.');
  const state=await api('/api/state'),a=state.bank_accounts.find(a=>a.bank==='vietinbank'&&a.account===account);
  if(!a?.zenmoney_account)throw Error('Свяжите банковский счёт с ZenMoney в настройках.');
  return {tabId:tab.id,account,accountId:a.id,zenAccount:a.zenmoney_account,state};
}
export async function syncStatus(){
  const job=await get(),busy=syncActive(job);
  let available=false,count=0;
  try{const c=await context();available=true;count=c.state.review.filter(r=>r.bank_account===c.accountId&&(r.state==='pending'||r.creation_job?.state==='prepared')).length;}catch{}
  const {inlineSendNotice}=await chrome.storage.session.get('inlineSendNotice');
  return {available,busy,count,notice:job?.notice||inlineSendNotice||''};
}
export async function startSync(){
  const old=await get();if(syncActive(old))return {notice:old.notice};
  const {autoJob}=await chrome.storage.local.get('autoJob');if(activeJob(autoJob))throw Error('История уже загружается. Дождитесь завершения.');
  const c=await context(),j={id:crypto.randomUUID(),captureId:crypto.randomUUID(),stage:'collecting',tabId:c.tabId,account:c.account,accountId:c.accountId,zenAccount:c.zenAccount,sent:0,notice:'Получаем историю счёта за шесть месяцев…'};
  await put(j);await chrome.alarms.create(alarm,{periodInMinutes:1});
  try{await startHistory(6,{...c,captureId:j.captureId});await put(j);kick();return {notice:j.notice};}
  catch(e){await finish(j,'error',e.message);throw e;}
}
async function accountState(j){
  const state=await api('/api/state'),a=state.bank_accounts.find(a=>a.id===j.accountId);
  if(!a||a.bank!=='vietinbank'||a.account!==j.account||a.zenmoney_account!==j.zenAccount)throw Error('Привязка счёта изменилась. Синхронизация остановлена.');
  return state.review.filter(r=>r.bank_account===a.id);
}
async function assertBankTab(j){
  const tab=await chrome.tabs.get(j.tabId),u=new URL(tab.url);
  if(u.origin!==origin||u.pathname!=='/payment-history'||u.searchParams.get('accountNo')!==j.account)throw Error('Счёт или страница изменились. Отправка остановлена.');
}
async function drive(){
  const j=await get();if(!syncActive(j))return;
  try{
    if(!(await chrome.storage.local.get('inlineEnabled')).inlineEnabled)throw Error('Синхронизация остановлена: интерфейс выключен.');
    if(j.stage==='collecting'){
      const {autoJob}=await chrome.storage.local.get('autoJob');
      if(autoJob?.id!==j.captureId)throw Error('Загрузка истории была заменена. Запустите синхронизацию снова.');
      if(activeJob(autoJob)){
        if(Date.now()-autoJob.updatedAt>90000)throw Error('Нет прогресса загрузки истории. Проверьте вход в банк и запустите синхронизацию снова.');
        j.notice=`Получаем историю: ${autoJob.part} из ${autoJob.parts.length} интервалов; сохранено ${autoJob.added+autoJob.duplicates} операций…`;await put(j);return;
      }
      if(autoJob.status!=='done')throw Error(autoJob.error||'История загружена не полностью. Отправка не начата.');
      j.notice='История получена. Получаем актуальные данные ZenMoney…';await put(j);
      await api('/api/zenmoney/sync',{});
      const rows=await accountState(j);
      j.ids=rows.filter(r=>r.state==='pending'||r.state==='queued').map(r=>r.id);
      j.stage='sending';j.notice=`Проверяем недостающие операции: ${j.ids.length}…`;await put(j);
    }
    // After worker suspension, verify the existing UUIDs; never blindly repeat a write.
    if(j.inflight){
      await api('/api/batch/verify',{bank:'vietinbank',account:j.accountId,creations_only:true});
      const rows=await accountState(j),byId=new Map(rows.map(r=>[r.id,r]));
      if(j.inflight.some(id=>byId.get(id)?.creation_job?.state!=='sent'))throw Error('Результат отправки не подтверждён. Проверьте результат в настройках; повтор заблокирован.');
      j.sent+=j.inflight.length;j.ids=j.ids.filter(id=>!j.inflight.includes(id));delete j.inflight;await put(j);
    }
    if(!j.ids.length){await finish(j,'done',`Синхронизация завершена. Добавлено в ZenMoney: ${j.sent}.`);return;}
    const scope={bank:'vietinbank',account:j.accountId,bank_ids:j.ids.slice(0,200),include_uncategorized:true,creations_only:true};
    await accountState(j);await assertBankTab(j);
    const preview=await api('/api/batch/preview',scope);
    const current=await accountState(j); // Includes confirmed creations from an earlier interrupted run.
    const unresolved=current.filter(r=>scope.bank_ids.includes(r.id)&&r.creation_job&&!['sent','prepared'].includes(r.creation_job.state));
    if(unresolved.length)throw Error('Есть неподтверждённые операции. Проверьте результат в настройках; повтор заблокирован.');
    const ready=preview.items.filter(i=>!i.error).slice(0,20);
    if(!ready.length){
      const window=new Set(scope.bank_ids),blocked=current.filter(r=>window.has(r.id)&&r.state!=='linked'&&r.state!=='ignored'&&r.creation_job?.state!=='sent');
      j.blocked=(j.blocked||0)+blocked.length;j.ids=j.ids.filter(id=>!window.has(id));await put(j);
    }else{
      await assertBankTab(j);
      j.inflight=ready.map(i=>i.id);j.notice=`Отправляем в ZenMoney: подтверждено ${j.sent}; осталось ${j.ids.length}…`;await put(j);
      // Restrict the actual write to the reviewed batch, not rows discovered later.
      const result=await api('/api/batch/send',{...scope,bank_ids:j.inflight,items:ready.map(i=>({kind:'creation',id:i.id,snapshot:i.snapshot}))});
      const confirmed=new Set((result.results||[]).filter(r=>r.state==='sent').map(r=>r.id));
      j.sent+=j.inflight.filter(id=>confirmed.has(id)).length;j.ids=j.ids.filter(id=>!confirmed.has(id));j.inflight=j.inflight.filter(id=>!confirmed.has(id));await put(j);
      if(j.inflight.length)throw Error('Результат части операций не подтверждён. Проверьте результат в настройках; повтор заблокирован.');
      delete j.inflight;await put(j);
    }
    if(!j.ids.length)await finish(j,'done',`Синхронизация завершена. Добавлено в ZenMoney: ${j.sent}.`+(j.blocked?` Требуют ручной сверки: ${j.blocked}.`:''));
    else kick();
  }catch(e){if(j.stage==='collecting')await stopHistory(j.captureId,e.message);await finish(j,'error',`Синхронизация остановлена. Добавлено в ZenMoney: ${j.sent}. ${e.message}`);}
}
function kick(){if(queued)return;queued=true;schedule(async()=>{queued=false;await drive();}).catch(()=>{});}
export function installSync(serialize){
  schedule=serialize;
  chrome.storage.onChanged.addListener((changes,area)=>{if(area==='local'&&changes.autoJob)kick();});
  chrome.alarms.onAlarm.addListener(a=>{if(a.name===alarm)kick();});
  // A stopped browser/updated extension interrupts collection in auto-background.
  // Sending can be recovered using the durable intent and read-only verification.
  get().then(async j=>{if(syncActive(j)){if(!await chrome.alarms.get(alarm))await chrome.alarms.create(alarm,{periodInMinutes:1});kick();}}).catch(()=>{});
}
