"""`opal listen ...` CLI. Most commands call the REST API (see OPERATION_COMMANDS for the mapping);
`event publish` is the exception and talks directly to the ZeroMQ event bus."""

from __future__ import annotations

import errno
import os
import sys
import time
import webbrowser

import typer

from opal_listener.cli.catalog_cmds import (
    intents_app,
    personas_app,
    profiles_app,
    settings_app,
    slots_app,
    wakewords_app,
)
from opal_listener.cli.client import ApiClient, api_url_from_env, xpub_url_from_env, xsub_url_from_env
from opal_listener.cli.event_cmds import event_app
from opal_listener.cli.output import CliState, done, state
from opal_listener.cli.reactions_cmds import reactions_app
from opal_listener.cli.review_cmds import failed_app, logs_app, perf_app, regression_app
from opal_listener.cli.service_cmds import config_app, service_app
from opal_listener.cli.test_cmds import pipeline_app, test_app
from opal_listener.cli.tree_cmds import register as register_tree

# REST operation (FastAPI function name) -> CLI command. Checked by the parity test.
OPERATION_COMMANDS: dict[str, str] = {
    "list_wakewords": "wakeword list",
    "create_wakeword": "wakeword add",
    "get_wakeword": "wakeword get",
    "update_wakeword": "wakeword update",
    "delete_wakeword": "wakeword remove",
    "list_intents": "intent list",
    "create_intent": "intent add",
    "get_intent": "intent get",
    "update_intent": "intent update",
    "delete_intent": "intent remove",
    "list_phrases": "intent phrases",
    "add_phrase": "intent add-phrase",
    "delete_phrase": "intent remove-phrase",
    "list_slots": "slot list",
    "create_slot": "slot add",
    "get_slot": "slot get",
    "update_slot": "slot update",
    "delete_slot": "slot remove",
    "list_slot_values": "slot values",
    "add_slot_value": "slot add-value",
    "delete_slot_value": "slot remove-value",
    "list_aliases": "slot aliases",
    "add_alias": "slot add-alias",
    "delete_alias": "slot remove-alias",
    "opal_profile_list": "profile list",
    "opal_profile_create": "profile add",
    "opal_profile_get": "profile get",
    "opal_profile_patch": "profile update",
    "opal_profile_delete": "profile remove",
    "opal_profile_activate": "profile activate",
    "opal_persona_list": "persona list",
    "opal_persona_create": "persona add",
    "opal_persona_get": "persona get",
    "opal_persona_patch": "persona update",
    "opal_persona_delete": "persona remove",
    "opal_persona_validate": "persona validate",
    "opal_setting_list": "setting list",
    "opal_setting_patch": "setting patch",
    "opal_setting_get": "setting get",
    "opal_setting_put": "setting set",
    "opal_setting_delete": "setting unset",
    "test_text": "test text",
    "test_audio": "test audio",
    "start_pipeline": "pipeline start",
    "stop_pipeline": "pipeline stop",
    "get_status": "pipeline status",
    "stream_results": "test listen",
    "list_logs": "log list",
    "count_logs": "log count",
    "get_log": "log get",
    "list_failed_utterances": "failed list",
    "create_failed_utterance": "failed add",
    "get_failed_utterance": "failed get",
    "update_failed_utterance": "failed update",
    "delete_failed_utterance": "failed remove",
    "promote_to_phrase": "failed promote",
    "list_regression_tests": "regression list",
    "create_regression_test": "regression add",
    "get_regression_test": "regression get",
    "update_regression_test": "regression update",
    "delete_regression_test": "regression remove",
    "run_regression_tests": "test regression",
    "get_last_performance": "service perf last",
    "get_performance_summary": "service perf summary",
    "get_performance_history": "service perf history",
    "opal_health_get": "service doctor",
    "get_audio_devices": "service devices",
    "opal_backup_list": "config backups",
    "opal_backup_create": "config backup",
    "opal_backup_download": "config download",
    "opal_backup_restore": "config restore",
    "opal_backup_delete": "config delete-backup",
    "opal_backup_export": "config export",
    "opal_backup_import": "config import",
    "opal_backup_validate_stored": "config validate",
    "opal_backup_validate": "config validate",
    "opal_info_get": "service info",
    "opal_info_ping": "service ping",
    "opal_health_live_get": "service live",
    "opal_health_ready_get": "service ready",
}

