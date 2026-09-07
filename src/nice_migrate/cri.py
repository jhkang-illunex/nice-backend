"""company_credit_cri.weight_sell_avg / weight_buy_avg 갱신 — cri2 누적망 점수.

기존 모듈 조합 루틴 (2026-08-25):
  1) nice_migrate.rate.update_trade_rate 가 company_edge 에 채운 sell_rate/buy_rate 를
     연도별로 읽어 판매망 S·구매망 P 행렬을 직접 구성하고,
  2) cri2 이관 구현(nice_ingest.pipelines.cri.pipeline — stdlib+numpy)의
     cumulative_scores_from_edges(희소 누적망 T=W+W²+… + 등급 가중평균 core,
     O(N+E) 메모리 — 2026-08-28 대규모 대응 재작성)를 그대로 호출해,
  3) 결과 점수를 company_credit_cri(bizno, grd_st_year) 행에 기록한다 — 임시 테이블
     COPY + 단일 JOIN UPDATE 로 벌크 반영(2026-09-07, 노드당 개별 UPDATE 왕복 제거).

행렬 정의 (cri2 와 동일 — rate 가 DB 에 이미 계산돼 있어 sales 유도 불필요):
  S[판매][구매] = sell_rate (= 거래액 / 판매자 매출총액 Σ_out)
  P[구매][판매] = buy_rate  (= 거래액 / 구매자 매입총액 Σ_in)

연도 매칭: company_edge.trade_year == company_credit_cri.grd_st_year 인 연도만 처리.
  등급 행이 없는 거래연도는 기록할 곳이 없어 건너뛰고 통계(years_skipped)로 보고.

cri2 규칙 유지:
  - 무등급(NR 등 grade_to_score 미매핑)·등급 테이블 부재 기업은 유효 가중에서 제외
    (coverage 하락으로만 반영). 점수 산출 불가('-') 는 NULL 로 기록.
  - 같은 bizno 에 등급 행이 복수면 (grd_st_year 유일 제약 실측) 연도당 1행 가정,
    위반 시 bat_seq 최대 행 기준.

⚠️ 에어갭: deploy/migrate/Dockerfile 이 src/nice_ingest 도 COPY 해야 import 가능
  (cri2 core 의존 — httpx 누락 사건과 동일 유형의 함정 방지).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from nice_ingest.pipelines.cri.pipeline import cumulative_scores_from_edges, grade_to_score

log = logging.getLogger(__name__)

# 대상 연도 = 거래연도 ∩ 등급연도 (양쪽 다 있어야 계산·기록 가능).
_YEARS_SQL = """
    SELECT DISTINCT CAST(e.trade_year AS text) AS y
    FROM {schema}.company_edge e
    JOIN {schema}.company_credit_cri g
      ON CAST(g.grd_st_year AS text) = CAST(e.trade_year AS text)
    ORDER BY y
"""

_EDGES_SQL = """
    SELECT from_bizno, to_bizno, sell_rate, buy_rate
    FROM {schema}.company_edge
    WHERE CAST(trade_year AS text) = :year
      AND sell_rate IS NOT NULL AND buy_rate IS NOT NULL
"""

# 연도당 bizno 1행 전제(실측 (bizno, grd_st_year) 유일). 복수면 bat_seq 최대 행 채택.
_GRADES_SQL = """
    SELECT bizno, crigrd
    FROM {schema}.company_credit_cri
    WHERE CAST(grd_st_year AS text) = :year
    ORDER BY bat_seq
"""

# 임시 테이블 경유 벌크 갱신(2026-09-07) — 노드 수만큼 개별 UPDATE 왕복하던 방식은
# 수백만 행에서 diag 계산 자체보다 더 큰 병목이었다(실측: v3 계산 8분대인데도 전체
# 미완료). COPY(단일 스트림) → JOIN UPDATE(단일 문) 로 왕복을 O(N) → O(1) 로 줄인다.
_CREATE_TMP_SQL = """
    CREATE TEMP TABLE IF NOT EXISTS _cri_scores
        (bizno text, sell double precision, buy double precision) ON COMMIT DROP
