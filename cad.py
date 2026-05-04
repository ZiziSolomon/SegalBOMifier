"""CAD model generator for Segal method buildings using build123d.

Future structural analysis — design constraints
------------------------------------------------
This model is intended to be fed into proper finite element analysis (FEA)
later — beam/frame analysis under Eurocode 5 (EN 1995) using timber strength
classes per EN 338, with explicit material properties (E, σ_bending,
σ_compression parallel and perpendicular to grain) and code-checked bolted
connection capacities. Likely toolchain: anaStruct for a 2D frame pass,
CalculiX / Code_Aster (via FreeCAD FEM) for full 3D when warranted.

Until that lands, a PyBullet rigid-body sim will run as a regression check —
each solid becomes a rigid body, each bolted connection a URDF fixed joint,
and gravity reveals anything that isn't actually attached.

Implications for code in this file:
  * Every member must carry an identifiable structural ROLE (post, joist,
    rafter, plate, brace, …) — don't generate anonymous geometry.
  * Every connection must be a first-class typed JOINT object linking
    specific members at a specific point — not three separate boolean ops
    that happen to line up.
  * Holes and bolts must be derived from the joint, not authored independently.
  * Don't bake assumptions that only make sense for visualisation (e.g.
    fudging member positions to look right) — FEA will see through it.
  * Keep member centrelines, end points, and section properties queryable;
    the FEA pass needs them.

Coordinate system
-----------------
  X  →  East   (positive)
  Y  →  North  (positive)
  Z  ↑  Up     (positive)

Origin: SW corner of the building at ground level (top of foundation pads
or existing slab).  All dimensions in mm.

Rendering conventions
---------------------
  Timbers  — rectangular cuboids (colour-coded by treatment) with M12
             clearance holes at structural bolt positions.
  Panels   — flat cuboids, colour-coded by material layer.
  Bolts    — three coaxial cylinders: head / unthreaded shank / threaded shank.

Usage
-----
  import sys; sys.path.insert(0, '..')
  from segal_method import SegalBuilding, SegalGrid, WallType
  from segal_method import cad

  grid = SegalGrid()
  building = SegalBuilding(grid)
  building.add_bay("kitchen", width=4, depth=4)
  # ... connect and configure bays ...

  assembly = cad.build_cad(building)
  cad.export(assembly, "my_build.step")

Limitations
-----------
  - Assumes a single E-W linear chain of bays (no L-shapes or T-junctions).
  - All bays must have the same depth_modules (consistent N-S span).
  - Flat roof only (Segal default: woodwool deck + loose membrane + shingle).
  - Bolt holes are placed at standard 45 / 90 mm offsets from beam ends;
    actual positions may differ from your structural engineer's drawing.
"""

from __future__ import annotations

import math
from build123d import (
    Align, Axis, Box, Compound, Cylinder, Color, Location,
    export_step, export_stl, export_brep,
)

import materials as mat
from building import SegalBuilding, WallType


# ── Colour palette ────────────────────────────────────────────────────────────

_C: dict[str, Color] = {
    "timber":         Color(0.65, 0.45, 0.25),   # natural softwood: warm brown
    "timber_treated": Color(0.40, 0.55, 0.35),   # pressure-treated: green-tinged
    "woodwool":       Color(0.85, 0.75, 0.55),   # straw/buff
    "plasterboard":   Color(0.95, 0.95, 0.92),   # near-white
    "fibre_cement":   Color(0.55, 0.55, 0.55),   # mid-grey
    "cedral":         Color(0.50, 0.53, 0.57),   # slightly bluer grey
    "featheredge":    Color(0.70, 0.58, 0.42),   # timber cladding: tan
    "osb":            Color(0.70, 0.60, 0.45),   # light tan
    "shingle":        Color(0.38, 0.38, 0.38),   # dark grey gravel
    "membrane":       Color(0.15, 0.15, 0.15),   # near-black felt
    "bolt_head":      Color(0.75, 0.75, 0.75),   # galvanised: bright silver
    "bolt_shank":     Color(0.85, 0.85, 0.85),   # slightly lighter
    "bolt_threaded":  Color(0.58, 0.58, 0.58),   # darker (thread contrast)
    "glass":          Color(0.70, 0.85, 0.90, 0.35),
    "concrete":       Color(0.68, 0.68, 0.63),
}

# Map outer cladding material name → colour key
_CLADDING_COLOUR: dict[str, str] = {
    "HardiePlank fibre cement weatherboard":    "fibre_cement",
    "Cedral Lap fibre cement weatherboard":     "cedral",
    "Selco softwood shiplap cladding 125x19mm": "featheredge",
    "Corrugated galvanised steel sheet":        "fibre_cement",  # similar tone
}

# Dict keyyed with nominal screw size stored as float (i.e. 10.0 for M10) gives a tuple of (tapHole,clearanceHole)
_BOLT_DICT = {8.0: (6.8,9.0), 10.0: (8.5,11.0), 12.0: (10.2, 13.5), 14.0: (12.0, 15.5), 16.0: (14.0, 17.5), 18.0: (15.5, 20.0), 20.0: (17.5, 22.0), 22.0: (19.5, 24.0), 24.0: (21.0, 26.0), 27.0: (24.0, 30.0), 30.0: (26.5, 33.0)}

