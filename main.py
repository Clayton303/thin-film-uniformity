"""
Thin Film Uniformity Calculator
Usage:
  python main.py --design <path>.dds --target <path>.SP [--output <path>.dds] [--iterations 5000]

Workflow:
  1. Parse .SP file to extract wavelength/%T transmission target
  2. Load nominal layer thicknesses from .dds via MacLeod COM
  3. Push SP data as new targets via COM
  4. Run Simplex (Nelder-Mead) using MacLeod's merit function
  5. Print and save optimized layer thicknesses
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from utils.sp_parser import parse_sp
from macleod.dds_editor import load_design, get_layers as xml_get_layers, print_layers
from macleod.com_interface import (
    connect, open_design, get_layers, set_targets_from_sp,
    run_simplex, save_design, print_comparison
)


def main():
    parser = argparse.ArgumentParser(description="MacLeod thin film uniformity optimizer")
    parser.add_argument("--design",     required=True, help="Path to .dds MacLeod design file")
    parser.add_argument("--target",     required=True, help="Path to .SP spectrophotometer file")
    parser.add_argument("--output",     default=None,  help="Output .dds path (default: <design>_optimized.dds)")
    parser.add_argument("--iterations", type=int, default=5000, help="Simplex max iterations (default: 5000)")
    parser.add_argument("--no-com",     action="store_true", help="Skip MacLeod COM — only parse files and exit")
    args = parser.parse_args()

    design_path = Path(args.design)
    target_path = Path(args.target)
    output_path = Path(args.output) if args.output else design_path.with_stem(design_path.stem + "_optimized")

    # --- Step 1: Parse .SP target file ---
    print(f"\n[1] Reading target: {target_path.name}")
    sp = parse_sp(target_path)
    print(f"    Chamber: {sp.chamber}  |  Design: {sp.design_name}  |  F={sp.f_factor}  |  Run={sp.run}")
    print(f"    Wavelength range: {min(sp.wavelengths):.0f}-{max(sp.wavelengths):.0f} nm  |  {len(sp.wavelengths)} points")

    # --- Step 2: Show nominal thicknesses from XML (no COM needed) ---
    print(f"\n[2] Nominal layer stack (from XML):")
    tree = load_design(design_path)
    nominal_xml = xml_get_layers(tree)
    print_layers(nominal_xml)

    if args.no_com:
        print("\n--no-com flag set. Stopping after file parse.")
        return

    # --- Step 3: Connect to MacLeod ---
    print(f"\n[3] Connecting to MacLeod...")
    session = connect()

    # --- Step 4: Open design via COM ---
    print(f"\n[4] Opening design in MacLeod...")
    design = open_design(session, design_path)
    nominal_com = get_layers(design)

    # --- Step 5: Push SP targets via COM ---
    print(f"\n[5] Loading SP transmission targets into MacLeod...")
    set_targets_from_sp(design, sp.wavelengths, sp.transmittances)

    # --- Step 6: Run Simplex ---
    print(f"\n[6] Running Simplex optimization...")
    result = run_simplex(design, max_iterations=args.iterations)

    # --- Step 7: Print comparison ---
    print(f"\n[7] Results:")
    print_comparison(nominal_com, result["layers"])
    print(f"\n    Merit function: {result['merit']:.6f}")
    print(f"    Converged: {result['converged']}  |  Evaluations: {result['iterations']}")

    # --- Step 8: Save ---
    save_design(design, output_path)
    print(f"\n[8] Optimized design saved: {output_path.name}")


if __name__ == "__main__":
    main()
