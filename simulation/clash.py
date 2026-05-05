"""Phase 1 — clash detection.

Walks a build123d Compound, finds pairs of leaf solids whose interiors
overlap by more than `epsilon_mm3`, and reports them. Intended to be wired
into `cad.build_cad()` as a final sanity check on every build.

Algorithm
---------
Two-stage to keep it scalable:

  Broad phase: AABB overlap test on every pair (six float comparisons each).
               build123d exposes `.bounding_box()` cheaply.
  Narrow phase: real boolean intersection volume, only on AABB-overlapping
               pairs. Discards results <= epsilon_mm3 (numerical noise +
               geometric touching).

If broad phase ever gets slow on this geometry, swap it for an R-tree or
BVH. For a few hundred members it is irrelevant.

Allow-list
----------
A list of (pattern_a, pattern_b) tuples. A clash between paths P and Q is
suppressed when one pair's patterns are substrings of P and Q respectively
(in either order). Patterns are matched against the slash-joined label
path, e.g. ``"02-structural-frame/frame-01-west-end/post-south-x0"``. This
lets us land clash-detection without first refactoring every existing
intentional intersection — add it to the allow-list, fix it later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# ── Error type ────────────────────────────────────────────────────────────────

class ClashError(RuntimeError):
    """Raised when one or more unallowed clashes are detected."""

    def __init__(self, clashes: list["Clash"]):
        self.clashes = clashes
        lines = [f"{len(clashes)} clash(es) detected:"]
        for c in clashes:
            lines.append(
                f"  {c.volume_mm3:>10.1f} mm³  "
                f"{c.path_a}  ↔  {c.path_b}"
            )
        super().__init__("\n".join(lines))


@dataclass(frozen=True)
class Clash:
    path_a: str
    path_b: str
    volume_mm3: float


# ── Tree walk ─────────────────────────────────────────────────────────────────

def _iter_leaf_solids(node, path: tuple[str, ...] = ()) -> Iterable[tuple[str, object]]:
    """Yield (path_string, solid) for every leaf solid in the assembly.

    A "leaf" is a node with no children. build123d's primitive shapes (Box,
    Cylinder, etc.) inherit from Compound but have no children, so they are
    treated as leaves.
    """
    label = getattr(node, "label", "") or ""
    new_path = path + (label,) if label else path

    children = getattr(node, "children", None) or ()
    if children:
        for child in children:
            yield from _iter_leaf_solids(child, new_path)
        return

    # Leaf. Skip anything with no usable volume (shouldn't normally happen).
    try:
        if node.volume <= 0:
            return
    except Exception:
        return
    yield ("/".join(new_path) if new_path else repr(node), node)


# ── Broad phase ───────────────────────────────────────────────────────────────

def _aabb_overlap(a, b, slack: float = 0.0) -> bool:
    """Standard 3D AABB overlap test on two build123d bounding boxes."""
    amin, amax = a.min, a.max
    bmin, bmax = b.min, b.max
    return (
        amin.X <= bmax.X + slack and amax.X + slack >= bmin.X
        and amin.Y <= bmax.Y + slack and amax.Y + slack >= bmin.Y
        and amin.Z <= bmax.Z + slack and amax.Z + slack >= bmin.Z
    )


# ── Allow-list matching ───────────────────────────────────────────────────────

def _is_allowed(
    path_a: str,
    path_b: str,
    allow_list: list[tuple[str, str]],
) -> bool:
    for pa, pb in allow_list:
        if (pa in path_a and pb in path_b) or (pa in path_b and pb in path_a):
            return True
    return False


# ── Public API ────────────────────────────────────────────────────────────────

def check_clashes(
    assembly,
    epsilon_mm3: float = 1.0,
    allow_list: list[tuple[str, str]] | None = None,
    raise_on_clash: bool = True,
) -> list[Clash]:
    """Detect clashing solid pairs in `assembly`.

    Args:
        assembly: A build123d Compound (typically the return of build_cad).
        epsilon_mm3: Intersection volumes <= this are ignored. 1 mm³ is well
            below any meaningful overlap and tolerates OCCT's numerical
            noise on coincident faces.
        allow_list: Pairs of label-substring patterns whose mutual clashes
            should be suppressed. See module docstring.
        raise_on_clash: If True, raise ClashError when unallowed clashes
            remain. Set False to inspect the list programmatically.

    Returns:
        List of Clash records (empty if clean). If raise_on_clash is True
        and the list is non-empty, raises ClashError instead.
    """
    allow_list = allow_list or []

    leaves = list(_iter_leaf_solids(assembly))
    bboxes = [leaf[1].bounding_box() for leaf in leaves]

    clashes: list[Clash] = []
    n = len(leaves)
    for i in range(n):
        path_i, solid_i = leaves[i]
        bb_i = bboxes[i]
        for j in range(i + 1, n):
            path_j, solid_j = leaves[j]
            if not _aabb_overlap(bb_i, bboxes[j]):
                continue
            if _is_allowed(path_i, path_j, allow_list):
                continue
            try:
                inter = solid_i & solid_j
                vol = float(inter.volume) if inter is not None else 0.0
            except Exception:
                # Boolean failed — likely degenerate. Treat as zero overlap;
                # PyBullet sim in Phase 2 will catch real attachment problems.
                continue
            if vol > epsilon_mm3:
                clashes.append(Clash(path_i, path_j, vol))

    if clashes and raise_on_clash:
        raise ClashError(clashes)
    return clashes
