import {categoryPicker} from './category-picker.js';
import {formatMoney} from './zenmoney-view.js';
const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
export function newOperationPicker(row,state,onAction){
  return categoryPicker({...row,category_draft:row.creation_draft?.category_id?{...row.creation_draft,id:row.creation_draft.category_id}:null,category_ranking:row.creation_ranking||[],category_suggestion:null},state,onAction,{newOperation:true});
}
export function creationDialog(items,scope,send){
  if(document.querySelector('.creation-dialog'))return;
  const dialog=el('dialog');dialog.className='pairs-review-dialog creation-dialog';dialog.setAttribute('aria-label','Создание новых операций');
  const header=el('div');header.className='pairs-review-header';header.append(el('h2','Отправить новые записи в ZenMoney'),el('p','Будут созданы новые доходы или расходы на указанных счетах. Проверьте, что это не переводы между своими счетами и не уже внесённые записи. До 20 операций за отправку.'));
  const list=el('div');list.className='pairs-review-list';const selected=new Set();
  const confirm=el('button'),cancel=el('button','Отмена');cancel.autofocus=true;
  const update=()=>{confirm.textContent=`Отправить в ZenMoney · ${selected.size}`;confirm.disabled=!selected.size||selected.size>20;};
  for(const item of items){
    const card=el('article');card.className='creation-item';const label=el('label'),check=el('input');check.type='checkbox';check.disabled=!!item.error;check.checked=!item.error&&selected.size<20;if(check.checked)selected.add(item.bank_id);
    check.onchange=()=>{check.checked?selected.add(item.bank_id):selected.delete(item.bank_id);update();};
    const sum=Number(item.amount_minor)/(item.currency==='VND'?1:100);
    label.append(check,document.createTextNode(` ${item.date} · ${item.account} · ${sum>0?'+':''}${formatMoney(String(sum),item.currency)}`));
    card.append(label,el('p',item.description),el('p',`Категория: ${item.category||'не выбрана'}${item.source==='auto'?' · автоматически по истории':''}`));
    if(item.error)card.append(el('p',item.error));list.append(card);
  }
  if(!items.length)list.append(el('p','Нет новых операций в выбранном банке и периоде.'));
  let submitted=false;confirm.onclick=()=>{if(submitted||confirm.disabled)return;submitted=true;dialog.close();send({...scope,items:items.filter(i=>selected.has(i.bank_id)).map(i=>({bank_id:i.bank_id,snapshot:i.snapshot}))});};
  cancel.onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove(),{once:true});
  const footer=el('div');footer.className='pairs-review-footer';footer.append(cancel,confirm);update();dialog.append(header,list,footer);document.body.append(dialog);dialog.showModal();
}

export function localRecord(row,onAction){
  const box=el('div');box.append(el('p',`Создана локально · ${row.creation_draft?.title||'категория сохранена'}. Ожидает отправки в ZenMoney.`));
  const button=el('button','Отменить локальное создание');button.dataset.apiAction='';
  button.onclick=()=>onAction(()=>({path:`/api/new-operations/${encodeURIComponent(row.id)}/reopen`,body:{}}));box.append(button);return box;
}
