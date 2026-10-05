export function normalizeBaseUrl(value) {
  let url;
  try { url = new URL(value); } catch { throw new Error('Укажите корректный адрес сервера.'); }
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/')
    throw new Error('Укажите адрес сервера без пути, пароля и параметров.');
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname)))
    throw new Error('Удалённый сервер должен использовать HTTPS.');
  return url.origin;
}
export async function settings() {
  return chrome.storage.local.get({baseUrl: 'http://127.0.0.1:18791', token: ''});
}
export async function api(path, body, method = 'POST') {
  const config = await settings();
  if (!config.token) throw new Error('Сохраните API-токен в настройках подключения.');
  let response;
  try {
    response = await fetch(normalizeBaseUrl(config.baseUrl) + path, {
      method: body === undefined ? 'GET' : method,
      headers: {'Authorization': `Bearer ${config.token}`, 'Content-Type': 'application/json'},
      ...(body === undefined ? {} : {body: JSON.stringify(body)}),
      signal: AbortSignal.timeout(60000)
    });
  } catch (error) {
    throw new Error(error.name === 'TimeoutError' ? 'Сервер не ответил вовремя. Обновите данные перед повтором.' : 'Сервер недоступен. Проверьте адрес и запуск Docker.');
  }
  let data;
  try { data = await response.json(); } catch { throw new Error(`Некорректный ответ сервера (HTTP ${response.status}).`); }
  if (!response.ok) {
    if (response.status === 401) throw new Error('Неверный API-токен. Проверьте настройки подключения.');
    throw new Error(typeof data.detail === 'string' ? data.detail : `Запрос отклонён (HTTP ${response.status}). Проверьте данные.`);
  }
  return data;
}
