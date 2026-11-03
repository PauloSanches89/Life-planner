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

### 2026-10-30 — prompt change

- By: user
- Step: Count today and tomorrow from the system date, and ask when a relative span could start on either day.
- Target: system, `planner/graph.py`
- Delta: "Count today, tonight, tomorrow, and yesterday from today, never from selected_day." "If they use a relative span such as the next 3 days, and today and selected_day are different, and they do not say which of those two days to count from, set ready to false and ask."
- Why: A task for today was dated on the open day, November 3, instead of the system date.
- Result: eval not rerun

### 2026-10-30 — hard stop

- By: agent
- Step: Override a today or tomorrow date that the model copied from the open day, and refuse a span until the user picks an anchor.
- Target: `planner/graph.py`
- Delta: `_apply_date_policy` sets tomorrow to the day after `service.today()`. An unresolved span stores `date_anchor` `unresolved` and asks "Do you mean from today (...), or from the day open in the planner (...)?"
- Why: The prompt alone still let the editor save the open day when the user said today.
- Result: eval not rerun

### 2026-11-02 — readme

- By: user
- Step: Say that the app starts a local Ollama server that is not already running.
- Target: `README.md`
- Delta: "If that server is on this machine and is not already running, the app starts it."
- Why: Chat failed because Ollama was not running, and the README told the user to start it by hand.
- Result: No eval was rerun.

### 2026-11-02 — prompt change

- By: user
- Step: Have the base model decide on the first message whether the user means a new task or an existing one, and read only that task's transcript.
- Target: system, `planner/graph.py`
- Delta: "On the first message, decide whether this is a new task or a change to a task already in the list." "A new task uses action create and an empty task_id. The app assigns the id. Do not invent an id." "task_transcript is the earlier conversation about this one task."
- Why: The model saw only the current sentence, so a later edit could not use what had already been said about that task.
- Result: eval not rerun

### 2026-11-02 — tool

- By: agent
- Step: Save each task's chat under the id assigned at the start, and create the task with that same id.
- Target: tool, `planner/graph.py`
- Delta: `create_task` takes `task_id`. A new task id is assigned when action is create and no unsaved id exists yet. Later edits load `task_transcript` for that id only.
- Why: The transcript has to stay on the task the user is changing, including before the task row is saved.
- Result: eval not rerun

### 2026-11-02 — prompt change

- By: user
- Step: Stop the base model after two questions, default a missing duration to ongoing, and ask once for an alarm time.
- Target: system, `planner/graph.py`
- Delta: "Do not ask a third time. After two questions, set ready to true with the fields you have." "Leave start_date and end_date empty unless the user states a duration." "If they want an alarm but name no time, set alarm to yes and ask once what time to use."
- Why: The editor never ran when the base model kept asking, or when an alarm had no time.
- Result: eval not rerun

### 2026-11-02 — tool

- By: user
- Step: Split saving a task from setting its alarm, links, and location.
- Target: tool, `planner/graph.py`
- Delta: `create_task` and `update_task` take the title, description, and duration. `set_alarm`, `add_link`, and `set_location` are separate calls.
- Why: One tool call was skipping the alarm and the location, and a prose reply was reported as a saved edit.
- Result: eval not rerun

### 2026-11-02 — hard stop

- By: agent
- Step: Create the task after two questions, and fail the turn when a required tool call is missing.
- Target: `planner/graph.py`
- Delta: `_next_question` returns nothing once `asks` is 2. An alarm left as yes with no time becomes no. `_missing_steps` marks the edit failed unless create or update, and any requested alarm, link, or location, was actually called.
- Why: The base model could ask forever, and the reviewer could say success when the editor had not called the tools.
- Result: eval not rerun

### 2026-11-02 — tool

- By: user
- Step: Treat subtasks as optional on create.
- Target: tool, `planner/graph.py`
- Delta: `create_task` accepts a missing subtasks value, or `{}`, and saves the task with no subtasks.
- Why: Creating "do stuff" failed because the editor sent subtasks as an empty object and the tool required a list.
- Result: eval not rerun

### 2026-11-02 — prompt change

