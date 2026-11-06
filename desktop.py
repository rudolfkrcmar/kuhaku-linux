"""What differs between Kūhaku and a Mac, for apps whose core runs on both.

Passwords live in the system keyring: libsecret on Kūhaku, the Keychain on a Mac.
Links open in Mado on Kūhaku, in the default browser on a Mac; files in Miru or
their own app on Kūhaku, in their own app on a Mac.
"""
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
MAC = sys.platform == "darwin"


def _spawn(argv):
    subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_url(url):
    _spawn(["open", url] if MAC else [str(HOME / ".local/bin/mado"), url])


def open_file(path, viewable=False):
    """viewable: a PDF, picture, video, sound or text, which Kūhaku's Miru shows."""
    if MAC:
        return _spawn(["open", str(path)])
    miru = HOME / ".local/bin/miru"
    _spawn([str(miru), str(path)] if viewable and miru.exists() else ["xdg-open", str(path)])


# --- passwords ------------------------------------------------------------------------
# service: the app's name in the keyring ("app.kuhaku.Tegami"); key: what the password is for.
if MAC:
    import ctypes
    import ctypes.util

    _sec = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Security"))
    _cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    _vp = ctypes.c_void_p
    _cf.CFStringCreateWithCString.restype = _vp
    _cf.CFStringCreateWithCString.argtypes = [_vp, ctypes.c_char_p, ctypes.c_uint32]
    _cf.CFDataCreate.restype = _vp
    _cf.CFDataCreate.argtypes = [_vp, ctypes.c_char_p, ctypes.c_long]
    _cf.CFDataGetLength.restype = ctypes.c_long
    _cf.CFDataGetLength.argtypes = [_vp]
    _cf.CFDataGetBytePtr.restype = _vp
    _cf.CFDataGetBytePtr.argtypes = [_vp]
    _cf.CFDictionaryCreate.restype = _vp
    _cf.CFDictionaryCreate.argtypes = [_vp, _vp, _vp, ctypes.c_long, _vp, _vp]
    _cf.CFRelease.argtypes = [_vp]
    for _f in ("SecItemAdd", "SecItemCopyMatching", "SecItemDelete"):
        getattr(_sec, _f).restype = ctypes.c_int32
    _sec.SecItemAdd.argtypes = [_vp, _vp]
    _sec.SecItemCopyMatching.argtypes = [_vp, ctypes.POINTER(_vp)]
    _sec.SecItemDelete.argtypes = [_vp]

    def _const(lib, name):
        return _vp.in_dll(lib, name).value

    def _str(s):
        return _cf.CFStringCreateWithCString(None, s.encode(), 0x08000100)   # UTF-8

    def _dict(pairs):
        n = len(pairs)
        keys = (_vp * n)(*[k for k, _ in pairs])
        vals = (_vp * n)(*[v for _, v in pairs])
        return _cf.CFDictionaryCreate(None, keys, vals, n,
                                      ctypes.addressof(_vp.in_dll(_cf, "kCFTypeDictionaryKeyCallBacks")),
                                      ctypes.addressof(_vp.in_dll(_cf, "kCFTypeDictionaryValueCallBacks")))

    def _query(service, key, *extra):
        made = [_str(service), _str(key)]
        pairs = [(_const(_sec, "kSecClass"), _const(_sec, "kSecClassGenericPassword")),
                 (_const(_sec, "kSecAttrService"), made[0]), (_const(_sec, "kSecAttrAccount"), made[1]), *extra]
        return _dict(pairs), made

    def _free(*refs):
        for r in refs:
            if r:
                _cf.CFRelease(r)

    def password_lookup(service, key):
        q, made = _query(service, key, (_const(_sec, "kSecReturnData"), _const(_cf, "kCFBooleanTrue")),
                         (_const(_sec, "kSecMatchLimit"), _const(_sec, "kSecMatchLimitOne")))
        out = _vp()
        status = _sec.SecItemCopyMatching(q, ctypes.byref(out))
        _free(q, *made)
        if status != 0 or not out.value:
            return None
        data = ctypes.string_at(_cf.CFDataGetBytePtr(out), _cf.CFDataGetLength(out))
        _free(out.value)
        return data.decode()

    def password_clear(service, key):
        q, made = _query(service, key)
        _sec.SecItemDelete(q)
        _free(q, *made)

    def password_store(service, key, label, pw):
        password_clear(service, key)
        raw = pw.encode()
        data, lab = _cf.CFDataCreate(None, raw, len(raw)), _str(label)
        q, made = _query(service, key, (_const(_sec, "kSecValueData"), data), (_const(_sec, "kSecAttrLabel"), lab))
        status = _sec.SecItemAdd(q, None)
        _free(q, data, lab, *made)
        if status != 0:
            raise RuntimeError(f"The Keychain did not take the password ({status})")
else:
    import gi
    gi.require_version("Secret", "1")
    from gi.repository import Secret

    _schemas = {}

    def _schema(service):
        if service not in _schemas:
            _schemas[service] = Secret.Schema.new(service, Secret.SchemaFlags.NONE,
                                                  {"account": Secret.SchemaAttributeType.STRING})
        return _schemas[service]

    def password_lookup(service, key):
        return Secret.password_lookup_sync(_schema(service), {"account": key}, None)

    def password_store(service, key, label, pw):
        Secret.password_store_sync(_schema(service), {"account": key}, Secret.COLLECTION_DEFAULT, label, pw, None)

    def password_clear(service, key):
        Secret.password_clear_sync(_schema(service), {"account": key}, None)
