"""Manage account metadata and hidden credential intake; never print a secret."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from repo_platform.credentials import custody
from repo_platform.tooling.tool_handshake import (tool_usage_payload, tool_result_payload, argparse_command_details, format_usage_text, format_result_text)

USAGE = {
    'id': 'agent_credentials',
    'purpose': 'One private account registry and OS-protected credential custody for supported handoffs.',
    'commands': {
        'usage': 'Offline interface and safety summary; does not open configuration.',
        'init': 'Create an empty private registry; never replace an existing registry.',
        'add': 'Add account metadata and fresh credential references; creates no provider account or secret.',
        'status': 'Read metadata only; never decrypts custody or proves live sign-in.',
        'storage-status': 'Probe the selected OS storage and collection without reading any credential.',
        'custody-status': 'Inspect exact credential presence without reading its plaintext; distinguishes storage failures.',
        'intake': 'Store an existing credential from hidden interactive console input only, absent custody only.'
    },
    'selectors': {'--config': 'Explicit registry path, otherwise AGENT_FRAMEWORK_CONFIG_HOME/accounts.json or OS config home.',
                  '--account': 'Exact user-chosen canonical account ID.',
                  '--origin': 'Exact canonical HTTPS origin. Repeat for add.',
                  '--credential': 'password, security_answer or totp for add; password/security_answer for intake.',
                  '--format': 'text or json; both are metadata-only.'},
    'state': 'AGENT_FRAMEWORK_STATE_HOME, otherwise OS state home/agent-framework (Windows LocalAppData/AgentFramework).',
    'security': ['Windows CurrentUser DPAPI; Linux unlocked Secret Service; no plaintext fallback.',
                 'Intake refuses redirected streams and echo fallback. No secret arguments, environment input, export or reveal.',
                 'Origin registration grants delivery scope only, not authorization for account use.',
                 'On an uncertain write, inspect safe status and provider state before retrying; never overwrite custody.'],
    'examples': ['python scripts/tools/agent_credentials.py init --config /home/example/.config/agent-framework/accounts.json',
                 'python scripts/tools/agent_credentials.py add --account example --origin https://example.test --credential password',
                 'python scripts/tools/agent_credentials.py intake --account example --origin https://example.test --credential password']
}

def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest='command', required=True)
    for name in USAGE['commands']:
        p = commands.add_parser(name, help=USAGE['commands'][name])
        p.add_argument('--format', choices=('text', 'json'), default='text')
        p.add_argument('--config', type=Path)
        if name in ('add', 'intake'):
            p.add_argument('--account', required=True)
            p.add_argument('--origin', action='append' if name == 'add' else 'store', required=True)
            p.add_argument('--credential', choices=sorted(custody.PURPOSES) if name == 'add' else ('password', 'security_answer'),
                           action='append' if name == 'add' else 'store', required=True)
        if name == 'custody-status':
            p.add_argument('--account', required=True)
            p.add_argument('--credential', choices=sorted(custody.PURPOSES), required=True)
        if name == 'status':
            p.add_argument('--account')
        if name == 'add':
            p.add_argument('--mailbox')
            p.add_argument('--username')
            p.add_argument('--federated', action='store_true')
            p.add_argument('--security-answer-bound', action='store_true')
    return result

def perform(args):
    path = args.config or custody.default_config()
    if args.command == 'usage':
        return usage_payload()
    if args.command == 'init':
        data = {'version': 1, 'namespace': str(uuid.uuid4()), 'accounts': []}
        custody.write_registry(path, data, create=True)
        return {'status': 'initialized', 'config': str(path), 'accounts': 0}
    if args.command == 'add':
        with custody.locked(path.with_suffix('.lock')):
            path, data = custody.load_registry(path)
            if any(row['id'] == args.account for row in data['accounts']):
                custody.fail('account_exists')
            row = {'id': args.account, 'status': 'active', 'allowed_origins': args.origin,
                   'identity': {k: getattr(args, k) for k in ('mailbox', 'username') if getattr(args, k)},
                   'credentials': {key: str(uuid.uuid4()) for key in args.credential}}
            if args.federated:
                row['federated'] = True
            if args.security_answer_bound:
                row['security_answer_bound'] = True
            data['accounts'].append(row)
            custody.write_registry(path, data)
        return {'status': 'metadata_added', 'account': args.account, 'provider_contacted': False}
    if args.command == 'custody-status':
        selection = custody.resolve_custody_selection(args.account, args.credential, config=path)
        with custody.store_for(selection) as store:
            present = store.present()
        return {'status': 'present_unverified' if present else 'absent', 'account': args.account,
                'credential': args.credential, 'credential_values_read': False, 'provider_contacted': False}
    if args.command == 'intake':
        selected = custody.resolve_selection(args.account, args.credential, args.origin, config=path)
        return custody.intake(selected)
    path, data = custody.load_registry(path)
    if args.command == 'status':
        rows = data['accounts']
        if args.account:
            rows = [row for row in rows if row['id'] == args.account]
            if not rows:
                custody.fail('account_not_found')
        return {'status': 'metadata_only', 'accounts': [{'account': row['id'], 'status': row['status'],
                  'allowed_origins': row['allowed_origins'], 'declared_credentials': sorted(row['credentials']),
                  'declared_identity_fields': sorted(row.get('identity', {}))} for row in rows],
                'custody_opened': False, 'provider_contacted': False}
    if args.command == 'storage-status':
        if sys.platform == 'win32':
            from repo_platform.credentials.storage import WindowsDpapi
            WindowsDpapi()
            return {'status': 'available', 'backend': 'windows_current_user_dpapi', 'credentials_read': False}
        if not sys.platform.startswith('linux'):
            custody.fail('platform_unsupported')
        from repo_platform.credentials.storage import SecretServiceStore
        # Probe the same exact collection with a synthetic non-item identity;
        # constructor neither searches nor reads nor creates items.
        from types import SimpleNamespace
        selected = SimpleNamespace(_registry=data, account_id='storage_probe', credential='password', reference=str(uuid.uuid4()))
        store = SecretServiceStore(selected)
        store.close()
        return {'status': 'available', 'backend': 'secret_service', 'credentials_read': False}
    custody.fail('request_invalid')

def usage_payload():
    readonly = {'usage', 'status', 'storage-status', 'custody-status'}
    return tool_usage_payload(
        tool_id='agent_credentials', purpose=USAGE['purpose'], entrypoint='python scripts/tools/agent_credentials.py',
        safe_first_calls=[{'label': 'Offline usage', 'cmd': 'python scripts/tools/agent_credentials.py usage --format json'}],
        verbs=[{'name': name, 'side_effects': 'read-only' if name in readonly else 'local-write', 'summary': meaning}
               for name, meaning in USAGE['commands'].items()],
        selectors=[{'name': name, 'required': False, 'summary': meaning} for name, meaning in USAGE['selectors'].items()],
        output_formats=['text', 'json'], side_effects={'network_access': False, 'writes_repo': False,
            'local_writes': 'init/add modify the private metadata registry; intake creates absent protected custody.'},
        examples=[{'cmd': example} for example in USAGE['examples']], docs=['docs/credentials.md'],
        command_details=argparse_command_details(parser()), extra={'security': USAGE['security'], 'state': USAGE['state']})

def main(argv=None):
    selected_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(selected_argv or ['usage'])
    try:
        data = perform(args)
        result = data if args.command == 'usage' else tool_result_payload(
            tool_id='agent_credentials', verb=args.command, summary=data.get('status', 'Completed'), data=data)
        code = 0
    except custody.CustodyError as error:
        result = tool_result_payload(tool_id='agent_credentials', verb=args.command,
            summary='Credential operation did not complete.', status='error',
            errors=[{'code': error.code, 'message': 'Inspect safe account and storage status before retrying.'}])
        code = 2
    except Exception:
        result = tool_result_payload(tool_id='agent_credentials', verb=args.command,
            summary='Credential operation did not complete.', status='error',
            errors=[{'code': 'credential_operation_failed', 'message': 'No diagnostic values are emitted.'}])
        code = 2
    if args.format == 'json':
        print(json.dumps(result))
    else:
        print(format_usage_text(result) if args.command == 'usage' and code == 0 else format_result_text(result), end='')
        if 'data' in result:
            for key, value in result['data'].items():
                print(f'{key}: {json.dumps(value) if isinstance(value, (dict, list)) else value}')
    return code

if __name__ == '__main__':
    raise SystemExit(main())
