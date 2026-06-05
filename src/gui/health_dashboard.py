"""
Chamber Health Dashboard — tkinter + matplotlib.

Window layout
─────────────
  [Chamber dropdown]                       [Scan new data]  [status]

  ╔══════════════════════╗  ╔══════════════════════════════╗
  ║  Design Uniformity   ║  ║  Material Health             ║
  ╠══════════════════════╩══╩══════════════════════════════╣
  ║  [Design dropdown]          [Material dropdown]        ║
  ║                                                        ║
  ║  Trend chart (Δλ per radius vs run date)               ║
  ║                                                        ║
  ║  Run table                                             ║
  ╚════════════════════════════════════════════════════════╝

Design tab:    Design dropdown → trend of all runs for that design/chamber.
Material tab:  Material dropdown limited to the 6 canonical single-material
               health-check designs.  Run table includes Date + Run # columns.
"""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional

import tkinter as tk
from tkinter import ttk, messagebox

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
import matplotlib.dates as mdates
import yaml

_SRC = Path(__file__).parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.uniformity_db import UniformityDB, RunSummary
from utils.uniformity_scanner import scan, SPECTRO_DIR

_DATE_FMT  = "%m/%d/%Y"
_ALL       = "All designs"
_COLORS    = ["#1f77b4","#ff7f0e","#2ca02c","#d62728",
              "#9467bd","#8c564b","#e377c2","#7f7f7f"]

_CFG_DIR   = Path(__file__).parent.parent.parent / "config"


# ---------------------------------------------------------------------------
# Material targets
# ---------------------------------------------------------------------------

def _load_material_targets() -> list[dict]:
    path = _CFG_DIR / "material_targets.yaml"
    if not path.exists():
        return []
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get("materials", [])


def _match_material(design: str, mat: dict) -> bool:
    """True if design name refers to this material at this target thickness."""
    if not design:
        return False
    d = design.lower()
    d = re.sub(r"\b(layer|film|single|unif)\b", "", d)
    names = [mat["name"].lower()] + [a.lower() for a in mat.get("aliases", [])]
    target = str(mat["target_nm"])
    return any(n in d for n in names) and target in d and "nm" in d


def _material_label(mat: dict) -> str:
    return f"{mat['name']}  {mat['target_nm']} nm"


# ---------------------------------------------------------------------------
# Shared chart + table panel
# ---------------------------------------------------------------------------

