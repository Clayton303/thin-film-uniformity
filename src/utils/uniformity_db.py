"""
SQLite cache for uniformity run data.

Schema
------
runs          One row per uniformity coating run.
measurements  One row per witness piece (radius) within a run.

The database lives at  data/uniformity.db  relative to the project root.
It is created automatically on first use.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


_DEFAULT_DB = Path(__file__).parent.parent.parent / "data" / "uniformity.db"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RunRecord:
    chamber: str
    design: str
    date: str           # MM/DD/YYYY
    f_factor: Optional[float]
    anchor_file: str    # filename of the first SP in the batch
    id: Optional[int] = None


@dataclass
class MeasurementRecord:
    run_id: int
    radius: float       # inches
    sp_file: str        # filename
    peak_nm: float
    peak_pct: float
    shift_nm: float     # Δλ vs reference radius for this run


@dataclass
class RunSummary:
    """Joined view used by the dashboard."""
    run: RunRecord
    measurements: list[MeasurementRecord] = field(default_factory=list)

    @property
    def uniformity_score(self) -> float:
        if not self.measurements:
            return 0.0
        shifts = [m.shift_nm for m in self.measurements]
        return max(shifts) - min(shifts)

    @property
    def date_str(self) -> str:
        return self.run.date or ""


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    _init_schema(con)
    return con


def _init_schema(con: sqlite3.Connection) -> None:
    con.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chamber     TEXT NOT NULL,
            design      TEXT NOT NULL,
            date        TEXT,
            f_factor    REAL,
            anchor_file TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS measurements (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id    INTEGER NOT NULL REFERENCES runs(id),
            radius    REAL NOT NULL,
            sp_file   TEXT NOT NULL,
            peak_nm   REAL,
            peak_pct  REAL,
            shift_nm  REAL
        );

        CREATE INDEX IF NOT EXISTS idx_meas_run ON measurements(run_id);
        CREATE INDEX IF NOT EXISTS idx_runs_chamber ON runs(chamber);
    """)
    con.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class UniformityDB:
    def __init__(self, db_path: str | Path | None = None):
        self._path = Path(db_path) if db_path else _DEFAULT_DB
        self._con  = _connect(self._path)

    # --- Write -----------------------------------------------------------

    def save_run(self, run: RunRecord, measurements: list[MeasurementRecord]) -> int:
        """Insert run + measurements.  Returns the run id.
        Silently skips if anchor_file already exists."""
        cur = self._con.cursor()
        try:
            cur.execute(
                "INSERT INTO runs (chamber, design, date, f_factor, anchor_file) "
                "VALUES (?, ?, ?, ?, ?)",
                (run.chamber, run.design, run.date, run.f_factor, run.anchor_file),
            )
            run_id = cur.lastrowid
        except sqlite3.IntegrityError:
            return -1   # already present

        cur.executemany(
            "INSERT INTO measurements (run_id, radius, sp_file, peak_nm, peak_pct, shift_nm) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (run_id, m.radius, m.sp_file, m.peak_nm, m.peak_pct, m.shift_nm)
                for m in measurements
            ],
        )
        self._con.commit()
        return run_id

    # --- Read ------------------------------------------------------------

    def known_anchor_files(self) -> set[str]:
        rows = self._con.execute("SELECT anchor_file FROM runs").fetchall()
        return {r["anchor_file"] for r in rows}

    def chambers(self) -> list[str]:
        rows = self._con.execute(
            "SELECT DISTINCT chamber FROM runs ORDER BY chamber"
        ).fetchall()
        return [r["chamber"] for r in rows]

    def designs_for_chamber(self, chamber: str) -> list[str]:
        rows = self._con.execute(
            "SELECT DISTINCT design FROM runs WHERE chamber=? ORDER BY design",
            (chamber,),
        ).fetchall()
        return [r["design"] for r in rows]

    def runs_for_chamber(
        self, chamber: str, design: Optional[str] = None, min_radii: int = 2
    ) -> list[RunSummary]:
        """Return runs for a chamber, optionally filtered by design.

        min_radii: skip runs with fewer than this many witness measurements
        (single-measurement batches can't show a uniformity profile).
        Results sorted oldest-first.
        """
        if design:
            run_rows = self._con.execute(
                "SELECT * FROM runs WHERE chamber=? AND design=? ORDER BY date, id",
                (chamber, design),
            ).fetchall()
        else:
            run_rows = self._con.execute(
                "SELECT * FROM runs WHERE chamber=? ORDER BY date, id",
                (chamber,),
            ).fetchall()

        summaries = []
        for rr in run_rows:
            run = RunRecord(
                id=rr["id"],
                chamber=rr["chamber"],
                design=rr["design"],
                date=rr["date"],
                f_factor=rr["f_factor"],
                anchor_file=rr["anchor_file"],
            )
            meas_rows = self._con.execute(
                "SELECT * FROM measurements WHERE run_id=? ORDER BY radius",
                (run.id,),
            ).fetchall()
            measurements = [
                MeasurementRecord(
                    run_id=r["run_id"],
                    radius=r["radius"],
                    sp_file=r["sp_file"],
                    peak_nm=r["peak_nm"],
                    peak_pct=r["peak_pct"],
                    shift_nm=r["shift_nm"],
                )
                for r in meas_rows
            ]
            if len(measurements) >= min_radii:
                summaries.append(RunSummary(run=run, measurements=measurements))

        return summaries

    def run_count(self) -> int:
        return self._con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    def close(self) -> None:
        self._con.close()
