import json
from urllib.error import HTTPError

from planner.assistant import GeminiChatClient


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


def test_gemini_request_uses_the_key_header_and_function_history():
    opener = Recording(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"functionCall": {"name": "list_tasks", "args": {}, "id": "call-1"}}]
                    }
                }
            ]
        }
    )
    client = GeminiChatClient("secret-key", "gemini-2.5-flash", opener)
    message = client.complete(
        [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "What is on today?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call-0", "function": {"name": "list_tasks", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "call-0", "content": '{"ok": true, "tasks": []}'},
        ],
        [
            {
                "type": "function",
                "function": {
                    "name": "list_tasks",
                    "description": "List tasks",
                    "parameters": {
                        "type": "object",
                        "properties": {"day": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
            }
        ],
    )

    assert opener.request.get_header("X-goog-api-key") == "secret-key"
    body = json.loads(opener.request.data.decode("utf-8"))
    assert body["systemInstruction"]["parts"][0]["text"] == "Be brief."
    assert body["tools"][0]["functionDeclarations"][0]["parameters"]["type"] == "OBJECT"
    assert "additionalProperties" not in body["tools"][0]["functionDeclarations"][0]["parameters"]
    assert body["contents"][1]["role"] == "model"
    assert body["contents"][2]["parts"][0]["functionResponse"]["name"] == "list_tasks"
    assert message["tool_calls"][0]["function"]["name"] == "list_tasks"
    assert "secret-key" not in opener.request.full_url


def test_gemini_checker_asks_for_json_and_hides_the_key_on_failure():
    def opener(request, timeout):
        raise HTTPError(
            request.full_url,
            403,
            "Forbidden",
            hdrs=None,
            fp=_Body(b'{"error":{"message":"Your project has been denied access. Free tier unavailable."}}'),
        )

    client = GeminiChatClient("secret-key", "gemini-2.5-flash", opener)

    try:
        client.complete([{"role": "user", "content": "check this"}])
    except RuntimeError as exc:
        text = str(exc)
    else:
        raise AssertionError("expected the Gemini error")

    assert "Free tier unavailable" in text
    assert "secret-key" not in text


class _Body:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    def read(self) -> bytes:
        return self.raw

    def close(self) -> None:
        return None
