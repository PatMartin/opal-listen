"""opal listen failed|regression|logs|perf"""

from __future__ import annotations

import typer

from opal_listener.cli.catalog_cmds import _changes, parse_pairs
from opal_listener.cli.output import console, done, render, render_kv, state

FAILED = "/api/v1/failed_utterances/"
REGRESSION = "/api/v1/regression/"
LOGS = "/api/v1/logs/"
PERF = "/api/v1/performance"

failed_app = typer.Typer(help="Review failed utterances and promote them to training phrases.", no_args_is_help=True)
regression_app = typer.Typer(help="Manage saved regression tests.", no_args_is_help=True)
logs_app = typer.Typer(help="Browse recognition logs.", no_args_is_help=True)
perf_app = typer.Typer(help="Performance metrics.", no_args_is_help=True)

FAILED_COLUMNS = (
    "id",
    "transcript",
    "expected_intent",
    "reviewed",
    "promoted_to_phrase",
    "notes",
    "created_at",
)
REGRESSION_COLUMNS = ("id", "name", "phrase", "expected_intent", "expected_slots", "enabled", "last_result")
LOG_COLUMNS = ("id", "source", "wakeword", "normalized_transcript", "intent_name", "slot_values", "intent_confidence",
               "matched", "no_match_reason", "total_latency_ms", "created_at")  # fmt: skip
PERF_COLUMNS = ("id", "recognition_log_id", "wakeword_latency_ms", "stt_latency_ms", "intent_latency_ms",
                "total_latency_ms", "wakeword_confidence", "intent_confidence", "matched")  # fmt: skip


# --- failed ----------------------------------------------------------------------------------


@failed_app.command("list")
def failed_list(
    ctx: typer.Context,
    reviewed: bool | None = typer.Option(None, "--reviewed/--unreviewed"),
    promoted: bool | None = typer.Option(None, "--promoted/--not-promoted"),
    limit: int = typer.Option(50, "--limit", max=500),
    offset: int = typer.Option(0, "--offset", min=0),
) -> None:
    """List failed utterances (newest first)."""
    params = {"limit": limit, "offset": offset, "reviewed": reviewed, "promoted": promoted}
    items = state(ctx).api.get(FAILED, params={k: v for k, v in params.items() if v is not None})
    render(ctx, items, FAILED_COLUMNS, "Failed utterances")


@failed_app.command("get")
def failed_get(ctx: typer.Context, fu_id: int) -> None:
    """Show a failed utterance."""
    render_kv(ctx, state(ctx).api.get(f"{FAILED}{fu_id}"))


@failed_app.command("add")
def failed_add(
    ctx: typer.Context,
    transcript: str,
    expected_intent: str | None = typer.Option(None, "--expected-intent"),
    notes: str | None = typer.Option(None, "--notes"),
    log_id: int | None = typer.Option(None, "--log-id", help="Related recognition log id"),
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
    fu_id: int,
    expected_intent: str | None = typer.Option(None, "--expected-intent"),
    notes: str | None = typer.Option(None, "--notes"),
    reviewed: bool | None = typer.Option(None, "--reviewed/--unreviewed"),
) -> None:
    """Annotate a failed utterance."""
    body = _changes(expected_intent=expected_intent, notes=notes, reviewed=reviewed)
    updated = state(ctx).api.patch(f"{FAILED}{fu_id}", json=body)
    done(ctx, f"failed utterance {fu_id} updated", updated)


@failed_app.command("remove")
def failed_remove(ctx: typer.Context, fu_id: int) -> None:
    """Delete a failed utterance."""
    state(ctx).api.delete(f"{FAILED}{fu_id}")
    done(ctx, f"failed utterance {fu_id} removed")


@failed_app.command("promote")
def failed_promote(
    ctx: typer.Context,
    fu_id: int,
    intent: str = typer.Option(..., "--intent", help="Intent that should have matched"),
    regression: bool = typer.Option(True, "--regression/--no-regression", help="Also create a regression test"),
) -> None:
    """Add a failed utterance as a training phrase (slot values become {placeholders})."""
    result = state(ctx).api.post(
        f"{FAILED}{fu_id}/promote", params={"intent_name": intent, "create_regression": regression}
    )
    done(ctx, f"promoted as '{result['phrase']['phrase']}' for intent '{intent}'", result)


