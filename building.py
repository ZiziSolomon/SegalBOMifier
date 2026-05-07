"""Segal method building topology.

A SegalBuilding is composed of named Bays connected at their faces.
The connection graph determines which faces are perimeter (external wall
by default) and which are interior (open by default). Users can override
any face's wall type.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from grid import SegalGrid


FACES = ("north", "south", "east", "west")

# Which dimension a face spans: north/south run along width, east/west along depth
FACE_AXIS = {
    "north": "width",
    "south": "width",
    "east": "depth",
    "west": "depth",
}

OPPOSITE_FACE = {
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east",
}


class WallType(Enum):
    """Type of infill for a bay face."""

    EXTERNAL = "external"    # fibre cement + woodwool + plasterboard
    TOOL_WALL = "tool_wall"  # fibre cement + woodwool + OSB (for hanging tools)
    WINDOW = "window"        # glass in aluminium track within lining
    PARTITION = "partition"  # woodwool + plasterboard both sides
    OPEN = "open"            # no wall - connected to adjacent bay
    NONE = "none"            # open to outside (balcony, covered walkway)


@dataclass
class Bay:
    """A single structural bay in a Segal building.

    A bay is a rectangular region of the tartan grid, defined by its
    module counts in each direction. It has four faces (north, south,
    east, west) which can hold different wall types.

    Args:
        name: Human-readable label (e.g. "kitchen", "bedroom_1").
        width_modules: Number of panel modules along the width (north/south faces).
        depth_modules: Number of panel modules along the depth (east/west faces).
        storeys: Number of storeys (1 or 2).
        enclosed: If False, skip floor boarding, floor insulation, and ceiling
            finish (e.g. open-fronted lean-to bays). Defaults to True.
    """

    name: str
    width_modules: int
    depth_modules: int
    storeys: int = 1
    enclosed: bool = True

    def __post_init__(self):
        if self.width_modules < 1:
            raise ValueError(f"width_modules must be >= 1, got {self.width_modules}")
        if self.depth_modules < 1:
            raise ValueError(f"depth_modules must be >= 1, got {self.depth_modules}")
        if self.storeys not in (1, 2):
            raise ValueError(f"storeys must be 1 or 2, got {self.storeys}")

    def face_modules(self, face: str) -> int:
        """Number of panel modules along a given face."""
        if face in ("north", "south"):
            return self.width_modules
        elif face in ("east", "west"):
            return self.depth_modules
        raise ValueError(f"Unknown face '{face}'")

    def width_mm(self, grid: SegalGrid) -> int:
        return grid.modules_to_mm(self.width_modules)

    def depth_mm(self, grid: SegalGrid) -> int:
        return grid.modules_to_mm(self.depth_modules)

    def floor_area_m2(self, grid: SegalGrid) -> float:
        return (self.width_mm(grid) / 1000) * (self.depth_mm(grid) / 1000)


@dataclass
class Connection:
    """A connection between two bay faces (they share a frame line)."""

    bay_a: str
    face_a: str
    bay_b: str
    face_b: str


class SegalBuilding:
    """A Segal method building composed of connected bays.

    Usage:
        grid = SegalGrid(panel_width=600, structural_thickness=50)
        building = SegalBuilding(grid)
        building.add_bay("kitchen", width=4, depth=4)
        building.add_bay("living", width=3, depth=4)
        building.connect("kitchen", "east", "living", "west")
        building.set_wall("living", "east", WallType.WINDOW)

    Args:
        grid: The tartan grid defining module dimensions.
        storey_height: Floor-to-ceiling height per storey in mm.
        ground_clearance: Height of undercroft below ground floor in mm.
    """

    def __init__(
        self,
        grid: SegalGrid,
        storey_height: int = 2400,
        ground_clearance: int = 450,
    ):
        self.grid = grid
        self.storey_height = storey_height
        self.ground_clearance = ground_clearance
        self._bays: dict[str, Bay] = {}
        self._connections: list[Connection] = []
        self._wall_overrides: dict[tuple[str, str], WallType] = {}

    @property
    def bays(self) -> dict[str, Bay]:
        return dict(self._bays)

    def add_bay(
        self,
        name: str,
        width: int,
        depth: int,
        storeys: int = 1,
        enclosed: bool = True,
    ) -> Bay:
        """Add a bay to the building.

        Args:
            name: Unique label for this bay.
            width: Number of panel modules along the width (north/south faces).
            depth: Number of panel modules along the depth (east/west faces).
            storeys: 1 or 2.
            enclosed: Set False for open-fronted bays (skips floor boarding,
                floor insulation, and ceiling finish in the BOM).

        Returns:
            The created Bay.
        """
        if name in self._bays:
            raise ValueError(f"Bay '{name}' already exists")
        bay = Bay(
            name=name,
            width_modules=width,
            depth_modules=depth,
            storeys=storeys,
            enclosed=enclosed,
        )
        self._bays[name] = bay
        return bay

    def connect(self, bay_a: str, face_a: str, bay_b: str, face_b: str) -> None:
        """Declare that two bay faces share a frame line.

        The faces must span the same number of modules (so the frame line
        is continuous). Connected faces default to WallType.OPEN.

        Args:
            bay_a: Name of first bay.
            face_a: Face of first bay ("north", "south", "east", "west").
            bay_b: Name of second bay.
            face_b: Face of second bay.

        Raises:
            ValueError: If bays don't exist, faces are invalid, or module
                counts don't match along the shared edge.
        """
        a = self._get_bay(bay_a)
        b = self._get_bay(bay_b)
        self._validate_face(face_a)
        self._validate_face(face_b)

        modules_a = a.face_modules(face_a)
        modules_b = b.face_modules(face_b)
        if modules_a != modules_b:
            raise ValueError(
                f"Cannot connect {bay_a}.{face_a} ({modules_a} modules) to "
                f"{bay_b}.{face_b} ({modules_b} modules) — lengths must match"
            )

        # Check for duplicate connections on the same face
        for conn in self._connections:
            if (conn.bay_a == bay_a and conn.face_a == face_a) or \
               (conn.bay_b == bay_a and conn.face_b == face_a):
                raise ValueError(
                    f"{bay_a}.{face_a} is already connected"
                )
            if (conn.bay_a == bay_b and conn.face_a == face_b) or \
               (conn.bay_b == bay_b and conn.face_b == face_b):
                raise ValueError(
                    f"{bay_b}.{face_b} is already connected"
                )

        self._connections.append(Connection(bay_a, face_a, bay_b, face_b))

    def set_wall(self, bay_name: str, face: str, wall_type: WallType) -> None:
        """Override the wall type for a bay face.

        By default, connected faces are OPEN and unconnected faces are EXTERNAL.
        This lets you change that — e.g. add a PARTITION between connected bays,
        or put a WINDOW on a perimeter face.
        """
        self._get_bay(bay_name)
        self._validate_face(face)
        self._wall_overrides[(bay_name, face)] = wall_type

    def get_wall_type(self, bay_name: str, face: str) -> WallType:
        """Get the effective wall type for a bay face.

        Priority: explicit override > auto-detection.
        Auto: connected = OPEN, unconnected = EXTERNAL.
        """
        self._get_bay(bay_name)
        self._validate_face(face)

        # Check for explicit override
        if (bay_name, face) in self._wall_overrides:
            return self._wall_overrides[(bay_name, face)]

        # Auto-detect: connected faces are open, perimeter faces are external
        if self.is_connected(bay_name, face):
            return WallType.OPEN
        return WallType.EXTERNAL

    def is_connected(self, bay_name: str, face: str) -> bool:
        """Check whether a bay face is connected to another bay."""
        for conn in self._connections:
            if (conn.bay_a == bay_name and conn.face_a == face) or \
               (conn.bay_b == bay_name and conn.face_b == face):
                return True
        return False

    def get_connected_bay(self, bay_name: str, face: str) -> Optional[str]:
        """Get the name of the bay connected on a given face, or None."""
        result = self.get_connection(bay_name, face)
        return result[0] if result else None

    def get_connection(
        self, bay_name: str, face: str
    ) -> Optional[tuple[str, str]]:
        """Get (connected_bay_name, connected_face) for a face, or None."""
        for conn in self._connections:
            if conn.bay_a == bay_name and conn.face_a == face:
                return (conn.bay_b, conn.face_b)
            if conn.bay_b == bay_name and conn.face_b == face:
                return (conn.bay_a, conn.face_a)
        return None

    def is_interior_frame_line(self, bay_name: str, face: str) -> bool:
        """Whether a face's frame line is shared with another bay.

        Interior frame lines get double beams (load from both sides).
        Perimeter frame lines get single beams.
        """
        return self.is_connected(bay_name, face)

    def unique_post_count(self) -> int:
        """Count unique post positions across the whole building.

        Each bay has 4 corner posts. Connected bays share posts along
        their shared frame line. We use a set-based approach: assign
        each bay a local coordinate origin and track which posts are
        shared via connections.

        For a simpler model: each isolated bay has 4 posts. Each
        connection merges 2 posts (the shared corners). But T-junctions
        and L-shapes complicate this — we need to track actual topology.

        Simplified formula:
        - Start: sum of 4 per bay
        - Each connection shares 2 corner posts
        - But corner-sharing at T/L junctions means some posts are
          shared by 3+ bays. We approximate by deducting 2 per connection.
        """
        total = 4 * len(self._bays)
        total -= 2 * len(self._connections)
        return max(total, 1)

    def post_height_mm(self, bay_name: str) -> int:
        """Total post height for a bay, from foundation to roof beam."""
        bay = self._get_bay(bay_name)
        return self.ground_clearance + self.storey_height * bay.storeys

    def max_storeys(self) -> int:
        """Highest storey count in the building."""
        if not self._bays:
            return 0
        return max(b.storeys for b in self._bays.values())

    def total_floor_area_m2(self) -> float:
        """Total floor area across all bays and storeys."""
        total = 0.0
        for bay in self._bays.values():
            total += bay.floor_area_m2(self.grid) * bay.storeys
        return total

    def perimeter_faces(self) -> list[tuple[str, str]]:
        """All (bay_name, face) pairs that are on the building perimeter."""
        result = []
        for bay_name in self._bays:
            for face in FACES:
                if not self.is_connected(bay_name, face):
                    result.append((bay_name, face))
        return result

    def all_faces(self) -> list[tuple[str, str, WallType]]:
        """All (bay_name, face, wall_type) triples in the building."""
        result = []
        for bay_name in self._bays:
            for face in FACES:
                result.append((bay_name, face, self.get_wall_type(bay_name, face)))
        return result

    def _get_bay(self, name: str) -> Bay:
        if name not in self._bays:
            raise ValueError(
                f"Bay '{name}' not found. "
                f"Available: {list(self._bays.keys())}"
            )
        return self._bays[name]

    @staticmethod
    def _validate_face(face: str) -> None:
        if face not in FACES:
            raise ValueError(
                f"Invalid face '{face}', must be one of {FACES}"
            )
