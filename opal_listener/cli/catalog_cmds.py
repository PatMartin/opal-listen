"""opal listen wakewords|intents|slots|profiles|settings"""

from __future__ import annotations

from typing import Any

import typer
import yaml

from opal_listener.cli.client import CliError
from opal_listener.cli.output import done, render, render_kv, state

WAKEWORDS = "/api/v1/wakewords/"
INTENTS = "/api/v1/intents/"
SLOTS = "/api/v1/slots/"
PROFILES = "/api/v1/profiles/"
SETTINGS = "/api/v1/settings/"

wakewords_app = typer.Typer(help="Manage wakewords.", no_args_is_help=True)
intents_app = typer.Typer(help="Manage intents and training phrases.", no_args_is_help=True)
slots_app = typer.Typer(help="Manage slots, slot values and aliases.", no_args_is_help=True)
profiles_app = typer.Typer(help="Manage recognition profiles.", no_args_is_help=True)
settings_app = typer.Typer(help="Read and change settings.", no_args_is_help=True)


def parse_pairs(pairs: list[str] | None) -> dict[str, Any]:
    """Parse key=value options; values are YAML scalars (numbers/bools are typed)."""
    result: dict[str, Any] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise CliError(f"expected key=value, got '{pair}'")
        key, raw = pair.split("=", 1)
        result[key.strip()] = yaml.safe_load(raw) if raw.strip() else ""
    return result


def _changes(**values: Any) -> dict[str, Any]:
    changes = {k: v for k, v in values.items() if v is not None}
    if not changes:
        raise CliError("nothing to update (pass at least one option)")
    return changes


# --- wakewords -------------------------------------------------------------------------------

WAKEWORD_COLUMNS = ("id", "name", "enabled", "confidence_threshold", "updated_at")


@wakewords_app.command("list")
def wakewords_list(ctx: typer.Context) -> None:
    """List wakewords."""
    render(ctx, state(ctx).api.get(WAKEWORDS), WAKEWORD_COLUMNS, "Wakewords")


@wakewords_app.command("get")
def wakewords_get(ctx: typer.Context, wakeword: str = typer.Argument(..., help="Name or id")) -> None:
    """Show one wakeword."""
    api = state(ctx).api
    render_kv(ctx, api.get(f"{WAKEWORDS}{api.resolve(WAKEWORDS, wakeword)['id']}"))


@wakewords_app.command("add")
def wakewords_add(
    ctx: typer.Context,
    name: str,
    threshold: float = typer.Option(0.8, "--threshold", min=0.0, max=1.0, help="Confidence threshold"),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
) -> None:
    """Add a wakeword."""
    body = {"name": name, "enabled": enabled, "confidence_threshold": threshold}
    created = state(ctx).api.post(WAKEWORDS, json=body)
    done(ctx, f"wakeword '{created['name']}' added (id {created['id']})", created)


@wakewords_app.command("update")
def wakewords_update(
    ctx: typer.Context,
    wakeword: str = typer.Argument(..., help="Name or id"),
    name: str | None = typer.Option(None, "--name"),
    threshold: float | None = typer.Option(None, "--threshold", min=0.0, max=1.0),
    enabled: bool | None = typer.Option(None, "--enabled/--disabled"),
) -> None:
    """Update a wakeword."""
    api = state(ctx).api
    item = api.resolve(WAKEWORDS, wakeword)
    body = _changes(name=name, confidence_threshold=threshold, enabled=enabled)
    updated = api.patch_with_etag(f"{WAKEWORDS}{item['id']}", json=body)
    done(ctx, f"wakeword '{updated['name']}' updated", updated)


@wakewords_app.command("remove")
def wakewords_remove(ctx: typer.Context, wakeword: str = typer.Argument(..., help="Name or id")) -> None:
    """Remove a wakeword."""
    api = state(ctx).api
    item = api.resolve(WAKEWORDS, wakeword)
    api.delete_with_etag(f"{WAKEWORDS}{item['id']}")
    done(ctx, f"wakeword '{item['name']}' removed")


# --- intents ---------------------------------------------------------------------------------

