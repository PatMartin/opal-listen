"""Contract test: the generated OpenAPI must be a compatible superset of opal_listen_v1.4.0.yaml.

Every path, method, operationId, parameter, request body, response status and documented response
header in the contract must exist; every contract schema must exist with the same fields, types,
constraints and required list. The app's declared info.version must equal the contract's.
Additive changes (new optional fields, new endpoints, typed responses instead of free-form objects)
are allowed.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from opal_listener.app_settings import PACKAGE_DIR, AppSettings
from opal_listener.main import create_app

CONTRACT = yaml.safe_load((PACKAGE_DIR.parent / "opal_listen_v1.4.0.yaml").read_text(encoding="utf-8"))
METHODS = ("get", "post", "put", "patch", "delete")
CONSTRAINTS = ("maxLength", "minLength", "maximum", "minimum", "default", "format", "contentMediaType")


def _iter_routes(routes: Any) -> list[Any]:
    """Flatten app.routes; newer FastAPI defers included routers behind lazy wrappers."""
    flat: list[Any] = []
    for route in routes:
        candidates = getattr(route, "effective_candidates", None)
        flat.extend(_iter_routes(candidates()) if callable(candidates) else [route])
    return flat


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    settings = AppSettings(data_dir=tmp_path_factory.mktemp("contract"))
    return create_app(settings).openapi()


def _ref_name(schema: dict[str, Any]) -> str | None:
    ref = schema.get("$ref")
    return ref.rsplit("/", 1)[-1] if ref else None


def _variants(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return schema.get("anyOf", [schema])


def _free_form(schema: dict[str, Any]) -> bool:
    return schema == {} or (schema.get("type") == "object" and "properties" not in schema)


def _compatible(contract: dict[str, Any], actual: dict[str, Any], gen: dict[str, Any]) -> bool:
    """True if every variant allowed by the contract schema is produced/accepted by the actual schema."""
    actual_variants = _variants(actual)
    for want in _variants(contract):
        if _free_form(want) and want.get("type") != "null":
            if not any(_free_form(a) or _ref_name(a) or a.get("type") == "object" for a in actual_variants):
                return False
            continue
        if not any(_variant_ok(want, a, gen) for a in actual_variants):
            return False
    return True


def _variant_ok(want: dict[str, Any], got: dict[str, Any], gen: dict[str, Any]) -> bool:
    want_ref, got_ref = _ref_name(want), _ref_name(got)
    if want_ref or got_ref:
        if want_ref == got_ref:
            return True
        if want_ref and got_ref:  # a subclass with extra fields is compatible
            want_props = set(CONTRACT["components"]["schemas"][want_ref].get("properties", {}))
            got_props = set(gen["components"]["schemas"][got_ref].get("properties", {}))
            return want_props <= got_props
        return False
    if want.get("type") != got.get("type"):
        return False
    if want.get("type") == "array":
        return _compatible(want.get("items", {}), got.get("items", {}), gen)
    if want.get("type") == "object" and "additionalProperties" in want:
        wap, gap = want["additionalProperties"], got.get("additionalProperties", True)
        if isinstance(wap, dict) and isinstance(gap, dict):
            return _compatible(wap, gap, gen)
    return True


def _operations() -> list[tuple[str, str]]:
    return [(p, m) for p, item in CONTRACT["paths"].items() for m in METHODS if m in item]


@pytest.mark.parametrize(("path", "method"), _operations(), ids=lambda v: str(v))
def test_operation_matches_contract(generated: dict[str, Any], path: str, method: str) -> None:
    want = CONTRACT["paths"][path][method]
    assert path in generated["paths"], f"missing path {path}"
    got = generated["paths"][path].get(method)
    assert got is not None, f"missing {method.upper()} {path}"
    assert got["operationId"] == want["operationId"]
    assert got.get("tags") == want.get("tags")

    got_params = {(p["name"], p["in"]): p for p in got.get("parameters", [])}
    for param in want.get("parameters", []):
        key = (param["name"], param["in"])
        assert key in got_params, f"missing parameter {key}"
        actual = got_params[key]
        assert actual.get("required", False) == param.get("required", False), key
        assert _compatible(param["schema"], actual["schema"], generated), key
        for constraint in CONSTRAINTS:
            if constraint in param["schema"]:
                assert actual["schema"].get(constraint) == param["schema"][constraint], (key, constraint)
    for key, actual in got_params.items():
        if key not in {(p["name"], p["in"]) for p in want.get("parameters", [])}:
            assert not actual.get("required"), f"new parameter {key} must be optional"

    if "requestBody" in want:
        assert "requestBody" in got, "missing request body"
        assert got["requestBody"].get("required", False) == want["requestBody"].get("required", False)
        for media, content in want["requestBody"]["content"].items():
            assert media in got["requestBody"]["content"], media
            assert _compatible(content["schema"], got["requestBody"]["content"][media]["schema"], generated)
    elif "requestBody" in got:
        assert not got["requestBody"].get("required"), "new request bodies must be optional"

    for status, response in want["responses"].items():
        assert status in got["responses"], f"missing response {status}"
        want_schema = response.get("content", {}).get("application/json", {}).get("schema")
        if want_schema is None or want_schema == {}:
            continue
        got_content = got["responses"][status].get("content", {})
        assert "application/json" in got_content, status
        assert _compatible(want_schema, got_content["application/json"]["schema"], generated), status


@pytest.mark.parametrize("name", sorted(CONTRACT["components"]["schemas"]))
def test_schema_matches_contract(generated: dict[str, Any], name: str) -> None:
    want = CONTRACT["components"]["schemas"][name]
    schemas = generated["components"]["schemas"]
    assert name in schemas, f"missing schema {name}"
    got = schemas[name]
    want_props, got_props = want.get("properties", {}), got.get("properties", {})
    for prop, spec in want_props.items():
        assert prop in got_props, f"{name}.{prop} missing"
        assert _compatible(spec, got_props[prop], generated), f"{name}.{prop} type changed"
        for constraint in CONSTRAINTS:
            if constraint in spec:
                assert got_props[prop].get(constraint) == spec[constraint], f"{name}.{prop}.{constraint}"
    new_props = set(got_props) - set(want_props)
    assert set(want.get("required", [])) == set(got.get("required", [])) - new_props, f"{name}: required differs"
    assert not (set(got.get("required", [])) & new_props), f"{name}: new fields must be optional"


def test_every_operation_has_a_cli_command() -> None:
    from opal_listener.cli.main import OPERATION_COMMANDS

    docs = {"openapi", "swagger_ui_html", "swagger_ui_redirect", "redoc_html"}
    operations = {r.name for r in _iter_routes(create_app(AppSettings()).routes) if hasattr(r, "methods")} - docs
    missing = sorted(operations - set(OPERATION_COMMANDS))
    assert not missing, f"REST operations without CLI command: {missing}"
    assert not sorted(set(OPERATION_COMMANDS) - operations), "stale CLI mapping entries"


def test_web_ui_parity_table_matches_api_and_cli(generated: dict[str, Any]) -> None:
    import re

    from opal_listener.cli.main import OPERATION_COMMANDS

    html = (PACKAGE_DIR / "view" / "static" / "opal-listen.html").read_text(encoding="utf-8")
    rows = re.findall(r'\["([a-z_]+)", "(GET|POST|PUT|PATCH|DELETE)", "([^"]+)", "([^"]+)", "(\w+)"\]', html)
    ui = {name: (method, path, cli) for name, method, path, cli, _view in rows}
    api = {}
    for path, item in generated["paths"].items():
        for method, operation in item.items():
            suffix = re.sub(r"\W", "_", path) + f"_{method}"
            api[operation["operationId"].removesuffix(suffix)] = (method.upper(), path)
    assert {n: (m, p) for n, (m, p, _) in ui.items()} == api
    for name, (_m, _p, cli) in ui.items():
        assert OPERATION_COMMANDS[name] in cli, f"{name}: UI says '{cli}', CLI says '{OPERATION_COMMANDS[name]}'"


def test_info_version_matches_contract(generated: dict[str, Any]) -> None:
    """The FastAPI-declared info.version is the REST contract version; it must track this file."""
    assert generated["info"]["version"] == CONTRACT["info"]["version"]


# Headers the OPAL standard manages: stamped at runtime and documented on contract responses.
_MANAGED_HEADERS = {
    "ETag",
    "Location",
    "X-Opal-Session-Id",
    "X-Opal-Transaction-Id",
    "X-Opal-Request-Id",
    "X-Opal-Causation-Id",
}


def _response_headers(response: dict[str, Any], source: dict[str, Any]) -> set[str]:
    """Header names documented on a response, resolving $ref'd component responses."""
    if "$ref" in response:
        response = source["components"]["responses"][_ref_name(response)]
    return set(response.get("headers", {}))


