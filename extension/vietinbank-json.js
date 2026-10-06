// Bank data is untrusted. Only transaction fields enter the normalized payload.
export const detailKeys = ['currency','remark','amount','balance','trxId','processDate','dorC','refType','refId','tellerId','corresponsiveAccount','corresponsiveName','channel','serviceBranchId','serviceBranchName','pmtType','sendingBankId','sendingBranchId','sendingBranchName','receivingBankId','receivingBranchId','receivingBranchName'];
export async function parseVietinBankJson(data) {
  const fail = message => { throw new Error(message); };
  if (!data || data.error !== false) fail('Банк вернул ошибку или неизвестный формат ответа.');
  if (typeof data.accountNo !== 'string' || !/^\d{6,30}$/.test(data.accountNo)) fail('Не удалось определить счёт.');
  const {currentPage:page,pageSize:size,totalRecords:total,transactions:rows} = data;
  if (![page,size,total].every(Number.isSafeInteger) || page<0 || size<1 || size>2000 || total<1 || total>100000 || !Array.isArray(rows)) fail('Некорректные счётчики истории.');
  const start=page*size+1, end=start+rows.length-1;
  if (start>total || rows.length!==Math.min(size,total-start+1)) fail('Ответ неполный: число операций не совпадает со счётчиками банка.');
  const text=(v,max=2000)=>{if(typeof v!=='string'||v.length>max)fail('Некорректное текстовое поле операции.');return v;};
  const money=v=>{if(typeof v!=='string'||!/^\d{1,13}$/.test(v)||BigInt(v)>9000000000000n)fail('Неподдерживаемая сумма VND.');return Number(v);};
  const transactions=[], seen=new Set();
  for (const row of rows) {
    if (!row || row.currency!=='VND' || !['D','C'].includes(row.dorC)) fail('Неизвестная валюта или направление операции.');
    const match=/^(\d{2})-(\d{2})-(\d{4}) (\d{2}):(\d{2}):(\d{2})$/.exec(text(row.processDate,100));
    if (!match) fail('Неизвестный формат даты операции.');
    const [,dd,mm,yyyy,hh,min,ss]=match, date=`${yyyy}-${mm}-${dd}`;
    if (+yyyy<1900 || +hh>23 || +min>59 || +ss>59 || Number.isNaN(Date.parse(date)) || new Date(date).toISOString().slice(0,10)!==date) fail('Некорректная дата операции.');
    const value=money(row.amount)*(row.dorC==='D'?-1:1), balance=money(row.balance);
    if (!value) fail('Нулевая операция не поддерживается.');
    const description=text(row.remark), rawDate=`${dd}/${mm}/${yyyy} ${hh}:${min}:${ss}`;
    const bankId=text(row.trxId,160).trim();
    let id=bankId?`vib:id:${bankId}`:null;
    if (!id) {
      const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify([data.accountNo,rawDate,value,balance,description.replace(/\s+/g,' ').trim()])));
      id='vib:f1:'+Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('');
    }
    if(seen.has(id)) fail('В ответе повторяется идентификатор операции; нужна сверка.');
    seen.add(id);
    const details={};
    for(const key of detailKeys) if(Object.hasOwn(row,key)) details[key]=row[key]===null?null:text(row[key]);
    transactions.push({external_id:id,date,amount_minor:value,description,raw:{reference:id,reference_kind:bankId?'bank':'fingerprint',date:rawDate,amount:row.amount,balance:row.balance,description,details}});
  }
  return {bank:'vietinbank',adapter_version:'2.0.0-json',account:data.accountNo,currency:'VND',capture:{start_date:null,end_date:null,row_start:start,row_end:end,total_rows:total,pages:1,complete:start===1&&end===total},transactions};
}
