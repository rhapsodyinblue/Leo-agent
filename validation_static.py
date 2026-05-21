import re


def validate_react_static_behavior_contract(content):
    content = content or ""
    issues = []

    # Catch handlers used in JSX but not declared.
    used_handlers = set(re.findall(r"on[A-Za-z]+=\{(?:\(\)\s*=>\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(|\})", content))
    declared_handlers = set(re.findall(r"\bconst\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\(", content))
    declared_handlers |= set(re.findall(r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", content))

    missing_handlers = sorted(h for h in used_handlers if h.startswith("handle") and h not in declared_handlers)
    if missing_handlers:
        issues.append(f"Referenced handler(s) are not defined: {missing_handlers}")

    # Catch formData.someArray.map when state initializes that key as a string.
    state_match = re.search(r"useState\s*\(\s*\{([\s\S]*?)\}\s*\)", content)
    if state_match:
        state_body = state_match.group(1)
        for key in sorted(set(re.findall(r"formData\.([A-Za-z0-9_]+)\.map\s*\(", content))):
            string_init = re.search(rf"\b{re.escape(key)}\s*:\s*(['\"])\s*\1", state_body)
            if string_init:
                issues.append(f"formData.{key}.map(...) is used, but {key} is initialized as an empty string instead of an array.")

    return {
        "success": not issues,
        "issues": issues,
        "report": "\n".join(f"- {issue}" for issue in issues)
    }
