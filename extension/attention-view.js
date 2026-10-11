import {beneficiaryLabel} from './beneficiary.js';
import {dateLabel} from './date-format.js';
import {newOperationPicker,localRecord} from './new-operations.js';
import {categoryPicker} from './category-picker.js';
import {categoryNeedsWork,zenRowsById,bankNeedsCategory,linkedIds} from './category-state.js';
import {reviewPairs} from './pairs-review-dialog.js';
import {exclusionButton} from './exclusions-view.js';
import {formatMoney} from './zenmoney-view.js';
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
export function attentionDays(container,state,days,onAction,{hideReconciled=false,includePending=false}={}){
  const saveButton=(label,selected,mass=false)=>{
    const button=el('button',label);button.dataset.apiAction='';
    button.onclick=()=>mass?reviewPairs(state,selected,onAction):onAction(()=>({path:'/api/review-pairs/confirm',body:{days:selected.map(d=>({bank_account:d.bank_account,date:d.date,snapshot:d.snapshot}))}}));return button;
  };
  const ready=days.filter(d=>d.preview_pairs?.length);
  if(state.capabilities?.local_review&&ready.length){
    const bar=el('section',undefined,'preview-pairs-actions');
    bar.append(el('p',`Предварительно спарено: ${ready.reduce((n,d)=>n+d.preview_pairs.length,0)}. Связи ещё не сохранены. Действие включает все показанные дни выбранного счёта и периода.`));
    if(ready.some(d=>d.preview_pairs.some(p=>p.order==='chronological')))bar.append(el('p','Повторяющиеся суммы спарены по времени банка и времени создания записей ZenMoney, от ранних к поздним. Время создания может отличаться от времени покупки.'));
    bar.append(saveButton(`Сохранить пары всех показанных дней · ${ready.reduce((n,d)=>n+d.preview_pairs.length,0)}`,ready,true));container.append(bar);
  }
  const bankMap=new Map(state.review.map(r=>[r.id,r]));
  const zenRows=zenRowsById(state);
  const pending=z=>includePending&&(z.category_draft||(state.category_sends||[]).some(s=>s.zen_id===z.id&&s.state!=='sent'));
  const settled=b=>!(b?linkedIds(b):[]).some(id=>pending(zenRows.get(id)||{}))&&b&&['linked','sent'].includes(b.state)&&b.linked&&!bankNeedsCategory(b,zenRows);
  for(const d of [...days].sort((a,b)=>b.date.localeCompare(a.date)||a.bank_account.localeCompare(b.bank_account))){
    const account=state.bank_accounts.find(a=>a.id===d.bank_account),title=state.zenmoney_accounts.find(a=>a.id===account?.zenmoney_account)?.title||account?.bank||'Счёт';
    const section=el('section',undefined,'attention-day');section.dataset.date=d.date;
    const heading=el('div',undefined,'section-heading');heading.append(el('h2',`${dateLabel(d.date)} · ${title}`));
    if(state.capabilities?.local_review&&d.preview_pairs?.length)heading.append(saveButton(`Сохранить пары дня · ${d.preview_pairs.length}`,[d]));
    section.append(heading);
    if(d.bank_total!==undefined){const total=v=>formatMoney(String(Number(v)/(account.currency==='VND'?1:100)),account.currency);section.append(el('p',`Итог банка: ${total(d.bank_total)} · Итог ZenMoney: ${total(d.zen_total)}`));}
    if(d.excluded_count)section.append(el('p',`Не участвуют в сведении: ${d.excluded_count}. Итоги для сведения: банк ${formatMoney(String(Number(d.reconcile_bank_total)/(account.currency==='VND'?1:100)),account.currency)} · ZenMoney ${formatMoney(String(Number(d.reconcile_zen_total)/(account.currency==='VND'?1:100)),account.currency)}. Исключённые записи также скрываются в компактном режиме.`,'muted'));
    if(d.split_search_limited)section.append(el('p','Поиск комбинаций ограничен из-за количества вариантов. Показанный список может быть неполным.'));
    const allBanks=(d.bank_ids||[]).map(id=>bankMap.get(id)).filter(Boolean),allZen=d.zen_rows||[];
    const banks=allBanks.filter(b=>!hideReconciled||(b.state!=='ignored'&&!settled(b)));
    const zs=allZen.filter(z=>{const b=bankMap.get(z.linked_bank_id);return !hideReconciled||(!z.excluded&&(!settled(b)||!(b.split_ids?.length?b.split_ids:[b.linked_id]).includes(z.id)));});
    const hidden=allBanks.length+allZen.length-banks.length-zs.length;
    if(hidden)section.append(el('p',`Скрыто сведённых записей: ${hidden}. Итоги выше относятся к полному дню.`,'muted'));
    if(!banks.length&&!zs.length)section.append(el('p','Все записи дня сведены и имеют категории; проверьте расхождения дня в полном виде.'));
    const usedB=new Set(),usedZ=new Set(),groups=[];
    for(const b of banks)if(b.state==='ignored'){groups.push({banks:[b],zen:[],excluded:true});usedB.add(b.id);}
    for(const z of zs)if(z.excluded){groups.push({banks:[],zen:[z],excluded:true});usedZ.add(z.id);}
    for(const b of banks){
      if(b.group?.kind!=='settlement'||usedB.has(b.id)||!b.linked)continue;
      const members=banks.filter(r=>b.group.bank_ids.includes(r.id)),parts=zs.filter(z=>b.group.zen_ids.includes(z.id));
      if(members.length===b.group.bank_ids.length&&parts.length===b.group.zen_ids.length){groups.push({banks:members,zen:parts,confirmed:true,settlement:true});members.forEach(r=>usedB.add(r.id));parts.forEach(z=>usedZ.add(z.id));}
    }
    for(const b of banks){if(usedB.has(b.id)||b.group?.kind==='settlement')continue;const parts=zs.filter(z=>(b.split_ids?.length?b.split_ids:[b.linked_id]).includes(z.id)&&z.linked_bank_id===b.id);if(parts.length&&b.linked){groups.push({banks:[b],zen:parts,confirmed:true});usedB.add(b.id);parts.forEach(z=>usedZ.add(z.id));}}
    for(const pair of d.preview_pairs||[]){
      const b=banks.find(b=>b.id===pair.bank_id),z=zs.find(z=>z.id===pair.zen_id);
      if(b&&z&&!usedB.has(b.id)&&!usedZ.has(z.id)){groups.push({banks:[b],zen:[z],confirmed:false,pair});usedB.add(b.id);usedZ.add(z.id);}
    }
    const amounts=new Map();
    for(const [kind,items,used] of [['banks',banks,usedB],['zen',zs,usedZ]])for(const r of items){if(used.has(r.id))continue;const key=String(Number(r.amount_minor));if(!amounts.has(key))amounts.set(key,{banks:[],zen:[],confirmed:false});amounts.get(key)[kind].push(r);}
    groups.push(...amounts.values());
    for(const g of groups)g.preview=!g.confirmed&&g.banks.length===1&&g.zen.length===1&&(d.preview_pairs||[]).some(p=>p.bank_id===g.banks[0].id&&p.zen_id===g.zen[0].id);
    for(const g of groups){
      const sample=g.banks[0]||g.zen[0],amount=g.settlement?g.banks.reduce((n,b)=>n+Number(b.amount_minor),0):Number(sample.amount_minor),currency=sample.currency||account.currency;
      const row=el('details',undefined,'attention-operation');
      const source=g.banks.length&&g.zen.length?'Банк + ZenMoney':g.banks.length?'Только банк':'Только ZenMoney';
      const status=g.excluded?'Не требует сведения':g.settlement?'Взаиморасчёт · связь подтверждена':g.confirmed?'Связь подтверждена':g.preview?(g.pair?.order==='chronological'?'Пара по порядку · не сохранена':'Предварительная пара · не сохранена'):g.banks.length&&g.zen.length?(g.banks.length===g.zen.length?'Суммы и количество совпадают · связь не подтверждена':'Количество различается'):'Нет пары в этот день';
      const summary=el('summary');summary.append(el('strong',(amount>0?'+':'')+formatMoney(String(currency==='VND'?amount:amount/100),currency)),el('span',source,'badge'),el('span',status));
      if(g.banks.length>1||g.zen.length>1)summary.append(el('small',`Банк: ${g.banks.length} · ZenMoney: ${g.zen.length}`));
      const description=g.zen[0]?.categories?.join(', ')||g.zen[0]?.category_draft?.title||g.banks[0]?.description||g.zen[0]?.payee||'Без категории';summary.append(el('p',description));
      for(const label of new Set(g.banks.map(beneficiaryLabel).filter(Boolean)))summary.append(el('p',label,'beneficiary-account'));
      row.append(summary);
      for(const b of g.banks){row.append(el('p',`Банк: ${formatMoney(String(currency==='VND'?b.amount_minor:b.amount_minor/100),currency)} · ${b.description}${beneficiaryLabel(b)?' · '+beneficiaryLabel(b):''}`));if(b.linked&&!g.confirmed)row.append(el('small',`Связано с ZenMoney за ${dateLabel(b.linked.date)}`));}
      for(const z of g.zen)row.append(el('p',`ZenMoney: ${formatMoney(String(currency==='VND'?Number(z.amount_minor):Number(z.amount_minor)/100),currency)} · ${z.payee||z.comment||'Без описания'} · ${z.categories.join(', ')|| (z.transfer?'Перевод':'Без категории')}`));
      if(g.pair?.order==='chronological'){
        row.append(el('p',`Время банка: ${g.banks[0].raw?.date||'не указано'} · Запись ZenMoney создана: ${new Date(g.zen[0].created*1000).toLocaleString('ru-RU')}`));
        if(g.pair.order_tied)row.append(el('p','Есть одинаковые отметки времени: при равенстве используется стабильный порядок идентификаторов. Проверьте категории перед сохранением.'));
      }
      if(state.capabilities?.local_review){

        for(const z of g.zen)if(!z.linked_bank_id){const actions=el('div',`ZenMoney · ${z.payee||z.comment||'Без описания'} `);actions.append(exclusionButton('zenmoney',z.id,!!z.excluded,onAction));row.append(actions);}
      }
      if(g.zen.some(pending))row.append(el('p','Категория ожидает отправки или проверки результата в ZenMoney.','notice'));
      if(g.confirmed&&g.zen.some(categoryNeedsWork))row.append(el('p','Нужно определить категорию.'));
      for(const b of g.banks){
        if(!g.settlement&&b.group?.kind==='settlement'&&b.state==='linked'&&state.capabilities?.local_review){const undo=el('button','Отменить весь взаиморасчёт');undo.dataset.apiAction='';undo.onclick=()=>onAction(()=>({path:`/api/review/${b.id}/link`,body:{},method:'DELETE'}));row.append(el('p','Состав группы изменился или больше не совпадает по сумме. Отмена освободит всех участников.'),undo);}
        const options=(d.split_candidates||[]).filter(p=>p.bank_id===b.id);
        if(options.length){
          row.append(el('h3','Возможная разбивка по категориям'));
          for(const option of options){
            const detail=el('details',undefined,'split-option');detail.append(el('summary',`${option.unique?'Единственная найденная комбинация':'Возможная комбинация'} · ${option.zen_ids.length} записей`));
            for(const id of option.zen_ids){const z=zs.find(z=>z.id===id);detail.append(el('p',`${formatMoney(String(currency==='VND'?Number(z.amount_minor):Number(z.amount_minor)/100),currency)} · ${z.categories.join(', ')||'Без категории'} · ${z.payee||z.comment||''}`));}
            if(g.pair?.order==='chronological'){
        row.append(el('p',`Время банка: ${g.banks[0].raw?.date||'не указано'} · Запись ZenMoney создана: ${new Date(g.zen[0].created*1000).toLocaleString('ru-RU')}`));
        if(g.pair.order_tied)row.append(el('p','Есть одинаковые отметки времени: при равенстве используется стабильный порядок идентификаторов. Проверьте категории перед сохранением.'));
      }
      if(state.capabilities?.local_review){const button=el('button','Сохранить составную связь');button.dataset.apiAction='';button.onclick=()=>onAction(()=>({path:`/api/review/${b.id}/split-link`,body:{snapshot:d.snapshot,zen_ids:option.zen_ids}}));detail.append(button);}
            row.append(detail);
          }
        }
        if(b.group?.kind!=='settlement'&&b.state==='linked'&&b.split_ids?.length&&state.capabilities?.local_review){const undo=el('button','Отменить составную связь');undo.dataset.apiAction='';undo.onclick=()=>onAction(()=>({path:`/api/review/${b.id}/link`,body:{},method:'DELETE'}));row.append(undo);}
      }
      if(!g.confirmed&&!g.preview&&state.capabilities?.local_review)for(const b of g.banks.filter(b=>b.state==='pending')){
        const candidates=g.zen.filter(z=>(b.candidates||[]).some(c=>c.id===z.id)&&!z.linked_bank_id);
        if(!candidates.length)continue;
        const label=el('label',`Сопоставить: ${b.description}`),select=el('select');select.setAttribute('aria-label',`Пара для ${b.description}`);select.append(new Option('Выберите операцию ZenMoney',''));
        candidates.forEach((z,i)=>select.append(new Option(`№${i+1} · ${z.payee||z.comment||'Без описания'} · ${z.categories.join(', ')||'Без категории'}${z.comment?' · '+z.comment:''}`,z.id)));label.append(select);row.append(label);
        const button=el('button','Подтвердить выбранную пару');button.dataset.apiAction='';button.onclick=()=>{if(!select.value){select.focus();return;}onAction(()=>({path:`/api/review/${b.id}/link`,body:{action:'link',match_id:select.value,reviewed_candidates:b.candidates.map(c=>c.id)}}));};row.append(button);
      }
      if(g.settlement&&state.capabilities?.local_review){const undo=el('button','Отменить весь взаиморасчёт');undo.dataset.apiAction='';undo.onclick=()=>onAction(()=>({path:`/api/review/${g.banks[0].id}/link`,body:{},method:'DELETE'}));row.append(undo);}
      const item=el('div',undefined,'attention-item');item.append(row);
      if(state.capabilities?.local_review)for(const b of g.banks)if(['pending','ignored'].includes(b.state)){const actions=el('div',undefined,'inline-exclusion');if(g.banks.length>1)actions.append(el('span',b.description+' '));actions.append(exclusionButton('bank',b.id,b.state==='ignored',onAction));item.append(actions);}
      if(g.zen.some(z=>z.category_draft?.valid===true))item.append(el('p','Категория выбрана · ожидает отправки в ZenMoney','pending-category'));
      if(state.capabilities?.local_review)for(const z of g.zen)if(!z.excluded&&!z.transfer&&(!z.categories?.length||z.category_draft)){
        const picker=categoryPicker(z,state,onAction);picker.classList.add('inline-category-picker','tw:m-4');
        if(g.zen.length>1)picker.querySelector('h3').textContent=`Категория ZenMoney · ${z.amount_minor} ${currency} · ${z.payee||z.comment||z.id}`;
        item.append(picker);
      }
      for(const b of g.banks){
        if(state.capabilities?.create&&b.state==='pending'&&!g.zen.length){const picker=newOperationPicker(b,state,onAction);picker.classList.add('tw:m-4');item.append(picker);}
        if(b.creation_job?.state==='prepared')item.append(localRecord(b,onAction));
    if(b.creation_job&&!['sent','prepared'].includes(b.creation_job.state))item.append(el('p',b.creation_job.error||'Создание ожидает проверки результата в ZenMoney.','notice'));
      }
      section.append(item);
    }
    const freeBanks=allBanks.filter(b=>b.state==='pending'),freeZen=allZen.filter(z=>!z.linked_bank_id&&!z.excluded);
    if(state.capabilities?.local_review&&freeBanks.length&&freeZen.length){
      const form=el('details',undefined,'settlement-form');form.append(el('summary','Создать группу взаиморасчёта'));
      form.append(el('p','Отметьте связанные записи. Сохраняется только локальная группа; эти операции не обучают категории.'));
      const chosenBanks=new Set(),chosenZen=new Set(),currency=account.currency;
      const totals=el('p'),save=el('button','Сохранить группу');save.dataset.apiAction='';
      const update=()=>{const bankSum=freeBanks.filter(b=>chosenBanks.has(b.id)).reduce((n,b)=>n+Number(b.amount_minor),0),zenSum=freeZen.filter(z=>chosenZen.has(z.id)).reduce((n,z)=>n+Number(z.amount_minor),0);const fmt=v=>formatMoney(String(v/(currency==='VND'?1:100)),currency);totals.textContent=`Банк: ${fmt(bankSum)} · ZenMoney: ${fmt(zenSum)} · Разница: ${fmt(bankSum-zenSum)}`;return chosenBanks.size&&chosenZen.size&&chosenBanks.size+chosenZen.size>=3&&bankSum===zenSum;};
      for(const [label,items,chosen] of [['Банк',freeBanks,chosenBanks],['ZenMoney',freeZen,chosenZen]]){form.append(el('h3',label));for(const r of items){const line=el('label'),check=el('input');check.type='checkbox';check.dataset.member=r.id;check.onchange=()=>{check.checked?chosen.add(r.id):chosen.delete(r.id);update();};line.append(check,document.createTextNode(` ${r.description||r.payee||r.comment||'Без описания'} · ${formatMoney(String(Number(r.amount_minor)/(currency==='VND'?1:100)),currency)}`));form.append(line);}}
      const message=el('p');save.onclick=()=>{if(!update()){message.textContent='Выберите связанные записи с обеих сторон с одинаковой итоговой суммой.';return;}onAction(()=>({path:'/api/reconciliation-groups',body:{bank_ids:[...chosenBanks],zen_ids:[...chosenZen],snapshot:d.snapshot}}));};
      update();form.append(totals,save,message);section.append(form);
    }
    container.append(section);
  }
  if(!days.length)container.append(el('p','Нет дней, требующих внимания, в выбранном счёте и периоде.','empty'));
}
