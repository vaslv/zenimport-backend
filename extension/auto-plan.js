export function planHistory(months,today=new Date()){
  if(![1,6].includes(months))throw Error('Выберите месяц или шесть месяцев.');
  const p=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Ho_Chi_Minh',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(today);
  const n=k=>Number(p.find(x=>x.type===k).value),end=new Date(Date.UTC(n('year'),n('month')-1,n('day')));
  const start=new Date(Date.UTC(n('year'),n('month')-1-months,1));start.setUTCDate(Math.min(n('day'),new Date(Date.UTC(start.getUTCFullYear(),start.getUTCMonth()+1,0)).getUTCDate()));
  const iso=d=>d.toISOString().slice(0,10),parts=[];
  for(let a=new Date(start);a<=end;){const b=new Date(Math.min(+end,+a+29*86400000));parts.push({from:iso(a),to:iso(b)});a=new Date(+b+86400000);}
  return {from:iso(start),to:iso(end),parts};
}
export const activeJob=j=>j&&['starting','navigating','running','saving'].includes(j.status);
export const jobLabels={start:'Загрузка запущена',navigation:'Открытие истории',attached:'Загрузчик подключён',click:'Действие на странице',filter:'Период установлен',search:'Поиск истории',response:'Ответ истории получен',save:'Страница сохранена',segment:'Интервал полностью загружен',done:'Весь период загружен',error:'Загрузка остановлена с ошибкой',cancel:'Загрузка отменена',wait:'Ожидание страницы',wait_done:'Ожидание завершено',network:'Запрос банка завершён'};
export function publicJob(job){if(!job)return null;const {account,seen,url,tabId,...rest}=job;return rest;}
