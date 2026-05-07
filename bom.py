"""Bill of Materials generation for Segal method buildings.

Walks the building topology and calculates material quantities for every
element, following the construction sequence from the PDF:
foundations -> frame -> roof -> floors -> external walls -> windows ->
partitions -> ceilings -> fixings.
"""

import csv
import math
import re
from dataclasses import dataclass, field
from io import StringIO
from typing import Optional

from building import SegalBuilding, WallType, FACES, FACE_AXIS
from grid import SegalGrid
import materials as mat


@dataclass
class BOMItem:
    """A single line item in the Bill of Materials."""

    category: str       # e.g. "Foundations", "Structural Frame"
    description: str    # e.g. "Structural post (column)"
    material: str       # e.g. "Softwood, pressure-treated"
    size: str           # e.g. "100x50mm"
    length_or_area: str  # e.g. "2850mm" or "0.36 m2"
    quantity: int | float
    unit: str           # e.g. "nr", "m", "m2", "m3", "litres"
    location: str = ""  # e.g. "kitchen.north" or "all bays"

    @property
    def quantity_display(self) -> str:
        if isinstance(self.quantity, float):
            return f"{self.quantity:.2f}"
        return str(self.quantity)


class BOM:
    """A complete Bill of Materials, composed of BOMItems.

    Provides output as a formatted table, CSV, or list of dicts.
    """

    def __init__(self, items: Optional[list[BOMItem]] = None):
        self.items: list[BOMItem] = items or []

    def add(self, item: BOMItem) -> None:
        self.items.append(item)

    def add_many(self, items: list[BOMItem]) -> None:
        self.items.extend(items)

    @property
    def categories(self) -> list[str]:
        """Unique categories in order of first appearance."""
        seen = {}
        for item in self.items:
            if item.category not in seen:
                seen[item.category] = True
        return list(seen.keys())

    def items_by_category(self, category: str) -> list[BOMItem]:
        return [i for i in self.items if i.category == category]

    def to_table(self) -> str:
        """Format the BOM as a readable text table.

        Column widths are derived from the actual data so long entries
        never overflow into adjacent columns.
        """
        headers = ["Description", "Material", "Size", "Length/Area", "Qty", "Unit", "Location"]
        # Index of the right-aligned column (Qty)
        QTY_COL = 4

        # Collect every row as plain strings so we can measure widths
        all_rows = [
            [
                item.description, item.material, item.size,
                item.length_or_area, item.quantity_display,
                item.unit, item.location,
            ]
            for item in self.items
        ]

        # Column widths = max of header or any data value in that column
        widths = [len(h) for h in headers]
        for row in all_rows:
            for col_i, val in enumerate(row):
                widths[col_i] = max(widths[col_i], len(str(val)))

        def fmt_row(vals: list) -> str:
            parts = []
            for col_i, (w, v) in enumerate(zip(widths, vals)):
                parts.append(f"{v:>{w}}" if col_i == QTY_COL else f"{v:<{w}}")
            return "  " + " ".join(parts)

        # Total printable width: 2-char indent + columns + single space between each
        row_width = 2 + sum(widths) + (len(widths) - 1)
        rule = "=" * row_width
        thin = "  " + "-" * (row_width - 2)

        lines = [rule, "SCHEDULE OF MATERIALS -- SEGAL METHOD BUILDING", rule]

        for cat in self.categories:
            heading = f"-- {cat.upper()} "
            lines.append("")
            lines.append(heading + "-" * max(0, row_width - len(heading)))
            lines.append(fmt_row(headers))
            lines.append(thin)
            for item in self.items_by_category(cat):
                row = [
                    item.description, item.material, item.size,
                    item.length_or_area, item.quantity_display,
                    item.unit, item.location,
                ]
                lines.append(fmt_row(row))

        lines.append("")
        lines.append(rule)
        return "\n".join(lines)

    def to_csv(self, path: str) -> None:
        """Write the BOM to a CSV file."""
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Category", "Description", "Material", "Size",
                "Length/Area", "Quantity", "Unit", "Location",
            ])
            for item in self.items:
                writer.writerow([
                    item.category, item.description, item.material,
                    item.size, item.length_or_area,
                    item.quantity_display, item.unit, item.location,
                ])

    def to_dicts(self) -> list[dict]:
        """Export as a list of dicts (handy for pricing integration)."""
        return [
            {
                "category": i.category,
                "description": i.description,
                "material": i.material,
                "size": i.size,
                "length_or_area": i.length_or_area,
                "quantity": i.quantity,
                "unit": i.unit,
                "location": i.location,
            }
            for i in self.items
        ]

    def consolidate(self) -> "BOM":
        """Return a new BOM with same-product items merged into single purchase lines.

        Items that originate from different bays or faces but represent the same
        physical product are combined so that pricing and wastage calculations
        operate on total quantities rather than per-location sub-totals.

        Two items are the same product when they share (category, description,
        material, size, unit) and, for count-based items, length_or_area as well
        (e.g. two batches of 100×50mm post at 2850mm are combined; two batches
        at different lengths stay separate).

        For area-derived items (unit in sheets/boards/rolls/packs, with an m²
        value in length_or_area but no "ea" qualifier), the areas are summed and
        length_or_area is updated to the total.  Pricers that re-derive purchase
        quantities from area then apply one ceiling over the full total, avoiding
        per-face rounding waste.  The quantity field is left as the per-location
        sum; use the cost estimate for the accurate purchase count.
        """
        from collections import defaultdict

        _AREA_UNITS = ("sheets", "boards", "rolls", "packs")
        _area_re = re.compile(r"([\d.]+)\s*m2")

        def _is_area_item(item: BOMItem) -> bool:
            return (
                item.unit in _AREA_UNITS
                and "ea" not in item.length_or_area
                and bool(_area_re.search(item.length_or_area))
            )

        def _parse_area(text: str) -> float:
            m = _area_re.search(text)
            return float(m.group(1)) if m else 0.0

        groups: dict = defaultdict(list)
        for item in self.items:
            if _is_area_item(item):
                # Ignore per-location area value when grouping
                key = (item.category, item.description, item.material, item.size, item.unit)
            else:
                key = (
                    item.category, item.description, item.material,
                    item.size, item.length_or_area, item.unit,
                )
            groups[key].append(item)

        new_bom = BOM()
        for items in groups.values():
            first = items[0]
            if len(items) == 1:
                new_bom.add(first)
                continue
            locations = ", ".join(i.location for i in items if i.location)
            total_qty = sum(i.quantity for i in items)
            if _is_area_item(first):
                total_area = sum(_parse_area(i.length_or_area) for i in items)
                new_bom.add(BOMItem(
                    category=first.category,
                    description=first.description,
                    material=first.material,
                    size=first.size,
                    length_or_area=f"{total_area:.1f} m2",
                    quantity=total_qty,
                    unit=first.unit,
                    location=locations,
                ))
            else:
                new_bom.add(BOMItem(
                    category=first.category,
                    description=first.description,
                    material=first.material,
                    size=first.size,
                    length_or_area=first.length_or_area,
                    quantity=total_qty,
                    unit=first.unit,
                    location=locations,
                ))
        return new_bom

    def price(self):
        """Return a PriceEstimate by matching items against the Selco catalogue.

        Equivalent to ``Pricer().price_bom(self)``.
        """
        from pricing import Pricer
        return Pricer().price_bom(self)


