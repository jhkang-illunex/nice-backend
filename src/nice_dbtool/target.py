"""extract_first_target — LLM 으로 1차 충격 대상 기업 선정.

입력
  node_list  : 후보 bizno 리스트 (모듈 1 ``fetch_subgraph`` 의 nodes 의 bizno).
  hscode     : 충격 원인 HS6/HS10 (모듈 1 입력과 동일). 6/10 자리 모두 허용.
  trade_year : 충격 HS 의 ra603 메타 조회용 연도. None 이면 메타 생략.

출력
  ['bizno', ...] — LLM 이 HIGH+MEDIUM 으로 분류한 기업 bizno 리스트.

기업 프로필 컬럼 (Plan B — 풍부 안, 10 컬럼)
  필수 6  : mainpdtpcl, scaledivcd, empnum, frgivs_crp_yn, ltgmktdivcd, upchecd
  보조 4  : etb_date, fadivcd, vtr_epr_yn, fundco_yn
  + bizno, korentrnm, korreprnm 은 항상 SELECT (식별/표시용 — 프로필 컬럼 카운트 외).

system prompt 에 ra603 메타 (충격 HS 의 산업분류 비중 top N) 1회 주입.
user prompt 는 노드별 차등 (해당 기업 프로필).

────────────────────────────────────────────────────────────────────────────
[다운그레이드 가이드 — Plan B → A / C 등으로 축소]
────────────────────────────────────────────────────────────────────────────
B → A (보조 4 제외, 필수 6 만, 토큰 ~15-20% 감소):
    _PROFILE_COLS_EXTRA: tuple[str, ...] = ()   # ← 한 줄 비우기
  • SQL 무변경 (어차피 전 컬럼 SELECT 해서 dict 보유).
  • prompt builder 가 _PROFILE_COLS 만 순회 → 빠진 4 컬럼은 자연 누락.
  • CPU 추론 노드 100 개 기준 ~100~200 초 → ~80~160 초로 감소.

B → C (최소, mainpdtpcl + upchecd 2 개만, 토큰 ~50% 감소):
    _PROFILE_COLS_BASE = ("mainpdtpcl", "upchecd")
    _PROFILE_COLS_EXTRA = ()
  • 가장 빠름. 경계 케이스 (MEDIUM vs LOW) 모호.

ra603 메타 비활성 (system prompt 시나리오 메타 블록 skip):
    호출 시 ``trade_year=None`` 으로 전달.
  • system prompt ~50-80 토큰 감소.
  • LLM 의 "주로 X 국가에서 수입" 같은 추론 컨텍스트는 사라짐.

새 컬럼 추가:
    _PROFILE_COLS_EXTRA = (..., "new_col_name")  + _LABEL_MAP 에 한국어 라벨 1줄.
  • SQL 의 SELECT 절도 ``_em_cols_str`` 통해 자동 포함.

새 KIS 코드 의미 매핑 추가 (raw 코드 → 한국어):
    _CODE_MAPS["new_col"] = {"0": "라벨A", "1": "라벨B", ...}
  • 매핑 못 찾는 raw 값은 그대로 통과 (안전 default).
  • 컬럼별 특수 fallback (예: ltgmktdivcd '그 외 = 기타') 은 ``_translate`` 안에 직접 추가.
────────────────────────────────────────────────────────────────────────────

호출 패턴 (UI 분리)
  모듈 1, 3, 2 를 *한 흐름에 chain* 하지 않고 UI 에서 따로 호출 — 운영자 결정.
  각 단계가 길어질 수 있으므로 (특히 모듈 3 의 노드별 LLM 호출, CPU 추론에서
  노드 100 개 = 분 단위) 사용자에게 *단계별 진행 상태* 를 보여주는 게 UX 정합.
  통합 ``/api/shock/run_all`` 같은 chain endpoint 는 의도적으로 두지 않음.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from nice_common.db import get_pg_engine
from nice_llm import get_llm_json_client

log = logging.getLogger(__name__)


_CHOICES: list[str] = ["HIGH", "MEDIUM", "LOW", "NONE"]
_PRIMARY: frozenset[str] = frozenset({"HIGH", "MEDIUM"})

_CATEGORY_DEFINITIONS = (
    "HIGH = 충격 HS 가 이 기업의 매출 또는 원가에 직접 30% 이상 영향. "
    "MEDIUM = 10~30% 또는 원자재/공급사슬 경로의 강한 간접 영향. "
    "LOW = <10%. NONE = 무관."
)


# ── 프로필 컬럼 (Plan B = 풍부). 다운그레이드는 docstring 상단 가이드 참조 ──

_PROFILE_COLS_BASE: tuple[str, ...] = (
    "mainpdtpcl",      # 주요 품목 (한국어) — 충격 HS 와 직접 대조 가능, 가장 중요
    "scaledivcd",      # 기업 규모 구분
    "empnum",          # 종업원 수
    "frgivs_crp_yn",   # 외국인 투자 여부 — 외환/수출입 노출 신호
    "ltgmktdivcd",     # 상장 시장 구분
    "upchecd",         # 기업 등록 HS6 — 충격 HS 와 같으면 즉시 HIGH 후보
)
_PROFILE_COLS_EXTRA: tuple[str, ...] = (
    "etb_date",        # 설립일 — 신생 vs 노포 구분
    "fadivcd",         # 외감/내감 구분
    "vtr_epr_yn",      # 벤처기업 Y/N
    "fundco_yn",       # 펀드회사 Y/N
)
# Plan B → A 다운그레이드 지점: 위 _PROFILE_COLS_EXTRA 를 () 로 비우면 자동 적용.
_PROFILE_COLS: tuple[str, ...] = _PROFILE_COLS_BASE + _PROFILE_COLS_EXTRA

_LABEL_MAP: dict[str, str] = {
    "mainpdtpcl":    "주요품목",
    "scaledivcd":    "규모구분",
    "empnum":        "종업원수",
    "frgivs_crp_yn": "외국인투자",
    "ltgmktdivcd":   "상장시장",
    "upchecd":       "등록HS6",
    "etb_date":      "설립일",
    "fadivcd":       "재무업종",
    "vtr_epr_yn":    "벤처기업",
    "fundco_yn":     "펀드회사",
}


# ── KIS 코드 의미 매핑 — raw 코드 → 한국어 ──────────────────────────────────
#
# 운영자 확인 기준 (2026-06-09):
#   scaledivcd  0=미대상, 1=대기업, 2=중소기업, 3=중견기업
#               * 일반 KIS 통례는 2=중견·3=중소 인 경우도 있음. 운영 데이터와
#                 어긋나면 아래 dict 값 두 줄만 교체.
#   ltgmktdivcd 1=코스피, 2=코스닥, 그 외=기타 (KONEX/K-OTC 등; _translate 에서 처리)
#   *_yn        Y=예, N=아니오 (운영자 미명시 — 한국어 일관성 위해 동일 정신으로 매핑)
#   fadivcd     재무업종 코드 — 의미 미확인 (운영자가 매핑 알려주면 여기 추가).
#               매핑 없는 컬럼은 raw 값 그대로 prompt 에 노출되어 LLM 이 *문맥 추정* 또는 무시.
_CODE_MAPS: dict[str, dict[str, str]] = {
    "scaledivcd": {
        "0": "미대상",
        "1": "대기업",
        "2": "중소기업",
        "3": "중견기업",
    },
    "ltgmktdivcd": {
        "1": "코스피",
        "2": "코스닥",
    },
    "frgivs_crp_yn": {"Y": "예", "N": "아니오"},
    "vtr_epr_yn":    {"Y": "예", "N": "아니오"},
    "fundco_yn":     {"Y": "예", "N": "아니오"},
    # "fadivcd": { ... }  ← 운영자 매핑 확인 후 한 줄 추가.
}


def _translate(col: str, val: object) -> str:
    """raw 코드값 → 사람이 읽는 의미. 매핑 없으면 raw 그대로 (안전 통과).

    컬럼별 특수 fallback:
      ltgmktdivcd : 매핑 못 찾는 값 → '기타'  (사용자 명시 정책)
      그 외       : raw 값 그대로
    """
    if val is None:
        return "-"
    s = str(val).strip()
    if not s or s == "None":
        return "-"
    mapping = _CODE_MAPS.get(col)
    if mapping is None:
        return s
    translated = mapping.get(s)
    if translated is not None:
        return translated
    # 컬럼별 fallback
    if col == "ltgmktdivcd":
        return "기타"
    return s


# ── SQL ─────────────────────────────────────────────────────────────────


_EM_IDENTITY_COLS: tuple[str, ...] = ("bizno", "korentrnm", "korreprnm")
_em_cols_str = ", ".join(f"em.{c}" for c in _EM_IDENTITY_COLS + _PROFILE_COLS)

_COMPANY_SQL = text(
    f"""
    SELECT {_em_cols_str}
    FROM public.origin_kis_em__s_em001 em
    WHERE em.bizno = ANY(:biznos)
    """
)


_RA603_META_SQL = text(
    """
    SELECT tscdcg, tscdvl, tseximdivcd, tstrdwgt
    FROM public.origin_kis_ra__s_ra603
    WHERE upchecd = :hs6
      AND CAST(bse_yr AS text) = :trade_year
    ORDER BY tstrdwgt DESC NULLS LAST
    LIMIT 10
    """
)


# ── 데이터 수집 ──────────────────────────────────────────────────────────


def _hs6(hscode: str) -> str:
    return (hscode or "").strip()[:6]


def _fetch_company_profiles(biznos: list[str]) -> dict[str, dict]:
    if not biznos:
        return {}
    with get_pg_engine().connect() as c:
        rows = c.execute(_COMPANY_SQL, {"biznos": list(biznos)}).mappings().fetchall()
    return {r["bizno"]: dict(r) for r in rows}


def _fetch_ra603_meta(hs6: str, trade_year: str | None) -> list[dict]:
    """충격 HS 의 ra603 메타 top10 (system prompt 주입용)."""
    if not trade_year:
        return []
    with get_pg_engine().connect() as c:
        rows = c.execute(
            _RA603_META_SQL, {"hs6": hs6, "trade_year": str(trade_year)}
        ).mappings().fetchall()
    return [dict(r) for r in rows]


# ── prompt builder ──────────────────────────────────────────────────────


def _build_system(hs6: str, ra603_meta: list[dict]) -> str:
    head = (
        "당신은 한국 무역 공급망 분석가입니다. "
        f"충격 시나리오: HS={hs6} 의 외생 수출입 가격/공급 충격이 발생했습니다. "
    )
    if ra603_meta:
        # 산업분류 비중 (cdcg / cdvl / wgt) 을 짧게 요약
        lines = []
        for m in ra603_meta:
            lines.append(
                f"{m.get('tscdcg')}={m.get('tscdvl')} (wgt={m.get('tstrdwgt')}, "
                f"dir={m.get('tseximdivcd')})"
            )
        head += "이 HS 의 산업분류/방향 비중 top10: " + " | ".join(lines) + ". "

    tail = (
        f"분류 정의: {_CATEGORY_DEFINITIONS} "
        "각 기업이 위 충격을 *어느 정도로* 직접 받는지를 분류하세요."
    )
    return head + tail


def _build_user(bizno: str, profile: dict | None) -> str:
    if profile is None:
        return f"bizno: {bizno}\n(프로필 누락 — 정보 없음)"

    lines = [
        f"bizno: {bizno}",
        f"기업명: {profile.get('korentrnm') or '-'}",
        f"대표자: {profile.get('korreprnm') or '-'}",
    ]
    for col in _PROFILE_COLS:
        val = profile.get(col)
        if val is None or str(val).strip() in ("", "None"):
            continue
        label = _LABEL_MAP.get(col, col)
        lines.append(f"{label}: {_translate(col, val)}")
    return "\n".join(lines)


# ── public API ──────────────────────────────────────────────────────────


def extract_first_target(
    node_list: list[str],
    *,
    hscode: str,
    trade_year: str | None = None,
) -> list[str]:
    """LLM 분류 → HIGH+MEDIUM 으로 판정된 bizno 만 반환.

    Args:
      node_list: 후보 bizno 리스트.
      hscode: 충격 원인 HS6/HS10. 6/10 자리 모두 허용 — 내부에서 LEFT 6.
      trade_year: ra603 시나리오 메타 조회 연도. None 이면 메타 skip
                  (system prompt ~50-80 토큰 절감, 컨텍스트는 약화).

    Returns:
      ['bizno', ...] — 1차 충격 대상.
    """
    if not node_list:
        return []

    hs6 = _hs6(hscode)
    profiles = _fetch_company_profiles(node_list)
    ra603_meta = _fetch_ra603_meta(hs6, trade_year)

    client = get_llm_json_client()
    system = _build_system(hs6, ra603_meta)

    primary: list[str] = []
    for bizno in node_list:
        user = _build_user(bizno, profiles.get(bizno))
        result = client.classify_choice(
            system=system,
            user=user,
            choices=_CHOICES,
            field="category",
            extra_keys=("reason",),
        )
        cat = result.get("category") if result else None
        if cat in _PRIMARY:
            primary.append(bizno)
        else:
            log.debug("bizno=%s category=%s — not primary", bizno, cat)

    return primary
