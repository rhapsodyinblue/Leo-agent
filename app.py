import os
import shutil
from pathlib import Path
import json
import tempfile
import subprocess
import re
import uuid
from datetime import datetime
import ollama
import chainlit as cl
from validation_syntax import validate_proposed_code_syntax
from validation_baselines import (
    baseline_missing_items_unexplained_by_mode,
    compare_target_file_baselines,
    detect_file_kind,
    format_unexplained_baseline_report,
    generate_target_file_baseline,
    parse_target_file_baseline_text,
)
from validation_static import validate_react_static_behavior_contract
from validation_runtime_js import (
    check_validation_js_contract,
    extract_validation_js,
    validate_js_snippet,
)

MODEL = "leo-build"
ESCALATION_MODEL = "qwen2.5-coder:14b"
COMPILER_MODEL = "qwen2.5-coder:7b"

LEO_FILES_PATH = os.path.expanduser("~/Desktop/Leo_Files")
TASK_QUEUE_PATH = os.path.join(LEO_FILES_PATH, "TASK_QUEUE.json")
TASK_ARCHIVE_PATH = os.path.join(LEO_FILES_PATH, "TASK_ARCHIVE.json")
MEMORY_INDEX_PATH = os.path.join(LEO_FILES_PATH, "MEMORY_INDEX.json")
EMBED_MODEL = "mxbai-embed-large"

MAX_HISTORY_MESSAGES = 6

def load_file(filename: str) -> str:

    path = os.path.join(LEO_FILES_PATH, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return f"[Missing: {filename}]"

def normalize_knowledge_filename(filename):
    filename = filename.strip()

    prefixes = [
        os.path.expanduser("~/Desktop/Leo_Files/"),
        "~/Desktop/Leo_Files/",
        "/Users/calebseely/Desktop/Leo_Files/"
    ]

    for prefix in prefixes:
        if filename.startswith(prefix):
            filename = filename.replace(prefix, "", 1)

    filename = filename.lstrip("/")

    if filename.startswith("..") or "/../" in filename:
        raise ValueError("Unsafe file path.")

    return filename

def safe_knowledge_path(filename):
    filename = normalize_knowledge_filename(filename)
    path = os.path.abspath(os.path.join(LEO_FILES_PATH, filename))

    if not path.startswith(os.path.abspath(LEO_FILES_PATH)):
        raise ValueError("Unsafe file path.")

    return path

def stage_file_operation(filename, content, operation="replace", reason=""):
    operation = (operation or "replace").lower().strip()

    if operation not in ["create", "append", "edit", "replace"]:
        operation = "append"

    original_content = ""
    original_exists = False

    try:
        file_path = safe_knowledge_path(filename)
        original_exists = os.path.exists(file_path)
        if original_exists:
            original_content = Path(file_path).read_text(encoding="utf-8")
    except Exception:
        original_content = ""

    cl.user_session.set("pending_write", {
        "filename": filename,
        "content": content,
        "operation": operation,
        "reason": reason,
        "rollback_available": operation in ["edit", "replace"] and original_exists,
        "original_content_snapshot": original_content,
        "candidate_content_snapshot": content,
        "staged_at": datetime.now().isoformat(timespec="seconds")
    })

def stage_file_write(filename, content):
    operation = "append" if str(content).startswith("__APPEND__") else "replace"
    if str(content).startswith("__APPEND__"):
        content = str(content).replace("__APPEND__", "", 1)
    stage_file_operation(filename, content, operation=operation, reason="legacy stage_file_write call")


def extract_pending_build_doc_intake_entries(build_doc_intake):
    """
    Extract pending PROJECT_BUILD_DOC_INTAKE entries into a concise list for the documenter.
    This keeps the model from having to rediscover entries inside raw markdown.
    """
    if not build_doc_intake:
        return "None"

    chunks = re.split(r"\n(?=## Build Doc Intake)", build_doc_intake)
    entries = []

    for chunk in chunks:
        if "## Build Doc Intake" not in chunk:
            continue
        if not re.search(r"Tester Status:\s*pending", chunk, flags=re.IGNORECASE):
            continue

        title_match = re.search(r"## Build Doc Intake\s*[—-]\s*(.+)", chunk)
        task_match = re.search(r"Original Queue Task:\s*(.*?)(?=\n[A-Z][A-Za-z ]*:\n|\nApproved Operation:|\nTarget File|\nTarget Files|\nKnown Implementation Evidence:|\nDocumenter Note:|\Z)", chunk, flags=re.DOTALL)
        op_match = re.search(r"Approved Operation:\s*(.*?)(?=\n[A-Z][A-Za-z ]*:\n|\nTarget File|\nTarget Files|\nKnown Implementation Evidence:|\nDocumenter Note:|\Z)", chunk, flags=re.DOTALL)
        target_match = re.search(r"Target Files?:\s*(.*?)(?=\nKnown Implementation Evidence:|\nDocumenter Note:|\Z)", chunk, flags=re.DOTALL)
        evidence_match = re.search(r"Known Implementation Evidence:\s*(.*?)(?=\nDocumenter Note:|\Z)", chunk, flags=re.DOTALL)

        title = (title_match.group(1).strip() if title_match else "Untitled intake entry")
        task = (task_match.group(1).strip() if task_match else "unknown")
        operation = (op_match.group(1).strip() if op_match else "unknown")
        target = (target_match.group(1).strip() if target_match else "unknown")
        evidence = (evidence_match.group(1).strip() if evidence_match else "unknown")

        entries.append(f"""- Item: {title}
  - Original Queue Task: {task}
  - Target File(s): {target}
  - Approved Operation: {operation}
  - Evidence: {evidence}
  - Tester Status: pending""")

    return "\n\n".join(entries) if entries else "None"


def get_pending_write():
    return cl.user_session.get("pending_write")

def clear_pending_write():
    cl.user_session.set("pending_write", None)


def enrich_pending_write_from_task(pending, task):
    if not pending:
        return None

    task_inputs = task.get("inputs", {}) or {}
    pending["task_id"] = task.get("task_id")
    pending["task_goal"] = task.get("goal")
    pending["original_task"] = task_inputs.get("original_queue_task") or task_inputs.get("source_task_goal") or task.get("goal")
    pending["source_task_id"] = task_inputs.get("source_task_id")
    pending["approved_create_project"] = task_inputs.get("approved_create_project")
    pending["approved_plan_file"] = task_inputs.get("approved_plan_file")
    pending["task_inputs"] = task_inputs
    return pending


def append_build_doc_intake(project_slug, original_task, filename, operation, source_task_id=None):
    if not project_slug:
        return

    try:
        intake_path, _ = create_project_path(project_slug, "PROJECT_BUILD_DOC_INTAKE.md")
        os.makedirs(os.path.dirname(intake_path), exist_ok=True)

        timestamp = datetime.now().isoformat(timespec="seconds")

        entry = f"""

## Build Doc Intake — {timestamp}

Status: implementation-approved
Tester Status: pending

Project:
{project_slug}

Original Queue Task ID:
{source_task_id or "unknown"}

Original Queue Task:
{original_task or "unknown"}

Approved Operation:
{operation}

Target File:
{filename}

Documenter Note:
This is an approved implementation event. Do not mark it as completed project truth until tester verification is added or the work is explicitly non-UI/internal-only.
"""

        with open(intake_path, "a", encoding="utf-8") as f:
            f.write(entry)

    except Exception as e:
        print(f"Build doc intake append failed: {e}")



def backup_file_before_change(filename, operation):
    file_path = safe_knowledge_path(filename)

    if not Path(file_path).exists():
        return None

    backup_dir = os.path.join(LEO_FILES_PATH, "BACKUPS")
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = filename.replace("/", "__")
    backup_name = f"{timestamp}_{operation.upper()}_{safe_name}.bak"
    backup_path = os.path.join(backup_dir, backup_name)

    with open(file_path, "r", encoding="utf-8") as src:
        content = src.read()

    with open(backup_path, "w", encoding="utf-8") as dst:
        dst.write(content)

    return backup_path



def is_auto_approvable(pending):
    if not pending:
        return False, "No pending operation."

    operation = pending.get("operation", "").lower()
    filename = pending.get("filename", "")
    review = pending.get("review") or {}
    review_text = review.get("response", "").lower()

    core_files = [
        "identity.md",
        "soul.md",
        "operating_model.md",
        "agents.md",
        "project_status.md",
        "memory.md",
        "app.py"
    ]

    if operation not in ["append", "create"]:
        return False, "Only APPEND and CREATE can be auto-approved."

    if filename.lower() in core_files:
        return False, "Core files cannot be auto-approved."

    if "recommendation: approve" not in review_text:
        return False, "Reviewer did not recommend APPROVE."

    if "risk level: low" not in review_text:
        return False, "Reviewer did not mark risk as LOW."

    return True, "Eligible for auto-approval."

def log_review(filename, operation, recommendation_text):
    log_path = os.path.join(LEO_FILES_PATH, "REVIEW_LOG.md")
    timestamp = datetime.now().isoformat(timespec="seconds")

    entry = f"""
## {timestamp}

- Operation: {str(operation).upper()}
- File: {filename}

### Review

{recommendation_text}
"""

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)

def log_file_operation(filename, operation, reason="", backup_path=None):
    log_path = os.path.join(LEO_FILES_PATH, "OPERATION_LOG.md")
    timestamp = datetime.now().isoformat(timespec="seconds")

    entry = f"""
## {timestamp}

- Operation: {operation.upper()}
- File: {filename}
- Reason: {reason or "No reason provided"}
- Backup: {backup_path or "None"}
"""

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)



def load_tasks():
    if not os.path.exists(TASK_QUEUE_PATH):
        return {"tasks": []}
    try:
        with open(TASK_QUEUE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"tasks": []}

def save_tasks(data):
    os.makedirs(LEO_FILES_PATH, exist_ok=True)
    with open(TASK_QUEUE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def create_task(goal, assigned_role="leader", requested_by="caleb", inputs=None):
    data = load_tasks()
    inputs = inputs or {}
    intent = inputs.get("intent") or classify_intent_simple(goal)

    task = {
        "task_id": str(uuid.uuid4())[:8],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "pending",
        "requested_by": requested_by,
        "assigned_role": assigned_role,
        "intent": intent,
        "goal": goal,
        "inputs": inputs,
        "next_action": None,
        "result": None,
        "needs_user": False,
        "memory_candidate": None
    }
    data["tasks"].append(task)
    save_tasks(data)
    return task

def update_task(task_id, updates):
    data = load_tasks()
    for task in data.get("tasks", []):
        if task["task_id"] == task_id:
            task.update(updates)
            task["updated_at"] = datetime.now().isoformat(timespec="seconds")
            save_tasks(data)
            return task
    return None

def get_next_pending_task():
    data = load_tasks()
    for task in data.get("tasks", []):
        if task.get("status") == "pending":
            return task
    return None


def normalize_task_prerequisites(prerequisites):
    if not prerequisites:
        return []

    if isinstance(prerequisites, dict):
        prerequisites = [prerequisites]

    if not isinstance(prerequisites, list):
        return []

    normalized = []
    for item in prerequisites:
        if not isinstance(item, dict):
            continue

        file_name = (item.get("file") or item.get("path") or item.get("target_file") or "").strip()
        must_contain = item.get("must_contain") or []

        if isinstance(must_contain, str):
            must_contain = [must_contain]

        must_contain = [str(x).strip() for x in must_contain if str(x).strip()]

        if file_name and must_contain:
            normalized.append({
                "file": file_name,
                "must_contain": must_contain,
                "reason": (item.get("reason") or "").strip()
            })

    return normalized


def normalize_task_dependency_titles(depends_on):
    if not depends_on:
        return []

    if isinstance(depends_on, str):
        depends_on = [depends_on]

    if not isinstance(depends_on, list):
        return []

    normalized = []
    for item in depends_on:
        title = str(item or "").strip()
        if title:
            normalized.append(title)

    return normalized


def canonicalize_dependency_title(title):
    return " ".join(str(title or "").strip().lower().split())


def resolve_task_file_path(task, file_name):
    file_name = (file_name or "").strip()

    if not file_name:
        return None

    if os.path.isabs(file_name):
        return file_name

    if file_name.startswith("CREATE_PROJECTS/"):
        return safe_knowledge_path(file_name)

    inputs = task.get("inputs") or {}
    project_slug = inputs.get("approved_create_project")

    if project_slug and not file_name.startswith(f"CREATE_PROJECTS/{project_slug}/"):
        return safe_knowledge_path(f"CREATE_PROJECTS/{project_slug}/{file_name}")

    return safe_knowledge_path(file_name)


def check_task_prerequisites(task):
    inputs = task.get("inputs") or {}
    prerequisites = normalize_task_prerequisites(inputs.get("prerequisites"))

    if not prerequisites:
        return {"ok": True, "failures": [], "checked": []}

    failures = []
    checked = []

    for prereq in prerequisites:
        file_name = prereq.get("file", "")
        required_strings = prereq.get("must_contain", [])
        resolved_path = resolve_task_file_path(task, file_name)

        check_record = {
            "file": file_name,
            "resolved_path": resolved_path,
            "must_contain": required_strings
        }
        checked.append(check_record)

        if not resolved_path or not Path(resolved_path).exists():
            failures.append({
                "file": file_name,
                "resolved_path": resolved_path,
                "missing": required_strings,
                "reason": "file_not_found"
            })
            continue

        try:
            content = Path(resolved_path).read_text(encoding="utf-8")
        except Exception as e:
            failures.append({
                "file": file_name,
                "resolved_path": resolved_path,
                "missing": required_strings,
                "reason": f"file_unreadable: {e}"
            })
            continue

        missing = [s for s in required_strings if s not in content]

        if missing:
            failures.append({
                "file": file_name,
                "resolved_path": resolved_path,
                "missing": missing,
                "reason": "missing_required_strings"
            })

    return {"ok": not failures, "failures": failures, "checked": checked}


def block_task_for_failed_prerequisites(task, prereq_result):
    lines = ["Prerequisite check failed before compiler/run."]

    for failure in prereq_result.get("failures", []):
        lines.append(f"- File: {failure.get('file')}")
        lines.append(f"  Reason: {failure.get('reason')}")
        missing = failure.get("missing") or []
        if missing:
            lines.append(f"  Missing: {', '.join(str(x) for x in missing)}")

    return update_task(task["task_id"], {
        "status": "blocked_prerequisite",
        "result": "\n".join(lines),
        "next_action": "Resolve prerequisite task first, then reset this task to pending.",
        "needs_user": False
    })


def get_next_runnable_task_with_prereq_check():
    data = load_tasks()
    blocked = []

    for task in data.get("tasks", []):
        if task.get("status") != "pending":
            continue

        prereq_result = check_task_prerequisites(task)

        if prereq_result.get("ok"):
            return task, blocked

        blocked_task = block_task_for_failed_prerequisites(task, prereq_result)
        blocked.append(blocked_task or task)

    return None, blocked

def get_task(task_id):
    data = load_tasks()
    for task in data.get("tasks", []):
        if task["task_id"] == task_id:
            return task
    return None

def needs_troubleshooting(text: str) -> bool:
    keywords = ["error","bug","fail","failure","broken","debug","traceback","exception","crash","not working","fix","webhook"]
    return any(k in text.lower() for k in keywords)

def needs_project_status(text: str) -> bool:
    keywords = ["status","next action","next step","what are we working on","blocker","priority","project"]
    return any(k in text.lower() for k in keywords)




CREATE_REQUIRED_FIELDS = [
    "Goal / Outcome",
    "User / Audience",
    "First Usable Version",
    "Success Criteria",
    "Platform / Runtime",
    "Data / Persistence",
    "Integrations",
    "Permissions / Auth",
    "UI / UX Requirements",
    "Main Workflow",
    "Edge Cases",
    "Constraints / Non-Goals",
    "Priority Tradeoff",
    "Validation Plan",
    "Deployment / Running Environment",
    "Maintenance"
]


def extract_create_plan_fields(plan):
    fields = {}
    text = (plan or "").split("## Clarification Log", 1)[0]

    for field in CREATE_REQUIRED_FIELDS:
        patterns = [f"### {field}", f"## {field}"]
        found = None

        for marker in patterns:
            idx = text.find(marker)
            if idx != -1:
                found = (idx, marker)
                break

        if not found:
            fields[field] = ""
            continue

        idx, marker = found
        next_positions = []

        for next_marker in ["\n### ", "\n## "]:
            pos = text.find(next_marker, idx + len(marker))
            if pos != -1:
                next_positions.append(pos)

        end = min(next_positions) if next_positions else len(text)
        value = text[idx + len(marker):end].strip()
        fields[field] = value

    return fields

def create_plan_has_pending_required_fields(plan):
    # Only inspect the structured scope section.
    # Ignore Clarification Log / raw evidence, which may contain duplicate field names.
    plan = (plan or "").split("## Clarification Log", 1)[0]

    pending = []
    for field in CREATE_REQUIRED_FIELDS:
        marker3 = f"### {field}"
        marker2 = f"## {field}"

        idx3 = plan.find(marker3)
        idx2 = plan.find(marker2)

        if idx3 == -1 and idx2 == -1:
            pending.append(field)
            continue

        if idx3 != -1 and (idx2 == -1 or idx3 < idx2):
            idx = idx3
            marker = marker3
        else:
            idx = idx2
            marker = marker2

        next_idx3 = plan.find("\n### ", idx + len(marker))
        next_idx2 = plan.find("\n## ", idx + len(marker))

        candidates = [x for x in [next_idx3, next_idx2] if x != -1]
        next_idx = min(candidates) if candidates else -1

        section = plan[idx: next_idx if next_idx != -1 else len(plan)]

        if "Pending" in section or "Pending clarification" in section:
            pending.append(field)

    return pending



def create_research_request_path(project_slug):
    project_slug = create_project_slug(project_slug)
    rel_path = f"CREATE_PROJECTS/{project_slug}/PROJECT_RESEARCH_REQUESTS.md"
    return safe_knowledge_path(rel_path), rel_path

def create_field_status_path(project_slug):
    project_slug = create_project_slug(project_slug)
    rel_path = f"CREATE_PROJECTS/{project_slug}/CREATE_FIELD_STATUS.json"
    return safe_knowledge_path(rel_path), rel_path

def load_create_field_status(project_slug):
    path, _ = create_field_status_path(project_slug)
    if not os.path.exists(path):
        return {"answered_fields": {}, "research_requested_fields": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"answered_fields": {}, "research_requested_fields": {}}

def save_create_field_status(project_slug, status):
    path, rel_path = create_field_status_path(project_slug)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)
    return rel_path

def detect_answered_create_field(answer_text):
    text = answer_text or ""
    for field in CREATE_REQUIRED_FIELDS:
        if text.lower().strip().startswith(field.lower() + ":"):
            return field
    for field in CREATE_REQUIRED_FIELDS:
        if ("\n" + field.lower() + ":") in text.lower():
            return field
    return None

def create_answer_requests_research(answer_text):
    return "/research" in (answer_text or "").lower()

def create_project_slug(name):
    name = (name or "").strip().lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    name = name.strip("_")
    return name or "untitled_project"

def create_project_path(project_slug, filename="PROJECT_PLAN.md"):
    project_slug = create_project_slug(project_slug)
    filename = filename.strip().lstrip("/")
    rel_path = f"CREATE_PROJECTS/{project_slug}/{filename}"
    return safe_knowledge_path(rel_path), rel_path

def auto_create_project_file(project_slug, content):
    file_path, rel_path = create_project_path(project_slug)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    if os.path.exists(file_path):
        return False, rel_path

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content.strip() + "\n")

    log_file_operation(rel_path, "create", reason="auto-created CREATE project plan", backup_path=None)
    return True, rel_path

def auto_append_project_file(project_slug, content):
    file_path, rel_path = create_project_path(project_slug)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    with open(file_path, "a", encoding="utf-8") as f:
        f.write("\n\n" + content.strip() + "\n")

    log_file_operation(rel_path, "append", reason="auto-appended CREATE project clarification", backup_path=None)
    return rel_path

def classify_intent_simple(goal):
    text = (goal or "").lower()

    fix_words = ["bug", "error", "broken", "fail", "failing", "fix", "debug", "not working", "crash", "traceback"]
    learn_words = ["learn", "remember", "memory", "explain", "understand", "lesson", "store", "knowledge", "pattern"]
    create_words = ["create", "build", "make", "add", "implement", "design", "draft", "generate", "write"]

    if any(w in text for w in fix_words):
        return "FIX"

    if any(w in text for w in learn_words):
        return "LEARN"

    if any(w in text for w in create_words):
        return "CREATE"

    return "PLAN"

def intent_guidance(intent):
    intent = (intent or "PLAN").upper()

    if intent == "CREATE":
        return """
Intent: CREATE

Rules:
- For creation tasks, prefer clarity before implementation.
- If the task is broad or underspecified, draft a plan and identify the minimum clarifying questions.
- Do not blindly implement large new systems without confirming scope.
- If research would improve the design, recommend research before final implementation.
"""

    if intent == "BUILD":
        return """
Intent: BUILD

Rules:
- Make the next useful implementation move.
- Use the approved plan as scope control, not as a reason to stop.
- Do not merely draft a plan unless the task explicitly asks for a plan.
- Prefer staging useful starter code over asking Caleb questions.
- The implementation does not need to be perfect; it needs to be good enough to enter the build/test/fix iteration loop.
- Use reasonable defaults inside the approved scope.
- Ask Caleb only if the task is genuinely impossible or the approved plan is contradictory.
- If creating code, avoid placeholder-only files. Include working state, sample data, calculations, validation hooks, or structure that moves the app forward.
"""
 
    if intent == "FIX":
        return """
Intent: FIX

Rules:
- For fix tasks, minimize friction.
- Do not ask Caleb questions unless critical information is missing.
- Prefer immediate diagnosis, targeted fix, and verification.
- If the first fix fails, retry once with a slightly different approach before escalating.
"""

    if intent == "LEARN":
        return """
Intent: LEARN

Rules:
- For learning tasks, focus on reusable lessons, explanations, and memory candidates.
- Do not force file edits unless Caleb explicitly asks.
- If the lesson is valuable, use memory_candidate.
"""

    return """
Intent: PLAN

Rules:
- For planning tasks, propose the next concrete step.
- Keep the scope narrow and actionable.
"""

def wants_structured(text: str) -> bool:
    t = text.lower()
    return t.startswith("/agent") or "structured output" in t or "json mode" in t

def build_system_prompt(user_message: str, structured: bool = False) -> str:
    prompt = f"""
You are Leo, Caleb's local AI agent system.

=== IDENTITY ===
{load_file("IDENTITY.md")}

=== SOUL ===
{load_file("SOUL.md")}

Core rules:
- Be direct, practical, and action-oriented.
- Do not restate protocols.
- Do not explain the whole framework.
- Give only the next useful action unless asked for more.
- Maximum response length: 8 bullets or fewer.
"""
    if structured:
        prompt += """
STRUCTURED OUTPUT MODE:
You MUST respond ONLY with valid JSON.
No markdown. No text before or after JSON.

{
  "role": "leader",
  "phase": "triage | plan | execute | test | document | escalate | none",
  "thought": "brief reasoning",
  "action": "next concrete action",
  "result": "answer or output",
  "needs_user": true,
  "next_agent": "none | coder | researcher | tester | documenter | leader",
  "memory_candidate": {
    "should_store": false,
    "lesson": "",
    "why_it_matters": "",
    "confidence": 0
  }
}
"""
    if needs_project_status(user_message):
        prompt += f"\n=== PROJECT STATUS ===\n{load_file('PROJECT_STATUS.md')}\n"

    if needs_troubleshooting(user_message):
        prompt += f"\n=== TROUBLESHOOTING ===\n{load_file('GENERAL-TROUBLESHOOTING-FRAMEWORK-V5-FINAL.md')}\n"

    return prompt

def build_task_prompt(task):
    inputs = task.get("inputs", {}) or {}

    target_file = (
        inputs.get("target_file")
        or inputs.get("file_path")
        or task.get("target_file")
        or ""
    )

    required_operation_info = recommended_file_operation(target_file) if target_file else None
    required_operation = required_operation_info.get("operation") if required_operation_info else "create"
    required_operation_reason = required_operation_info.get("reason") if required_operation_info else "No target file detected."

    edit_mode = recommended_edit_mode(task, target_file, required_operation)

    task.setdefault("inputs", {})
    task["inputs"]["required_operation"] = required_operation
    task["inputs"]["required_operation_reason"] = required_operation_reason
    task["inputs"]["edit_mode"] = edit_mode

    existing_file_context = ""
    existing_content = ""

    if target_file:
        try:
            file_path = safe_knowledge_path(target_file)
            if Path(file_path).exists():
                existing_content = Path(file_path).read_text(encoding="utf-8")
        except Exception as e:
            existing_file_context = f"""
Existing target file:
[Could not load existing target file: {e}]
"""

    if required_operation == "edit":
        numbered_existing_content = add_line_numbers_for_prompt(existing_content) if existing_content else ""

        existing_file_context = f"""
Existing target file with line numbers:
--- BEGIN NUMBERED TARGET FILE ---
{numbered_existing_content[:24000]}
--- END NUMBERED TARGET FILE ---
"""

        mode_guidance = edit_mode_guidance(edit_mode)

        return f"""
You are implementing a mode-aware edit for Leo.

{mode_guidance}

Task:
{task.get("goal")}

Target file:
{target_file}

System-required operation:
edit

Why:
{required_operation_reason}

The system owns operation choice. You are ONLY allowed to produce an edit patch.
Do not produce full-file CONTENT.
Do not mention create or replace.

Current file view:
{existing_file_context}

Search/replace edit rules:
- Prefer SEARCH/REPLACE edit blocks.
- SEARCH must be exact contiguous code from the current file.
- SEARCH should be the smallest stable anchor that fully contains the local change.
- REPLACE must include the SEARCH code with only the needed minimal changes.
- Preserve existing behavior inside SEARCH unless you explicitly name an intentional adaptation.
- If new functionality can be added without removing existing behavior, carry the existing behavior forward.
- If an existing state key, handler, input binding, rendered label, or behavior changes meaning, list it under INTENTIONAL_ADAPTATIONS.
- INTENTIONAL_ADAPTATIONS is only for existing behavior that changes or is removed; new additions are not adaptations.
- Use multiple EDIT_BLOCKS when the requested behavior depends on separate mechanisms.
- Each requested UI/behavior should be added in one location only.

Fallback line-range rules:
- Use EDIT_RANGE_START / EDIT_RANGE_END only when exact SEARCH anchoring is not practical.
- Line numbers are 1-based and inclusive.
- REPLACE is the complete new version of the selected lines.
- Use plain code without line numbers, markdown fences, or language labels.

Return exactly this format:

STATUS: done
RESULT: short summary
FILE: {target_file}
OPERATION: edit
REASON: short reason

IMPLEMENTATION_ANALYSIS:
TASK_INTERPRETATION:
- explain what the task requires in this file
APPROACHES_CONSIDERED:
- list viable approaches considered
CHOSEN_APPROACH:
- explain the selected approach and why
REJECTED_APPROACHES:
- explain approaches not taken and why
ASSUMPTIONS:
- list assumptions made, or "none"
STATE_SHAPE_IMPACT:
- name every existing state/data key whose shape or meaning changes, or "none"
HANDLER_IMPACT:
- name every existing handler whose responsibility changes, or "none"
UI_BEHAVIOR_IMPACT:
- name every existing rendered label/input/section whose meaning changes, or "none"
EXISTING_BEHAVIOR_IMPACT:
- name every existing behavior changed, removed, replaced, or "none"
RISKS:
- list risks/tradeoffs introduced, or "none"
FOLLOWUP_OBLIGATIONS:
- list follow-up work needed because of this implementation, or "none"

EXPECTED_AFTER:
- baseline facts that should remain true
- new facts expected after this task
- behavior that should change
- behavior that should not change
- intentional structural or behavioral adaptations
INTENTIONAL_ADAPTATIONS:
- list existing state keys, handlers, input bindings, rendered labels, or behaviors whose meaning changes
- list existing behavior intentionally removed or replaced
- explain why each adaptation is necessary
- write "none" if all existing behavior is preserved

EDIT_BLOCK 1
PURPOSE: short description of this region's role
SEARCH:
exact existing contiguous code from the current file
REPLACE:
same code with minimal changes

Use additional edit blocks when the behavior depends on separate mechanisms:

EDIT_BLOCK 2
PURPOSE: short description of this region's role
SEARCH:
exact existing contiguous code from the current file
REPLACE:
same code with minimal changes

EDIT_BLOCK 3
PURPOSE: short description of this region's role
SEARCH:
exact existing contiguous code from the current file
REPLACE:
same code with minimal changes
"""

    # create / replace prompt
    existing_file_context = ""
    if existing_content:
        existing_file_context = f"""
Existing target file:
--- BEGIN EXISTING TARGET FILE ---
{existing_content[:12000]}
--- END EXISTING TARGET FILE ---
"""

    return f"""
You are implementing a full-file {required_operation} operation for Leo.

Task:
{task.get("goal")}

Target file:
{target_file}

System-required operation:
{required_operation}

Why:
{required_operation_reason}

The system owns operation choice. You are ONLY allowed to produce CONTENT for this operation.
Do not output EDIT_RANGE_START.
Do not output EDIT_RANGE_END.
Do not output SEARCH or REPLACE.

{existing_file_context}

Return exactly this format:

STATUS: done
RESULT: short summary
FILE: {target_file}
OPERATION: {required_operation}
REASON: short reason
EXPECTED_AFTER:
- baseline facts that should remain true
- new facts expected after this task
- behavior that should change
- behavior that should not change
- intentional structural or behavioral adaptations
CONTENT:
complete file contents here
"""

