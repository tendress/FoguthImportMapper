# Streamlit Import Mapper

Entrypoint: `streamlit_import_mapper.py`

## Run locally

```powershell
D:/OrionAPI/.venv/Scripts/python.exe -m streamlit run .\streamlit_import_mapper.py
```

## Configure Supabase connection

Preferred (Streamlit hosting): set `DATABASE_URL` in Streamlit Secrets.

Example `secrets.toml` (local) or Streamlit Cloud Secrets:

```toml
DATABASE_URL = "postgresql://USER:PASSWORD@HOST:5432/DBNAME"
```

Local dev: set `DATABASE_URL` in `D:\OrionAPI\.env`.

The app uses these Supabase tables:
- `wb_phone_numbers(contact_id, phone_numbers_parsed)`
- `wb_emails(contact_id, email_addresses_parsed)`
- `wb_tags(contact_id, tags_parsed)`

## Notes

- Preview highlights rows that match an existing Wealthbox contact by phone or email.
- Export excludes internal helper columns (same behavior as the webapp export).
