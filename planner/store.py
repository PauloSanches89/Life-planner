"""Atomic JSON store under a caller-supplied root, defaulting to userdata/."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from planner.clock import Clock
from planner.models import (
    STORE_VERSION,
    Document,
    Profile,
    StoreError,
    document_from_dict,
    document_to_dict,
    require_aware,
)


class Store:
    def __init__(self, root: Path, clock: Clock) -> None:
        self.root = Path(root)
        self.path = self.root / "store.json"
        self._clock = clock

    def load(self) -> Document:
        if not self.path.exists():
            document = self._new_document()
            self.save(document)
            return document
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StoreError(f"Store file is not valid JSON: {self.path}") from exc
        return document_from_dict(raw)

    def save(self, document: Document) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(document_to_dict(document), indent=2, ensure_ascii=False) + "\n"
        temporary = self.path.with_name("store.json.tmp")
        temporary.write_text(payload, encoding="utf-8", newline="\n")
        os.replace(temporary, self.path)

    def _new_document(self) -> Document:
        now = require_aware(self._clock.now())
        return Document(
            version=STORE_VERSION,
            profile=Profile(
                id=str(uuid4()),
                display_name="Local user",
                avatar_url=None,
                provider="anonymous",
            ),
            selected_day=now.date(),
            tasks=[],
        )


def default_store_root() -> Path:
    """Repo-level userdata directory. Callers pass a different root per subject later."""
    return Path(__file__).resolve().parent.parent / "userdata"
