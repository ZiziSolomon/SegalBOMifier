"""Default material specifications for Segal method buildings.

These are typical dimensions and descriptions drawn from the Architects' Journal
Segal Method special issue. All dimensions in mm unless noted.

The specs are organised by building element (foundations, frame, roof, etc.)
matching the construction sequence. Each spec is a dict so it's easy to
override individual values or feed into a future pricing system.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class TimberSpec:
    """A timber section specification."""

    width: int  # mm
    depth: int  # mm
    grade: str = "C24"
    treatment: str = "pressure-treated"
    description: str = ""

    @property
    def section_label(self) -> str:
        return f"{self.width}x{self.depth}mm"

    @property
    def cross_section_m2(self) -> float:
        return (self.width / 1000) * (self.depth / 1000)


@dataclass(frozen=True)
class PanelSpec:
    """A building board/panel specification."""

    width: int  # mm - should match grid panel_width
    height: int  # mm - typically storey_height
    thickness: int  # mm
    material: str = ""
    description: str = ""

    @property
    def area_m2(self) -> float:
        return (self.width / 1000) * (self.height / 1000)


@dataclass(frozen=True)
class BoltSpec:
    """A bolt/fixing specification."""

    diameter: int  # mm (M-size)
    length: int  # mm
    type: str = "galvanised hex bolt"
    description: str = ""

    @property
    def label(self) -> str:
        return f"M{self.diameter}x{self.length}mm {self.type}"


# ── Structural frame ────────────────────────────────────────────────────────

# Posts: slender sections, pressure-treated softwood
POST_TIMBER = TimberSpec(
    width=100, depth=50,
    treatment="pressure-treated",
    description="Structural post (column)",
)

# Beams: C24 stress-graded for higher loads
BEAM_TIMBER = TimberSpec(
    width=200, depth=50,
    grade="C24",
    treatment="pressure-treated",
    description="Structural beam",
)

# Joists: same depth as beams so undersides align (key Segal principle)
JOIST_TIMBER = TimberSpec(
    width=200, depth=50,
    grade="C16",
    treatment="pressure-treated",
    description="Floor/roof joist",
)

# Bracing diagonals
BRACE_TIMBER = TimberSpec(
    width=100, depth=25,
    description="Cross-brace diagonal",
)

# Joist bearer (bolted to beam side, joists sit on it)
BEARER_TIMBER = TimberSpec(
    width=50, depth=50,
    description="Joist bearer",
)


# ── Frame bolts ─────────────────────────────────────────────────────────────

FRAME_BOLT = BoltSpec(
    diameter=12, length=150,
    type="galvanised hex bolt",
    description="Frame joint bolt",
)

JOIST_COACH_BOLT = BoltSpec(
    diameter=10, length=100,
    type="galvanised coach bolt",
    description="Joist-to-bearer bolt",
)


# ── Foundations ──────────────────────────────────────────────────────────────

FOUNDATION_PAD_WIDTH   = 600  # mm square plan
FOUNDATION_PAD_DEPTH   = 500  # mm deep (or to suit soil)
PAVING_SLAB_SIZE       = 600  # mm square cap (plan)
PAVING_SLAB_THICKNESS  =  40  # mm; post base bears onto this
LEAD_SHEET_SIZE        = 150  # mm square moisture seal


# ── Roof ────────────────────────────────────────────────────────────────────

ROOF_OVERHANG = 600  # mm all around
ROOF_WOODWOOL_THICKNESS = 50  # mm
ROOF_FELT_LAYERS = 3
ROOF_SHINGLE_DEPTH = 40  # mm layer of 20mm diameter shingle
FASCIA_TIMBER = TimberSpec(width=200, depth=25, description="Roof fascia")
UPSTAND_TIMBER = TimberSpec(width=100, depth=50, description="Roof edge upstand")
CAPPING_TIMBER = TimberSpec(width=50, depth=25, description="Roof edge capping")


# ── Floors ──────────────────────────────────────────────────────────────────

FLOOR_BOARD = TimberSpec(
    width=150, depth=25,
    grade="",
    treatment="untreated",
    description="T&G softwood floor board",
)

FLOOR_INSULATION_THICKNESS = 100  # mm PIR (IKO Enertherm or equivalent)
FLOOR_INSULATION_MATERIAL = "IKO Enertherm PIR board"  # rigid PIR board, Selco stock


# ── External walls ──────────────────────────────────────────────────────────

# Outer face options — all present as the same BOM description/role;
# the material field drives which catalogue entry the pricer selects.

# Default: HardiePlank (Glasal equivalent, low-maintenance, fire-rated)
EXTERNAL_PANEL_OUTER = PanelSpec(
    width=600, height=2400, thickness=8,
    material="HardiePlank fibre cement weatherboard",
    description="External weatherproof panel",
)

# Alternative: Cedral Lap (same fibre-cement family, typically cheaper)
CEDRAL_LAP_OUTER = PanelSpec(
    width=600, height=2400, thickness=10,
    material="Cedral Lap fibre cement weatherboard",
    description="External weatherproof panel",
)

# Alternative: corrugated galvanised steel ('wiggly tin')
WIGGLY_STEEL_OUTER = PanelSpec(
    width=600, height=2400, thickness=1,
    material="Corrugated galvanised steel sheet",
    description="External weatherproof panel",
)

# Alternative: Selco softwood shiplap (cheapest; check whether pre-treated or
# stain needed, and verify AD B boundary distance before using on habitable walls)
FEATHEREDGE_OUTER = PanelSpec(
    width=600, height=2400, thickness=19,
    material="Selco softwood shiplap cladding 125x19mm",
    description="External weatherproof panel",
)

# Core: woodwool slab
EXTERNAL_PANEL_CORE = PanelSpec(
    width=600, height=2400, thickness=50,
    material="Woodwool slab",
    description="External wall insulating core",
)

# Inner face: plasterboard (standard finish for habitable rooms)
EXTERNAL_PANEL_INNER = PanelSpec(
    width=600, height=2400, thickness=12,
    material="Plasterboard",
    description="External wall internal finish",
)

# Inner face: OSB for tool-hanging walls (e.g. unenclosed lean-to bays)
# 18mm OSB can take coach screws, tool rails, and hanging loads directly.
TOOL_WALL_INNER = PanelSpec(
    width=600, height=2400, thickness=18,
    material="OSB/3 18mm",
    description="Tool-wall inner face (structural board for fixings)",
)

# Wall battens and fixings
WALL_BATTEN = TimberSpec(width=50, depth=25, description="Wall clamping batten")
SOLE_PLATE = TimberSpec(width=50, depth=50, description="Wall sole plate")
WALL_BOLT = BoltSpec(
    diameter=8, length=100,
    type="galvanised hex bolt",
    description="Wall panel clamping bolt",
)
WALL_BLOCK = TimberSpec(width=50, depth=50, description="Grid position block")

BOLTS_PER_BATTEN = 3  # 3 bolts per batten at each joint position
BATTENS_PER_JOINT = 3  # 3 battens (inner, core, outer) at each joint


# ── Windows ─────────────────────────────────────────────────────────────────

GLASS_THICKNESS = 4  # mm float glass
WINDOW_ALUMINIUM_ANGLE = "25x25x3mm aluminium angle"
WINDOW_LINING = TimberSpec(width=50, depth=25, description="Window lining timber")
WINDOW_BEAD = TimberSpec(width=25, depth=12, description="Window glazing bead")


# ── Partitions ──────────────────────────────────────────────────────────────

PARTITION_CORE = PanelSpec(
    width=600, height=2400, thickness=50,
    material="Woodwool slab",
    description="Partition structural core",
)

PARTITION_FINISH = PanelSpec(
    width=600, height=2400, thickness=12,
    material="Plasterboard",
    description="Partition decorative finish",
)


# ── Ceilings ────────────────────────────────────────────────────────────────

CEILING_BOARD = PanelSpec(
    width=600, height=0, thickness=12,  # height = cut to joist spacing
    material="Plasterboard",
    description="Ceiling board",
)

CEILING_BATTEN = TimberSpec(width=25, depth=25, description="Ceiling batten")

# Fire lining for first-floor ceiling in 2-storey buildings
FIRE_LINING = TimberSpec(
    width=100, depth=25,
    treatment="untreated",
    description="Sacrificial fire lining",
)


# ── Breather membrane ────────────────────────────────────────────────────────

# Vapour-permeable, wind-tight layer behind ventilated cladding.
# Selco does not stock this — source from Travis Perkins / Screwfix / specialist.
BREATHER_MEMBRANE_MATERIAL = "Breather membrane (vapour-permeable, wind-tight)"
BREATHER_MEMBRANE_ROLL_M2 = 50  # standard 1m × 50m roll


# ── Wall insulation (PIR between posts) ──────────────────────────────────────

# 100mm PIR fills the full post depth (100×47mm posts).
# Uses the same Selco product as floor insulation (IKO Enertherm PIR 2400×1200mm).
# Two 600mm-wide cuts per board → same sheet-yield as floor.
WALL_PIR_THICKNESS = 100  # mm
WALL_PIR_MATERIAL = "IKO Enertherm PIR board"


# ── General fixings ─────────────────────────────────────────────────────────

SCREWS_PER_BOARD = 8  # No. 8 x 50mm for board fixing
SCREWS_PER_BATTEN = 4  # No. 10 x 75mm for batten fixing
STAIN_COVERAGE_M2_PER_LITRE = 12  # approximate for wood stain
