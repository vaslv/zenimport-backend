import {api} from './api.js';
import {parseVietinBankJson} from './vietinbank-json.js';
import {activeJob} from './auto-plan.js';
import {bankContext} from './bank-context.js';
import {startSync,syncStatus,installSync} from './full-sync.js';
const origin='https://ipay.vietinbank.vn';
const ids=['inline-history-ui','inline-history-observer'];
let registration=Promise.resolve(),work=Promise.resolve(),syncing,logs=Promise.resolve();
function log(action,ok,ms){logs=logs.catch(()=>{}).then(async()=>{const {inlineDiagnostics=[]}=await chrome.storage.local.get('inlineDiagnostics');await chrome.storage.local.set({inlineDiagnostics:[...inlineDiagnostics.slice(-119),{at:Date.now(),action,ok,ms}]});});}
const totals=new Map();
async function register(){
  const {inlineEnabled}=await chrome.storage.local.get('inlineEnabled');
  const allowed=await chrome.permissions.contains({origins:[origin+'/*']});
  const existing=await chrome.scripting.getRegisteredContentScripts({ids});
  if(!inlineEnabled||!allowed){if(existing.length)await chrome.scripting.unregisterContentScripts({ids:existing.map(s=>s.id)});return;}
  const scripts=[{id:ids[0],js:['operation-form.js','inline-content.js'],world:'ISOLATED'}, {id:ids[1],js:['inline-observer-init.js','bank-observer.js','bank-session.js'],world:'MAIN'}];
  for(const s of scripts){const spec={...s,matches:[origin+'/*'],runAt:'document_start',persistAcrossSessions:true};if(existing.some(e=>e.id===s.id))await chrome.scripting.updateContentScripts([spec]);else await chrome.scripting.registerContentScripts([spec]);}
}
function install(){registration=registration.then(register).catch(()=>{});}
chrome.runtime.onInstalled.addListener(install);chrome.runtime.onStartup.addListener(install);
chrome.permissions.onRemoved.addListener(install);install();
async function scoped(account){
  const state=await api('/api/state');
  const a=state.bank_accounts.find(a=>a.bank==='vietinbank'&&a.account===account);
  return {account:a||null,categories:state.categories.filter(c=>!c.deleted),operation_catalog:state.operation_catalog,
    rows:a?state.review.filter(r=>r.bank_account===a.id):[],
    days:a?state.day_review.filter(d=>d.bank_account===a.id):[],
    sends:state.category_sends||[],operation_sends:state.operation_sends||[],last_sync:state.last_sync};
}
async function handle(m,sender){
  if(sender.id!==chrome.runtime.id)throw Error('Недоступно');
  const trusted=sender.url===chrome.runtime.getURL('popup.html');
  const toolsPage=sender.url===chrome.runtime.getURL('tools.html');
  if(['inline-popup-state','inline-new-send','inline-toggle'].includes(m.type)){
    if(!trusted)throw Error('Недоступно');
    if(m.type==='inline-toggle'){await toggle(m.enabled===true);return {};}
    if(m.type==='inline-new-send'){try{return await startSync();}catch(e){await notice(e.message);throw e;}}
    const {inlineEnabled}=await chrome.storage.local.get('inlineEnabled');
    const status=await syncStatus();
    return {enabled:!!inlineEnabled,...status};
  }
  if(m.type==='inline-enable'){
    if(!trusted&&!toolsPage)throw Error('Недоступно');
    if(!await chrome.permissions.contains({origins:[origin+'/*']}))throw Error('Разрешите доступ к VietinBank.');
    await chrome.storage.local.set({inlineEnabled:true});await register();return {};
  }
  if(sender.frameId!==0||!sender.tab)throw Error('Недоступно');
  const account=await bankContext(sender,m.account);
  if(!(await chrome.storage.local.get('inlineEnabled')).inlineEnabled)throw Error('Включите работу на странице банка в меню ZenImport.');
  if(m.type==='inline-session-status'){
    if(!['active','paused','waiting','unsupported','signed-out','refreshing','extended','error','inactive'].includes(m.status))throw Error('Неизвестный статус');
    await log('session-'+m.status,m.status!=='error',0);return {};
  }
  if(m.type==='inline-sync'){
    if(!syncing)syncing=api('/api/zenmoney/sync',{}).finally(()=>{syncing=null;});
    await syncing;
    const state=await scoped(account);
    if(state.account){await bankContext(sender,account);await api('/api/new-operations/prepare',{bank:'vietinbank',account:state.account.id});}
    return scoped(account);
  }
  if(m.type==='inline-state')return scoped(account);
  if(m.type==='inline-link'){
    const state=await scoped(account),row=state.rows.find(r=>r.id===m.id);
    if(!row||row.state!=='pending'||!row.mapped||!row.candidates.some(c=>c.id===m.zen_id))throw Error('Совпадение недоступно.');
    await bankContext(sender,account);
    await api(`/api/review/${encodeURIComponent(row.id)}/link`,{action:'link',match_id:m.zen_id,reviewed_candidates:m.candidates});return scoped(account);
  }
  if(m.type==='inline-operation'){
    const state=await scoped(account),row=state.rows.find(r=>r.id===m.id);
    const part=m.zen_id&&row?.linked&&(row.group?.zen_ids||[]).includes(m.zen_id)?state.days.flatMap(d=>d.zen_rows||[]).find(z=>z.id===m.zen_id):null;
    const model=m.zen_id?part?.operation_edit:row?.operation_edit;
    if(!model?.editable||model.definition.account_id!==state.account?.zenmoney_account||m.edit?.definition?.account_id!==model.definition.account_id)throw Error('Операция или банковский счёт недоступны.');
    const path=model.source==='bank'?`/api/new-operations/${encodeURIComponent(row.id)}/operation`:`/api/zenmoney/transactions/${encodeURIComponent(model.id)}/operation`;
    await bankContext(sender,account);await api(path,m.edit,'PUT');
    if(model.source==='zen')await sendEdit('operation',model.id);
    return scoped(account);
  }
  if(m.type==='inline-import'){
    const {autoJob}=await chrome.storage.local.get('autoJob');
    if(activeJob(autoJob))return scoped(account); // The existing collector owns this response.
    let d=m.data;if(!d||JSON.stringify(d).length>1500000||d.accountNo!==account||d.error!==false)throw Error('Ответ истории относится к другому счёту или повреждён.');
    const key=sender.tab.id+':'+account;
    if(d.currentPage===0){totals.delete(key);if(Number.isSafeInteger(d.totalRecords))totals.set(key,{total:d.totalRecords,next:1,at:Date.now()});}
    else if(d.totalRecords===0){const prior=totals.get(key);if(!prior||prior.next!==d.currentPage||Date.now()-prior.at>600000)throw Error('Нет первой страницы ответа. Повторите Search в банке.');d={...d,totalRecords:prior.total};prior.next++;}
    if(d.currentPage===0&&d.totalRecords===0&&Array.isArray(d.transactions)&&!d.transactions.length)return scoped(account);
    const payload=await parseVietinBankJson(d);await bankContext(sender,account);await api('/api/imports',payload);
    const state=await scoped(account);if(state.account){await bankContext(sender,account);await api('/api/new-operations/prepare',{bank:'vietinbank',account:state.account.id});}
    return scoped(account);
  }
  if(m.type==='inline-category'){
    const state=await scoped(account),row=state.rows.find(r=>r.id===m.id);
    if(!row||!state.categories.some(c=>c.id===m.category))throw Error('Операция или категория недоступна.');
    await bankContext(sender,account);
    if(row.state==='pending'&&row.mapped&&!row.candidates?.length&&!state.days.some(d=>(d.split_candidates||[]).some(p=>p.bank_id===row.id))){
      await api(`/api/new-operations/${encodeURIComponent(row.id)}/category`,{category_id:m.category},'PUT');
    }else if(row.linked&&row.group?.kind==='single'){
      const z=state.days.flatMap(d=>d.zen_rows||[]).find(z=>z.id===row.linked_id);
      if(!z||z.transfer||state.sends.some(s=>s.zen_id===z.id&&s.state==='uncertain'))throw Error('Нужна проверка операции в приложении.');
      if(!z.category_draft&&z.tag_ids?.length===1&&z.tag_ids[0]===m.category)return state;
      await api(`/api/zenmoney/transactions/${encodeURIComponent(z.id)}/category`,{category_id:m.category,expected_tags:z.tag_ids||[]},'PUT');
      await sendEdit('category',z.id);
    }else throw Error('Сначала уточните соответствие в приложении.');
    return scoped(account);
  }
  throw Error('Неизвестное действие');
}
chrome.tabs.onRemoved.addListener(id=>{for(const k of totals.keys())if(k.startsWith(id+':'))totals.delete(k);});
chrome.runtime.onMessage.addListener((m,sender,reply)=>{
  if(!['inline-enable','inline-toggle','inline-popup-state','inline-new-send','inline-sync','inline-state','inline-import','inline-category','inline-operation','inline-link','inline-session-status'].includes(m?.type))return;
  // Serialize writes: user edits may update an existing record; only the popup can create records.
  const started=Date.now();
  const run=()=>handle(m,sender);
  const promise=m.type==='inline-sync'?run():(work=work.catch(()=>{}).then(run));
  promise.then(data=>{log(m.type,true,Date.now()-started);reply({ok:true,data});},e=>{log(m.type,false,Date.now()-started);reply({ok:false,error:e.message});});return true;
});


