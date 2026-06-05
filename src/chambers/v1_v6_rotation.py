"""
V1 / V6 single-rotation mask correction.

Workflow:
  1. Load the current mask profile (.msk) and sensitivity calibration (.cut).
  2. Accept per-radius thickness corrections (% overage at each radius).
  3. Compute new mask widths:  delta_width = correction_pct * inchperpercent
     (inchperpercent < 0: positive overage -> negative delta -> narrower mask
      -> less material -> thinner coat, correcting the overage)
  4. Write the new .msk file (next revision number).
  5. Generate the corresponding .tap G-code via tormac_generator.

Sign convention for corrections
  correction_pct > 0  coating was too THICK  (mask will be narrowed)
  correction_pct < 0  coating was too THIN   (mask will be widened)
  F-factor form:  correction_pct = (F - 1.0) * 100
"""

import re
from pathlib import Path

import numpy as np
import yaml

from gcode.tormac_generator import profile_to_toolpath, write_tap, write_npl

_DEFAULT_CONFIG = Path(__file__).parent.parent.parent / "config" / "chambers.yaml"


# ---------------------------------------------------------------------------
# Low-level file I/O
# ---------------------------------------------------------------------------

def load_msk(path: str | Path) -> tuple[list[float], list[float]]:
    """Parse a .msk file. Returns (radii, widths) in inches."""
    lines = Path(path).read_text().splitlines()
    radii, widths = [], []
    for line in lines[1:]:   # skip header row
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) == 2:
            radii.append(float(parts[0]))
            widths.append(float(parts[1]))
    return radii, widths


def save_msk(radii: list[float], widths: list[float], path: str | Path) -> None:
    """Write a .msk file."""
    lines = ["rad,width"]
    for r, w in zip(radii, widths):
        lines.append(f"{r},{w:.13f}")
    Path(path).write_text("\n".join(lines) + "\n")


def load_cut(path: str | Path) -> tuple[list[float], list[float]]:
    """Parse a .cut sensitivity file. Returns (positions, inchperpercent)."""
    lines = Path(path).read_text().splitlines()
    positions, sensitivities = [], []
    for line in lines[1:]:   # skip header
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2:
            positions.append(float(parts[0]))
            sensitivities.append(float(parts[1]))
    return positions, sensitivities


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def find_latest_msk(mask_dir: str | Path, chamber: str, material: str) -> Path:
    """Return the highest-revision .msk file for chamber/material in mask_dir.

    Excludes calibration variants (e.g. Rev8cal) and bad-run variants
    (e.g. Rev7 bad).  Only files whose stem ends in Rev<digits> are matched.
    """
    mask_dir = Path(mask_dir)
    candidates: list[tuple[int, Path]] = []
    for p in mask_dir.glob("*.msk"):
        name = p.stem
        if material not in name or chamber not in name:
            continue
        m = re.search(r"[Rr]ev(\d+)$", name)
        if m:
            candidates.append((int(m.group(1)), p))
    if not candidates:
        raise FileNotFoundError(
            f"No .msk files found for {chamber} {material} in {mask_dir}"
        )
    return max(candidates, key=lambda t: t[0])[1]


def find_latest_cut(mask_dir: str | Path, material: str) -> Path:
    """Return the most-recent .cut sensitivity file for material in mask_dir.

    Files are named Sensitivity_{material}_Single_{MMDDYYYY}.cut; the date
    string is compared lexicographically after converting to YYYYMMDD so
    that the numeric maximum is the most recent.
    """
    mask_dir = Path(mask_dir)
    candidates: list[tuple[str, Path]] = []
    for p in mask_dir.glob(f"Sensitivity_{material}_Single_*.cut"):
        m = re.search(r"(\d{2})(\d{2})(\d{4})\.cut$", p.name)
        if m:
            yyyymmdd = m.group(3) + m.group(1) + m.group(2)   # YYYYMMDD for sort
            candidates.append((yyyymmdd, p))
    if not candidates:
        raise FileNotFoundError(
            f"No .cut sensitivity file found for {material} in {mask_dir}"
        )
    return max(candidates, key=lambda t: t[0])[1]


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config(config_path: str | Path | None = None) -> dict:
    p = Path(config_path) if config_path else _DEFAULT_CONFIG
    with open(p) as f:
        return yaml.safe_load(f)


def _chamber_cfg(chamber: str, config_path=None) -> dict:
    cfg = _load_config(config_path)
    if chamber not in cfg.get("chambers", {}):
        raise KeyError(f"Chamber '{chamber}' not found in config")
    return cfg["chambers"][chamber]


# ---------------------------------------------------------------------------
# Core correction logic
# ---------------------------------------------------------------------------

