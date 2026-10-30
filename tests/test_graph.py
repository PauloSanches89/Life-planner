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


def test_base_asks_for_a_name_and_a_day_before_any_edit(service):
    base = ScriptedModel(
        [
            base_decision(
                False,
                "What should the task be called, and which day is it on?",
                {"action": "create"},
            )
        ]
    )

    turn = run_turn(service, "create a new task", "summary", base, ScriptedModel([]), ScriptedModel([]))

    assert turn.reply == "What should the task be called, and which day is it on?"
    assert service.list_tasks() == []
    assert service.get_intake()["action"] == "create"


def test_base_keeps_collected_fields_and_asks_about_an_alarm(service, store, clock):
    service.set_intake({"action": "create", "title": "Go to Beach"})
    base = ScriptedModel(
        [
            base_decision(
                False,
                "Would you like to set an alarm?",
                {"action": "create", "title": "Go to Beach", "start_date": "2026-12-01"},
            )
        ]
    )

    turn = run_turn(
        service,
        "Go to Beach on 1 December 2026",
        "summary",
        base,
        ScriptedModel([]),
        ScriptedModel([]),
    )

    assert turn.reply == "Would you like to set an alarm?"
    assert service.list_tasks() == []
    reloaded = PlannerService(Store(store.root, clock), clock)
    assert reloaded.get_intake()["title"] == "Go to Beach"
    assert reloaded.get_intake()["start_date"] == "2026-12-01"


def test_ready_without_a_date_does_not_reach_the_editor(service):
    base = ScriptedModel(
        [base_decision(True, "", {"action": "create", "title": "Go to Beach", "alarm": "none"})]
    )

    turn = run_turn(service, "create Go to Beach, no alarm", "summary", base, ScriptedModel([]), ScriptedModel([]))

    assert "day" in turn.reply.lower()
    assert service.list_tasks() == []
    assert service.get_intake()["title"] == "Go to Beach"


def test_editor_prose_without_a_tool_call_is_not_a_saved_task(service):
    intake = {"action": "create", "title": "Go to Beach", "start_date": "2026-12-01", "alarm": "none"}
    base = ScriptedModel([base_decision(True, "", intake)])
    editor = ScriptedModel([AIMessage(content="I created Go to Beach.")])
    reviewer = ScriptedModel([reviewer_reply("Go to Beach successfully created on 1 December 2026")])

    turn = run_turn(service, "create Go to Beach on 2026-12-01, no alarm", "summary", base, editor, reviewer)

    assert service.list_tasks() == []
    assert "success" not in turn.reply.lower()
    assert "did not change" in turn.reply.lower()


def test_a_deleted_task_note_lets_the_next_turn_restore_that_task(service):
    task = service.create_task("Go to Beach")
    service.update_task(task.id, start_date=date(2026, 12, 1), end_date=date(2026, 12, 1))
    base = ScriptedModel(
        [base_decision(True, "", {"action": "delete", "task_id": task.id, "title": "Go to Beach"})]
    )
    editor = ScriptedModel([editor_call("delete_task", {"task_id": task.id})])
    reviewer = ScriptedModel([reviewer_reply("Go to Beach successfully deleted")])

    deleted = run_turn(service, "delete Go to Beach", "summary", base, editor, reviewer)

    assert deleted.reply == "Go to Beach successfully deleted"
    assert service.list_tasks() == []
    assert "deleted" in service.get_task_notes()[task.id]

    base = ScriptedModel([base_decision(True, "", {"action": "restore", "task_id": task.id})])
    editor = ScriptedModel([editor_call("restore_task", {"task_id": task.id})])
    reviewer = ScriptedModel([reviewer_reply("Go to Beach successfully restored")])
    restored = run_turn(service, "reinstate the Go to Beach deletion", "summary", base, editor, reviewer)

    prompt = base.seen[0][0].content
    assert "Go to Beach" in prompt
    assert "deleted" in prompt
    assert restored.reply == "Go to Beach successfully restored"
    assert service.get_task(task.id).title == "Go to Beach"
    assert service.get_task(task.id).start_date == date(2026, 12, 1)
    assert "restored" in service.get_task_notes()[task.id]
