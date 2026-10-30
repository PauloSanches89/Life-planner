# Life-planner

Planner for one signed-in person at a time. Tasks can span days and keep subtasks, links, a location, and alarms. Sign-in is Auth0, managed at [manage.auth0.com](https://manage.auth0.com/). Each user's document is encrypted on disk under `userdata/`, which is gitignored. The same item can be stored in Azure Cosmos DB later by filling in the Cosmos values in `.env`.

The window and a local HTTP server serve `ui/`. Lovable can edit those files while the server is running.

## Setup

The project requires CPython 3.14. Create the virtual environment from a 3.14 interpreter. On this machine the Windows `py` launcher only has 3.12 registered; 3.14.8 is the uv-installed interpreter below.

```powershell
& "$env:USERPROFILE\AppData\Roaming\uv\python\cpython-3.14.8-windows-x86_64-none\python.exe" -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
```

If `python` on your `PATH` is already 3.14, `python -m venv .venv` is enough. Cosmos DB support is the optional `cloud` extra: `pip install -e ".[cloud]"`.

Copy [.env.example](.env.example) to `.env`. Auth0 domain, client id, and client secret are required before sign-in works. Chat uses a local Ollama model when `OLLAMA_MODEL` is set, then `GEMINI_API_KEY`, then Azure OpenAI. The map tab embeds the task location from Google Maps without an API key. `.env` is gitignored.

In the Auth0 application, set the allowed callback URL to `http://127.0.0.1:8765/callback` (or whatever `AUTH0_CALLBACK_URL` is).

## Run

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

That starts the API at `http://127.0.0.1:8765/` and opens the same address in the desktop window. Open that URL in a browser instead if you are editing the page with Lovable.

With `OLLAMA_MODEL` set, that local model answers and also checks each create, edit, and delete. A refused edit is not saved. Ollama has to be running. Leave the model name empty to use Gemini or the Azure OpenAI deployments in `.env.example` instead.

`dist/Life-planner/Life-planner.exe` is an older local-only build. It does not sign in with Auth0 or serve the HTTP API. Use `python main.py` for this version.