# Two bolt positions per joint end, measured from end of beam (mm)
_BOLT_OFFSETS = (45.0, 90.0)
# Two bolt heights in the 200 mm beam cross-section (measured from bottom)
_BOLT_Z_FRACS = (0.30, 0.70)   # × section.width


# ── Layout helper ─────────────────────────────────────────────────────────────

def _linear_layout(building: SegalBuilding) -> dict[str, tuple[int, int]]:
    """Walk the E-W connection chain to find each bay's module-grid origin.

    Returns {bay_name: (x_modules, y_modules)} with (0, 0) at the SW corner.
    Raises ValueError if the building is not a simple E-W linear chain.
    """
    west_starts = [
        name for name in building.bays
        if not building.is_connected(name, "west")
    ]
    if len(west_starts) != 1:
        raise ValueError(
            f"Expected exactly one westernmost bay, found {west_starts}. "
            "Non-linear layouts need explicit bay coordinates."
        )

    layout: dict[str, tuple[int, int]] = {}
    x = 0
    current: str | None = west_starts[0]
    while current is not None:
        layout[current] = (x, 0)
        bay = building.bays[current]
        x += bay.width_modules
        current = building.get_connected_bay(current, "east")
    return layout


# ── Primitive builders ────────────────────────────────────────────────────────

def _col(shape, key: str):
    """Assign colour and return shape (for chaining)."""
    shape.color = _C[key]
    return shape


def _beam_x(
    length: float,
    section: mat.TimberSpec,
    colour_key: str,
    bolt_holes: bool = False,
    n_hole_ends: int = 2,   # 1 = near end only, 2 = both ends
) -> object:
    """Return a timber beam running along +X.

    Cross-section: section.depth (Y-axis, structural thickness) ×
                   section.width (Z-axis, structural height).
    Origin at the near-end bottom-centreline (x=0, y=0, z=0).

    If bolt_holes=True, M12 clearance holes are drilled through Y at each
    active end using _BOLT_OFFSETS and _BOLT_Z_FRACS.
    """
    b = Box(length, float(section.depth), float(section.width),
            align=(Align.MIN, Align.CENTER, Align.MIN))

    if bolt_holes:
        hr = _BOLT_DICT[mat.FRAME_BOLT.diameter][1] 
        drill = section.depth + 4.0  # slightly deeper than section thickness
        ends: list[float] = [0.0]    # near end
        if n_hole_ends == 2:
            ends.append(length)      # far end (offsets measured inward)

        for end in ends:
            sign = +1.0 if end == 0.0 else -1.0
            for off in _BOLT_OFFSETS:
                bx = end + sign * off
                for frac in _BOLT_Z_FRACS:
                    bz = frac * section.width
                    hole = Cylinder(hr, drill)
                    hole = hole.rotate(Axis.X, 90)   # align cylinder with Y
                    hole = hole.move(Location((bx, 0.0, bz)))
                    b = b - hole

    return _col(b, colour_key)


def _beam_y(
    length: float,
    section: mat.TimberSpec,
    colour_key: str,
    bolt_holes: bool = False,
    n_hole_ends: int = 2,
) -> object:
    """Return a timber beam running along +Y.

    Internally creates an X-beam then rotates 90° around Z.  After rotation:
      structural thickness  →  X-axis
      structural height     →  Z-axis
      span                  →  Y-axis

    Origin at the near-end bottom-centreline (x=0, y=0, z=0).
    """
    b = _beam_x(length, section, colour_key, bolt_holes, n_hole_ends)
    # After Rz(+90): old +X → +Y, old +Y → -X, Z unchanged.
    # Align.MIN in old X keeps the near end at the origin.
    return b.rotate(Axis.Z, 90)


def _panel(
    width: float, height: float, thickness: float, colour_key: str
) -> object:
    """Flat panel: width (X) × thickness (Y) × height (Z).

    Origin at the near-face SW corner (x=0, y=0, z=0).
    """
    p = Box(width, thickness, height, align=(Align.MIN, Align.MIN, Align.MIN))
    return _col(p, colour_key)


def _bolt_assy(spec: mat.BoltSpec, direction: str = "y") -> Compound:
    """Three coaxial cylinders assembled along +Z then rotated to direction.

    direction: "x", "y", or "z" — axis the bolt shank runs along (positive).
    Origin at the underside of the bolt head.
    """
    r = spec.diameter / 2.0
    head_r    = r * 1.55     # hex head approximated as cylinder
    head_h    = r * 1.35
    thread_l  = spec.length * 0.40
    shank_l   = spec.length - thread_l

    head     = Cylinder(head_r, head_h)
    shank    = Cylinder(r,      shank_l)
    threaded = Cylinder(r,      thread_l)

    head     = _col(head.move(Location((0, 0,  head_h / 2))),                        "bolt_head")
    shank    = _col(shank.move(Location((0, 0, head_h + shank_l / 2))),              "bolt_shank")
    threaded = _col(threaded.move(Location((0, 0, head_h + shank_l + thread_l / 2))), "bolt_threaded")

    assy = Compound(children=[head, shank, threaded])

    if direction == "y":
        assy = assy.rotate(Axis.X, 90)
    elif direction == "x":
        assy = assy.rotate(Axis.Y, -90)
    # "z" → already aligned

    return assy


