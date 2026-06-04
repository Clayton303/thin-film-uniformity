"""
Thin Film Uniformity Calculator
Usage:
  python main.py --design <path>.dds --target <path>.SP [--output <path>.dds] [--iterations 5000]

Workflow:
  1. Parse .SP file to extract wavelength/%T transmission target
  2. Load .dds design, replace optimization targets with .SP data
  3. Open modified design in MacLeod, run Simplex optimization
  4. Print and save optimized layer thicknesses
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from utils.sp_parser import parse_sp
from macleod.dds_editor import load_design, get_layers, replace_targets, save_design, print_layers
from macleod.com_interface import connect, open_design, run_simplex, get_layer_thicknesses, save_design as com_save


def main():
    parser = argparse.ArgumentParser(description="MacLeod thin film uniformity optimizer")
    parser.add_argument("--design",     required=True, help="Path to .dds MacLeod design file")
    parser.add_argument("--target",     required=True, help="Path to .SP spectrophotometer file")
    parser.add_argument("--output",     default=None,  help="Output .dds path (default: <design>_optimized.dds)")
    parser.add_argument("--iterations", type=int, default=5000, help="Simplex max iterations (default: 5000)")
    parser.add_argument("--no-com",     action="store_true", help="Skip MacLeod COM — only update targets in XML and exit")
    args = parser.parse_args()

    design_path = Path(args.design)
    target_path = Path(args.target)
    output_path = Path(args.output) if args.output else design_path.with_stem(design_path.stem + "_optimized")

    # --- Step 1: Parse .SP target file ---
    print(f"\n[1] Reading target: {target_path.name}")
    sp = parse_sp(target_path)
    print(f"    Chamber: {sp.chamber}  |  Design: {sp.design_name}  |  F={sp.f_factor}  |  Run={sp.run}")
    print(f"    Wavelength range: {min(sp.wavelengths):.0f}–{max(sp.wavelengths):.0f} nm  |  {len(sp.wavelengths)} points")

    # --- Step 2: Load design and show nominal thicknesses ---
    print(f"\n[2] Loading design: {design_path.name}")
    tree = load_design(design_path)
    nominal_layers = get_layers(tree)
    print("\n    Nominal layer stack:")
    print_layers(nominal_layers)

    # --- Step 3: Replace targets with .SP data ---
    print(f"\n[3] Replacing optimization targets ({len(sp.wavelengths)} points from .SP data)")
    replace_targets(tree, sp.wavelengths, sp.transmittances)

    modified_path = design_path.with_stem(design_path.stem + "_with_sp_targets")
    save_design(tree, modified_path)
    print(f"    Modified design saved: {modified_path.name}")

    if args.no_com:
        print("\n--no-com flag set. Stopping after XML edit.")
        return

    # --- Step 4: MacLeod COM — open, optimize, extract ---
    print("\n[4] Connecting to MacLeod...")
    app = connect(launch=True)

    print("\n[5] Opening modified design in MacLeod...")
    design = open_design(app, modified_path)

    print("\n[6] Running Simplex optimization...")
    merit = run_simplex(app, design, max_iterations=args.iterations)

    print("\n[7] Extracting optimized layer thicknesses...")
    opt_layers = get_layer_thicknesses(design)

    print("\n    Optimized layer stack:")
    print(f"{'Layer':<8} {'Material':<30} {'Nominal (nm)':<16} {'Optimized (nm)':<16} {'Delta (nm)':<12}")
    print("-" * 84)
    for nom, opt in zip(nominal_layers, opt_layers):
        delta = opt["thickness_nm"] - nom.thickness_nm
        print(f"{opt['number']:<8} {opt['material']:<30} {nom.thickness_nm:<16.4f} {opt['thickness_nm']:<16.4f} {delta:+.4f}")

    # --- Step 5: Save result ---
    com_save(design, output_path)
    print(f"\n[8] Optimized design saved: {output_path}")
    print(f"    Final merit function: {merit:.6f}")


if __name__ == "__main__":
    main()
