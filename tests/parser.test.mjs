import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source = readFileSync(new URL('../extension/extract.js', import.meta.url), 'utf8');
function extract(host = 'localhost', amount = '-65,000') {
  const table = {dataset: {account: 'test'}, querySelectorAll: () => [{querySelectorAll: () => ['id-1','2026-10-01',amount,'<img onerror=alert(1)>'].map(textContent => ({textContent,cloneNode(){return {textContent,querySelectorAll:()=>[]};}}))}]};
  return vm.runInNewContext(source, {location: {hostname: host, pathname: '/demo-bank'}, document: {querySelector: () => table}});
}
test('normalizes explicit VND format; preserves raw fields without HTML', async () => {
  const result = await extract(); assert.equal(result.ok, true);
  assert.equal(result.data.transactions[0].amount_minor, -65000);
  assert.equal(result.data.transactions[0].raw.amount, '-65,000');
  assert.equal(Object.hasOwn(result.data, 'html'), false);
});
test('rejects unsupported domains instead of guessing a bank', async () => assert.equal((await extract('bank.example')).ok, false));
test('rejects ambiguous separators and unsafe amounts', async () => {
  for (const amount of ['65.000', '65,00', 'Infinity', '1e9', '9007199254740992', '0']) assert.equal((await extract('localhost', amount)).ok, false);
});

const {parseVietinBankJson}=await import('../extension/vietinbank-json.js');
const tx=(changes={})=>({currency:'VND',remark:'Synthetic coffee',amount:'65000',balance:'935000',trxId:'FAKE-1',processDate:'06-10-2026 12:29:59',dorC:'D',corresponsiveName:'Example shop',...changes});
const response=(changes={})=>({error:false,accountNo:'111122223333',currentPage:0,nextPage:1,pageSize:1,totalRecords:2,transactions:[tx()],sessionId:'NEVER-SAVE',requestId:'NEVER-SAVE',checksum:'NEVER-SAVE',...changes});
test('JSON page keeps exact amounts, debit/credit, details and removes envelope secrets',async()=>{
  const p=await parseVietinBankJson(response());
  assert.equal(p.transactions[0].amount_minor,-65000);
  assert.equal(p.transactions[0].date,'2026-10-06');
  assert.equal(p.transactions[0].raw.details.corresponsiveName,'Example shop');
  assert.equal(p.capture.complete,false);assert.equal(p.capture.row_end,1);
  assert.ok(!JSON.stringify(p).includes('NEVER-SAVE'));
  const second=await parseVietinBankJson(response({currentPage:1,transactions:[tx({dorC:'C'})]}));
  assert.equal(second.capture.row_start,2);assert.equal(second.transactions[0].amount_minor,65000);
  assert.equal(second.capture.complete,false);
});
test('missing bank ID has stable fingerprint and transaction-only allowlist',async()=>{
  const input=response({transactions:[tx({trxId:'',sessionId:'NEVER-SAVE'})]});
  const a=await parseVietinBankJson(input), b=await parseVietinBankJson(input);
  assert.equal(a.transactions[0].external_id,b.transactions[0].external_id);
  assert.equal(a.transactions[0].raw.reference_kind,'fingerprint');
  assert.ok(!JSON.stringify(a).includes('NEVER-SAVE'));
});
test('JSON rejects failed responses, incomplete pages and unknown formats',async()=>{
  for(const patch of [{error:true},{currentPage:-1},{pageSize:0},{totalRecords:0},{transactions:[]},{accountNo:'?'},{pageSize:2}])await assert.rejects(parseVietinBankJson(response(patch)));
  for(const patch of [{amount:'1e6'},{amount:'-100'},{amount:'0'},{amount:'9000000000001'},{dorC:'?'},{currency:'USD'},{processDate:'31-02-2026 12:00:00'},{processDate:'06-10-2026 24:00:00'}])await assert.rejects(parseVietinBankJson(response({transactions:[tx(patch)]})));
});
test('JSON refuses identical IDs within a response',async()=>{
  await assert.rejects(parseVietinBankJson(response({pageSize:2,transactions:[tx(),tx()]})));
});
test('complete applies only to a single response covering the whole result',async()=>{
  assert.equal((await parseVietinBankJson(response({totalRecords:1}))).capture.complete,true);
});

