const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
let activePicker;
export function destroyCategoryPickers(container){for(const select of container.querySelectorAll('.category-picker select'))select.tomselect?.destroy();}
export function categoryPicker(row,state,onAction){
  const box=el('section',undefined,'category-picker');box.dataset.zenId=row.id;
  const categories=(state.categories||[]).filter(c=>!c.deleted),byId=new Map(categories.map(c=>[c.id,c]));
  const compare=(a,b)=>a.title.localeCompare(b.title,'ru',{sensitivity:'base'})||a.id.localeCompare(b.id);
  const ranking=(row.category_ranking||row.category_suggestion?.distribution||[]).filter(c=>byId.has(c.id)&&c.count>0).map(c=>({...c,title:byId.get(c.id).title})).sort((a,b)=>b.count-a.count||compare(a,b));
  const priority=new Set(ranking.map(c=>c.id)),others=categories.filter(c=>!priority.has(c.id)).sort(compare);
  const saved=row.category_draft?.id||'';box.value=row.category_draft?.valid===false?'':saved;
  const select=el('select'),message=el('p',undefined,'muted');
  select.setAttribute('aria-label','Категория операции ZenMoney');
  const options=[...ranking.map(c=>({...c,priority:true})),...others,{id:'',title:'Без выбранной категории'}];
  const update=()=>{message.textContent=box.value!==saved?'Выбор ещё не сохранён.':box.value?'Категория сохранена локально; ожидает отправки в ZenMoney.':'Выберите категорию и сохраните её локально.';};
  box.append(select);
  const picker=new TomSelect(select,{
    plugins:['dropdown_input'],maxItems:1,maxOptions:null,create:false,allowEmptyOption:true,
    valueField:'id',labelField:'title',searchField:['title'],sortField:[{field:'$order'},{field:'$score'}],
    options,items:box.value?[box.value]:[],placeholder:'Поиск по категориям',closeAfterSelect:true,
    onDropdownOpen(){if(activePicker&&activePicker!==this)activePicker.close();activePicker=this;},
    onDestroy(){if(activePicker===this)activePicker=null;},
    onChange(value){box.value=value;update();},
    render:{
      option(data,escape){return `<div class="category-option" data-category-id="${escape(data.id)}">${data.priority?'<strong>':'<span>'}${escape(data.title)}${data.priority?'</strong>':'</span>'}${data.priority?`<small>В истории: ${data.count}${data.count===1?' · один пример':' · повторяется'}</small>`:''}</div>`;},
      item(data,escape){return `<div>${escape(data.title)}</div>`;},
      no_results(){return '<div class="no-results">Категории не найдены</div>';}
    }
  });
  picker.control_input.setAttribute('aria-label','Поиск по категориям');
  const save=el('button','Сохранить категорию');save.type='button';save.dataset.apiAction='';
  save.onclick=()=>{if(box.value===saved&&row.category_draft?.valid!==false)return;onAction(()=>({path:`/api/zenmoney/transactions/${encodeURIComponent(row.id)}/category`,method:'PUT',body:{category_id:box.value||null,expected_tags:row.tag_ids||[]}}));};
  const evidence=el('p',undefined,'category-priorities');
  if(ranking.length){
    evidence.append(document.createTextNode('Предполагаемые категории: '));
    ranking.forEach((c,i)=>{if(i)evidence.append(document.createTextNode('; '));evidence.append(el('strong',`${c.title} · ${c.count}`));});
    evidence.append(document.createTextNode(ranking.length>1?' · несколько вариантов — проверьте выбор.':ranking[0].count>=2?' · повторяется в истории.':' · только один пример — проверьте выбор.'));
  }else evidence.textContent='Нет подходящих подтверждённых примеров для подсказки категории.';
  box.prepend(el('h3','Категория операции ZenMoney'),evidence);box.append(message,save);
  if(row.category_draft?.valid===false)box.append(el('p','Категория недоступна или данные ZenMoney изменились. Проверьте выбор заново.','notice'));
  if(row.candidates?.length)box.append(el('p','Есть возможные совпадения с ZenMoney. Выбор категории не подтверждает создание новой операции.','muted'));
  update();return box;
}