# ── Portal frame ──────────────────────────────────────────────────────────────

def _portal_frame(
    building: SegalBuilding,
    x_mm: float,
    depth_mm: float,
    include_bolts: bool,
) -> tuple[list, list]:
    """One portal frame (2 posts + head beam + tie beam) at the given X position.

    The frame sits in the Y-Z plane.  Posts are centred on x_mm; beams run
    N-S (Y-direction) with their structural thickness (50 mm) centred on x_mm.

    Returns:
        (structural_parts, bolt_parts) — separated so the caller can place them
        in different labelled compounds.
    """
    ps = mat.POST_TIMBER    # 100 wide × 50 deep
    bs = mat.BEAM_TIMBER    # 200 wide × 50 deep

    post_h = building.ground_clearance + building.storey_height   # 2850 mm
    gc     = building.ground_clearance                            # 450 mm
    structural: list = []
    bolt_parts: list = []

    # Half of post depth in Y (the face-to-centreline distance).
    # Beams span between post inner faces so they don't pass through posts.
    post_half = float(ps.depth) / 2   # = 25 mm
    beam_span = depth_mm - 2 * post_half   # = 2550 mm for a 2600 mm bay

    # --- Posts (100 × 50 mm in plan, centred on x_mm) ---
    for y_mm in (0.0, depth_mm):
        post = Box(float(ps.width), float(ps.depth), float(post_h),
                   align=(Align.CENTER, Align.CENTER, Align.MIN))
        post = _col(post, "timber_treated")
        post = post.move(Location((x_mm, y_mm, 0.0)))
        side = "south" if y_mm == 0.0 else "north"
        post.label = f"post-{side}-x{int(x_mm)}"
        structural.append(post)

    # --- Head beam: top of posts, running N-S ---
    # Starts at south post inner face (y=post_half), ends at north post inner face.
    hb = _beam_y(beam_span, bs, "timber_treated", bolt_holes=True, n_hole_ends=2)
    z_hb = float(post_h - bs.width)   # top face flush with post tops
    hb = hb.move(Location((x_mm, post_half, z_hb)))
    hb.label = f"head-beam-x{int(x_mm)}"
    structural.append(hb)

    # --- Tie beam: at floor level, running N-S ---
    tb = _beam_y(beam_span, bs, "timber_treated", bolt_holes=True, n_hole_ends=2)
    z_tb = float(gc - bs.width)        # top face flush with floor level
    tb = tb.move(Location((x_mm, post_half, z_tb)))
    tb.label = f"tie-beam-x{int(x_mm)}"
    structural.append(tb)

    # --- Bolts at beam ends (2 bolts × 2 ends × 2 beams = 8 per frame) ---
    if include_bolts:
        bolt_z = [float(bs.width * f) for f in _BOLT_Z_FRACS]
        for beam_z_bot, beam_name in ((z_hb, "hb"), (z_tb, "tb")):
            for y_end, end_name, sign in (
                (post_half, "south", +1.0),
                (depth_mm - post_half, "north", -1.0),
            ):
                for off in _BOLT_OFFSETS:
                    by = y_end + sign * off
                    for bz_offset in bolt_z:
                        b = _bolt_assy(mat.FRAME_BOLT, "y")
                        b = b.move(Location((x_mm, by, beam_z_bot + bz_offset)))
                        b.label = (
                            f"bolt-{beam_name}-{end_name}"
                            f"-x{int(x_mm)}-y{int(by)}-z{int(beam_z_bot+bz_offset)}"
                        )
                        bolt_parts.append(b)

    return structural, bolt_parts


# ── Longitudinal beams ────────────────────────────────────────────────────────

def _longitudinal_beams(
    building: SegalBuilding,
    portal_xs: list[float],
    depth_mm: float,
    include_bolts: bool,
) -> list:
    """N-side and S-side eaves beams, one span per bay between portal faces.

    Each span runs from one portal's east face to the next portal's west face,
    so there is no overlap with the N-S portal beams at the corners.
    """
    bs         = mat.BEAM_TIMBER
    ps         = mat.POST_TIMBER
    # Longitudinal beams sit between portal posts along X, so they must clear
    # the post's full X-extent (POST_TIMBER.width), not the beam's own depth.
    post_half_x = float(ps.width) / 2
    post_h     = building.ground_clearance + building.storey_height
    z_bot      = float(post_h - bs.width)
    parts: list = []

    for y_mm, side in ((0.0, "south"), (depth_mm, "north")):
        for i in range(len(portal_xs) - 1):
            x0     = portal_xs[i]     + post_half_x
            x1     = portal_xs[i + 1] - post_half_x
            length = x1 - x0
            beam   = _beam_x(length, bs, "timber_treated",
                             bolt_holes=True, n_hole_ends=2)
            beam   = beam.move(Location((x0, y_mm, z_bot)))
            beam.label = f"long-beam-{side}-span{i + 1}-x{int(x0)}"
            parts.append(beam)

    return parts


