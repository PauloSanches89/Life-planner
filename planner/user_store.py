"""Per-user document repository. Local files now, the same item in Cosmos DB later."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Protocol

from planner.clock import Clock
from planner.crypto import generate_dek, open_payload, seal
from planner.models import (
    STORE_VERSION,
    Document,
    Profile,
    StoreError,
    document_from_dict,
    document_to_dict,
    require_aware,
)
from planner.settings import Settings


class UserRepository(Protocol):
    def load(self) -> Document:
        """Return this user's document, creating it on first sign-in."""

    def save(self, document: Document) -> None:
        """Persist the document as ciphertext bound to the user id."""


class LocalEncryptedStore:
    def __init__(self, root: Path, profile: Profile, master_key: bytes, clock: Clock) -> None:
        self.root = Path(root)
        self.profile = profile
        self._master_key = master_key
        self._clock = clock
        digest = hashlib.sha256(profile.id.encode("utf-8")).hexdigest()
        self.path = self.root / "users" / f"{digest}.json"
        self._dek: bytes | None = None

    def load(self) -> Document:
        if not self.path.exists():
            document = self._new_document()
            self.save(document)
            return document
        payload, self._dek = open_payload(self._master_key, self.profile.id, self._read_envelope())
        try:
            return document_from_dict(json.loads(payload.decode("utf-8")))
        except json.JSONDecodeError as exc:
            raise StoreError(f"Decrypted item is not valid JSON: {self.path}") from exc

    def save(self, document: Document) -> None:
        if document.profile.id != self.profile.id:
            raise StoreError("Refusing to save a document for a different user")
        dek = self._dek if self._dek is not None else self._dek_from_disk()
        if dek is None:
            dek = generate_dek()
        payload = json.dumps(document_to_dict(document), ensure_ascii=False).encode("utf-8")
        envelope = seal(self._master_key, dek, self.profile.id, payload)
        self._write(envelope)
        self._dek = dek

    def _dek_from_disk(self) -> bytes | None:
        if not self.path.exists():
            return None
        _payload, dek = open_payload(self._master_key, self.profile.id, self._read_envelope())
        return dek

    def _read_envelope(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StoreError(f"Store file is not valid JSON: {self.path}") from exc
        if not isinstance(raw, dict):
            raise StoreError("Encrypted item must be an object")
        return raw

    def _write(self, envelope: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, self.path)

    def _new_document(self) -> Document:
        now = require_aware(self._clock.now())
        return Document(
            version=STORE_VERSION,
            profile=self.profile,
            selected_day=now.date(),
            tasks=[],
            summary="",
        )


class CosmosUserStore:
    """Same encrypted item as the local file, addressed by /userId."""

    def __init__(
        self,
        profile: Profile,
        master_key: bytes,
        clock: Clock,
        endpoint: str,
        key: str,
        database: str,
        container: str,
    ) -> None:
        self.profile = profile
        self._master_key = master_key
        self._clock = clock
        self._dek: bytes | None = None
        self._container = _cosmos_container(endpoint, key, database, container)

    def load(self) -> Document:
        envelope = self._read()
        if envelope is None:
            document = LocalEncryptedStore._new_document(self)
            self.save(document)
            return document
        payload, self._dek = open_payload(self._master_key, self.profile.id, envelope)
        try:
            return document_from_dict(json.loads(payload.decode("utf-8")))
        except json.JSONDecodeError as exc:
            raise StoreError("Decrypted Cosmos item is not valid JSON") from exc

    def save(self, document: Document) -> None:
        if document.profile.id != self.profile.id:
            raise StoreError("Refusing to save a document for a different user")
        dek = self._dek
        if dek is None:
            existing = self._read()
            if existing is not None:
                _payload, dek = open_payload(self._master_key, self.profile.id, existing)
        if dek is None:
            dek = generate_dek()
        payload = json.dumps(document_to_dict(document), ensure_ascii=False).encode("utf-8")
        self._container.upsert_item(seal(self._master_key, dek, self.profile.id, payload))
        self._dek = dek

    def _read(self) -> dict | None:
        try:
            item = self._container.read_item(item=self.profile.id, partition_key=self.profile.id)
        except Exception as exc:
            name = exc.__class__.__name__
            if name == "CosmosResourceNotFoundError":
                return None
            raise
        return dict(item)


def open_user_store(
    root: Path,
    profile: Profile,
    master_key: bytes,
    clock: Clock,
    settings: Settings,
) -> UserRepository:
    if settings.cosmos_configured:
        return CosmosUserStore(
            profile,
            master_key,
            clock,
            settings.cosmos_endpoint,
            settings.cosmos_key,
            settings.cosmos_database,
            settings.cosmos_container,
        )
    return LocalEncryptedStore(root, profile, master_key, clock)


def _cosmos_container(endpoint: str, key: str, database: str, container: str):
    try:
        from azure.cosmos import CosmosClient
    except ImportError as exc:
        raise StoreError("Install the cloud extra to use Cosmos DB: pip install -e \".[cloud]\"") from exc
    client = CosmosClient(endpoint, credential=key)
    return client.get_database_client(database).get_container_client(container)


def auth_profile(sub: str, name: str, picture: str | None) -> Profile:
    display = name.strip() or sub
    return Profile(id=sub, display_name=display, avatar_url=picture or None, provider="auth0")