- By: user
- Step: Save location with the task tools, and stop telling the editor to call set_location.
- Target: system, `planner/graph.py`
- Delta: "Call create_task for a new task, or update_task for an edit, with the title, description, duration, and location."
- Why: A saved cafe location was reported as missing because the reviewer required a separate set_location call.
- Result: eval not rerun

### 2026-11-02 — hard stop

- By: user
- Step: Stop failing a turn when location was saved on create or update.
- Target: `planner/graph.py`
- Delta: `_missing_steps` no longer requires `set_location`.
- Why: The location was already stored by create_task, and the extra check told the user the change did not happen.
- Result: eval not rerun

### 2026-11-02 — prompt change

- By: agent
- Step: Tell the editor that a delete or restore is only that one tool.
- Target: system, `planner/graph.py`
- Delta: "When action is delete, call only delete_task. When action is restore, call only restore_task."
- Why: "delete the make tea task" called create_task, which failed with "That task already exists" and the task stayed.
- Result: eval not rerun

### 2026-11-02 — tool

- By: user
- Step: Delete or restore the task from the id already in state.
- Target: tool, `planner/graph.py`
- Delta: `_required_call` runs `delete_task` or `restore_task` and does not run the model's create or update call.
- Why: The editor called create_task for "make tea", that call failed because the task existed, and the failed call stopped the delete.
- Result: eval not rerun

### 2026-11-02 — prompt change

- By: user
- Step: Treat a list as a new task whose items are subtasks.
- Target: system, `planner/graph.py`
- Delta: "A list, such as a shopping list, is a new task. The title is the list name, such as shopping list. Each item on the list is a subtask."
- Why: "create a shopping list that has eggs, cream and milk on it" was answered with "What would you like to change?" and nothing was saved.
- Result: eval not rerun

### 2026-11-02 — tool

- By: user
- Step: Save a requested list as a task, with one subtask per item.
- Target: tool, `planner/graph.py`
- Delta: `_apply_list_request` sets the title and subtasks from the message. `_required_call` then runs `create_task` with those subtasks.
- Why: The model did not turn "shopping list" into a create, so eggs, cream, and milk were never stored.
- Result: eval not rerun

### 2026-11-02 — tool

- By: user
- Step: Split list items when the user leaves out commas.
- Target: tool, `planner/graph.py`
- Delta: `_split_items` separates words on spaces as well as commas. The word "and" is a separator. The words after the last "and" stay one item.
- Why: "mil cheese eggs and toilet paper" saved "mil cheese eggs" as one subtask because there was no comma.
- Result: eval not rerun

### 2026-11-02 — model change

- By: user
- Step: Turn off reasoning for the local chat model.
- Target: model, `planner/server.py`
- Delta: `reasoning=False` on the base, editor, and reviewer `ChatOllama` clients.
- Why: Each chat waited on a full thinking pass before the answer, and a save waited on two or three of those.
- Result: eval not rerun

### 2026-11-02 — prompt change

- By: user
- Step: Skip the reviewer model when the tool result is already known.
- Target: system, `planner/graph.py`
- Delta: `reviewer_node` returns `factual_reply(outcome)` when `ok` is present and does not call the reviewer.
- Why: The reply was another Ollama generation after the save had already succeeded or failed.
- Result: eval not rerun

### 2026-11-02 — prompt change

- By: user
- Step: Have the base model fill the task schema, including a misspelled list.
- Target: system, `planner/graph.py`
- Delta: "Fill intake to this schema and repair typos so the values are usable." "subtasks is an array of strings, one item each." "A place named with for, at, or from goes in location." "s hopping means shopping."
- Why: "make s hopping list for woolworths with cake, bread and popcorn" did not match the list regex, so the reply was "What would you like to change?"
- Result: eval not rerun

### 2026-11-02 — tool

- By: user
- Step: Stop parsing a list sentence with a regex.
- Target: tool, `planner/graph.py`
- Delta: Removed `_apply_list_request`, `_parse_list`, and `_split_items`. A create saves the title, location, and subtasks the model put in intake.
- Why: The regex rejected a list when the wording was not exact, instead of letting the model fit the schema.
- Result: eval not rerun

### 2026-11-02 — model change