# ── Joists ────────────────────────────────────────────────────────────────────

def _joists(
    building: SegalBuilding,
    portal_xs: list[float],
    depth_mm: float,
    z_bot: float,
    overhang_e_w: float = 0.0,
    label_prefix: str = "joist",
) -> list:
    """E-W joists at every intermediate N-S module line.

    Each joist spans between portal beam faces (no overlap with N-S portal beams).
    If overhang_e_w > 0, separate cantilever segments extend beyond the end portals.
    """
    js         = mat.JOIST_TIMBER
    g          = building.grid
    beam_half  = float(mat.BEAM_TIMBER.depth) / 2   # = 25 mm
    first_bay  = next(iter(building.bays.values()))
    depth_mods = first_bay.depth_modules

    parts: list = []
    for m in range(1, depth_mods):           # 1, 2, 3 for a 4-module bay
        y_mm = float(m * g.module_pitch)

        # One segment per bay span (between portal faces)
        for i in range(len(portal_xs) - 1):
            x0     = portal_xs[i]     + beam_half
            x1     = portal_xs[i + 1] - beam_half
            length = x1 - x0
            j      = _beam_x(length, js, "timber_treated")
            j      = j.move(Location((x0, y_mm, z_bot)))
            j.label = f"{label_prefix}-y{int(y_mm)}-span{i + 1}-x{int(x0)}"
            parts.append(j)

        if overhang_e_w > 0:
            # West cantilever: from overhang start to first portal face
            xw0 = portal_xs[0]  - overhang_e_w
            xw1 = portal_xs[0]  + beam_half
            jw  = _beam_x(xw1 - xw0, js, "timber_treated")
            jw  = jw.move(Location((xw0, y_mm, z_bot)))
            jw.label = f"{label_prefix}-y{int(y_mm)}-overhang-west"
            parts.append(jw)

            # East cantilever: from last portal face to overhang end
            xe0 = portal_xs[-1] - beam_half
            xe1 = portal_xs[-1] + overhang_e_w
            je  = _beam_x(xe1 - xe0, js, "timber_treated")
            je  = je.move(Location((xe0, y_mm, z_bot)))
            je.label = f"{label_prefix}-y{int(y_mm)}-overhang-east"
            parts.append(je)

    return parts


# ── Roof assembly ─────────────────────────────────────────────────────────────

def _roof(
    building: SegalBuilding,
    portal_xs: list[float],
    depth_mm: float,
) -> list:
    """Flat Segal roof: joists + woodwool deck + loose felt membrane + shingle.

    Joists span between portal faces (no overlap at intermediate portals).
    Cantilever segments extend mat.ROOF_OVERHANG (600 mm) beyond end portals.
    The deck, membrane, and shingle slabs extend the full overhang on all four sides.
    """
    post_h = building.ground_clearance + building.storey_height
    ov     = float(mat.ROOF_OVERHANG)
    js     = mat.JOIST_TIMBER
    us     = mat.UPSTAND_TIMBER
    parts: list = []

    # --- Roof joists ---
    z_joist_bot = float(post_h - js.width)
    parts.extend(_joists(building, portal_xs, depth_mm, z_joist_bot,
                          overhang_e_w=ov, label_prefix="roof-joist"))

    # --- Deck extents (overhang on all four sides) ---
    total_width_mm = portal_xs[-1] - portal_xs[0]
    deck_x0 = portal_xs[0] - ov
    deck_y0 = -ov
    deck_w  = total_width_mm + 2 * ov
    deck_d  = depth_mm + 2 * ov
    z_deck  = float(post_h)

    ww_t = float(mat.ROOF_WOODWOOL_THICKNESS)
    deck = Box(deck_w, deck_d, ww_t, align=(Align.MIN, Align.MIN, Align.MIN))
    deck = _col(deck, "woodwool")
    deck = deck.move(Location((deck_x0, deck_y0, z_deck)))
    deck.label = "woodwool-deck"
    parts.append(deck)

    z_mem = z_deck + ww_t
    mem_t = 5.0
    mem = Box(deck_w, deck_d, mem_t, align=(Align.MIN, Align.MIN, Align.MIN))
    mem = _col(mem, "membrane")
    mem = mem.move(Location((deck_x0, deck_y0, z_mem)))
    mem.label = "felt-membrane"
    parts.append(mem)

    z_sh = z_mem + mem_t
    sh_t = float(mat.ROOF_SHINGLE_DEPTH)
    sh = Box(deck_w, deck_d, sh_t, align=(Align.MIN, Align.MIN, Align.MIN))
    sh = _col(sh, "shingle")
    sh = sh.move(Location((deck_x0, deck_y0, z_sh)))
    sh.label = "shingle-ballast"
    parts.append(sh)

    # --- Perimeter upstand (sits on top of woodwool; retains shingle) ---
    z_us   = z_deck + ww_t
    us_thk = float(us.depth)   # 50 mm

    for label, x0, y0, run_x in (
        ("upstand-south", deck_x0,          deck_y0,          deck_w),
        ("upstand-north", deck_x0,          deck_y0 + deck_d, deck_w),
    ):
        u = _beam_x(run_x, us, "timber_treated")
        u = u.move(Location((x0, y0, z_us)))
        u.label = label
        parts.append(u)

    inner_d = deck_d - 2 * us_thk
    for label, x0, y0 in (
        ("upstand-west",  deck_x0,          deck_y0 + us_thk),
        ("upstand-east",  deck_x0 + deck_w, deck_y0 + us_thk),
    ):
        u = _beam_y(inner_d, us, "timber_treated")
        u = u.move(Location((x0, y0, z_us)))
        u.label = label
        parts.append(u)

    return parts


