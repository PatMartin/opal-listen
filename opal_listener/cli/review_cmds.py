"""opal listen failed|perf"""

from __future__ import annotations

from typing import Any

import typer

from opal_listener.cli.catalog_cmds import _changes
from opal_listener.cli.output import console, done, print_json, render, render_kv, state

FAILED = "/api/v1/failed_utterance/"
PERF = "/api/v1/performance"

failed_app = typer.Typer(help="Review failed utterances.", no_args_is_help=True)
perf_app = typer.Typer(help="Performance metrics.", no_args_is_help=True)

FAILED_COLUMNS = (
    "id",
    "transcript",
    "expected_intent",
    "reviewed",
    "notes",
    "created_at",
)
PERF_COLUMNS = ("id", "recognition_log_id", "wakeword_latency_ms", "stt_latency_ms", "intent_latency_ms",
                "total_latency_ms", "wakeword_confidence", "intent_confidence", "matched")  # fmt: skip


# --- failed ----------------------------------------------------------------------------------


@failed_app.command("list")
def failed_list(
    ctx: typer.Context,
    reviewed: bool | None = typer.Option(None, "--reviewed/--unreviewed"),
    limit: int = typer.Option(50, "--limit", max=500),
) -> None:
    """List failed utterances (newest first)."""
    params = {"limit": limit, "reviewed": reviewed}
    items = state(ctx).api.get(FAILED, params={k: v for k, v in params.items() if v is not None})
    render(ctx, items, FAILED_COLUMNS, "Failed utterances")


@failed_app.command("get")
def failed_get(ctx: typer.Context, fu_id: str) -> None:
    """Show a failed utterance."""
    render_kv(ctx, state(ctx).api.get(f"{FAILED}{fu_id}"))


@failed_app.command("add")
def failed_add(
    ctx: typer.Context,
    transcript: str,
    expected_intent: str | None = typer.Option(None, "--expected-intent"),
    notes: str | None = typer.Option(None, "--notes"),
    log_id: str | None = typer.Option(None, "--log-id", help="Related recognition log id"),
) -> None:
    """Record a failed utterance manually."""
    body = {
        "transcript": transcript,
        "expected_intent": expected_intent,
        "notes": notes,
        "recognition_log_id": log_id,
    }
    created = state(ctx).api.post(FAILED, json=body)
    done(ctx, f"failed utterance {created['id']} recorded", created)


@failed_app.command("update")
def failed_update(
    ctx: typer.Context,
    fu_id: str,
    expected_intent: str | None = typer.Option(None, "--expected-intent"),
    notes: str | None = typer.Option(None, "--notes"),
    reviewed: bool | None = typer.Option(None, "--reviewed/--unreviewed"),
) -> None:
    """Annotate a failed utterance."""
    body = _changes(expected_intent=expected_intent, notes=notes, reviewed=reviewed)
    updated = state(ctx).api.patch(f"{FAILED}{fu_id}", json=body)
    done(ctx, f"failed utterance {fu_id} updated", updated)


@failed_app.command("remove")
def failed_remove(ctx: typer.Context, fu_id: str) -> None:
    """Delete a failed utterance."""
    state(ctx).api.delete(f"{FAILED}{fu_id}")
    done(ctx, f"failed utterance {fu_id} removed")


def _render_list(ctx: typer.Context, items: list[dict[str, Any]], columns: tuple[str, ...], title: str) -> None:
    """Bounded list: --json prints the REST array; humans get the table."""
    if state(ctx).json_output:
        print_json(items)
        return
    render(ctx, items, columns, title)


# --- perf ------------------------------------------------------------------------------------


@perf_app.command("last")
def perf_last(ctx: typer.Context) -> None:
    """Performance of the most recent recognition."""
    last = state(ctx).api.get(f"{PERF}/last")
    if last is None:
        done(ctx, "no recognitions yet", None)
        return
    render_kv(ctx, last, "Last recognition")


@perf_app.command("summary")
def perf_summary(ctx: typer.Context, limit: int = typer.Option(100, "--limit", max=500)) -> None:
    """Average latency/confidence and match rate over the last N recognitions."""
    summary = state(ctx).api.get(f"{PERF}/summary", params={"limit": limit})
    if state(ctx).json_output:
        render(ctx, summary)
        return
    flat = {k: v for k, v in summary.items() if not isinstance(v, dict)}
    render_kv(ctx, flat, f"Performance (last {limit})")
    latency = [{"metric": k, **v} for k, v in summary.items() if isinstance(v, dict)]
    render(ctx, latency, ("metric", "avg", "p50", "p95", "max"), "Latency (ms)")


@perf_app.command("history")
def perf_history(
    ctx: typer.Context,
    limit: int = typer.Option(100, "--limit", max=500),
) -> None:
    """Per-recognition performance rows (newest first)."""
    items = state(ctx).api.get(f"{PERF}/history", params={"limit": limit})
    _render_list(ctx, items, PERF_COLUMNS, "Performance history")
