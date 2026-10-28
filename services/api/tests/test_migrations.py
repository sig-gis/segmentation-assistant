"""Migration runner: order, skip-applied, one transaction per file."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from app.migrations import apply_migrations, pending_migrations


class FakeConn:
    def __init__(self, applied=()):
        self.applied = list(applied)
        self.executed: list[str] = []
        self.transactions = 0

    async def execute(self, sql, *args):
        if sql.startswith("INSERT INTO schema_migrations"):
            self.applied.append(args[0])
        else:
            self.executed.append(sql.strip())

    async def fetch(self, sql):
        return [{"name": n} for n in self.applied]

    @asynccontextmanager
    async def transaction(self):
        self.transactions += 1
        yield


def _write(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql)


def test_runs_pending_files_in_name_order(tmp_path):
    _write(tmp_path, "003_b.sql", "SELECT 3;")
    _write(tmp_path, "002_a.sql", "SELECT 2;")
    (tmp_path / "README.md").write_text("not sql")
    conn = FakeConn()

    ran = asyncio.run(apply_migrations(conn, tmp_path))

    assert ran == ["002_a.sql", "003_b.sql"]
    assert conn.executed[-2:] == ["SELECT 2;", "SELECT 3;"]
    assert conn.transactions == 2
    assert conn.applied == ["002_a.sql", "003_b.sql"]


def test_skips_already_applied(tmp_path):
    _write(tmp_path, "002_a.sql", "SELECT 2;")
    _write(tmp_path, "003_b.sql", "SELECT 3;")
    conn = FakeConn(applied=["002_a.sql"])

    assert asyncio.run(apply_migrations(conn, tmp_path)) == ["003_b.sql"]
    assert "SELECT 2;" not in conn.executed


def test_missing_directory_is_a_no_op(tmp_path):
    assert pending_migrations(tmp_path / "nope", set()) == []


def test_repo_migrations_directory_holds_only_numbered_sql():
    from app.config import Settings

    directory = Settings().migrations_dir()
    assert directory.is_dir()
    for path in directory.glob("*.sql"):
        assert path.name[:3].isdigit(), path.name