async function sendEdit(kind,id){
  const base=kind==='operation'?'/api/operation-updates':'/api/category-updates';
  const preview=await api(base+'/preview',{}),item=preview.items.find(i=>i.zen_id===id);
  if(!item||item.error)throw Error(item?.error||'Данные изменились. Проверьте операцию в настройках.');
  const result=await api(base+'/send',{items:[{zen_id:id,snapshot:item.snapshot}]});
  const found=(result.results||[]).find(r=>r.zen_id===id||r.id===id);
  if(!found||found.state!=='sent')throw Error('Обновление не подтверждено. Автоматический повтор заблокирован; проверьте результат в настройках.');
}
async function toggle(enabled){
  if(enabled&&!await chrome.permissions.contains({origins:[origin+'/*']}))throw Error('Разрешите доступ к VietinBank.');
  await chrome.storage.local.set({inlineEnabled:enabled});await register();
  for(const tab of await chrome.tabs.query({url:origin+'/*'})){
    try{
      if(enabled){
        await chrome.scripting.executeScript({target:{tabId:tab.id},world:'MAIN',files:['inline-observer-init.js','bank-observer.js','bank-session.js']});
        await chrome.scripting.executeScript({target:{tabId:tab.id},files:['operation-form.js','inline-content.js']});
      }
      await chrome.scripting.executeScript({target:{tabId:tab.id},world:'MAIN',func:value=>{window.__zenImportInlineEnabled=value;if(!value)window.__zenImportStopInlineObserver?.();},args:[enabled]});
      await chrome.tabs.sendMessage(tab.id,{type:'inline-visibility',enabled});
    }catch{ /* Navigation may remove a tab between enumeration and injection. */ }
  }
}
async function notice(value){await chrome.storage.session.set({inlineSendNotice:value});return value;}
installSync(run=>{work=work.catch(()=>{}).then(run);return work;});
