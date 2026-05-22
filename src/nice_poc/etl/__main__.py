"""``python -m nice_poc.etl --help``

도메인 서브커맨드 (디렉토리 컨벤션)::

    masters   <root>
    firms     <root>
    supplies  <root>
    trade     <root>
    all       <root>

Generic 업로드 (임의 CSV)::

    upload-pg     <csv> --table <name> --pk <col>[,col2,...] [--rename old=new,...] [--dry-run]
    upload-neo4j  <csv> --cypher-file <path>                 [--rename old=new,...] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from nice_poc.etl.pipelines import (
    load_firms,
    load_masters,
    load_supplies,
    load_trade,
)
from nice_poc.etl.sources.csv_source import CsvSource
from nice_poc.etl.upload import upload_to_neo4j, upload_to_pg


def _parse_rename(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    out: dict[str, str] = {}
    for pair in value.split(","):
        if "=" not in pair:
            raise argparse.ArgumentTypeError(f"invalid --rename pair: {pair!r}")
        old, new = pair.split("=", 1)
        out[old.strip()] = new.strip()
    return out


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="nice_poc.etl")
    sub = p.add_subparsers(dest="cmd", required=True)

    for cmd in ("masters", "firms", "supplies", "trade", "all"):
        sp = sub.add_parser(cmd, help=f"{cmd} pipeline (디렉토리 컨벤션)")
        sp.add_argument("root", type=Path, help="원천 CSV 디렉토리")

    pg = sub.add_parser("upload-pg", help="임의 CSV → PG 테이블 UPSERT")
    pg.add_argument("csv", type=Path)
    pg.add_argument("--table", required=True)
    pg.add_argument("--pk", required=True, help="콤마 구분")
    pg.add_argument("--columns", help="콤마 구분 (지정 시 그 컬럼만 적재)")
    pg.add_argument("--rename", help="콤마/등호: old1=new1,old2=new2")
    pg.add_argument("--delimiter", default=",")
    pg.add_argument("--encoding", default="utf-8")
    pg.add_argument("--dry-run", action="store_true")

    neo = sub.add_parser("upload-neo4j", help="임의 CSV → Neo4j Cypher 실행")
    neo.add_argument("csv", type=Path)
    neo.add_argument("--cypher-file", type=Path, required=True,
                     help="UNWIND $rows AS row ... 형태의 Cypher 파일")
    neo.add_argument("--columns", help="콤마 구분")
    neo.add_argument("--rename", help="old1=new1,old2=new2")
    neo.add_argument("--delimiter", default=",")
    neo.add_argument("--encoding", default="utf-8")
    neo.add_argument("--dry-run", action="store_true")

    return p.parse_args()


def _split_csv(value: str | None) -> list[str] | None:
    return [c.strip() for c in value.split(",")] if value else None


def main() -> None:
    args = _parse()
    out: dict[str, object] = {}

    if args.cmd in ("masters", "firms", "supplies", "trade", "all"):
        src = CsvSource(root=args.root)
        if args.cmd in ("masters", "all"):
            out["masters"] = asdict(load_masters(src))
        if args.cmd in ("firms", "all"):
            out["firms"] = asdict(load_firms(src))
        if args.cmd in ("supplies", "all"):
            out["supplies"] = asdict(load_supplies(src))
        if args.cmd in ("trade", "all"):
            out["trade"] = asdict(load_trade(src))

    elif args.cmd == "upload-pg":
        report = upload_to_pg(
            args.csv,
            table=args.table,
            pk=_split_csv(args.pk) or [],
            columns=_split_csv(args.columns),
            rename=_parse_rename(args.rename),
            delimiter=args.delimiter,
            encoding=args.encoding,
            dry_run=args.dry_run,
        )
        out["upload_pg"] = {"table": args.table, **asdict(report)}

    elif args.cmd == "upload-neo4j":
        cypher = Path(args.cypher_file).read_text(encoding="utf-8")
        report = upload_to_neo4j(
            args.csv,
            cypher=cypher,
            columns=_split_csv(args.columns),
            rename=_parse_rename(args.rename),
            delimiter=args.delimiter,
            encoding=args.encoding,
            dry_run=args.dry_run,
        )
        out["upload_neo4j"] = {"cypher_file": str(args.cypher_file), **asdict(report)}

    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
