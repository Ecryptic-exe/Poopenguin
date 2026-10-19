# Compatibility alias for chuni_penguin.cogs.poopenguin.copypasta_manager.
import importlib as _importlib
import sys as _sys

_canonical = _importlib.import_module("chuni_penguin.cogs.poopenguin.copypasta_manager")
_sys.modules[__name__] = _canonical
