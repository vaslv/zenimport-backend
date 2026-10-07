import {formatMoney} from './zenmoney-view.js';
const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
const labels={synced:'Уже в ZenMoney',possible:'Проверить совпадение',new:'Новая',unmapped:'Выберите счёт',conflict:'Связь изменилась',queued:'В очереди',sent:'Отправлена',ignored:'Пропущена'};
export const reviewStatus=t=>t.reconciliation_status||(['linked','sent'].includes(t.state)?'synced':t.state!=='pending'?t.state:!t.mapped?'unmapped':t.candidates.length?'possible':'new');
function money(t){const result=formatMoney(String(t.currency==='VND'?t.amount_minor:t.amount_minor/100),t.currency);return t.amount_minor>0?'+'+result:result;}
function icon(title,transfer){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
  const p=document.createElementNS(svg.namespaceURI,'path');
  p.setAttribute('d',transfer?'M4 8h15m-4-4 4 4-4 4M20 16H5m4-4-4 4 4 4':/ресторан|еда|продукт|кофе/i.test(title)?'M4 9h16l-2 11H6L4 9Zm4 0 2-6m6 6-2-6M9 12v5m6-5v5':/транспорт|автомоб|такси/i.test(title)?'m5 9 2-5h10l2 5M4 9h16v9H4V9Zm3 9v2m10-2v2M7 13h1m8 0h1':'M5 5h14v14H5V5Zm4 4h6m-6 4h6');
  svg.append(p);return svg;
}
export function renderReview(container,state,{account='',status='all',query='',start='',end='',limit=100,onMore,onAction}){
  container.replaceChildren();
  const accounts=new Map(state.bank_accounts.map(a=>[a.id,a])),zen=new Map(state.zenmoney_accounts.map(a=>[a.id,a]));
  const filtered=state.review.filter(t=>(!account||t.bank_account===account)&&(!start||t.date>=start)&&(!end||t.date<=end)&&(!query||[t.description,t.raw?.details?.corresponsiveName,t.linked?.payee].join(' ').toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru'))));
  const counts={all:filtered.length,synced:0,new:0,attention:0};
  for(const t of filtered){const s=reviewStatus(t);if(s==='synced')counts.synced++;else if(s==='new')counts.new++;else counts.attention++;}
  const rows=filtered.filter(t=>status==='all'||(status==='attention'?!['synced','new'].includes(reviewStatus(t)):reviewStatus(t)===status)).sort((a,b)=>b.date.localeCompare(a.date)||(b.raw?.date||'').localeCompare(a.raw?.date||'')||a.id.localeCompare(b.id));
  let day,group;
  const button=(text,fn)=>{const b=node('button',text,'secondary');b.dataset.apiAction='';b.onclick=()=>onAction(fn);return b;};
  for(const t of rows.slice(0,limit)){
    if(day!==t.date){day=t.date;group=node('section',undefined,'ledger-day');group.dataset.date=day;
      group.append(node('h2',new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'long',weekday:'short',timeZone:'UTC'}).format(new Date(day+'T12:00:00Z'))));container.append(group);}
    const s=reviewStatus(t),suggestion=t.category_suggestion,linked=t.linked;
    const categories=linked?.categories||[],title=categories.length?categories.join(', '):suggestion?.title||t.raw?.details?.corresponsiveName||'Без категории';
    const a=accounts.get(t.bank_account),accountTitle=zen.get(a?.zenmoney_account)?.title||`${a?.bank||'Банк'} · ${a?.account?.slice(-4)||''}`;
    const item=node('details',undefined,'ledger-entry');item.dataset.transaction=t.id;item.dataset.state=s;
    const summary=node('summary',undefined,'ledger-summary');
    const symbol=node('span',undefined,'category-symbol');symbol.append(icon(title,linked?.transfer));
    const content=node('span',undefined,'ledger-text');content.append(node('strong',title,'ledger-title'),node('span','▱ '+accountTitle,'ledger-account'));
    if(t.description&&t.description!==title)content.append(node('span',t.description,'ledger-description'));
    const statusLabel=node('span',labels[s]||s,'sync-status status-'+s);content.append(statusLabel);
    if(suggestion?.title)content.append(node('span',`Предположение · ${suggestion.count} примера`,'suggestion-label'));
    const sum=node('span',money(t),'ledger-amount'+(t.amount_minor>0?' income':''));summary.append(symbol,content,sum);item.append(summary);
    const detail=node('div',undefined,'ledger-detail');detail.append(node('p',t.description));
    if(!t.mapped)detail.append(node('p','Сопоставьте банковский счёт со счётом ZenMoney в разделе «Счета банка».','notice'));
    if(s==='conflict')detail.append(node('p','Связанная запись отсутствует или больше не совпадает по счёту, сумме и дате. Проверьте историю и отмените связь при необходимости.','notice'));
    if(linked)detail.append(node('p',`ZenMoney: ${linked.date} · ${linked.payee||linked.comment||'Без описания'} · ${linked.categories?.join(', ')||'Без категории'}`));
    if(t.state==='linked'&&state.capabilities?.local_review)detail.append(button('Отменить связь',()=>({path:`/api/review/${t.id}/link`,body:{},method:'DELETE'})));
    if(t.state==='pending'){
      if(t.candidates.length)detail.append(node('h3',`Совпадения в ZenMoney · ${t.candidates.length}`));
      for(const c of t.candidates){
        const candidate=node('div',undefined,'match-option');candidate.append(node('strong',`${c.date} · ${money(t)}`),node('p',c.categories?.join(', ')||'Без категории'),node('p',c.payee||c.comment||c.id),node('small',c.reason));
        if(state.capabilities?.local_review||state.capabilities?.write)candidate.append(button('Это та же операция',()=>({path:`/api/review/${t.id}/${state.capabilities?.local_review?'link':'confirm'}`,body:{action:'link',match_id:c.id,reviewed_candidates:t.candidates.map(x=>x.id)}})));
        detail.append(candidate);
      }
      if(suggestion){
        const why=node('section',undefined,'category-evidence');why.append(node('h3',suggestion.conflict?'История неоднозначна':`Предполагаемая категория: ${suggestion.title}`),node('p',`${suggestion.reason}. ${suggestion.count} операций.`));
        suggestion.distribution.forEach(c=>why.append(node('p',`${c.title}: ${c.count}`)));
        suggestion.examples.forEach(e=>why.append(node('small',`${e.date} · ${e.payee||'Подтверждённая связь'} · ${e.title}`)));detail.append(why);
      }else if(!t.candidates.length)detail.append(node('p','Пока недостаточно подтверждённой истории этого получателя для подсказки категории.','muted'));
      if(!state.capabilities?.write)detail.append(node('p','Запись отключена: новые операции пока не отправляются в ZenMoney.','muted'));
      else if(t.mapped){
        const select=node('select');select.setAttribute('aria-label','Категория');select.append(new Option('Категория',''));state.categories.forEach(c=>select.append(new Option(c.title,c.id)));if(suggestion?.category)select.value=suggestion.category;detail.append(select);
        detail.append(button('Подтвердить создание',()=>{if(t.candidates.length&&!confirm('Есть похожие операции. Создать отдельную?'))return null;return {path:`/api/review/${t.id}/confirm`,body:{action:'create',category:select.value||null,reviewed_candidates:t.candidates.map(c=>c.id)}};}),button('Пропустить',()=>({path:`/api/review/${t.id}/confirm`,body:{action:'ignore',reviewed_candidates:t.candidates.map(c=>c.id)}})));
      }
    }
    const raw=node('details',undefined,'raw-details');raw.append(node('summary','Исходные данные банка'),node('pre',JSON.stringify(t.raw,null,2)));detail.append(raw);item.append(detail);group.append(item);
  }
  if(!rows.length)container.append(node('p',filtered.length?'Нет операций с выбранным статусом.':'За выбранный период операций нет.','empty'));
  if(rows.length>limit){const more=node('button',`Показать ещё · осталось ${rows.length-limit}`,'secondary');more.onclick=onMore;container.append(more);}
  return {counts,total:rows.length,shown:Math.min(limit,rows.length)};
}
