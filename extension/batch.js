import {sendNewBatches} from './send-new-batches.js';
import {api} from './api.js';
import {changeSummary} from './operation-review.js';
import {dateLabel} from './date-format.js';
import {formatMoney} from './zenmoney-view.js';
const $=id=>document.getElementById(id),el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
const query=new URLSearchParams(location.search),scope={bank:query.get('bank'),account:query.get('account')};
const mode=query.get('mode');
if(mode==='all'||mode==='categorized'){scope.creations_only=true;scope.include_uncategorized=mode==='all';}
let working=false,lastItems=[];
const labels={sent:'Подтверждено в ZenMoney',prepared:'Подготовлено локально',uncertain:'Результат не подтверждён — проверьте повторно',conflict:'Конфликт — требуется сверка',not_applied:'Изменение не применено'};
function showResults(data){
  $('results').replaceChildren();
  for(const item of data.results){const prior=lastItems.find(i=>i.kind===item.kind&&i.id===item.id);const row=el('p',`${item.kind==='creation'?'Новая операция':item.kind==='operation'?'Тип операции':'Категория'} · ${prior?.description||prior?.payee||item.id} · ${labels[item.state]||item.state}${item.error?' · '+item.error:''}`);$('results').append(row);}
  $('batch-status').textContent=`Подтверждено: ${data.results.filter(i=>i.state==='sent').length} из ${data.results.length}.`;
  if(data.message)$('batch-status').textContent=data.message;
}
async function run(fn){if(working)return;working=true;$('preview').disabled=$('verify').disabled=$('retry').disabled=true;try{await fn();}catch(e){$('batch-status').textContent=e.message+' Если отправка началась, нажмите «Проверить результат отправки».';}finally{working=false;$('preview').disabled=$('verify').disabled=$('retry').disabled=false;}}
function review(items){
  lastItems=items;const dialog=el('dialog');dialog.className='pairs-review-dialog';dialog.setAttribute('aria-label','Общая отправка в ZenMoney');
  const head=el('div');head.className='pairs-review-header';head.append(el('h2','Проверьте изменения перед отправкой'),el('p',scope.creations_only?(scope.include_uncategorized?'Новые записи, включая записи без категории.':'Только новые записи с категорией.')+' Будут созданы операции выбранных типов. Отправка выполняется последовательными пакетами по 20 операций.':'Новые записи, категории, переводы и долги будут отправлены одним запросом. Проверьте оба счёта, суммы и человека. До 20 операций за отправку.'));
  const list=el('div');list.className='pairs-review-list';const selected=new Set();
  const send=el('button'),cancel=el('button','Отмена');cancel.autofocus=true;
  const update=()=>{send.textContent=`Отправить в ZenMoney · ${selected.size}`;send.disabled=!selected.size||(!scope.creations_only&&selected.size>20);};
  items.forEach((item,index)=>{
    const card=el('article');card.className='creation-item';const label=el('label'),check=el('input');check.type='checkbox';check.disabled=!!item.error;check.checked=!item.error&&(scope.creations_only||selected.size<20);if(check.checked)selected.add(index);
    check.onchange=()=>{check.checked?selected.add(index):selected.delete(index);update();};
    const amount=item.kind==='creation'?formatMoney(String(item.amount_minor/(item.currency==='VND'?1:100)),item.currency):formatMoney(String(Number(item.income||0)-Number(item.outcome||0)),item.currency||'');
    label.append(check,document.createTextNode(`${item.kind==='creation'?'Создать':item.kind==='operation'?'Изменить тип':'Изменить категорию'} · ${dateLabel(item.date)} · ${item.account}${item.kind==='operation'?'':' · '+amount}`));
    card.append(label,el('p',item.description||item.payee||'Без описания'),el('p',changeSummary(item)));
    if(item.before_details)card.append(el('p','Было: '+changeSummary({...item,details:item.before_details,type_label:'Текущее движение'})));
    if(item.error)card.append(el('p',item.error));list.append(card);
  });
  if(!items.length)list.append(el('p','Нет подготовленных изменений для этого счёта.'));
  send.onclick=()=>{if(send.disabled)return;const chosen=items.filter((_,i)=>selected.has(i)).map(i=>({kind:i.kind,id:i.id,snapshot:i.snapshot}));dialog.close();run(async()=>{$('batch-status').textContent='Отправляем и проверяем результат…';const body={...scope,items:chosen};showResults(await (scope.creations_only?sendNewBatches(body,b=>api('/api/batch/send',b)):api('/api/batch/send',body)));});};
  cancel.onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove(),{once:true});const footer=el('div');footer.className='pairs-review-footer';footer.append(cancel,send);update();dialog.append(head,list,footer);document.body.append(dialog);dialog.showModal();
}
$('preview').onclick=()=>run(async()=>{$('batch-status').textContent='Получаем свежие данные и готовим локальные записи…';const data=await api('/api/batch/preview',scope);$('batch-status').textContent=`Подготовлено изменений: ${data.items.filter(i=>!i.error).length}.`;review(data.items);});
$('retry').onclick=()=>run(async()=>{
  $('batch-status').textContent='Проверяем полную базу ZenMoney. Повтор будет возможен только после просмотра и подтверждения…';
  scope.creations_only=true;scope.include_uncategorized=true;
  const data=await api('/api/batch/retry-preview',scope);
  $('batch-status').textContent='Подготовлены только отсутствующие записи. Их исходные идентификаторы сохранены. Проверьте список перед повторной отправкой.';
  review(data.items);
});
$('verify').onclick=()=>run(async()=>{$('batch-status').textContent='Проверяем фактические данные ZenMoney без повторной отправки…';showResults(await api('/api/batch/verify',scope));});
if(scope.bank&&scope.account){$('scope').textContent=`Банк: ${scope.bank}. Отправка только для выбранного банковского счёта.`;$('preview').click();}
else{$('batch-status').textContent='Откройте отправку из меню расширения на странице истории банка.';$('preview').disabled=$('verify').disabled=$('retry').disabled=true;}
