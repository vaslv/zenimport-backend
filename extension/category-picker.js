const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
export function categoryPicker(row,state,onAction){
  const box=el('section',undefined,'category-picker');box.dataset.zenId=row.id;
  const categories=(state.categories||[]).filter(c=>!c.deleted),byId=new Map(categories.map(c=>[c.id,c]));
  const compare=(a,b)=>a.title.localeCompare(b.title,'ru',{sensitivity:'base'})||a.id.localeCompare(b.id);
  const ranking=(row.category_ranking||row.category_suggestion?.distribution||[]).filter(c=>byId.has(c.id)&&c.count>0).map(c=>({...c,title:byId.get(c.id).title})).sort((a,b)=>b.count-a.count||compare(a,b));
  const priority=new Set(ranking.map(c=>c.id)),others=categories.filter(c=>!priority.has(c.id)).sort(compare);
  const saved=row.category_draft?.id||'';box.value=row.category_draft?.valid===false?'':saved;
  const menu=el('details',undefined,'category-menu'),toggle=el('summary');
  const list=el('div',undefined,'category-options'),message=el('p',undefined,'muted');
  const update=()=>{toggle.textContent=`Категория: ${byId.get(box.value)?.title||'Выберите категорию'}`;message.textContent=box.value!==saved?'Выбор ещё не сохранён.':box.value?'Категория сохранена локально; ещё не отправлена в ZenMoney.':'Выберите категорию и сохраните её локально.';for(const b of list.querySelectorAll('button'))b.setAttribute('aria-pressed',String(b.dataset.categoryId===box.value));};
  const option=(category,ranked=false)=>{
    const button=el('button',undefined,'category-option');button.type='button';button.dataset.categoryId=category.id;
    button.append(el(ranked?'strong':'span',category.title));
    if(ranked)button.append(el('small',`В истории: ${category.count}${category.count===1?' · один пример':' · повторяется'}`));
    button.onclick=()=>{box.value=category.id;update();menu.open=false;toggle.focus();};list.append(button);
  };
  if(ranking.length){list.append(el('p','Чаще у этого получателя','category-option-heading'));ranking.forEach(c=>option(c,true));}
  list.append(el('p',ranking.length?'Остальные категории':'Все категории','category-option-heading'));others.forEach(c=>option(c));
  option({id:'',title:'Без выбранной категории'});
  menu.append(toggle,list);menu.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();menu.open=false;toggle.focus();}});
  const save=el('button','Сохранить категорию');save.type='button';save.dataset.apiAction='';
  save.onclick=()=>{if(box.value===saved&&row.category_draft?.valid!==false)return;onAction(()=>({path:`/api/zenmoney/transactions/${encodeURIComponent(row.id)}/category`,method:'PUT',body:{category_id:box.value||null,expected_tags:row.tag_ids||[]}}));};
  box.append(el('h3','Категория операции ZenMoney'),menu,message,save);
  if(row.category_draft?.valid===false)box.append(el('p','Категория недоступна или данные ZenMoney изменились. Проверьте выбор заново.','notice'));
  if(row.candidates?.length)box.append(el('p','Есть возможные совпадения с ZenMoney. Выбор категории не подтверждает создание новой операции.','muted'));
  update();return box;
}
