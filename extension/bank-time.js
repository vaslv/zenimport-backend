// Preserve the bank's wall-clock time; do not convert it to the browser timezone.
export function bankTime(row){
  for(const value of [row.raw?.date,row.raw?.details?.processDate]){
    if(typeof value!=='string')continue;
    const match=/^(?:(\d{4})-(\d{2})-(\d{2})|(\d{2})[/-](\d{2})[/-](\d{4}))[ T](\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value.trim());
    if(!match)continue;
    const date=match[1]?`${match[1]}-${match[2]}-${match[3]}`:`${match[6]}-${match[5]}-${match[4]}`;
    if(date!==row.date||Number(match[7])>23||Number(match[8])>59||Number(match[9]||0)>59)continue;
    return `${match[7]}:${match[8]}${match[9]?':'+match[9]:''}`;
  }
  return '';
}
