"""Account/day/amount reconciliation before creating another remote UUID."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
import re
from uuid import uuid4

from . import ownership, predictions


def reference(bank):
    # This namespace is defined by the VietinBank connector. Fingerprints and
    # generic descriptions are not remote identities.
    value = bank['external_id']
    if bank['identity_kind'] == 'bank' and value.startswith('vib:id:'):
        token = value[len('vib:id:'):]
        if re.fullmatch(r'[A-Za-z0-9]{6,128}', token):
            return token
    return None


def pool(db):
    # Ownership belongs to the bank-side account. One remote transfer can
    # legitimately represent an outgoing leg in bank A and an incoming leg in B.
    claimed, unknown = ownership.claims(db)
    excluded = {r[0] for r in db.execute('SELECT zen_id FROM zen_exclusions')}
    buckets = defaultdict(list)
    for tx in predictions.entities(db, 'transaction').values():
        if tx['id'] in excluded or tx['id'] in unknown or tx.get('hold'):
            continue
        for target in {tx.get('incomeAccount'), tx.get('outcomeAccount')} - {None}:
            if (tx['id'], target) not in claimed:
                buckets[(target, tx.get('date'))].append(tx)
    return buckets


def movement(tx, target, instrument):
    """Signed movement in the selected account, independent of operation type."""
    with localcontext() as precision:
        precision.prec = 80
        value = Decimal(0)
        for side, sign in [('income', 1), ('outcome', -1)]:
            amount = Decimal(tx.get(side) or 0)
            if tx.get(side+'Account') == target and amount:
                if tx.get(side+'Instrument') != instrument:
                    return None
                value += sign*amount
        return value


def matches(db, candidates):
    """References first; all remaining types use account/day/signed amount."""
    if not candidates:
        return {}
    buckets = pool(db)
    claimed = set()
    proposed = {}
    for bank, account in candidates:
        token = reference(bank)
        if token is None:
            continue
        pattern = re.compile(r'(?<![A-Za-z0-9])' + re.escape(token) + r'(?![A-Za-z0-9])')
        found = []
        for tx in buckets[(account['id'], bank['local_date'])]:
            if not pattern.search(tx.get('comment') or ''):
                continue
            if movement(tx, account['id'], account['instrument']) != signed(bank):
                continue
            found.append(tx['id'])
        if len(found) == 1:
            proposed[bank['id']] = found[0]
    bank_targets = {bank['id']: account['id'] for bank, account in candidates}
    owners = Counter((zen, bank_targets[bank]) for bank, zen in proposed.items())
    result = {bank: (zen, 'bank_reference') for bank, zen in proposed.items() if owners[(zen, bank_targets[bank])] == 1}
    claimed.update((zen, bank_targets[bank]) for bank, (zen, _) in result.items())
    groups = defaultdict(list)
    targets = defaultdict(set)
    for identifier, target in db.execute("SELECT id,zenmoney_account_id FROM bank_accounts WHERE selection='enabled'"):
        targets[target].add(identifier)
    for bank, account in candidates:
        if bank['id'] not in result:
            key = (account['id'], bank['local_date'], bank['direction'], Decimal(bank['amount']), account['instrument'])
            groups[key].append(bank)
    for (target, day, direction, amount, instrument), banks in groups.items():
        # Two distinct physical accounts bound to the same target need explicit
        # references; they cannot compete for a day/amount-only match.
        if len(targets[target]) != 1:
            continue
        value = amount if direction == 'income' else amount.copy_negate()
        remote = [t for t in buckets[(target, day)] if (t['id'], target) not in claimed
            and movement(t, target, instrument) == value]
        remote.sort(key=lambda t: (t.get('created') or 0, t['id']))
        banks.sort(key=lambda b: (datetime.fromisoformat(b['occurred_at']).timestamp() if b.get('occurred_at') else 0, b['id']))
        origin = 'day_amount' if len(banks) == len(remote) == 1 else 'day_amount_order'
        for bank, found in zip(banks, remote):
            result[bank['id']] = (found['id'], origin)
            claimed.add((found['id'], target))
    return result


def signed(bank):
    value = Decimal(bank['amount'])
    return value if bank['direction'] == 'income' else value.copy_negate()


@dataclass(frozen=True)
class Group:
    account_id: str
    banks: tuple[str, ...]
    zens: tuple[str, ...]
    kind: str


def unique_subset(items, target, budget):
    """Only a proved unique nonempty combination; zero and exhausted searches fail."""
    if not target or not 2 <= len(items) <= 16:
        return None
    found, exhausted = [], False
    same_sign = all(value*target > 0 for _, value in items)
    def visit(index, total, keys):
        nonlocal exhausted
        if budget[0] <= 0:
            exhausted = True
            return
        budget[0] -= 1
        if len(found) > 1 or exhausted:
            return
        if index == len(items):
            if total == target and len(keys) > 1:
                found.append(tuple(keys))
            return
        if same_sign and abs(total) > abs(target):
            return
        key, value = items[index]
        visit(index+1, total+value, keys+[key])
        visit(index+1, total, keys)
    with localcontext() as precision:
        precision.prec = 80
        visit(0, Decimal(0), [])
    return found[0] if len(found) == 1 and not exhausted else None


def group_matches(db, candidates, singles):
    """Unique splits, merged fees and net settlements within a complete day."""
    if not candidates:
        return []
    buckets, days, targets = pool(db), defaultdict(list), defaultdict(set)
    grouped = {r[0] for r in db.execute('SELECT zen_id FROM link_zen_members')}
    for identifier, target in db.execute("SELECT id,zenmoney_account_id FROM bank_accounts WHERE selection='enabled'"):
        targets[target].add(identifier)
    bank_targets = {b['id']: a['id'] for b, a in candidates}
    claimed = {(zen, bank_targets[bank]) for bank, (zen, _) in singles.items()}
    for bank, account in candidates:
        if bank['id'] not in singles and len(targets[account['id']]) == 1:
            days[(bank['account_id'], account['id'], account['instrument'], bank['local_date'])].append(bank)
    proposals = []
    for (account_id, target, instrument, day), banks in days.items():
        checkpoint = len(proposals)
        remote = {t['id']: movement(t, target, instrument) for t in buckets[(target, day)]
            if (t['id'], target) not in claimed and t['id'] not in grouped}
        remote = {k: v for k, v in remote.items() if v is not None and v != 0}
        budget = [20000]
        for bank in banks:
            zens = unique_subset(list(remote.items()), signed(bank), budget)
            if zens:
                kind = 'split' if all(remote[z]*signed(bank) > 0 for z in zens) else 'settlement'
                proposals.append((target, Group(account_id, (bank['id'],), zens, kind)))
        for zen, value in remote.items():
            bs = unique_subset([(b['id'], signed(b)) for b in banks], value, budget)
            if bs:
                proposals.append((target, Group(account_id, bs, (zen,), 'settlement')))
        if budget[0] <= 0:
            # Unsearched alternatives could conflict with an earlier proposal.
            del proposals[checkpoint:]
    bank_owners = Counter(b for _, g in proposals for b in g.banks)
    # The stored group model owns a UUID globally; conflicting proposals must
    # not violate its uniqueness constraint or silently choose one account.
    zen_owners = Counter(z for _, g in proposals for z in g.zens)
    return [g for target, g in proposals if all(bank_owners[b] == 1 for b in g.banks)
            and all(zen_owners[z] == 1 for z in g.zens)]


def recover_transfer_ownership(db, candidates, now):
    """Corroborate legacy single links with a newly captured opposite bank leg.

    A mutable current mapping alone is not proof. Require the original capture
    authorization, unchanged financial movement/target, and an unambiguous opposite
    movement from another physical account. Do not infer splits or settlements.
    """
    if not candidates:
        return
    exact, unknown = ownership.claims(db)
    remote = predictions.entities(db, 'transaction')
    accounts = predictions.entities(db, 'account')
    source = db.execute('SELECT source_hash FROM zen_mirror_state WHERE id=1').fetchone()[0]
    bindings = defaultdict(set)
    for identifier, target in db.execute("SELECT id,zenmoney_account_id FROM bank_accounts WHERE selection='enabled'"):
        bindings[target].add(identifier)
    excluded = {r[0] for r in db.execute('SELECT zen_id FROM zen_exclusions')}
    proposals = []
    for zen in sorted(unknown):
        tx = remote.get(zen)
        if not tx or tx.get('hold') or zen in excluded:
            continue
        sides = {tx.get('incomeAccount'), tx.get('outcomeAccount')}
        if None in sides or len(sides) != 2 or not tx.get('income') or not tx.get('outcome'):
            continue
        owners = list(db.execute('SELECT bank_id FROM (' + ownership.KNOWN + ') WHERE zen_id=?', (zen,)))
        # Multi-bank historical groups need their own durable evidence.
        if len(owners) != 1:
            continue
        old = owners[0][0]
        proof = db.execute('SELECT * FROM delivery_authorizations WHERE bank_id=?', (old,)).fetchone()
        if not proof or proof['source_hash'] != source or proof['target_id'] not in sides:
            continue
        bank = predictions.bank_record(db, old, proof['revision'])
        mapping = db.execute('SELECT zenmoney_account_id FROM bank_accounts WHERE id=?', (bank['account_id'],)).fetchone()
        current = predictions.bank_record(db, old)
        target = proof['target_id']
        if (current['local_date'] != bank['local_date'] or signed(current) != signed(bank)
                or mapping[0] != target or bindings[target] != {bank['account_id']}):
            continue
        instrument = accounts.get(target, {}).get('instrument')
        if instrument is None or bank['local_date'] != tx.get('date') or movement(tx, target, instrument) != signed(bank):
            continue
        other = next(iter(sides - {target}))
        if (zen, other) in exact or len(bindings[other]) != 1:
            continue
        peers = [(b, a) for b, a in candidates if b['account_id'] != bank['account_id'] and a['id'] == other
            and b['local_date'] == tx.get('date') and movement(tx, other, a['instrument']) == signed(b)]
        if len(peers) != 1:
            continue
        peer, account = peers[0]
        # A second equal remote transfer would make the new evidence ambiguous.
        alternatives = [t for t in remote.values() if not t.get('hold') and t['id'] not in excluded
            and (t['id'], other) not in exact and t.get('date') == peer['local_date']
            and other in (t.get('incomeAccount'), t.get('outcomeAccount'))
            and movement(t, other, account['instrument']) == signed(peer)]
        if len(alternatives) == 1:
            proposals.append((old, zen, target, peer['id']))
    counts = Counter(peer for _, _, _, peer in proposals)
    for old, zen, target, peer in proposals:
        if counts[peer] == 1:
            ownership.remember(db, old, zen, target, 'corroborated_transfer', now)


def reconcile(db, candidates, now):
    """Persist one plan in the caller's intent transaction, without remote writes."""
    ownership.backfill(db)
    recover_transfer_ownership(db, candidates, now)
    singles = matches(db, candidates)
    groups = group_matches(db, candidates, singles)
    recognized = set(singles) | {b for g in groups for b in g.banks}
    targets = {b['id']: a['id'] for b, a in candidates}
    for bank, (zen, origin) in singles.items():
        ownership.remember(db, bank, zen, targets[bank], origin, now)
        db.execute('INSERT INTO processing_receipts VALUES (?,?,?,?)', (bank, zen, origin, now))
    for group in groups:
        for bank in group.banks:
            for zen in group.zens:
                ownership.remember(db, bank, zen, targets[bank], 'day_amount_group', now)
        identifier = str(uuid4())
        db.execute('INSERT INTO link_groups VALUES (?,?,?,?,?)',
            (identifier, group.kind, group.account_id, now, 'day_amount_group'))
        db.executemany('INSERT INTO link_bank_members VALUES (?,?)', ((identifier, b) for b in group.banks))
        db.executemany('INSERT INTO link_zen_members VALUES (?,?)', ((identifier, z) for z in group.zens))
    for bank in recognized:
        db.execute('DELETE FROM deliveries WHERE bank_id=? AND ever_attempted=0', (bank,))
        origin = singles[bank][1] if bank in singles else 'day_amount_group'
        db.execute("UPDATE bank_processing SET state='delivered',reason=?,updated_at=? WHERE bank_id=?", ('existing_'+origin, now, bank))
    return recognized, ownership.blocked(db, candidates) - recognized