INTENT_COLUMNS = ("id", "name", "enabled", "confidence_threshold", "phrase_count", "description")


@intents_app.command("list")
def intents_list(ctx: typer.Context) -> None:
    """List intents."""
    items = state(ctx).api.get(INTENTS)
    if not state(ctx).json_output:
        items = [{**i, "phrase_count": len(i["phrases"])} for i in items]
    render(ctx, items, INTENT_COLUMNS, "Intents")


@intents_app.command("get")
def intents_get(ctx: typer.Context, intent: str = typer.Argument(..., help="Name or id")) -> None:
    """Show an intent with its phrases."""
    api = state(ctx).api
    item = api.get(f"{INTENTS}{api.resolve(INTENTS, intent)['id']}")
    if state(ctx).json_output:
        render(ctx, item)
        return
    render_kv(ctx, {k: v for k, v in item.items() if k != "phrases"}, f"Intent {item['name']}")
    render(ctx, item["phrases"], ("id", "phrase"), "Phrases")


@intents_app.command("add")
def intents_add(
    ctx: typer.Context,
    name: str,
    description: str | None = typer.Option(None, "--description"),
    threshold: float = typer.Option(0.7, "--threshold", min=0.0, max=1.0),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
    phrase: list[str] | None = typer.Option(None, "--phrase", help="Training phrase (repeatable)"),
) -> None:
    """Add an intent (optionally with training phrases)."""
    body = {
        "name": name,
        "description": description,
        "enabled": enabled,
        "confidence_threshold": threshold,
        "phrases": phrase or [],
    }
    created = state(ctx).api.post(INTENTS, json=body)
    done(ctx, f"intent '{created['name']}' added with {len(created['phrases'])} phrase(s)", created)


@intents_app.command("update")
def intents_update(
    ctx: typer.Context,
    intent: str = typer.Argument(..., help="Name or id"),
    name: str | None = typer.Option(None, "--name"),
    description: str | None = typer.Option(None, "--description"),
    threshold: float | None = typer.Option(None, "--threshold", min=0.0, max=1.0),
    enabled: bool | None = typer.Option(None, "--enabled/--disabled"),
) -> None:
    """Update an intent."""
    api = state(ctx).api
    item = api.resolve(INTENTS, intent)
    body = _changes(name=name, description=description, confidence_threshold=threshold, enabled=enabled)
    updated = api.patch_with_etag(f"{INTENTS}{item['id']}", json=body)
    done(ctx, f"intent '{updated['name']}' updated", updated)


@intents_app.command("remove")
def intents_remove(ctx: typer.Context, intent: str = typer.Argument(..., help="Name or id")) -> None:
    """Remove an intent and its phrases."""
    api = state(ctx).api
    item = api.resolve(INTENTS, intent)
    api.delete_with_etag(f"{INTENTS}{item['id']}")
    done(ctx, f"intent '{item['name']}' removed")


@intents_app.command("phrases")
def intents_phrases(ctx: typer.Context, intent: str = typer.Argument(..., help="Name or id")) -> None:
    """List training phrases of an intent."""
    api = state(ctx).api
    item = api.resolve(INTENTS, intent)
    render(
        ctx,
        api.get(f"{INTENTS}{item['id']}/phrases"),
        ("id", "phrase", "created_at"),
        f"{item['name']} phrases",
    )


@intents_app.command("add-phrase")
def intents_add_phrase(
    ctx: typer.Context,
    intent: str = typer.Argument(..., help="Name or id"),
    phrase: str = typer.Argument(..., help="e.g. 'turn on [the] {room_name} light'"),
) -> None:
    """Add a training phrase to an intent."""
    api = state(ctx).api
    item = api.resolve(INTENTS, intent)
    created = api.post(f"{INTENTS}{item['id']}/phrases", json={"phrase": phrase})
    done(ctx, f"phrase added to '{item['name']}' (id {created['id']})", created)


