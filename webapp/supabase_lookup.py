from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

import psycopg2
import psycopg2.extras

from .mapper_logic import WealthboxLookups


def _as_str_list(v: object) -> List[str]:
    if v is None:
        return []

    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if x is not None and str(x).strip() != ""]

    if isinstance(v, dict):
        # Some JSON blobs might be {"values": [...]} etc.
        flat: List[str] = []
        for item in v.values():
            flat.extend(_as_str_list(item))
        return flat

    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        # JSON array/object stored as text
        if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
            try:
                return _as_str_list(json.loads(s))
            except Exception:
                return [s]
        return [s]

    return [str(v)]


def _norm_email(e: str) -> str:
    return (e or "").strip().lower()


def _norm_phone(p: str) -> str:
    digits = "".join(ch for ch in (p or "") if ch.isdigit())
    if len(digits) > 10:
        digits = digits[-10:]
    return digits


def _fetch_rows(
    conn,
    table: str,
    value_col: str,
) -> List[Tuple[int, object]]:
    sql = f"SELECT contact_id, {value_col} AS v FROM {table}"  # nosec - fixed identifiers
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    out: List[Tuple[int, object]] = []
    for r in rows:
        try:
            cid = int(r["contact_id"])
        except Exception:
            continue
        out.append((cid, r.get("v")))
    return out


def load_wealthbox_lookups(database_url: str) -> WealthboxLookups:
    """Load Wealthbox match lookups from Supabase Postgres.

    Expected tables:
      - wb_phone_numbers(contact_id, phone_numbers_parsed)
      - wb_emails(contact_id, email_addresses_parsed)
      - wb_tags(contact_id, tags_parsed)

    Returns empty lookups if tables are missing or connection fails.
    """

    phone_to_contact_ids: Dict[str, Set[int]] = {}
    email_to_contact_ids: Dict[str, Set[int]] = {}
    contact_id_to_tags: Dict[int, Set[str]] = {}

    if not (database_url or "").strip():
        return WealthboxLookups(phone_to_contact_ids, email_to_contact_ids, contact_id_to_tags)

    try:
        conn = psycopg2.connect(database_url)
    except Exception:
        return WealthboxLookups(phone_to_contact_ids, email_to_contact_ids, contact_id_to_tags)

    try:
        with conn:
            # Phones
            try:
                for cid, v in _fetch_rows(conn, "wb_phone_numbers", "phone_numbers_parsed"):
                    for raw in _as_str_list(v):
                        p = _norm_phone(raw)
                        if not p:
                            continue
                        phone_to_contact_ids.setdefault(p, set()).add(cid)
            except Exception:
                pass

            # Emails
            try:
                for cid, v in _fetch_rows(conn, "wb_emails", "email_addresses_parsed"):
                    for raw in _as_str_list(v):
                        e = _norm_email(raw)
                        if not e:
                            continue
                        email_to_contact_ids.setdefault(e, set()).add(cid)
            except Exception:
                pass

            # Tags
            try:
                for cid, v in _fetch_rows(conn, "wb_tags", "tags_parsed"):
                    tags = {t.strip() for t in _as_str_list(v) if str(t).strip()}
                    if not tags:
                        continue
                    contact_id_to_tags.setdefault(cid, set()).update(tags)
            except Exception:
                pass

    finally:
        try:
            conn.close()
        except Exception:
            pass

    return WealthboxLookups(phone_to_contact_ids, email_to_contact_ids, contact_id_to_tags)