test('DevTools captures only newly armed history responses, strips secrets, supports base64',async()=>{
  const src=readFileSync(new URL('../extension/devtools.js',import.meta.url),'utf8').replace("import {progress} from './capture-progress.js';",'').replace("import {describeHistoryRequest} from './bank-contract.js';",'').replace("import {detailKeys} from './vietinbank-json.js';",'');
  const {detailKeys}=await import('../extension/vietinbank-json.js');
  const {describeHistoryRequest}=await import('../extension/bank-contract.js');
  let listener;const sent=[];let saved;const now=Date.now();
  const state={status:'waiting',tabId:42,startedAt:now-1000,expires:now+10000};
  const chrome={devtools:{inspectedWindow:{tabId:42},network:{onRequestFinished:{addListener:fn=>listener=fn}}},runtime:{onMessage:{addListener:()=>{}},sendMessage:async m=>sent.push(m)},storage:{local:{get:async()=>({})},session:{get:async()=>({bankCapture:state}),set:async value=>{saved=value;}}}};
  vm.runInNewContext(src,{chrome,progress:async()=>{},setTimeout:(fn,ms)=>setTimeout(fn,Math.min(ms,25)),clearTimeout,detailKeys,describeHistoryRequest,URL,Date,JSON,Number,TextDecoder,Uint8Array,atob});
  const request={request:{method:'POST',url:'https://api-ipay.vietinbank.vn/ipay/wa/getHistTransactions'},response:{status:200},startedDateTime:new Date(now).toISOString(),getContent:cb=>cb(btoa(JSON.stringify(response())),'base64')};
  await listener(request);assert.equal(sent.length,1);assert.ok(!JSON.stringify(sent).includes('NEVER-SAVE'));assert.equal(sent[0].tabId,42);
  await listener({...request,startedDateTime:new Date(now-5000).toISOString()});assert.equal(sent.length,1);
  await listener({...request,request:{url:'https://api-ipay.vietinbank.vn/ipay/wa/login'}});assert.equal(sent.length,1);
  await listener({...request,request:{...request.request,method:'OPTIONS'}});assert.equal(sent.length,1);
  state.status='ready';await listener(request);assert.equal(sent.length,1);
  state.status='waiting';await listener({...request,getContent:()=>Promise.resolve({content:JSON.stringify(response()),encoding:''})});assert.equal(sent.length,2);
  await listener({...request,getContent:cb=>cb('invalid','')});assert.equal(sent[2].error,'network-response-unavailable');
  state.diagnoseRequest=true;
  await listener({...request,request:{...request.request,postData:{text:JSON.stringify({sessionId:'SECRET',accountNo:'SECRET',currentPage:0})}}});
  assert.equal(saved.bankContract.report.body.fields.currentPage.value,0);
  assert.ok(!JSON.stringify(saved).includes('SECRET'));
  await listener({...request,getContent:()=>undefined});
  assert.equal(sent.at(-1).error,'network-response-unavailable');
});

test('history date hints use Vietnam dates and clamp month ends',async()=>{
  const {historyPeriod}=await import('../extension/history-period.js');
  assert.equal(historyPeriod(6,new Date('2026-10-06T05:00:00Z')),'06.04.2026 — 06.10.2026');
  assert.equal(historyPeriod(1,new Date('2026-03-31T05:00:00Z')),'28.02.2026 — 31.03.2026');
  assert.equal(historyPeriod(1,new Date('2026-10-05T18:00:00Z')),'06.09.2026 — 06.10.2026');
});

