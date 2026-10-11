// ISOLATED world: account-scoped UI. Credentials remain in the service worker.
(() => {
  if(globalThis.__zenImportInlineUI)return;globalThis.__zenImportInlineUI=true;
  let account=null,generation=0,state=null,fresh=false,busy=false,signature='',timer,refreshAt=0;
  const mounted=new Map();let banner,activeMenu,loadError='';
  const text=e=>e?.textContent?.replace(/\s+/g,' ').trim()||'';
  const norm=v=>String(v??'').replace(/\s+/g,' ').trim();
  const el=(tag,value)=>{const e=document.createElement(tag);if(value!==undefined)e.textContent=value;return e;};
  const css=`:host{display:block;width:100%;color:#292735;font:14px/1.4 system-ui,sans-serif;text-align:left}*{box-sizing:border-box}[hidden]{display:none!important}section{padding:8px 12px;min-width:0}.operation{display:grid;grid-template-columns:minmax(180px,1fr) minmax(200px,1.5fr) minmax(220px,1.5fr);align-items:center;gap:16px;background:#f8f7fb;border-bottom:1px solid #e4e0ed}p{margin:0}strong{font-weight:500}button,input,select{font:inherit}button{cursor:pointer;border:0;background:transparent;color:#476fa7;padding:0;text-decoration:underline}button:disabled{cursor:wait;opacity:.6}.good{color:#258057}.error{color:#ae3737}.picker{min-width:0}.trigger{display:flex;justify-content:space-between;align-items:center;gap:8px;width:100%;padding:8px 10px;border:1px solid #d8d3e0;border-radius:6px;background:white;color:#292735;text-decoration:none;text-align:left}.menu{position:fixed;z-index:2147483647;padding:6px;border:1px solid #d8d3e0;border-radius:6px;background:white;box-shadow:0 6px 20px #0002;max-height:320px;overflow:auto}.menu input{width:100%;padding:8px;border:1px solid #d8d3e0;border-radius:4px;position:sticky;top:0;background:white;color:#292735}.options{display:grid;padding-top:4px}.options button{text-align:left;color:#292735;text-decoration:none;padding:8px;border-radius:4px}.options button:hover,.options button:focus{background:#eeeaf5;outline:2px solid #b4a6cb}.options button[aria-selected=true]{background:#f3effa;font-weight:600}.hints{overflow-wrap:anywhere}.hints button{display:inline} @media(max-width:700px){.operation{grid-template-columns:1fr;gap:6px}.menu{max-height:240px}}`;
  function host(){const h=el('zenimport-operation');const root=h.attachShadow({mode:'open'});const style=el('style',css),box=el('section');root.append(style,box);return {h,box};}
  function clear(){activeMenu?.close();for(const panel of mounted.values())panel.container?.remove();document.querySelectorAll('tr[data-zenimport-row]').forEach(h=>h.remove());mounted.clear();signature='';}
  function status(message,error=false){if(!document.body)return;if(!banner?.h.isConnected){banner=host();banner.h.id='zenimport-status';document.body.prepend(banner.h);}if(banner.message===message&&banner.error===error)return;banner.message=message;banner.error=error;banner.box.replaceChildren(el('strong','ZenImport · '),el('span',message));banner.box.setAttribute('role','status');banner.box.className=error?'error':'';if(error){const retry=el('button','Повторить получение');retry.onclick=e=>{if(e.isTrusted)start();};banner.box.append(document.createTextNode(' · '),retry);}}
  async function rpc(type,extra={}){const r=await chrome.runtime.sendMessage({type,account,...extra});if(!r?.ok)throw Error(r?.error||'Перезагрузите страницу после обновления расширения.');return r.data;}
  function current(){const u=new URL(location.href);if(u.pathname!=='/payment-history')return null;const a=u.searchParams.get('accountNo');return /^\d{6,30}$/.test(a||'')?a:null;}
  async function start(){
    const g=++generation;fresh=false;loadError='';state=null;clear();status('Получаем актуальные данные ZenMoney…');render();
    try{const result=await rpc('inline-sync');if(g!==generation||current()!==account)return;state=result;fresh=true;refreshAt=Date.now();signature='';render();}
    catch(e){if(g===generation){loadError=e.message;signature='';render();status(e.message,true);}}
  }
  const number=v=>{const s=norm(v).replace(/ VND$/,'').replaceAll(',','');return /^[+-]?\d+$/.test(s)&&Number.isSafeInteger(Number(s))?Number(s):null;};
  function matches(cells,row){
    const raw=row.raw||{},date=norm(raw.date||raw.details?.processDate).replaceAll('-','/');
    return date===cells[0].replaceAll('-','/')&&norm(row.description)===cells[1]&&row.amount_minor===number(cells[2])&&number(raw.balance)===number(cells[3]);
  }
  function view(row){
    if(row.state==='ignored')return {label:'Игнорируется'};
    const ids=row.group?.zen_ids||(row.split_ids?.length?row.split_ids:[row.linked_id]);
    const zs=state.days.flatMap(d=>d.zen_rows||[]).filter(z=>ids.includes(z.id));
    const uncertain=row.creation_job&&['uncertain','conflict'].includes(row.creation_job.state)||state.sends.some(s=>ids.includes(s.zen_id)&&['uncertain','conflict'].includes(s.state));
    if(uncertain)return {label:'Нужно проверить результат отправки'};
    if(row.linked&&['linked','sent'].includes(row.state)){
      const pending=zs.some(z=>z.category_draft);
      const complete=zs.length===ids.length&&zs.every(z=>z.transfer||z.categories?.length);
      const single=row.group?.kind==='single'&&zs.length===1&&!zs[0].transfer;
      const z=zs[0];
      return {label:pending?'Изменения ожидают отправки':complete?'✓ В ZenMoney · '+zs.flatMap(z=>z.categories||[]).join(', '):'Нужна категория',good:!pending&&complete,
        editable:single,selected:z?.category_draft?.valid?z.category_draft.id:z?.tag_ids?.[0]||'',ranking:z?.category_ranking||[]};
    }
    if(row.creation_job?.state==='prepared')return {label:'Создана локально · ожидает отправки'};
    const unresolved=row.candidates?.length||state.days.some(d=>(d.split_candidates||[]).some(p=>p.bank_id===row.id));
    if(!row.mapped)return {label:'Нужно связать банковский счёт с ZenMoney'};
    if(unresolved||row.state!=='pending')return {label:'Нужно уточнить соответствие'};
    return {label:row.creation_draft?.valid?'Новая · категория выбрана локально':'Новая · выберите категорию',editable:true,
      selected:row.creation_draft?.valid?row.creation_draft.category_id:'',ranking:row.creation_ranking||[]};
  }
  function card(row){
    const panel=host(),v=view(row),heading=el('strong',v.label);panel.box.className='operation';if(v.good)heading.className='good';panel.box.append(heading);
    if(v.editable){
      const categories=[...state.categories].sort((a,b)=>a.title.localeCompare(b.title,'ru'));
      const ranked=(v.ranking||[]).filter(r=>categories.some(c=>c.id===r.id)).sort((a,b)=>b.count-a.count);
      const ordered=[...ranked.map(r=>categories.find(c=>c.id===r.id)),...categories.filter(c=>!ranked.some(r=>r.id===c.id))];
      const hints=el('p');hints.className='hints';
      const picker=el('div');picker.className='picker';
      const select=el('select');select.hidden=true;select.setAttribute('aria-label','Категория');select.add(new Option('Выберите категорию',''));for(const c of ordered)select.add(new Option(c.title,c.id));select.value=v.selected;
      const trigger=el('button');trigger.className='trigger';trigger.type='button';trigger.setAttribute('role','combobox');trigger.setAttribute('aria-label','Категория');trigger.setAttribute('aria-expanded','false');trigger.setAttribute('aria-haspopup','listbox');trigger.append(el('span',categories.find(c=>c.id===v.selected)?.title||'Выберите категорию'),el('span','▾'));
      const menu=el('div');menu.className='menu';menu.hidden=true;
      const search=el('input');search.type='search';search.placeholder='Поиск категории';search.setAttribute('aria-label','Поиск категории');
      const options=el('div');options.className='options';options.id='category-options';options.setAttribute('role','listbox');trigger.setAttribute('aria-controls',options.id);
      const close=()=>{menu.hidden=true;trigger.setAttribute('aria-expanded','false');if(activeMenu?.h===panel.h)activeMenu=null;};
      const position=()=>{const r=trigger.getBoundingClientRect();menu.style.left=Math.max(8,Math.min(r.left,innerWidth-r.width-8))+'px';menu.style.width=Math.min(r.width,innerWidth-16)+'px';menu.style.top=Math.max(8,Math.min(r.top,innerHeight-180))+'px';menu.style.maxHeight=Math.max(120,Math.min(320,innerHeight-parseFloat(menu.style.top)-8))+'px';};
      async function save(id){
        if(!id||busy)return;close();busy=true;let failure;const g=generation;trigger.disabled=true;select.value=id;heading.textContent='Сохраняем…';
        try{const result=await rpc('inline-category',{id:row.id,category:id});if(g===generation){state=result;signature='';}}
        catch(e){if(g===generation)failure=e.message;}
        finally{busy=false;if(g===generation){signature='';render();if(failure)status(failure,true);}}
      }
      const fill=()=>{options.replaceChildren();for(const c of ordered.filter(c=>c.title.toLocaleLowerCase('ru').includes(search.value.toLocaleLowerCase('ru')))){const option=el('button',c.title);option.type='button';option.setAttribute('role','option');option.setAttribute('aria-selected',String(c.id===v.selected));option.onclick=e=>{if(e.isTrusted)save(c.id);};options.append(option);}if(!options.children.length)options.append(el('p','Категории не найдены'));};
      const open=()=>{if(busy)return;activeMenu?.close();activeMenu={h:panel.h,close,position};search.value='';fill();menu.hidden=false;position();trigger.setAttribute('aria-expanded','true');search.focus();};
      trigger.onclick=e=>{if(e.isTrusted)menu.hidden?open():close();};
      trigger.onkeydown=e=>{if(e.key==='ArrowDown'){e.preventDefault();open();}};
      menu.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();close();trigger.focus();}else if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();const all=[search,...options.querySelectorAll('button')],i=all.indexOf(panel.h.shadowRoot.activeElement);all[(i+(e.key==='ArrowDown'?1:-1)+all.length)%all.length].focus();}};
      search.oninput=fill;menu.append(search,options);picker.append(select,trigger,menu);
      if(ranked.length){hints.append(document.createTextNode('Подходят: '));ranked.forEach((r,i)=>{if(i)hints.append(document.createTextNode(', '));const c=categories.find(c=>c.id===r.id),b=el('button',c.title);b.title=`Подтверждённых примеров: ${r.count}`;b.onclick=e=>{if(e.isTrusted)save(c.id);};hints.append(b);});}
      panel.box.append(hints,picker);
    }
    panel.h.addEventListener('click',e=>e.stopPropagation());return panel;
  }
  function render(){
    if(!account||busy)return;
    const selected=text(document.querySelector('.vt-select__single-value .cus-option'));
    if(!selected.startsWith(account+' - ')||!selected.endsWith(' - Current account')){clear();status('Ожидаем историю выбранного счёта…');return;}
    const tables=[...document.querySelectorAll('table.cms-table')];
    if(tables.length!==1||[...tables[0].querySelectorAll('thead th,thead td')].map(text).join('|')!=='DATE|DESCRIPTION|AMOUNT|BALANCE'){clear();status('Ожидаем таблицу истории DATE / DESCRIPTION / AMOUNT / BALANCE…');return;}
    const rows=[...tables[0].querySelectorAll('tbody tr:not([data-zenimport-row])')].map(tr=>({tr,cells:[...tr.children].filter(n=>n.tagName==='TD').map(td=>{const clone=td.cloneNode(true);clone.querySelectorAll('zenimport-operation').forEach(e=>e.remove());return text(clone);})}));
    const key=JSON.stringify([fresh,loadError,rows.map(r=>r.cells)]);if(key===signature&&[...mounted].every(([tr,panel])=>tr.isConnected&&panel.container?.isConnected&&panel.container.previousElementSibling===tr))return;clear();signature=key;
    let found=0;
    for(const {tr,cells} of rows){if(cells.length!==4)continue;const matchesRows=fresh&&state?state.rows.filter(r=>matches(cells,r)):[];let panel;
      if(matchesRows.length===1&&rows.filter(r=>JSON.stringify(r.cells)===JSON.stringify(cells)).length===1){panel=card(matchesRows[0]);found++;}
      else{panel=host();panel.box.append(el('strong',!fresh?(loadError?'Данные недоступны · повторите получение':'Получаем данные ZenMoney…'):matchesRows.length?'Неоднозначная строка · нужна сверка':'Ожидаем данные операции из ответа банка'));}
      const container=el('tr'),cell=el('td');container.dataset.zenimportRow='';cell.colSpan=4;cell.style.cssText='padding:0!important;border:0!important;';container.append(cell);cell.append(panel.h);tr.after(container);panel.container=container;mounted.set(tr,panel);
    }
    if(fresh)status(`Данные ZenMoney получены. Опознано строк: ${found} из ${rows.length}.`);
  }
  window.addEventListener('message',async e=>{
    if(e.source!==window||e.origin!==location.origin||e.data?.source!=='zenimport-inline-history'||!account)return;
    const g=generation;
    if(e.data.error){status('Не удалось прочитать ответ банка. Повторите Search.',true);return;}
    try{const result=await rpc('inline-import',{data:e.data.data});if(g!==generation)return;state=result;signature='';render();}
    catch(error){if(g===generation){fresh=false;loadError=error.message;signature='';render();status(error.message,true);}}
  });
  function tick(){
    const next=current();if(next!==account){account=next;generation++;fresh=false;state=null;clear();banner?.h.remove();banner=null;if(account)start();}
    if(account){if(!fresh&&!banner)status('Получаем актуальные данные ZenMoney…');render();if(fresh&&!busy&&Date.now()-refreshAt>15000){refreshAt=Date.now();const g=generation;rpc('inline-state').then(s=>{if(g===generation){const changed=JSON.stringify(state)!==JSON.stringify(s);state=s;if(changed){signature='';render();}}},e=>{if(g===generation){fresh=false;loadError=e.message;signature='';render();status(e.message,true);}});}}
  }
  document.addEventListener('pointerdown',e=>{if(activeMenu&&!e.composedPath().includes(activeMenu.h))activeMenu.close();},true);
  document.addEventListener('focusin',e=>{if(activeMenu&&!e.composedPath().includes(activeMenu.h))activeMenu.close();},true);
  window.addEventListener('resize',()=>activeMenu?.position());
  window.addEventListener('scroll',()=>activeMenu?.position(),true);
  let scheduled=false;
  const observer=new MutationObserver(()=>{if(scheduled)return;scheduled=true;queueMicrotask(()=>{scheduled=false;tick();});});
  observer.observe(document,{childList:true,subtree:true,characterData:true});
  timer=setInterval(tick,1000);window.addEventListener('pagehide',()=>{observer.disconnect();activeMenu?.close();clearInterval(timer);generation++;clear();banner?.h.remove();},{once:true});
  tick();
})();
