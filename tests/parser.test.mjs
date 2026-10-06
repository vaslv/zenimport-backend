import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source = readFileSync(new URL('../extension/extract.js', import.meta.url), 'utf8');
function extract(host = 'localhost', amount = '-65,000') {
  const table = {dataset: {account: 'test'}, querySelectorAll: () => [{querySelectorAll: () => ['id-1','2026-10-01',amount,'<img onerror=alert(1)>'].map(textContent => ({textContent}))}]};
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
  const src=readFileSync(new URL('../extension/devtools.js',import.meta.url),'utf8').replace("import {detailKeys} from './vietinbank-json.js';",'');
  const {detailKeys}=await import('../extension/vietinbank-json.js');
  let listener;const sent=[];const now=Date.now();
  const state={status:'waiting',tabId:42,startedAt:now-1000,expires:now+10000};
  const chrome={devtools:{inspectedWindow:{tabId:42},network:{onRequestFinished:{addListener:fn=>listener=fn}}},runtime:{onMessage:{addListener:()=>{}},sendMessage:async m=>sent.push(m)},storage:{session:{get:async()=>({bankCapture:state})}}};
  vm.runInNewContext(src,{chrome,detailKeys,URL,Date,JSON,Number,TextDecoder,Uint8Array,atob});
  const request={request:{method:'POST',url:'https://api-ipay.vietinbank.vn/ipay/wa/getHistTransactions'},response:{status:200},startedDateTime:new Date(now).toISOString(),getContent:cb=>cb(btoa(JSON.stringify(response())),'base64')};
  await listener(request);assert.equal(sent.length,1);assert.ok(!JSON.stringify(sent).includes('NEVER-SAVE'));assert.equal(sent[0].tabId,42);
  await listener({...request,startedDateTime:new Date(now-5000).toISOString()});assert.equal(sent.length,1);
  await listener({...request,request:{url:'https://api-ipay.vietinbank.vn/ipay/wa/login'}});assert.equal(sent.length,1);
  await listener({...request,request:{...request.request,method:'OPTIONS'}});assert.equal(sent.length,1);
  state.status='ready';await listener(request);assert.equal(sent.length,1);
  state.status='waiting';await listener({...request,getContent:()=>Promise.resolve({content:JSON.stringify(response()),encoding:''})});assert.equal(sent.length,2);
  await listener({...request,getContent:cb=>cb('invalid','')});assert.equal(sent[2].error,'network-response-unavailable');
});

test('history date hints use Vietnam dates and clamp month ends',async()=>{
  const {historyPeriod}=await import('../extension/history-period.js');
  assert.equal(historyPeriod(6,new Date('2026-10-06T05:00:00Z')),'06.04.2026 — 06.10.2026');
  assert.equal(historyPeriod(1,new Date('2026-03-31T05:00:00Z')),'28.02.2026 — 31.03.2026');
  assert.equal(historyPeriod(1,new Date('2026-10-05T18:00:00Z')),'06.09.2026 — 06.10.2026');
});
