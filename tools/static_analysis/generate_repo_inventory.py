#!/usr/bin/env python3
from pathlib import Path
import ast
import re
import sqlite3
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "generated"

IGNORE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    "dist", "build", "logs", ".pytest_cache"
}

TARGET_DIRS = [
    "modules",
    "ai",
    "intelligence",
    "backend",
    "frontend",
]

def should_ignore(path: Path) -> bool:
    return any(part in IGNORE_DIRS for part in path.parts)

def write(name: str, content: str):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(content, encoding="utf-8")

def list_repo_files():
    lines = ["# Generated Repo File Index", ""]
    for path in sorted(ROOT.rglob("*")):
        if should_ignore(path):
            continue
        if path.is_file():
            rel = path.relative_to(ROOT)
            if rel.suffix in {".py", ".js", ".jsx", ".ts", ".tsx", ".md", ".txt", ".json"}:
                lines.append(f"- `{rel}`")
    write("repo_file_index.md", "\n".join(lines) + "\n")

def extract_python_symbols():
    lines = ["# Generated Python Symbol Index", ""]
    for path in sorted(ROOT.rglob("*.py")):
        if should_ignore(path):
            continue

        rel = path.relative_to(ROOT)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception as e:
            lines.append(f"## `{rel}`")
            lines.append(f"- Parse error: `{e}`")
            lines.append("")
            continue

        classes = []
        funcs = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes.append(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.append(node.name)

        if classes or funcs:
            lines.append(f"## `{rel}`")
            if classes:
                lines.append("### Classes")
                for c in classes:
                    lines.append(f"- `{c}`")
            if funcs:
                lines.append("### Functions")
                for f in funcs:
                    lines.append(f"- `{f}`")
            lines.append("")

    write("python_symbol_index.md", "\n".join(lines) + "\n")

def grep_cli_commands():
    targets = [
        ROOT / "pipeline.py",
    ]

    patterns = [
        r"add_parser\(['\"]([^'\"]+)['\"]",
        r"subparsers\.add_parser\(['\"]([^'\"]+)['\"]",
        r"parser\.add_argument\(['\"](--[^'\"]+)['\"]",
        r"add_argument\(['\"](--[^'\"]+)['\"]",
    ]

    lines = ["# Generated CLI Command Index", ""]

    for file in targets:
        if not file.exists():
            continue

        rel = file.relative_to(ROOT)
        text = file.read_text(encoding="utf-8", errors="ignore")
        lines.append(f"## `{rel}`")
        found = []

        for i, line in enumerate(text.splitlines(), start=1):
            for pattern in patterns:
                match = re.search(pattern, line)
                if match:
                    found.append((i, match.group(1), line.strip()))

        for lineno, item, raw in found:
            lines.append(f"- Line {lineno}: `{item}`")
            lines.append(f"  - `{raw}`")

        lines.append("")

    write("cli_command_index.md", "\n".join(lines) + "\n")

def grep_dangerous_ops():
    patterns = [
        "shutil.move",
        "shutil.copy",
        "os.remove",
        "unlink",
        "rename",
        "replace",
        "save",
        "delete",
        "write",
        "mutagen",
        "easyid3",
        "id3",
        "--apply",
        "apply",
        "commit",
    ]

    lines = ["# Generated Dangerous Operation Index", ""]
    for path in sorted(ROOT.rglob("*.py")):
        if should_ignore(path):
            continue

        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        hits = []

        for i, line in enumerate(text, start=1):
            lower = line.lower()
            if any(p.lower() in lower for p in patterns):
                hits.append((i, line.strip()))

        if hits:
            lines.append(f"## `{rel}`")
            for lineno, raw in hits:
                lines.append(f"- Line {lineno}: `{raw}`")
            lines.append("")

    write("dangerous_operations_index.md", "\n".join(lines) + "\n")

def grep_logging_outputs():
    patterns = [
        ".jsonl",
        ".json",
        ".log",
        "logging.",
        "logger.",
        "Log file",
        "Summary file",
        "JSONL file",
    ]

    lines = ["# Generated Logging Index", ""]
    for path in sorted(ROOT.rglob("*.py")):
        if should_ignore(path):
            continue

        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        hits = []

        for i, line in enumerate(text, start=1):
            if any(p in line for p in patterns):
                hits.append((i, line.strip()))

        if hits:
            lines.append(f"## `{rel}`")
            for lineno, raw in hits:
                lines.append(f"- Line {lineno}: `{raw}`")
            lines.append("")

    write("logging_index.md", "\n".join(lines) + "\n")

def grep_sql_schema():
    patterns = [
        "CREATE TABLE",
        "CREATE INDEX",
        "ALTER TABLE",
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
    ]

    lines = ["# Generated Schema / SQL Index", ""]
    for path in sorted(ROOT.rglob("*.py")):
        if should_ignore(path):
            continue

        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        hits = []

        for i, line in enumerate(text, start=1):
            upper = line.upper()
            if any(p in upper for p in patterns):
                hits.append((i, line.strip()))

        if hits:
            lines.append(f"## `{rel}`")
            for lineno, raw in hits:
                lines.append(f"- Line {lineno}: `{raw}`")
            lines.append("")

    write("schema_sql_index.md", "\n".join(lines) + "\n")

def inspect_sqlite_files():
    lines = ["# Generated SQLite Schema Dump", ""]

    for db_path in sorted(ROOT.rglob("*.db")):
        if should_ignore(db_path):
            continue

        rel = db_path.relative_to(ROOT)
        lines.append(f"## `{rel}`")

        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("SELECT name, type, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name")
            rows = cur.fetchall()
            for name, typ, sql in rows:
                lines.append(f"### {typ}: `{name}`")
                lines.append("```sql")
                lines.append(sql)
                lines.append("```")
            conn.close()
        except Exception as e:
            lines.append(f"- Could not inspect: `{e}`")

        lines.append("")

    write("sqlite_schema_dump.md", "\n".join(lines) + "\n")

def grep_safety_logic():
    patterns = [
        "confidence",
        "threshold",
        "review",
        "queue",
        "quarantine",
        "ignored",
        "skip",
        "hard block",
        "mismatch",
        "similarity",
        "isrc",
        "dry_run",
        "dry-run",
        "apply",
    ]

    lines = ["# Generated Safety Logic Index", ""]
    for path in sorted(ROOT.rglob("*.py")):
        if should_ignore(path):
            continue

        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        hits = []

        for i, line in enumerate(text, start=1):
            lower = line.lower()
            if any(p in lower for p in patterns):
                hits.append((i, line.strip()))

        if hits:
            lines.append(f"## `{rel}`")
            for lineno, raw in hits[:120]:
                lines.append(f"- Line {lineno}: `{raw}`")
            if len(hits) > 120:
                lines.append(f"- ... truncated {len(hits) - 120} additional hits")
            lines.append("")

    write("safety_logic_index.md", "\n".join(lines) + "\n")

def create_summary():
    lines = [
        "# Generated Static Analysis Summary",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "Generated files:",
        "",
        "- `repo_file_index.md`",
        "- `python_symbol_index.md`",
        "- `cli_command_index.md`",
        "- `dangerous_operations_index.md`",
        "- `logging_index.md`",
        "- `schema_sql_index.md`",
        "- `sqlite_schema_dump.md`",
        "- `safety_logic_index.md`",
        "",
        "Use these files as compact AI input instead of asking Claude/ChatGPT to scan the full repository.",
    ]
    write("STATIC_ANALYSIS_SUMMARY.md", "\n".join(lines) + "\n")

def main():
    list_repo_files()
    extract_python_symbols()
    grep_cli_commands()
    grep_dangerous_ops()
    grep_logging_outputs()
    grep_sql_schema()
    inspect_sqlite_files()
    grep_safety_logic()
    create_summary()
    print(f"Generated static analysis files in: {OUT}")

if __name__ == "__main__":
    main()
