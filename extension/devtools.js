import {progress} from './capture-progress.js';
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
  const {autoJob:job}=await chrome.storage.local.get('autoJob');
  const automatic=job&&['running','saving'].includes(job.status)&&job.tabId===tabId;
  const {bankCapture:capture}=await chrome.storage.session.get('bankCapture');
  const state=automatic?{status:'waiting',tabId,startedAt:job.startedAt,expires:job.startedAt+1200000}:capture;
  if(!state||state.status!=='waiting'||state.tabId!==tabId||state.expires<Date.now())return;
  // Ignore requests initiated before the operator armed this capture.
  if(!Number.isFinite(Date.parse(request.startedDateTime))||Date.parse(request.startedDateTime)<state.startedAt)return;
  const send=message=>chrome.runtime.sendMessage(automatic?{type:'auto-bank-response',tabId,startedAt:Date.parse(request.startedDateTime),...message}:{type:'bank-response',tabId,captureId:state.expires,...message});
  try {
    await progress('Network',state.expires,'response');
    if(state.diagnoseRequest) {
      const report=describeHistoryRequest(request.request);
      await chrome.storage.session.set({bankContract:{captureId:state.expires,report}});
      await progress('Network',state.expires,'schema');
    }
    if(request.response.status<200||request.response.status>=300)throw Error();
    const {content,encoding}=await new Promise((resolve,reject)=>{
      // Callback supported by older Chrome; current versions may return a Promise.
      const timer=setTimeout(()=>reject(Error('Response timeout')),15000);
      const done=value=>{clearTimeout(timer);resolve(value);};
      const failed=error=>{clearTimeout(timer);reject(error);};
      try {
        const pending=request.getContent((content,encoding)=>done({content,encoding}));
        if(pending?.then)pending.then(done,failed);
      }catch(error){failed(error);}
    });
    if(typeof content!=='string'||content.length>2000000)throw Error();
    const decoded=encoding==='base64'?new TextDecoder().decode(Uint8Array.from(atob(content),c=>c.charCodeAt(0))):content;
    if(decoded.length>1500000)throw Error();
    await progress('Network',state.expires,'body');
    const value=JSON.parse(decoded),data={};
    for(const key of ['error','accountNo','currentPage','nextPage','pageSize','totalRecords'])data[key]=value[key];
    if(!Array.isArray(value.transactions)||value.transactions.length>2000)throw Error();
    data.transactions=value.transactions.map(row=>Object.fromEntries(detailKeys.filter(key=>Object.hasOwn(row,key)).map(key=>[key,row[key]])));
    await send({data});
  }catch{await progress('Network',state.expires,'error');await send({error:'network-response-unavailable'});}
});
