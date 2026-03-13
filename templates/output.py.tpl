"""Output formatting utilities."""

from __future__ import annotations

import json
import sys
from typing import Any, Optional


def output_json(data: Any) -> None:
    """Print data as formatted JSON."""
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def output_table(rows: list[dict], columns: Optional[list[str]] = None) -> None:
    """Print data as an aligned text table."""
    if not rows:
        print("(no results)")
        return
    if columns is None:
        columns = list(rows[0].keys())
    widths = {col: len(col) for col in columns}
    for row in rows:
        for col in columns:
            val = str(row.get(col, ""))
            widths[col] = max(widths[col], len(val))
    header = "  ".join(col.ljust(widths[col]) for col in columns)
    sep = "  ".join("-" * widths[col] for col in columns)
    print(header)
    print(sep)
    for row in rows:
        line = "  ".join(str(row.get(col, "")).ljust(widths[col]) for col in columns)
        print(line)


def output_detail(data: dict, keys: Optional[list[str]] = None) -> None:
    """Print a single record as key-value pairs."""
    if keys is None:
        keys = list(data.keys())
    max_key_len = max(len(k) for k in keys) if keys else 0
    for k in keys:
        v = data.get(k, "")
        print(f"  {k.ljust(max_key_len)}  {v}")


def output_result(data: Any, as_json: bool, table_columns: Optional[list[str]] = None) -> None:
    """Unified output: JSON mode or table/detail mode."""
    if as_json:
        output_json(data)
        return
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            output_table(data, table_columns)
        else:
            for item in data:
                print(item)
    elif isinstance(data, dict):
        if "items" in data and isinstance(data["items"], list):
            total = data.get("total", len(data["items"]))
            print(f"Total: {total}")
            output_table(data["items"], table_columns)
        else:
            output_detail(data)
    else:
        print(data)


def output_success(msg: str = "OK") -> None:
    print(f"[OK] {msg}")


def output_error(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
