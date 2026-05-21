from pathlib import Path


def validate_proposed_code_syntax(filename, content):
    import tempfile
    import subprocess
    import json

    filename = filename or ""
    suffix = Path(filename).suffix or ".js"

    if suffix not in [".js", ".jsx", ".ts", ".tsx"]:
        return {"success": True, "stdout": "", "stderr": "", "exit_code": 0, "skipped": True}

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / ("proposed" + suffix)
            tmp_path.write_text(content, encoding="utf-8")

            project_dir = Path.home() / "leo-agent"
            target_project = Path.home() / "Desktop" / "Leo_Files"
            cwd = target_project if target_project.exists() else project_dir

            validator_path = cwd / ".leo_validate_syntax.js"
            validator_path.write_text(r"""
const fs = require('fs');
const parser = require('@babel/parser');

const filePath = process.argv[2];
const code = fs.readFileSync(filePath, 'utf8');

try {
  parser.parse(code, {
    sourceType: 'module',
    plugins: [
      'jsx',
      'typescript',
      'classProperties',
      'objectRestSpread',
      'optionalChaining',
      'nullishCoalescingOperator'
    ],
    errorRecovery: false
  });

  console.log('Syntax OK');
  process.exit(0);
} catch (err) {
  console.error(err.message);
  if (err.loc) {
    console.error(`Line: ${err.loc.line}, Column: ${err.loc.column}`);
  }
  process.exit(1);
}
""", encoding="utf-8")

            cmd = ["node", str(validator_path), str(tmp_path)]

            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=20
            )

            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
                "skipped": False,
                "command": "node @babel/parser JSX syntax check"
            }

    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "exit_code": 1,
            "skipped": False,
            "command": "Babel syntax validator"
        }
