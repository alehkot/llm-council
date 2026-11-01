"""Pure compatibility views of saved council results."""

from copy import deepcopy
from typing import Any, Optional


def normalize_stage3(stage3: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Return a detached versioned view without rewriting data or inventing dates.

    Legacy flat answers remain accessible through the same `current` key as new
    records. Preserve original text and extra evidence fields, including usage.
    """
    if stage3 is None:
        return None
    if "current" in stage3 and "versions" in stage3:
        return deepcopy(stage3)
    version = deepcopy(stage3)
    version.setdefault("mode", "best_of_breed")
    version.setdefault("timestamp", None)
    return {"current": version, "versions": [deepcopy(version)]}
