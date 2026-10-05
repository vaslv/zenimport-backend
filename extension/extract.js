// Injected into Chrome's ISOLATED world. No backend token or network calls here.
(() => {
  const adapters = [{
    id: 'demo', version: '1.0.0',
    detect: () => ['127.0.0.1', 'localhost'].includes(location.hostname)
      && location.pathname === '/demo-bank'
      && document.querySelector('table[data-zenbridge-demo]'),
    extract() {
      const table = document.querySelector('table[data-zenbridge-demo]');
      const transactions = [...table.querySelectorAll('tbody tr')].map(row => {
        const cells = [...row.querySelectorAll('td')].map(cell => cell.textContent.trim());
        if (cells.length !== 4) throw new Error('Формат строки изменился. Импорт остановлен.');
        const [reference, date, amount, description] = cells;
        // Demo format is explicit: ISO date, signed integer VND, commas as grouping.
        if (!reference || !/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)$/.test(amount))
          throw new Error('Неоднозначная дата, сумма или банковский ID.');
        const value = Number(amount.replaceAll(',', ''));
        if (!Number.isSafeInteger(value) || value === 0) throw new Error('Некорректная сумма');
        return {external_id: reference, date, amount_minor: value, description,
          raw: {reference, date, amount, description}};
      });
      if (!transactions.length || transactions.length > 2000) throw new Error('Нужно от 1 до 2000 видимых операций.');
      return {bank: this.id, adapter_version: this.version, account: table.dataset.account,
        currency: 'VND', transactions};
    }
  }];
  try {
    const adapter = adapters.find(item => item.detect());
    if (!adapter) throw new Error('Банк пока не поддерживается. Нужен проверенный адаптер для этого домена.');
    return {ok: true, data: adapter.extract()};
  } catch (error) { return {ok: false, error: error.message}; }
})();
