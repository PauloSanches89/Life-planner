import json
from datetime import date
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from planner.assistant import choose_mode, run_turn
from planner.auth import authorize_url, code_challenge
from planner.crypto import load_master_key
from planner.models import StoreError, document_from_dict, document_to_dict
from planner.server import PlannerApp
from planner.service import PlannerService
from planner.settings import Settings
from planner.user_store import LocalEncryptedStore, auth_profile


def blank_settings(**overrides) -> Settings:
    values = dict(
        auth0_domain="",
        auth0_client_id="",
        auth0_client_secret="",
        auth0_audience="",
        auth0_callback_url="http://127.0.0.1:8765/callback",
        data_encryption_key="",
        azure_endpoint="",
        azure_api_key="",
        azure_api_version="2024-10-21",
        azure_assistant_deployment="",
        azure_checker_deployment="",
        maps_key="",
        cosmos_endpoint="",
        cosmos_key="",
        cosmos_database="",
        cosmos_container="",
        port=8765,
    )
    values.update(overrides)
    return Settings(**values)


class Scripted:
    def __init__(self, messages) -> None:
        self.messages = list(messages)

    def complete(self, messages, tools=None):
        return self.messages.pop(0)


def test_version_1_document_upgrades_to_the_cosmos_shape():
    document = document_from_dict(
        {
            "version": 1,
            "profile": {
                "id": "local",
                "display_name": "Local user",
                "avatar_url": None,
                "provider": "anonymous",
            },
            "selected_day": "2026-10-28",
            "tasks": [],
        }
    )

    saved = document_to_dict(document)

    assert document.summary == ""
    assert document.selected_day == date(2026, 10, 28)
    assert saved["version"] == 2
    assert saved["id"] == saved["userId"] == "local"
    assert saved["selectedDay"] == "2026-10-28"
    saved["userId"] = "someone-else"
    with pytest.raises(StoreError):
        document_from_dict(saved)


def test_encrypted_file_hides_the_task_and_reloads_for_that_user(tmp_path, clock):
    key = load_master_key("", tmp_path)
    profile = auth_profile("auth0|ada", "Ada", None)
    store = LocalEncryptedStore(tmp_path, profile, key, clock)
    service = PlannerService(store, clock)
    service.create_task("UniqueShoppingTitleZZZ")
    service.update_task(service.list_tasks_on(date(2026, 10, 28))[0].id, location="Market Street")

    raw = store.path.read_text(encoding="utf-8")
    envelope = json.loads(raw)
    assert "UniqueShoppingTitleZZZ" not in raw
    assert envelope["userId"] == "auth0|ada"
    reloaded = PlannerService(LocalEncryptedStore(tmp_path, profile, key, clock), clock)
    task = reloaded.list_tasks_on(date(2026, 10, 28))[0]
    assert task.title == "UniqueShoppingTitleZZZ"
    assert task.location == "Market Street"

    other = LocalEncryptedStore(tmp_path, auth_profile("auth0|bob", "Bob", None), key, clock)
    assert other.load().tasks == []


def test_choose_mode_follows_the_users_words():
    assert choose_mode("read my tasks for today", "summary") == "detailed"
    assert choose_mode("summarize my day", "detailed") == "summary"
    assert choose_mode("create a new task named go shopping", "summary") == "summary"


def test_checker_blocks_a_delete_the_user_did_not_ask_for(service):
    task = service.create_task("Keep me")
    assistant = Scripted(
        [
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "delete_task",
                            "arguments": json.dumps({"task_id": task.id}),
                        },
                    }
                ],
            },
            {"content": "I left that task alone."},
        ]
    )
    checker = Scripted([{"content": '{"allow": false, "reason": "You asked me to read tasks, not delete one."}'}])

    turn = run_turn(service, "read my tasks for today", "summary", assistant, checker)

    assert turn.mode == "detailed"
    assert turn.reply == "I left that task alone."
    assert service.get_task(task.id).title == "Keep me"


def test_allowed_create_uses_the_requested_name_and_saves_a_summary(service):
    assistant = Scripted(
        [
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "create_task",
                            "arguments": json.dumps({"title": "go shopping"}),
                        },
                    }
                ],
            },
            {"content": "I created go shopping. What should I add to the list?"},
        ]
    )
    checker = Scripted([{"content": '{"allow": true}'}])

    turn = run_turn(service, 'create a new task named "go shopping"', "summary", assistant, checker)

    assert turn.reply == "I created go shopping. What should I add to the list?"
    assert [task.title for task in service.list_tasks_on(date(2026, 10, 28))] == ["go shopping"]
    assert "go shopping" in service.get_summary()


def test_authorize_url_uses_the_auth0_host_and_pkce():
    settings = blank_settings(
        auth0_domain="https://tenant.example/",
        auth0_client_id="client",
        auth0_client_secret="secret",
    )

    url = authorize_url(settings, "state-1", "verifier-1")

    assert url.startswith("https://tenant.example/authorize?")
    assert "client_id=client" in url
    assert "code_challenge_method=S256" in url
    assert code_challenge("verifier-1") in url
    assert "=" not in code_challenge("verifier-1")[-2:]


def test_http_api_keeps_each_signed_in_user_separate(tmp_path, clock):
    app = PlannerApp(
        blank_settings(),
        tmp_path,
        clock,
        load_master_key("", tmp_path),
        Path(__file__).resolve().parent.parent / "ui",
    )
    httpd = app.serve(0)
    port = httpd.server_address[1]
    try:
        status, body = call(port, "GET", "/api/session")
        assert status == 401
        assert "AUTH0_DOMAIN" in body["missing"]

        status, body = call(port, "POST", "/api/rpc", {"name": "create_task", "args": []})
        assert status == 401

        ada = app.sign_in_for_tests("auth0|ada", "Ada")
        bob = app.sign_in_for_tests("auth0|bob", "Bob")
        status, created = call(port, "POST", "/api/rpc", {"name": "create_task", "args": ["go shopping"]}, ada)
        assert status == 200
        assert created["task"]["title"] == "go shopping"
        assert created["screen"]["user"]["display_name"] == "Ada"

        status, bob_view = call(port, "POST", "/api/rpc", {"name": "snapshot", "args": [None]}, bob)
        assert status == 200
        assert bob_view["screen"]["tasks"] == []
        assert bob_view["screen"]["user"]["display_name"] == "Bob"

        status, chat = call(port, "POST", "/api/chat", {"message": "read my tasks for today"}, ada)
        assert status == 200
        assert chat["ok"] is False
        assert "Azure OpenAI" in chat["error"]

        page, html, _headers = call_text(port, "GET", "/")
        assert page == 200
        assert b"Sign in with Auth0" in html
    finally:
        app.stop()


def call(port, method, path, body=None, cookie=None):
    status, payload, _headers = call_text(port, method, path, body, cookie)
    return status, json.loads(payload.decode("utf-8"))


def call_text(port, method, path, body=None, cookie=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if cookie:
        request.add_header("Cookie", f"planner_session={cookie}")
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, response.read(), response.headers
    except HTTPError as exc:
        return exc.code, exc.read(), exc.headers
