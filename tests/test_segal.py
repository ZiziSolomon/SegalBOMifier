"""Tests for the Segal Method BOM generator."""

import math
import os
import tempfile

import pytest

from segal_method import SegalGrid, SegalBuilding, WallType, BOMCalculator, BOM


# ── Grid tests ──────────────────────────────────────────────────────────────


class TestSegalGrid:
    def test_default_values(self):
        grid = SegalGrid()
        assert grid.panel_width == 600
        assert grid.structural_thickness == 50

    def test_module_pitch(self):
        grid = SegalGrid(panel_width=600, structural_thickness=50)
        assert grid.module_pitch == 650

    def test_module_pitch_1200(self):
        grid = SegalGrid(panel_width=1200, structural_thickness=50)
        assert grid.module_pitch == 1250

    def test_modules_to_mm(self):
        grid = SegalGrid(panel_width=600, structural_thickness=50)
        assert grid.modules_to_mm(1) == 650
        assert grid.modules_to_mm(4) == 2600

    def test_invalid_panel_width(self):
        with pytest.raises(ValueError):
            SegalGrid(panel_width=0)

    def test_invalid_structural_thickness(self):
        with pytest.raises(ValueError):
            SegalGrid(structural_thickness=-10)

    def test_face_modules(self):
        grid = SegalGrid()
        assert grid.face_modules(3, 5, "north") == 3
        assert grid.face_modules(3, 5, "south") == 3
        assert grid.face_modules(3, 5, "east") == 5
        assert grid.face_modules(3, 5, "west") == 5

    def test_face_modules_invalid(self):
        grid = SegalGrid()
        with pytest.raises(ValueError):
            grid.face_modules(3, 5, "up")


# ── Building topology tests ─────────────────────────────────────────────────


class TestBay:
    def test_creation(self):
        grid = SegalGrid()
        b = SegalBuilding(grid)
        bay = b.add_bay("kitchen", width=4, depth=3)
        assert bay.name == "kitchen"
        assert bay.width_modules == 4
        assert bay.depth_modules == 3
        assert bay.storeys == 1

    def test_dimensions(self):
        grid = SegalGrid(panel_width=600, structural_thickness=50)
        b = SegalBuilding(grid)
        bay = b.add_bay("room", width=4, depth=3)
        assert bay.width_mm(grid) == 4 * 650
        assert bay.depth_mm(grid) == 3 * 650

    def test_floor_area(self):
        grid = SegalGrid(panel_width=600, structural_thickness=50)
        b = SegalBuilding(grid)
        bay = b.add_bay("room", width=4, depth=4)
        area = bay.floor_area_m2(grid)
        expected = (4 * 0.65) * (4 * 0.65)  # 2.6m x 2.6m = 6.76 m2
        assert abs(area - expected) < 0.01

    def test_invalid_modules(self):
        grid = SegalGrid()
        b = SegalBuilding(grid)
        with pytest.raises(ValueError):
            b.add_bay("bad", width=0, depth=3)

    def test_invalid_storeys(self):
        grid = SegalGrid()
        b = SegalBuilding(grid)
        with pytest.raises(ValueError):
            b.add_bay("bad", width=3, depth=3, storeys=3)

    def test_duplicate_name(self):
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("kitchen", width=3, depth=3)
        with pytest.raises(ValueError):
            b.add_bay("kitchen", width=4, depth=4)


