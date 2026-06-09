"""
Index PE scan PDFs in the scan cloud directory by run number.

File naming convention:  {run} AB[...].pdf
  e.g.  V1-1049 AB.pdf
        V1-1049 AB HR.pdf
        V7-93 AB HR.pdf
        V7-93 AB AR.pdf

The first token of every filename is matched against a V#-### pattern.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Callable, Optional

SCAN_ROOT = Path(
    r"C:\Users\User\FiveNine Dropbox\FiveNine Optics Team Folder"
    r"\Coating Run Data\Scans\PE scans cloud"
)

_RUN_RE = re.compile(r'^([Vv]\d+-\d+)', re.IGNORECASE)


def _sort_key(path: Path) -> tuple[int, str]:
    """Rank HR files first, then plain AB, then everything else."""
    name = path.name.upper()
    if "AB HR" in name:
        return (0, name)
    if re.search(r'AB\.PDF$', name, re.IGNORECASE):
        return (1, name)
    if "AB" in name:
        return (2, name)
    return (3, name)


class ScanIndex:
    """Maps normalised run numbers to lists of matching AB scan PDFs."""

    def __init__(self) -> None:
        self._index: dict[str, list[Path]] = defaultdict(list)
        self.ready = False

    def build(
        self,
        root: Path = SCAN_ROOT,
        progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Walk the scan directory and populate the index."""
        idx: dict[str, list[Path]] = defaultdict(list)
        count = 0
        for p in root.rglob("*.pdf"):
            if "AB" not in p.name:
                continue
            m = _RUN_RE.match(p.name)
            if not m:
                continue
            key = m.group(1).upper()
            idx[key].append(p)
            count += 1

        # Sort each entry so HR files come first
        for key in idx:
            idx[key].sort(key=_sort_key)

        self._index = idx
        self.ready  = True
        if progress:
            progress(f"Scan index ready — {count} AB files across {len(idx)} runs.")

    def lookup(self, run: str) -> list[Path]:
        """Return sorted list of AB scan PDFs for *run* (e.g. 'V1-1049')."""
        if not run:
            return []
        key = run.strip().upper()
        return list(self._index.get(key, []))
