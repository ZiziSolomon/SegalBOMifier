# segal_method -- BOM Generator for Segal Method Buildings

A Python module for estimating materials needed for timber-frame buildings
using Walter Segal's self-build method. Define a building as connected
modular bays on a tartan grid, and get a detailed Bill of Materials.

Based on Jon Broome's description in *The Architects' Journal* special issue.

## Setup

No external dependencies -- just Python 3.10+.

Clone or copy the `segal_method/` folder into your project, then:

```python
from segal_method import SegalBuilding, SegalGrid, WallType
```

To run the tests:

```
python -m pytest segal_method/tests/ -v
```

## Quick Start

```python
from segal_method import SegalBuilding, SegalGrid, WallType

# 1. Define the grid
#    panel_width: width of standard building panels (mm), usually 600 or 1200
#    structural_thickness: column/batten gap width (mm), usually 50
grid = SegalGrid(panel_width=600, structural_thickness=50)

# 2. Create a building
#    storey_height: floor-to-ceiling per storey (mm)
#    ground_clearance: undercroft height below ground floor (mm)
building = SegalBuilding(grid, storey_height=2400, ground_clearance=450)

# 3. Add bays (width and depth are in module counts, not mm)
building.add_bay("kitchen", width=4, depth=4)
building.add_bay("living", width=3, depth=4)

# 4. Connect adjacent bays along shared frame lines
#    Connected faces must have the same module count
building.connect("kitchen", "east", "living", "west")

# 5. Generate and print the BOM
bom = building.generate_bom()
print(bom.to_table())
```

## Concepts

### The Tartan Grid

Everything in a Segal building sits on a **tartan grid** -- alternating bands
of panel-width (600mm) and structural-thickness (50mm). The **module pitch**
(650mm with defaults) is the centre-to-centre distance between grid lines.

A bay that is 4 modules wide spans `4 * 650 = 2600mm` column-to-column.

### Bays

A **bay** is a rectangular structural unit defined by its module counts:

```python
building.add_bay("bedroom", width=3, depth=4, storeys=2)
```

- `width` -- modules along the north/south faces
- `depth` -- modules along the east/west faces
- `storeys` -- 1 or 2 (affects post height, floor/ceiling count, wall panels)

Each bay has four faces: `north`, `south`, `east`, `west`.

### Connections

Bays are connected explicitly. A connection declares that two faces share
a structural frame line:

```python
building.connect("kitchen", "east", "living", "west")
```

The connected faces must span the same number of modules (e.g. both have
`depth=4`), otherwise you get a `ValueError`.

### Wall Types

Each face gets a wall type, auto-detected by default:

| Type | When | What it produces |
|---|---|---|
| `EXTERNAL` | Perimeter face (default) | Fibre cement + woodwool + plasterboard sandwich |
| `OPEN` | Connected face (default) | Nothing -- passage between bays |
| `WINDOW` | Override only | Glass panes in aluminium tracks + timber lining |
| `PARTITION` | Override only | Woodwool + plasterboard both sides |
| `NONE` | Override only | No infill (open to outside, e.g. balcony) |

Override any face:

```python
building.set_wall("living", "east", WallType.WINDOW)
building.set_wall("kitchen", "east", WallType.PARTITION)
```

## BOM Output

The BOM covers 9 categories in construction order:

1. **Foundations** -- concrete pads, paving slabs, lead seals, gravel
2. **Structural Frame** -- posts, beams (single/double), joists, bracing, bolts
3. **Roof** -- woodwool deck, felt membrane, shingle, fascia, upstand
4. **Floors** -- T&G boarding, insulation, support panels
5. **External Walls** -- fibre cement, woodwool, plasterboard, battens, bolts
6. **Windows** -- glass panes, aluminium tracks, linings, beads
7. **Partitions** -- woodwool, plasterboard (both sides), battens, bolts
8. **Ceilings** -- plasterboard, battens, fire lining (2-storey)
9. **Fixings & Sundries** -- screws (with box counts), wood stain

### Output Formats

```python
bom = building.generate_bom()

# Formatted text table
print(bom.to_table())

# CSV file
bom.to_csv("materials.csv")

# List of dicts (for programmatic use / pricing integration)
for item in bom.to_dicts():
    print(item["description"], item["quantity"], item["unit"])
```

## Full Example -- 4-Bay Bungalow

```python
from segal_method import SegalBuilding, SegalGrid, WallType

grid = SegalGrid(panel_width=600, structural_thickness=50)
building = SegalBuilding(grid, storey_height=2400, ground_clearance=450)

# Four bays in a 2x2 layout
building.add_bay("kitchen_dining", width=4, depth=4)
building.add_bay("living",        width=3, depth=4)
building.add_bay("bedroom_1",     width=4, depth=3)
building.add_bay("bedroom_2",     width=3, depth=3)

# Connect them
building.connect("kitchen_dining", "east",  "living",    "west")
building.connect("kitchen_dining", "south", "bedroom_1", "north")
building.connect("living",         "south", "bedroom_2", "north")
building.connect("bedroom_1",      "east",  "bedroom_2", "west")

# Windows on the north and east perimeter
building.set_wall("kitchen_dining", "north", WallType.WINDOW)
building.set_wall("living",         "east",  WallType.WINDOW)

# Partition between kitchen and living room
building.set_wall("kitchen_dining", "east", WallType.PARTITION)

bom = building.generate_bom()
print(bom.to_table())
print(f"Total floor area: {building.total_floor_area_m2():.1f} m2")
print(f"Unique posts: {building.unique_post_count()}")
```

## Customising Material Specs

Default material dimensions are in `materials.py`. You can modify them
directly if your project uses different sections or panel sizes:

```python
from segal_method import materials as mat

# Use heavier beam sections
mat.BEAM_TIMBER = mat.TimberSpec(width=250, depth=50, grade="C24",
                                 treatment="pressure-treated",
                                 description="Structural beam")
```

## What's Next

- **Selco pricing**: the `bom.to_dicts()` output is structured for plugging
  into a price lookup. Each item has `material`, `size`, and `quantity` fields
  ready to match against a product catalogue.