class TestBuilding:
    def _make_two_bay_building(self):
        """Helper: two 4x4 bays connected east-west."""
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("left", width=4, depth=4)
        b.add_bay("right", width=3, depth=4)
        b.connect("left", "east", "right", "west")
        return b

    def test_connect(self):
        b = self._make_two_bay_building()
        assert b.is_connected("left", "east")
        assert b.is_connected("right", "west")
        assert not b.is_connected("left", "north")

    def test_connect_mismatched_lengths(self):
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("a", width=4, depth=4)
        b.add_bay("b", width=3, depth=3)  # depth differs
        with pytest.raises(ValueError, match="lengths must match"):
            b.connect("a", "east", "b", "west")

    def test_connect_nonexistent_bay(self):
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("a", width=4, depth=4)
        with pytest.raises(ValueError, match="not found"):
            b.connect("a", "east", "ghost", "west")

    def test_double_connect_same_face(self):
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("a", width=4, depth=4)
        b.add_bay("b", width=3, depth=4)
        b.add_bay("c", width=2, depth=4)
        b.connect("a", "east", "b", "west")
        with pytest.raises(ValueError, match="already connected"):
            b.connect("a", "east", "c", "west")

    def test_auto_wall_types(self):
        b = self._make_two_bay_building()
        # Connected faces default to OPEN
        assert b.get_wall_type("left", "east") == WallType.OPEN
        assert b.get_wall_type("right", "west") == WallType.OPEN
        # Perimeter faces default to EXTERNAL
        assert b.get_wall_type("left", "north") == WallType.EXTERNAL
        assert b.get_wall_type("right", "south") == WallType.EXTERNAL

    def test_wall_override(self):
        b = self._make_two_bay_building()
        b.set_wall("left", "east", WallType.PARTITION)
        assert b.get_wall_type("left", "east") == WallType.PARTITION
        b.set_wall("left", "north", WallType.WINDOW)
        assert b.get_wall_type("left", "north") == WallType.WINDOW

    def test_unique_post_count_single_bay(self):
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("solo", width=4, depth=4)
        assert b.unique_post_count() == 4

    def test_unique_post_count_two_connected(self):
        b = self._make_two_bay_building()
        # 2 bays x 4 posts = 8, minus 2 shared = 6
        assert b.unique_post_count() == 6

    def test_unique_post_count_four_bays(self):
        """Four bays in a 2x2 grid, all connected."""
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("nw", width=4, depth=4)
        b.add_bay("ne", width=3, depth=4)
        b.add_bay("sw", width=4, depth=3)
        b.add_bay("se", width=3, depth=3)
        b.connect("nw", "east", "ne", "west")
        b.connect("nw", "south", "sw", "north")
        b.connect("ne", "south", "se", "north")
        b.connect("sw", "east", "se", "west")
        # 4*4=16 posts, minus 4 connections * 2 = 8
        assert b.unique_post_count() == 8

    def test_perimeter_faces(self):
        b = self._make_two_bay_building()
        perimeter = b.perimeter_faces()
        # 2 bays x 4 faces = 8, minus 2 connected = 6 perimeter faces
        assert len(perimeter) == 6
        # left.east and right.west should NOT be in perimeter
        assert ("left", "east") not in perimeter
        assert ("right", "west") not in perimeter

    def test_interior_frame_line(self):
        b = self._make_two_bay_building()
        assert b.is_interior_frame_line("left", "east")
        assert not b.is_interior_frame_line("left", "north")

    def test_get_connected_bay(self):
        b = self._make_two_bay_building()
        assert b.get_connected_bay("left", "east") == "right"
        assert b.get_connected_bay("right", "west") == "left"
        assert b.get_connected_bay("left", "north") is None

    def test_post_height(self):
        grid = SegalGrid()
        b = SegalBuilding(grid, storey_height=2400, ground_clearance=450)
        b.add_bay("single", width=4, depth=4, storeys=1)
        b.add_bay("double", width=4, depth=4, storeys=2)
        assert b.post_height_mm("single") == 450 + 2400
        assert b.post_height_mm("double") == 450 + 4800


# ── BOM tests ───────────────────────────────────────────────────────────────


