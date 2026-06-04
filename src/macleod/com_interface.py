"""
MacLeod COM automation via EMacleod.EssentialMacleod ProgID.

Workflow:
  1. Connect to running MacLeod instance (or launch it)
  2. Open a .dds design file
  3. Run Simplex optimization
  4. Return optimized layer thicknesses
"""

import os
import time
from pathlib import Path

try:
    import win32com.client
    _COM_AVAILABLE = True
except ImportError:
    _COM_AVAILABLE = False


PROGID = "EMacleod.EssentialMacleod"


def connect(launch: bool = True):
    """Return a live MacLeod COM application object."""
    if not _COM_AVAILABLE:
        raise RuntimeError("pywin32 not installed. Run: pip install pywin32")

    try:
        app = win32com.client.GetActiveObject(PROGID)
        print("Connected to running MacLeod instance.")
        return app
    except Exception:
        if not launch:
            raise RuntimeError("MacLeod is not running. Start it manually or pass launch=True.")
        print("MacLeod not running — launching...")
        app = win32com.client.Dispatch(PROGID)
        time.sleep(3)
        return app


def open_design(app, path: str | Path):
    """Open a .dds file and return the design object."""
    abs_path = str(Path(path).resolve())
    design = app.Designs.Open(abs_path)
    if design is None:
        raise RuntimeError(f"MacLeod failed to open design: {abs_path}")
    print(f"Opened design: {Path(abs_path).name}")
    return design


def run_simplex(app, design, max_iterations: int = 5000) -> float:
    """Run Simplex refinement. Returns final merit function value."""
    refine = app.Refine
    refine.Design = design

    # Set Simplex as the active method and configure iterations
    params = design.RefinementParameters
    params.SimplexNumberOfIterations = max_iterations

    print(f"Running Simplex (max {max_iterations} iterations)...")
    refine.Simplex()

    merit = refine.MeritFunction
    print(f"Simplex complete. Merit function: {merit:.6f}")
    return merit


def get_layer_thicknesses(design) -> list[dict]:
    """Return list of {number, material, thickness_nm} for all non-substrate layers."""
    layers = []
    for i in range(1, design.Layers.Count + 1):
        layer = design.Layers.Item(i)
        thick = layer.Thickness
        if thick > 0:
            layers.append({
                "number": i,
                "material": layer.Material,
                "thickness_nm": thick,
            })
    return layers


def save_design(design, path: str | Path) -> None:
    design.SaveAs(str(Path(path).resolve()))
    print(f"Design saved to: {path}")


def list_com_methods(obj) -> None:
    """Debug helper: print all methods and properties on a COM object."""
    try:
        for name in dir(obj):
            if not name.startswith("_"):
                print(name)
    except Exception as e:
        print(f"Could not enumerate COM object: {e}")
