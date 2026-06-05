"""
Per-radius uniformity metrics extracted from SP spectral data.

Primary metric: peak wavelength position, found by fitting a quadratic to
the neighbourhood of the spectral maximum.  This works well for single-layer
interference films (clear bell-shaped peak) and gives a useful relative
indicator for multi-layer designs.

Derived metrics
---------------
peak_shift_profile  Δλ(R) = λ_peak(R) - λ_peak(R_ref)  where R_ref is the
                    smallest measured radius (usually R=0 or R=2).
                    Positive = thicker than reference; negative = thinner.

uniformity_score    peak-to-peak range of Δλ across all radii (nm).
                    Lower is more uniform.  Zero would be perfect.
"""

from __future__ import annotations

import numpy as np


# ---------------------------------------------------------------------------
# Peak finding
# ---------------------------------------------------------------------------

def find_peak(wavelengths: list[float], transmittances: list[float]) -> tuple[float, float]:
    """Return (peak_wavelength_nm, peak_pct_T) using quadratic interpolation.

    Uses the maximum and its two neighbours for sub-point precision.
    Falls back to the raw maximum if the peak is at an edge.
    """
    wl = np.asarray(wavelengths, dtype=float)
    tr = np.asarray(transmittances, dtype=float)

    idx = int(np.argmax(tr))

    if 0 < idx < len(wl) - 1:
        x = wl[idx - 1 : idx + 2]
        y = tr[idx - 1 : idx + 2]
        coeffs = np.polyfit(x, y, 2)
        if coeffs[0] < 0:                          # concave down — real peak
            peak_x = float(-coeffs[1] / (2 * coeffs[0]))
            peak_y = float(np.polyval(coeffs, peak_x))
            # Sanity: interpolated peak must be within the window
            if x[0] <= peak_x <= x[-1]:
                return peak_x, peak_y

    return float(wl[idx]), float(tr[idx])


# ---------------------------------------------------------------------------
# Run-level metrics
# ---------------------------------------------------------------------------

def peak_shift_profile(
    radii: list[float],
    peaks: list[float],
) -> dict[float, float]:
    """Compute Δλ(R) = peak(R) - peak(R_ref) for each radius.

    R_ref is the smallest radius in the supplied data (R=0 if measured).
    Returns {radius: delta_nm}.
    """
    if not radii:
        return {}
    ref_idx = int(np.argmin(radii))
    ref_peak = peaks[ref_idx]
    return {r: float(p - ref_peak) for r, p in zip(radii, peaks)}


def uniformity_score(shifts: dict[float, float]) -> float:
    """Peak-to-peak range of Δλ in nm.  0 = perfectly uniform."""
    if not shifts:
        return 0.0
    vals = list(shifts.values())
    return float(max(vals) - min(vals))
