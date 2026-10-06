(() => {
  globalThis.__zenImportRemoveBridge?.();
  const listener=event=>{
    if(event.source!==window||event.origin!=='https://ipay.vietinbank.vn'||event.data?.source!=='zenimport-history')return;
    chrome.runtime.sendMessage({type:'bank-response',data:event.data.data,error:event.data.error}).catch(()=>{});
  };
  window.addEventListener('message',listener);
  const remove=()=>{window.removeEventListener('message',listener);clearTimeout(timer);};
  const timer=setTimeout(remove,610000);
  globalThis.__zenImportRemoveBridge=remove;
})();
