"""Synthetic-only OS store smoke/provisioning. Never use an existing registry."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from repo_platform.credentials import custody, totp

ACCOUNT = 'framework_fixture'
PASSWORD = 'framework-fixture-password-not-a-secret'
SEED = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'

def provision(config, origins):
    if config.exists():
        raise RuntimeError('Synthetic fixture requires an absent registry')
    data = {'version': 1, 'namespace': str(uuid.uuid4()), 'accounts': [{
        'id': ACCOUNT, 'status': 'active', 'allowed_origins': origins,
        'identity': {'username': 'synthetic-user'},
        'credentials': {'password': str(uuid.uuid4()), 'totp': str(uuid.uuid4())}}]}
    custody.write_registry(config, data, create=True)
    selected = custody.resolve_selection(ACCOUNT, 'password', origins[0], config=config)
    with custody.locked(custody.lock_path(selected)), custody.store_for(selected) as store:
        custody.write_payload(selected, PASSWORD, store)
    assert custody.read_value(selected) == PASSWORD
    return selected

def remove(config):
    path, data = custody.load_registry(config)
    if len(data['accounts']) != 1 or data['accounts'][0]['id'] != ACCOUNT:
        raise RuntimeError('Not a synthetic fixture registry')
    for purpose in ('password', 'totp'):
        selection = custody.resolve_custody_selection(ACCOUNT, purpose, config=config)
        with custody.store_for(selection) as store:
            store.delete()
    config.unlink()

def qualify(config, origins):
    selected = provision(config, origins)
    try:
        with custody.store_for(selected) as store:
            try:
                custody.write_payload(selected, 'replacement synthetic value', store)
                raise AssertionError('Overwrite was accepted')
            except custody.CustodyError as error:
                assert error.code == 'credentials_already_provisioned'
        try:
            custody.resolve_pair(ACCOUNT, 'password', origins[0], 'https://untrusted.invalid', config=config)
            raise AssertionError('Untrusted frame admitted')
        except custody.CustodyError as error:
            assert error.code == 'origin_not_allowed'
        assert totp.operate('totp_status', ACCOUNT, origins[0], config=config)['state'] == 'not_configured'
        assert totp.operate('capture_totp', ACCOUNT, origins[0], config=config, value=SEED)['state'] == 'pending'
        try:
            totp.operate('totp_code', ACCOUNT, origins[0], config=config)
            raise AssertionError('Pending factor admitted as active')
        except custody.CustodyError as error:
            assert error.code == 'totp_enrollment_pending'
        assert len(totp.operate('totp_enrollment_code', ACCOUNT, origins[0], config=config)) == 6
        assert totp.operate('activate_totp', ACCOUNT, origins[0], config=config)['state'] == 'active'
        assert totp.operate('totp_code', ACCOUNT, origins[0], config=config, _now=59) == '287082'
        assert custody.read_value(selected) == PASSWORD
        print(json.dumps({'status': 'passed', 'backend': 'dpapi' if sys.platform == 'win32' else 'secret_service',
                          'checks': ['protected_roundtrip', 'absent_only_write', 'independent_origin_admission',
                                     'pending_active_totp', 'RFC6238_vector'], 'secrets_emitted': False}))
    finally:
        remove(config)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('qualify', 'provision', 'remove'))
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--origin', action='append', default=[])
    args = parser.parse_args()
    os.environ['AGENT_FRAMEWORK_STATE_HOME'] = str(args.state_root)
    origins = args.origin or ['https://example.test', 'https://frame.example.test']
    if args.operation == 'qualify':
        qualify(args.config, origins)
    elif args.operation == 'provision':
        provision(args.config, origins)
        print(json.dumps({'status': 'synthetic_fixture_provisioned', 'account': ACCOUNT}))
    else:
        remove(args.config)
        print(json.dumps({'status': 'synthetic_fixture_removed'}))

if __name__ == '__main__':
    try:
        main()
    except custody.CustodyError as error:
        print(json.dumps({'status': 'error', 'code': error.code}))
        raise SystemExit(2)
