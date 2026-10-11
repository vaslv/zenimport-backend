import {dateLabel} from './date-format.js';
import {formatMoney} from './zenmoney-view.js';
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
export function reviewPairs(state,days,onAction,{field='preview_pairs',path='/api/review-pairs/confirm'}={}){
  if(document.querySelector('.pairs-review-dialog'))return;
  const request={path,body:{days:days.map(d=>({bank_account:d.bank_account,date:d.date,snapshot:d.snapshot}))}};
  const count=days.reduce((n,d)=>n+(d[field]||[]).length,0);
  const dialog=el('dialog',undefined,'pairs-review-dialog');dialog.setAttribute('aria-labelledby','pairs-review-title');
  const header=el('div',undefined,'pairs-review-header'),title=el('h2','Проверка пар перед сохранением');title.id='pairs-review-title';
  header.append(title,el('p',`Пар: ${count} · Дней и счетов: ${days.length}`),el('p','Проверьте обе стороны каждой пары. Подтверждение сохранит только локальные связи.'));
  const list=el('div',undefined,'pairs-review-list');list.tabIndex=0;list.setAttribute('aria-label','Все пары для сохранения');
  const banks=new Map(state.review.map(b=>[b.id,b]));let complete=count>0;
  for(const day of [...days].sort((a,b)=>b.date.localeCompare(a.date)||a.bank_account.localeCompare(b.bank_account))){
    const account=state.bank_accounts.find(a=>a.id===day.bank_account),target=state.zenmoney_accounts.find(a=>a.id===account?.zenmoney_account);
    const section=el('section',undefined,'pairs-review-day');section.dataset.date=day.date;
    section.append(el('h3',`${dateLabel(day.date)} · ${target?.title||account?.bank||'Счёт'} · ${account?.account?.slice(-4)||''}`));
    for(const pair of day[field]||[]){
      const bank=banks.get(pair.bank_id),zen=(day.zen_rows||[]).find(z=>z.id===pair.zen_id)||bank?.candidates?.find(z=>z.id===pair.zen_id);
      const row=el('article',undefined,'pairs-review-pair');row.dataset.bankId=pair.bank_id;row.dataset.zenId=pair.zen_id;
      row.append(el('p',pair.order==='chronological'?'Пара по порядку: время банка → время создания записи ZenMoney':'Совпадение суммы, дня и счёта','pairs-review-reason'));
      if(pair.order_tied)row.append(el('p','Одинаковые отметки времени: применён порядок идентификаторов. Проверьте категорию.','notice'));
      const sides=el('div',undefined,'pairs-review-sides');
      const money=value=>formatMoney(String(Number(value)/(account?.currency==='VND'?1:100)),account?.currency||'');
      if(!bank||!zen){complete=false;row.append(el('p','Данные пары недоступны. Отмените и обновите список.','notice'));}
      else{
        const left=el('div'),right=el('div');
        left.append(el('strong',`Банк · ${money(bank.amount_minor)}`),el('p',bank.description||'Без описания'),el('small',bank.raw?.date||dateLabel(bank.date)));
        right.append(el('strong',`ZenMoney · ${money(zen.amount_minor??bank.amount_minor)}`),el('p',zen.payee||zen.comment||'Без описания'),el('p',(zen.categories||[]).join(', ')||(zen.transfer?'Перевод':'Без категории')));
        if(zen.comment&&zen.comment!==zen.payee)right.append(el('p',zen.comment));
        if(zen.created)right.append(el('small',`Создана: ${new Date(zen.created*1000).toLocaleString('ru-RU')}`));
        sides.append(left,right);row.append(sides);
      }
      section.append(row);
    }
    list.append(section);
  }
  const footer=el('div',undefined,'pairs-review-footer'),cancel=el('button','Отмена','secondary'),save=el('button',`Подтвердить и сохранить · ${count}`);
  cancel.type=save.type='button';cancel.autofocus=true;save.disabled=!complete;
  let submitted=false;
  cancel.onclick=()=>dialog.close();
  save.onclick=()=>{if(submitted)return;submitted=true;save.disabled=true;dialog.close();onAction(()=>request);};
  dialog.addEventListener('close',()=>dialog.remove(),{once:true});
  footer.append(cancel,save);dialog.append(header,list,footer);document.body.append(dialog);dialog.showModal();
}
