// Calendar dates stay in UTC so their day never changes with the browser timezone.
const formatter=new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'long',year:'numeric',timeZone:'UTC'});
export function dateLabel(value){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(value||''))return 'Дата не указана';
  const date=new Date(value+'T00:00:00Z');
  if(!Number.isFinite(date.getTime())||date.toISOString().slice(0,10)!==value)return 'Дата не указана';
  const parts=Object.fromEntries(formatter.formatToParts(date).map(p=>[p.type,p.value]));
  return `${parts.day} ${parts.month} ${parts.year}`;
}
