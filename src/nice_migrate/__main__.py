"""company_edge.trade_rate 마이그레이션 CLI.

사용 예
  python -m nice_migrate --year 2026                 # 환경변수(POSTGRES_*) 사용
  python -m nice_migrate --host db --user nice --password nice --dbname nice_innovation
  python -m nice_migrate --dsn postgresql+psycopg://nice:nice@db:5432/nice_innovation
  python -m nice_migrate --env-file .env --dry-run
  python -m nice_migrate --year 2026 --buy-fallback   # 무매출 target 도 buy_rate 채움
  python -m nice_migrate --shell                      # 갱신 없이 IPython 데이터 조회 쉘
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from nice_migrate.rate import build_engine, load_env_file, update_trade_rate


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="nice_migrate",
        description="company_edge 를 년단위 정규화해 trade_rate + sell_rate/buy_rate(거래망 공유율) 갱신.",
    )
    p.add_argument("--dsn", help="전체 DSN (postgresql+psycopg://user:pw@host:port/db). 지정 시 개별 인자 무시.")
    p.add_argument("--host")
    p.add_argument("--port")
    p.add_argument("--user")
    p.add_argument("--password")
    p.add_argument("--dbname")
    p.add_argument("--env-file", help=".env 파일 경로 (KEY=VALUE 주입, 기존 환경변수 우선).")
    p.add_argument("--schema", default="public")
    p.add_argument("--year", help="특정 거래연도만 (예: 2026). 미지정 시 전체.")
    p.add_argument("--no-alter", action="store_true",
                   help="trade_rate 컬럼 타입 자동 보정(double precision) 비활성.")
    p.add_argument("--no-shares", action="store_true",
                   help="sell_rate/buy_rate(거래망 공유율) 동시 갱신 비활성 (기본은 함께 채움).")
    p.add_argument("--buy-fallback", action="store_true",
                   help="target 이 무매출이라 buy_rate=0 인 행을 target 매입 총액 기준으로 "
                        "재계산(buy_rate_basis='target_purchases'). 기본 off — CRI 등 하류 계산에 "
                        "영향을 주는 정의 변경이라 명시적으로 켜야 적용됨.")
    p.add_argument("--dry-run", action="store_true", help="갱신 없이 대상 행 수만 출력.")
    p.add_argument("--shell", action="store_true",
                   help="갱신 없이 IPython 쉘 진입(engine/pd 준비된 상태) — 데이터 직접 조회·핸들링용.")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.env_file:
        load_env_file(args.env_file)

    try:
        engine = build_engine(
            dsn=args.dsn, host=args.host, port=args.port,
            user=args.user, password=args.password, dbname=args.dbname,
        )
        if args.shell:
            from nice_migrate.shell import start_shell

            start_shell(engine, schema=args.schema)
            return 0
        stats = update_trade_rate(
            engine, year=args.year, schema=args.schema,
            dry_run=args.dry_run, alter_column=not args.no_alter,
            fill_shares=not args.no_shares, fill_buy_fallback=args.buy_fallback,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
