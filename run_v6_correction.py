"""
One-shot script: optimize 5 V6 uniformity scans and produce corrected masks.
  R=2" -> P1014116  R=3" -> P1014117  R=4" -> P1014118
  R=5" -> P1014119  R=6" -> P1014120
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from utils.sp_parser import parse_sp
from macleod.com_interface import (
    connect, open_design, get_layers, set_targets_from_sp, run_linked_simplex,
)
from chambers.v1_v6_rotation import apply_correction

DESIGN  = (r"C:\Users\User\FiveNine Dropbox\FiveNine Optics Team Folder"
           r"\Coating Designs\Eric\Uniformity\Uniformity HW 16L.dds")
SPECTRO = r"\\59o-spectro\uvwinlab\DATA"

SCANS = [
    (2.0, "P1014116.SP"),
    (3.0, "P1014117.SP"),
    (4.0, "P1014118.SP"),
    (5.0, "P1014119.SP"),
    (6.0, "P1014120.SP"),
]

# ── Connect and open nominal design ──────────────────────────────────────────
session = connect()
design  = open_design(session, DESIGN)
nominal = get_layers(design)

sio2_layers  = [l for l in nominal if "SiO2"  in l["material"]]
ta2o5_layers = [l for l in nominal if "Ta2O5" in l["material"]]
frozen_layer = max(sio2_layers, key=lambda l: l["number"])

print(f"\nNominal: {len(nominal)} layers  |  "
      f"Ta2O5: {len(ta2o5_layers)}  |  "
      f"SiO2: {len(sio2_layers)}  |  "
      f"Frozen: Layer {frozen_layer['number']} ({frozen_layer['thickness_nm']:.2f} nm)\n")

# ── Optimise each scan ───────────────────────────────────────────────────────
corrections_sio2  = {}
corrections_ta2o5 = {}

for radius, sp_file in SCANS:
    print(f"--- R={radius}\"  {sp_file} ---")
    sp = parse_sp(Path(SPECTRO) / sp_file)
    set_targets_from_sp(design, sp.wavelengths, sp.transmittances)
    res = run_linked_simplex(design, nominal)

    corrections_sio2[radius]  = res["sio2_pct_correction"]
    corrections_ta2o5[radius] = res["ta2o5_pct_correction"]

    print(f"  SiO2   scale={res['sio2_scale']:.6f}   correction={res['sio2_pct_correction']:+.3f}%")
    print(f"  Ta2O5  scale={res['ta2o5_scale']:.6f}   correction={res['ta2o5_pct_correction']:+.3f}%")
    print(f"  merit={res['merit']:.6f}   iters={res['iterations']}   converged={res['converged']}\n")

# -- Summary table ---
print("=" * 52)
print(f"  {'Radius':>8}   {'SiO2 corr %':>12}   {'Ta2O5 corr %':>12}")
print("-" * 52)
for r in sorted(corrections_sio2):
    print(f"  {r:>8.1f}\"  {corrections_sio2[r]:>+12.3f}   {corrections_ta2o5[r]:>+12.3f}")
print("=" * 52)

# -- Apply to V6 masks ---
print("\nWriting corrected V6 masks...")
sio2_msk,  sio2_tap  = apply_correction("V6", "SiO2",  corrections_sio2)
ta2o5_msk, ta2o5_tap = apply_correction("V6", "Ta2O5", corrections_ta2o5)

print(f"\n  SiO2  mask : {sio2_msk.name}")
print(f"  SiO2  tap  : {sio2_tap.name}")
print(f"  Ta2O5 mask : {ta2o5_msk.name}")
print(f"  Ta2O5 tap  : {ta2o5_tap.name}")
print(f"\n  Saved to   : {sio2_msk.parent}")
