"""1안 — HS코드로 1차 충격 대상 기업(시드) 선별.

목적
  외생 충격 HS (hs4/6/10) 가 주어지면, 그 HS 를 *실제로 거래* 하는 기업을
  ``origin_kis_ra__s_ra603`` 의 거래구성 비율(tstrdwgt) + 금액구간 코드
  (tstotusdamtstncd) 로 점수화해 영향도 높은 순으로 반환한다. 반환된
  bizno 가 모듈 1(``fetch_subgraph``) 의 1차 시드가 된다.

ra603 구조 (검증 결과)
  한 행 = (bse_yr, upchecd, tseximdivcd, tscdcg, tscdvl, tstrdwgt, tstotusdamtstncd).
  * upchecd          : 기업 단위 코드 (company.upchecd 와 1:1 → bizno 매핑 가능).
  * tscdvl           : 거래 *품목* HS 코드. ← HS 필터는 여기에 건다 (upchecd 아님).
  * tstrdwgt         : 그 (upchecd,연도,방향) 안에서 이 HS 의 거래구성 비율 (%).
  * tstotusdamtstncd : 금액구간 코드 (1~7, 클수록 큰 금액. 0/'' = 미상).
  * tscdcg           : 코드 그룹. 'H10'/'H6'=HS, 'M3'/'M4'/'M6'=MTI, 'ALL'=합계.

왜 ``tscdcg='H10'`` 단독인가 (이중계상 방지)
  H6 와 H10 은 *동일 거래의 두 평행 표현* 이라, (upchecd,연도,방향) 안에서
  각각 비율 합이 100 → 둘을 합치면 200 이 된다. H10 단독은 42개 upchecd
  전부를 커버하면서 정확히 100% 파티션이므로 중복 없이 안전.
  HS4/6/10 입력은 모두 ``LEFT(tscdvl, N)`` 접두 매칭으로 통일 (H10 값은 길이
  10 이라 hs4/hs6/hs10 어느 자릿수든 접두 비교 가능).

영향도 점수 (2단계 집계)
  1) 셀 집계: (upchecd, bse_yr, tseximdivcd) 별로 매칭 HS행의
       cell_ratio = Σ tstrdwgt           (그 셀에서 이 HS 패밀리의 노출 %, 0~100)
       cell_tier  = max(tstotusdamtstncd) (그 셀에서 가장 큰 금액구간)
  2) firm 집계: upchecd 별로
       exposure_ratio = avg(cell_ratio)   (연도·방향 셀 평균 — 100 초과 방지)
       amount_tier    = max(cell_tier)    (피크 절대 규모)
  3) score = ratio_weight * (exposure_ratio/100) + tier_weight * (amount_tier/7)
     · ratio 만 높음 = 그 기업엔 중요하나 절대 규모 작을 수 있음.
     · tier 만 높음  = 대기업의 부수 품목.
     둘을 함께 봐야 "크게 영향받는" 이 성립하므로 가중합. 가중치/임계는 인자로 조정.

필터 (모두 선택)
  year : bse_yr 일치 (None=전체 연도). 수출입/연도 필터는 무용일 가능성이
         높아 default 는 전체 — 그때 위 2단계 집계가 firm 단위로 묶어준다.
  exim : tseximdivcd 일치 (None=전체 방향). 0/3 의 수입·수출 의미는 운영
         코드표 확정 전까지 미지정 default.

주의 — 씨앗 모집단 상한
  ra603 에는 42개 upchecd 만 존재. HS 가 ra603 에 없으면 0건 반환되며
  이는 정상(데이터 커버리지 한계). 호출자는 빈 결과를 처리해야 한다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import text

from nice_common.db import get_pg_engine

log = logging.getLogger(__name__)

DEFAULT_RATIO_WEIGHT = 0.5
DEFAULT_TIER_WEIGHT = 0.5
MAX_AMOUNT_TIER = 7  # tstotusdamtstncd 의 최대 구간 (정규화 분모)


@dataclass
class ExposedFirm:
    upchecd: str
    bizno: str | None  # company 매핑 (1:1). 매핑 실패 시 None → 시드 불가.
    korentrnm: str | None  # 표시용 기업명
    exposure_ratio: float  # avg(cell_ratio), 0~100
    amount_tier: int  # max(cell_tier), 0~7
    score: float  # 가중 결합 점수 (0~1 근방)
    n_cells: int  # 집계에 들어간 (연도,방향) 셀 수 — 디버그/신뢰도


@dataclass
class PrimarySelectionResult:
    hscode: str
    hs_digits: int
    year: str | None
    exim: str | None
    firms: list[ExposedFirm] = field(default_factory=list)  # score 내림차순

    def seed_biznos(self) -> list[str]:
        """모듈 1(fetch_subgraph) 에 넘길 1차 시드 bizno 리스트 (매핑된 것만)."""
        return [f.bizno for f in self.firms if f.bizno]


# ── SQL ─────────────────────────────────────────────────────────────────────
#
# 2단계 집계를 단일 쿼리로:
#   matched : 셀(upchecd,연도,방향) 별 cell_ratio / cell_tier
#   외층     : upchecd 별 exposure_ratio(avg) / amount_tier(max) / n_cells
# year/exim 필터는 값이 있을 때만 WHERE 절을 동적으로 덧붙인다 (typed-NULL 회피).
_SELECT_SQL_TMPL = """
    WITH matched AS (
        SELECT upchecd,
               bse_yr,
               tseximdivcd,
               SUM(tstrdwgt)                                          AS cell_ratio,
               MAX(NULLIF(NULLIF(TRIM(tstotusdamtstncd), ''), '0')::int) AS cell_tier
        FROM public.origin_kis_ra__s_ra603
        WHERE tscdcg = 'H10'
          AND LEFT(tscdvl, :n) = :hs
          {year_clause}
          {exim_clause}
        GROUP BY upchecd, bse_yr, tseximdivcd
    )
    SELECT upchecd,
           AVG(cell_ratio)              AS exposure_ratio,
           COALESCE(MAX(cell_tier), 0)  AS amount_tier,
           COUNT(*)                     AS n_cells
    FROM matched
    GROUP BY upchecd
