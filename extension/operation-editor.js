import './operation-form.js';
export function operationEditor(model,state,onAction,ranking=[]){
  return globalThis.ZenImportOperationForm.render(model,state.operation_catalog||{accounts:state.zenmoney_accounts,instruments:state.zenmoney_instruments,merchants:state.zenmoney_merchants},state.categories||[],async body=>{
    const path=model.source==='bank'?`/api/new-operations/${encodeURIComponent(model.id)}/operation`:`/api/zenmoney/transactions/${encodeURIComponent(model.id)}/operation`;
    const result=await onAction(()=>({path,method:'PUT',body}));
    if(result?.error)throw Error(result.error);
  },{ranking});
}
