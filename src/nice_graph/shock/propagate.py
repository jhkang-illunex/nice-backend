"""쇼크 전파 엔진 — **이전됨**: 순수 계산 엔진은 ``nice_shock.engine`` 으로 옮겼다.

이 모듈은 하위호환을 위한 re-export shim 이다. 신규 코드는 ``nice_shock.engine`` 을
직접 import 할 것. (엔진은 DB 의존이 전혀 없어 shock API 서버에서 독립 사용된다.)
"""
from __future__ import annotations

from nice_shock.engine import (
    DEFAULT_CYCLE_DAMPING,
    DEFAULT_EPSILON,
    DEFAULT_MAX_ITER,
    DampedCycle,
    EdgePropagateRow,
    ShockResult,
    ShockRow,
    propagate_dispatch,
    propagate_shock,
    propagate_shock_scc,
)

__all__ = [
    "DEFAULT_CYCLE_DAMPING",
    "DEFAULT_EPSILON",
    "DEFAULT_MAX_ITER",
    "DampedCycle",
    "EdgePropagateRow",
    "ShockResult",
    "ShockRow",
    "propagate_dispatch",
    "propagate_shock",
    "propagate_shock_scc",
]
