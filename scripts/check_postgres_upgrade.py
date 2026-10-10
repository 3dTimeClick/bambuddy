"""Check that upgrading a PostgreSQL database gives the schema of a fresh install.

A fresh install gets every table from the models (``create_all``); an existing
database only gets what the migrations in ``core/database.py`` add to it. Run
on an empty database, as a plain migration check would, ``create_all`` builds
everything first and the migrations have nothing left to do, so a migration
that is missing or adds a column differently goes unnoticed.

This builds a database with an older release's code, upgrades it with the
current code twice, and compares its schema with a fresh install: every column
(type with length, precision and element type, nullability, default), and how
many of each constraint and index there are. Constraints and indexes are
compared by definition, so only a name differing is not drift. A restart must
leave every schema, fresh or upgraded, exactly as the first start left it.

Drift that existed when this check was introduced is listed in
scripts/postgres_schema_drift.txt, each line the object and exactly how it
differs. Drift not listed there fails, including a listed object that now
differs in another way, and so does a listed line that no longer drifts:
remove it.

Usage (needs git with the release tags and a PostgreSQL server the given user
may create databases on; every database it creates is dropped again):

    python scripts/check_postgres_upgrade.py --url postgresql://user:pw@host:5432/postgres

Never point it at a server holding data you care about.
"""

import argparse
import asyncio
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

REPO = Path(__file__).resolve().parents[1]
ALLOWLIST = REPO / "scripts" / "postgres_schema_drift.txt"
# The first release with PostgreSQL support: the oldest schema an install can have.
FIRST_PG_RELEASE = "v0.2.3"

_INIT_DB = """
import asyncio
from backend.app.core import database
from backend.app.core.config import settings
assert settings.database_url.startswith("postgresql+asyncpg://"), settings.database_url
asyncio.run(database.init_db())
"""


