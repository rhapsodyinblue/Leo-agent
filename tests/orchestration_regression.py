import ast
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"

HELPERS = {
    "load_pending_writes",
    "save_pending_writes",
    "create_pending_write",
    "update_pending_write",
    "get_active_pending_write",
    "mark_pending_write_approved",
    "mark_pending_write_canceled",
    "persist_pending_write",
    "stage_file_operation",
    "get_pending_write",
    "clear_pending_write",
    "task_file_write_operation",
    "task_requires_durable_write",
    "task_has_durable_write_receipt",
    "task_has_unapproved_durable_write",
    "record_task_durable_write_approval",
    "task_expects_file_artifact",
    "file_operation_is_stageable",
    "extract_model_status_from_text",
    "completion_verification",
    "build_completion_verification_for_task",
    "update_task_completion_verification",
    "mark_task_artifact_staging_failed",
    "load_tasks",
    "save_tasks",
    "create_task",
    "update_task",
    "normalize_task_prerequisites",
    "normalize_task_dependency_titles",
    "canonicalize_dependency_title",
    "task_dependency_title",
    "find_project_dependency_title_matches",
    "resolve_dependency_id_for_title",
    "unresolved_dependency_titles_after_clears",
    "resolve_task_file_path",
    "check_task_prerequisites",
    "block_task_for_failed_prerequisites",
    "get_compiled_descendants",
    "latest_non_superseded_compiled_descendant",
    "latest_runnable_compiled_descendant",
    "dependency_task_is_satisfied",
    "supersede_prior_compiled_descendants",
    "validate_create_compile_source",
    "check_task_dependencies",
    "reset_blocked_create_tasks",
    "get_task",
}


class UserSession:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value


class ChainlitStub:
    user_session = UserSession()


def load_app_helpers(tmp_root):
    source = APP_PATH.read_text(encoding="utf-8")
    module = ast.parse(source)
    body = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name in HELPERS
    ]
    namespace = {
        "os": os,
        "json": json,
        "re": re,
        "uuid": uuid,
        "Path": Path,
        "datetime": datetime,
        "cl": ChainlitStub,
        "LEO_FILES_PATH": tmp_root,
        "TASK_QUEUE_PATH": os.path.join(tmp_root, "TASK_QUEUE.json"),
        "TASK_ARCHIVE_PATH": os.path.join(tmp_root, "TASK_ARCHIVE.json"),
        "PENDING_WRITES_PATH": os.path.join(tmp_root, "PENDING_WRITES.json"),
    }

    def normalize_knowledge_filename(filename):
        filename = str(filename or "").strip().lstrip("/")
        if filename.startswith("..") or "/../" in filename:
            raise ValueError("Unsafe file path.")
        return filename

    def safe_knowledge_path(filename):
        filename = normalize_knowledge_filename(filename)
        path = os.path.abspath(os.path.join(tmp_root, filename))
        if not path.startswith(os.path.abspath(tmp_root)):
            raise ValueError("Unsafe file path.")
        return path

    def classify_intent_simple(_goal):
        return "PLAN"

    namespace.update({
        "normalize_knowledge_filename": normalize_knowledge_filename,
        "safe_knowledge_path": safe_knowledge_path,
        "classify_intent_simple": classify_intent_simple,
    })
    exec(compile(ast.Module(body=body, type_ignores=[]), "<app_helpers>", "exec"), namespace)
    return namespace


def task(task_id, status="pending", intent="BUILD", goal=None, inputs=None, **extra):
    return {
        "task_id": task_id,
        "status": status,
        "intent": intent,
        "goal": goal or task_id,
        "inputs": inputs or {},
        "next_action": None,
        "result": None,
        "needs_user": False,
        **extra,
    }


def save_queue(ns, tasks):
    ns["save_tasks"]({"tasks": tasks})


def load_queue(ns):
    return ns["load_tasks"]().get("tasks", [])


def by_id(tasks):
    return {item["task_id"]: item for item in tasks}


def test_dependency_override(ns):
    dep = task("dep", status="pending")
    target = task("target", inputs={
        "depends_on_task_ids": ["dep"],
        "dependency_override": True,
    })

    result = ns["check_task_dependencies"](target, by_id([dep, target]))

    assert result["ok"] is True
    assert result["dependency_override"] is True
    assert target["inputs"]["depends_on_task_ids"] == ["dep"]


