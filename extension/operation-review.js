import {dateLabel} from './date-format.js';
import {formatMoney} from './zenmoney-view.js';
import './operation-form.js';
const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
export function changeSummary(item){
  const d=item.details;if(!d)return item.kind==='category'?`${(item.before_categories||[]).join(', ')||'Без категории'} → ${item.category}`:item.category||'Без категории';
  const type=item.type_label||globalThis.ZenImportOperationForm.labels[item.definition?.kind]||'Операция';
  const amounts=`${d.from_account||'Счёт'}: −${formatMoney(d.outcome,d.outcome_currency)} → ${d.to_account||'Счёт'}: +${formatMoney(d.income,d.income_currency)}`;
  return `${type} · ${amounts}${d.payee?' · '+d.payee:''}${d.categories?.length?' · '+d.categories.join(', '):''}`;
}
export function reviewChanges(items,send){
  if(document.querySelector('.operation-review-dialog'))return;
  const dialog=el('dialog');dialog.className='pairs-review-dialog operation-review-dialog';dialog.setAttribute('aria-label','Общая отправка в ZenMoney');
  const head=el('div');head.className='pairs-review-header';head.append(el('h2','Проверьте все изменения'),el('p','Сначала решения сохранены локально. Новые операции создаются, существующие изменяются с прежним ID. Проверьте тип, счета, валюты, суммы и человека. До 20 изменений за отправку.'));
  const list=el('div');list.className='pairs-review-list';const chosen=new Set(),confirm=el('button'),cancel=el('button','Отмена');cancel.autofocus=true;
  const update=()=>{confirm.textContent=`Отправить в ZenMoney · ${chosen.size}`;confirm.disabled=!chosen.size||chosen.size>20;};
  items.forEach((item,index)=>{const card=el('article');card.className='operation-review-item';const label=el('label'),check=el('input');check.type='checkbox';check.checked=!item.error&&chosen.size<20;check.disabled=!!item.error;if(check.checked)chosen.add(index);check.onchange=()=>{check.checked?chosen.add(index):chosen.delete(index);update();};label.append(check,document.createTextNode(` ${item.kind==='creation'?'Создать':'Изменить'} · ${dateLabel(item.date)} · ${item.account||'Счёт'}`));
    card.append(label,el('p',item.description||item.payee||'Без описания'));
    if(item.before_details)card.append(el('p','Было: '+changeSummary({...item,details:item.before_details,type_label:'Текущее движение'})));
    card.append(el('p','Будет: '+changeSummary(item)));if(item.error)card.append(el('p',item.error));list.append(card);
  });
  if(!items.length)list.append(el('p','Нет изменений, готовых к отправке.'));
  let submitted=false;confirm.onclick=()=>{if(submitted||confirm.disabled)return;submitted=true;dialog.close();send(items.filter((_,i)=>chosen.has(i)));};cancel.onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove(),{once:true});const footer=el('div');footer.className='pairs-review-footer';footer.append(cancel,confirm);update();dialog.append(head,list,footer);document.body.append(dialog);dialog.showModal();
}
