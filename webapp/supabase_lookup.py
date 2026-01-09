from __future__ import annotations

import json
import re
from typing import Dict, List, Set, Tuple

import pandas as pd
import psycopg2
import psycopg2.extras

from .mapper_logic import WealthboxLookups


def _as_list(v: object) -> List[object]:
    if v is None:
        return []

    if isinstance(v, (list, tuple)):
        return [x for x in v if x is not None]

    if isinstance(v, dict):
        # Some JSON blobs might be {"values": [...]} etc.
        flat: List[object] = []
        for item in v.values():
            flat.extend(_as_list(item))
        return flat

    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        # JSON array/object stored as text
        if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
            try:
                return _as_list(json.loads(s))
            except Exception:
                return [s]
        return [s]

    return [v]


def _safe_lower_strip(v: object) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip().lower()


def _normalize_phone_value(v: object) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    try:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = str(int(round(float(v))))
        s = str(v)
        if "e" in s.lower():
            try:
                s = str(int(round(float(s))))
            except Exception:
                pass
        digits = re.sub(r"[^\d]", "", s)
        if len(digits) >= 11 and digits.startswith("1"):
            digits = digits[1:]
        return digits
    except Exception:
        return ""


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
    phone_rows_loaded = 0
    email_rows_loaded = 0

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
                rows = _fetch_rows(conn, "wb_phone_numbers", "phone_numbers_parsed")
                phone_rows_loaded = len(rows)
                for cid, v in rows:
                    for item in _as_list(v):
                        if isinstance(item, dict) and "address" in item:
                            digits = _normalize_phone_value(item.get("address"))
                        else:
                            digits = _normalize_phone_value(item)
                        if digits:
                            phone_to_contact_ids.setdefault(digits, set()).add(cid)
            except Exception:
                pass

            # Emails
            try:
                rows = _fetch_rows(conn, "wb_emails", "email_addresses_parsed")
                email_rows_loaded = len(rows)
                for cid, v in rows:
                    for item in _as_list(v):
                        if isinstance(item, dict) and "address" in item:
                            em = _safe_lower_strip(item.get("address"))
                        else:
                            em = _safe_lower_strip(item)
                        if em:
                            email_to_contact_ids.setdefault(em, set()).add(cid)
            except Exception:
                pass

            # Tags
            try:
                for cid, v in _fetch_rows(conn, "wb_tags", "tags_parsed"):
                    tags: Set[str] = set()
                    for item in _as_list(v):
                        if isinstance(item, dict):
                            tag = (
                                item.get("name")
                                or item.get("tag_name")
                                or item.get("title")
                                or item.get("label")
                                or ""
                            )
                            tag = str(tag).strip()
                            if tag:
                                tags.add(tag)
                        else:
                            s = str(item).strip()
                            if s:
                                tags.add(s)
                    if not tags:
                        continue
                    contact_id_to_tags.setdefault(cid, set()).update(tags)
            except Exception:
                pass

            # Fallback: if parsed tables are empty, try wb_contacts (best-effort)
            if not phone_to_contact_ids or not email_to_contact_ids:
                try:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute("SELECT * FROM wb_contacts")
                        rows = cur.fetchall()
                    for r in rows:
                        cid_val = r.get("contact_id") or r.get("id") or r.get("rowid")
                        try:
                            cid = int(cid_val)
                        except Exception:
                            continue
                        for k, v in r.items():
                            lk = str(k).lower()
                            if "phone" in lk and v is not None:
                                for item in _as_list(v):
                                    digits = _normalize_phone_value(
                                        item.get("address") if isinstance(item, dict) else item
                                    )
                                    if digits:
                                        phone_to_contact_ids.setdefault(digits, set()).add(cid)
                            if "email" in lk and v is not None:
                                for item in _as_list(v):
                                    em = _safe_lower_strip(
                                        item.get("address") if isinstance(item, dict) else item
                                    )
                                    if em:
                                        email_to_contact_ids.setdefault(em, set()).add(cid)
                except Exception:
                    pass

    finally:
        try:
            conn.close()
        except Exception:
            pass

    return WealthboxLookups(
        phone_to_contact_ids,
        email_to_contact_ids,
        contact_id_to_tags,
        phone_rows_loaded=phone_rows_loaded,
        email_rows_loaded=email_rows_loaded,
    )
