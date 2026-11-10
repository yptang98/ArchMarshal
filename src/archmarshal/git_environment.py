"""Keep read-only Git inspection bound to the explicitly selected checkout."""
from __future__ import annotations

import os


def inspection_environment() -> dict[str, str]:
    # Git's inherited repository/config overrides take precedence over -C.
    return {**{key: value for key, value in os.environ.items()
               if not key.upper().startswith("GIT_")}, "GIT_OPTIONAL_LOCKS": "0"}
