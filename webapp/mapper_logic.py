from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any, Dict, Mapping, Optional, Set, Tuple

import pandas as pd


# Mirror import_mapper.py
TARGET_COLUMNS: list[str] = [
    "first_name",
    "last_name",
    "phone",
    "email",
    "address",
    "city",
    "state",
    "zip",
    "notes",
    "household_name",
    "household_role",
]

EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")


def _normalize_phone_value(v: Any) -> str:
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


def _safe_lower_strip(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip().lower()


def best_match(name: str, candidates: list[str]) -> Optional[str]:
    if not candidates:
        return None
    lower_map = {str(c).lower(): str(c) for c in candidates}
    if name.lower() in lower_map:
        return lower_map[name.lower()]
    matches = get_close_matches(name, candidates, n=1, cutoff=0.6)
    return matches[0] if matches else None


def auto_suggest(source_cols: list[str]) -> Dict[str, str]:
    suggestions: Dict[str, str] = {}
    for t in TARGET_COLUMNS:
        found = best_match(t, source_cols)
        if found:
            suggestions[t] = found
    return suggestions


def apply_mapping(df: pd.DataFrame, mapping: Mapping[str, Optional[str]]) -> pd.DataFrame:
    """Apply column mapping with the same semantics as import_mapper.py.

    - Renames mapped source columns to the TARGET_COLUMNS names
    - Ensures all TARGET_COLUMNS exist
    - Builds household_name and attendee/guest household pairing (if a Type column exists)
    - Adds _orig_index column preserving original row index
    """

    rename: Dict[str, str] = {}
    for target, src in mapping.items():
        if src and src != "(none)" and src in df.columns:
            rename[str(src)] = str(target)

    mapped = df.rename(columns=rename).copy()
    for col in TARGET_COLUMNS:
        if col not in mapped.columns:
            mapped[col] = pd.NA

    if "last_name" in mapped.columns and "first_name" in mapped.columns:
        last = mapped["last_name"].fillna("").astype(str).str.strip()
        first = mapped["first_name"].fillna("").astype(str).str.strip()
        combined = last.where(last != "", "") + (", " + first).where(first != "", "")
        mapped["household_name"] = combined.str.strip().replace("", pd.NA)

    type_col = next((c for c in mapped.columns if str(c).lower() == "type"), None)
    if (
        type_col
        and "last_name" in mapped.columns
        and "first_name" in mapped.columns
        and "household_role" in mapped.columns
    ):
        last_attendee_idx: Optional[int] = None
        last_attendee_first = ""
        last_attendee_last = ""
        for i in mapped.index:
            raw_type = mapped.at[i, type_col]
            tval = str(raw_type).strip().lower() if raw_type is not None else ""
            cur_last = str(mapped.at[i, "last_name"]).strip() if pd.notna(mapped.at[i, "last_name"]) else ""
            cur_first = str(mapped.at[i, "first_name"]).strip() if pd.notna(mapped.at[i, "first_name"]) else ""

            if tval == "attendee":
                last_attendee_idx = int(i)
                last_attendee_first = cur_first
                last_attendee_last = cur_last
                mapped.at[i, "household_role"] = "1"
            elif tval == "guest":
                if (
                    last_attendee_idx is not None
                    and last_attendee_last
                    and cur_last
                    and cur_last.lower() == last_attendee_last.lower()
                ):
                    common_last = last_attendee_last or cur_last
                    name_str = f"{common_last}, {cur_first} and {last_attendee_first}"
                    name_str = re.sub(r"\s+,\s+", ", ", name_str)
                    name_str = re.sub(r"\s+and\s+", " and ", name_str)
                    mapped.at[i, "household_name"] = name_str
                    mapped.at[last_attendee_idx, "household_name"] = name_str
                    mapped.at[last_attendee_idx, "household_role"] = "1"
                    mapped.at[i, "household_role"] = "4"
                    for addr_col in ("address", "city", "state", "zip"):
                        if addr_col in mapped.columns:
                            mapped.at[i, addr_col] = mapped.at[last_attendee_idx, addr_col]

    mapped = mapped.reset_index(drop=False).rename(columns={"index": "_orig_index"})
    return mapped


@dataclass(frozen=True)
class WealthboxLookups:
    phone_to_contact_ids: Mapping[str, Set[int]]
    email_to_contact_ids: Mapping[str, Set[int]]
    contact_id_to_tags: Mapping[int, Set[str]]
    phone_rows_loaded: int = 0
    email_rows_loaded: int = 0


def compute_wb_matches(
    mapped: pd.DataFrame, wb: WealthboxLookups
) -> Tuple[Set[int], Dict[int, str], Dict[int, str]]:
    """Match rows to Wealthbox contacts by phone/email like import_mapper.py."""

    match_rows: Set[int] = set()
    row_contacts: Dict[int, str] = {}
    row_tags: Dict[int, str] = {}

    for idx, row in mapped.iterrows():
        row_idx = int(idx)
        contact_ids: Set[int] = set()

        norm_phone = _normalize_phone_value(row.get("phone", ""))
        if norm_phone and len(norm_phone) >= 7:
            contact_ids |= wb.phone_to_contact_ids.get(norm_phone, set())

        norm_email = _safe_lower_strip(row.get("email", ""))
        if norm_email and "@" in norm_email:
            contact_ids |= wb.email_to_contact_ids.get(norm_email, set())

        if not contact_ids:
            continue

        match_rows.add(row_idx)
        row_contacts[row_idx] = ",".join(str(i) for i in sorted(contact_ids))

        tags: Set[str] = set()
        for cid in contact_ids:
            tags |= wb.contact_id_to_tags.get(int(cid), set())
        row_tags[row_idx] = ",".join(sorted(tags))

    return match_rows, row_contacts, row_tags


def simple_validate(mapped: pd.DataFrame):
    checks = []
    for c in TARGET_COLUMNS:
        if c in mapped.columns:
            checks.append((f"Missing in {c}", int(mapped[c].isna().sum())))
        else:
            checks.append((f"Missing column {c}", "Not mapped"))
    if "email" in mapped.columns:
        n_bad = (~mapped["email"].dropna().astype(str).str.match(EMAIL_RE)).sum()
        checks.append(("Invalid email count", int(n_bad)))
    if "phone" in mapped.columns:
        digits_only = mapped["phone"].fillna("").astype(str).str.replace(r"\D", "", regex=True)
        too_short = (digits_only.str.len() < 7) & (digits_only.str.len() > 0)
        checks.append(("Phone values with <7 digits", int(too_short.sum())))
    return checks


def export_csv_bytes(mapped: pd.DataFrame) -> bytes:
    df = mapped.copy()
    if "_orig_index" in df.columns:
        df = df.drop(columns=["_orig_index"], errors="ignore")
    if "wb_tags" in df.columns:
        df = df.drop(columns=["wb_tags"], errors="ignore")

    # Export only target columns, in order.
    for c in TARGET_COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA
    final = df[TARGET_COLUMNS]
    if "zip" in final.columns:
        final["zip"] = final["zip"].apply(
            lambda x: str(x)[:-2] if str(x).endswith(".0") else str(x) if pd.notna(x) else x
        )
    return final.to_csv(index=False).encode("utf-8")
