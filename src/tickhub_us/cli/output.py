"""CLI 输出：stdout 只放数据，stderr 放警告与结构化错误。

``--output json`` 原样打印 DataEnvelope（含溯源字段），适合管道与 Agent；
``--output table`` 用 rich 渲染成人类可读的表格，并在末尾把 ``warnings``
与阻断提示写到 stderr，避免污染 stdout。
"""

from __future__ import annotations

import json
import sys
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

_MAX_COLUMNS = 12
_MAX_ROWS = 40
_MAX_WIDTH = 80


def console() -> Console:
    return Console(markup=False, highlight=False, soft_wrap=False)


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    return text if len(text) <= _MAX_WIDTH else text[: _MAX_WIDTH - 1] + "…"


def _provenance(envelope: dict[str, Any]) -> str:
    parts = []
    for key, label in (
        ("source", "source"),
        ("compliance_tier", "tier"),
        ("freshness", "freshness"),
        ("fetched_at", "fetched_at"),
    ):
        value = envelope.get(key)
        if value:
            parts.append(f"{label}={value}")
    if envelope.get("cache_hit"):
        parts.append("cache_hit=true")
    if envelope.get("domain"):
        parts.insert(0, f"domain={envelope['domain']}")
    return "  ".join(parts)


def _render_rows(
    con: Console,
    rows: list[dict[str, Any]],
    *,
    title: str | None = None,
    max_rows: int = _MAX_ROWS,
    max_columns: int = _MAX_COLUMNS,
) -> None:
    if not rows:
        con.print("[empty]" if not title else f"{title}: [empty]")
        return
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    truncated_columns = len(columns) > max_columns
    if truncated_columns:
        columns = columns[:max_columns]
    table = Table(title=title, show_lines=False, header_style="bold", expand=False)
    for name in columns:
        table.add_column(str(name), overflow="fold")
    for row in rows[:max_rows]:
        table.add_row(*(_cell(row.get(name)) for name in columns))
    con.print(table)
    if truncated_columns or len(rows) > max_rows:
        notes = []
        if truncated_columns:
            notes.append(f"仅显示前 {max_columns} 列")
        if len(rows) > max_rows:
            notes.append(f"共 {len(rows)} 行，仅显示前 {max_rows} 行")
        con.print("… " + "；".join(notes))


def _render_payload(con: Console, payload: Any) -> None:
    if isinstance(payload, list):
        if payload and all(isinstance(item, dict) for item in payload):
            _render_rows(con, payload)
        elif payload:
            for item in payload[: _MAX_ROWS]:
                con.print(_cell(item))
            if len(payload) > _MAX_ROWS:
                con.print(f"… 共 {len(payload)} 行，仅显示前 {_MAX_ROWS} 行")
        else:
            con.print("[empty]")
        return

    if not isinstance(payload, dict):
        con.print(_cell(payload))
        return

    scalars = {
        key: value
        for key, value in payload.items()
        if not isinstance(value, (dict, list, tuple))
    }
    nested = {
        key: value
        for key, value in payload.items()
        if isinstance(value, (dict, list, tuple))
    }
    if scalars:
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column(justify="right", style="bold")
        table.add_column(overflow="fold")
        for key, value in scalars.items():
            table.add_row(str(key), _cell(value))
        con.print(table)
    for key, value in nested.items():
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            _render_rows(con, value, title=str(key))
        elif isinstance(value, list) and not value:
            con.print(f"{key}: [empty]")
        elif isinstance(value, list):
            con.print(f"{key}: " + ", ".join(_cell(item) for item in value[: _MAX_ROWS]))
        else:
            con.print(f"{key}:")
            _render_payload(con, value)
    if not scalars and not nested:
        con.print("[empty]")


def emit(payload: Any, *, output: str = "table") -> None:
    """把返回值写到 stdout；table 模式下 warnings 写 stderr。"""
    if output == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return

    con = console()
    if isinstance(payload, dict) and "payload" in payload:
        line = _provenance(payload)
        if line:
            con.print(line, style="dim")
        con.print()
        _render_payload(con, payload.get("payload"))
        for warning in payload.get("warnings") or ():
            print(f"warning: {warning}", file=sys.stderr)
        return
    _render_payload(con, payload)


def fail(exc: Any) -> "typer.Exit":
    """结构化错误写 stderr，并以错误码退出。"""
    detail = exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)}
    print(json.dumps(detail, ensure_ascii=False, indent=2, default=str), file=sys.stderr)
    return typer.Exit(code=getattr(exc, "exit_code", 1))
