"""
State persistence.

Two interchangeable backends, chosen by whether DATABASE_URL is set:

* PostgresStorage -- required in production (Render's filesystem is ephemeral,
  so processed-edition state and the match log MUST live in an external DB).
* FileStorage -- zero-setup fallback for local development.

Both track which JO editions have been processed (so we never re-alert) and a
log of every match found (surfaced via the /status endpoint).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .config import Settings

log = logging.getLogger("legifrance.storage")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage(Protocol):
    def get_processed(self) -> set[str]: ...
    def mark_processed(self, edition_id: str, label: str) -> None: ...
    def log_matches(self, edition_id: str, edition_label: str, hits: list[dict]) -> None: ...
    def recent_matches(self, limit: int = 50) -> list[dict]: ...
    def stats(self) -> dict: ...


# --------------------------------------------------------------------------- #
# File backend (local dev)
# --------------------------------------------------------------------------- #

class FileStorage:
    def __init__(self, state_file: str, matches_file: str) -> None:
        self.state_path = Path(state_file)
        self.matches_path = Path(matches_file)

    def get_processed(self) -> set[str]:
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                return set(data.get("processed", []))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Could not read state file (%s); starting fresh.", exc)
        return set()

    def mark_processed(self, edition_id: str, label: str) -> None:
        processed = self.get_processed()
        processed.add(edition_id)
        self.state_path.write_text(
            json.dumps({"processed": sorted(processed)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def log_matches(self, edition_id: str, edition_label: str, hits: list[dict]) -> None:
        with self.matches_path.open("a", encoding="utf-8") as fh:
            for hit in hits:
                for m in hit["matches"]:
                    fh.write(json.dumps({
                        "found_at": _now(),
                        "edition_id": edition_id,
                        "edition_label": edition_label,
                        "text_id": hit["id"],
                        "text_title": hit["title"],
                        "name": m["name"],
                        "level": m["level"],
                        "matched_tokens": m["matched_tokens"],
                        "snippet": m.get("snippet", ""),
                    }, ensure_ascii=False) + "\n")

    def recent_matches(self, limit: int = 50) -> list[dict]:
        if not self.matches_path.exists():
            return []
        lines = self.matches_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(ln) for ln in lines[-limit:]][::-1]

    def stats(self) -> dict:
        return {
            "backend": "file",
            "processed_count": len(self.get_processed()),
            "match_count": len(self.recent_matches(limit=10**6)),
        }


# --------------------------------------------------------------------------- #
# Postgres backend (production)
# --------------------------------------------------------------------------- #

class PostgresStorage:
    def __init__(self, dsn: str) -> None:
        # Imported lazily so local/file-only users don't need the driver loaded.
        import psycopg  # noqa: F401  (validates the dependency is installed)

        self.dsn = dsn
        self._init_schema()

    def _connect(self):
        import psycopg

        return psycopg.connect(self.dsn, autocommit=True)

    def _init_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_editions (
                    edition_id   TEXT PRIMARY KEY,
                    label        TEXT,
                    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS match_log (
                    id             BIGSERIAL PRIMARY KEY,
                    found_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                    edition_id     TEXT,
                    edition_label  TEXT,
                    text_id        TEXT,
                    text_title     TEXT,
                    name           TEXT,
                    level          TEXT,
                    matched_tokens TEXT,
                    snippet        TEXT
                );
                """
            )
        log.info("Postgres schema ready.")

    def get_processed(self) -> set[str]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT edition_id FROM processed_editions;")
            return {row[0] for row in cur.fetchall()}

    def mark_processed(self, edition_id: str, label: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO processed_editions (edition_id, label)
                VALUES (%s, %s)
                ON CONFLICT (edition_id) DO NOTHING;
                """,
                (edition_id, label),
            )

    def log_matches(self, edition_id: str, edition_label: str, hits: list[dict]) -> None:
        rows = []
        for hit in hits:
            for m in hit["matches"]:
                rows.append((
                    edition_id, edition_label, hit["id"], hit["title"],
                    m["name"], m["level"], json.dumps(m["matched_tokens"]),
                    m.get("snippet", ""),
                ))
        if not rows:
            return
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO match_log
                    (edition_id, edition_label, text_id, text_title,
                     name, level, matched_tokens, snippet)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                """,
                rows,
            )

    def recent_matches(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT found_at, edition_id, edition_label, text_id, text_title,
                       name, level, matched_tokens, snippet
                FROM match_log ORDER BY id DESC LIMIT %s;
                """,
                (limit,),
            )
            cols = [d.name for d in cur.description]
            out = []
            for row in cur.fetchall():
                rec = dict(zip(cols, row))
                rec["found_at"] = rec["found_at"].isoformat()
                try:
                    rec["matched_tokens"] = json.loads(rec["matched_tokens"])
                except (TypeError, json.JSONDecodeError):
                    pass
                out.append(rec)
            return out

    def stats(self) -> dict:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM processed_editions;")
            processed = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM match_log;")
            matches = cur.fetchone()[0]
        return {"backend": "postgres", "processed_count": processed, "match_count": matches}


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #

def get_storage(settings: Settings) -> Storage:
    if settings.database_url:
        log.info("Using Postgres storage backend.")
        return PostgresStorage(settings.database_url)
    log.info("Using file storage backend (%s).", settings.state_file)
    return FileStorage(settings.state_file, settings.matches_file)
