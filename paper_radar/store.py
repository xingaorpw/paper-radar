"""SQLite persistence for papers and ratings."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path.home() / ".paper-radar" / "radar.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    published TEXT NOT NULL DEFAULT '',
    authors TEXT NOT NULL DEFAULT '[]',
    categories TEXT NOT NULL DEFAULT '[]',
    first_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ratings (
    paper_id TEXT PRIMARY KEY,
    action TEXT NOT NULL CHECK (action IN ('like', 'skip')),
    created_at TEXT NOT NULL,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_papers_published ON papers(published DESC);
CREATE INDEX IF NOT EXISTS idx_ratings_action ON ratings(action);
"""


def resolve_db_path(path: str | Path | None = None) -> Path:
    """Resolve the database path, honoring ``PAPER_RADAR_DB`` when set."""

    if path is not None:
        return Path(path).expanduser()
    override = os.environ.get("PAPER_RADAR_DB", "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_DB_PATH


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _connect(path: str | Path | None = None) -> sqlite3.Connection:
    db_path = resolve_db_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_SCHEMA)
    return connection


@contextmanager
def _connection(path: str | Path | None = None) -> Iterable[sqlite3.Connection]:
    connection = _connect(path)
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(path: str | Path | None = None) -> Path:
    """Create the database and tables if needed; return the resolved path."""

    with _connection(path):
        pass
    return resolve_db_path(path)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    try:
        return [str(item) for item in value if str(item).strip()]
    except TypeError:
        return []


def _dump_list(value: Any) -> str:
    return json.dumps(_as_list(value), ensure_ascii=False)


def _load_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return _as_list(loaded)


def _row_to_paper(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "summary": row["summary"],
        "published": row["published"],
        "authors": _load_list(row["authors"]),
        "categories": _load_list(row["categories"]),
        "first_seen": row["first_seen"],
    }


def upsert_papers(
    papers: Iterable[Mapping[str, Any]], path: str | Path | None = None
) -> int:
    """Insert or refresh papers and return the number of newly added rows."""

    now = _utc_now()
    records: list[tuple[str, str, str, str, str, str, str]] = []
    for paper in papers:
        if not isinstance(paper, Mapping):
            continue
        paper_id = str(paper.get("id") or "").strip()
        if not paper_id:
            continue
        records.append(
            (
                paper_id,
                str(paper.get("title") or ""),
                str(paper.get("summary") or ""),
                str(paper.get("published") or ""),
                _dump_list(paper.get("authors")),
                _dump_list(paper.get("categories")),
                now,
            )
        )

    with _connection(path) as connection:
        before = connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        connection.executemany(
            """
            INSERT INTO papers
                (id, title, summary, published, authors, categories, first_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                summary = excluded.summary,
                published = excluded.published,
                authors = excluded.authors,
                categories = excluded.categories
            """,
            records,
        )
        after = connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    return int(after - before)


def all_papers(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Return every paper, newest published date first."""

    with _connection(path) as connection:
        rows = connection.execute(
            "SELECT * FROM papers ORDER BY published DESC, id DESC"
        ).fetchall()
    return [_row_to_paper(row) for row in rows]


def get_paper(paper_id: str, path: str | Path | None = None) -> dict[str, Any] | None:
    """Look up one paper by id, or return ``None`` when it is not stored."""

    with _connection(path) as connection:
        row = connection.execute(
            "SELECT * FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
    return _row_to_paper(row) if row is not None else None


def set_rating(
    paper_id: str, action: str, path: str | Path | None = None
) -> None:
    """Store or replace a rating.  The latest rating wins."""

    if action not in {"like", "skip"}:
        raise ValueError("action 只能是 like 或 skip")
    with _connection(path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        if exists is None:
            raise ValueError(f"找不到论文 {paper_id}，请先运行 fetch 入库。")
        connection.execute(
            """
            INSERT INTO ratings (paper_id, action, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(paper_id) DO UPDATE SET
                action = excluded.action,
                created_at = excluded.created_at
            """,
            (paper_id, action, _utc_now()),
        )


def ratings(path: str | Path | None = None) -> dict[str, dict[str, str]]:
    """Return ratings keyed by paper id."""

    with _connection(path) as connection:
        rows = connection.execute(
            "SELECT paper_id, action, created_at FROM ratings"
        ).fetchall()
    return {
        row["paper_id"]: {
            "action": row["action"],
            "created_at": row["created_at"],
        }
        for row in rows
    }
