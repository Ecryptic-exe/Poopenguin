"""Only the explicitly enabled local LM Studio extension imports this module."""

import os
from urllib.parse import urlsplit

LM_STUDIO_BASE_URL = os.environ.get("LM_STUDIO_BASE_URL", "")
MODEL_NAME = os.environ.get("LM_STUDIO_MODEL", "")
VISION_MODEL_NAME = os.environ.get("LM_STUDIO_VISION_MODEL", "qwen3-vl-4b-instruct")
if urlsplit(LM_STUDIO_BASE_URL).hostname not in {"localhost", "127.0.0.1", "::1"} or not MODEL_NAME:
    raise RuntimeError("Set a loopback LM_STUDIO_BASE_URL and LM_STUDIO_MODEL before enabling local AI.")