def test_dependency_clears_are_isolated(ns):
    dep_a = task("dep-a", status="pending", inputs={"title": "Alpha"})
    dep_b = task("dep-b", status="pending", inputs={"title": "Beta"})

    clear_id_target = task("target-id", inputs={
        "depends_on_task_ids": ["dep-a", "dep-b"],
        "cleared_dependency_ids": ["dep-a"],
    })
    result = ns["check_task_dependencies"](clear_id_target, by_id([dep_a, dep_b, clear_id_target]))
    assert result["ok"] is False
    assert result["ignored_dependency_ids"] == ["dep-a"]
    assert result["failures"] == [{"task_id": "dep-b", "reason": "dependency_not_done:pending"}]

    project_tasks = [dep_a, dep_b]
    resolved_id = ns["resolve_dependency_id_for_title"](project_tasks, "Alpha")
    assert resolved_id == "dep-a"
    clear_title_target = task("target-title", inputs={
        "depends_on_titles": ["Alpha", "Beta"],
        "depends_on_task_ids": ["dep-a", "dep-b"],
        "cleared_dependency_titles": ["Alpha"],
        "cleared_dependency_ids": [resolved_id],
    })
    result = ns["check_task_dependencies"](clear_title_target, by_id([dep_a, dep_b, clear_title_target]))
    assert result["ok"] is False
    assert result["failures"] == [{"task_id": "dep-b", "reason": "dependency_not_done:pending"}]

    mixed_target = task("target-mixed", inputs={
        "depends_on_titles": ["Alpha", "Beta"],
        "depends_on_task_ids": ["dep-b"],
        "cleared_dependency_titles": ["Beta"],
        "cleared_dependency_ids": ["dep-b"],
    })
    result = ns["check_task_dependencies"](mixed_target, by_id([dep_a, dep_b, mixed_target]))
    assert result["ok"] is False
    assert result["failures"] == [{"title": "Alpha", "reason": "unresolved_dependency_title"}]


def test_reset_blocked_create_tasks(ns, tmp_root):
    blocked = task("blocked", status="blocked_prerequisite", inputs={
        "approved_create_project": "demo",
        "prerequisites": [{"file": "state.txt", "must_contain": ["READY"]}],
    })
    other_project = task("other", status="blocked_prerequisite", inputs={
        "approved_create_project": "other",
        "prerequisites": [{"file": "state.txt", "must_contain": ["READY"]}],
    })
    save_queue(ns, [blocked, other_project])

    reset, still_blocked = ns["reset_blocked_create_tasks"]("demo")
    assert reset == []
    assert [item["task"]["task_id"] for item in still_blocked] == ["blocked"]

    state_path = Path(tmp_root) / "CREATE_PROJECTS" / "demo" / "state.txt"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("READY", encoding="utf-8")

    reset, still_blocked = ns["reset_blocked_create_tasks"]("demo")
    assert [item["task_id"] for item in reset] == ["blocked"]
    assert still_blocked == []
    statuses = {item["task_id"]: item["status"] for item in load_queue(ns)}
    assert statuses == {"blocked": "pending", "other": "blocked_prerequisite"}


def test_compile_source_lifecycle_helpers(ns):
    source = task("source", status="pending", inputs={"approved_create_project": "demo"})
    old_child = task("old-child", status="pending", inputs={
        "approved_create_project": "demo",
        "source_task_id": "source",
        "compiled_from_task_id": "source",
    })
    save_queue(ns, [source, old_child])

    ok, reason = ns["validate_create_compile_source"](source, "demo")
    assert ok is True, reason

    child = ns["create_task"](
        "compiled child",
        assigned_role="coder",
        requested_by="create_compile",
        inputs={
            "intent": "BUILD",
            "approved_create_project": "demo",
            "source_task_id": "source",
            "compiled_from_task_id": "source",
        },
    )
    superseded = ns["supersede_prior_compiled_descendants"]("source", child["task_id"])
    source_inputs = {**source["inputs"], "latest_compiled_task_id": child["task_id"], "compiled_task_ids": [child["task_id"]]}
    ns["update_task"]("source", {
        "status": "compiled",
        "inputs": source_inputs,
        "next_action": f"Run latest compiled task `{child['task_id']}`.",
    })

    tasks = by_id(load_queue(ns))
    assert tasks["source"]["status"] == "compiled"
    assert tasks[child["task_id"]]["status"] == "pending"
    assert [item["task_id"] for item in superseded] == ["old-child"]
    assert tasks["old-child"]["status"] == "superseded"


def test_compiled_source_dependency_uses_latest_child(ns):
    source = task("source", status="compiled", inputs={
        "latest_compiled_task_id": "child",
        "compiled_task_ids": ["child"],
    })
    child = task("child", status="pending", inputs={"source_task_id": "source"})

    satisfied, latest = ns["dependency_task_is_satisfied"](source, by_id([source, child]))
    assert satisfied is False
    assert latest["task_id"] == "child"

    child["status"] = "done"
    satisfied, latest = ns["dependency_task_is_satisfied"](source, by_id([source, child]))
    assert satisfied is True
    assert latest["task_id"] == "child"