def extract_task_target_file(text):
    text = text or ""

    patterns = [
        r"Build exactly ONE file:\s*`?([A-Za-z0-9_./-]+\.(?:js|jsx|ts|tsx|css|json|md))`?",
        r"Target file:\s*`?([A-Za-z0-9_./-]+\.(?:js|jsx|ts|tsx|css|json|md))`?",
        r"FILE:\s*`?([A-Za-z0-9_./-]+\.(?:js|jsx|ts|tsx|css|json|md))`?",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()

    return ""




def append_create_build_state(project_slug, text):
    if not project_slug or not text:
        return

    try:
        state_path, _ = create_project_path(project_slug, "PROJECT_BUILD_STATE.md")
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)
        with open(state_path, "a", encoding="utf-8") as f:
            f.write(text.strip() + "\n\n")
    except Exception as e:
        print(f"Failed to append CREATE build state: {e}")


def load_create_build_state(project_slug, limit=12000):
    if not project_slug:
        return ""

    try:
        state_path, _ = create_project_path(project_slug, "PROJECT_BUILD_STATE.md")
        if not Path(state_path).exists():
            return ""
        text = Path(state_path).read_text(encoding="utf-8")
        return text[-limit:]
    except Exception:
        return ""

def score_file_maturity(filename):
    try:
        path = safe_knowledge_path(filename)
        if not Path(path).exists():
            return {
                "exists": False,
                "score": 0,
                "classification": "new",
                "reasons": ["File does not exist yet."]
            }

        content = Path(path).read_text(encoding="utf-8")
    except Exception as e:
        return {
            "exists": False,
            "score": 0,
            "classification": "unknown",
            "reasons": [f"Could not inspect file maturity: {e}"]
        }

    score = 0
    reasons = []

    lines = [line for line in content.splitlines() if line.strip()]
    line_count = len(lines)

    if line_count >= 75:
        score += 1
        reasons.append(f"File has {line_count} non-empty lines.")
    if line_count >= 150:
        score += 1
        reasons.append("File is large enough that full replacement is riskier.")

    imports = re.findall(r"^\s*import\s+.*?;", content, flags=re.MULTILINE)
    if len(imports) >= 2:
        score += 1
        reasons.append("Multiple imports detected.")

    if re.search(r"from\s+['\"](\./|\../)", content):
        score += 1
        reasons.append("Local project imports detected.")

    if "useEffect" in content or "localStorage" in content:
        score += 1
        reasons.append("State lifecycle or persistence behavior detected.")

    handlers = re.findall(r"\b(handle[A-Z][A-Za-z0-9_]*|on[A-Z][A-Za-z0-9_]*)\b", content)
    if len(set(handlers)) >= 2:
        score += 1
        reasons.append("Multiple handlers/interactions detected.")

    components = re.findall(r"\b(?:function|const)\s+([A-Z][A-Za-z0-9_]*)\b", content)
    if len(set(components)) >= 2:
        score += 1
        reasons.append("Multiple components/functions detected.")

    feature_terms = [
        "DataEntry", "Dashboard", "Forecast", "Transactions",
        "Recurring", "Assets", "Liabilities", "budgetData",
        "currentCash", "recurringIncome", "recurringExpenses",
        "upcomingTransactions"
    ]
    matched_terms = [term for term in feature_terms if term in content]
    if len(matched_terms) >= 3:
        score += 1
        reasons.append("Existing project-specific feature/data structures detected.")

    placeholder_terms = [
        "TODO", "placeholder", "Add financial metrics", "Add more input fields",
        "Simple UI element for testing purposes"
    ]
    has_placeholder = any(term.lower() in content.lower() for term in placeholder_terms)

    if not has_placeholder and line_count >= 25:
        score += 1
        reasons.append("File appears to contain non-placeholder implementation.")
    elif has_placeholder:
        reasons.append("Starter/placeholder text detected.")

    if score <= 2:
        classification = "starter"
    elif score <= 5:
        classification = "developing"
    else:
        classification = "mature"

    if not reasons:
        reasons.append("File is small and simple.")

    return {
        "exists": True,
        "score": score,
        "classification": classification,
        "reasons": reasons
    }



def recommended_edit_mode(task, filename="", operation="edit"):
    goal = str((task or {}).get("goal", "")).lower()
    inputs = (task or {}).get("inputs", {}) or {}

    explicit = str(inputs.get("edit_mode") or "").lower().strip()
    if explicit in ["surgical", "adaptive", "refactor", "replacement"]:
        return explicit

    if operation in ["create", "replace"]:
        return "replacement"

    refactor_terms = [
        "refactor", "clean up", "simplify", "extract", "rename",
        "deduplicate", "reorganize", "improve readability"
    ]

    adaptive_terms = [
        "add form", "add feature", "new behavior", "support",
        "allow", "track", "manage", "dynamic", "array", "schema",
        "state shape", "data shape", "multiple"
    ]

    surgical_terms = [
        "change text", "rename label", "add button", "add paragraph",
        "fix typo", "small change", "update copy"
    ]

    if any(term in goal for term in refactor_terms):
        return "refactor"

    if any(term in goal for term in adaptive_terms):
        return "adaptive"

    if any(term in goal for term in surgical_terms):
        return "surgical"

    return "surgical"


def edit_mode_guidance(edit_mode):
    if edit_mode == "adaptive":
        return """
EDIT_MODE: adaptive

Adaptive mode philosophy:
- Add requested behavior while preserving existing behavior unless an intentional adaptation is required.
- Structural changes are allowed when they support the task.
- Any changed state shape, handler responsibility, input binding, rendered label, or behavior must be named in INTENTIONAL_ADAPTATIONS.
- Existing behavior is authoritative by default; insufficiency alone does not silently authorize replacement.
- Prefer SEARCH/REPLACE edit blocks with exact anchors and minimal changes.
"""

    if edit_mode == "refactor":
        return """
EDIT_MODE: refactor

Refactor mode philosophy:
- Improve internal implementation while preserving external behavior.
- User-visible behavior, data contracts, props, outputs, and existing flows should remain equivalent.
- Any behavior change is outside refactor mode unless explicitly requested.
- Prefer SEARCH/REPLACE edit blocks with exact anchors and minimal changes.
"""

    if edit_mode == "replacement":
        return """
EDIT_MODE: replacement

Replacement mode philosophy:
- Full-file replacement is allowed when the system-selected operation permits it.
- Preserve required project contracts and expected behavior.
- Use CONTENT for full-file output when requested by the operation contract.
"""

    return """
EDIT_MODE: surgical

Surgical mode philosophy:
- Make the smallest safe change that satisfies the task.
- Preserve existing state shape, handlers, input bindings, rendered labels, and behavior.
- Use SEARCH/REPLACE edit blocks with exact anchors and minimal changes.
- If a broader change seems useful but is not required, leave it for a separate task.
"""



def recommended_file_operation(filename):
    """
    System-owned operation gate.
    Temporary strict mode: any existing non-trivial file should be edited, not replaced.
    """
    maturity = score_file_maturity(filename)

    if not maturity.get("exists"):
        return {
            "operation": "create",
            "reason": "Target file does not exist.",
            "maturity": maturity
        }

    score = maturity.get("score", 0)
    classification = maturity.get("classification", "unknown")

    if score >= 1:
        return {
            "operation": "edit",
            "reason": f"Existing file has maturity score {score} ({classification}); use surgical edit to preserve current behavior.",
            "maturity": maturity
        }

    return {
        "operation": "replace",
        "reason": f"Existing file has maturity score {score} ({classification}); replace is allowed for very small starter files.",
        "maturity": maturity
    }


def replace_risk_from_maturity(filename, operation):
    if operation != "replace":
        return None

    maturity = score_file_maturity(filename)

    if not maturity.get("exists"):
        return maturity

    classification = maturity.get("classification")
    score = maturity.get("score", 0)

    if classification == "starter":
        maturity["decision"] = "allow"
        maturity["risk_label"] = "MEDIUM — starter file replace. Existing file appears small or early-stage."
    elif classification == "developing":
        maturity["decision"] = "allow"
        maturity["risk_label"] = "HIGH — developing file replace. Review carefully because existing behavior may be overwritten."
    else:
        maturity["decision"] = "block"
        maturity["risk_label"] = "BLOCKED — mature file replace. Use an edit-style task or explicit override."

    maturity["summary"] = f"File maturity score: {score} ({classification})"
    return maturity

def validate_task_tool_limits(task, filename, content, operation):
    inputs = task.get("inputs", {}) or {}
    limits = inputs.get("tool_limits") or {}

    writable_files = limits.get("writable_files") or []
    max_file_writes = limits.get("max_file_writes")

    if writable_files and filename not in writable_files:
        return False, f"Tool limit violation: this task may only write {writable_files}, but tried to write `{filename}`."

    if max_file_writes is not None and int(max_file_writes) < 1:
        return False, "Tool limit violation: this task is not allowed to write files."

    if limits.get("allow_multi_file_output") is False:
        # Catch common accidental second-file output embedded in content.
        extra_file_markers = [
            "/* src/",
            "// src/",
            "CSS (src/",
            "HTML (src/",
            "```css",
            "```html",
        ]
        if any(marker in content for marker in extra_file_markers):
            return False, "Tool limit violation: task is limited to one file, but the staged content appears to include extra file content."

    return True, ""




def extract_baseline_js(text):
    text = text or ""
    m = re.search(
        r"BASELINE_JS:\s*(.*?)(?=\nVALIDATION_JS:|\nCONTENT:|\nSTATUS:|\Z)",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )
    if not m:
        return ""

    snippet = m.group(1).strip()

    if snippet.startswith("```"):
        snippet = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", snippet)
        snippet = re.sub(r"\n?```$", "", snippet).strip()

    # Strip loose language labels like:
    # BASELINE_JS:
    # javascript
    # const x = ...
    snippet = re.sub(r"^(javascript|js)\s*\n", "", snippet, flags=re.IGNORECASE).strip()

    return snippet



def extract_implementation_analysis(text):
    text = text or ""
    match = re.search(
        r"IMPLEMENTATION_ANALYSIS:\s*(.*?)(?=\nEXPECTED_AFTER:|\nINTENTIONAL_ADAPTATIONS:|\nEDIT_BLOCK\s*\d*|\nVALIDATION_JS:|\nCONTENT:|\nSTATUS:|\Z)",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )
    if not match:
        return ""
    return match.group(1).strip()


def extract_expected_after(text):
    text = text or ""
    match = re.search(
        r"EXPECTED_AFTER:\s*(.*?)(?=\nINTENTIONAL_ADAPTATIONS:|\nEDIT_BLOCK\s*\d*|\nVALIDATION_JS:|\nCONTENT:|\nSTATUS:|\Z)",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )
    if not match:
        return ""
    return match.group(1).strip()


