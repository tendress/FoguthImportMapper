from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Sequence, Set, Tuple

import pandas as pd


# Keep this list stable: it drives the Streamlit mapping UI.
TARGET_COLUMNS: list[str] = [
    "First Name",
    "Last Name",
    "Company",
    "Email",
    "Email 2",
    "Phone",
    "Phone 2",
    "Address 1",
    "Address 2",
    "City",
    "State",
    "Zip",
]


_EMAIL_RE = re.compile(r"\s+")
_NON_DIGIT_RE = re.compile(r"\D+")


def _norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").strip().lower())


def _norm_email(v: object) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip().lower()
    s = _EMAIL_RE.sub("", s)
    return s


def _norm_phone(v: object) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v)
    digits = _NON_DIGIT_RE.sub("", s)
    if len(digits) > 10:
        # Common cases include +1 prefix; keep last 10 digits.
        digits = digits[-10:]
    return digits


def auto_suggest(source_cols: Sequence[str]) -> Dict[str, str]:
    """Best-effort suggestions from uploaded columns to TARGET_COLUMNS."""

    src_by_key: Dict[str, str] = {}
    for c in source_cols:
        src_by_key[_norm_key(str(c))] = str(c)

    synonyms: Dict[str, Tuple[str, ...]] = {
        "First Name": ("firstname", "first", "givenname", "fname"),
        "Last Name": ("lastname", "last", "surname", "lname"),
        "Company": ("company", "organization", "business", "employer"),
        "Email": ("email", "emailaddress", "email1", "e-mail"),
        "Email 2": ("email2", "emailaddress2", "alternateemail", "secondaryemail"),
        "Phone": ("phone", "phonenumber", "phone1", "mobile", "cell"),
        "Phone 2": ("phone2", "phonenumber2", "alternatephone", "secondaryphone"),
        "Address 1": ("address1", "street", "streetaddress", "street1", "addr1"),
        "Address 2": ("address2", "street2", "suite", "unit", "addr2"),
        "City": ("city", "town"),
        "State": ("state", "province", "region"),
        "Zip": ("zip", "zipcode", "postal", "postalcode"),
    }

    out: Dict[str, str] = {}
    for target, keys in synonyms.items():
        for k in keys:
            match = src_by_key.get(_norm_key(k))
            if match:
                out[target] = match
                break

    # If there are exact name matches, prefer them.
    for target in TARGET_COLUMNS:
        key = _norm_key(target)
        if key in src_by_key:
            out[target] = src_by_key[key]

    return out


def apply_mapping(df: pd.DataFrame, mapping: Mapping[str, Optional[str]]) -> pd.DataFrame:
    """Return a DataFrame with TARGET_COLUMNS populated from source df."""

    out = pd.DataFrame(index=df.index)
    for target in TARGET_COLUMNS:
        src = mapping.get(target)
        if src and src in df.columns:
            out[target] = df[src]
        else:
            out[target] = pd.NA
    return out


@dataclass(frozen=True)
class WealthboxLookups:
    phone_to_contact_ids: Mapping[str, Set[int]]
    email_to_contact_ids: Mapping[str, Set[int]]
    contact_id_to_tags: Mapping[int, Set[str]]


def _iter_values(row: pd.Series, cols: Iterable[str]) -> Iterable[object]:
    for c in cols:
        if c in row.index:
            yield row[c]


def compute_wb_matches(
    mapped: pd.DataFrame, wb: WealthboxLookups
) -> Tuple[Set[int], Dict[int, str], Dict[int, str]]:
    """Match rows to Wealthbox contacts by email/phone.

    Returns:
      match_rows: set of row indexes that match at least one contact
      row_contacts: row index -> comma-separated contact id(s)
      row_tags: row index -> comma-separated tag(s) for the matched contact(s)
    """

    email_cols = [c for c in mapped.columns if "email" in c.lower()]
    phone_cols = [c for c in mapped.columns if "phone" in c.lower()]

    match_rows: Set[int] = set()
    row_contacts: Dict[int, str] = {}
    row_tags: Dict[int, str] = {}

    for row_pos in range(int(len(mapped))):
        row = mapped.iloc[row_pos]
        contact_ids: Set[int] = set()

        for v in _iter_values(row, email_cols):
            e = _norm_email(v)
            if not e:
                continue
            contact_ids |= wb.email_to_contact_ids.get(e, set())

        for v in _iter_values(row, phone_cols):
            p = _norm_phone(v)
            if not p:
                continue
            contact_ids |= wb.phone_to_contact_ids.get(p, set())

        if not contact_ids:
            continue

        match_rows.add(row_pos)
        row_contacts[row_pos] = ",".join(str(i) for i in sorted(contact_ids))

        tags: Set[str] = set()
        for cid in contact_ids:
            tags |= wb.contact_id_to_tags.get(int(cid), set())
        row_tags[row_pos] = ",".join(sorted(tags))

    return match_rows, row_contacts, row_tags


def simple_validate(mapped: pd.DataFrame):
    """Lightweight health checks for the mapped output."""

    def _count_blank(col: str) -> int:
        if col not in mapped.columns:
            return int(len(mapped))
        s = mapped[col]
        # Treat NA, empty string and whitespace-only as blank.
        return int((s.isna() | (s.astype(str).str.strip() == "")).sum())

    total = int(len(mapped))
    missing_first = _count_blank("First Name")
    missing_last = _count_blank("Last Name")

    email_cols = [c for c in mapped.columns if "email" in c.lower()]
    phone_cols = [c for c in mapped.columns if "phone" in c.lower()]

    any_email = pd.Series(False, index=mapped.index)
    for c in email_cols:
        any_email |= (~mapped[c].isna()) & (mapped[c].astype(str).str.strip() != "")

    any_phone = pd.Series(False, index=mapped.index)
    for c in phone_cols:
        any_phone |= (~mapped[c].isna()) & (mapped[c].astype(str).str.strip() != "")

    missing_both = int((~any_email & ~any_phone).sum())

    return [
        ("rows", total),
        ("missing_first_name", missing_first),
        ("missing_last_name", missing_last),
        ("missing_email_and_phone", missing_both),
    ]


def export_csv_bytes(mapped: pd.DataFrame) -> bytes:
    export_df = mapped.copy()
    drop_cols = [c for c in export_df.columns if str(c).startswith("wb_")]
    if drop_cols:
        export_df = export_df.drop(columns=drop_cols, errors="ignore")

    # Keep stable column order.
    cols = [c for c in TARGET_COLUMNS if c in export_df.columns]
    other = [c for c in export_df.columns if c not in cols]
    export_df = export_df[cols + other]

    return export_df.to_csv(index=False).encode("utf-8")
