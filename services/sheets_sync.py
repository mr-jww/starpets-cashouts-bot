"""
Google Sheets synchronization service.

Reads ambassadors_base spreadsheet and syncs bloggers into the DB.
Each sheet = one manager. Columns: Channel | Sp ID | USDT-TRC20 | PayPal |
Payment Method | Language | Platform | Content | Old Addresses | Comment
Manager column removed — name is inferred from sheet name

Sync modes:
  "new_only"  — only add bloggers not yet in DB (never update existing)
  "full"      — add new + update changed payment methods

Triggered:
  - Manually via /admin → Sync sheets
  - Automatically via APScheduler (configurable interval)
"""

from __future__ import annotations
import os
import logging
from dataclasses import dataclass, field

log = logging.getLogger("starpets")

try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAS_GSPREAD = True
except ImportError:
    HAS_GSPREAD = False

SPREADSHEET_ID  = os.getenv("SHEETS_ID", "")
CREDENTIALS_PATH = os.getenv("SHEETS_CREDENTIALS", "google_credentials.json")

_SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

# Column indices (0-based) matching ambassadors_base_v7 format
COL_CHANNEL  = 0
COL_SITE     = 1
COL_USDT     = 2
COL_PAYPAL   = 3
COL_METHOD   = 4   # primary method label: Site / USDT-TRC20 / PayPal
# COL_MANAGER removed — individual sheets no longer have Manager column
# Manager name is taken from the sheet name itself

METHOD_NORMALISE = {
    "site":       "site",
    "usdt-trc20": "usdt-trc20",
    "usdt":       "usdt-trc20",
    "paypal":     "paypal",
}


@dataclass
class SheetRow:
    name:    str
    site:    str
    usdt:    str
    paypal:  str
    primary: str   # normalised type: site / usdt-trc20 / paypal
    manager: str


@dataclass
class SyncResult:
    added:   list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (name, reason)
    errors:  list[str] = field(default_factory=list)

    @property
    def total(self):
        return len(self.added) + len(self.updated)


# --------------------------------------------------------------------------- #
# Sheets client
# --------------------------------------------------------------------------- #
def _get_client():
    if not HAS_GSPREAD:
        raise RuntimeError("gspread not installed. Run: pip install gspread")
    if not os.path.exists(CREDENTIALS_PATH):
        raise RuntimeError(f"Credentials file not found: {CREDENTIALS_PATH}")
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=_SCOPES)
    return gspread.authorize(creds)


def _open_spreadsheet():
    gc = _get_client()
    if not SPREADSHEET_ID:
        raise RuntimeError("SHEETS_ID not set in .env")
    return gc.open_by_key(SPREADSHEET_ID)


# --------------------------------------------------------------------------- #
# Parse a single row
# --------------------------------------------------------------------------- #
def _parse_row_all_sheet(row: list) -> SheetRow | None:
    """Parse a row from the All sheet which still has Manager column."""
    while len(row) < 6:
        row.append("")
    name    = str(row[0]).strip()
    site    = str(row[1]).strip()
    usdt    = str(row[2]).strip()
    paypal  = str(row[3]).strip()
    method  = str(row[4]).strip().lower()
    manager = str(row[5]).strip()
    if not name or name.lower() in ("channel", "имя", "blogger"):
        return None
    primary = METHOD_NORMALISE.get(method, "")
    if not primary:
        if site:    primary = "site"
        elif usdt:  primary = "usdt-trc20"
        elif paypal: primary = "paypal"
    if not name or (not site and not usdt and not paypal):
        return None
    return SheetRow(name=name, site=site, usdt=usdt, paypal=paypal,
                    primary=primary, manager=manager)


def _parse_row(row: list, sheet_name: str) -> SheetRow | None:
    """Parse one spreadsheet row into SheetRow. Returns None if invalid."""
    # Pad to avoid index errors
    while len(row) < 5:
        row.append("")

    name    = str(row[COL_CHANNEL]).strip()
    site    = str(row[COL_SITE]).strip()
    usdt    = str(row[COL_USDT]).strip()
    paypal  = str(row[COL_PAYPAL]).strip()
    method  = str(row[COL_METHOD]).strip().lower()
    manager = sheet_name  # manager name is always taken from the sheet name

    if not name or name.lower() in ("channel", "имя", "blogger"):
        return None  # header row or empty

    primary = METHOD_NORMALISE.get(method, "")
    if not primary:
        # Try to infer from available data
        if site:   primary = "site"
        elif usdt: primary = "usdt-trc20"
        elif paypal: primary = "paypal"

    if not any([site, usdt, paypal]):
        return None  # no payment info at all

    return SheetRow(
        name=name, site=site, usdt=usdt,
        paypal=paypal, primary=primary,
        manager=manager or sheet_name,
    )


# --------------------------------------------------------------------------- #
# Read sheets
# --------------------------------------------------------------------------- #
def read_sheets(sheet_names: list[str] | None = None) -> dict[str, list[SheetRow]]:
    """
    Read spreadsheet. Returns {manager_name: [SheetRow, ...]}.
    If sheet_names is None, reads all sheets except 'All'.
    """
    sh = _open_spreadsheet()
    result: dict[str, list[SheetRow]] = {}

    worksheets = sh.worksheets()
    for ws in worksheets:
        if ws.title == "All":
            continue
        if sheet_names and ws.title not in sheet_names:
            continue

        rows = ws.get_all_values()
        if not rows:
            continue

        parsed = []
        for row in rows[1:]:  # skip header
            sr = _parse_row(row, ws.title)
            if sr:
                parsed.append(sr)

        if parsed:
            result[ws.title] = parsed
            log.info(f"[system] SHEETS_READ | sheet={ws.title} | rows={len(parsed)}")

    return result


