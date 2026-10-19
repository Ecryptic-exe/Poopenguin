# Compatibility alias for chuni_penguin.cogs.poopenguin.i18n.
import importlib as _importlib
import sys as _sys

_canonical = _importlib.import_module("chuni_penguin.cogs.poopenguin.i18n")
_sys.modules[__name__] = _canonical
