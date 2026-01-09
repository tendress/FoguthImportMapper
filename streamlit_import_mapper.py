from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from webapp.mapper_logic import (
    TARGET_COLUMNS,
    apply_mapping,
    auto_suggest,
    compute_wb_matches,
    export_csv_bytes,
    simple_validate,
)
from webapp.supabase_lookup import load_wealthbox_lookups
from webapp.sqlite_lookup import load_wealthbox_lookups_from_sqlite


def _load_env() -> None:
    # For local dev: prefer workspace root .env
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    load_dotenv(Path(__file__).resolve().parent / ".." / ".env", override=False)


def _get_database_url() -> str:
    # Streamlit Cloud best practice: use st.secrets
    try:
        secrets = st.secrets  # type: ignore[attr-defined]
    except Exception:
        secrets = {}

    # Common Streamlit secrets patterns
    candidates = [
        secrets.get("DATABASE_URL"),
        secrets.get("database_url"),
        (secrets.get("supabase") or {}).get("DATABASE_URL") if isinstance(secrets.get("supabase"), dict) else None,
        (secrets.get("supabase") or {}).get("database_url") if isinstance(secrets.get("supabase"), dict) else None,
        (secrets.get("connections") or {}).get("postgresql", {}).get("url")
        if isinstance(secrets.get("connections"), dict)
        else None,
    ]

    for v in candidates:
        if v:
            return str(v).strip()

    # Environment variable fallbacks
    for k in ("DATABASE_URL", "database_url", "SUPABASE_DATABASE_URL"):
        v = os.getenv(k, "")
        if v.strip():
            return v.strip()

    return ""


def _get_sqlite_db_path() -> str:
    # Mirror import_mapper.py: FOGUTH_DATA_ROOT or script directory
    script_dir = Path(__file__).resolve().parent
    data_root = Path(os.environ.get("FOGUTH_DATA_ROOT", str(script_dir)))
    db_path = (data_root / "FoguthForge.db").resolve()
    return str(db_path)


@st.cache_data(ttl=600)
def _cached_wb(database_url: str):
    return load_wealthbox_lookups(database_url)


def _read_upload_to_df(uploaded_file) -> pd.DataFrame:
    name = (uploaded_file.name or "").lower()
    data = uploaded_file.getvalue()

    if name.endswith(".xlsx") or name.endswith(".xls"):
        return pd.read_excel(io.BytesIO(data))

    try:
        return pd.read_csv(io.BytesIO(data))
    except UnicodeDecodeError:
        return pd.read_csv(io.BytesIO(data), encoding="latin-1")


def _style_matches(match_rows: set[int]):
    def _row_style(row):
        idx = int(row.name)
        if idx in match_rows:
            return ["background-color: #d6ffd6"] * len(row)
        return [""] * len(row)

    return _row_style


def _fmt_cell(v) -> str:
    if pd.isna(v):
        return ""
    s = str(v)
    return s[:-2] if s.endswith(".0") else s


def main() -> None:
    _load_env()

    st.set_page_config(page_title="Import Mapper", layout="wide")
    st.title("Import Mapper")

    with st.sidebar:
        st.header("Connection")
        db_url = _get_database_url()
        sqlite_path = _get_sqlite_db_path()
        sqlite_exists = Path(sqlite_path).exists()

        if db_url:
            st.success("DATABASE_URL set (Postgres)")
        elif sqlite_exists:
            st.success("Using local FoguthForge.db (SQLite)")
            st.caption(f"Detected: {sqlite_path}")
        else:
            st.error("DATABASE_URL missing")
            st.caption(
                "Set DATABASE_URL in Streamlit Secrets (or secrets.toml) or as an environment variable. "
                "For local mode, place FoguthForge.db under FOGUTH_DATA_ROOT (or next to this script)."
            )

    uploaded = st.file_uploader("Upload CSV/XLSX", type=["csv", "xlsx", "xls"])
    if not uploaded:
        st.stop()

    df = _read_upload_to_df(uploaded).fillna(pd.NA)
    st.caption(f"Loaded {len(df):,} rows × {len(df.columns):,} columns")

    source_cols = list(df.columns.astype(str))
    suggestions = auto_suggest(source_cols)

    st.subheader("Map columns")

    mapping: Dict[str, Optional[str]] = {}
    cols = st.columns(2)
    for i, target in enumerate(TARGET_COLUMNS):
        with cols[i % 2]:
            options = ["(none)"] + source_cols
            default = suggestions.get(target)
            default_index = options.index(default) if default in options else 0
            choice = st.selectbox(
                label=target,
                options=options,
                index=default_index,
                key=f"map__{target}",
            )
            mapping[target] = None if choice == "(none)" else str(choice)

    apply_clicked = st.button("Apply Mapping")
    if not apply_clicked:
        st.stop()

    mapped = apply_mapping(df, mapping)

    if db_url:
        wb = _cached_wb(db_url)
    else:
        sqlite_path = _get_sqlite_db_path()
        wb = load_wealthbox_lookups_from_sqlite(sqlite_path) if Path(sqlite_path).exists() else None
    if wb is None:
        match_rows, row_contacts, row_tags = set(), {}, {}
    else:
        match_rows, row_contacts, row_tags = compute_wb_matches(mapped, wb)

    mapped = mapped.copy()
    mapped["wb_tags"] = ""
    for ridx, tags in row_tags.items():
        mapped.at[int(ridx), "wb_tags"] = tags

    st.subheader("Summary")
    st.write({"matched_rows": len(match_rows), "total_rows": int(len(mapped))})

    checks = simple_validate(mapped)
    if wb is not None:
        checks.append(("WB phone rows loaded", getattr(wb, "phone_rows_loaded", 0)))
        checks.append(("WB email rows loaded", getattr(wb, "email_rows_loaded", 0)))
    st.subheader("Checks")
    st.table(pd.DataFrame(checks, columns=["check", "value"]))

    st.subheader("Preview")
    highlight = st.checkbox("Highlight Wealthbox matches", value=True)
    display_cols = TARGET_COLUMNS + ["wb_tags"]
    preview = mapped[display_cols].copy()
    for c in preview.columns:
        preview[c] = preview[c].apply(_fmt_cell)
    if highlight and match_rows:
        st.dataframe(preview.style.apply(_style_matches(match_rows), axis=1), width="stretch")
    else:
        st.dataframe(preview, width="stretch")

    st.subheader("Export")
    out_bytes = export_csv_bytes(mapped)
    out_name = (uploaded.name.rsplit(".", 1)[0] if uploaded.name else "mapped") + "_mapped.csv"
    st.download_button("Download mapped CSV", data=out_bytes, file_name=out_name, mime="text/csv")


if __name__ == "__main__":
    main()
