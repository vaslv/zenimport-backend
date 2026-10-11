// Runs in Chrome's ISOLATED world. Never reads cookies, storage, or bank API responses.
(async () => {
  const collectAll = globalThis.__zenImportAllPages === true;
  delete globalThis.__zenImportAllPages;
  const text = node => {if(!node)return '';const clean=node.cloneNode(true);clean.querySelectorAll?.('zenimport-operation').forEach(e=>e.remove());return clean.textContent?.replace(/\s+/g, ' ').trim()||'';};
  const fail = message => { throw new Error(message); };
  const day = value => {
    const m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value);
    if (!m) fail('Неподдерживаемая дата VietinBank.');
    const iso = `${m[3]}-${m[2]}-${m[1]}`;
    const d = new Date(iso + 'T00:00:00Z');
    if (!Number.isFinite(d.getTime()) || d.toISOString().slice(0,10) !== iso) fail('Некорректная дата.');
    return iso;
  };
  const money = (value, signed) => {
    const pattern = signed ? /^([+-])((?:\d{1,3}(?:,\d{3})+|\d+)) VND$/ : /^([+-]?)((?:\d{1,3}(?:,\d{3})+|\d+)) VND$/;
    const m = pattern.exec(value);
    if (!m) fail('Неоднозначная сумма или валюта; ожидается VND.');
    const result = Number(m[1] + m[2].replaceAll(',',''));
    if (!Number.isSafeInteger(result) || Math.abs(result)>9000000000000 || (signed && !result)) fail('Некорректная сумма.');
    return result;
  };
  const fingerprint = async parts => {
    const bytes = await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(parts)));
    return 'vib:f1:' + [...new Uint8Array(bytes)].map(b=>b.toString(16).padStart(2,'0')).join('');
  };
  function vietinSnapshot() {
    if(location.protocol!=='https:' || location.hostname!=='ipay.vietinbank.vn' || location.pathname!=='/payment-history') fail('Откройте историю текущего счёта VietinBank.');
    const account = new URL(location.href).searchParams.get('accountNo');
    const selected = text(document.querySelector('.vt-select__single-value .cus-option'));
    if (!account || !/^\d{8,20}$/.test(account) || !selected.startsWith(account+' - ') || !selected.endsWith(' - Current account')) fail('Не удалось однозначно определить счёт.');
    const tables = [...document.querySelectorAll('table.cms-table')];
    if(tables.length!==1) fail('Неоднозначная таблица истории.');
    const table=tables[0], headers=[...table.querySelectorAll('thead th,thead td')].map(text);
    if(headers.join('|')!=='DATE|DESCRIPTION|AMOUNT|BALANCE') fail('Поддерживается проверенная английская версия истории DATE / DESCRIPTION / AMOUNT / BALANCE.');
    const rows=[...table.querySelectorAll('tbody tr:not([data-zenimport-row])')].map(row=>[...row.querySelectorAll('td')].map(text));
    if(!rows.length || rows.some(r=>r.length!==4)) fail('Нет загруженных операций или формат строк изменился.');
    const range=text(document.querySelector('.table-pagination-total-range')).match(/^(\d+)-(\d+)$/);
    const totalText=text(document.querySelector('.table-pagination-total-total'));
    if(!range || !/^\d+$/.test(totalText)) fail('Не удалось проверить количество строк истории.');
    const start=Number(range[1]), end=Number(range[2]), total=Number(totalText);
    if(start<1 || end>total || end-start+1!==rows.length || total>100000) fail('Таблица ещё загружается или счётчик изменился.');
    const period=document.querySelector('input[placeholder="Choose time range"]')?.value;
    let from=null,to=null;
    if(period) {
      const m=/^(\d{2}\/\d{2}\/\d{4}) - (\d{2}\/\d{2}\/\d{4})$/.exec(period);
      if(!m) fail('Не удалось распознать выбранный период.');
      from=day(m[1]);to=day(m[2]);
      if(from>to) fail('Неверный период.');
    }
    const filters=[...document.querySelectorAll('input')].map(e=>[e.placeholder,e.type,e.type==='radio'?e.checked:e.value]);
    return {account,rows,start,end,total,from,to,filters,signature:JSON.stringify(rows)};
  }
  async function vietinExtract() {
    const initial=vietinSnapshot();
    if(collectAll && initial.start!==1) fail('Для сбора периода сначала откройте страницу 1 истории.');
    if(collectAll && initial.total>2000) fail('В одном импорте до 2000 операций. Уменьшите период.');
    if(collectAll && !initial.from) fail('Откройте Filter, чтобы выбранный период был виден. Примените Search перед сбором.');
    const stableContext=s=>s.account===initial.account && s.total===initial.total && s.from===initial.from && s.to===initial.to && JSON.stringify(s.filters)===JSON.stringify(initial.filters);
    let snapshot=initial, rows=[...initial.rows], pages=1;
    while(collectAll && snapshot.end<initial.total) {
      const next=document.querySelector(`.table-pagination [role="button"][aria-label="Page ${pages+1}"],.table-pagination button[aria-label="Page ${pages+1}"]`);
      if(!next || next.disabled || next.getAttribute('aria-disabled')==='true') fail('Не найдена следующая страница. Данные не отправлены.');
      const previous=snapshot;
      next.click();
      const deadline=Date.now()+30000;
      let candidate=null;
      while(Date.now()<deadline) {
        await new Promise(resolve=>setTimeout(resolve,300));
        let s; try{s=vietinSnapshot();}catch{continue;}
        if(!stableContext(s)) fail('Счёт, фильтр или количество операций изменились. Начните сбор заново.');
        if(s.start!==previous.end+1 || s.signature===previous.signature) continue;
        if(candidate?.signature===s.signature && candidate.end===s.end) {snapshot=s;break;}
        candidate=s;
      }
      if(snapshot===previous) fail('Страница не загрузилась за 30 секунд. Импорт отменён; можно повторить с первой страницы.');
      rows.push(...snapshot.rows);pages++;
    }
    const transactions=[];const seen=new Set();
    for(const [rawDate,description,amount,balance] of rows) {
      const m=/^(\d{2}\/\d{2}\/\d{4}) (\d{2}):(\d{2}):(\d{2})$/.exec(rawDate);
      if(!m || +m[2]>23 || +m[3]>59 || +m[4]>59 || !description) fail('Неполная дата/время или описание операции.');
      const date=day(m[1]), value=money(amount,true), closing=money(balance,false);
      if(initial.from && (date<initial.from || date>initial.to)) fail('Операция вне выбранного периода. Сначала примените Search.');
      const id=await fingerprint([initial.account,rawDate,value,closing,description]);
      if(seen.has(id)) fail('Найдены неразличимые строки. Автоматическое объединение запрещено.');
      seen.add(id);
      transactions.push({external_id:id,date,amount_minor:value,description,
        raw:{reference:id,reference_kind:'fingerprint',date:rawDate,amount,description,balance}});
    }
    if(collectAll && transactions.length!==initial.total) fail('Число собранных операций не совпало с банком.');
    return {bank:'vietinbank',adapter_version:'1.0.0',account:initial.account,currency:'VND',transactions,
      capture:{start_date:initial.from,end_date:initial.to,row_start:initial.start,row_end:snapshot.end,total_rows:initial.total,pages,
        complete:initial.start===1 && snapshot.end===initial.total}};
  }
  function demoExtract() {
    const table=document.querySelector('table[data-zenbridge-demo]');
    if(!table) fail('Демонстрационная таблица не найдена.');
    const transactions=[...table.querySelectorAll('tbody tr')].map(row=>{
      const cells=[...row.querySelectorAll('td')].map(text);
      if(cells.length!==4) fail('Формат строки изменился. Импорт остановлен.');
      const [reference,date,amount,description]=cells;
      if(!reference || !/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)$/.test(amount)) fail('Неоднозначная дата, сумма или банковский ID.');
      const value=Number(amount.replaceAll(',',''));
      if(!Number.isSafeInteger(value)||!value)fail('Некорректная сумма');
      return {external_id:reference,date,amount_minor:value,description,raw:{reference,date,amount,description}};
    });
    if(!transactions.length || transactions.length>2000)fail('Нужно от 1 до 2000 видимых операций.');
    return {bank:'demo',adapter_version:'1.0.0',account:table.dataset.account,currency:'VND',transactions};
  }
  let ownsLock=false;
  try {
    if(globalThis.__zenImportCollectionRunning)fail('Предыдущий сбор ещё выполняется. Дождитесь его завершения.');
    if(collectAll){globalThis.__zenImportCollectionRunning=true;ownsLock=true;}
    if(location.hostname==='ipay.vietinbank.vn')return {ok:true,data:await vietinExtract()};
    if(['127.0.0.1','localhost'].includes(location.hostname)&&location.pathname==='/demo-bank')return {ok:true,data:demoExtract()};
    fail('Банк пока не поддерживается. Нужен проверенный адаптер для этого домена.');
  }catch(error){return {ok:false,error:error.message};}
  finally{if(ownsLock)delete globalThis.__zenImportCollectionRunning;}
})();