# ── Floor ─────────────────────────────────────────────────────────────────────

def _floor(
    building: SegalBuilding,
    portal_xs: list[float],
    layout: dict[str, tuple[int, int]],
) -> list:
    """Floor joists + T&G boarding for enclosed bays only.

    Open lean-to bays (enclosed=False) are skipped entirely.
    Joists span between portal faces within each bay.
    """
    gc        = building.ground_clearance
    g         = building.grid
    js        = mat.JOIST_TIMBER
    fb        = mat.FLOOR_BOARD
    beam_half = float(mat.BEAM_TIMBER.depth) / 2    # = 25 mm
    z_joist   = float(gc - js.width)
    z_board   = float(gc)
    parts: list = []

    for bay_name, (bx_mods, by_mods) in layout.items():
        bay = building.bays[bay_name]
        if not bay.enclosed:
            continue

        bx0        = float(bx_mods * g.module_pitch)
        bx1        = bx0 + float(bay.width_mm(g))
        by0        = float(by_mods * g.module_pitch)
        depth_mods = bay.depth_modules
        x0_span    = bx0 + beam_half
        x1_span    = bx1 - beam_half

        for m in range(1, depth_mods):
            y_mm  = float(m * g.module_pitch)
            j     = _beam_x(x1_span - x0_span, js, "timber_treated")
            j     = j.move(Location((x0_span, y_mm, z_joist)))
            j.label = f"floor-joist-y{int(y_mm)}-{bay_name}"
            parts.append(j)

        board = Box(float(bay.width_mm(g)), float(bay.depth_mm(g)), float(fb.depth),
                    align=(Align.MIN, Align.MIN, Align.MIN))
        board = _col(board, "timber")
        board = board.move(Location((bx0, by0, z_board)))
        board.label = f"floor-boarding-{bay_name}"
        parts.append(board)

    return parts


# ── External walls ────────────────────────────────────────────────────────────

def _panel_offsets(n_modules: int, pitch: int) -> list[tuple[float, float]]:
    """(start_mm, end_mm) for each 600 mm panel band along a face of n_modules.

    Panels sit between the structural posts.  Each post is 50 mm wide, centred
    on a grid line, so each panel starts 25 mm after its left grid line and ends
    25 mm before the next grid line.
    """
    half = pitch // 2 - mat.POST_TIMBER.depth // 2  # = 325 - 25 = 300? No.
    # simpler: start = i * pitch + 25, end = (i+1) * pitch - 25
    half_struct = mat.POST_TIMBER.depth // 2         # = 25 mm
    return [
        (i * pitch + half_struct, (i + 1) * pitch - half_struct)
        for i in range(n_modules)
    ]


