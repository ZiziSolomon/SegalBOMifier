"""Segal method tartan grid geometry.

The tartan grid is the foundation of the Segal method. It alternates between
panel-width bands (where infill panels sit) and structural-thickness bands
(where columns and battens go). All dimensions in the system derive from this grid.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SegalGrid:
    """Defines the tartan grid that governs all Segal method dimensions.

    Args:
        panel_width: Width of standard building panels in mm (typically 600 or 1200).
        structural_thickness: Width of the structural gap between panels in mm
            (typically 50, matching column/batten thickness).
    """

    panel_width: int = 600
    structural_thickness: int = 50

    def __post_init__(self):
        if self.panel_width <= 0:
            raise ValueError(f"panel_width must be positive, got {self.panel_width}")
        if self.structural_thickness <= 0:
            raise ValueError(
                f"structural_thickness must be positive, got {self.structural_thickness}"
            )

    @property
    def module_pitch(self) -> int:
        """Centre-to-centre distance between adjacent grid lines (mm).

        One module = one panel width + one structural gap.
        """
        return self.panel_width + self.structural_thickness

    def modules_to_mm(self, n_modules: int) -> int:
        """Convert a module count to a centre-to-centre dimension in mm.

        This is the distance from one column centreline to another,
        spanning n_modules panels.
        """
        return n_modules * self.module_pitch

    def face_modules(self, width_modules: int, depth_modules: int, face: str) -> int:
        """Return the module count along a given face of a bay.

        North/south faces span the width, east/west faces span the depth.
        """
        if face in ("north", "south"):
            return width_modules
        elif face in ("east", "west"):
            return depth_modules
        else:
            raise ValueError(f"Unknown face '{face}', expected north/south/east/west")
