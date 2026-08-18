"""``ksic.embedding`` 일괄 적재 — ``nice_rag.search.ksic_embed.bulk_embed_ksic`` 위임."""

from __future__ import annotations

import argparse
import logging
import sys


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help=(
            "임베딩 API 한 호출 당 텍스트 수. ksic 의 search_text 는 하위 항목명이 "
            "결합돼 hsk 보다 길다 — TEI 기본 payload 제한에서 64 는 413, 8 이 안전 "
            "(실측 2026-08-18)."
        ),
    )
    p.add_argument(
        "--rebuild",
        action="store_true",
        help="embedding 존재 row 도 재임베딩 (기본: NULL 인 row 만)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="디버깅용 처리 상한 (기본: 전체)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="UPDATE 없이 임베딩 호출까지만 (DB 미수정)",
    )


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # 지연 import — ingest 컨테이너에서 nice_rag 를 실제 사용하는 시점에만 로딩.
    from nice_rag.search.ksic_embed import bulk_embed_ksic

    try:
        report = bulk_embed_ksic(
            batch_size=args.batch_size,
            only_missing=not args.rebuild,
            dry_run=args.dry_run,
            limit=args.limit,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"bulk embed failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    print(report.summary())
    return 0
