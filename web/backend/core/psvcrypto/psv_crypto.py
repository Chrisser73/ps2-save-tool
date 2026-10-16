from __future__ import annotations

import hashlib
import hmac

PS1_AES_KEY = bytes.fromhex("AB5ABC9FC1F49DE6A051DBAEFA518859")
PS2_AES_KEY = bytes.fromhex("EA02CEEF5BB4D2998F611910D77F51C6")
PSV_AES_IV = bytes.fromhex("B30FFEEDB7DC5EB7133DA60D1B6B2CDC")
PSV_DEFAULT_SEED = b"www.bucanero.com.ar\0"  # 20 bytes including C-string terminator

SEED_OFFSET = 0x08
SIGNATURE_OFFSET = 0x1C
SIGNATURE_SIZE = 20


def _crypto_backend():
    try:
        from Crypto.Cipher import AES  # type: ignore
        return ("pycryptodome", AES)
    except ImportError:
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError as exc:
            raise RuntimeError(
                "PSV signing needs either 'cryptography' or 'pycryptodome'. "
                "Run: pip install -r requirements.txt"
            ) from exc
        return ("cryptography", (Cipher, algorithms, modes))


def _aes_cbc_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    if len(data) % 16:
        raise ValueError("AES-CBC input must be a multiple of 16 bytes")
    backend, api = _crypto_backend()
    if backend == "pycryptodome":
        return api.new(key, api.MODE_CBC, iv).decrypt(data)
    Cipher, algorithms, modes = api
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return dec.update(data) + dec.finalize()


def _aes_ecb(data: bytes, key: bytes, *, decrypt: bool) -> bytes:
    if len(data) % 16:
        raise ValueError("AES-ECB input must be a multiple of 16 bytes")
    backend, api = _crypto_backend()
    if backend == "pycryptodome":
        cipher = api.new(key, api.MODE_ECB)
        return cipher.decrypt(data) if decrypt else cipher.encrypt(data)
    Cipher, algorithms, modes = api
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    ctx = cipher.decryptor() if decrypt else cipher.encryptor()
    return ctx.update(data) + ctx.finalize()


def _derive_ps2_salt(seed: bytes) -> bytearray:
    block = bytearray(64)
    block[:20] = seed
    salt = bytearray(_aes_cbc_decrypt(bytes(block), PS2_AES_KEY, PSV_AES_IV))
    salt[20:] = b"\0" * 44
    return salt


def _derive_ps1_salt(seed: bytes) -> bytearray:
    if len(seed) != 20:
        raise ValueError("PSV/VMP seed must contain exactly 20 bytes")
    first = seed[:16]
    salt = bytearray(64)
    salt[:16] = _aes_ecb(first, PS1_AES_KEY, decrypt=True)
    salt[16:32] = _aes_ecb(first, PS1_AES_KEY, decrypt=False)
    for i in range(16):
        salt[i] ^= PSV_AES_IV[i]

    work = bytearray(b"\xFF" * 20)
    work[:4] = seed[16:20]
    for i in range(16):
        salt[16 + i] ^= work[i]
    # The PS1 PSV/VMP algorithm retains 20 derived bytes and clears the rest.
    salt[20:] = b"\0" * 44
    return salt


def _calculate_signature(
    data: bytes,
    *,
    save_type: int,
    seed_offset: int = SEED_OFFSET,
    signature_offset: int = SIGNATURE_OFFSET,
) -> bytes:
    if len(data) < max(seed_offset + 20, signature_offset + SIGNATURE_SIZE):
        raise ValueError("Signed save data is too short")
    seed = data[seed_offset : seed_offset + 20]
    salt = _derive_ps1_salt(seed) if save_type == 1 else _derive_ps2_salt(seed)

    for i in range(64):
        salt[i] ^= 0x36

    unsigned = bytearray(data)
    unsigned[signature_offset : signature_offset + SIGNATURE_SIZE] = b"\0" * SIGNATURE_SIZE
    inner = hashlib.sha1(bytes(salt) + bytes(unsigned)).digest()

    for i in range(64):
        salt[i] ^= 0x6A
    return hashlib.sha1(bytes(salt) + inner).digest()


def calculate_ps1_signature(data: bytes) -> bytes:
    return _calculate_signature(data, save_type=1)


def calculate_ps2_signature(data: bytes) -> bytes:
    return _calculate_signature(data, save_type=2)


def _verify(data: bytes, *, save_type: int, seed_offset: int, signature_offset: int) -> bool:
    if len(data) < signature_offset + SIGNATURE_SIZE:
        return False
    stored = data[signature_offset : signature_offset + SIGNATURE_SIZE]
    try:
        expected = _calculate_signature(
            data,
            save_type=save_type,
            seed_offset=seed_offset,
            signature_offset=signature_offset,
        )
    except (ValueError, RuntimeError):
        return False
    return hmac.compare_digest(stored, expected)


def verify_ps1_signature(data: bytes) -> bool:
    return _verify(data, save_type=1, seed_offset=SEED_OFFSET, signature_offset=SIGNATURE_OFFSET)


def verify_ps2_signature(data: bytes) -> bool:
    return _verify(data, save_type=2, seed_offset=SEED_OFFSET, signature_offset=SIGNATURE_OFFSET)


def _sign(data: bytes, *, save_type: int, seed_offset: int, signature_offset: int) -> bytes:
    out = bytearray(data)
    out[signature_offset : signature_offset + SIGNATURE_SIZE] = b"\0" * SIGNATURE_SIZE
    signature = _calculate_signature(
        bytes(out),
        save_type=save_type,
        seed_offset=seed_offset,
        signature_offset=signature_offset,
    )
    out[signature_offset : signature_offset + SIGNATURE_SIZE] = signature
    return bytes(out)


def sign_ps1_psv(data: bytes) -> bytes:
    return _sign(data, save_type=1, seed_offset=SEED_OFFSET, signature_offset=SIGNATURE_OFFSET)


def sign_ps2_psv(data: bytes) -> bytes:
    return _sign(data, save_type=2, seed_offset=SEED_OFFSET, signature_offset=SIGNATURE_OFFSET)


def calculate_vmp_signature(data: bytes) -> bytes:
    return _calculate_signature(data, save_type=1, seed_offset=0x0C, signature_offset=0x20)


def verify_vmp_signature(data: bytes) -> bool:
    return _verify(data, save_type=1, seed_offset=0x0C, signature_offset=0x20)


def sign_vmp(data: bytes) -> bytes:
    return _sign(data, save_type=1, seed_offset=0x0C, signature_offset=0x20)
