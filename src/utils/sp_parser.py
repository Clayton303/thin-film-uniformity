"""
Parse PerkinElmer UV WinLab ASCII .SP files.

Header line 9 (index 8) encodes the run metadata.  Two formats are seen:

  Full (anchor file):   "[chamber]-Unif [design] [date], F=[factor], R=[radius]"
  Short (batch files):  "R=[radius]"

In both cases R= is the witness piece radial position in inches.
The short format appears in batches where the operator only labeled the
first file with the full description.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SPFile:
    path: Path
    chamber: Optional[str]       # e.g. "V6"
    design_name: Optional[str]   # e.g. "HW 16L" or "Hf layer 350nm"
    date: Optional[str]          # MM/DD/YYYY from anchor header
    f_factor: Optional[float]    # overall correction factor (anchor file only)
    radius: Optional[float]      # witness position in inches (R= value)
    wavelengths: list = field(default_factory=list)   # nm, descending
    transmittances: list = field(default_factory=list) # %T

    # Legacy alias so existing code using .run still works
    @property
    def run(self) -> Optional[float]:
        return self.radius


def parse_sp(path: str | Path) -> SPFile:
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    result = SPFile(path=path, chamber=None, design_name=None,
                    date=None, f_factor=None, radius=None)

    # Line 9 (index 8) holds run metadata
    if len(lines) > 8:
        _parse_header_line(lines[8].strip(), result)

    # Read data after #DATA marker
    in_data = False
    for line in lines:
        if line.strip() == "#DATA":
            in_data = True
            continue
        if in_data and line.strip():
            parts = line.strip().split()
            if len(parts) == 2:
                try:
                    result.wavelengths.append(float(parts[0]))
                    result.transmittances.append(float(parts[1]))
                except ValueError:
                    pass

    return result


def _parse_header_line(line: str, result: SPFile) -> None:
    # Full anchor — handles both formats:
    #   "V6-Unif HW 16L 06/03/2026, F=1.044, R=2"   (original)
    #   "V1 Uniformity HW 16L 5-12-26 F=1.044, R=2"  (space + full word, no comma before F)
    m = re.match(
        r"^([A-Za-z0-9]+)[-\s]+Unif(?:ormity)?\s+(.+?)\s+(\S+)[,\s]+F=([\d.]+)[,\s]+R=([\d.]+)",
        line,
        re.IGNORECASE,
    )
    if m:
        result.chamber     = m.group(1).strip()
        result.design_name = m.group(2).strip()
        result.date        = m.group(3).strip().rstrip(",")
        result.f_factor    = float(m.group(4))
        result.radius      = float(m.group(5))
        return

    # Short batch-file header: "R=3"
    m = re.match(r"^R=([\d.]+)\s*$", line)
    if m:
        result.radius = float(m.group(1))
