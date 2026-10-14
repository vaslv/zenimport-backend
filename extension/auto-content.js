// ISOLATED world controller. Reads only the history UI and sanitized response messages.
(() => {
  const config=globalThis.__zenImportAutoConfig;delete globalThis.__zenImportAutoConfig;
  if(!config)return;
  globalThis.__zenImportAutoAbort?.();
  let stopped=false,accountReady=false,gate=Infinity,inbox=[];
  const rpc=async(type,extra={})=>{const r=await chrome.runtime.sendMessage({type,id:config.id,...extra});if(!r?.ok)throw Error(r?.error||'Связь с загрузчиком потеряна.');return r;};
  const event=(stage,details={})=>rpc('auto-event',{stage,details});
  const visible=e=>{if(!e||e.closest('[aria-hidden="true"]'))return false;const r=e.getBoundingClientRect();return !!(r.width&&r.height)&&getComputedStyle(e).visibility!=='hidden';};
  const text=e=>e?.textContent?.replace(/\s+/g,' ').trim()||'';
  function guard(){
    if(stopped)throw Error('Загрузка остановлена.');
    if(location.pathname!=='/payment-history'||new URL(location.href).searchParams.get('accountNo')!==config.account)throw Error('Банк сменил страницу или завершил сессию.');
    const selected=text(document.querySelector('.vt-select__single-value .cus-option'));
    if(accountReady&&(!selected.startsWith(config.account+' - ')||!selected.endsWith(' - Current account')))throw Error('Выбран другой или неподдерживаемый счёт.');
  }
  async function wait(check,action,limit=30000){
    const start=Date.now();let polls=0;await event('wait',{action});
    while(Date.now()-start<limit){guard();await rpc('auto-check');const value=check();if(value){await event('wait_done',{action,ms:Date.now()-start,polls});return value;}polls++;await new Promise(r=>setTimeout(r,350));}
    throw Error(`Не дождался этапа ${action}. Проверьте вход, Network и сообщение банка.`);
  }
  async function click(node,action){guard();if(!node||node.disabled||node.getAttribute('aria-disabled')==='true')throw Error(`Элемент ${action} недоступен.`);await event('click',{action});node.click();}
  const button=label=>[...document.querySelectorAll('button')].find(b=>visible(b)&&text(b)===label);
  const dateLabel=iso=>new Date(iso+'T00:00:00Z').toLocaleDateString('en-US',{timeZone:'UTC',weekday:'long',month:'long',day:'numeric',year:'numeric'});
  const calendar=()=>document.querySelector('[role="application"][aria-label="Calendar"]');
  function days(){const cal=calendar();if(!cal)return [];const bounds=cal.getBoundingClientRect();return [...cal.querySelectorAll('[role="button"],button')].filter(e=>{const r=e.getBoundingClientRect();return visible(e)&&r.left>=bounds.left-1&&r.right<=bounds.right+1&&/\d{4}/.test(e.getAttribute('aria-label')||'');});}
  async function pick(iso,kind){
    for(let step=0;step<25;step++){
      const available=await wait(()=>days().length&&days(),'calendar_days');
      const target=available.find(e=>(e.getAttribute('aria-label')||'').includes(dateLabel(iso)));
      if(target){await click(target,kind);return;}
      const match=(available[0].getAttribute('aria-label')||'').match(/([A-Z][a-z]+ \d{1,2}, \d{4})/);
      if(!match)throw Error('Неизвестная разметка дат календаря.');
      const backwards=new Date(iso+'T00:00:00Z')<new Date(match[1]+' UTC');
      const signature=available.map(e=>e.getAttribute('aria-label')).join('|');
      await click(calendar().querySelector(`img[alt="button ${backwards?'back':'next'}"]`),backwards?'previous_month':'next_month');
      await wait(()=>days().map(e=>e.getAttribute('aria-label')).join('|')!==signature,'calendar_changed');
    }
    throw Error('Превышен предел переходов календаря.');
  }
  const display=iso=>iso.split('-').reverse().join('/');
  async function filter(period){
    if(!visible(document.querySelector('input[placeholder="Choose time range"]')))await click(button('Filter'),'open_filter');
    const input=await wait(()=>{const e=document.querySelector('input[placeholder="Choose time range"]');return visible(e)&&e;},'filter_ready');
    for(const placeholder of ['Input keyword to search','From','To']){
      const field=document.querySelector(`input[placeholder="${placeholder}"]`);
      if(field?.value){await event('click',{action:'clear_filter'});Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(field,'');field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));}
    }
    const radios=[...document.querySelectorAll('input[type="radio"]')];
    const all=radios.find(r=>text(r.closest('label')||r.parentElement)==='All');
    if(!all)throw Error('Не найден фильтр All.');if(!all.checked)await click(all,'all_transactions');
    await click(input,'open_calendar');await wait(calendar,'calendar_ready');
    await pick(period.from,'start_date');await pick(period.to,'end_date');
    await click(button('Chọn'),'apply_dates');
    await wait(()=>input.value===`${display(period.from)} - ${display(period.to)}`,'dates_applied');
    await event('filter',period);
  }
  const accept=(data,startedAt,error)=>{
    if(stopped||!(startedAt>=gate))return;
    rpc('auto-observed',{data,readError:!!error}).then(()=>{if(!stopped)inbox.push({data,error});}).catch(()=>{if(!stopped)inbox.push({error:true});});
  };
  const onWindow=e=>{if(e.source===window&&e.origin===location.origin&&['zenimport-history','zenimport-inline-history'].includes(e.data?.source))accept(e.data.data,e.data.startedAt,e.data.error);};
  const onExtension=m=>{if(m?.type==='auto-bank-data'&&m.id===config.id)accept(m.data,m.startedAt,m.error);};
  window.addEventListener('message',onWindow);chrome.runtime.onMessage.addListener(onExtension);
  function cleanup(){stopped=true;window.removeEventListener('message',onWindow);chrome.runtime.onMessage.removeListener(onExtension);}
  globalThis.__zenImportAutoAbort=cleanup;
  async function response(page){return wait(()=>{
    const found=inbox.find(e=>e.error||e.data?.currentPage===page);
    if(found){inbox=inbox.filter(e=>e!==found);if(found.error)throw Error('Ответ истории не удалось прочитать.');return found.data;}
    return null;
  },'api_response',45000);}
  async function walkUntil(rows,total){
    await wait(()=>Number(text(document.querySelector('.table-pagination-total-total')))===total,'table_total');
    for(let attempts=0;attempts<10000;attempts++){
      const totalNode=document.querySelector('.table-pagination-total-total');
      if(Number(text(totalNode))!==total)throw Error('Счётчик таблицы не совпадает с ответом API.');
      const range=text(document.querySelector('.table-pagination-total-range')).match(/^(\d+)-(\d+)$/);
      const current=document.querySelector('.table-pagination [aria-label*="is your current page"]');
      if(!range||!current)throw Error('Не удалось определить страницу таблицы.');
      const end=Number(range[2]);if(end>=rows)return;
      const page=Number((current.getAttribute('aria-label')||'').match(/Page (\d+)/)?.[1]);
      const next=document.querySelector(`.table-pagination [aria-label="Page ${page+1}"]`);
      await click(next,'next_table_page');
      await wait(()=>text(document.querySelector('.table-pagination-total-range'))!==range[0],'table_page');
    }
    throw Error('Превышен предел страниц таблицы.');
  }
  (async()=>{
    try{
      await wait(()=>text(document.querySelector('.vt-select__single-value .cus-option')),'account_ready');accountReady=true;guard();
      for(let part=0;part<config.parts.length;part++){
        await rpc('auto-begin',{part});await filter(config.parts[part]);
        inbox=[];gate=Date.now();await event('search',{part,...config.parts[part]});await click(button('Search'),'search');
        let page=0,done=await rpc('auto-page',{part,data:await response(page)});
        while(done.rows<done.total){
          // The bank requests another JSON page only after exhausting its cached table rows.
          await walkUntil(done.rows,done.total);
          const current=document.querySelector('.table-pagination [aria-label*="is your current page"]');
          const n=Number(current?.getAttribute('aria-label')?.match(/Page (\d+)/)?.[1]);
          await click(document.querySelector(`.table-pagination [aria-label="Page ${n+1}"]`),'next_api_page');
          done=await rpc('auto-page',{part,data:await response(++page)});
        }
        if(done.total){await wait(()=>Number(text(document.querySelector('.table-pagination-total-total')))===done.total,'verify_total');}
        await rpc('auto-end',{part});
      }
    }catch(error){await rpc('auto-error',{error:error.message}).catch(()=>{});}
    finally{cleanup();window.postMessage({source:'zenimport-stop-auto'},location.origin);}
  })();
})();
