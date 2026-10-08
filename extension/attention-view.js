import {formatMoney} from './zenmoney-view.js';
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
export function attentionDays(container,state,days,onAction){
  const saveButton=(label,selected)=>{
    const button=el('button',label);button.dataset.apiAction='';
    button.onclick=()=>onAction(()=>({path:'/api/review-pairs/confirm',body:{days:selected.map(d=>({bank_account:d.bank_account,date:d.date,snapshot:d.snapshot}))}}));return button;
  };
  const ready=days.filter(d=>d.preview_pairs?.length);
  if(state.capabilities?.local_review&&ready.length){
    const bar=el('section',undefined,'preview-pairs-actions');
    bar.append(el('p',`Предварительно спарено: ${ready.reduce((n,d)=>n+d.preview_pairs.length,0)}. Связи ещё не сохранены. Действие включает все показанные дни выбранного счёта и периода.`));
    bar.append(saveButton(`Сохранить пары всех показанных дней · ${ready.reduce((n,d)=>n+d.preview_pairs.length,0)}`,ready));container.append(bar);
  }
  const bankMap=new Map(state.review.map(r=>[r.id,r]));
  for(const d of [...days].sort((a,b)=>b.date.localeCompare(a.date)||a.bank_account.localeCompare(b.bank_account))){
    const account=state.bank_accounts.find(a=>a.id===d.bank_account),title=state.zenmoney_accounts.find(a=>a.id===account?.zenmoney_account)?.title||account?.bank||'Счёт';
    const section=el('section',undefined,'attention-day');section.dataset.date=d.date;
    const heading=el('div',undefined,'section-heading');heading.append(el('h2',`${d.date} · ${title}`));
    if(state.capabilities?.local_review&&d.preview_pairs?.length)heading.append(saveButton(`Сохранить пары дня · ${d.preview_pairs.length}`,[d]));
    section.append(heading);
    const banks=(d.bank_ids||[]).map(id=>bankMap.get(id)).filter(Boolean),zs=d.zen_rows||[],usedB=new Set(),usedZ=new Set(),groups=[];
    for(const b of banks){const z=zs.find(z=>z.id===b.linked_id&&z.linked_bank_id===b.id);if(z&&b.linked){groups.push({banks:[b],zen:[z],confirmed:true});usedB.add(b.id);usedZ.add(z.id);}}
    const amounts=new Map();
    for(const [kind,items,used] of [['banks',banks,usedB],['zen',zs,usedZ]])for(const r of items){if(used.has(r.id))continue;const key=String(Number(r.amount_minor));if(!amounts.has(key))amounts.set(key,{banks:[],zen:[],confirmed:false});amounts.get(key)[kind].push(r);}
    groups.push(...amounts.values());
    for(const g of groups)g.preview=!g.confirmed&&g.banks.length===1&&g.zen.length===1&&(d.preview_pairs||[]).some(p=>p.bank_id===g.banks[0].id&&p.zen_id===g.zen[0].id);
    for(const g of groups){
      const sample=g.banks[0]||g.zen[0],amount=Number(sample.amount_minor),currency=sample.currency||account.currency;
      const row=el('details',undefined,'attention-operation');
      const source=g.banks.length&&g.zen.length?'Банк + ZenMoney':g.banks.length?'Только банк':'Только ZenMoney';
      const status=g.confirmed?'Связь подтверждена':g.preview?'Предварительная пара · не сохранена':g.banks.length&&g.zen.length?(g.banks.length===g.zen.length?'Суммы и количество совпадают · связь не подтверждена':'Количество различается'):'Нет пары в этот день';
      const summary=el('summary');summary.append(el('strong',(amount>0?'+':'')+formatMoney(String(currency==='VND'?amount:amount/100),currency)),el('span',source,'badge'),el('span',status));
      if(g.banks.length>1||g.zen.length>1)summary.append(el('small',`Банк: ${g.banks.length} · ZenMoney: ${g.zen.length}`));
      const description=g.zen[0]?.categories?.join(', ')||g.banks[0]?.description||g.zen[0]?.payee||'Без категории';summary.append(el('p',description));row.append(summary);
      for(const b of g.banks){row.append(el('p',`Банк: ${b.description}`));if(b.linked&&!g.confirmed)row.append(el('small',`Связано с ZenMoney за ${b.linked.date}`));}
      for(const z of g.zen)row.append(el('p',`ZenMoney: ${z.payee||z.comment||'Без описания'} · ${z.categories.join(', ')|| (z.transfer?'Перевод':'Без категории')}`));
      if(g.confirmed&&g.zen.some(z=>!z.transfer&&!z.categories.length))row.append(el('p','Нужно определить категорию.'));
      if(!g.confirmed&&!g.preview&&state.capabilities?.local_review)for(const b of g.banks.filter(b=>b.state==='pending')){
        const candidates=g.zen.filter(z=>(b.candidates||[]).some(c=>c.id===z.id)&&!z.linked_bank_id);
        if(!candidates.length)continue;
        const label=el('label',`Сопоставить: ${b.description}`),select=el('select');select.setAttribute('aria-label',`Пара для ${b.description}`);select.append(new Option('Выберите операцию ZenMoney',''));
        candidates.forEach((z,i)=>select.append(new Option(`№${i+1} · ${z.payee||z.comment||'Без описания'} · ${z.categories.join(', ')||'Без категории'}${z.comment?' · '+z.comment:''}`,z.id)));label.append(select);row.append(label);
        const button=el('button','Подтвердить выбранную пару');button.dataset.apiAction='';button.onclick=()=>{if(!select.value){select.focus();return;}onAction(()=>({path:`/api/review/${b.id}/link`,body:{action:'link',match_id:select.value,reviewed_candidates:b.candidates.map(c=>c.id)}}));};row.append(button);
      }
      section.append(row);
    }
    container.append(section);
  }
  if(!days.length)container.append(el('p','Нет дней, требующих внимания, в выбранном счёте и периоде.','empty'));
}
