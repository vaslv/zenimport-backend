import {parseVietinBankJson} from './vietinbank-json.js';
chrome.storage.local.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'});
chrome.storage.session.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'});
chrome.runtime.onInstalled.addListener(()=>chrome.storage.local.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'}));
let processing=false;
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if(message?.type!=='bank-response')return;
  if(sender.id!==chrome.runtime.id)return;
  const devtools=sender.url===chrome.runtime.getURL('devtools.html');
  if(!devtools&&(sender.frameId!==0||!sender.tab||!sender.url?.startsWith('https://ipay.vietinbank.vn/')))return;
  const tabId=devtools?message.tabId:sender.tab.id;
  if(processing)return;
  processing=true;
  (async()=>{
    const {bankCapture:state}=await chrome.storage.session.get('bankCapture');
    if(!state||state.status!=='waiting'||state.tabId!==tabId||state.expires<Date.now())return;
    if(devtools&&message.captureId!==state.expires)return;
    try {
      if(message.error)throw Error('Ответ истории не получен. Начните захват заново.');
      if(JSON.stringify(message.data)?.length>1500000)throw Error('Ответ слишком большой.');
      const payload=await parseVietinBankJson(message.data);
      await chrome.storage.session.set({bankCapture:{...state,status:'ready',payload}});
    }catch(error){await chrome.storage.session.set({bankCapture:{...state,status:'error',error:error.message}});}
  })().then(()=>reply({ok:true}),()=>reply({ok:false})).finally(()=>{processing=false;});
  return true;
});
