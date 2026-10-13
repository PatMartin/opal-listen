"""Rendering helpers: rich tables for humans, raw JSON with --json."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from opal_listener.cli.client import ApiClient

console = Console()
err_console = Console(stderr=True)
KEY_COLUMNS = frozenset({"id", "name", "filename", "test_id", "intent_name", "value"})


class CliState:
    def __init__(self, api: ApiClient, json_output: bool, xpub_url: str = "", xsub_url: str = "") -> None:
        self.api = api
        self.json_output = json_output
        self.xpub_url = xpub_url
        self.xsub_url = xsub_url


def state(ctx: typer.Context) -> CliState:
    obj = ctx.obj
    if not isinstance(obj, CliState):
        raise RuntimeError("CLI state not initialised")
    return obj


def print_json(data: Any) -> None:
    console.print_json(json.dumps(data, default=str))


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if isinstance(value, dict | list):
        return json.dumps(value)
    return str(value)


def render(ctx: typer.Context, data: Any, columns: Sequence[str] | None = None, title: str | None = None) -> None:
    if state(ctx).json_output or columns is None:
        print_json(data)
        return
    rows = data if isinstance(data, list) else [data]
    table = Table(title=title, show_lines=False)
    for column in columns:
        table.add_column(
            column, no_wrap=column in KEY_COLUMNS, min_width=len(column) if column in KEY_COLUMNS else None
        )
    for row in rows:
        table.add_row(*(_cell(row.get(c)) for c in columns))
    console.print(table)


def render_kv(ctx: typer.Context, data: dict[str, Any], title: str | None = None) -> None:
    if state(ctx).json_output:
        print_json(data)
        return
    table = Table(title=title, show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    for key, value in data.items():
        table.add_row(key, _cell(value))
    console.print(table)


def done(ctx: typer.Context, message: str, data: Any = None) -> None:
    if state(ctx).json_output:
        print_json(data if data is not None else {"status": "ok", "message": message})
    else:
        console.print(f"[green]✓[/green] {message}")


RESULT_FIELDS = (
    "wakeword",
    "transcript",
    "normalized_transcript",
    "intent_name",
    "slot_values",
    "wakeword_confidence",
    "intent_confidence",
    "matched",
    "total_latency_ms",
)


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    summary = {k: result.get(k) for k in RESULT_FIELDS}
    if not result.get("matched"):
        summary["no_match_reason"] = result.get("no_match_reason")
    return summary
