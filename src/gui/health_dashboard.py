"""
Chamber Health Dashboard — tkinter + matplotlib.

Window layout
─────────────
  [Chamber dropdown]                       [Scan new data]  [status]

  ╔══════════════════╗╔══════════════════════════╗
  ║ Single Material  ║║ Multi-Material           ║
  ╠══════════════════╩╩══════════════════════════╣
  ║  [Material dropdown]  OR  [Design combo]     ║
  ║                                              ║
  ║  Trend chart  (Δλ per radius vs run date)    ║
  ║                                              ║
  ║  Run table                                   ║
  ╚══════════════════════════════════════════════╝

Tabs
────
  Single Material    — 6 canonical single-material health checks
  Multi-Material     — combination layer-stack health checks
                       (HW 16L Ta/SiO₂, 8L Ta/SiO₂, 16L Hf/SiO₂, …)
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
from utils.uniformity_scanner import scan, backfill_run_numbers, SPECTRO_DIR

_DATE_FMT = "%m/%d/%Y"
_COLORS   = ["#1f77b4","#ff7f0e","#2ca02c","#d62728",
             "#9467bd","#8c564b","#e377c2","#7f7f7f"]

_CFG_DIR  = Path(__file__).parent.parent.parent / "config"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    path = _CFG_DIR / "material_targets.yaml"
    if not path.exists():
        return {"materials": [], "designs": []}
    with open(path) as f:
        data = yaml.safe_load(f)
    return {
        "materials": data.get("materials", []),
        "designs":   data.get("designs",   []),
    }


# ---------------------------------------------------------------------------
# Target matching
# ---------------------------------------------------------------------------

def _matches_single(design: str, target: dict) -> bool:
    """True if design name refers to this single-material target."""
    if not design:
        return False
    d = re.sub(r"[^a-z0-9 ]", " ", design.lower())
    names = [target["name"].lower()] + [a.lower() for a in target.get("aliases", [])]
    t = str(target["target_nm"])
    return any(n in d for n in names) and t in d and "nm" in d


def _matches_combo(design: str, target: dict) -> bool:
    """True if design name matches all keywords for this combination target."""
    if not design:
        return False
    tokens = set(re.findall(r"[a-z0-9]+", design.lower()))
    return all(kw.lower() in tokens for kw in target.get("keywords", []))


def _single_label(mat: dict) -> str:
    return f"{mat['name']}  {mat['target_nm']} nm"


def _combo_label(des: dict) -> str:
    return des["label"]


# ---------------------------------------------------------------------------
# Shared chart + table panel
# ---------------------------------------------------------------------------

class _UniformityPanel(ttk.Frame):
    """Reusable trend chart + run table, used in every tab."""

    def __init__(self, parent: tk.Widget):
        super().__init__(parent)
        self._build_chart()
        self._build_table()

    # ── Build ──────────────────────────────────────────────────────────────

    def _build_chart(self) -> None:
        self._fig = Figure(figsize=(10, 4.6), dpi=96)
        self._ax_wl  = self._fig.add_subplot(2, 1, 1)
        self._ax_pct = self._fig.add_subplot(2, 1, 2, sharex=self._ax_wl)

        frame = ttk.Frame(self)
        frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self._canvas = FigureCanvasTkAgg(self._fig, master=frame)
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        nav = NavigationToolbar2Tk(self._canvas, frame)
        nav.update()

    def _build_table(self) -> None:
        frame = ttk.LabelFrame(self, text="Runs", padding=4)
        frame.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=(0, 4))

        cols = ("date", "run_number", "design", "f_factor", "radii", "score")
        headings = {
            "date":       ("Date",          100),
            "run_number": ("Run #",          90),
            "design":     ("Design",        220),
            "f_factor":   ("F",              60),
            "radii":      ('Radii (")',     180),
            "score":      ("Score (nm p-p)", 110),
        }

        self._tree = ttk.Treeview(frame, columns=cols, show="headings", height=4)
        for col, (text, width) in headings.items():
            self._tree.heading(col, text=text)
            anchor = "center" if col in ("date", "run_number", "f_factor", "score") else "w"
            self._tree.column(col, width=width, anchor=anchor)

        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    # ── Update ─────────────────────────────────────────────────────────────

    def update_display(self, summaries: list[RunSummary], title: str = "") -> None:
        self._update_chart(summaries, title)
        self._update_table(summaries)

    def _update_chart(self, summaries: list[RunSummary], title: str) -> None:
        ax_wl  = self._ax_wl
        ax_pct = self._ax_pct
        ax_wl.clear()
        ax_pct.clear()

        if not summaries:
            ax_wl.set_title(title or "No data for selection")
            self._canvas.draw()
            return

        all_radii = sorted({m.radius for s in summaries for m in s.measurements})
        dates     = [_parse_date(s.run.date) for s in summaries]
        use_dates = len(set(d.date() for d in dates)) > 1
        x_vals    = dates if use_dates else list(range(len(summaries)))

        # Reference %T per run: measurement at minimum radius
        pct_refs = {
            s.run.id: min(s.measurements, key=lambda m: m.radius).peak_pct
            for s in summaries if s.measurements
        }

        for i, radius in enumerate(all_radii):
            y_wl, y_pct, x_used = [], [], []
            for xi, s in zip(x_vals, summaries):
                m = next((m for m in s.measurements if m.radius == radius), None)
                if m is not None:
                    y_wl.append(m.shift_nm)
                    y_pct.append(m.peak_pct - pct_refs.get(s.run.id, m.peak_pct))
                    x_used.append(xi)
            color = _COLORS[i % len(_COLORS)]
            lbl   = f'R={radius}"'
            if y_wl:
                ax_wl.plot(x_used, y_wl, "o-", color=color, label=lbl,
                           linewidth=1.6, markersize=5)
            if y_pct:
                ax_pct.plot(x_used, y_pct, "o-", color=color, label=lbl,
                            linewidth=1.6, markersize=5)

        for ax in (ax_wl, ax_pct):
            ax.axhline(0, color="black", linewidth=0.5, linestyle="--")
            ax.grid(True, linewidth=0.4, alpha=0.5)

        ax_wl.set_title(title, fontsize=10)
        ax_wl.set_ylabel("Δλ (nm)\nvs R_min", fontsize=8)
        ax_pct.set_ylabel("Δ%T\nvs R_min", fontsize=8)

        if use_dates:
            ax_pct.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d/%y"))
            ax_pct.xaxis.set_major_locator(mdates.AutoDateLocator())
            self._fig.autofmt_xdate(rotation=25, ha="right")
        else:
            ax_pct.set_xlabel("Run index")

        if all_radii:
            ax_wl.legend(loc="best", fontsize=8, ncol=min(3, len(all_radii)))

        self._fig.tight_layout(pad=1.5)
        self._canvas.draw()

    def _update_table(self, summaries: list[RunSummary]) -> None:
        for row in self._tree.get_children():
            self._tree.delete(row)

        for s in reversed(summaries):
            radii_str = "  ".join(f'{m.radius}"' for m in s.measurements)
            self._tree.insert("", tk.END, values=(
                s.run.date or "?",
                s.run.run_number or "—",
                s.run.design,
                f"{s.run.f_factor:.3f}" if s.run.f_factor else "—",
                radii_str,
                f"{s.uniformity_score:.2f}",
            ))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class HealthDashboard(tk.Tk):
    def __init__(self, db: UniformityDB, spectro_dir: Path = SPECTRO_DIR):
        super().__init__()
        self._db          = db
        self._spectro_dir = spectro_dir
        cfg = _load_config()
        self._singles  = cfg["materials"]
        self._combos   = cfg["designs"]

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

        # Notebook (two tabs)
        self._nb = ttk.Notebook(self)
        self._nb.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        self._build_single_tab()
        self._build_combo_tab()

    # ── Tab: Single Material ──────────────────────────────────────────────

    def _build_single_tab(self) -> None:
        frame = ttk.Frame(self._nb)
        self._nb.add(frame, text="  Single Material  ")

        sub = ttk.Frame(frame, padding=(6, 4))
        sub.pack(fill=tk.X)
        ttk.Label(sub, text="Material:").pack(side=tk.LEFT)
        self._single_var = tk.StringVar()
        opts = [_single_label(m) for m in self._singles]
        self._single_cb  = ttk.Combobox(
            sub, textvariable=self._single_var,
            values=opts, state="readonly", width=22,
        )
        self._single_cb.pack(side=tk.LEFT, padx=(2, 8))
        if opts:
            self._single_cb.current(0)
        self._single_cb.bind("<<ComboboxSelected>>",
                             lambda _: self._reload_single())

        self._single_panel = _UniformityPanel(frame)
        self._single_panel.pack(fill=tk.BOTH, expand=True)

    # ── Tab: Multi-Material ───────────────────────────────────────────────

    def _build_combo_tab(self) -> None:
        frame = ttk.Frame(self._nb)
        self._nb.add(frame, text="  Multi-Material  ")

        sub = ttk.Frame(frame, padding=(6, 4))
        sub.pack(fill=tk.X)
        ttk.Label(sub, text="Design:").pack(side=tk.LEFT)
        self._combo_var = tk.StringVar()
        opts = [_combo_label(d) for d in self._combos]
        self._combo_cb  = ttk.Combobox(
            sub, textvariable=self._combo_var,
            values=opts, state="readonly", width=32,
        )
        self._combo_cb.pack(side=tk.LEFT, padx=(2, 8))
        if opts:
            self._combo_cb.current(0)
        self._combo_cb.bind("<<ComboboxSelected>>",
                            lambda _: self._reload_combo())

        self._combo_panel = _UniformityPanel(frame)
        self._combo_panel.pack(fill=tk.BOTH, expand=True)

    # ── Data refresh ──────────────────────────────────────────────────────

    _KNOWN_CHAMBERS = ["V1", "V2", "V3", "V4", "V5", "V6", "V7"]

    def _refresh_chambers(self) -> None:
        in_db = set(self._db.chambers())
        chambers = self._KNOWN_CHAMBERS + sorted(in_db - set(self._KNOWN_CHAMBERS))
        self._chamber_cb["values"] = chambers
        if chambers:
            self._chamber_var.set(chambers[0])
            self._on_chamber_changed()

    def _on_chamber_changed(self, _event=None) -> None:
        self._reload_single()
        self._reload_combo()

    def _reload_single(self) -> None:
        ch    = self._chamber_var.get()
        label = self._single_var.get()
        if not ch or not label:
            return
        mat = next((m for m in self._singles if _single_label(m) == label), None)
        if mat is None:
            return
        all_runs = self._db.runs_for_chamber(ch)
        runs = [s for s in all_runs if _matches_single(s.run.design, mat)]
        self._single_panel.update_display(runs, title=f"{ch}  —  {label}")

    def _reload_combo(self) -> None:
        ch    = self._chamber_var.get()
        label = self._combo_var.get()
        if not ch or not label:
            return
        des = next((d for d in self._combos if _combo_label(d) == label), None)
        if des is None:
            return
        all_runs = self._db.runs_for_chamber(ch)
        runs = [s for s in all_runs if _matches_combo(s.run.design, des)]
        self._combo_panel.update_display(runs, title=f"{ch}  —  {label}")

    # ── Background scan ───────────────────────────────────────────────────

    def _scan_async(self) -> None:
        self._scan_btn.config(state=tk.DISABLED)
        self._status_var.set("Scanning…")

        def worker():
            try:
                n = scan(
                    self._db,
                    spectro_dir=self._spectro_dir,
                    progress=lambda m: self.after(
                        0, lambda msg=m: self._status_var.set(msg)
                    ),
                )
                backfill_run_numbers(
                    self._db,
                    progress=lambda m: self.after(
                        0, lambda msg=m: self._status_var.set(msg)
                    ),
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