def test_done_file_writer_needs_durable_receipt(ns):
    writer = task("writer", status="done", file_operation={"filename": "out.txt"})
    satisfied, _latest = ns["dependency_task_is_satisfied"](writer, by_id([writer]))
    assert satisfied is False

    save_queue(ns, [writer])
    ns["record_task_durable_write_approval"]({
        "task_id": "writer",
        "filename": "out.txt",
        "operation": "create",
    }, approved_at="2026-05-24T00:00:00")

    updated = ns["get_task"]("writer")
    satisfied, _latest = ns["dependency_task_is_satisfied"](updated, by_id([updated]))
    assert satisfied is True
    assert updated["inputs"]["durable_write_approved_at"] == "2026-05-24T00:00:00"


def test_pending_write_queue(ns):
    write_a = ns["stage_file_operation"]("a.txt", "A", operation="create", reason="A")
    write_b = ns["stage_file_operation"]("b.txt", "B", operation="create", reason="B")

    data = ns["load_pending_writes"]()
    assert [item["write_id"] for item in data["writes"]] == [write_a["write_id"], write_b["write_id"]]
    assert ns["get_pending_write"]()["write_id"] == write_b["write_id"]

    ChainlitStub.user_session.set("active_pending_write_id", write_a["write_id"])
    assert ns["get_pending_write"]()["write_id"] == write_a["write_id"]
    assert len(ns["load_pending_writes"]()["writes"]) == 2


def test_approved_write_marks_queue_and_receipt(ns):
    queued = ns["stage_file_operation"]("artifact.txt", "artifact", operation="create", reason="task output")
    build = task("build", status="done", intent="BUILD", completion_verification={
        "state": "staged_artifact",
        "checked_at": "2026-05-24T00:00:00",
        "evidence": [],
        "failures": [],
    })
    save_queue(ns, [build])
    queued["task_id"] = "build"

    ns["record_task_durable_write_approval"](queued, approved_at="2026-05-24T00:01:00")
    ns["mark_pending_write_approved"](queued["write_id"], approved_at="2026-05-24T00:01:00")

    stored_write = next(
        item
        for item in ns["load_pending_writes"]()["writes"]
        if item["write_id"] == queued["write_id"]
    )
    stored_task = ns["get_task"]("build")
    assert stored_write["status"] == "approved"
    assert stored_task["inputs"]["durable_write_approved_at"] == "2026-05-24T00:01:00"
    assert stored_task["completion_verification"]["state"] == "durable_receipt"


def test_build_done_without_artifact_needs_artifact(ns):
    build = task("build", status="done", intent="BUILD", model_status="done")
    verification = ns["build_completion_verification_for_task"](build, "done", None)
    assert verification["state"] == "missing_artifact"

    save_queue(ns, [build])
    updated = ns["mark_task_artifact_staging_failed"](
        build,
        "build_task_done_without_stageable_file_operation",
        "No artifact was parsed.",
    )
    assert updated["status"] == "needs_artifact"
    assert updated["completion_verification"]["state"] == "artifact_staging_failed"


def run_all():
    with tempfile.TemporaryDirectory() as tmp_root:
        ns = load_app_helpers(tmp_root)
        tests = [
            ("dependency override", lambda: test_dependency_override(ns)),
            ("dependency clears", lambda: test_dependency_clears_are_isolated(ns)),
            ("reset blocked", lambda: test_reset_blocked_create_tasks(ns, tmp_root)),
            ("compile lifecycle", lambda: test_compile_source_lifecycle_helpers(ns)),
            ("compiled source dependency", lambda: test_compiled_source_dependency_uses_latest_child(ns)),
            ("durable receipt dependency", lambda: test_done_file_writer_needs_durable_receipt(ns)),
            ("pending write queue", lambda: test_pending_write_queue(ns)),
            ("approved write receipt", lambda: test_approved_write_marks_queue_and_receipt(ns)),
            ("build needs artifact", lambda: test_build_done_without_artifact_needs_artifact(ns)),
        ]
        for index, (name, test) in enumerate(tests, start=1):
            test()
            print(f"ok {index} - {name}")

    print("orchestration regression harness passed")


if __name__ == "__main__":
    try:
        run_all()
    except AssertionError as exc:
        print(f"orchestration regression harness failed: {exc}", file=sys.stderr)
        raise