"""
_TRUNCATE_TMP_SQL = "TRUNCATE _cri_scores"
_COPY_TMP_SQL = "COPY _cri_scores (bizno, sell, buy) FROM STDIN"
_BULK_UPDATE_SQL = """
    UPDATE {schema}.company_credit_cri c
    SET weight_sell_avg = t.sell, weight_buy_avg = t.buy
    FROM _cri_scores t
    WHERE CAST(c.bizno AS text) = t.bizno AND CAST(c.grd_st_year AS text) = :year
"""
# SQLite(테스트 전용 — COPY/TEMP TABLE ON COMMIT DROP 미지원) 폴백. 프로덕션 경로 아님.
_ROW_UPDATE_SQL = """
    UPDATE {schema}.company_credit_cri
    SET weight_sell_avg = :sell, weight_buy_avg = :buy
    WHERE CAST(bizno AS text) = :bizno AND CAST(grd_st_year AS text) = :year
"""


def _edges_from_rates(
    rows: Sequence[tuple[str, str, float, float] | Any],
) -> tuple[list[tuple[str, str, float]], list[tuple[str, str, float]], list[str]]:
    """(from, to, sell_rate, buy_rate) 행들로 S·P 엣지 리스트 구성. bizno 는 strip 정규화.

    dense 행렬(dict N×N)을 만들지 않는다 — 대규모(수백만 노드)에서 O(N²) 메모리 폭발
    방지. core(cumulative_scores_from_edges)가 O(N+E) 로 처리.
    """
    nodes = sorted(
        {r[0].strip() for r in rows} | {r[1].strip() for r in rows}
    )
    s_edges: list[tuple[str, str, float]] = []
    p_edges: list[tuple[str, str, float]] = []
    for frm, to, sell_rate, buy_rate in rows:
        f, t = frm.strip(), to.strip()
        s_edges.append((f, t, float(sell_rate)))
        p_edges.append((t, f, float(buy_rate)))
    return s_edges, p_edges, nodes


def update_cri_weights(
    engine: Engine,
    *,
    year: str | None = None,
    schema: str = "public",
    dry_run: bool = False,
) -> dict:
    """연도별 cri2 누적망 점수를 산출해 company_credit_cri 에 기록.

    dry_run=True 면 계산까지만 하고 UPDATE 는 생략(통계만 반환).
    반환: {years, per_year: {연도: {nodes, edges, graded_nodes, scored_sell, scored_buy,
      rows_updated, nodes_without_grade_row, db_read_s, compute_s, db_write_s, total_s}},
      years_skipped(등급 행 없는 거래연도), db_years_detect_s(연도 미지정 시 교집합
      조회 시간), total_s(전 연도 합계)} — 소요 시간을 DB 처리(읽기/쓰기)와
      알고리즘(cri2 core 계산)으로 분리 기록 — 병목 위치 파악용(2026-09-07).
    """
    out: dict = {"years": [], "per_year": {}, "years_skipped": [], "dry_run": dry_run}
    with engine.begin() as c:
        if year:
            years = [str(year)]
        else:
            t_detect = time.perf_counter()
            years = [
                r[0] for r in c.execute(text(_YEARS_SQL.format(schema=schema))).fetchall()
            ]
            all_trade_years = [
                r[0] for r in c.execute(
                    text(f"SELECT DISTINCT CAST(trade_year AS text) FROM {schema}.company_edge ORDER BY 1")  # noqa: S608
                ).fetchall()
            ]
            out["years_skipped"] = [y for y in all_trade_years if y not in years]
            out["db_years_detect_s"] = round(time.perf_counter() - t_detect, 3)
        for y in years:
            t_read = time.perf_counter()
            rows = c.execute(
                text(_EDGES_SQL.format(schema=schema)), {"year": y}
            ).fetchall()
            if not rows:
                db_read_s = round(time.perf_counter() - t_read, 3)
                log.warning("[cri] year=%s: sell_rate/buy_rate 채워진 엣지 0행 — "
                            "update_trade_rate 선행 필요. 건너뜀", y)
                out["per_year"][y] = {
                    "nodes": 0, "edges": 0, "rows_updated": 0,
                    "db_read_s": db_read_s, "compute_s": 0.0, "db_write_s": 0.0,
                    "total_s": db_read_s,
                }
                continue
            grades = {
                r[0].strip(): r[1]
                for r in c.execute(text(_GRADES_SQL.format(schema=schema)), {"year": y})
            }
            db_read_s = time.perf_counter() - t_read

            t_compute = time.perf_counter()
            s_edges, p_edges, nodes = _edges_from_rates(rows)
            score_by_id = {n: grade_to_score(grades.get(n)) for n in nodes}
            scores = cumulative_scores_from_edges(nodes, s_edges, p_edges, score_by_id)
            to_write = [
                (n, scores[n]["sell_score"], scores[n]["buy_score"])
                for n in nodes if n in grades  # 등급 테이블에 행 없음 → 기록할 곳 없음
            ]
            no_grade_row = len(nodes) - len(to_write)
            compute_s = time.perf_counter() - t_compute

            t_write = time.perf_counter()
            updated = _bulk_write(c, schema, y, to_write) if not dry_run else 0
            db_write_s = time.perf_counter() - t_write

            stat = {
                "nodes": len(nodes),
                "edges": len(rows),
                "graded_nodes": sum(1 for n in nodes if score_by_id[n] is not None),
                "scored_sell": sum(1 for n in nodes if scores[n]["sell_score"] is not None),
                "scored_buy": sum(1 for n in nodes if scores[n]["buy_score"] is not None),
                "rows_updated": updated,
                "nodes_without_grade_row": no_grade_row,
                "db_read_s": round(db_read_s, 3),
                "compute_s": round(compute_s, 3),
                "db_write_s": round(db_write_s, 3),
                "total_s": round(db_read_s + compute_s + db_write_s, 3),
            }
            out["per_year"][y] = stat
            log.info("[cri] year=%s: %s", y, stat)
        out["years"] = years
    out["total_s"] = round(
        out.get("db_years_detect_s", 0.0)
        + sum(s["total_s"] for s in out["per_year"].values()),
        3,
    )
    return out


def _bulk_write(
    c, schema: str, year: str, rows: list[tuple[str, float | None, float | None]]
) -> int:
    """rows=[(bizno, sell, buy), ...] 를 반영. 반환값 = 실제 갱신된 행 수.

    PostgreSQL: 임시 테이블 COPY(1 스트림) 후 단일 JOIN UPDATE — 개별 UPDATE 왕복
    (노드당 1회)이 대규모(수백만 행)에서 지배적이던 라운드트립 비용을 제거.
    그 외 dialect(SQLite — 단위테스트 전용, COPY/TEMP TABLE ON COMMIT DROP 미지원):
    행별 UPDATE 폴백 — 소규모 고정에서만 쓰이므로 성능 무관.
    """
    if not rows:
        return 0
    if c.dialect.name != "postgresql":
        updated = 0
        for bizno, sell, buy in rows:
            res = c.execute(
                text(_ROW_UPDATE_SQL.format(schema=schema)),
                {"sell": sell, "buy": buy, "bizno": bizno, "year": year},
            )
            updated += res.rowcount
        return updated
    c.exec_driver_sql(_CREATE_TMP_SQL)
    c.exec_driver_sql(_TRUNCATE_TMP_SQL)
    raw_cur = c.connection.cursor()
    with raw_cur.copy(_COPY_TMP_SQL) as copy:
        for row in rows:
            copy.write_row(row)
    res = c.execute(text(_BULK_UPDATE_SQL.format(schema=schema)), {"year": year})
    return res.rowcount
