from pathlib import Path
import tempfile
import subprocess
import re


def extract_validation_js(text):
    text = text or ""
    m = re.search(
        r"VALIDATION_JS:\s*(.*?)(?=\nCONTENT:|\nSTATUS:|\Z)",
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
    # VALIDATION_JS:
    # javascript
    # const x = ...
    snippet = re.sub(r"^(javascript|js)\s*\n", "", snippet, flags=re.IGNORECASE).strip()

    return snippet


def validate_js_snippet(snippet, timeout_seconds=8):
    snippet = snippet or ""

    if not snippet.strip():
        return {
            "success": False,
            "stdout": "",
            "stderr": "No JavaScript snippet provided.",
            "exit_code": 1
        }

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".js",
            delete=False,
            mode="w",
            encoding="utf-8"
        ) as f:
            f.write(snippet)
            temp_path = f.name

        result = subprocess.run(
            ["node", temp_path],
            capture_output=True,
            text=True,
            timeout=timeout_seconds
        )

        return {
            "success": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "exit_code": result.returncode
        }

    except FileNotFoundError:
        return {
            "success": False,
            "stdout": "",
            "stderr": "Node.js is not installed or not available on PATH.",
            "exit_code": 127
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Validation timed out after {timeout_seconds} seconds.",
            "exit_code": 124
        }

    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "exit_code": 1
        }

    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass


def check_validation_js_contract(snippet):
    snippet = snippet or ""
    lowered = snippet.lower()

    forbidden_patterns = [
        ("React import/require", r"\brequire\s*\(\s*['\"]react['\"]\s*\)|\bfrom\s+['\"]react['\"]|\bimport\s+react\b"),
        ("Project file import/require", r"\brequire\s*\(\s*['\"]\./|\brequire\s*\(\s*['\"]\.\./|\bfrom\s+['\"]\./|\bfrom\s+['\"]\.\./"),
        ("JSX syntax", r"<[A-Z][A-Za-z0-9_]*\b|<[a-z]+[\s>][\s\S]*</[a-z]+>"),
        ("Jest/test syntax", r"\btest\s*\(|\bit\s*\(|\bexpect\s*\(|\bjest\."),
        ("Testing Library/render", r"@testing-library|render\s*\("),
        ("DOM/browser API", r"\bdocument\.|\bwindow\.|\bReactDOM\b"),
        ("Hooks/component execution", r"\buseState\s*\(|\buseEffect\s*\("),
    ]

    violations = []
    for label, pattern in forbidden_patterns:
        if re.search(pattern, snippet, flags=re.IGNORECASE):
            violations.append(label)

    if violations:
        return False, (
            "VALIDATION_JS contract violation. "
            "VALIDATION_JS must be standalone Node-compatible JavaScript that directly exercises changed logic. "
            "Do not use React, JSX, imports/requires of project files, Jest, Testing Library, DOM APIs, or hooks. "
            "Violations: " + ", ".join(sorted(set(violations)))
        )

    if "console.log" not in snippet:
        return False, "VALIDATION_JS must print concrete runtime evidence using console.log."

    return True, ""
