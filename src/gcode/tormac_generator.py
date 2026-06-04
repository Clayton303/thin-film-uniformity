"""
Generate Tormac G-code (.tap) from a V1/V6 radial mask profile.

Coordinate transform (from Mask_Magic_All_Rev4.bas):
  x = sqrt(rad^2 - (width/2)^2) + x_offset   (x_offset = 8.215 for V1 and V6)
  y = +/- width/2

The mask opening at each radius is a chord of that circle. x is the
perpendicular distance from the rotation axis to the chord, plus the
machine-coordinate offset.

Toolpath: cubic spline through lower half (outer->inner, y<0) then upper
half (inner->outer, y>0), with fixed G-code header/footer for two-pass cut
at Z=+0.01 and Z=-0.01.
"""

import math
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline

# Machine constants (hardcoded in Mask_Magic_All_Rev4.bas OutputGcode)
_ENTRY_X  = 17.5791
_ENTRY_Y  = -0.7324
_COMP_X   = 17.5747   # cutter-comp engagement point
_COMP_Y   = -0.6073
_EXIT_X   = 17.5791
_EXIT_Y   =  0.7327
_OVERSHOOT = 0.25     # approach/exit extension past mask edge

_STEPS_PER_SEG = 10   # spline samples per segment between mask data points

# --- G-code text blocks (trailing spaces preserved from BASIC source) ---

_HEADER = (
    "%\r\n"
    "(APR-20-2022-10:54:03AM) \r\n"
    "(T6 -1/4 X 5/8 3FL PN:13825003) \r\n"
    "G00 G17 G20 G64 G40 G80 G90 G94 \r\n"
    "T6 M6 G43 H6 \r\n"
    '("F-PROFILE-3")\r\n'
    "S5000 M03 \r\n"
    f"G00 G90 G54 X{_ENTRY_X:.4f} Y{_ENTRY_Y:.4f} \r\n"
    "M08 \r\n"
    "Z2.59 \r\n"
    "Z0.225 \r\n"
    "G01 Z0.01 F2. \r\n"
    f"G41 D6 X{_COMP_X:.4f} Y{_COMP_Y:.4f} F15.\r\n"
)

_PASS_SEP = (
    f"G40 X{_EXIT_X:.4f} Y{_EXIT_Y:.4f} \r\n"
    "G00 Z2. \r\n"
    f"Y{_ENTRY_Y:.4f} \r\n"
    "Z0.225 \r\n"
    "G01 Z-0.01 F2. \r\n"
    f"G41 D6 X{_COMP_X:.4f} Y{_COMP_Y:.4f} F15. \r\n"
)

_FOOTER = (
    f"G40 X{_EXIT_X:.4f} Y{_EXIT_Y:.4f} \r\n"
    "G00 Z2.59 \r\n"
    "M09 \r\n"
    "M05 \r\n"
    "G91 G28 Z0 \r\n"
    "G28 X0 Y0 \r\n"
    "G90 \r\n"
    "A0 \r\n"
    "M30 \r\n"
    "%\r\n"
)


def profile_to_toolpath(
    radii: list[float],
    widths: list[float],
    x_offset: float = 8.215,
) -> tuple[list[float], list[float]]:
    """Convert radial mask profile to XY toolpath coordinate arrays.

    radii and widths must be ordered inner -> outer (ascending radius).

    Returns (toolpath_x, toolpath_y) matching the point sequence written
    by Mask_Magic_All_Rev4.bas:
      [0]        entry point (_COMP_X, _COMP_Y)
      [1]        approach overshoot (outer edge x + 0.25, outer edge y)
      [2..N+2]   lower-half spline (outer -> inner, y < 0)
      [N+3..M]   upper-half spline (skip innermost, inner+1 -> outer, y > 0)
      [M+1]      exit overshoot (last x + 0.25, last y)
      [M+2]      return (_COMP_X, -_COMP_Y)
    """
    # Chord geometry: x = sqrt(r^2 - (w/2)^2) + offset
    pts_x  = [math.sqrt(r**2 - (w / 2)**2) + x_offset for r, w in zip(radii, widths)]
    half_w = [w / 2 for w in widths]

    # Lower half: y = -half_w, sample outer -> inner (decreasing x)
    # Fit spline with pts_x ascending, then sample in reverse.
    lower_sx, lower_sy = _spline_sample(pts_x, [-h for h in half_w], reverse=True)

    # Upper half: y = +half_w, sample inner -> outer (increasing x)
    upper_sx, upper_sy = _spline_sample(pts_x, half_w, reverse=False)

    # Assemble (mirrors BASIC indexing exactly)
    tx = [_COMP_X, lower_sx[0] + _OVERSHOOT] + lower_sx + upper_sx[1:]
    ty = [_COMP_Y, lower_sy[0]]               + lower_sy + upper_sy[1:]
    tx += [tx[-1] + _OVERSHOOT, _COMP_X]
    ty += [ty[-1],               -_COMP_Y]

    return tx, ty


def write_tap(
    toolpath_x: list[float],
    toolpath_y: list[float],
    output_path: str | Path,
) -> None:
    """Write a Tormac two-pass .tap G-code file from toolpath arrays."""
    xy_lines = "".join(
        f"X{x:.4f} Y{y:.4f}\r\n" for x, y in zip(toolpath_x, toolpath_y)
    )
    content = _HEADER + xy_lines + _PASS_SEP + xy_lines + _FOOTER
    Path(output_path).write_bytes(content.encode("ascii"))


# ---------------------------------------------------------------------------

def _spline_sample(
    xs: list[float],
    ys: list[float],
    reverse: bool,
    steps: int = _STEPS_PER_SEG,
) -> tuple[list[float], list[float]]:
    """Fit a CubicSpline through (xs, ys) with xs ascending, then sample.

    If reverse=True, returns samples from xs[-1] down to xs[0] (outer->inner).
    If reverse=False, returns samples from xs[0] up to xs[-1] (inner->outer).
    """
    cs = CubicSpline(xs, ys)
    out_x: list[float] = []
    out_y: list[float] = []

    if reverse:
        for i in range(len(xs) - 1, 0, -1):
            t = np.linspace(xs[i], xs[i - 1], steps, endpoint=False)
            out_x.extend(t.tolist())
            out_y.extend(cs(t).tolist())
        out_x.append(xs[0])
        out_y.append(float(cs(xs[0])))
    else:
        for i in range(len(xs) - 1):
            t = np.linspace(xs[i], xs[i + 1], steps, endpoint=False)
            out_x.extend(t.tolist())
            out_y.extend(cs(t).tolist())
        out_x.append(xs[-1])
        out_y.append(float(cs(xs[-1])))

    return out_x, out_y
