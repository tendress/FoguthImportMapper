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


def _load_env() -> None:
    # For local dev: prefer workspace root .env
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    load_dotenv(Path(__file__).resolve().parent / ".." / ".env", override=False)


def _get_database_url() -> str:
    # Streamlit Cloud best practice: use st.secrets
    try:
        secret_val = st.secrets.get("DATABASE_URL")  # type: ignore[attr-defined]
    except Exception:
        secret_val = None
    if secret_val:
        return str(secret_val).strip()
    return os.getenv("DATABASE_URL", "").strip()


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
        if db_url:
            st.success("DATABASE_URL set")
        else:
            st.error("DATABASE_URL missing")
            st.caption("Set it in Streamlit secrets or as an environment variable.")

    uploaded = st.file_uploader("Upload CSV/XLSX", type=["csv", "xlsx", "xls"])
    if not uploaded:
        st.stop()

    df = _read_upload_to_df(uploaded).fillna(pd.NA).reset_index(drop=True)
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

    if not st.button("Preview"):
        st.stop()

    mapped = apply_mapping(df, mapping)

    if not db_url:
        st.error("Cannot run Wealthbox matching without DATABASE_URL")
        st.stop()

    wb = _cached_wb(db_url)
    match_rows, row_contacts, row_tags = compute_wb_matches(mapped, wb)

    mapped = mapped.copy()
    mapped["wb_tags"] = ""
    for ridx, tags in row_tags.items():
        mapped.at[ridx, "wb_tags"] = tags

    st.subheader("Summary")
    st.write({"matched_rows": len(match_rows), "total_rows": int(len(mapped))})

    checks = simple_validate(mapped)
    st.subheader("Checks")
    st.table(pd.DataFrame(checks, columns=["check", "value"]))

    st.subheader("Preview")
    display_cols = TARGET_COLUMNS + ["wb_tags"]
    preview = mapped[display_cols].head(200).copy()
    for c in preview.columns:
        preview[c] = preview[c].apply(_fmt_cell)
    st.dataframe(preview.style.apply(_style_matches(match_rows), axis=1), width="stretch")

    st.subheader("Export")
    out_bytes = export_csv_bytes(mapped)
    out_name = (uploaded.name.rsplit(".", 1)[0] if uploaded.name else "mapped") + "_mapped.csv"
    st.download_button("Download mapped CSV", data=out_bytes, file_name=out_name, mime="text/csv")


if __name__ == "__main__":
    main()
