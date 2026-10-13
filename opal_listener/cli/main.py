"""`opal listen ...` CLI. Most commands call the REST API (see OPERATION_COMMANDS for the mapping);
`event publish` is the exception and talks directly to the ZeroMQ event bus."""

from __future__ import annotations

import typer

from opal_listener.cli.catalog_cmds import intents_app, profiles_app, settings_app, slots_app, wakewords_app
from opal_listener.cli.client import ApiClient, api_url_from_env, xpub_url_from_env, xsub_url_from_env
from opal_listener.cli.event_cmds import event_app
from opal_listener.cli.output import CliState
from opal_listener.cli.review_cmds import failed_app, logs_app, perf_app, regression_app
from opal_listener.cli.system_cmds import config_app, system_app
from opal_listener.cli.test_cmds import pipeline_app, test_app

# REST operation (FastAPI function name) -> CLI command. Checked by the parity test.
OPERATION_COMMANDS: dict[str, str] = {
    "list_wakewords": "wakewords list",
    "create_wakeword": "wakewords add",
    "get_wakeword": "wakewords get",
    "update_wakeword": "wakewords update",
    "delete_wakeword": "wakewords remove",
    "list_intents": "intents list",
    "create_intent": "intents add",
    "get_intent": "intents get",
    "update_intent": "intents update",
    "delete_intent": "intents remove",
    "list_phrases": "intents phrases",
    "add_phrase": "intents add-phrase",
    "delete_phrase": "intents remove-phrase",
    "list_slots": "slots list",
    "create_slot": "slots add",
    "get_slot": "slots get",
    "update_slot": "slots update",
    "delete_slot": "slots remove",
    "list_slot_values": "slots values",
    "add_slot_value": "slots add-value",
    "delete_slot_value": "slots remove-value",
    "list_aliases": "slots aliases",
    "add_alias": "slots add-alias",
    "delete_alias": "slots remove-alias",
    "list_profiles": "profiles list",
    "create_profile": "profiles add",
    "get_profile": "profiles get",
    "update_profile": "profiles update",
    "delete_profile": "profiles remove",
    "activate_profile": "profiles activate",
    "get_all_settings": "settings list",
    "patch_listen_settings": "settings set",
    "test_text": "test text",
    "test_audio": "test audio",
    "start_pipeline": "pipeline start",
    "stop_pipeline": "pipeline stop",
    "get_status": "pipeline status",
    "stream_results": "test listen",
    "list_logs": "logs list",
    "count_logs": "logs count",
    "get_log": "logs get",
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
    "get_last_performance": "perf last",
    "get_performance_summary": "perf summary",
    "get_performance_history": "perf history",
    "health_check": "system doctor",
    "get_audio_devices": "system devices",
    "list_backups": "config backups",
    "create_backup": "config backup",
    "download_backup": "config download",
    "restore_backup": "config restore",
    "delete_backup": "config delete-backup",
    "export_config": "config export",
    "import_config": "config import",
    "validate_config": "config validate",
    "validate_listen_backup_payload": "config validate",
    "root": "system info",
    "healthz": "system ping",
    "get_listen_health_live": "system live",
    "get_listen_health_ready": "system ready",
}

app = typer.Typer(help="Opal command line.", no_args_is_help=True, pretty_exceptions_enable=False)
listen_app = typer.Typer(
    help="Wakeword, STT, intent and slot recognition (talks to the opal listener REST API).",
    no_args_is_help=True,
)
app.add_typer(listen_app, name="listen")

for sub_app, name in (
    (wakewords_app, "wakewords"),
    (intents_app, "intents"),
    (slots_app, "slots"),
    (profiles_app, "profiles"),
    (settings_app, "settings"),
    (test_app, "test"),
    (pipeline_app, "pipeline"),
    (failed_app, "failed"),
    (regression_app, "regression"),
    (logs_app, "logs"),
    (perf_app, "perf"),
    (config_app, "config"),
    (system_app, "system"),
    (event_app, "event"),
):
    listen_app.add_typer(sub_app, name=name)


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


if __name__ == "__main__":
    app()
