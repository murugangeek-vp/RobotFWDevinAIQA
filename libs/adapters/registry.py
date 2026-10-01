"""PROD-01: config-driven adapter registry.

Adapters register a `type` name via decorator; create_*() resolves that name
from a contract source block or an environment target block and constructs it
with matching kwargs only. Adding an adapter = ``@register_*("name")`` on the
implementation — no engine or suite changes.
"""

import inspect

_SOURCE_REGISTRY: dict = {}
_TARGET_REGISTRY: dict = {}
_WRITER_REGISTRY: dict = {}


def register_source(kind: str):
    def deco(cls):
        _SOURCE_REGISTRY[kind] = cls
        return cls

    return deco


def register_target(kind: str):
    def deco(cls):
        _TARGET_REGISTRY[kind] = cls
        return cls

    return deco


def register_writer(kind: str):
    """Loader connection factory: fn(host, port, database, user, password)."""

    def deco(fn):
        _WRITER_REGISTRY[kind] = fn
        return fn

    return deco


def _ensure_builtin():
    # Importing the built-in adapter modules runs their register_* decorators.
    import libs.adapters.api_source  # noqa: F401
    import libs.adapters.csv_source  # noqa: F401
    import libs.adapters.mysql_target  # noqa: F401
    import libs.adapters.postgres_target  # noqa: F401
    import libs.adapters.s3_source  # noqa: F401


def _ctor_kwargs(fn, cfg: dict) -> dict:
    params = inspect.signature(fn).parameters
    return {k: v for k, v in cfg.items() if k in params}


def create_source(cfg: dict):
    """Build the source adapter for a contract `source:` block."""
    _ensure_builtin()
    cls = _SOURCE_REGISTRY.get(cfg.get("type"))
    if cls is None:
        raise ValueError(
            f"unknown source type {cfg.get('type')!r}; registered: {sorted(_SOURCE_REGISTRY)}"
        )
    return cls(**_ctor_kwargs(cls, cfg))


def create_target(cfg: dict, user: str, password: str):
    """Build the read-only target adapter for an environment `target:` block."""
    _ensure_builtin()
    cls = _TARGET_REGISTRY.get(cfg.get("type"))
    if cls is None:
        raise ValueError(
            f"unknown target type {cfg.get('type')!r}; registered: {sorted(_TARGET_REGISTRY)}"
        )
    return cls(**_ctor_kwargs(cls, {**cfg, "user": user, "password": password}))


def create_writer(cfg: dict, user: str, password: str):
    """Open the read-write loader connection for an environment `target:` block."""
    _ensure_builtin()
    fn = _WRITER_REGISTRY.get(cfg.get("type"))
    if fn is None:
        raise ValueError(
            f"unknown writer type {cfg.get('type')!r}; registered: {sorted(_WRITER_REGISTRY)}"
        )
    return fn(**_ctor_kwargs(fn, {**cfg, "user": user, "password": password}))
