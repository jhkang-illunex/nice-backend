"""CLI 진입점.

사용법::

    python -m nice_ingest list                     # 등록된 파이프라인 표시
    python -m nice_ingest run <name> [args...]     # 특정 파이프라인 실행
    python -m nice_ingest run <name> --help        # 그 파이프라인의 옵션

예::

    python -m nice_ingest run hscode --file=/work/관세청_HS부호_20260101.xlsx
"""

from __future__ import annotations

import argparse
import sys

from nice_ingest.registry import all_pipelines, get


def _usage() -> str:
    return (
        "usage: nice_ingest <command> [args...]\n"
        "\n"
        "commands:\n"
        "  list                       등록된 파이프라인 나열\n"
        "  run <name> [args...]       파이프라인 실행 (run <name> --help 로 옵션 확인)\n"
    )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)

    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write(_usage())
        return 0

    cmd, *rest = argv

    if cmd == "list":
        for p in all_pipelines():
            print(f"{p.name:20s} {p.description}")
        return 0

    if cmd == "run":
        if not rest or rest[0] in ("-h", "--help"):
            print("usage: nice_ingest run <name> [args...]")
            print("\nregistered pipelines:")
            for p in all_pipelines():
                print(f"  {p.name:20s} {p.description}")
            return 0
        name, *pipeline_args = rest
        try:
            pipeline = get(name)
        except KeyError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        sub = argparse.ArgumentParser(prog=f"nice_ingest run {pipeline.name}")
        pipeline.add_args(sub)
        ns = sub.parse_args(pipeline_args)
        return pipeline.run(ns)

    print(f"unknown command: {cmd!r}\n\n{_usage()}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