class BOMCalculator:
    """Walks a SegalBuilding and produces a detailed BOM.

    Each _calc_* method handles one element category, adding items
    to the BOM in construction sequence.
    """

    def __init__(
        self,
        building: SegalBuilding,
        include_foundations: bool = True,
        outer_panel_specs: Optional[dict] = None,
    ):
        self.building = building
        self.grid = building.grid
        self.bom = BOM()
        self.include_foundations = include_foundations
        # Maps bay_name -> PanelSpec for outer wall material overrides.
        # Falls back to mat.EXTERNAL_PANEL_OUTER for any bay not listed.
        self.outer_panel_specs: dict = outer_panel_specs or {}

    def calculate(self) -> BOM:
        """Run all calculations and return the complete BOM."""
        if self.include_foundations:
            self._calc_foundations()
        self._calc_frame()
        self._calc_roof()
        self._calc_floors()
        self._calc_external_walls()
        self._calc_windows()
        self._calc_partitions()
        self._calc_ceilings()
        self._calc_fixings()
        return self.bom

    # ── 1. Foundations ───────────────────────────────────────────────────

    def _calc_foundations(self) -> None:
        cat = "Foundations"
        n_posts = self.building.unique_post_count()

        # Concrete pads
        pad_vol = (
            (mat.FOUNDATION_PAD_WIDTH / 1000)
            * (mat.FOUNDATION_PAD_WIDTH / 1000)
            * (mat.FOUNDATION_PAD_DEPTH / 1000)
        )
        self.bom.add(BOMItem(
            category=cat,
            description="Concrete foundation pad",
            material="In-situ concrete",
            size=f"{mat.FOUNDATION_PAD_WIDTH}x{mat.FOUNDATION_PAD_WIDTH}mm",
            length_or_area=f"{mat.FOUNDATION_PAD_DEPTH}mm deep",
            quantity=n_posts,
            unit="nr",
            location="all post positions",
        ))
        self.bom.add(BOMItem(
            category=cat,
            description="Concrete (total volume)",
            material="In-situ concrete",
            size="",
            length_or_area="",
            quantity=round(n_posts * pad_vol, 2),
            unit="m3",
            location="all post positions",
        ))

        # Paving slab caps
        self.bom.add(BOMItem(
            category=cat,
            description="Paving slab cap",
            material="Concrete paving slab",
            size=f"{mat.PAVING_SLAB_SIZE}x{mat.PAVING_SLAB_SIZE}mm",
            length_or_area="",
            quantity=n_posts,
            unit="nr",
            location="all post positions",
        ))

        # Lead moisture seals
        self.bom.add(BOMItem(
            category=cat,
            description="Lead moisture seal",
            material="Lead sheet",
            size=f"{mat.LEAD_SHEET_SIZE}x{mat.LEAD_SHEET_SIZE}mm",
            length_or_area="",
            quantity=n_posts,
            unit="nr",
            location="all post positions",
        ))

        # Gravel for oversite (total footprint area * ~100mm depth)
        footprint_m2 = sum(
            b.floor_area_m2(self.grid) for b in self.building.bays.values()
        )
        gravel_m3 = round(footprint_m2 * 0.1, 2)
        self.bom.add(BOMItem(
            category=cat,
            description="Oversite gravel",
            material="20mm gravel",
            size="",
            length_or_area=f"{footprint_m2:.1f} m2",
            quantity=gravel_m3,
            unit="m3",
            location="below building",
        ))

        # Paving slab border (perimeter of building footprint)
        perimeter_m = self._approx_perimeter_m()
        border_slabs = math.ceil(perimeter_m / (mat.PAVING_SLAB_SIZE / 1000))
        self.bom.add(BOMItem(
            category=cat,
            description="Paving slab border",
            material="Concrete paving slab",
            size=f"{mat.PAVING_SLAB_SIZE}x{mat.PAVING_SLAB_SIZE}mm",
            length_or_area=f"{perimeter_m:.1f}m perimeter",
            quantity=border_slabs,
            unit="nr",
            location="building perimeter",
        ))

    # ── 2. Structural Frame ─────────────────────────────────────────────

    def _calc_frame(self) -> None:
        cat = "Structural Frame"
        b = self.building

        # Posts
        for bay_name, bay in b.bays.items():
            height = b.post_height_mm(bay_name)
            self.bom.add(BOMItem(
                category=cat,
                description=mat.POST_TIMBER.description,
                material=f"Softwood, {mat.POST_TIMBER.treatment}",
                size=mat.POST_TIMBER.section_label,
                length_or_area=f"{height}mm",
                quantity=4,
                unit="nr",
                location=bay_name,
            ))

        # Deduplicate post count note
        n_posts = b.unique_post_count()
        total_raw = 4 * len(b.bays)
        if n_posts < total_raw:
            self.bom.add(BOMItem(
                category=cat,
                description="Posts shared at connections",
                material="(deduct shared posts)",
                size=mat.POST_TIMBER.section_label,
                length_or_area="",
                quantity=-(total_raw - n_posts),
                unit="nr",
                location="shared positions",
            ))

        # Beams — along each face of each bay, at each level
        for bay_name, bay in b.bays.items():
            n_levels = bay.storeys + 1  # floor tie beam + roof (+ first floor if 2-storey)
            for face in FACES:
                modules = bay.face_modules(face)
                beam_length = self.grid.modules_to_mm(modules)
                is_interior = b.is_interior_frame_line(bay_name, face)
                # Double beam at interior, single at perimeter
                beam_count = 2 if is_interior else 1

                # Only count each shared frame line once: skip the "reverse" side
                if is_interior:
                    connected = b.get_connected_bay(bay_name, face)
                    if connected and connected < bay_name:
                        # The other bay already counted this frame line
                        continue

                self.bom.add(BOMItem(
                    category=cat,
                    description=f"Beam ({'double' if is_interior else 'single'})",
                    material=f"Softwood {mat.BEAM_TIMBER.grade}, {mat.BEAM_TIMBER.treatment}",
                    size=mat.BEAM_TIMBER.section_label,
                    length_or_area=f"{beam_length}mm",
                    quantity=beam_count * n_levels,
                    unit="nr",
                    location=f"{bay_name}.{face}",
                ))

        # Joist bearers (bolted to beam sides, joists rest on them)
        for bay_name, bay in b.bays.items():
            n_levels = bay.storeys + 1
            # Bearers run along the beams on north and south faces
            # (joists span east-west between north/south frame lines)
            for face in ("north", "south"):
                bearer_length = self.grid.modules_to_mm(bay.face_modules(face))
                self.bom.add(BOMItem(
                    category=cat,
                    description=mat.BEARER_TIMBER.description,
                    material=f"Softwood",
                    size=mat.BEARER_TIMBER.section_label,
                    length_or_area=f"{bearer_length}mm",
                    quantity=n_levels,
                    unit="nr",
                    location=f"{bay_name}.{face}",
                ))

        # Joists — span between north/south frames at modular spacing
        for bay_name, bay in b.bays.items():
            n_levels = bay.storeys + 1
            joist_span = self.grid.modules_to_mm(bay.depth_modules)
            # One joist per module pitch along the width, plus one at each end
            n_joists = bay.width_modules + 1
            self.bom.add(BOMItem(
                category=cat,
                description=mat.JOIST_TIMBER.description,
                material=f"Softwood {mat.JOIST_TIMBER.grade}, {mat.JOIST_TIMBER.treatment}",
                size=mat.JOIST_TIMBER.section_label,
                length_or_area=f"{joist_span}mm",
                quantity=n_joists * n_levels,
                unit="nr",
                location=bay_name,
            ))

        # Bracing — one braced bay per floor level for the whole building
        max_storeys = b.max_storeys()
        n_brace_levels = max_storeys + 1  # each floor + roof
        # Each braced bay needs 2 diagonal timbers
        if b.bays:
            # Pick a representative bay for diagonal length
            rep_bay = next(iter(b.bays.values()))
            diag_w = self.grid.modules_to_mm(rep_bay.width_modules)
            diag_d = self.grid.modules_to_mm(rep_bay.depth_modules)
            diag_length = int(math.sqrt(diag_w**2 + diag_d**2))
            self.bom.add(BOMItem(
                category=cat,
                description="Cross-brace diagonal",
                material=f"Softwood",
                size=mat.BRACE_TIMBER.section_label,
                length_or_area=f"{diag_length}mm",
                quantity=2 * n_brace_levels,
                unit="nr",
                location="bracing bay",
            ))

        # Frame bolts — 4 per beam-column joint
        # Joints: each beam end meets a column = 2 joints per beam
        total_beams = sum(
            i.quantity for i in self.bom.items
            if i.category == cat and "Beam" in i.description
        )
        n_frame_bolts = int(total_beams * 2 * 4)  # 2 ends x 4 bolts
        self.bom.add(BOMItem(
            category=cat,
            description=mat.FRAME_BOLT.description,
            material=mat.FRAME_BOLT.type,
            size=mat.FRAME_BOLT.label,
            length_or_area="",
            quantity=n_frame_bolts,
            unit="nr",
            location="all frame joints",
        ))

        # Joist coach bolts — 2 per joist (each end)
        total_joists = sum(
            i.quantity for i in self.bom.items
            if i.category == cat and "joist" in i.description.lower()
            and "bearer" not in i.description.lower()
        )
        self.bom.add(BOMItem(
            category=cat,
            description=mat.JOIST_COACH_BOLT.description,
            material=mat.JOIST_COACH_BOLT.type,
            size=mat.JOIST_COACH_BOLT.label,
            length_or_area="",
            quantity=int(total_joists * 2),
            unit="nr",
            location="all joist connections",
        ))

    # ── 3. Roof ─────────────────────────────────────────────────────────

    def _calc_roof(self) -> None:
        cat = "Roof"

        # OSB/3 structural deck — per bay, laid over joists
        # Replaces woodwool slabs. Warm-roof build: deck then insulation then felt.
        OSB_SHEET_M2 = 2.97  # 2440×1220mm
        footprint_m2 = sum(
            b.floor_area_m2(self.grid) for b in self.building.bays.values()
        )
        for bay_name, bay in self.building.bays.items():
            bay_m2 = bay.floor_area_m2(self.grid)
            self.bom.add(BOMItem(
                category=cat,
                description="Structural roof deck",
                material="OSB/3 18mm",
                size="2440x1220x18mm",
                length_or_area=f"{bay_m2:.1f} m2",
                quantity=math.ceil(bay_m2 / OSB_SHEET_M2),
                unit="sheets",
                location=bay_name,
            ))

        # PIR insulation over deck — entire roof footprint (overhang is uninsulated)
        PIR_BOARD_M2 = 2.88  # 2400×1200mm IKO Enertherm
        self.bom.add(BOMItem(
            category=cat,
            description="Roof insulation",
            material=mat.WALL_PIR_MATERIAL,
            size=f"{mat.WALL_PIR_THICKNESS}mm thick",
            length_or_area=f"{footprint_m2:.1f} m2",
            quantity=math.ceil(footprint_m2 / PIR_BOARD_M2),
            unit="boards",
            location="entire roof",
        ))

        # Felt membrane — total roof area including overhang
        overhang_extra = self._approx_perimeter_m() * (mat.ROOF_OVERHANG / 1000)
        corner_area = 4 * (mat.ROOF_OVERHANG / 1000) ** 2
        total_roof_m2 = footprint_m2 + overhang_extra + corner_area
        for layer in range(1, mat.ROOF_FELT_LAYERS + 1):
            self.bom.add(BOMItem(
                category=cat,
                description=f"Bituminous felt layer {layer}",
                material="Bituminous roofing felt",
                size="roll",
                length_or_area=f"{total_roof_m2:.1f} m2",
                quantity=math.ceil(total_roof_m2 / 10),
                unit="rolls",
                location="entire roof",
            ))

        # Shingle ballast removed — BituBond cold adhesive bonds felt down directly

        # Hot bitumen for bonding felt layers together
        # ~1.5 kg/m2 per layer, 2 bonded interfaces for 3 layers
        bitumen_kg = round(total_roof_m2 * 1.5 * (mat.ROOF_FELT_LAYERS - 1), 1)
        self.bom.add(BOMItem(
            category=cat,
            description="Hot bitumen (felt bonding)",
            material="Bitumen",
            size="",
            length_or_area=f"{total_roof_m2:.1f} m2",
            quantity=bitumen_kg,
            unit="kg",
            location="entire roof",
        ))

        # Fascia — only on perimeter
        perimeter_m = self._approx_perimeter_m()
        # Add overhang perimeter (outer edge of overhang)
        self.bom.add(BOMItem(
            category=cat,
            description=mat.FASCIA_TIMBER.description,
            material="Softwood, treated",
            size=mat.FASCIA_TIMBER.section_label,
            length_or_area=f"{perimeter_m:.1f}m",
            quantity=math.ceil(perimeter_m / 4.8),  # 4.8m standard lengths
            unit="lengths",
            location="roof perimeter",
        ))

        # Upstand
        self.bom.add(BOMItem(
            category=cat,
            description=mat.UPSTAND_TIMBER.description,
            material="Softwood, treated",
            size=mat.UPSTAND_TIMBER.section_label,
            length_or_area=f"{perimeter_m:.1f}m",
            quantity=math.ceil(perimeter_m / 4.8),
            unit="lengths",
            location="roof perimeter",
        ))

        # Edge capping
        self.bom.add(BOMItem(
            category=cat,
            description=mat.CAPPING_TIMBER.description,
            material="Softwood, treated",
            size=mat.CAPPING_TIMBER.section_label,
            length_or_area=f"{perimeter_m:.1f}m",
            quantity=math.ceil(perimeter_m / 4.8),
            unit="lengths",
            location="roof perimeter",
        ))

        # Spacer blocks for capping
        n_spacers = math.ceil(perimeter_m / 0.6)  # one every 600mm
        self.bom.add(BOMItem(
            category=cat,
            description="Capping spacer block",
            material="Softwood offcut",
            size="50x50x25mm",
            length_or_area="",
            quantity=n_spacers,
            unit="nr",
            location="roof perimeter",
        ))

        # Roof outlet
        self.bom.add(BOMItem(
            category=cat,
            description="Roof outlet",
            material="Proprietary roof outlet",
            size="",
            length_or_area="",
            quantity=1,
            unit="nr",
            location="roof overhang",
        ))

    # ── 4. Floors ───────────────────────────────────────────────────────

    def _calc_floors(self) -> None:
        cat = "Floors"

        for bay_name, bay in self.building.bays.items():
            if not bay.enclosed:
                # Open/unenclosed bays get no floor finish or insulation.
                # The ground-level structure (joists, posts) is still built,
                # but the floor is left as gravel or slabs.
                continue

            area_m2 = bay.floor_area_m2(self.grid)
            width_m = bay.width_mm(self.grid) / 1000
            depth_m = bay.depth_mm(self.grid) / 1000

            for storey in range(bay.storeys):
                level = "ground floor" if storey == 0 else "first floor"
                loc = f"{bay_name} ({level})"

                # T&G boards — run along the shorter span direction
                board_width_m = mat.FLOOR_BOARD.width / 1000
                n_boards = math.ceil(width_m / board_width_m)
                board_length = bay.depth_mm(self.grid)
                self.bom.add(BOMItem(
                    category=cat,
                    description=mat.FLOOR_BOARD.description,
                    material="Softwood T&G",
                    size=mat.FLOOR_BOARD.section_label,
                    length_or_area=f"{board_length}mm",
                    quantity=n_boards,
                    unit="nr",
                    location=loc,
                ))

                # Ground floor insulation
                if storey == 0:
                    self.bom.add(BOMItem(
                        category=cat,
                        description="Floor insulation",
                        material=mat.FLOOR_INSULATION_MATERIAL,
                        size=f"{mat.FLOOR_INSULATION_THICKNESS}mm thick",
                        length_or_area=f"{area_m2:.1f} m2",
                        quantity=math.ceil(area_m2 / 1.2),  # ~1.2m2 per pack
                        unit="packs",
                        location=loc,
                    ))

                    # Insulation support panels
                    n_joists = bay.width_modules + 1
                    self.bom.add(BOMItem(
                        category=cat,
                        description="Insulation support panel",
                        material="Plywood strip",
                        size="6mm thick",
                        length_or_area=f"{board_length}mm",
                        quantity=n_joists - 1,  # one between each joist pair
                        unit="nr",
                        location=loc,
                    ))

    # ── 5. External Walls ───────────────────────────────────────────────

    def _calc_external_walls(self) -> None:
        cat = "External Walls"

        for bay_name, bay in self.building.bays.items():
            for face in FACES:
                wt = self.building.get_wall_type(bay_name, face)
                if wt not in (WallType.EXTERNAL, WallType.TOOL_WALL):
                    continue

                modules = bay.face_modules(face)
                loc = f"{bay_name}.{face}"
                panel_h = self.building.storey_height * bay.storeys

                # Outer panels — use per-bay override if provided, else default
                outer = self.outer_panel_specs.get(bay_name, mat.EXTERNAL_PANEL_OUTER)
                self.bom.add(BOMItem(
                    category=cat,
                    description=outer.description,
                    material=outer.material,
                    size=f"{self.grid.panel_width}x{panel_h}x{outer.thickness}mm",
                    length_or_area=f"{self.grid.panel_width * panel_h / 1e6:.2f} m2 ea",
                    quantity=modules,
                    unit="nr",
                    location=loc,
                ))

                # Breather membrane — behind cladding on all external/tool-wall faces
                face_area_m2 = self.grid.modules_to_mm(modules) * panel_h / 1e6
                self.bom.add(BOMItem(
                    category=cat,
                    description="Breather membrane",
                    material=mat.BREATHER_MEMBRANE_MATERIAL,
                    size="1m wide roll",
                    length_or_area=f"{face_area_m2:.2f} m2",
                    quantity=math.ceil(face_area_m2 / mat.BREATHER_MEMBRANE_ROLL_M2),
                    unit="rolls",
                    location=loc,
                ))

                # PIR insulation between posts — enclosed (heated) EXTERNAL walls only.
                # Open/unenclosed bays have cladding for weather protection but no
                # thermal envelope, so insulation is neither needed nor appropriate.
                if wt == WallType.EXTERNAL and bay.enclosed:
                    PIR_BOARD_M2 = 2.88  # 2400×1200mm; 2 cuts of 600mm per board
                    self.bom.add(BOMItem(
                        category=cat,
                        description="Wall insulation",
                        material=mat.WALL_PIR_MATERIAL,
                        size=f"{mat.WALL_PIR_THICKNESS}mm thick",
                        length_or_area=f"{face_area_m2:.2f} m2",
                        quantity=math.ceil(face_area_m2 / PIR_BOARD_M2),
                        unit="boards",
                        location=loc,
                    ))

                # Inner panels — plasterboard for standard walls, OSB for tool walls
                inner = (
                    mat.TOOL_WALL_INNER
                    if wt == WallType.TOOL_WALL
                    else mat.EXTERNAL_PANEL_INNER
                )
                self.bom.add(BOMItem(
                    category=cat,
                    description=inner.description,
                    material=inner.material,
                    size=f"{self.grid.panel_width}x{panel_h}x{inner.thickness}mm",
                    length_or_area=f"{self.grid.panel_width * panel_h / 1e6:.2f} m2 ea",
                    quantity=modules,
                    unit="nr",
                    location=loc,
                ))

                # Sole plate
                face_length = self.grid.modules_to_mm(modules)
                self.bom.add(BOMItem(
                    category=cat,
                    description=mat.SOLE_PLATE.description,
                    material="Softwood",
                    size=mat.SOLE_PLATE.section_label,
                    length_or_area=f"{face_length}mm",
                    quantity=1,
                    unit="nr",
                    location=loc,
                ))

                # Battens and bolts at joint positions
                n_joints = modules + 1  # one at each edge + between panels
                n_battens = n_joints * mat.BATTENS_PER_JOINT
                n_bolts = n_joints * mat.BATTENS_PER_JOINT * mat.BOLTS_PER_BATTEN
                self.bom.add(BOMItem(
                    category=cat,
                    description=mat.WALL_BATTEN.description,
                    material="Softwood",
                    size=mat.WALL_BATTEN.section_label,
                    length_or_area=f"{panel_h}mm",
                    quantity=n_battens,
                    unit="nr",
                    location=loc,
                ))
                self.bom.add(BOMItem(
                    category=cat,
                    description=mat.WALL_BOLT.description,
                    material=mat.WALL_BOLT.type,
                    size=mat.WALL_BOLT.label,
                    length_or_area="",
                    quantity=n_bolts,
                    unit="nr",
                    location=loc,
                ))

                # Grid position blocks (top and bottom at each joint)
                self.bom.add(BOMItem(
                    category=cat,
                    description=mat.WALL_BLOCK.description,
                    material="Softwood offcut",
                    size=mat.WALL_BLOCK.section_label,
                    length_or_area="50mm long",
                    quantity=n_joints * 2,
                    unit="nr",
                    location=loc,
                ))

    # ── 6. Windows ──────────────────────────────────────────────────────

    def _calc_windows(self) -> None:
        cat = "Windows"

        for bay_name, bay in self.building.bays.items():
            for face in FACES:
                wt = self.building.get_wall_type(bay_name, face)
                if wt != WallType.WINDOW:
                    continue

                modules = bay.face_modules(face)
                loc = f"{bay_name}.{face}"
                panel_h = self.building.storey_height * bay.storeys
                pane_h = panel_h // 2  # half-height panes (fixed + sliding)

                # Fixed glass panes (one per module)
                self.bom.add(BOMItem(
                    category=cat,
                    description="Fixed glass pane",
                    material=f"{mat.GLASS_THICKNESS}mm float glass, polished edges",
                    size=f"{self.grid.panel_width}x{pane_h}mm",
                    length_or_area=f"{self.grid.panel_width * pane_h / 1e6:.2f} m2",
                    quantity=modules,
                    unit="nr",
                    location=loc,
                ))

                # Sliding glass panes
                self.bom.add(BOMItem(
                    category=cat,
                    description="Sliding glass pane",
                    material=f"{mat.GLASS_THICKNESS}mm float glass, polished edges",
                    size=f"{self.grid.panel_width}x{pane_h}mm",
                    length_or_area=f"{self.grid.panel_width * pane_h / 1e6:.2f} m2",
                    quantity=modules,
                    unit="nr",
                    location=loc,
                ))

                # Aluminium angle tracks (top and bottom per pane pair)
                track_length = self.grid.panel_width
                self.bom.add(BOMItem(
                    category=cat,
                    description="Aluminium angle track",
                    material=mat.WINDOW_ALUMINIUM_ANGLE,
                    size="25x25x3mm",
                    length_or_area=f"{track_length}mm",
                    quantity=modules * 2,  # top + bottom per module
                    unit="nr",
                    location=loc,
                ))

                # Spacer washers (2 per track)
                self.bom.add(BOMItem(
                    category=cat,
                    description="Track spacer washer",
                    material="Aluminium",
                    size="",
                    length_or_area="",
                    quantity=modules * 2 * 2,
                    unit="nr",
                    location=loc,
                ))

                # Window lining timber
                lining_perimeter = 2 * (self.grid.panel_width + panel_h)
                self.bom.add(BOMItem(
                    category=cat,
                    description=mat.WINDOW_LINING.description,
                    material="Softwood",
                    size=mat.WINDOW_LINING.section_label,
                    length_or_area=f"{lining_perimeter}mm",
                    quantity=modules,
                    unit="sets",
                    location=loc,
                ))

                # Glazing beads (4 per pane, 2 panes per module)
                self.bom.add(BOMItem(
                    category=cat,
                    description=mat.WINDOW_BEAD.description,
                    material="Softwood",
                    size=mat.WINDOW_BEAD.section_label,
                    length_or_area="",
                    quantity=modules * 2 * 4,
                    unit="nr",
                    location=loc,
                ))

                # Window wall still needs sole plate + battens at edges
                face_length = self.grid.modules_to_mm(modules)
                self.bom.add(BOMItem(
                    category=cat,
                    description=mat.SOLE_PLATE.description,
                    material="Softwood",
                    size=mat.SOLE_PLATE.section_label,
                    length_or_area=f"{face_length}mm",
                    quantity=1,
                    unit="nr",
                    location=loc,
                ))

    # ── 7. Partitions ───────────────────────────────────────────────────

    def _calc_partitions(self) -> None:
        cat = "Partitions"

        for bay_name, bay in self.building.bays.items():
            for face in FACES:
                wt = self.building.get_wall_type(bay_name, face)
                if wt != WallType.PARTITION:
                    continue

                # Only count each shared partition once: if both sides
                # resolve to PARTITION, only count from the first alphabetically
                conn_info = self.building.get_connection(bay_name, face)
                if conn_info:
                    conn_bay, conn_face = conn_info
                    other_wt = self.building.get_wall_type(conn_bay, conn_face)
                    if other_wt == WallType.PARTITION and conn_bay < bay_name:
                        continue

                modules = bay.face_modules(face)
                loc = f"{bay_name}.{face}"
                panel_h = self.building.storey_height  # partitions are per-storey

                # Core (woodwool)
                self.bom.add(BOMItem(
                    category=cat,
                    description=mat.PARTITION_CORE.description,
                    material=mat.PARTITION_CORE.material,
                    size=f"{self.grid.panel_width}x{panel_h}x{mat.PARTITION_CORE.thickness}mm",
                    length_or_area=f"{self.grid.panel_width * panel_h / 1e6:.2f} m2 ea",
                    quantity=modules * bay.storeys,
                    unit="nr",
                    location=loc,
                ))

                # Plasterboard both sides
                self.bom.add(BOMItem(
                    category=cat,
                    description=mat.PARTITION_FINISH.description,
                    material=mat.PARTITION_FINISH.material,
                    size=f"{self.grid.panel_width}x{panel_h}x{mat.PARTITION_FINISH.thickness}mm",
                    length_or_area=f"{self.grid.panel_width * panel_h / 1e6:.2f} m2 ea",
                    quantity=modules * 2 * bay.storeys,  # x2 for both sides
                    unit="nr",
                    location=loc,
                ))

                # Sole plate
                face_length = self.grid.modules_to_mm(modules)
                self.bom.add(BOMItem(
                    category=cat,
                    description=mat.SOLE_PLATE.description,
                    material="Softwood",
                    size=mat.SOLE_PLATE.section_label,
                    length_or_area=f"{face_length}mm",
                    quantity=bay.storeys,
                    unit="nr",
                    location=loc,
                ))

                # Battens and bolts
                n_joints = modules + 1
                n_battens = n_joints * mat.BATTENS_PER_JOINT
                n_bolts = n_joints * mat.BATTENS_PER_JOINT * mat.BOLTS_PER_BATTEN
                self.bom.add(BOMItem(
                    category=cat,
                    description=mat.WALL_BATTEN.description,
                    material="Softwood",
                    size=mat.WALL_BATTEN.section_label,
                    length_or_area=f"{panel_h}mm",
                    quantity=n_battens * bay.storeys,
                    unit="nr",
                    location=loc,
                ))
                self.bom.add(BOMItem(
                    category=cat,
                    description=mat.WALL_BOLT.description,
                    material=mat.WALL_BOLT.type,
                    size=mat.WALL_BOLT.label,
                    length_or_area="",
                    quantity=n_bolts * bay.storeys,
                    unit="nr",
                    location=loc,
                ))

    # ── 8. Ceilings ─────────────────────────────────────────────────────

    def _calc_ceilings(self) -> None:
        cat = "Ceilings"

        for bay_name, bay in self.building.bays.items():
            if not bay.enclosed:
                # Open bays have exposed roof joists — no ceiling finish.
                continue

            for storey in range(bay.storeys):
                level = "ground floor" if storey == 0 else "first floor"
                loc = f"{bay_name} ({level})"

                # Plasterboard ceiling panels — 600mm wide, cut to joist spacing
                board_length = self.grid.module_pitch  # fits between joists
                n_boards_across = bay.depth_modules
                n_boards_along = bay.width_modules
                n_boards = n_boards_across * n_boards_along

                self.bom.add(BOMItem(
                    category=cat,
                    description="Ceiling board",
                    material=mat.CEILING_BOARD.material,
                    size=f"{self.grid.panel_width}x{board_length}x{mat.CEILING_BOARD.thickness}mm",
                    length_or_area=f"{n_boards * self.grid.panel_width * board_length / 1e6:.1f} m2",
                    quantity=n_boards,
                    unit="nr",
                    location=loc,
                ))

                # Ceiling battens
                # One batten at each board edge along the joist direction
                n_battens = (n_boards_along + 1) * n_boards_across
                self.bom.add(BOMItem(
                    category=cat,
                    description=mat.CEILING_BATTEN.description,
                    material="Softwood",
                    size=mat.CEILING_BATTEN.section_label,
                    length_or_area=f"{board_length}mm",
                    quantity=n_battens,
                    unit="nr",
                    location=loc,
                ))

                # Fire lining for ceiling below first floor (in 2-storey bays)
                if storey == 0 and bay.storeys == 2:
                    n_joists = bay.width_modules + 1
                    joist_span = self.grid.modules_to_mm(bay.depth_modules)
                    self.bom.add(BOMItem(
                        category=cat,
                        description=mat.FIRE_LINING.description,
                        material="Softwood (sacrificial)",
                        size=mat.FIRE_LINING.section_label,
                        length_or_area=f"{joist_span}mm",
                        quantity=n_joists,
                        unit="nr",
                        location=loc,
                    ))

    # ── 9. Fixings Summary ──────────────────────────────────────────────

    def _calc_fixings(self) -> None:
        cat = "Fixings & Sundries"

        # Count total boards (external + partition + ceiling panels)
        total_boards = 0
        for item in self.bom.items:
            if item.unit == "nr" and any(
                kw in item.description.lower()
                for kw in ("panel", "board", "finish", "core")
            ):
                total_boards += int(item.quantity) if item.quantity > 0 else 0

        # Board-fixing screws
        n_board_screws = total_boards * mat.SCREWS_PER_BOARD
        self.bom.add(BOMItem(
            category=cat,
            description="Board fixing screws",
            material="No. 8 x 50mm zinc screws",
            size="No. 8 x 50mm",
            length_or_area="",
            quantity=n_board_screws,
            unit="nr",
            location="all boards",
        ))
        # Sell in boxes of 200
        self.bom.add(BOMItem(
            category=cat,
            description="Board fixing screws (boxes)",
            material="No. 8 x 50mm zinc screws",
            size="box of 200",
            length_or_area="",
            quantity=math.ceil(n_board_screws / 200),
            unit="boxes",
            location="all boards",
        ))

        # Count total battens
        total_battens = sum(
            int(i.quantity) for i in self.bom.items
            if "batten" in i.description.lower() and i.quantity > 0
        )
        n_batten_screws = total_battens * mat.SCREWS_PER_BATTEN
        self.bom.add(BOMItem(
            category=cat,
            description="Batten fixing screws",
            material="No. 10 x 75mm zinc screws",
            size="No. 10 x 75mm",
            length_or_area="",
            quantity=n_batten_screws,
            unit="nr",
            location="all battens",
        ))
        self.bom.add(BOMItem(
            category=cat,
            description="Batten fixing screws (boxes)",
            material="No. 10 x 75mm zinc screws",
            size="box of 200",
            length_or_area="",
            quantity=math.ceil(n_batten_screws / 200),
            unit="boxes",
            location="all battens",
        ))

        # Preservative wood stain for external timber
        # Estimate external timber area: fascia + battens + sole plates
        ext_faces = [
            (bn, f) for bn, f, wt in self.building.all_faces()
            if wt in (WallType.EXTERNAL, WallType.WINDOW, WallType.NONE)
        ]
        ext_timber_m2 = 0.0
        for bay_name, face in ext_faces:
            bay = self.building.bays[bay_name]
            modules = bay.face_modules(face)
            n_joints = modules + 1
            # Battens: n_joints * 3 battens * height * 50mm width
            panel_h = self.building.storey_height * bay.storeys
            ext_timber_m2 += n_joints * 3 * (panel_h / 1000) * (0.05)
        # Add fascia area
        perimeter_m = self._approx_perimeter_m()
        ext_timber_m2 += perimeter_m * (mat.FASCIA_TIMBER.depth / 1000)

        stain_litres = math.ceil(ext_timber_m2 / mat.STAIN_COVERAGE_M2_PER_LITRE)
        if stain_litres > 0:
            self.bom.add(BOMItem(
                category=cat,
                description="Preservative wood stain",
                material="Exterior wood stain",
                size="",
                length_or_area=f"{ext_timber_m2:.1f} m2 coverage",
                quantity=stain_litres,
                unit="litres",
                location="all external timber",
            ))

    # ── Helpers ──────────────────────────────────────────────────────────

    def _approx_perimeter_m(self) -> float:
        """Approximate building perimeter from bay dimensions.

        Sums the lengths of all perimeter faces (faces not connected
        to another bay).
        """
        total_mm = 0
        for bay_name, face in self.building.perimeter_faces():
            bay = self.building.bays[bay_name]
            modules = bay.face_modules(face)
            total_mm += self.grid.modules_to_mm(modules)
        return total_mm / 1000