@intents_app.command("remove-phrase")
def intents_remove_phrase(
    ctx: typer.Context,
    intent: str = typer.Argument(..., help="Name or id"),
    phrase: str = typer.Argument(..., help="Phrase id or exact text"),
) -> None:
    """Remove a training phrase."""
    api = state(ctx).api
    item = api.resolve(INTENTS, intent)
    target = api.resolve(f"{INTENTS}{item['id']}/phrases", phrase, key="phrase")
    phrases_path = f"{INTENTS}{item['id']}/phrases"
    api.delete_with_etag(f"{phrases_path}/{target['id']}", etag_path=phrases_path)
    done(ctx, f"phrase '{target['phrase']}' removed from '{item['name']}'")


# --- slots -----------------------------------------------------------------------------------


def _value(api: Any, slot: dict[str, Any], value: str) -> dict[str, Any]:
    return api.resolve(f"{SLOTS}{slot['id']}/values", value, key="value")


@slots_app.command("list")
def slots_list(ctx: typer.Context) -> None:
    """List slots with their values."""
    items = state(ctx).api.get(SLOTS)
    if not state(ctx).json_output:
        items = [
            {
                **s,
                "values": ", ".join(
                    v["value"]
                    + (f" ({', '.join(a['alias'] for a in v.get('aliases', []))})" if v.get("aliases") else "")
                    for v in s["values"]
                ),
            }
            for s in items
        ]
    render(ctx, items, ("id", "name", "slot_type", "values", "description"), "Slots")


@slots_app.command("get")
def slots_get(ctx: typer.Context, slot: str = typer.Argument(..., help="Name or id")) -> None:
    """Show a slot."""
    api = state(ctx).api
    render(ctx, api.get(f"{SLOTS}{api.resolve(SLOTS, slot)['id']}"))


@slots_app.command("add")
def slots_add(
    ctx: typer.Context,
    name: str,
    slot_type: str = typer.Option("list", "--type", help="list | number | text"),
    description: str | None = typer.Option(None, "--description"),
    value: list[str] | None = typer.Option(None, "--value", help="Initial value (repeatable)"),
) -> None:
    """Add a slot."""
    api = state(ctx).api
    created = api.post(SLOTS, json={"name": name, "slot_type": slot_type, "description": description})
    for v in value or []:
        api.post(f"{SLOTS}{created['id']}/values", json={"value": v})
    done(ctx, f"slot '{created['name']}' added (id {created['id']})", created)


@slots_app.command("update")
def slots_update(
    ctx: typer.Context,
    slot: str = typer.Argument(..., help="Name or id"),
    name: str | None = typer.Option(None, "--name"),
    slot_type: str | None = typer.Option(None, "--type"),
    description: str | None = typer.Option(None, "--description"),
) -> None:
    """Update a slot."""
    api = state(ctx).api
    item = api.resolve(SLOTS, slot)
    updated = api.patch_with_etag(
        f"{SLOTS}{item['id']}", json=_changes(name=name, slot_type=slot_type, description=description)
    )
    done(ctx, f"slot '{updated['name']}' updated", updated)


@slots_app.command("remove")
def slots_remove(ctx: typer.Context, slot: str = typer.Argument(..., help="Name or id")) -> None:
    """Remove a slot (fails if phrases still use it)."""
    api = state(ctx).api
    item = api.resolve(SLOTS, slot)
    api.delete_with_etag(f"{SLOTS}{item['id']}")
    done(ctx, f"slot '{item['name']}' removed")


@slots_app.command("values")
def slots_values(ctx: typer.Context, slot: str = typer.Argument(..., help="Name or id")) -> None:
    """List the values of a slot with aliases."""
    api = state(ctx).api
    item = api.resolve(SLOTS, slot)
    values = api.get(f"{SLOTS}{item['id']}/values")
    if not state(ctx).json_output:
        values = [{**v, "aliases": ", ".join(a["alias"] for a in v["aliases"])} for v in values]
    render(ctx, values, ("id", "value", "aliases"), f"{item['name']} values")


