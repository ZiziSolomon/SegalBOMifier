"""Interactive wizard for generating Segal lean-to price estimate variants.

Run as::

    python -m segal_method.estimate_wizard

or call :func:`run` programmatically.

Workflow
--------
1. How many lean-to bays?  (each is one 4×4-module bay, ~2.6×2.6m on plan)
2. Connected to the office or a separate structure?
3. Cladding choice for the lean-to bays.
4. Cladding choice for the office.
→  Writes a CSV named to describe the selected variant.
"""

from pathlib import Path

from . import SegalBuilding, SegalGrid
from . import materials as mat
from .bom import BOM


# ── Cladding options ──────────────────────────────────────────────────────────

CLADDING_OPTIONS = [
    (
        "hardieplank",
        "HardiePlank fibre cement  (~£82/m²)  low maintenance, fire-rated  (Jewson)",
        mat.EXTERNAL_PANEL_OUTER,
    ),
    (
        "cedral",
        "Cedral Lap fibre cement   (~£53/m²)  similar performance, cheaper  (specialist merchant)  [est. price]",
        mat.CEDRAL_LAP_OUTER,
    ),
    (
        "shiplap",
        "Selco softwood shiplap    (~£25/m²)  cheapest; needs treatment every ~7 yrs  (Selco)",
        mat.FEATHEREDGE_OUTER,
    ),
    (
        "wiggly_steel",
        "Corrugated steel sheet    (~£10/m²)  very durable, fully fire-rated, minimal maintenance  (metal merchant / TP)  [est. price]",
        mat.WIGGLY_STEEL_OUTER,
    ),
]

# Keyed lookup
_SPEC_BY_KEY = {key: spec for key, _, spec in CLADDING_OPTIONS}
_LABEL_BY_KEY = {key: label for key, label, _ in CLADDING_OPTIONS}


# ── Input helpers ─────────────────────────────────────────────────────────────

def _ask_int(prompt: str, min_val: int, max_val: int) -> int:
    while True:
        raw = input(prompt).strip()
        if raw.isdigit():
            val = int(raw)
            if min_val <= val <= max_val:
                return val
        print(f"  Please enter a whole number between {min_val} and {max_val}.")


def _ask_choice(prompt: str, options: list) -> str:
    """options: list of (key, label, ...) tuples.  Returns key."""
    print(prompt)
    for i, (key, label, *_) in enumerate(options, 1):
        print(f"  {i})  {label}")
    while True:
        raw = input("Choice: ").strip()
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(options):
                return options[idx - 1][0]
        print(f"  Please enter a number between 1 and {len(options)}.")


