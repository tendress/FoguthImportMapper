import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
import re
import sqlite3
import json
from pathlib import Path
import os
from typing import Any, List

TARGET_COLUMNS = [
    "first_name", "last_name", "phone", "email", "address", "city", "state", "zip",
    "notes", "household_name", "household_role"
]
EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")
# Resolve DB path relative to this script (or FOGUTH_DATA_ROOT if set)
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get("FOGUTH_DATA_ROOT", SCRIPT_DIR))
DB_PATH = str((DATA_ROOT / "FoguthForge.db").resolve())

def _conn():
    return sqlite3.connect(DB_PATH)

def _normalize_phone_value(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    try:
        # Numeric -> int string
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            # Avoid scientific notation issues
            v = str(int(round(float(v))))
        s = str(v)
        # Handle scientific notation like 1.2345E+09
        if 'e' in s.lower():
            try:
                s = str(int(round(float(s))))
            except Exception:
                pass
        digits = re.sub(r'[^\d]', '', s)
        # Drop leading country code 1 if 11+ digits
        if len(digits) >= 11 and digits.startswith('1'):
            digits = digits[1:]
        return digits
    except Exception:
        return ""

def _safe_lower_strip(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip().lower()

def load_file(path):
    if path.lower().endswith((".xls", ".xlsx")):
        xls = pd.read_excel(path, sheet_name=None)
        return xls[list(xls.keys())[0]]
    return pd.read_csv(path)

def best_match(name, candidates):
    from difflib import get_close_matches
    if not candidates: return None
    lower_map = {c.lower(): c for c in candidates}
    if name.lower() in lower_map: return lower_map[name.lower()]
    matches = get_close_matches(name, candidates, n=1, cutoff=0.6)
    return matches[0] if matches else None

def auto_suggest(source_cols):
    suggestions = {}
    for t in TARGET_COLUMNS:
        found = best_match(t, source_cols)
        suggestions[t] = found
    return suggestions

class ImportMapperApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Import & Column Mapper")
        self.geometry("1400x900")
        self.df = None
        self.mapped_df = None
        self.iid_to_index = {}
        self.wb_phone_data = None
        self.wb_email_data = None
        self.wb_tags_data = None
        self.mapping_vars = {}
        self.create_widgets()
        self.setup_treeview_tags()

    def create_widgets(self):
        main = ttk.PanedWindow(self, orient='horizontal')
        main.pack(fill='both', expand=True)

        # Left: mapping controls
        left = ttk.Frame(main, width=320)
        main.add(left, weight=0)
        ttk.Button(left, text="Open CSV / Excel", command=self.open_file).pack(fill='x', padx=8, pady=8)
        self.file_label = ttk.Label(left, text="No file loaded")
        self.file_label.pack(fill='x', padx=8, pady=(0,8))

        self.mapping_frame = ttk.LabelFrame(left, text="Column Mapping")
        self.mapping_frame.pack(fill='x', padx=8, pady=8)
        self.mapping_widgets = {}
        for t in TARGET_COLUMNS:
            row = ttk.Frame(self.mapping_frame)
            row.pack(fill='x', padx=2, pady=1)
            ttk.Label(row, text=t, width=16).pack(side='left')
            var = tk.StringVar()
            cb = ttk.Combobox(row, textvariable=var, values=[], state='readonly')
            cb.pack(side='left', fill='x', expand=True)
            self.mapping_vars[t] = var
            self.mapping_widgets[t] = cb

        ttk.Button(left, text="Apply Mapping", command=self.apply_mapping).pack(fill='x', padx=8, pady=8)
        ttk.Button(left, text="Clear Highlights", command=self.clear_highlights).pack(fill='x', padx=8, pady=2)
        ttk.Button(left, text="Export CSV", command=self.on_export).pack(fill='x', padx=8, pady=2)

        # Right: grid and validation
        right = ttk.Frame(main)
        main.add(right, weight=1)
        self.rows_label = ttk.Label(right, text="No data loaded")
        self.rows_label.pack(anchor='w', padx=8, pady=4)


        legend = ttk.Frame(right)
        legend.pack(anchor='w', padx=8, pady=(0,4))

        # Single legend entry for any Wealthbox match
        self.wb_match_color = "#90EE90"  # light green; tweak if you prefer
        tk.Label(legend, text=" Wealthbox Match ", bg=self.wb_match_color, relief="solid").pack(side='left', padx=5)

        # Right: grid and validation
        tree_frame = ttk.Frame(right)
        tree_frame.pack(fill='both', expand=True, padx=8, pady=8)

        # Treeview + scrollbars
        self.tree = ttk.Treeview(tree_frame, columns=[], show='headings')
        self.vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)

        # Grid placement
        self.tree.grid(row=0, column=0, sticky='nsew')
        self.vsb.grid(row=0, column=1, sticky='ns')
        self.hsb.grid(row=1, column=0, sticky='ew')

        # Make tree_frame expandable
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        self.tree.bind("<Double-1>", self.on_double_click)
        val_frame = ttk.LabelFrame(right, text="Validation Report")
        val_frame.pack(fill='x', padx=8, pady=8)
        self.val_text = tk.Text(val_frame, height=8, wrap='word')
        self.val_text.pack(fill='both', padx=6, pady=6)

    def setup_treeview_tags(self):
        # Single tag for any Wealthbox (phone or email) match
        bg = getattr(self, "wb_match_color", "#90EE90")
        self.tree.tag_configure("wb_match", background=bg)

    def open_file(self):
        path = filedialog.askopenfilename(title="Select CSV or Excel", filetypes=[("CSV files","*.csv"),("Excel files","*.xlsx;*.xls")])
        if not path: return
        try:
            self.df = load_file(path)
        except Exception as e:
            messagebox.showerror("Load error", f"Could not read file: {e}")
            return
        self.file_label.config(text=path.split("/")[-1])
        self.rows_label.config(text=f"Loaded {len(self.df)} rows")
        self.populate_mapping_controls()

    def populate_mapping_controls(self):
        source_cols = list(self.df.columns.astype(str)) # type: ignore
        for t, cb in self.mapping_widgets.items():
            cb['values'] = ["(none)"] + source_cols
            cb.set("(none)")
        # Auto-suggest
        suggestions = auto_suggest(source_cols)
        for t, suggestion in suggestions.items():
            if suggestion:
                self.mapping_vars[t].set(suggestion)

    def apply_mapping(self):
        if self.df is None: return
        rename = {}
        for t, var in self.mapping_vars.items():
            v = var.get()
            if v and v != "(none)":
                rename[v] = t
        mapped = self.df.rename(columns=rename).copy()
        for col in TARGET_COLUMNS:
            if col not in mapped.columns:
                mapped[col] = pd.NA
        if 'last_name' in mapped.columns and 'first_name' in mapped.columns:
            last = mapped['last_name'].fillna('').astype(str).str.strip()
            first = mapped['first_name'].fillna('').astype(str).str.strip()
            combined = last.where(last != '', '') + (', ' + first).where(first != '', '')
            mapped['household_name'] = combined.str.strip().replace('', pd.NA)
        # Attendee/Guest household pairing logic
        # If a row has Type == 'guest' and the last previous 'attendee' shares the last name,
        # set household_name to "Last, guestFirst and attendeeFirst" and roles (attendee=1, guest=4).
        type_col = next((c for c in mapped.columns if str(c).lower() == 'type'), None)
        if type_col and 'last_name' in mapped.columns and 'first_name' in mapped.columns and 'household_role' in mapped.columns:
            last_attendee_idx = None
            last_attendee_first = ''
            last_attendee_last = ''
            for i in mapped.index:
                raw_type = mapped.at[i, type_col]
                tval = str(raw_type).strip().lower() if raw_type is not None else ''
                cur_last = str(mapped.at[i, 'last_name']).strip() if pd.notna(mapped.at[i, 'last_name']) else ''
                cur_first = str(mapped.at[i, 'first_name']).strip() if pd.notna(mapped.at[i, 'first_name']) else ''
                if tval == 'attendee':
                    last_attendee_idx = i
                    last_attendee_first = cur_first
                    last_attendee_last = cur_last
                    mapped.at[i, 'household_role'] = '1'
                elif tval == 'guest':
                    if last_attendee_idx is not None and last_attendee_last and cur_last and cur_last.lower() == last_attendee_last.lower():
                        common_last = last_attendee_last or cur_last
                        name_str = f"{common_last}, {cur_first} and {last_attendee_first}"
                        name_str = re.sub(r"\s+,\s+", ", ", name_str)
                        name_str = re.sub(r"\s+and\s+", " and ", name_str)
                        mapped.at[i, 'household_name'] = name_str
                        mapped.at[last_attendee_idx, 'household_name'] = name_str
                        mapped.at[last_attendee_idx, 'household_role'] = '1'
                        mapped.at[i, 'household_role'] = '4'
                        # Copy address fields from attendee to guest
                        for addr_col in ('address', 'city', 'state', 'zip'):
                            if addr_col in mapped.columns:
                                mapped.at[i, addr_col] = mapped.at[last_attendee_idx, addr_col]
        mapped = mapped.reset_index(drop=False).rename(columns={'index': '_orig_index'})
        self.mapped_df = mapped
        self.populate_tree(mapped)
        self.validate_and_highlight()

    def populate_tree(self, df, contact_matches=None, wb_matches=None):
        self.tree.delete(*self.tree.get_children())

        display_order = TARGET_COLUMNS + ["wb_tags"]
        self.tree["columns"] = display_order

        # Ensure scrollbars remain bound after column changes
        if hasattr(self, "vsb") and hasattr(self, "hsb"):
            self.tree.configure(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)

        for c in display_order:
            self.tree.heading(c, text=c)
            if c in ['address', 'notes', 'household_name']:
                self.tree.column(c, width=250, anchor='w', minwidth=200, stretch=False)
            elif c in ['first_name', 'last_name', 'email']:
                self.tree.column(c, width=150, anchor='w', minwidth=120, stretch=False)
            elif c == "wb_tags":
                self.tree.column(c, width=240, anchor='w', minwidth=180, stretch=False)
            else:
                self.tree.column(c, width=120, anchor='w', minwidth=100, stretch=False)

        for i, row in df.iterrows():
            iid = f"r{i}"
            vals = [self._fmt_cell(row.get(c)) for c in TARGET_COLUMNS]

            # WB tags for this row
            tags_str = ""
            if contact_matches and i in contact_matches:
                all_tags = set()
                for cid in contact_matches[i]:
                    all_tags.update(self.get_tags_for_contact(cid))
                tags_str = ", ".join(sorted(all_tags))
            vals.append(tags_str)

            self.tree.insert("", "end", iid=iid, values=vals)
            self.iid_to_index[iid] = row.get('_orig_index', i)

            # Unified highlight for any Wealthbox match
            row_tags = ("wb_match",) if (wb_matches and i in wb_matches) else ()
            self.tree.item(iid, tags=row_tags)

        self.tree.update_idletasks()

    def validate_and_highlight(self):
        wb_phone = self.load_wb_phone_data()
        wb_email = self.load_wb_email_data()
        self.load_wb_tags_data()

        phone_matches, email_matches, contact_matches = set(), set(), {}
        for idx, row in self.mapped_df.iterrows(): # type: ignore
            row_contact_ids = set()

            phone = row.get('phone', '')
            norm_phone = _normalize_phone_value(phone)
            if norm_phone and len(norm_phone) >= 7 and not wb_phone.empty:
                # Normalize WB phones again just in case
                wb_phone['phone'] = wb_phone['phone'].astype(str).apply(_normalize_phone_value)
                wb_match = wb_phone[wb_phone['phone'] == norm_phone]
                if not wb_match.empty:
                    phone_matches.add(idx)
                    row_contact_ids.update(wb_match['contact_id'].tolist())

            email = row.get('email', '')
            norm_email = _safe_lower_strip(email)
            if norm_email and '@' in norm_email and not wb_email.empty:
                wb_email['email'] = wb_email['email'].astype(str).str.strip().str.lower()
                wb_match = wb_email[wb_email['email'] == norm_email]
                if not wb_match.empty:
                    email_matches.add(idx)
                    row_contact_ids.update(wb_match['contact_id'].tolist())

            if row_contact_ids:
                contact_matches[idx] = list(row_contact_ids)

        # Unified set of Wealthbox matches
        wb_matches = phone_matches | email_matches

        # Reload grid with tags and unified highlight
        self.populate_tree(self.mapped_df, contact_matches, wb_matches)

        checks = self.simple_validate(self.mapped_df)
        # Append Wealthbox diagnostics
        try:
            wb_phone_n = 0 if wb_phone is None else len(wb_phone)
            wb_email_n = 0 if wb_email is None else len(wb_email)
            checks.append(("WB phone rows loaded", wb_phone_n))
            checks.append(("WB email rows loaded", wb_email_n))
        except Exception:
            pass
        self.val_text.delete('1.0', tk.END)
        self.val_text.insert('1.0', "\n".join(f"{k}: {v}" for k, v in checks))

    def on_double_click(self, event): # type: ignore
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell": return
        row_iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not row_iid or not col: return
        col_index = int(col.replace('#', '')) - 1
        col_name = self.tree["columns"][col_index]
        if col_name == "wb_tags":
            tags_val = self.tree.set(row_iid, col_name)
            if not tags_val:
                messagebox.showinfo("WB Tags", "No tags for this row.")
            else:
                messagebox.showinfo("WB Tags", tags_val)
            return
        # ...existing editable cell logic...
        bbox = self.tree.bbox(row_iid, column=col_name)
        if not bbox: return
        x, y, width, height = bbox
        cur_val = self.tree.set(row_iid, col_name)
        entry = tk.Entry(self.tree)
        entry.place(x=x, y=y, width=width, height=height)
        entry.insert(0, cur_val) # type: ignore
        entry.focus_set()
        def save_edit(event=None):
            new_val = entry.get()
            entry.destroy()
            self.tree.set(row_iid, col_name, new_val)
            mapped_index = self.iid_to_index.get(row_iid)
            if mapped_index is None: return
            mask = self.mapped_df['_orig_index'] == mapped_index # type: ignore
            if mask.any():
                self.mapped_df.loc[mask, col_name] = new_val # type: ignore
        def cancel_edit(event=None): entry.destroy()
        entry.bind("<Return>", save_edit)
        entry.bind("<FocusOut>", save_edit)
        entry.bind("<Escape>", cancel_edit)

    def add_tags_column_to_grid(self, contact_matches):
        if "wb_tags" not in self.tree["columns"]:
            self.tree["columns"] = list(self.tree["columns"]) + ["wb_tags"]
            self.tree.heading("wb_tags", text="WB Tags")
            self.tree.column("wb_tags", width=200, anchor='w', minwidth=150)
        for item in self.tree.get_children():
            idx = int(item.replace('r', ''))
            tags_str = ""
            if idx in contact_matches:
                all_tags = set()
                for cid in contact_matches[idx]:
                    all_tags.update(self.get_tags_for_contact(cid))
                tags_str = ", ".join(sorted(all_tags))
            self.tree.set(item, "wb_tags", tags_str)
        self.tree.update_idletasks()

    def clear_highlights(self):
        for item in self.tree.get_children():
            self.tree.item(item, tags=())
        if "wb_tags" in self.tree["columns"]:
            cols = [c for c in self.tree["columns"] if c != "wb_tags"]
            self.tree["columns"] = cols
            for col in cols:
                self.tree.heading(col, text=col)
            self.populate_tree(self.mapped_df)

    def load_wb_phone_data(self):
        if self.wb_phone_data is not None: return self.wb_phone_data
        try:
            conn = _conn()
            df = pd.read_sql_query("SELECT contact_id, phone_numbers_parsed FROM wb_phone_numbers", conn)
            conn.close()
            phone_data = []
            for _, row in df.iterrows():
                cid = row['contact_id']
                raw = row['phone_numbers_parsed']
                if not raw: continue
                try:
                    parsed = json.loads(raw) if raw.strip().startswith("[") else [raw]
                except Exception:
                    parsed = [raw]
                for p in parsed:
                    if isinstance(p, dict) and 'address' in p:
                        digits = _normalize_phone_value(p.get('address'))
                        if digits:
                            phone_data.append({'contact_id': cid, 'phone': digits})
                    elif isinstance(p, str):
                        digits = _normalize_phone_value(p)
                        if digits:
                            phone_data.append({'contact_id': cid, 'phone': digits})
            df_out = pd.DataFrame(phone_data)
            if df_out.empty:
                # Fallback: derive from wb_contacts
                try:
                    conn = _conn()
                    dfc = pd.read_sql_query("SELECT rowid AS contact_id, * FROM wb_contacts", conn)
                    conn.close()
                    phone_cols = [c for c in dfc.columns if 'phone' in str(c).lower()]
                    phone_rows = []
                    for _, r in dfc.iterrows():
                        for c in phone_cols:
                            v = r.get(c)
                            if v is None or (isinstance(v, float) and pd.isna(v)): continue
                            try:
                                # Try JSON array
                                parsed = json.loads(v) if isinstance(v, str) and v.strip().startswith('[') else [v]
                            except Exception:
                                parsed = [v]
                            for item in parsed:
                                if isinstance(item, dict):
                                    digits = _normalize_phone_value(item.get('address') or item.get('value') or item.get('phone'))
                                else:
                                    digits = _normalize_phone_value(item)
                                if digits:
                                    phone_rows.append({'contact_id': r['contact_id'], 'phone': digits})
                    df_out = pd.DataFrame(phone_rows)
                except Exception:
                    df_out = pd.DataFrame()
            self.wb_phone_data = df_out
            return self.wb_phone_data
        except Exception:
            # Global fallback: try wb_contacts directly
            try:
                conn = _conn()
                dfc = pd.read_sql_query("SELECT rowid AS contact_id, * FROM wb_contacts", conn)
                conn.close()
                phone_cols = [c for c in dfc.columns if 'phone' in str(c).lower()]
                phone_rows = []
                for _, r in dfc.iterrows():
                    for c in phone_cols:
                        v = r.get(c)
                        if v is None or (isinstance(v, float) and pd.isna(v)): continue
                        try:
                            parsed = json.loads(v) if isinstance(v, str) and v.strip().startswith('[') else [v]
                        except Exception:
                            parsed = [v]
                        for item in parsed:
                            if isinstance(item, dict):
                                digits = _normalize_phone_value(item.get('address') or item.get('value') or item.get('phone'))
                            else:
                                digits = _normalize_phone_value(item)
                            if digits:
                                phone_rows.append({'contact_id': r['contact_id'], 'phone': digits})
                return pd.DataFrame(phone_rows)
            except Exception:
                return pd.DataFrame()

    def load_wb_email_data(self):
        if self.wb_email_data is not None: return self.wb_email_data
        try:
            conn = _conn()
            df = pd.read_sql_query("SELECT contact_id, email_addresses_parsed FROM wb_emails", conn)
            conn.close()
            email_data = []
            for _, row in df.iterrows():
                cid = row['contact_id']
                raw = row['email_addresses_parsed']
                if not raw: continue
                try:
                    parsed = json.loads(raw) if raw.strip().startswith("[") else [raw]
                except Exception:
                    parsed = [raw]
                for e in parsed:
                    if isinstance(e, dict) and 'address' in e:
                        em = _safe_lower_strip(e.get('address'))
                        if em:
                            email_data.append({'contact_id': cid, 'email': em})
                    elif isinstance(e, str):
                        em = _safe_lower_strip(e)
                        if em:
                            email_data.append({'contact_id': cid, 'email': em})
            df_out = pd.DataFrame(email_data)
            if df_out.empty:
                # Fallback: derive from wb_contacts
                try:
                    conn = _conn()
                    dfc = pd.read_sql_query("SELECT rowid AS contact_id, * FROM wb_contacts", conn)
                    conn.close()
                    email_cols = [c for c in dfc.columns if 'email' in str(c).lower()]
                    email_rows: List[dict] = []
                    for _, r in dfc.iterrows():
                        for c in email_cols:
                            v = r.get(c)
                            if v is None or (isinstance(v, float) and pd.isna(v)): continue
                            try:
                                parsed = json.loads(v) if isinstance(v, str) and v.strip().startswith('[') else [v]
                            except Exception:
                                parsed = [v]
                            for item in parsed:
                                if isinstance(item, dict):
                                    em = _safe_lower_strip(item.get('address') or item.get('value') or item.get('email'))
                                else:
                                    em = _safe_lower_strip(item)
                                if em:
                                    email_rows.append({'contact_id': r['contact_id'], 'email': em})
                    df_out = pd.DataFrame(email_rows)
                except Exception:
                    df_out = pd.DataFrame()
            self.wb_email_data = df_out
            return self.wb_email_data
        except Exception:
            # Global fallback: try wb_contacts directly
            try:
                conn = _conn()
                dfc = pd.read_sql_query("SELECT rowid AS contact_id, * FROM wb_contacts", conn)
                conn.close()
                email_cols = [c for c in dfc.columns if 'email' in str(c).lower()]
                email_rows: List[dict] = []
                for _, r in dfc.iterrows():
                    for c in email_cols:
                        v = r.get(c)
                        if v is None or (isinstance(v, float) and pd.isna(v)): continue
                        try:
                            parsed = json.loads(v) if isinstance(v, str) and v.strip().startswith('[') else [v]
                        except Exception:
                            parsed = [v]
                        for item in parsed:
                            if isinstance(item, dict):
                                em = _safe_lower_strip(item.get('address') or item.get('value') or item.get('email'))
                            else:
                                em = _safe_lower_strip(item)
                            if em:
                                email_rows.append({'contact_id': r['contact_id'], 'email': em})
                return pd.DataFrame(email_rows)
            except Exception:
                return pd.DataFrame()

    def load_wb_tags_data(self):
        if self.wb_tags_data is not None: return self.wb_tags_data
        try:
            conn = _conn()
            df = pd.read_sql_query("SELECT contact_id, tags_parsed FROM wb_tags", conn)
            conn.close()
            tags_dict = {}
            for _, row in df.iterrows():
                cid = row['contact_id']
                raw = row['tags_parsed']
                if not raw: continue
                try:
                    parsed = json.loads(raw) if raw.strip().startswith("[") else [raw]
                except Exception:
                    parsed = [raw]
                tags = []
                for t in parsed:
                    if isinstance(t, dict):
                        tags.append(t.get('name') or t.get('tag_name') or t.get('title') or t.get('label') or str(t))
                    elif isinstance(t, str):
                        tags.append(t)
                tags_dict[str(cid)] = [tag for tag in tags if tag]
            self.wb_tags_data = tags_dict
            return self.wb_tags_data
        except Exception:
            return {}

    def get_tags_for_contact(self, contact_id):
        tags_dict = self.load_wb_tags_data()
        return tags_dict.get(str(contact_id), [])

    def simple_validate(self, df):
        checks = []
        for c in TARGET_COLUMNS:
            if c in df.columns:
                checks.append((f"Missing in {c}", int(df[c].isna().sum())))
            else:
                checks.append((f"Missing column {c}", "Not mapped"))
        if 'email' in df.columns:
            n_bad = (~df['email'].dropna().astype(str).str.match(EMAIL_RE)).sum()
            checks.append(("Invalid email count", int(n_bad)))
        if 'phone' in df.columns:
            digits_only = df['phone'].fillna('').astype(str).str.replace(r"\D", "", regex=True)
            too_short = (digits_only.str.len() < 7) & (digits_only.str.len() > 0)
            checks.append(("Phone values with <7 digits", int(too_short.sum())))
        return checks

    def _fmt_cell(self, v):
        if pd.isna(v): return ""
        s = str(v)
        return s[:-2] if s.endswith('.0') else s

    def on_export(self):
        if self.mapped_df is None:
            messagebox.showinfo("No data", "Load a file first")
            return
        f = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files","*.csv")])
        if not f: return
        export_cols = [c for c in self.tree["columns"] if c != "wb_tags"]
        df = self.mapped_df.copy()
        if '_orig_index' in df.columns:
            df = df.drop(columns=['_orig_index'])
        for c in export_cols:
            if c not in df.columns:
                df[c] = pd.NA
        final = df[export_cols]
        if 'zip' in final.columns:
            final['zip'] = final['zip'].apply(lambda x: str(x)[:-2] if str(x).endswith('.0') else str(x) if pd.notna(x) else x)
        final.to_csv(f, index=False)
        messagebox.showinfo("Saved", f"Saved mapped CSV to: {f}")

    def on_double_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell": return
        row_iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not row_iid or not col: return
        col_index = int(col.replace('#', '')) - 1
        col_name = self.tree["columns"][col_index]
        if col_name == "wb_tags":
            messagebox.showinfo("Read Only", "WB Tags column is read-only")
            return
        bbox = self.tree.bbox(row_iid, column=col_name)
        if not bbox: return
        x, y, width, height = bbox
        cur_val = self.tree.set(row_iid, col_name)
        entry = tk.Entry(self.tree)
        entry.place(x=x, y=y, width=width, height=height)
        entry.insert(0, cur_val) # type: ignore
        entry.focus_set()
        def save_edit(event=None):
            new_val = entry.get()
            entry.destroy()
            self.tree.set(row_iid, col_name, new_val)
            mapped_index = self.iid_to_index.get(row_iid)
            if mapped_index is None: return
            mask = self.mapped_df['_orig_index'] == mapped_index # type: ignore
            if mask.any():
                self.mapped_df.loc[mask, col_name] = new_val # type: ignore
        def cancel_edit(event=None): entry.destroy()
        entry.bind("<Return>", save_edit)
        entry.bind("<FocusOut>", save_edit)
        entry.bind("<Escape>", cancel_edit)

if __name__ == "__main__":
    app = ImportMapperApp()
    app.mainloop()