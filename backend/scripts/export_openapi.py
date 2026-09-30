"""Export the FastAPI OpenAPI schema as deterministic, versioned JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from narravant.main import app

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _sort_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sort_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sort_json(item) for item in value]
    return value


def export_openapi(output: Path) -> None:
    """Write FastAPI's schema with stable key ordering and a final newline."""
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_sort_json(app.openapi()), ensure_ascii=False, indent=2)
    output.write_text(f"{payload}\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=BACKEND_ROOT / "openapi.json")
    args = parser.parse_args()
    export_openapi(args.output)


if __name__ == "__main__":
    main()
