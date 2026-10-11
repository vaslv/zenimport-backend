import {sendNewBatches} from './send-new-batches.js';
import {dateLabel} from './date-format.js';
import {creationDialog} from './new-operations.js';
import {bankScope,bankTitle} from './bank-scope.js';
import {categorySendDialog} from './category-send-dialog.js';
import {renderReview} from './review-view.js';
import {destroyCategoryPickers} from './category-picker.js';
import {renderAccountCards,renderHistory} from './zenmoney-view.js';
import {api, settings, normalizeBaseUrl} from './api.js';
const $ = id => document.getElementById(id);
const names = {pending:'Ожидает проверки',queued:'Подтверждена',sent:'Отправлена',linked:'Связана',ignored:'Пропущена',uncertain:'Результат неизвестен',conflict:'Конфликт'};
const titles = {overview:'Обзор',zenmoney:'Данные ZenMoney',banks:'Банки','bank-settings':'Привязка счетов',review:'Операции',attention:'Требуют внимания',settings:'Подключение'};
let state = null, busy = false, connected = false, historyOffset = 0, historyPage = null, historyAccount = '', zenTab = 'accounts';
function el(tag, text, className) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(className)n.className=className; return n; }
function empty(node, text) { if(!node.childNodes.length)node.append(el('p',text,'empty')); }
function action(text, fn) { const b=el('button',text); b.dataset.apiAction=''; b.onclick=()=>run(fn); return b; }
function select(items, label) { const n=el('select'); n.setAttribute('aria-label',label); n.append(new Option(label,'')); items.forEach(i=>n.append(new Option(i.title,i.id))); return n; }
function amount(tx) { return new Intl.NumberFormat('ru-RU',{style:'currency',currency:tx.currency}).format(tx.amount_minor/(tx.currency==='VND'?1:100)); }
function feedback(text, error=false) { $('status').textContent=text; $('feedback').classList.toggle('error',error); }
function controls() {
  document.querySelectorAll('[data-api-action]').forEach(b=>b.disabled=busy||!connected);
  $('history-prev').disabled=busy||!connected||historyOffset===0;
  $('history-next').disabled=busy||!connected||!historyPage?.has_more;
  $('history-account').disabled=busy||!connected;
  $('show-archived').disabled=busy;
  $('save').disabled=busy; $('url').disabled=busy; $('token').disabled=busy;
  categoryControls();
  const creationRows=state&&bankWorkspace()?bankScope(state,selectedBank()).review.filter(r=>(!reviewAccount||r.bank_account===reviewAccount)&&(!$('review-start').value||r.date>=$('review-start').value)&&(!$('review-end').value||r.date<=$('review-end').value)):[];
  const newRows=creationRows.filter(r=>r.state==='pending'&&r.creation_draft?.valid);
  const prepared=creationRows.filter(r=>r.creation_job?.state==='prepared');
  $('creation-create').hidden=!state?.capabilities?.create||!bankWorkspace();
  $('creation-create').textContent=`Создать локальные записи · ${newRows.length}`;
  $('creation-create').disabled=busy||!connected||!newRows.length;
  $('creation-send').hidden=!state?.capabilities?.create||!bankWorkspace();
  $('creation-send').textContent='Отправить новые с категорией';
  $('creation-send').disabled=busy||!connected||!(newRows.length||prepared.length);
  $('creation-send-all').hidden=$('creation-send').hidden;
  $('creation-send-all').disabled=busy||!connected||!creationRows.some(r=>r.state==='pending'||r.creation_job?.state==='prepared');
  $('creation-verify').hidden=!state?.capabilities?.create||!creationRows.some(r=>r.creation_job&&!['sent','prepared'].includes(r.creation_job.state));

  $('send').disabled=busy||!connected||!state?.capabilities?.write||!state?.outbox?.some(x=>['queued','uncertain'].includes(x.state));
  $('demo').disabled=busy||!connected||state?.mode!=='demo';
  $('connection').textContent=busy?'Обновление…':connected?'Сервер подключён':'Нет подключения';
  $('refresh').disabled=busy; // Reconnect is possible after a network error.
}
function selectedBank(){
  const [route,id]=location.hash.slice(1).split('/');
  return ['banks','bank-settings'].includes(route)&&id?decodeURIComponent(id):'';
}
function creationScope(){return {bank:selectedBank(),account:reviewAccount||null,start:$('review-start').value||null,end:$('review-end').value||null};}
function bankWorkspace(){return location.hash.startsWith('#banks/')&&!!selectedBank();}
function categoryScopeIds(){
  if(!bankWorkspace())return null;
  return new Set((bankScope(state,selectedBank()).day_review||[])
    .filter(d=>(!reviewAccount||d.bank_account===reviewAccount)&&(!$('review-start').value||d.date>=$('review-start').value)&&(!$('review-end').value||d.date<=$('review-end').value))
    .flatMap(d=>d.zen_rows||[]).filter(z=>z.category_draft).map(z=>z.id));
}
function categoryControls(){
  const ids=state?categoryScopeIds():null;
  const count=ids?ids.size:state?.category_draft_count||0;
  const jobs=state?(bankWorkspace()?bankScope(state,selectedBank()).category_sends:state.category_sends||[]):[];
  const unresolved=jobs.some(s=>s.state!=='sent');
  $('category-send').hidden=!state?.capabilities?.category_write||location.hash==='#banks';
  $('category-verify').hidden=!state?.capabilities?.category_write||!unresolved||location.hash==='#banks';
  $('category-send-status').hidden=!unresolved||location.hash==='#banks';
  $('category-send').textContent=`Отправить категории в ZenMoney · ${count}`;
  $('category-send').disabled=busy||!connected||!count||location.hash==='#banks';
}
function navigate() {
  let name=(location.hash.slice(1)||'banks').split('/')[0]; if(!titles[name])name='overview';
  document.querySelectorAll('[data-view]').forEach(n=>n.hidden=n.dataset.view!==(name==='attention'||bankWorkspace()?'review':name));
  document.querySelectorAll('nav a').forEach(n=>{if(n.hash===`#${name==='bank-settings'?'banks':name}`)n.setAttribute('aria-current','page');else n.removeAttribute('aria-current');});
  $('title').textContent=bankWorkspace()?bankTitle(selectedBank()):titles[name];
  $('bank-workspace').hidden=!bankWorkspace();
  $('bank-settings-link').href=`#bank-settings/${encodeURIComponent(selectedBank())}`;
  $('bank-scope-note').textContent='Операции выбранного банка и только явно привязанных к нему счетов ZenMoney.';
  const attention=name==='attention'||bankWorkspace();
  $('hide-reconciled-label').hidden=!attention;
  $('hide-categorized-label').hidden=!attention;
  $('review-search').closest('label').hidden=attention;
  document.querySelector('.status-filters').hidden=attention;
  $('queue-panel').hidden=attention||(!state?.capabilities?.write&&!state?.outbox?.length);
  if(state){render();controls();if(bankWorkspace()&&state.capabilities?.create&&connected&&!busy)run(async()=>{});}
  if(name==='zenmoney')setZenTab(location.hash.split('/')[1]||zenTab);
}
function render() {
  destroyCategoryPickers($('review'));
  for(const id of ['metrics','accounts','bank-rows','review','outbox','zen','zen-accounts','categories'])$(id).replaceChildren();
  $('category-send').hidden=!state?.capabilities?.category_write;
  $('category-send').textContent=`Отправить категории в ZenMoney · ${state?.category_draft_count||0}`;
  const unresolved=(state?.category_sends||[]).filter(s=>s.state!=='sent');
  $('category-verify').hidden=!state?.capabilities?.category_write||!unresolved.length;
  $('category-send-status').hidden=!unresolved.length;
  $('category-send-status').textContent=unresolved.length?'Есть неподтверждённые результаты отправки. Проверьте результат перед повторной отправкой.':'';
  $('mode').textContent=state ? (state.mode==='demo'?'DEMO · учебные данные':'LIVE · история ZenMoney'):'Нет подключения';
  $('last-sync').textContent=state?.last_sync ? `Данные ZenMoney получены: ${new Date(state.last_sync*1000).toLocaleString('ru-RU')}`:'Данные ZenMoney ещё не получены';
  if(!state) { controls(); return; }
  for(const [label,value] of [['Банковские операции',state.review.length],['Операции ZenMoney',state.zenmoney_summary?.transactions?.count??state.zenmoney_transactions.length],['На проверке',state.review.filter(t=>t.state==='pending').length]]) {
    const card=el('div',undefined,'metric'); card.append(el('strong',String(value)),el('span',label)); $('metrics').append(card);
  }
  const count=renderAccountCards($('zen-accounts'),state,{showArchived:$('show-archived').checked,onAccount:id=>{
    run(async()=>{historyAccount=id;historyOffset=0;$('history-start').value='';$('history-end').value='';setZenTab('transactions');location.hash='zenmoney/transactions';});
  }});
  $('account-count').textContent=`Показано ${count.visible} из ${count.total} · В архиве: ${count.archived}`;
  const filter=$('history-account');filter.replaceChildren(new Option('Все счета',''));
  state.zenmoney_accounts.filter(a=>!a.deleted&&(!a.archive||$('show-archived').checked||String(a.id)===historyAccount)).forEach(a=>filter.append(new Option(a.title+(a.archive?' · архив':''),a.id)));
  if(historyAccount&&!state.zenmoney_accounts.some(a=>String(a.id)===historyAccount))filter.append(new Option('Недоступный счёт',historyAccount));
  filter.value=historyAccount;
  $('history-title').textContent=historyAccount?`Операции · ${state.zenmoney_accounts.find(a=>String(a.id)===historyAccount)?.title||'Недоступный счёт'}`:'Все операции';
  $('history-clear-account').hidden=!historyAccount;
  state.categories.forEach(t=>$('categories').append(el('span',t.title,'badge'))); empty($('categories'),'Категорий пока нет.');
  $('bank-list').replaceChildren();
  for(const bank of [...new Set(state.bank_accounts.map(a=>a.bank))].sort()){
    const card=el('article'),link=el('a',bankTitle(bank));link.href=`#banks/${encodeURIComponent(bank)}`;
    card.append(el('h3',bankTitle(bank)),el('p',`Счетов: ${state.bank_accounts.filter(a=>a.bank===bank).length}`),link);$('bank-list').append(card);
  }
  empty($('bank-list'),'Сначала импортируйте операции через иконку расширения.');
  const bankAccounts=selectedBank()?state.bank_accounts.filter(a=>a.bank===selectedBank()):state.bank_accounts;
  bankAccounts.forEach(a=>{
    const card=el('article'); card.append(el('h3',`${a.bank} / ${a.account} · ${a.currency}`));
    const target=select(state.zenmoney_accounts.filter(z=>!z.archive),'Счёт ZenMoney'); target.value=a.zenmoney_account||'';
    const lastImport=state.bank_imports?.find(i=>i.bank_account===a.id && i.capture);
    if(lastImport){const c=lastImport.capture;card.append(el('p',`${c.start_date||'Период не виден'} — ${c.end_date||''}: строки ${c.row_start}–${c.row_end} из ${c.total_rows}. ${c.complete?'Все строки выбранного поиска собраны.':'Частичный импорт.'}`,'muted'));}
    const locked=state.review.some(t=>t.bank_account===a.id&&['queued','sent','linked'].includes(t.state));
    target.disabled=locked; card.append(target);
    if(!locked)card.append(action('Сопоставить',()=>{if(!target.value)throw new Error('Выберите счёт ZenMoney.');return api(`/api/accounts/${a.id}/mapping`,{zenmoney_account:target.value},'PUT');}));
    else card.append(el('p','Привязка используется подтверждёнными операциями.','muted'));
    $('accounts').append(card);
  });
  empty($('accounts'),'Сначала импортируйте операции через иконку расширения на странице поддерживаемого банка.');
  state.review.filter(tx=>!selectedBank()||bankAccounts.some(a=>a.id===tx.bank_account)).forEach(tx=>{
    const card=el('article'); card.append(el('span',names[tx.state]||tx.state,'badge'),el('h3',`${dateLabel(tx.date)} · ${amount(tx)}`),el('p',tx.description));
    const raw=el('details'); raw.append(el('summary','Исходные поля'),el('pre',JSON.stringify(tx.raw,null,2))); card.append(raw); $('bank-rows').append(card);
  });
  empty($('bank-rows'),'Банковских операций пока нет.'); renderLedger();
  $('queue-panel').hidden=bankWorkspace()||location.hash==='#attention'||!state.capabilities?.write&&!state.outbox.length;
  state.outbox.forEach(item=>{const row=el('article');row.append(el('strong',names[item.state]||item.state),el('p',item.zen_id,'muted'));if(item.error)row.append(el('p',item.error));if(item.state==='queued')row.append(action('Вернуть на проверку',()=>api(`/api/outbox/${item.bank_transaction}/reopen`,{})));$('outbox').append(row);});
  empty($('outbox'),'Очередь пуста. Подтверждение добавляет операцию в очередь, отправка выполняется отдельно.');
  const coverage=state.zenmoney_summary?.transactions;
  $('history-coverage').textContent=coverage?.first_date?`В базе: ${dateLabel(coverage.first_date)} — ${dateLabel(coverage.last_date)}. Полнота периода ещё не подтверждена.`:'История ещё не загружена.';
  $('history-page').textContent=historyPage?`Найдено: ${historyPage.total}. Показано: ${historyPage.items.length?historyOffset+1:0}–${historyOffset+historyPage.items.length}.`:'';
  renderHistory($('zen'),state.zenmoney_transactions,state,historyAccount,fn=>run(async()=>{const request=fn();if(request)await api(request.path,request.body,request.method);}));
  $('write-note').textContent=state.mode==='demo'?'DEMO: подтверждение и отправка работают только с учебными данными.':'Связи и категории сохраняются локально. Новые записи создаются отдельно после просмотра и подтверждения.';
  controls();
}
let reviewAccount='',reviewFilter='all',reviewLimit=100;
function renderLedger(){
  if(!state)return;
  const scoped=bankWorkspace()?bankScope(state,selectedBank()):state;
  if(!scoped.bank_accounts.some(a=>a.id===reviewAccount))reviewAccount='';
  const filter=$('review-account');filter.replaceChildren(new Option(bankWorkspace()?'Все счета банка':'Все счета',''));
  for(const a of scoped.bank_accounts){const z=state.zenmoney_accounts.find(z=>z.id===a.zenmoney_account);filter.append(new Option(z?.title||`${a.bank} · ${a.account.slice(-4)}`,a.id));}
  filter.value=reviewAccount;
  if($('review-start').value&&$('review-end').value&&$('review-start').value>$('review-end').value){destroyCategoryPickers($('review'));$('review').replaceChildren(el('p','Начало периода позже конца.','empty'));return;}
  const result=renderReview($('review'),scoped,{account:reviewAccount,status:reviewFilter,query:$('review-search').value,start:$('review-start').value,end:$('review-end').value,limit:reviewLimit,workDays:location.hash==='#attention'||bankWorkspace(),attentionOnly:!bankWorkspace()||$('bank-attention-only').checked,includePending:bankWorkspace(),hideReconciled:$('hide-reconciled').checked,hideCategorized:$('hide-categorized').checked,
    onShowAll:()=>{reviewFilter='all';reviewLimit=100;$('review-search').value='';},
    onMore:()=>{reviewLimit+=100;renderLedger();controls();},
    onAction:fn=>run(async()=>{const request=fn();if(request)await api(request.path,request.body,request.method);})});
  $('review-count').textContent=bankWorkspace()?`Дней: ${result.days||0}`:location.hash==='#attention'?`Дней, требующих внимания: ${result.days||0}`:`Показано ${result.shown} из ${result.total} · Банковская история, новые сначала`;
  const labels={all:'Все',synced:'Уже в ZenMoney',new:'Новые',attention:'Проверить'};
  document.querySelectorAll('[data-review-status]').forEach(b=>{const k=b.dataset.reviewStatus;b.textContent=`${labels[k]} · ${result.counts[k]}`;b.setAttribute('aria-pressed',String(reviewFilter===k));});
}
$('bank-attention-only').onchange=()=>{renderLedger();controls();};
$('hide-categorized').onchange=()=>{renderLedger();controls();};
$('hide-reconciled').onchange=()=>{renderLedger();controls();};
$('review-account').onchange=()=>{reviewAccount=$('review-account').value;reviewLimit=100;renderLedger();controls();};
for(const id of ['review-search','review-start','review-end'])$(id).oninput=()=>{reviewLimit=100;renderLedger();controls();};
document.querySelectorAll('[data-review-status]').forEach(b=>b.onclick=()=>{reviewFilter=b.dataset.reviewStatus;reviewLimit=100;renderLedger();controls();});
async function refresh() {
  if(bankWorkspace())await api('/api/new-operations/prepare',creationScope());
  const result=await api('/api/state');
  if(!['demo','live'].includes(result.mode)||!['bank_accounts','zenmoney_accounts','categories','review','outbox','zenmoney_transactions'].every(k=>Array.isArray(result[k])))throw new Error('Сервер вернул несовместимый формат данных.');
  const params=new URLSearchParams({limit:'100',offset:String(historyOffset)});
  if(historyAccount)params.set('account',historyAccount);
  if($('history-start').value)params.set('start',$('history-start').value);
  if($('history-end').value)params.set('end',$('history-end').value);
  historyPage=await api(`/api/zenmoney/history?${params}`);
  state={...result,zenmoney_transactions:historyPage.items}; connected=true; render();
}
async function run(fn) {
  if(busy)return;busy=true;controls();feedback('Выполняется…');
  try {const result=await fn();await refresh();feedback(result?.message|| (result?.results?`Отправка завершена: ${result.results.filter(x=>x.state==='sent').length} успешно, ${result.results.filter(x=>x.state!=='sent').length} требуют внимания.`:'Данные обновлены.'));}
  catch(error){connected=false;feedback(error.message,true);}
  finally{busy=false;controls();}
}
$('settings').onsubmit=async event=>{
  event.preventDefault(); if(busy)return;
  try {
    const baseUrl=normalizeBaseUrl($('url').value),token=$('token').value.trim();
    if(token.length<24)throw new Error('API-токен должен содержать не менее 24 символов.');
    const url=new URL(baseUrl);
    if(url.protocol==='https:'&&!await chrome.permissions.request({origins:[`https://${url.hostname}/*`]}))throw new Error('Доступ к серверу не разрешён.');
    await chrome.storage.local.set({baseUrl,token});historyOffset=0;historyPage=null;historyAccount='';state=null;connected=false;render();
    await run(async()=>{});
  }catch(error){feedback(error.message,true);}
};
function setZenTab(name){
  zenTab=['accounts','transactions','categories'].includes(name)?name:'accounts';
  document.querySelectorAll('[data-zen-tab]').forEach(tab=>{
    const selected=tab.dataset.zenTab===zenTab;tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;
    $(`zen-panel-${tab.dataset.zenTab}`).hidden=!selected;
  });
}
const tabs=[...document.querySelectorAll('[data-zen-tab]')];
tabs.forEach((tab,index)=>{
  tab.onclick=()=>{setZenTab(tab.dataset.zenTab);location.hash=`zenmoney/${zenTab}`;};
  tab.onkeydown=event=>{
    let next;if(event.key==='ArrowRight')next=(index+1)%tabs.length;else if(event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=tabs.length-1;else return;
    event.preventDefault();tabs[next].click();tabs[next].focus();
  };
});
$('show-archived').onchange=()=>{if(state)render();};
$('history-account').onchange=()=>{const value=$('history-account').value;run(async()=>{historyAccount=value;historyOffset=0;});};
$('history-clear-account').onclick=()=>run(async()=>{historyAccount='';historyOffset=0;});
$('history-filter').onsubmit=event=>{event.preventDefault();run(async()=>{historyOffset=0;});};
$('history-prev').onclick=()=>run(async()=>{historyOffset=Math.max(0,historyOffset-100);});
$('history-next').onclick=()=>run(async()=>{historyOffset+=100;});
$('refresh').onclick=()=>run(async()=>{});
$('category-send').onclick=()=>run(async()=>{const preview=await api('/api/category-updates/preview',{});const ids=categoryScopeIds();categorySendDialog(ids?preview.items.filter(i=>ids.has(i.zen_id)):preview.items,state,request=>run(()=>api(request.path,request.body)));});
$('creation-create').onclick=()=>run(async()=>{const result=await api('/api/new-operations/create',creationScope());return {message:`Создано локальных записей: ${result.created.length}. Пропущено: ${result.skipped.length}.`};});
$('creation-send').onclick=()=>run(async()=>{const scope=creationScope();const preview=await api('/api/new-operations/prepare-preview',scope);creationDialog(preview.items,scope,body=>run(()=>sendNewBatches(body,b=>api('/api/new-operations/send',b))));});
$('creation-send-all').onclick=()=>run(async()=>{const scope={...creationScope(),include_uncategorized:true};const preview=await api('/api/new-operations/prepare-preview',scope);creationDialog(preview.items,scope,body=>run(()=>sendNewBatches(body,b=>api('/api/new-operations/send',b))));});
$('creation-verify').onclick=()=>run(()=>api('/api/new-operations/verify',{}));
$('category-verify').onclick=()=>run(()=>api('/api/category-updates/verify',{}));
$('sync').onclick=()=>run(()=>api('/api/zenmoney/sync',{}));
$('demo').onclick=async()=>{const config=await settings();chrome.tabs.create({url:config.baseUrl+'/demo-bank'});};
$('send').onclick=()=>{if(confirm('Отправить подтверждённые демонстрационные операции?'))run(()=>api('/api/outbox/send',{}));};
window.addEventListener('hashchange',navigate);
const config=await settings();$('url').value=config.baseUrl;$('token').value=config.token;
if(!config.token)location.hash='settings';navigate();render();
if(config.token)await run(async()=>{});
