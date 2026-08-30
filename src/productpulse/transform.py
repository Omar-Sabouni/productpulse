"""Execute the versioned SQL analytics transformations in order."""

from __future__ import annotations

from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQL_DIR = PROJECT_ROOT / "sql"


def run_transformations(
    conn: duckdb.DuckDBPyConnection,
    sql_dir: Path | None = None,
    *,
    manage_transaction: bool = True,
) -> list[str]:
    """Run all ordered SQL files and return their filenames.

    By default the transformation batch owns its transaction. Callers that
    need ingestion and transformation to share one outer transaction can pass
    ``manage_transaction=False``.
    """

    directory = Path(sql_dir) if sql_dir is not None else DEFAULT_SQL_DIR
    sql_files = sorted(directory.glob("*.sql"))
    if not sql_files:
        raise FileNotFoundError(f"No SQL transformations found in {directory}")

    conn.execute("SET TimeZone = 'UTC'")
    if manage_transaction:
        conn.execute("BEGIN TRANSACTION")
    try:
        for path in sql_files:
            conn.execute(path.read_text(encoding="utf-8"))
        if manage_transaction:
            conn.execute("COMMIT")
    except Exception:
        if manage_transaction:
            conn.execute("ROLLBACK")
        raise
    return [path.name for path in sql_files]
