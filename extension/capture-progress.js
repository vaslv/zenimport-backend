const labels={start:'Запуск принят',tab:'Банковская вкладка найдена',network:'Network подключён',observer:'Перехват ответа установлен',waiting:'Ожидание нового запроса истории',response:'Запрос истории завершён',schema:'Схема запроса сохранена',body:'Тело ответа прочитано',parsing:'Проверка операций',ready:'Операции готовы к сохранению',saving:'Сохранение в локальную базу',saved:'Операции сохранены локально',error:'Этап завершился ошибкой',timeout:'Истекло время ожидания',download:'Скачивание схемы запущено'};
let queue=Promise.resolve();
export function progress(source,id,stage){
  if(!['Popup','Network','Worker'].includes(source)||!labels[stage])return Promise.resolve();
  queue=queue.catch(()=>{}).then(async()=>{
    const key='bankProgress'+source;
    const old=(await chrome.storage.session.get(key))[key];
    const events=old?.id===id?old.events:[];
    await chrome.storage.session.set({[key]:{id,events:[...events,{at:Date.now(),stage,label:labels[stage]}].slice(-30)}});
  });
  return queue;
}
