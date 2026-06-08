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
from typing import Optional, List

import tkinter as tk
from tkinter import ttk, messagebox

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
import yaml

_SRC = Path(__file__).parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.uniformity_db import UniformityDB, RunSummary
from utils.uniformity_scanner import SPECTRO_DIR, _build_records
from utils.uniformity_analyzer import analyze_run, resolve_design_label
from utils.design_resolver import is_multi_material
from utils.sp_parser import parse_sp

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

def _material_labels(design: str) -> tuple[str, str]:
    """Infer (scale1_label, scale2_label) from design name."""
    d = design.lower()
    if "hf" in d:
        return "HfO₂", "SiO₂"
    return "Ta₂O₅", "SiO₂"


class _UniformityPanel(ttk.Frame):
    """Run table + per-run uniformity profile charts."""

    def __init__(self, parent: tk.Widget, *, show_run_number: bool = False):
        super().__init__(parent)
        self._show_run_number = show_run_number
        self._summaries: list[RunSummary] = []
        self._title: str = ""
        self._sort_col: str = "date"
        self._sort_asc: bool = False
        self._build_chart_area()
        self._build_table()

    # ── Build ──────────────────────────────────────────────────────────────

    def _build_chart_area(self) -> None:
        frame = ttk.Frame(self)
        frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._fig = Figure(figsize=(10, 4.5), dpi=96, layout="constrained")
        self._canvas = FigureCanvasTkAgg(self._fig, master=frame)
        self._canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        nav = NavigationToolbar2Tk(self._canvas, frame)
        nav.update()
        self._show_placeholder()

    def _show_placeholder(self) -> None:
        self._fig.clear()
        ax = self._fig.add_subplot(111)
        ax.text(0.5, 0.5, "Select a run to view its uniformity profile",
                ha="center", va="center", transform=ax.transAxes,
                fontsize=11, color="gray")
        ax.axis("off")
        self._canvas.draw()

    def _build_table(self) -> None:
        frame = ttk.LabelFrame(self, text="Runs", padding=4)
        frame.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=(0, 4))

        if self._show_run_number:
            cols = ("date", "run_number", "design", "ref_design", "radii", "score")
            headings = {
                "date":       ("Date",           100),
                "run_number": ("Run #",           90),
                "design":     ("Design",         180),
                "ref_design": ("Ref design",     190),
                "radii":      ('Radii (")',      160),
                "score":      ("Score (% p-p)",  90),
            }
        else:
            cols = ("date", "design", "f_factor", "radii", "score")
            headings = {
                "date":     ("Date",          100),
                "design":   ("Design",        240),
                "f_factor": ("F",              60),
                "radii":    ('Radii (")',     200),
                "score":    ("Score (% p-p)", 110),
            }

        self._headings = headings
        self._tree = ttk.Treeview(
            frame, columns=cols, show="headings", height=5,
            selectmode="extended",
        )
        _sortable = {"date", "run_number", "score"}
        for col, (text, width) in headings.items():
            if col in _sortable:
                self._tree.heading(col, text=text, command=lambda c=col: self._sort_by(c))
            else:
                self._tree.heading(col, text=text)
            anchor = "center" if col in ("date", "run_number", "f_factor", "score") else "w"
            self._tree.column(col, width=width, anchor=anchor)
        self._refresh_sort_indicator()
        self._tree.bind("<<TreeviewSelect>>", lambda _: self._on_selection_changed())

        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    # ── Update ─────────────────────────────────────────────────────────────

    def update_display(
        self,
        summaries: list[RunSummary],
        title: str = "",
        y_label: str | None = None,   # kept for API compatibility, unused
    ) -> None:
        self._title = title
        self._update_table(summaries)
        self._show_placeholder()

    # ── Sorting ────────────────────────────────────────────────────────────

    def _sort_key(self, s: RunSummary):
        col = self._sort_col
        if col == "date":
            return _parse_date(s.run.date)
        if col == "run_number":
            rn = s.run.run_number or ""
            m = re.search(r"\d+", rn)
            return (0 if rn else 1, int(m.group()) if m else 0)
        if col == "score":
            if s.measurements:
                ref = min(s.measurements, key=lambda m: m.radius).peak_nm
                return s.uniformity_score / ref * 100 if ref else 0.0
            return 0.0
        return 0

    def _sort_by(self, col: str) -> None:
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        self._refresh_sort_indicator()
        self._populate_table()

    def _refresh_sort_indicator(self) -> None:
        for col, (text, _) in self._headings.items():
            label = f"{text} {'▲' if self._sort_asc else '▼'}" if col == self._sort_col else text
            self._tree.heading(col, text=label)

    def _update_table(self, summaries: list[RunSummary]) -> None:
        self._summaries = summaries
        self._populate_table()

    def _populate_table(self) -> None:
        for row in self._tree.get_children():
            self._tree.delete(row)

        ordered = sorted(self._summaries, key=self._sort_key, reverse=not self._sort_asc)
        for s in ordered:
            radii_str = "  ".join(f'{m.radius}"' for m in s.measurements)
            if s.has_scale_data:
                score_str = f"{s.uniformity_score:.3f}"
            elif s.measurements:
                ref_nm = min(s.measurements, key=lambda m: m.radius).peak_nm
                score_str = f"{s.uniformity_score / ref_nm * 100:.4f}" if ref_nm else "—"
            else:
                score_str = "—"
            if self._show_run_number:
                rn       = s.run.run_number or "—"
                ref      = Path(s.run.design_file).stem if s.run.design_file else "—"
                analysed = " *" if s.has_scale_data else ""
                self._tree.insert("", tk.END, iid=str(s.run.id), values=(
                    s.run.date or "?", rn, s.run.design,
                    ref + analysed, radii_str, score_str,
                ))
            else:
                f_str = f"{s.run.f_factor:.3f}" if s.run.f_factor else "—"
                self._tree.insert("", tk.END, iid=str(s.run.id), values=(
                    s.run.date or "?", s.run.design, f_str, radii_str, score_str,
                ))

    # ── Selection → charts ─────────────────────────────────────────────────

    def _on_selection_changed(self) -> None:
        selected_ids = {int(iid) for iid in self._tree.selection()}
        visible = [s for s in self._summaries if s.run.id in selected_ids] if selected_ids else []
        self._draw_profiles(visible)

    def _draw_profiles(self, summaries: list[RunSummary]) -> None:
        scale_runs = [s for s in summaries if s.has_scale_data]

        if not scale_runs:
            self._show_placeholder()
            return

        n     = len(scale_runs)
        ncols = min(n, 3)
        nrows = (n + ncols - 1) // ncols

        self._fig.clear()

        for i, s in enumerate(scale_runs):
            ax = self._fig.add_subplot(nrows, ncols, i + 1)

            meas       = sorted(s.measurements, key=lambda m: m.radius)
            scale_meas = [m for m in meas if m.scale1 is not None]

            if not scale_meas:
                ax.text(0.5, 0.5, "Not yet analyzed", ha="center", va="center",
                        transform=ax.transAxes, color="gray", fontsize=9)
                ax.axis("off")
                continue

            radii   = [m.radius for m in scale_meas]
            scales1 = [m.scale1 for m in scale_meas]
            scales2 = [m.scale2 for m in scale_meas if m.scale2 is not None]
            norm1   = [v / scales1[0] for v in scales1]
            pv1     = (max(norm1) - min(norm1)) * 100
            mat1, mat2 = _material_labels(s.run.design)

            ax.plot(radii, norm1, "o-", color="steelblue",
                    linewidth=1.5, markersize=4,
                    label=f"{mat1}  {pv1:.2f}% p-v")

            if len(scales2) == len(scales1):
                norm2 = [v / scales2[0] for v in scales2]
                pv2   = (max(norm2) - min(norm2)) * 100
                ax.plot(radii, norm2, "s--", color="darkorange",
                        linewidth=1.5, markersize=4,
                        label=f"{mat2}  {pv2:.2f}% p-v")

            ax.axhline(1.0, color="black", linewidth=0.7, linestyle="--")
            run_label = s.run.run_number or s.run.date or f"run {s.run.id}"
            ax.set_title(run_label, fontsize=9)
            ax.set_xlabel('Radius (")', fontsize=7)
            ax.set_ylabel("Normalized thickness", fontsize=7)
            ax.tick_params(labelsize=7)
            ax.grid(True, linewidth=0.4, alpha=0.5)
            ax.legend(fontsize=7, loc="best")

        self._canvas.draw()