test('request diagnostics deny secrets and account values while exposing dates/paging',async()=>{
  const {describeHistoryRequest}=await import('../extension/bank-contract.js');
  const report=describeHistoryRequest({url:'https://api-ipay.vietinbank.vn/ipay/wa/getHistTransactions?sessionId=SECRET',method:'POST',headers:[{name:'Authorization',value:'SECRET'}],postData:{text:JSON.stringify({sessionId:'SECRET',accountNo:'SECRET',checksum:'SECRET',fromDate:'01/04/2026',toDate:'06/10/2026',currentPage:1,pageSize:100,nested:{token:'SECRET'},rows:[{secret:'SECRET'}]})}});
  assert.ok(!JSON.stringify(report).includes('SECRET'));
  assert.equal(report.body.fields.currentPage.value,1);
  assert.equal(report.body.fields.fromDate.value,'01/04/2026');
  assert.deepEqual(report.body.fields.accountNo,{type:'string'});
  assert.ok(!Object.hasOwn(report,'headers'));
  const form=describeHistoryRequest({url:report.endpoint,method:'POST',postData:{mimeType:'application/x-www-form-urlencoded',text:'token=SECRET&pageSize=100'}});
  assert.ok(!JSON.stringify(form).includes('SECRET'));assert.equal(form.body.fields.pageSize.value,'100');
  assert.throws(()=>describeHistoryRequest({url:'https://example.com/',method:'GET'}));
});

test('automatic history plan covers six months with contiguous bounded intervals',async()=>{
  const {planHistory,publicJob}=await import('../extension/auto-plan.js');
  const p=planHistory(6,new Date('2026-10-07T00:00:00Z'));
  assert.equal(p.from,'2026-04-07');assert.equal(p.to,'2026-10-07');
  for(let i=0;i<p.parts.length;i++){
    const a=p.parts[i];assert.ok((Date.parse(a.to)-Date.parse(a.from))/86400000<=29);
    if(i)assert.equal(Date.parse(a.from)-Date.parse(p.parts[i-1].to),86400000);
  }
  assert.equal(planHistory(1,new Date('2026-03-31T00:00:00Z')).from,'2026-02-28');
  assert.deepEqual(publicJob({account:'private',url:'secret',tabId:1,events:[]}),{events:[]});
});

test('response diagnostics retain rejected counters and types without bank secrets',async()=>{
  const {describeResponse}=await import('../extension/response-diagnostics.js');
  const data={currentPage:1,nextPage:'0',pageSize:100,totalRecords:'32',error:false,accountNo:'PRIVATE',sessionId:'SECRET',transactions:[
    {trxId:'PRIVATE-ID',processDate:'07-09-2026 10:00:00',remark:'SECRET',corresponsiveName:'SECRET',amount:'SECRET'},
    {trxId:'PRIVATE-ID',processDate:'SECRET'}]};
  const report=describeResponse(data,{part:0,account:'PRIVATE',parts:[{from:'2026-09-07',to:'2026-10-06'}],current:{page:1,total:132,rows:100}});
  assert.equal(report.counters.totalRecords.value,'32');assert.equal(report.counters.totalRecords.type,'string');
  assert.equal(report.expected.total,132);assert.equal(report.rowCount,2);assert.equal(report.identifiers.repeated,1);
  assert.equal(report.dates.invalid,1);assert.ok(report.accountMatches);
  assert.ok(!JSON.stringify(report).includes('SECRET'));assert.ok(!JSON.stringify(report).includes('PRIVATE'));
  assert.equal(describeResponse({totalRecords:'SECRET'}, {part:0,parts:[{}]}).counters.totalRecords.value,undefined);
  assert.equal(describeResponse(null,{part:0,parts:[{}]}).transactionsType,'missing');
});