app = typer.Typer(help="Opal command line.", no_args_is_help=True, pretty_exceptions_enable=False)
listen_app = typer.Typer(
    help="Wakeword, STT, intent and slot recognition (talks to the opal listener REST API).",
    no_args_is_help=True,
)
app.add_typer(listen_app, name="listen")

service_app.add_typer(perf_app, name="perf")

for sub_app, name in (
    (wakewords_app, "wakeword"),
    (intents_app, "intent"),
    (slots_app, "slot"),
    (profiles_app, "profile"),
    (settings_app, "setting"),
    (personas_app, "persona"),
    (test_app, "test"),
    (pipeline_app, "pipeline"),
    (failed_app, "failed"),
    (regression_app, "regression"),
    (logs_app, "log"),
    (reactions_app, "reaction"),
    (config_app, "config"),
    (service_app, "service"),
    (event_app, "event"),
):
    listen_app.add_typer(sub_app, name=name)

register_tree(listen_app)


@listen_app.command("gui")
def listen_gui(
    ctx: typer.Context,
    print_only: bool = typer.Option(False, "--print", help="Print the URL instead of opening a browser"),
) -> None:
    """Open the web console (SPA) in a browser; starts the local server if it is not running."""
    import urllib.parse

    from opal_listener.cli.service_cmds import launch_server

    api = state(ctx).api
    url = f"{api.base_url}/ui/opal-listen.html"
    try:
        api.get("/api/v1/info/ping")
    except Exception:
        parsed = urllib.parse.urlparse(api.base_url)
        launch_server(parsed.hostname or "127.0.0.1", parsed.port or 8000, None)
        for _ in range(100):  # ~10s for the server to come up
            try:
                api.get("/api/v1/info/ping")
                break
            except Exception:
                time.sleep(0.1)
        else:
            done(ctx, "server did not come up — see logs/opal_listen.log", {"url": url})
            return
    if print_only or not webbrowser.open(url):
        done(ctx, url, {"url": url})
        return
    done(ctx, f"opened {url}", {"url": url})


@listen_app.callback()
def listen_callback(
    ctx: typer.Context,
    api_url: str = typer.Option(None, "--api-url", help="REST API base URL [env: OPAL_LISTEN_API_URL]"),
    xpub: str = typer.Option(None, "--xpub", help="ZeroMQ event publisher URL [env: OPAL_EVENT_XPUB_URL]"),
    xsub: str = typer.Option(None, "--xsub", help="ZeroMQ event subscriber URL [env: OPAL_EVENT_XSUB_URL]"),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON responses"),
    timeout: float = typer.Option(60.0, "--timeout", help="HTTP timeout in seconds"),
) -> None:
    ctx.obj = CliState(
        ApiClient(api_url or api_url_from_env(), timeout),
        json_output,
        xpub_url=xpub or xpub_url_from_env(),
        xsub_url=xsub or xsub_url_from_env(),
    )


def _is_closed_pipe(exc: OSError) -> bool:
    """The reader of our stdout went away (`opal ... | head`). POSIX reports EPIPE; Windows
    reports EINVAL on the flush to a closed pipe. Only count it when stdout is not a TTY so a
    genuine EINVAL elsewhere still surfaces."""
    if isinstance(exc, BrokenPipeError) or exc.errno == errno.EPIPE:
        return True
    return exc.errno == errno.EINVAL and not sys.stdout.isatty()


def run() -> None:
    """Console-script entry point: `app()` plus quiet exit when piped into `head` & co."""
    try:
        app(prog_name="opal")
    except OSError as exc:
        if not _is_closed_pipe(exc):
            raise
        # Point stdout at devnull so the interpreter's final flush doesn't raise again.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        sys.exit(1)


if __name__ == "__main__":
    run()
