"""Client and helpers for the official DILA / PISTE Legifrance API."""

from __future__ import annotations

import logging
import time

import requests

from .config import Settings

log = logging.getLogger("legifrance.api")


# --------------------------------------------------------------------------- #
# Schema-tolerant JSON helpers (the API's field names vary across versions)
# --------------------------------------------------------------------------- #

def first_present(d: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return None


def collect_texts(node, acc: list[dict]) -> None:
    """Recursively walk a jorfCont response and collect every JO text node,
    capturing its id ('JORFTEXT...') and a human-readable title."""
    if isinstance(node, dict):
        node_id = first_present(node, ("id", "cid", "textCid"))
        title = first_present(node, ("title", "titre", "intTitre", "pathTitle"))
        if node_id and node_id.startswith("JORFTEXT"):
            acc.append({"id": node_id, "title": title or ""})
        for value in node.values():
            collect_texts(value, acc)
    elif isinstance(node, list):
        for item in node:
            collect_texts(item, acc)


def stringify(node) -> str:
    """Flatten any JSON structure into one searchable string of its text values."""
    parts: list[str] = []

    def walk(n) -> None:
        if isinstance(n, str):
            parts.append(n)
        elif isinstance(n, dict):
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(node)
    return " ".join(parts)


def edition_id(jo: dict) -> str | None:
    return first_present(jo, ("id", "cid", "jorfContId"))


def edition_label(jo: dict) -> str:
    # "titre" already reads e.g. "JORF n°0143 du 20 juin 2026" -- use it as-is.
    titre = first_present(jo, ("titre", "title"))
    if titre:
        return titre
    num = first_present(jo, ("num", "numero", "numJo", "number")) or "?"
    date_ms = jo.get("datePubli") or jo.get("dateParution")
    if isinstance(date_ms, (int, float)):
        date = time.strftime("%Y-%m-%d", time.gmtime(date_ms / 1000))
    else:
        date = "?"
    return f"JORF n°{num} ({date})"


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #

class LegifranceClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.piste_client_id or not settings.piste_client_secret:
            raise RuntimeError(
                "Missing PISTE_CLIENT_ID / PISTE_CLIENT_SECRET."
            )
        self.settings = settings
        self.session = requests.Session()
        self._token: str | None = None
        self._token_expiry = 0.0

    # -- auth ---------------------------------------------------------------
    def _token_valid(self) -> bool:
        return self._token is not None and time.time() < self._token_expiry - 30

    def _authenticate(self) -> None:
        s = self.settings
        log.info("Requesting OAuth token (%s)...", "sandbox" if s.piste_sandbox else "prod")
        resp = self.session.post(
            s.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": s.piste_client_id,
                "client_secret": s.piste_client_secret,
                "scope": "openid",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + int(data.get("expires_in", 3600))
        log.info("Authenticated; token valid for %ss.", data.get("expires_in"))

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    # -- requests -----------------------------------------------------------
    def post(self, path: str, payload: dict) -> dict:
        if not self._token_valid():
            self._authenticate()
        url = f"{self.settings.api_base}{path}"
        resp = self.session.post(url, json=payload, headers=self._headers(), timeout=60)
        if resp.status_code == 401:  # token expired mid-run -> refresh once
            self._authenticate()
            resp = self.session.post(url, json=payload, headers=self._headers(), timeout=60)
        resp.raise_for_status()
        if self.settings.request_delay_s:
            time.sleep(self.settings.request_delay_s)
        return resp.json()

    # -- high-level endpoints ----------------------------------------------
    def last_n_jo(self, n: int) -> list[dict]:
        """Most recent Journal Officiel editions, newest first."""
        data = self.post("/consult/lastNJo", {"nbElement": n})
        for key in ("results", "containers", "jo", "list"):
            if isinstance(data.get(key), list):
                return data[key]
        for value in data.values():  # fallback: any top-level list
            if isinstance(value, list):
                return value
        log.warning("Unexpected lastNJo response shape: keys=%s", list(data))
        return []

    def jorf_cont(self, container_id: str) -> dict:
        """Full contents (sommaire) of one JO edition."""
        return self.post("/consult/jorfCont", {"id": container_id})

    def jorf_text(self, text_cid: str) -> dict:
        """Full body of one JO text."""
        return self.post("/consult/jorf", {"textCid": text_cid})
