import {api, settings} from './api.js';
const $ = id => document.getElementById(id);
const stateNames = {pending: 'Ожидает проверки', queued: 'Подтверждена', sent: 'Отправлена', linked: 'Связана', ignored: 'Пропущена', uncertain: 'Результат неизвестен', conflict: 'Конфликт'};
function el(tag, text, className) {
  const node = document.createElement(tag); if (text !== undefined) node.textContent = text;
  if (className) node.className = className; return node;
}
function button(text, fn) { const node = el('button', text); node.onclick = () => run(fn); return node; }
function select(items, placeholder) {
  const node = el('select'); node.append(new Option(placeholder, ''));
  items.forEach(item => node.append(new Option(item.title, item.id))); return node;
}
let busy = false;
async function run(fn) {
  if (busy) return;
  busy = true; document.querySelectorAll('button').forEach(n => n.disabled = true);
  $('status').textContent = 'Выполняется…';
  try { const result = await fn(); await refresh(); $('status').textContent = result?.results ? JSON.stringify(result.results) : 'Готово'; }
  catch (error) { $('status').textContent = error.message; }
  finally { busy = false; document.querySelectorAll('button').forEach(n => n.disabled = false); }
}
async function refresh() {
  const state = await api('/api/state');
  $('mode').textContent = state.mode === 'demo' ? 'ДЕМО · без записи в ZenMoney' : 'LIVE · реальные данные';
  for (const id of ['accounts', 'review', 'outbox', 'zen']) $(id).replaceChildren();
  if (!state.bank_accounts.length) $('accounts').append(el('p', 'Сначала считайте операции через иконку расширения на странице банка.'));
  state.bank_accounts.forEach(account => {
    const row = el('div', undefined, 'account');
    row.append(el('strong', `${account.bank} / ${account.account} · ${account.currency}`));
    const target = select(state.zenmoney_accounts.filter(a => !a.archive), 'Выберите счёт ZenMoney');
    target.value = account.zenmoney_account || '';
    row.append(target, button('Сопоставить', () => api(`/api/accounts/${account.id}/mapping`, {zenmoney_account: target.value}, 'PUT')));
    $('accounts').append(row);
  });
  if (!state.review.length) $('review').append(el('p', 'Операций пока нет. Для первого прогона откройте http://127.0.0.1:8000/demo-bank.'));
  state.review.forEach(tx => {
    const card = el('article');
    card.append(el('span', stateNames[tx.state], 'badge'), el('h3', `${tx.date} · ${new Intl.NumberFormat('ru-RU', {style:'currency',currency:tx.currency}).format(tx.amount_minor / (tx.currency === 'VND' ? 1 : 100))}`), el('p', tx.description));
    const raw = el('details'); raw.append(el('summary', 'Исходные поля'), el('pre', JSON.stringify(tx.raw, null, 2))); card.append(raw);
    if (tx.state === 'pending' && tx.mapped) {
      const category = select(state.categories, 'Без категории'); card.append(category);
      const decide = (action, match_id = null) => api(`/api/review/${tx.id}/confirm`, {action, match_id, category: category.value || null, reviewed_candidates: tx.candidates.map(c => c.id)});
      tx.candidates.forEach(candidate => {
        card.append(el('p', `${candidate.reason}: ${candidate.date} · ${candidate.payee || candidate.comment || candidate.id}`),
          button('Это та же операция', () => decide('link', candidate.id)));
      });
      card.append(button('Подтвердить создание', () => {
        if (tx.candidates.length && !confirm('Есть похожие операции. Создать отдельную операцию?')) return;
        return decide('create');
      }), button('Пропустить', () => decide('ignore')));
    } else if (!tx.mapped) card.append(el('p', 'Сначала сопоставьте счёт выше.'));
    $('review').append(card);
  });
  state.outbox.forEach(item => {
    const row = el('div');
    row.append(el('p', `${item.zen_id} · ${stateNames[item.state] || item.state}${item.error ? ' · ' + item.error : ''}`));
    if (item.state === 'queued') row.append(button('Вернуть на проверку', () => api(`/api/outbox/${item.bank_transaction}/reopen`, {})));
    $('outbox').append(row);
  });
  if (!state.outbox.length) $('outbox').append(el('p', 'Нет подтверждённых операций.'));
  state.zenmoney_transactions.forEach(tx => $('zen').append(el('p', `${tx.date} · +${tx.income} / −${tx.outcome} · ${tx.comment || tx.payee || ''}${tx.deleted ? ' · удалена' : ''}`)));
}
$('settings').onsubmit = async event => {
  event.preventDefault();
  try {
    const url = new URL($('url').value);
    if (url.username || url.password || url.search || url.hash || url.pathname !== '/') throw new Error('Укажите только адрес сервера без пути и параметров.');
    if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname))) throw new Error('Удалённый сервер должен использовать HTTPS.');
    if (url.protocol === 'https:') {
      const granted = await chrome.permissions.request({origins: [`${url.origin}/*`]});
      if (!granted) throw new Error('Доступ к серверу не разрешён');
    }
    await chrome.storage.local.set({baseUrl: url.origin, token: $('token').value.trim()});
    await run(async () => {});
  } catch (error) { $('status').textContent = error.message; }
};
$('refresh').onclick = () => run(async () => {});
$('sync').onclick = () => run(() => api('/api/zenmoney/sync', {}));
$('send').onclick = () => {
  if (confirm('Отправить все подтверждённые операции? В LIVE-режиме они появятся в ZenMoney.')) run(() => api('/api/outbox/send', {}));
};
const config = await settings(); $('url').value = config.baseUrl; $('token').value = config.token;
if (config.token) await run(async () => {});