class TestBOM:
    def _single_bay_bom(self) -> BOM:
        grid = SegalGrid(panel_width=600, structural_thickness=50)
        b = SegalBuilding(grid, storey_height=2400, ground_clearance=450)
        b.add_bay("room", width=4, depth=4)
        return b.generate_bom()

    def _four_bay_bom(self) -> BOM:
        grid = SegalGrid(panel_width=600, structural_thickness=50)
        b = SegalBuilding(grid, storey_height=2400, ground_clearance=450)
        b.add_bay("nw", width=4, depth=4)
        b.add_bay("ne", width=3, depth=4)
        b.add_bay("sw", width=4, depth=3)
        b.add_bay("se", width=3, depth=3)
        b.connect("nw", "east", "ne", "west")
        b.connect("nw", "south", "sw", "north")
        b.connect("ne", "south", "se", "north")
        b.connect("sw", "east", "se", "west")
        b.set_wall("ne", "east", WallType.WINDOW)
        b.set_wall("nw", "east", WallType.PARTITION)
        return b.generate_bom()

    def test_bom_has_all_categories(self):
        bom = self._single_bay_bom()
        cats = bom.categories
        assert "Foundations" in cats
        assert "Structural Frame" in cats
        assert "Roof" in cats
        assert "Floors" in cats
        assert "External Walls" in cats
        assert "Ceilings" in cats
        assert "Fixings & Sundries" in cats

    def test_foundation_post_count(self):
        bom = self._single_bay_bom()
        pads = [i for i in bom.items if i.description == "Concrete foundation pad"]
        assert len(pads) == 1
        assert pads[0].quantity == 4  # 4 corners

    def test_external_wall_panels(self):
        """Single bay with 4 external faces of 4 modules each."""
        bom = self._single_bay_bom()
        outer = [
            i for i in bom.items
            if i.description == "External weatherproof panel"
        ]
        # 4 faces, each with 4 modules = 16 panels total
        total = sum(i.quantity for i in outer)
        assert total == 16

    def test_windows_appear(self):
        bom = self._four_bay_bom()
        glass = [i for i in bom.items if "glass" in i.description.lower()]
        assert len(glass) > 0  # we set ne.east to WINDOW

    def test_partitions_appear(self):
        bom = self._four_bay_bom()
        partitions = [i for i in bom.items if i.category == "Partitions"]
        assert len(partitions) > 0  # we set nw.east to PARTITION

    def test_connected_bays_share_posts(self):
        """Two connected bays should have fewer posts than two isolated ones."""
        grid = SegalGrid()

        b1 = SegalBuilding(grid)
        b1.add_bay("a", width=4, depth=4)
        b1.add_bay("b", width=4, depth=4)
        # Not connected: 8 posts total

        b2 = SegalBuilding(grid)
        b2.add_bay("a", width=4, depth=4)
        b2.add_bay("b", width=4, depth=4)
        b2.connect("a", "east", "b", "west")
        # Connected: 6 posts

        assert b1.unique_post_count() > b2.unique_post_count()

    def test_to_table_output(self):
        bom = self._single_bay_bom()
        table = bom.to_table()
        assert "SCHEDULE OF MATERIALS" in table
        assert "FOUNDATIONS" in table.upper()
        assert "STRUCTURAL FRAME" in table.upper()

    def test_to_csv(self):
        bom = self._single_bay_bom()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as f:
            path = f.name
        try:
            bom.to_csv(path)
            with open(path) as f:
                content = f.read()
            assert "Category" in content
            assert "Foundations" in content
        finally:
            os.unlink(path)

    def test_to_dicts(self):
        bom = self._single_bay_bom()
        dicts = bom.to_dicts()
        assert len(dicts) > 0
        assert "category" in dicts[0]
        assert "quantity" in dicts[0]

    def test_unenclosed_bay_omits_floor_and_ceiling(self):
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("open", width=4, depth=4, enclosed=False)
        bom = b.generate_bom()
        # No floor boards, insulation, or ceiling boards for open bay
        floor_items = [i for i in bom.items if i.category == "Floors"]
        ceiling_items = [i for i in bom.items if i.category == "Ceilings"]
        assert len(floor_items) == 0
        assert len(ceiling_items) == 0

    def test_unenclosed_bay_has_no_wall_insulation(self):
        """Open bays have cladding for weather but no thermal envelope — no PIR."""
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("open", width=4, depth=4, enclosed=False)
        bom = b.generate_bom()
        wall_items = [i for i in bom.items if i.category == "External Walls"]
        insulation = [
            i for i in wall_items
            if "PIR" in i.material or "Enertherm" in i.material
        ]
        assert len(insulation) == 0, "Open bays should not have wall PIR insulation"

    def test_enclosed_bay_still_has_wall_insulation(self):
        """Enclosed bays should still get PIR in external walls (thermal envelope)."""
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("room", width=4, depth=4, enclosed=True)
        bom = b.generate_bom()
        wall_items = [i for i in bom.items if i.category == "External Walls"]
        insulation = [
            i for i in wall_items
            if "PIR" in i.material or "Enertherm" in i.material
        ]
        assert len(insulation) > 0, "Enclosed bays should have wall PIR insulation"

    def test_unenclosed_bay_still_has_roof(self):
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("open", width=4, depth=4, enclosed=False)
        bom = b.generate_bom()
        roof_items = [i for i in bom.items if i.category == "Roof"]
        assert len(roof_items) > 0

    def test_two_storey_has_fire_lining(self):
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("house", width=4, depth=4, storeys=2)
        bom = b.generate_bom()
        fire = [i for i in bom.items if "fire" in i.description.lower()]
        assert len(fire) > 0

    def test_bom_item_count_scales(self):
        """More bays should produce more BOM items."""
        grid = SegalGrid()

        b1 = SegalBuilding(grid)
        b1.add_bay("a", width=4, depth=4)
        bom1 = b1.generate_bom()

        b2 = SegalBuilding(grid)
        b2.add_bay("a", width=4, depth=4)
        b2.add_bay("b", width=4, depth=4)
        bom2 = b2.generate_bom()

        assert len(bom2.items) > len(bom1.items)

    def test_no_foundations(self):
        """include_foundations=False should produce no Foundations items."""
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("room", width=4, depth=4)
        bom = b.generate_bom(include_foundations=False)
        found = [i for i in bom.items if i.category == "Foundations"]
        assert len(found) == 0

    def test_foundations_included_by_default(self):
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("room", width=4, depth=4)
        bom = b.generate_bom()
        found = [i for i in bom.items if i.category == "Foundations"]
        assert len(found) > 0

    def test_tool_wall_uses_osb_inner(self):
        """TOOL_WALL faces should specify OSB, not plasterboard, on the inner leaf."""
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("store", width=4, depth=4)
        b.set_wall("store", "north", WallType.TOOL_WALL)
        bom = b.generate_bom()
        wall_items = [i for i in bom.items if i.category == "External Walls"
                      and i.location == "store.north"]
        materials = [i.material for i in wall_items]
        assert any("OSB" in m for m in materials), "Expected OSB on tool wall inner"
        assert not any("Plasterboard" in m for m in materials), \
            "Plasterboard should not appear on a tool wall"

    def test_tool_wall_has_outer_and_breather_membrane(self):
        """TOOL_WALL should have fibre cement outer + breather membrane; no woodwool."""
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("store", width=4, depth=4)
        b.set_wall("store", "north", WallType.TOOL_WALL)
        bom = b.generate_bom()
        wall_items = [i for i in bom.items if i.category == "External Walls"
                      and i.location == "store.north"]
        materials = [i.material for i in wall_items]
        descs = [i.description for i in wall_items]
        assert any("fibre cement" in m.lower() for m in materials), "Missing outer fibre cement"
        assert any("breather" in m.lower() for m in materials), "Missing breather membrane"
        assert not any("Woodwool" in m for m in materials), "Woodwool should be removed"

    def test_external_wall_has_pir_insulation(self):
        """EXTERNAL walls should have PIR insulation; TOOL_WALL should not."""
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("room", width=4, depth=4)
        b.set_wall("room", "north", WallType.TOOL_WALL)
        bom = b.generate_bom()

        south_items = [i for i in bom.items if i.category == "External Walls"
                       and i.location == "room.south"]
        north_items = [i for i in bom.items if i.category == "External Walls"
                       and i.location == "room.north"]

        assert any("PIR" in i.material or "Enertherm" in i.material for i in south_items), \
            "EXTERNAL wall should have PIR insulation"
        assert not any("PIR" in i.material or "Enertherm" in i.material for i in north_items), \
            "TOOL_WALL should not have PIR insulation"

    def test_roof_uses_osb_deck_and_pir(self):
        """Roof should have OSB deck and PIR insulation; no woodwool or shingle."""
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("room", width=4, depth=4)
        bom = b.generate_bom()
        roof_items = [i for i in bom.items if i.category == "Roof"]
        roof_mats = [i.material for i in roof_items]
        roof_descs = [i.description for i in roof_items]
        assert any("OSB" in m for m in roof_mats), "Roof should have OSB deck"
        assert any("PIR" in m or "Enertherm" in m for m in roof_mats), \
            "Roof should have PIR insulation"
        assert not any("Woodwool" in m for m in roof_mats), "Woodwool deck should be gone"
        assert not any("shingle" in d.lower() for d in roof_descs), \
            "Shingle ballast should be removed"

    def test_standard_external_wall_unchanged(self):
        """EXTERNAL faces should still use plasterboard, not OSB."""
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("room", width=4, depth=4)
        # All faces default to EXTERNAL — check north face
        bom = b.generate_bom()
        wall_items = [i for i in bom.items if i.category == "External Walls"
                      and i.location == "room.north"]
        materials = [i.material for i in wall_items]
        assert any("Plasterboard" in m for m in materials), \
            "Standard external wall should use plasterboard"
        assert not any("OSB" in m for m in materials), \
            "OSB should not appear on a standard external wall"


