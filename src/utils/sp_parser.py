"""
Parse PerkinElmer UV WinLab ASCII .SP files.

Header line 9 encodes: "[chamber]-Unif [design] [date], F=[factor], R=[run]"
Data section after #DATA: tab-separated wavelength(nm) and %T pairs, descending wavelength.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SPFile:
    path: Path
    chamber: Optional[str]
    design_name: Optional[str]
    date: Optional[str]
    f_factor: Optional[float]
    run: Optional[int]
    wavelengths: list = field(default_factory=list)   # nm
    transmittances: list = field(default_factory=list) # %T


def parse_sp(path: str | Path) -> SPFile:
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    result = SPFile(path=path, chamber=None, design_name=None,
                    date=None, f_factor=None, run=None)

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
    # "V6-Unif HW 16L 06/03/2026, F=1.044, R=2"
    m = re.match(r"^([^-]+)-Unif\s+(.+?)\s+(\d{2}/\d{2}/\d{4}),\s*F=([\d.]+),\s*R=(\d+)", line)
    if m:
        result.chamber = m.group(1).strip()
        result.design_name = m.group(2).strip()
        result.date = m.group(3).strip()
        result.f_factor = float(m.group(4))
        result.run = int(m.group(5))
