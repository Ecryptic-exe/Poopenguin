"""Lazy local adapter for C's unchanged modules; never loads its .env or client."""

import hashlib
import importlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


class RatingUnavailable(Exception):
    """Safe boundary error; never include module exception text or local paths."""


def engine_files(root: Path) -> dict[str, str]:
    files = {}
    for folder in ("ratingbot", "engine", "assets"):
        for path in sorted((root / folder).glob("*")):
            if path.is_file() and path.suffix in {".py", ".json", ".html", ".otf"}:
                files[path.relative_to(root).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    return files


class RatingRuntime:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        self.files = engine_files(self.root)
        required = {
            "ratingbot/__init__.py",
            "ratingbot/catalog.py",
            "ratingbot/config.py",
            "ratingbot/service.py",
            "ratingbot/worker.py",
        }
        if not required <= self.files.keys():
            msg = "Rating engine files are missing."
            raise RatingUnavailable(msg)
        self.package = (
            "_chuni_rating_"
            + hashlib.sha256(
                json.dumps([str(self.root), self.files], sort_keys=True).encode()
            ).hexdigest()[:16]
        )
        if self.package not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                self.package,
                self.root / "ratingbot/__init__.py",
                submodule_search_locations=[str(self.root / "ratingbot")],
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[self.package] = module
            try:
                spec.loader.exec_module(module)
            except BaseException:
                del sys.modules[self.package]
                raise
        self.catalog = self.module("catalog")

    def module(self, name: str) -> Any:
        # C has no type annotations for these factories. Any stays at this
        # optional external-module boundary; application DTOs are typed separately.
        return importlib.import_module(self.package + "." + name)

    def assert_unchanged(self):
        if engine_files(self.root) != self.files:
            msg = "Rating engine changed; restart with the reviewed version."
            raise RatingUnavailable(msg)

    def loaded_modules(self) -> tuple[ModuleType, ...]:
        return tuple(
            module
            for name, module in sys.modules.items()
            if name == self.package or name.startswith(self.package + ".")
        )
