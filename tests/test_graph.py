import json
from datetime import date

from langchain_core.messages import AIMessage

from planner.assistant import run_turn
from planner.service import PlannerService
from planner.store import Store


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


def test_asking_what_a_new_task_needs_explains_and_does_not_create(service):
    reply = "I need a title, and whether the task stays persistent or is locked to a date."
    base = ScriptedModel([base_decision(False, reply, {"action": "answer"})])
    editor = ScriptedModel([])

    turn = run_turn(
        service,
        "What information do you need to create a task?",
        "summary",
        base,
        editor,
        ScriptedModel([]),
    )

    assert turn.reply == reply
    assert service.list_tasks() == []
    assert editor.seen == []


def test_base_asks_for_a_name_before_creating(service):
    base = ScriptedModel([base_decision(False, "What should the task be called?", {"action": "create"})])

    turn = run_turn(service, "create a new task", "summary", base, ScriptedModel([]), ScriptedModel([]))

    assert turn.reply == "What should the task be called?"
    assert service.list_tasks() == []
    assert service.get_intake()["action"] == "create"
    assert service.get_intake()["asks"] == 1
    assert service.get_intake()["task_id"]


def test_an_alarm_without_a_time_is_asked_once(service, store, clock):
    base = ScriptedModel(
        [base_decision(False, "What time?", {"action": "create", "title": "Go to Beach", "alarm": "yes"})]
    )

    turn = run_turn(service, "create Go to Beach and set an alarm", "summary", base, ScriptedModel([]), ScriptedModel([]))

    assert turn.reply == "What time?"
    assert service.list_tasks() == []
    reloaded = PlannerService(Store(store.root, clock), clock)
    assert reloaded.get_intake()["title"] == "Go to Beach"
    assert reloaded.get_intake()["alarm"] == "yes"
    assert reloaded.get_intake()["alarm_asked"] is True


def test_a_missing_alarm_time_is_saved_as_no_alarm(service):
    service.set_intake(
        {
            "action": "create",
            "task_id": "task-pack",
            "title": "Go to Beach",
            "alarm": "yes",
            "alarm_asked": True,
            "asks": 1,
        }
    )
    base = ScriptedModel([base_decision(True, "", {"action": "create", "title": "Go to Beach", "alarm": "yes"})])
    editor = ScriptedModel([editor_call("create_task", {"title": "Go to Beach"})])
    reviewer = ScriptedModel([reviewer_reply("Go to Beach successfully created, with no end date")])

    turn = run_turn(service, "I don't know the time", "summary", base, editor, reviewer)

    saved = service.get_task("task-pack")
    assert saved.ongoing is True
    assert saved.alarms == []
    assert turn.reply == "Go to Beach successfully created, with no end date"


def test_a_turn_names_each_step_while_it_runs(service):
    base = ScriptedModel([base_decision(True, "", {"action": "create", "title": "Go to Beach", "alarm": "no"})])
    editor = ScriptedModel(
        [
            editor_call("list_tasks", {}),
            editor_call("create_task", {"title": "Go to Beach"}),
        ]
    )
    reviewer = ScriptedModel([reviewer_reply("Go to Beach successfully created, with no end date")])
    seen = []

    run_turn(service, "create Go to Beach, no alarm", "summary", base, editor, reviewer, seen.append)

    assert seen == [
        "Reading your message",
        "Applying the change",
        "Retrieving task data",
        "Creating task",
        "Writing a reply",
    ]


def test_a_task_with_no_duration_is_ongoing(service):
    base = ScriptedModel([base_decision(True, "", {"action": "create", "title": "Go to Beach", "alarm": "no"})])
    editor = ScriptedModel([editor_call("create_task", {"title": "Go to Beach"})])
    reviewer = ScriptedModel([reviewer_reply("Go to Beach successfully created, with no end date")])

    turn = run_turn(service, "create Go to Beach, no alarm", "summary", base, editor, reviewer)

    assert service.list_tasks()[0].title == "Go to Beach"
    assert service.list_tasks()[0].ongoing is True
    assert "successfully created" in turn.reply.lower()


