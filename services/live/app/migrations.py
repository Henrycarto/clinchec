"""Apply pending schema migrations.

The gap this closes: `migrations/` used to be mounted at Postgres's
`/docker-entrypoint-initdb.d`, which runs **only when the data directory is
empty**. Every migration after `001_init.sql` therefore applied to fresh
databases and to no others. Four of them — rule clauses, the advisory flag, the
delegated polarity, retirement — had to be run by hand against any environment
older than they were, and nothing recorded whether that had happened.

So this is a real runner:

* migrations are files in `migrations/`, applied in filename order;
* `schema_migrations` records what has been applied, with a checksum;
* a checksum that no longer matches is an error, not a silent divergence;
* a Postgres advisory lock means several service instances starting at once
  produce one migration run and several no-ops, rather than a race.

Every migration in the tree is written to be safely re-runnable — `IF NOT
EXISTS`, `DROP CONSTRAINT IF EXISTS`, `ON CONFLICT DO NOTHING` — which is what
lets this adopt a database that was built by the old initdb path: the first run
against an existing schema re-applies everything, changes nothing, and records
it. Keep new migrations to that standard.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from app.config import Settings
from app.db import init_engine

logger = logging.getLogger(__name__)

#: Migrations live beside the service that owns the schema. Live is the only
#: service with database access — Scan and Forms reach it through Live's API —
#: so "shared schema in infra/" described the file's location rather than its
#: ownership, and put it outside the Docker build context of the one image that
#: needs it.
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

#: Arbitrary but fixed. Any instance running migrations takes this lock, so a
#: rolling deploy that starts five tasks at once serialises instead of five
#: connections racing to create the same table.
_LOCK_KEY = 8_675_309

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """A migration could not be applied, or has changed since it was."""


def _checksum(sql: str) -> str:
    # Newline-normalised: the repository is developed on Windows and deployed on
    # Linux, and a checksum that changes with the line endings would report
    # every migration as tampered with on the first Linux run.
    return hashlib.sha256(sql.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def discover(directory: Path | None = None) -> list[Path]:
    """Migration files in application order."""
    directory = directory or MIGRATIONS_DIR
    if not directory.is_dir():
        raise MigrationError(f"migrations directory not found: {directory}")
    return sorted(directory.glob("*.sql"))


async def apply_pending(
    settings: Settings, directory: Path | None = None
) -> list[str]:
    """Apply every migration not yet recorded. Returns the filenames applied.

    Raises rather than continuing on a failure. A service that starts against a
    half-migrated schema is worse than one that refuses to start: it serves
    reads that look fine until they hit the column that never arrived.
    """
    files = discover(directory)
    engine = init_engine(settings)
    applied: list[str] = []

    async with engine.begin() as connection:
        # The raw asyncpg connection, because these files hold several
        # statements each and asyncpg's prepared-statement path takes one.
        raw = await connection.get_raw_connection()
        driver = raw.driver_connection

        await driver.execute(f"SELECT pg_advisory_lock({_LOCK_KEY})")
        try:
            await driver.execute(_TRACKING_TABLE)
            recorded = {
                row["filename"]: row["checksum"]
                for row in await driver.fetch(
                    "SELECT filename, checksum FROM schema_migrations"
                )
            }

            for path in files:
                sql = path.read_text(encoding="utf-8")
                checksum = _checksum(sql)
                previous = recorded.get(path.name)

                if previous == checksum:
                    continue
                if previous is not None:
                    raise MigrationError(
                        f"{path.name} has changed since it was applied "
                        f"({previous[:12]} -> {checksum[:12]}). Migrations are "
                        f"immutable once applied; add a new one instead."
                    )

                logger.info("Applying migration %s", path.name)
                try:
                    await driver.execute(sql)
                except Exception as exc:  # noqa: BLE001 — re-raised with context
                    raise MigrationError(f"{path.name} failed: {exc}") from exc

                await driver.execute(
                    "INSERT INTO schema_migrations (filename, checksum) "
                    "VALUES ($1, $2) "
                    "ON CONFLICT (filename) DO UPDATE SET checksum = EXCLUDED.checksum",
                    path.name,
                    checksum,
                )
                applied.append(path.name)
        finally:
            await driver.execute(f"SELECT pg_advisory_unlock({_LOCK_KEY})")

    if applied:
        logger.info("Applied %d migration(s): %s", len(applied), ", ".join(applied))
    else:
        logger.info("Schema up to date (%d migration(s) recorded)", len(files))
    return applied


def main() -> None:
    """Entry point for the one-shot migrate step: `python -m app.migrations`."""
    import asyncio
    import sys

    from app.config import get_settings
    from app.db import dispose_engine

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)-8s %(name)s: %(message)s"
    )

    async def run() -> int:
        try:
            await apply_pending(get_settings())
            return 0
        except MigrationError as exc:
            logger.error("Migration failed: %s", exc)
            return 1
        finally:
            await dispose_engine()

    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
