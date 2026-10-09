"""Local category edits owned by ZenMoney transactions; never change the mirror."""
import json
import time
from .service import get_entity, fail, audit


def save(db, identifier, category, expected_tags):
    job = db.execute("SELECT 1 FROM zen_category_sends WHERE zen_id=? AND state='uncertain'", (identifier,)).fetchone()
    if job: fail('Сначала проверьте результат предыдущей отправки')
    tx = get_entity(db, 'transaction', identifier)
    tags = sorted(tx.get('tag') or [])
    if tags != sorted(expected_tags):
        fail('Категории ZenMoney изменились. Обновите данные и проверьте выбор.')
    if category is None:
        db.execute('DELETE FROM zen_category_drafts WHERE zen_id=?', (identifier,))
    else:
        if tx.get('deleted') or tx.get('hold') or tx.get('incomeAccount') != tx.get('outcomeAccount'):
            fail('Категория недоступна для этой операции ZenMoney')
        if db.execute('SELECT 1 FROM reconciliation_exclusions WHERE zen_id=?', (identifier,)).fetchone():
            fail('Операция исключена из сведения')
        tag = get_entity(db, 'tag', category)
        if tag.get('deleted'):
            fail('Категория удалена; обновите список')
        db.execute('INSERT INTO zen_category_drafts VALUES (?,?,?,?) ON CONFLICT(zen_id) DO UPDATE SET category_id=excluded.category_id,original_tags=excluded.original_tags,updated_at=excluded.updated_at',
                   (identifier, category, json.dumps(tags), int(time.time())))
    audit(db, 'zen_category_draft', {'zen_id': identifier, 'category_id': category})
    return {'category_id': category}


def decorate(db, days, rows, transactions, tags):
    drafts = {r['zen_id']: dict(r) for r in db.execute('SELECT * FROM zen_category_drafts')}
    banks = {r['id']: r for r in rows}
    for day in days:
        for z in day['zen_rows']:
            z['tag_ids'] = transactions[z['id']].get('tag') or []
            draft = drafts.get(z['id'])
            z['category_draft'] = None
            if draft:
                category = draft['category_id']
                conflict = json.loads(draft['original_tags']) != sorted(z['tag_ids'])
                z['category_draft'] = {'id': category, 'title': tags.get(category, 'Категория недоступна'),
                                       'valid': category in tags and not conflict, 'conflict': conflict}
            bank = banks.get(z['linked_bank_id'])
            z['category_ranking'] = bank.get('category_ranking', []) if bank and bank.get('linked') and (bank.get('group') or {}).get('kind') != 'settlement' else []