# ── Pricing tests ───────────────────────────────────────────────────────────


from segal_method.pricing import (
    Pricer, PriceEstimate, PriceLine,
    _cheapest_timber, _cheapest_pack,
    _parse_length_mm, _parse_area_m2,
)


class TestPricingHelpers:
    """Unit tests for the low-level optimisation helpers."""

    LENGTHS = [
        {"length_m": 3.0, "price_ex_vat": 9.00, "price_inc_vat": 10.80},
        {"length_m": 4.8, "price_ex_vat": 13.00, "price_inc_vat": 15.60},
        {"length_m": 6.0, "price_ex_vat": 15.00, "price_inc_vat": 18.00},
    ]

    def test_single_piece_fits_shortest(self):
        # 1 piece of 2850mm → fits in 3.0m (floor(3000/2850)=1), cost = 9.00
        ex, inc, note = _cheapest_timber(1, 2850, self.LENGTHS)
        assert ex == 9.00
        assert "3.0m" in note

    def test_two_pieces_one_stock(self):
        # 2 pieces of 1400mm → 3.0m gives floor(3000/1400)=2 per length → 1 length needed
        ex, inc, note = _cheapest_timber(2, 1400, self.LENGTHS)
        assert ex == 9.00
        assert "1×" in note

    def test_piece_too_long_for_short_stock(self):
        # 1 piece of 3100mm → only 4.8m and 6.0m can fill it
        ex, inc, note = _cheapest_timber(1, 3100, self.LENGTHS)
        assert ex == 13.00  # cheapest is 4.8m
        assert "4.8m" in note

    def test_zero_pieces(self):
        ex, inc, note = _cheapest_timber(0, 2400, self.LENGTHS)
        assert ex == 0.0

    def test_pick_cheapest_not_fewest(self):
        # 3 pieces of 2000mm:
        #  - 3.0m: floor(3000/2000)=1 per, need 3 lengths = 3×9 = 27
        #  - 4.8m: floor(4800/2000)=2 per, need 2 lengths = 2×13 = 26  ← cheaper
        #  - 6.0m: floor(6000/2000)=3 per, need 1 length  = 1×15 = 15  ← cheapest
        ex, inc, note = _cheapest_timber(3, 2000, self.LENGTHS)
        assert ex == 15.00
        assert "6.0m" in note

    PACKS = [
        {"qty": 4,  "price_ex_vat": 4.50,  "price_inc_vat": 5.40},
        {"qty": 10, "price_ex_vat": 9.00,  "price_inc_vat": 10.80},
    ]

    def test_pack_single_small(self):
        # 1 bolt → cheapest is 1× pack-4 = 4.50
        ex, inc, note = _cheapest_pack(1, self.PACKS)
        assert ex == 4.50
        assert "pack-4" in note

    def test_pack_five_bolts(self):
        # 5 bolts: 2× pack-4 = 9.00, 1× pack-10 = 9.00 → tie, pack-4 wins (first)
        ex, inc, note = _cheapest_pack(5, self.PACKS)
        assert ex == 9.00

    def test_pack_eleven_bolts(self):
        # 11 bolts: 3× pack-4 = 13.50, 2× pack-10 = 18.00 → pack-4 wins
        ex, inc, note = _cheapest_pack(11, self.PACKS)
        assert ex == 13.50
        assert "pack-4" in note

    def test_parse_length_mm(self):
        assert _parse_length_mm("2850mm") == 2850
        assert _parse_length_mm("3676mm") == 3676
        assert _parse_length_mm("2600mm") == 2600
        assert _parse_length_mm("6.8 m2") is None
        assert _parse_length_mm("") is None

    def test_parse_area_m2(self):
        assert _parse_area_m2("34.2 m2") == pytest.approx(34.2)
        assert _parse_area_m2("6.8 m2") == pytest.approx(6.8)
        assert _parse_area_m2("2600mm") is None