@pytest.mark.parametrize(("path", "method"), _operations(), ids=lambda v: str(v))
def test_documented_response_headers(generated: dict[str, Any], path: str, method: str) -> None:
    """Contract-documented response headers must be documented by the generated spec, and the
    generated spec must not document managed headers the contract does not."""
    want_op = CONTRACT["paths"][path][method]
    got_op = generated["paths"][path][method]
    for status, want_response in want_op["responses"].items():
        want = _response_headers(want_response, CONTRACT)
        got = _response_headers(got_op["responses"].get(status, {}), generated)
        assert want <= got, f"{method.upper()} {path} {status}: undocumented headers {want - got}"
        extra = (got & _MANAGED_HEADERS) - want
        assert not extra, f"{method.upper()} {path} {status}: documents headers the contract lacks {extra}"


def test_cli_command_map_resolves_to_real_commands() -> None:
    """Every OPERATION_COMMANDS value must resolve to a real command in the `opal listen` CLI tree,
    so a renamed or mistyped command cannot leave a dead REST-to-CLI mapping."""
    from typer.main import get_command

    from opal_listener.cli.main import OPERATION_COMMANDS, app

    listen = get_command(app).commands["listen"]
    unresolved: dict[str, str] = {}
    for operation, command_path in OPERATION_COMMANDS.items():
        node: Any = listen
        for part in command_path.split():
            node = getattr(node, "commands", {}).get(part)
            if node is None:
                unresolved[operation] = command_path
                break
    assert not unresolved, f"OPERATION_COMMANDS values that resolve to no command: {unresolved}"


