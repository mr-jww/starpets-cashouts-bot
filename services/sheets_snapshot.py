"""
Google Sheets full snapshot service.

Saves the SPBBB spreadsheet exactly as it appears in Google Sheets — every
sheet, every column, no restructuring. Used for two purposes:

1. Daily automatic snapshot, stored under SHEETS_BACKUP_DIR with a timestamped
   filename (similar to the existing DB backup mechanism in bot.py).
2. On-demand live snapshot, generated fresh when an admin requests it.

This is intentionally separate from services/sheets_sync.py, which reads and
restructures sheet data into SheetRow objects for syncing into the bot's own
database. This module just mirrors the spreadsheet as a workbook file.
"""

from __future__ import annotations

import os
import glob
from datetime import datetime

try:
    from openpyxl import Workbook
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

from services.sheets_sync import _open_spreadsheet, HAS_GSPREAD, SPREADSHEET_ID

SHEETS_BACKUP_DIR = os.getenv("SHEETS_BACKUP_DIR", "sheets_backups")
SHEETS_BACKUP_KEEP = int(os.getenv("SHEETS_BACKUP_KEEP", "30"))

os.makedirs(SHEETS_BACKUP_DIR, exist_ok=True)


def _build_snapshot_xlsx() -> bytes:
    """Read every sheet from the live spreadsheet and build an .xlsx
    byte string that mirrors it exactly, column for column."""
    if not HAS_GSPREAD or not SPREADSHEET_ID:
        raise RuntimeError("Google Sheets is not configured (HAS_GSPREAD or SHEETS_ID missing).")
    if not HAS_OPENPYXL:
        raise RuntimeError("openpyxl is not installed. Run: pip install openpyxl")

    spreadsheet = _open_spreadsheet()
    wb = Workbook()
    wb.remove(wb.active)  # remove the default empty sheet

    for ws in spreadsheet.worksheets():
        # Sheet names in Excel can't exceed 31 chars or contain []:*?/\
        safe_name = ws.title[:31]
        for ch in '[]:*?/\\':
            safe_name = safe_name.replace(ch, '_')
        out_ws = wb.create_sheet(title=safe_name or "Sheet")
        values = ws.get_all_values()
        for row in values:
            out_ws.append(row)

    import io
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def save_daily_snapshot() -> str:
    """Build a snapshot and write it to SHEETS_BACKUP_DIR. Returns the file path."""
    data = _build_snapshot_xlsx()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(SHEETS_BACKUP_DIR, f"SPBBB_backup_{ts}.xlsx")
    with open(path, "wb") as f:
        f.write(data)
    _cleanup_old_snapshots()
    return path


def _cleanup_old_snapshots() -> None:
    """Keep only the most recent SHEETS_BACKUP_KEEP snapshots."""
    files = sorted(
        glob.glob(os.path.join(SHEETS_BACKUP_DIR, "SPBBB_backup_*.xlsx")),
        key=os.path.getmtime,
        reverse=True,
    )
    for old_file in files[SHEETS_BACKUP_KEEP:]:
        try:
            os.remove(old_file)
        except OSError:
            pass


def list_snapshots(limit: int = 10) -> list[tuple[str, datetime, int]]:
    """Return (path, modified_time, size_bytes) for the most recent snapshots."""
    files = sorted(
        glob.glob(os.path.join(SHEETS_BACKUP_DIR, "SPBBB_backup_*.xlsx")),
        key=os.path.getmtime,
        reverse=True,
    )
    result = []
    for f in files[:limit]:
        stat = os.stat(f)
        result.append((f, datetime.fromtimestamp(stat.st_mtime), stat.st_size))
    return result