"""Email alerts via the Resend HTTP API (https://resend.com)."""

from __future__ import annotations

import logging

import requests

from .config import Settings

log = logging.getLogger("legifrance.notifier")

RESEND_ENDPOINT = "https://api.resend.com/emails"


def _build_body(edition_label: str, hits: list[dict]) -> str:
    lines = [f"Name match(es) found in {edition_label}:", ""]
    for hit in hits:
        lines.append(f"  Text : {hit['title'] or hit['id']}")
        lines.append(f"  ID   : {hit['id']}")
        lines.append(f"  Link : https://www.legifrance.gouv.fr/jorf/id/{hit['id']}")
        for m in hit["matches"]:
            if m["level"] == "full":
                tag = "FULL NAME"
            else:
                tag = "PARTIAL (" + "/".join(m["matched_tokens"]) + ")"
            lines.append(f"    • {m['name']}  [{tag}]")
            if m.get("snippet"):
                lines.append(f"      ...{m['snippet']}...")
        lines.append("")
    return "\n".join(lines)


def send_alert(settings: Settings, edition_label: str, hits: list[dict]) -> bool:
    """Send an alert email. Returns True on success; never raises."""
    if not settings.email_enabled:
        return False
    if not (settings.resend_api_key and settings.recipients):
        log.warning("Email enabled but RESEND_API_KEY/EMAIL_TO incomplete; skipping.")
        return False

    body = _build_body(edition_label, hits)
    try:
        resp = requests.post(
            RESEND_ENDPOINT,
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.email_from,
                "to": settings.recipients,
                "subject": f"[Legifrance] Match in {edition_label}",
                "text": body,
            },
            timeout=30,
        )
        resp.raise_for_status()
        log.info("Alert email sent to %s (id=%s).",
                 ", ".join(settings.recipients), resp.json().get("id"))
        return True
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else ""
        log.error("Failed to send alert email: %s -- %s", exc, detail)
    except requests.RequestException as exc:
        log.error("Failed to send alert email: %s", exc)
    return False