class TestPricerIntegration:
    """Integration tests: price a known BOM and check key outputs."""

    @pytest.fixture
    def simple_bom(self):
        """A minimal single-bay enclosed building."""
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("room", width=4, depth=4, enclosed=True)
        return b.generate_bom(include_foundations=False)

    def test_price_returns_estimate(self, simple_bom):
        estimate = simple_bom.price()
        assert isinstance(estimate, PriceEstimate)

    def test_has_priced_lines(self, simple_bom):
        estimate = simple_bom.price()
        assert len(estimate.priced) > 0

    def test_total_is_positive(self, simple_bom):
        estimate = simple_bom.price()
        assert estimate.total_ex_vat > 0
        assert estimate.total_inc_vat > estimate.total_ex_vat  # VAT adds money

    def test_inc_vat_is_ex_vat_plus_twenty_percent(self, simple_bom):
        estimate = simple_bom.price()
        ratio = estimate.total_inc_vat / estimate.total_ex_vat
        # Selco catalogue is all 20% VAT; ratio won't be exact due to rounding
        assert 1.18 < ratio < 1.22

    def test_frame_bolts_priced(self, simple_bom):
        estimate = simple_bom.price()
        bolt_lines = [p for p in estimate.priced if "bolt" in p.description.lower()]
        assert len(bolt_lines) > 0, "Frame bolts should be priced"

    def test_structural_timber_priced(self, simple_bom):
        estimate = simple_bom.price()
        timber_lines = [
            p for p in estimate.priced
            if any(k in p.description.lower() for k in ("post", "beam", "joist"))
        ]
        assert len(timber_lines) > 0, "Structural timber should be priced"

    def test_pir_insulation_priced(self, simple_bom):
        estimate = simple_bom.price()
        pir_lines = [p for p in estimate.priced if "insulation" in p.description.lower()]
        assert len(pir_lines) > 0, "PIR insulation should be priced"

    def test_roofing_felt_priced(self, simple_bom):
        estimate = simple_bom.price()
        felt_lines = [p for p in estimate.priced if "felt layer" in p.description.lower()]
        assert len(felt_lines) == 3, "All three felt layers should be priced"

    def test_cap_sheet_vs_underlay(self, simple_bom):
        estimate = simple_bom.price()
        lines_by_desc = {p.description: p for p in estimate.priced}
        cap = next((p for p in estimate.priced if "layer 3" in p.description.lower()), None)
        underlay = next((p for p in estimate.priced if "layer 1" in p.description.lower()), None)
        assert cap is not None and underlay is not None
        # Cap sheet (£42.61/roll) costs more than underlay (£21.00/roll) per roll
        assert cap.unit_price_ex > underlay.unit_price_ex

    def test_breather_membrane_is_priced(self, simple_bom):
        estimate = simple_bom.price()
        priced_descs = [p.description.lower() for p in estimate.priced]
        assert any("breather" in d for d in priced_descs), \
            "Breather membrane should now be priced via specialist supplier catalogue"

    def test_to_table_returns_string(self, simple_bom):
        estimate = simple_bom.price()
        table = estimate.to_table()
        assert isinstance(table, str)
        assert "COST ESTIMATE" in table
        assert "TOTAL" in table

    def test_to_csv_writes_file(self, simple_bom, tmp_path):
        estimate = simple_bom.price()
        path = str(tmp_path / "estimate.csv")
        estimate.to_csv(path)
        import csv
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        assert rows[0][0] == "Category"
        assert any("TOTAL" in r[1] for r in rows if len(r) > 1)

    def test_tool_wall_osb_priced(self):
        """OSB panels (tool wall) should be priced as Selco OSB sheets."""
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("workshop", width=4, depth=4, enclosed=False)
        b.set_wall("workshop", "north", WallType.TOOL_WALL)
        bom = b.generate_bom(include_foundations=False)
        estimate = bom.price()
        osb_lines = [p for p in estimate.priced if "osb" in p.qty_purchased.lower()
                     or "tool-wall" in p.description.lower()]
        # The inner face OSB should be priced
        inner_lines = [
            p for p in estimate.priced
            if "tool-wall" in p.description.lower() or "inner face" in p.description.lower()
        ]
        assert len(inner_lines) > 0, "Tool-wall OSB should be priced"