def _ask_yes_no(prompt: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input(f"{prompt} {hint}: ").strip().lower()
        if raw in ("", "y", "yes"):
            return True if (raw in ("y", "yes") or default) else False
        if raw in ("n", "no"):
            return False
        print("  Please enter y or n.")


# ── Building factories ────────────────────────────────────────────────────────

def _build_connected(
    n_leanto: int,
    leanto_spec,
    office_spec,
    include_foundations: bool,
) -> BOM:
    """Single building: N open lean-to bays + 1 enclosed office, all connected."""
    grid = SegalGrid()
    b = SegalBuilding(grid)

    bay_names = [f"lean_to_{i + 1}" for i in range(n_leanto)]
    for name in bay_names:
        b.add_bay(name, width=4, depth=4, enclosed=False)
    b.add_bay("office", width=4, depth=4, enclosed=True)

    for i in range(n_leanto - 1):
        b.connect(bay_names[i], "east", bay_names[i + 1], "west")
    b.connect(bay_names[-1], "east", "office", "west")

    outer_specs = {name: leanto_spec for name in bay_names}
    outer_specs["office"] = office_spec

    return b.generate_bom(
        include_foundations=include_foundations,
        outer_panel_specs=outer_specs,
    )


def _build_separate(
    n_leanto: int,
    leanto_spec,
    office_spec,
    include_foundations: bool,
) -> tuple[BOM, BOM]:
    """Two independent structures; returns (lean_to_bom, office_bom)."""
    grid = SegalGrid()

    # Lean-to
    b_lt = SegalBuilding(grid)
    bay_names = [f"lean_to_{i + 1}" for i in range(n_leanto)]
    for name in bay_names:
        b_lt.add_bay(name, width=4, depth=4, enclosed=False)
    for i in range(n_leanto - 1):
        b_lt.connect(bay_names[i], "east", bay_names[i + 1], "west")
    bom_lt = b_lt.generate_bom(
        include_foundations=include_foundations,
        outer_panel_specs={name: leanto_spec for name in bay_names},
    )

    # Office
    b_off = SegalBuilding(grid)
    b_off.add_bay("office", width=4, depth=4, enclosed=True)
    bom_off = b_off.generate_bom(
        include_foundations=include_foundations,
        outer_panel_specs={"office": office_spec},
    )

    return bom_lt, bom_off


# ── CSV filename builder ──────────────────────────────────────────────────────

def _filename(n_leanto: int, layout: str, leanto_key: str, office_key: str) -> str:
    return f"lean_to_{n_leanto}bay_{layout}_lean-{leanto_key}_office-{office_key}.csv"


# ── Main entry point ──────────────────────────────────────────────────────────

def run(output_dir: Path = Path(".")) -> None:
    """Run the interactive wizard and write the CSV(s) to *output_dir*."""
    print()
    print("=" * 60)
    print("  Segal lean-to price estimator")
    print("=" * 60)
    print()
    print("Each lean-to 'module' is one 4×4-grid bay (~2.6×2.6 m on plan),")
    print("the same size as the office.  The current build has 2 lean-to bays.")
    print()

    n_leanto = _ask_int("How many lean-to bays? (1–8): ", 1, 8)
    print()

    layout_opts = [
        ("connected", "Connected — shares a frame line with the office"),
        ("separate",  "Separate  — free-standing structure, office built independently"),
    ]
    layout = _ask_choice("Is the office connected to the lean-to or a separate structure?", layout_opts)
    print()

    leanto_key = _ask_choice("Cladding for the lean-to bays:", CLADDING_OPTIONS)
    print()
    office_key = _ask_choice("Cladding for the office:", CLADDING_OPTIONS)
    print()

    include_foundations = not _ask_yes_no(
        "Building on an existing slab or patio (skip foundations)?", default=True
    )
    print()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    leanto_spec = _SPEC_BY_KEY[leanto_key]
    office_spec = _SPEC_BY_KEY[office_key]

    if layout == "connected":
        bom = _build_connected(n_leanto, leanto_spec, office_spec, include_foundations)
        estimate = bom.consolidate().price()
        fname = _filename(n_leanto, layout, leanto_key, office_key)
        fpath = output_dir / fname
        estimate.to_csv(str(fpath))
        print(f"  Written → {fpath}")
        print(f"  Total ex VAT:  £{estimate.total_ex_vat:,.2f}")
        print(f"  Total inc VAT: £{estimate.total_inc_vat:,.2f}")

    else:  # separate
        bom_lt, bom_off = _build_separate(
            n_leanto, leanto_spec, office_spec, include_foundations
        )
        est_lt  = bom_lt.consolidate().price()
        est_off = bom_off.consolidate().price()

        fname_lt  = f"lean_to_{n_leanto}bay_separate_lean-{leanto_key}.csv"
        fname_off = f"office_separate_office-{office_key}.csv"
        fpath_lt  = output_dir / fname_lt
        fpath_off = output_dir / fname_off

        est_lt.to_csv(str(fpath_lt))
        est_off.to_csv(str(fpath_off))

        combined = est_lt.total_ex_vat + est_off.total_ex_vat
        combined_inc = est_lt.total_inc_vat + est_off.total_inc_vat

        print(f"  Lean-to → {fpath_lt}")
        print(f"            £{est_lt.total_ex_vat:,.2f} ex VAT")
        print(f"  Office  → {fpath_off}")
        print(f"            £{est_off.total_ex_vat:,.2f} ex VAT")
        print(f"  Combined:  £{combined:,.2f} ex VAT  /  £{combined_inc:,.2f} inc VAT")

    print()
    print("Note: prices marked [est.] are estimates — verify with local supplier.")
    print()


if __name__ == "__main__":
    run()
