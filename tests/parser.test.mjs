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