# ---------------------------------------------------------------------------
# Add-run dialog
# ---------------------------------------------------------------------------

class _AddRunDialog(tk.Toplevel):
    """Modal dialog to add a uniformity run from a P-file range."""

    def __init__(self, parent: tk.Widget, db: UniformityDB,
                 spectro_dir: Path, on_done):
        super().__init__(parent)
        self.title("Add Uniformity Run")
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)
        self._db          = db
        self._spectro_dir = spectro_dir
        self._on_done     = on_done
        self._build()

    def _build(self) -> None:
        p = {"padx": 6, "pady": 3}

        # ── SP range ──────────────────────────────────────────────────────
        rng = ttk.LabelFrame(self, text="SP file range", padding=10)
        rng.pack(fill=tk.X, padx=14, pady=(14, 4))

        ttk.Label(rng, text="From P").grid(row=0, column=0, sticky="e", **p)
        self._start_var = tk.StringVar()
        ttk.Entry(rng, textvariable=self._start_var, width=10).grid(
            row=0, column=1, sticky="w", **p)

        ttk.Label(rng, text="To P").grid(row=0, column=2, sticky="e", **p)
        self._end_var = tk.StringVar()
        ttk.Entry(rng, textvariable=self._end_var, width=10).grid(
            row=0, column=3, sticky="w", **p)

        ttk.Button(rng, text="Preview", command=self._preview).grid(
            row=0, column=4, padx=(12, 0))

        # ── Preview / detected info ────────────────────────────────────────
        self._preview_var = tk.StringVar()
        ttk.Label(self, textvariable=self._preview_var, foreground="#444",
                  wraplength=420, justify="left").pack(padx=14, pady=(4, 0))

        # ── Run details ───────────────────────────────────────────────────
        det = ttk.LabelFrame(self, text="Run details", padding=10)
        det.pack(fill=tk.X, padx=14, pady=4)

        ttk.Label(det, text="Run number:").grid(row=0, column=0, sticky="e", **p)
        self._runnum_var = tk.StringVar()
        ttk.Entry(det, textvariable=self._runnum_var, width=14).grid(
            row=0, column=1, sticky="w", **p)
        ttk.Label(det, text="optional — e.g. V6-439",
                  foreground="gray").grid(row=0, column=2, sticky="w", **p)

        # ── Error label ───────────────────────────────────────────────────
        self._err_var = tk.StringVar()
        ttk.Label(self, textvariable=self._err_var, foreground="red",
                  wraplength=420, justify="left").pack(padx=14)

        # ── Buttons ───────────────────────────────────────────────────────
        btns = ttk.Frame(self)
        btns.pack(fill=tk.X, padx=14, pady=(6, 14))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(
            side=tk.RIGHT, padx=4)
        self._add_btn = ttk.Button(btns, text="Add Run", command=self._add)
        self._add_btn.pack(side=tk.RIGHT, padx=4)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _parse_range(self):
        """Return (paths, error_str). paths is [] on error."""
        try:
            start = int(self._start_var.get().strip().lstrip("Pp"))
            end   = int(self._end_var.get().strip().lstrip("Pp"))
        except ValueError:
            return [], "Enter numeric P-file numbers (e.g. 1014134)"
        if end < start:
            return [], "End must be ≥ start"
        if end - start > 50:
            return [], "Range too large (max 50 files)"
        paths = [self._spectro_dir / f"P{i}.SP" for i in range(start, end + 1)]
        missing = [p.name for p in paths if not p.exists()]
        if missing:
            return [], f"Not found: {', '.join(missing)}"
        return paths, ""

    def _preview(self) -> None:
        self._err_var.set("")
        self._preview_var.set("")
        paths, err = self._parse_range()
        if err:
            self._err_var.set(err)
            return
        try:
            sp_files = [parse_sp(p) for p in paths]
        except Exception as e:
            self._err_var.set(str(e))
            return
        anchor = next((s for s in sp_files
                       if s.chamber and s.design_name and s.date), None)
        if anchor is None:
            self._err_var.set(
                "No anchor file found — first file must have a full uniformity header "
                "(Chamber-Unif Design Date, F=…, R=…)")
            return
        radii = [s.radius for s in sp_files if s.radius is not None]
        self._preview_var.set(
            f"Detected:  {anchor.chamber}  |  {anchor.design_name}  |  "
            f"{anchor.date}  |  F={anchor.f_factor}  |  "
            f"Radii: {', '.join(str(r) for r in sorted(radii))}\"")

    def _add(self) -> None:
        self._err_var.set("")
        paths, err = self._parse_range()
        if err:
            self._err_var.set(err)
            return
        try:
            sp_files = [parse_sp(p) for p in paths]
        except Exception as e:
            self._err_var.set(str(e))
            return

        anchor = next((s for s in sp_files
                       if s.chamber and s.design_name and s.date), None)
        if anchor is None:
            self._err_var.set(
                "No anchor file found in range — the first SP file must contain "
                "the full uniformity header.")
            return

        run, measurements = _build_records(anchor, anchor.path.name, sp_files)

        run_number = self._runnum_var.get().strip()
        if run_number:
            run.run_number = run_number

        run_id = self._db.save_run(run, measurements)
        if run_id < 0:
            self._err_var.set(
                "This run is already in the database (anchor file already exists).")
            return

        self._on_done()
        self.destroy()


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

        ttk.Button(
            toolbar, text="Add uniformity run", command=self._open_add_dialog,
        ).pack(side=tk.RIGHT, padx=4)
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

        ttk.Label(sub, text="Ref:").pack(side=tk.LEFT)
        self._single_ref_var = tk.StringVar(value="—")
        ttk.Label(sub, textvariable=self._single_ref_var,
                  foreground="#555", width=30).pack(side=tk.LEFT, padx=(2, 8))

        self._single_analyze_btn = ttk.Button(
            sub, text="Analyze visible runs",
            command=lambda: self._analyze_async(self._single_summaries, self._single_panel),
        )
        self._single_analyze_btn.pack(side=tk.LEFT)

        self._single_summaries: List[RunSummary] = []
        self._single_panel = _UniformityPanel(frame, show_run_number=True)
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

        ttk.Label(sub, text="Ref:").pack(side=tk.LEFT)
        self._combo_ref_var = tk.StringVar(value="—")
        ttk.Label(sub, textvariable=self._combo_ref_var,
                  foreground="#555", width=30).pack(side=tk.LEFT, padx=(2, 8))

        self._combo_analyze_btn = ttk.Button(
            sub, text="Analyze visible runs",
            command=lambda: self._analyze_async(self._combo_summaries, self._combo_panel),
        )
        self._combo_analyze_btn.pack(side=tk.LEFT)

        self._combo_summaries: List[RunSummary] = []
        self._combo_panel = _UniformityPanel(frame, show_run_number=True)
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
        self._single_summaries = runs

        # Show reference design label
        ref = (resolve_design_label(ch, label, [])
               if runs else "—")
        self._single_ref_var.set(ref)

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
        self._combo_summaries = runs

        # Show reference design label
        kws = [d.get("keywords", []) for d in self._combos]
        ref = (resolve_design_label(ch, label, kws) if runs else "—")
        self._combo_ref_var.set(ref)

        self._combo_panel.update_display(runs, title=f"{ch}  —  {label}")

    # ── Background analysis ───────────────────────────────────────────────

    def _analyze_async(
        self,
        summaries: List[RunSummary],
        panel: "_UniformityPanel",
    ) -> None:
        """Run MacLeod analysis for all visible runs in a background thread."""
        if not summaries:
            messagebox.showinfo("Analyze", "No runs to analyse for this selection.")
            return

        kws = [d.get("keywords", []) for d in self._combos]

        def worker():
            for i, s in enumerate(summaries):
                self.after(0, lambda i=i, s=s: self._status_var.set(
                    f"Analysing run {i+1}/{len(summaries)}: "
                    f"{s.run.chamber} {s.run.date} {s.run.run_number or ''}…"
                ))
                try:
                    analyze_run(
                        s, self._db,
                        combo_keywords=kws,
                        progress=lambda m: self.after(
                            0, lambda msg=m: self._status_var.set(msg)
                        ),
                    )
                except Exception as exc:
                    self.after(0, lambda e=exc: self._status_var.set(f"Error: {e}"))

            # Reload DB and refresh panel
            ch    = self._chamber_var.get()
            fresh = self._db.runs_for_chamber(ch)
            run_ids = {s.run.id for s in summaries}
            updated = [s for s in fresh if s.run.id in run_ids]
            self.after(0, lambda: panel.update_display(
                updated,
                title=panel._title,
            ))
            self.after(0, lambda: self._status_var.set(
                f"Analysis done — {len(summaries)} run(s)"
            ))

        threading.Thread(target=worker, daemon=True).start()

    # ── Add run dialog ────────────────────────────────────────────────────

    def _open_add_dialog(self) -> None:
        _AddRunDialog(
            self, self._db, self._spectro_dir,
            on_done=self._refresh_chambers,
        )


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
