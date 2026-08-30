from __future__ import annotations

from pathlib import Path

import pytest

from productpulse.ingest import connect_database, ingest_csv

FIXTURES = Path(__file__).parent / "fixtures"
VALID_FIXTURES = FIXTURES / "valid"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "productpulse.duckdb"


@pytest.fixture
def connection(db_path: Path):
    conn = connect_database(db_path)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def load_valid_sources():
    """Load the hand-checkable fixture in dependency order."""

    def _load(conn):
        return {
            entity: ingest_csv(conn, entity, VALID_FIXTURES / f"{entity}.csv")
            for entity in ("users", "sessions", "events")
        }

    return _load
