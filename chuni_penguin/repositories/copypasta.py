"""Explicit local template storage; no deployment data is selected implicitly."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class CopypastaStorageError(ValueError):
    """A template file cannot safely be read or replaced."""


class CopypastaRepository:
    """One local JSON pool, owned by one Bot process.

    The caller supplies an existing working copy. Missing, empty or malformed
    input fails closed; it is never replaced with an empty default. Unknown
    fields are preserved. Successful writes retain the previous bytes in .bak
    and atomically replace the JSON file. This is not a multi-process store.
    """

    def __init__(self, path: Path):
        self.path = path
        self.backup_path = path.with_suffix(path.suffix + ".bak")

    @staticmethod
    def _validate(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict) or not isinstance(data.get("types", {}), dict):
            msg = "Copypasta JSON must contain an object with an optional types object."
            raise CopypastaStorageError(msg)
        return data

    def _decode(self, raw: bytes) -> dict[str, Any]:
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            msg = "Copypasta JSON is empty or invalid; the original file was preserved."
            raise CopypastaStorageError(msg) from exc
        return self._validate(data)

    def load(self) -> dict[str, Any]:
        return self._decode(self.path.read_bytes())

    @staticmethod
    def _replace(path: Path, raw: bytes) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save(self, data: dict[str, Any]) -> None:
        self._validate(data)
        encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        previous = self.path.read_bytes()
        self._decode(previous)
        self._replace(self.backup_path, previous)
        self._replace(self.path, encoded)
