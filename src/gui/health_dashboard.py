"""
Chamber Health Dashboard — tkinter + matplotlib.

Layout
------
  [Chamber dropdown]  [Design dropdown]  [Refresh button]

  Upper chart: uniformity trend over time
    X = run date (or sequential index)
    Y = peak-wavelength shift (nm) vs reference radius
    One coloured line per measured radius
    Each point = one run

  Lower panel: run list table
    Date | Design | F | Radii | Score (nm p-p)
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
import matplotlib.dates as mdates
from datetime import datetime

# Add src to path so we can import our modules
_SRC = Path(__file__).parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.uniformity_db import UniformityDB, RunSummary
from utils.uniformity_scanner import scan, SPECTRO_DIR

_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
]
_DATE_FMT = "%m/%d/%Y"
_ALL = "All designs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(date_str: str) -> datetime:
    try:
        return datetime.strptime(date_str, _DATE_FMT)
    except (ValueError, TypeError):
        return datetime.min


def _run_label(summary: RunSummary) -> str:
    """Short label for a run: date + anchor file stem."""
    return f"{summary.run.date or '?'}  [{Path(summary.run.anchor_file).stem}]"


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class HealthDashboard(tk.Tk):
    def __init__(self, db: UniformityDB, spectro_dir: Path = SPECTRO_DIR):
        super().__init__()
        self._db = db
        self._spectro_dir = spectro_dir

        self.title("Chamber Health Dashboard — FiveNine Optics")
        self.geometry("1100x760")
        self.resizable(True, True)

        self._build_ui()
        self._refresh_chambers()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        # ── Top toolbar ──────────────────────────────────────────────────
        toolbar = ttk.Frame(self, padding=6)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(toolbar, text="Chamber:").pack(side=tk.LEFT)
        self._chamber_var = tk.StringVar()
        self._chamber_cb = ttk.Combobox(
            toolbar, textvariable=self._chamber_var,
            state="readonly", width=6,
        )
        self._chamber_cb.pack(side=tk.LEFT, padx=(2, 12))
        self._chamber_cb.bind("<<ComboboxSelected>>", self._on_chamber_changed)

        ttk.Label(toolbar, text="Design:").pack(side=tk.LEFT)
        self._design_var = tk.StringVar(value=_ALL)
        self._design_cb = ttk.Combobox(
            toolbar, textvariable=self._design_var,
            state="readonly", width=28,
        )
        self._design_cb.pack(side=tk.LEFT, padx=(2, 12))
        self._design_cb.bind("<<ComboboxSelected>>", self._on_design_changed)

        self._scan_btn = ttk.Button(
            toolbar, text="Scan new data", command=self._scan_async,
        )
        self._scan_btn.pack(side=tk.RIGHT, padx=4)

        self._status_var = tk.StringVar(value="")
        ttk.Label(toolbar, textvariable=self._status_var, foreground="gray").pack(
            side=tk.RIGHT, padx=8
        )

        # ── Trend chart (matplotlib) ──────────────────────────────────────
        self._fig = Figure(figsize=(10, 4.5), dpi=96)
        self._ax  = self._fig.add_subplot(111)
        self._fig.tight_layout(pad=2.0)

        chart_frame = ttk.Frame(self)
        chart_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=6, pady=(0, 2))

        self._canvas = FigureCanvasTkAgg(self._fig, master=chart_frame)
        self._canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        nav = NavigationToolbar2Tk(self._canvas, chart_frame)
        nav.update()

        # ── Run table ─────────────────────────────────────────────────────
        table_frame = ttk.LabelFrame(self, text="Runs", padding=4)
        table_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=(0, 6))

        cols = ("date", "design", "f_factor", "radii", "score")
        self._tree = ttk.Treeview(
            table_frame, columns=cols, show="headings", height=5,
        )
        self._tree.heading("date",     text="Date")
        self._tree.heading("design",   text="Design")
        self._tree.heading("f_factor", text="F")
        self._tree.heading("radii",    text="Radii (\")")
        self._tree.heading("score",    text="Score (nm p-p)")
        self._tree.column("date",     width=100, anchor="center")
        self._tree.column("design",   width=220)
        self._tree.column("f_factor", width=60,  anchor="center")
        self._tree.column("radii",    width=200)
        self._tree.column("score",    width=100, anchor="center")

        sb = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    # ------------------------------------------------------------------ Data

    def _refresh_chambers(self) -> None:
        chambers = self._db.chambers()
        self._chamber_cb["values"] = chambers
        if chambers:
            self._chamber_var.set(chambers[0])
            self._on_chamber_changed()

    def _on_chamber_changed(self, _event=None) -> None:
        chamber = self._chamber_var.get()
        designs = [_ALL] + self._db.designs_for_chamber(chamber)
        self._design_cb["values"] = designs
        self._design_var.set(_ALL)
        self._reload_chart()

    def _on_design_changed(self, _event=None) -> None:
        self._reload_chart()

    def _reload_chart(self) -> None:
        chamber = self._chamber_var.get()
        design  = self._design_var.get()
        if not chamber:
            return

        summaries = self._db.runs_for_chamber(
            chamber,
            design=None if design == _ALL else design,
        )
        self._update_chart(summaries)
        self._update_table(summaries)

    # ------------------------------------------------------------------ Chart

    def _update_chart(self, summaries: list[RunSummary]) -> None:
        ax = self._ax
        ax.clear()

        if not summaries:
            ax.set_title("No data for selection")
            self._canvas.draw()
            return

        # Collect all unique radii across runs
        all_radii = sorted({m.radius for s in summaries for m in s.measurements})

        # Build per-radius time series
        dates  = [_parse_date(s.run.date) for s in summaries]
        # Use run index if dates are all the same
        use_dates = len(set(d.date() for d in dates)) > 1

        x_vals = dates if use_dates else list(range(len(summaries)))

        for i, radius in enumerate(all_radii):
            y_vals = []
            x_used = []
            for xi, summary in zip(x_vals, summaries):
                meas = next((m for m in summary.measurements if m.radius == radius), None)
                if meas is not None:
                    y_vals.append(meas.shift_nm)
                    x_used.append(xi)

            if y_vals:
                color = _COLORS[i % len(_COLORS)]
                label = f'R={radius}"'
                ax.plot(x_used, y_vals, "o-", color=color, label=label,
                        linewidth=1.5, markersize=5)

        # Formatting
        chamber = self._chamber_var.get()
        design  = self._design_var.get()
        title   = f"{chamber}  —  {design}" if design != _ALL else chamber
        ax.set_title(f"Uniformity trend: {title}", fontsize=11)
        ax.set_ylabel("Peak shift Δλ (nm)\nvs smallest radius")
        ax.axhline(0, color="black", linewidth=0.5, linestyle="--")

        if use_dates:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            self._fig.autofmt_xdate(rotation=30, ha="right")
        else:
            ax.set_xlabel("Run index (oldest → newest)")

        if all_radii:
            ax.legend(loc="upper left", fontsize=8, ncol=2)

        ax.grid(True, linewidth=0.4, alpha=0.5)
        self._fig.tight_layout(pad=1.5)
        self._canvas.draw()

    # ------------------------------------------------------------------ Table

    def _update_table(self, summaries: list[RunSummary]) -> None:
        for row in self._tree.get_children():
            self._tree.delete(row)

        for s in reversed(summaries):   # newest first
            radii_str = "  ".join(f'{m.radius}"' for m in s.measurements)
            score_str = f"{s.uniformity_score:.3f}"
            f_str     = f"{s.run.f_factor:.3f}" if s.run.f_factor else "—"
            self._tree.insert(
                "", tk.END,
                values=(s.run.date or "?", s.run.design, f_str, radii_str, score_str),
            )

    # ------------------------------------------------------------------ Scan

    def _scan_async(self) -> None:
        """Run scanner in a background thread so the UI stays responsive."""
        self._scan_btn.config(state=tk.DISABLED)
        self._status_var.set("Scanning…")

        def worker():
            try:
                n = scan(
                    self._db,
                    spectro_dir=self._spectro_dir,
                    progress=lambda msg: self.after(0, lambda m=msg: self._status_var.set(m)),
                )
                self.after(0, lambda: self._scan_done(n))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Scan error", str(exc)))
                self.after(0, self._scan_reset)

        threading.Thread(target=worker, daemon=True).start()

    def _scan_done(self, n_new: int) -> None:
        self._scan_reset()
        self._status_var.set(f"{n_new} new run(s) added")
        self._refresh_chambers()

    def _scan_reset(self) -> None:
        self._scan_btn.config(state=tk.NORMAL)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    db = UniformityDB()
    app = HealthDashboard(db)
    app.mainloop()
    db.close()


if __name__ == "__main__":
    main()
