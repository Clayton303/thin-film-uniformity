# Thin Film Uniformity Calculator

Python tool to parse MacLeod thin film designs, load transmission targets from a spectrophotometer,
and compute per-chamber uniformity corrections for ion beam coating chambers at FiveNine Optics.

---

## Project Status

- [x] File formats identified and parsed (MacLeod XML, PerkinElmer .SP)
- [x] Project directory structure created
- [x] MacLeod COM interface module (`src/macleod/com_interface.py`)
- [x] .SP target file parser (`src/utils/sp_parser.py`)
- [x] V1/V6 single-rotation mask correction + G-code output (`src/chambers/v1_v6_rotation.py`, `src/gcode/tormac_generator.py`)
- [ ] V2/V7 planetary tilt correction
- [ ] MacLeod feedback loop (write corrected thicknesses back)
- [ ] Dropbox/network file resolver
- [ ] V3, V4, V5 (on hold)

---

## Chamber Overview

| Chamber | Method | Output | Status |
|---------|--------|--------|--------|
| V1 | Single rotation + CNC mask | Mask profile → Tormac G-code | Active |
| V2 | Planetary tilt | Corrected tilt angles | Active |
| V3 | CNC cut mask | G-code | On hold |
| V4 | CNC cut mask | G-code | On hold |
| V5 | CNC cut mask (different geometry) | G-code | On hold |
| V6 | Single rotation + CNC mask | Mask profile → Tormac G-code | Active |
| V7 | Planetary tilt | Corrected tilt angles | Active |

---

## File Formats

### MacLeod Design File (.dds)
- XML format, root element: `EssentialMacleodDesign`
- Layers at: `EssentialMacleodDesign > Layers > Layer`
- Key fields per layer: `LayerNumber`, `Material`, `Thickness` (physical, nm)
- Material naming convention: `[material] BB [chamber] R[run]`
  - Example: `Ta2O5 BB V3 R15`, `SiO2 BB V3 R15`
- Layer 17 (last) is the substrate (SlideGlass, Thickness=0), not a coating layer
- Sample design (Uniformity HW 16L): 16 active layers, alternating Ta2O5/SiO2
  - Ta2O5: ~136.34 nm, SiO2: ~167.80 nm (layers 1-15)
  - Final SiO2 layer 16: 20.33 nm

### Layer Linking in Uniformity Optimization
- MacLeod layers can be "linked" — linked layers share a common thickness variable and must scale together
- During uniformity optimization, linked layers are treated as a single group: all members receive the same corrected thickness
- **Exception — Layer 16**: this layer is linked to the other SiO2 layers but must be held fixed (not optimized) because it does not affect uniformity; exclude it from the optimization variable set regardless of its link status

