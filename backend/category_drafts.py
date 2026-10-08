"""Explicit local category choices, separate from mirrored history and learning."""
import time
from .service import get_row, get_entity, fail, audit


def save(db, identifier, category):
    row = get_row(db, 'bank_transactions', identifier)
    if row['state'] != 'pending':
        fail('Категория выбирается только для ещё не обработанной банковской операции')
    if category is None:
        db.execute('DELETE FROM category_drafts WHERE bank_id=?', (identifier,))
    else:
        tag = get_entity(db, 'tag', category)
        if tag.get('deleted'):
            fail('Категория удалена; обновите список')
        db.execute('INSERT INTO category_drafts VALUES (?,?,?) ON CONFLICT(bank_id) DO UPDATE SET category_id=excluded.category_id,updated_at=excluded.updated_at',
                   (identifier, category, int(time.time())))
    audit(db, 'category_draft', {'bank_id': identifier, 'category_id': category})
    return {'category_id': category}