- By: user
- Step: Turn Ollama reasoning back on.
- Target: model, `planner/server.py`
- Delta: Removed `reasoning=False` from the base, editor, and reviewer `ChatOllama` clients.
- Why: The speed change was abandoned before it was committed.
- Result: eval not rerun

### 2026-11-02 — prompt change

- By: user
- Step: Call the reviewer again after a known tool result.
- Target: system, `planner/graph.py`
- Delta: `reviewer_node` always calls the reviewer. It no longer returns `factual_reply(outcome)` early.
- Why: Skipping the reviewer to shorten the wait was abandoned before it was committed.
- Result: eval not rerun

### 2026-11-03 — model change

- By: user
- Step: Leave Ollama reasoning on.
- Target: model, `planner/server.py`
- Delta: Removed `reasoning=False` from the base, editor, and reviewer `ChatOllama` clients.
- Why: With thinking off, `qwen3:4b` does nothing. The line had been added again while diagnosing a failed save.
- Result: eval not rerun

### 2026-11-03 — prompt change

- By: user
- Step: Let the base and reviewer write their own replies.
- Target: system, `planner/graph.py`
- Delta: `BASE_PROMPT` says that when ready is false the model writes its own short polite question, and leaves reply empty when ready is true. `REVIEWER_PROMPT` says to write a helpful polite reply from the tool result and not claim a missing step. Removed `factual_reply`, `honest_reply`, and `_close_reply`.
- Why: Follow-up questions and the final reply were fixed sentences instead of the models' own words.
- Result: eval not rerun

### 2026-11-03 — model change

- By: user
- Step: Turn reasoning off for the editor only.
- Target: model, `planner/server.py`
- Delta: The middle `ChatOllama` client is constructed with `reasoning=False`. The base and reviewer clients are not.
- Why: The editor needs to call tools. Thinking stays on for the base and the reviewer.
- Result: eval not rerun

### 2026-11-03 — prompt change

- By: user
- Step: Tell the base model what a new task requires.
- Target: system, `planner/graph.py`
- Delta: `BASE_PROMPT` says a create needs a title and whether the task is persistent (`ongoing` true, no dates) or locked to `start_date` and `end_date`. Description, location, subtasks, an alarm, and links are optional. A question about those requirements uses action `answer`, ready false, and the model's own words.
- Why: Asking what information is needed was treated as an incomplete edit.
- Result: eval not rerun

### 2026-11-03 — prompt change

- By: user
- Step: Give the model earlier session summaries for a named task.
- Target: system, `planner/graph.py`
- Delta: The base and editor context now include `task_sessions` instead of `task_transcript`. The prompt says those summaries are earlier chats and are not part of the current chat.
- Why: A new opening of the app starts an empty chat. The model still needs a short record of earlier chats about the same task.
- Result: eval not rerun

### 2026-11-03 — data change

- By: user
- Step: Keep a chat log per session and one summary per task per session.
- Target: tool, `planner/models.py`, `planner/service.py`, `planner/server.py`
- Delta: A new process archives the previous visible chat onto the user document and starts an empty chat. A refresh of the same process still shows the in-memory chat. Each task stores `{session_id, summary}` built from that session's turns, capped at 500 characters.
- Why: The previous conversation was being copied back into the page on every launch.
- Result: eval not rerun

### 2026-11-03 — prompt change

- By: user
- Step: Stop sending earlier chat summaries to the editor.
- Target: system, `planner/graph.py`
- Delta: Removed the `task_sessions` sentence from `EDITOR_PROMPT` and removed `task_sessions` from the editor's input. The base prompt still receives those summaries.
- Why: The editor applies the intake the base model already filled. The earlier chat is not an input it can apply.
- Result: eval not rerun

### 2026-11-03 — model change

- By: user
- Step: Time out a stuck Ollama chat call and try it once more.
- Target: model, `planner/assistant.py`, `planner/server.py`
- Delta: Each Ollama chat client waits 90 seconds for the next response byte. `OneRetry` runs that call one more time after `httpx.TimeoutException`. A second timeout becomes "The assistant took too long. Try again."
- Why: A chat request stayed open while the runner had processed none of the prompt, and the page kept spinning.
- Result: eval not rerun
