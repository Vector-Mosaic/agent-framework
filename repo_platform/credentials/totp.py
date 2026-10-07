"""Account-bound TOTP; active factors and pending enrollment remain distinct."""
from __future__ import annotations
import base64
from contextlib import contextmanager
import hashlib
import hmac
import math
import re
import struct
import time
from urllib.parse import parse_qs, urlsplit
from .custody import (CustodyError, fail, resolve_selection, refresh, locked, lock_path,
                      store_for, read_payload, write_payload)

def parameters(options):
    if not isinstance(options, dict) or set(options) - {'algorithm', 'digits', 'period'}:
        fail('totp_parameters_invalid')
    result = {'algorithm': 'SHA1', 'digits': 6, 'period': 30, **options}
    if (result['algorithm'] not in ('SHA1', 'SHA256', 'SHA512')
            or type(result['digits']) is not int or result['digits'] not in (6, 8)
            or type(result['period']) is not int or not 15 <= result['period'] <= 120):
        fail('totp_parameters_invalid')
    return result


def enrollment(value, options):
    parameters(options)
    if not isinstance(value, str) or not value or len(value) > 2048:
        fail('totp_enrollment_invalid')
    value = value.strip()
    if value.startswith('otpauth://'):
        parsed = urlsplit(value)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.netloc != 'totp' or parsed.fragment or set(query) - {'secret', 'issuer', 'algorithm', 'digits', 'period'}:
            fail('totp_enrollment_invalid')
        if 'secret' not in query or any(len(items) != 1 for items in query.values()):
            fail('totp_enrollment_invalid')
        try:
            encoded_options = {key: int(query[key][0]) if key in ('digits', 'period') else query[key][0].upper()
                               for key in ('algorithm', 'digits', 'period') if key in query}
        except Exception:
            fail('totp_parameters_invalid')
        if any(key in options and options[key] != item for key, item in encoded_options.items()):
            fail('totp_parameters_invalid')
        options = {**encoded_options, **options}
        value = query['secret'][0]
    seed = re.sub(r'\s+', '', value).upper().rstrip('=')
    if not re.fullmatch(r'[A-Z2-7]{16,205}', seed):
        fail('totp_enrollment_invalid')
    try:
        decoded = base64.b32decode(seed + '=' * (-len(seed) % 8))
        if not 10 <= len(decoded) <= 128 or base64.b32encode(decoded).decode().rstrip('=') != seed:
            fail('totp_enrollment_invalid')
    except Exception:
        fail('totp_enrollment_invalid')
    return seed, parameters(options)


def generate(seed, settings, now=None):
    settings = parameters(settings)
    instant = time.time() if now is None else now
    if not isinstance(instant, (int, float)) or isinstance(instant, bool) or not math.isfinite(instant) or instant < 0:
        fail('totp_clock_invalid')
    try:
        key = base64.b32decode(seed + '=' * (-len(seed) % 8))
        counter = struct.pack('>Q', int(instant // settings['period']))
        digest = hmac.new(key, counter, getattr(hashlib, settings['algorithm'].lower())).digest()
        offset = digest[-1] & 15
        number = (struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % (10 ** settings['digits'])
        return str(number).zfill(settings['digits'])
    except Exception:
        fail('totp_enrollment_invalid')



def validate(payload):
    if (not isinstance(payload, dict) or set(payload) != {'seed', 'settings', 'state'}
            or payload['state'] not in ('pending', 'active')):
        fail('totp_custody_invalid')
    seed, settings = enrollment(payload['seed'], payload['settings'])
    if seed != payload['seed'] or settings != payload['settings']:
        fail('totp_custody_invalid')
    return payload


def operate(operation, account, origin, *, config=None, value=None, options=None,
            replace_pending=False, expected=None, _store=None, _now=None):
    selected = resolve_selection(account, 'totp', origin, config=config)
    if expected is not None and (selected._registry != expected._registry
            or selected._account != expected._account or selected.reference != expected.reference
            or selected.origin != expected.origin or selected.config != expected.config):
        fail('selection_changed')
    if type(replace_pending) is not bool:
        fail('request_invalid')
    @contextmanager
    def chosen():
        if _store is not None:
            yield _store
        else:
            with store_for(selected) as store:
                yield store
    try:
        with locked(lock_path(selected)), chosen() as store:
            previous = read_payload(selected, store)
            if previous is not None:
                validate(previous)
            if operation == 'totp_status':
                return {'state': previous['state'] if previous else 'not_configured'}
            if operation == 'capture_totp':
                seed, settings = enrollment(value, {} if options is None else options)
                if previous:
                    if previous['seed'] == seed and previous['settings'] == settings:
                        return {'state': previous['state']}
                    if previous['state'] != 'pending' or not replace_pending:
                        fail('totp_already_configured')
                payload = {'seed': seed, 'settings': settings, 'state': 'pending'}
                write_payload(selected, payload, store, replace=previous is not None)
                if read_payload(selected, store) != payload:
                    fail('credential_readback_mismatch')
                return {'state': 'pending'}
            if previous is None:
                fail('totp_not_configured')
            if operation == 'activate_totp':
                previous['state'] = 'active'
                write_payload(selected, previous, store, replace=True)
                return {'state': 'active'}
            if operation in ('totp_code', 'totp_enrollment_code'):
                required = 'active' if operation == 'totp_code' else 'pending'
                if previous['state'] != required:
                    fail('totp_enrollment_pending' if required == 'active' else 'totp_already_configured')
                refresh(selected)
                return generate(previous['seed'], previous['settings'], _now)
            fail('request_invalid')
    except CustodyError:
        raise
    except Exception:
        fail('totp_custody_failed')
