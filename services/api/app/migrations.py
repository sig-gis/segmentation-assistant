"""Build and upgrade the database schema at startup.

The schema is a set of numbered files in `services/storage/migrations/`:
`001_schema.sql` creates everything, later files (`002_*.sql`, ...) change
it. On connect, the API runs any file not yet listed in the
`schema_migrations` table, in name order, each in its own transaction, so a
new database and an upgraded one end up identical.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name        TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ DEFAULT now()
)
"""


def pending_migrations(directory: Path, applied: set[str]) -> list[Path]:
    if not directory.is_dir():
        return []
    return [p for p in sorted(directory.glob("*.sql")) if p.name not in applied]


async def apply_migrations(conn, directory: Path) -> list[str]:
    """Run pending migration files on an asyncpg connection; returns their names."""
    await conn.execute(_TRACKING_TABLE)
    rows = await conn.fetch("SELECT name FROM schema_migrations")
    applied = {row["name"] for row in rows}
    ran = []
    for path in pending_migrations(directory, applied):
        async with conn.transaction():
            await conn.execute(path.read_text())
            await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", path.name)
        logger.info("applied migration %s", path.name)
        ran.append(path.name)
    return ran