def newest_stable_release() -> str:
    tags = subprocess.run(
        ["git", "tag", "--list", "v*", "--sort=-v:refname"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.split()
    stable = [t for t in tags if re.fullmatch(r"v\d+(\.\d+)+", t)]
    if not stable:
        sys.exit("no release tags found; fetch them (git fetch --tags)")
    return stable[0]


def extract_release(tag: str, into: Path) -> Path:
    """The release's backend/ as a directory to run init_db from (git archive, read-only)."""
    archive = into / f"{tag}.tar"
    with archive.open("wb") as out:
        subprocess.run(["git", "archive", tag, "backend"], cwd=REPO, check=True, stdout=out)
    target = into / tag
    with tarfile.open(archive) as tar:
        tar.extractall(target, filter="data")
    return target


def run_init_db(code_root: Path, url: str, label: str) -> None:
    """Run init_db (create_all + migrations) of the code under code_root against url."""
    with tempfile.TemporaryDirectory() as data_dir:
        env = {**os.environ, "DATABASE_URL": url, "DATA_DIR": data_dir, "PYTHONPATH": str(code_root)}
        done = subprocess.run([sys.executable, "-c", _INIT_DB], cwd=data_dir, env=env, capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"init_db failed ({label}):\n{done.stdout}{done.stderr}")


def with_database(url: str, name: str, driver: str = "") -> str:
    parts = urlsplit(url)
    scheme = "postgresql" + (f"+{driver}" if driver else "")
    return urlunsplit((scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


async def dump_schema(url: str) -> dict[str, str]:
    """Every column, constraint and index of the public schema, as {object: definition}."""
    import asyncpg

    conn = await asyncpg.connect(url)
    try:
        schema: dict[str, str] = {}
        for row in await conn.fetch(
            "SELECT table_name, column_name, data_type, udt_name, character_maximum_length, numeric_precision, "
            "numeric_scale, datetime_precision, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_schema = 'public'"
        ):
            table, column, data_type, udt, length, precision, scale, dt_precision, nullable, default = row
            # udt_name names what data_type leaves generic: the element type of an
            # ARRAY (_int4), the enum or domain of a USER-DEFINED column.
            attributes = [f"udt {udt}"]
            if length is not None:
                attributes.append(f"length {length}")
            if precision is not None:
                attributes.append(f"precision {precision}")
            if scale is not None:
                attributes.append(f"scale {scale}")
            if dt_precision is not None:
                attributes.append(f"time precision {dt_precision}")
            schema[f"column {table}.{column}"] = (
                f"{data_type} ({', '.join(attributes)})"
                + (" NULL" if nullable == "YES" else " NOT NULL")
                + f" default {default}"
            )
        # Counted: two identical constraints or indexes are drift too (an
        # unnamed constraint that every start adds again, say).
        objects: Counter[str] = Counter()
        for row in await conn.fetch(
            "SELECT conrelid::regclass::text, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE connamespace = 'public'::regnamespace"
        ):
            objects[f"constraint {row[0]}: {row[1]}"] += 1
        for row in await conn.fetch("SELECT tablename, indexdef FROM pg_indexes WHERE schemaname = 'public'"):
            definition = re.sub(r"^CREATE (UNIQUE )?INDEX \S+ ON ", r"CREATE \1INDEX ON ", row[1])
            objects[f"index {row[0]}: {definition}"] += 1
        schema.update({key: f"{count}x" for key, count in objects.items()})
        return schema
    finally:
        await conn.close()


def drift(
    fresh: dict[str, str], upgraded: dict[str, str], labels: tuple[str, str] = ("fresh install", "upgraded")
) -> set[str]:
    """One line per object that differs between the two schemas: the object and how."""
    return {
        f"{key} | {labels[0]}: {fresh.get(key) or 'missing'}; {labels[1]}: {upgraded.get(key) or 'missing'}"
        for key in fresh.keys() | upgraded.keys()
        if fresh.get(key) != upgraded.get(key)
    }


def read_allowlist(path: Path) -> set[str]:
    """The allowlist's lines; '#' starts a comment only at the beginning of a line."""
    if not path.exists():
        return set()
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return {line for line in lines if line and not line.startswith("#")}


def compare(found: dict[str, set[str]], allowed: set[str]) -> list[str]:
    """Problems: drift (per start release) not in the allowlist, and allowlist lines no start shows."""
    problems = []
    for release, lines in sorted(found.items()):
        for line in sorted(lines - allowed):
            problems.append(f"upgraded from {release}: {line}")
    seen = set().union(*found.values()) if found else set()
    for line in sorted(allowed - seen):
        problems.append(f"no longer drifts, remove it from {ALLOWLIST.name}: {line}")
    return problems


def start_twice(url: str, label: str) -> tuple[dict[str, str], list[str]]:
    """Start the current code on url twice; the schema, and what the restart changed."""
    run_init_db(REPO, url, label)
    first = asyncio.run(dump_schema(with_database(url, urlsplit(url).path.lstrip("/"))))
    run_init_db(REPO, url, f"{label}, restarted")
    second = asyncio.run(dump_schema(with_database(url, urlsplit(url).path.lstrip("/"))))
    changed = drift(first, second, ("first start", "restart"))
    return second, [f"{label}: a restart changed the schema: {line}" for line in sorted(changed)]


async def _admin(url: str, *statements: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(url)
    try:
        for statement in statements:
            await conn.execute(statement)
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", required=True, help="PostgreSQL server URL (its database is only used to connect)")
    parser.add_argument(
        "--from-release", action="append", help=f"start release(s); default {FIRST_PG_RELEASE} and the newest stable"
    )
    args = parser.parse_args()
    releases = args.from_release or sorted({FIRST_PG_RELEASE, newest_stable_release()})
    admin_url = with_database(args.url, urlsplit(args.url).path.lstrip("/") or "postgres")

    databases = {"fresh": "bambuddy_check_fresh"} | {r: "bambuddy_check_" + re.sub(r"\W", "_", r) for r in releases}
    asyncio.run(_admin(admin_url, *(f"DROP DATABASE IF EXISTS {db}" for db in databases.values())))
    asyncio.run(_admin(admin_url, *(f"CREATE DATABASE {db}" for db in databases.values())))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            fresh, problems = start_twice(with_database(args.url, databases["fresh"], "asyncpg"), "fresh install")

            found = {}
            for release in releases:
                url = with_database(args.url, databases[release], "asyncpg")
                run_init_db(extract_release(release, Path(tmp)), url, f"{release} install")
                upgraded, changed = start_twice(url, f"upgrade from {release}")
                problems += changed
                found[release] = drift(fresh, upgraded)
                print(f"{release}: upgrade ok, {len(found[release])} object(s) differ from a fresh install")
    finally:
        asyncio.run(_admin(admin_url, *(f"DROP DATABASE IF EXISTS {db}" for db in databases.values())))

    problems += compare(found, read_allowlist(ALLOWLIST))
    if problems:
        print("\nSchema problems:\n  " + "\n  ".join(problems))
        print(
            "\nA new column or constraint needs a migration in core/database.py that builds it exactly as the "
            "model does. See the docstring of scripts/check_postgres_upgrade.py."
        )
        return 1
    print("Upgraded schemas match a fresh install (apart from the listed known drift).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
