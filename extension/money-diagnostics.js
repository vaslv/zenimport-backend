// Shared validation and redacted format description; never return the value.
export function moneyIssue(value,allowNegative=false){
  if(typeof value!=='string')return 'type';
  if(!(allowNegative?/^-?\d{1,13}$/:/^\d{1,13}$/).test(value))return 'format';
  if(BigInt(value)>9000000000000n||BigInt(value)<-9000000000000n)return 'limit';
  return null;
}
export function describeMoney(value,allowNegative=false){
  const type=value===null?'null':Array.isArray(value)?'array':typeof value;
  const result={type,issue:moneyIssue(value,allowNegative)};
  if(typeof value!=='string')return result;
  result.length=value.length;
  result.shape=value.slice(0,80).replace(/[0-9]/g,'#').replace(/[^#.,+\-()\s]/g,'?').replace(/\s/g,' ');
  const decimal=/^[+-]?\d+[.,](\d+)$/.exec(value);
  if(decimal)result.fractionAllZero=/^0+$/.test(decimal[1]);
  return result;
}

// A negative debit/credit magnitude is accepted only when an older neighbour
// independently confirms its net effect. Do not guess across page boundaries.
export function negativeAmountIssue(row,older){
  if(!older)return 'missing_neighbor';
  if(moneyIssue(row?.amount,true)||moneyIssue(row?.balance)||moneyIssue(older.balance)||row.currency!=='VND'||older.currency!=='VND'||!['D','C'].includes(row.dorC))return 'invalid_neighbor';
  const sortable=s=>typeof s==='string'&&/^(\d{2})-(\d{2})-(\d{4}) (\d{2}:\d{2}:\d{2})$/.test(s)?s.replace(/^(\d{2})-(\d{2})-(\d{4}) (.*)$/,'$3-$2-$1 $4'):null;
  const current=sortable(row.processDate),previous=sortable(older.processDate);
  if(!current||!previous||previous>=current)return 'chronology';
  const net=BigInt(row.amount)*(row.dorC==='D'?-1n:1n);
  return BigInt(row.balance)-BigInt(older.balance)===net?null:'balance_mismatch';
}
