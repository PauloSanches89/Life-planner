"""User boundary. Phase 1 has an anonymous local profile; OAuth can replace it."""

from typing import Protocol

from planner.models import Document, Profile


class ProfileSource(Protocol):
    def load(self) -> Document:
        """Return the current document."""


class Identity(Protocol):
    def current_user(self) -> Profile:
        """Return the profile Phase 2 shows in the header."""


class AnonymousIdentity:
    """Reads the profile stored with the local document."""

    def __init__(self, source: ProfileSource) -> None:
        self._source = source

    def current_user(self) -> Profile:
        return self._source.load().profile
