import {dateLabel} from './date-format.js';
import {formatMoney} from './zenmoney-view.js';
const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
export function categorySendDialog(items,state,send){
  if(document.querySelector('.category-send-dialog'))return;
  const dialog=el('dialog');dialog.className='pairs-review-dialog category-send-dialog';dialog.setAttribute('aria-label','Проверка категорий перед отправкой');
  const heading=el('div');heading.className='pairs-review-header';heading.append(el('h2','Отправить категории в ZenMoney'),el('p','Обновляются категории существующих записей. Суммы, даты и счета остаются прежними. Отметьте до 20 изменений.'));
  const list=el('div');list.className='pairs-review-list';
  const selected=new Set(),save=el('button','Подтвердить отправку'),cancel=el('button','Отмена');cancel.autofocus=true;
  const update=()=>{save.textContent=`Подтвердить отправку · ${selected.size}`;save.disabled=!selected.size||selected.size>20;};
  for(const item of items){
    const card=el('article');card.className='category-send-item';const label=el('label'),check=el('input');check.type='checkbox';check.disabled=!!item.error;check.checked=!item.error&&selected.size<20;if(check.checked)selected.add(item.zen_id);
    check.onchange=()=>{check.checked?selected.add(item.zen_id):selected.delete(item.zen_id);update();};
    const currency=(state.zenmoney_instruments||[]).find(i=>i.id===item.instrument)?.shortTitle||'';
    label.append(check,document.createTextNode(` ${dateLabel(item.date)} · ${item.account} · ${formatMoney(String(Number(item.income||0)-Number(item.outcome||0)),currency)}`));
    card.append(label,el('p',item.payee||'Без описания'),el('p',`${item.before_categories.join(', ')||'Без категории'} → ${item.category}`));
    if(item.error)card.append(el('p',item.error));list.append(card);
  }
  if(!items.length)list.append(el('p','Нет сохранённых категорий для отправки.'));
  let submitted=false;save.onclick=()=>{if(submitted||save.disabled)return;submitted=true;dialog.close();send({path:'/api/category-updates/send',body:{items:items.filter(i=>selected.has(i.zen_id)).map(i=>({zen_id:i.zen_id,snapshot:i.snapshot}))}});};
  cancel.onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove(),{once:true});const footer=el('div');footer.className='pairs-review-footer';footer.append(cancel,save);update();dialog.append(heading,list,footer);document.body.append(dialog);dialog.showModal();
}
