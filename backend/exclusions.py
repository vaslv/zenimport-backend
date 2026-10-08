"""Local reconciliation exclusions survive imports and ZenMoney synchronization."""
import time
from .service import audit, fail, get_row, get_entity


def zen_ids(db):
    return {r[0] for r in db.execute('SELECT zen_id FROM reconciliation_exclusions')}


def set_excluded(db, source, identifier, excluded):
    if source == 'bank':
        row = get_row(db, 'bank_transactions', identifier)
        if row['state'] not in ('pending', 'ignored'):
            fail('Сначала отмените связь или верните операцию из очереди')
        db.execute('UPDATE bank_transactions SET state=? WHERE id=?', ('ignored' if excluded else 'pending', identifier))
    elif source == 'zenmoney':
        get_entity(db, 'transaction', identifier)
        if db.execute('SELECT 1 FROM reconciliation_zen_members WHERE zen_id=?', (identifier,)).fetchone() or db.execute('SELECT 1 FROM outbox WHERE zen_id=?', (identifier,)).fetchone():
            fail('Сначала отмените связь; связанную операцию нельзя исключить')
        if excluded:
            db.execute('INSERT OR IGNORE INTO reconciliation_exclusions VALUES (?,?)', (identifier, int(time.time())))
        else:
            db.execute('DELETE FROM reconciliation_exclusions WHERE zen_id=?', (identifier,))
    else:
        fail('Неизвестный источник', 422)
    audit(db, 'exclude' if excluded else 'restore_excluded', {'source': source, 'id': identifier})
    return {'excluded': excluded}
