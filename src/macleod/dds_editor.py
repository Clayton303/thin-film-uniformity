"""
Read and write MacLeod .dds XML design files.

Key XML structure:
  EssentialMacleodDesign
    Layers / Layer[@LayerNumber]  — physical thickness in nm, material name
    Targets / Target[@Number]     — optimization targets
      TargetType 84  = %T (transmittance percentage)
      Polarisation 80 = both polarizations
      RequiredValue   = target %T value (0–100 scale)
      Wavelength      = nm
      Tolerance       = acceptable deviation
      Weight          = optimizer weight
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Layer:
    number: int
    material: str
    thickness_nm: float   # physical thickness


def load_design(path: str | Path) -> ET.ElementTree:
    return ET.parse(str(path))


def get_layers(tree: ET.ElementTree) -> list[Layer]:
    layers = []
    for elem in tree.findall(".//Layer"):
        num = int(elem.get("LayerNumber", 0))
        mat = elem.findtext("Material", "").strip()
        thick_text = elem.findtext("Thickness", "0").strip()
        thick = float(thick_text)
        if thick > 0:  # skip substrate (thickness=0)
            layers.append(Layer(number=num, material=mat, thickness_nm=thick))
    return layers


def print_layers(layers: list[Layer]) -> None:
    print(f"{'Layer':<8} {'Material':<30} {'Thickness (nm)':<16}")
    print("-" * 56)
    for layer in layers:
        print(f"{layer.number:<8} {layer.material:<30} {layer.thickness_nm:<16.6f}")


def replace_targets(tree: ET.ElementTree,
                    wavelengths: list[float],
                    transmittances: list[float],
                    tolerance: float = 1.0,
                    weight: float = 1.0) -> None:
    """Replace all existing targets with new %T targets from SP data."""
    root = tree.getroot()

    # Remove existing Targets block
    targets_elem = root.find("Targets")
    if targets_elem is not None:
        root.remove(targets_elem)

    targets_elem = ET.SubElement(root, "Targets")

    for i, (wl, t) in enumerate(zip(wavelengths, transmittances)):
        tgt = ET.SubElement(targets_elem, "Target", attrib={"Number": str(i)})
        _sub(tgt, "ContextIndex", "0")
        _sub(tgt, "LinkNumber", "0")
        _sub(tgt, "IncidentAngle", " 0")
        _sub(tgt, "LinkMultiplier", "0" if i > 0 else "1")
        _sub(tgt, "LinkChild", "0")
        _sub(tgt, "Derivative", "0")
        _sub(tgt, "Polarisation", "80")      # both polarizations
        _sub(tgt, "RequiredValue", f" {t:.6f}")
        _sub(tgt, "TargetType", "84")        # %T
        _sub(tgt, "Tolerance", f" {tolerance}")
        _sub(tgt, "Wavelength", f" {wl:.6f}")
        _sub(tgt, "Weight", f" {weight}")
        _sub(tgt, "Operator", "0")


def save_design(tree: ET.ElementTree, path: str | Path) -> None:
    ET.indent(tree, space=" ")
    tree.write(str(path), encoding="unicode", xml_declaration=True)


def _sub(parent: ET.Element, tag: str, text: str) -> ET.Element:
    el = ET.SubElement(parent, tag)
    el.text = text
    return el
