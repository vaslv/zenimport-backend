import {detailKeys} from './vietinbank-json.js';
const kind=v=>v===undefined?'missing':v===null?'null':Array.isArray(v)?'array':typeof v;
// Only known counters may retain values. No arbitrary bank strings enter a report.
const counter=v=>({type:kind(v),...(Number.isSafeInteger(v)||typeof v==='boolean'||v===null?{value:v}:typeof v==='string'&&/^-?\d{1,12}$/.test(v)?{value:v}:{})});
export function describeResponse(data,job){
  const rows=Array.isArray(data?.transactions)?data.transactions:[];
  const report={at:Date.now(),part:job.part,expected:{page:job.current?.page??0,total:job.current?.total??null,rowsSaved:job.current?.rows??0,period:job.parts[job.part]},
    counters:Object.fromEntries(['currentPage','nextPage','pageSize','totalRecords','error'].map(k=>[k,counter(data?.[k])])),
    accountMatches:typeof data?.accountNo==='string'&&data.accountNo===job.account,
    transactionsType:kind(data?.transactions),rowCount:rows.length,fields:{}};
  const dates=[],ids=new Set();let repeatedIds=0,emptyIds=0,invalidDates=0;
  for(const row of rows.slice(0,2000)){
    for(const key of detailKeys){const types=report.fields[key]??=[];const t=kind(row?.[key]);if(!types.includes(t))types.push(t);}
    const id=row?.trxId;if(typeof id!=='string'||!id.trim())emptyIds++;else{if(ids.has(id.trim()))repeatedIds++;ids.add(id.trim());}
    const m=typeof row?.processDate==='string'&&/^(\d{2})-(\d{2})-(\d{4}) \d{2}:\d{2}:\d{2}$/.exec(row.processDate);
    if(m)dates.push(`${m[3]}-${m[2]}-${m[1]}`);else invalidDates++;
  }
  dates.sort();report.dates={first:dates[0]??null,last:dates.at(-1)??null,invalid:invalidDates};
  report.identifiers={unique:ids.size,repeated:repeatedIds,empty:emptyIds};
  return report;
}
