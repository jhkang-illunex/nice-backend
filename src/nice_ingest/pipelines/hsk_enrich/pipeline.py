"""HSK 검색 텍스트 보강 — KIS HS 계층(s_ra417) → detail/search_text/tsv 재생성.

흐름 (단일 트랜잭션)
  1. detail_ko/detail_en: ``public.origin_kis_ra__s_ra417`` 의 부모 chain 을
     ' > ' 로 결합 (류 2 → 호 4 → 소호 6 → 세번 8 → 품목 10).
  2. search_text (trigram + 임베딩 입력): 본문 슬롯 + 마지막 조항 슬롯 —
     name_ko | name_en | detail_ko | detail_en | standard_trade_name |
     nature_integrated_name | hs_content | 한정조항모음
  3. search_tsv (전문검색): setweight 가중 색인 —
     A=품목명, B=계층 chain, C=분류명, D=한정/제외 조항.
     ts_rank 기본 가중 {A:1.0, B:0.4, C:0.2, D:0.1} 가 자동 적용된다.

텍스트 정규화 규칙
  - 구분자류 문자는 공백 치환 — 문자 단위(translate)라 중첩 깊이 무관:
      괄호 ()（）   : '(기타 어류의 어육(신선 또는 냉장))' 같은 중첩 포함,
                      '락(lac)' 병기 동의어가 독립 토큰으로 풀림
      대괄호 []［］ : '[하소(?燒)한 것인지에 상관없다]' 제한 조항
      낫표 「」｢｣   : '「농약관리법」' '｢농약관리법｣' 법령 인용 (두 형태 혼재)
      가운뎃점 ㆍ·  : '말ㆍ당나귀ㆍ노새' — tsvector 'simple' 이 'ㆍ규선석' 처럼
                      붙은 토큰을 만들어 단독 검색이 안 되는 문제의 직접 원인
  - 한정/제외 조항('제외|한정|상관없' 포함 괄호/대괄호 세그먼트)은 *계층
    chain(detail)에서만* 분리한다. 호 레벨 조항은 모든 형제 품목에 복제되어
    "팽창된 점토는 제외한다" 가 '팽창 점토' 질의의 오답을 만들기 때문 —
    조항은 D 가중치로만 검색에 기여하고, 임베딩 입력에서는 문장 끝으로 밀린다.
    반면 *품목명(name) 자체의 조항* 은 그 품목 고유의 식별 정보(예: 농약원제
    '농약관리법에 따라 등록된 것으로 한정')이므로 A 가중에 그대로 둔다.
    (영문 조항은 패턴 정형성이 낮아 1차에서는 미분리.)

주의
  - s_ra417 의 ``eng_hs_item_cfc_dsc_cont`` 에는 상류 ETL(pandas) 흔적인
    'nan' 문자열이 섞여 있다(1,092행). NULLIF 로 걸러야 오염되지 않는다.
  - 임베딩은 갱신하지 않는다 — 본 파이프라인 후 ``hsk_embed --rebuild`` 필요.
  - ``--skip-detail`` 로 1단계(detail 재빌드, 각 ~3분)를 건너뛰고 텍스트/색인만
    재생성할 수 있다 (detail 원본이 이미 최신일 때의 반복 작업용).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

log = logging.getLogger(__name__)

# ─── SQL 조각 ────────────────────────────────────────────────────────────────

# 부모 chain 매칭: s_ra417.hsitemcfccd 는 10자리 zero-padded (예: 호 0101 → '0101000000')
_CHAIN_PARENTS_SQL = """
    r.hsitemcfccd IN (
        LEFT(h2.hs_code, 2) || '00000000',
        LEFT(h2.hs_code, 4) || '000000',
        LEFT(h2.hs_code, 6) || '0000',
        LEFT(h2.hs_code, 8) || '00',
        h2.hs_code
    )
