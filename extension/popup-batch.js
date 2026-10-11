import {api} from './api.js';
const button=document.getElementById('batch-open'),status=document.getElementById('batch-count');
async function scope(){
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});const url=new URL(tab?.url||'about:blank');
  if(url.origin!=='https://ipay.vietinbank.vn'||url.pathname!=='/payment-history')throw Error('Откройте историю нужного счёта VietinBank.');
  const state=await api('/api/state'),account=state.bank_accounts.find(a=>a.bank==='vietinbank'&&a.account===url.searchParams.get('accountNo'));
  if(!account?.zenmoney_account)throw Error('Сначала свяжите банковский счёт с ZenMoney в приложении.');
  return {state,account};
}
button.onclick=async()=>{button.disabled=true;try{const {account}=await scope();await chrome.tabs.create({url:chrome.runtime.getURL('batch.html')+'?'+new URLSearchParams({bank:'vietinbank',account:account.id})});}catch(e){status.textContent=e.message;}finally{button.disabled=false;}};
scope().then(({state,account})=>{
  const rows=state.review.filter(r=>r.bank_account===account.id),newCount=rows.filter(r=>r.state==='pending'&&r.creation_draft?.valid||r.creation_job?.state==='prepared').length;
  const categories=new Set(state.day_review.filter(d=>d.bank_account===account.id).flatMap(d=>d.zen_rows||[]).filter(z=>z.category_draft?.valid).map(z=>z.id));
  status.textContent=`Выбраны категории новых операций: ${newCount}. Изменения категорий: ${categories.size}. Перед отправкой проверим соответствия.`;
}).catch(e=>{status.textContent=e.message;});
