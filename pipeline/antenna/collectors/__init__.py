"""Collector registry. Any module here with SLUG and collect() is picked up."""

from __future__ import annotations

import importlib
import pkgutil
import sys
from types import ModuleType


def load_all() -> list[ModuleType]:
    """Import every collector module, in stage order: discover, identify, enrich.

    A module that fails to import is reported and skipped, so one broken
    collector never stops the others from running.
    """
    mods = []
    for info in pkgutil.iter_modules(__path__):
        if info.name == "base" or info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{info.name}")
        except Exception as e:  # noqa: BLE001 - report and carry on
            print(f"  ! collector {info.name} failed to import: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        if hasattr(mod, "SLUG") and hasattr(mod, "collect"):
            mods.append(mod)
    order = {"discover": 0, "identify": 1, "enrich": 2}
    return sorted(mods, key=lambda m: (order.get(getattr(m, "STAGE", "discover"), 0), m.SLUG))
