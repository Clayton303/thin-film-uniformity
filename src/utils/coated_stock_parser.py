"""
Parse the Coated Stock Excel workbook and provide search functionality.

Column layout (Coated STK sheet):
  A  ID
  B  Material
  C  Size
  D  ROC
  E  Coating  — run numbers, e.g. "V1-1049 & V1-1052"
  F  QTY
  G  Notes    — wavelength + transmission info, e.g. "HR @ 1064nm (5ppm)"
  H  Customer
  I  Remove date
  J  Comments

The first run number in column E is the HR coating; the second (if present)
is the AR coating.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

EXCEL_PATH = Path(
    r"C:\Users\User\FiveNine Dropbox\FiveNine Optics Team Folder"
    r"\Inventory end of month counts\6. Coated Stk.xlsx"
)


@dataclass
class CoatedItem:
    row_id:           int
    material:         str
    size:             str
    roc:              str
    coating:          str
    hr_run:           str            # first run number
    ar_run:           str            # second run number, or ""
    qty:              str
    notes:            str
    customer:         str
    wl_min:           Optional[float]   # nm — smallest wavelength found in notes
    wl_max:           Optional[float]   # nm — largest wavelength found in notes
    transmission_ppm: Optional[float]   # ppm if explicitly stated


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_runs(coating: str) -> tuple[str, str]:
    runs = re.findall(r'[Vv]\d+-\d+', coating)
    hr = runs[0] if runs else coating.strip()
    ar = runs[1] if len(runs) > 1 else ""
    return hr, ar


def _parse_wavelengths(notes: str) -> tuple[Optional[float], Optional[float]]:
    # Match 3- or 4-digit numbers (nm range) followed by nm
    wls = [float(m) for m in re.findall(r'(\d{3,4}(?:\.\d+)?)\s*nm', notes, re.IGNORECASE)]
    if not wls:
        return None, None
    return min(wls), max(wls)


def _parse_transmission(notes: str) -> Optional[float]:
    m = re.search(r'\((?:T=)?([\d.]+)\s*ppm\)', notes, re.IGNORECASE)
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_coated_stock(path: Path = EXCEL_PATH) -> list[CoatedItem]:
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb["Coated STK "]

    items: list[CoatedItem] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:          # header row
            continue
        if not any(row):    # blank row
            continue

        row_id   = row[0]
        material = str(row[1] or "").strip()
        size     = str(row[2] or "").strip()
        roc      = str(row[3] or "").strip()
        coating  = str(row[4] or "").strip()
        qty      = str(row[5] or "").strip()
        notes    = str(row[6] or "").strip()
        customer = str(row[7] or "").strip()

        if not coating or coating.lower() == "none":
            continue

        hr_run, ar_run = _parse_runs(coating)
        wl_min, wl_max = _parse_wavelengths(notes)
        trans          = _parse_transmission(notes)

        items.append(CoatedItem(
            row_id=row_id if isinstance(row_id, int) else i,
            material=material,
            size=size,
            roc=roc,
            coating=coating,
            hr_run=hr_run,
            ar_run=ar_run,
            qty=qty,
            notes=notes,
            customer=customer,
            wl_min=wl_min,
            wl_max=wl_max,
            transmission_ppm=trans,
        ))

    wb.close()
    return items


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(
    items: list[CoatedItem],
    wl_min: Optional[float]   = None,
    wl_max: Optional[float]   = None,
    trans_min: Optional[float] = None,
    trans_max: Optional[float] = None,
    tolerance_nm: float        = 10.0,
) -> list[CoatedItem]:
    """Return items matching the wavelength and/or transmission criteria.

    Wavelength logic
    ----------------
    Single-value search (wl_max is None): item's wavelength range must overlap
    the point ± tolerance_nm.
    Range search: item's range must overlap [wl_min, wl_max].

    Transmission logic
    ------------------
    Only applied when trans_min or trans_max are given.  Items without an
    explicit ppm value in their notes are excluded when a transmission filter
    is active.
    """
    results: list[CoatedItem] = []

    for item in items:
        # ── Wavelength filter ──────────────────────────────────────────────
        if wl_min is not None:
            if item.wl_min is None:
                continue
            lo = wl_min - tolerance_nm
            hi = (wl_max if wl_max is not None else wl_min) + tolerance_nm
            item_lo = item.wl_min
            item_hi = item.wl_max if item.wl_max is not None else item.wl_min
            if item_hi < lo or item_lo > hi:
                continue

        # ── Transmission filter ────────────────────────────────────────────
        if trans_min is not None or trans_max is not None:
            if item.transmission_ppm is None:
                continue
            if trans_min is not None and item.transmission_ppm < trans_min:
                continue
            if trans_max is not None and item.transmission_ppm > trans_max:
                continue

        results.append(item)

    return results
