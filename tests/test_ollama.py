import json
import threading
import time
from urllib.error import URLError

from planner.assistant import OLLAMA_KEEP_ALIVE, OllamaChatClient, OllamaWarmup


class ScriptedResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class Recording:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.request = None

    def __call__(self, request, timeout):
        self.request = request
        return ScriptedResponse(self.payload)


def test_ollama_request_targets_the_local_chat_endpoint():
    opener = Recording(
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "create_task",
                                    "arguments": {"title": "go shopping"},
                                }
                            }
                        ],
                    }
                }
            ]
        }
    )
    client = OllamaChatClient("http://127.0.0.1:11434", "qwen3:4b", opener)

    message = client.complete(
        [{"role": "user", "content": "create a task named go shopping"}],
        [{"type": "function", "function": {"name": "create_task"}}],
    )

    assert opener.request.full_url == "http://127.0.0.1:11434/v1/chat/completions"
    body = json.loads(opener.request.data.decode("utf-8"))
    assert body["model"] == "qwen3:4b"
    assert body["stream"] is False
    assert body["keep_alive"] == OLLAMA_KEEP_ALIVE
    assert body["tools"][0]["function"]["name"] == "create_task"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"title": "go shopping"}'
    assert message["tool_calls"][0]["id"] == "call-0"


def test_ollama_checker_asks_for_json_and_reports_when_the_server_is_down():
    seen = {}

    def opener(request, timeout):
        seen["body"] = json.loads(request.data.decode("utf-8"))
        raise URLError("connection refused")

    client = OllamaChatClient("http://127.0.0.1:11434", "qwen3:4b", opener)

    try:
        client.complete([{"role": "user", "content": "check this"}])
    except RuntimeError as exc:
        text = str(exc)
    else:
        raise AssertionError("expected the Ollama error")

    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["keep_alive"] == OLLAMA_KEEP_ALIVE
    assert "not running" in text


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


def test_warmup_opens_the_app_when_ollama_is_down():
    def opener(request, timeout):
        raise URLError("connection refused")

    warmup = OllamaWarmup("http://127.0.0.1:11434", "qwen3:4b", opener)
    warmup.start()
    warmup.stop()