"""


def _build_select(year: str | None, exim: str | None):
    return text(
        _SELECT_SQL_TMPL.format(
            year_clause="AND CAST(bse_yr AS text) = :year" if year is not None else "",
            exim_clause="AND TRIM(tseximdivcd) = :exim" if exim is not None else "",
        )
    )

# upchecd → bizno / 기업명 (company 는 upchecd 1:1, 드물게 1:N → 다중 시드)
_COMPANY_SQL = text(
    """
    SELECT upchecd, bizno, korentrnm
    FROM public.company
    WHERE upchecd = ANY(:upchecds)
    """
)


# ── helpers ─────────────────────────────────────────────────────────────────


def _norm_hs(hscode: str) -> str:
    """숫자만 남겨 정규화. HS4/6/10 외 길이는 그대로 두되 길이로 N 을 잡는다."""
    return re.sub(r"\D", "", hscode or "")


def _fetch_company_map(upchecds: list[str]) -> dict[str, list[tuple[str, str | None]]]:
    if not upchecds:
        return {}
    out: dict[str, list[tuple[str, str | None]]] = {}
    with get_pg_engine().connect() as c:
        rows = c.execute(_COMPANY_SQL, {"upchecds": list(upchecds)}).mappings().fetchall()
    for r in rows:
        out.setdefault(r["upchecd"], []).append((r["bizno"], r.get("korentrnm")))
    return out


# ── public API ──────────────────────────────────────────────────────────────


def select_primary_firms(
    hscode: str,
    *,
    year: str | None = None,
    exim: str | None = None,
    top_k: int | None = None,
    min_ratio: float = 0.0,
    ratio_weight: float = DEFAULT_RATIO_WEIGHT,
    tier_weight: float = DEFAULT_TIER_WEIGHT,
) -> PrimarySelectionResult:
    """HS코드 → 영향도 높은 1차 기업(시드) 선별.

    Args:
      hscode: 충격 HS. 4/6/10 자리 digit string (구분자 허용, 내부 정규화).
      year:   bse_yr 필터. None=전체 연도.
      exim:   tseximdivcd 필터('0'/'3'). None=전체 방향.
      top_k:  상위 K 개만 반환. None=전체.
      min_ratio: exposure_ratio(%) 하한 — 이 미만 firm 제외 (default 0).
      ratio_weight / tier_weight: 점수 가중치 (거래비율 vs 금액규모).

    Returns:
      PrimarySelectionResult — firms 는 score 내림차순.
      ``.seed_biznos()`` 로 모듈 1 입력용 bizno 리스트를 바로 얻는다.
    """
    hs = _norm_hs(hscode)
    n = len(hs)
    if n not in (4, 6, 10):
        log.warning("hscode 자릿수가 4/6/10 이 아님 (len=%d): %r — 접두 %d자리로 진행", n, hscode, n)
    if n == 0:
        return PrimarySelectionResult(hscode=hscode, hs_digits=0, year=year, exim=exim)

    params: dict[str, object] = {"n": n, "hs": hs}
    if year is not None:
        params["year"] = str(year)
    if exim is not None:
        params["exim"] = str(exim).strip()
    with get_pg_engine().connect() as c:
        rows = c.execute(_build_select(year, exim), params).mappings().fetchall()

    if not rows:
        log.info("select_primary_firms: hs=%s year=%s exim=%s → 0 firms (ra603 미수록 가능)", hs, year, exim)
        return PrimarySelectionResult(hscode=hs, hs_digits=n, year=year, exim=exim)

    company_map = _fetch_company_map([r["upchecd"] for r in rows])

    firms: list[ExposedFirm] = []
    for r in rows:
        ratio = float(r["exposure_ratio"] or 0.0)
        if ratio < min_ratio:
            continue
        tier = int(r["amount_tier"] or 0)
        score = ratio_weight * (ratio / 100.0) + tier_weight * (tier / MAX_AMOUNT_TIER)
        mapped = company_map.get(r["upchecd"]) or [(None, None)]
        for bizno, name in mapped:  # 1:N 이면 각 bizno 가 개별 시드
            firms.append(
                ExposedFirm(
                    upchecd=r["upchecd"],
                    bizno=bizno,
                    korentrnm=name,
                    exposure_ratio=round(ratio, 4),
                    amount_tier=tier,
                    score=round(score, 6),
                    n_cells=int(r["n_cells"]),
                )
            )

    firms.sort(key=lambda f: f.score, reverse=True)
    if top_k is not None:
        firms = firms[:top_k]

    log.info(
        "select_primary_firms: hs=%s(n=%d) year=%s exim=%s → %d firms (mapped=%d)",
        hs, n, year, exim, len(firms), sum(1 for f in firms if f.bizno),
    )
    return PrimarySelectionResult(hscode=hs, hs_digits=n, year=year, exim=exim, firms=firms)
