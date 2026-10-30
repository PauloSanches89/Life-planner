import json
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from langchain_core.messages import AIMessage

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
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="",
        cosmos_endpoint="",
        cosmos_key="",
        cosmos_database="",
        cosmos_container="",
        port=8765,
    )
    values.update(overrides)
    return Settings(**values)


class ScriptedModel:
    def __init__(self, messages) -> None:
        self.messages = list(messages)
        self.seen = []

    def bind_tools(self, tools, **kwargs):
        return self

    def invoke(self, messages):
        self.seen.append(messages)
        return self.messages.pop(0)


def base_decision(ready, reply, intake):
    return AIMessage(content=json.dumps({"ready": ready, "reply": reply, "intake": intake}))


def editor_call(name, args):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": "call-1", "type": "tool_call"}],
    )


def reviewer_reply(reply):
    return AIMessage(content=json.dumps({"reply": reply}))


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


def test_create_task_span_shows_on_every_day_from_start_through_end(service):
    intake = {
        "action": "create",
        "title": "Trip",
        "start_date": "2026-11-01",
        "end_date": "2026-11-03",
        "alarm": "none",
    }
    base = ScriptedModel([base_decision(True, "", intake)])
    editor = ScriptedModel([editor_call("create_task", intake)])
    reviewer = ScriptedModel([reviewer_reply("Trip successfully created on 1 November 2026")])

    turn = run_turn(
        service,
        "create a task named Trip from 2026-11-01 to 2026-11-03, no alarm",
        "summary",
        base,
        editor,
        reviewer,
    )

    assert turn.reply == "Trip successfully created on 1 November 2026"
    assert service.list_tasks_on(date(2026, 10, 28)) == []
    assert service.list_tasks_on(date(2026, 11, 4)) == []
    for day_number in (1, 2, 3):
        found = service.list_tasks_on(date(2026, 11, day_number))
        assert [task.title for task in found] == ["Trip"]
        assert found[0].start_date == date(2026, 11, 1)
        assert found[0].end_date == date(2026, 11, 3)
        assert found[0].ongoing is False


def test_create_task_with_one_date_uses_that_day_for_both_ends(service):
    intake = {"action": "create", "title": "Dentist", "start_date": "2026-11-02", "alarm": "none"}
    base = ScriptedModel([base_decision(True, "", intake)])
    editor = ScriptedModel([editor_call("create_task", {"title": "Dentist", "start_date": "2026-11-02", "alarm": "none"})])
    reviewer = ScriptedModel([reviewer_reply("Dentist successfully created on 2 November 2026")])

    run_turn(service, "create a task named Dentist on 2026-11-02, no alarm", "summary", base, editor, reviewer)

    found = service.list_tasks_on(date(2026, 11, 2))
    assert [task.title for task in found] == ["Dentist"]
    assert found[0].start_date == found[0].end_date == date(2026, 11, 2)
    assert service.list_tasks_on(date(2026, 10, 28)) == []


def test_create_task_with_an_end_before_the_start_is_not_saved(service):
    intake = {
        "action": "create",
        "title": "Backwards",
        "start_date": "2026-11-03",
        "end_date": "2026-11-01",
        "alarm": "none",
    }
    base = ScriptedModel([base_decision(True, "", intake)])
    editor = ScriptedModel([editor_call("create_task", intake)])
    reviewer = ScriptedModel([reviewer_reply("Backwards successfully created on 3 November 2026")])

    turn = run_turn(
        service,
        "create a task named Backwards from 2026-11-03 to 2026-11-01, no alarm",
        "summary",
        base,
        editor,
        reviewer,
    )

    assert "success" not in turn.reply.lower()
    assert service._store.load().tasks == []


def test_choose_mode_follows_the_users_words():
    assert choose_mode("read my tasks for today", "summary") == "detailed"
    assert choose_mode("summarize my day", "detailed") == "summary"
    assert choose_mode("create a new task named go shopping", "summary") == "summary"


def test_a_read_request_does_not_edit_a_task(service):
    task = service.create_task("Keep me")
    base = ScriptedModel(
        [base_decision(False, "You have one task: Keep me.", {"action": "answer"})]
    )
    editor = ScriptedModel([])
    reviewer = ScriptedModel([])

    turn = run_turn(service, "read my tasks for today", "summary", base, editor, reviewer)

    assert turn.mode == "detailed"
    assert turn.reply == "You have one task: Keep me."
    assert service.get_task(task.id).title == "Keep me"


def test_allowed_create_uses_the_requested_name_and_saves_a_summary(service):
    intake = {"action": "create", "title": "go shopping", "start_date": "2026-10-28", "alarm": "none"}
    base = ScriptedModel([base_decision(True, "", intake)])
    editor = ScriptedModel([editor_call("create_task", intake)])
    reviewer = ScriptedModel([reviewer_reply("go shopping successfully created on 28 October 2026")])

    turn = run_turn(service, 'create a new task named "go shopping" on 2026-10-28, no alarm', "summary", base, editor, reviewer)

    assert turn.reply == "go shopping successfully created on 28 October 2026"
    assert [task.title for task in service.list_tasks_on(date(2026, 10, 28))] == ["go shopping"]
    assert "go shopping" in service.get_summary()
    assert "created" in next(iter(service.get_task_notes().values()))


def test_remembered_login_opens_the_planner_after_a_restart(tmp_path, clock):
    key = load_master_key("", tmp_path)
    ui = Path(__file__).resolve().parent.parent / "ui"
    first = PlannerApp(blank_settings(), tmp_path, clock, key, ui)
    session_id = first.sign_in_for_tests("auth0|ada", "Ada", remember=True)
    saved = (tmp_path / "session.json").read_text(encoding="utf-8")
    assert "Ada" not in saved
    assert "auth0|ada" not in saved

    clock.set(clock.now() + timedelta(days=29))
    second = PlannerApp(blank_settings(), tmp_path, clock, key, ui)
    httpd = second.serve(0)
    try:
        status, body = call(httpd.server_address[1], "GET", "/api/session", cookie=session_id)
        assert status == 200
        assert body["display_name"] == "Ada"
        status, _ = call(httpd.server_address[1], "POST", "/logout", cookie=session_id)
        assert status == 200
    finally:
        second.stop()

    assert not (tmp_path / "session.json").exists()
    third = PlannerApp(blank_settings(), tmp_path, clock, key, ui)
    assert third.sessions == {}


def test_remembered_login_expires_after_thirty_days(tmp_path, clock):
    key = load_master_key("", tmp_path)
    ui = Path(__file__).resolve().parent.parent / "ui"
    first = PlannerApp(blank_settings(), tmp_path, clock, key, ui)
    first.sign_in_for_tests("auth0|ada", "Ada", remember=True)
    clock.set(clock.now() + timedelta(days=31))

    second = PlannerApp(blank_settings(), tmp_path, clock, key, ui)

    assert second.sessions == {}
    assert not (tmp_path / "session.json").exists()


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
        assert "GEMINI_API_KEY" in chat["error"]

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
