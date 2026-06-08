"""
Multi-radius uniformity analysis using MacLeod COM.

For each SP file (one per witness radius), loads %T targets into MacLeod,
runs linked-layer Simplex (one scale factor per material group), and charts
the physical thickness delta (optimized - nominal) per layer vs radius.

Usage:
  python analyze_uniformity.py <P1014134.SP> <P1014135.SP> ...
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from utils.sp_parser import parse_sp
from macleod.dds_editor import load_design, get_layers as xml_get_layers
from macleod.com_interface import connect, open_design, get_layers, set_targets_from_sp, run_linked_simplex

DESIGN_PATH = (
    r"C:\Users\User\FiveNine Dropbox\FiveNine Optics Team Folder"
    r"\Coating Designs\Eric\Uniformity\Uniformity HW 16layers.dds"
)


def compute_layer_deltas(nominal_layers, result):
    """
    Returns list of {number, material, nominal_nm, delta_nm} for all active layers.
    Frozen layer gets delta_nm=0.
    """
    deltas = []
    ta_s = result["ta2o5_scale"]
    si_s = result["sio2_scale"]
    frozen = result["frozen_layer"]
    for layer in nominal_layers:
        t0 = layer["thickness_nm"]
        n = layer["number"]
        mat = layer["material"]
        if "Ta2O5" in mat:
            delta = (ta_s - 1.0) * t0
        elif "SiO2" in mat and n != frozen:
            delta = (si_s - 1.0) * t0
        else:
            delta = 0.0
        deltas.append({"number": n, "material": mat, "nominal_nm": t0, "delta_nm": delta})
    return deltas


def plot_results(all_results, run_label):
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker

    radii = [r["radius"] for r in all_results]
    ta_scales = [r["result"]["ta2o5_scale"] for r in all_results]
    si_scales = [r["result"]["sio2_scale"] for r in all_results]

    # Normalize to R=2 (first point)
    ta_ref = ta_scales[0]
    si_ref = si_scales[0]
    ta_norm = [s / ta_ref for s in ta_scales]
    si_norm = [s / si_ref for s in si_scales]

    fig, ax = plt.subplots(figsize=(8, 5))
    fig.suptitle(f"V6 Uniformity — HW 16L — {run_label}", fontsize=12)

    ax.plot(radii, ta_norm, marker="o", color="steelblue", label="Ta2O5")
    ax.plot(radii, si_norm, marker="s", color="darkorange", label="SiO2")
    ax.axhline(1.0, color="black", linewidth=0.8, linestyle="--")

    ax.set_xlabel("Radius (inches)")
    ax.set_ylabel("Normalized thickness (R=2\" = 1)")
    ax.xaxis.set_major_locator(ticker.MultipleLocator(1))
    ax.grid(True, alpha=0.3)
    ax.legend()

    plt.tight_layout()
    plt.show()


def build_curve_points(all_results):
    """Build the list of dicts expected by mariadb_export.export_run."""
    ta_ref = all_results[0]["result"]["ta2o5_scale"]
    si_ref = all_results[0]["result"]["sio2_scale"]
    points = []
    for r in all_results:
        res = r["result"]
        points.append({
            "radius":       r["radius"],
            "ta2o5_scale":  res["ta2o5_scale"],
            "sio2_scale":   res["sio2_scale"],
            "ta2o5_norm":   res["ta2o5_scale"] / ta_ref,
            "sio2_norm":    res["sio2_scale"] / si_ref,
            "merit":        res["merit"],
            "converged":    res["converged"],
        })
    return points


def main():
    import argparse
    parser = argparse.ArgumentParser(description="MacLeod uniformity analyzer")
    parser.add_argument("sp_files", nargs="+", help=".SP witness files")
    parser.add_argument("--export", action="store_true", help="Export results to MariaDB dashboard")
    parser.add_argument("--db-host",     default="localhost")
    parser.add_argument("--db-port",     type=int, default=3306)
    parser.add_argument("--db-user",     default="")
    parser.add_argument("--db-password", default="")
    parser.add_argument("--db-name",     default="")
    args = parser.parse_args()

    sp_paths = [Path(p) for p in args.sp_files]

    print("\n[1] Parsing SP files...")
    sp_files = []
    for p in sp_paths:
        sp = parse_sp(p)
        if sp.radius is None:
            print(f"  WARNING: no radius found in {p.name}, skipping")
            continue
        sp_files.append(sp)
        print(f"  {p.name}  R={sp.radius}\"  F={sp.f_factor}  {len(sp.wavelengths)} pts")

    sp_files.sort(key=lambda s: s.radius)

    print("\n[2] Connecting to MacLeod...")
    session = connect()

    print(f"\n[3] Opening design: {Path(DESIGN_PATH).name}")
    design = open_design(session, DESIGN_PATH)
    nominal_layers = get_layers(design)
    print(f"  {len(nominal_layers)} active layers loaded")

    print("\n[4] Running linked-layer Simplex per radius...\n")
    all_results = []
    for sp in sp_files:
        print(f"  --- R={sp.radius}\" ({sp.path.name}) ---")
        set_targets_from_sp(design, sp.wavelengths, sp.transmittances)
        result = run_linked_simplex(design, nominal_layers)
        result["radius"] = sp.radius
        all_results.append({"radius": sp.radius, "result": result})
        print(
            f"  Ta2O5: {result['ta2o5_pct_correction']:+.2f}%  "
            f"SiO2: {result['sio2_pct_correction']:+.2f}%  "
            f"merit={result['merit']:.6f}  "
            f"({'converged' if result['converged'] else 'NOT converged'})"
        )

    # Summary table
    print("\n" + "=" * 62)
    print(f"  V6 Uniformity - HW 16L - 6/5/26")
    print("=" * 62)
    print(f"  {'R (in)':<10} {'Ta2O5 corr %':>14} {'SiO2 corr %':>14} {'Merit':>10}")
    print("  " + "-" * 52)
    for r in all_results:
        res = r["result"]
        print(
            f"  {r['radius']:<10.1f} {res['ta2o5_pct_correction']:>+14.3f} "
            f"{res['sio2_pct_correction']:>+14.3f} {res['merit']:>10.6f}"
        )
    ta_vals = [r["result"]["ta2o5_pct_correction"] for r in all_results]
    si_vals = [r["result"]["sio2_pct_correction"] for r in all_results]
    print("=" * 62)
    print(f"\n  Ta2O5 spread: {max(ta_vals) - min(ta_vals):.3f}%")
    print(f"  SiO2  spread: {max(si_vals) - min(si_vals):.3f}%\n")

    # Export to MariaDB
    if args.export:
        from utils.mariadb_export import connect as db_connect, ensure_schema, export_run
        from datetime import date as date_type
        anchor = sp_files[0]
        run_date = date_type.today()
        if anchor.date:
            try:
                parts = anchor.date.replace("-", "/").split("/")
                if len(parts) == 3:
                    m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
                    if y < 100:
                        y += 2000
                    run_date = date_type(y, m, d)
            except ValueError:
                pass
        chamber = anchor.chamber or "unknown"
        design_name = anchor.design_name or Path(DESIGN_PATH).stem
        f_factor = anchor.f_factor

        print(f"\n[5] Exporting to MariaDB ({args.db_host}/{args.db_name})...")
        conn = db_connect(args.db_host, args.db_user, args.db_password, args.db_name, args.db_port)
        ensure_schema(conn)
        curve_points = build_curve_points(all_results)
        run_id = export_run(conn, chamber, design_name, run_date, f_factor, curve_points)
        conn.close()
        print(f"  Saved as run_id={run_id}  chamber={chamber}  date={run_date}  {len(curve_points)} radii")

    # Chart
    run_label = "6/5/26"
    if sp_files and sp_files[0].date:
        run_label = sp_files[0].date
    plot_results(all_results, run_label)


if __name__ == "__main__":
    main()
