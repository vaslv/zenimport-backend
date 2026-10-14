// MAIN world. Reuse VietinBank's own profile request and response interceptors.
// No credentials, profile data or bank request headers leave this closure.
(() => {
  if(window.__zenImportSession)return;window.__zenImportSession=true;
  let runtime,attempted=false,lastActivity=Date.now(),lastRefresh=Date.now(),pending=false,failed=false,lastStatus='';
  const emit=status=>{if(status===lastStatus)return;lastStatus=status;window.postMessage({source:'zenimport-session',status},location.origin);};
  const active=()=>window.__zenImportInlineEnabled!==false&&location.origin==='https://ipay.vietinbank.vn'&&location.pathname==='/payment-history'&&/^\d{6,30}$/.test(new URLSearchParams(location.search).get('accountNo')||'');
  const activity=e=>{if(e.isTrusted&&document.visibilityState==='visible'&&document.hasFocus())lastActivity=Date.now();};
  for(const event of ['pointerdown','pointermove','keydown','wheel'])document.addEventListener(event,activity,{capture:true,passive:true});
  function client(){
    if(runtime)return runtime;
    if(attempted||!Array.isArray(window.webpackJsonp)||window.webpackJsonp.push===Array.prototype.push)return null;
    attempted=true;let require;const id='zenimport-session-'+crypto.randomUUID();
    window.webpackJsonp.push([[id],{[id]:(module,exports,r)=>{require=r;}},[[id]]]);
    if(!require)return null;
    delete require.m[id];delete require.c[id];
    // IDs and exports verified against the bank's public 46c2726d00430d27ec6a bundle.
    if(!['Q2AE','tjvR','VL96'].every(k=>require.m[k]))return null;
    const store=require('Q2AE').default,api=require('tjvR'),cache=require('VL96');
    if(typeof store?.getState!=='function'||typeof api.profile!=='function'||typeof cache.clearByKeys!=='function'||!cache.cacheKeys?.profile)return null;
    runtime={store,api,cache};return runtime;
  }
  async function tick(){
    if(!active()){emit('inactive');return;}
    if(document.visibilityState!=='visible'||!document.hasFocus()||Date.now()-lastActivity>180000){emit('paused');return;}
    if(failed){emit('error');return;}if(pending)return;
    try{
      const c=client();if(!c){emit(attempted?'unsupported':'waiting');return;}
      const state=c.store.getState();
      if(!state.auth?.credentials?.isAuthenticated){emit('signed-out');return;}
      const expiration=Number(state.meta?.sessionExpiration);
      if(!Number.isFinite(expiration)||expiration<30||expiration>3600){emit('unsupported');return;}
      emit('active');
      if(Date.now()-lastRefresh<Math.min(120000,expiration*500))return;
      pending=true;emit('refreshing');
      c.cache.clearByKeys(c.cache.cacheKeys.profile);
      const result=await c.api.profile();
      if(result?.data?.error!==false)throw Error('Profile unavailable');
      lastRefresh=Date.now();emit('extended');
    }catch{failed=true;emit('error');}finally{pending=false;}
  }
  const timer=setInterval(tick,10000);
  window.addEventListener('pagehide',()=>{clearInterval(timer);for(const event of ['pointerdown','pointermove','keydown','wheel'])document.removeEventListener(event,activity,true);},{once:true});
  tick();
})();