def test_subtasks_are_optional_when_the_editor_sends_an_empty_object(service):
    base = ScriptedModel([base_decision(True, "", {"action": "create", "title": "do stuff", "alarm": "no"})])
    editor = ScriptedModel([editor_call("create_task", {"title": "do stuff", "ongoing": True, "subtasks": {}})])
    reviewer = ScriptedModel([reviewer_reply("do stuff successfully created, with no end date")])

    turn = run_turn(service, 'make an ongoing task called "do stuff"', "summary", base, editor, reviewer)

    saved = service.list_tasks()[0]
    assert saved.title == "do stuff"
    assert saved.ongoing is True
    assert saved.subtasks == []
    assert "successfully created" in turn.reply.lower()


def test_a_location_on_create_is_saved_without_a_separate_tool(service):
    base = ScriptedModel(
        [
            base_decision(
                True,
                "",
                {"action": "create", "title": "do stuff", "alarm": "no", "location": "vida e cafe kromboom"},
            )
        ]
    )
    editor = ScriptedModel([editor_call("create_task", {"title": "do stuff", "ongoing": True})])
    reviewer = ScriptedModel([reviewer_reply("do stuff successfully created, with no end date")])

    turn = run_turn(
        service,
        'make an ongoing task called "do stuff" and in the location add vida e cafe kromboom',
        "summary",
        base,
        editor,
        reviewer,
    )

    assert service.list_tasks()[0].location == "vida e cafe kromboom"
    assert "did not save the location" not in turn.reply.lower()
    assert "successfully created" in turn.reply.lower()


def test_the_model_schema_saves_each_list_item(service):
    intake = {
        "action": "create",
        "title": "shopping list",
        "alarm": "no",
        "subtasks": ["eggs", "cream", "milk"],
    }
    base = ScriptedModel([base_decision(True, "", intake)])
    editor = ScriptedModel([editor_call("create_task", {"title": "shopping list", "ongoing": True})])
    reviewer = ScriptedModel([reviewer_reply("shopping list successfully created, with no end date")])

    turn = run_turn(
        service,
        "create a shopping list that has eggs, cream and milk on it",
        "summary",
        base,
        editor,
        reviewer,
    )

    saved = service.list_tasks()[0]
    assert saved.title == "shopping list"
    assert [item.text for item in saved.subtasks] == ["eggs", "cream", "milk"]
    assert saved.ongoing is True
    assert "successfully created" in turn.reply.lower()
    assert "subtasks is an array of strings" in base.seen[0][0].content


def test_a_repaired_list_schema_keeps_the_place_and_the_items(service):
    intake = {
        "action": "create",
        "title": "shopping list",
        "location": "woolworths",
        "alarm": "no",
        "subtasks": ["cake", "bread", "popcorn"],
    }
    base = ScriptedModel([base_decision(True, "", intake)])
    editor = ScriptedModel([editor_call("create_task", {"title": "shopping list", "ongoing": True})])
    reviewer = ScriptedModel([reviewer_reply("shopping list successfully created, with no end date")])

    turn = run_turn(
        service,
        "make s hopping list for woolworths with cake, bread and popcorn",
        "summary",
        base,
        editor,
        reviewer,
    )

    saved = service.list_tasks()[0]
    assert saved.title == "shopping list"
    assert saved.location == "woolworths"
    assert [item.text for item in saved.subtasks] == ["cake", "bread", "popcorn"]
    assert "successfully created" in turn.reply.lower()


def test_a_list_sentence_is_not_parsed_when_the_model_leaves_the_schema_empty(service):
    base = ScriptedModel([base_decision(False, "What would you like to plan?", {})])

    turn = run_turn(
        service,
        "make a shopping list with cake, bread and popcorn",
        "summary",
        base,
        ScriptedModel([]),
        ScriptedModel([]),
    )

    assert service.list_tasks() == []
    assert turn.reply == "What would you like to plan?"


def test_editor_prose_without_a_tool_call_is_not_a_saved_task(service):
    intake = {"action": "create", "title": "Go to Beach", "start_date": "2026-12-01", "alarm": "none"}
    base = ScriptedModel([base_decision(True, "", intake)])
    editor = ScriptedModel([AIMessage(content="I created Go to Beach.")])
    reviewer = ScriptedModel([reviewer_reply("The editor did not change a task.")])

    turn = run_turn(service, "create Go to Beach on 2026-12-01, no alarm", "summary", base, editor, reviewer)

    assert service.list_tasks() == []
    assert turn.reply == "The editor did not change a task."


