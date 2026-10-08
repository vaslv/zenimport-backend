import {formatMoney} from './zenmoney-view.js';
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
const amount=(r)=>{const n=Number(r.amount_minor),v=formatMoney(String(r.currency==='VND'?n:n/100),r.currency);return n>0?'+'+v:v;};
export function dayComparison(day,state){
  const box=el('div',undefined,'day-comparison');
  const allBank=new Map(state.review.map(r=>[r.id,r]));
  const banks=(day.bank_ids||[]).map(id=>allBank.get(id)).filter(Boolean),zen=day.zen_rows||[];
  const bankLabels=new Map(banks.map((r,i)=>[r.id,`Б${i+1}`])),zenLabels=new Map(zen.map((r,i)=>[r.id,`З${i+1}`]));
  const bankColumn=el('section'),zenColumn=el('section');
  bankColumn.append(el('h4',`Банк · ${banks.length}`));zenColumn.append(el('h4',`ZenMoney · ${zen.length}`));
  const card=(label,r,description)=>{const c=el('article',undefined,'comparison-operation');c.append(el('strong',`${label} · ${amount(r)}`),el('p',description||'Без описания'));return c;};
  for(const r of banks){
    const c=card(bankLabels.get(r.id),r,r.description);c.dataset.bankId=r.id;
    const linked=r.state==='linked'||r.state==='sent';
    const peers=zen.filter(z=>!z.excluded&&z.same_day_bank_ids.includes(r.id));
    if(r.state==='ignored')c.append(el('p','Не требует сведения'));
    else if(linked){c.append(el('p',r.linked?`Связано: ${(r.split_ids?.length?r.split_ids.map(id=>zenLabels.get(id)||id).join(', '):zenLabels.get(r.linked_id))||'ZenMoney за '+r.linked.date}`:'Связь требует проверки','comparison-linked'));}
    else if(peers.length)c.append(el('p',`Возможные пары по сумме: ${peers.map(z=>zenLabels.get(z.id)).join(', ')}. Не подтверждены.`,'comparison-possible'));
    else c.append(el('p','Нет пары по сумме в этот день','comparison-missing'));
    const other=(r.candidates||[]).filter(z=>z.date!==day.date);
    if(!linked&&other.length)c.append(el('small',`Есть кандидаты в другие дни: ${[...new Set(other.map(z=>z.date))].join(', ')}`));
    if(r.raw?.date)c.append(el('small',r.raw.date));bankColumn.append(c);
  }
  for(const r of zen){
    const c=card(zenLabels.get(r.id),r,r.payee||r.comment);c.dataset.zenId=r.id;
    if(r.comment&&r.comment!==r.payee)c.append(el('p',r.comment));
    c.append(el('small',(r.transfer?'Перевод · ':'')+(r.categories.join(', ')||'Без категории')));
    if(r.excluded)c.append(el('p','Не требует сведения'));
    else if(r.linked_bank_id){const linked=allBank.get(r.linked_bank_id);c.append(el('p',`${linked?.linked?'Связано':'Связь требует проверки'}: ${bankLabels.get(r.linked_bank_id)||'банк за '+(linked?.date||'другой день')}`,'comparison-linked'));}
    else if(r.same_day_bank_ids.length)c.append(el('p',`Возможные пары по сумме: ${r.same_day_bank_ids.map(id=>bankLabels.get(id)).join(', ')}. Не подтверждены.`,'comparison-possible'));
    else c.append(el('p','Нет пары по сумме в банке в этот день','comparison-missing'));
    zenColumn.append(c);
  }
  if(!banks.length)bankColumn.append(el('p','Банковских операций за этот день не загружено.'));
  if(!zen.length)zenColumn.append(el('p','Операций ZenMoney за этот день нет.'));
  box.append(bankColumn,zenColumn);return box;
}