def _wall_face_panels(
    building: SegalBuilding,
    bay_name: str,
    face: str,
    bay_x0_mm: float,
    bay_y0_mm: float,
    outer_spec: mat.PanelSpec,
) -> list:
    """All three wall layers for one bay face.

    Panels sit between the structural posts (panel bands only, not structural bands).
    Layers are stacked outward from the building frame:
      inner (plasterboard) → core (woodwool) → outer (cladding)
    """
    g       = building.grid
    bay     = building.bays[bay_name]
    post_h  = building.ground_clearance + building.storey_height   # 2850
    z0      = float(building.ground_clearance)                     # 450
    wall_h  = float(building.storey_height)                        # 2400

    inner_t = float(mat.EXTERNAL_PANEL_INNER.thickness)   # 12 mm plasterboard
    core_t  = float(mat.EXTERNAL_PANEL_CORE.thickness)    # 50 mm woodwool
    outer_t = float(outer_spec.thickness)                  # varies by cladding

    outer_col = _CLADDING_COLOUR.get(outer_spec.material, "fibre_cement")
    parts = []

    if face in ("north", "south"):
        # Wall runs E-W; panels laid along X.
        n_mods   = bay.width_modules
        pitch    = g.module_pitch
        offsets  = _panel_offsets(n_mods, pitch)
        y_grid   = bay_y0_mm + (depth_mm_from_bay(bay, g) if face == "north" else 0.0)

        # Outward direction: +Y for north face, -Y for south face
        sign = +1.0 if face == "north" else -1.0

        # Layer offsets along Y for each face direction.
        # "Inner" (plasterboard) is on the room side; core and outer face outward.
        # sign=+1 (north): outward = +Y, so inner is inward (−Y of grid line).
        # sign=−1 (south): outward = −Y, so inner is inward (+Y of grid line).
        if sign > 0:
            y_inner = y_grid - inner_t   # just inside the grid line
            y_core  = y_grid             # at grid line, extends outward (+Y)
            y_outer = y_grid + core_t
        else:
            y_inner = y_grid             # at grid line, extends inward (+Y)
            y_core  = y_grid - core_t    # extends outward (−Y)
            y_outer = y_grid - core_t - outer_t

        for (xL, xR) in offsets:
            pw = xR - xL
            x0 = bay_x0_mm + xL

            inner = _panel(pw, wall_h, inner_t, "plasterboard")
            inner = inner.move(Location((x0, y_inner, z0)))
            inner.label = f"panel-plasterboard-{face}-x{int(x0)}"
            parts.append(inner)

            core = _panel(pw, wall_h, core_t, "woodwool")
            core = core.move(Location((x0, y_core, z0)))
            core.label = f"panel-woodwool-{face}-x{int(x0)}"
            parts.append(core)

            outer_p = _panel(pw, wall_h, outer_t, outer_col)
            outer_p = outer_p.move(Location((x0, y_outer, z0)))
            outer_p.label = f"panel-cladding-{face}-x{int(x0)}"
            parts.append(outer_p)

    elif face in ("east", "west"):
        # Wall runs N-S; panels laid along Y.
        n_mods  = bay.depth_modules
        pitch   = g.module_pitch
        offsets = _panel_offsets(n_mods, pitch)
        x_grid  = bay_x0_mm + (bay.width_mm(g) if face == "east" else 0.0)

        # Outward direction: +X for east face, -X for west face
        sign = +1.0 if face == "east" else -1.0

        for (yL, yR) in offsets:
            pw = yR - yL
            y0_panel = bay_y0_mm + yL

            # E/W panels are built directly as Box(thickness_X, width_Y, height_Z).
            # sign=+1 (east): outward = +X, inner is on the room side (−X of grid line).
            # sign=−1 (west): outward = −X, inner is on the room side (+X of grid line).
            if sign > 0:
                xi = x_grid - inner_t   # inner just inside grid line
                xc = x_grid             # core at grid line, extends outward (+X)
                xo = x_grid + core_t
            else:
                xi = x_grid             # inner at grid line, extends inward (+X)
                xc = x_grid - core_t    # core extends outward (−X)
                xo = x_grid - core_t - outer_t

            p = Box(inner_t, pw, wall_h, align=(Align.MIN, Align.MIN, Align.MIN))
            p = _col(p, "plasterboard")
            p = p.move(Location((xi, y0_panel, z0)))
            p.label = f"panel-plasterboard-{face}-y{int(y0_panel)}"
            parts.append(p)

            c = Box(core_t, pw, wall_h, align=(Align.MIN, Align.MIN, Align.MIN))
            c = _col(c, "woodwool")
            c = c.move(Location((xc, y0_panel, z0)))
            c.label = f"panel-woodwool-{face}-y{int(y0_panel)}"
            parts.append(c)

            o = Box(outer_t, pw, wall_h, align=(Align.MIN, Align.MIN, Align.MIN))
            o = _col(o, outer_col)
            o = o.move(Location((xo, y0_panel, z0)))
            o.label = f"panel-cladding-{face}-y{int(y0_panel)}"
            parts.append(o)

    return parts


def depth_mm_from_bay(bay, g) -> float:
    """Return the bay's N-S dimension in mm."""
    return float(bay.depth_mm(g))


def _external_walls(
    building: SegalBuilding,
    layout: dict[str, tuple[int, int]],
    outer_panel_specs: dict[str, mat.PanelSpec] | None,
) -> list:
    """All external wall panels for the whole building."""
    g     = building.grid
    outer_panel_specs = outer_panel_specs or {}
    parts = []

    for bay_name, (bx_mods, by_mods) in layout.items():
        bay     = building.bays[bay_name]
        bx0     = float(bx_mods * g.module_pitch)
        by0     = float(by_mods * g.module_pitch)
        o_spec  = outer_panel_specs.get(bay_name, mat.EXTERNAL_PANEL_OUTER)

        for face in ("north", "south", "east", "west"):
            wt = building.get_wall_type(bay_name, face)
            if wt in (WallType.EXTERNAL, WallType.TOOL_WALL):
                # Tool walls use OSB instead of plasterboard on the inner face,
                # but otherwise identical layout — the colour difference
                # communicates the distinction.
                effective_spec = o_spec
                if wt == WallType.TOOL_WALL:
                    effective_spec = mat.EXTERNAL_PANEL_OUTER  # outer unchanged
                parts.extend(
                    _wall_face_panels(
                        building, bay_name, face,
                        bx0, by0, effective_spec,
                    )
                )
            elif wt == WallType.WINDOW:
                # Glazed face: one glass panel per bay face (simplified)
                parts.extend(
                    _window_face(building, bay_name, face, bx0, by0)
                )

    return parts


