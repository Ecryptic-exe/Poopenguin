"""Explicit operator review, separate from unconfirmed search candidates."""

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


def bind_manual(
    root: Path, *, row: int, song_id: int, difficulty: str, reviewer: str, evidence: str
):
    from chuni_penguin.services.phase1_charts import DIFFICULTIES

    source = root / "snapshots/manual.json"
    rows = json.loads(source.read_text(encoding="utf-8"))["charts"]
    seeds = json.loads(
        (root / "chuni_penguin/database/seeds/songs.json").read_text(encoding="utf-8")
    )
    song = next((s for s in seeds if s["id"] == song_id), None)
    if (
        not 0 <= row < len(rows)
        or song is None
        or not any(c["difficulty"] == difficulty for c in song["charts"])
    ):
        raise ValueError("Unknown source row or canonical chart.")
    if rows[row]["difficulty"].casefold() != DIFFICULTIES[difficulty].casefold():
        raise ValueError("Source and canonical difficulty differ.")
    if len(reviewer.strip()) < 2 or len(evidence.strip()) < 12:
        raise ValueError("Supply a reviewer and concrete identity evidence.")
    destination = root / "snapshots/manual_bindings.json"
    bindings = json.loads(destination.read_text(encoding="utf-8"))
    if any(item["row"] == row for item in bindings):
        raise ValueError(
            "Source row already bound; inspect the existing association first."
        )
    bindings.append(
        {
            "row": row,
            "song_id": song_id,
            "difficulty": difficulty,
            "reviewer": reviewer,
            "evidence": evidence,
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    )
    shutil.copy2(destination, str(destination) + ".bak")
    descriptor, temporary = tempfile.mkstemp(dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(bindings, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