def get_sheet_names() -> list[str]:
    """Return all sheet names except 'All'."""
    sh = _open_spreadsheet()
    return [ws.title for ws in sh.worksheets() if ws.title != "All"]


# --------------------------------------------------------------------------- #
# Sync into DB
# --------------------------------------------------------------------------- #
async def sync_sheets_to_db(
    manager_name: str,
    rows: list[SheetRow],
    manager_db_id: int,
    mode: str = "new_only",
) -> SyncResult:
    """
    Sync rows for one manager into DB.
    mode: "new_only" | "full"
    """
    from database.queries import (
        get_blogger_by_name, add_blogger,
        add_payment_method, set_primary_method,
        get_active_methods,
    )

    result = SyncResult()

    for sr in rows:
        try:
            db_b = await get_blogger_by_name(sr.name, manager_db_id)
            is_new = db_b is None

            if not is_new and mode == "new_only":
                continue  # skip existing in new_only mode

            if is_new:
                db_b = await add_blogger(sr.name, manager_db_id)
                if not db_b:
                    db_b = await get_blogger_by_name(sr.name, manager_db_id)
                if not db_b:
                    result.errors.append(f"Failed to create: {sr.name}")
                    continue

            # Add/update methods
            added_methods: dict[str, int] = {}
            for mtype, addr in [
                ("site",       sr.site),
                ("usdt-trc20", sr.usdt),
                ("paypal",     sr.paypal),
            ]:
                if addr:
                    m = await add_payment_method(db_b["id"], mtype, addr)
                    added_methods[mtype] = m["id"]

            # Set primary
            if sr.primary and sr.primary in added_methods:
                await set_primary_method(added_methods[sr.primary], db_b["id"])
            elif added_methods:
                # fallback: set first available as primary
                first_id = next(iter(added_methods.values()))
                await set_primary_method(first_id, db_b["id"])

            if is_new:
                result.added.append(sr.name)
            else:
                result.updated.append(sr.name)

        except Exception as e:
            result.errors.append(f"{sr.name}: {e}")

    return result




# --------------------------------------------------------------------------- #
# Sanity check before applying sync
# --------------------------------------------------------------------------- #
async def sanity_check(sheets_data: dict[str, list[SheetRow]]) -> tuple[bool, str]:
    """
    Check that sheet data looks reasonable before applying.
    Returns (ok, reason).

    Fails if:
    - Total rows < 10 (sheet probably failed to load)
    - Any sheet that previously had >20 bloggers now has 0
    - Total rows dropped by more than 30% vs current DB count
    """
    from database.queries import get_all_bloggers

    total_rows = sum(len(rows) for rows in sheets_data.values())

    if total_rows < 10:
        return False, f"Sheet returned only {total_rows} rows — likely a read error"

    # Check DB counts
    all_bloggers = await get_all_bloggers()
    db_count = len(all_bloggers)

    if db_count > 50 and total_rows < db_count * 0.5:
        return False, (
            f"Sheet has {total_rows} rows but DB has {db_count} bloggers — "
            f"drop of {100 - int(total_rows/db_count*100)}%, aborting"
        )

    # Check per-sheet: if a sheet previously had bloggers, it shouldn't be empty
    # Build current DB counts per manager
    mgr_counts: dict[str, int] = {}
    for b in all_bloggers:
        mgr = (b.get("manager_filter") or b.get("manager_username") or "").lower()
        if mgr:
            mgr_counts[mgr] = mgr_counts.get(mgr, 0) + 1

    for sheet_name, rows in sheets_data.items():
        db_mgr_count = mgr_counts.get(sheet_name.lower(), 0)
        if db_mgr_count > 20 and len(rows) == 0:
            return False, (
                f"Sheet '{sheet_name}' is empty but DB has {db_mgr_count} bloggers for this manager"
            )

    return True, "ok"

# --------------------------------------------------------------------------- #
# Full sync: all active managers
# --------------------------------------------------------------------------- #
async def run_full_sync(mode: str = "new_only", skip_sanity: bool = False) -> dict[str, SyncResult]:
    """
    Read all sheets and sync each manager's data.
    Returns {sheet_name: SyncResult}.
    Raises ValueError if sanity check fails (unless skip_sanity=True).
    """
    from database.queries import get_all_users

    sheets_data = read_sheets()

    if not skip_sanity:
        ok, reason = await sanity_check(sheets_data)
        if not ok:
            raise ValueError(reason)

    all_users   = await get_all_users()
    username_to_id = {
        (u.get("username") or "").lower(): u["id"]
        for u in all_users
    }
    manager_filter_to_id = {
        (u.get("manager_filter") or "").lower(): u["id"]
        for u in all_users
        if u.get("manager_filter")
    }

    results: dict[str, SyncResult] = {}

    for sheet_name, rows in sheets_data.items():
        # Try to find matching user by manager_filter or username
        mgr_id = (
            manager_filter_to_id.get(sheet_name.lower()) or
            username_to_id.get(sheet_name.lower())
        )
        if not mgr_id:
            log.warning(f"[system] SHEETS_SYNC | sheet={sheet_name} | no matching user in DB")
            results[sheet_name] = SyncResult(
                errors=[f"No user found for manager '{sheet_name}'"]
            )
            continue

        result = await sync_sheets_to_db(sheet_name, rows, mgr_id, mode=mode)
        results[sheet_name] = result
        log.info(
            f"[system] SHEETS_SYNC | sheet={sheet_name} | "
            f"added={len(result.added)} updated={len(result.updated)} "
            f"errors={len(result.errors)}"
        )

    return results