"""Contract test: the generated OpenAPI must be a compatible superset of the service contract.

Every path, method, operationId, parameter, request body, response status and documented response
header in the contract must exist; every contract schema must exist with the same fields, types,
constraints and required list. The app's declared info.version must equal the contract's.
Additive changes (new optional fields, new endpoints, typed responses instead of free-form objects)
are allowed.

Contracts may mount shared-kernel path items and reference external components through relative
``$ref``s (``openapi/opal_listen_v*.yaml`` + ``openapi/shared/opal_kernel.yaml``). The loader below
resolves those into a single in-memory document before any assertion runs.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from opal_listener.app_settings import PACKAGE_DIR, AppSettings
from opal_listener.main import create_app

CONTRACT_PATH = PACKAGE_DIR.parent / "openapi" / "opal_listen_v1.7.1.yaml"
METHODS = ("get", "post", "put", "patch", "delete")
CONSTRAINTS = ("maxLength", "minLength", "maximum", "minimum", "default", "format", "contentMediaType")


def _ref_name(schema: dict[str, Any]) -> str | None:
    ref = schema.get("$ref")
    return ref.rsplit("/", 1)[-1] if ref else None


def _walk(node: Any) -> Any:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _resolve_contract(path: Path) -> tuple[dict[str, Any], set[str]]:
    """Load a contract YAML, inlining external ``$ref``s (e.g. the shared OPAL kernel).

    Returns (resolved_contract, kernel_schema_names). A self-contained contract is returned
    unchanged with an empty kernel set.
    """
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    external_refs = {
        node["$ref"] for node in _walk(doc) if isinstance(node.get("$ref"), str) and not node["$ref"].startswith("#")
    }
    if not external_refs:
        return doc, set()

    sources: dict[str, dict[str, Any]] = {}
    for ref in external_refs:
        filename = ref.split("#", 1)[0]
        source_path = (path.parent / filename).resolve()
        sources[filename] = yaml.safe_load(source_path.read_text(encoding="utf-8"))

    def lookup(ref: str) -> Any:
        filename, fragment = ref.split("#", 1)
        node: Any = sources[filename]
        for part in fragment.strip("/").split("/"):
            node = node[part]
        return node

    # Resolve path-level mounts ({"$ref": "...#/components/pathItems/<name>"}).
    for route, item in doc["paths"].items():
        if isinstance(item, dict) and "$ref" in item:
            resolved = copy.deepcopy(lookup(item["$ref"]))
            resolved.update({k: v for k, v in item.items() if k != "$ref"})
            doc["paths"][route] = resolved

    # Rewrite remaining external refs to internal ones; components get merged next.
    for node in _walk(doc):
        ref = node.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#"):
            node["$ref"] = "#" + ref.split("#", 1)[1]

    # Merge every referenced external component into the contract (transitively).
    kernel_schemas: set[str] = set()
    pending = True
    while pending:
        pending = False
        refs = {
            node["$ref"]
            for node in _walk(doc)
            if isinstance(node.get("$ref"), str) and node["$ref"].startswith("#/components/")
        }
        for ref in refs:
            section, name = ref.split("/")[2:4]
            target = doc.setdefault("components", {}).setdefault(section, {})
            if name in target:
                continue
            for source in sources.values():
                candidate = source.get("components", {}).get(section, {}).get(name)
                if candidate is not None:
                    target[name] = copy.deepcopy(candidate)
                    if section == "schemas":
                        kernel_schemas.add(name)
                    pending = True
                    break
            else:
                raise KeyError(f"unresolvable contract reference {ref}")

    def resolve_param(param: Any) -> Any:
        name = _ref_name(param) if isinstance(param, dict) else None
        resolved = copy.deepcopy(doc["components"]["parameters"][name]) if name else param
        # Component parameters may themselves be refs (v1.7.1: PersonaId -> OpalPersonaIdParameter).
        return resolve_param(resolved) if _ref_name(resolved) else resolved

    # Dereference parameter and response entries so op-level assertions see real objects.
    for item in doc["paths"].values():
        if not isinstance(item, dict):
            continue
        item["parameters"] = [resolve_param(p) for p in item.get("parameters", [])]
        for method in METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            op["parameters"] = [resolve_param(p) for p in op.get("parameters", [])]
            for status, response in list(op.get("responses", {}).items()):
                if isinstance(response, dict) and _ref_name(response):
                    op["responses"][status] = copy.deepcopy(doc["components"]["responses"][_ref_name(response)])

    # Hoist pathItem-level parameters into each operation (op-level params win).
    for item in doc["paths"].values():
        if not isinstance(item, dict):
            continue
        shared = item.pop("parameters", [])
        if not shared:
            continue
        for method in METHODS:
            op = item.get(method)
            if isinstance(op, dict):
                own = {(p["name"], p["in"]) for p in op.get("parameters", [])}
                op["parameters"] = [p for p in shared if (p["name"], p["in"]) not in own] + op.get("parameters", [])
    return doc, kernel_schemas


CONTRACT, KERNEL_SCHEMAS = _resolve_contract(CONTRACT_PATH)


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
            want_schema = CONTRACT["components"]["schemas"][want_ref]
            got_props = set(gen["components"]["schemas"][got_ref].get("properties", {}))
            if want_ref in KERNEL_SCHEMAS:
                # Kernel schemas are open unions: only their required props are normative.
                return set(want_schema.get("required", [])) <= got_props
            return set(want_schema.get("properties", {})) <= got_props
        return False
    if "type" not in want and "anyOf" not in want and "enum" not in want:
        # Contract prop with no type constraint (e.g. opaque id, native JSON value): anything goes.
        return True
    if want.get("type") != got.get("type"):
        return False
    if want.get("type") == "array":
        return _compatible(want.get("items", {}), got.get("items", {}), gen)
    if want.get("type") == "object" and "additionalProperties" in want:
        wap, gap = want["additionalProperties"], got.get("additionalProperties", True)
        if isinstance(wap, dict) and isinstance(gap, dict):
            return _compatible(wap, gap, gen)
    return True


def _constraint_ok(spec: dict[str, Any], got: dict[str, Any], constraint: str) -> bool:
    """Compare a JSON-Schema constraint (minLength, minimum, ...) between contract and generated
    prop/param schema. Nullable fields emit the constraint on their same-type anyOf variant."""
    if got.get(constraint) is not None:
        return got[constraint] == spec[constraint]
    want_type = spec.get("type")
    return any(v.get("type") == want_type and v.get(constraint) == spec[constraint] for v in got.get("anyOf", []))


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
                assert _constraint_ok(param["schema"], actual["schema"], constraint), (key, constraint)
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
    want_required = set(want.get("required", []))
    open_union = name in KERNEL_SCHEMAS  # kernel schemas declare optional props services may omit
    for prop, spec in want_props.items():
        if prop not in got_props:
            assert open_union and prop not in want_required, f"{name}.{prop} missing"
            continue
        assert _compatible(spec, got_props[prop], generated), f"{name}.{prop} type changed"
        for constraint in CONSTRAINTS:
            if constraint in spec:
                assert _constraint_ok(spec, got_props[prop], constraint), f"{name}.{prop}.{constraint}"
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
            # v1.6.0 froze operationIds at their old (plural) path-derived names, so the id no
            # longer ends with the current path's suffix; strip the generated `_api_v1_*` tail.
            name = re.sub(r"_api_v1_.*$", "", operation["operationId"].removesuffix(suffix))
            api[name] = (method.upper(), path)
    assert {n: (m, p) for n, (m, p, _) in ui.items()} == api
    for name, (_m, _p, cli) in ui.items():
        assert OPERATION_COMMANDS[name] in cli, f"{name}: UI says '{cli}', CLI says '{OPERATION_COMMANDS[name]}'"


def test_info_version_matches_contract(generated: dict[str, Any]) -> None:
    """The FastAPI-declared info.version is the REST contract version; it must track this file."""
    assert generated["info"]["version"] == CONTRACT["info"]["version"]


def test_security_metadata_matches_contract(generated: dict[str, Any]) -> None:
    """When the contract declares global security, the generated spec must declare every contract
    security scheme and an equivalent requirement — globally or on every contract operation."""
    want_security = CONTRACT.get("security")
    if not want_security:
        return
    for name in CONTRACT.get("components", {}).get("securitySchemes", {}):
        assert name in generated.get("components", {}).get("securitySchemes", {}), f"missing security scheme {name}"
    if generated.get("security") != want_security:
        for path, method in _operations():
            op = generated["paths"][path][method]
            assert op.get("security") == want_security, (
                f"{method.upper()} {path} lacks the contract security requirement"
            )


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


def _normalize_ui_path(path: str) -> str:
    """Fold SPA path templates to a contract-comparable shape: `{id}` and `${expr}` both -> `{}`."""
    import re

    path = path.split("?")[0]  # strip query templates (e.g. the EventSource debug/token params)
    return re.sub(r"\$\{[^}]*\}|\{[^}]*\}", "{}", path)


def _ui_call_paths(html: str) -> set[str]:
    """Every path literal the SPA can actually invoke (api/apiWithEtag/fetch/EventSource)."""
    import re

    paths = {m.group(1) for m in re.finditer(r'api(?:WithEtag)?\(\s*"[A-Z]+",\s*["`](/[^`"]+)', html)}
    paths |= {m.group(1) for m in re.finditer(r'(?:fetch|EventSource)\(\s*[`"](?:\$\{S\.base\})?(/[^`"]+)', html)}
    return {_normalize_ui_path(p) for p in paths}


def test_web_ui_exercises_every_contract_path() -> None:
    """Every contract path must be reachable from a real SPA call — a parity-table row alone is not
    an action (regression: opal_setting_get and opal_backup_download had rows but no call sites)."""
    html = (PACKAGE_DIR / "view" / "static" / "opal-listen.html").read_text(encoding="utf-8")
    called = _ui_call_paths(html)
    missing = {_normalize_ui_path(p) for p in CONTRACT["paths"]} - called
    assert not missing, f"contract paths with no SPA call site: {sorted(missing)}"


def _js_call_region(text: str, start: int) -> str:
    """Return the source region of the parenthesized call starting at `start` (index of `(`).

    Balanced-scan that skips string/template contents, so inner `()`/`{}` don't end the call.
    """
    depth = 0
    i = start
    quote: str | None = None
    while i < len(text):
        c = text[i]
        if quote is not None:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    return text[start:]


def test_web_ui_query_params_match_contract() -> None:
    """Query keys the SPA sends must be declared contract query params — undeclared params are
    silently dropped by FastAPI (regression: the SPA sent `offset` to cursor-paginated endpoints)."""
    import re

    html = (PACKAGE_DIR / "view" / "static" / "opal-listen.html").read_text(encoding="utf-8")
    contract_query: dict[tuple[str, str], set[str]] = {}
    for path, item in CONTRACT["paths"].items():
        for method, op in item.items():
            if method in ("get", "post", "put", "patch", "delete"):
                names = {p["name"] for p in op.get("parameters") or [] if p.get("in") == "query"}
                contract_query[(method.upper(), _normalize_ui_path(path))] = names
    violations: dict[str, set[str]] = {}
    for m in re.finditer(r'(?:api|apiWithEtag)\(\s*"([A-Z]+)",\s*["`](/[^`"]+)', html):
        method, path = m.group(1), _normalize_ui_path(m.group(2))
        if (method, path) not in contract_query:
            continue
        call = _js_call_region(html, m.start() + m.group(0).index("(", 3))
        qm = re.search(r"query:\s*\{([^}]*)\}", call)
        if not qm:
            continue
        sent = {k for k in re.findall(r"(\w+)\s*:", qm.group(1))} - contract_query[(method, path)]
        if sent:
            violations[f"{method} {path}"] = sent
    assert not violations, f"SPA sends query params the contract does not declare: {violations}"


def test_opal_correlation_headers_stamped_at_runtime(client: TestClient) -> None:
    """Behavioral check for the documented OPAL headers: transaction/request ids are always
    returned (generated when absent), session/causation are echoed only when supplied, and
    malformed ids are rejected with a 422 OpalError — on success and error responses alike."""
    response = client.get("/api/v1/wakeword/")
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
    echoed = client.get("/api/v1/wakeword/", headers=context)
    for name, value in context.items():
        assert echoed.headers[name] == value

    not_found = client.get("/api/v1/wakeword/999999")
    assert not_found.status_code == 404
    assert not_found.json()["error"]["code"] == "not_found"
    assert not_found.headers["X-Opal-Transaction-Id"]
    assert not_found.headers["X-Opal-Request-Id"]

    invalid = client.get("/api/v1/wakeword/", headers={"X-Opal-Transaction-Id": "not opaque"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_header"
    assert invalid.headers["X-Opal-Transaction-Id"]
    assert invalid.headers["X-Opal-Request-Id"]