# ── BOM consolidation tests ──────────────────────────────────────────────────


class TestBOMConsolidate:
    def _three_bay_bom(self):
        grid = SegalGrid()
        b = SegalBuilding(grid)
        b.add_bay("a", width=4, depth=4, enclosed=True)
        b.add_bay("b", width=4, depth=4, enclosed=True)
        b.add_bay("c", width=4, depth=4, enclosed=True)
        b.connect("a", "east", "b", "west")
        b.connect("b", "east", "c", "west")
        return b.generate_bom(include_foundations=False)

    def test_consolidate_reduces_item_count(self):
        bom = self._three_bay_bom()
        consolidated = bom.consolidate()
        assert len(consolidated.items) < len(bom.items)

    def test_consolidate_sums_count_items(self):
        """Identical-length timber from multiple bays should merge into one line."""
        bom = self._three_bay_bom()
        consolidated = bom.consolidate()
        joists = [
            i for i in consolidated.items
            if "joist" in i.description.lower() and "bearer" not in i.description.lower()
        ]
        # All three bays have joists at the same span — must be one consolidated line
        assert len(joists) == 1
        assert joists[0].quantity == 30  # 3 bays × 10 joists each

    def test_consolidate_sums_area_items(self):
        """OSB deck areas from multiple bays should be summed in length_or_area."""
        bom = self._three_bay_bom()
        consolidated = bom.consolidate()
        osb = [i for i in consolidated.items if i.description == "Structural roof deck"]
        assert len(osb) == 1
        # 3 bays × 6.8 m² = 20.4 m²
        assert "20.4" in osb[0].length_or_area

    def test_consolidate_different_lengths_stay_separate(self):
        """Timbers at different lengths must not be merged."""
        grid = SegalGrid()
        b = SegalBuilding(grid)
        # Two isolated bays with different depths so joists are different lengths
        b.add_bay("a", width=4, depth=4)   # joists span 4 modules = 2600mm
        b.add_bay("b", width=4, depth=3)   # joists span 3 modules = 1950mm
        bom = b.generate_bom(include_foundations=False)
        consolidated = bom.consolidate()
        joists = [
            i for i in consolidated.items
            if "joist" in i.description.lower() and "bearer" not in i.description.lower()
        ]
        assert len(joists) == 2, "Joists at different spans must stay separate"

    def test_consolidate_preserves_single_items_unchanged(self):
        """Items that already appear once should pass through untouched."""
        bom = self._three_bay_bom()
        consolidated = bom.consolidate()
        # Roof outlet is always one item regardless of bay count
        outlets = [i for i in consolidated.items if "roof outlet" in i.description.lower()]
        assert len(outlets) == 1
        assert outlets[0].quantity == 1

    def test_osb_pricer_uses_area_for_sheets(self):
        """Pricer must derive sheet count from area, not from pre-rounded quantity.

        Three identical bays: per-bay rounding gives 9 sheets; pricing from
        total area should give 7 (ceil(20.4 / 2.97)).
        """
        bom = self._three_bay_bom()
        consolidated = bom.consolidate()
        estimate = consolidated.price()
        deck_lines = [
            p for p in estimate.priced if "roof deck" in p.description.lower()
        ]
        assert len(deck_lines) == 1
        assert "7" in deck_lines[0].qty_purchased

    def test_consolidation_reduces_breather_membrane_rolls(self):
        """Breather membrane rolls should be far fewer when areas are combined."""
        bom = self._three_bay_bom()

        # Unconsolidated: each face gets its own roll calculation
        unconsolidated_estimate = bom.price()
        membrane_before = sum(
            int(p.qty_purchased.split()[0])
            for p in unconsolidated_estimate.priced
            if "breather" in p.description.lower()
        )

        # Consolidated: one area calculation over all faces
        consolidated_estimate = bom.consolidate().price()
        membrane_after = sum(
            int(p.qty_purchased.split()[0])
            for p in consolidated_estimate.priced
            if "breather" in p.description.lower()
        )

        assert membrane_after < membrane_before