class _UniformityPanel(ttk.Frame):
    """Reusable trend chart + run table; used in both notebook tabs."""

    def __init__(self, parent: tk.Widget, *, show_run_number: bool = False):
        super().__init__(parent)
        self._show_run_number = show_run_number
        self._build_chart()
        self._build_table()

    # ── Build ──────────────────────────────────────────────────────────────

    def _build_chart(self) -> None:
        self._fig = Figure(figsize=(10, 3.8), dpi=96)
        self._ax  = self._fig.add_subplot(111)
        self._fig.tight_layout(pad=2.0)

        frame = ttk.Frame(self)
        frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self._canvas = FigureCanvasTkAgg(self._fig, master=frame)
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        nav = NavigationToolbar2Tk(self._canvas, frame)
        nav.update()

    def _build_table(self) -> None:
        frame = ttk.LabelFrame(self, text="Runs", padding=4)
        frame.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=(0, 4))

        if self._show_run_number:
            cols = ("date", "run_number", "design", "radii", "score")
            headings = {
                "date":       ("Date",          100),
                "run_number": ("Run #",          80),
                "design":     ("Design",        200),
                "radii":      ("Radii (\")",    200),
                "score":      ("Score (nm p-p)", 110),
            }
        else:
            cols = ("date", "design", "f_factor", "radii", "score")
            headings = {
                "date":     ("Date",          100),
                "design":   ("Design",        220),
                "f_factor": ("F",              60),
                "radii":    ("Radii (\")",    200),
                "score":    ("Score (nm p-p)", 110),
            }

        self._tree = ttk.Treeview(frame, columns=cols, show="headings", height=5)
        for col, (text, width) in headings.items():
            self._tree.heading(col, text=text)
            anchor = "center" if col in ("date","run_number","f_factor","score") else "w"
            self._tree.column(col, width=width, anchor=anchor)

        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    # ── Update ─────────────────────────────────────────────────────────────

    def update_display(
        self,
        summaries: list[RunSummary],
        title: str = "",
        y_label: str = "Peak shift Δλ (nm)\nvs smallest radius",
    ) -> None:
        self._update_chart(summaries, title, y_label)
        self._update_table(summaries)

    def _update_chart(
        self, summaries: list[RunSummary], title: str, y_label: str
    ) -> None:
        ax = self._ax
        ax.clear()

        if not summaries:
            ax.set_title(title or "No data for selection")
            self._canvas.draw()
            return

        all_radii = sorted({m.radius for s in summaries for m in s.measurements})
        dates = [_parse_date(s.run.date) for s in summaries]
        use_dates = len(set(d.date() for d in dates)) > 1
        x_vals = dates if use_dates else list(range(len(summaries)))

        for i, radius in enumerate(all_radii):
            y, x_used = [], []
            for xi, s in zip(x_vals, summaries):
                m = next((m for m in s.measurements if m.radius == radius), None)
                if m is not None:
                    y.append(m.shift_nm)
                    x_used.append(xi)
            if y:
                ax.plot(x_used, y, "o-", color=_COLORS[i % len(_COLORS)],
                        label=f'R={radius}"', linewidth=1.6, markersize=5)

        ax.axhline(0, color="black", linewidth=0.5, linestyle="--")
        ax.set_title(title, fontsize=10)
        ax.set_ylabel(y_label, fontsize=8)

        if use_dates:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d/%y"))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            self._fig.autofmt_xdate(rotation=25, ha="right")
        else:
            ax.set_xlabel("Run index")

        if all_radii:
            ax.legend(loc="best", fontsize=8, ncol=min(3, len(all_radii)))
        ax.grid(True, linewidth=0.4, alpha=0.5)
        self._fig.tight_layout(pad=1.5)
        self._canvas.draw()

    def _update_table(self, summaries: list[RunSummary]) -> None:
        for row in self._tree.get_children():
            self._tree.delete(row)

        for s in reversed(summaries):
            radii_str = "  ".join(f'{m.radius}"' for m in s.measurements)
            score_str = f"{s.uniformity_score:.2f}"
            if self._show_run_number:
                rn = s.run.run_number or "—"
                self._tree.insert("", tk.END, values=(
                    s.run.date or "?", rn, s.run.design, radii_str, score_str,
                ))
            else:
                f_str = f"{s.run.f_factor:.3f}" if s.run.f_factor else "—"
                self._tree.insert("", tk.END, values=(
                    s.run.date or "?", s.run.design, f_str, radii_str, score_str,
                ))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class HealthDashboard(tk.Tk):
    def __init__(self, db: UniformityDB, spectro_dir: Path = SPECTRO_DIR):
        super().__init__()
        self._db          = db
        self._spectro_dir = spectro_dir
        self._materials   = _load_material_targets()

        self.title("Chamber Health Dashboard  —  FiveNine Optics")
        self.geometry("1120x780")
        self.resizable(True, True)

        self._build_ui()
        self._refresh_chambers()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Shared top toolbar
        toolbar = ttk.Frame(self, padding=6)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(toolbar, text="Chamber:").pack(side=tk.LEFT)
        self._chamber_var = tk.StringVar()
        self._chamber_cb  = ttk.Combobox(
            toolbar, textvariable=self._chamber_var,
            state="readonly", width=6,
        )
        self._chamber_cb.pack(side=tk.LEFT, padx=(2, 16))
        self._chamber_cb.bind("<<ComboboxSelected>>", self._on_chamber_changed)

        self._scan_btn = ttk.Button(
            toolbar, text="Scan new data", command=self._scan_async,
        )
        self._scan_btn.pack(side=tk.RIGHT, padx=4)
        self._status_var = tk.StringVar()
        ttk.Label(toolbar, textvariable=self._status_var,
                  foreground="gray").pack(side=tk.RIGHT, padx=8)

        # Notebook
        self._nb = ttk.Notebook(self)
        self._nb.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        self._build_design_tab()
        self._build_material_tab()

    # ── Design tab ────────────────────────────────────────────────────────

    def _build_design_tab(self) -> None:
        frame = ttk.Frame(self._nb)
        self._nb.add(frame, text="  Design Uniformity  ")

        sub = ttk.Frame(frame, padding=(6, 4))
        sub.pack(fill=tk.X)
        ttk.Label(sub, text="Design:").pack(side=tk.LEFT)
        self._design_var = tk.StringVar(value=_ALL)
        self._design_cb  = ttk.Combobox(
            sub, textvariable=self._design_var,
            state="readonly", width=32,
        )
        self._design_cb.pack(side=tk.LEFT, padx=(2, 0))
        self._design_cb.bind("<<ComboboxSelected>>", lambda _: self._reload_design())

        self._design_panel = _UniformityPanel(frame, show_run_number=False)
        self._design_panel.pack(fill=tk.BOTH, expand=True)

    # ── Material Health tab ───────────────────────────────────────────────

    def _build_material_tab(self) -> None:
        frame = ttk.Frame(self._nb)
        self._nb.add(frame, text="  Material Health  ")

        sub = ttk.Frame(frame, padding=(6, 4))
        sub.pack(fill=tk.X)
        ttk.Label(sub, text="Material:").pack(side=tk.LEFT)
        self._material_var = tk.StringVar()
        mat_options = [_material_label(m) for m in self._materials]
        self._material_cb  = ttk.Combobox(
            sub, textvariable=self._material_var,
            values=mat_options, state="readonly", width=22,
        )
        self._material_cb.pack(side=tk.LEFT, padx=(2, 0))
        if mat_options:
            self._material_cb.current(0)
        self._material_cb.bind("<<ComboboxSelected>>", lambda _: self._reload_material())

        self._material_panel = _UniformityPanel(frame, show_run_number=True)
        self._material_panel.pack(fill=tk.BOTH, expand=True)

    # ── Data refresh ──────────────────────────────────────────────────────

    def _refresh_chambers(self) -> None:
        chambers = self._db.chambers()
        self._chamber_cb["values"] = chambers
        if chambers:
            self._chamber_var.set(chambers[0])
            self._on_chamber_changed()

    def _on_chamber_changed(self, _event=None) -> None:
        ch = self._chamber_var.get()
        designs = [_ALL] + self._db.designs_for_chamber(ch)
        self._design_cb["values"] = designs
        self._design_var.set(_ALL)
        self._reload_design()
        self._reload_material()

    def _reload_design(self) -> None:
        ch     = self._chamber_var.get()
        design = self._design_var.get()
        if not ch:
            return
        runs = self._db.runs_for_chamber(
            ch, design=None if design == _ALL else design,
        )
        title = f"{ch}  —  {design}" if design != _ALL else ch
        self._design_panel.update_display(runs, title=title)

    def _reload_material(self) -> None:
        ch    = self._chamber_var.get()
        label = self._material_var.get()
        if not ch or not label:
            return

        # Find the matching material target
        mat = next(
            (m for m in self._materials if _material_label(m) == label), None
        )
        if mat is None:
            return

        # Filter runs whose design matches this material
        all_runs = self._db.runs_for_chamber(ch)
        runs = [s for s in all_runs if _match_material(s.run.design, mat)]

        title = f"{ch}  —  {label}"
        self._material_panel.update_display(
            runs,
            title=title,
            y_label=f"Peak shift Δλ (nm)\nvs smallest radius",
        )

    # ── Background scan ───────────────────────────────────────────────────

    def _scan_async(self) -> None:
        self._scan_btn.config(state=tk.DISABLED)
        self._status_var.set("Scanning…")

        def worker():
            try:
                n = scan(
                    self._db,
                    spectro_dir=self._spectro_dir,
                    progress=lambda m: self.after(0, lambda msg=m:
                                                  self._status_var.set(msg)),
                )
                self.after(0, lambda: self._scan_done(n))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Scan error", str(exc)))
                self.after(0, self._scan_reset)

        threading.Thread(target=worker, daemon=True).start()

    def _scan_done(self, n: int) -> None:
        self._scan_reset()
        self._status_var.set(f"{n} new run(s) added")
        self._refresh_chambers()

    def _scan_reset(self) -> None:
        self._scan_btn.config(state=tk.NORMAL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(date_str: Optional[str]) -> datetime:
    try:
        return datetime.strptime(date_str, _DATE_FMT)
    except (ValueError, TypeError):
        return datetime.min


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    db  = UniformityDB()
    app = HealthDashboard(db)
    app.mainloop()
    db.close()


if __name__ == "__main__":
    main()
