// Shared DOM editor in extension pages and the bank's isolated content world.
(() => {
  if(globalThis.ZenImportOperationForm)return;
  const labels={expense:'Расход',income:'Приход',refund:'Возврат покупки',transfer:'Перевод между счетами',lend:'Долг · Дать',borrow:'Долг · Взять',repay:'Долг · Вернуть',collect:'Долг · Получить возврат'};
  const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const css=`.bank-editor .operation-types{display:flex;flex:0 0 auto;align-items:center;gap:6px;height:34px}.bank-editor .operation-type{display:flex;align-items:center;justify-content:center;width:34px;height:34px;padding:0;border:1px solid #d8d3e0;border-radius:50%;background:transparent;color:#66616f;text-decoration:none;flex:none}.bank-editor .operation-type[aria-checked=true]{background:#ef665c;border-color:#ef665c;color:white}.bank-editor .operation-type:hover:not(:disabled){border-color:#ef665c}.bank-editor .operation-type:focus-visible{outline:2px solid #476fa7;outline-offset:2px}.bank-editor .operation-type svg{width:20px;height:20px;pointer-events:none}.bank-editor .operation-type:disabled{opacity:.3}@media(max-width:600px){.bank-editor .operation-types{gap:3px}.bank-editor .operation-type{width:28px;height:28px}.bank-editor .operation-type svg{width:18px;height:18px}}.bank-editor .operation-category-trigger{text-align:left;text-decoration:none;position:relative;padding-right:28px}.bank-editor .operation-category-trigger:after{content:'▾';position:absolute;right:10px;top:50%;transform:translateY(-50%);pointer-events:none}.bank-parameters>label.bank-detail-kind{flex:0 0 170px}.bank-parameters>label.bank-other-amount{flex:0 1 200px;max-width:200px}@media(max-width:700px){.bank-parameters>label.bank-detail-kind{flex-basis:125px}.bank-parameters>label.bank-other-amount{flex-basis:135px}}.operation-editor.bank-editor{border:0;margin:0;padding:0;min-width:0;width:100%}.bank-editor .operation-fields{display:flex;align-items:flex-start;flex-wrap:nowrap;gap:8px;margin:0}.bank-editor .operation-fields>label{flex:0 0 145px}.bank-parameters{display:flex;align-items:flex-start;gap:8px;flex:1;min-width:0}.bank-parameters>label{flex:1;min-width:0}.operation-editor.bank-editor input,.operation-editor.bank-editor select,.bank-editor .operation-category-trigger{height:34px;padding:6px 10px;font-size:13px}.bank-editor .operation-hints{display:block;white-space:normal;overflow-wrap:anywhere;margin:4px 0 0;font-size:12px;line-height:1.5}.bank-editor .operation-hints button{display:inline;text-align:left}.bank-editor .operation-hints:empty{display:none}.bank-editor .operation-note{display:none}.bank-editor .operation-category-menu{font-size:13px}.bank-editor .operation-category-menu input{height:34px}@media(max-width:700px){.bank-editor .operation-fields>label{flex-basis:110px}.bank-parameters{gap:5px}.operation-editor.bank-editor input,.operation-editor.bank-editor select,.bank-editor .operation-category-trigger{padding:5px;font-size:12px}}.operation-editor{margin:12px 0;padding:12px;border:1px solid #e4e0ed;border-radius:8px;color:#292735;font:14px/1.5 system-ui,sans-serif;min-width:0}.operation-editor summary{cursor:pointer;font-weight:600}.operation-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin-top:12px}.operation-editor label{display:flex;flex-direction:column;gap:5px;min-width:0;margin:0}.operation-editor input,.operation-editor select,.operation-category-trigger{font:inherit;width:100%;min-width:0;padding:8px;border:1px solid #d8d3e0;border-radius:6px;background:white;color:#292735;box-sizing:border-box}.operation-editor [hidden]{display:none!important}.operation-editor button{font:inherit;cursor:pointer}.operation-editor .operation-save{margin:12px 0 0;padding:8px 14px;border:0;border-radius:6px;background:#ef665c;color:white;text-decoration:none}.operation-editor .operation-message{margin:8px 0 0}.operation-editor .operation-category{position:relative}.operation-category-menu{position:absolute;top:0;left:0;right:0;z-index:2147483647;max-height:260px;overflow:auto;background:#fff;border:1px solid #d8d3e0;padding:6px;border-radius:6px;box-shadow:0 4px 15px #0002}.operation-category-menu input{position:sticky;top:0}.operation-category-menu button{display:block;width:100%;text-align:left;border:0;background:white;padding:8px;color:#292735;text-decoration:none}.operation-category-menu button:hover,.operation-category-menu button:focus{background:#eeeaf5}.operation-hints{grid-column:1/-1}.operation-hints button{border:0;background:transparent;color:#476fa7;text-decoration:underline;padding:0}.operation-fields .operation-note{grid-column:1/-1;margin:0;color:#777} .operation-editor button:disabled{opacity:.6;cursor:default}`;
  let active=null,serial=0;
  document.addEventListener('pointerdown',e=>{if(active&&!e.composedPath().includes(active.root))active.close();},true);
  document.addEventListener('focusin',e=>{if(active&&!e.composedPath().includes(active.root))active.close();},true);
  window.addEventListener('resize',()=>active?.close());
  window.addEventListener('scroll',e=>{if(active?.compact&&!e.composedPath().includes(active.root))active.close();},true);
  function render(model,catalog,categories,onSave,{ranking=[],automatic=false,compact=false}={}){
    if(!model||(!model.editable&&!compact))return null;
    const box=node(compact?'div':'details');box.className='operation-editor'+(compact?' bank-editor':'');box.dataset.operationSource=model.source;box.dataset.operationId=model.id;box.dataset.operationDraft=String(!!model.draft);
    box.open=!!model.draft;box.append(node('style',css));if(!compact)box.append(node('summary',`Тип операции · ${model.label||'Выберите тип'}${model.draft?' · сохранён локально':''}`));
    const fields=node('div');fields.className='operation-fields';const parameters=node('div');parameters.className='bank-parameters';
    const label=(title,input)=>{const n=node('label',compact?'':title);n.title=title;n.append(input);(compact&&fields.children.length?parameters:fields).append(n);return n;};
    const d=model.definition||{},type=node('select');type.setAttribute('aria-label','Тип операции');
    const positive=Number(model.amount)>0;
    const mainKinds=compact?{expense:'Расход',income:'Приход',transfer:'Трансфер',debt:'Долг'}:labels;
    for(const [id,title] of Object.entries(mainKinds)){const option=new Option(title,id);option.disabled=compact?(positive?id==='expense':id==='income'):positive?['expense','lend','repay'].includes(id):['income','refund','borrow','collect'].includes(id);type.add(option);}
    type.value=compact?(['lend','borrow','repay','collect'].includes(d.kind)?'debt':d.kind==='refund'?'income':d.kind|| (positive?'income':'expense')):d.kind|| (positive?'income':'expense');
    const typeButtons=[];
    if(compact){
      const group=node('div');group.className='operation-types';group.setAttribute('role','radiogroup');group.setAttribute('aria-label','Тип операции');
      const paths={expense:'M5 12h14',income:'M5 12h14 M12 5v14',transfer:'M15 4H11a7 7 0 0 0 0 14h7 M14 14l4 4-4 4',debt:'M4 8h8V4l8 8-8 8v-4H4z'};
      for(const option of type.options){
        const b=node('button');b.type='button';b.className='operation-type';b.dataset.kind=option.value;b.title=option.text;b.setAttribute('role','radio');b.setAttribute('aria-label',option.text);
        const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),path=document.createElementNS(svg.namespaceURI,'path');
        svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.setAttribute('fill','none');svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','1.8');svg.setAttribute('stroke-linecap','round');svg.setAttribute('stroke-linejoin','round');path.setAttribute('d',paths[option.value]);svg.append(path);b.append(svg);
        const select=()=>{if(b.disabled)return;type.value=option.value;update();if(automatic)schedule();};
        b.onclick=e=>{if(e.isTrusted)select();};
        b.onkeydown=e=>{if(!e.isTrusted||!['ArrowRight','ArrowLeft','ArrowDown','ArrowUp','Home','End'].includes(e.key))return;e.preventDefault();const enabled=typeButtons.filter(b=>!b.disabled),i=enabled.indexOf(b),target=e.key==='Home'?enabled[0]:e.key==='End'?enabled.at(-1):enabled[(i+(['ArrowRight','ArrowDown'].includes(e.key)?1:-1)+enabled.length)%enabled.length];if(target){target.focus();type.value=target.dataset.kind;update();if(automatic)schedule();}};
        typeButtons.push(b);group.append(b);
      }
      fields.append(group);
    }else label('Тип операции',type);
    if(compact)fields.append(parameters);
    const debtAction=node('select');debtAction.setAttribute('aria-label','Действие с долгом');
    for(const [id,title] of Object.entries(positive?{borrow:'Взять в долг',collect:'Получить возврат'}:{lend:'Дать в долг',repay:'Вернуть долг'}))debtAction.add(new Option(title,id));
    debtAction.value=['lend','borrow','repay','collect'].includes(d.kind)?d.kind:positive?'borrow':'lend';
    const debtActionLabel=compact?label('Действие с долгом',debtAction):null;
    const incomeAction=node('select');incomeAction.setAttribute('aria-label','Вид поступления');incomeAction.add(new Option('Поступление','income'));incomeAction.add(new Option('Возврат покупки','refund'));incomeAction.value=d.kind==='refund'?'refund':'income';
    const incomeActionLabel=compact?label('Вид поступления',incomeAction):null;
    if(compact){debtActionLabel.className=incomeActionLabel.className='bank-detail-kind';}
    const kind=()=>compact?(type.value==='debt'?debtAction.value:type.value==='income'?incomeAction.value:type.value):type.value;
    const primary=(catalog?.accounts||[]).find(a=>a.id===d.account_id),instruments=catalog?.instruments||[];
    const code=id=>instruments.find(i=>i.id===id)?.shortTitle||'';
    const users=new Map((catalog?.users||[]).map(u=>[u.id,u]));
    function root(id){const seen=new Set();while(!seen.has(id)){seen.add(id);const parent=users.get(id)?.parent;if(parent==null)return id;id=parent;}return id;}
    const other=node('select');other.setAttribute('aria-label','Второй счёт');other.add(new Option(compact?(positive?'Со счёта…':'На счёт…'):'Выберите второй счёт',''));
    for(const a of catalog?.accounts||[]){if(a.id===d.account_id||a.archive||a.deleted||a.type==='debt'||root(a.user)!==root(primary?.user))continue;other.add(new Option(`${a.title} · ${code(a.instrument)}`,a.id));}other.value=d.other_account_id||'';
    const otherLabel=label(positive?'Счёт списания':'Счёт поступления',other);
    const amount=node('input');amount.type='text';amount.inputMode='decimal';amount.setAttribute('aria-label','Сумма на втором счёте');amount.value=d.other_amount??'';amount.placeholder='Сумма в валюте второго счёта';const amountLabel=label('Сумма на втором счёте',amount);if(compact)amountLabel.className='bank-other-amount';
    const person=node('input');person.setAttribute('aria-label','Человек для долга');person.value=['lend','borrow','repay','collect'].includes(d.kind)?d.payee||'':'';person.maxLength=200;
    const people=node('datalist');people.id='operation-people-'+(++serial);person.setAttribute('list',people.id);
    for(const m of catalog?.merchants||[])if(m.title)people.append(new Option(m.title,m.title));box.append(people);const personLabel=label('Человек',person);
    const categoryBox=node('div');categoryBox.className='operation-category';const categoryLabel=label('Категория',categoryBox);
    const trigger=node('button');trigger.type='button';trigger.className='operation-category-trigger';trigger.setAttribute('role','combobox');trigger.setAttribute('aria-label','Категория для типа операции');trigger.setAttribute('aria-expanded','false');
    const menu=node('div');menu.className='operation-category-menu';menu.hidden=true;const search=node('input');search.type='search';search.setAttribute('aria-label','Поиск категории типа операции');search.placeholder='Поиск категории';const options=node('div');options.setAttribute('role','listbox');menu.append(search,options);categoryBox.append(trigger,menu);
    let chosen=d.category_id||'',available=[];
    const close=()=>{menu.hidden=true;trigger.setAttribute('aria-expanded','false');if(active?.root===box)active=null;};
    function selectCategory(id,user=false){chosen=id;trigger.textContent=available.find(c=>c.id===chosen)?.title||'Выберите категорию';close();if(user&&automatic)schedule();}
    function fill(){options.replaceChildren();const filtered=available.filter(c=>c.title.toLocaleLowerCase('ru').includes(search.value.toLocaleLowerCase('ru')));for(const c of filtered){const b=node('button',c.title);b.type='button';if(ranking.some(r=>r.id===c.id))b.style.fontWeight='600';b.setAttribute('role','option');b.onclick=e=>{if(e.isTrusted)selectCategory(c.id,true);};options.append(b);}if(!filtered.length)options.append(node('p','Категории не найдены'));}
    trigger.onclick=e=>{if(!e.isTrusted)return;if(!menu.hidden){close();return;}active?.close();active={root:box,close,compact};search.value='';fill();menu.hidden=false;if(compact){const r=trigger.getBoundingClientRect();menu.style.cssText=`position:fixed;left:${Math.max(8,Math.min(r.left,innerWidth-r.width-8))}px;top:${Math.max(8,Math.min(r.top,innerHeight-180))}px;width:${Math.min(r.width,innerWidth-16)}px;max-height:${Math.max(100,Math.min(260,innerHeight-Math.max(8,Math.min(r.top,innerHeight-180))-8))}px;right:auto;`;}trigger.setAttribute('aria-expanded','true');search.focus();};
    search.oninput=fill;menu.onkeydown=e=>{if(e.key==='Escape'){close();trigger.focus();}else if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();const all=[search,...options.querySelectorAll('button')],focused=box.getRootNode().activeElement,i=all.indexOf(focused);all[(i+(e.key==='ArrowDown'?1:-1)+all.length)%all.length]?.focus();}};
    const hints=node('p');hints.className='operation-hints';if(compact)categoryLabel.append(hints);else fields.append(hints);
    const note=node('p');note.className='operation-note';note.hidden=compact;note.textContent=`Счёт: ${primary?.title||'не найден'}. Сумма и дата исходной операции сохраняются.`;fields.append(note);
    const message=node('p');message.className='operation-message';message.hidden=compact;message.setAttribute('role','status');
    function updateTypes(){for(const b of typeButtons){b.setAttribute('aria-checked',String(b.dataset.kind===type.value));b.tabIndex=b.dataset.kind===type.value?0:-1;b.disabled=!model.editable||[...type.options].find(o=>o.value===b.dataset.kind).disabled;}}
    function update(){
      updateTypes();
      const debt=['lend','borrow','repay','collect'].includes(kind()),transfer=kind()==='transfer';
      if(compact){debtActionLabel.hidden=!debt;incomeActionLabel.hidden=type.value!=='income';}
      person.placeholder=positive?'У кого':'Кому';personLabel.hidden=!debt;otherLabel.hidden=!transfer;categoryLabel.hidden=debt||transfer;
      const target=(catalog?.accounts||[]).find(a=>a.id===other.value);amountLabel.hidden=!transfer||!target||target.instrument===primary?.instrument;
      if(target)amount.placeholder=`Сумма в ${code(target.instrument)} на втором счёте`;
      available=categories.filter(c=>!c.deleted&&!c.archive&&(kind()==='refund'?c.showIncome===false&&c.showOutcome===true:kind()==='expense'?c.showOutcome!==false:c.showIncome!==false)).sort((a,b)=>a.title.localeCompare(b.title,'ru'));
      const priority=ranking.filter(r=>available.some(c=>c.id===r.id)).sort((a,b)=>b.count-a.count);available.sort((a,b)=>priority.findIndex(r=>r.id===a.id)<0?(priority.some(r=>r.id===b.id)?1:0):priority.some(r=>r.id===b.id)?priority.findIndex(r=>r.id===a.id)-priority.findIndex(r=>r.id===b.id):-1);
      if(!available.some(c=>c.id===chosen))chosen='';selectCategory(chosen);hints.hidden=debt||transfer;hints.replaceChildren();
      if(priority.length){if(!compact)hints.append(document.createTextNode('Подходят: '));priority.forEach((r,i)=>{if(i)hints.append(document.createTextNode(', '));const b=node('button',available.find(c=>c.id===r.id).title);b.type='button';b.onclick=e=>{if(e.isTrusted)selectCategory(r.id,true);};hints.append(b);});}
      message.textContent=model.error||'';box.title=model.error||'';
    }
    type.onchange=e=>{update();if(e.isTrusted&&automatic)schedule();};other.onchange=e=>{update();if(e.isTrusted&&automatic)schedule();};debtAction.onchange=incomeAction.onchange=e=>{update();if(e.isTrusted&&automatic)schedule();};
    const save=node('button','Сохранить параметры');save.type='button';save.className='operation-save';save.dataset.apiAction='';
    let pendingTimer;
    function schedule(){clearTimeout(pendingTimer);pendingTimer=setTimeout(()=>submit(),350);}
    for(const field of [amount,person])field.onchange=e=>{if(e.isTrusted&&automatic)schedule();};
    async function submit(){if(!model.editable||save.disabled||!box.isConnected)return;const transfer=kind()==='transfer',debt=['lend','borrow','repay','collect'].includes(kind());const definition={kind:kind(),account_id:d.account_id,category_id:!transfer&&!debt?(chosen||null):null,other_account_id:transfer?(other.value||null):null,other_amount:transfer&&!amountLabel.hidden?(amount.value.trim().replace(',','.')||null):null,payee:debt?person.value.trim()||null:null};
      if(automatic&&((transfer&&(!definition.other_account_id||(!amountLabel.hidden&&!definition.other_amount)))||(debt&&!definition.payee))){message.textContent=transfer?'Выберите второй счёт и сумму перевода.':'Укажите человека для долга.';return;}
      save.disabled=true;for(const field of fields.querySelectorAll('input,select,button'))field.disabled=true;message.textContent=model.source==='zen'&&automatic?'Обновляем ZenMoney…':'Сохраняем…';try{await onSave({definition,expected_snapshot:model.snapshot});message.textContent=automatic&&model.source==='zen'?'Подтверждено в ZenMoney.':'Сохранено локально; ожидает подтверждения отправки.';}catch(error){message.textContent=error.message;}finally{save.disabled=false;for(const field of fields.querySelectorAll('input,select,button'))field.disabled=false;updateTypes();}}
    save.onclick=e=>{if(e.isTrusted)submit();};save.hidden=automatic;
    update();box.append(fields,message,save);if(!model.editable)for(const field of fields.querySelectorAll('input,select,button'))field.disabled=true;return box;
  }
  globalThis.ZenImportOperationForm={render,labels};
})();