"""

# 구분자 문자 집합과 동일 길이의 공백 (translate 1:1 매핑).
# ᆞ: NFKC 가 ㆍ(U+318D)를 매핑하는 아래아(U+119E). □/∙: 원천 데이터의 깨진
# 괄호·불릿 잔재. \x7f(DEL)/\xa0(NBSP): KIS 원천에 섞인 제어·공백 문자.
_SEPS = "()（）[]［］「」｢｣ㆍᆞ∙·□\x7f\xa0"
_SEPS_SPACES = " " * len(_SEPS)

# 한정/제외 조항 세그먼트 — 괄호/대괄호 1단계(내부에 같은 종류 괄호 없음)만 추출.
# 그룹 1 = 전체 세그먼트. 키워드 alternation 을 비캡처 (?:...) 로 쓰면 SQLAlchemy
# text() 가 ':제외' 를 바인드 파라미터로 오인하므로 일반 그룹 사용 (seg[2] 는 무시).
_CLAUSE_BRACKET = r"(\[[^\[\]]*(제외|한정|상관없)[^\[\]]*\])"
_CLAUSE_PAREN = r"(\([^()]*(제외|한정|상관없)[^()]*\))"


def _clean(expr: str) -> str:
    """NFKC 정규화 → 구분자 공백화 → 연속 공백 압축 SQL 식.

    NFKC 가 먼저 — 호환 문자(㎜→mm, ⓚ→k, ＇→', ㈜→'(주)')를 표준형으로 풀면
    질의어(ASCII 단위 표기 등)와 토큰이 일치하고, NFKC 가 새로 만든 괄호도
    뒤따르는 translate 가 마저 제거한다.
    """
    return (
        f"btrim(regexp_replace(translate(normalize({expr}, NFKC),"
        f" '{_SEPS}', '{_SEPS_SPACES}'),"
        f" ' {{2,}}', ' ', 'g'))"
    )


def _strip_clauses(expr: str) -> str:
    """한정/제외 조항 세그먼트를 제거하는 SQL 식 (대괄호 → 괄호 순)."""
    return (
        f"regexp_replace(regexp_replace({expr},"
        f" '{_CLAUSE_BRACKET}', ' ', 'g'),"
        f" '{_CLAUSE_PAREN}', ' ', 'g')"
    )


def _extract_clauses(expr: str) -> str:
    """한정/제외 조항 세그먼트만 모아 공백 결합하는 SQL 식 (스칼라 서브쿼리)."""
    return f"""
        COALESCE((SELECT string_agg(m.seg[1], ' ')
                  FROM regexp_matches({expr}, '{_CLAUSE_BRACKET}', 'g') AS m(seg)), '')
        || ' ' ||
        COALESCE((SELECT string_agg(m.seg[1], ' ')
                  FROM regexp_matches({expr}, '{_CLAUSE_PAREN}', 'g') AS m(seg)), '')
    """


# 조항 추출 원천: 한글 chain 만 — 품목명 조항은 품목 고유 정보라 A 에 유지
_CLAUSE_SRC = "COALESCE(h.detail_ko, '')"

_NAMES = "COALESCE(h.name_ko, '') || ' ' || COALESCE(h.name_en, '')"
_DETAILS = "COALESCE(h.detail_ko, '') || ' ' || COALESCE(h.detail_en, '')"
_CATEGORIES = (
    "COALESCE(h.standard_trade_name, '') || ' ' || "
    "COALESCE(h.nature_integrated_name, '') || ' ' || COALESCE(h.hs_content, '')"
)

STEPS: list[tuple[str, str, str]] = [
    (
        "detail",
        "detail_ko 빌드 (s_ra417 한글 chain)",
        f"""
        UPDATE rag.hsk h SET detail_ko = sub.chain
        FROM (
            SELECT h2.hs_code,
                   string_agg(
                       NULLIF(btrim(r.kor_hs_item_cfc_dsc_cont), 'nan'),
                       ' > ' ORDER BY r.cfc_depth
                   ) AS chain
            FROM rag.hsk h2
            JOIN public.origin_kis_ra__s_ra417 r ON {_CHAIN_PARENTS_SQL}
            GROUP BY h2.hs_code
        ) sub
        WHERE sub.hs_code = h.hs_code
        """,
    ),
    (
        "detail",
        "detail_en 빌드 (s_ra417 영문 chain, 'nan' 제거)",
        f"""
        UPDATE rag.hsk h SET detail_en = sub.chain
        FROM (
            SELECT h2.hs_code,
                   string_agg(
                       NULLIF(btrim(r.eng_hs_item_cfc_dsc_cont), 'nan'),
                       ' > ' ORDER BY r.cfc_depth
                   ) AS chain
            FROM rag.hsk h2
            JOIN public.origin_kis_ra__s_ra417 r ON {_CHAIN_PARENTS_SQL}
            GROUP BY h2.hs_code
        ) sub
        WHERE sub.hs_code = h.hs_code
        """,
    ),
    (
        "text",
        "search_text 재구성 (본문 슬롯 + 끝 조항 슬롯)",
        f"""
        UPDATE rag.hsk h SET search_text =
            {_clean(f'''
                COALESCE(h.name_ko, '') || ' | ' ||
                COALESCE(h.name_en, '') || ' | ' ||
                {_strip_clauses("COALESCE(h.detail_ko, '')")} || ' | ' ||
                COALESCE(h.detail_en, '') || ' | ' ||
                COALESCE(h.standard_trade_name, '') || ' | ' ||
                COALESCE(h.nature_integrated_name, '') || ' | ' ||
                COALESCE(h.hs_content, '') || ' | ' ||
                {_extract_clauses(_CLAUSE_SRC)}
            ''')}
        """,
    ),
    (
        "text",
        "search_tsv 가중 재계산 (A=품목명 B=chain C=분류 D=조항)",
        f"""
        UPDATE rag.hsk h SET search_tsv =
            setweight(to_tsvector('simple', {_clean(_NAMES)}), 'A') ||
            setweight(to_tsvector('simple', {_clean(_strip_clauses(_DETAILS))}), 'B') ||
            setweight(to_tsvector('simple', {_clean(_CATEGORIES)}), 'C') ||
            setweight(to_tsvector('simple', {_clean(_extract_clauses(_CLAUSE_SRC))}), 'D')
        """,
    ),
]

_VERIFY_SQL = """
    SELECT count(*) AS total,
           count(detail_ko) AS with_detail_ko,
           count(detail_en) AS with_detail_en,
           count(*) FILTER (
               WHERE detail_en LIKE 'nan >%' OR detail_en LIKE '%> nan'
                  OR detail_en LIKE '%> nan >%' OR btrim(detail_en) = 'nan'
           ) AS en_nan_polluted,
           count(*) FILTER (WHERE search_text LIKE '%(%') AS with_paren
    FROM rag.hsk
"""


def enrich(*, dry_run: bool = False, skip_detail: bool = False) -> int:
    from sqlalchemy import text

    from nice_poc.db import get_pg_engine

    engine = get_pg_engine()
    steps = [(label, sql) for group, label, sql in STEPS if not (skip_detail and group == "detail")]

    if dry_run:
        with engine.connect() as c:
            n = c.execute(text("SELECT count(*) FROM rag.hsk")).scalar()
            n_src = c.execute(
                text("SELECT count(*) FROM public.origin_kis_ra__s_ra417")
            ).scalar()
        print(f"dry-run: rag.hsk {n} rows, s_ra417 {n_src} rows — 실행 예정 단계:")
        for label, _ in steps:
            print(f"  - {label}")
        return 0

    with engine.begin() as c:
        for label, sql in steps:
            t0 = time.perf_counter()
            c.execute(text(sql))
            print(f"  [{time.perf_counter() - t0:6.2f}s] {label}")

    with engine.connect() as c:
        row = c.execute(text(_VERIFY_SQL)).mappings().fetchone()
    print(
        f"검증: total={row['total']}, detail_ko={row['with_detail_ko']}, "
        f"detail_en={row['with_detail_en']}, en_nan_polluted={row['en_nan_polluted']}, "
        f"괄호 잔존={row['with_paren']}"
    )
    print("임베딩은 갱신되지 않음 — `hsk_embed --rebuild` 를 이어서 실행하세요.")
    return 0 if row["en_nan_polluted"] == 0 and row["with_paren"] == 0 else 1


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="대상 행 수와 실행 단계만 출력 (UPDATE 미실행)",
    )
    p.add_argument(
        "--skip-detail",
        action="store_true",
        help="detail_ko/en 재빌드 생략 — search_text/tsv 만 재생성 (~25초)",
    )


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        return enrich(dry_run=args.dry_run, skip_detail=args.skip_detail)
    except Exception as exc:  # noqa: BLE001
        print(f"enrich failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
