"""Original project version lookup, retaining the current-working-directory path."""

import tomllib
from pathlib import Path


def get_version_from_pyproject() -> str:
    with Path("pyproject.toml").open("rb") as f:
        pyproject = tomllib.load(f)

    return "v" + pyproject["project"]["version"]