# --- regression ------------------------------------------------------------------------------


@regression_app.command("list")
def regression_list(ctx: typer.Context) -> None:
    """List regression tests."""
    render(ctx, state(ctx).api.get(REGRESSION), REGRESSION_COLUMNS, "Regression tests")


@regression_app.command("get")
def regression_get(ctx: typer.Context, test_id: int) -> None:
    """Show a regression test."""
    render_kv(ctx, state(ctx).api.get(f"{REGRESSION}{test_id}"))


@regression_app.command("add")
def regression_add(
    ctx: typer.Context,
    name: str,
    phrase: str,
    expect_intent: str = typer.Option(..., "--expect-intent", help="Intent name or 'no_match'"),
    slot: list[str] | None = typer.Option(None, "--slot", help="Expected slot key=value (repeatable)"),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
) -> None:
    """Add a regression test."""
    body = {
        "name": name,
        "phrase": phrase,
        "expected_intent": expect_intent,
        "expected_slots": parse_pairs(slot),
        "enabled": enabled,
    }
    created = state(ctx).api.post(REGRESSION, json=body)
    done(ctx, f"regression test {created['id']} added", created)


@regression_app.command("update")
def regression_update(
    ctx: typer.Context,
    test_id: int,
    name: str | None = typer.Option(None, "--name"),
    phrase: str | None = typer.Option(None, "--phrase"),
    expect_intent: str | None = typer.Option(None, "--expect-intent"),
    slot: list[str] | None = typer.Option(None, "--slot", help="Replace expected slots key=value (repeatable)"),
    enabled: bool | None = typer.Option(None, "--enabled/--disabled"),
) -> None:
    """Update a regression test."""
    body = _changes(
        name=name,
        phrase=phrase,
        expected_intent=expect_intent,
        expected_slots=parse_pairs(slot) if slot else None,
        enabled=enabled,
    )
    updated = state(ctx).api.patch(f"{REGRESSION}{test_id}", json=body)
    done(ctx, f"regression test {test_id} updated", updated)


@regression_app.command("remove")
def regression_remove(ctx: typer.Context, test_id: int) -> None:
    """Delete a regression test."""
    state(ctx).api.delete(f"{REGRESSION}{test_id}")
    done(ctx, f"regression test {test_id} removed")


# --- logs ------------------------------------------------------------------------------------


@logs_app.command("list")
def logs_list(
    ctx: typer.Context,
    limit: int = typer.Option(100, "--limit", max=500),
    cursor: str | None = typer.Option(None, "--cursor", help="Opaque continuation token from a previous page"),
    intent: str | None = typer.Option(None, "--intent"),
    matched: bool | None = typer.Option(None, "--matched/--no-match"),
) -> None:
    """Recent recognition logs (newest first)."""
    params = {"limit": limit, "cursor": cursor, "intent_name": intent, "matched": matched}
    page = state(ctx).api.get(LOGS, params={k: v for k, v in params.items() if v is not None})
    render(ctx, page["items"], LOG_COLUMNS, "Recognition logs")
    if page["has_more"] and not state(ctx).json_output:
        console.print(f"[dim]more results: pass --cursor {page['next_cursor']}[/dim]")


@logs_app.command("get")
def logs_get(ctx: typer.Context, log_id: int) -> None:
    """Show one recognition log."""
    render_kv(ctx, state(ctx).api.get(f"{LOGS}{log_id}"))


@logs_app.command("count")
def logs_count(ctx: typer.Context) -> None:
    """Count logs (total / matched / no_match)."""
    render_kv(ctx, state(ctx).api.get(f"{LOGS}count"), "Recognition logs")


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
def perf_summary(ctx: typer.Context, limit: int = typer.Option(100, "--limit", max=1000)) -> None:
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
    cursor: str | None = typer.Option(None, "--cursor", help="Opaque continuation token from a previous page"),
) -> None:
    """Per-recognition performance rows (newest first)."""
    params = {"limit": limit, "cursor": cursor}
    page = state(ctx).api.get(f"{PERF}/history", params={k: v for k, v in params.items() if v is not None})
    render(ctx, page["items"], PERF_COLUMNS, "Performance history")
    if page["has_more"] and not state(ctx).json_output:
        console.print(f"[dim]more results: pass --cursor {page['next_cursor']}[/dim]")
