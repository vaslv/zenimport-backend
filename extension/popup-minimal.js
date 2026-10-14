const $=id=>document.getElementById(id);let waiting=false;
async function rpc(type,extra={}){const r=await chrome.runtime.sendMessage({type,...extra});if(!r?.ok)throw Error(r?.error||'Не удалось выполнить действие.');return r.data;}
async function update(){if(waiting)return;try{const s=await rpc('inline-popup-state');$('inline-enable').checked=s.enabled;$('send-new').disabled=!s.enabled||!s.count;$('send-new').textContent='Синхронизировать'+(s.count?' · '+s.count:'');if(s.notice)$('send-status').textContent=s.notice;}catch(e){$('send-new').disabled=true;$('send-status').textContent=e.message;}}
$('inline-enable').onchange=async()=>{const enabled=$('inline-enable').checked;waiting=true;$('inline-enable').disabled=true;try{if(enabled&&!await chrome.permissions.request({origins:['https://ipay.vietinbank.vn/*']}))throw Error('Нужен доступ к странице VietinBank.');await rpc('inline-toggle',{enabled});}catch(e){$('send-status').textContent=e.message;}finally{waiting=false;$('inline-enable').disabled=false;await update();}};
$('settings').onclick=()=>chrome.tabs.create({url:chrome.runtime.getURL('app.html')+'#settings'});
$('send-new').onclick=async()=>{if(waiting)return;waiting=true;$('inline-enable').disabled=true;$('send-new').disabled=true;$('send-status').textContent='Синхронизируем операции текущей страницы…';try{const r=await rpc('inline-new-send');$('send-status').textContent=r.notice;}catch(e){$('send-status').textContent=e.message;}finally{waiting=false;$('inline-enable').disabled=false;await update();}};
chrome.storage.onChanged.addListener((changes,area)=>{if(area==='session'&&changes.inlineSendNotice)$('send-status').textContent=changes.inlineSendNotice.newValue||'';});
update();
// Keep the count current if the bank finishes loading while the popup is open.
setInterval(update,2000);
