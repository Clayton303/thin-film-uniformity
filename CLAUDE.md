# Thin Film Uniformity Calculator

Python tool to parse MacLeod thin film designs, load transmission targets from a spectrophotometer,
and compute per-chamber uniformity corrections for ion beam coating chambers at FiveNine Optics.

---

## Project Status

- [x] File formats identified and parsed (MacLeod XML, PerkinElmer .SP)
- [x] Project directory structure created
- [ ] MacLeod COM interface module
- [ ] .SP target file parser
- [ ] V1/V6 single-rotation mask correction + G-code output
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

### Spectrophotometer Target File (.SP)
- Format: PerkinElmer UV WinLab ASCII
- File location: `\\59o-spectro\uvwinlab\DATA\`
- Header line 9 encodes run metadata:
  - Format: `[chamber]-Unif [design] [date], F=[factor], R=[run]`
  - Example: `V6-Unif HW 16L 06/03/2026, F=1.044, R=2`
  - `F` = uniformity correction factor applied during deposition
  - `R` = run number
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
3. **Tormac G-code dialect** — standard Fanuc-style or machine-specific? Need a sample or spec.
4. **Mask geometry** — radius range, step size for V1/V6 mask profiles. Need existing mask drawing or profile data.
5. **Witness measurement locations** — are .SP files named by a convention that encodes chamber/date, or is the header the only metadata?

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