def parse_build_response(text):
    text = text or ""

    def grab(label, next_labels):
        boundary = "|".join([rf"\n{n}:" for n in next_labels])
        if boundary:
            pattern = rf"{label}:\s*(.*?)(?={boundary}|\Z)"
        else:
            pattern = rf"{label}:\s*(.*)\Z"
        m = re.search(pattern, text, flags=re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else ""

    status = grab("STATUS", ["RESULT", "FILE", "OPERATION", "REASON", "CONTENT"]) or "done"
    result = grab("RESULT", ["FILE", "OPERATION", "REASON", "CONTENT"])
    filename = grab("FILE", ["OPERATION", "REASON", "CONTENT"])
    operation = grab("OPERATION", ["REASON", "CONTENT"]) or "create"
    reason = grab("REASON", ["CONTENT"])
    content = grab("CONTENT", [])

    
    if operation.lower().strip() == "edit" and not content:
        if extract_line_range_edit_blocks(text) or extract_edit_blocks(text):
            content = "[EDIT_PATCH_PENDING_RUNTIME_APPLICATION]"
    if not content and reason:
        parts = re.split(r"\n\s*(?:jsx|javascript|js|tsx|typescript)\s*\n", reason, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            reason = parts[0].strip()
            content = parts[1].strip()

    if not content:
        m = re.search(r"(import\s+React[\s\S]*)", text)
        if m:
            content = m.group(1).strip()

    operation = operation.lower().strip()
    if operation not in ["create", "edit", "replace", "append"]:
        operation = "create"

    old_text = None
    new_text = None

    if operation == "edit":
        old_match = re.search(r"OLD:\s*(.*?)(?=\nNEW:|\Z)", content, flags=re.DOTALL | re.IGNORECASE)
        new_match = re.search(r"NEW:\s*(.*)\Z", content, flags=re.DOTALL | re.IGNORECASE)

        if old_match and new_match:
            old_text = old_match.group(1).strip()
            new_text = new_match.group(1).strip()
            content = new_text

    # Trim assistant explanation accidentally included after the code.
    content = re.split(r"\n\s*This component\b|\n\s*The component\b|\n\s*Explanation\b", content, maxsplit=1)[0].strip()

    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", content)
        content = re.sub(r"\n?```$", "", content).strip()

    if operation == "edit":
        if old_text and old_text.startswith("```"):
            old_text = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", old_text)
            old_text = re.sub(r"\n?```$", "", old_text).strip()

        if new_text and new_text.startswith("```"):
            new_text = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", new_text)
            new_text = re.sub(r"\n?```$", "", new_text).strip()

    has_line_range_edit = operation == "edit" and bool(extract_line_range_edit_blocks(text))
    has_search_replace_edit = operation == "edit" and bool(extract_edit_blocks(text))

    if not filename:
        return None

    if operation == "edit":
        if not content and (has_line_range_edit or has_search_replace_edit):
            content = "[EDIT_PATCH_PENDING_RUNTIME_APPLICATION]"
        elif not (old_text and new_text is not None) and not has_line_range_edit and not has_search_replace_edit:
            return None
    elif not content:
        return None

    status = status.lower().strip()
    if status not in ["done", "blocked", "needs_user"]:
        status = "done"

    implementation_analysis = extract_implementation_analysis(text)

    file_operation = {
        "should_stage": True,
        "operation": operation,
        "filename": filename,
        "content": content,
        "reason": reason or f"Implementation for {filename}.",
        "implementation_analysis": implementation_analysis
    }

    if operation == "edit":
        file_operation["old_text"] = old_text
        file_operation["new_text"] = new_text

        line_edit = extract_line_range_edit_blocks(text)
        if line_edit:
            file_operation["edit_range_start"] = line_edit.get("start")
            file_operation["edit_range_end"] = line_edit.get("end")
            file_operation["replace"] = line_edit.get("replace", "")

        search_replace_edit = extract_edit_blocks(text)
        if search_replace_edit:
            file_operation["search"] = search_replace_edit.get("search", "")
            file_operation["replace"] = search_replace_edit.get("replace", "")

    return {
        "status": status,
        "result": result or f"Prepared file operation for {filename}.",
        "next_action": None,
        "needs_user": status == "needs_user",
        "memory_candidate": {"should_store": False, "lesson": "", "why_it_matters": "", "confidence": 0},
        "file_operation": file_operation
    }


async def call_ollama(messages, temperature=0.2, model=MODEL):
    client = ollama.AsyncClient(timeout=300.0)
    r = await client.chat(
        model=model,
        messages=messages,
        stream=False,
        options={"num_ctx": 16384, "temperature": temperature}
    )
    return r["message"]["content"]

async def call_ollama_stream(messages, msg, temperature=0.2):
    client = ollama.AsyncClient(timeout=300.0)
    full = ""
    async for part in await client.chat(
        model=MODEL,
        messages=messages,
        stream=True,
        options={"num_ctx": 16384, "temperature": temperature}
    ):
        token = part["message"]["content"]
        full += token
        await msg.stream_token(token)
    return full

def clean_json_text(text: str):
    text = text.strip()

    if text.startswith("```json"):
        text = text.replace("```json", "", 1).strip()

    if text.startswith("```"):
        text = text.replace("```", "", 1).strip()

    if text.endswith("```"):
        text = text[:-3].strip()

    return text

def parse_json_or_none(text: str):
    try:
        return json.loads(clean_json_text(text))
    except:
        return None

def extract_target_filename_from_goal(goal):
    goal = goal or ""
    known_files = [
        "PROJECT_STATUS.md",
        "AGENTS.md",
        "MEMORY.md",
        "SOUL.md",
        "IDENTITY.md",
        "OPERATING_MODEL.md",
        "INFRASTRUCTURE_STATUS.md",
        "TROUBLESHOOTING_REFERENCE.md",
        "GENERAL-TROUBLESHOOTING-FRAMEWORK-V5-FINAL.md"
    ]

    for filename in known_files:
        if filename.lower() in goal.lower():
            return filename

    return None

def extract_preservation_anchors(filename, max_anchors=60):
    try:
        file_path = safe_knowledge_path(filename)

        if not Path(file_path).exists():
            return ""

        content = Path(file_path).read_text(encoding="utf-8")
        anchors = []

        # Imports
        for match in re.finditer(r"^\s*import\s+(.+?)\s+from\s+['\"](.+?)['\"]", content, re.MULTILINE):
            anchors.append(f"import: {match.group(1).strip()} from {match.group(2).strip()}")

        # React/function/class components or named functions
        for match in re.finditer(r"\b(?:const|function|class)\s+([A-Za-z_][A-Za-z0-9_]*)", content):
            anchors.append(f"declaration: {match.group(1)}")

        # Props in simple component signatures
        for match in re.finditer(r"\((\{[^)]{1,200}\})\)\s*=>", content):
            props = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", match.group(1))
            for prop in props:
                anchors.append(f"prop: {prop}")

        # Structural React state anchors: const [x, setX] = useState(...)
        for match in re.finditer(r"const\s*\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\]\s*=\s*useState\s*\((.*?)\)\s*;", content, re.DOTALL):
            state_name = match.group(1)
            setter_name = match.group(2)
            initial_value = match.group(3).strip()

            if initial_value.startswith("{"):
                anchors.append(f"state pattern: {state_name} object managed by {setter_name}")
            elif initial_value.startswith("["):
                anchors.append(f"state pattern: {state_name} array managed by {setter_name}")
            else:
                anchors.append(f"state pattern: {state_name} value managed by {setter_name}")

        # Object-state update/spread patterns
        for match in re.finditer(r"set([A-Za-z0-9_]+)\s*\(\s*\(?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)?\s*=>\s*\(\s*\{\s*\.\.\.\2", content, re.DOTALL):
            anchors.append(f"state update pattern: set{match.group(1)} preserves previous object with ...{match.group(2)}")

        if "...prevData" in content:
            anchors.append("state update pattern: setFormData(prevData => ({ ...prevData, ... }))")

        if "[name]: value" in content:
            anchors.append("generic input update pattern: [name]: value updates matching formData key")

        # Submit patterns
        for match in re.finditer(r"\bonSubmit\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", content):
            anchors.append(f"submit pattern: onSubmit({match.group(1)})")

        # value={formData.someKey} style bindings
        for match in re.finditer(r"value=\{\s*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\}", content):
            anchors.append(f"binding pattern: value={{{match.group(1)}.{match.group(2)}}}")

        # Dynamic list/map patterns
        for match in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\.map\s*\(", content):
            anchors.append(f"render pattern: {match.group(1)}.map(...)")

        # Object keys, with noisy capitalized label fragments filtered out
        for match in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*:", content):
            key = match.group(1)
            if key not in {"http", "https"} and not key[0].isupper():
                anchors.append(f"state/data key: {key}")

        # Handler/function names
        for match in re.finditer(r"\b(handle[A-Za-z0-9_]+)\b", content):
            anchors.append(f"handler: {match.group(1)}")

        # Common JSX labels/headings
        for match in re.finditer(r"<(?:h1|h2|h3|label)[^>]*>([^<]{2,80})</(?:h1|h2|h3|label)>", content):
            label = " ".join(match.group(1).split())
            if label:
                anchors.append(f"rendered label/heading: {label}")

        # De-dupe while preserving order
        seen = set()
        unique = []
        for anchor in anchors:
            if anchor not in seen:
                seen.add(anchor)
                unique.append(anchor)

        if not unique:
            return ""

        selected = unique[:max_anchors]
        return "\n".join(f"- {anchor}" for anchor in selected)

    except Exception as e:
        return f"[Could not extract preservation anchors for {filename}: {e}]"




def strip_code_fence(text):
    text = text or ""
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    text = re.sub(r"^(jsx|javascript|js|tsx|typescript|python|text)\s*\n", "", text, flags=re.IGNORECASE)
    return text.strip()


def add_line_numbers_for_prompt(content):
    lines = content.splitlines()
    return "\n".join(f"{i + 1:04d} | {line}" for i, line in enumerate(lines))


def strip_leading_line_numbers(text):
    text = text or ""
    cleaned = []
    for line in text.splitlines():
        cleaned.append(re.sub(r"^\s*\d{1,6}\s*\|\s?", "", line))
    return "\n".join(cleaned)


def build_jsx_slice_causal_map(slice_text, base_line=1):
    slice_text = slice_text or ""
    lines = slice_text.splitlines()
    entries = []

    block_start = None
    block_lines = []

    def flush_block():
        nonlocal block_start, block_lines
        if block_start is None or not block_lines:
            return

        block = "\n".join(block_lines)
        facts = extract_jsx_surface_facts(block)
        has_facts = any(facts.get(k) for k in facts)

        if has_facts:
            summary_parts = []
            for key, values in facts.items():
                if values:
                    summary_parts.append(f"{key}: {values}")

            entries.append(
                f"- Source lines {base_line + block_start - 1}-{base_line + block_start + len(block_lines) - 2}\n"
                f"  Causes: {'; '.join(summary_parts)}\n"
                f"  Source block:\n{block}"
            )

        block_start = None
        block_lines = []

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()

        starts_block = (
            stripped.startswith("<div")
            or stripped.startswith("<section")
            or stripped.startswith("<form")
            or stripped.startswith("{formData.")
            or ".map(" in stripped
        )

        if block_start is None and starts_block:
            block_start = idx
            block_lines = [line]
            continue

        if block_start is not None:
            block_lines.append(line)

            joined = "\n".join(block_lines)
            open_divs = len(re.findall(r"<div\b", joined))
            close_divs = len(re.findall(r"</div>", joined))
            likely_complete = (
                (open_divs > 0 and close_divs >= open_divs)
                or stripped.endswith("))}")
                or stripped.endswith("))}")
            )

            if likely_complete and len(block_lines) >= 2:
                flush_block()

    flush_block()

    return "\n\n".join(entries) if entries else "- No causal JSX blocks detected in selected slice."


def build_react_wiring_map(content):
    content = content or ""
    lines = content.splitlines()
    entries = []

    state_match = re.search(r"useState\s*\(\s*\{([\s\S]*?)\}\s*\)", content)
    if state_match:
        state_body = state_match.group(1)
        state_entries = []
        for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^,\n]+)", state_body):
            state_entries.append(f"{m.group(1)}: {m.group(2).strip()}")
        if state_entries:
            entries.append("State shape:\n" + "\n".join(f"- {x}" for x in state_entries))

    handlers = []
    for idx, line in enumerate(lines, start=1):
        m = re.search(r"\bconst\s+(handle[A-Za-z0-9_]+)\s*=\s*\((.*?)\)\s*=>", line)
        if m:
            handlers.append(f"- {m.group(1)} at line {idx}, params: ({m.group(2)})")
    if handlers:
        entries.append("Handler declarations:\n" + "\n".join(handlers))

    mapped = sorted(set(re.findall(r"formData\.([A-Za-z0-9_]+)\.map\s*\(", content)))
    if mapped:
        entries.append("Mapped state collections:\n" + "\n".join(f"- formData.{x}.map(...)" for x in mapped))

    jsx_handler_refs = sorted(set(re.findall(r"on[A-Za-z]+=\{(?:\(\)\s*=>\s*)?([A-Za-z_][A-Za-z0-9_]*)", content)))
    if jsx_handler_refs:
        entries.append("JSX handler references:\n" + "\n".join(f"- {x}" for x in jsx_handler_refs))

    return "\n\n".join(entries) if entries else "- No React wiring facts detected."


async def generate_fim_style_replacement(filename, task_goal, original_content, start, end, proposed_replace, expected_after=""):
    """
    FIM-style middle generation.
    The line range is the targeting layer. This helper regenerates only the middle
    using prefix/suffix context so the model does not rewrite the whole file.
    """
    lines = original_content.splitlines()

    if start < 1 or end < start or end > len(lines):
        return proposed_replace

    prefix = "\n".join(lines[max(0, start - 80):start - 1])
    old_middle = "\n".join(lines[start - 1:end])
    suffix = "\n".join(lines[end:min(len(lines), end + 80)])

    old_slice_surface = extract_jsx_surface_facts(old_middle)
    old_slice_surface_report = "\n".join(
        f"- {key}: {value}" for key, value in old_slice_surface.items() if value
    ) or "- No JSX-visible behavior surface detected."

    old_slice_causal_map = build_jsx_slice_causal_map(old_middle, base_line=start)
    react_wiring_map = build_react_wiring_map(original_content)

    prompt = f"""
You are generating the replacement middle for a surgical file edit.

Target file:
{filename}

Task:
{task_goal}

Expected after:
{expected_after or "No explicit EXPECTED_AFTER provided."}

Observed behavior surface inside the selected old slice:
{old_slice_surface_report}

Causal map for selected old slice:
{old_slice_causal_map}

Full-file React wiring map:
{react_wiring_map}

Use the causal map as the guide for how the old code creates current behavior.
Use the React wiring map as the guide for how state, handlers, mapped collections, and JSX references fit together.
Carry forward or adapt the source blocks responsible for continuing behavior.
Add the new requested behavior with the needed state shape, handler wiring, mapped collection compatibility, and JSX references.

When adding behavior that uses formData.someKey.map(...), the corresponding state key should be array-shaped.
When adding a JSX handler reference, the corresponding handler should exist in the component.
When using an existing generic handler, align the input name shape with what that handler can update.

You are given:
- PREFIX: code before the edit range
- OLD_MIDDLE: code currently inside the edit range
- SUFFIX: code after the edit range
- PROPOSED_REPLACE: an initial replacement draft that may be flawed

Your job:
Return the corrected replacement middle.

Replacement semantics:
- OLD_MIDDLE is removed and replaced by your output.
- Your output is the complete new version of OLD_MIDDLE.
- Carry forward the observed behavior surface from OLD_MIDDLE that should continue to exist.
- Add the new requested behavior from the task into that carried-forward code.
- You may restructure the selected range when that helps the code fit naturally.
- Use PREFIX and SUFFIX as surrounding context.
- Return plain replacement code without markdown fences or language labels.

PREFIX:
---
{prefix}
---

OLD_MIDDLE:
---
{old_middle}
---

PROPOSED_REPLACE:
---
{proposed_replace}
---

SUFFIX:
---
{suffix}
---

Return ONLY replacement middle:
"""

    try:
        result = await call_ollama(
            [
                {"role": "system", "content": "You are a precise code infill engine. Return only the replacement middle."},
                {"role": "user", "content": prompt}
            ],
            model=MODEL,
            temperature=0.0
        )
        cleaned = strip_code_fence((result or "").strip())
        return cleaned if cleaned else proposed_replace
    except Exception:
        return proposed_replace


async def repair_fim_replacement_with_slice_diff(filename, task_goal, old_slice, failed_replace, diff_report, expected_after=""):
    prompt = f"""
Your previous replacement middle failed slice preservation.

Target file:
{filename}

Task:
{task_goal}

Expected after:
{expected_after or "No explicit EXPECTED_AFTER provided."}

OLD_SLICE that must be behavior-preserved:
---
{old_slice}
---

FAILED_REPLACE:
---
{failed_replace}
---

Observed preservation failure:
---
{diff_report}
---

Return a corrected replacement middle.

Replacement semantics:
- OLD_SLICE is removed and replaced by your output.
- Your output is the complete new version of OLD_SLICE.
- Carry forward every behavior listed as missing unless the task intentionally changes it.
- Keep the new requested behavior.
- You may restructure the slice when that helps the code fit naturally.
- Return plain replacement code without markdown fences or language labels.
"""

    try:
        result = await call_ollama(
            [
                {"role": "system", "content": "You repair code infill output. Return only corrected replacement middle."},
                {"role": "user", "content": prompt}
            ],
            model=MODEL,
            temperature=0.0
        )
        cleaned = strip_code_fence((result or "").strip())
        return cleaned if cleaned else failed_replace
    except Exception:
        return failed_replace



def extract_multi_edit_blocks(text):
    text = text or ""

    pattern = re.compile(
        r"EDIT_BLOCK\s*\d*.*?"
        r"EDIT_RANGE_START:\s*(\d+)\s*"
        r"EDIT_RANGE_END:\s*(\d+)\s*"
        r"REPLACE:\s*(.*?)(?=\nEDIT_BLOCK\s*\d*|\nCONTENT:|\nSTATUS:|\nRESULT:|\Z)",
        flags=re.DOTALL | re.IGNORECASE
    )

    blocks = []
    for m in pattern.finditer(text):
        start = int(m.group(1))
        end = int(m.group(2))
        replace = strip_code_fence(m.group(3).strip())
        replace = strip_leading_line_numbers(replace).rstrip()

        if start >= 1 and end >= start and replace:
            blocks.append({
                "start": start,
                "end": end,
                "replace": replace
            })

    if not blocks:
        return None

    # Apply bottom-up so earlier line numbers remain stable.
    return sorted(blocks, key=lambda b: b["start"], reverse=True)


def extract_line_range_edit_blocks(text):
    text = text or ""

    start_match = re.search(r"\nEDIT_RANGE_START:\s*(\d+)", text, flags=re.IGNORECASE)
    end_match = re.search(r"\nEDIT_RANGE_END:\s*(\d+)", text, flags=re.IGNORECASE)
    replace_match = re.search(
        r"\nREPLACE:\s*(.*?)(?=\nCONTENT:|\nSTATUS:|\nRESULT:|\nFILE:|\nOPERATION:|\Z)",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    if not start_match or not end_match or not replace_match:
        return None

    start = int(start_match.group(1))
    end = int(end_match.group(1))
    replace = strip_code_fence(replace_match.group(1).strip())
    replace = strip_leading_line_numbers(replace).rstrip()

    if start < 1 or end < start or not replace:
        return None

    return {"start": start, "end": end, "replace": replace}


def extract_jsx_surface_facts(text):
    text = text or ""

    facts = {
        "labels_headings": set(),
        "input_names": set(),
        "input_values": set(),
        "buttons": set(),
        "mapped_collections": set(),
    }

    for m in re.finditer(r"<label[^>]*>\s*([^<]+?)\s*</label>", text, flags=re.DOTALL):
        facts["labels_headings"].add(re.sub(r"\s+", " ", m.group(1)).strip())

    for m in re.finditer(r"<h[1-6][^>]*>\s*([^<]+?)\s*</h[1-6]>", text, flags=re.DOTALL):
        facts["labels_headings"].add(re.sub(r"\s+", " ", m.group(1)).strip())

    for m in re.finditer(r"<button[^>]*>\s*([^<]+?)\s*</button>", text, flags=re.DOTALL):
        facts["buttons"].add(re.sub(r"\s+", " ", m.group(1)).strip())

    for m in re.finditer(r"name=\{?`?([^\\s}>]+)", text):
        raw = m.group(1).strip().strip('"').strip("'")
        if raw:
            facts["input_names"].add(raw)

    for m in re.finditer(r"value=\{([^}]+)\}", text):
        raw = m.group(1).strip()
        if raw:
            facts["input_values"].add(raw)

    for m in re.finditer(r"formData\.([A-Za-z0-9_]+)\.map\s*\(", text):
        facts["mapped_collections"].add(m.group(1))

    return {k: sorted(v) for k, v in facts.items()}


def compare_jsx_slice_surface(old_slice, new_slice):
    before = extract_jsx_surface_facts(old_slice)
    after = extract_jsx_surface_facts(new_slice)

    missing = {}
    added = {}

    for key in before.keys():
        before_set = set(before.get(key, []))
        after_set = set(after.get(key, []))

        lost = sorted(before_set - after_set)
        gained = sorted(after_set - before_set)

        if lost:
            missing[key] = lost
        if gained:
            added[key] = gained

    ok = not missing

    lines = ["JSX_SLICE_SURFACE_DIFF:", f"preservation_ok: {ok}"]
    if missing:
        lines.append("missing_from_new_slice:")
        for key, values in missing.items():
            lines.append(f"- {key}: {values}")
    else:
        lines.append("missing_from_new_slice: none")

    if added:
        lines.append("added_in_new_slice:")
        for key, values in added.items():
            lines.append(f"- {key}: {values}")
    else:
        lines.append("added_in_new_slice: none")

    return {
        "ok": ok,
        "before": before,
        "after": after,
        "missing": missing,
        "added": added,
        "report": "\n".join(lines),
    }



def apply_multi_edit_blocks(original_content, blocks):
    content = original_content

    for block in sorted(blocks, key=lambda b: b["start"], reverse=True):
        result = apply_line_range_edit_to_content(
            content,
            block.get("start"),
            block.get("end"),
            block.get("replace", "")
        )

        if not result.get("success"):
            return result

        content = result.get("content", "")

    return {
        "success": True,
        "content": content
    }


def apply_line_range_edit_to_content(original_content, start, end, replace):
    lines = original_content.splitlines()

    if start < 1 or end < start:
        return {"success": False, "error": "Invalid edit range."}

    if end > len(lines):
        return {
            "success": False,
            "error": f"Edit range ends at line {end}, but file only has {len(lines)} lines."
        }

    replacement_lines = replace.splitlines()

    new_lines = lines[:start - 1] + replacement_lines + lines[end:]

    trailing_newline = "\n" if original_content.endswith("\n") else ""
    return {
        "success": True,
        "content": "\n".join(new_lines) + trailing_newline,
        "old_text": "\n".join(lines[start - 1:end]),
        "replace": replace
    }


def extract_multi_search_replace_blocks(text):
    text = text or ""

    pattern = re.compile(
        r"EDIT_BLOCK\s*\d*.*?"
        r"SEARCH:\s*(.*?)\s*"
        r"REPLACE:\s*(.*?)(?=\nEDIT_BLOCK\s*\d*|\nCONTENT:|\nSTATUS:|\nRESULT:|\Z)",
        flags=re.DOTALL | re.IGNORECASE
    )

    blocks = []

    for match in pattern.finditer(text):
        search = strip_code_fence(match.group(1).strip())
        replace = strip_code_fence(match.group(2).strip())

        if search:
            blocks.append({
                "search": search,
                "replace": replace
            })

    return blocks or None


def apply_multi_search_replace_blocks(original_content, blocks):
    content = original_content
    applied = []

    for idx, block in enumerate(blocks or [], start=1):
        search = block.get("search", "")
        replace = block.get("replace", "")

        result = apply_search_replace_to_content(content, search, replace)

        if not result.get("success"):
            return {
                "success": False,
                "content": content,
                "applied": applied,
                "error": f"SEARCH/REPLACE block {idx} failed: {result.get('error')}"
            }

        content = result.get("content", content)
        applied.append({
            "index": idx,
            "search": search,
            "replace": replace
        })

    return {
        "success": True,
        "content": content,
        "applied": applied
    }



def extract_edit_blocks(text):
    text = text or ""

    search_match = re.search(
        r"(?:^|\n)SEARCH:\s*(.*?)(?=\nREPLACE:|\nCONTENT:|\nSTATUS:|\Z)",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )
    replace_match = re.search(
        r"(?:^|\n)REPLACE:\s*(.*?)(?=\nCONTENT:|\nSTATUS:|\nRESULT:|\nEDIT_BLOCK\s*\d*|\Z)",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    if not search_match or not replace_match:
        return None

    search = strip_code_fence(search_match.group(1).strip())
    replace = strip_code_fence(replace_match.group(1).strip())

    if not search:
        return None

    return {"search": search, "replace": replace}


def apply_search_replace_to_content(original_content, search, replace):
    if not search:
        return {
            "success": False,
            "content": original_content,
            "count": 0,
            "error": "SEARCH block is empty."
        }

    count = original_content.count(search)

    if count == 1:
        return {
            "success": True,
            "content": original_content.replace(search, replace, 1),
            "count": count,
            "error": ""
        }

    # Indentation-tolerant fallback:
    # If the model copied the right block but lost leading spaces, match by stripped lines.
    original_lines = original_content.splitlines()
    search_lines = search.splitlines()

    if search_lines:
        stripped_search = [line.strip() for line in search_lines]
        matches = []

        for i in range(0, len(original_lines) - len(search_lines) + 1):
            window = original_lines[i:i + len(search_lines)]
            if [line.strip() for line in window] == stripped_search:
                matches.append((i, i + len(search_lines)))

        if len(matches) == 1:
            start, end = matches[0]
            indent = original_lines[start][:len(original_lines[start]) - len(original_lines[start].lstrip())]
            replace_lines = replace.splitlines()

            if replace_lines:
                min_indent = None
                for line in replace_lines:
                    if line.strip():
                        leading = len(line) - len(line.lstrip())
                        min_indent = leading if min_indent is None else min(min_indent, leading)

                if min_indent is not None:
                    adjusted_replace_lines = []
                    for line in replace_lines:
                        if line.strip():
                            adjusted_replace_lines.append(indent + line[min_indent:])
                        else:
                            adjusted_replace_lines.append(line)
                    replace = "\n".join(adjusted_replace_lines)

            new_lines = original_lines[:start] + replace.splitlines() + original_lines[end:]
            trailing = "\n" if original_content.endswith("\n") else ""

            return {
                "success": True,
                "content": "\n".join(new_lines) + trailing,
                "count": 1,
                "error": ""
            }

    return {
        "success": False,
        "content": original_content,
        "count": count,
        "error": f"SEARCH block must appear exactly once. Found {count} exact matches and no unique indentation-tolerant match."
    }


def extract_intentional_adaptations(text):
    text = text or ""
    match = re.search(
        r"INTENTIONAL_ADAPTATIONS:\s*(.*?)(?=\nEDIT_BLOCK\s*\d*|\nEXPECTED_AFTER:|\nCONTENT:|\nSTATUS:|\nRESULT:|\Z)",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )
    return match.group(1).strip() if match else ""


async def repair_full_candidate_baseline_preservation(filename, task_goal, original_content, candidate_content, baseline_report, expected_after=""):
    prompt = f"""
A full patched candidate file failed baseline preservation.

Target file:
{filename}

Task:
{task_goal}

Expected after:
{expected_after or "No explicit EXPECTED_AFTER provided."}

Baseline preservation report:
---
{baseline_report}
---

Original file:
---
{original_content}
---

Candidate file:
---
{candidate_content}
---

Return the corrected complete candidate file.

Repair goal:
- Restore continuing behavior from the original file that the baseline report says is missing.
- Keep the new requested behavior that was added.
- Preserve the candidate's valid syntax.
- Return plain file contents without markdown fences or explanations.
"""

    try:
        result = await call_ollama(
            [
                {"role": "system", "content": "You repair full-file candidates by restoring missing baseline behavior while keeping requested new behavior. Return only corrected file contents."},
                {"role": "user", "content": prompt}
            ],
            model=MODEL,
            temperature=0.0
        )
        cleaned = strip_code_fence((result or "").strip())
        return cleaned if cleaned else candidate_content
    except Exception:
        return candidate_content



async def repair_full_candidate_static_behavior(filename, task_goal, candidate_content, static_report, expected_after=""):
    prompt = f"""
A full patched candidate file failed static React behavior validation.

Target file:
{filename}

Task:
{task_goal}

Expected after:
{expected_after or "No explicit EXPECTED_AFTER provided."}

Static behavior failures:
---
{static_report}
---

Candidate file:
---
{candidate_content}
---

Return ONLY the corrected complete file contents.

Rules:
- Do not use markdown fences.
- Do not include explanations.
- Preserve all existing behavior unless the task explicitly requires changing it.
- Fix every static behavior failure.
- If JSX uses formData.someKey.map(...), that state key must be initialized as an array.
- If JSX references a handler, that handler must be defined in the component.
- Keep the newly requested behavior.
"""

    try:
        result = await call_ollama(
            [
                {"role": "system", "content": "You repair complete React component files. Return only corrected file contents."},
                {"role": "user", "content": prompt}
            ],
            model=MODEL,
            temperature=0.0
        )
        cleaned = strip_code_fence((result or "").strip())
        return cleaned if cleaned else candidate_content
    except Exception:
        return candidate_content


async def repair_full_candidate_syntax(filename, task_goal, candidate_content, syntax_result, expected_after=""):
    prompt = f"""
A full patched candidate file failed syntax validation.

Target file:
{filename}

Task:
{task_goal}

Expected after:
{expected_after or "No explicit EXPECTED_AFTER provided."}

Syntax validation command:
{syntax_result.get("command")}

Exit code:
{syntax_result.get("exit_code")}

Parser/stdout:
{syntax_result.get("stdout") or "[empty]"}

Parser/stderr:
{syntax_result.get("stderr") or "[empty]"}

Candidate file:
---
{candidate_content}
---

Return the corrected complete file contents.

Repair goal:
- Fix the syntax issue reported above.
- Preserve existing behavior that is already present in the candidate.
- Keep the newly requested behavior.
- Return plain file contents without markdown fences or explanations.
"""

    try:
        result = await call_ollama(
            [
                {"role": "system", "content": "You repair syntax in complete code files. Return only corrected file contents."},
                {"role": "user", "content": prompt}
            ],
            model=MODEL,
            temperature=0.0
        )
        cleaned = strip_code_fence((result or "").strip())
        return cleaned if cleaned else candidate_content
    except Exception:
        return candidate_content

def maybe_load_file_context_for_task(task):
    goal = task.get("goal", "")
    inputs = task.get("inputs", {}) or {}

    approved_plan_file = inputs.get("approved_plan_file")
    read_before_modify = inputs.get("read_before_modify")

    explicit_target_file = inputs.get("target_file") or inputs.get("file_path")

    target_file = explicit_target_file or read_before_modify or extract_target_filename_from_goal(goal) or approved_plan_file

    if not target_file:
        return "", None

    try:
        file_path = safe_knowledge_path(target_file)

        if not Path(file_path).exists():
            return "", None

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        system_baseline = generate_target_file_baseline(target_file, content)

        task.setdefault("inputs", {})
        task["inputs"]["last_read_file"] = target_file
        task["inputs"]["read_before_modify"] = target_file
        task["inputs"]["auto_read_file"] = target_file
        task["inputs"]["baseline_before"] = system_baseline

        context_label = "APPROVED CREATE PLAN CONTEXT" if approved_plan_file else "AUTO-READ FILE CONTEXT"

        context = f"""

=== {context_label} ===
File: {target_file}

{content[:12000]}

=== SYSTEM-GENERATED TARGET FILE BASELINE ===
{system_baseline}

Instructions:
- This baseline is generated by the system from the current target file.
- Treat it as the source of truth for current target-file structure and observable implementation facts.
- Preserve baseline facts unless the task explicitly requires changing them.
- If you change something shown in this baseline, explain that change clearly in REASON.
=== END SYSTEM-GENERATED TARGET FILE BASELINE ===

Instructions:
- Treat this file as high-priority grounding context.
- If this is an approved CREATE plan, do not ask the user for details already answered in the plan.
- Proceed with reasonable implementation defaults inside the approved scope.
- Ask the user only if the approved plan is genuinely insufficient or contradictory.

=== END {context_label} ===
"""

        return context, target_file

    except Exception:
        return "", None




async def get_embedding(text):
    client = ollama.AsyncClient(timeout=300.0)
    response = await client.embeddings(
        model=EMBED_MODEL,
        prompt=text
    )
    return response.get("embedding")

def save_memory_index(entries):
    data = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "embedding_model": EMBED_MODEL,
        "entries": entries
    }

    with open(MEMORY_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def load_memory_index():
    if not os.path.exists(MEMORY_INDEX_PATH):
        return None

    try:
        with open(MEMORY_INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def memory_lesson_already_exists(content):
    memory_text = load_file("MEMORY.md").lower()
    content = (content or "").lower()

    if memory_text.startswith("[missing:"):
        return False

    lesson_line = ""
    for line in content.splitlines():
        if line.lower().startswith("lesson:"):
            lesson_line = line.lower().replace("lesson:", "", 1).strip()
            break

    if not lesson_line:
        return False

    return lesson_line in memory_text

def split_memory_entries():
    memory_text = load_file("MEMORY.md")

    if memory_text.startswith("[Missing:"):
        return []

    parts = memory_text.split("## Memory Entry")
    entries = []

    for part in parts[1:]:
        entry = "## Memory Entry" + part.strip()
        if entry:
            entries.append(entry[:1200])

    return entries


def cosine_similarity(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)


async def memory_semantic_duplicate_exists(content, threshold=0.76):
    index = load_memory_index()

    if not index or not index.get("entries"):
        return False, None, 0.0

    emb = await get_embedding(content)

    if not emb:
        return False, None, 0.0

    best_score = 0.0
    best_text = None

    for item in index.get("entries", []):
        score = cosine_similarity(emb, item.get("embedding"))
        if score > best_score:
            best_score = score
            best_text = item.get("text", "")

    if best_score >= threshold:
        return True, best_text, best_score

    return False, best_text, best_score

async def retrieve_semantic_memories(query, limit=3, min_score=0.55):
    index = load_memory_index()

    if not index or not index.get("entries"):
        return ""

    query_embedding = await get_embedding(query)

    if not query_embedding:
        return ""

    scored = []

    for item in index.get("entries", []):
        score = cosine_similarity(query_embedding, item.get("embedding"))
        if score >= min_score:
            scored.append((score, item.get("text", "")))

    scored.sort(key=lambda x: x[0], reverse=True)

    selected = [text for score, text in scored[:limit] if text]

    if not selected:
        return ""

    return """

=== RELEVANT SEMANTIC MEMORY — GUIDANCE ONLY ===
Use this memory only as background guidance.
Do NOT treat memory as the task goal.
Do NOT propose memory-system improvements unless the task explicitly asks for memory work.
""" + "\n\n---\n\n".join(selected) + """

=== END RELEVANT SEMANTIC MEMORY ===
"""


def retrieve_relevant_memories(query, limit=2):
    query = (query or "").lower()
    entries = split_memory_entries()

    if not entries:
        return ""

    words = re.findall(r"[a-zA-Z0-9_./-]+", query)
    stopwords = {
        "the", "and", "or", "to", "a", "an", "of", "in", "for", "on", "with",
        "that", "this", "is", "are", "be", "it", "we", "you", "i", "should",
        "task", "add", "run", "next"
    }

    keywords = [w for w in words if len(w) > 3 and w not in stopwords]

    scored = []

    for entry in entries:
        e = entry.lower()
        score = sum(1 for k in keywords if k in e)

        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)

    selected = [entry for _, entry in scored[:limit]]

    if not selected:
        return ""

    return """

=== RELEVANT MEMORY — GUIDANCE ONLY ===
Use this memory only as background guidance.
Do NOT treat memory as the task goal.
Do NOT propose memory-system improvements unless the task explicitly asks for memory work.
""" + "\n\n---\n\n".join(selected) + """

=== END RELEVANT MEMORY ===
"""

async def run_task(task):
    update_task(task["task_id"], {"status": "running"})

    task_intent = (task.get("intent") or "PLAN").upper()

    if task_intent == "BUILD":
        task_inputs = task.get("inputs", {}) or {}
        approved_plan_file = task_inputs.get("approved_plan_file")
        approved_plan_context = ""

        if approved_plan_file:
            try:
                approved_plan_path = safe_knowledge_path(approved_plan_file)
                if Path(approved_plan_path).exists():
                    with open(approved_plan_path, "r", encoding="utf-8") as f:
                        approved_plan_context = f.read()[:4000]
            except Exception as e:
                approved_plan_context = f"[Could not load approved CREATE plan: {e}]"

        system_prompt = f"""
You are Leo, Caleb's local AI agent system.

=== IDENTITY ===
{load_file("IDENTITY.md")}

=== SOUL ===
{load_file("SOUL.md")}

=== ACTIVE PROJECT OPERATING CONTRACT ===
File: {approved_plan_file or "None"}

{approved_plan_context}

=== END ACTIVE PROJECT OPERATING CONTRACT ===

For this BUILD task, the ACTIVE PROJECT OPERATING CONTRACT is the source of truth for every implementation decision.

Decision hierarchy:
1. Follow the ACTIVE PROJECT OPERATING CONTRACT.
2. Follow the current task.
3. Follow existing project files and compatible local code.
4. Use generic coding conventions only when the project contract and task do not decide the issue.

If a generic best practice, common framework pattern, or model training instinct conflicts with the ACTIVE PROJECT OPERATING CONTRACT, follow the contract.

The project plan was created through Caleb's clarification and approval process. Treat its choices as intentional, even when another approach seems common or convenient.

BUILD cognition contract:
A BUILD response is not complete with code alone.
A BUILD response must include an IMPLEMENTATION_ANALYSIS artifact before EXPECTED_AFTER.

IMPLEMENTATION_ANALYSIS is part of implementation, not commentary.

Use IMPLEMENTATION_ANALYSIS to document:
- task interpretation
- approaches considered
- chosen approach
- rejected approaches
- assumptions
- state/data shape impact
- handler impact
- UI/input behavior impact
- existing behavior impact
- risks/tradeoffs
- follow-up obligations

This analysis is required so tester can judge whether adaptations are forward progress, incomplete progress, or regression.

If you change an existing state shape, handler responsibility, input binding, rendered label, or behavior, name it in IMPLEMENTATION_ANALYSIS and INTENTIONAL_ADAPTATIONS.

BUILD validation rule:
For BUILD work, validation results outrank intended behavior.
If a syntax check, trace, test, concrete intermediate value, or other validation result shows something different from what you expected the code to do, trust the validation result.
Do not explain away the mismatch. Change the code until the validation result and the intended behavior match.

Runtime grounding policy:
A system-generated target-file baseline is expected before modifying an existing file.
VALIDATION_JS is optional for BUILD tasks.

Skip runtime grounding only when:
- the task changes only static text
- the task changes only styling/CSS/layout
- the task changes only comments/documentation
"""
    else:
        system_prompt = build_system_prompt(task["goal"], structured=True)

    # Inject grounding context
    task_inputs = task.get("inputs", {}) or {}
    approved_plan_file = task_inputs.get("approved_plan_file")
    approved_plan_context = ""

    if approved_plan_file:
        try:
            approved_plan_path = safe_knowledge_path(approved_plan_file)
            if Path(approved_plan_path).exists():
                with open(approved_plan_path, "r", encoding="utf-8") as f:
                    approved_plan_context = f.read()[:16000]
        except Exception as e:
            approved_plan_context = f"[Could not load approved CREATE plan: {e}]"

    if approved_plan_context and task_intent != "BUILD":
        system_prompt += f"""

=== APPROVED CREATE PROJECT PLAN — PRIMARY BUILD CONTEXT ===
File: {approved_plan_file}

{approved_plan_context}

=== END APPROVED CREATE PROJECT PLAN ===

Rules:
- For this task, the approved CREATE project plan is the source of truth.
- Use the task goal plus the approved plan to decide what to build.
- Do not drift outside the approved plan.
- Do not ask Caleb for details already answered in the approved plan.
- If implementation details are missing, choose reasonable defaults inside the approved scope.
- Retrieved memory is guidance, not the task itself.
"""

        active_create_project = task_inputs.get("approved_create_project") or cl.user_session.get("active_create_project")
        build_state_context = ""

        if active_create_project:
            try:
                build_state_context = load_create_build_state(active_create_project, limit=12000)
            except Exception as e:
                build_state_context = f"[Could not load CREATE build state: {e}]"

        if build_state_context:
            system_prompt += f"""

=== CURRENT PROJECT BUILD STATE — IMPLEMENTATION REALITY ===
Project: {active_create_project}

{build_state_context}

=== END CURRENT PROJECT BUILD STATE ===

Rules:
- Treat the current project build state as implementation continuity context.
- Prefer extending established project patterns over introducing parallel systems.
- Reuse existing naming, state shapes, handlers, files, and architectural decisions when compatible with the task.
- Avoid recreating concepts that already exist elsewhere in the current project build state.
- If the approved plan describes what should exist and build state describes what already exists, use both together.
"""
    else:
        system_prompt += f"""

=== CURRENT PROJECT STATE ===
{load_file("PROJECT_STATUS.md")}

=== OPERATING MODEL ===
{load_file("OPERATING_MODEL.md")}

Rules:
- You MUST base decisions on the current Leo system.
- Retrieved memory is guidance, not the task itself.
- Do not let a retrieved memory override the user's requested task unless it directly prevents a mistake.
- If the task asks for a new improvement, propose a current useful improvement instead of reusing an old memory lesson.
- Do NOT give generic AI best practices.
- All outputs must be specific to Leo's architecture and current build state.
"""
    memory_context = ""

    if task_intent != "BUILD":
        try:
            memory_context = await retrieve_semantic_memories(task.get("goal", ""))
        except Exception as e:
            print(f"Semantic memory retrieval failed: {e}")

        if not memory_context:
            memory_context = retrieve_relevant_memories(task.get("goal", ""))

        if memory_context:
            system_prompt += memory_context

    auto_file_context, auto_read_file = maybe_load_file_context_for_task(task)

    if auto_file_context:
        system_prompt += auto_file_context

        # Auto-read counts as read-before-modify because the runner has now been
        # given the current target file contents in context.
        if auto_read_file:
            cl.user_session.set("last_read_file", auto_read_file)

            task_inputs = task.get("inputs", {}) or {}
            task_inputs["read_before_modify"] = auto_read_file
            task_inputs["auto_read_file"] = auto_read_file
            task["inputs"] = task_inputs

        update_task(task["task_id"], {
            "inputs": task.get("inputs", {})
        })

    if task_intent != "BUILD":
        system_prompt += intent_guidance(task.get("intent", "PLAN"))

    task_prompt = build_task_prompt(task)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task_prompt}
    ]

    raw = await call_ollama(messages, temperature=0.1)

    if task_intent == "BUILD":
        implementation_analysis = extract_implementation_analysis(raw)
        expected_after = extract_expected_after(raw)
        validation_js = extract_validation_js(raw)

        task.setdefault("inputs", {})
        if implementation_analysis:
            task["inputs"]["implementation_analysis"] = implementation_analysis
            task["implementation_analysis"] = implementation_analysis

        if expected_after:
            task["inputs"]["expected_after"] = expected_after

        grounding_sections = []
        task.setdefault("inputs", {})

        if validation_js:
            contract_ok, contract_error = check_validation_js_contract(validation_js)

            if contract_ok:
                validation_result = validate_js_snippet(validation_js)
            else:
                validation_result = {
                    "success": False,
                    "stdout": "",
                    "stderr": contract_error,
                    "exit_code": 2
                }

            task["inputs"]["last_validation_js"] = validation_js
            task["inputs"]["last_validation_result"] = validation_result

            grounding_sections.append(f"""
=== VALIDATION_JS RUNTIME RESULT ===

You requested VALIDATION_JS. The system executed it with Node.js.

Exit code:
{validation_result.get("exit_code")}

Success:
{validation_result.get("success")}

STDOUT:
{validation_result.get("stdout") or "[empty]"}

STDERR:
{validation_result.get("stderr") or "[empty]"}

Purpose:
This is the observed behavior of your proposed implementation logic.
Runtime output is authoritative.
If it differs from what you expected, revise the implementation before returning final CONTENT.
=== END VALIDATION_JS RUNTIME RESULT ===
""")

        if grounding_sections:
            grounding_feedback = """

Runtime grounding results are authoritative.
Return the same required BUILD format again.
Use the system-generated target-file baseline to preserve current behavior.
Use EXPECTED_AFTER as the implementation contract for what must remain true and what must be added.
If your proposed CONTENT fails EXPECTED_AFTER, your CONTENT is wrong even if your reasoning says it should work.
Use the validation result to repair or confirm proposed behavior.
If VALIDATION_JS failed because of a contract violation, either remove it or replace it with a standalone Node-compatible snippet that directly exercises the changed logic.
Do not include VALIDATION_JS again unless another runtime check is necessary.

""" + "\n".join(grounding_sections)

            raw = await call_ollama(
                messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": grounding_feedback}
                ],
                temperature=0.0
            )

        parsed_preview = parse_build_response(raw)

        if parsed_preview and (parsed_preview.get("file_operation") or parsed_preview.get("file_write")):
            preview_op = parsed_preview.get("file_operation") or parsed_preview.get("file_write")
            proposed_content = preview_op.get("content", "")
            baseline_before = task.get("inputs", {}).get("baseline_before", "")

            preview_operation = (preview_op.get("operation") or preview_op.get("mode") or "").lower().strip()

            if proposed_content and baseline_before and preview_operation != "edit":
                baseline_after = generate_target_file_baseline(preview_op.get("filename") or task.get("inputs", {}).get("target_file"), proposed_content)
                baseline_diff = compare_target_file_baselines(baseline_before, baseline_after)

                task.setdefault("inputs", {})
                task["inputs"]["baseline_after"] = baseline_after
                task["inputs"]["baseline_diff"] = baseline_diff

                if not baseline_diff.get("ok"):
                    baseline_feedback = f"""
=== TARGET FILE BASELINE COMPARISON ===

The system compared the current target-file baseline against your proposed CONTENT.

{baseline_diff.get("report")}

Rule:
The proposed CONTENT must preserve baseline_before facts unless the task explicitly requires changing them.
If a missing baseline item was intentionally changed by the task, explain that clearly in REASON.
Otherwise revise CONTENT so the baseline_after preserves the missing facts while adding the requested behavior.

Return the same required BUILD format again.
=== END TARGET FILE BASELINE COMPARISON ===
"""

                    raw = await call_ollama(
                        messages + [
                            {"role": "assistant", "content": raw},
                            {"role": "user", "content": baseline_feedback}
                        ],
                        temperature=0.0
                    )

        parsed = parse_build_response(raw)

        if parsed is None:
            build_repair_prompt = f"""
The previous BUILD response did useful reasoning, but it did not follow the required BUILD output format.

Convert the response into exactly this format:

STATUS: done
RESULT: short summary
FILE: path/to/file
OPERATION: create|replace|edit
Operation choice:
- Use create only when the target file does not exist.
- Use replace only for new, tiny, low-maturity starter files or explicit full-file rebuilds.
- Use edit for existing developing/mature files when preserving existing behavior matters.
- If the file has meaningful existing state, handlers, JSX, imports, or user-facing behavior, prefer edit.
- Mature file replacement may be blocked by the runtime; avoid replace unless the task explicitly requires a full rebuild.
REASON: short reason, including structural/data-shape changes and why
EXPECTED_AFTER:
- baseline facts that should remain true
- new state/data/handler/rendered/input facts expected after this task
- concrete state transition expectations:
  - input/event
  - expected changed key
  - expected value
- behavior that should change
- behavior that should not change
CONTENT:
complete file contents here

For OPERATION: edit, you are not responsible for rewriting the file.

Your job is to provide a line-range patch only:

EDIT_RANGE_START:
starting line number from the numbered target file

EDIT_RANGE_END:
ending line number from the numbered target file

REPLACE:
replacement code for those lines only

Runtime edit capability:
- The system will copy the current target file into a temporary edit candidate.
- The system will replace the inclusive line range with your REPLACE block.
- The system will validate the resulting full file.
- The system will stage the validated candidate for user approval.

Edit truth:
- EDIT_RANGE_START / EDIT_RANGE_END / REPLACE is the source of truth for edit work.
- CONTENT is the source of truth only for create/replace work.
- Do not output CONTENT when OPERATION is edit.
- Do not output SEARCH when OPERATION is edit.
- Use exactly one EDIT_RANGE_START, one EDIT_RANGE_END, and one REPLACE block.
- REPLACE must be raw text only.
- Do not include line numbers in REPLACE.
- Do not include language labels like jsx, javascript, python, or text after REPLACE.
- Do not wrap REPLACE in markdown fences.
- Choose the smallest stable line range that safely contains the change.
- REPLACE must preserve unrelated logic and formatting inside the selected range.

Rules:
- Output ONLY the BUILD format above.
- Do not use markdown fences.
- Do not explain outside the format.
- CONTENT must contain only the complete file contents.
- Do not include VALIDATION_JS unless it is necessary.
- Preserve the corrected implementation logic from the previous response.
"""

            repaired_raw = await call_ollama(
                messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": build_repair_prompt}
                ],
                temperature=0.0
            )

            repaired_parsed = parse_build_response(repaired_raw)

            if repaired_parsed is not None:
                raw = repaired_raw
                parsed = repaired_parsed

    else:
        parsed = parse_json_or_none(raw)

        if parsed is None:
            repair = await call_ollama([
                {"role": "system", "content": "Repair invalid JSON. Output ONLY valid JSON. No markdown. If file content uses backticks or multiline code, convert it into a valid JSON string with escaped newlines and quotes."},
                {"role": "user", "content": raw}
            ], temperature=0.0)
            parsed = parse_json_or_none(repair)

    if parsed is None:
        return update_task(task["task_id"], {
            "status": "blocked",
            "result": raw,
            "next_action": "JSON parsing failed. Review raw output.",
            "needs_user": True
        })

    final_status = parsed.get("status", "done")
    if final_status not in ["done", "blocked", "needs_user"]:
        final_status = "done"

    return update_task(task["task_id"], {
        "status": final_status,
        "result": parsed.get("result"),
        "next_action": parsed.get("next_action"),
        "needs_user": parsed.get("needs_user", False),
        "inputs": task.get("inputs", {}),
        "memory_candidate": parsed.get("memory_candidate"),
        "file_operation": parsed.get("file_operation") or parsed.get("file_write"),
        "file_write": parsed.get("file_write"),
        "expected_after": task.get("inputs", {}).get("expected_after"),
        "validation_js": task.get("inputs", {}).get("last_validation_js"),
        "validation_result": task.get("inputs", {}).get("last_validation_result"),
        "baseline_before": task.get("inputs", {}).get("baseline_before"),
        "baseline_after": task.get("inputs", {}).get("baseline_after"),
        "baseline_diff": task.get("inputs", {}).get("baseline_diff")
    })

