#!/usr/bin/env python3
"""Minimal local release-check helper for the example skill."""

from __future__ import annotations

import json


def main() -> int:
    print(json.dumps({"release_check": "example", "status": "manual-review-required"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

