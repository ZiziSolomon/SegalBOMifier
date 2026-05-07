"""Segal Method BOM Generator.

A Python module for estimating materials needed for Walter Segal's
timber-frame building method. Define buildings as connected modular bays
on a tartan grid and generate a detailed Bill of Materials.

Usage:
    from segal_method import SegalBuilding, SegalGrid, WallType

    grid = SegalGrid(panel_width=600, structural_thickness=50)
    building = SegalBuilding(grid)
    building.add_bay("kitchen", width=4, depth=4)
    bom = building.generate_bom()
    print(bom.to_table())
"""

from grid import SegalGrid
from building import Bay, SegalBuilding, WallType
from bom import BOM, BOMItem, BOMCalculator


def _attach_generate_bom(cls):
    """Attach generate_bom() to SegalBuilding so users don't need
    to know about BOMCalculator directly."""

    def generate_bom(
        self,
        include_foundations: bool = True,
        outer_panel_specs: dict = None,
    ) -> BOM:
        """Calculate and return a complete Bill of Materials.

        Args:
            include_foundations: Set False if building on an existing slab
                or patio and foundation pads are not needed.
            outer_panel_specs: Optional dict mapping bay names to a
                :class:`~segal_method.materials.PanelSpec` for the outer
                wall cladding.  Any bay not listed uses the default
                HardiePlank spec.  Example::

                    from segal_method import materials as mat
                    bom = building.generate_bom(outer_panel_specs={
                        "lean_to_1": mat.FEATHEREDGE_OUTER,
                        "office":    mat.CEDRAL_LAP_OUTER,
                    })
        """
        return BOMCalculator(
            self,
            include_foundations=include_foundations,
            outer_panel_specs=outer_panel_specs,
        ).calculate()

    cls.generate_bom = generate_bom
    return cls


_attach_generate_bom(SegalBuilding)


__all__ = [
    "SegalGrid",
    "SegalBuilding",
    "Bay",
    "WallType",
    "BOM",
    "BOMItem",
    "BOMCalculator",
]
