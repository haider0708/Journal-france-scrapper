"""Scan orchestration: fetch new editions, search texts, persist + alert."""

from __future__ import annotations

import logging

import requests

from .config import Settings
from .legifrance import (
    LegifranceClient,
    collect_texts,
    edition_id,
    edition_label,
    stringify,
)
from .matching import find_names, find_snippet
from .notifier import send_alert
from .storage import Storage

log = logging.getLogger("legifrance.monitor")


def process_edition(client: LegifranceClient, settings: Settings, jo: dict) -> list[dict]:
    """Search one edition; return the list of matching texts (may be empty)."""
    label = edition_label(jo)
    cont_id = edition_id(jo)
    log.info("Processing %s [%s]", label, cont_id)

    container = client.jorf_cont(cont_id)
    texts: list[dict] = []
    collect_texts(container, texts)
    seen: set[str] = set()
    texts = [t for t in texts if not (t["id"] in seen or seen.add(t["id"]))]
    log.info("  %d text(s) in this edition.", len(texts))

    hits: list[dict] = []
    for text in texts:
        try:
            body = client.jorf_text(text["id"])
        except requests.HTTPError as exc:
            log.warning("  Could not fetch %s: %s", text["id"], exc)
            body = {}
        full_text = f"{text['title']} {stringify(body)}"

        matches = find_names(full_text, settings.names)
        if matches:
            for m in matches:
                m["snippet"] = find_snippet(full_text, m["matched_tokens"][0])
            hits.append({"id": text["id"], "title": text["title"], "matches": matches})
            for m in matches:
                log.info(
                    "  >>> %s MATCH: '%s' (tokens: %s) in '%s' (%s)",
                    m["level"].upper(), m["name"], ", ".join(m["matched_tokens"]),
                    text["title"] or "?", text["id"],
                )

    if not hits:
        log.info("  No match in %s.", label)
    return hits


def run_scan(settings: Settings, storage: Storage, client: LegifranceClient | None = None) -> dict:
    """Process every newly-published edition. Resumable: state is committed
    per-edition, so an interrupted run continues where it left off."""
    if not settings.names:
        raise RuntimeError("SEARCH_NAMES is empty.")
    if client is None:
        client = LegifranceClient(settings)

    log.info("=" * 70)
    log.info("Run start. Searching for: %s", " ; ".join(settings.names))

    processed = storage.get_processed()
    editions = client.last_n_jo(settings.lookback_editions)
    log.info("Fetched %d recent edition(s).", len(editions))

    # Oldest-first so state stays consistent if interrupted.
    new_editions = [
        jo for jo in reversed(editions)
        if edition_id(jo) and edition_id(jo) not in processed
    ]
    summary = {
        "fetched": len(editions),
        "new_editions": len(new_editions),
        "editions_processed": 0,
        "matches": 0,
        "matched_editions": [],
    }
    if not new_editions:
        log.info("No new editions since last run. Nothing to do.")
        return summary
    log.info("%d new edition(s) to process.", len(new_editions))

    for jo in new_editions:
        cont_id = edition_id(jo)
        label = edition_label(jo)
        try:
            hits = process_edition(client, settings, jo)
        except requests.HTTPError as exc:
            log.error("Failed on %s: %s -- will retry next run.", label, exc)
            continue  # do NOT mark processed -> retried next run

        if hits:
            storage.log_matches(cont_id, label, hits)
            send_alert(settings, label, hits)
            summary["matches"] += sum(len(h["matches"]) for h in hits)
            summary["matched_editions"].append(label)

        storage.mark_processed(cont_id, label)
        summary["editions_processed"] += 1

    log.info("Run complete. %d match(es) across %d new edition(s).",
             summary["matches"], summary["editions_processed"])
    return summary
