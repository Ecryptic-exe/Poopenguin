"""Command diagnostics contain metadata, never raw arguments or exception values."""

import sys
import traceback
from pathlib import Path
from typing import Any

from structlog.typing import EventDict


def exception_metadata(exc: BaseException | None) -> dict[str, Any]:
    if exc is None:
        return {"type": "Unknown", "frames": []}
    frames = []
    for frame, line in traceback.walk_tb(exc.__traceback__):
        frames.append(
            {
                "file": Path(frame.f_code.co_filename).name,
                "line": line,
                "function": frame.f_code.co_name,
            }
        )
        if len(frames) >= 40:
            break
    return {
        "type": f"{type(exc).__module__}.{type(exc).__qualname__}",
        "frames": frames,
    }


def protect_command_diagnostics(logger, method_name: str, event_dict: EventDict):
    for name in ("args", "kwargs"):
        value = event_dict.pop(name, None)
        if value is not None:
            event_dict[name + "_count"] = (
                len(value) if isinstance(value, (tuple, list, dict)) else None
            )
    info = event_dict.pop("exc_info", None)
    if info:
        exc = sys.exc_info()[1] if info is True else info
        if isinstance(exc, tuple) and len(exc) == 3:
            exc = exc[1]
        event_dict["error_metadata"] = exception_metadata(
            exc if isinstance(exc, BaseException) else None
        )
    return event_dict
