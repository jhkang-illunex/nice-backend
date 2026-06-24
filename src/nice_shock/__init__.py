"""nice_shock — 순수 쇼크 전파 계산 패키지 (DB 의존 없음).

구성
  engine    : 거듭제곱급수/SCC 닫힌해 전파 엔진 (numpy·networkx 만 의존).
  scenario  : triple_list(엣지 목록)·seed_list 입력의 tariff/volume 시나리오.
  api       : FastAPI 앱 (외부 노출). 그래프를 클라이언트가 제공하므로 DB·LLM 불요.

설계: shock 서버는 stateless — 입력으로 받은 거래 그래프(triple_list)만으로 전파한다.
거래 그래프 조립(DB→triple_list)은 nice_dbtool 책임이고, 여기선 계산만 한다.
"""
from nice_shock.engine import (  # noqa: F401
    ShockResult,
    propagate_dispatch,
    propagate_shock,
    propagate_shock_scc,
)
