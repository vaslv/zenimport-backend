// Suggested calendar dates only: the bank's filters remain the source of truth.
export function historyPeriod(months, today=new Date()) {
  if(![1,6].includes(months))throw new Error('Unsupported history period');
  const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Ho_Chi_Minh',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(today);
  const value=type=>Number(parts.find(p=>p.type===type).value);
  const end=new Date(Date.UTC(value('year'),value('month')-1,value('day')));
  const start=new Date(Date.UTC(value('year'),value('month')-1-months,1));
  const last=new Date(Date.UTC(start.getUTCFullYear(),start.getUTCMonth()+1,0)).getUTCDate();
  start.setUTCDate(Math.min(value('day'),last));
  const format=d=>`${String(d.getUTCDate()).padStart(2,'0')}.${String(d.getUTCMonth()+1).padStart(2,'0')}.${d.getUTCFullYear()}`;
  return `${format(start)} — ${format(end)}`;
}