@cl.on_chat_start
async def start():
    cl.user_session.set("history", [])
    cl.user_session.set("pending_write", None)
    await cl.Message(content=f"🚀 Leo ({MODEL}) + Task Runner online.").send()

@cl.on_message
async def main(message: cl.Message):
    user_text = message.content.strip()


    # ===== SAFE FILE WRITE TOOL =====

    if user_text.startswith("/file write "):
        rest = user_text.replace("/file write ", "", 1).strip()

        if "\n" not in rest:
            await cl.Message(content="Use this format:\n\n/file write FILENAME.md\nYour content here").send()
            return

        filename, content = rest.split("\n", 1)
        filename = normalize_knowledge_filename(filename.strip())
        content = content.strip()

        if not filename or not content:
            await cl.Message(content="Missing filename or content.").send()
            return

        try:
            safe_knowledge_path(filename)
        except Exception as e:
            await cl.Message(content=f"Unsafe filename: {str(e)}").send()
            return

        file_path = safe_knowledge_path(filename)
        file_exists = Path(file_path).exists()

        operation = "replace" if file_exists else "create"
        approval_command = "/approve replace" if operation == "replace" else "/approve write"
        risk = "HIGH — this will overwrite the existing file." if operation == "replace" else "LOW — this will create a new file."

        stage_file_operation(filename, content, operation=operation, reason="manual /file write command")
        preview = content[:5000]

        await cl.Message(content=f"""Proposed file {operation.upper()} staged.

Operation: {operation.upper()}
Risk: {risk}
File: {filename}

Preview:
---
{preview}
---

To approve this {operation}, run:
{approval_command}

To cancel, run:
/cancel write""").send()
        return








    if user_text == "/memory rebuild-index":
        try:
            entries = split_memory_entries()

            if not entries:
                await cl.Message(content="No memory entries found to index.").send()
                return

            indexed = []

            await cl.Message(content=f"Rebuilding memory index for {len(entries)} entries...").send()

            for i, entry in enumerate(entries, start=1):
                emb = await get_embedding(entry)

                if emb:
                    indexed.append({
                        "id": f"memory_{i}",
                        "text": entry,
                        "embedding": emb
                    })

            save_memory_index(indexed)

            await cl.Message(content=f"Memory index rebuilt. Indexed {len(indexed)} entries using {EMBED_MODEL}.").send()
            return

        except Exception as e:
            await cl.Message(content=f"Memory index rebuild failed: {str(e)}").send()
            return


    if user_text.startswith("/memory propose"):
        content = user_text.replace("/memory propose", "", 1).strip()

        if not content:
            await cl.Message(content="""Use this format:

/memory propose
Lesson: ...
Why it matters: ...
Source: ...
Confidence: 0.0-1.0""").send()
            return

        cl.user_session.set("pending_memory", {
            "content": content,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "review": None
        })

        await cl.Message(content=f"""Memory proposal staged.

Preview:
---
{content}
---

Next:
- Review: /memory review
- Approve: /memory approve
- Cancel: /memory cancel""").send()
        return

    if user_text == "/memory review":
        pending_memory = cl.user_session.get("pending_memory")

        if not pending_memory:
            await cl.Message(content="No pending memory proposal to review.").send()
            return

        review_prompt = f"""
You are reviewing a proposed memory write for Caleb's local Leo agent system.

Memory proposal:
---
{pending_memory.get("content", "")}
---

Evaluate whether this should be saved to MEMORY.md.

Criteria:
- Is it generalizable beyond this moment?
- Will it improve future Leo behavior?
- Is it specific enough to be useful?
- Is it safe and non-sensitive?
- Is it not just a temporary task note?

Respond exactly in this format:

Recommendation: APPROVE | REVISE | REJECT
Risk Level: LOW | MEDIUM | HIGH
Reason:
- ...
Suggested Rewrite:
- ...
"""

        try:
            review_response = await call_ollama(
                [
                    {"role": "system", "content": "You are a strict memory reviewer for an agent system."},
                    {"role": "user", "content": review_prompt}
                ],
                model="qwen2.5-coder:14b",
                temperature=0.1
            )

            pending_memory["review"] = {
                "model": "qwen2.5-coder:14b",
                "reviewed_at": datetime.now().isoformat(timespec="seconds"),
                "response": review_response
            }

            cl.user_session.set("pending_memory", pending_memory)

            await cl.Message(content=f"""Memory Review

{review_response}

Next:
- Approve: /memory approve
- Cancel: /memory cancel""").send()
            return

        except Exception as e:
            await cl.Message(content=f"Memory review failed: {str(e)}").send()
            return

    if user_text == "/memory approve":
        pending_memory = cl.user_session.get("pending_memory")

        if not pending_memory:
            await cl.Message(content="No pending memory proposal to approve.").send()
            return

        review = pending_memory.get("review")

        if not review:
            await cl.Message(content="Memory has not been reviewed yet. Run /memory review first.").send()
            return

        review_text = review.get("response", "").lower()

        if "recommendation: approve" not in review_text:
            await cl.Message(content="Memory review did not recommend APPROVE. Revise or cancel this memory proposal.").send()
            return

        memory_path = safe_knowledge_path("MEMORY.md")
        os.makedirs(os.path.dirname(memory_path), exist_ok=True)

        timestamp = datetime.now().isoformat(timespec="seconds")

        entry = f"""

## Memory Entry — {timestamp}

{pending_memory.get("content", "").strip()}
"""

        log_review(
            "MEMORY.md",
            "MEMORY_WRITE",
            f"""Memory write approved at {timestamp}

Memory content:
---
{pending_memory.get("content", "").strip()}
---

Review:
---
{review.get("response", "").strip()}
---"""
        )

        with open(memory_path, "a", encoding="utf-8") as f:
            f.write(entry)

        log_file_operation(
            "MEMORY.md",
            "append",
            reason="approved memory write-back",
            backup_path=None
        )

        cl.user_session.set("pending_memory", None)

        await cl.Message(content="Memory approved and appended to MEMORY.md.").send()
        return

    if user_text == "/memory cancel":
        cl.user_session.set("pending_memory", None)
        await cl.Message(content="Cancelled pending memory proposal.").send()
        return


    if user_text == "/rollback retry surgical":
        pending = get_pending_write()

        if not pending:
            await cl.Message(content="No pending staged operation to rollback and retry.").send()
            return

        if not pending.get("rollback_available"):
            await cl.Message(content="Rollback retry is not available for this staged operation.").send()
            return

        filename = pending.get("filename")
        original_content = pending.get("original_content_snapshot", "")
        previous_goal = pending.get("task_goal") or pending.get("original_task") or "Redo the previous file edit."

        try:
            file_path = safe_knowledge_path(filename)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(original_content)

            retry_goal = f"""Surgically retry the previous failed edit with maximum preservation.

Target file:
{filename}

Original task summary:
{previous_goal[:1200]}

Rules:
- Use EDIT_MODE surgical.
- Preserve existing state shape, handlers, input bindings, rendered labels, and behavior.
- Do not convert scalar state to arrays or objects.
- Do not remove or rewrite existing handlers.
- Prefer exact SEARCH/REPLACE blocks.
- Add only the smallest local behavior needed.
- Avoid adaptive restructuring from the previous staged attempt.
"""

            retry_inputs = {
                "target_file": filename,
                "edit_mode": "surgical",
                "intent": "BUILD",
                "source": "rollback_retry_surgical",
                "previous_task_goal": previous_goal[:2000],
                "read_before_modify": filename
            }

            task = create_task(
                retry_goal,
                assigned_role="coder",
                requested_by="rollback_retry",
                inputs=retry_inputs
            )

            clear_pending_write()

            await cl.Message(content=f"""Rollback completed and surgical retry task created.

Restored file: `{filename}`
New task ID: `{task['task_id']}`

Next:
`/task run {task['task_id']}`""").send()
            return

        except Exception as e:
            await cl.Message(content=f"Rollback retry failed for `{filename}`: {e}").send()
            return


    if user_text == "/rollback staged":
        pending = get_pending_write()

        if not pending:
            await cl.Message(content="No pending staged operation to rollback.").send()
            return

        if not pending.get("rollback_available"):
            await cl.Message(content="Rollback is not available for this staged operation.").send()
            return

        filename = pending.get("filename")
        original_content = pending.get("original_content_snapshot", "")

        try:
            file_path = safe_knowledge_path(filename)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(original_content)

            clear_pending_write()

            await cl.Message(content=f"""Rollback completed.

Restored file: `{filename}`

The staged operation was cancelled and the file was restored from the pre-stage snapshot.""").send()
            return

        except Exception as e:
            await cl.Message(content=f"Rollback failed for `{filename}`: {e}").send()
            return


    if user_text == "/write preview raw":
        pending = cl.user_session.get("pending_write")
        if not pending:
            await cl.Message(content="No pending write operation.").send()
            return

        content = pending.get("content", "")
        raw_preview = repr(content[:8000])

        message = (
            "Raw Pending Write Preview\n\n"
            f"Operation: {pending.get('operation')}\n"
            f"File: {pending.get('filename')}\n"
            f"Reason: {pending.get('reason', '')}\n\n"
            "RAW REPR:\n"
            + raw_preview
            + "\n\nUse this when normal preview rendering may strip backticks or JSX template literals."
        )

        await cl.Message(content=message).send()
        return


    if user_text == "/write preview full":
        pending = get_pending_write()

        if not pending:
            await cl.Message(content="No pending write operation.").send()
            return

        filename = pending.get("filename")
        operation = pending.get("operation")
        reason = pending.get("reason", "")
        content = pending.get("content", "")

        approval_command = "/approve replace" if operation == "replace" else "/approve edit" if operation == "edit" else "/approve write"

        await cl.Message(content=f"""## Full Pending Write Preview

Operation: `{operation.upper()}`
File: `{filename}`
Reason: {reason}

FULL CONTENT:
---
{content}
---

Approval command:
`{approval_command}`

Cancel:
`/cancel write`""").send()
        return



    if user_text == "/test pending":
        pending = get_pending_write()

        if not pending:
            await cl.Message(content="No pending operation to test.").send()
            return

        filename = pending.get("filename")
        operation = pending.get("operation")
        reason = pending.get("reason", "")
        content = pending.get("content", "")[:7000]
        expected_after = pending.get("expected_after", "")
        baseline_report = pending.get("baseline_report", "")

        test_prompt = f"""
You are the TESTER role for Leo.

You are NOT the implementer.
Your job is to judge whether the staged implementation represents:

- correct forward progress
- incomplete but salvageable work
- architectural regression
- invalid adaptation
- implementation drift
- or a correct adaptive improvement

File: {filename}
Operation: {operation}

Reason:
{reason}

EXPECTED_AFTER:
---
{expected_after[:5000]}
---

BASELINE REPORT:
---
{baseline_report[:5000]}
---

CONTENT PREVIEW:
---
{content}
---

You must decide:

1. Is this implementation direction GOOD or BAD?
2. Did the coder preserve important behavior?
3. Did the coder introduce unauthorized architecture changes?
4. Is rollback preferable?
5. Should this become:
   - APPROVE
   - FIX_FORWARD
   - ROLLBACK_RETRY
   - SPLIT_TASKS
   - BLOCKED

Hard approval rules:
- If BASELINE REPORT contains "preservation_ok: False", verdict MUST NOT be APPROVE.
- If BASELINE REPORT contains "missing_from_after", verdict MUST NOT be APPROVE unless every missing item is explicitly named and justified in INTENTIONAL_ADAPTATIONS.
- Do NOT approve if INTENTIONAL_ADAPTATIONS says none but BASELINE REPORT shows missing existing behavior.
- Do NOT approve if an existing handler was removed, renamed, or semantically rewritten without a clear approved reason.
- Do NOT approve if an existing input binding, rendered label, state key, or mapped collection disappeared without an explicit justified adaptation.
- Do NOT approve if the implementation claims existing behavior remains unchanged but the baseline report shows missing_from_after.
- Do NOT approve if the implementation changes scalar state to array/object state unless that exact adaptation is named and justified.
- Do NOT approve if the candidate breaks or rewires unrelated form fields.

Verdict guidance:
- APPROVE only when the implementation is complete, internally consistent, and has no unexplained baseline losses.
- FIX_FORWARD when the direction is good but missing small follow-up pieces.
- ROLLBACK_RETRY when the implementation direction is bad, over-adaptive, or creates regression risk.
- SPLIT_TASKS when the task is too broad and should be decomposed.
- BLOCKED when you cannot judge confidently from the evidence.

Rules:
- You are allowed to approve adaptive changes IF they are genuinely better architecture AND explicitly declared.
- You are allowed to reject adaptive changes if they create regression risk.
- If the implementation is directionally correct but incomplete, prefer FIX_FORWARD.
- If the implementation changed architecture unnecessarily, prefer ROLLBACK_RETRY.
- If the task is too broad or ambiguous, prefer SPLIT_TASKS.
- Be decisive and concrete.

Return STRICT JSON ONLY:

{{
  "verdict": "APPROVE | FIX_FORWARD | ROLLBACK_RETRY | SPLIT_TASKS | BLOCKED",
  "confidence": 0.0,
  "summary": "...",
  "architectural_assessment": "...",
  "preservation_assessment": "...",
  "regression_risk": "...",
  "resolved_retry_task": "...",
  "followup_tasks": [
    {{
      "role": "coder",
      "goal": "..."
    }}
  ]
}}
"""

        try:
            tester_response = await call_ollama(
                [
                    {"role": "system", "content": "You are Leo's architectural tester and adjudicator."},
                    {"role": "user", "content": test_prompt}
                ],
                model="qwen2.5-coder:14b",
                temperature=0.1
            )

            parsed = safe_json_loads(tester_response)

            if not isinstance(parsed, dict):
                parsed = {
                    "verdict": "BLOCKED",
                    "confidence": 0.0,
                    "summary": "Tester response was not valid JSON. Raw response saved for review.",
                    "architectural_assessment": "",
                    "preservation_assessment": "",
                    "regression_risk": "",
                    "resolved_retry_task": "",
                    "followup_tasks": []
                }

            pending["tester_verdict"] = {
                "model": "qwen2.5-coder:14b",
                "tested_at": datetime.now().isoformat(timespec="seconds"),
                "raw_response": tester_response,
                "parsed": parsed
            }

            cl.user_session.set("pending_write", pending)

            await cl.Message(content=f"""Tester verdict completed.

Verdict: {parsed.get("verdict", "UNKNOWN")}
Confidence: {parsed.get("confidence", "unknown")}

Summary:
{parsed.get("summary", "No summary.")}

Next:
- Approve manually if desired
- /rollback retry surgical
- /cancel write
- future: automated tester-driven retry routing""").send()

            return

        except Exception as e:
            await cl.Message(content=f"Tester adjudication failed: {str(e)}").send()
            return


    if user_text == "/review pending":
        pending = get_pending_write()

        if not pending:
            await cl.Message(content="No pending operation to review.").send()
            return

        filename = pending.get("filename")
        operation = pending.get("operation")
        reason = pending.get("reason", "")
        content = pending.get("content", "")[:5000]

        review_prompt = f"""
You are a strict code and system safety reviewer.

Review the following pending file operation.

Operation: {operation}
File: {filename}
Reason: {reason}

Content Preview:
---
{content}
---

Your job:

1. Determine if this operation is SAFE, RISKY, or UNSAFE
2. Recommend: APPROVE, REVISE, or REJECT
3. Explain WHY clearly
4. Suggest improvements if needed

Respond in this format:

Recommendation: APPROVE | REVISE | REJECT
Risk Level: LOW | MEDIUM | HIGH
Reason:
- ...
Concerns:
- ...
Suggested Fix:
- ...
"""

        try:
            review_response = await call_ollama(
                [
                    {"role": "system", "content": "You are a strict and precise system reviewer."},
                    {"role": "user", "content": review_prompt}
                ],
                model="qwen2.5-coder:14b",
                temperature=0.1
            )

            pending["review"] = {
                "model": "qwen2.5-coder:14b",
                "reviewed_at": datetime.now().isoformat(timespec="seconds"),
                "response": review_response
            }
            cl.user_session.set("pending_write", pending)

            log_review(filename, operation, review_response)

            # ===== AUTO-APPROVAL AFTER REVIEW =====
            ok, reason_auto = is_auto_approvable(pending)

            if ok:
                pending["auto_approved"] = True
                pending["auto_approved_at"] = datetime.now().isoformat(timespec="seconds")
                cl.user_session.set("pending_write", pending)

                op = pending.get("operation", "append")
                filename = pending.get("filename")
                content = pending.get("content", "").strip()
                reason = pending.get("reason", "")
                reason = f"{reason} [AUTO-APPROVED after reviewer APPROVE/LOW]"

                if op not in ["append", "create"]:
                    await cl.Message(content=f"""🧠 Review (qwen2.5-coder:14b)

{review_response}

Auto-approval not allowed for operation: {op}""").send()
                    return

                try:
                    file_path = safe_knowledge_path(filename)
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)

                    if op == "create":
                        if os.path.exists(file_path):
                            await cl.Message(content=f"Auto-approval refused: `{filename}` already exists, so CREATE is not allowed.").send()
                            return

                        with open(file_path, "w", encoding="utf-8") as f:
                            f.write(content + "\n")

                    elif op == "append":
                        with open(file_path, "a", encoding="utf-8") as f:
                            f.write("\n\n" + content + "\n")

                    log_file_operation(filename, op, reason=reason, backup_path=None)
                    clear_pending_write()

                    await cl.Message(content=f"""🧠 Review (qwen2.5-coder:14b)

{review_response}

✅ Auto-approved and executed.

Operation: {op.upper()}
File: {filename}""").send()
                    return

                except Exception as e:
                    await cl.Message(content=f"Auto-approved execution failed: {str(e)}").send()
                    return
            else:
                await cl.Message(content=f"""🧠 Review (qwen2.5-coder:14b)

{review_response}

❌ Not auto-approved: {reason_auto}

To proceed manually:
- /approve write | /approve edit | /approve replace
- /cancel write""").send()
                return

        except Exception as e:
            await cl.Message(content=f"Review failed: {str(e)}").send()

        return

    if user_text.startswith("/file rollback "):
        backup_name = user_text.replace("/file rollback ", "", 1).strip()

        if not backup_name:
            await cl.Message(content="Use this format:\n\n/file rollback BACKUP_FILENAME.bak").send()
            return

        try:
            backup_name = backup_name.strip().lstrip("/")
            if ".." in backup_name or "/" in backup_name:
                await cl.Message(content="Rollback refused: use only a backup filename from BACKUPS, not a path.").send()
                return

            backup_path = os.path.join(LEO_FILES_PATH, "BACKUPS", backup_name)

            if not Path(backup_path).exists():
                await cl.Message(content=f"Backup not found: {backup_name}").send()
                return

            parts = backup_name.split("_", 3)
            if len(parts) < 4:
                await cl.Message(content="Rollback refused: backup filename format not recognized.").send()
                return

            original_file = parts[3].replace("__", "/")
            if original_file.endswith(".bak"):
                original_file = original_file[:-4]

            with open(backup_path, "r", encoding="utf-8") as f:
                backup_content = f.read()

            stage_file_operation(
                original_file,
                backup_content,
                operation="replace",
                reason=f"rollback from backup {backup_name}"
            )

            await cl.Message(content=f"""Rollback staged.

Operation: REPLACE
Risk: HIGH — this will restore `{original_file}` from backup.
Backup: {backup_name}
Target file: {original_file}

To approve rollback, run:
/approve replace

To cancel, run:
/cancel write""").send()
            return

        except Exception as e:
            await cl.Message(content=f"Rollback staging failed: {str(e)}").send()
            return


    if user_text.startswith("/file read full "):
        filename = user_text.replace("/file read full ", "", 1).strip()

        try:
            filename = normalize_knowledge_filename(filename)
            file_path = safe_knowledge_path(filename)

            if not Path(file_path).exists():
                await cl.Message(content=f"File not found: {filename}").send()
                return

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            cl.user_session.set("last_read_file", filename)

            await cl.Message(content=f"""File read full: {filename}

---
{content}
---""").send()

        except Exception as e:
            await cl.Message(content=f"Read full failed: {str(e)}").send()

        return

    if user_text.startswith("/file read "):
        filename = user_text.replace("/file read ", "", 1).strip()

        try:
            filename = normalize_knowledge_filename(filename)
            file_path = safe_knowledge_path(filename)

            if not Path(file_path).exists():
                await cl.Message(content=f"File not found: {filename}").send()
                return

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            preview = content[:3000]
            cl.user_session.set("last_read_file", filename)

            await cl.Message(content=f"""📄 File read: {filename}

Preview:
---
{preview}
---

(Showing first {len(preview)} characters)""").send()

        except Exception as e:
            await cl.Message(content=f"Read failed: {str(e)}").send()

        return


    if user_text.startswith("/file edit "):
        rest = user_text.replace("/file edit ", "", 1).strip()

        if "\nOLD:\n" not in rest or "\nNEW:\n" not in rest:
            await cl.Message(content="""Use this format:

/file edit FILENAME.md
OLD:
exact text to replace
NEW:
replacement text""").send()
            return

        filename_part, remainder = rest.split("\nOLD:\n", 1)
        old_text, new_text = remainder.split("\nNEW:\n", 1)

        filename = normalize_knowledge_filename(filename_part.strip())
        old_text = old_text.strip()
        new_text = new_text.strip()

        if not filename or not old_text or not new_text:
            await cl.Message(content="Missing filename, OLD text, or NEW text.").send()
            return

        try:
            file_path = safe_knowledge_path(filename)

            if not Path(file_path).exists():
                await cl.Message(content=f"Edit refused: file not found: {filename}").send()
                return

            with open(file_path, "r", encoding="utf-8") as f:
                current = f.read()

            if old_text not in current:
                await cl.Message(content=f"""Edit refused: OLD text was not found exactly in `{filename}`.

Use `/file read full {filename}` to inspect the current file, then try again.""").send()
                return

            cl.user_session.set("pending_write", {
                "filename": filename,
                "operation": "edit",
                "old_text": old_text,
                "new_text": new_text,
                "content": new_text,
                "reason": "manual /file edit command"
            })

            await cl.Message(content=f"""Proposed file EDIT staged.

Operation: EDIT
Risk: MEDIUM — exact text replacement.
File: {filename}

OLD:
---
{old_text[:1500]}
---

NEW:
---
{new_text[:1500]}
---

To approve this edit, run:
/approve edit

To cancel, run:
/cancel write""").send()
            return

        except Exception as e:
            await cl.Message(content=f"Edit staging failed: {str(e)}").send()
            return


    if user_text.startswith("/file append "):
        rest = user_text.replace("/file append ", "", 1).strip()

        if "\n" not in rest:
            await cl.Message(content="Use this format:\n\n/file append FILENAME.md\nContent to append here").send()
            return

        filename, content = rest.split("\n", 1)
        filename = normalize_knowledge_filename(filename.strip())
        content = content.strip()

        if not filename or not content:
            await cl.Message(content="Missing filename or content.").send()
            return

        try:
            safe_knowledge_path(filename)
        except Exception as e:
            await cl.Message(content=f"Unsafe filename: {str(e)}").send()
            return

        stage_file_operation(filename, content, operation="append", reason="manual /file append command")
        preview = content[:2000]

        await cl.Message(content=f"""Proposed file append staged.

File: {filename}

Append Preview:
---
{preview}
---

To approve and append this content, run:
/approve write

To cancel, run:
/cancel write""").send()
        return



    if user_text == "/auto approve pending":
        pending = get_pending_write()

        ok, reason = is_auto_approvable(pending)

        if not ok:
            await cl.Message(content=f"Auto-approval refused: {reason}").send()
            return

        pending["auto_approved"] = True
        pending["auto_approved_at"] = datetime.now().isoformat(timespec="seconds")
        cl.user_session.set("pending_write", pending)

        operation = pending.get("operation", "append")

        if operation == "create":
            user_text = "/approve write"
        elif operation == "append":
            user_text = "/approve write"
        else:
            await cl.Message(content="Auto-approval refused: unsupported operation.").send()
            return


    if user_text == "/approve reviewed":
        pending = get_pending_write()

        if not pending:
            await cl.Message(content="No pending operation to approve.").send()
            return

        review = pending.get("review")

        if not review:
            await cl.Message(content="This operation has not been reviewed yet. Run `/review pending` first.").send()
            return

        review_text = review.get("response", "").lower()

        if "recommendation: approve" not in review_text:
            await cl.Message(content="Review did not recommend APPROVE. Use manual approval only if you intentionally override the review.").send()
            return

        operation = pending.get("operation", "replace")

        if operation == "replace":
            user_text = "/approve replace"
        elif operation == "edit":
            user_text = "/approve edit"
        else:
            user_text = "/approve write"


    if user_text in ["/approve write", "/approve edit", "/approve replace"]:
        pending = get_pending_write()

        if not pending:
            await cl.Message(content="No pending file operation to approve.").send()
            return

        operation = pending.get("operation", "replace")
        filename = pending["filename"]

        required_approval = "/approve write"
        if operation == "replace":
            required_approval = "/approve replace"
        elif operation == "edit":
            required_approval = "/approve edit"

        if user_text != required_approval:
            await cl.Message(content=f"This staged operation is {operation.upper()} and requires `{required_approval}`.").send()
            return

        try:
            file_path = safe_knowledge_path(filename)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            content = pending["content"].strip()

            reason = pending.get("reason", "")
            if pending.get("auto_approved"):
                reason = f"{reason} [AUTO-APPROVED after reviewer APPROVE/LOW]"
            backup_path = None

            if operation in ["edit", "replace"]:
                backup_path = backup_file_before_change(filename, operation)

            if operation == "create":
                if os.path.exists(file_path):
                    await cl.Message(content=f"Create refused: `{filename}` already exists. Use append, edit, or replace instead.").send()
                    return
                mode = "w"
                final_content = content + "\n"

            elif operation == "append":
                mode = "a"
                final_content = "\n\n" + content + "\n"

            elif operation == "edit":
                # Surgical edits are applied and validated before staging.
                # Approval promotes the already-validated staged full-file content.
                final_content = content + "\n"

                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(final_content)

                log_file_operation(filename, operation, reason=reason, backup_path=backup_path)

                active_project = cl.user_session.get("active_create_project")
                if active_project:
                    append_create_build_state(active_project, f"""
## Approved File Operation — {datetime.now().isoformat(timespec='seconds')}

File: `{filename}`
Operation: `edit`
Backup: `{backup_path or 'None'}`

Summary:
Approved validated staged edit from CREATE workflow.
""")

                clear_pending_write()

                await cl.Message(content=f"Approved EDIT completed for: {filename}\nBackup: {backup_path or 'None'}").send()
                return

                with open(file_path, "r", encoding="utf-8") as f:
                    current = f.read()

                if old_text not in current:
                    await cl.Message(content=f"Edit refused: OLD text no longer exists exactly in `{filename}`. Re-read the file and stage the edit again.").send()
                    return

                final_content = current.replace(old_text, new_text, 1)

                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(final_content)

                log_file_operation(filename, operation, reason=reason, backup_path=backup_path)

                active_project = cl.user_session.get("active_create_project")
                if active_project:
                    append_create_build_state(active_project, f"""
## Approved File Operation — {datetime.now().isoformat(timespec='seconds')}

File: `{filename}`
Operation: `edit`
Backup: `{backup_path or 'None'}`

Summary:
Approved staged edit from CREATE workflow.
""")

                    append_build_doc_intake(
                        project_slug=active_project,
                        original_task=pending.get("original_task") or pending.get("task_goal") or pending.get("goal") or reason,
                        filename=filename,
                        operation=operation,
                        source_task_id=pending.get("task_id") or pending.get("source_task_id")
                    )

                clear_pending_write()

                await cl.Message(content=f"Approved EDIT completed for: {filename}\nBackup: {backup_path or 'None'}").send()
                return

            elif operation == "replace":
                mode = "w"
                final_content = content + "\n"

            else:
                await cl.Message(content=f"Unknown operation: {operation}").send()
                return

            with open(file_path, mode, encoding="utf-8") as f:
                f.write(final_content)

            log_file_operation(filename, operation, reason=reason, backup_path=backup_path)

            active_project = cl.user_session.get("active_create_project")
            if active_project:
                append_create_build_state(active_project, f"""
## Approved File Operation — {datetime.now().isoformat(timespec='seconds')}

File: `{filename}`
Operation: `{operation}`
Backup: `{backup_path or 'None'}`

Summary:
Approved staged file operation from CREATE workflow.
""")

                append_build_doc_intake(
                    project_slug=active_project,
                    original_task=pending.get("original_task") or pending.get("task_goal") or pending.get("goal") or reason,
                    filename=filename,
                    operation=operation,
                    source_task_id=pending.get("task_id") or pending.get("source_task_id")
                )

            clear_pending_write()

            await cl.Message(content=f"Approved {operation.upper()} completed for: {filename}\nBackup: {backup_path or 'None'}").send()
            return

        except Exception as e:
            await cl.Message(content=f"File operation failed: {str(e)}").send()
            return

    if user_text == "/cancel write":
        clear_pending_write()
        await cl.Message(content="Cancelled pending file write.").send()
        return



    if user_text.startswith("/create start "):
        goal = user_text.replace("/create start ", "", 1).strip()

        if not goal:
            await cl.Message(content="Use this format:\n\n/create start Make me a budgeting app").send()
            return

        project_slug = create_project_slug(goal)
        cl.user_session.set("active_create_project", project_slug)

        initial_plan = f"""# CREATE Project Plan — {project_slug}

## Original Request

{goal}

## Required Scope Fields

Each required field must be answered with either specific detail or `N/A — reason`.

### Goal / Outcome
Pending

### User / Audience
Pending

### First Usable Version
Pending

### Success Criteria
Pending

### Platform / Runtime
Pending

### Data / Persistence
Pending

### Integrations
Pending

### Permissions / Auth
Pending

### UI / UX Requirements
Pending

### Main Workflow
Pending

### Edge Cases
Pending

### Constraints / Non-Goals
Pending

### Priority Tradeoff
Pending

### Validation Plan
Pending

### Deployment / Running Environment
Pending

### Maintenance

### Future Expansion / Later Ideas
Pending

## Optional Notes / Nice-to-Haves

Pending

## Clarification Log

No answers captured yet.

## Next Step

Ask Caleb to answer each Required Scope Field with either details or `N/A — reason`, then run `/create answer` with the answers so they are migrated into this plan file.
"""

        created, rel_path = auto_create_project_file(project_slug, initial_plan)

        if not created:
            await cl.Message(content=f"CREATE project already exists: `{rel_path}`\nActive project set to `{project_slug}`.").send()
            return

        await cl.Message(content=f"""CREATE project started.

Project: `{project_slug}`
Plan file: `{rel_path}`

Answer the Required Scope Fields, then send them with `/create answer`.

You may answer any field with `N/A — reason`.

Required fields:
- Goal / Outcome
- User / Audience
- First Usable Version
- Success Criteria
- Platform / Runtime
- Data / Persistence
- Integrations
- Permissions / Auth
- UI / UX Requirements
- Main Workflow
- Edge Cases
- Constraints / Non-Goals
- Priority Tradeoff
- Validation Plan
- Deployment / Running Environment
- Maintenance""").send()
        return

    if user_text.startswith("/create answer"):
        answer_text = user_text.replace("/create answer", "", 1).strip()
        project_slug = cl.user_session.get("active_create_project")

        if not project_slug:
            await cl.Message(content="No active CREATE project. Start one with `/create start ...`.").send()
            return

        if not answer_text:
            await cl.Message(content="Use this format:\n\n/create answer\nYour answers here").send()
            return

        timestamp = datetime.now().isoformat(timespec="seconds")

        raw_block = f"""## Clarification Evidence — {timestamp}

### Raw Caleb Answers

{answer_text}
"""

        rel_path = auto_append_project_file(project_slug, raw_block)

        answered_field = detect_answered_create_field(answer_text)
        requested_research = create_answer_requests_research(answer_text)

        status = load_create_field_status(project_slug)

        status_note = "No required field marked answered."

        if answered_field and requested_research:
            status.setdefault("research_requested_fields", {})[answered_field] = {
                "requested_at": timestamp,
                "raw_answer": answer_text
            }

            research_path, research_rel_path = create_research_request_path(project_slug)
            os.makedirs(os.path.dirname(research_path), exist_ok=True)

            with open(research_path, "a", encoding="utf-8") as f:
                f.write(f"\n## Research Request — {timestamp}\n\n")
                f.write(f"### Field\n{answered_field}\n\n")
                f.write(f"### Raw Request\n{answer_text}\n\n")
                f.write("### Status\nPending research tool support.\n")

            status_note = f"Research requested for `{answered_field}`. Field was NOT marked answered. Research request: `{research_rel_path}`."
        elif answered_field:
            status.setdefault("answered_fields", {})[answered_field] = {
                "answered_at": timestamp,
                "raw_answer": answer_text
            }
            status_note = f"Marked `{answered_field}` as answered."
        elif requested_research:
            status_note = "Research/uncertainty detected, but no required field label was found. No field marked answered."

        status_rel_path = save_create_field_status(project_slug, status)

        await cl.Message(content=f"""Raw clarification evidence appended.

Project: `{project_slug}`
Plan file: `{rel_path}`
Field status: `{status_rel_path}`

{status_note}

Appended evidence:
---
{raw_block[:2000]}
---""").send()
        return



    if user_text.startswith("/create use "):
        project_slug = user_text.replace("/create use ", "", 1).strip()
        project_slug = create_project_slug(project_slug)

        file_path, rel_path = create_project_path(project_slug)

        if not Path(file_path).exists():
            await cl.Message(content=f"CREATE project not found: `{rel_path}`").send()
            return

        cl.user_session.set("active_create_project", project_slug)

        await cl.Message(content=f"Active CREATE project set to `{project_slug}`.\nPlan file: `{rel_path}`").send()
        return




    if user_text == "/create next-questions":
        project_slug = cl.user_session.get("active_create_project")

        if not project_slug:
            await cl.Message(content="No active CREATE project. Use `/create use <project_slug>` first.").send()
            return

        proposal_path, proposal_rel_path = create_project_path(project_slug, "PROJECT_PLAN_PROPOSAL.md")
        file_path, rel_path = create_project_path(project_slug)

        if Path(proposal_path).exists():
            active_plan_path = proposal_path
            active_rel_path = proposal_rel_path
        else:
            active_plan_path = file_path
            active_rel_path = rel_path

        if not Path(active_plan_path).exists():
            await cl.Message(content=f"Plan file not found: `{active_rel_path}`").send()
            return

        with open(active_plan_path, "r", encoding="utf-8") as f:
            plan = f.read()

        pending_fields = create_plan_has_pending_required_fields(plan)

        field_status = load_create_field_status(project_slug)
        answered_fields = set((field_status.get("answered_fields") or {}).keys())

        pending_fields = [field for field in pending_fields if field not in answered_fields]

        if not pending_fields:
            await cl.Message(content="All required fields are filled or answered. You can run `/create propose-fields`.").send()
            return

        question_prompt = f"""
You are guiding CREATE clarification in small batches.

Project: {project_slug}

Plan:
---
{plan[:20000]}
---

Pending fields:
{chr(10).join("- " + f for f in pending_fields)}

Your job:
- Ask EXACTLY 1 question
- Ask ONLY about the FIRST pending field listed below
- Do NOT ask about already-filled fields, even if they seem interesting or related
- Use prior answers explicitly in each question when relevant.
- Prefer confirmation-style questions when previous answers imply something.
- Each question should briefly mention the clue from the plan when possible.
- DO NOT assume or fill answers.
- DO NOT restate all fields
- Questions do not need to be short; they need to be clear and alignment-focused.
- Each question should include:
  - the field being clarified
  - why it matters
  - 2–4 example answer options when helpful
- Ask 1 robust question per batch, not a long form.

Good example:
"You mentioned using this on your phone — should this be a mobile-first web app or something else?"

Bad example:
"Platform: mobile app"

Return ONLY one question. Include the field name, why it matters, and 2–4 example answer options when helpful.
"""

        try:
            response = await call_ollama(
                [
                    {"role": "system", "content": "You ask targeted clarification questions for CREATE workflows."},
                    {"role": "user", "content": question_prompt}
                ],
                model=MODEL,
                temperature=0.3
            )

            await cl.Message(content=f"""Next clarification question:

{response}

Answer them using:

/create answer
<your answers>""").send()
            return

        except Exception as e:
            await cl.Message(content=f"Create next-questions failed: {str(e)}").send()
            return



    if user_text in ["/create propose-plan", "/create propose-fields"]:
        project_slug = cl.user_session.get("active_create_project")

        if not project_slug:
            await cl.Message(content="No active CREATE project. Use `/create use <project_slug>` first.").send()
            return

        file_path, rel_path = create_project_path(project_slug)

        if not Path(file_path).exists():
            await cl.Message(content=f"Plan/evidence file not found: `{rel_path}`").send()
            return

        with open(file_path, "r", encoding="utf-8") as f:
            evidence = f.read()

        proposal_prompt = f"""
You are creating a conservative field-placement proposal from raw CREATE project evidence.

Project: {project_slug}
Evidence file: {rel_path}

Raw evidence:
---
{evidence[-30000:]}
---

Your job:
- Produce a clean PROJECT_PLAN.md field proposal.
- Map explicit Caleb-provided answers into the correct required fields.
- Re-articulate within each field for clarity.
- Do NOT perform cross-field coherence analysis yet.
- Do NOT invent features, workflows, auth, tests, deployment, or edge cases.

Evidence precedence rules:
- Later clarification evidence overrides earlier conflicting evidence.
- "Priority Resolution", "Correction", "Override", or "For v1..." statements are GOVERNING CORRECTIONS when they conflict with earlier scope.
- A GOVERNING CORRECTION must be applied globally across all v1 fields.

Required internal process:
1. First identify the newest GOVERNING CORRECTION statements.
2. List what capabilities those corrections remove from v1.
3. List what capabilities those corrections move to future expansion.
4. Then generate the field proposal using the corrected v1 scope.
5. Before finalizing each v1 field, check whether it still contains removed capabilities.
6. If it does, rewrite that field to remove the contradicted capability.

Important:
- If a later correction removes a requirement from v1, remove that requirement from EVERY active v1 field, including Success Criteria, Main Workflow, Edge Cases, Validation Plan, Integrations, Constraints / Non-Goals, and Priority Tradeoff.
- If a later correction moves something to future expansion, keep it only in Maintenance or Optional Notes, not in Success Criteria, Main Workflow, Edge Cases, Validation Plan, Integrations, or v1 constraints.
- When older and newer evidence conflict, prefer the newest explicit Caleb decision.
- Do not preserve older contradicted requirements in any v1 field just because they appeared in earlier evidence.
- If a removed v1 capability was the only content for a field, rewrite that field around the remaining valid v1 scope or mark it Pending.
- If cross-device sync is removed from v1, do NOT preserve "across devices", "all devices", "phone/laptop/tablet sync", or similar language in active v1 fields unless it clearly means independent local use on each device with separate local data.
- For local-only v1 scope, phrase device behavior as "available locally on the device/browser where the data was entered" unless Caleb explicitly says otherwise.
- For validation under local-only v1 scope, validate each device/browser instance independently unless sync is explicitly reintroduced.
- Do NOT include the internal process in the final output.

Field rules:
- If a required field is unanswered, write exactly: Pending
- If Caleb explicitly said something is not applicable, write: N/A — reason
- Preserve important raw clarification notes at the bottom.

Required fields:
{chr(10).join("- " + field for field in CREATE_REQUIRED_FIELDS)}

Return ONLY the proposed PROJECT_PLAN.md content.
"""

        try:
            proposal = await call_ollama(
                [
                    {"role": "system", "content": "You are a strict project-plan field compiler. Extract only explicit user-provided scope."},
                    {"role": "user", "content": proposal_prompt}
                ],
                model="qwen2.5-coder:14b",
                temperature=0.1
            )

            proposal_path, proposal_rel_path = create_project_path(project_slug, "PROJECT_PLAN_PROPOSAL.md")
            os.makedirs(os.path.dirname(proposal_path), exist_ok=True)

            with open(proposal_path, "w", encoding="utf-8") as f:
                f.write(proposal.strip() + "\n")

            log_file_operation(
                proposal_rel_path,
                "create",
                reason="created CREATE project field proposal",
                backup_path=None
            )

            await cl.Message(content=f"""CREATE field proposal generated.

Proposal file: `{proposal_rel_path}`

Preview:
---
{proposal[:3000]}
---

Review it. If field placement/re-articulation looks good, the next step is `/create propose-final`.""").send()
            return

        except Exception as e:
            await cl.Message(content=f"Create propose-fields failed: {str(e)}").send()
            return



    if user_text == "/create audit-plan":
        project_slug = cl.user_session.get("active_create_project")

        if not project_slug:
            await cl.Message(content="No active CREATE project. Use `/create use <project_slug>` first.").send()
            return

        proposal_path, proposal_rel_path = create_project_path(project_slug, "PROJECT_PLAN_PROPOSAL.md")

        if not Path(proposal_path).exists():
            await cl.Message(content=f"Proposal file not found: `{proposal_rel_path}`. Run `/create propose-fields` first.").send()
            return

        with open(proposal_path, "r", encoding="utf-8") as f:
            proposal = f.read()

        fields = extract_create_plan_fields(proposal)

        pair_checks = [
            ("Success Criteria", "Constraints / Non-Goals"),
            ("Success Criteria", "Integrations"),
            ("Success Criteria", "Validation Plan"),
            ("Data / Persistence", "Integrations"),
            ("Data / Persistence", "Deployment / Running Environment"),
            ("Permissions / Auth", "Main Workflow"),
            ("Permissions / Auth", "Integrations"),
            ("Platform / Runtime", "Deployment / Running Environment"),
            ("Priority Tradeoff", "First Usable Version"),
            ("Priority Tradeoff", "Success Criteria"),
            ("Edge Cases", "Validation Plan"),
        ]

        triad_checks = [
            ("Success Criteria", "Constraints / Non-Goals", "Integrations"),
            ("Data / Persistence", "Deployment / Running Environment", "Integrations"),
            ("Permissions / Auth", "Main Workflow", "Integrations"),
            ("Priority Tradeoff", "Success Criteria", "First Usable Version"),
            ("Edge Cases", "Validation Plan", "Success Criteria"),
        ]

        findings = []

        async def run_check(label, selected):
            field_text = "\n\n".join(
                f"## {name}\n{fields.get(name, '').strip() or 'Pending'}"
                for name in selected
            )

            prompt = f"""
Can these requirements coexist as written?

Project: {project_slug}
Check type: {label}

Requirements:
---
{field_text}
---

Answer YES or NO first.

Return format:
### {label}: {' + '.join(selected)}

Can coexist: YES | NO
Reason:
...
"""

            return await call_ollama(
                [
                    {"role": "system", "content": "You perform focused architectural consistency audits."},
                    {"role": "user", "content": prompt}
                ],
                model=MODEL,
                temperature=0.1
            )

        try:
            for a, b in pair_checks:
                findings.append(await run_check("Pair Check", [a, b]))

            for triad in triad_checks:
                findings.append(await run_check("Triad Check", list(triad)))

            aggregate_prompt = f"""
You are aggregating focused CREATE plan audit findings.

Project: {project_slug}

Findings:
---
{chr(10).join(findings)}
---

Create a concise coherence review.

Sections:
## Executive Summary
## Blockers
## Tensions
## Scope Risks
## No-Issue Checks
## Recommended Clarifications Before Approval

Rules:
- Do not rewrite the project plan.
- Do not invent scope.
- If a blocker exists, say the plan should not be approved yet.
- If no blocker exists, say whether it is safe to proceed.
"""

            review = await call_ollama(
                [
                    {"role": "system", "content": "You aggregate focused audit findings into a decision-ready review."},
                    {"role": "user", "content": aggregate_prompt}
                ],
                model=MODEL,
                temperature=0.1
            )

            review_path, review_rel_path = create_project_path(project_slug, "PROJECT_COHERENCE_REVIEW.md")
            os.makedirs(os.path.dirname(review_path), exist_ok=True)

            with open(review_path, "w", encoding="utf-8") as f:
                f.write(review.strip() + "\n")

            await cl.Message(content=f"""CREATE coherence audit completed.

Review file: `{review_rel_path}`

Preview:
---
{review[:4000]}
---""").send()
            return

        except Exception as e:
            await cl.Message(content=f"Create audit-plan failed: {str(e)}").send()
            return



    if user_text == "/create resolve-audit":
        project_slug = cl.user_session.get("active_create_project")

        if not project_slug:
            await cl.Message(content="No active CREATE project. Use `/create use <project_slug>` first.").send()
            return

        review_path, review_rel_path = create_project_path(project_slug, "PROJECT_COHERENCE_REVIEW.md")
        proposal_path, proposal_rel_path = create_project_path(project_slug, "PROJECT_PLAN_PROPOSAL.md")

        if not Path(review_path).exists():
            await cl.Message(content=f"Coherence review not found: `{review_rel_path}`. Run `/create audit-plan` first.").send()
            return

        if not Path(proposal_path).exists():
            await cl.Message(content=f"Proposal file not found: `{proposal_rel_path}`. Run `/create propose-fields` first.").send()
            return

        with open(review_path, "r", encoding="utf-8") as f:
            review = f.read()

        with open(proposal_path, "r", encoding="utf-8") as f:
            proposal = f.read()

        prompt = f"""
You are creating targeted remediation questions from a CREATE project coherence audit.

Project: {project_slug}

Current field proposal:
---
{proposal[:20000]}
---

Coherence review:
---
{review[:20000]}
---

Your job:
- Do NOT fix the plan yourself.
- Convert the most important blocker/tension into a user decision.
- Ask EXACTLY ONE question.
- The question must explain:
  1. which requirements conflict
  2. why they cannot both be true as written
  3. what governing choice Caleb must make
- Provide 2-4 answer options when helpful.
- Avoid generic stakeholder language.
- Ask Caleb directly.

Return ONLY the remediation question.
"""

        try:
            question = await call_ollama(
                [
                    {"role": "system", "content": "You turn project audit blockers into targeted clarification questions."},
                    {"role": "user", "content": prompt}
                ],
                model=MODEL,
                temperature=0.2
            )

            await cl.Message(content=f"""Audit remediation question:

{question}

Answer using:

/create answer
<your corrected decision>

Then rerun:
/create propose-fields
/create audit-plan""").send()
            return

        except Exception as e:
            await cl.Message(content=f"Create resolve-audit failed: {str(e)}").send()
            return


    if user_text == "/create propose-final":
        project_slug = cl.user_session.get("active_create_project")

        if not project_slug:
            await cl.Message(content="No active CREATE project. Use `/create use <project_slug>` first.").send()
            return

        proposal_path, proposal_rel_path = create_project_path(project_slug, "PROJECT_PLAN_PROPOSAL.md")

        if not Path(proposal_path).exists():
            await cl.Message(content=f"Proposal file not found: `{proposal_rel_path}`").send()
            return

        with open(proposal_path, "r", encoding="utf-8") as f:
            proposal = f.read()

        final_prompt = f"""
You are refining a CREATE project plan into a final draft.

Project: {project_slug}

Current proposal:
---
{proposal[:30000]}
---

Your job:
- Preserve Caleb's original meaning and scope.
- Improve wording only where it makes the plan clearer.
- Keep the required-field structure stable.
- DO NOT invent new scope.
- DO NOT add new requirements.
- DO NOT remove important constraints.

Most importantly, perform a strict cross-field constraint compatibility audit.

For every major requirement, compare it against:
- success criteria
- constraints / non-goals
- integrations
- data / persistence
- permissions / auth
- deployment / runtime
- priority tradeoffs
- validation plan
- future expansion notes

You MUST flag:
- mutually exclusive requirements
- requirements that contradict constraints or non-goals
- success criteria that require excluded capabilities
- validation plans that assume impossible behavior
- integrations that violate stated data, privacy, local, offline, or deployment constraints
- deployment/runtime choices that conflict with data, auth, integration, or success criteria assumptions
- priority tradeoffs that conflict with v1 requirements
- future expansion notes that create risk for current architecture
- hidden infrastructure required by any stated requirement

After the final draft, include:

## Change Rationale

Briefly explain actual wording/structure changes made. Do not claim changes you did not make.

## Coherence Review

Include:
- Confirmed Alignments
- Potential Tensions
- Scope Risks
- Important Non-Changes
- Remaining Ambiguities

Rules for Coherence Review:
- Do NOT change scope inside the review.
- Do NOT turn observations into requirements.
- Phrase uncertain items as review notes, not decisions.
- If no issue exists, say so plainly.

Return ONLY the final draft markdown.
"""

        try:
            response = await call_ollama(
                [
                    {"role": "system", "content": "You refine project plans for clarity and coherence without changing scope."},
                    {"role": "user", "content": final_prompt}
                ],
                model=MODEL,
                temperature=0.2
            )

            final_path, final_rel_path = create_project_path(project_slug, "PROJECT_PLAN_FINAL_DRAFT.md")
            os.makedirs(os.path.dirname(final_path), exist_ok=True)

            with open(final_path, "w", encoding="utf-8") as f:
                f.write(response.strip() + "\n")

            await cl.Message(content=f"""CREATE final proposal generated.

Final draft file: `{final_rel_path}`

Preview:
---
{response[:4000]}
---

Review it carefully. If it accurately reflects the intended project scope and architecture, the next step is `/create approve-plan`.""").send()
            return

        except Exception as e:
            await cl.Message(content=f"Create propose-final failed: {str(e)}").send()
            return



    if user_text == "/create approve-plan":
        project_slug = cl.user_session.get("active_create_project")

        if not project_slug:
            await cl.Message(content="No active CREATE project. Use `/create use <project_slug>` first.").send()
            return

        final_path, final_rel_path = create_project_path(project_slug, "PROJECT_PLAN_FINAL_DRAFT.md")
        proposal_path, proposal_rel_path = create_project_path(project_slug, "PROJECT_PLAN_PROPOSAL.md")
        plan_path, plan_rel_path = create_project_path(project_slug, "PROJECT_PLAN.md")

        review_path, review_rel_path = create_project_path(project_slug, "PROJECT_COHERENCE_REVIEW.md")

        if not Path(review_path).exists():
            await cl.Message(content="No coherence review found. Run `/create audit-plan` before approving.").send()
            return

        with open(review_path, "r", encoding="utf-8") as f:
            review_text = f.read()

        review_lower = review_text.lower()

        blocker_indicators = [
            "## blockers",
            "blockers\n",
            "finding: blocker",
            "severity: blocker",
            "should not be approved",
            "not be approved",
            "do not approve",
            "cannot proceed",
        ]

        clean_indicators = [
            "there are no blockers",
            "no blockers identified",
            "no blockers were identified",
            "safe to proceed",
            "safe to approve",
        ]

        has_blocker = any(x in review_lower for x in blocker_indicators)
        has_clean_signal = any(x in review_lower for x in clean_indicators)

        if has_blocker and not has_clean_signal:
            await cl.Message(content=f"""Cannot approve CREATE plan yet.

Reason: `{review_rel_path}` appears to contain blockers or a do-not-approve recommendation.

Run:
`/create resolve-audit`

or update the plan and rerun:
`/create propose-fields`
`/create audit-plan`""").send()
            return

        source_candidates = []

        if Path(final_path).exists():
            source_candidates.append((Path(final_path).stat().st_mtime, final_path, final_rel_path))

        if Path(proposal_path).exists():
            source_candidates.append((Path(proposal_path).stat().st_mtime, proposal_path, proposal_rel_path))

        if not source_candidates:
            await cl.Message(content="No final draft or proposal found. Run `/create propose-fields` first.").send()
            return

        _, source_path, source_rel_path = max(source_candidates, key=lambda x: x[0])

        backup_dir = Path.home() / "Desktop" / "Leo_Files" / "BACKUPS"
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = plan_rel_path.replace("/", "__")
        backup_path = backup_dir / f"{timestamp}_APPROVE_{safe_name}.bak"

        if Path(plan_path).exists():
            shutil.copy2(plan_path, backup_path)

        with open(source_path, "r", encoding="utf-8") as f:
            approved_plan = f.read()

        approval_stamp = datetime.now().isoformat(timespec="seconds")
        approval_header = f"""<!-- CREATE_APPROVED: true -->
<!-- CREATE_APPROVED_AT: {approval_stamp} -->
<!-- CREATE_APPROVED_FROM: {source_rel_path} -->

"""

        if "<!-- CREATE_APPROVED: true -->" not in approved_plan:
            approved_plan = approval_header + approved_plan

        with open(plan_path, "w", encoding="utf-8") as f:
            f.write(approved_plan.strip() + "\n")

        log_file_operation(
            plan_rel_path,
            "replace",
            reason=f"approved CREATE project plan from {source_rel_path}",
            backup_path=str(backup_path) if backup_path.exists() else None
        )

        await cl.Message(content=f"""CREATE project plan approved.

Canonical plan: `{plan_rel_path}`
Approved from: `{source_rel_path}`
Backup: `{backup_path if backup_path.exists() else "none"}`

This project now has an approved operating contract.""").send()
        return


    if user_text == "/create sync":
        project_slug = cl.user_session.get("active_create_project")

        if not project_slug:
            await cl.Message(content="No active CREATE project. Use `/create use <project_slug>` first.").send()
            return

        file_path, rel_path = create_project_path(project_slug)

        if not Path(file_path).exists():
            await cl.Message(content=f"Plan file not found: `{rel_path}`").send()
            return

        with open(file_path, "r", encoding="utf-8") as f:
            plan = f.read()

        sync_prompt = f"""
You are syncing a CREATE project plan.

Project: {project_slug}
Plan file: {rel_path}

Latest clarification updates / plan tail:
---
{plan[-12000:]}
---

Full plan / beginning:
---
{plan[:12000]}
---

Rewrite the plan into the current required-field structure.

Rules:
- Preserve the original request.
- You may ONLY use text that already exists in the plan (Original Request + Clarification Updates).
- You are NOT allowed to introduce ANY new concepts, workflows, features, or behaviors.
- You are performing extraction and reorganization ONLY, not generation.

- If a field is not explicitly specified in existing text, write EXACTLY: `Pending`.

- DO NOT:
  - invent flows (e.g., login, auth, export/import)
  - add edge cases
  - add validation steps
  - add system behaviors
  - expand short answers into detailed systems

- DO:
  - copy relevant phrases
  - lightly rephrase for clarity (without adding meaning)
  - keep content minimal and literal

Field mapping guidance:
- "responsive React web app", "desktop and phone", or "web interface" belongs under Platform / Runtime.
- "local storage", "manual entry", or "local-only data" belongs under Data / Persistence.
- "no login", "no auth", or "personal use" belongs under Permissions / Auth.
- Do NOT map data storage answers into Deployment / Running Environment.
- Deployment / Running Environment should remain Pending unless Caleb explicitly describes how/where the app will be run or hosted.
- Phrases like "user manually enters", "then the user views", "after that", or step-by-step usage belong under Main Workflow.
- Phrases like "irregular one-time transactions", "missing optional fields", "future-dated transactions", "negative cashflow months", or "forecast changes without changing dashboard data" belong under Edge Cases.
- Phrases like "validate v1", "validation plan", "sample data", "known expected results", "correctly calculate", "cashflow", "net worth", "monthly recurring income and expenses", or "forecast changes" belong under Validation Plan.
- If Caleb says the app should be validated by sample data with known expected results, copy that directly into Validation Plan.

- If Caleb explicitly said something is not applicable, use: `N/A — reason`
- Otherwise, missing = `Pending`
- Preserve clarification history under Clarification Log.
- Keep the output concise and literal.

Required fields:
{chr(10).join("- " + field for field in CREATE_REQUIRED_FIELDS)}

Return ONLY the full updated PROJECT_PLAN.md content.
"""

        try:
            synced = await call_ollama(
                [
                    {"role": "system", "content": "You rewrite CREATE project plans into structured durable scope files."},
                    {"role": "user", "content": sync_prompt}
                ],
                model=MODEL,
                temperature=0.1
            )

            stage_file_operation(
                rel_path,
                synced.strip(),
                operation="replace",
                reason="sync CREATE project plan required fields"
            )

            await cl.Message(content=f"""CREATE plan sync staged.

Operation: REPLACE
Risk: HIGH — this rewrites the project plan file.
File: {rel_path}

Preview:
---
{synced[:2500]}
---

To approve:
/approve replace

To cancel:
/cancel write""").send()
            return

        except Exception as e:
            await cl.Message(content=f"Create sync failed: {str(e)}").send()
            return




    if user_text == "/create build-queue":
        project_slug = cl.user_session.get("active_create_project")

        if not project_slug:
            await cl.Message(content="No active CREATE project. Use `/create use <project_slug>` first.").send()
            return

        plan_path, plan_rel_path = create_project_path(project_slug, "PROJECT_PLAN.md")

        if not Path(plan_path).exists():
            await cl.Message(content=f"Approved plan not found: `{plan_rel_path}`").send()
            return

        with open(plan_path, "r", encoding="utf-8") as f:
            plan = f.read()

        build_state = load_create_build_state(project_slug)

        state_path, state_rel_path = create_project_path(project_slug, "PROJECT_BUILD_STATE.md")
        intake_path, intake_rel_path = create_project_path(project_slug, "PROJECT_BUILD_DOC_INTAKE.md")

        if Path(intake_path).exists():
            intake_mtime = Path(intake_path).stat().st_mtime
            state_mtime = Path(state_path).stat().st_mtime if Path(state_path).exists() else 0

            if intake_mtime > state_mtime:
                await cl.Message(content=f"""Cannot create build queue yet.

Reason:
`{intake_rel_path}` contains approved implementation intake newer than `{state_rel_path}`.

Project reality may be stale.

Run:
`/create document-state`

Then run:
`/create build-queue`""").send()
                return

        if "<!-- CREATE_APPROVED: true -->" not in plan:
            await cl.Message(content=f"""Cannot create build queue yet.

Reason: `{plan_rel_path}` is not approved.

Run:
`/create propose-fields`
`/create audit-plan`
`/create approve-plan`""").send()
            return

        queue_prompt = f"""
You are decomposing an approved CREATE project plan into small implementation tasks.

Project: {project_slug}
Approved plan file: {plan_rel_path}

Approved project plan:
---
{plan[:20000]}
---

Current CREATE build state:
---
{build_state or "No approved build state recorded yet."}
---

Create a bounded executable build queue and a deferred backlog.

Your job:
- Select the next useful build area from the approved plan.
- Break that area down recursively until each executable task is small enough for the task runner to implement directly.
- Create up to 50 executable tasks.
- Put larger areas/slices that should be decomposed later into backlog_items.

Return ONLY valid JSON in this format:
{{
  "executable_tasks": [
    {{
      "task": "short executable task",
      "target_file": "src/path/File.js",
      "acceptance_criteria": "specific acceptance criteria",
      "depends_on": [],
      "prerequisites": [
        {{
          "file": "src/path/PrerequisiteFile.js",
          "must_contain": ["requiredStringOne", "requiredStringTwo"],
          "reason": "why these strings must exist before this task can safely run"
        }}
      ],
      "why_now": "why this task should happen at this point in the sequence"
    }}
  ],
  "backlog_items": ["larger slice to decompose later"]
}}

Rules:
- executable_tasks must be ordered in the recommended execution sequence.
- Put prerequisite/setup tasks before tasks that depend on them.
- Use depends_on to name earlier task titles this task relies on.
- Use prerequisites to declare simple mechanical file/string checks that must pass before this task can safely run.
- prerequisites must be broad software-development checks, not app-specific guesses.
- Only include prerequisites when a later task genuinely depends on earlier file contents.
- Use why_now to explain the sequencing reason.
- Decompose by behavior, not by broad feature name.
- One executable task should change one behavior in one target file when possible.
- Each task should be small enough that a coding model can implement it directly.
- Each task must include a target file path when possible.
- Each task must describe the concrete behavior to add or change.
- Each task must include minimum acceptance criteria.
- Avoid placeholder-friendly tasks like "create component" or "implement feature."
- Prefer tasks like "add localStorage fallback to Dashboard.js" or "add controlled inputs for dashboard metrics."
- Use the approved plan as scope control.
- Prefer implementation tasks over planning tasks.
- Do not include testing-only tasks yet.
- Do not include future expansion work.
- Keep each task goal short, concrete, and executable.
- Avoid giant tasks that combine data model, UI, persistence, validation, and styling all at once.
- Return ONLY the JSON object. No markdown.
"""

        try:
            raw = await call_ollama(
                [
                    {"role": "system", "content": "You decompose approved project plans into small implementation tasks."},
                    {"role": "user", "content": queue_prompt}
                ],
                model=MODEL,
                temperature=0.2
            )

            queue_obj = parse_json_or_none(raw)

            if not isinstance(queue_obj, dict):
                repair = await call_ollama(
                    [
                        {"role": "system", "content": "Repair this into ONLY a valid JSON object with executable_tasks and backlog_items arrays. No markdown."},
                        {"role": "user", "content": raw}
                    ],
                    temperature=0.0
                )
                queue_obj = parse_json_or_none(repair)

            if not isinstance(queue_obj, dict):
                await cl.Message(content=f"""Create build-queue failed: model did not return a JSON object.

Raw output:
---
{raw[:2000]}
---""").send()
                return

            tasks_list = queue_obj.get("executable_tasks") or []
            backlog_items = queue_obj.get("backlog_items") or []

            if not isinstance(tasks_list, list):
                tasks_list = []

            if not isinstance(backlog_items, list):
                backlog_items = []

            backlog_path, backlog_rel_path = create_project_path(project_slug, "PROJECT_BUILD_BACKLOG.md")
            os.makedirs(os.path.dirname(backlog_path), exist_ok=True)

            if backlog_items:
                with open(backlog_path, "a", encoding="utf-8") as f:
                    f.write(f"\n## Build Backlog — {datetime.now().isoformat(timespec='seconds')}\n\n")
                    for item in backlog_items:
                        if isinstance(item, str) and item.strip():
                            f.write(f"- {item.strip()}\n")

            queue_batch_id = str(uuid.uuid4())[:8]
            created = []
            created_entries = []
            for queue_order, item in enumerate(tasks_list[:50], start=1):
                task_goal = ""
                file_path = ""
                task_title = ""
                depends_on_titles = []
                prerequisites = []

                if isinstance(item, str):
                    task_goal = item.strip()
                    task_title = task_goal

                elif isinstance(item, dict):
                    title = (item.get("task") or item.get("goal") or item.get("title") or "").strip()
                    file_path = (item.get("target_file") or item.get("file_path") or item.get("file") or "").strip()
                    acceptance = (item.get("acceptance_criteria") or item.get("acceptance") or "").strip()
                    depends_on_titles = normalize_task_dependency_titles(item.get("depends_on"))
                    prerequisites = normalize_task_prerequisites(item.get("prerequisites"))
                    why_now = (item.get("why_now") or item.get("sequence_reason") or "").strip()
                    task_title = title

                    parts = []
                    if title:
                        parts.append(title)
                    if file_path:
                        parts.append(f"Target file: {file_path}")
                    if acceptance:
                        parts.append(f"Acceptance criteria: {acceptance}")
                    if depends_on_titles:
                        parts.append("Depends on: " + "; ".join(depends_on_titles))
                    if prerequisites:
                        prereq_bits = []
                        for prereq in prerequisites:
                            prereq_bits.append(f"{prereq.get('file')}: " + ", ".join(prereq.get("must_contain", [])))
                        parts.append("Prerequisites: " + "; ".join(prereq_bits))
                    if why_now:
                        parts.append(f"Why now: {why_now}")

                    task_goal = "\n".join(parts).strip()

                if not task_goal:
                    continue

                target_file = file_path or extract_task_target_file(task_goal)
                task = create_task(
                    task_goal,
                    assigned_role="leader",
                    requested_by="create_build_queue",
                    inputs={
                        "intent": "BUILD",
                        "approved_create_project": project_slug,
                        "approved_plan_file": plan_rel_path,
                        "original_queue_task": task_goal,
                        "queue_task_created_at": datetime.now().isoformat(timespec="seconds"),
                        "depends_on": depends_on_titles,
                        "depends_on_titles": depends_on_titles,
                        "depends_on_task_ids": [],
                        "prerequisites": prerequisites,
                        "queue_batch_id": queue_batch_id,
                        "queue_order": queue_order,
                        "read_before_modify": plan_rel_path,
                        "target_file": target_file,
                        "tool_limits": {
                            "file_reads": "unlimited",
                            "max_file_writes": 1,
                            "writable_files": [target_file] if target_file else [],
                            "allow_multi_file_output": False
                        }
                    }
                )
                created.append(task)
                created_entries.append({
                    "task": task,
                    "title": task_title,
                    "depends_on_titles": depends_on_titles,
                    "queue_order": queue_order
                })

            for idx, entry in enumerate(created_entries):
                resolved_dependency_ids = []
                dependency_titles = entry.get("depends_on_titles") or []

                if dependency_titles:
                    earlier_entries = created_entries[:idx]
                    earlier_title_map = {
                        canonicalize_dependency_title(e.get("title")): e.get("task", {}).get("task_id")
                        for e in earlier_entries
                        if canonicalize_dependency_title(e.get("title")) and e.get("task", {}).get("task_id")
                    }

                    for dependency_title in dependency_titles:
                        resolved_id = earlier_title_map.get(canonicalize_dependency_title(dependency_title))
                        if resolved_id:
                            resolved_dependency_ids.append(resolved_id)

                update_task(entry["task"]["task_id"], {
                    "inputs": {
                        **(entry["task"].get("inputs") or {}),
                        "depends_on_task_ids": resolved_dependency_ids
                    }
                })
                entry["task"]["inputs"]["depends_on_task_ids"] = resolved_dependency_ids

            if not created:
                await cl.Message(content=f"""Create build-queue failed: no valid task goals were created.

Parsed object type: `{type(queue_obj).__name__}`
Parsed keys: `{list(queue_obj.keys()) if isinstance(queue_obj, dict) else "N/A"}`
Executable tasks type: `{type(tasks_list).__name__}`
Executable tasks preview:
---
{str(tasks_list)[:2000]}
---

Raw output preview:
---
{raw[:2000]}
---""").send()
                return

            lines = []
            for t in created:
                lines.append(f"- `{t['task_id']}` — {t['goal']}")

            await cl.Message(content=f"""✅ CREATE build queue created.

Project: `{project_slug}`
Approved plan: `{plan_rel_path}`
Tasks created: {len(created)}
Backlog items saved: {len([x for x in backlog_items if isinstance(x, str) and x.strip()])}
Backlog file: `{backlog_rel_path}`

{chr(10).join(lines)}

Next:
`/task run next`""").send()
            return

        except Exception as e:
            await cl.Message(content=f"Create build-queue failed: {str(e)}").send()
            return


    if user_text == "/create build-task":
        await cl.Message(content="""`/create build-task` has been replaced.

Use the current CREATE build flow:

1. `/create build-queue`
2. `/create compile-task <source_task_id>`
3. `/task run <compiled_task_id>`""").send()
        return

    if False and user_text == "/create build-task":
        project_slug = cl.user_session.get("active_create_project")

        if not project_slug:
            await cl.Message(content="No active CREATE project. Use `/create use <project_slug>` first.").send()
            return

        plan_path, plan_rel_path = create_project_path(project_slug, "PROJECT_PLAN.md")

        if not Path(plan_path).exists():
            await cl.Message(content=f"Approved plan not found: `{plan_rel_path}`").send()
            return

        with open(plan_path, "r", encoding="utf-8") as f:
            plan = f.read()

        if "<!-- CREATE_APPROVED: true -->" not in plan:
            await cl.Message(content=f"""Cannot create build task yet.

Reason: `{plan_rel_path}` is not approved.

Run:
`/create propose-fields`
`/create audit-plan`
`/create approve-plan`""").send()
            return

        build_state = load_create_build_state(project_slug)

        task_prompt = f"""
You are choosing the next implementation move for a CREATE project.

Project: {project_slug}
Approved plan file: {plan_rel_path}

Approved project plan:
---
{plan[:20000]}
---

Current CREATE build state:
---
{build_state or "No approved build state recorded yet."}
---

Target-file preservation anchors detected from existing code:
---
{preservation_anchors or "No target file anchors available yet."}
---

These anchors are implementation progress. A strong compiled task helps the BUILD runner preserve them while adding the requested slice.

BUILD runner capability profile:
The BUILD runner receives:
- the ACTIVE PROJECT OPERATING CONTRACT
- this task prompt
- the target file path when specified
- the existing target file only after Caleb runs /file read for safety
- a one-file write limit for this task

The BUILD runner does not automatically inspect the whole codebase, run tests, infer hidden dependencies, or safely coordinate multi-file changes.

Because the BUILD runner currently has a one-file write limit, create tasks that can be completed with exactly one file operation.
If the implementation idea naturally requires multiple files, choose the first useful one-file slice instead of asking for the whole multi-file change.

Single-file slices are strongly preferred because they are easier to review, approve, rollback, document, and test.
A strong task describes one target file and one complete improvement inside that file.
When a broader feature will eventually need wiring across multiple files, this task should focus on the safest useful slice first, such as creating the isolated component, improving one existing component, wiring an existing component into one parent file, passing existing state into one child component, or preparing one file’s props/callback interface for the next slice.
Connection and integration tasks are encouraged when the implementation can be completed cleanly through one target file.

Write the task for the actual BUILD runner, not an ideal engineer with full repo awareness.

Central question:
Given the approved plan and current build state, if this exact task were handed to the BUILD runner, would it likely produce working code that moves the project forward from where it is now?

Do not choose tasks that mainly rebuild or re-solve work already recorded in the current build state unless that existing work is clearly insufficient.

Your job:
Create exactly ONE BUILD task prompt that passes that test.

Before returning the task, mentally check:
Would this exact task likely produce working code that moves the approved plan forward in this run?

What the task must do:
- Identify one concrete implementation move.
- Target one file when possible.
- Ask for working code that changes runtime behavior.
- Preserve the approved project plan as scope control.
- Include the minimum implementation context needed for the BUILD runner to succeed.
- Prefer user-facing or runtime behavior over static display when the approved plan requires interaction.
- Keep the task bounded enough to execute in one file operation.
- Ask for complete file content only.
- If sample data or placeholders are used, they should demonstrate working behavior, not replace it.
- Prefer tasks that leave the app more usable after this run.
- When targeting an existing file, preserve the current state shape, data model, imports, and compatible behavior unless the approved task explicitly calls for changing them.
- When targeting an existing file, strong tasks build on the file's current component style and existing handlers. If the file is a functional component using hooks, prefer extending that pattern rather than changing paradigms. Existing user-facing inputs and handlers are valuable implementation progress and should be preserved while adding the requested slice.
- You are encouraged to include small example snippets, sample inputs/outputs, function signatures, or expected runtime behavior when they help clarify the implementation move.
- Examples should clarify intent and expected behavior, not fully implement the solution.
- The BUILD runner should still perform the implementation work.
- Favor tasks that move real application behavior forward: state, input, persistence, calculation, validation, rendering from data, or integration with an existing file.
- A strong task should feel like the next useful coding move in an active app build, not a demo or tutorial.
- Placeholder values or sample data are appropriate when they help demonstrate that the implemented behavior works.
- They should support working behavior, not replace it.
- The task should still create working behavior in this run.
- Avoid tasks where the main deliverable is only mock display, placeholder layout, or future-facing scaffolding.

Prefer:
- runtime behavior over static display-only output
- stateful or persistent behavior when the approved plan calls for user-entered data
- calculations wired to actual state/data instead of fixed display values
- local persistence when the approved plan calls for local/manual data
- concrete implementation movement over mock-only presentation
- sample data that proves behavior works, not sample data instead of behavior
- one bounded file/artifact at a time

A weak task usually says things like:
- render placeholders
- mock display only
- initial layout only
- future tasks will add the real behavior
- verify placeholder values

A strong task usually says things like:
- load default data into state
- let the user edit values
- save/load from localStorage
- calculate derived metrics from state
- render data from user-entered or persisted values
- update existing file behavior

Return ONLY the final BUILD task prompt text.
"""

        try:
            task_goal = await call_ollama(
                [
                    {"role": "system", "content": "You convert approved project plans into concrete first build tasks."},
                    {"role": "user", "content": task_prompt}
                ],
                model=MODEL,
                temperature=0.2
            )

            task = create_task(
                task_goal.strip(),
                inputs={
                    "intent": "BUILD",
                    "approved_create_project": project_slug,
                    "approved_plan_file": plan_rel_path,
                    "read_before_modify": plan_rel_path,
                    "target_file": extract_task_target_file(task_goal),
                    "tool_limits": {
                        "file_reads": "unlimited",
                        "max_file_writes": 1,
                        "writable_files": [extract_task_target_file(task_goal)] if extract_task_target_file(task_goal) else [],
                        "allow_multi_file_output": False
                    }
                }
            )

            cl.user_session.set("last_created_task_id", task["task_id"])

            await cl.Message(content=f"""✅ CREATE build task created.

Project: `{project_slug}`
Task ID: `{task['task_id']}`
Approved plan: `{plan_rel_path}`

Task:
{task['goal']}

Next:
`/task run {task['task_id']}`
or
`/task run next`""").send()
            return

        except Exception as e:
            await cl.Message(content=f"Create build-task failed: {str(e)}").send()
            return



    if user_text == "/create document-state":
        project_slug = cl.user_session.get("active_create_project")

        if not project_slug:
            await cl.Message(content="No active CREATE project. Use `/create use <project_slug>` first.").send()
            return

        plan_path, plan_rel_path = create_project_path(project_slug, "PROJECT_PLAN.md")
        state_path, state_rel_path = create_project_path(project_slug, "PROJECT_BUILD_STATE.md")

        if not Path(plan_path).exists():
            await cl.Message(content=f"Approved plan not found: `{plan_rel_path}`").send()
            return

        with open(plan_path, "r", encoding="utf-8") as f:
            plan = f.read()

        existing_state = ""
        if Path(state_path).exists():
            existing_state = Path(state_path).read_text(encoding="utf-8")

        intake_path, intake_rel_path = create_project_path(project_slug, "PROJECT_BUILD_DOC_INTAKE.md")
        build_doc_intake = ""
        if Path(intake_path).exists():
            build_doc_intake = Path(intake_path).read_text(encoding="utf-8")

        pending_intake_summary = extract_pending_build_doc_intake_entries(build_doc_intake)

        # Lightweight project file scan
        project_root = Path(LEO_FILES_PATH)
        candidate_files = []
        for pattern in ["src/**/*.js", "src/**/*.jsx", "src/**/*.ts", "src/**/*.tsx"]:
            candidate_files.extend(project_root.glob(pattern))

        file_summaries = []
        for fp in sorted(candidate_files)[:20]:
            try:
                rel = str(fp.relative_to(project_root))
                content = fp.read_text(encoding="utf-8")[:2500]
                file_summaries.append(f"--- FILE: {rel} ---\n{content}")
            except Exception:
                pass

        document_prompt = f"""
You are the DOCUMENT state maintainer for Caleb's CREATE project.

Your job:
Update the durable build-state document so future build tasks know what already exists and what should happen next.

Approved plan:
---
{plan[:12000]}
---

Existing build state:
Historical context only. Do not preserve prior classifications unless supported by current intake, tester evidence, or source evidence.
---
{existing_state[-8000:] if existing_state else "No prior build state recorded."}
---

Project build doc intake:
File: {intake_rel_path if build_doc_intake else "None"}
---
{build_doc_intake[-12000:] if build_doc_intake else "No build doc intake recorded yet."}
---

Extracted pending intake entries:
Every item in this list must appear under "Implementation Approved / Pending Tester".
---
{pending_intake_summary}
---

Current project file snapshots:
---
{chr(10).join(file_summaries) if file_summaries else "No source files found."}
---

Write PROJECT_BUILD_STATE.md content.

Focus on facts, not speculation.

Required evidence handling:
- PROJECT_BUILD_DOC_INTAKE.md is a required task-evidence source.
- Every intake item must be reflected somewhere in PROJECT_BUILD_STATE.md.
- Every "## Build Doc Intake" entry with "Tester Status: pending" must appear under "Implementation Approved / Pending Tester".
- If PROJECT_BUILD_DOC_INTAKE.md contains multiple pending entries, include all of them.
- If an intake item says Status: implementation-approved or implementation-approved-backfill, do not list that task as fully missing.
- If Tester Status is pending, classify the item as implementation-approved / pending tester verification unless the intake entry represents foundational project scaffolding or baseline app structure.
- Baseline/foundation/scaffolding intake entries belong under "Baseline Implementation Facts".
- Baseline Implementation Facts are foundational project structures that exist and are accepted as current project substrate.
- Do not mark pending-tester work as fully completed.
Documenter role:
You preserve verified project reality for the entire team.

You preserve project reality for the team.

Accuracy matters more than alarm.
Empty sections are valid when no evidence belongs there.
Record only evidenced reality.

Your state documents become operational context for:
- planners
- build queues
- compilers
- task runners
- coders
- testers

Verified reality includes:
- approved implementation evidence
- tester verification results
- source-file evidence
- failed implementation evidence
- not-started approved-plan work with no implementation evidence yet
- contradictions between evidence sources
- uncertain areas where evidence conflicts or is insufficient to classify

Your job is not to speculate.
Your job is to preserve operational truth with maximum clarity and continuity.

If intake evidence and source files disagree, mark the item as uncertain and explain the contradiction.

Classification rules:
- Each work item belongs in exactly one section.
- Implementation-approved with Tester Status pending belongs only in Implementation Approved / Pending Tester.
- Pending tester work is not completed.
- Lack of implementation evidence does not equal evidence of failure.
- A verified failure/block requires an explicit failure source: tester failure, implementation failure, approval failure, rejection, or direct contradiction.
- "Not found", "not present", "not added", or "not implemented yet" are not failure sources; classify those as Not Started unless evidence conflicts.
- If source evidence exists but approved behavior is incomplete, classify as Partially Implemented / Needs Expansion.
- Partially Implemented / Needs Expansion requires specific non-empty Existing Evidence from source files, intake, or tester results.
- If Existing Evidence would be "None", the item cannot be Partially Implemented; classify it as Not Started unless evidence conflicts.
- Partial implementation is not failure evidence unless a verified source explicitly indicates failure.
- If a plan requirement has no intake, no tester result, and no source evidence, classify it as Not Started.
- Not-started work is not uncertain unless evidence conflicts.
- Use "None" for empty sections.
- Do not copy these classification rules into PROJECT_BUILD_STATE.md.

Truth hierarchy:
1. PROJECT_BUILD_DOC_INTAKE.md and tester results are workflow truth.
2. Current source files are implementation evidence.
3. Existing PROJECT_BUILD_STATE.md is historical context only.
4. Approved plan is scope reference only.

Current source files show what currently exists.
They do not override workflow classification state.

Do not use current files alone to classify work as:
- completed
- verified
- failed
- blocked
- pending tester

Workflow classification must come from intake evidence and tester evidence first.

Output exactly this structure:

# Project Build State — {project_slug}

## Implementation Approved / Pending Tester
- None, or:
- Item: <short name>
  - Original Queue Task: <task text>
  - Target File: <file path>
  - Approved Operation: <operation>
  - Evidence: <approved implementation/source evidence>
  - Tester Status: pending

## Completed Build Items
- None, or:
- Item: <short name>
  - Verified By: <tester/non-UI/internal evidence>
  - Evidence: <specific evidence>
  - Files: <files involved>

## Baseline Implementation Facts
- None, or:
- Item: <short name>
  - Baseline Evidence: <foundational implementation evidence>
  - Files: <files involved>
  - Notes: <why this is foundational substrate instead of feature work>

## Verified Failures / Blocks
- None, or:
- Item: <short name>
  - Verified Failure Source: <tester failure, implementation failure, approval failure, rejection, or explicit contradiction>
  - Current Status: failed or blocked
  - Files: <files involved>

## Partially Implemented / Needs Expansion
- None, or:
- Item: <short name>
  - Existing Evidence: <current implementation evidence>
  - Missing Approved Behavior: <approved-plan behavior still missing>
  - Files: <files involved>
  - Tester Status: <pending/not-tested/tested>

## Not Started Items
- None, or:
- Item: <short name>
  - Approved Plan Requirement: <requirement>
  - Evidence Status: no intake, no tester result, no source evidence

## Uncertain Items
- None, or:
- Item: <short name>
  - Conflict / Uncertainty: <what evidence conflicts or is insufficient>
  - Evidence Sources Checked: <intake/tester/source/plan>

## Current Implementation Facts
- Files:
- Components / Functions:
- Data Shapes:
- Runtime / UI Facts:
- Persistence / Storage Facts:

"""

        try:
            state_doc = await call_ollama(
                [
                    {"role": "system", "content": "You are a precise project documenter. You summarize current implementation state for future build tasks."},
                    {"role": "user", "content": document_prompt}
                ],
                model=MODEL,
                temperature=0.1
            )

            Path(state_path).parent.mkdir(parents=True, exist_ok=True)
            Path(state_path).write_text(state_doc.strip() + "\n", encoding="utf-8")

            await cl.Message(content=f"""✅ CREATE build state documented.

Project: `{project_slug}`
State file: `{state_rel_path}`

Next:
`/create build-queue`""").send()
            return

        except Exception as e:
            await cl.Message(content=f"Create document-state failed: {str(e)}").send()
            return


    if user_text == "/create continue":
        project_slug = cl.user_session.get("active_create_project")

        if not project_slug:
            await cl.Message(content="No active CREATE project. Start one with `/create start ...`.").send()
            return

        file_path, rel_path = create_project_path(project_slug)

        if not Path(file_path).exists():
            await cl.Message(content=f"Plan file not found: `{rel_path}`").send()
            return

        with open(file_path, "r", encoding="utf-8") as f:
            plan = f.read()

        if "<!-- CREATE_APPROVED: true -->" not in plan:
            await cl.Message(content=f"""CREATE project is not approved for build yet.

Run the planning pipeline first:
`/create propose-fields`
`/create audit-plan`
`/create approve-plan`

Canonical plan lacking approval marker:
`{rel_path}`""").send()
            return

        pending_fields = create_plan_has_pending_required_fields(plan)

        if pending_fields:
            await cl.Message(content=f"""CREATE project is not ready to build.

Reason: required scope fields are still pending.

Pending required fields:
{chr(10).join("- " + field for field in pending_fields)}

Answer these fields in batches using `/create next-questions`, then respond with `/create answer`:
/create answer
<your answers>""").send()
            return

        continue_prompt = f"""
You are continuing a CREATE project using its durable plan file.

Project: {project_slug}
Plan file: {rel_path}

Project plan:
---
{plan[:20000]}
---

Decide the next best step.

Important:
- Do not blindly build if important clarification is still unresolved.
- Treat earlier "Pending clarification" sections as potentially stale if later clarification updates resolved them.
- If clarification is needed, ask no more than 5 targeted questions.
- If research would improve the plan, recommend specific research topics.
- If the project is clear enough, recommend the next build task.
- Keep the output concise and actionable.

Respond in this format:

CREATE Continue Decision

Status: NEEDS_CLARIFICATION | NEEDS_RESEARCH | READY_TO_BUILD

Reason:
...

Targeted Questions:
1. ...

Recommended Research:
- ...

Next Build Task:
...
"""

        try:
            response = await call_ollama(
                [
                    {"role": "system", "content": "You are a CREATE project continuation planner."},
                    {"role": "user", "content": continue_prompt}
                ],
                model=MODEL,
                temperature=0.2
            )

            await cl.Message(content=response).send()
            return

        except Exception as e:
            await cl.Message(content=f"Create continue failed: {str(e)}").send()
            return


    if user_text == "/create read":
        project_slug = cl.user_session.get("active_create_project")

        if not project_slug:
            await cl.Message(content="No active CREATE project.").send()
            return

        file_path, rel_path = create_project_path(project_slug)

        if not Path(file_path).exists():
            await cl.Message(content=f"Plan file not found: `{rel_path}`").send()
            return

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        await cl.Message(content=f"""CREATE plan: `{rel_path}`

---
{content[:5000]}
---""").send()
        return


    if user_text.startswith("/create compile-task "):
        source_task_id = user_text.replace("/create compile-task ", "", 1).strip()
        project_slug = cl.user_session.get("active_create_project")

        if not project_slug:
            await cl.Message(content="No active CREATE project. Use `/create use <project_slug>` first.").send()
            return

        source_task = get_task(source_task_id)

        if not source_task:
            await cl.Message(content=f"No task found with ID `{source_task_id}`.").send()
            return

        plan_path, plan_rel_path = create_project_path(project_slug, "PROJECT_PLAN.md")

        if not Path(plan_path).exists():
            await cl.Message(content=f"Approved plan not found: `{plan_rel_path}`").send()
            return

        with open(plan_path, "r", encoding="utf-8") as f:
            plan = f.read()

        if "<!-- CREATE_APPROVED: true -->" not in plan:
            await cl.Message(content=f"""Cannot compile build task yet.

Reason: `{plan_rel_path}` is not approved.

Run:
`/create propose-fields`
`/create audit-plan`
`/create approve-plan`""").send()
            return

        build_state = load_create_build_state(project_slug)

        source_goal = source_task.get("goal", "").strip()
        source_inputs = source_task.get("inputs") or {}
        source_target = source_inputs.get("target_file") or extract_task_target_file(source_goal)

        if not source_target:
            target_resolution_prompt = f"""
You are resolving the best single target file for a queued CREATE task.

Project: {project_slug}

Source queued task:
---
{source_goal}
---

Approved project plan:
---
{plan[:8000]}
---

Current CREATE build state:
---
{build_state or "No approved build state recorded yet."}
---

Choose the single existing or intended file that should receive this one-file implementation slice.
Prefer existing files when the build state shows related functionality already exists.
Return ONLY the file path. No explanation.
"""

            resolved_target = await call_ollama(
                [
                    {"role": "system", "content": "You resolve one target file path for a runnable implementation slice."},
                    {"role": "user", "content": target_resolution_prompt}
                ],
                model=COMPILER_MODEL,
                temperature=0.0
            )

            candidate = resolved_target.strip().strip("`")
            if candidate and "." in candidate and " " not in candidate:
                source_target = candidate

        preservation_anchors = ""
        if source_target:
            preservation_anchors = extract_preservation_anchors(source_target)

        compile_prompt = f"""
You are compiling a queued CREATE task into a runner-ready BUILD task.

Project: {project_slug}
Approved plan file: {plan_rel_path}

Source queued task ID: {source_task_id}

Source queued task:
---
{source_goal}
---

Source task target file, if known:
---
{source_target or "No target file recorded yet."}
---

Approved project plan:
---
{plan[:16000]}
---

Current CREATE build state:
---
{build_state or "No approved build state recorded yet."}
---

Target-file preservation anchors detected from existing code:
---
{preservation_anchors or "No target file anchors available yet."}
---

These anchors are already implemented progress. The compiled task should carry the relevant anchors forward explicitly so the BUILD runner knows what existing structures to preserve.

BUILD runner capability profile:
The BUILD runner receives this compiled task and can safely perform one file operation.
The runner works best when the task has one clear target file, one implementation slice, concrete acceptance criteria, and enough context to preserve existing work.

Your job:
Turn the source queued task into exactly ONE runner-ready BUILD task prompt.

Scope boundary:

- The source queued task is the ONLY implementation goal.
- Do not compile a different nearby queue item.
- Do not replace the source task with a similar requirement from the approved plan, build state, preservation anchors, backlog, or neighboring task list.
- Approved plan, build state, and preservation anchors are background context only.
- If source task says asset, the compiled task must remain about asset.
- If source task says liability, the compiled task must remain about liability.
- If source task says recurring income, the compiled task must remain about recurring income.
- If source task says recurring expense, the compiled task must remain about recurring expense.

- The source queued task defines the implementation boundary.

- Compile means clarify and operationalize the task, not expand its scope.

- Preserve the original implementation intent while making the task executable by the BUILD runner.

- Use build state and preservation anchors to protect existing implementation progress, not to broaden the task.

- Prefer minimal sufficient changes that accomplish the requested implementation slice cleanly.

A strong compiled task:
- identifies one target file
- describes one complete improvement inside that file
- includes concrete acceptance criteria
- preserves current component style, state shape, data model, imports, and compatible behavior
- builds on the current implementation facts recorded in build state
- explicitly preserves relevant target-file anchors detected from existing code
- can be completed with one file operation
- gives the BUILD runner enough context to succeed without asking it to coordinate several files at once

Connection and integration tasks are valuable when they can be completed cleanly through one target file.
For example, wiring an existing child component into one parent file is a good one-file integration task.
When a broader feature needs companion edits, describe the expected props/callback/interface from the target file's perspective.
The compiled task should remain complete inside the target file while making future connection work easy.

When targeting an existing file:
- preserve existing user-facing inputs and handlers while adding the requested slice
- avoid replacing working behavior with a narrower version of the file
- build on the current structure instead of narrowing the file around only the new feature

Return ONLY the final compiled BUILD task prompt text using this format:

TARGET_FILE:
src/path/File.js

PRESERVE:
- existing anchor or behavior to preserve
- existing anchor or behavior to preserve

TASK:
<runner-ready one-file BUILD task prompt>

The TASK section must include this exact implementation-analysis requirement after the task description:

Before editing, produce IMPLEMENTATION_ANALYSIS documenting:
- task interpretation
- approaches considered
- chosen approach
- rejected approaches
- assumptions
- state/data shape impact
- handler impact
- UI/input behavior impact
- existing behavior impact
- risks/tradeoffs
- follow-up obligations

The PRESERVE section should include the most relevant anchors from the target-file preservation list, especially existing props, state/data keys, handlers, rendered sections, and interactions that should remain in the updated file.
"""

        try:
            compiled_goal = await call_ollama(
                [
                    {"role": "system", "content": "You compile queued implementation slices into runner-ready one-file BUILD tasks."},
                    {"role": "user", "content": compile_prompt}
                ],
                model=COMPILER_MODEL,
                temperature=0.15
            )

            target_file = extract_task_target_file(compiled_goal) or source_target

            if not target_file:
                lines = compiled_goal.splitlines()
                for idx, line in enumerate(lines):
                    if line.strip().upper() == "TARGET_FILE:" and idx + 1 < len(lines):
                        candidate = lines[idx + 1].strip().strip("`")
                        if candidate:
                            target_file = candidate
                            break

            if not target_file:
                for line in compiled_goal.splitlines():
                    stripped = line.strip()
                    lowered = stripped.lower()
                    if lowered.startswith("target file:"):
                        candidate = stripped.split(":", 1)[1].strip().strip("`")
                        if candidate:
                            target_file = candidate
                            break

            compiled_task = create_task(
                compiled_goal.strip(),
                assigned_role="leader",
                requested_by="create_compile_task",
                inputs={
                    "intent": "BUILD",
                    "approved_create_project": project_slug,
                    "approved_plan_file": plan_rel_path,
                    "source_task_id": source_task_id,
                    "read_before_modify": target_file or plan_rel_path,
                    "target_file": target_file,
                    "tool_limits": {
                        "file_reads": "unlimited",
                        "max_file_writes": 1,
                        "writable_files": [target_file] if target_file else [],
                        "allow_multi_file_output": False
                    }
                }
            )

            cl.user_session.set("last_created_task_id", compiled_task["task_id"])

            await cl.Message(content=f"""✅ CREATE task compiled.

Project: `{project_slug}`
Source task: `{source_task_id}`
Compiled task ID: `{compiled_task['task_id']}`
Target file: `{target_file or "Not detected"}`

Compiled task:
{compiled_task['goal']}

Next:
`/task run latest`""").send()
            return

        except Exception as e:
            await cl.Message(content=f"Create compile-task failed: {str(e)}").send()
            return


    if user_text.startswith("/task add "):
        goal = user_text.replace("/task add ", "", 1).strip()

        task_inputs = {}
        last_read_file = cl.user_session.get("last_read_file")
        if last_read_file:
            task_inputs["last_read_file"] = last_read_file
            task_inputs["read_before_modify"] = last_read_file

        task = create_task(goal, inputs=task_inputs)

        await cl.Message(content=f"✅ Task added\nID: `{task['task_id']}`\nGoal: {task['goal']}").send()
        return


    if user_text.startswith("/task continue"):
        try:
            queue = load_tasks()
            tasks = queue.get("tasks", [])

            if not tasks:
                await cl.Message(content="No tasks found.").send()
                return

            last = None
            for t in reversed(tasks):
                if t.get("status") == "done":
                    last = t
                    break

            if not last:
                await cl.Message(content="No completed task found to continue from.").send()
                return

            next_action = (last.get("next_action") or "").strip()

            if not next_action or next_action.lower().startswith("none"):
                await cl.Message(content="No actionable next step to continue.").send()
                return

            # Create new task from next_action
            new_task = create_task(next_action, assigned_role="leader", requested_by="task_continue")

            await cl.Message(content=f"""🔁 Continued task created

ID: `{new_task['task_id']}`
Goal: {new_task['goal']}

Next:
Run: /task run {new_task['task_id']}
Or: /task run next""").send()

            # Optional auto-run
            if user_text.strip() == "/task continue run":
                await cl.Message(content=f"Running task `{new_task['task_id']}`...").send()
                updated = await run_task(new_task)

                await cl.Message(content=f"""✅ Model run complete — staging/validation may still continue

ID: `{updated['task_id']}`
Status: **{updated['status']}**
Result: {updated.get('result')}
Next action: {updated.get('next_action')}""").send()

            return

        except Exception as e:
            await cl.Message(content=f"Task continue failed: {str(e)}").send()
            return

    if user_text in ["/task list", "/task list pending", "/task list recent"]:
        tasks = load_tasks().get("tasks", [])

        if user_text == "/task list pending":
            tasks = [t for t in tasks if t.get("status") == "pending"]
            title = "## Pending Task Queue"
        elif user_text == "/task list recent":
            tasks = tasks[-15:]
            title = "## Recent Task Queue"
        else:
            title = "## Task Queue"

        if not tasks:
            await cl.Message(content="No matching tasks.").send()
            return

        out = [title]
        for t in tasks:
            out.append(f"`{t['task_id']}` — **{t['status']}**\nRole: {t['assigned_role']}\nIntent: {t.get('intent', 'PLAN')}\nRequested by: {t.get('requested_by')}\nGoal: {t['goal']}\nNext: {t.get('next_action')}")

        await cl.Message(content="\n\n".join(out)).send()
        return

    if user_text == "/task archive done":
        data = load_tasks()
        tasks = data.get("tasks", [])

        to_archive = [t for t in tasks if t.get("status") == "done"]
        remaining = [t for t in tasks if t.get("status") != "done"]

        if not to_archive:
            await cl.Message(content="No done tasks to archive.").send()
            return

        archive_data = {"tasks": []}
        if os.path.exists(TASK_ARCHIVE_PATH):
            try:
                with open(TASK_ARCHIVE_PATH, "r", encoding="utf-8") as f:
                    archive_data = json.load(f)
            except Exception:
                archive_data = {"tasks": []}

        archived_at = datetime.now().isoformat(timespec="seconds")
        for t in to_archive:
            t["archived_at"] = archived_at

        archive_data.setdefault("tasks", []).extend(to_archive)

        with open(TASK_ARCHIVE_PATH, "w", encoding="utf-8") as f:
            json.dump(archive_data, f, indent=2)

        save_tasks({"tasks": remaining})

        await cl.Message(content=f"""✅ Archived done tasks.

Archived: {len(to_archive)}
Remaining active tasks: {len(remaining)}
Archive file: `TASK_ARCHIVE.json`

Next:
`/task list pending`
or
`/task list recent`""").send()
        return

    if user_text == "/task run next":
        task, blocked = get_next_runnable_task_with_prereq_check()
        if not task:
            if blocked:
                lines = []
                for b in blocked:
                    lines.append(f"- `{b.get('task_id')}` — {b.get('goal', '')[:160]}")
                await cl.Message(content=f"""No runnable pending tasks.

Blocked by failed prerequisites:
{chr(10).join(lines)}

Next:
- Run `/task list`
- Complete prerequisite tasks first
- Or manually reset a blocked task after fixing prerequisites""").send()
            else:
                await cl.Message(content="No pending tasks to run.").send()
            return

        if blocked:
            lines = []
            for b in blocked:
                lines.append(f"- `{b.get('task_id')}` — {b.get('goal', '')[:160]}")
            await cl.Message(content=f"""Skipped {len(blocked)} blocked prerequisite task(s).

{chr(10).join(lines)}

Running next runnable task `{task['task_id']}`...""").send()
        else:
            await cl.Message(content=f"Running task `{task['task_id']}`...").send()
        updated = await run_task(task)

        # ===== AUTO MEMORY PROPOSAL =====
        try:
            mc = updated.get("memory_candidate") or {}

            if mc.get("should_store"):
                lesson = mc.get("lesson", "").strip()
                why = mc.get("why_it_matters", "").strip()
                confidence = mc.get("confidence", "")

                memory_response = f"""Lesson: {lesson}
Why it matters: {why}
Source: Task execution
Confidence: {confidence}"""

                semantic_dup, matched_memory, dup_score = await memory_semantic_duplicate_exists(memory_response)

                if memory_lesson_already_exists(memory_response):
                    await cl.Message(content="🧠 Memory proposal skipped: lesson already exists in MEMORY.md.").send()
                    # Memory sidecar only: do not override task next_action.
                elif semantic_dup:
                    closest = (matched_memory or "")[:1200]
                    await cl.Message(content=f"""🧠 Memory proposal skipped: semantically similar lesson already exists in MEMORY.md.

Similarity: {dup_score:.2f}

Closest existing memory:
---
{closest}
---""").send()
                    # Memory sidecar only: do not override task next_action.
                else:
                    cl.user_session.set("pending_memory", {
                        "content": memory_response.strip(),
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "review": None,
                        "auto_generated": True,
                        "source": "memory_candidate"
                    })

                    await cl.Message(content=f"""🧠 Auto Memory Proposal

{memory_response}

Next:
- Review: /memory review
- Approve: /memory approve
- Cancel: /memory cancel""").send()

            else:
                result_text = str(updated.get("result", ""))

                if result_text:
                    memory_prompt = f"""
You are extracting a reusable lesson from a task result.

Task result:
---
{result_text}
---

If there is a useful, generalizable lesson, extract it.

Rules:
- Must improve future decisions
- Must not be task-specific
- Must not be trivial
- Must be concise
- Must not duplicate an existing memory in different words
- If the lesson is already covered by MEMORY.md, return: NONE

Return ONLY if valuable:

Lesson: ...
Why it matters: ...
Source: Task execution
Confidence: 0.0-1.0

If no valuable lesson, return: NONE
"""

                    memory_response = await call_ollama(
                        [
                            {"role": "system", "content": "You extract high-value reusable lessons."},
                            {"role": "user", "content": memory_prompt}
                        ],
                        model="qwen2.5-coder:7b",
                        temperature=0.2
                    )

                    if memory_response and "NONE" not in memory_response:
                        semantic_dup, matched_memory, dup_score = await memory_semantic_duplicate_exists(memory_response)

                        if memory_lesson_already_exists(memory_response):
                            await cl.Message(content="🧠 Memory proposal skipped: lesson already exists in MEMORY.md.").send()
                            # Memory sidecar only: do not override task next_action.
                        elif semantic_dup:
                            closest = (matched_memory or "")[:1200]
                            await cl.Message(content=f"""🧠 Memory proposal skipped: semantically similar lesson already exists in MEMORY.md.

Similarity: {dup_score:.2f}

Closest existing memory:
---
{closest}
---""").send()
                            # Memory sidecar only: do not override task next_action.
                        else:
                            cl.user_session.set("pending_memory", {
                                "content": memory_response.strip(),
                                "created_at": datetime.now().isoformat(timespec="seconds"),
                                "review": None,
                                "auto_generated": True,
                                "source": "fallback_extractor"
                            })

                            await cl.Message(content=f"""🧠 Auto Memory Proposal

{memory_response}

Next:
- Review: /memory review
- Approve: /memory approve
- Cancel: /memory cancel""").send()
                    else:
                        await cl.Message(content="🧠 No reusable memory proposed from this task.").send()

        except Exception as e:
            print(f"Auto memory proposal failed: {e}")


        await cl.Message(content=f"""✅ Model run complete — staging/validation may still continue

ID: `{updated['task_id']}`
Status: **{updated['status']}**
Result: {updated.get('result')}
Next action: {updated.get('next_action')}""").send()

        fw = updated.get("file_operation") or updated.get("file_write") or {}
        if fw.get("should_stage") and fw.get("filename") and fw.get("content"):
            operation = fw.get("operation") or fw.get("mode") or "append"
            operation = operation.lower().strip()

            task_inputs_for_operation = updated.get("inputs") or task.get("inputs") or {}
            required_operation = (task_inputs_for_operation.get("required_operation") or "").lower().strip()
            if required_operation not in ["create", "edit", "replace"] and fw.get("filename"):
                required_operation = recommended_file_operation(fw.get("filename", "")).get("operation", "").lower().strip()
            if required_operation in ["create", "edit", "replace"]:
                operation = required_operation

            if operation not in ["create", "append", "edit", "replace"]:
                operation = "append"

            filename = normalize_knowledge_filename(fw["filename"])
            content = fw.get("content", "")
            reason = fw.get("reason", "")

            if operation == "edit":
                raw_text_for_edit = raw if "raw" in locals() else str(updated.get("result", ""))

                multi_search_replace_blocks = extract_multi_search_replace_blocks(raw_text_for_edit)
                multi_edit_blocks = extract_multi_edit_blocks(raw_text_for_edit)

                if multi_search_replace_blocks:
                    line_edit_blocks = None
                    edit_blocks = {
                        "multi": True,
                        "blocks": multi_search_replace_blocks
                    }

                elif multi_edit_blocks:
                    line_edit_blocks = multi_edit_blocks
                    edit_blocks = None

                elif fw.get("edit_range_start") and fw.get("edit_range_end") and fw.get("replace"):
                    line_edit_blocks = [{
                        "start": int(fw.get("edit_range_start")),
                        "end": int(fw.get("edit_range_end")),
                        "replace": fw.get("replace", "")
                    }]
                    edit_blocks = None

                elif fw.get("search") and fw.get("replace"):
                    line_edit_blocks = None
                    edit_blocks = {
                        "search": fw.get("search", ""),
                        "replace": fw.get("replace", "")
                    }

                else:
                    single_block = extract_line_range_edit_blocks(raw_text_for_edit)
                    line_edit_blocks = [single_block] if single_block else None
                    edit_blocks = extract_edit_blocks(raw_text_for_edit) if not line_edit_blocks else None

                existing_file_path_for_edit = safe_knowledge_path(filename)
                if not Path(existing_file_path_for_edit).exists():
                    await cl.Message(content=f"""The file operation was not staged yet.

EDIT requires an existing file.

Target file: `{filename}`""").send()
                    return

                original_content_for_edit = Path(existing_file_path_for_edit).read_text(encoding="utf-8")

                if line_edit_blocks:
                    expected_after_for_fim = updated.get("expected_after") or task.get("expected_after") or ""
                    original_lines_for_slice = original_content_for_edit.splitlines()
                    repaired_blocks = []

                    for block in line_edit_blocks:
                        fim_replace = await generate_fim_style_replacement(
                            filename=filename,
                            task_goal=task.get("goal", ""),
                            original_content=original_content_for_edit,
                            start=block.get("start"),
                            end=block.get("end"),
                            proposed_replace=block.get("replace", ""),
                            expected_after=expected_after_for_fim
                        )

                        old_slice_for_validation = "\n".join(
                            original_lines_for_slice[block.get("start") - 1:block.get("end")]
                        )
                        slice_surface_diff = compare_jsx_slice_surface(old_slice_for_validation, fim_replace)

                        if not slice_surface_diff.get("ok"):
                            repaired_replace = await repair_fim_replacement_with_slice_diff(
                                filename=filename,
                                task_goal=task.get("goal", ""),
                                old_slice=old_slice_for_validation,
                                failed_replace=fim_replace,
                                diff_report=slice_surface_diff.get("report", ""),
                                expected_after=expected_after_for_fim
                            )

                            repaired_diff = compare_jsx_slice_surface(old_slice_for_validation, repaired_replace)

                            if repaired_diff.get("ok"):
                                fim_replace = repaired_replace
                            else:
                                await cl.Message(content=f"""The file operation was not staged yet.

Slice-level JSX preservation check failed after repair attempt.

File: `{filename}`
Operation: `edit`
Range: {block.get("start")}–{block.get("end")}

{repaired_diff.get("report")}

The replacement slice should carry forward existing visible behavior inside the selected range while adding the requested behavior.""").send()
                                return

                        repaired_blocks.append({
                            "start": block.get("start"),
                            "end": block.get("end"),
                            "replace": fim_replace
                        })

                    patch_result = apply_multi_edit_blocks(
                        original_content_for_edit,
                        repaired_blocks
                    )

                    line_edit_blocks = repaired_blocks
                elif edit_blocks:
                    if edit_blocks.get("multi"):
                        patch_result = apply_multi_search_replace_blocks(
                            original_content_for_edit,
                            edit_blocks.get("blocks", [])
                        )
                    else:
                        patch_result = apply_search_replace_to_content(
                            original_content_for_edit,
                            edit_blocks.get("search", ""),
                            edit_blocks.get("replace", "")
                        )
                else:
                    await cl.Message(content=f"""The file operation was not staged yet.

EDIT requires either SEARCH/REPLACE blocks or fallback EDIT_RANGE_START, EDIT_RANGE_END, and REPLACE.

File: `{filename}`

The BUILD response must provide an edit patch. Full-file CONTENT is not accepted for edit operations.""").send()
                    return

                if not patch_result.get("success"):
                    await cl.Message(content=f"""The file operation was not staged yet.

Surgical edit application failed.

File: `{filename}`

{patch_result.get("error")}

The BUILD response must provide a valid line range from the numbered target file.""").send()
                    return

                content = patch_result.get("content", "")
                fw["content"] = content

                # Debug artifact: persist the full patched edit candidate before validation/staging.
                try:
                    candidate_path = existing_file_path_for_edit + ".leo_candidate"
                    Path(candidate_path).write_text(content, encoding="utf-8")
                    fw["candidate_path"] = candidate_path
                except Exception:
                    pass
                if line_edit_blocks:
                    fw["edit_blocks"] = line_edit_blocks
                    if len(line_edit_blocks) == 1:
                        fw["edit_range_start"] = line_edit_blocks[0].get("start")
                        fw["edit_range_end"] = line_edit_blocks[0].get("end")
                        fw["replace"] = line_edit_blocks[0].get("replace", "")
                elif edit_blocks:
                    fw["search"] = edit_blocks.get("search", "")
                    fw["replace"] = edit_blocks.get("replace", "")

            existing_file_path = safe_knowledge_path(filename)
            file_exists = Path(existing_file_path).exists()

            # ===== FILE EXISTENCE ENFORCEMENT =====
            if not file_exists:
                # Force CREATE for non-existent files
                operation = "create"

            elif file_exists:
                # Prevent CREATE on existing files
                if operation == "create":
                    operation = "replace"

            # ===== READ BEFORE MODIFY (only for existing files) =====
            if file_exists and operation in ["append", "edit", "replace"]:
                task_inputs = updated.get("inputs") or {}
                last_read_file = cl.user_session.get("last_read_file")

                has_read_context = (
                    last_read_file == filename
                    or task_inputs.get("read_before_modify") == filename
                    or task_inputs.get("source_file_read") == filename
                    or "file read" in str(task_inputs).lower()
                )

                if not has_read_context:
                    await cl.Message(content=f"""I cannot safely modify `{filename}` yet.

Reason: this file already exists, and Leo must inspect its current structure before staging an operation.

Next step:
`/file read {filename}`

Then re-run the task. Leo will remember the read file and continue without blocking.""").send()
                    return

            maturity_note = ""
            maturity = replace_risk_from_maturity(filename, operation)

            if maturity and maturity.get("decision") == "block":
                reasons = "\n".join(f"- {r}" for r in maturity.get("reasons", [])[:8])
                await cl.Message(content=f"""The file operation was not staged yet.

Replace risk is too high for this file.

{maturity.get("summary")}
Classification: {maturity.get("classification")}

Reasons:
{reasons}

Target file: `{filename}`
Operation: `{operation}`

Suggested next step:
Use an edit-oriented task for this file, or explicitly choose to replace it after reviewing the current file.""").send()
                return

            if maturity and operation == "replace":
                reasons = "\n".join(f"- {r}" for r in maturity.get("reasons", [])[:5])
                maturity_note = f"""

File maturity:
{maturity.get("summary")}
Classification: {maturity.get("classification")}
Decision: {maturity.get("decision")}

Why:
{reasons}
"""

            if operation == "edit":
                risk = "LOW-MEDIUM — surgical edit staged after exact SEARCH match, temp patch, syntax validation, and baseline checks."
            elif operation == "replace":
                risk = maturity.get("risk_label") if maturity else "HIGH — this will overwrite the entire file."
            elif operation == "create":
                risk = "LOW — this creates a new file only if it does not already exist."
            else:
                risk = "LOW — this appends content without removing existing content."

            static_behavior_result = validate_react_static_behavior_contract(content)
            if not static_behavior_result.get("success"):
                expected_after_for_static_repair = updated.get("expected_after") or task.get("expected_after") or ""
                repaired_content = await repair_full_candidate_static_behavior(
                    filename=filename,
                    task_goal=task.get("goal", ""),
                    candidate_content=content,
                    static_report=static_behavior_result.get("report", ""),
                    expected_after=expected_after_for_static_repair
                )

                repaired_static_result = validate_react_static_behavior_contract(repaired_content)

                if not repaired_static_result.get("success"):
                    await cl.Message(content=f"""The file operation was not staged yet.

Static behavior contract failed after repair attempt.

File: `{filename}`
Operation: `{operation}`

{repaired_static_result.get("report")}

The candidate should define referenced handlers and keep mapped state values array-compatible.""").send()
                    return

                content = repaired_content
                fw["content"] = content

                try:
                    if operation == "edit":
                        candidate_path = existing_file_path_for_edit + ".leo_candidate"
                        Path(candidate_path).write_text(content, encoding="utf-8")
                        fw["candidate_path"] = candidate_path
                except Exception:
                    pass

            syntax_result = validate_proposed_code_syntax(filename, content)
            if not syntax_result.get("success"):
                expected_after_for_syntax_repair = updated.get("expected_after") or task.get("expected_after") or ""
                repaired_content = await repair_full_candidate_syntax(
                    filename=filename,
                    task_goal=task.get("goal", ""),
                    candidate_content=content,
                    syntax_result=syntax_result,
                    expected_after=expected_after_for_syntax_repair
                )

                repaired_syntax_result = validate_proposed_code_syntax(filename, repaired_content)

                if not repaired_syntax_result.get("success"):
                    await cl.Message(content=f"""The file operation was not staged yet.

Syntax validation failed after repair attempt.

File: `{filename}`
Command: `{repaired_syntax_result.get("command")}`
Exit code: `{repaired_syntax_result.get("exit_code")}`

STDOUT:
{repaired_syntax_result.get("stdout") or "[empty]"}

STDERR:
{repaired_syntax_result.get("stderr") or "[empty]"}

The candidate needs another repair pass before it can be safely staged.""").send()
                    return

                content = repaired_content
                fw["content"] = content

                try:
                    candidate_path = safe_knowledge_path(filename) + ".leo_candidate"
                    Path(candidate_path).write_text(content, encoding="utf-8")
                    fw["candidate_path"] = candidate_path
                except Exception:
                    pass

            ok, violation = validate_task_tool_limits(task, filename, content, operation)
            if not ok:
                await cl.Message(content=f"""The file operation was not staged yet.

{violation}

Target file: `{filename}`
Operation: `{operation}`

This usually means the BUILD response needs to be narrowed to the requested file before it can be safely approved.""").send()
                return

            expected_after = updated.get("expected_after") or task.get("expected_after") or ""
            expected_after_note = "\n\nExpected after contract:\n"
            expected_after_note += expected_after[:3000] if expected_after else "No EXPECTED_AFTER recorded."

            baseline_before = (
                (task.get("inputs") or {}).get("baseline_before")
                or (updated.get("inputs") or {}).get("baseline_before")
                or ""
            )

            baseline_diff = {}
            if baseline_before and content:
                baseline_after = generate_target_file_baseline(filename, content)
                baseline_diff = compare_target_file_baselines(baseline_before, baseline_after)
            else:
                baseline_diff = (updated.get("baseline_diff") or task.get("baseline_diff") or {})

            baseline_report = ""
            if isinstance(baseline_diff, dict):
                baseline_report = baseline_diff.get("report") or str(baseline_diff)
            elif baseline_diff:
                baseline_report = str(baseline_diff)

            baseline_note = "\n\nBaseline comparison:\n"
            if baseline_report:
                baseline_note += baseline_report[:4000]
            else:
                baseline_note += "No baseline comparison recorded."

            if isinstance(baseline_diff, dict) and baseline_diff and baseline_diff.get("ok") is False:
                task_inputs_for_mode = (task.get("inputs") or {})
                edit_mode_for_baseline = (
                    task_inputs_for_mode.get("edit_mode")
                    or (updated.get("inputs") or {}).get("edit_mode")
                    or "surgical"
                )

                raw_text_for_adaptations = raw if "raw" in locals() else str(updated.get("result", ""))
                intentional_adaptations = extract_intentional_adaptations(raw_text_for_adaptations)

                unexplained_baseline_missing = baseline_missing_items_unexplained_by_mode(
                    baseline_diff=baseline_diff,
                    reason_text=reason,
                    expected_after_text=expected_after,
                    edit_mode=edit_mode_for_baseline,
                    task_goal=task.get("goal", ""),
                    result_text=str(updated.get("result", "")),
                    intentional_adaptations_text=intentional_adaptations
                )

                if not unexplained_baseline_missing:
                    baseline_diff["ok"] = True
                    baseline_report = (
                        baseline_diff.get("report", "")
                        + f"\n\nMode-aware baseline accepted under EDIT_MODE={edit_mode_for_baseline}."
                    )
                else:
                    baseline_report = format_unexplained_baseline_report(
                        baseline_report,
                        unexplained_baseline_missing
                    )

                    original_content_for_baseline_repair = ""
                    try:
                        original_content_for_baseline_repair = Path(safe_knowledge_path(filename)).read_text(encoding="utf-8")
                    except Exception:
                        original_content_for_baseline_repair = ""

                    expected_after_for_baseline_repair = expected_after or ""
                    repaired_content = await repair_full_candidate_baseline_preservation(
                        filename=filename,
                        task_goal=task.get("goal", ""),
                        original_content=original_content_for_baseline_repair,
                        candidate_content=content,
                        baseline_report=baseline_report[:6000] if baseline_report else "No baseline report available.",
                        expected_after=expected_after_for_baseline_repair
                    )

                    repaired_baseline_after = generate_target_file_baseline(filename, repaired_content)
                    repaired_baseline_diff = compare_target_file_baselines(
                        task.get("inputs", {}).get("baseline_before", ""),
                        repaired_baseline_after
                    )

                    if repaired_baseline_diff.get("ok") is False:
                        await cl.Message(content=f"""The file operation was not staged yet.

Baseline preservation check failed after repair attempt.

File: `{filename}`
Operation: `{operation}`

{repaired_baseline_diff.get("report", "No baseline report available.")[:4000]}

The candidate should preserve baseline facts unless the task intentionally changes them.""").send()
                        return

                    content = repaired_content
                    fw["content"] = content

                    try:
                        candidate_path = safe_knowledge_path(filename) + ".leo_candidate"
                        Path(candidate_path).write_text(content, encoding="utf-8")
                        fw["candidate_path"] = candidate_path
                    except Exception:
                        pass

                    baseline_after = repaired_baseline_after
                    baseline_diff = repaired_baseline_diff
                    baseline_report = repaired_baseline_diff.get("report", "")

            stage_file_operation(filename, content, operation=operation, reason=reason)

            pending = get_pending_write()
            pending = enrich_pending_write_from_task(pending, task)
            if pending:
                cl.user_session.set("pending_write", pending)

            await cl.Message(content=f"""Proposed file operation staged from task.

Operation: {operation.upper()}
Risk: {risk}
File: {filename}
Reason: {reason}{maturity_note}{expected_after_note}{baseline_note}

Preview:
```jsx
{content[:5000]}
```

Approval command required:
{"/approve replace" if operation == "replace" else "/approve edit" if operation == "edit" else "/approve write"}

To cancel, run:
/cancel write""").send()

        return

    if user_text.startswith("/task run "):
        task_id = user_text.replace("/task run ", "", 1).strip()

        if task_id == "latest":
            latest_task_id = cl.user_session.get("last_created_task_id")

            if not latest_task_id:
                await cl.Message(content="No recently created task found in this session.").send()
                return

            task_id = latest_task_id

        task = get_task(task_id)

        if not task:
            await cl.Message(content=f"No task found with ID `{task_id}`.").send()
            return

        prereq_result = check_task_prerequisites(task)
        if not prereq_result.get("ok"):
            blocked_task = block_task_for_failed_prerequisites(task, prereq_result)
            failures = prereq_result.get("failures", [])
            lines = []
            for failure in failures:
                missing = failure.get("missing") or []
                lines.append(f"- `{failure.get('file')}` missing: {', '.join(str(x) for x in missing)}")
            await cl.Message(content=f"""❌ Task blocked by failed prerequisites.

Task: `{task['task_id']}`

{chr(10).join(lines)}

Status set to `blocked_prerequisite`.
Run prerequisite work first, then reset this task to pending when ready.""").send()
            return

        await cl.Message(content=f"Running task `{task['task_id']}`...").send()
        updated = await run_task(task)

        # ===== AUTO MEMORY PROPOSAL =====
        try:
            mc = updated.get("memory_candidate") or {}

            if mc.get("should_store"):
                lesson = mc.get("lesson", "").strip()
                why = mc.get("why_it_matters", "").strip()
                confidence = mc.get("confidence", "")

                memory_response = f"""Lesson: {lesson}
Why it matters: {why}
Source: Task execution
Confidence: {confidence}"""

                semantic_dup, matched_memory, dup_score = await memory_semantic_duplicate_exists(memory_response)

                if memory_lesson_already_exists(memory_response):
                    await cl.Message(content="🧠 Memory proposal skipped: lesson already exists in MEMORY.md.").send()
                    # Memory sidecar only: do not override task next_action.
                elif semantic_dup:
                    closest = (matched_memory or "")[:1200]
                    await cl.Message(content=f"""🧠 Memory proposal skipped: semantically similar lesson already exists in MEMORY.md.

Similarity: {dup_score:.2f}

Closest existing memory:
---
{closest}
---""").send()
                    # Memory sidecar only: do not override task next_action.
                else:
                    cl.user_session.set("pending_memory", {
                        "content": memory_response.strip(),
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                        "review": None,
                        "auto_generated": True,
                        "source": "memory_candidate"
                    })

                    await cl.Message(content=f"""🧠 Auto Memory Proposal

{memory_response}

Next:
- Review: /memory review
- Approve: /memory approve
- Cancel: /memory cancel""").send()

            else:
                result_text = str(updated.get("result", ""))

                if result_text:
                    memory_prompt = f"""
You are extracting a reusable lesson from a task result.

Task result:
---
{result_text}
---

If there is a useful, generalizable lesson, extract it.

Rules:
- Must improve future decisions
- Must not be task-specific
- Must not be trivial
- Must be concise
- Must not duplicate an existing memory in different words
- If the lesson is already covered by MEMORY.md, return: NONE

Return ONLY if valuable:

Lesson: ...
Why it matters: ...
Source: Task execution
Confidence: 0.0-1.0

If no valuable lesson, return: NONE
"""

                    memory_response = await call_ollama(
                        [
                            {"role": "system", "content": "You extract high-value reusable lessons."},
                            {"role": "user", "content": memory_prompt}
                        ],
                        model="qwen2.5-coder:7b",
                        temperature=0.2
                    )

                    if memory_response and "NONE" not in memory_response:
                        semantic_dup, matched_memory, dup_score = await memory_semantic_duplicate_exists(memory_response)

                        if memory_lesson_already_exists(memory_response):
                            await cl.Message(content="🧠 Memory proposal skipped: lesson already exists in MEMORY.md.").send()
                            # Memory sidecar only: do not override task next_action.
                        elif semantic_dup:
                            closest = (matched_memory or "")[:1200]
                            await cl.Message(content=f"""🧠 Memory proposal skipped: semantically similar lesson already exists in MEMORY.md.

Similarity: {dup_score:.2f}

Closest existing memory:
---
{closest}
---""").send()
                            # Memory sidecar only: do not override task next_action.
                        else:
                            cl.user_session.set("pending_memory", {
                                "content": memory_response.strip(),
                                "created_at": datetime.now().isoformat(timespec="seconds"),
                                "review": None,
                                "auto_generated": True,
                                "source": "fallback_extractor"
                            })

                            await cl.Message(content=f"""🧠 Auto Memory Proposal

{memory_response}

Next:
- Review: /memory review
- Approve: /memory approve
- Cancel: /memory cancel""").send()
                    else:
                        await cl.Message(content="🧠 No reusable memory proposed from this task.").send()

        except Exception as e:
            print(f"Auto memory proposal failed: {e}")


        await cl.Message(content=f"""✅ Model run complete — staging/validation may still continue

ID: `{updated['task_id']}`
Status: **{updated['status']}**
Result: {updated.get('result')}
Next action: {updated.get('next_action')}""").send()

        fw = updated.get("file_operation") or updated.get("file_write") or {}
        if fw.get("should_stage") and fw.get("filename") and fw.get("content"):
            operation = fw.get("operation") or fw.get("mode") or "append"
            operation = operation.lower().strip()

            task_inputs_for_operation = updated.get("inputs") or task.get("inputs") or {}
            required_operation = (task_inputs_for_operation.get("required_operation") or "").lower().strip()
            if required_operation not in ["create", "edit", "replace"] and fw.get("filename"):
                required_operation = recommended_file_operation(fw.get("filename", "")).get("operation", "").lower().strip()
            if required_operation in ["create", "edit", "replace"]:
                operation = required_operation

            if operation not in ["create", "append", "edit", "replace"]:
                operation = "append"

            filename = normalize_knowledge_filename(fw["filename"])
            content = fw.get("content", "")
            reason = fw.get("reason", "")

            if operation == "edit":
                raw_text_for_edit = raw if "raw" in locals() else str(updated.get("result", ""))

                multi_search_replace_blocks = extract_multi_search_replace_blocks(raw_text_for_edit)
                multi_edit_blocks = extract_multi_edit_blocks(raw_text_for_edit)

                if multi_search_replace_blocks:
                    line_edit_blocks = None
                    edit_blocks = {
                        "multi": True,
                        "blocks": multi_search_replace_blocks
                    }

                elif multi_edit_blocks:
                    line_edit_blocks = multi_edit_blocks
                    edit_blocks = None

                elif fw.get("edit_range_start") and fw.get("edit_range_end") and fw.get("replace"):
                    line_edit_blocks = [{
                        "start": int(fw.get("edit_range_start")),
                        "end": int(fw.get("edit_range_end")),
                        "replace": fw.get("replace", "")
                    }]
                    edit_blocks = None

                elif fw.get("search") and fw.get("replace"):
                    line_edit_blocks = None
                    edit_blocks = {
                        "search": fw.get("search", ""),
                        "replace": fw.get("replace", "")
                    }

                else:
                    single_block = extract_line_range_edit_blocks(raw_text_for_edit)
                    line_edit_blocks = [single_block] if single_block else None
                    edit_blocks = extract_edit_blocks(raw_text_for_edit) if not line_edit_blocks else None

                existing_file_path_for_edit = safe_knowledge_path(filename)
                if not Path(existing_file_path_for_edit).exists():
                    await cl.Message(content=f"""The file operation was not staged yet.

EDIT requires an existing file.

Target file: `{filename}`""").send()
                    return

                original_content_for_edit = Path(existing_file_path_for_edit).read_text(encoding="utf-8")

                if line_edit_blocks:
                    expected_after_for_fim = updated.get("expected_after") or task.get("expected_after") or ""
                    original_lines_for_slice = original_content_for_edit.splitlines()
                    repaired_blocks = []

                    for block in line_edit_blocks:
                        fim_replace = await generate_fim_style_replacement(
                            filename=filename,
                            task_goal=task.get("goal", ""),
                            original_content=original_content_for_edit,
                            start=block.get("start"),
                            end=block.get("end"),
                            proposed_replace=block.get("replace", ""),
                            expected_after=expected_after_for_fim
                        )

                        old_slice_for_validation = "\n".join(
                            original_lines_for_slice[block.get("start") - 1:block.get("end")]
                        )
                        slice_surface_diff = compare_jsx_slice_surface(old_slice_for_validation, fim_replace)

                        if not slice_surface_diff.get("ok"):
                            repaired_replace = await repair_fim_replacement_with_slice_diff(
                                filename=filename,
                                task_goal=task.get("goal", ""),
                                old_slice=old_slice_for_validation,
                                failed_replace=fim_replace,
                                diff_report=slice_surface_diff.get("report", ""),
                                expected_after=expected_after_for_fim
                            )

                            repaired_diff = compare_jsx_slice_surface(old_slice_for_validation, repaired_replace)

                            if repaired_diff.get("ok"):
                                fim_replace = repaired_replace
                            else:
                                await cl.Message(content=f"""The file operation was not staged yet.

Slice-level JSX preservation check failed after repair attempt.

File: `{filename}`
Operation: `edit`
Range: {block.get("start")}–{block.get("end")}

{repaired_diff.get("report")}

The replacement slice should carry forward existing visible behavior inside the selected range while adding the requested behavior.""").send()
                                return

                        repaired_blocks.append({
                            "start": block.get("start"),
                            "end": block.get("end"),
                            "replace": fim_replace
                        })

                    patch_result = apply_multi_edit_blocks(
                        original_content_for_edit,
                        repaired_blocks
                    )

                    line_edit_blocks = repaired_blocks
                elif edit_blocks:
                    if edit_blocks.get("multi"):
                        patch_result = apply_multi_search_replace_blocks(
                            original_content_for_edit,
                            edit_blocks.get("blocks", [])
                        )
                    else:
                        patch_result = apply_search_replace_to_content(
                            original_content_for_edit,
                            edit_blocks.get("search", ""),
                            edit_blocks.get("replace", "")
                        )
                else:
                    await cl.Message(content=f"""The file operation was not staged yet.

EDIT requires either SEARCH/REPLACE blocks or fallback EDIT_RANGE_START, EDIT_RANGE_END, and REPLACE.

File: `{filename}`

The BUILD response must provide an edit patch. Full-file CONTENT is not accepted for edit operations.""").send()
                    return

                if not patch_result.get("success"):
                    await cl.Message(content=f"""The file operation was not staged yet.

Surgical edit application failed.

File: `{filename}`

{patch_result.get("error")}

The BUILD response must provide a valid line range from the numbered target file.""").send()
                    return

                content = patch_result.get("content", "")
                fw["content"] = content

                # Debug artifact: persist the full patched edit candidate before validation/staging.
                try:
                    candidate_path = existing_file_path_for_edit + ".leo_candidate"
                    Path(candidate_path).write_text(content, encoding="utf-8")
                    fw["candidate_path"] = candidate_path
                except Exception:
                    pass
                if line_edit_blocks:
                    fw["edit_blocks"] = line_edit_blocks
                    if len(line_edit_blocks) == 1:
                        fw["edit_range_start"] = line_edit_blocks[0].get("start")
                        fw["edit_range_end"] = line_edit_blocks[0].get("end")
                        fw["replace"] = line_edit_blocks[0].get("replace", "")
                elif edit_blocks:
                    fw["search"] = edit_blocks.get("search", "")
                    fw["replace"] = edit_blocks.get("replace", "")

            existing_file_path = safe_knowledge_path(filename)
            file_exists = Path(existing_file_path).exists()

            # ===== FILE EXISTENCE ENFORCEMENT =====
            if not file_exists:
                # Force CREATE for non-existent files
                operation = "create"

            elif file_exists:
                # Prevent CREATE on existing files
                if operation == "create":
                    operation = "replace"

            # ===== READ BEFORE MODIFY (only for existing files) =====
            if file_exists and operation in ["append", "edit", "replace"]:
                task_inputs = updated.get("inputs") or {}
                last_read_file = cl.user_session.get("last_read_file")

                has_read_context = (
                    last_read_file == filename
                    or task_inputs.get("read_before_modify") == filename
                    or task_inputs.get("source_file_read") == filename
                    or "file read" in str(task_inputs).lower()
                )

                if not has_read_context:
                    await cl.Message(content=f"""I cannot safely modify `{filename}` yet.

Reason: this file already exists, and Leo must inspect its current structure before staging an operation.

Next step:
`/file read {filename}`

Then re-run the task. Leo will remember the read file and continue without blocking.""").send()
                    return

            maturity_note = ""
            maturity = replace_risk_from_maturity(filename, operation)

            if maturity and maturity.get("decision") == "block":
                reasons = "\n".join(f"- {r}" for r in maturity.get("reasons", [])[:8])
                await cl.Message(content=f"""The file operation was not staged yet.

Replace risk is too high for this file.

{maturity.get("summary")}
Classification: {maturity.get("classification")}

Reasons:
{reasons}

Target file: `{filename}`
Operation: `{operation}`

Suggested next step:
Use an edit-oriented task for this file, or explicitly choose to replace it after reviewing the current file.""").send()
                return

            if maturity and operation == "replace":
                reasons = "\n".join(f"- {r}" for r in maturity.get("reasons", [])[:5])
                maturity_note = f"""

File maturity:
{maturity.get("summary")}
Classification: {maturity.get("classification")}
Decision: {maturity.get("decision")}

Why:
{reasons}
"""

            if operation == "edit":
                risk = "LOW-MEDIUM — surgical edit staged after exact SEARCH match, temp patch, syntax validation, and baseline checks."
            elif operation == "replace":
                risk = maturity.get("risk_label") if maturity else "HIGH — this will overwrite the entire file."
            elif operation == "create":
                risk = "LOW — this creates a new file only if it does not already exist."
            else:
                risk = "LOW — this appends content without removing existing content."

            static_behavior_result = validate_react_static_behavior_contract(content)
            if not static_behavior_result.get("success"):
                expected_after_for_static_repair = updated.get("expected_after") or task.get("expected_after") or ""
                repaired_content = await repair_full_candidate_static_behavior(
                    filename=filename,
                    task_goal=task.get("goal", ""),
                    candidate_content=content,
                    static_report=static_behavior_result.get("report", ""),
                    expected_after=expected_after_for_static_repair
                )

                repaired_static_result = validate_react_static_behavior_contract(repaired_content)

                if not repaired_static_result.get("success"):
                    await cl.Message(content=f"""The file operation was not staged yet.

Static behavior contract failed after repair attempt.

File: `{filename}`
Operation: `{operation}`

{repaired_static_result.get("report")}

The candidate should define referenced handlers and keep mapped state values array-compatible.""").send()
                    return

                content = repaired_content
                fw["content"] = content

                try:
                    if operation == "edit":
                        candidate_path = existing_file_path_for_edit + ".leo_candidate"
                        Path(candidate_path).write_text(content, encoding="utf-8")
                        fw["candidate_path"] = candidate_path
                except Exception:
                    pass

            syntax_result = validate_proposed_code_syntax(filename, content)
            if not syntax_result.get("success"):
                expected_after_for_syntax_repair = updated.get("expected_after") or task.get("expected_after") or ""
                repaired_content = await repair_full_candidate_syntax(
                    filename=filename,
                    task_goal=task.get("goal", ""),
                    candidate_content=content,
                    syntax_result=syntax_result,
                    expected_after=expected_after_for_syntax_repair
                )

                repaired_syntax_result = validate_proposed_code_syntax(filename, repaired_content)

                if not repaired_syntax_result.get("success"):
                    await cl.Message(content=f"""The file operation was not staged yet.

Syntax validation failed after repair attempt.

File: `{filename}`
Command: `{repaired_syntax_result.get("command")}`
Exit code: `{repaired_syntax_result.get("exit_code")}`

STDOUT:
{repaired_syntax_result.get("stdout") or "[empty]"}

STDERR:
{repaired_syntax_result.get("stderr") or "[empty]"}

The candidate needs another repair pass before it can be safely staged.""").send()
                    return

                content = repaired_content
                fw["content"] = content

                try:
                    candidate_path = safe_knowledge_path(filename) + ".leo_candidate"
                    Path(candidate_path).write_text(content, encoding="utf-8")
                    fw["candidate_path"] = candidate_path
                except Exception:
                    pass

            ok, violation = validate_task_tool_limits(task, filename, content, operation)
            if not ok:
                await cl.Message(content=f"""The file operation was not staged yet.

{violation}

Target file: `{filename}`
Operation: `{operation}`

This usually means the BUILD response needs to be narrowed to the requested file before it can be safely approved.""").send()
                return

            expected_after = updated.get("expected_after") or task.get("expected_after") or ""
            expected_after_note = "\n\nExpected after contract:\n"
            expected_after_note += expected_after[:3000] if expected_after else "No EXPECTED_AFTER recorded."

            baseline_before = (
                (task.get("inputs") or {}).get("baseline_before")
                or (updated.get("inputs") or {}).get("baseline_before")
                or ""
            )

            baseline_diff = {}
            if baseline_before and content:
                baseline_after = generate_target_file_baseline(filename, content)
                baseline_diff = compare_target_file_baselines(baseline_before, baseline_after)
            else:
                baseline_diff = (updated.get("baseline_diff") or task.get("baseline_diff") or {})

            baseline_report = ""
            if isinstance(baseline_diff, dict):
                baseline_report = baseline_diff.get("report") or str(baseline_diff)
            elif baseline_diff:
                baseline_report = str(baseline_diff)

            baseline_note = "\n\nBaseline comparison:\n"
            if baseline_report:
                baseline_note += baseline_report[:4000]
            else:
                baseline_note += "No baseline comparison recorded."

            if isinstance(baseline_diff, dict) and baseline_diff and baseline_diff.get("ok") is False:
                task_inputs_for_mode = (task.get("inputs") or {})
                edit_mode_for_baseline = (
                    task_inputs_for_mode.get("edit_mode")
                    or (updated.get("inputs") or {}).get("edit_mode")
                    or "surgical"
                )

                raw_text_for_adaptations = raw if "raw" in locals() else str(updated.get("result", ""))
                intentional_adaptations = extract_intentional_adaptations(raw_text_for_adaptations)

                unexplained_baseline_missing = baseline_missing_items_unexplained_by_mode(
                    baseline_diff=baseline_diff,
                    reason_text=reason,
                    expected_after_text=expected_after,
                    edit_mode=edit_mode_for_baseline,
                    task_goal=task.get("goal", ""),
                    result_text=str(updated.get("result", "")),
                    intentional_adaptations_text=intentional_adaptations
                )

                if not unexplained_baseline_missing:
                    baseline_diff["ok"] = True
                    baseline_report = (
                        baseline_diff.get("report", "")
                        + f"\n\nMode-aware baseline accepted under EDIT_MODE={edit_mode_for_baseline}."
                    )
                else:
                    baseline_report = format_unexplained_baseline_report(
                        baseline_report,
                        unexplained_baseline_missing
                    )

                    original_content_for_baseline_repair = ""
                    try:
                        original_content_for_baseline_repair = Path(safe_knowledge_path(filename)).read_text(encoding="utf-8")
                    except Exception:
                        original_content_for_baseline_repair = ""

                    expected_after_for_baseline_repair = expected_after or ""
                    repaired_content = await repair_full_candidate_baseline_preservation(
                        filename=filename,
                        task_goal=task.get("goal", ""),
                        original_content=original_content_for_baseline_repair,
                        candidate_content=content,
                        baseline_report=baseline_report[:6000] if baseline_report else "No baseline report available.",
                        expected_after=expected_after_for_baseline_repair
                    )

                    repaired_baseline_after = generate_target_file_baseline(filename, repaired_content)
                    repaired_baseline_diff = compare_target_file_baselines(
                        task.get("inputs", {}).get("baseline_before", ""),
                        repaired_baseline_after
                    )

                    if repaired_baseline_diff.get("ok") is False:
                        await cl.Message(content=f"""The file operation was not staged yet.

Baseline preservation check failed after repair attempt.

File: `{filename}`
Operation: `{operation}`

{repaired_baseline_diff.get("report", "No baseline report available.")[:4000]}

The candidate should preserve baseline facts unless the task intentionally changes them.""").send()
                        return

                    content = repaired_content
                    fw["content"] = content

                    try:
                        candidate_path = safe_knowledge_path(filename) + ".leo_candidate"
                        Path(candidate_path).write_text(content, encoding="utf-8")
                        fw["candidate_path"] = candidate_path
                    except Exception:
                        pass

                    baseline_after = repaired_baseline_after
                    baseline_diff = repaired_baseline_diff
                    baseline_report = repaired_baseline_diff.get("report", "")

            stage_file_operation(filename, content, operation=operation, reason=reason)

            pending = get_pending_write()
            pending = enrich_pending_write_from_task(pending, task)
            if pending:
                cl.user_session.set("pending_write", pending)

            await cl.Message(content=f"""Proposed file operation staged from task.

Operation: {operation.upper()}
Risk: {risk}
File: {filename}
Reason: {reason}{maturity_note}{expected_after_note}{baseline_note}

Preview:
```jsx
{content[:5000]}
```

Approval command required:
{"/approve replace" if operation == "replace" else "/approve edit" if operation == "edit" else "/approve write"}

To cancel, run:
/cancel write""").send()

        return

    history = cl.user_session.get("history") or []
    structured = wants_structured(user_text)

    if user_text.lower().startswith("/agent"):
        user_text = user_text[len("/agent"):].strip()

    system_prompt = build_system_prompt(user_text, structured)
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history[-MAX_HISTORY_MESSAGES:])
    messages.append({"role": "user", "content": user_text})

    msg = cl.Message(content="")
    await msg.send()

    try:
        if structured:
            raw = await call_ollama(messages, temperature=0.1)
            parsed = parse_json_or_none(raw)
            if parsed:
                full = json.dumps(parsed, indent=2)
                await msg.stream_token("```json\n" + full + "\n```")

                next_agent = parsed.get("next_agent", "none")
                action = parsed.get("action", "")

                if next_agent and next_agent != "none" and action:
                    task = create_task(
                        goal=action,
                        assigned_role=next_agent,
                        requested_by="agent",
                        inputs={
                            "source": "/agent",
                            "parent_result": parsed
                        }
                    )

                    await cl.Message(
                        content=f"🧩 Drafted and staged creation of follow-up task `{task['task_id']}` for role `{next_agent}`.\nGoal: {action}"
                    ).send()
            else:
                full = raw
                await msg.stream_token(raw)
        else:
            full = await call_ollama_stream(messages, msg)

        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": full})
        cl.user_session.set("history", history[-MAX_HISTORY_MESSAGES:])
        await msg.update()

    except Exception as e:
        await cl.Message(content=f"Error: {str(e)}").send()

def safe_json_loads(text, default=None):
    text = text or ""

    try:
        return json.loads(text)
    except Exception:
        pass

    # Try fenced JSON
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except Exception:
            pass

    # Try first full JSON object in response
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass

    return default