test('money format diagnostics identify field and shape without disclosing values',async()=>{
  const {describeMoney,moneyIssue}=await import('../extension/money-diagnostics.js');
  assert.equal(moneyIssue('9000000000000'),null);
  assert.equal(moneyIssue('9000000000001'),'limit');
  assert.equal(describeMoney('-1234.00').shape,'-####.##');
  assert.equal(describeMoney('-1234.00').fractionAllZero,true);
  assert.equal(describeMoney('1234.50').fractionAllZero,false);
  assert.equal(describeMoney('SECRET').shape,'??????');
  assert.equal(describeMoney(null).issue,'type');
  assert.equal(describeMoney(1234).type,'number');
  await assert.rejects(parseVietinBankJson(response({transactions:[tx({balance:'-1234.00'})]})),/Строка 1: поле balance/);
  await assert.rejects(parseVietinBankJson(response({transactions:[tx({amount:'1,234'})]})),/Строка 1: поле amount/);
});

test('negative amounts require an independently matching older balance',async()=>{
  const pair=(direction='D',balance='6000')=>response({pageSize:2,totalRecords:2,transactions:[
    tx({trxId:'REVERSAL',amount:'-1000',balance,dorC:direction}),
    tx({trxId:'OLDER',balance:'5000',processDate:'06-10-2026 12:28:59'})]});
  assert.equal((await parseVietinBankJson(pair())).transactions[0].amount_minor,1000);
  assert.equal((await parseVietinBankJson(pair('C','4000'))).transactions[0].amount_minor,-1000);
  await assert.rejects(parseVietinBankJson(pair('D','4000')),/balance_mismatch/);
  await assert.rejects(parseVietinBankJson(response({transactions:[tx({amount:'-1000'})]})),/missing_neighbor/);
  const sameTime=pair();sameTime.transactions[1].processDate=sameTime.transactions[0].processDate;
  await assert.rejects(parseVietinBankJson(sameTime),/chronology/);
  const {describeResponse}=await import('../extension/response-diagnostics.js');
  const report=describeResponse(pair(),{part:0,parts:[{}]});
  assert.equal(report.negativeAmounts[0].balanceCheck,'verified');
  assert.equal(report.negativeAmounts[0].direction,'D');assert.equal(report.moneyIssueCount,0);
  assert.ok(!JSON.stringify(report).includes('-1000'));
});

test('local category decisions resolve review work without pretending to sync',async()=>{
  const {categoryNeedsWork,bankNeedsCategory}=await import('../extension/category-state.js');
  const z={id:'z',categories:[],transfer:false};
  assert.equal(categoryNeedsWork(z),true);
  z.category_draft={id:'food',valid:true};
  assert.equal(categoryNeedsWork(z),false);
  const b={linked_id:'z',linked:{categories:[]}};
  assert.equal(bankNeedsCategory(b,new Map([['z',z]])),false);
  z.category_draft.valid=false;
  assert.equal(categoryNeedsWork(z),true);
  assert.equal(bankNeedsCategory(b,new Map([['z',z]])),true);
  const part={id:'part',categories:[],transfer:false};
  z.category_draft.valid=true;b.split_ids=['z','part'];
  assert.equal(bankNeedsCategory(b,new Map([['z',z],['part',part]])),true);
  part.category_draft={id:'other',valid:true};
  assert.equal(bankNeedsCategory(b,new Map([['z',z],['part',part]])),false);
});

