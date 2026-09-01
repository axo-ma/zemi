"""Strict Arsenal endpoint configuration and legacy normalization."""

from __future__ import annotations

from copy import deepcopy
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

KINDS = {"managed", "external"}
PROTOCOLS = {"openai", "anthropic"}
HEALTHCHECKS = {"none", "tcp", "models"}
_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value.strip()


def _positive(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{path} must be greater than zero")
    return float(value)


def _expand(value: str, path: str, environ: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        resolved = environ.get(name)
        if not resolved:
            raise ValueError(f"{path} requires non-empty environment variable {name!r}")
        return resolved
    return _ENV.sub(replace, value)


def _url(value: Any, path: str, environ: dict[str, str]) -> str:
    resolved = _expand(_text(value, path), path, environ).rstrip("/")
    parts = urlsplit(resolved)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError(f"{path} must be an http(s) URL without credentials")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _names(items: list[dict[str, Any]], path: str) -> None:
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{path}[{index}] must be a table")
        name = _text(item.get("name"), f"{path}[{index}].name")
        if name in seen:
            raise ValueError(f"duplicate {path} name {name!r}")
        seen.add(name)


def normalize_endpoints(arsenal: dict[str, Any], *, environ: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Return secret-redacted normalized endpoints from new and legacy config."""
    env = os.environ if environ is None else environ
    configured = arsenal.get("endpoints", [])
    legacy = arsenal.get("llamas", [])
    if not isinstance(configured, list):
        raise ValueError("arsenal.endpoints must be an array of tables")
    if not isinstance(legacy, list):
        raise ValueError("arsenal.llamas must be an array of tables")
    if not configured and not legacy:
        raise ValueError("arsenal must contain [[arsenal.endpoints]] or [[arsenal.llamas]]")

    endpoints = [deepcopy(item) for item in configured]
    for index, llama in enumerate(legacy):
        if not isinstance(llama, dict):
            raise ValueError(f"arsenal.llamas[{index}] must be a table")
        runtime = {
            "engine": llama.get("llama_build"), "host": llama.get("host"),
            "port": llama.get("port"), "startup_timeout": llama.get("startup_timeout"),
        }
        models = []
        for model in llama.get("models", []):
            normalized = deepcopy(model)
            normalized["model"] = model.get("alias")
            normalized["context_window"] = model.get("ctx_size")
            normalized["artifact"] = {key: model.get(key) for key in
                                      ("source", "owner", "repository", "filename")}
            normalized["runtime"] = {key: model.get(key) for key in
                                     ("ctx_size", "threads", "threads_batch", "reasoning")}
            models.append(normalized)
        endpoints.append({"name": llama.get("name"), "kind": "managed",
                          "protocol": "openai", "provider": "llama_cpp",
                          "healthcheck": "models", "connect_timeout": 5.0,
                          "request_timeout": float(llama.get("startup_timeout", 120.0)),
                          "validate_model": True, "runtime": runtime, "models": models,
                          "_legacy_name": llama.get("name")})

    _names(endpoints, "arsenal.endpoints")
    global_models: set[str] = set()
    for ei, endpoint in enumerate(endpoints):
        path = f"arsenal.endpoints[{ei}]"
        kind = endpoint.get("kind")
        protocol = endpoint.get("protocol")
        healthcheck = endpoint.get("healthcheck", "models")
        if kind not in KINDS:
            raise ValueError(f"{path}.kind must be 'managed' or 'external'")
        if protocol not in PROTOCOLS:
            raise ValueError(f"{path}.protocol must be 'openai' or 'anthropic'")
        if healthcheck not in HEALTHCHECKS:
            raise ValueError(f"{path}.healthcheck must be 'none', 'tcp', or 'models'")
        endpoint["healthcheck"] = healthcheck
        endpoint["connect_timeout"] = _positive(endpoint.get("connect_timeout", 5.0), f"{path}.connect_timeout")
        endpoint["request_timeout"] = _positive(endpoint.get("request_timeout", 120.0), f"{path}.request_timeout")
        endpoint["validate_model"] = endpoint.get("validate_model", True)
        if not isinstance(endpoint["validate_model"], bool):
            raise ValueError(f"{path}.validate_model must be a boolean")
        headers = endpoint.get("headers")
        if headers is not None and (
            not isinstance(headers, dict) or
            any(not isinstance(key, str) or not isinstance(value, str)
                for key, value in headers.items())
        ):
            raise ValueError(f"{path}.headers must be a table of strings")
        models = endpoint.get("models")
        if not isinstance(models, list) or not models:
            raise ValueError(f"{path}.models must be a non-empty array of tables")
        _names(models, f"{path}.models")

        if kind == "external":
            if "runtime" in endpoint or "artifact" in endpoint:
                raise ValueError(f"{path}: external endpoint cannot contain runtime or artifact")
            endpoint["base_url"] = _url(endpoint.get("base_url"), f"{path}.base_url", env)
        else:
            if protocol != "openai":
                raise ValueError(f"{path}: managed llama.cpp requires protocol = 'openai'")
            if "base_url" in endpoint:
                raise ValueError(f"{path}: managed endpoint derives base_url from runtime.host/port")
            runtime = endpoint.get("runtime")
            if not isinstance(runtime, dict):
                raise ValueError(f"{path}.runtime is required for managed llama.cpp")
            runtime["engine"] = _text(runtime.get("engine"), f"{path}.runtime.engine")
            runtime["host"] = _text(runtime.get("host"), f"{path}.runtime.host")
            port = runtime.get("port")
            if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                raise ValueError(f"{path}.runtime.port must be an integer from 1 to 65535")
            runtime["startup_timeout"] = _positive(runtime.get("startup_timeout", 120.0), f"{path}.runtime.startup_timeout")
            endpoint["base_url"] = f"http://{runtime['host']}:{port}"

        key_env = endpoint.pop("api_key_env", None)
        if key_env is not None:
            key_env = _text(key_env, f"{path}.api_key_env")
            api_key = env.get(key_env)
            if not api_key:
                raise ValueError(f"{path}.api_key_env requires non-empty environment variable {key_env!r}")
            endpoint["api_key"] = api_key
        else:
            endpoint["api_key"] = "llama.cpp" if kind == "managed" else ""

        for mi, model in enumerate(models):
            mpath = f"{path}.models[{mi}]"
            name = _text(model.get("name"), f"{mpath}.name")
            if name in global_models:
                raise ValueError(f"duplicate global Arsenal model name {name!r}")
            global_models.add(name)
            model["model"] = _text(model.get("model"), f"{mpath}.model")
            context = model.get("context_window")
            if context is not None:
                model["context_window"] = int(_positive(context, f"{mpath}.context_window"))
            assistants = model.get("assistants", [])
            if not isinstance(assistants, list):
                raise ValueError(f"{mpath}.assistants must be an array of tables")
            _names(assistants, f"{mpath}.assistants")
            if kind == "external" and ("runtime" in model or "artifact" in model):
                raise ValueError(f"{mpath}: external model cannot contain runtime or artifact")
            if kind == "managed":
                artifact, model_runtime = model.get("artifact"), model.get("runtime")
                if not isinstance(artifact, dict) or not isinstance(model_runtime, dict):
                    raise ValueError(f"{mpath}: managed model requires artifact and runtime tables")
                for field in ("source", "owner", "repository", "filename"):
                    artifact[field] = _text(artifact.get(field), f"{mpath}.artifact.{field}")
                for field in ("ctx_size", "threads", "threads_batch"):
                    model_runtime[field] = int(_positive(model_runtime.get(field), f"{mpath}.runtime.{field}"))
                model_runtime["reasoning"] = _text(model_runtime.get("reasoning"), f"{mpath}.runtime.reasoning")
                model.update(artifact)
                model.update(model_runtime)
                model["alias"] = model["model"]
                model["context_window"] = model_runtime["ctx_size"]
    return endpoints


def redacted_endpoint(endpoint: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(endpoint)
    if "api_key" in result:
        result["api_key"] = "<redacted>" if result["api_key"] else ""
    for key in list(result.get("headers", {})):
        if any(marker in key.lower() for marker in ("authorization", "api-key", "token", "secret")):
            result["headers"][key] = "<redacted>"
    return result
