"""Pass 2c: Extract API routes from route/controller/view files.

Python is read with `ast`, so a decorator is only reported as an endpoint when
it really is one. The previous regex matched any `.get(`/`.post(` call, which
turned `requests.get(url)` and `config.get("key")` into endpoints, and turned
`@api_view(["GET", "POST"])` into an endpoint whose *path* was the string
`"GET", "POST"`.

JavaScript and TypeScript have no parser here; they get a deliberately narrow
regex over `app.<method>("literal")` / `router.<method>("literal")`, which
misses computed paths and chained `.route()` builders. Those misses are
reported.
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

from extractor.models import Confidence, ExtractedRoute

logger = logging.getLogger(__name__)

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")

# Path segments that name a resource in neither FastAPI nor Flask.
NON_ENTITY_SEGMENTS = {"api", "v1", "v2", "v3", "rest", "internal", "public", "admin"}

_JS_ROUTE = re.compile(
    r"\b(?:app|router)\s*\.\s*(?P<method>get|post|put|patch|delete|head|options)\s*\(\s*"
    r"(?P<quote>['\"`])(?P<path>[^'\"`]+)(?P=quote)"
)

_JS_DYNAMIC = re.compile(r"\b(?:app|router)\s*\.\s*(?:get|post|put|patch|delete)\s*\(\s*[^'\"`\s)]")


def analyze_routes(
    file_paths: list[str],
    root: Path,
    *,
    warnings: list[str] | None = None,
) -> list[ExtractedRoute]:
    """Extract API endpoints from classified route files."""
    notes = warnings if warnings is not None else []
    routes: list[ExtractedRoute] = []

    for rel in file_paths:
        try:
            source = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            notes.append(f"{rel}: skipped, cannot read ({e.strerror or e})")
            continue

        if rel.endswith(".py"):
            try:
                tree = ast.parse(source, filename=rel)
            except (SyntaxError, ValueError, RecursionError) as e:
                notes.append(f"{rel}: skipped, not parseable as Python ({e})")
                continue
            routes.extend(_python_routes(tree, rel, notes))
        else:
            routes.extend(_js_routes(source, rel, notes))

    return _dedupe(routes)


def _dedupe(routes: list[ExtractedRoute]) -> list[ExtractedRoute]:
    seen: set[tuple[str, str]] = set()
    unique = []
    for route in routes:
        key = (route.method, route.path)
        if key not in seen:
            seen.add(key)
            unique.append(route)
    return unique


def _python_routes(tree: ast.Module, rel: str, notes: list[str]) -> list[ExtractedRoute]:
    prefixes = _router_prefixes(tree)
    routes: list[ExtractedRoute] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func

            if isinstance(func, ast.Name) and func.id == "api_view":
                notes.append(
                    f"{rel}: @api_view on {node.name} declares methods but not a path; "
                    f"Django URL conf is not read, so this endpoint is not in the report"
                )
                continue

            if not isinstance(func, ast.Attribute):
                continue

            receiver = _receiver_name(func.value)
            prefix = prefixes.get(receiver, "")

            if func.attr in HTTP_METHODS:
                path = _first_string(decorator)
                if path is None:
                    notes.append(
                        f"{rel}: {receiver}.{func.attr} on {node.name} has no literal path"
                    )
                    continue
                routes.append(
                    _route(prefix + path, func.attr.upper(), rel, _summary(decorator, node))
                )
            elif func.attr in ("route", "add_url_rule"):
                path = _first_string(decorator)
                if path is None:
                    notes.append(f"{rel}: {receiver}.route on {node.name} has no literal path")
                    continue
                for method in _flask_methods(decorator):
                    routes.append(_route(prefix + path, method, rel, _summary(decorator, node)))

    return routes


def _receiver_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _router_prefixes(tree: ast.Module) -> dict[str, str]:
    """Map router variable name to its declared path prefix."""
    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        factory = node.value.func
        factory_name = factory.attr if isinstance(factory, ast.Attribute) else getattr(
            factory, "id", ""
        )
        if factory_name not in ("APIRouter", "Blueprint", "FastAPI", "Flask", "Router"):
            continue
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg in ("prefix", "url_prefix") and isinstance(kw.value, ast.Constant):
                if isinstance(kw.value.value, str):
                    prefix = kw.value.value.rstrip("/")
        for target in node.targets:
            if isinstance(target, ast.Name):
                prefixes[target.id] = prefix
    return prefixes


def _first_string(call: ast.Call) -> str | None:
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    for kw in call.keywords:
        if kw.arg in ("path", "rule") and isinstance(kw.value, ast.Constant):
            if isinstance(kw.value.value, str):
                return kw.value.value
    return None


def _flask_methods(call: ast.Call) -> list[str]:
    for kw in call.keywords:
        if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
            found = [
                e.value.upper()
                for e in kw.value.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
            if found:
                return found
    return ["GET"]


def _summary(call: ast.Call, func: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    for kw in call.keywords:
        if kw.arg == "summary" and isinstance(kw.value, ast.Constant):
            if isinstance(kw.value.value, str):
                return kw.value.value
    doc = ast.get_docstring(func) or ""
    return doc.strip().splitlines()[0].strip() if doc.strip() else ""


def _js_routes(source: str, rel: str, notes: list[str]) -> list[ExtractedRoute]:
    routes = [
        _route(m.group("path"), m.group("method").upper(), rel, "", Confidence.MEDIUM)
        for m in _JS_ROUTE.finditer(source)
    ]
    if _JS_DYNAMIC.search(source):
        notes.append(
            f"{rel}: at least one route is registered with a computed path; "
            f"only string-literal paths are extracted from JavaScript"
        )
    return routes


def _route(
    path: str,
    method: str,
    source_file: str,
    summary: str,
    confidence: Confidence = Confidence.HIGH,
) -> ExtractedRoute:
    normalized = "/" + path.strip("/") if path.strip("/") else "/"
    return ExtractedRoute(
        path=normalized,
        method=method,
        entity_name=_entity_from_path(normalized),
        source_file=source_file,
        summary=summary,
        confidence=confidence,
    )


def _entity_from_path(path: str) -> str:
    """Name the resource a path addresses, or nothing if it does not name one.

    Returning `""` matters: the old version returned the literal `"{record_id}"`
    for `/{record_id}`, and that string went on to become an entity name.
    """
    for segment in path.strip("/").split("/"):
        if not segment or segment.startswith(("{", ":", "<")):
            continue
        if segment.lower() in NON_ENTITY_SEGMENTS:
            continue
        if not re.fullmatch(r"[A-Za-z][\w-]*", segment):
            continue
        return _singularize(segment.replace("-", "_"))
    return ""


def _singularize(name: str) -> str:
    if name.endswith("ies") and len(name) > 3:
        return name[:-3] + "y"
    if name.endswith(("ses", "xes", "zes", "ches", "shes")):
        return name[:-2]
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name