function sessionHarness(){
  let now=0,tick,requests=0,fail=false,focused=true;const messages=[],events={};
  const state={auth:{credentials:{isAuthenticated:true}},meta:{sessionExpiration:300}};
  const modules={Q2AE:{default:{getState:()=>state}},tjvR:{profile:async()=>{requests++;if(fail)throw Error();return {data:{error:false,secret:'never-expose'}};}},VL96:{cacheKeys:{profile:'profile'},clearByKeys:()=>{}}};
  const require=id=>modules[id];require.m={Q2AE:true,tjvR:true,VL96:true};require.c={};
  const chunks=[];chunks.push=function(chunk){for(const f of Object.values(chunk[1]))f({}, {},require);};
  const document={visibilityState:'visible',hasFocus:()=>focused,addEventListener:(key,fn)=>events[key]=fn,removeEventListener:()=>{}};
  const location={origin:'https://ipay.vietinbank.vn',pathname:'/payment-history',search:'?accountNo=111122223333'};
  const window={webpackJsonp:chunks,postMessage:m=>messages.push(m),addEventListener:()=>{}};
  vm.runInNewContext(readFileSync(new URL('../extension/bank-session.js',import.meta.url),'utf8'),{window,document,location,URLSearchParams,crypto:{randomUUID:()=> 'fixture'},Date:{now:()=>now},setInterval:fn=>{tick=fn;},clearInterval:()=>{}});
  return {state,document,location,messages,get requests(){return requests;},advance:async ms=>{now+=ms;await tick();},activity:()=>events.pointerdown({isTrusted:true}),synthetic:()=>events.pointerdown({isTrusted:false}),fail:()=>{fail=true;},blur:()=>{focused=false;}};
}
test('bank session extends through profile only while visible and active; never exports profile',async()=>{
  const h=sessionHarness();await h.advance(120000);assert.equal(h.requests,1);assert.equal(h.messages.at(-1).status,'extended');
  assert.equal(JSON.stringify(h.messages).includes('never-expose'),false);
  await h.advance(190000);assert.equal(h.requests,1);assert.equal(h.messages.at(-1).status,'paused');
  h.synthetic();await h.advance(1000);assert.equal(h.requests,1);
  h.activity();await h.advance(1000);assert.equal(h.requests,2);
  h.document.visibilityState='hidden';await h.advance(120000);assert.equal(h.requests,2);
  h.document.visibilityState='visible';h.activity();h.blur();await h.advance(1000);assert.equal(h.requests,2);
});
test('bank session stops on profile failure, logout, unsupported expiration and leaving history',async()=>{
  const h=sessionHarness();h.fail();await h.advance(120000);assert.equal(h.requests,1);assert.equal(h.messages.at(-1).status,'error');h.activity();await h.advance(120000);assert.equal(h.requests,1);
  const other=sessionHarness();other.state.auth.credentials.isAuthenticated=false;await other.advance(120000);assert.equal(other.requests,0);assert.equal(other.messages.at(-1).status,'signed-out');
  other.state.auth.credentials.isAuthenticated=true;other.state.meta.sessionExpiration=0;await other.advance(1000);assert.equal(other.messages.at(-1).status,'unsupported');
  other.location.pathname='/home';await other.advance(1000);assert.equal(other.messages.at(-1).status,'inactive');assert.equal(other.requests,0);
});

test('reviewed new operations are sent in sequential batches; uncertain results stop later batches',async()=>{
  const {sendNewBatches}=await import('../extension/send-new-batches.js');
  const body={bank:'demo',include_uncategorized:true,items:Array.from({length:45},(_,i)=>({bank_id:String(i),snapshot:'a'.repeat(64)}))};
  const calls=[];
  const result=await sendNewBatches(body,async b=>{calls.push(b);return {results:b.items.map(i=>({...i,state:'sent'}))};});
  assert.deepEqual(calls.map(b=>b.items.length),[20,20,5]);
  assert.deepEqual(calls.flatMap(b=>b.items),body.items);assert.equal(result.results.length,45);
  assert.ok(calls.every(b=>b.include_uncategorized));
  for(const failure of ['uncertain','throw','missing']){
    let count=0;
    const stopped=await sendNewBatches(body,async b=>{
      count++;if(failure==='throw')throw Error('timeout');
      return {results:failure==='missing'?[]:b.items.map(i=>({...i,state:failure}))};
    });
    assert.equal(count,1);assert.match(stopped.message,/остановлена/);
  }
});
