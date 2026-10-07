"""OS credential stores with no plaintext fallback or provider auto-selection."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import json
import os
import secrets
import sys
from .custody import CustodyError, _binding, checked_path, private_directory, state_root, fail

class _DataBlob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

def _blob(value):
    buffer = ctypes.create_string_buffer(value)
    return _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer

class WindowsDpapi:
    """CurrentUser DPAPI, adapted from the original custody owner's narrow API."""
    def __init__(self):
        if sys.platform != 'win32':
            fail('windows_required')
        self.crypt = ctypes.WinDLL('crypt32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.crypt.CryptProtectData.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
        self.crypt.CryptProtectData.restype = wintypes.BOOL
        self.crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
        self.crypt.CryptUnprotectData.restype = wintypes.BOOL
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel.LocalFree.restype = ctypes.c_void_p

    def protect(self, value, *, entropy):
        input_blob, input_buffer = _blob(value)
        entropy_blob, entropy_buffer = _blob(entropy)
        result = _DataBlob()
        if not self.crypt.CryptProtectData(ctypes.byref(input_blob), 'Agent Framework credential',
                ctypes.byref(entropy_blob), None, None, 1, ctypes.byref(result)):
            fail('dpapi_protect_failed')
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            self.kernel.LocalFree(result.pbData)

    def unprotect(self, value, *, entropy):
        input_blob, input_buffer = _blob(value)
        entropy_blob, entropy_buffer = _blob(entropy)
        result = _DataBlob()
        description = wintypes.LPWSTR()
        if not self.crypt.CryptUnprotectData(ctypes.byref(input_blob), ctypes.byref(description),
                ctypes.byref(entropy_blob), None, None, 1, ctypes.byref(result)):
            fail('dpapi_unprotect_failed')
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            if description:
                self.kernel.LocalFree(description)
            self.kernel.LocalFree(result.pbData)

class WindowsStore:
    def __init__(self, selection):
        self.path = checked_path(state_root() / 'credentials' / selection._registry['namespace'] / (selection.reference + '.dpapi'))
        self.entropy = json.dumps(_binding(selection), sort_keys=True, separators=(',', ':')).encode()
        self.dpapi = WindowsDpapi()

    def present(self):
        return checked_path(self.path).is_file()

    def read(self):
        path = checked_path(self.path)
        if not path.exists():
            return None
        if not path.is_file() or path.stat().st_size > 65536:
            fail('credential_envelope_invalid')
        return self.dpapi.unprotect(path.read_bytes(), entropy=self.entropy)

    def write(self, raw, *, replace=False):
        private_directory(self.path.parent)
        checked_path(self.path)
        protected = self.dpapi.protect(raw, entropy=self.entropy)
        temporary = self.path.with_name('.' + self.path.name + '.' + secrets.token_hex(8))
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(protected)
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                checked_path(self.path)
                os.replace(temporary, self.path)
            else:
                try:
                    os.link(temporary, self.path)
                except FileExistsError:
                    fail('credentials_already_provisioned')
        finally:
            temporary.unlink(missing_ok=True)

    def delete(self):
        """Internal exact-reference removal, used by synthetic qualification cleanup."""
        checked_path(self.path).unlink(missing_ok=True)

    def close(self):
        pass

class SecretServiceStore:
    def __init__(self, selection):
        self.connection = None
        try:
            if not sys.platform.startswith('linux'):
                fail('linux_required')
            import secretstorage
            self.connection = secretstorage.dbus_init()
            from secretstorage.collection import Collection
            # Direct construction intentionally refuses a missing collection;
            # get_default_collection would create/prompts as a hidden side effect.
            self.collection = Collection(self.connection, selection._registry.get(
                'secret_service_collection', '/org/freedesktop/secrets/aliases/default'))
            if self.collection.is_locked():
                fail('secret_service_locked')
            self.attributes = {key: str(value) for key, value in _binding(selection).items()}
            self.attributes['application'] = 'agent-framework'
        except CustodyError:
            self.close()
            raise
        except ImportError:
            self.close()
            fail('secret_service_dependency_missing')
        except Exception:
            self.close()
            fail('secret_service_unavailable')

    def _item(self):
        try:
            if self.collection.is_locked():
                fail('secret_service_locked')
            items = list(self.collection.search_items(self.attributes))
            if len(items) > 1:
                fail('credential_reference_conflict')
            if not items:
                return None
            if items[0].is_locked():
                fail('secret_service_locked')
            if items[0].get_attributes() != self.attributes:
                fail('custody_binding_mismatch')
            return items[0]
        except CustodyError:
            raise
        except Exception:
            fail('secret_service_unavailable')

    def present(self):
        return self._item() is not None

    def read(self):
        try:
            item = self._item()
            return item.get_secret() if item else None
        except CustodyError:
            raise
        except Exception:
            fail('credential_read_failed')

    def write(self, raw, *, replace=False):
        try:
            item = self._item()
            if item and not replace:
                fail('credentials_already_provisioned')
            if item:
                item.set_secret(raw, content_type='application/json')
            else:
                self.collection.create_item('Agent Framework credential', self.attributes, raw,
                                            replace=False, content_type='application/json')
            # Ambiguous creation must be reconciled; never choose one duplicate.
            self._item()
        except CustodyError:
            raise
        except Exception:
            fail('credential_write_uncertain')

    def delete(self):
        item = self._item()
        if item:
            item.delete()

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None
