"""The keyring's secret exchange ("sx-aes-1"), so a password typed in Kūhaku's window reaches
gnome-keyring encrypted, as it does from GNOME's own prompter.

Each side makes a Diffie-Hellman key pair (the 1536-bit IETF IKE group), the shared secret
becomes a 16-byte key through HKDF-SHA256, and the password travels AES-128-CBC encrypted
with PKCS#7 padding. Messages are small key files:

    [sx-aes-1]
    public=<base64>
    secret=<base64>
    iv=<base64>

AES comes from the system's libcrypto (OpenSSL), through ctypes.
"""

import base64
import ctypes
import ctypes.util
import hashlib
import hmac
import secrets

from kuhaku_log import report  # faults go to the log instead of vanishing

PROTOCOL = "sx-aes-1"

# RFC 3526, 1536-bit MODP group ("ietf-ike-grp-modp-1536" in gcr), generator 2.
PRIME = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA237327FFFFFFFFFFFFFFFF",
    16,
)
BASE = 2
PRIME_BYTES = (PRIME.bit_length() + 7) // 8


def _int_bytes(n, size=0):
    raw = n.to_bytes(max(1, (n.bit_length() + 7) // 8), "big")
    return raw.rjust(size, b"\0")


def _hkdf(ikm, length=16):
    prk = hmac.new(b"\0" * 32, ikm, hashlib.sha256).digest()  # no salt: a hash's length of zeros
    out, block, i = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + bytes([i]), hashlib.sha256).digest()
        out += block
        i += 1
    return out[:length]


def _parse(text):
    """The [sx-aes-1] group of an exchange message, as a dict."""
    values, group = {}, None
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("["):
            group = line.strip("[]")
        elif "=" in line and group == PROTOCOL:
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    return values


_crypto = None


def _libcrypto():
    global _crypto
    if _crypto is None:
        lib = ctypes.CDLL(ctypes.util.find_library("crypto") or "libcrypto.so.3")
        lib.EVP_CIPHER_CTX_new.restype = ctypes.c_void_p
        lib.EVP_CIPHER_CTX_free.argtypes = [ctypes.c_void_p]
        lib.EVP_aes_128_cbc.restype = ctypes.c_void_p
        for name in ("EVP_EncryptInit_ex", "EVP_DecryptInit_ex"):
            getattr(lib, name).argtypes = [ctypes.c_void_p] * 3 + [ctypes.c_char_p] * 2
        lib.EVP_CIPHER_CTX_set_padding.argtypes = [ctypes.c_void_p, ctypes.c_int]
        for name in ("EVP_EncryptUpdate", "EVP_DecryptUpdate"):
            getattr(lib, name).argtypes = [
                ctypes.c_void_p,
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_int),
                ctypes.c_char_p,
                ctypes.c_int,
            ]
        _crypto = lib
    return _crypto


def _aes_cbc(key, iv, data, encrypt):
    """AES-128-CBC over whole blocks (the padding is done here, not by OpenSSL)."""
    lib = _libcrypto()
    ctx = lib.EVP_CIPHER_CTX_new()
    if not ctx:
        raise RuntimeError("libcrypto: no cipher context")
    try:
        init = lib.EVP_EncryptInit_ex if encrypt else lib.EVP_DecryptInit_ex
        update = lib.EVP_EncryptUpdate if encrypt else lib.EVP_DecryptUpdate
        if init(ctx, lib.EVP_aes_128_cbc(), None, key, iv) != 1:
            raise RuntimeError("libcrypto: cipher init failed")
        lib.EVP_CIPHER_CTX_set_padding(ctx, 0)
        out = ctypes.create_string_buffer(len(data) + 16)
        n = ctypes.c_int(0)
        if update(ctx, out, ctypes.byref(n), data, len(data)) != 1 or n.value != len(data):
            raise RuntimeError("libcrypto: cipher failed")
        return out.raw[: n.value]
    finally:
        lib.EVP_CIPHER_CTX_free(ctx)


def available():
    """Whether AES can be done here (libcrypto loads)."""
    try:
        _libcrypto()
        return True
    except Exception:
        report()
        return False


class Exchange:
    """One side of a secret exchange: ours, the prompter's."""

    def __init__(self):
        self.private = secrets.randbits(PRIME.bit_length() - 1) | 1
        self.public = pow(BASE, self.private, PRIME)
        self.key = None

    def _public_b64(self):
        return base64.b64encode(_int_bytes(self.public)).decode()

    def begin(self):
        """The first message: our public key."""
        return f"[{PROTOCOL}]\npublic={self._public_b64()}\n"

    def receive(self, text):
        """Take the other side's message; derive the shared key. Returns any secret it carries."""
        values = _parse(text)
        if "public" not in values:
            raise ValueError("exchange has no public key")
        peer = int.from_bytes(base64.b64decode(values["public"]), "big")
        if not 1 < peer < PRIME - 1:
            raise ValueError("exchange public key out of range")
        self.key = _hkdf(_int_bytes(pow(peer, self.private, PRIME), PRIME_BYTES))
        if "secret" in values and "iv" in values:
            padded = _aes_cbc(self.key, base64.b64decode(values["iv"]), base64.b64decode(values["secret"]), False)
            pad = padded[-1] if padded else 0
            if not 1 <= pad <= 16 or padded[-pad:] != bytes([pad]) * pad:
                raise ValueError("exchange secret badly padded")
            return padded[:-pad].decode()
        return None

    def send(self, secret=None):
        """Our next message, carrying `secret` encrypted when given (receive() must come first)."""
        text = f"[{PROTOCOL}]\npublic={self._public_b64()}\n"
        if secret is None:
            return text
        if self.key is None:
            raise ValueError("no shared key yet")
        data = secret.encode()
        pad = 16 - len(data) % 16
        iv = secrets.token_bytes(16)
        enc = _aes_cbc(self.key, iv, data + bytes([pad]) * pad, True)
        return text + f"secret={base64.b64encode(enc).decode()}\niv={base64.b64encode(iv).decode()}\n"
