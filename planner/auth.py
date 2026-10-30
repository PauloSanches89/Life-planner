"""Auth0 Authorization Code flow with PKCE. Accounts are managed at manage.auth0.com."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from planner.settings import Settings


class AuthError(Exception):
    """Auth0 rejected the login or the network call failed."""


def code_verifier() -> str:
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def authorize_url(settings: Settings, state: str, verifier: str) -> str:
    query = {
        "response_type": "code",
        "client_id": settings.auth0_client_id,
        "redirect_uri": settings.auth0_callback_url,
        "scope": "openid profile email",
        "state": state,
        "code_challenge": code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    if settings.auth0_audience:
        query["audience"] = settings.auth0_audience
    return f"https://{_host(settings.auth0_domain)}/authorize?{urlencode(query)}"


def exchange_profile(settings: Settings, code: str, verifier: str, opener=None) -> dict:
    """Trade the callback code for the Auth0 user. Returns sub, name, and picture."""
    post = opener or urlopen
    host = _host(settings.auth0_domain)
    token_body = urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": settings.auth0_client_id,
            "client_secret": settings.auth0_client_secret,
            "code": code,
            "redirect_uri": settings.auth0_callback_url,
            "code_verifier": verifier,
        }
    ).encode("utf-8")
    token_request = Request(
        f"https://{host}/oauth/token",
        data=token_body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    tokens = _read_json(post, token_request)
    access = tokens.get("access_token")
    if not isinstance(access, str) or not access:
        raise AuthError("Auth0 did not return an access token")
    info_request = Request(
        f"https://{host}/userinfo",
        headers={"Authorization": f"Bearer {access}"},
    )
    profile = _read_json(post, info_request)
    sub = profile.get("sub")
    if not isinstance(sub, str) or not sub:
        raise AuthError("Auth0 user info is missing a subject")
    name = profile.get("name") or profile.get("nickname") or profile.get("email") or sub
    picture = profile.get("picture")
    return {
        "sub": sub,
        "name": name if isinstance(name, str) else sub,
        "picture": picture if isinstance(picture, str) else None,
    }


def _host(domain: str) -> str:
    value = domain.strip().removeprefix("https://").removeprefix("http://").strip("/")
    if not value:
        raise AuthError("AUTH0_DOMAIN is empty")
    return value


def _read_json(opener, request: Request) -> dict:
    try:
        with opener(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AuthError("Auth0 request failed") from exc
    if not isinstance(payload, dict):
        raise AuthError("Auth0 returned an unexpected payload")
    return payload
