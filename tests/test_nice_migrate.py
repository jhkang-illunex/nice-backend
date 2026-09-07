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


def test_no_rate_requires_cri(capsys) -> None:
    """--no-rate 단독은 오류 — 실행할 게 없음."""
    import nice_migrate.__main__ as m

    rc = m.main(["--no-rate", "--dsn", "postgresql+psycopg://u:p@h:1/d"])
    assert rc == 1
    assert "--no-rate" in capsys.readouterr().err


def test_cri_no_rate_skips_rate_update(monkeypatch, capsys) -> None:
    """--cri --no-rate 는 update_trade_rate 호출 없이 update_cri_weights 만 실행."""
    import nice_migrate.__main__ as m
    import nice_migrate.cri as cri_mod

    calls: list[str] = []
    monkeypatch.setattr(m, "build_engine", lambda **kw: object())
    monkeypatch.setattr(m, "update_trade_rate", lambda *a, **kw: calls.append("rate"))
    monkeypatch.setattr(
        cri_mod, "update_cri_weights",
        lambda *a, **kw: calls.append("cri") or {"years": []},
    )
    rc = m.main(["--cri", "--no-rate", "--dsn", "postgresql+psycopg://u:p@h:1/d"])
    assert rc == 0
    assert calls == ["cri"]  # rate 호출 안 됨
    out = capsys.readouterr().out
    assert '"rate"' not in out and '"cri"' in out


def test_cri_with_rate_runs_both(monkeypatch) -> None:
    """--cri (--no-rate 없음) 는 기존과 동일하게 rate 선행 후 cri — 출력에 둘 다 포함."""
    import nice_migrate.__main__ as m
    import nice_migrate.cri as cri_mod

    calls: list[str] = []
    monkeypatch.setattr(m, "build_engine", lambda **kw: object())
    monkeypatch.setattr(
        m, "update_trade_rate",
        lambda *a, **kw: calls.append("rate") or {"updated": 1},
    )
    monkeypatch.setattr(
        cri_mod, "update_cri_weights",
        lambda *a, **kw: calls.append("cri") or {"years": []},
    )
    rc = m.main(["--cri", "--dsn", "postgresql+psycopg://u:p@h:1/d"])
    assert rc == 0
    assert calls == ["rate", "cri"]
