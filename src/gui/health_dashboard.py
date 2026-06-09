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

import numpy as np
from scipy.interpolate import CubicSpline
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
from utils.uniformity_analyzer import resolve_design_label
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

def _pv_tag(pv: float) -> str:
    """Traffic-light tag name for a p-v uniformity score (%)."""
    if pv < 0.5:
        return "pv_green"
    if pv <= 1.0:
        return "pv_yellow"
    return "pv_red"

# Foreground colors for chart legend text (dark enough to read on white)
_PV_TEXT_COLOR = {
    "pv_green":  "darkgreen",
    "pv_yellow": "darkorange",
    "pv_red":    "darkred",
}


def _spline_smooth(x: list, y: list, n: int = 300):
    """Return (xs, ys) — a smooth cubic spline through (x, y).
    Falls back to the original points if fewer than 4 are provided."""
    if len(x) < 4:
        return x, y
    cs = CubicSpline(x, y)
    xs = np.linspace(x[0], x[-1], n)
    return xs, cs(xs)


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
        self._tree.tag_configure("pv_green",  background="#c6efce")
        self._tree.tag_configure("pv_yellow", background="#ffeb9c")
        self._tree.tag_configure("pv_red",    background="#ffc7ce")

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
            score_float: float | None = None
            if s.has_scale_data:
                score_float = s.uniformity_score
                score_str   = f"{score_float:.3f}"
            elif s.measurements:
                ref_nm = min(s.measurements, key=lambda m: m.radius).peak_nm
                score_str = f"{s.uniformity_score / ref_nm * 100:.4f}" if ref_nm else "—"
            else:
                score_str = "—"
            tag = (_pv_tag(score_float),) if score_float is not None else ()
            if self._show_run_number:
                rn       = s.run.run_number or "—"
                ref      = Path(s.run.design_file).stem if s.run.design_file else "—"
                analysed = " *" if s.has_scale_data else ""
                self._tree.insert("", tk.END, iid=str(s.run.id), tags=tag, values=(
                    s.run.date or "?", rn, s.run.design,
                    ref + analysed, radii_str, score_str,
                ))
            else:
                f_str = f"{s.run.f_factor:.3f}" if s.run.f_factor else "—"
                self._tree.insert("", tk.END, iid=str(s.run.id), tags=tag, values=(
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

            xs1, ys1 = _spline_smooth(radii, norm1)
            ax.plot(xs1, ys1, "-", color="steelblue", linewidth=1.5)
            ax.plot(radii, norm1, "o", color="steelblue", markersize=4,
                    label=f"{mat1}  {pv1:.2f}% p-v")

            if len(scales2) == len(scales1):
                norm2 = [v / scales2[0] for v in scales2]
                pv2   = (max(norm2) - min(norm2)) * 100
                xs2, ys2 = _spline_smooth(radii, norm2)
                ax.plot(xs2, ys2, "--", color="darkorange", linewidth=1.5)
                ax.plot(radii, norm2, "s", color="darkorange", markersize=4,
                        label=f"{mat2}  {pv2:.2f}% p-v")

            ax.axhline(1.0, color="black", linewidth=0.7, linestyle="--")
            run_label = s.run.run_number or s.run.date or f"run {s.run.id}"
            ax.set_title(run_label, fontsize=9)
            ax.set_xlabel('Radius (")', fontsize=7)
            ax.set_ylabel("Normalized thickness", fontsize=7)
            ax.tick_params(labelsize=7)
            ax.grid(True, linewidth=0.4, alpha=0.5)
            leg = ax.legend(fontsize=7, loc="best")
            pvs = [pv1] + ([pv2] if len(scales2) == len(scales1) else [])
            for txt, pv in zip(leg.get_texts(), pvs):
                txt.set_color(_PV_TEXT_COLOR[_pv_tag(pv)])
                txt.set_fontweight("bold")

        self._canvas.draw()


# ---------------------------------------------------------------------------
# Add-run dialog
# ---------------------------------------------------------------------------

class _AddRunDialog(tk.Toplevel):
    """Modal dialog: enter SP range → analyze in MacLeod → preview → save."""

    def __init__(self, parent: tk.Widget, db: UniformityDB,
                 spectro_dir: Path, on_done):
        super().__init__(parent)
        self.title("Add Uniformity Run")
        self.geometry("700x720")
        self.resizable(True, True)
        self.grab_set()
        self.transient(parent)
        self._db               = db
        self._spectro_dir      = spectro_dir
        self._on_done          = on_done
        self._sp_files         = None
        self._anchor           = None
        self._analysis_results = None   # list of {radius, scale1, scale2, merit}
        self._design_name_map: dict[str, Path] = {}   # combobox label → .dds path
        self._selected_dds: Path | None = None
        self._build()

    # ── Build ──────────────────────────────────────────────────────────────

    def _build(self) -> None:
        p = {"padx": 6, "pady": 3}

        # Row 0: SP range + run number + Preview button
        inp = ttk.LabelFrame(self, text="SP file range", padding=10)
        inp.pack(fill=tk.X, padx=12, pady=(12, 4))
        inp.columnconfigure(3, weight=1)   # combobox column stretches

        ttk.Label(inp, text="From P").grid(row=0, column=0, sticky="e", **p)
        self._start_var = tk.StringVar()
        ttk.Entry(inp, textvariable=self._start_var, width=10).grid(
            row=0, column=1, sticky="w", **p)

        ttk.Label(inp, text="To P").grid(row=0, column=2, sticky="e", **p)
        self._end_var = tk.StringVar()
        ttk.Entry(inp, textvariable=self._end_var, width=10).grid(
            row=0, column=3, sticky="w", **p)

        ttk.Label(inp, text="Run #").grid(row=0, column=4, sticky="e", **p)
        self._runnum_var = tk.StringVar()
        ttk.Entry(inp, textvariable=self._runnum_var, width=10).grid(
            row=0, column=5, sticky="w", **p)
        ttk.Label(inp, text="optional", foreground="gray").grid(
            row=0, column=6, sticky="w", padx=(0, 8))

        self._preview_btn = ttk.Button(inp, text="Preview",
                                        command=self._do_preview)
        self._preview_btn.grid(row=0, column=7, padx=(4, 0))

        # Row 1: Reference design selector + Analyze button
        ttk.Label(inp, text="Reference design:").grid(
            row=1, column=0, columnspan=2, sticky="e", **p)
        self._design_var = tk.StringVar()
        self._design_cb  = ttk.Combobox(inp, textvariable=self._design_var,
                                         state="disabled", width=44)
        self._design_cb.grid(row=1, column=2, columnspan=5, sticky="ew", **p)

        self._analyze_btn = ttk.Button(inp, text="Analyze",
                                        command=self._start_analysis,
                                        state=tk.DISABLED)
        self._analyze_btn.grid(row=1, column=7, padx=(4, 0))

        # Detected metadata line
        self._info_var = tk.StringVar()
        ttk.Label(self, textvariable=self._info_var, foreground="#444",
                  wraplength=660).pack(padx=12, pady=(4, 0), anchor="w")

        # Analysis log
        log_frame = ttk.LabelFrame(self, text="Analysis log", padding=4)
        log_frame.pack(fill=tk.X, padx=12, pady=4)

        self._log = tk.Text(log_frame, height=8, state=tk.DISABLED,
                            font=("Courier", 8), wrap=tk.WORD)
        sb = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self._log.yview)
        self._log.configure(yscrollcommand=sb.set)
        self._log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        # Uniformity preview chart
        chart_frame = ttk.LabelFrame(self, text="Uniformity profile", padding=4)
        chart_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

        self._fig = Figure(figsize=(8, 2.6), dpi=88, layout="constrained")
        self._chart_canvas = FigureCanvasTkAgg(self._fig, master=chart_frame)
        self._chart_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Error label
        self._err_var = tk.StringVar()
        ttk.Label(self, textvariable=self._err_var, foreground="red",
                  wraplength=660).pack(padx=12)

        # Buttons
        btns = ttk.Frame(self)
        btns.pack(fill=tk.X, padx=12, pady=(4, 12))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(
            side=tk.RIGHT, padx=4)
        self._save_btn = ttk.Button(btns, text="Save to Dashboard",
                                     command=self._save, state=tk.DISABLED)
        self._save_btn.pack(side=tk.RIGHT, padx=4)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _log_append(self, msg: str) -> None:
        self._log.configure(state=tk.NORMAL)
        self._log.insert(tk.END, msg + "\n")
        self._log.see(tk.END)
        self._log.configure(state=tk.DISABLED)

    def _parse_range(self):
        try:
            start = int(self._start_var.get().strip().lstrip("Pp"))
            end   = int(self._end_var.get().strip().lstrip("Pp"))
        except ValueError:
            return [], [], "Enter numeric P-file numbers (e.g. 1014134)"
        if end < start:
            return [], [], "End must be ≥ start"
        if end - start > 50:
            return [], [], "Range too large (max 50 files)"
        all_paths  = [self._spectro_dir / f"P{i}.SP" for i in range(start, end + 1)]
        found      = [p for p in all_paths if p.exists()]
        skipped    = [p.name for p in all_paths if not p.exists()]
        if not found:
            return [], [], "No SP files found in that range."
        return found, skipped, ""

    # ── Analysis ───────────────────────────────────────────────────────────

    def _do_preview(self) -> None:
        """Stage 1: parse SP files, detect anchor, populate design dropdown."""
        self._err_var.set("")
        self._info_var.set("")
        self._analysis_results = None
        self._save_btn.configure(state=tk.DISABLED)
        self._analyze_btn.configure(state=tk.DISABLED)
        self._design_cb.configure(state="disabled")
        self._design_var.set("")
        self._design_name_map = {}

        paths, skipped, err = self._parse_range()
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
            loaded = ", ".join(p.name for p in paths)
            skip_note = f"  (skipped: {', '.join(skipped)})" if skipped else ""
            self._err_var.set(
                f"No anchor file found in: {loaded}{skip_note}\n"
                "The anchor file must contain a full header like:\n"
                "  V6-Unif HW 16L 06/03/2026, F=1.044, R=2"
            )
            return

        radii = sorted(s.radius for s in sp_files if s.radius is not None)
        skip_note = f"  (skipped {len(skipped)}: {', '.join(skipped)})" if skipped else ""
        self._info_var.set(
            f"{anchor.chamber}  |  {anchor.design_name}  |  {anchor.date}  |  "
            f"F={anchor.f_factor}  |  Radii: {', '.join(str(r) for r in radii)}\""
            f"{skip_note}"
        )
        self._sp_files = sp_files
        self._anchor   = anchor

        # Populate design dropdown from available .dds files for this chamber
        try:
            from utils.design_resolver import (list_available_paths,
                                                find_design_file,
                                                is_multi_material)
        except ImportError as e:
            self._err_var.set(f"Import error: {e}")
            return

        available = list_available_paths(anchor.chamber)
        options: list[str] = []
        name_map: dict[str, Path] = {}

        for category in ("Single material", "Multi material"):
            tag = "Single" if category.startswith("S") else "Multi"
            for dds_path in available.get(category, []):
                label = f"[{tag}]  {dds_path.stem}"
                options.append(label)
                name_map[label] = dds_path

        self._design_name_map = name_map

        if not options:
            self._err_var.set(
                f"No reference designs found for chamber {anchor.chamber}. "
                "Add .dds files to Chamber Uniformity/{chamber}/.")
            return

        self._design_cb["values"] = options
        self._design_cb.configure(state="readonly")

        # Pre-select best fuzzy match
        multi   = is_multi_material(anchor.design_name, [])
        best    = find_design_file(anchor.chamber, anchor.design_name, multi)
        default = options[0]
        if best:
            for label, path in name_map.items():
                if path.resolve() == best.resolve():
                    default = label
                    break
        self._design_var.set(default)
        self._analyze_btn.configure(state=tk.NORMAL)

    def _start_analysis(self) -> None:
        """Stage 2: run MacLeod analysis using the selected reference design."""
        selected = self._design_var.get()
        if not selected or selected not in self._design_name_map:
            self._err_var.set("Select a reference design from the dropdown.")
            return

        self._selected_dds = self._design_name_map[selected]
        self._err_var.set("")
        self._analysis_results = None
        self._save_btn.configure(state=tk.DISABLED)
        self._log.configure(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.configure(state=tk.DISABLED)
        self._fig.clear()
        self._chart_canvas.draw()
        self._analyze_btn.configure(state=tk.DISABLED)
        threading.Thread(target=self._run_analysis, daemon=True).start()

    def _run_analysis(self) -> None:
        def log(msg):
            self.after(0, lambda m=msg: self._log_append(m))

        try:
            import win32com.client
            from utils.design_resolver import identify_primary_material
            from macleod.com_interface import get_layers, set_targets_from_sp
            from utils.uniformity_analyzer import _run_generalised_simplex
        except ImportError as e:
            self.after(0, lambda: self._err_var.set(f"Import error: {e}"))
            self.after(0, lambda: self._analyze_btn.configure(state=tk.NORMAL))
            return

        dds    = self._selected_dds
        anchor = self._anchor

        log(f"Design: {dds.name}")

        try:
            session    = win32com.client.Dispatch("EMacleod.Session")
            design_obj = session.OpenDesign(str(dds.resolve()))
            if isinstance(design_obj, tuple):
                design_obj = design_obj[0]
        except Exception as e:
            self.after(0, lambda: self._err_var.set(f"MacLeod error: {e}"))
            self.after(0, lambda: self._analyze_btn.configure(state=tk.NORMAL))
            return

        nominal         = get_layers(design_obj)
        layer_materials = [l["material"] for l in nominal]
        # Use the selected .dds stem for material ID (overrides SP header design name)
        primary_kw      = identify_primary_material(dds.stem, layer_materials)
        if not primary_kw:
            self.after(0, lambda: self._err_var.set("Could not identify primary material"))
            self.after(0, lambda: self._analyze_btn.configure(state=tk.NORMAL))
            return

        log(f"Primary material: {primary_kw}  ({len(nominal)} layers)")

        results = []
        sp_with_radius = sorted(
            (s for s in self._sp_files if s.radius is not None and s.wavelengths),
            key=lambda s: s.radius,
        )
        for sp in sp_with_radius:
            set_targets_from_sp(design_obj, sp.wavelengths, sp.transmittances)
            r = _run_generalised_simplex(design_obj, nominal, primary_kw)
            r["radius"] = sp.radius
            results.append(r)
            dev1 = (r["scale1"] - 1.0) * 100
            dev2 = (r["scale2"] - 1.0) * 100 if r.get("scale2") else 0.0
            log(f"  R={sp.radius}\"  {primary_kw}: {dev1:+.2f}%  "
                f"secondary: {dev2:+.2f}%  merit={r['merit']:.4f}")

        log(f"Done — {len(results)}/{len(sp_with_radius)} radii")
        self._analysis_results = results
        self.after(0, self._on_analysis_complete)

    def _on_analysis_complete(self) -> None:
        self._analyze_btn.configure(state=tk.NORMAL)
        if not self._analysis_results:
            return
        self._save_btn.configure(state=tk.NORMAL)
        self._draw_preview_chart()

    def _draw_preview_chart(self) -> None:
        results = self._analysis_results
        if not results:
            return

        radii   = [r["radius"]  for r in results]
        scales1 = [r["scale1"]  for r in results]
        scales2 = [r["scale2"]  for r in results if r.get("scale2") is not None]
        norm1   = [s / scales1[0] for s in scales1]
        pv1     = (max(norm1) - min(norm1)) * 100
        design_label = self._selected_dds.stem if self._selected_dds else self._anchor.design_name
        mat1, mat2   = _material_labels(design_label)

        self._fig.clear()
        ax = self._fig.add_subplot(111)
        xs1, ys1 = _spline_smooth(radii, norm1)
        ax.plot(xs1, ys1, "-", color="steelblue", linewidth=1.5)
        ax.plot(radii, norm1, "o", color="steelblue", markersize=4,
                label=f"{mat1}  {pv1:.2f}% p-v")

        if len(scales2) == len(scales1):
            norm2 = [s / scales2[0] for s in scales2]
            pv2   = (max(norm2) - min(norm2)) * 100
            xs2, ys2 = _spline_smooth(radii, norm2)
            ax.plot(xs2, ys2, "--", color="darkorange", linewidth=1.5)
            ax.plot(radii, norm2, "s", color="darkorange", markersize=4,
                    label=f"{mat2}  {pv2:.2f}% p-v")

        ax.axhline(1.0, color="black", linewidth=0.7, linestyle="--")
        label = (self._runnum_var.get().strip()
                 or f"{self._anchor.chamber} {self._anchor.date}")
        ax.set_title(label, fontsize=9)
        ax.set_xlabel('Radius (")', fontsize=8)
        ax.set_ylabel("Normalized thickness", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(True, linewidth=0.4, alpha=0.5)
        leg = ax.legend(fontsize=8)
        pvs = [pv1] + ([pv2] if len(scales2) == len(scales1) else [])
        for txt, pv in zip(leg.get_texts(), pvs):
            txt.set_color(_PV_TEXT_COLOR[_pv_tag(pv)])
            txt.set_fontweight("bold")
        self._chart_canvas.draw()

    # ── Save ───────────────────────────────────────────────────────────────

    def _save(self) -> None:
        if not self._analysis_results or not self._anchor:
            return

        run, measurements = _build_records(
            self._anchor, self._anchor.path.name, self._sp_files)
        run_number = self._runnum_var.get().strip()
        if run_number:
            run.run_number = run_number

        run_id = self._db.save_run(run, measurements)
        if run_id < 0:
            self._err_var.set("This run is already in the database.")
            return

        for r in self._analysis_results:
            self._db.set_scales(run_id, r["radius"], r["scale1"], r.get("scale2"))

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
