"""Phase 2 — PyBullet rigid-body drop test.

Each leaf solid in the assembly becomes a rigid body whose mass is computed
from its bounding-box volume × material density.  Foundation pads and paving
slabs are pinned to the world (mass = 0).  All other members are dynamic.

Connections are NOT modelled as programmatic constraints.  When bolt
assemblies are present in the assembly they are loaded as steel rigid bodies,
and their physical geometry (head + shank passing through the clearance holes)
is what holds the structure together — exactly as in reality.

Currently the bolt assemblies in cad.py are commented out, so there are no
physical connections in the simulation and every structural member will drift.
That is the correct and expected result for the current state of the model.

Decision gate (measured at runtime)
------------------------------------
    wall-clock < 5 s    -> run every build
    5-60 s              -> run on demand + pre-commit
    > 60 s              -> run only when explicitly invoked

The `RigidBodyResult.recommended_cadence` field carries this verdict so the
caller can decide whether to wire this into build_cad().
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Iterable

from .clash import _iter_leaf_solids, _aabb_overlap


# ── Material densities ────────────────────────────────────────────────────────

_PINE_KG_M3     = 500.0
_STEEL_KG_M3    = 7850.0
_CONCRETE_KG_M3 = 2400.0

_MM_TO_M   = 1e-3
_MM3_TO_M3 = 1e-9


def _density_kg_m3(path: str) -> float:
    lp = path.lower()
    if "bolt" in lp:
        return _STEEL_KG_M3
    if "pad-" in lp or "slab-" in lp or "concrete" in lp:
        return _CONCRETE_KG_M3
    return _PINE_KG_M3


def _is_foundation(path: str) -> bool:
    """Return True for members that should be pinned to the world.

    Only the buried concrete pads are pinned — they're in the ground.
    Paving slabs just rest on pads by gravity (dynamic concrete bodies).
    """
    return "pad-" in path.lower()


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class DriftRecord:
    path: str
    drift_mm: float

    def __str__(self) -> str:
        return f"{self.drift_mm:>9.1f} mm  {self.path}"


@dataclass
class RigidBodyResult:
    drifters: list[DriftRecord] = field(default_factory=list)
    wall_clock_s: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.drifters

    @property
    def recommended_cadence(self) -> str:
        if self.wall_clock_s < 5:
            return "every_build"
        elif self.wall_clock_s < 60:
            return "on_demand"
        else:
            return "explicit_only"

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [
            f"Rigid-body sim: {status}"
            f"  ({self.wall_clock_s:.1f} s -> cadence: {self.recommended_cadence})"
        ]
        for d in sorted(self.drifters, key=lambda r: -r.drift_mm):
            lines.append(f"  DRIFT  {d}")
        return "\n".join(lines)


# ── Simulation ────────────────────────────────────────────────────────────────

def run_rigid_body_sim(
    assembly,
    epsilon_mm: float = 5.0,
    sim_time: float = 2.0,
    verbose: bool = False,
) -> RigidBodyResult:
    """Drop-test the assembly under gravity and report unattached members.

    Args:
        assembly:    A build123d Compound (typically the return of build_cad).
        epsilon_mm:  Drift threshold in mm.  Defaults to 5 mm.
        sim_time:    Simulated time in seconds.  Defaults to 2 s.
        verbose:     Print per-body debug information during setup.

    Returns:
        RigidBodyResult containing the list of drifting members, wall-clock
        time, and the recommended invocation cadence.

    Raises:
        ImportError: If pybullet is not installed.
    """
    try:
        import pybullet as p
    except ImportError as exc:
        raise ImportError(
            "pybullet is required for rigid-body simulation. "
            "Install with:  pip install pybullet"
        ) from exc

    t0 = time.perf_counter()

    leaves  = list(_iter_leaf_solids(assembly))
    bboxes  = [leaf[1].bounding_box() for leaf in leaves]

    client = p.connect(p.DIRECT)
    try:
        p.setGravity(0, 0, -9.81, physicsClientId=client)

        body_ids: list[int] = []
        centroids_m: list[tuple[float, float, float]] = []

        for i, (path, solid) in enumerate(leaves):
            bb = bboxes[i]

            # AABB centroid used as the body's reference-frame position.
            cx = (bb.min.X + bb.max.X) * 0.5 * _MM_TO_M
            cy = (bb.min.Y + bb.max.Y) * 0.5 * _MM_TO_M
            cz = (bb.min.Z + bb.max.Z) * 0.5 * _MM_TO_M
            centroids_m.append((cx, cy, cz))

            # Box collision shape from AABB half-extents.
            hx = (bb.max.X - bb.min.X) * 0.5 * _MM_TO_M
            hy = (bb.max.Y - bb.min.Y) * 0.5 * _MM_TO_M
            hz = (bb.max.Z - bb.min.Z) * 0.5 * _MM_TO_M
            col_id = p.createCollisionShape(
                p.GEOM_BOX,
                halfExtents=[hx, hy, hz],
                physicsClientId=client,
            )

            if _is_foundation(path):
                mass = 0.0   # static: pinned to world
            else:
                vol_m3 = solid.volume * _MM3_TO_M3
                mass   = vol_m3 * _density_kg_m3(path)

            body_id = p.createMultiBody(
                baseMass=mass,
                baseCollisionShapeIndex=col_id,
                basePosition=(cx, cy, cz),
                physicsClientId=client,
            )
            body_ids.append(body_id)

            if verbose:
                tag = "STATIC" if mass == 0.0 else f"{mass:.3f} kg"
                print(f"  [{i:03d}] {tag:>12}  {path}")

        if verbose:
            n_bolts = sum(1 for path, _ in leaves if "bolt" in path.lower())
            print(f"  {len(leaves)} bodies loaded, {n_bolts} bolt assemblies")

        # Build JOINT_FIXED constraints from bolt assemblies.
        # Bolts hold members together through clamping force, not geometry, so
        # pure collision physics cannot model them. Instead each bolt assembly
        # acts as a detector: any two structural members whose AABBs both
        # overlap the bolt's AABB get a JOINT_FIXED constraint between them.
        # Bolt assemblies are NOT loaded as PyBullet bodies (they sit inside
        # clearance holes at t=0 and would cause explosive forces on startup).
        n_constraints = 0
        constrained_pairs: set[tuple[int, int]] = set()
        for bi, (bpath, _) in enumerate(leaves):
            if "bolt" not in bpath.lower():
                continue
            bridged = [
                mi for mi, (mpath, _) in enumerate(leaves)
                if "bolt" not in mpath.lower()
                and not _is_foundation(mpath)
                and _aabb_overlap(bboxes[bi], bboxes[mi], slack=1.0)
            ]
            for a in range(len(bridged)):
                for b in range(a + 1, len(bridged)):
                    i, j = bridged[a], bridged[b]
                    pair = (min(i, j), max(i, j))
                    if pair in constrained_pairs:
                        continue
                    constrained_pairs.add(pair)
                    pi, pj = centroids_m[i], centroids_m[j]
                    # Satisfy: P_i + (0,0,0) == P_j + child_frame at t=0.
                    child_frame = [pi[k] - pj[k] for k in range(3)]
                    p.createConstraint(
                        body_ids[i], -1, body_ids[j], -1,
                        p.JOINT_FIXED,
                        [0, 0, 0], [0, 0, 0], child_frame,
                        physicsClientId=client,
                    )
                    n_constraints += 1

        if verbose:
            print(f"  {n_constraints} JOINT_FIXED constraints created")

        # Record positions before stepping (initial world positions of COMs).
        initial_pos = [
            p.getBasePositionAndOrientation(bid, physicsClientId=client)[0]
            for bid in body_ids
        ]

        # Step at PyBullet's default 240 Hz.
        dt      = 1.0 / 240.0
        n_steps = int(sim_time / dt)
        for _ in range(n_steps):
            p.stepSimulation(physicsClientId=client)

        # Collect drifters.
        epsilon_m = epsilon_mm * _MM_TO_M
        drifters: list[DriftRecord] = []
        for i, (bid, (path, _)) in enumerate(zip(body_ids, leaves)):
            final_pos, _ = p.getBasePositionAndOrientation(bid, physicsClientId=client)
            ip = initial_pos[i]
            drift_m = math.sqrt(sum((f - s) ** 2 for f, s in zip(final_pos, ip)))
            if drift_m > epsilon_m:
                drifters.append(DriftRecord(path=path, drift_mm=drift_m / _MM_TO_M))

    finally:
        p.disconnect(physicsClientId=client)

    return RigidBodyResult(
        drifters=drifters,
        wall_clock_s=time.perf_counter() - t0,
    )