@slots_app.command("add-value")
def slots_add_value(
    ctx: typer.Context,
    slot: str = typer.Argument(..., help="Name or id"),
    value: str = typer.Argument(..., help="Canonical value"),
    alias: list[str] | None = typer.Option(None, "--alias", help="Alias (repeatable)"),
) -> None:
    """Add a value (and optional aliases) to a slot."""
    api = state(ctx).api
    item = api.resolve(SLOTS, slot)
    created = api.post(f"{SLOTS}{item['id']}/values", json={"value": value})
    for a in alias or []:
        api.post(f"{SLOTS}{item['id']}/values/{created['id']}/aliases", json={"alias": a})
    done(ctx, f"value '{created['value']}' added to '{item['name']}'", created)


@slots_app.command("remove-value")
def slots_remove_value(
    ctx: typer.Context,
    slot: str = typer.Argument(..., help="Name or id"),
    value: str = typer.Argument(..., help="Value or value id"),
) -> None:
    """Remove a slot value."""
    api = state(ctx).api
    item = api.resolve(SLOTS, slot)
    target = _value(api, item, value)
    values_path = f"{SLOTS}{item['id']}/values"
    api.delete_with_etag(f"{values_path}/{target['id']}", etag_path=values_path)
    done(ctx, f"value '{target['value']}' removed from '{item['name']}'")


@slots_app.command("aliases")
def slots_aliases(
    ctx: typer.Context,
    slot: str = typer.Argument(..., help="Name or id"),
    value: str = typer.Argument(..., help="Value or value id"),
) -> None:
    """List aliases of a slot value."""
    api = state(ctx).api
    item = api.resolve(SLOTS, slot)
    target = _value(api, item, value)
    render(ctx, api.get(f"{SLOTS}{item['id']}/values/{target['id']}/aliases"), ("id", "alias"), "Aliases")


@slots_app.command("add-alias")
def slots_add_alias(
    ctx: typer.Context,
    slot: str = typer.Argument(..., help="Name or id"),
    value: str = typer.Argument(..., help="Value or value id"),
    alias: str = typer.Argument(..., help="Alias that normalizes to the value"),
) -> None:
    """Add an alias to a slot value."""
    api = state(ctx).api
    item = api.resolve(SLOTS, slot)
    target = _value(api, item, value)
    created = api.post(f"{SLOTS}{item['id']}/values/{target['id']}/aliases", json={"alias": alias})
    done(ctx, f"alias '{alias}' -> '{target['value']}' added", created)


@slots_app.command("remove-alias")
def slots_remove_alias(
    ctx: typer.Context,
    slot: str = typer.Argument(..., help="Name or id"),
    value: str = typer.Argument(..., help="Value or value id"),
    alias: str = typer.Argument(..., help="Alias or alias id"),
) -> None:
    """Remove an alias."""
    api = state(ctx).api
    item = api.resolve(SLOTS, slot)
    target = _value(api, item, value)
    base = f"{SLOTS}{item['id']}/values/{target['id']}/aliases"
    found = api.resolve(base, alias, key="alias")
    api.delete_with_etag(f"{base}/{found['id']}", etag_path=base)
    done(ctx, f"alias '{found['alias']}' removed")


# --- profiles --------------------------------------------------------------------------------

PROFILE_COLUMNS = (
    "id",
    "name",
    "is_active",
    "wakeword_names",
    "confidence_thresholds",
    "enabled_intents",
    "description",
)


def _profile_body(
    description: str | None,
    wakeword: list[str] | None,
    threshold: list[str] | None,
    vad: list[str] | None,
    intent: list[str] | None,
    slot: list[str] | None,
    model: list[str] | None,
) -> dict[str, Any]:
    return {
        "description": description,
        "wakeword_names": wakeword,
        "confidence_thresholds": parse_pairs(threshold) if threshold else None,
        "vad_settings": parse_pairs(vad) if vad else None,
        "enabled_intents": intent,
        "enabled_slots": slot,
        "model_paths": {k: str(v) for k, v in parse_pairs(model).items()} if model else None,
    }


_WAKEWORD_OPT = typer.Option(None, "--wakeword", help="Wakeword name (repeatable)")
_THRESHOLD_OPT = typer.Option(None, "--threshold", help="wakeword|intent|stt=0.8 (repeatable)")
_VAD_OPT = typer.Option(None, "--vad", help="VAD setting key=value, e.g. silence_end_ms=700 (repeatable)")
_INTENT_OPT = typer.Option(None, "--intent", help="Enabled intent (repeatable; none = all)")
_SLOT_OPT = typer.Option(None, "--slot", help="Enabled slot (repeatable; none = all)")
_MODEL_OPT = typer.Option(None, "--model", help="vosk_model|whisper_model=path (repeatable)")


