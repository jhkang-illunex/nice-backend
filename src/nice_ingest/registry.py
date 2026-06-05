"""파이프라인 레지스트리.

각 파이프라인은 ``register(name, fn)`` 으로 자기를 등록하고, CLI 는
``list`` / ``run <name> [args]`` 로 호출한다.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Pipeline:
    name: str
    description: str
    # parser 를 받아 자체 옵션을 등록하고, ns 를 받아 실행하는 함수
    add_args: Callable[[argparse.ArgumentParser], None]
    run: Callable[[argparse.Namespace], int]


_REGISTRY: dict[str, Pipeline] = {}


def register(p: Pipeline) -> None:
    if p.name in _REGISTRY:
        raise ValueError(f"pipeline already registered: {p.name}")
    _REGISTRY[p.name] = p


def get(name: str) -> Pipeline:
    if name not in _REGISTRY:
        raise KeyError(f"unknown pipeline: {name!r} (registered: {sorted(_REGISTRY)})")
    return _REGISTRY[name]


def all_pipelines() -> list[Pipeline]:
    return sorted(_REGISTRY.values(), key=lambda p: p.name)


def _autoload() -> None:
    """``pipelines/*/`` 하위 패키지를 import 하여 register() 가 실행되게 한다."""
    import importlib
    import pkgutil

    from nice_ingest import pipelines as pipelines_pkg

    for m in pkgutil.iter_modules(pipelines_pkg.__path__):
        if m.ispkg:
            importlib.import_module(f"{pipelines_pkg.__name__}.{m.name}")


_autoload()
