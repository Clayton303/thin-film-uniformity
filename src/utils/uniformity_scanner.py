"""
Scan the spectrophotometer data directory for uniformity runs and populate
the UniformityDB cache.

A uniformity run is identified by an "anchor" SP file whose header line
matches the full pattern:
    [chamber]-Unif [design] [date], F=[factor], R=[radius]

Subsequent files in the same batch carry only "R=[radius]" as their title.
The scanner groups them by looking forward from each anchor through the next
MAX_BATCH_LOOKAHEAD P-numbers, collecting files measured within
MAX_BATCH_MINUTES of the anchor.

Design-type detection
---------------------
"Single layer" designs are identified heuristically: if the design name
contains a material formula followed by a thickness (e.g. "Hf layer 350nm",
"SiO2 500nm", "Ta2O5 layer 200nm").  Everything else is treated as
multi-layer.  The type is stored in the design field as-is; the dashboard
uses it only for display labels.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from utils.sp_parser import SPFile, parse_sp
from utils.uniformity_metrics import find_peak, peak_shift_profile
from utils.uniformity_db import UniformityDB, RunRecord, MeasurementRecord
from utils.run_log_reader import RunLogReader


SPECTRO_DIR   = Path(r"\\59o-spectro\uvwinlab\DATA")
MAX_BATCH_LOOKAHEAD = 20    # scan this many P-numbers ahead of anchor
MAX_BATCH_MINUTES   = 120   # group files within this time window


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pnum(path: Path) -> Optional[int]:
    """Extract the numeric suffix from a filename like P1014116.SP."""
    m = re.search(r"P(\d+)", path.stem, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)


def _is_anchor(sp: SPFile) -> bool:
    return (sp.chamber is not None and sp.design_name is not None
            and sp.date is not None and sp.f_factor is not None)


def _is_short_r(sp: SPFile) -> bool:
    """File has only R= in header (no full metadata)."""
    return sp.radius is not None and sp.chamber is None


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------

def scan(
    db: UniformityDB,
    spectro_dir: Path = SPECTRO_DIR,
    progress: Optional[Callable[[str], None]] = None,
    run_log: Optional[RunLogReader] = None,
) -> int:
    """Scan spectro_dir for new uniformity runs; add them to db.

    If run_log is provided (or if the default Chamber run log R3.xlsx is
    accessible), run numbers are looked up automatically and stored.

    Returns the number of new runs added.
    """
    def _log(msg: str) -> None:
        if progress:
            progress(msg)

    # Use caller-supplied reader or try the default path
    if run_log is None:
        run_log = RunLogReader()

    known = db.known_anchor_files()

    # Build sorted list of all SP files
    all_sp = sorted(spectro_dir.glob("*.SP"), key=lambda p: _pnum(p) or 0)
    pnum_to_path = {_pnum(p): p for p in all_sp if _pnum(p) is not None}

    added = 0
    consumed_pnums: set[int] = set()   # followers already absorbed into a prior batch

    for sp_path in all_sp:
        pnum = _pnum(sp_path)
        anchor_name = sp_path.name

        # Skip files already in DB or already consumed as a follower this scan
        if anchor_name in known or (pnum is not None and pnum in consumed_pnums):
            continue

        # Parse cheaply: only check header line
        sp = _parse_cheap(sp_path)
        if sp is None or not _is_anchor(sp):
            continue

        _log(f"Found anchor: {anchor_name}")

        # Collect the full batch starting from this anchor
        batch, batch_consumed = _collect_batch(sp, sp_path, pnum_to_path)
        consumed_pnums.update(batch_consumed)
        if not batch:
            continue

        # Compute metrics; look up run number + coating date from log
        run, measurements = _build_records(sp, anchor_name, batch)
        if run_log.is_available() and run.chamber and run.date:
            entry = run_log.find_run_entry(run.chamber, run.date, run.design or "")
            if entry:
                run.run_number, run.date = entry   # use run log date as canonical

        run_id = db.save_run(run, measurements)
        if run_id > 0:
            added += 1
            rn_str = f" [{run.run_number}]" if run.run_number else ""
            _log(f"  Saved run {run_id}: {run.chamber} | {run.design} | "
                 f"{run.date}{rn_str} | {len(measurements)} radii")

    return added


def backfill_run_numbers(
    db: UniformityDB,
    run_log: Optional[RunLogReader] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> int:
    """Try to assign run numbers to existing DB runs that have none.

    Call this after updating the Chamber run log, or any time you want to
    refresh run-number assignments for previously scanned runs.
    Returns the number of runs updated.
    """
    def _log(msg: str) -> None:
        if progress:
            progress(msg)

    if run_log is None:
        run_log = RunLogReader()
    if not run_log.is_available():
        _log("Run log not found — skipping backfill")
        return 0

    updated = 0
    for chamber in db.chambers():
        for summary in db.runs_for_chamber(chamber, min_radii=1):
            run = summary.run
            if run.run_number:
                continue  # already assigned
            entry = run_log.find_run_entry(
                run.chamber, run.date or "", run.design or ""
            )
            if entry and run.id is not None:
                rn, log_date = entry
                db.set_run_number(run.id, rn, run_log_date=log_date)
                _log(f"  Backfilled {run.chamber} {run.date} -> {rn} ({log_date})")
                updated += 1
    return updated


def _parse_cheap(path: Path) -> Optional[SPFile]:
    """Parse only header; skip full data read for the initial anchor check."""
    try:
        return parse_sp(path)
    except Exception:
        return None


def _same_batch(anchor: SPFile, candidate: SPFile) -> bool:
    """True if candidate is from the same uniformity run as anchor."""
    return (candidate.chamber    == anchor.chamber
            and candidate.design_name == anchor.design_name
            and candidate.date        == anchor.date
            and candidate.f_factor    == anchor.f_factor)


def _collect_batch(
    anchor_sp: SPFile,
    anchor_path: Path,
    pnum_to_path: dict[int, Path],
) -> tuple[list[SPFile], set[int]]:
    """Return (batch SPFiles, consumed P-numbers for follower files).

    Accepts two kinds of followers:
    - Short-R files  (header is just 'R=n' — original format)
    - Full-header files with same chamber/design/date/F (all-header format)
    """
    base_pnum    = _pnum(anchor_path)
    anchor_mtime = _mtime(anchor_path)
    cutoff       = anchor_mtime + timedelta(minutes=MAX_BATCH_MINUTES)

    batch:    list[SPFile] = [anchor_sp]
    consumed: set[int]     = set()

    for offset in range(1, MAX_BATCH_LOOKAHEAD + 1):
        pnum = base_pnum + offset
        candidate_path = pnum_to_path.get(pnum)
        if candidate_path is None:
            break
        if _mtime(candidate_path) > cutoff:
            break

        sp = _parse_cheap(candidate_path)
        if sp is None:
            break

        if _is_short_r(sp):
            batch.append(sp)
            consumed.add(pnum)
        elif _is_anchor(sp) and _same_batch(anchor_sp, sp):
            # Full-header follower from the same run
            batch.append(sp)
            consumed.add(pnum)
        elif _is_anchor(sp):
            # Different run starts — stop collecting
            break
        # else: unrelated file with no R header — skip, keep looking

    return batch, consumed


def _build_records(
    anchor: SPFile,
    anchor_filename: str,
    batch: list[SPFile],
) -> tuple[RunRecord, list[MeasurementRecord]]:
    """Convert a parsed batch into DB records."""
    run = RunRecord(
        chamber=anchor.chamber,
        design=anchor.design_name,
        date=anchor.date,
        f_factor=anchor.f_factor,
        anchor_file=anchor_filename,
    )

    radii, peaks_nm, peaks_pct = [], [], []
    sp_files = []

    for sp in batch:
        if sp.radius is None or not sp.wavelengths:
            continue
        peak_nm, peak_pct = find_peak(sp.wavelengths, sp.transmittances)
        radii.append(sp.radius)
        peaks_nm.append(peak_nm)
        peaks_pct.append(peak_pct)
        sp_files.append(sp.path.name)

    shifts = peak_shift_profile(radii, peaks_nm)

    measurements = [
        MeasurementRecord(
            run_id=0,       # filled in by DB on insert
            radius=r,
            sp_file=f,
            peak_nm=p,
            peak_pct=pt,
            shift_nm=shifts.get(r, 0.0),
        )
        for r, f, p, pt in zip(radii, sp_files, peaks_nm, peaks_pct)
    ]

    return run, measurements
