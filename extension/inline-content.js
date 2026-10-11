// ISOLATED world: account-scoped UI. Credentials remain in the service worker.
(() => {
  if(globalThis.__zenImportInlineUI)return;globalThis.__zenImportInlineUI=true;
  let account=null,generation=0,state=null,fresh=false,busy=false,signature='',timer,refreshAt=0;
  const mounted=new Map();let banner;
  const text=e=>e?.textContent?.replace(/\s+/g,' ').trim()||'';
  const norm=v=>String(v??'').replace(/\s+/g,' ').trim();
  const el=(tag,value)=>{const e=document.createElement(tag);if(value!==undefined)e.textContent=value;return e;};
  const css=`:host{display:block;color:#292735;font:14px/1.5 system-ui,sans-serif;text-align:left;margin:8px 0}*{box-sizing:border-box}section{background:#f8f7fb;border:1px solid #e4e0ed;border-radius:10px;padding:12px;min-width:0}p{margin:4px 0}strong{display:block}button,input,select{font:inherit}input,select{display:block;width:100%;max-width:100%;margin:6px 0;padding:7px;border:1px solid #d8d3e0;border-radius:6px;background:white;color:#292735}button{cursor:pointer;border:0;background:transparent;color:#476fa7;padding:0;text-decoration:underline}button:disabled{cursor:wait;opacity:.6}.good{color:#258057}.muted{color:#716b7e}a{color:#476fa7}.error{color:#ae3737}`;
  function host(){const h=el('zenimport-operation');const root=h.attachShadow({mode:'open'});const style=el('style',css),box=el('section');root.append(style,box);return {h,box};}
  function clear(){for(const {h} of mounted.values())h.remove();document.querySelectorAll('table.cms-table zenimport-operation').forEach(h=>h.remove());mounted.clear();signature='';}
  function status(message,error=false){if(!document.body)return;if(!banner?.h.isConnected){banner=host();banner.h.id='zenimport-status';document.body.prepend(banner.h);}banner.box.replaceChildren(el('strong','ZenImport'),el('p',message));banner.box.setAttribute('role','status');banner.box.className=error?'error':'';if(error){const retry=el('button','Повторить получение');retry.onclick=e=>{if(e.isTrusted)start();};banner.box.append(retry);}}
  async function rpc(type,extra={}){const r=await chrome.runtime.sendMessage({type,account,...extra});if(!r?.ok)throw Error(r?.error||'Перезагрузите страницу после обновления расширения.');return r.data;}
  function current(){const u=new URL(location.href);if(u.pathname!=='/payment-history')return null;const a=u.searchParams.get('accountNo');return /^\d{6,30}$/.test(a||'')?a:null;}
  async function start(){
    const g=++generation;fresh=false;state=null;clear();status('Получаем актуальные данные ZenMoney…');
    try{const result=await rpc('inline-sync');if(g!==generation||current()!==account)return;state=result;fresh=true;refreshAt=Date.now();signature='';render();}
    catch(e){if(g===generation)status(e.message,true);}
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
    const panel=host(),v=view(row),heading=el('strong',v.label);if(v.good)heading.className='good';panel.box.append(heading);
    if(v.editable){
      const search=el('input');search.type='search';search.placeholder='Поиск категории';search.setAttribute('aria-label','Поиск категории');
      const select=el('select');select.setAttribute('aria-label','Категория');
      const categories=[...state.categories].sort((a,b)=>a.title.localeCompare(b.title,'ru'));
      const ranked=(v.ranking||[]).filter(r=>categories.some(c=>c.id===r.id)).sort((a,b)=>b.count-a.count);
      const fill=()=>{select.replaceChildren(new Option('Выберите категорию',''));const ordered=[...ranked.map(r=>categories.find(c=>c.id===r.id)),...categories.filter(c=>!ranked.some(r=>r.id===c.id))];for(const c of ordered)if(c.id===v.selected||c.title.toLocaleLowerCase('ru').includes(search.value.toLocaleLowerCase('ru')))select.add(new Option(c.title,c.id));select.value=v.selected;};
      const note=el('p','Изменения сохраняются локально.');note.className='muted';
      async function save(id){
        if(!id||busy)return;busy=true;let failure;const g=generation;search.disabled=select.disabled=true;note.textContent='Сохраняем…';
        try{const result=await rpc('inline-category',{id:row.id,category:id});if(g===generation){state=result;signature='';render();}}
        catch(e){if(g===generation){failure=e.message;select.value=v.selected;}}
        finally{busy=false;search.disabled=select.disabled=false;if(g===generation){signature='';render();if(failure)status(failure,true);}}
      }
      if(ranked.length){const hints=el('p','Подходят: ');ranked.forEach((r,i)=>{if(i)hints.append(document.createTextNode(', '));const c=categories.find(c=>c.id===r.id),b=el('button',c.title);b.title=`Подтверждённых примеров: ${r.count}`;b.onclick=e=>{if(e.isTrusted&&!busy){select.value=c.id;save(c.id);}};hints.append(b);});panel.box.append(hints);}
      search.oninput=fill;select.onchange=e=>{if(e.isTrusted)save(select.value);};fill();panel.box.append(search,select,note);
    }
    const link=el('button','Открыть сверку в ZenImport');link.onclick=e=>{if(e.isTrusted)rpc('inline-open').catch(error=>status(error.message,true));};panel.box.append(link);
    // Prevent a category click from opening the bank transaction modal.
    panel.h.addEventListener('click',e=>e.stopPropagation());return panel;
  }
  function render(){
    if(!fresh||!state||busy)return;
    const selected=text(document.querySelector('.vt-select__single-value .cus-option'));
    if(!selected.startsWith(account+' - ')||!selected.endsWith(' - Current account')){clear();status('Ожидаем историю выбранного счёта…');return;}
    const tables=[...document.querySelectorAll('table.cms-table')];
    if(tables.length!==1||[...tables[0].querySelectorAll('thead th,thead td')].map(text).join('|')!=='DATE|DESCRIPTION|AMOUNT|BALANCE'){clear();status('Ожидаем таблицу истории DATE / DESCRIPTION / AMOUNT / BALANCE…');return;}
    const rows=[...tables[0].querySelectorAll('tbody tr')].map(tr=>({tr,cells:[...tr.children].filter(n=>n.tagName==='TD').map(td=>{const clone=td.cloneNode(true);clone.querySelectorAll('zenimport-operation').forEach(e=>e.remove());return text(clone);})}));
    const key=JSON.stringify(rows.map(r=>r.cells));if(key===signature&&[...mounted.keys()].every(tr=>tr.isConnected))return;clear();signature=key;
    let found=0;
    for(const {tr,cells} of rows){if(cells.length!==4)continue;const matchesRows=state.rows.filter(r=>matches(cells,r));let panel;
      if(matchesRows.length===1&&rows.filter(r=>JSON.stringify(r.cells)===JSON.stringify(cells)).length===1){panel=card(matchesRows[0]);found++;}
      else{panel=host();panel.box.append(el('strong',matchesRows.length?'Неоднозначная строка · нужна сверка':'Ожидаем данные операции из ответа банка'));}
      tr.children[1].append(panel.h);mounted.set(tr,panel);
    }
    status(`Данные ZenMoney получены. Опознано строк: ${found} из ${rows.length}. Выбор категорий сохраняется локально.`);
  }
  window.addEventListener('message',async e=>{
    if(e.source!==window||e.origin!==location.origin||e.data?.source!=='zenimport-inline-history'||!account)return;
    const g=generation;
    if(e.data.error){status('Не удалось прочитать ответ банка. Повторите Search.',true);return;}
    try{const result=await rpc('inline-import',{data:e.data.data});if(g!==generation)return;state=result;signature='';render();}
    catch(error){if(g===generation){fresh=false;clear();status(error.message,true);}}
  });
  function tick(){
    const next=current();if(next!==account){account=next;generation++;fresh=false;state=null;clear();banner?.h.remove();banner=null;if(account)start();}
    if(account){if(!fresh&&!banner)status('Получаем актуальные данные ZenMoney…');render();if(fresh&&!busy&&Date.now()-refreshAt>15000){refreshAt=Date.now();const g=generation;rpc('inline-state').then(s=>{if(g===generation){const changed=JSON.stringify(state)!==JSON.stringify(s);state=s;if(changed){signature='';render();}}},e=>{if(g===generation){fresh=false;clear();status(e.message,true);}});}}
  }
  timer=setInterval(tick,1000);window.addEventListener('pagehide',()=>{clearInterval(timer);generation++;clear();banner?.h.remove();},{once:true});
  tick();
})();
