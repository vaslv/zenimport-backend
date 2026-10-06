import {describeHistoryRequest} from './bank-contract.js';
import {detailKeys} from './vietinbank-json.js';
const tabId=chrome.devtools.inspectedWindow.tabId;
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if(sender.id===chrome.runtime.id&&message?.type==='devtools-status'&&message.tabId===tabId)reply({connected:true});
});
chrome.devtools.network.onRequestFinished.addListener(async request=>{
  if(['OPTIONS','HEAD'].includes(request.request.method))return;
  let url;try{url=new URL(request.request.url);}catch{return;}
  if(url.origin+url.pathname!=='https://api-ipay.vietinbank.vn/ipay/wa/getHistTransactions')return;
  const {bankCapture:state}=await chrome.storage.session.get('bankCapture');
  if(!state||state.status!=='waiting'||state.tabId!==tabId||state.expires<Date.now())return;
  // Ignore requests initiated before the operator armed this capture.
  if(!Number.isFinite(Date.parse(request.startedDateTime))||Date.parse(request.startedDateTime)<state.startedAt)return;
  if(state.diagnoseRequest) {
    const report=describeHistoryRequest(request.request);
    await chrome.storage.session.set({bankContract:{captureId:state.expires,report}});
  }
  const send=message=>chrome.runtime.sendMessage({type:'bank-response',tabId,captureId:state.expires,...message});
  try {
    if(request.response.status<200||request.response.status>=300)throw Error();
    const {content,encoding}=await new Promise((resolve,reject)=>{
      // Callback supported by older Chrome; current versions may return a Promise.
      const pending=request.getContent((content,encoding)=>resolve({content,encoding}));
      if(pending?.then)pending.then(resolve,reject);
    });
    if(typeof content!=='string'||content.length>2000000)throw Error();
    const decoded=encoding==='base64'?new TextDecoder().decode(Uint8Array.from(atob(content),c=>c.charCodeAt(0))):content;
    if(decoded.length>1500000)throw Error();
    const value=JSON.parse(decoded),data={};
    for(const key of ['error','accountNo','currentPage','nextPage','pageSize','totalRecords'])data[key]=value[key];
    if(!Array.isArray(value.transactions)||value.transactions.length>2000)throw Error();
    data.transactions=value.transactions.map(row=>Object.fromEntries(detailKeys.filter(key=>Object.hasOwn(row,key)).map(key=>[key,row[key]])));
    await send({data});
  }catch{await send({error:'network-response-unavailable'});}
});