def test_a_deleted_task_note_lets_the_next_turn_restore_that_task(service):
    task = service.create_task("Go to Beach")
    service.update_task(task.id, start_date=date(2026, 12, 1), end_date=date(2026, 12, 1))
    base = ScriptedModel(
        [base_decision(True, "", {"action": "delete", "task_id": task.id, "title": "Go to Beach"})]
    )
    editor = ScriptedModel([editor_call("delete_task", {"task_id": task.id})])
    reviewer = ScriptedModel([reviewer_reply("Go to Beach successfully deleted")])

    deleted = run_turn(service, "delete Go to Beach", "summary", base, editor, reviewer)

    assert deleted.reply.startswith("Go to Beach successfully deleted")
    assert service.list_tasks() == []
    assert "deleted" in service.get_task_notes()[task.id]

    base = ScriptedModel([
        base_decision(True, "", {"action": "restore", "task_id": task.id}),
        base_decision(True, "", {"action": "restore", "task_id": task.id}),
    ])
    editor = ScriptedModel([editor_call("restore_task", {"task_id": task.id})])
    reviewer = ScriptedModel([reviewer_reply("Go to Beach successfully restored")])
    identifier = ScriptedModel([AIMessage(content=json.dumps({"task_id": task.id}))])
    restored = run_turn(
        service,
        "reinstate the Go to Beach deletion",
        "summary",
        base,
        editor,
        reviewer,
        identifier=identifier,
    )

    prompt = base.seen[0][0].content
    assert "Go to Beach" in prompt
    assert "deleted" in prompt
    assert restored.reply.startswith("Go to Beach successfully restored")
    assert service.get_task(task.id).title == "Go to Beach"
    assert service.get_task(task.id).start_date == date(2026, 12, 1)
    assert "restored" in service.get_task_notes()[task.id]


def test_delete_removes_the_task_when_the_editor_tries_to_create_it(service):
    task = service.create_task("make tea")
    base = ScriptedModel(
        [base_decision(True, "", {"action": "delete", "task_id": task.id, "title": "make tea"})]
    )
    editor = ScriptedModel([editor_call("create_task", {"title": "make tea", "task_id": task.id})])
    reviewer = ScriptedModel([reviewer_reply("make tea successfully deleted")])

    turn = run_turn(service, "delete the make tea task", "summary", base, editor, reviewer)

    assert service.list_tasks() == []
    assert turn.reply == "make tea successfully deleted"
    assert "already exists" not in turn.reply.lower()


def test_today_follows_the_system_date_while_another_day_is_open(service):
    service.set_selected_day(date(2026, 11, 3))
    intake = {
        "action": "create",
        "title": "Pack",
        "start_date": "2026-11-03",
        "end_date": "2026-11-03",
        "alarm": "none",
    }
    base = ScriptedModel([base_decision(True, "", intake)])
    editor = ScriptedModel(
        [editor_call("create_task", {"title": "Pack", "start_date": "2026-11-03", "end_date": "2026-11-03", "alarm": "none"})]
    )
    reviewer = ScriptedModel([reviewer_reply("Pack successfully created on 28 October 2026")])

    turn = run_turn(service, "create a task named Pack for today, no alarm", "summary", base, editor, reviewer)

    assert "successfully created" in turn.reply.lower()
    assert [task.title for task in service.list_tasks_on(date(2026, 10, 28))] == ["Pack"]
    assert service.list_tasks_on(date(2026, 11, 3)) == []


def test_tomorrow_is_the_day_after_the_system_date(service):
    service.set_selected_day(date(2026, 11, 3))
    intake = {"action": "create", "title": "Pack", "start_date": "2026-11-04", "alarm": "none"}
    base = ScriptedModel([base_decision(True, "", intake)])
    editor = ScriptedModel(
        [editor_call("create_task", {"title": "Pack", "start_date": "2026-11-04", "alarm": "none"})]
    )
    reviewer = ScriptedModel([reviewer_reply("Pack successfully created on 29 October 2026")])

    turn = run_turn(service, "make a task named Pack for tomorrow, no alarm", "summary", base, editor, reviewer)

    assert "successfully created" in turn.reply.lower()
    found = service.list_tasks_on(date(2026, 10, 29))
    assert [task.title for task in found] == ["Pack"]
    assert found[0].start_date == found[0].end_date == date(2026, 10, 29)


