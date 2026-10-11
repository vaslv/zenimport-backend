import {dateLabel} from './date-format.js';
export function exclusionButton(source,id,excluded,onAction){
  const button=document.createElement('button');button.className='secondary';button.dataset.apiAction='';
  button.textContent=excluded?'Вернуть в проверку':'Игнорировать';
  button.onclick=()=>onAction(()=>({path:`/api/exclusions/${source}/${encodeURIComponent(id)}`,body:{},method:excluded?'DELETE':'PUT'}));
  return button;
}
export function excludedRecords(container,state,{account,start,end,onAction}){
  const entries=[];
  for(const b of state.review)if(b.state==='ignored')entries.push({source:'bank',id:b.id,date:b.date,account:b.bank_account,label:b.description});
  const seen=new Set();
  for(const d of state.day_review||[])for(const z of d.zen_rows||[])if(z.excluded&&!seen.has(z.id)){
    seen.add(z.id);entries.push({source:'zenmoney',id:z.id,date:d.date,account:d.bank_account,label:z.payee||z.comment||'Без описания'});
  }
  const visible=entries.filter(r=>(!account||r.account===account)&&(!start||r.date>=start)&&(!end||r.date<=end));
  if(!visible.length)return;
  const box=document.createElement('details');box.className='excluded-records';
  const summary=document.createElement('summary');summary.textContent=`Исключены из сведения · ${visible.length}`;box.append(summary);
  for(const r of visible){const row=document.createElement('p');row.textContent=`${dateLabel(r.date)} · ${r.source==='bank'?'Банк':'ZenMoney'} · ${r.label} `;row.append(exclusionButton(r.source,r.id,true,onAction));box.append(row);}
  container.append(box);
}
