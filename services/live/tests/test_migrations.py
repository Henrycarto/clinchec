"""The migration runner.

What this replaces: `migrations/` mounted at Postgres's initdb hook, which fires
only when the data directory is empty. Migrations 002-005 therefore reached
fresh databases and no others, had to be applied by hand elsewhere, and nothing
recorded whether that had happened.

The database-touching behaviour is proven against a real Postgres in the drill
that accompanies this change. What is worth pinning here is the file discipline
the runner depends on — ordering, immutability, and the idempotency that lets it
adopt a database built by the old path.
"""

from __future__ import annotations

import pytest

from app.migrations import MIGRATIONS_DIR, MigrationError, _checksum, discover


def test_migrations_apply_in_filename_order():
    names = [path.name for path in discover()]
    assert names == sorted(names)
    assert names[0].startswith("001_"), names


def test_every_migration_is_numbered_uniquely():
    """Two files sharing a prefix would apply in an order nobody chose."""
    prefixes = [path.name.split("_", 1)[0] for path in discover()]
    assert len(prefixes) == len(set(prefixes)), prefixes


def test_migrations_are_re_runnable():
    """The runner adopts databases built by the old initdb path by applying
    everything once against an existing schema. That only works because each
    file is safe to re-run, so it is asserted rather than assumed.

    Any statement that creates or alters must say IF NOT EXISTS, drop first, or
    handle the conflict. A new migration that cannot do so needs a guard clause
    and a note explaining it.
    """
    offenders: list[str] = []
    for path in discover():
        for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if line.startswith("--") or not line:
                continue
            lowered = line.lower()
            if lowered.startswith(("create table", "create index", "create unique index")):
                if "if not exists" not in lowered:
                    offenders.append(f"{path.name}:{number} {line[:70]}")
            elif lowered.startswith("insert into") and "on conflict" not in (
                path.read_text(encoding="utf-8").lower()
            ):
                offenders.append(f"{path.name}:{number} {line[:70]}")
    assert not offenders, "not safely re-runnable:\n  " + "\n  ".join(offenders)


def test_checksum_ignores_line_endings():
    """The repository is developed on Windows and deployed on Linux.

    A checksum that moved with the line endings would report every migration as
    modified on the first Linux run — and the runner treats a changed checksum
    as tampering and refuses to start.
    """
    assert _checksum("SELECT 1;\r\nSELECT 2;\r\n") == _checksum("SELECT 1;\nSELECT 2;\n")


def test_a_missing_directory_is_an_error_not_an_empty_run(tmp_path):
    """Silently finding no migrations would let a service start against an
    unmigrated database and report success."""
    with pytest.raises(MigrationError):
        discover(tmp_path / "nope")


def test_the_packaged_directory_is_the_one_that_ships():
    """The runner looks beside the service, not at a path outside the Docker
    build context — which is what made the files unreachable from the image."""
    assert MIGRATIONS_DIR.name == "migrations"
    assert MIGRATIONS_DIR.parent.name == "live"
    assert (MIGRATIONS_DIR / "001_init.sql").exists()