### V1/V6 Mask Files
Located at: `...\Coating Run Data\Run Data\Masks\V1\SiO2_Single\` and `...\Ta2O5_Single\`

Four file types per mask revision, named `[part#] [Material] Mask V1 Rev[N]`:

| Extension | Format | Description |
|-----------|--------|-------------|
| `.msk` | CSV `rad,width` | Radial mask profile — 10 points, radius and cut width in inches |
| `.tap` | Fanuc G-code | CNC toolpath for Tormac machine (G20 inch, G41 cutter comp, G00/G01 moves) |
| `.cut` | CSV `pos,inchperpercent` | Sensitivity calibration — mask width change (inches) per 1% thickness change at each radius |
| `.npl` | Binary (MacLeod plot) | Visualization file — not used programmatically |

**`.msk` details:**
- 10 radial points: 1.26, 1.9, 2.85, 3.8, 4.75, 5.7, 6.175, 6.65, 7.6, 8.55 inches
- `width` = full cut width at that radius (inches)

**`.cut` details:**
- 10 radial points (slightly different spacing than `.msk`: uses 2.8 not 2.85)
- `inchperpercent` is negative — narrowing the mask (smaller width) increases deposited thickness
- Same sensitivity values are used for both SiO2 and Ta2O5 in V1 (chamber geometry only)
- Most recent calibration: `05222025.cut`

**`.tap` (Tormac G-code) details:**
- Standard Fanuc dialect: G20 (inch), G17, G64, G40, G80, G90, G94
- Tool: T6 — 1/4" × 5/8" 3FL end mill (P/N 13825003)
- Cutter compensation: G41 D6
- Two-pass cut at Z=+0.01 and Z=−0.01 inches
- XY path traces both edges of the mask opening in a single closed contour

**`.msk` → `.tap` coordinate transform (from `Mask_Magic_All_Rev4.bas`):**

For V1 and V6: `xOffset = 8.215` inches (machine origin to rotation center distance)

For each `(rad, width)` point:
```
x = sqrt(rad² − (width/2)²) + xOffset
y_lower = −width/2
y_upper = +width/2
```

The mask opening at radius `rad` is a chord of that circle; `x` is the perpendicular distance from the rotation axis to the chord, shifted by `xOffset`.

After computing discrete (x, y) points:
1. Fit cubic spline through lower half (outer→inner, y negative)
2. Fit cubic spline through upper half (inner→outer, y positive)
3. Concatenate into a closed toolpath

G-code structure (two identical passes at Z=+0.01 and Z=−0.01):
- **Header**: fixed — G20 inch, T6, S5000, approach at X=17.5791 Y=−0.7324, cutter comp G41 D6
- **Toolpath**: X…Y… lines for each spline point
- **Pass separator**: G40 retract, reposition, Z=−0.01, re-engage G41 D6
- **Footer**: fixed — G40, Z retract, G28 home, M30

Spline resolution: 10 steps per segment between original mask data points (~90 XY moves per half).

**Correction workflow:**
1. Read current `.msk` profile
2. Compute required % thickness correction at each radial point from uniformity measurement
3. Multiply by sensitivity (`inchperpercent`, interpolating `.cut` to match `.msk` radii) to get width delta
4. Apply delta to current widths → new `.msk` profile
5. Apply coordinate transform + cubic spline to generate new `.tap` G-code

### Spectrophotometer Target File (.SP)
- Format: PerkinElmer UV WinLab ASCII
- File location: `\\59o-spectro\uvwinlab\DATA\`
- Header line 9 encodes run metadata:
  - Format: `[chamber]-Unif [design] [date], F=[factor], R=[radius_inches]`
  - Example: `V6-Unif HW 16L 06/03/2026, F=1.044, R=2`
  - `F` = uniformity correction factor applied during deposition
  - `R` = witness piece radial position in inches (NOT a run number)
  - Batch measurements: only the first SP file in a set gets the full header;
    subsequent files may only carry `R=[radius]` as the title field
- Data section starts after `#DATA` marker
- Format: tab-separated `wavelength\t%T` pairs
- Range: 700 nm → 400 nm, 1 nm steps, 301 points

---

## File Paths

| Resource | Path |
|----------|------|
| Dropbox root | `C:\Users\User\FiveNine Dropbox\FiveNine Optics Team Folder\` |
| Coating designs | `...\Coating Designs\Eric\Uniformity\` |
| Spectro data | `\\59o-spectro\uvwinlab\DATA\` |
| V1 masks | `...\Coating Run Data\Run Data\Masks\V1\` (subdirs: `SiO2_Single\`, `Ta2O5_Single\`) |
| V6 masks | `...\Coating Run Data\Run Data\Masks\V6\` (subdirs: `SiO2_Single\`, `Ta2O5_Single\`) |
| MacLeod scripts | `...\Eric\Thin Film Center\References\Scripts\` |
| Project root | `C:\Users\User\thin-film-uniformity\` |

---

## Project Structure

```
thin-film-uniformity/
├── CLAUDE.md                  # This file — project context for Claude Code
├── main.py                    # CLI entry point
├── config/
│   └── chambers.yaml          # Per-chamber calibration config
├── src/
│   ├── macleod/
│   │   └── interface.py       # MacLeod COM automation + XML parser
│   ├── chambers/
│   │   ├── base.py            # Abstract chamber class
│   │   ├── v1_v6_rotation.py  # Single rotation + mask logic
│   │   └── v2_v7_planetary.py # Planetary tilt correction
│   ├── gcode/
│   │   └── tormac_generator.py # G-code output for Tormac CNC
│   └── utils/
│       ├── file_resolver.py   # Dropbox + UNC network path handling
│       └── sp_parser.py       # PerkinElmer .SP file parser
└── data/
    └── samples/               # Sample .dds and .SP files for testing
```

---

## Open Questions

1. **F factor origin** — is `F=1.044` the pre-run deposition rate correction, or derived post-run from measured vs. ideal spectrum comparison?
2. **V2/V7 tilt calibration** — lookup table or formula? Need to see the calibration data format.
3. ~~**Tormac G-code dialect**~~ — confirmed standard Fanuc (G20 inch, G41 cutter comp). ✓
4. ~~**Mask geometry**~~ — 10 radial points, 1.26"–8.55" range. ✓
5. **Witness measurement locations** — are .SP files named by a convention that encodes chamber/date, or is the header the only metadata?
6. ~~**`.msk` → `.tap` coordinate transform**~~ — confirmed: `X = sqrt(rad² − (width/2)²) + 8.215`, cubic spline, two-pass G-code. See `Mask_Magic_All_Rev4.bas`. ✓

---

## Key Decisions

- MacLeod interface: COM scripting (not file parsing) — license confirmed
- File storage: locally synced Dropbox folder (not API)
- MacLeod version: most recent (as of June 2026)
- Build order: V1, V2, V6, V7 → then V3, V4, V5

---

## Notes

- The `.dds` XML file is large (~393 KB for a 16-layer design) due to embedded optimization data
- The `F` factor in the `.SP` header is the most direct uniformity correction signal per run
- Accumulating F history per chamber will allow trend analysis and proactive correction
