// Shared validation and redacted format description; never return the value.
export function moneyIssue(value){
  if(typeof value!=='string')return 'type';
  if(!/^\d{1,13}$/.test(value))return 'format';
  if(BigInt(value)>9000000000000n)return 'limit';
  return null;
}
export function describeMoney(value){
  const type=value===null?'null':Array.isArray(value)?'array':typeof value;
  const result={type,issue:moneyIssue(value)};
  if(typeof value!=='string')return result;
  result.length=value.length;
  result.shape=value.slice(0,80).replace(/[0-9]/g,'#').replace(/[^#.,+\-()\s]/g,'?').replace(/\s/g,' ');
  const decimal=/^[+-]?\d+[.,](\d+)$/.exec(value);
  if(decimal)result.fractionAllZero=/^0+$/.test(decimal[1]);
  return result;
}
