"""
Analyse uniformity runs against reference MacLeod designs.

For each SP file in a run, this module:
  1. Opens the reference .dds design in MacLeod.
  2. Pushes the measured %T spectrum as optimizer targets.
  3. Runs a linked Simplex with one scale factor per material group
     (generalised from run_linked_simplex to handle any two-material design).
  4. Stores the resulting scale factors in the DB via db.set_scales().

Scale factor interpretation
───────────────────────────
  scale1   primary-material thickness / nominal  (>1 = thicker than design)
  scale2   secondary-material scale              (None for single-material runs)

Thickness deviation in % = (scale - 1) * 100

The primary material is identified by find_primary_material(); the secondary
is everything else except the highest-numbered thin termination layer
(same freeze rule as run_linked_simplex in com_interface.py).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Callable

import numpy as np
from scipy.optimize import minimize

from utils.uniformity_db import UniformityDB, RunSummary
from utils.design_resolver import find_design_file, identify_primary_material, is_multi_material
from utils.sp_parser import parse_sp

_SPECTRO_DIR = Path(r"\\59o-spectro\uvwinlab\DATA")


# ---------------------------------------------------------------------------
# Generalised linked simplex
# ---------------------------------------------------------------------------

def _run_generalised_simplex(
    design,
    nominal_layers: list[dict],
    primary_keyword: str,
    max_iterations: int = 1500,
) -> dict:
    """Two-scale-factor optimisation for any primary/secondary material pair.

    primary_keyword: substring to identify primary-material layers
                     (e.g. "Ta2O5", "HfO2", "Al2O3")

    Returns dict with scale1, scale2, merit, converged.
    Layer with the highest number among the secondary group is frozen
    (same rule as run_linked_simplex for Layer 16).
    """
    primary = [l for l in nominal_layers if primary_keyword in l["material"]]
    other   = [l for l in nominal_layers if primary_keyword not in l["material"]]

    # Freeze the highest-numbered non-primary layer (thin termination)
    if other:
        frozen  = max(other, key=lambda l: l["number"])
        secondary = [l for l in other if l["number"] != frozen["number"]]
    else:
        frozen    = None
        secondary = []

    pri_t0 = [l["thickness_nm"] for l in primary]
    sec_t0 = [l["thickness_nm"] for l in secondary]

    def _apply(s1: float, s2: float) -> None:
        for l, t0 in zip(primary, pri_t0):
            design.SetPhysicalThickness(l["number"], s1 * t0)
        for l, t0 in zip(secondary, sec_t0):
            design.SetPhysicalThickness(l["number"], s2 * t0)
        if frozen:
            design.SetPhysicalThickness(frozen["number"], frozen["thickness_nm"])

    def objective(x: np.ndarray) -> float:
        _apply(x[0], x[1])
        return float(design.CalculateMeritFigure())

    result = minimize(
        objective,
        np.array([1.0, 1.0]),
        method="Nelder-Mead",
        options={"maxiter": max_iterations, "xatol": 1e-7, "fatol": 1e-7, "disp": False},
    )
    _apply(1.0, 1.0)   # restore nominal

    s1, s2 = float(result.x[0]), float(result.x[1])
    return {
        "scale1":    s1,
        "scale2":    s2,
        "merit":     result.fun,
        "converged": result.success,
    }


# ---------------------------------------------------------------------------
# Per-SP-file analysis
# ---------------------------------------------------------------------------

def _sp_path(sp_file: str, spectro_dir: Path = _SPECTRO_DIR) -> Optional[Path]:
    """Resolve an SP filename to a full path (spectro share first, then glob)."""
    p = spectro_dir / sp_file
    if p.exists():
        return p
    return None


# ---------------------------------------------------------------------------
# Public: analyse one run
# ---------------------------------------------------------------------------

def analyze_run(
    summary: RunSummary,
    db: UniformityDB,
    combo_keywords: list[list[str]] | None = None,
    spectro_dir: Path = _SPECTRO_DIR,
    progress: Optional[Callable[[str], None]] = None,
) -> bool:
    """Run MacLeod analysis for a single uniformity run.

    Finds the reference design, opens it in MacLeod, analyses each SP file,
    and stores scale1/scale2 in the DB.  Returns True on success.

    combo_keywords: list of keyword-token lists defining multi-material
                    designs (loaded from config/material_targets.yaml by
                    the caller); used to pick Single vs Multi material folder.
    """
    def _log(msg: str) -> None:
        if progress:
            progress(msg)

    run = summary.run

    try:
        import win32com.client
    except ImportError:
        _log("pywin32 not available — cannot run MacLeod analysis")
        return False

    # Determine folder type
    multi = is_multi_material(run.design or "", combo_keywords or [])
    dds = find_design_file(run.chamber, run.design or "", multi)
    if dds is None:
        _log(f"No reference design found for {run.chamber} / {run.design}")
        return False

    _log(f"Reference design: {dds.name}")

    # Open MacLeod session and design
    try:
        session = win32com.client.Dispatch("EMacleod.Session")
        design_obj = session.OpenDesign(str(dds.resolve()))
        if isinstance(design_obj, tuple):
            design_obj = design_obj[0]
    except Exception as e:
        _log(f"MacLeod error opening design: {e}")
        return False

    # Get nominal layers
    from macleod.com_interface import get_layers, set_targets_from_sp
    nominal = get_layers(design_obj)
    layer_materials = [l["material"] for l in nominal]
    primary_kw = identify_primary_material(run.design or "", layer_materials)
    if not primary_kw:
        _log("Could not identify primary material")
        return False

    _log(f"Primary material: {primary_kw}  ({len(nominal)} layers)")

    # Store reference design path
    if run.id is not None:
        db.set_design_file(run.id, str(dds))

    # Analyse each witness piece
    success_count = 0
    for meas in sorted(summary.measurements, key=lambda m: m.radius):
        sp_path = _sp_path(meas.sp_file, spectro_dir)
        if sp_path is None:
            _log(f"  R={meas.radius}\"  SP file not found: {meas.sp_file}")
            continue

        try:
            sp = parse_sp(sp_path)
            if not sp.wavelengths:
                continue
            set_targets_from_sp(design_obj, sp.wavelengths, sp.transmittances)
            result = _run_generalised_simplex(design_obj, nominal, primary_kw)
            db.set_scales(run.id, meas.radius, result["scale1"], result["scale2"])
            dev1 = (result["scale1"] - 1.0) * 100
            dev2 = (result["scale2"] - 1.0) * 100
            _log(f"  R={meas.radius}\"  {primary_kw}: {dev1:+.2f}%  "
                 f"secondary: {dev2:+.2f}%  merit={result['merit']:.4f}")
            success_count += 1
        except Exception as e:
            _log(f"  R={meas.radius}\"  Error: {e}")

    _log(f"Analysis complete: {success_count}/{len(summary.measurements)} radii")
    return success_count > 0


# ---------------------------------------------------------------------------
# Public: resolve design file for display (no MacLeod needed)
# ---------------------------------------------------------------------------

def resolve_design_label(
    chamber: str,
    design_name: str,
    combo_keywords: list[list[str]] | None = None,
) -> str:
    """Return a short label for the reference design (filename stem or '—')."""
    multi = is_multi_material(design_name, combo_keywords or [])
    dds   = find_design_file(chamber, design_name, multi)
    return dds.stem if dds else "—"
