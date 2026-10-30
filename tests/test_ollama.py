import json
from urllib.error import URLError

from planner.assistant import OllamaChatClient


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
    assert "not running" in text