def _window_face(
    building: SegalBuilding,
    bay_name: str,
    face: str,
    bx0: float,
    by0: float,
) -> list:
    """A single glass panel representing a glazed face (simplified)."""
    g      = building.grid
    bay    = building.bays[bay_name]
    z0     = float(building.ground_clearance)
    wall_h = float(building.storey_height)
    t_glass = 50.0   # 50 mm nominal thickness (glazing + lining)

    if face in ("north", "south"):
        w = float(bay.width_mm(g))
        y_grid = by0 + (float(bay.depth_mm(g)) if face == "north" else 0.0)
        sign   = +1.0 if face == "north" else -1.0
        y0     = y_grid if sign > 0 else y_grid - t_glass
        glass  = Box(w, t_glass, wall_h, align=(Align.MIN, Align.MIN, Align.MIN))
        glass  = _col(glass, "glass")
        return [glass.move(Location((bx0, y0, z0)))]
    else:
        d = float(bay.depth_mm(g))
        x_grid = bx0 + (float(bay.width_mm(g)) if face == "east" else 0.0)
        sign   = +1.0 if face == "east" else -1.0
        x0     = x_grid if sign > 0 else x_grid - t_glass
        glass  = Box(t_glass, d, wall_h, align=(Align.MIN, Align.MIN, Align.MIN))
        glass  = _col(glass, "glass")
        return [glass.move(Location((x0, by0, z0)))]


# ── Foundations ───────────────────────────────────────────────────────────────

def _foundations(
    building: SegalBuilding,
    layout: dict[str, tuple[int, int]],
) -> list:
    """Concrete pad and paving-slab cap at each unique post position."""
    g     = building.grid
    pad_w = float(mat.FOUNDATION_PAD_WIDTH)     # 600 mm square
    pad_d = float(mat.FOUNDATION_PAD_DEPTH)     # 500 mm deep (below ground)
    slab_t = 40.0                               # paving slab cap (mm)
    parts = []

    post_xy: set[tuple[float, float]] = set()

    for bay_name, (bx_mods, by_mods) in layout.items():
        bay = building.bays[bay_name]
        for dx in (0, bay.width_modules):
            for dy in (0, bay.depth_modules):
                x = float((bx_mods + dx) * g.module_pitch)
                y = float((by_mods + dy) * g.module_pitch)
                post_xy.add((x, y))

    for (x, y) in sorted(post_xy):
        # Concrete base (below ground, z = -pad_d to z = 0)
        pad = Box(pad_w, pad_w, pad_d, align=(Align.CENTER, Align.CENTER, Align.MAX))
        pad = _col(pad, "concrete")
        pad = pad.move(Location((x, y, 0.0)))
        pad.label = f"pad-x{int(x)}-y{int(y)}"
        parts.append(pad)

        # Paving slab cap (z = -slab_t to z = 0)
        slab = Box(pad_w, pad_w, slab_t, align=(Align.CENTER, Align.CENTER, Align.MAX))
        slab = _col(slab, "concrete")
        slab = slab.move(Location((x, y, 0.0)))
        slab.label = f"slab-x{int(x)}-y{int(y)}"
        parts.append(slab)

    return parts


# ── Main assembly ─────────────────────────────────────────────────────────────

