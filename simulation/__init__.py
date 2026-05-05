"""Simulation / verification passes that consume the CAD assembly.

  - clash:       solid-pair intersection detection (Phase 1).
  - rigid_body:  PyBullet drop-test for unattached members (Phase 2).
"""

from .clash import ClashError, check_clashes
from .rigid_body import DriftRecord, RigidBodyResult, run_rigid_body_sim

__all__ = [
    "ClashError",
    "check_clashes",
    "DriftRecord",
    "RigidBodyResult",
    "run_rigid_body_sim",
]
