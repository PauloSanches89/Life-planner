# Life-planner

Local-first planner. Tasks can span days and keep subtasks, external links, and alarms. Data is stored in `userdata/`, which is gitignored. The window shows a local profile and does not log in.

## Setup

The project requires CPython 3.14. Create the virtual environment from a 3.14 interpreter. On this machine the Windows `py` launcher only has 3.12 registered; 3.14.8 is the uv-installed interpreter below.

```powershell
& "$env:USERPROFILE\AppData\Roaming\uv\python\cpython-3.14.8-windows-x86_64-none\python.exe" -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
```

If `python` on your `PATH` is already 3.14, `python -m venv .venv` is enough.

## Run

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```
