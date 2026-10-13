"""opal listen test ... and opal listen pipeline ..."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import typer

from opal_listener.cli.client import CliError
from opal_listener.cli.output import console, done, print_json, render, render_kv, state, summarize_result

RECOGNITION = "/api/v1/recognition"

test_app = typer.Typer(
    help="Test recognition with text, audio files, the microphone or regression tests.", no_args_is_help=True
)
pipeline_app = typer.Typer(help="Control the live microphone pipeline.", no_args_is_help=True)


def _show_result(ctx: typer.Context, result: dict[str, Any], verbose: bool) -> None:
    if state(ctx).json_output or verbose:
        print_json(result if verbose else summarize_result(result))
        return
    summary = summarize_result(result)
    colour = "green" if result.get("matched") else "yellow"
    console.print(f"[{colour}]{'MATCH' if result.get('matched') else 'NO MATCH'}[/{colour}]")
    print_json(summary)


def _check_expectation(result: dict[str, Any], expect_intent: str | None) -> None:
    if expect_intent is not None and result.get("intent_name") != expect_intent:
        raise CliError(f"expected intent '{expect_intent}', got '{result.get('intent_name')}'")


@test_app.command("text")
def test_text(
    ctx: typer.Context,
    text: str = typer.Argument(..., help="Utterance, e.g. 'computer, turn on the kitchen light'"),
    save: bool = typer.Option(True, "--save/--no-save", help="Store in recognition logs"),
    expect_intent: str | None = typer.Option(None, "--expect-intent", help="Exit 1 if the intent differs"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show the full result"),
) -> None:
    """Run text through normalization, intent matching and slot extraction."""
    result = state(ctx).api.post(f"{RECOGNITION}/test_text", json={"text": text}, params={"save_log": save})
    _show_result(ctx, result, verbose)
    _check_expectation(result, expect_intent)


@test_app.command("audio")
def test_audio(
    ctx: typer.Context,
    wav: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="16-bit PCM .wav file"),
    save: bool = typer.Option(True, "--save/--no-save", help="Store in recognition logs"),
    require_wakeword: bool = typer.Option(False, "--require-wakeword", help="no_match unless a wakeword is heard"),
    expect_intent: str | None = typer.Option(None, "--expect-intent", help="Exit 1 if the intent differs"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show the full result"),
) -> None:
    """Run a .wav file through the full wakeword + STT + intent pipeline."""
    with wav.open("rb") as fh:
        result = state(ctx).api.post(
            f"{RECOGNITION}/test_audio",
            files={"file": (wav.name, fh, "audio/wav")},
            params={"save_log": save, "require_wakeword": require_wakeword},
        )
    _show_result(ctx, result, verbose)
    _check_expectation(result, expect_intent)


def _level_bar(dbfs: float, width: int = 30) -> str:
    filled = max(0, min(width, int((dbfs + 90) / 90 * width)))
    return "█" * filled + "·" * (width - filled)


def _stream(ctx: typer.Context, debug: bool, count: int, duration: float, start: bool, verbose: bool) -> None:
    api = state(ctx).api
    started_here = False
    if start:
        started_here = api.post(f"{RECOGNITION}/start").get("status") == "started"
        if not state(ctx).json_output:
            console.print("[dim]listening... say the wakeword (Ctrl-C to stop)[/dim]")
    results = 0
    deadline = time.monotonic() + duration if duration > 0 else None
    params = {"debug": debug}
    try:
        for event, data in api.stream_events(
            f"{RECOGNITION}/stream", params=params, idle_timeout=duration if duration > 0 else None
        ):
            if deadline and time.monotonic() > deadline:
                break
            if event == "result":
                results += 1
                _show_result(ctx, data, verbose)
                if count and results >= count:
                    break
            elif not debug:
                continue
            elif state(ctx).json_output:
                print_json({"event": event, "data": data})
            elif event == "audio_level":
                console.print(f"[dim]{data['state']:>9} {_level_bar(data['dbfs'])} {data['dbfs']:6.1f} dBFS[/dim]")
            elif event == "partial":
                console.print(f"[cyan]… {data['text']}[/cyan]")
            elif event == "status":
                console.print(f"[magenta]status: {data.get('state')} {data.get('last_error') or ''}[/magenta]")
            else:
                console.print(f"[bold]{event}[/bold] {data}")
    except KeyboardInterrupt:
        pass
    finally:
        if started_here:
            api.post(f"{RECOGNITION}/stop")


@test_app.command("listen")
def test_listen(
    ctx: typer.Context,
    count: int = typer.Option(0, "--count", "-n", help="Stop after N results (0 = unlimited)"),
    duration: float = typer.Option(0, "--duration", help="Stop after N seconds (0 = unlimited)"),
    start: bool = typer.Option(True, "--start/--no-start", help="Start the pipeline if it is not running"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show full results"),
) -> None:
    """Listen on the microphone and print recognition results."""
    _stream(ctx, debug=False, count=count, duration=duration, start=start, verbose=verbose)


@test_app.command("listen-debug")
def test_listen_debug(
    ctx: typer.Context,
    count: int = typer.Option(0, "--count", "-n", help="Stop after N results (0 = unlimited)"),
    duration: float = typer.Option(0, "--duration", help="Stop after N seconds (0 = unlimited)"),
    start: bool = typer.Option(True, "--start/--no-start", help="Start the pipeline if it is not running"),
) -> None:
    """Like 'listen' but also shows audio levels, partial transcripts, wakeword and VAD events."""
    _stream(ctx, debug=True, count=count, duration=duration, start=start, verbose=True)


@test_app.command("regression")
def test_regression(
    ctx: typer.Context,
    all_tests: bool = typer.Option(False, "--all", help="Include disabled tests"),
) -> None:
    """Run saved regression tests against the current intent model (exit 1 on failure)."""
    run = state(ctx).api.post("/api/v1/regression/run", params={"enabled_only": not all_tests})
    if state(ctx).json_output:
        print_json(run)
    else:
        render(
            ctx,
            run["results"],
            ("test_id", "name", "phrase", "expected_intent", "actual_intent", "passed", "failure_reason"),
            f"Regression: {run['passed']}/{run['total']} passed",
        )
    if run["failed"]:
        raise CliError(f"{run['failed']} regression test(s) failed")


@pipeline_app.command("start")
def pipeline_start(ctx: typer.Context) -> None:
    """Start the microphone pipeline."""
    result = state(ctx).api.post(f"{RECOGNITION}/start")
    done(ctx, f"pipeline {result['status']}", result)


@pipeline_app.command("stop")
def pipeline_stop(ctx: typer.Context) -> None:
    """Stop the microphone pipeline."""
    result = state(ctx).api.post(f"{RECOGNITION}/stop")
    done(ctx, f"pipeline {result['status']}", result)


@pipeline_app.command("status")
def pipeline_status(ctx: typer.Context) -> None:
    """Show pipeline state and counters."""
    render_kv(ctx, state(ctx).api.get(f"{RECOGNITION}/status"), "Pipeline")
