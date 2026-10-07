"""Synthetic credential contract tests; production stores are not substituted by CLI flags."""
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
from repo_platform.credentials import custody, totp

class MemoryStore:
    def __init__(self):
        self.raw = None
    def read(self):
        return self.raw
    def write(self, raw, *, replace=False):
        if self.raw is not None and not replace:
            custody.fail('credentials_already_provisioned')
        self.raw = raw

class Tty(io.StringIO):
    def isatty(self):
        return True

class Contracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / 'config/accounts.json'
        self.env = patch.dict(os.environ, {'AGENT_FRAMEWORK_STATE_HOME': str(self.root / 'state')})
        self.env.start()
        self.data = {'version': 1, 'namespace': str(uuid.uuid4()), 'accounts': [{
            'id': 'example', 'status': 'active', 'allowed_origins': ['https://example.test', 'https://frame.example.test'],
            'identity': {'username': 'synthetic-user'},
            'credentials': {'password': str(uuid.uuid4()), 'totp': str(uuid.uuid4()), 'security_answer': str(uuid.uuid4())}}]}
        custody.write_registry(self.config, self.data, create=True)
        self.selected = custody.resolve_selection('example', 'password', 'https://example.test', config=self.config)
        self.store = MemoryStore()
    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()
    def assertCode(self, code, fn):
        with self.assertRaises(custody.CustodyError) as observed:
            fn()
        self.assertEqual(observed.exception.code, code)
        self.assertNotIn('private synthetic', str(observed.exception))
    def test_independent_outer_and_field_origins(self):
        good = custody.resolve_pair('example', 'password', 'https://example.test', 'https://frame.example.test', config=self.config)
        self.assertEqual(good.origin, 'https://frame.example.test')
        for outer, inner in [('https://bad.invalid', 'https://example.test'), ('https://example.test', 'https://bad.invalid')]:
            self.assertCode('origin_not_allowed', lambda: custody.resolve_pair('example', 'password', outer, inner, config=self.config))
    def test_exact_origin_only(self):
        for bad in ['http://example.test', 'https://example.test/', 'https://user@example.test', 'https://example.test:443', 'https://example.test/path']:
            self.assertCode('origin_invalid', lambda: custody.origin(bad))
    def test_registry_conflicts_and_federation(self):
        d = copy.deepcopy(self.data)
        d['accounts'][0]['credentials']['totp'] = d['accounts'][0]['credentials']['password']
        self.assertCode('credential_reference_conflict', lambda: custody.validate_registry(d))
        d = copy.deepcopy(self.data)
        d['accounts'][0]['federated'] = True
        self.assertCode('federated_credentials_not_local', lambda: custody.validate_registry(d))
        self.assertCode('credential_not_provider_bound', lambda: custody.resolve_selection('example', 'security_answer', 'https://example.test', config=self.config))
    def test_metadata_drift_refuses_read(self):
        custody.write_payload(self.selected, 'private synthetic value', self.store)
        self.data['accounts'][0]['allowed_origins'].append('https://other.test')
        custody.write_registry(self.config, self.data)
        self.assertCode('selection_changed', lambda: custody.read_value(self.selected, _store=self.store))
    def test_envelope_swap_rejected(self):
        custody.write_payload(self.selected, 'private synthetic value', self.store)
        data = json.loads(self.store.raw)
        data['binding']['account'] = 'other'
        self.store.raw = json.dumps(data).encode()
        self.assertCode('custody_binding_mismatch', lambda: custody.read_value(self.selected, _store=self.store))
    def test_hidden_intake_and_refused_overwrite(self):
        tty = Tty()
        result = custody.intake(self.selected, _store=self.store, _stdin=tty, _stderr=tty,
                                _reader=lambda *a, **k: 'private synthetic value')
        self.assertEqual(result['status'], 'stored_unverified')
        self.assertNotIn('private synthetic', json.dumps(result) + tty.getvalue())
        self.assertEqual(custody.read_value(self.selected, _store=self.store), 'private synthetic value')
        self.assertCode('credentials_already_provisioned', lambda: custody.intake(self.selected, _store=self.store,
            _stdin=tty, _stderr=tty, _reader=lambda *a, **k: self.fail('must not prompt')))
    def test_redirected_console_and_echo_fallback_rejected(self):
        self.assertCode('interactive_console_required', lambda: custody.intake(self.selected, _store=self.store,
            _stdin=io.StringIO(), _stderr=Tty()))
        def echo(*a, **k):
            import warnings, getpass
            warnings.warn('echo unavailable', getpass.GetPassWarning)
        self.assertCode('interactive_console_required', lambda: custody.intake(self.selected, _store=self.store,
            _stdin=Tty(), _stderr=Tty(), _reader=echo))
        self.assertIsNone(self.store.raw)
    def test_pending_active_and_replacement(self):
        def op(operation, **kwargs):
            return totp.operate(operation, 'example', 'https://example.test', config=self.config, _store=self.store, **kwargs)
        seed = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
        self.assertCode('totp_parameters_invalid', lambda: op('capture_totp', value=seed, options=[]))
        self.assertEqual(op('capture_totp', value=seed), {'state': 'pending'})
        self.assertCode('totp_enrollment_pending', lambda: op('totp_code'))
        self.assertEqual(op('totp_enrollment_code', _now=59), '287082')
        self.assertEqual(op('activate_totp'), {'state': 'active'})
        self.assertEqual(op('totp_code', _now=59), '287082')
        self.assertCode('totp_already_configured', lambda: op('capture_totp', value='JBSWY3DPEHPK3PXP', replace_pending=True))
    def test_totp_rfc6238_sha_variants(self):
        import base64
        vectors = [('SHA1', b'12345678901234567890', '94287082'),
                   ('SHA256', b'12345678901234567890123456789012', '46119246'),
                   ('SHA512', b'1234567890123456789012345678901234567890123456789012345678901234', '90693936')]
        for algorithm, seed, code in vectors:
            self.assertEqual(totp.generate(base64.b32encode(seed).decode().rstrip('='),
                                          {'algorithm': algorithm, 'digits': 8, 'period': 30}, 59), code)
    def test_symlink_registry_rejected(self):
        link = self.root / 'linked.json'
        try:
            link.symlink_to(self.config)
        except OSError:
            self.skipTest('Host cannot create symlink as current principal')
        self.assertCode('custody_path_invalid', lambda: custody.load_registry(link))

    def test_secret_service_provider_schema_preserves_binding_checks(self):
        from repo_platform.credentials.storage import SecretServiceStore
        from types import SimpleNamespace
        expected = {'application': 'agent-framework', 'namespace': 'fixture-namespace',
                    'account': 'example', 'purpose': 'password', 'reference': 'fixture-reference', 'version': '1'}
        observed = {**expected, 'xdg:schema': 'org.freedesktop.Secret.Generic'}
        item = SimpleNamespace(is_locked=lambda: False, get_attributes=lambda: observed)
        store = object.__new__(SecretServiceStore)
        store.attributes = expected
        store.collection = SimpleNamespace(is_locked=lambda: False, search_items=lambda attrs: [item])
        self.assertIs(store._item(), item)
        for key in expected:
            original = observed[key]
            observed[key] = 'changed'
            self.assertCode('custody_binding_mismatch', store._item)
            observed[key] = original
        observed['unknown-provider-field'] = 'extra'
        self.assertCode('custody_binding_mismatch', store._item)
        del observed['unknown-provider-field']
        store.collection.search_items = lambda attrs: [item, item]
        self.assertCode('credential_reference_conflict', store._item)

if __name__ == '__main__':
    unittest.main()
