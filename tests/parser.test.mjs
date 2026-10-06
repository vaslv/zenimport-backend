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