@profiles_app.command("list")
def profiles_list(ctx: typer.Context) -> None:
    """List profiles."""
    render(ctx, state(ctx).api.get(PROFILES), PROFILE_COLUMNS, "Profiles")


@profiles_app.command("get")
def profiles_get(ctx: typer.Context, profile: str = typer.Argument(..., help="Name or id")) -> None:
    """Show a profile."""
    api = state(ctx).api
    render_kv(ctx, api.get(f"{PROFILES}{api.resolve(PROFILES, profile)['id']}"))


@profiles_app.command("add")
def profiles_add(
    ctx: typer.Context,
    name: str,
    description: str | None = typer.Option(None, "--description"),
    activate: bool = typer.Option(False, "--activate", help="Make this the active profile"),
    wakeword: list[str] | None = _WAKEWORD_OPT,
    threshold: list[str] | None = _THRESHOLD_OPT,
    vad: list[str] | None = _VAD_OPT,
    intent: list[str] | None = _INTENT_OPT,
    slot: list[str] | None = _SLOT_OPT,
    model: list[str] | None = _MODEL_OPT,
) -> None:
    """Add a profile."""
    body = {
        k: v
        for k, v in _profile_body(description, wakeword, threshold, vad, intent, slot, model).items()
        if v is not None
    }
    created = state(ctx).api.post(PROFILES, json={"name": name, "is_active": activate, **body})
    done(ctx, f"profile '{created['name']}' added (id {created['id']})", created)


@profiles_app.command("update")
def profiles_update(
    ctx: typer.Context,
    profile: str = typer.Argument(..., help="Name or id"),
    name: str | None = typer.Option(None, "--name"),
    description: str | None = typer.Option(None, "--description"),
    wakeword: list[str] | None = _WAKEWORD_OPT,
    threshold: list[str] | None = _THRESHOLD_OPT,
    vad: list[str] | None = _VAD_OPT,
    intent: list[str] | None = _INTENT_OPT,
    slot: list[str] | None = _SLOT_OPT,
    model: list[str] | None = _MODEL_OPT,
) -> None:
    """Update a profile (list/map options replace the stored value)."""
    api = state(ctx).api
    item = api.resolve(PROFILES, profile)
    body = _changes(name=name, **_profile_body(description, wakeword, threshold, vad, intent, slot, model))
    updated = api.patch_with_etag(f"{PROFILES}{item['id']}", json=body)
    done(ctx, f"profile '{updated['name']}' updated", updated)


@profiles_app.command("activate")
def profiles_activate(ctx: typer.Context, profile: str = typer.Argument(..., help="Name or id")) -> None:
    """Activate a profile (deactivates all others)."""
    api = state(ctx).api
    item = api.resolve(PROFILES, profile)
    updated = api.post(f"{PROFILES}{item['id']}/activate")
    done(ctx, f"profile '{updated['name']}' is now active", updated)


@profiles_app.command("remove")
def profiles_remove(ctx: typer.Context, profile: str = typer.Argument(..., help="Name or id")) -> None:
    """Remove a profile."""
    api = state(ctx).api
    item = api.resolve(PROFILES, profile)
    api.delete_with_etag(f"{PROFILES}{item['id']}")
    done(ctx, f"profile '{item['name']}' removed")


# --- settings --------------------------------------------------------------------------------


@settings_app.command("list")
def settings_list(ctx: typer.Context) -> None:
    """List all settings."""
    render_kv(ctx, state(ctx).api.get(SETTINGS), "Settings")


@settings_app.command("set")
def settings_set(ctx: typer.Context, key: str, value: str) -> None:
    """Atomically set a setting (validated server-side; fetches the current ETag automatically)."""
    updated = state(ctx).api.patch_with_etag(SETTINGS, json={key: value})
    done(ctx, f"{key} = {updated[key]}", updated)
