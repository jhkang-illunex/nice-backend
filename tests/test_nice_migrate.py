"""nice_migrate — CLI DB 연결 빌더·.env 파서 단위 테스트 (DB 연결 없이)."""
from __future__ import annotations

import subprocess
import sys

from nice_migrate.rate import build_engine, load_env_file


def test_build_engine_from_args() -> None:
    eng = build_engine(host="h", port="5555", user="u", password="pw", dbname="db")
    url = eng.url.render_as_string(hide_password=False)
    assert url == "postgresql+psycopg://u:pw@h:5555/db"


def test_build_engine_dsn_takes_priority() -> None:
    eng = build_engine(dsn="postgresql+psycopg://a:b@c:1/d", host="ignored")
    assert eng.url.host == "c" and eng.url.database == "d"


def test_build_engine_env_fallback(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "envhost")
    monkeypatch.setenv("POSTGRES_USER", "envuser")
    eng = build_engine()  # 인자 없음 → 환경변수 폴백
    assert eng.url.host == "envhost"
    assert eng.url.username == "envuser"


def test_load_env_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MY_K", raising=False)
    f = tmp_path / ".env"
    f.write_text('# comment\nMY_K="vvv"\nEMPTY=\n', encoding="utf-8")
    load_env_file(str(f))
    import os
    assert os.environ["MY_K"] == "vvv"


def test_migrate_is_db_driver_only() -> None:
    """마이그레이션은 sqlalchemy 만 의존 — nice_poc 등 프로젝트 패키지 미의존(깨끗한 서브프로세스)."""
    code = (
        "import sys, nice_migrate.rate, nice_migrate.__main__;"
        "leaked=[m for m in sys.modules if m.startswith('nice_poc') or m.startswith('nice_graph') "
        "or m.startswith('nice_shock') or m.startswith('nice_rag')];"
        "print(leaked); sys.exit(1 if leaked else 0)"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, f"마이그레이션이 프로젝트 패키지를 끌어옴: {r.stdout.strip()}"
