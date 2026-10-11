import {api} from './api.js';
import {dateLabel} from './date-format.js';
import {formatMoney} from './zenmoney-view.js';
const $=id=>document.getElementById(id),el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
const query=new URLSearchParams(location.search),scope={bank:query.get('bank'),account:query.get('account')};
let working=false,lastItems=[];
const labels={sent:'Подтверждено в ZenMoney',prepared:'Подготовлено локально',uncertain:'Результат не подтверждён — проверьте повторно',conflict:'Конфликт — требуется сверка',not_applied:'Изменение не применено'};
function showResults(data){
  $('results').replaceChildren();
  for(const item of data.results){const prior=lastItems.find(i=>i.kind===item.kind&&i.id===item.id);const row=el('p',`${item.kind==='creation'?'Новая операция':'Категория'} · ${prior?.description||prior?.payee||item.id} · ${labels[item.state]||item.state}${item.error?' · '+item.error:''}`);$('results').append(row);}
  $('batch-status').textContent=`Подтверждено: ${data.results.filter(i=>i.state==='sent').length} из ${data.results.length}.`;
}
async function run(fn){if(working)return;working=true;$('preview').disabled=$('verify').disabled=true;try{await fn();}catch(e){$('batch-status').textContent=e.message+' Если отправка началась, нажмите «Проверить результат отправки».';}finally{working=false;$('preview').disabled=$('verify').disabled=false;}}
function review(items){
  lastItems=items;const dialog=el('dialog');dialog.className='pairs-review-dialog';dialog.setAttribute('aria-label','Общая отправка в ZenMoney');
  const head=el('div');head.className='pairs-review-header';head.append(el('h2','Проверьте изменения перед отправкой'),el('p','Новые записи и изменения категорий будут отправлены одним запросом. До 20 операций за отправку. Без категории записи не создаются.'));
  const list=el('div');list.className='pairs-review-list';const selected=new Set();
  const send=el('button'),cancel=el('button','Отмена');cancel.autofocus=true;
  const update=()=>{send.textContent=`Отправить в ZenMoney · ${selected.size}`;send.disabled=!selected.size||selected.size>20;};
  items.forEach((item,index)=>{
    const card=el('article');card.className='creation-item';const label=el('label'),check=el('input');check.type='checkbox';check.disabled=!!item.error;check.checked=!item.error&&selected.size<20;if(check.checked)selected.add(index);
    check.onchange=()=>{check.checked?selected.add(index):selected.delete(index);update();};
    const amount=item.kind==='creation'?formatMoney(String(item.amount_minor/(item.currency==='VND'?1:100)),item.currency):formatMoney(String(Number(item.income||0)-Number(item.outcome||0)),item.currency||'');
    label.append(check,document.createTextNode(`${item.kind==='creation'?'Создать':'Изменить категорию'} · ${dateLabel(item.date)} · ${item.account} · ${amount}`));
    card.append(label,el('p',item.description||item.payee||'Без описания'),el('p',`${item.kind==='category'?(item.before_categories.join(', ')||'Без категории')+' → ':''}${item.category||'Не выбрана'}`));
    if(item.error)card.append(el('p',item.error));list.append(card);
  });
  if(!items.length)list.append(el('p','Нет подготовленных изменений для этого счёта.'));
  send.onclick=()=>{if(send.disabled)return;const chosen=items.filter((_,i)=>selected.has(i)).map(i=>({kind:i.kind,id:i.id,snapshot:i.snapshot}));dialog.close();run(async()=>{$('batch-status').textContent='Отправляем и проверяем результат…';showResults(await api('/api/batch/send',{...scope,items:chosen}));});};
  cancel.onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove(),{once:true});const footer=el('div');footer.className='pairs-review-footer';footer.append(cancel,send);update();dialog.append(head,list,footer);document.body.append(dialog);dialog.showModal();
}
$('preview').onclick=()=>run(async()=>{$('batch-status').textContent='Получаем свежие данные и готовим локальные записи…';const data=await api('/api/batch/preview',scope);$('batch-status').textContent=`Подготовлено изменений: ${data.items.filter(i=>!i.error).length}.`;review(data.items);});
$('verify').onclick=()=>run(async()=>{$('batch-status').textContent='Проверяем фактические данные ZenMoney без повторной отправки…';showResults(await api('/api/batch/verify',scope));});
if(scope.bank&&scope.account){$('scope').textContent=`Банк: ${scope.bank}. Отправка только для выбранного банковского счёта.`;$('preview').click();}
else{$('batch-status').textContent='Откройте отправку из меню расширения на странице истории банка.';$('preview').disabled=$('verify').disabled=true;}
