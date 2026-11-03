import json
import threading
import time
from urllib.error import URLError

import httpx

from planner.assistant import OLLAMA_KEEP_ALIVE, OneRetry, OllamaWarmup, ensure_ollama


class ScriptedResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_warmup_loads_the_model_until_the_app_closes():
    calls = []
    loaded = threading.Event()

    def opener(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        calls.append((request.full_url, body, timeout))
        if body["keep_alive"] == OLLAMA_KEEP_ALIVE:
            loaded.set()
        return ScriptedResponse({"done": True})

    warmup = OllamaWarmup("http://127.0.0.1:11434/", "qwen3:4b", opener)
    warmup.start()
    assert loaded.wait(1)
    warmup.stop()

    assert calls[0][0] == "http://127.0.0.1:11434/api/generate"
    assert calls[0][1] == {"model": "qwen3:4b", "keep_alive": OLLAMA_KEEP_ALIVE}
    assert "prompt" not in calls[0][1]
    assert calls[0][2] == 180
    assert calls[-1][1]["keep_alive"] == 0
    assert calls[-1][2] == 10


def test_warmup_still_unloads_when_the_load_is_in_flight():
    calls = []
    started = threading.Event()

    def opener(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        calls.append(body["keep_alive"])
        if body["keep_alive"] == OLLAMA_KEEP_ALIVE and not started.is_set():
            started.set()
            while not warmup._closed.is_set():
                time.sleep(0.01)
        return ScriptedResponse({"done": True})

    warmup = OllamaWarmup("http://127.0.0.1:11434", "qwen3:4b", opener)
    warmup.start()
    assert started.wait(1)
    warmup.stop()

    assert calls[0] == OLLAMA_KEEP_ALIVE
    assert calls[-1] == 0


def test_warmup_refreshes_the_lease_while_the_app_is_open():
    calls = []
    refreshed = threading.Event()

    def opener(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        calls.append(body["keep_alive"])
        if calls.count(OLLAMA_KEEP_ALIVE) >= 2:
            refreshed.set()
        return ScriptedResponse({"done": True})

    warmup = OllamaWarmup("http://127.0.0.1:11434", "qwen3:4b", opener, interval=0.01)
    warmup.start()
    assert refreshed.wait(1)
    warmup.stop()
    finished = len(calls)
    time.sleep(0.05)

    assert calls[:-1] == [OLLAMA_KEEP_ALIVE] * (len(calls) - 1)
    assert calls[-1] == 0
    assert len(calls) == finished
    assert len(calls) >= 3


def test_app_start_leaves_a_running_ollama_alone():
    def opener(request, timeout):
        return ScriptedResponse({"version": "0.0.0"})

    called = []
    assert ensure_ollama("http://127.0.0.1:11434", opener, lambda: "ollama", called.append) is True
    assert called == []


def test_app_start_runs_local_ollama_when_it_is_down():
    probes = {"count": 0}

    def opener(request, timeout):
        probes["count"] += 1
        if probes["count"] == 1:
            raise URLError("connection refused")
        return ScriptedResponse({"version": "0.0.0"})

    started = []

    def popen(args, **kwargs):
        started.append(args)

    assert ensure_ollama("http://127.0.0.1:11434", opener, lambda: r"C:\Ollama\ollama.exe", popen, lambda _seconds: None) is True
    assert started == [[r"C:\Ollama\ollama.exe", "serve"]]


def test_app_start_does_not_launch_a_remote_ollama():
    def opener(request, timeout):
        raise URLError("connection refused")

    called = []
    assert ensure_ollama("http://ollama.example:11434", opener, lambda: "ollama", called.append) is False
    assert called == []


def test_app_start_opens_when_ollama_cannot_be_found():
    def opener(request, timeout):
        raise URLError("connection refused")

    assert ensure_ollama("http://127.0.0.1:11434", opener, lambda: None, lambda *args, **kwargs: None) is False


def test_warmup_opens_the_app_when_ollama_is_down():
    def opener(request, timeout):
        raise URLError("connection refused")

    warmup = OllamaWarmup("http://127.0.0.1:11434", "qwen3:4b", opener)
    warmup.start()
    warmup.stop()


class _FlakyModel:
    def __init__(self, failures: int, result="ok") -> None:
        self.failures = failures
        self.result = result
        self.calls = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise httpx.ReadTimeout("The read operation timed out")
        return self.result


def test_a_timed_out_model_call_is_tried_once_more():
    model = _FlakyModel(1, "recovered")

    assert OneRetry(model).invoke(["hello"]) == "recovered"
    assert model.calls == 2


def test_a_second_timeout_is_not_tried_again():
    model = _FlakyModel(2)
    wrapped = OneRetry(model).bind_tools([])

    try:
        wrapped.invoke(["hello"])
    except httpx.ReadTimeout:
        assert model.calls == 2
    else:
        raise AssertionError("the second timeout should reach the caller")
