// MAIN world: passively observe history responses from the exact history endpoint.
// No requests are issued, no request bodies, headers or credentials are read.
(() => {
  const inline=window.__zenImportInlineObserve===true;delete window.__zenImportInlineObserve;
  const stopKey=inline?'__zenImportStopInlineObserver':'__zenImportStopObserver';
  window[stopKey]?.();
  const continuous=inline||window.__zenImportAutoObserve===true;delete window.__zenImportAutoObserve;
  const endpoint='https://api-ipay.vietinbank.vn/ipay/wa/getHistTransactions';
  const keys=['currency','remark','amount','balance','trxId','processDate','dorC','refType','refId','tellerId','corresponsiveAccount','corresponsiveName','channel','serviceBranchId','serviceBranchName','pmtType','sendingBankId','sendingBranchId','sendingBranchName','receivingBankId','receivingBranchId','receivingBranchName'];
  const originalFetch=window.fetch, originalOpen=XMLHttpRequest.prototype.open, originalSend=XMLHttpRequest.prototype.send;
  const urls=new WeakMap(); let active=true;
  const matches=url=>{try {const u=new URL(url,location.href);return u.origin+u.pathname===endpoint;}catch{return false;}};
  const emit=data=>{if(!active)return;window.postMessage({source:inline?'zenimport-inline-history':'zenimport-history',...data},location.origin);if(!continuous)stop();};
  const receive=(value,startedAt)=>{
    if(!active)return;
    try {
      if(!value || !Array.isArray(value.transactions) || value.transactions.length>2000) throw Error();
      const clean={};
      for(const key of ['error','accountNo','currentPage','nextPage','pageSize','totalRecords']) clean[key]=value[key];
      clean.transactions=value.transactions.map(row=>Object.fromEntries(keys.filter(k=>Object.hasOwn(row,k)).map(k=>[k,row[k]])));
      if(JSON.stringify(clean).length>1500000)throw Error();
      emit({data:clean,startedAt});
    } catch {emit({startedAt,error:'Ответ истории не удалось прочитать. Повторите захват или загрузите JSON файлом.'});}
  };
  function wrappedFetch(...args) {
    const startedAt=Date.now();
    const result=originalFetch.apply(this,args);
    const input=args[0];
    if(matches(typeof input==='string'||input instanceof URL?String(input):input?.url)) {
      result.then(async response=>{if(!active)return;try {
        if(!response.ok)throw Error();
        const copy=response.clone(); const reader=copy.body.getReader(); let bytes=0;const chunks=[];
        while(true){const {value,done}=await reader.read();if(done)break;bytes+=value.byteLength;if(bytes>1500000){reader.cancel();throw Error();}chunks.push(value);}
        const joined=new Uint8Array(bytes);let offset=0;for(const chunk of chunks){joined.set(chunk,offset);offset+=chunk.length;}
        receive(JSON.parse(new TextDecoder().decode(joined)),startedAt);
      }catch{emit({startedAt,error:'Не удалось прочитать ответ истории банка.'});}},()=>emit({startedAt,error:'Запрос истории завершился ошибкой.'}));
    }
    return result;
  }
  function wrappedOpen(method,url,...rest){urls.set(this,matches(url));return originalOpen.call(this,method,url,...rest);}
  function wrappedSend(...args){
    const startedAt=Date.now();
    this.addEventListener('load',()=>{if(!active||!(urls.get(this)||matches(this.responseURL)))return;try{
      if(this.status<200||this.status>=300)throw Error();
      if(this.responseType==='json')receive(this.response,startedAt);
      else if((this.responseType===''||this.responseType==='text')&&this.responseText.length<=1500000)receive(JSON.parse(this.responseText),startedAt);
      else throw Error();
    }catch{emit({startedAt,error:'Не удалось прочитать ответ истории банка.'});}},{once:true});
    return originalSend.apply(this,args);
  }
  const stopMessage=e=>{if(e.source===window&&e.origin===location.origin&&e.data?.source==='zenimport-stop-auto')stop();};
  if(!inline)window.addEventListener('message',stopMessage);
  function stop(){window.removeEventListener('message',stopMessage);active=false;clearTimeout(timer);if(window.fetch===wrappedFetch)window.fetch=originalFetch;if(XMLHttpRequest.prototype.open===wrappedOpen)XMLHttpRequest.prototype.open=originalOpen;if(XMLHttpRequest.prototype.send===wrappedSend)XMLHttpRequest.prototype.send=originalSend;}
  const timer=inline?null:setTimeout(()=>{emit({startedAt:Date.now(),error:'Время ожидания истекло. Начните захват заново.'});stop();},continuous?1200000:600000);
  window.fetch=wrappedFetch;XMLHttpRequest.prototype.open=wrappedOpen;XMLHttpRequest.prototype.send=wrappedSend;
  window[stopKey]=stop;
})();
