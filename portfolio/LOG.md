# Portfolio log — Life planner

- Started: 2026-10-30
- Primary shape: agent
- Problem: Chat must collect a task change, apply it, and report only what was actually saved.

## Current bar

- [x] Two or three real tools
- [x] A hard stop
- [x] One garbage-tool or tool-error run
- [ ] A written break
- [x] A README
- [ ] A demo

## Journal

<!-- append below this line -->

### 2026-10-30 — note

- By: user
- Step: Start the portfolio log for the existing Life planner repo.
- Why: The repo already had the planner, login, and chat work.
- Result: Earlier steps were not logged.

### 2026-10-28 — readme

- By: reconstructed
- Step: Add a README in the initial commit, then replace the title-only file when the local server landed.
- Target: `README.md`
- Delta: Commit `186133f` is only `# Life-planner`. Commit `c07ecbf` describes Auth0 sign-in, encrypted `userdata/`, the local server, and Azure OpenAI `gpt-4o-mini` plus a checker deployment.
- Why: The commit messages do not state a failure.
- Result: No eval was rerun.

### 2026-10-30 — tool

- By: reconstructed
- Step: Add planner tools on the assistant in commit `c07ecbf`.
- Target: tool, `planner/assistant.py`
- Delta: `list_tasks`, `get_task`, `create_task`, `update_task`, `delete_task`, `add_subtask`, `update_subtask`, `delete_subtask`, `add_link`, `delete_link`, `update_summary`.
- Why: The commit message is "Move to local server - online hosting possible". It does not state a failure.
- Result: No eval was rerun.

### 2026-10-30 — prompt change

- By: reconstructed
- Step: Give the assistant one system prompt that reads and edits through those tools.
- Target: system, `planner/assistant.py`
- Delta: "You are the assistant and the manager for this person's planner. Use tools to read and change only what they asked for."
- Why: The commit message does not state a failure.
- Result: eval not rerun

### 2026-10-30 — hard stop

- By: reconstructed
- Step: Refuse a mutating tool call unless the checker returns allow, and stop the tool loop after five rounds.
- Target: `planner/assistant.py`
- Delta: Checker reply must be `{"allow": true}` or `{"allow": false, "reason": "why not"}`. Otherwise the edit is not saved. `for _round in range(5)`.
- Why: The file docstring says a checker must allow each edit. The commit message does not state a failure.
- Result: `test_checker_blocks_a_delete_the_user_did_not_ask_for` is in the same commit. No scored eval.

### 2026-10-30 — model change

- By: reconstructed
- Step: Prefer a local Ollama model, then Gemini, then Azure OpenAI.
- Target: model, `.env.example`, `planner/server.py`, `planner/settings.py`
- Delta: Commit `fbcebec` adds `OLLAMA_MODEL` and `GEMINI_MODEL=gemini-2.5-flash`. When `OLLAMA_MODEL` is set, that model is both the assistant and the checker. Azure `gpt-4o-mini` remains the assistant deployment only when Ollama and Gemini are unset.
- Why: The commit message is "ollama integration". It does not state a failure.
- Result: eval not rerun

### 2026-10-30 — readme

- By: reconstructed
- Step: Document the Ollama, Gemini, and Azure order in the README.
- Target: `README.md`
- Delta: "Chat uses a local Ollama model when `OLLAMA_MODEL` is set, then `GEMINI_API_KEY`, then Azure OpenAI."
- Why: The commit message does not state a failure.
- Result: No eval was rerun.

### 2026-10-30 — prompt change

- By: reconstructed
- Step: Tell the assistant how a task spans days.
- Target: system, `planner/assistant.py`
- Delta: Commit `5768d67` adds "A task covers every day from start_date through end_date, including both." The checker line adds "A start_date and a later end_date are allowed when the user asked for more than one day."
- Why: The commit message is "Ollama stays open 2 min lease". It does not state why the span sentences were added.
- Result: eval not rerun

### 2026-10-30 — model change

- By: reconstructed
- Step: Hold the Ollama model on a two-minute lease and refresh it while the app is open.
- Target: model, `planner/assistant.py`, `main.py`
- Delta: `OLLAMA_KEEP_ALIVE = "2m"` on chat requests and on an empty `/api/generate` refresh. Close sends `keep_alive` `0`.
- Why: The commit message is "Ollama stays open 2 min lease".
- Result: eval not rerun

### 2026-10-30 — garbage run

- By: reconstructed
- Step: Script a create_task call whose end date is before its start date.
- Target: tool, `tests/test_signed_in.py`
- Delta: `test_create_task_with_an_end_before_the_start_is_not_saved` calls `create_task` with start `2026-11-03` and end `2026-11-01`. The checker returns `{"allow": true}`.
- Why: The commit does not describe a live model run.
- Result: The test asserts `service._store.load().tasks == []`.

### 2026-10-30 — tool

- By: reconstructed
- Step: Replace the single assistant tool list with the editor tools in the uncommitted working tree.
- Target: tool, `planner/graph.py`
- Delta: Editor tools are `list_tasks`, `get_task`, `create_task`, `update_task`, `delete_task`, and `restore_task`. `git diff HEAD` deletes the old `TOOLS` list from `planner/assistant.py`. `planner/graph.py` is untracked, so this is not in a commit.
- Why: The diff does not state a failure.
- Result: No eval was rerun.

### 2026-10-30 — prompt change

- By: reconstructed
- Step: Split the system prompt into a base collector, an editor, and a reviewer.
- Target: system, `planner/graph.py`
- Delta: Base: "You do not create, edit, or delete tasks yourself." Editor: "Do not write a message to the user." Reviewer: "Use only the result object. If result.ok is false, say the change did not happen."
- Why: The working tree does not state a failure.
- Result: eval not rerun

### 2026-10-30 — hard stop

- By: reconstructed
- Step: Keep the editor from running until a create has a name, a day, and an alarm decision.
- Target: `planner/graph.py`
- Delta: Ready stays false until title, `start_date` or `ongoing`, and alarm `none` or a timezone-aware datetime are present. If the editor returns no tool call, the result error is "The editor did not change a task."
- Why: The working tree does not state a failure.
- Result: `tests/test_graph.py` is untracked. No scored eval.
