// Shared DOM editor in extension pages and the bank's isolated content world.
(() => {
  if(globalThis.ZenImportOperationForm)return;
  const labels={expense:'Расход',income:'Приход',refund:'Возврат покупки',transfer:'Перевод между счетами',lend:'Долг · Дать',borrow:'Долг · Взять',repay:'Долг · Вернуть',collect:'Долг · Получить возврат'};
  const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const css=`.operation-editor{margin:12px 0;padding:12px;border:1px solid #e4e0ed;border-radius:8px;color:#292735;font:14px/1.5 system-ui,sans-serif;min-width:0}.operation-editor summary{cursor:pointer;font-weight:600}.operation-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin-top:12px}.operation-editor label{display:flex;flex-direction:column;gap:5px;min-width:0;margin:0}.operation-editor input,.operation-editor select,.operation-category-trigger{font:inherit;width:100%;min-width:0;padding:8px;border:1px solid #d8d3e0;border-radius:6px;background:white;color:#292735;box-sizing:border-box}.operation-editor [hidden]{display:none!important}.operation-editor button{font:inherit;cursor:pointer}.operation-editor .operation-save{margin:12px 0 0;padding:8px 14px;border:0;border-radius:6px;background:#ef665c;color:white;text-decoration:none}.operation-editor .operation-message{margin:8px 0 0}.operation-editor .operation-category{position:relative}.operation-category-menu{position:absolute;top:0;left:0;right:0;z-index:2147483647;max-height:260px;overflow:auto;background:#fff;border:1px solid #d8d3e0;padding:6px;border-radius:6px;box-shadow:0 4px 15px #0002}.operation-category-menu input{position:sticky;top:0}.operation-category-menu button{display:block;width:100%;text-align:left;border:0;background:white;padding:8px;color:#292735;text-decoration:none}.operation-category-menu button:hover,.operation-category-menu button:focus{background:#eeeaf5}.operation-hints{grid-column:1/-1}.operation-hints button{border:0;background:transparent;color:#476fa7;text-decoration:underline;padding:0}.operation-fields .operation-note{grid-column:1/-1;margin:0;color:#777} .operation-editor button:disabled{opacity:.6;cursor:default}`;
  let active=null,serial=0;
  document.addEventListener('pointerdown',e=>{if(active&&!e.composedPath().includes(active.root))active.close();},true);
  function render(model,catalog,categories,onSave,{ranking=[],automatic=false}={}){
    if(!model?.editable)return null;
    const box=node('details');box.className='operation-editor';box.dataset.operationSource=model.source;box.dataset.operationId=model.id;
    box.open=!!model.draft;box.append(node('style',css),node('summary',`Тип операции · ${model.label||'Выберите тип'}${model.draft?' · сохранён локально':''}`));
    const fields=node('div');fields.className='operation-fields';const label=(title,input)=>{const n=node('label',title);n.append(input);fields.append(n);return n;};
    const d=model.definition||{},type=node('select');type.setAttribute('aria-label','Тип операции');
    const positive=Number(model.amount)>0;
    for(const [id,title] of Object.entries(labels)){const option=new Option(title,id);option.disabled=positive?['expense','lend','repay'].includes(id):['income','refund','borrow','collect'].includes(id);type.add(option);}type.value=d.kind|| (positive?'income':'expense');label('Тип операции',type);
    const primary=(catalog?.accounts||[]).find(a=>a.id===d.account_id),instruments=catalog?.instruments||[];
    const code=id=>instruments.find(i=>i.id===id)?.shortTitle||'';
    const users=new Map((catalog?.users||[]).map(u=>[u.id,u]));
    function root(id){const seen=new Set();while(!seen.has(id)){seen.add(id);const parent=users.get(id)?.parent;if(parent==null)return id;id=parent;}return id;}
    const other=node('select');other.setAttribute('aria-label','Второй счёт');other.add(new Option('Выберите второй счёт',''));
    for(const a of catalog?.accounts||[]){if(a.id===d.account_id||a.archive||a.deleted||a.type==='debt'||root(a.user)!==root(primary?.user))continue;other.add(new Option(`${a.title} · ${code(a.instrument)}`,a.id));}other.value=d.other_account_id||'';
    const otherLabel=label(positive?'Счёт списания':'Счёт поступления',other);
    const amount=node('input');amount.type='text';amount.inputMode='decimal';amount.setAttribute('aria-label','Сумма на втором счёте');amount.value=d.other_amount??'';amount.placeholder='Сумма в валюте второго счёта';const amountLabel=label('Сумма на втором счёте',amount);
    const person=node('input');person.setAttribute('aria-label','Человек для долга');person.value=['lend','borrow','repay','collect'].includes(d.kind)?d.payee||'':'';person.maxLength=200;
    const people=node('datalist');people.id='operation-people-'+(++serial);person.setAttribute('list',people.id);
    for(const m of catalog?.merchants||[])if(m.title)people.append(new Option(m.title,m.title));box.append(people);const personLabel=label('Человек',person);
    const categoryBox=node('div');categoryBox.className='operation-category';const categoryLabel=label('Категория',categoryBox);
    const trigger=node('button');trigger.type='button';trigger.className='operation-category-trigger';trigger.setAttribute('role','combobox');trigger.setAttribute('aria-label','Категория для типа операции');trigger.setAttribute('aria-expanded','false');
    const menu=node('div');menu.className='operation-category-menu';menu.hidden=true;const search=node('input');search.type='search';search.setAttribute('aria-label','Поиск категории типа операции');search.placeholder='Поиск категории';const options=node('div');options.setAttribute('role','listbox');menu.append(search,options);categoryBox.append(trigger,menu);
    let chosen=d.category_id||'',available=[];
    const close=()=>{menu.hidden=true;trigger.setAttribute('aria-expanded','false');if(active?.root===box)active=null;};
    function selectCategory(id,user=false){chosen=id;trigger.textContent=available.find(c=>c.id===chosen)?.title||'Выберите категорию';close();if(user&&automatic)schedule();}
    function fill(){options.replaceChildren();const filtered=available.filter(c=>c.title.toLocaleLowerCase('ru').includes(search.value.toLocaleLowerCase('ru')));for(const c of filtered){const b=node('button',c.title);b.type='button';b.setAttribute('role','option');b.onclick=e=>{if(e.isTrusted)selectCategory(c.id,true);};options.append(b);}if(!filtered.length)options.append(node('p','Категории не найдены'));}
    trigger.onclick=e=>{if(!e.isTrusted)return;if(!menu.hidden){close();return;}active?.close();active={root:box,close};search.value='';fill();menu.hidden=false;trigger.setAttribute('aria-expanded','true');search.focus();};
    search.oninput=fill;menu.onkeydown=e=>{if(e.key==='Escape'){close();trigger.focus();}else if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();const all=[search,...options.querySelectorAll('button')],focused=box.getRootNode().activeElement,i=all.indexOf(focused);all[(i+(e.key==='ArrowDown'?1:-1)+all.length)%all.length]?.focus();}};
    const hints=node('p');hints.className='operation-hints';fields.append(hints);
    const note=node('p');note.className='operation-note';note.textContent=`Счёт: ${primary?.title||'не найден'}. Сумма и дата исходной операции сохраняются.`;fields.append(note);
    const message=node('p');message.className='operation-message';message.setAttribute('role','status');
    function update(){
      const debt=['lend','borrow','repay','collect'].includes(type.value),transfer=type.value==='transfer';
      personLabel.hidden=!debt;otherLabel.hidden=!transfer;categoryLabel.hidden=debt||transfer;
      const target=(catalog?.accounts||[]).find(a=>a.id===other.value);amountLabel.hidden=!transfer||!target||target.instrument===primary?.instrument;
      if(target)amount.placeholder=`Сумма в ${code(target.instrument)} на втором счёте`;
      available=categories.filter(c=>!c.deleted&&!c.archive&&(type.value==='refund'?c.showIncome===false&&c.showOutcome===true:type.value==='expense'?c.showOutcome!==false:c.showIncome!==false)).sort((a,b)=>a.title.localeCompare(b.title,'ru'));
      const priority=ranking.filter(r=>available.some(c=>c.id===r.id));available.sort((a,b)=>priority.findIndex(r=>r.id===a.id)<0?(priority.some(r=>r.id===b.id)?1:0):priority.some(r=>r.id===b.id)?priority.findIndex(r=>r.id===a.id)-priority.findIndex(r=>r.id===b.id):-1);
      if(!available.some(c=>c.id===chosen))chosen='';selectCategory(chosen);hints.hidden=debt||transfer;hints.replaceChildren();
      if(priority.length){hints.append(document.createTextNode('Подходят: '));priority.forEach((r,i)=>{if(i)hints.append(document.createTextNode(', '));const b=node('button',available.find(c=>c.id===r.id).title);b.type='button';b.onclick=e=>{if(e.isTrusted)selectCategory(r.id,true);};hints.append(b);});}
      message.textContent=model.error||'';
    }
    type.onchange=e=>{update();if(e.isTrusted&&automatic)schedule();};other.onchange=e=>{update();if(e.isTrusted&&automatic)schedule();};
    const save=node('button','Сохранить параметры');save.type='button';save.className='operation-save';save.dataset.apiAction='';
    let pendingTimer;
    function schedule(){clearTimeout(pendingTimer);pendingTimer=setTimeout(()=>submit(),350);}
    for(const field of [amount,person])field.onchange=e=>{if(e.isTrusted&&automatic)schedule();};
    async function submit(){if(save.disabled||!box.isConnected)return;const transfer=type.value==='transfer',debt=['lend','borrow','repay','collect'].includes(type.value);const definition={kind:type.value,account_id:d.account_id,category_id:!transfer&&!debt?(chosen||null):null,other_account_id:transfer?(other.value||null):null,other_amount:transfer&&!amountLabel.hidden?(amount.value.trim().replace(',','.')||null):null,payee:debt?person.value.trim()||null:null};
      if(automatic&&((transfer&&(!definition.other_account_id||(!amountLabel.hidden&&!definition.other_amount)))||(debt&&!definition.payee))){message.textContent=transfer?'Выберите второй счёт и сумму перевода.':'Укажите человека для долга.';return;}
      save.disabled=true;for(const field of fields.querySelectorAll('input,select,button'))field.disabled=true;message.textContent=model.source==='zen'&&automatic?'Обновляем ZenMoney…':'Сохраняем…';try{await onSave({definition,expected_snapshot:model.snapshot});message.textContent=automatic&&model.source==='zen'?'Подтверждено в ZenMoney.':'Сохранено локально; ожидает подтверждения отправки.';}catch(error){message.textContent=error.message;}finally{save.disabled=false;for(const field of fields.querySelectorAll('input,select,button'))field.disabled=false;}}
    save.onclick=e=>{if(e.isTrusted)submit();};save.hidden=automatic;
    update();box.append(fields,message,save);return box;
  }
  globalThis.ZenImportOperationForm={render,labels};
})();
