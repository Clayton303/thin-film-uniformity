"""
Resolve a uniformity run's SP design name to a reference MacLeod .dds file
in the Chamber Uniformity folder tree.

Folder layout
─────────────
  Chamber Uniformity/
    {chamber}/
      Single material/   ← single-layer uniformity designs
      Multi material/    ← combination layer-stack designs

Matching strategy
─────────────────
Score each candidate filename by the number of word-tokens it shares with
the SP design name.  Return the best-scoring candidate.  This is
intentionally loose so temporary / placeholder designs still resolve while
the exact designs are being finalised.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

_UNIFORMITY_DESIGNS = (
    Path(r"C:\Users\User\FiveNine Dropbox\FiveNine Optics Team Folder"
         r"\Coating Designs\Chamber Uniformity")
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _score(design_name: str, candidate: Path) -> tuple[int, int]:
    """Primary: token overlap (higher = better).
    Tiebreaker: fewer extra tokens in filename (more specific match preferred)."""
    d_tok = _tokens(design_name)
    c_tok = _tokens(candidate.stem)
    overlap = len(d_tok & c_tok)
    extra   = len(c_tok - d_tok)   # tokens in filename not in design query
    return (overlap, -extra)       # max() picks higher overlap, then fewer extras


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_design_file(
    chamber: str,
    design_name: str,
    multi_material: bool,
    base_dir: Path | None = None,
) -> Optional[Path]:
    """Return the best-matching .dds file for this run, or None.

    Parameters
    ----------
    chamber:        e.g. "V2"
    design_name:    SP header design string, e.g. "HW 16L" or "Ta2O5 300nm"
    multi_material: True → search Multi material/, False → Single material/
    base_dir:       override for the Chamber Uniformity root (testing)
    """
    root    = Path(base_dir) if base_dir else _UNIFORMITY_DESIGNS
    subdir  = "Multi material" if multi_material else "Single material"
    folder  = root / chamber / subdir

    if not folder.exists():
        return None

    candidates = sorted(folder.glob("*.dds"))
    if not candidates:
        return None

    scored = [((_score(design_name, p), p)) for p in candidates]
    best_score, best = max(scored, key=lambda t: t[0])

    # Return best match even if score is zero (placeholder designs)
    return best


def is_multi_material(design_name: str, combo_keywords: list[list[str]]) -> bool:
    """True if design_name matches any of the multi-material keyword sets."""
    tokens = _tokens(design_name)
    return any(
        all(kw.lower() in tokens for kw in kws)
        for kws in combo_keywords
    )


def identify_primary_material(design_name: str, layer_materials: list[str]) -> Optional[str]:
    """Identify the primary material to track uniformity for.

    Looks for the thickest or first non-SiO2, non-substrate material in the
    design, falling back to the first material keyword found in design_name.
    Returns a keyword string suitable for use with 'keyword in material_name'.
    """
    # Map common aliases to canonical tokens
    ALIASES = {
        "hf":     "HfO2",
        "hfo2":   "HfO2",
        "ta2o5":  "Ta2O5",
        "ta":     "Ta2O5",
        "sio2":   "SiO2",
        "nb2o5":  "Nb2O5",
        "nb":     "Nb2O5",
        "al2o3":  "Al2O3",
        "al":     "Al2O3",
        "tio2":   "TiO2",
        "ti":     "TiO2",
        "si":     "Si",
    }
    # Check design name tokens first
    for tok in _tokens(design_name):
        canon = ALIASES.get(tok)
        if canon and any(canon.lower() in m.lower() for m in layer_materials):
            return canon

    # Fall back to first non-SiO2 material in the layer list
    for m in layer_materials:
        if "SiO2" not in m and "Substrate" not in m and "Air" not in m:
            return m.split()[0]   # first word of material name

    return None


def list_available(chamber: str, base_dir: Path | None = None) -> dict[str, list[str]]:
    """Return {'Single material': [...filenames...], 'Multi material': [...]}."""
    root = Path(base_dir) if base_dir else _UNIFORMITY_DESIGNS
    result: dict[str, list[str]] = {}
    for sub in ("Single material", "Multi material"):
        folder = root / chamber / sub
        result[sub] = (
            [p.name for p in sorted(folder.glob("*.dds"))]
            if folder.exists() else []
        )
    return result
