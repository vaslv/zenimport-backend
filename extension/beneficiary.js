export function beneficiaryLabel(row){
  const details=row.raw?.details||{};
  const account=String(details.corresponsiveAccount||'').trim();
  if(!account)return '';
  const name=String(details.corresponsiveName||'').trim();
  return `Beneficiary account: ${account}${name&&name!==account?' · '+name:''}`;
}
