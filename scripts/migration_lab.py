"""Explicit live acceptance tools; ordinary tests never call ZenMoney.

The pinned identity comes from the earlier verified SDK test-account journal.
The production env is read by init only to reject accidentally reused tokens.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from uuid import UUID, uuid4


def read_env(path):
    values = {}
    for raw in Path(path).read_text().splitlines():
        if raw.strip() and not raw.lstrip().startswith('#') and '=' in raw:
            key, value = raw.split('=', 1)
            values[key.strip()] = value.strip().strip('\"\'')
    return values


def fingerprint(token):
    return hashlib.sha256(token.encode()).hexdigest()


def save(path, data):
    from zenmoney_api.protocol import encode_json
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encode_json(data)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def initialize(root, token_file, identity_file, main_env, ports):
    token = read_env(token_file).get('ZENMONEY_TEST_TOKEN', '')
    main_settings = read_env(main_env)
    main_token = main_settings.get('ZENMONEY_TOKEN', '')
    prior = json.loads(Path(identity_file).read_text())
    user = prior.get('user')
    if (not token or not main_token or token == main_token or
            type(user) is not int or user <= 0 or
            prior.get('cleanup', {}).get('state') != 'verified'):
        raise RuntimeError('Expected separate tokens and a verified historical test identity')
    main_port = int(main_settings.get('HTTP_PORT') or 18792)
    if len(set(ports)) != 2 or any(type(p) is not int or not 1024 <= p <= 65535 or p in (18792, main_port) for p in ports):
        raise RuntimeError('Use two distinct unprivileged lab ports outside the production port')
    run = str(uuid4())
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    state = root/run
    state.mkdir(mode=0o700)
    for name in ('server-a', 'server-b'):
        (state/name).mkdir(mode=0o700)
    identity = dict(run=run, expected_user=user, token_sha256=fingerprint(token),
        forbidden_token_sha256=fingerprint(main_token),
        provenance='earlier_verified_sdk_test_journal',
        created_at=datetime.now(timezone.utc).isoformat())
    # init has no SDK/network dependency; this is metadata, not a financial journal.
    fd = os.open(state/'identity.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(identity, stream)
    env = dict(LAB_RUN_ID=run, LAB_STATE_DIR=str(state),
        LAB_TOKEN_FILE=str(Path(token_file).resolve()), LAB_UID=os.getuid(), LAB_GID=os.getgid(),
        LAB_API_TOKEN_A=secrets.token_urlsafe(32), LAB_API_TOKEN_B=secrets.token_urlsafe(32),
        LAB_PORT_A=ports[0], LAB_PORT_B=ports[1])
    if any(any(c in str(value) for c in '\n\r\"$') for value in env.values()):
        raise RuntimeError('Unsupported environment path')
    fd = os.open(state/'lab.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(''.join(f'{key}="{value}"\n' for key, value in env.items()))
    return state


def validate_source(identity, token, run):
    if (str(UUID(run)) != run or identity.get('run') != run or
            identity.get('token_sha256') != fingerprint(token) or
            identity.get('forbidden_token_sha256') == fingerprint(token)):
        raise RuntimeError('Lab identity or token mismatch')


def validate_identity(data, identity, token, run):
    validate_source(identity, token, run)
    users = data.get('user', [])
    if (len(users) != 1 or type(identity.get('expected_user')) is not int or
            users[0].get('id') != identity['expected_user']):
        raise RuntimeError('Remote user is not the verified standalone test account')
    if any(key not in data for key in ('account', 'tag', 'transaction', 'instrument')):
        raise RuntimeError('Full test-account snapshot required')


def catalog_plan(data, identity):
    from zenmoney_api import entities
    instruments = {row.get('shortTitle'): row['id'] for row in data['instrument']}
    if not {'USD', 'VND'} <= instruments.keys():
        raise RuntimeError('Test catalog requires USD and VND')
    user = identity['expected_user']
    prefix = 'ZenImport LAB '+identity['run'][:8]
    accounts = [entities.account(user=user, title=prefix+' '+label, instrument=instruments[currency])
        for label, currency in [('A VND', 'VND'), ('B VND', 'VND'), ('C USD', 'USD')]]
    tags = [entities.tag(user=user, title=prefix+' '+label, showIncome=income,
        budgetIncome=income, showOutcome=not income, budgetOutcome=not income)
        for label, income in [('Expense', False), ('Income', True)]]
    merchant = entities.merchant(user=user, title=prefix+' Person')
    return {'account': accounts, 'tag': tags, 'merchant': [merchant]}


def verify_catalog(data, plan):
    for kind, rows in plan.items():
        actual = {row['id']: row for row in data.get(kind, [])}
        for row in rows:
            current = actual.get(row['id'])
            keys = {'id', 'user', 'title'}
            if kind == 'account':
                keys |= {'instrument', 'type', 'archive', 'startBalance', 'balance'}
            if kind == 'tag':
                keys |= {'showIncome', 'showOutcome', 'archive'}
            if current is None or current.get('deleted') or any(current.get(k) != row.get(k) for k in keys):
                return False
    return True


def unchanged_baseline(data, baseline):
    # Existing accounts/categories and transactions are never acceptance targets.
    # Server change timestamps may advance; all other retained fields must agree.
    for kind in ('account', 'tag', 'merchant', 'transaction'):
        current = {row['id']: row for row in data.get(kind, [])}
        for old in baseline.get(kind, []):
            got = current.get(old['id'])
            if got is None or any(got.get(k) != v for k, v in old.items() if k != 'changed'):
                return False
    return True


def prepare_catalog(api, state, identity, token):
    with (state/'.catalog.lock').open('a') as lock:
        os.chmod(state/'.catalog.lock', 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another catalog acceptance is running') from None
        return _prepare_catalog(api, state, identity, token)


def _prepare_catalog(api, state, identity, token):
    from zenmoney_api.protocol import decode_json
    data = api.read_diff()
    validate_identity(data, identity, token, identity['run'])
    path = state/'catalog.json'
    if path.exists():
        journal = decode_json(path.read_bytes())
        if journal.get('run') != identity['run'] or journal.get('user') != identity['expected_user']:
            raise RuntimeError('Catalog journal belongs to another run or user')
        # No retransmission, including a write whose response was lost.
        if not verify_catalog(data, journal['plan']) or not unchanged_baseline(data, journal['baseline']):
            raise RuntimeError('Existing catalog intent unconfirmed; inspect instead of repeating')
        journal['state'] = 'verified'
        save(path, journal)
        return journal
    plan = catalog_plan(data, identity)
    for kind, rows in plan.items():
        existing = {r['id'] for r in data.get(kind, [])}
        if any(r['id'] in existing for r in rows):
            raise RuntimeError('Catalog ID collides with a preexisting entity')
    journal = dict(run=identity['run'], user=identity['expected_user'], state='intent',
        baseline={k: data.get(k, []) for k in ('account', 'tag', 'merchant', 'transaction')}, plan=plan)
    save(path, journal)  # durable intent before the first network write
    try:
        api.diff(0, changes=plan)
    except Exception:
        journal['state'] = 'uncertain'; save(path, journal)
        raise RuntimeError('Catalog result uncertain; repeat only to check, never to resend') from None
    data = api.read_diff()
    validate_identity(data, identity, token, identity['run'])
    if not verify_catalog(data, plan) or not unchanged_baseline(data, journal['baseline']):
        raise RuntimeError('Catalog or preexisting entities failed verification')
    journal['state'] = 'verified'; save(path, journal)
    return journal


def remote_action(command, write=False, options=None):
    from zenmoney_api import Client
    identity = json.loads(Path(os.environ['LAB_IDENTITY_FILE']).read_text())
    run = os.environ['LAB_RUN_ID']
    token = read_env(os.environ['LAB_TOKEN_FILE']).get('ZENMONEY_TEST_TOKEN', '')
    if not token:
        raise RuntimeError('Explicit ZENMONEY_TEST_TOKEN required')
    validate_source(identity, token, run)
    state = Path(os.environ['LAB_STATE_DIR'])
    from migration_history import AuditedTransport
    with Client(token,transport=AuditedTransport(state/'tools-sdk-audit.jsonl')) as api:
        data = api.read_diff()
        validate_identity(data, identity, token, run)
        if command == 'catalog':
            if not write:
                raise RuntimeError('Catalog creation requires --write')
            journal = prepare_catalog(api, state, identity, token)
            print(json.dumps({'stage':'catalog', 'identity_verified':True,
                'state':journal['state'], 'created_counts':{k:len(v) for k,v in journal['plan'].items()},
                'baseline_preserved':True}))
            return
        if command == 'inspect':
            print(json.dumps({'stage':'inspect', 'identity_verified':True,
                'counts':{k:len(data.get(k, [])) for k in ('account','tag','merchant','transaction')},
                'currencies_available':all(any(i.get('shortTitle') == c for i in data['instrument']) for c in ('USD','VND'))}))
            return
        if command in ('history','mutate','verify'):
            from migration_history import prepare_history, mobile_changes, verify_history, history_lock
            if command in ('history','mutate') and not write:
                raise RuntimeError('Controlled financial changes require --write')
            with history_lock(state):
                if command=='history':
                    journal=prepare_history(api,state,identity,token)
                    print(json.dumps({'stage':'history','verified':True,
                        'variants':{k:{'seed_rows':len(v['seed']),'bank_rows':len(v['records'])} for k,v in journal['variants'].items()}}))
                elif command=='mutate':mobile_changes(api,state,identity,token)
                else:verify_history(api,state,identity,token,options.variant,options.instance,later=options.later,edited=options.edited)
            return
    if command == 'serve':
        os.environ['ZENMONEY_TOKEN'] = token
        os.execv(sys.executable, [sys.executable, '-m', 'uvicorn', 'migration_history:create_lab_app',
            '--app-dir','/app/scripts','--factory', '--host', '0.0.0.0', '--port', '8000', '--workers', '1', '--no-access-log'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    for flag in ('root', 'token-file', 'identity-file', 'main-env'):
        init.add_argument('--'+flag, type=Path, required=True)
    init.add_argument('--ports', type=int, nargs=2, default=[18892, 18893])
    sub.add_parser('inspect')
    catalog = sub.add_parser('catalog'); catalog.add_argument('--write', action='store_true')
    for command in ('history','mutate'):
        action=sub.add_parser(command);action.add_argument('--write',action='store_true')
    verify=sub.add_parser('verify')
    verify.add_argument('--variant',choices=('migrated','empty'),required=True)
    verify.add_argument('--instance',choices=('a','b'),required=True)
    verify.add_argument('--later',action='store_true')
    verify.add_argument('--edited',action='store_true')
    sub.add_parser('serve')
    args = parser.parse_args()
    try:
        if args.command == 'init':
            state = initialize(args.root, args.token_file, args.identity_file, args.main_env, args.ports)
            print('Private lab configuration: '+str(state/'lab.env'))
        else:
            remote_action(args.command, getattr(args, 'write', False),args)
    except Exception:
        # No SDK response, credential, path or personal identity in error output.
        parser.exit(1, 'Lab action failed; inspect the private state before retrying. No automatic write retry.\n')


if __name__ == '__main__':
    main()