def test_a_relative_span_asks_which_day_to_count_from(service):
    service.set_selected_day(date(2026, 11, 10))
    task = service.create_task("Errand")
    service.update_task(task.id, start_date=date(2026, 11, 10), end_date=date(2026, 11, 10))
    question = "Do you mean 28 October 2026 or 10 November 2026?"
    base = ScriptedModel([base_decision(True, question, {"action": "delete", "task_id": task.id})])

    turn = run_turn(service, "delete the next 3 days' tasks", "summary", base, ScriptedModel([]), ScriptedModel([]))

    assert turn.reply == question
    assert service.get_task(task.id).title == "Errand"
    assert service.get_intake()["date_anchor"] == "unresolved"


def test_choosing_today_counts_the_span_from_the_system_date(service):
    service.set_selected_day(date(2026, 11, 10))
    service.set_intake(
        {
            "action": "create",
            "title": "Pack",
            "alarm": "none",
            "date_anchor": "unresolved",
            "start_date": "2026-11-10",
            "end_date": "2026-11-12",
        }
    )
    base = ScriptedModel(
        [
            base_decision(
                True,
                "",
                {"action": "create", "title": "Pack", "alarm": "none", "start_date": "2026-11-10", "end_date": "2026-11-12"},
            )
        ]
    )
    editor = ScriptedModel(
        [editor_call("create_task", {"title": "Pack", "start_date": "2026-11-10", "end_date": "2026-11-12", "alarm": "none"})]
    )
    reviewer = ScriptedModel([reviewer_reply("Pack successfully created on 28 October 2026")])

    turn = run_turn(service, "from today", "summary", base, editor, reviewer)

    assert "successfully created" in turn.reply.lower()
    found = service.list_tasks_on(date(2026, 10, 28))
    assert [task.title for task in found] == ["Pack"]
    assert found[0].start_date == date(2026, 10, 28)
    assert found[0].end_date == date(2026, 11, 12)


def test_a_new_task_gets_an_id_before_it_is_saved(service):
    base = ScriptedModel(
        [base_decision(False, "What should the task be called, and which day is it on?", {"action": "create"})]
    )

    turn = run_turn(service, "create a new task", "summary", base, ScriptedModel([]), ScriptedModel([]))

    task_id = service.get_intake()["task_id"]
    assert task_id
    assert service.list_tasks() == []
    transcript = service.get_transcript(task_id)
    assert transcript[0] == {"role": "user", "text": "create a new task"}
    assert turn.reply in transcript[1]["text"]


def test_the_saved_task_uses_the_id_from_the_start_of_the_chat(service):
    base = ScriptedModel(
        [base_decision(False, "What should the task be called, and which day is it on?", {"action": "create"})]
    )
    run_turn(service, "create a new task", "summary", base, ScriptedModel([]), ScriptedModel([]))
    task_id = service.get_intake()["task_id"]
    base = ScriptedModel(
        [base_decision(True, "", {"action": "create", "title": "Pack", "start_date": "2026-12-01", "alarm": "none"})]
    )
    editor = ScriptedModel(
        [editor_call("create_task", {"title": "Pack", "start_date": "2026-12-01", "alarm": "none"})]
    )
    reviewer = ScriptedModel([reviewer_reply("Pack successfully created on 1 December 2026")])

    run_turn(service, "Pack on 1 December 2026, no alarm", "summary", base, editor, reviewer)

    assert service.get_task(task_id).title == "Pack"
    spoken = [item["text"] for item in service.get_transcript(task_id) if item["role"] == "user"]
    assert spoken == ["create a new task", "Pack on 1 December 2026, no alarm"]


def test_an_existing_task_keeps_its_id_and_later_edits_see_only_its_transcript(service):
    beach = service.create_task("Go to Beach")
    service.update_task(beach.id, start_date=date(2026, 12, 1), end_date=date(2026, 12, 1))
    dentist = service.create_task("Dentist")
    service.update_task(dentist.id, start_date=date(2026, 11, 2), end_date=date(2026, 11, 2))
    service.record_task_session(beach.id, "session-a", "pack towels", "Noted for the beach.")
    service.record_task_session(dentist.id, "session-a", "arrive early", "Noted for the dentist.")
    change = {"action": "update", "task_id": beach.id, "start_date": "2026-12-04", "end_date": "2026-12-04"}
    base = ScriptedModel([base_decision(True, "", change)])
    editor = ScriptedModel(
        [editor_call("update_task", {"task_id": beach.id, "start_date": "2026-12-04", "end_date": "2026-12-04"})]
    )
    reviewer = ScriptedModel([reviewer_reply("Go to Beach successfully updated")])

    turn = run_turn(
        service,
        "move Go to Beach to Friday",
        "summary",
        base,
        editor,
        reviewer,
        chat_session_id="session-now",
    )

    assert "successfully updated" in turn.reply.lower()
    assert service.get_task(beach.id).start_date == date(2026, 12, 4)
    prompt = base.seen[0][0].content
    assert "pack towels" in prompt
    assert "arrive early" not in prompt
    editor_text = "\n".join(message.content for message in editor.seen[0])
    assert "pack towels" not in editor_text
    assert "arrive early" not in editor_text
    assert service.get_intake() == {}