def _next_revision(msk_path: Path) -> int:
    m = re.search(r"[Rr]ev(\d+)$", msk_path.stem)
    return (int(m.group(1)) + 1) if m else 1


def apply_correction(
    chamber: str,
    material: str,
    corrections: dict[float, float],
    output_dir: str | Path | None = None,
    config_path: str | Path | None = None,
) -> tuple[Path, Path]:
    """Compute and write a corrected mask revision.

    Parameters
    ----------
    chamber     : "V1" or "V6"
    material    : "SiO2" or "Ta2O5"
    corrections : mapping of {radius_inch: correction_pct}
                  correction_pct > 0  = coating was too thick at that radius
                  Sparse: corrections are linearly interpolated to mask radii.
                  Pass a single-key dict for uniform correction across all radii.
    output_dir  : where to write the new .msk / .tap (default: same as source)
    config_path : override for config/chambers.yaml

    Returns
    -------
    (new_msk_path, new_tap_path)
    """
    ch_cfg   = _chamber_cfg(chamber, config_path)
    x_offset = ch_cfg["x_offset"]
    mask_dir = Path(ch_cfg["materials"][material]["mask_dir"])

    msk_path = find_latest_msk(mask_dir, chamber, material)
    cut_path = find_latest_cut(mask_dir, material)

    radii, widths = load_msk(msk_path)
    cut_pos, cut_sens = load_cut(cut_path)

    # Interpolate corrections and sensitivity to the mask radii
    if len(corrections) == 1:
        # Single value: apply uniformly
        pct = next(iter(corrections.values()))
        interp_pct = [pct] * len(radii)
    else:
        corr_r = sorted(corrections)
        corr_v = [corrections[r] for r in corr_r]
        interp_pct = np.interp(radii, corr_r, corr_v).tolist()

    interp_sens = np.interp(radii, cut_pos, cut_sens).tolist()

    # delta_width = correction_pct * inchperpercent
    # inchperpercent < 0: positive correction (too thick) -> narrower mask
    new_widths = [w + p * s for w, p, s in zip(widths, interp_pct, interp_sens)]

    # Build output paths (increment revision in stem)
    next_rev  = _next_revision(msk_path)
    new_stem  = re.sub(r"[Rr]ev\d+$", f"Rev{next_rev}", msk_path.stem)
    out_dir   = Path(output_dir) if output_dir else msk_path.parent
    new_msk   = out_dir / f"{new_stem}.msk"
    new_tap   = out_dir / f"{new_stem}.tap"

    save_msk(radii, new_widths, new_msk)

    tx, ty = profile_to_toolpath(radii, new_widths, x_offset)
    write_tap(tx, ty, new_tap)
    write_npl(radii, new_widths, tx, ty, new_tap.with_suffix(".npl"), x_offset,
              title=f"Cut Line, Tool: {new_stem}")

    return new_msk, new_tap


def apply_f_correction(
    chamber: str,
    material: str,
    f_factor: float,
    output_dir: str | Path | None = None,
    config_path: str | Path | None = None,
) -> tuple[Path, Path]:
    """Convenience wrapper: uniform F-factor correction across all radii.

    f_factor = actual_deposition / target_deposition
      > 1.0  coating was too thick (mask will be narrowed)
      < 1.0  coating was too thin  (mask will be widened)
    """
    pct = (f_factor - 1.0) * 100.0
    # Use a single-entry dict so apply_correction broadcasts uniformly
    ch_cfg   = _chamber_cfg(chamber, config_path)
    mask_dir = Path(ch_cfg["materials"][material]["mask_dir"])
    radii, _ = load_msk(find_latest_msk(mask_dir, chamber, material))
    corrections = {radii[0]: pct}   # single value -> uniform broadcast
    return apply_correction(chamber, material, corrections, output_dir, config_path)


# ---------------------------------------------------------------------------
# Standalone: regenerate .tap from an existing .msk without changing widths
# ---------------------------------------------------------------------------

def regenerate_tap(
    msk_path: str | Path,
    x_offset: float = 8.215,
    tap_path: str | Path | None = None,
) -> Path:
    """Re-generate a .tap file from an existing .msk without modifying widths."""
    msk_path = Path(msk_path)
    out_path = Path(tap_path) if tap_path else msk_path.with_suffix(".tap")
    radii, widths = load_msk(msk_path)
    tx, ty = profile_to_toolpath(radii, widths, x_offset)
    write_tap(tx, ty, out_path)
    write_npl(radii, widths, tx, ty, out_path.with_suffix(".npl"), x_offset,
              title=f"Cut Line, Tool: {msk_path.stem}")
    return out_path
