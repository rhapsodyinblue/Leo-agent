import ast
import re


def detect_file_kind(filename):
    lower = (filename or "").lower()
    if lower.endswith((".jsx", ".tsx")):
        return "react_component"
    if lower.endswith((".js", ".ts")):
        return "javascript_or_react"
    if lower.endswith(".html"):
        return "html"
    if lower.endswith(".css"):
        return "css"
    if lower.endswith(".json"):
        return "json"
    if lower.endswith(".md"):
        return "markdown"
    return "unknown"


def generate_target_file_baseline(filename, content):
    content = content or ""

    baseline = {
        "file": filename,
        "file_kind": detect_file_kind(filename),
        "component_names": sorted(set(re.findall(r"\b(?:function|const)\s+([A-Z][A-Za-z0-9_]*)\b", content))),
        "exports": sorted(set(re.findall(r"export\s+default\s+([A-Za-z0-9_]+)", content))),
        "props": [],
        "state_keys": [],
        "handlers": sorted(set(re.findall(r"\b(handle[A-Z][A-Za-z0-9_]*|on[A-Z][A-Za-z0-9_]*)\b", content))),
        "rendered_labels_headings": [],
        "input_bindings": [],
        "mapped_collections": sorted(set(re.findall(r"formData\.([A-Za-z_][A-Za-z0-9_]*)\.map\s*\(", content))),
        "submit_calls": sorted(set(re.findall(r"\b(onSubmit|handleSubmit)\s*\(([^)]*)\)", content))),
    }

    prop_matches = re.findall(
        r"(?:const|function)\s+[A-Z][A-Za-z0-9_]*\s*(?:=\s*)?\(\s*\{([^}]+)\}",
        content
    )
    props = []
    for match in prop_matches:
        for part in match.split(","):
            name = part.strip().split(":")[0].strip()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                props.append(name)
    baseline["props"] = sorted(set(props))

    state_blocks = re.findall(r"useState\s*\(\s*\{([\s\S]*?)\}\s*\)", content)
    state_keys = []
    for block in state_blocks:
        state_keys.extend(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", block, flags=re.MULTILINE))
    baseline["state_keys"] = sorted(set(state_keys))

    labels = re.findall(r"<label[^>]*>\s*([^<]+?)\s*</label>", content, flags=re.IGNORECASE)
    headings = re.findall(r"<h[1-6][^>]*>\s*([^<]+?)\s*</h[1-6]>", content, flags=re.IGNORECASE)
    baseline["rendered_labels_headings"] = sorted(set(x.strip() for x in labels + headings if x.strip()))

    input_blocks = re.findall(r"<input[\s\S]*?/>", content)
    for block in input_blocks:
        def attr_value(attr):
            m = re.search(rf'{attr}\s*=\s*(?:"([^"]*)"|{{([^}}]+)}})', block)
            return ((m.group(1) or m.group(2) or "").strip() if m else "")

        baseline["input_bindings"].append({
            "type": attr_value("type"),
            "id": attr_value("id"),
            "name": attr_value("name"),
            "value": attr_value("value"),
            "onChange": attr_value("onChange"),
        })

    lines = [
        "TARGET_FILE_BASELINE:",
        f"file: {baseline['file']}",
        f"file_kind: {baseline['file_kind']}",
        f"component_names: {baseline['component_names']}",
        f"exports: {baseline['exports']}",
        f"props: {baseline['props']}",
        f"state_keys: {baseline['state_keys']}",
        f"handlers: {baseline['handlers']}",
        f"rendered_labels_headings: {baseline['rendered_labels_headings']}",
        f"mapped_collections: {baseline['mapped_collections']}",
        f"submit_calls: {baseline['submit_calls']}",
        "input_bindings:",
    ]

    for binding in baseline["input_bindings"]:
        lines.append(f"- {binding}")

    return "\n".join(lines)


def parse_target_file_baseline_text(baseline_text):
    baseline_text = baseline_text or ""
    result = {
        "state_keys": set(),
        "handlers": set(),
        "rendered_labels_headings": set(),
        "mapped_collections": set(),
        "input_names": set(),
        "input_values": set(),
    }

    for line in baseline_text.splitlines():
        stripped = line.strip()

        def parse_list_after(prefix):
            raw = stripped[len(prefix):].strip()
            try:
                parsed = ast.literal_eval(raw)
                if isinstance(parsed, list):
                    return set(str(x) for x in parsed)
            except Exception:
                pass
            return set()

        if stripped.startswith("state_keys:"):
            result["state_keys"] = parse_list_after("state_keys:")
        elif stripped.startswith("handlers:"):
            result["handlers"] = parse_list_after("handlers:")
        elif stripped.startswith("rendered_labels_headings:"):
            result["rendered_labels_headings"] = parse_list_after("rendered_labels_headings:")
        elif stripped.startswith("mapped_collections:"):
            result["mapped_collections"] = parse_list_after("mapped_collections:")
        elif stripped.startswith("- {") and "'name':" in stripped:
            try:
                item = ast.literal_eval(stripped[2:])
                name = str(item.get("name", "")).strip()
                value = str(item.get("value", "")).strip()
                if name:
                    result["input_names"].add(name)
                if value:
                    result["input_values"].add(value)
            except Exception:
                pass

    return result


def compare_target_file_baselines(before_text, after_text):
    before = parse_target_file_baseline_text(before_text)
    after = parse_target_file_baseline_text(after_text)

    protected_fields = [
        "state_keys",
        "handlers",
        "rendered_labels_headings",
        "mapped_collections",
        "input_names",
        "input_values",
    ]

    missing = {}
    added = {}

    for field in protected_fields:
        missing_items = sorted(before[field] - after[field])
        added_items = sorted(after[field] - before[field])
        if missing_items:
            missing[field] = missing_items
        if added_items:
            added[field] = added_items

    ok = not missing

    lines = [
        "TARGET_FILE_BASELINE_DIFF:",
        f"preservation_ok: {ok}",
    ]

    if missing:
        lines.append("missing_from_after:")
        for field, items in missing.items():
            lines.append(f"- {field}: {items}")
    else:
        lines.append("missing_from_after: none")

    if added:
        lines.append("added_in_after:")
        for field, items in added.items():
            lines.append(f"- {field}: {items}")
    else:
        lines.append("added_in_after: none")

    return {
        "ok": ok,
        "missing": missing,
        "added": added,
        "report": "\n".join(lines),
    }


def baseline_missing_items_unexplained_by_mode(
    baseline_diff,
    reason_text,
    expected_after_text,
    edit_mode="surgical",
    task_goal="",
    result_text="",
    intentional_adaptations_text=""
):
    """
    Returns missing baseline items that remain unexplained under the active edit mode.
    """
    if not isinstance(baseline_diff, dict):
        return {}

    missing = baseline_diff.get("missing") or {}
    if not missing:
        return {}

    edit_mode = (edit_mode or "surgical").lower().strip()

    if edit_mode == "replacement":
        return {}

    if edit_mode == "surgical":
        # Surgical mode means preservation-first. Missing baseline behavior is treated as unexplained.
        return missing

    explanation_text = "\n".join([
        reason_text or "",
        expected_after_text or "",
        task_goal or "",
        result_text or "",
        intentional_adaptations_text or ""
    ]).lower()

    def item_terms(item):
        raw = str(item or "").strip()
        low = raw.lower()
        terms = {low}

        if "." in low:
            terms.add(low.split(".")[-1])

        terms.add(low.replace(":", "").strip())

        bracket_base = low.split("[", 1)[0].replace("`", "").strip()
        if bracket_base:
            terms.add(bracket_base)

        return {t for t in terms if t}

    unexplained = {}

    for field, items in missing.items():
        still_unexplained = []

        for item in items:
            terms = item_terms(item)
            explained = any(term and term in explanation_text for term in terms)

            if edit_mode == "refactor":
                # Refactor mode may alter implementation, but visible behavior remains protected.
                visible_fields = {
                    "rendered_labels_headings",
                    "input_names",
                    "input_values",
                    "mapped_collections"
                }
                if field in visible_fields:
                    explained = False

            if not explained:
                still_unexplained.append(item)

        if still_unexplained:
            unexplained[field] = still_unexplained

    return unexplained


def format_unexplained_baseline_report(original_report, unexplained):
    lines = [
        "TARGET_FILE_BASELINE_DIFF:",
        "preservation_ok: False",
        "unexplained_missing_from_after:"
    ]

    for field, items in unexplained.items():
        lines.append(f"- {field}: {items}")

    lines.append("")
    lines.append("Original baseline report:")
    lines.append(original_report or "No baseline report available.")

    return "\n".join(lines)
