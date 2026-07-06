#!/usr/bin/env python3
"""Sync custom_components/modelship_conversation from a newer Home Assistant Core release.

Usage: sync_upstream.py <upstream-tag> <path-to-checked-out-core-component-dir>

- ai_task.py, conversation.py, entity.py, stt.py, tts.py are byte-identical to
  upstream: copied over as-is.
- const.py, __init__.py, config_flow.py: fresh upstream copy + a maintained
  unified diff patch (.github/sync/patches/*.patch) applied on top.
- manifest.json, strings.json, translations/en.json: fresh upstream copy with
  a structured (JSON-level) rebrand transform applied.

Prints one line per file: "ok <name>", "identical <name>" or "CONFLICT <name>".
Exits non-zero if any file conflicted; conflicted files are left untouched
(the previous, still-working version stays in place) rather than writing
something broken.
"""

import json
from pathlib import Path
import re
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
DEST = REPO_ROOT / "custom_components" / "modelship_conversation"
PATCH_DIR = Path(__file__).resolve().parent / "patches"

IDENTICAL_FILES = ["ai_task.py", "conversation.py", "entity.py", "stt.py", "tts.py"]
PATCHED_FILES = ["const.py", "__init__.py", "config_flow.py"]

MANIFEST_OVERRIDES = {
    "domain": "modelship_conversation",
    "name": "Modelship Conversation",
    "codeowners": ["@alez007"],
    "documentation": "https://github.com/alez007/modelship-conversation",
    "iot_class": "local_polling",
    "issue_tracker": "https://github.com/alez007/modelship-conversation/issues",
}
MANIFEST_DROP = ["quality_scale"]

STRINGS_USER_STEP = {
    "data": {
        "base_url": "Base URL",
        "api_key": "[%key:common::config_flow::data::api_key%]",
    },
    "data_description": {
        "base_url": (
            "The base URL of your Modelship server's OpenAI-compatible API, "
            "e.g. http://homeassistant:8000/v1"
        ),
        "api_key": (
            "Optional. Modelship does not require authentication; leave blank "
            "to use a placeholder key."
        ),
    },
    "description": (
        "Connect to your Modelship server by providing its base URL. The "
        "connection is validated against the server's model list."
    ),
}
STRINGS_ISSUES_DROP = ["deprecated_generate_content", "deprecated_generate_image"]
STRINGS_TOP_LEVEL_DROP = ["services"]


def apply_patch(name: str, src: Path) -> bool:
    """Apply the maintained patch to a fresh upstream copy of `name`.

    Writes to a scratch file first so a partially-applied (some hunks failed)
    result never overwrites the tracked file — on failure DEST is left as-is.
    """
    patch_file = PATCH_DIR / f"{name}.patch"
    scratch = src.parent / f"{name}.synced"
    reject_file = src.parent / f"{name}.synced.rej"
    result = subprocess.run(
        ["patch", "--fuzz=3", "-p1", "-o", str(scratch), str(src)],
        cwd=src.parent,
        input=patch_file.read_bytes(),
        capture_output=True,
    )
    ok = result.returncode == 0
    if ok:
        (DEST / name).write_bytes(scratch.read_bytes())
    else:
        sys.stderr.write(result.stdout.decode())
        sys.stderr.write(result.stderr.decode())
    scratch.unlink(missing_ok=True)
    reject_file.unlink(missing_ok=True)
    return ok


def _compact_string_arrays(text: str) -> str:
    """Collapse multi-line JSON arrays of strings onto one line (HA manifest style)."""

    def repl(match: re.Match) -> str:
        items = re.findall(r'"[^"]*"', match.group(0))
        return "[" + ", ".join(items) + "]"

    return re.sub(r"\[\s*(?:\"[^\"]*\"\s*,?\s*)+\]", repl, text)


def rebrand_manifest(upstream_path: Path, new_version: str) -> None:
    data = json.loads(upstream_path.read_text())
    for key in MANIFEST_DROP:
        data.pop(key, None)
    data.update(MANIFEST_OVERRIDES)
    data["version"] = new_version
    # Keep upstream's key order where possible, our overrides appended after.
    ordered = {k: data[k] for k in upstream_path_keys(upstream_path) if k in data}
    for k, v in data.items():
        ordered.setdefault(k, v)
    text = _compact_string_arrays(json.dumps(ordered, indent=2)) + "\n"
    (DEST / "manifest.json").write_text(text)


def upstream_path_keys(path: Path) -> list[str]:
    return list(json.loads(path.read_text()).keys())


def rebrand_strings(upstream_path: Path) -> bool:
    text = upstream_path.read_text()
    text = text.replace(
        "component::openai_conversation::", "component::modelship_conversation::"
    )
    data = json.loads(text)

    try:
        user_step = data["config"]["step"]["user"]
    except KeyError:
        return False
    user_step.clear()
    user_step.update(STRINGS_USER_STEP)

    issues = data.get("issues", {})
    for key in STRINGS_ISSUES_DROP:
        issues.pop(key, None)

    for key in STRINGS_TOP_LEVEL_DROP:
        data.pop(key, None)

    out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    (DEST / "strings.json").write_text(out)
    (DEST / "translations" / "en.json").write_text(out)
    return True


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <upstream-tag> <upstream-component-dir>")
        return 2
    new_tag, upstream_dir = sys.argv[1], Path(sys.argv[2])

    conflicts = []

    for name in IDENTICAL_FILES:
        src = upstream_dir / name
        dst = DEST / name
        if dst.exists() and dst.read_bytes() == src.read_bytes():
            print(f"identical {name}")
        else:
            dst.write_bytes(src.read_bytes())
            print(f"ok {name}")

    for name in PATCHED_FILES:
        if apply_patch(name, upstream_dir / name):
            print(f"ok {name}")
        else:
            print(f"CONFLICT {name}")
            conflicts.append(name)

    try:
        rebrand_manifest(upstream_dir / "manifest.json", new_tag)
        print("ok manifest.json")
    except Exception as exc:  # noqa: BLE001
        print(f"CONFLICT manifest.json ({exc})")
        conflicts.append("manifest.json")

    try:
        if rebrand_strings(upstream_dir / "strings.json"):
            print("ok strings.json / translations/en.json")
        else:
            print("CONFLICT strings.json (expected keys missing)")
            conflicts.append("strings.json")
    except Exception as exc:  # noqa: BLE001
        print(f"CONFLICT strings.json ({exc})")
        conflicts.append("strings.json")

    notice = REPO_ROOT / "NOTICE"
    notice_text = notice.read_text()
    notice_text = re.sub(
        r"Merge base: tag \S+", f"Merge base: tag {new_tag}", notice_text
    )
    notice.write_text(notice_text)

    if conflicts:
        sys.stderr.write(
            "\nConflicts in: "
            + ", ".join(conflicts)
            + "\nThese files were left unchanged; resolve manually against "
            + f"upstream tag {new_tag}.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
