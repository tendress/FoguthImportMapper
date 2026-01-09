from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, Set

import pandas as pd

from .mapper_logic import WealthboxLookups, _normalize_phone_value, _safe_lower_strip


def load_wealthbox_lookups_from_sqlite(db_path: str) -> WealthboxLookups:
    """Load Wealthbox lookup maps from a local SQLite FoguthForge.db.

    Mirrors the parsing behavior in import_mapper.py:
      - wb_phone_numbers(contact_id, phone_numbers_parsed)
      - wb_emails(contact_id, email_addresses_parsed)
      - wb_tags(contact_id, tags_parsed)

    Returns empty lookups if db/path or tables are missing.
    """

    phone_to_contact_ids: Dict[str, Set[int]] = {}
    email_to_contact_ids: Dict[str, Set[int]] = {}
    contact_id_to_tags: Dict[int, Set[str]] = {}

    p = Path(db_path)
    if not db_path or not p.exists():
        return WealthboxLookups(phone_to_contact_ids, email_to_contact_ids, contact_id_to_tags)

    try:
        conn = sqlite3.connect(str(p))
    except Exception:
        return WealthboxLookups(phone_to_contact_ids, email_to_contact_ids, contact_id_to_tags)

    phone_rows_loaded = 0
    email_rows_loaded = 0

    try:
        # Phones
        try:
            df = pd.read_sql_query("SELECT contact_id, phone_numbers_parsed FROM wb_phone_numbers", conn)
            phone_rows_loaded = int(len(df))
            for _, row in df.iterrows():
                cid = int(row["contact_id"])
                raw = row["phone_numbers_parsed"]
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw) if str(raw).strip().startswith("[") else [raw]
                except Exception:
                    parsed = [raw]
                for item in parsed:
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
            df = pd.read_sql_query("SELECT contact_id, email_addresses_parsed FROM wb_emails", conn)
            email_rows_loaded = int(len(df))
            for _, row in df.iterrows():
                cid = int(row["contact_id"])
                raw = row["email_addresses_parsed"]
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw) if str(raw).strip().startswith("[") else [raw]
                except Exception:
                    parsed = [raw]
                for item in parsed:
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
            df = pd.read_sql_query("SELECT contact_id, tags_parsed FROM wb_tags", conn)
            for _, row in df.iterrows():
                cid = int(row["contact_id"])
                raw = row["tags_parsed"]
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw) if str(raw).strip().startswith("[") else [raw]
                except Exception:
                    parsed = [raw]
                tags: Set[str] = set()
                for t in parsed:
                    if isinstance(t, dict):
                        tag = (
                            t.get("name")
                            or t.get("tag_name")
                            or t.get("title")
                            or t.get("label")
                            or ""
                        )
                        tag = str(tag).strip()
                        if tag:
                            tags.add(tag)
                    else:
                        s = str(t).strip()
                        if s:
                            tags.add(s)
                if tags:
                    contact_id_to_tags.setdefault(cid, set()).update(tags)
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