def test_opal_correlation_headers_stamped_at_runtime(client: TestClient) -> None:
    """Behavioral check for the documented OPAL headers: transaction/request ids are always
    returned (generated when absent), session/causation are echoed only when supplied, and
    malformed ids are rejected with a 422 OpalError — on success and error responses alike."""
    response = client.get("/api/v1/wakewords/")
    assert response.status_code == 200
    assert response.headers["X-Opal-Transaction-Id"]
    assert response.headers["X-Opal-Request-Id"]
    assert response.headers["ETag"]
    assert "X-Opal-Session-Id" not in response.headers
    assert "X-Opal-Causation-Id" not in response.headers

    context = {
        "X-Opal-Session-Id": "sess-1",
        "X-Opal-Transaction-Id": "tx-1",
        "X-Opal-Request-Id": "req-1",
        "X-Opal-Causation-Id": "cause-1",
    }
    echoed = client.get("/api/v1/wakewords/", headers=context)
    for name, value in context.items():
        assert echoed.headers[name] == value

    not_found = client.get("/api/v1/wakewords/999999")
    assert not_found.status_code == 404
    assert not_found.json()["error"]["code"] == "not_found"
    assert not_found.headers["X-Opal-Transaction-Id"]
    assert not_found.headers["X-Opal-Request-Id"]

    invalid = client.get("/api/v1/wakewords/", headers={"X-Opal-Transaction-Id": "not opaque"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_header"
    assert invalid.headers["X-Opal-Transaction-Id"]
    assert invalid.headers["X-Opal-Request-Id"]
