// sender.url identifies the source document; a bank SPA can change routes without
// creating another document. Keep its origin trusted and validate the live route.
export const bankOrigin='https://ipay.vietinbank.vn';
export function historyAccount(url){
  try{const u=new URL(url),a=u.searchParams.get('accountNo');return u.origin===bankOrigin&&u.pathname==='/payment-history'&&/^\d{6,30}$/.test(a||'')?a:null;}catch{return null;}
}
export async function bankContext(sender,account){
  if(sender.id!==chrome.runtime.id||sender.frameId!==0||!sender.tab)throw Error('Недоступно');
  let trusted=false;try{trusted=new URL(sender.url).origin===bankOrigin;}catch{}
  if(!trusted)throw Error('Недоступно');
  const tab=await chrome.tabs.get(sender.tab.id);
  if(!account||historyAccount(tab.url)!==account)throw Error('Банковский счёт или страница изменились.');
  // Address the same document, so a response from a replacement document cannot
  // validate an old request. This probe is answered synchronously in ISOLATED.
  let page;try{page=await chrome.tabs.sendMessage(tab.id,{type:'inline-context'},{frameId:0,...(sender.documentId?{documentId:sender.documentId}:{})});}catch{throw Error('Страница банка перезагружена. Повторите действие на текущей странице.');}
  if(page?.account!==account)throw Error('Банковский счёт или страница изменились.');
  return account;
}
