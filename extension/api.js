export async function settings() {
  return chrome.storage.local.get({baseUrl: 'http://127.0.0.1:8000', token: ''});
}
export async function api(path, body, method = 'POST') {
  const config = await settings();
  if (!config.token) throw new Error('Откройте интерфейс и сохраните API-токен.');
  const response = await fetch(config.baseUrl + path, {
    method: body === undefined ? 'GET' : method,
    headers: {'Authorization': `Bearer ${config.token}`, 'Content-Type': 'application/json'},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
    signal: AbortSignal.timeout(120000)
  });
  let data;
  try { data = await response.json(); } catch { throw new Error(`Ответ сервера: HTTP ${response.status}`); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  return data;
}
