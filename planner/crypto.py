"""Encrypt a planner document with a per-user key wrapped by a local master key."""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from planner.models import StoreError


def load_master_key(encoded: str, root: Path) -> bytes:
    """Use DATA_ENCRYPTION_KEY when it is set, otherwise a key kept under userdata/."""
    if encoded.strip():
        try:
            key = base64.b64decode(encoded.strip(), validate=True)
        except ValueError as exc:
            raise StoreError("DATA_ENCRYPTION_KEY must be base64") from exc
        if len(key) != 32:
            raise StoreError("DATA_ENCRYPTION_KEY must decode to 32 bytes")
        return key
    path = root / "master.key"
    if path.exists():
        key = path.read_bytes()
        if len(key) != 32:
            raise StoreError("userdata/master.key must be 32 bytes")
        return key
    root.mkdir(parents=True, exist_ok=True)
    key = AESGCM.generate_key(bit_length=256)
    path.write_bytes(key)
    return key


def generate_dek() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def seal(master_key: bytes, dek: bytes, user_id: str, payload: bytes) -> dict:
    """Return the Cosmos item that stores ciphertext for one user."""
    wrapped, dek_nonce = _encrypt(master_key, dek, _aad(user_id))
    ciphertext, nonce = _encrypt(dek, payload, _aad(user_id))
    return {
        "id": user_id,
        "userId": user_id,
        "wrappedDek": wrapped,
        "dekNonce": dek_nonce,
        "nonce": nonce,
        "ciphertext": ciphertext,
    }


def seal_local(master_key: bytes, aad: bytes, payload: bytes) -> dict:
    """Encrypt a small local blob with the master key."""
    ciphertext, nonce = _encrypt(master_key, payload, aad)
    return {"nonce": nonce, "ciphertext": ciphertext}


def open_local(master_key: bytes, aad: bytes, envelope: dict) -> bytes:
    try:
        return _decrypt(master_key, envelope["ciphertext"], envelope["nonce"], aad)
    except (KeyError, InvalidTag, ValueError) as exc:
        raise StoreError("Encrypted item could not be opened") from exc


def open_payload(master_key: bytes, user_id: str, envelope: dict) -> tuple[bytes, bytes]:
    if envelope.get("userId") != user_id or envelope.get("id") != user_id:
        raise StoreError("Encrypted item is for a different user")
    try:
        dek = _decrypt(master_key, envelope["wrappedDek"], envelope["dekNonce"], _aad(user_id))
        payload = _decrypt(dek, envelope["ciphertext"], envelope["nonce"], _aad(user_id))
    except (KeyError, InvalidTag, ValueError) as exc:
        raise StoreError("Encrypted item could not be opened") from exc
    return payload, dek


def _aad(user_id: str) -> bytes:
    return user_id.encode("utf-8")


def _encrypt(key: bytes, plaintext: bytes, aad: bytes) -> tuple[str, str]:
    nonce = os.urandom(12)
    blob = AESGCM(key).encrypt(nonce, plaintext, aad)
    return _b64(blob), _b64(nonce)


def _decrypt(key: bytes, blob: str, nonce: str, aad: bytes) -> bytes:
    return AESGCM(key).decrypt(_unb64(nonce), _unb64(blob), aad)


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value, validate=True)