def test_two_sessions_for_one_task_each_keep_a_summary(service):
    beach = service.create_task("Go to Beach")
    service.record_task_session(beach.id, "session-a", "pack towels", "Noted for the beach.")
    service.record_task_session(beach.id, "session-b", "bring sunscreen", "Added to the beach task.")

    stored = service.earlier_task_sessions(beach.id)
    assert [item["session_id"] for item in stored] == ["session-a", "session-b"]
    assert "Noted for the beach." in stored[0]["summary"]
    assert "Added to the beach task." in stored[1]["summary"]
    assert service.chat_log("session-a")[0]["text"] == "pack towels"
    assert service.chat_log("session-b")[0]["text"] == "bring sunscreen"

    base = ScriptedModel([base_decision(False, "The beach task had earlier notes.", {"action": "answer"})])
    run_turn(
        service,
        "what did we say about Go to Beach",
        "summary",
        base,
        ScriptedModel([]),
        ScriptedModel([]),
        chat_session_id="session-c",
    )

    prompt = base.seen[0][0].content
    assert "Noted for the beach." in prompt
    assert "Added to the beach task." in prompt
    assert "session-c" not in prompt
    still = service.earlier_task_sessions(beach.id, "session-c")
    assert [item["session_id"] for item in still] == ["session-a", "session-b"]


def test_a_create_does_not_ask_the_identifier(service):
    identifier = ScriptedModel([])
    base = ScriptedModel([base_decision(False, "What should the task be called?", {"action": "create"})])

    run_turn(
        service,
        "create a new task",
        "summary",
        base,
        ScriptedModel([]),
        ScriptedModel([]),
        identifier=identifier,
    )

    assert identifier.seen == []


def test_one_title_match_does_not_ask_the_identifier(service):
    beach = service.create_task("Go to Beach")
    identifier = ScriptedModel([])
    base = ScriptedModel([base_decision(False, "Which day?", {"action": "update", "task_id": beach.id})])

    run_turn(
        service,
        "move Go to Beach",
        "summary",
        base,
        ScriptedModel([]),
        ScriptedModel([]),
        identifier=identifier,
    )

    assert identifier.seen == []


def test_identifier_runs_when_no_title_matches(service):
    beach = service.create_task("Go to Beach")
    service.update_task(beach.id, description="Bring a towel")
    dentist = service.create_task("Dentist")
    service.record_task_session(beach.id, "session-a", "pack towels", "Noted for the beach.")
    identifier = ScriptedModel([AIMessage(content=json.dumps({"task_id": beach.id}))])
    base = ScriptedModel([base_decision(False, "Which day?", {"action": "update", "task_id": beach.id})])

    run_turn(
        service,
        "move the beach outing",
        "summary",
        base,
        ScriptedModel([]),
        ScriptedModel([]),
        identifier=identifier,
    )

    catalog = identifier.seen[0][1].content
    assert beach.id in catalog
    assert "Go to Beach" in catalog
    assert dentist.id in catalog
    assert "Dentist" in catalog
    assert "pack towels" not in catalog
    assert "Bring a towel" not in catalog
    prompt = base.seen[0][0].content
    assert "pack towels" in prompt
    assert "Dentist" not in prompt


def test_identifier_runs_when_several_titles_match(service):
    day = service.create_task("Beach day")
    trip = service.create_task("Beach trip")
    identifier = ScriptedModel([AIMessage(content=json.dumps({"task_id": trip.id}))])
    base = ScriptedModel([base_decision(False, "Which day?", {"action": "update", "task_id": trip.id})])

    run_turn(
        service,
        "change Beach day and Beach trip",
        "summary",
        base,
        ScriptedModel([]),
        ScriptedModel([]),
        identifier=identifier,
    )

    assert identifier.seen
    prompt = base.seen[0][0].content
    assert trip.id in prompt
    assert "Beach day" not in prompt
