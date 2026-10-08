import {exclusionButton} from './exclusions-view.js';
// Decimal strings stay decimal strings: formatting never rounds them through Number.
export function formatMoney(value, currency='') {
  if(value===null||value===undefined)return 'Сумма не указана';
  const match=/^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(String(value));
  if(!match)return 'Сумма не указана';
  const exponent=Number(match[4]||0);
  if(Math.abs(exponent)>100)return 'Сумма вне диапазона';
  let integer=match[2],fraction=match[3]||'';
  const digits=integer+fraction,point=integer.length+exponent;
  if(point<=0){integer='0';fraction='0'.repeat(-point)+digits;}
  else if(point>=digits.length){integer=digits+'0'.repeat(point-digits.length);fraction='';}
  else{integer=digits.slice(0,point);fraction=digits.slice(point);}
  fraction=fraction.replace(/0+$/,'');
  if(/^[A-Z]{3}$/.test(currency)){
    const digits=new Intl.NumberFormat('ru-RU',{style:'currency',currency}).resolvedOptions().minimumFractionDigits;
    fraction=fraction.padEnd(digits,'0');
  }
  const grouped=new Intl.NumberFormat('ru-RU').format(BigInt(integer));
  return `${match[1]}${grouped}${fraction?','+fraction:''}${currency?' '+currency:''}`;
}
const node=(tag,text,className)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;};
const nonzero=value=>value!=null&&/[1-9]/.test(String(value).split(/[eE]/)[0]);
export function lookups(state){
  const map=items=>new Map((items||[]).map(x=>[String(x.id),x]));
  return {accounts:map(state.zenmoney_accounts),tags:map(state.categories),instruments:map(state.zenmoney_instruments),merchants:map(state.zenmoney_merchants)};
}
function accountName(id,lookup){return lookup.accounts.get(String(id))?.title||'Неизвестный счёт';}
function currency(id,lookup){return lookup.instruments.get(String(id))?.shortTitle||'';}
export function renderAccountCards(container,state,{showArchived,onAccount}) {
  container.replaceChildren();const lookup=lookups(state);
  const all=state.zenmoney_accounts.filter(a=>!a.deleted),visible=all.filter(a=>showArchived||!a.archive);
  for(const account of visible){
    const button=node('button',undefined,'account-card');button.type='button';button.dataset.apiAction='';button.dataset.accountId=account.id;
    button.setAttribute('aria-label',`Операции по счёту ${account.title}`);
    const top=node('span',undefined,'account-card-top');top.append(node('strong',account.title));
    if(account.archive)top.append(node('span','Архив','badge archive-badge'));
    button.append(top,node('span',currency(account.instrument,lookup)||'Валюта не указана','account-currency'),node('span','Смотреть операции →','account-link'));
    button.onclick=()=>onAccount(String(account.id));container.append(button);
  }
  if(!visible.length)container.append(node('p',all.length?'Активных счетов нет. Включите показ архивных.':'Счета ещё не загружены. Запустите синхронизацию.','empty'));
  return {visible:visible.length,total:all.length,archived:all.filter(a=>a.archive).length};
}
function dateLabel(value){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(value||''))return 'Дата не указана';
  return new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}).format(new Date(value+'T00:00:00Z'));
}
export function renderHistory(container,items,state,selectedAccount,onAction){
  container.replaceChildren();const lookup=lookups(state);let lastDay=null,list;
  for(const tx of items){
    if(!list||tx.date!==lastDay){
      lastDay=tx.date;const section=node('section',undefined,'transaction-day');section.dataset.date=tx.date||'';
      section.append(node('h3',dateLabel(tx.date),'day-heading'));list=node('div',undefined,'transaction-list');section.append(list);container.append(section);
    }
    const income=tx._amounts?.income??tx.income,outcome=tx._amounts?.outcome??tx.outcome;
    const hasIncome=nonzero(income),hasOutcome=nonzero(outcome);
    const transfer=hasIncome&&hasOutcome&&tx.incomeAccount!==tx.outcomeAccount;
    const kind=transfer?'Перевод':hasIncome&&hasOutcome?'Движения по счёту':hasIncome?'Доход':hasOutcome?'Расход':'Нулевая операция';
    const categoryNames=(tx.tag||[]).map(id=>lookup.tags.get(String(id))?.title||'Неизвестная категория');
    const payee=tx.payee||lookup.merchants.get(String(tx.merchant))?.title;
    const row=node('article',undefined,'transaction-row');row.dataset.transactionId=tx.id;
    const content=node('div',undefined,'transaction-main');
    const badges=node('div',undefined,'transaction-labels');badges.append(node('span',kind,transfer?'type-transfer':'transaction-type'));
    if(tx.hold)badges.append(node('span','Ожидает проведения','badge'));
    if(tx.deleted)badges.append(node('span','Удалена','badge'));
    content.append(badges,node('h4',payee||tx.comment||(transfer?'Перевод между счетами':'Без описания')));
    if(tx.comment&&tx.comment!==payee&&payee)content.append(node('p',tx.comment,'transaction-comment'));
    const accounts=transfer?`${accountName(tx.outcomeAccount,lookup)} → ${accountName(tx.incomeAccount,lookup)}`:
      accountName(hasOutcome?tx.outcomeAccount:tx.incomeAccount,lookup);
    content.append(node('p',accounts,'transaction-accounts'),node('p',categoryNames.join(' · ')||'Без категории','transaction-category'));
    const sums=node('div',undefined,'transaction-sums');
    const showSide=account=>!selectedAccount||!transfer||String(account)===selectedAccount;
    if(hasOutcome&&showSide(tx.outcomeAccount))sums.append(node('strong','−'+formatMoney(outcome,currency(tx.outcomeInstrument??lookup.accounts.get(String(tx.outcomeAccount))?.instrument,lookup)),'money expense'));
    if(hasIncome&&showSide(tx.incomeAccount))sums.append(node('strong','+'+formatMoney(income,currency(tx.incomeInstrument??lookup.accounts.get(String(tx.incomeAccount))?.instrument,lookup)),'money income'));
    if(!hasIncome&&!hasOutcome)sums.append(node('strong',formatMoney(income??outcome,currency(tx.incomeInstrument,lookup)),'money'));
    if(transfer&&selectedAccount)sums.append(node('span','Движение по выбранному счёту','muted'));
    const details=node('details',undefined,'transaction-details');details.append(node('summary','Подробности'));
    const dl=node('dl');
    const fields=[['ID операции',tx.id],['Исходный получатель',tx.originalPayee]];
    if(transfer)fields.push(['Списано',formatMoney(outcome,currency(tx.outcomeInstrument??lookup.accounts.get(String(tx.outcomeAccount))?.instrument,lookup))],['Зачислено',formatMoney(income,currency(tx.incomeInstrument??lookup.accounts.get(String(tx.incomeAccount))?.instrument,lookup))]);
    for(const [label,value] of fields)if(value){dl.append(node('dt',label),node('dd',String(value)));}
    if(onAction&&state.capabilities?.local_review){const excluded=(state.excluded_zen_ids||[]).includes(tx.id);if(excluded)badges.append(node('span','Не требует сведения','badge'));const linked=state.review.some(r=>(r.group?.zen_ids||r.split_ids||[]).includes(tx.id)||r.linked_id===tx.id)||state.outbox.some(o=>o.zen_id===tx.id);if(!linked)details.append(exclusionButton('zenmoney',tx.id,excluded,onAction));}
    details.append(dl);content.append(details);row.append(content,sums);list.append(row);
  }
  if(!items.length)container.append(node('p','Операций по выбранным условиям нет.','empty'));
}
