"""Sanitized post-cutover startup/database check. No credentials or player output."""

import json
import re
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime
from pathlib import Path


def process_state(root, record):
    """Old successful startup logs are not evidence that the bot is still alive."""
    controller_id = int(record["process_id"])
    executable = str(root / ".venv/Scripts/python.exe").replace("'", "''")
    command = (
        f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {controller_id}'; "
        "[ordered]@{process_alive=[bool]$p; process_identity_matches=[bool]("
        f"$p -and $p.ExecutablePath -eq '{executable}' -and "
        "$p.CommandLine -match 'launcher\\.py\\s+--env-file\\s+\\.env')"
        "} | ConvertTo-Json -Compress"
    )
    checked = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return json.loads(checked.stdout)


def main():
    root = Path(__file__).resolve().parents[1]
    record = json.loads(
        (root / "data/runtime/bot-process.json").read_text(encoding="utf-8-sig")
    )
    log = Path(record["stdout"]).read_text(encoding="utf-8", errors="replace")
    stderr = Path(record["stderr"]).read_text(encoding="utf-8", errors="replace")
    startup = None
    for line in log.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if value.get("event") == "Extension startup finished":
            startup = value.get("startup")
    with closing(
        sqlite3.connect((root / "data/penguin.sqlite3").as_uri() + "?mode=ro", uri=True)
    ) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        active = connection.execute(
            "SELECT COUNT(*) FROM chart_annotations WHERE active=1"
        ).fetchone()[0]
        methods = dict(
            connection.execute(
                "SELECT match_method,COUNT(*) FROM chart_annotations WHERE active=1 GROUP BY match_method"
            )
        )
    result = {
        "checked_at": datetime.now().astimezone().isoformat(),
        "process_id": record["process_id"],
        "gateway_connected": bool(
            re.search("connected to Gateway", log, re.IGNORECASE)
        ),
        "guild_prefixes_loaded": "Loaded guild prefixes" in log,
        "startup_complete": bool(startup and startup.get("complete")),
        "database_service_ready": (startup or {})
        .get("services", {})
        .get("database", {})
        .get("state")
        == "ready",
        "loaded_extensions": sum(
            item.get("state") == "loaded"
            for item in (startup or {}).get("extensions", {}).values()
        ),
        "stderr_empty": not stderr.strip(),
        "runtime_errors": len(
            re.findall(
                r'"level":\s*"error"|Traceback \(most recent call last\)', log + stderr
            )
        ),
        "database_integrity": integrity,
        "foreign_key_errors": foreign_keys,
        "active_annotations": active,
        "matching_methods": methods,
        "new_required_env_settings": [],
        "remote_commands_modified": False,
    }
    result.update(process_state(root, record))
    result["complete"] = all(
        (
            result["gateway_connected"],
            result["process_alive"],
            result["process_identity_matches"],
            result["guild_prefixes_loaded"],
            result["startup_complete"],
            result["database_service_ready"],
            result["stderr_empty"],
            result["runtime_errors"] == 0,
            integrity == "ok",
            foreign_keys == 0,
            active == 834,
        )
    )
    (root / "reports/annotation-runtime-20261016.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
