"""Names the process reads from the environment. Secrets stay out of the repo."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """Fill unset variables from a local .env file. Existing process values win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    auth0_domain: str
    auth0_client_id: str
    auth0_client_secret: str
    auth0_audience: str
    auth0_callback_url: str
    data_encryption_key: str
    azure_endpoint: str
    azure_api_key: str
    azure_api_version: str
    azure_assistant_deployment: str
    azure_checker_deployment: str
    gemini_api_key: str
    gemini_model: str
    ollama_base_url: str
    ollama_model: str
    cosmos_endpoint: str
    cosmos_key: str
    cosmos_database: str
    cosmos_container: str
    port: int

    @classmethod
    def from_env(cls) -> Settings:
        port = int(os.environ.get("PORT", "8765") or "8765")
        callback = os.environ.get("AUTH0_CALLBACK_URL", "").strip()
        if not callback:
            callback = f"http://127.0.0.1:{port}/callback"
        return cls(
            auth0_domain=os.environ.get("AUTH0_DOMAIN", "").strip(),
            auth0_client_id=os.environ.get("AUTH0_CLIENT_ID", "").strip(),
            auth0_client_secret=os.environ.get("AUTH0_CLIENT_SECRET", "").strip(),
            auth0_audience=os.environ.get("AUTH0_AUDIENCE", "").strip(),
            auth0_callback_url=callback,
            data_encryption_key=os.environ.get("DATA_ENCRYPTION_KEY", "").strip(),
            azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip(),
            azure_api_key=os.environ.get("AZURE_OPENAI_API_KEY", "").strip(),
            azure_api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "").strip() or "2024-10-21",
            azure_assistant_deployment=os.environ.get("AZURE_OPENAI_ASSISTANT_DEPLOYMENT", "").strip(),
            azure_checker_deployment=os.environ.get("AZURE_OPENAI_CHECKER_DEPLOYMENT", "").strip(),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
            gemini_model=os.environ.get("GEMINI_MODEL", "").strip() or "gemini-2.5-flash",
            ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "").strip() or "http://127.0.0.1:11434",
            ollama_model=os.environ.get("OLLAMA_MODEL", "").strip(),
            cosmos_endpoint=os.environ.get("COSMOS_ENDPOINT", "").strip(),
            cosmos_key=os.environ.get("COSMOS_KEY", "").strip(),
            cosmos_database=os.environ.get("COSMOS_DATABASE", "").strip(),
            cosmos_container=os.environ.get("COSMOS_CONTAINER", "").strip(),
            port=port,
        )

    def missing_auth(self) -> list[str]:
        missing = []
        if not self.auth0_domain:
            missing.append("AUTH0_DOMAIN")
        if not self.auth0_client_id:
            missing.append("AUTH0_CLIENT_ID")
        if not self.auth0_client_secret:
            missing.append("AUTH0_CLIENT_SECRET")
        return missing

    @property
    def chat_configured(self) -> bool:
        if self.ollama_model or self.gemini_api_key:
            return True
        return bool(
            self.azure_endpoint
            and self.azure_api_key
            and self.azure_assistant_deployment
            and self.azure_checker_deployment
        )

    @property
    def cosmos_configured(self) -> bool:
        return bool(
            self.cosmos_endpoint and self.cosmos_key and self.cosmos_database and self.cosmos_container
        )
