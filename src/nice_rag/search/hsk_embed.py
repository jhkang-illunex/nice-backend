"""HSK 임베딩 헬퍼 — Qwen3-Embedding-0.6B 권장 패턴 적용.

권장사항(모델 카드 §Usage):
  1. **query 측에만** instruct prefix 부착, document 측은 raw text 그대로.
  2. 코사인 유사도 색인 사용 → 임베딩 정규화(L2) 권장.
  3. 출력 1024-d. Matryoshka 로 32~1024 사이 truncate 가능
     (호출 측 ``EMBED_DIM`` 으로 지정; 본 헬퍼는 truncate 후 재정규화).

본 헬퍼는 *모델 무관* 코드만 두고, 모델 ID 는 ``RagSettings`` 에서 주입한다 —
``EMBED_MODEL`` 환경변수만 바꾸면 다른 모델로 swap 가능(차원만 맞으면 됨).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import text

from nice_poc.db import get_pg_engine
from nice_rag.clients import get_embed_client
from nice_rag.config import get_rag_settings

log = logging.getLogger(__name__)


def _l2_normalize(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return list(vec)
    return [x / norm for x in vec]


def _truncate(vec: Sequence[float], dim: int) -> list[float]:
    """Matryoshka truncate — 앞 dim 차원만 잘라낸 뒤 재정규화 책임은 호출 측."""
    if len(vec) <= dim:
        return list(vec)
    return list(vec[:dim])


def _postprocess(vec: Sequence[float], *, dim: int, normalize: bool) -> list[float]:
    out = _truncate(vec, dim)
    if normalize:
        out = _l2_normalize(out)
    return out


def _format_query(text: str, instruction: str) -> str:
    """Qwen3 retrieval 표준 포맷 — `Instruct: {task}\\nQuery: {q}`."""
    instruction = (instruction or "").strip()
    if not instruction:
        return text
    return f"Instruct: {instruction}\nQuery: {text}"


def embed_documents(texts: Iterable[str]) -> list[list[float]]:
    """문서(=hsk row) 임베딩 — instruct prefix 없이 그대로."""
    s = get_rag_settings()
    client = get_embed_client()
    batch = [t for t in texts]
    if not batch:
        return []
    raw = client.embed(batch)
    return [_postprocess(v, dim=s.embed_dim, normalize=s.embed_normalize) for v in raw]


def embed_query(text: str) -> list[float]:
    """쿼리 임베딩 — Qwen3 권장 instruct prefix 적용."""
    s = get_rag_settings()
    client = get_embed_client()
    formatted = _format_query(text, s.embed_query_instruction)
    raw = client.embed([formatted])[0]
    return _postprocess(raw, dim=s.embed_dim, normalize=s.embed_normalize)


def _vec_to_pg(vec: Sequence[float]) -> str:
    """pgvector textual 표현 — pgvector-python 의존 없이 동작."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


@dataclass
class BulkEmbedReport:
    candidates: int = 0
    embedded: int = 0
    batches: int = 0
    dry_run: bool = False

    def summary(self) -> str:
        suffix = " (dry-run)" if self.dry_run else ""
        return (
            f"candidates : {self.candidates}\n"
            f"embedded   : {self.embedded}{suffix}\n"
            f"batches    : {self.batches}"
        )


_SELECT_CANDIDATES = text(
    """
    SELECT hs_code, search_text
    FROM hsk
    WHERE search_text IS NOT NULL
      AND (:only_missing = false OR embedding IS NULL)
    ORDER BY hs_code
    """
)

# CAST 는 named param 으로 받은 textual vector 를 vector(1024) 로 변환.
_UPDATE_EMBEDDING = text(
    "UPDATE hsk SET embedding = CAST(:embedding AS vector) WHERE hs_code = :hs_code"
)


def bulk_embed_hsk(
    *,
    batch_size: int = 64,
    only_missing: bool = True,
    dry_run: bool = False,
    limit: int | None = None,
) -> BulkEmbedReport:
    """``hsk.search_text`` 일괄 임베딩 → ``hsk.embedding`` UPDATE.

    Args:
      batch_size: 임베딩 API 한 호출 당 텍스트 수. TEI CPU 에서 64 가 무난.
      only_missing: embedding IS NULL row 만 (기본). False 면 재임베딩.
      dry_run: 후보 조회 + 임베딩 호출까지만 (UPDATE 미실행).
      limit: 디버깅용 상한.
    """
    report = BulkEmbedReport(dry_run=dry_run)
    engine = get_pg_engine()

    with engine.connect() as conn:
        rows = conn.execute(
            _SELECT_CANDIDATES, {"only_missing": only_missing}
        ).mappings().all()

    if limit is not None:
        rows = rows[:limit]
    report.candidates = len(rows)
    if not rows:
        return report

    # 배치 단위로 임베딩 → UPDATE
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        texts = [r["search_text"] for r in chunk]
        vecs = embed_documents(texts)
        if len(vecs) != len(chunk):
            raise RuntimeError(
                f"embed backend returned {len(vecs)} vectors for {len(chunk)} inputs"
            )

        report.batches += 1
        if dry_run:
            report.embedded += len(chunk)
            continue

        update_params = [
            {"hs_code": r["hs_code"], "embedding": _vec_to_pg(v)}
            for r, v in zip(chunk, vecs, strict=True)
        ]
        with engine.begin() as conn:
            conn.execute(_UPDATE_EMBEDDING, update_params)
        report.embedded += len(chunk)

        log.info(
            "embedded batch %d (%d/%d)",
            report.batches,
            report.embedded,
            report.candidates,
        )

    return report


def build_document_text(
    name_ko: str,
    name_en: str | None = None,
    description: str | None = None,
    standard_trade_name: str | None = None,
    nature_name: str | None = None,
) -> str:
    """hsk row 의 텍스트 필드들을 임베딩용 단일 문자열로 정규화.

    중복 방지를 위해 None/공백 제거 후 ' | ' 로 결합. 컬럼 순서는 한국어 검색
    품질에 가장 직접적인 한글품목명 → 한국표준무역분류명 → 성질 → 영문 → 설명.
    """
    parts: list[str] = []
    for v in (name_ko, standard_trade_name, nature_name, name_en, description):
        if v is None:
            continue
        s = str(v).strip()
        if s:
            parts.append(s)
    return " | ".join(parts)
