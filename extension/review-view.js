import {dateLabel} from './date-format.js';
import {newOperationPicker,localRecord} from './new-operations.js';
import {categoryPicker,destroyCategoryPickers} from './category-picker.js';
import {categoryNeedsWork,zenRowsById,bankNeedsCategory,linkedIds} from './category-state.js';
import {reviewPairs} from './pairs-review-dialog.js';
import {exclusionButton,excludedRecords} from './exclusions-view.js';
import {attentionDays} from './attention-view.js';
import {dayComparison} from './day-comparison.js';
import {formatMoney} from './zenmoney-view.js';
const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
const labels={synced:'Уже в ZenMoney',possible:'Проверить совпадение',new:'Новая',unmapped:'Выберите счёт',conflict:'Связь изменилась',queued:'В очереди',sent:'Отправлена',ignored:'Не требует сведения'};
export const reviewStatus=t=>t.reconciliation_status||(['linked','sent'].includes(t.state)?'synced':t.state!=='pending'?t.state:!t.mapped?'unmapped':t.candidates.length?'possible':'new');
function money(t){const result=formatMoney(String(t.currency==='VND'?t.amount_minor:t.amount_minor/100),t.currency);return t.amount_minor>0?'+'+result:result;}
function icon(title,transfer){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
  const p=document.createElementNS(svg.namespaceURI,'path');
  p.setAttribute('d',transfer?'M4 8h15m-4-4 4 4-4 4M20 16H5m4-4-4 4 4 4':/ресторан|еда|продукт|кофе/i.test(title)?'M4 9h16l-2 11H6L4 9Zm4 0 2-6m6 6-2-6M9 12v5m6-5v5':/транспорт|автомоб|такси/i.test(title)?'m5 9 2-5h10l2 5M4 9h16v9H4V9Zm3 9v2m10-2v2M7 13h1m8 0h1':'M5 5h14v14H5V5Zm4 4h6m-6 4h6');
  svg.append(p);return svg;
}
export function renderReview(container,state,{account='',status='all',query='',start='',end='',limit=100,workDays=false,attentionOnly=true,includePending=false,hideReconciled=false,onMore,onAction,onShowAll}){
  destroyCategoryPickers(container);container.replaceChildren();
  const zenRows=zenRowsById(state);
  const accounts=new Map(state.bank_accounts.map(a=>[a.id,a])),zen=new Map(state.zenmoney_accounts.map(a=>[a.id,a]));
  const key=(account,date)=>JSON.stringify([account,date]);
  const needsWork=new Set();
  for(const t of state.review){
    if(t.state==='ignored')continue;
    if(reviewStatus(t)!=='synced'||bankNeedsCategory(t,zenRows))needsWork.add(key(t.bank_account,t.date));
  }
  for(const d of state.day_review||[]){
    if((!d.equal&&!d.resolved)||(d.zen_rows||[]).some(z=>!z.excluded&&(!z.linked_bank_id||categoryNeedsWork(z)||(includePending&&(z.category_draft||(state.category_sends||[]).some(s=>s.zen_id===z.id&&s.state!=='sent'))))))needsWork.add(key(d.bank_account,d.date));
  }
  const showDay=(account,date)=>!workDays||!attentionOnly||needsWork.has(key(account,date));
  if(workDays){status='all';query='';limit=Infinity;}
  const filtered=state.review.filter(t=>showDay(t.bank_account,t.date)&&(!account||t.bank_account===account)&&(!start||t.date>=start)&&(!end||t.date<=end)&&(!query||[t.description,t.raw?.details?.corresponsiveName,t.linked?.payee].join(' ').toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru'))));
  const counts={all:filtered.length,synced:0,new:0,attention:0};
  for(const t of filtered){const s=reviewStatus(t);if(s==='synced')counts.synced++;else if(s==='new')counts.new++;else if(s!=='ignored')counts.attention++;}
  const rows=filtered.filter(t=>status==='all'||(status==='attention'?!['synced','new','ignored'].includes(reviewStatus(t)):reviewStatus(t)===status)).sort((a,b)=>b.date.localeCompare(a.date)||(b.raw?.date||'').localeCompare(a.raw?.date||'')||a.id.localeCompare(b.id));
  if(workDays){
    const days=(state.day_review||[]).filter(d=>showDay(d.bank_account,d.date)&&(!account||d.bank_account===account)&&(!start||d.date>=start)&&(!end||d.date<=end));
    // Accounts without mapping have no day_review; keep their rows visible too.
    for(const t of filtered)if(!days.some(d=>d.bank_account===t.bank_account&&d.date===t.date))days.push({bank_account:t.bank_account,date:t.date,bank_ids:filtered.filter(r=>r.bank_account===t.bank_account&&r.date===t.date).map(r=>r.id),zen_rows:[]});
    const pending=[...new Map((state.day_review||[]).filter(d=>(!account||d.bank_account===account)&&(!start||d.date>=start)&&(!end||d.date<=end)).flatMap(d=>d.zen_rows||[]).filter(z=>z.category_draft?.valid===true).map(z=>[z.id,z])).values()];
    if(pending.length){const note=node('p',`Категории выбраны локально: ${pending.length}. Ожидают отправки в ZenMoney. `,'pending-categories');const link=node('a','Посмотреть в разделе «Операции»');link.href='#review';link.onclick=()=>onShowAll?.();if(!includePending)note.append(link);container.append(note);}
    attentionDays(container,state,days,onAction,{hideReconciled,includePending});
    if(!includePending)excludedRecords(container,state,{account,start,end,onAction});

    return {counts,total:rows.length,shown:rows.length,days:days.length};
  }
  excludedRecords(container,state,{account,start,end,onAction});
  let day,group;
  const profiles=(state.recipient_profiles||[]).filter(p=>!account||p.bank_account===account);
  if(profiles.length){const box=node('details',undefined,'category-evidence');box.append(node('summary',`Получатели с подтверждённой историей · ${profiles.length}`));for(const p of profiles){box.append(node('strong',p.name));for(const c of p.categories)box.append(node('p',`${c.title} · ${c.count} ${c.repeated?'— повторяется':'— один пример'}`));}container.append(box);}
  const button=(text,fn)=>{const b=node('button',text,'secondary');b.dataset.apiAction='';b.onclick=()=>onAction(fn);return b;};
  const scopedDays=(state.day_review||[]).filter(d=>showDay(d.bank_account,d.date)&&(!account||d.bank_account===account)&&(!start||d.date>=start)&&(!end||d.date<=end));
  const comparisonDays=scopedDays.filter(d=>Array.isArray(d.zen_rows));
  if(comparisonDays.length){
    const complete=node('details',undefined,'full-day-review');complete.open=workDays;
    complete.append(node('summary','Полная сверка по дням — банк и ZenMoney'));
    complete.append(node('p','Все операции выбранного счёта и периода из загруженной истории. Поиск, статус и пагинация основного списка здесь не применяются. Б/З — номера записей внутри дня; совпадение суммы ещё не подтверждает связь.'));
    for(const d of comparisonDays){
      const a=accounts.get(d.bank_account),entry=node('details',undefined,'comparison-day');entry.dataset.date=d.date;
      entry.append(node('summary',`${dateLabel(d.date)} · ${zen.get(a?.zenmoney_account)?.title||a?.bank} · Банк ${d.bank_count} / ZenMoney ${d.zen_count} · ${d.equal?'Суммы совпадают':'Есть расхождения'}`));
      entry.append(dayComparison(d,state));complete.append(entry);
    }
    container.append(complete);
  }
  const readyDays=scopedDays.filter(d=>d.bulk_eligible===true);
  if(state.capabilities?.local_review&&scopedDays.length){
    const box=node('section',undefined,'bulk-day-review day-proposal');
    const pairs=readyDays.reduce((sum,d)=>sum+d.pairs.length,0);
    box.append(node('h3','Массовое сопоставление истории'));
    box.append(node('p',`Полностью однозначных дней: ${readyDays.length}. Операций для связи: ${pairs}.`));
    box.append(node('p','Используются выбранные счёт и период целиком, включая следующие страницы. Поиск и фильтр статуса не ограничивают действие. Дни с сомнениями целиком остаются для ручной проверки.'));
    if(readyDays.length){
      const preview=node('details');preview.append(node('summary','Посмотреть список дней'));
      for(const d of readyDays){const a=accounts.get(d.bank_account);preview.append(node('p',`${dateLabel(d.date)} · ${zen.get(a?.zenmoney_account)?.title||a?.bank||'Счёт'} · ${d.pairs.length} операций`));}box.append(preview);
      const bulk=node('button',`Сопоставить все однозначные дни · ${pairs} операций`,'secondary');bulk.dataset.apiAction='';bulk.onclick=()=>reviewPairs(state,readyDays,onAction,{field:'pairs',path:'/api/review-days/confirm'});box.append(bulk);
    }else box.append(node('p','Нет полностью однозначных дней для массового сопоставления.'));
    container.prepend(box);
  }
  for(const t of rows.slice(0,limit)){
    if(day!==t.date){day=t.date;group=node('section',undefined,'ledger-day');group.dataset.date=day;
      group.append(node('h2',dateLabel(day)));container.append(group);
      for(const d of (state.day_review||[]).filter(d=>d.date===day&&showDay(d.bank_account,d.date)&&(!account||d.bank_account===account))){
        const box=node('details',undefined,'day-proposal');
        const a=accounts.get(d.bank_account),title=zen.get(a?.zenmoney_account)?.title||a?.bank||'Счёт';
        box.append(node('summary',`${title}: банк ${d.bank_count} / ZenMoney ${d.zen_count} · ${d.equal?'Суммы дня совпадают':'День не совпадает'}`));
        box.append(node('p','Проверка всех загруженных операций счёта за день, включая скрытые фильтрами. Перед подтверждением убедитесь, что история банка за день загружена полностью.'));
        if(Array.isArray(d.zen_rows))box.append(dayComparison(d,state));
        if(d.ambiguous)box.append(node('p',`Неоднозначных операций: ${d.ambiguous}. Количество записей различается или не хватает времени для упорядочивания.`));
        for(const pair of d.pairs){const r=state.review.find(r=>r.id===pair.bank_id),c=r?.candidates.find(c=>c.id===pair.zen_id);if(r)box.append(node('p',`${money(r)} · ${r.description} → ${c?.payee||c?.comment||'ZenMoney'} · ${c?.categories?.join(', ')||'Без категории'}`));}
        if(d.pairs.length&&state.capabilities?.local_review)box.append(button(`Подтвердить пары дня · ${d.pairs.length}`,()=>({path:`/api/review-days/${d.bank_account}/${d.date}/confirm`,body:{snapshot:d.snapshot}})));
        group.append(box);
      }
    }
    const s=reviewStatus(t),suggestion=t.category_suggestion,linked=t.linked;
    const drafts=linkedIds(t).map(id=>zenRows.get(id)?.category_draft).filter(d=>d?.valid===true);
    const categories=drafts.length?drafts.map(d=>d.title):linked?.categories||[],title=categories.length?categories.join(', '):(t.state==='pending'?t.category_draft?.title:null)||suggestion?.title||t.raw?.details?.corresponsiveName||'Без категории';
    const a=accounts.get(t.bank_account),accountTitle=zen.get(a?.zenmoney_account)?.title||`${a?.bank||'Банк'} · ${a?.account?.slice(-4)||''}`;
    const item=node('details',undefined,'ledger-entry');item.dataset.transaction=t.id;item.dataset.state=s;
    const summary=node('summary',undefined,'ledger-summary');
    const symbol=node('span',undefined,'category-symbol');symbol.append(icon(title,linked?.transfer));
    const content=node('span',undefined,'ledger-text');content.append(node('strong',title,'ledger-title'),node('span','▱ '+accountTitle,'ledger-account'));
    if(t.description&&t.description!==title)content.append(node('span',t.description,'ledger-description'));
    const statusLabel=node('span',labels[s]||s,'sync-status status-'+s);content.append(statusLabel);
    if(drafts.length)content.append(node('span','Категория выбрана · ожидает отправки','pending-category'));
    if(suggestion)content.append(node('span',`${suggestion.conflict?'Категория неоднозначна':'Уверенная подсказка'} · ${suggestion.count} примера · требуется подтверждение`,'suggestion-label '+(suggestion.conflict?'confidence-low':'confidence-high')));
    const sum=node('span',money(t),'ledger-amount'+(t.amount_minor>0?' income':''));summary.append(symbol,content,sum);item.append(summary);
    const detail=node('div',undefined,'ledger-detail');detail.append(node('p',t.description));
    if(!t.mapped)detail.append(node('p','Сопоставьте банковский счёт со счётом ZenMoney в разделе «Банки» → «Привязка счетов».','notice'));
    if(s==='conflict')detail.append(node('p','Связанная запись отсутствует или больше не совпадает по счёту, сумме и дате. Проверьте историю и отмените связь при необходимости.','notice'));
    if(linked)detail.append(node('p',`ZenMoney: ${dateLabel(linked.date)} · ${linked.payee||linked.comment||'Без описания'} · ${linked.categories?.join(', ')||'Без категории'}`));
    if(t.state==='linked'&&state.capabilities?.local_review)detail.append(button(t.group?.kind==='settlement'?'Отменить весь взаиморасчёт':'Отменить связь',()=>({path:`/api/review/${t.id}/link`,body:{},method:'DELETE'})));
    if(state.capabilities?.local_review&&['pending','ignored'].includes(t.state))detail.append(exclusionButton('bank',t.id,t.state==='ignored',onAction));
    if(state.capabilities?.local_review&&linked){
      const ids=t.group?.zen_ids||(t.split_ids?.length?t.split_ids:[t.linked_id]);
      const zs=new Map((state.day_review||[]).flatMap(d=>d.zen_rows||[]).map(z=>[z.id,z]));
      for(const id of ids){const z=zs.get(id);if(z&&!z.excluded&&!z.transfer&&(!z.categories?.length||z.category_draft))detail.append(categoryPicker(z,state,onAction));}
    }
    if(t.creation_job?.state==='prepared')detail.append(localRecord(t,onAction));
    if(t.creation_job&&!['sent','prepared'].includes(t.creation_job.state))detail.append(node('p',t.creation_job.error||'Создание ожидает проверки результата.','notice'));
    if(t.state==='pending'){
      if(state.capabilities?.create&&!t.candidates.length)detail.append(newOperationPicker(t,state,onAction));
      const picker=node('select');picker.append(new Option('Без категории',''));
      for(const c of state.categories||[])if(!c.deleted)picker.append(new Option(c.title,c.id));
      if(state.capabilities?.write)detail.append(picker);
      if(t.candidates.length)detail.append(node('h3',`Совпадения в ZenMoney · ${t.candidates.length}`));
      for(const c of t.candidates){
        const candidate=node('div',undefined,'match-option');candidate.append(node('strong',`${dateLabel(c.date)} · ${money(t)}`),node('p',c.categories?.join(', ')||'Без категории'),node('p',c.payee||c.comment||c.id),node('small',c.reason));
        if(state.capabilities?.local_review||state.capabilities?.write)candidate.append(button('Это та же операция',()=>({path:`/api/review/${t.id}/${state.capabilities?.local_review?'link':'confirm'}`,body:{action:'link',match_id:c.id,reviewed_candidates:t.candidates.map(x=>x.id)}})));
        detail.append(candidate);
      }
      if(suggestion){
        const why=node('section',undefined,'category-evidence');why.append(node('h3',suggestion.conflict?'История неоднозначна':`Предполагаемая категория: ${suggestion.title}`),node('p',`${suggestion.reason}. ${suggestion.count} операций.`));
        suggestion.distribution.forEach(c=>why.append(node('p',`${c.title}: ${c.count}`)));
        suggestion.examples.forEach(e=>why.append(node('small',`${dateLabel(e.date)} · ${e.payee||'Подтверждённая связь'} · ${e.title}`)));detail.append(why);
      }else if(!t.candidates.length)detail.append(node('p','Пока недостаточно повторений для уверенного выбора категории. Доступные примеры показаны в списке.','muted'));
      if(!state.capabilities?.write&&!state.capabilities?.create)detail.append(node('p','Запись отключена: новые операции пока не отправляются в ZenMoney.','muted'));
      else if(state.capabilities?.write&&t.mapped){

        detail.append(button('Подтвердить создание',()=>{if(t.candidates.length&&!confirm('Есть похожие операции. Создать отдельную?'))return null;return {path:`/api/review/${t.id}/confirm`,body:{action:'create',category:picker.value||null,reviewed_candidates:t.candidates.map(c=>c.id)}};}));
      }
    }
    const raw=node('details',undefined,'raw-details');raw.append(node('summary','Исходные данные банка'),node('pre',JSON.stringify(t.raw,null,2)));detail.append(raw);item.append(detail);group.append(item);
  }
  if(workDays&&!rows.length&&!comparisonDays.length)container.append(node('p','Нет дней, требующих внимания, в выбранном счёте и периоде.','empty'));
  else if(!rows.length&&!comparisonDays.length)container.append(node('p',filtered.length?'Нет операций с выбранным статусом.':'За выбранный период операций нет.','empty'));
  if(rows.length>limit){const more=node('button',`Показать ещё · осталось ${rows.length-limit}`,'secondary');more.onclick=onMore;container.append(more);}
  return {counts,total:rows.length,shown:Math.min(limit,rows.length)};
}
