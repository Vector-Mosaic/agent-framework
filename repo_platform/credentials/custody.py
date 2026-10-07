"""Metadata-only account selection and private, OS-protected credential custody."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass, field
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from urllib.parse import urlsplit
import uuid
import warnings

MAXIMUM_BYTES = 16384
ACCOUNT = re.compile(r'^[a-z][a-z0-9_.-]{0,127}$')
PURPOSES = {'password', 'security_answer', 'totp'}

class CustodyError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__('The private credential operation did not complete.')

def fail(code):
    raise CustodyError(code) from None

def default_config():
    if os.environ.get('AGENT_FRAMEWORK_CONFIG_HOME'):
        return Path(os.environ['AGENT_FRAMEWORK_CONFIG_HOME']).expanduser() / 'accounts.json'
    if sys.platform == 'win32':
        return Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming')) / 'AgentFramework/accounts.json'
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'agent-framework/accounts.json'

def state_root():
    if os.environ.get('AGENT_FRAMEWORK_STATE_HOME'):
        return Path(os.environ['AGENT_FRAMEWORK_STATE_HOME']).expanduser()
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'AgentFramework'
    return Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'agent-framework'

def checked_path(path, *, private=False):
    path = Path(os.path.abspath(Path(path).expanduser()))
    for candidate in (*reversed(path.parents), path):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            fail('custody_path_invalid')
    if private and path.exists() and os.name != 'nt':
        info = path.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            fail('custody_permissions_invalid')
    return path

def private_directory(path):
    path = checked_path(path)
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    return checked_path(path, private=True)

@contextmanager
def locked(path):
    path = checked_path(path)
    private_directory(path.parent)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, 'a+b') as stream:
        checked_path(path, private=True)
        if stream.tell() == 0:
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fail('custody_busy')
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

def _uuid(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            fail('account_registry_invalid')
        return value
    except (ValueError, AttributeError):
        fail('account_registry_invalid')

def origin(value):
    try:
        parsed = urlsplit(value)
        if (not isinstance(value, str) or parsed.scheme != 'https' or not parsed.hostname
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                or any(c.isspace() for c in value) or '\\' in value):
            fail('origin_invalid')
        host = parsed.hostname.encode('idna').decode('ascii').lower()
        if ':' in host:
            host = '[' + host + ']'
        port = parsed.port
        canonical = 'https://' + host + (':' + str(port) if port and port != 443 else '')
        if value != canonical:
            fail('origin_invalid')
        return canonical
    except CustodyError:
        raise
    except Exception:
        fail('origin_invalid')

def text(value):
    if not isinstance(value, str) or not value or '\0' in value or len(value.encode('utf-8')) > MAXIMUM_BYTES:
        fail('credential_invalid')
    return value

def validate_registry(data):
    if (not isinstance(data, dict) or set(data) - {'version', 'namespace', 'accounts', 'secret_service_collection'}
            or type(data.get('version')) is not int or data['version'] != 1
            or not isinstance(data.get('accounts'), list)):
        fail('account_registry_invalid')
    _uuid(data.get('namespace'))
    collection = data.get('secret_service_collection', '/org/freedesktop/secrets/aliases/default')
    if not isinstance(collection, str) or not re.fullmatch(r'/org/freedesktop/secrets/(?:collection|aliases)/[A-Za-z0-9_/]+', collection):
        fail('account_registry_invalid')
    ids, refs = set(), set()
    for row in data['accounts']:
        if (not isinstance(row, dict) or set(row) - {'id', 'status', 'allowed_origins', 'identity', 'credentials', 'security_answer_bound', 'federated'}
                or not isinstance(row.get('id'), str) or not ACCOUNT.fullmatch(row['id'])
                or row['id'] in ids or row.get('status') not in ('active', 'disabled', 'recovery_required')
                or not isinstance(row.get('allowed_origins'), list) or not row['allowed_origins']
                or not isinstance(row.get('identity', {}), dict) or set(row.get('identity', {})) - {'mailbox', 'username'}
                or not isinstance(row.get('credentials'), dict) or set(row['credentials']) - PURPOSES
                or type(row.get('federated', False)) is not bool or type(row.get('security_answer_bound', False)) is not bool):
            fail('account_registry_invalid')
        ids.add(row['id'])
        for item in row['allowed_origins']:
            origin(item)
        if len(set(row['allowed_origins'])) != len(row['allowed_origins']):
            fail('account_registry_invalid')
        for value in row.get('identity', {}).values():
            text(value)
        for reference in row['credentials'].values():
            _uuid(reference)
            if reference in refs:
                fail('credential_reference_conflict')
            refs.add(reference)
        if row.get('federated') and set(row['credentials']) - {'totp'}:
            fail('federated_credentials_not_local')
    return data

def load_registry(config=None):
    path = checked_path(default_config() if config is None else Path(config), private=True)
    try:
        if not path.is_file() or path.stat().st_size > 1024 * 1024:
            fail('account_registry_unavailable')
        data = json.loads(path.read_text(encoding='utf-8'))
    except CustodyError:
        raise
    except Exception:
        fail('account_registry_invalid')
    return path, validate_registry(data)

def write_registry(config, data, *, create=False):
    validate_registry(data)
    path = checked_path(Path(config))
    private_directory(path.parent)
    temporary = path.with_name('.' + path.name + '.' + secrets.token_hex(8))
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        if create:
            try:
                os.link(temporary, path)
            except FileExistsError:
                fail('account_registry_exists')
        else:
            checked_path(path)
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

@dataclass(frozen=True)
class Selection:
    account_id: str
    credential: str
    origin: str | None
    config: Path
    _registry: dict = field(repr=False)
    _account: dict = field(repr=False)

    @property
    def account_ref(self):
        return self.account_id

    @property
    def reference(self):
        key = 'totp' if self.credential in ('totp', 'totp_code', 'totp_enrollment_code') else self.credential
        return self._account['credentials'].get(key)

def resolve_custody_selection(account, credential, *, config=None):
    if not isinstance(account, str) or not ACCOUNT.fullmatch(account):
        fail('account_invalid')
    if credential not in PURPOSES | {'mailbox', 'username', 'totp_code', 'totp_enrollment_code'}:
        fail('credential_selector_invalid')
    path, data = load_registry(config)
    rows = [r for r in data['accounts'] if r['id'] == account]
    if len(rows) != 1:
        fail('account_not_found')
    row = rows[0]
    if row['status'] != 'active':
        fail('account_unavailable')
    key = 'totp' if credential in ('totp_code', 'totp_enrollment_code') else credential
    if key not in row.get('identity', {}) and key not in row['credentials']:
        fail('credential_not_declared')
    if key == 'security_answer' and not row.get('security_answer_bound'):
        fail('credential_not_provider_bound')
    return Selection(account, credential, None, path, data, row)

def resolve_selection(account, credential, origin_value, *, config=None):
    selected = resolve_custody_selection(account, credential, config=config)
    admitted = origin(origin_value)
    if admitted not in selected._account['allowed_origins']:
        fail('origin_not_allowed')
    return Selection(account, credential, admitted, selected.config, selected._registry, selected._account)

def refresh(selection):
    if not isinstance(selection, Selection):
        fail('selection_invalid')
    current = (resolve_custody_selection(selection.account_id, selection.credential, config=selection.config)
               if selection.origin is None else resolve_selection(selection.account_id, selection.credential, selection.origin, config=selection.config))
    if current != selection:
        fail('selection_changed')
    return current

def resolve_pair(account, credential, tab_origin, field_origin, *, config=None):
    outer = resolve_selection(account, credential, tab_origin, config=config)
    inner = resolve_selection(account, credential, field_origin, config=config)
    if outer._registry != inner._registry or outer._account != inner._account:
        fail('selection_changed')
    return inner

def _binding(selection):
    purpose = 'totp' if selection.credential in ('totp', 'totp_code', 'totp_enrollment_code') else selection.credential
    return {'version': 1, 'namespace': selection._registry['namespace'], 'account': selection.account_id,
            'purpose': purpose, 'reference': selection.reference}

def lock_path(selection):
    return state_root() / 'credentials' / selection._registry['namespace'] / (selection.reference + '.lock')

@contextmanager
def store_for(selection):
    from .storage import WindowsStore, SecretServiceStore
    if sys.platform == 'win32':
        store = WindowsStore(selection)
    elif sys.platform.startswith('linux'):
        store = SecretServiceStore(selection)
    else:
        fail('platform_unsupported')
    try:
        yield store
    finally:
        store.close()

def read_payload(selection, store):
    refresh(selection)
    raw = store.read()
    if raw is None:
        return None
    try:
        if len(raw) > 32768:
            fail('credential_envelope_invalid')
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {'binding', 'value'} or data['binding'] != _binding(selection):
            fail('custody_binding_mismatch')
        refresh(selection)
        return data['value']
    except CustodyError:
        raise
    except Exception:
        fail('credential_envelope_invalid')

def write_payload(selection, value, store, *, replace=False):
    refresh(selection)
    raw = json.dumps({'binding': _binding(selection), 'value': value}, separators=(',', ':')).encode()
    if len(raw) > 32768:
        fail('credential_envelope_invalid')
    store.write(raw, replace=replace)

def read_value(selection, *, _store=None):
    current = refresh(selection)
    if current.credential in ('mailbox', 'username'):
        return text(current._account['identity'][current.credential])
    if current.credential in ('totp', 'totp_code', 'totp_enrollment_code'):
        fail('credential_selector_invalid')
    if _store is not None:
        value = read_payload(current, _store)
    else:
        with store_for(current) as store:
            value = read_payload(current, store)
    if value is None:
        fail('credentials_not_provisioned')
    return text(value)

def intake(selection, *, _store=None, _reader=None, _stdin=None, _stderr=None):
    """Hidden console intake. Dependency injection is for synthetic unit tests."""
    refresh(selection)
    if selection.credential not in ('password', 'security_answer'):
        fail('credential_selector_invalid')
    input_stream = sys.stdin if _stdin is None else _stdin
    output_stream = sys.stderr if _stderr is None else _stderr
    if not input_stream.isatty() or not output_stream.isatty():
        fail('interactive_console_required')
    @contextmanager
    def chosen_store():
        if _store is not None:
            yield _store
        else:
            with store_for(selection) as store:
                yield store
    value = None
    try:
        with locked(lock_path(selection)), chosen_store() as store:
            if read_payload(selection, store) is not None:
                fail('credentials_already_provisioned')
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter('error', getpass.GetPassWarning)
                    value = (_reader or getpass.getpass)('Existing credential (hidden): ', stream=output_stream)
            except (EOFError, KeyboardInterrupt):
                return {'status': 'cancelled'}
            except getpass.GetPassWarning:
                fail('interactive_console_required')
            text(value)
            write_payload(selection, value, store)
            if not secrets.compare_digest(read_payload(selection, store).encode(), value.encode()):
                fail('credential_readback_mismatch')
        return {'status': 'stored_unverified', 'account': selection.account_id, 'credential': selection.credential,
                'provider_contacted': False, 'credential_values_emitted': False}
    except CustodyError:
        raise
    except Exception:
        fail('credential_intake_failed')
    finally:
        value = None
