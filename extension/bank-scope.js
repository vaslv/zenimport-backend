// Reconciliation scope is defined by imported bank accounts and their explicit mappings.
export const bankTitle=bank=>bank==='vietinbank'?'VietinBank':bank==='demo'?'Демобанк':bank;
export function bankScope(state,bank){
  const accounts=state.bank_accounts.filter(a=>a.bank===bank),ids=new Set(accounts.map(a=>a.id));
  const zenIds=new Set(accounts.map(a=>a.zenmoney_account).filter(Boolean));
  const days=(state.day_review||[]).filter(d=>ids.has(d.bank_account));
  const txIds=new Set(days.flatMap(d=>d.zen_rows||[]).map(z=>z.id));
  const review=state.review.filter(r=>ids.has(r.bank_account)),bankIds=new Set(review.map(r=>r.id));
  return {...state,bank_accounts:accounts,zenmoney_accounts:state.zenmoney_accounts.filter(a=>zenIds.has(a.id)),
    review,day_review:days,category_sends:(state.category_sends||[]).filter(s=>txIds.has(s.zen_id)),
    recipient_profiles:(state.recipient_profiles||[]).filter(p=>ids.has(p.bank_account)),
    outbox:state.outbox.filter(o=>bankIds.has(o.bank_transaction))};
}