def build_cad(
    building: SegalBuilding,
    outer_panel_specs: dict[str, mat.PanelSpec] | None = None,
    include_foundations: bool = True,
    include_bolts: bool = True,
    include_floor: bool = True,
    include_roof: bool = True,
    include_walls: bool = True,
) -> Compound:
    """Build a full CAD assembly of the building.

    Args:
        building: A configured SegalBuilding instance.
        outer_panel_specs: Optional dict mapping bay names to a PanelSpec for
            the outer cladding (same interface as generate_bom).  Any bay not
            listed uses the default HardiePlank spec.
        include_foundations: If True, concrete pads and paving slab caps are
            included below ground level.  Set False when building on an
            existing slab.
        include_bolts: If True, M12 bolt assemblies are shown at structural
            joints.  Set False for faster generation and cleaner visual.

    Returns:
        A labelled Compound hierarchy importable into Fusion 360 / FreeCAD:
            Segal Build
            +-- Structure       (posts, beams, longitudinal beams)
            +-- Bolts           (if include_bolts; M12 assemblies)
            +-- Floor           (joists + T&G boarding)
            +-- Roof            (joists, woodwool, membrane, shingle, upstand)
            +-- Walls
            |   +-- <bay_name>  (one sub-component per bay)
            |       +-- north / south / east / west  (only rendered faces)
            +-- Foundations     (if include_foundations)

        Faces with WallType.OPEN or WallType.NONE produce no geometry and
        therefore no sub-component — open lean-to fronts simply won't appear.
        Set open faces explicitly before calling build_cad:
            building.set_wall("lean_to_1", "south", WallType.NONE)

    Raises:
        ValueError: If the building is not a simple E-W linear chain.
    """
    layout = _linear_layout(building)
    g      = building.grid
    outer_panel_specs = outer_panel_specs or {}

    # Global dimensions
    first_bay      = next(iter(building.bays.values()))
    depth_mm       = float(first_bay.depth_mm(g))
    total_width_mm = float(sum(bay.width_mm(g) for bay in building.bays.values()))

    # Portal-frame X positions: every bay boundary (including start and end)
    portal_xs: list[float] = [0.0]
    x_acc = 0.0
    for bay in building.bays.values():
        x_acc += bay.width_mm(g)
        portal_xs.append(float(x_acc))

    # ── Structure ────────────────────────────────────────────────────────────
    # Each portal frame becomes its own sub-compound containing its members
    # *and* the bolts that join them — bolts live with the assembly they fix.
    frame_compounds: list = []
    n_portals = len(portal_xs)
    for i, x_mm in enumerate(portal_xs):
        s, b = _portal_frame(building, x_mm, depth_mm, include_bolts)
        if i == 0:
            frame_label = f"frame-{i + 1:02d}-west-end"
        elif i == n_portals - 1:
            frame_label = f"frame-{i + 1:02d}-east-end"
        else:
            frame_label = f"frame-{i + 1:02d}"
        frame_children = list(s)
        if b:
            frame_children.append(Compound(children=b, label="bolts"))
        frame_compounds.append(Compound(children=frame_children, label=frame_label))

    # Longitudinal eaves beams grouped by side
    long_beams = _longitudinal_beams(building, portal_xs, depth_mm, include_bolts)
    frame_compounds.append(Compound(children=long_beams, label="longitudinal-beams"))

    top_children: list = [
        Compound(children=frame_compounds, label="02-structural-frame")
    ]

    # ── Floor / Roof ─────────────────────────────────────────────────────────
    if include_floor:
        floor_parts = _floor(building, portal_xs, layout)
        if floor_parts:
            top_children.append(Compound(children=floor_parts, label="03-floor"))

    if include_roof:
        top_children.append(
            Compound(children=_roof(building, portal_xs, depth_mm), label="05-roof")
        )

    # ── Walls: per bay → per face ─────────────────────────────────────────────
    if include_walls:
        bay_compounds: list = []
        for bay_name, (bx_mods, by_mods) in layout.items():
            bay    = building.bays[bay_name]
            bx0    = float(bx_mods * g.module_pitch)
            by0    = float(by_mods * g.module_pitch)
            o_spec = outer_panel_specs.get(bay_name, mat.EXTERNAL_PANEL_OUTER)

            face_compounds: list = []
            for face in ("north", "south", "east", "west"):
                wt = building.get_wall_type(bay_name, face)
                if wt in (WallType.EXTERNAL, WallType.TOOL_WALL):
                    face_parts = _wall_face_panels(
                        building, bay_name, face, bx0, by0, o_spec
                    )
                    face_compounds.append(
                        Compound(children=face_parts, label=face)
                    )
                elif wt == WallType.WINDOW:
                    face_parts = _window_face(building, bay_name, face, bx0, by0)
                    face_compounds.append(
                        Compound(children=face_parts, label=f"{face} (glazed)")
                    )
                # WallType.OPEN / NONE: intentionally open — no geometry added

            if face_compounds:
                bay_compounds.append(
                    Compound(children=face_compounds, label=bay_name)
                )

        if bay_compounds:
            top_children.append(Compound(children=bay_compounds, label="04-walls"))

    # ── Foundations ──────────────────────────────────────────────────────────
    if include_foundations:
        top_children.insert(
            0,
            Compound(
                children=_foundations(building, layout),
                label="01-foundations",
            ),
        )

    return Compound(children=top_children, label="Segal Build")


# ── Export helpers ────────────────────────────────────────────────────────────

def export(
    assembly: Compound,
    path: str,
    fmt: str = "step",
) -> None:
    """Export a CAD assembly to a file.

    Args:
        assembly: The Compound returned by build_cad().
        path: Output file path (extension is added if not present).
        fmt: One of "step", "stl", "brep".  Defaults to "step".
            STEP is recommended as the primary format — it preserves solid
            geometry and imports cleanly into FreeCAD, Fusion 360, etc.
            Convert to STL/OBJ for mesh-based renderers.
    """
    fmt = fmt.lower()
    if fmt == "step":
        if not path.endswith(".step"):
            path += ".step"
        export_step(assembly, path)
    elif fmt == "stl":
        if not path.endswith(".stl"):
            path += ".stl"
        export_stl(assembly, path)
    elif fmt == "brep":
        if not path.endswith(".brep"):
            path += ".brep"
        export_brep(assembly, path)
    else:
        raise ValueError(f"Unknown format '{fmt}'. Use 'step', 'stl', or 'brep'.")

    print(f"Exported {fmt.upper()} -> {path}")
