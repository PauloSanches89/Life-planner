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

Copy [.env.example](.env.example) to `.env`. Auth0 domain, client id, and client secret are required before sign-in works. Azure OpenAI and the Google Maps key can stay empty until you use the assistant or the map tab. `.env` is gitignored.

In the Auth0 application, set the allowed callback URL to `http://127.0.0.1:8765/callback` (or whatever `AUTH0_CALLBACK_URL` is).

## Run

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

That starts the API at `http://127.0.0.1:8765/` and opens the same address in the desktop window. Open that URL in a browser instead if you are editing the page with Lovable.

The assistant deployment is meant to be Azure OpenAI `gpt-4o-mini`. A second deployment checks each create, edit, and delete and refuses changes that do not follow the user's request. Both names are in `.env.example`.

`dist/Life-planner/Life-planner.exe` is an older local-only build. It does not sign in with Auth0 or serve the HTTP API. Use `python main.py` for this version.
