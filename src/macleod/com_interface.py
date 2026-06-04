"""
MacLeod COM automation via EMacleod.Session ProgID.

Correct calling conventions (from generated type library):
  GetPhysicalThickness(layer, 0.0)  -> (retcode, thickness_nm)
  GetMaterialName(layer, "")        -> (retcode, material_str)
  SetPhysicalThickness(layer, nm)   -> void
  CalculateMeritFigure()            -> (retcode, merit_float)
  Targets.GetRequiredValue(i, 0.0)  -> (retcode, value)
  Targets.GetWavelength(i, 0.0)     -> (retcode, wavelength)
  Targets.AddTarget()               -> index
  Targets.DeleteTarget(i)           -> void
  Targets.NumberOfSpecifications    -> int (property)

Optimization: scipy Nelder-Mead calling MacLeod's CalculateMeritFigure as objective.
"""

import time
from pathlib import Path

try:
    import win32com.client
    _COM_AVAILABLE = True
except ImportError:
    _COM_AVAILABLE = False

try:
    from scipy.optimize import minimize
    import numpy as np
    _SCIPY_AVAILABLE = True
except ImportError:
    _SCIPY_AVAILABLE = False

SESSION_PROGID = "EMacleod.Session"
_TARGET_TYPE_T = 84   # %T transmittance
_POLARIZATION_BOTH = 80


def connect():
    if not _COM_AVAILABLE:
        raise RuntimeError("pywin32 not installed. Run: pip install pywin32")
    session = win32com.client.Dispatch(SESSION_PROGID)
    print("Connected to MacLeod COM (EMacleod.Session).")
    return session


def open_design(session, path: str | Path):
    """Open a .dds file. Returns the design COM object."""
    abs_path = str(Path(path).resolve())
    result = session.OpenDesign(abs_path)
    # OpenDesign returns (design, is_new_flag)
    design = result[0] if isinstance(result, tuple) else result
    if design is None:
        raise RuntimeError(f"MacLeod failed to open: {abs_path}")
    print(f"Opened: {Path(abs_path).name}")
    return design


def get_layers(design) -> list[dict]:
    """Return [{number, material, thickness_nm}] for all non-substrate layers."""
    layers = []
    n = design.NumberOfLayers
    for i in range(1, n + 1):
        mat = design.GetMaterialName(i, "")
        thick = design.GetPhysicalThickness(i, 0.0)
        if thick > 0:
            layers.append({"number": i, "material": mat, "thickness_nm": thick})
    return layers


def set_targets_from_sp(design, wavelengths: list[float], transmittances: list[float],
                         tolerance: float = 1.0, weight: float = 1.0) -> None:
    """Clear existing targets and load new %T targets from SP data via COM."""
    targets = design.Targets
    n_existing = targets.NumberOfSpecifications

    # Delete all existing targets — delete from end (1-based index, count down to 1)
    for i in range(n_existing, 0, -1):
        targets.DeleteTarget(i)

    # Add new targets — AddTarget(TargetType) appends; new index = NumberOfSpecifications after add
    for wl, t in zip(wavelengths, transmittances):
        targets.AddTarget(_TARGET_TYPE_T)
        idx = targets.NumberOfSpecifications   # 1-based: last added target
        targets.SetPolarization(idx, _POLARIZATION_BOTH)
        targets.SetWavelength(idx, wl)
        targets.SetRequiredValue(idx, t)
        targets.SetTolerance(idx, tolerance)
        targets.SetWeight(idx, weight)
        targets.SetContext(idx, "Normal")
        targets.SetIncidentAngle(idx, 0.0)
        targets.SetLinkNumber(idx, 0)
        targets.SetOperator(idx, 0)

    print(f"Set {len(wavelengths)} targets via COM ({min(wavelengths):.0f}-{max(wavelengths):.0f} nm).")


def get_merit(design) -> float:
    """Return current merit function value from MacLeod."""
    return float(design.CalculateMeritFigure())


def run_simplex(design, max_iterations: int = 5000, tolerance: float = 1e-6) -> dict:
    """
    Drive Nelder-Mead (Simplex) optimization in Python using MacLeod's
    CalculateMeritFigure as the objective function.

    Only optimizes non-substrate layers (thickness > 0).
    Returns dict with optimized layer thicknesses and final merit.
    """
    if not _SCIPY_AVAILABLE:
        raise RuntimeError("scipy not installed. Run: pip install scipy numpy")

    layers = get_layers(design)
    layer_numbers = [l["number"] for l in layers]
    x0 = np.array([l["thickness_nm"] for l in layers])

    call_count = [0]

    def objective(x):
        call_count[0] += 1
        for num, thick in zip(layer_numbers, x):
            design.SetPhysicalThickness(num, float(thick))
        merit = get_merit(design)
        if call_count[0] % 100 == 0:
            print(f"  Iteration {call_count[0]:>5}  merit={merit:.6f}")
        return merit

    print(f"Starting Simplex on {len(layers)} layers (max {max_iterations} iterations)...")
    initial_merit = objective(x0)
    print(f"  Initial merit: {initial_merit:.6f}")

    result = minimize(
        objective,
        x0,
        method="Nelder-Mead",
        options={
            "maxiter": max_iterations,
            "xatol": tolerance,
            "fatol": tolerance,
            "disp": False,
        },
    )

    print(f"  Simplex finished after {call_count[0]} evaluations. Final merit: {result.fun:.6f}")

    # Write final thicknesses back to design
    for num, thick in zip(layer_numbers, result.x):
        design.SetPhysicalThickness(num, float(thick))

    return {
        "layers": [
            {"number": num, "material": l["material"], "thickness_nm": float(t)}
            for num, l, t in zip(layer_numbers, layers, result.x)
        ],
        "merit": result.fun,
        "iterations": call_count[0],
        "converged": result.success,
    }


def save_design(design, path: str | Path) -> None:
    design.SaveAs(str(Path(path).resolve()))
    print(f"Saved: {path}")


def print_comparison(nominal: list[dict], optimized: list[dict]) -> None:
    print(f"\n{'Layer':<8} {'Material':<30} {'Nominal (nm)':<16} {'Optimized (nm)':<16} {'Delta (nm)':<12}")
    print("-" * 84)
    for nom, opt in zip(nominal, optimized):
        delta = opt["thickness_nm"] - nom["thickness_nm"]
        print(f"{opt['number']:<8} {opt['material']:<30} {nom['thickness_nm']:<16.4f} {opt['thickness_nm']:<16.4f} {delta:+.4f}")
