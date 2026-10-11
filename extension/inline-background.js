import {api} from './api.js';
import {parseVietinBankJson} from './vietinbank-json.js';
import {activeJob} from './auto-plan.js';
const origin='https://ipay.vietinbank.vn';
const ids=['inline-history-ui','inline-history-observer'];
let registration=Promise.resolve(),work=Promise.resolve(),syncing,logs=Promise.resolve();
function log(action,ok,ms){logs=logs.catch(()=>{}).then(async()=>{const {inlineDiagnostics=[]}=await chrome.storage.local.get('inlineDiagnostics');await chrome.storage.local.set({inlineDiagnostics:[...inlineDiagnostics.slice(-119),{at:Date.now(),action,ok,ms}]});});}
const totals=new Map();
function accountOf(url){try{const u=new URL(url);return u.origin===origin&&u.pathname==='/payment-history'&&/^\d{6,30}$/.test(u.searchParams.get('accountNo')||'')?u.searchParams.get('accountNo'):null;}catch{return null;}}
async function register(){
  const {inlineEnabled}=await chrome.storage.local.get('inlineEnabled');
  const allowed=await chrome.permissions.contains({origins:[origin+'/*']});
  const existing=await chrome.scripting.getRegisteredContentScripts({ids});
  if(!inlineEnabled||!allowed){if(existing.length)await chrome.scripting.unregisterContentScripts({ids:existing.map(s=>s.id)});return;}
  const scripts=[{id:ids[0],js:['inline-content.js'],world:'ISOLATED'}, {id:ids[1],js:['inline-observer-init.js','bank-observer.js'],world:'MAIN'}];
  for(const s of scripts)if(!existing.some(e=>e.id===s.id))await chrome.scripting.registerContentScripts([{...s,matches:[origin+'/*'],runAt:'document_start',persistAcrossSessions:true}]);
}
function install(){registration=registration.then(register).catch(()=>{});}
chrome.runtime.onInstalled.addListener(install);chrome.runtime.onStartup.addListener(install);
chrome.permissions.onRemoved.addListener(install);install();
async function scoped(account){
  const state=await api('/api/state');
  const a=state.bank_accounts.find(a=>a.bank==='vietinbank'&&a.account===account);
  return {account:a||null,categories:state.categories.filter(c=>!c.deleted).map(c=>({id:c.id,title:c.title})),
    rows:a?state.review.filter(r=>r.bank_account===a.id):[],
    days:a?state.day_review.filter(d=>d.bank_account===a.id):[],
    sends:state.category_sends||[],last_sync:state.last_sync};
}
async function handle(m,sender){
  if(sender.id!==chrome.runtime.id)throw Error('Недоступно');
  const trusted=sender.url===chrome.runtime.getURL('popup.html');
  if(m.type==='inline-enable'){
    if(!trusted)throw Error('Недоступно');
    if(!await chrome.permissions.contains({origins:[origin+'/*']}))throw Error('Разрешите доступ к VietinBank.');
    await chrome.storage.local.set({inlineEnabled:true});await register();return {};
  }
  if(sender.frameId!==0||!sender.tab)throw Error('Недоступно');
  const account=accountOf(sender.url),tab=await chrome.tabs.get(sender.tab.id);
  if(!account||account!==accountOf(tab.url)||account!==m.account)throw Error('Банковский счёт или страница изменились.');
  if(!(await chrome.storage.local.get('inlineEnabled')).inlineEnabled)throw Error('Включите работу на странице банка в меню ZenImport.');
  if(m.type==='inline-sync'){
    if(!syncing)syncing=api('/api/zenmoney/sync',{}).finally(()=>{syncing=null;});
    await syncing;
    const state=await scoped(account);
    if(state.account)await api('/api/new-operations/prepare',{bank:'vietinbank',account:state.account.id});
    return scoped(account);
  }
  if(m.type==='inline-state')return scoped(account);
  if(m.type==='inline-import'){
    const {autoJob}=await chrome.storage.local.get('autoJob');
    if(activeJob(autoJob))return scoped(account); // The existing collector owns this response.
    let d=m.data;if(!d||JSON.stringify(d).length>1500000||d.accountNo!==account||d.error!==false)throw Error('Ответ истории относится к другому счёту или повреждён.');
    const key=sender.tab.id+':'+account;
    if(d.currentPage===0){totals.delete(key);if(Number.isSafeInteger(d.totalRecords))totals.set(key,{total:d.totalRecords,next:1,at:Date.now()});}
    else if(d.totalRecords===0){const prior=totals.get(key);if(!prior||prior.next!==d.currentPage||Date.now()-prior.at>600000)throw Error('Нет первой страницы ответа. Повторите Search в банке.');d={...d,totalRecords:prior.total};prior.next++;}
    if(d.currentPage===0&&d.totalRecords===0&&Array.isArray(d.transactions)&&!d.transactions.length)return scoped(account);
    const payload=await parseVietinBankJson(d);await api('/api/imports',payload);
    const state=await scoped(account);if(state.account)await api('/api/new-operations/prepare',{bank:'vietinbank',account:state.account.id});
    return scoped(account);
  }
  if(m.type==='inline-category'){
    const state=await scoped(account),row=state.rows.find(r=>r.id===m.id);
    if(!row||!state.categories.some(c=>c.id===m.category))throw Error('Операция или категория недоступна.');
    if(row.state==='pending'&&row.mapped&&!row.candidates?.length&&!state.days.some(d=>(d.split_candidates||[]).some(p=>p.bank_id===row.id))){
      await api(`/api/new-operations/${encodeURIComponent(row.id)}/category`,{category_id:m.category},'PUT');
    }else if(row.linked&&row.group?.kind==='single'){
      const z=state.days.flatMap(d=>d.zen_rows||[]).find(z=>z.id===row.linked_id);
      if(!z||z.transfer||state.sends.some(s=>s.zen_id===z.id&&s.state==='uncertain'))throw Error('Нужна проверка операции в приложении.');
      await api(`/api/zenmoney/transactions/${encodeURIComponent(z.id)}/category`,{category_id:m.category,expected_tags:z.tag_ids||[]},'PUT');
    }else throw Error('Сначала уточните соответствие в приложении.');
    return scoped(account);
  }
  throw Error('Неизвестное действие');
}
chrome.tabs.onRemoved.addListener(id=>{for(const k of totals.keys())if(k.startsWith(id+':'))totals.delete(k);});
chrome.runtime.onMessage.addListener((m,sender,reply)=>{
  if(!['inline-enable','inline-sync','inline-state','inline-import','inline-category'].includes(m?.type))return;
  // Serialize imports and local edits; no content-script action can send to ZenMoney.
  const started=Date.now();
  const run=()=>handle(m,sender);
  const promise=m.type==='inline-sync'?run():(work=work.catch(()=>{}).then(run));
  promise.then(data=>{log(m.type,true,Date.now()-started);reply({ok:true,data});},e=>{log(m.type,false,Date.now()-started);reply({ok:false,error:e.message});});return true;
});
