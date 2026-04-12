"""Pricing module for Segal method BOM cost estimation.

Reads selco_catalogue.json, matches each BOM item to a catalogue entry,
and applies cut-optimisation for timber and pack-optimisation for bolts.

Usage::

    from segal_method import SegalBuilding
    bom = building.generate_bom(include_foundations=False)
    estimate = bom.price()
    print(estimate.to_table())
"""

import csv
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .bom import BOM, BOMItem


CATALOGUE_PATH = Path(__file__).parent / "selco_catalogue.json"

# Sentinel returned by _price_item to silently skip an item (not unpriced, not priced)
_SKIP = object()


def _load_catalogue() -> dict:
    with open(CATALOGUE_PATH, encoding="utf-8") as f:
        return json.load(f)


# ── Cutting / packing optimisers ──────────────────────────────────────────────

def _cheapest_timber(
    n_pieces: int,
    piece_mm: int,
    lengths_data: list[dict],
) -> tuple[float, float, str]:
    """Return (total_ex, total_inc, note) for cheapest way to buy *n_pieces* of *piece_mm*.

    Tries every stock length and picks the one that minimises ex-VAT cost.
    A stock length that is shorter than *piece_mm* is skipped.
    """
    if n_pieces <= 0:
        return 0.0, 0.0, ""

    best_ex = float("inf")
    best_inc = 0.0
    best_note = ""

    for entry in lengths_data:
        stock_mm = int(round(entry["length_m"] * 1000))
        if stock_mm < piece_mm:
            continue
        pieces_per_stock = stock_mm // piece_mm
        n_stocks = math.ceil(n_pieces / pieces_per_stock)
        cost_ex = n_stocks * entry["price_ex_vat"]
        cost_inc = n_stocks * entry["price_inc_vat"]
        if cost_ex < best_ex:
            best_ex = cost_ex
            best_inc = cost_inc
            best_note = f"{n_stocks}× {entry['length_m']}m"

    if best_ex == float("inf"):
        return 0.0, 0.0, "no stock long enough"

    return round(best_ex, 2), round(best_inc, 2), best_note


def _cheapest_pack(
    n_items: int,
    packs_data: list[dict],
) -> tuple[float, float, str]:
    """Return (total_ex, total_inc, note) for cheapest pack combination for *n_items*."""
    if n_items <= 0:
        return 0.0, 0.0, ""

    best_ex = float("inf")
    best_inc = 0.0
    best_note = ""

    for pack in packs_data:
        n_packs = math.ceil(n_items / pack["qty"])
        cost_ex = n_packs * pack["price_ex_vat"]
        cost_inc = n_packs * pack["price_inc_vat"]
        if cost_ex < best_ex:
            best_ex = cost_ex
            best_inc = cost_inc
            best_note = f"{n_packs}× pack-{pack['qty']}"

    if best_ex == float("inf"):
        return 0.0, 0.0, ""

    return round(best_ex, 2), round(best_inc, 2), best_note


# ── Field parsers ──────────────────────────────────────────────────────────────

def _parse_length_mm(text: str) -> Optional[int]:
    """Extract integer mm from strings like '2850mm'."""
    m = re.match(r"^(\d+)mm$", text.strip())
    return int(m.group(1)) if m else None


def _parse_area_m2(text: str) -> Optional[float]:
    """Extract float m² from strings like '34.2 m2' or '6.8 m2'."""
    m = re.search(r"([\d.]+)\s*m2", text)
    return float(m.group(1)) if m else None


def _parse_linear_m(text: str) -> Optional[float]:
    """Extract float metres from strings like '28.8m'."""
    m = re.match(r"([\d.]+)m$", text.strip())
    return float(m.group(1)) if m else None


# ── Price output types ─────────────────────────────────────────────────────────

@dataclass
class PriceLine:
    """One priced row in a cost estimate."""

    category: str
    description: str
    qty_purchased: str      # e.g. "4 lengths", "2 sheets", "3× pack-10"
    unit_price_ex: float    # ex-VAT per purchased unit
    total_ex: float
    total_inc: float
    note: str = ""          # e.g. "2× 4.8m per piece × 2 lengths"


@dataclass
class PriceEstimate:
    """Full cost estimate produced from a BOM."""

    priced: list[PriceLine] = field(default_factory=list)
    unpriced: list[tuple[str, str]] = field(default_factory=list)  # (category, description)

    @property
    def total_ex_vat(self) -> float:
        return round(sum(p.total_ex for p in self.priced), 2)

    @property
    def total_inc_vat(self) -> float:
        return round(sum(p.total_inc for p in self.priced), 2)

    def to_table(self) -> str:
        """Return a formatted text table of the cost estimate."""
        headers = ["Category", "Description", "Qty purchased", "Unit £ ex", "Total £ ex", "Total £ inc", "Note"]
        QTY_COL = -1  # no right-aligned column; keep all left

        all_rows = [
            [
                p.category,
                p.description,
                p.qty_purchased,
                f"{p.unit_price_ex:.2f}",
                f"{p.total_ex:.2f}",
                f"{p.total_inc:.2f}",
                p.note,
            ]
            for p in self.priced
        ]

        widths = [len(h) for h in headers]
        for row in all_rows:
            for ci, val in enumerate(row):
                widths[ci] = max(widths[ci], len(str(val)))

        def fmt_row(vals: list) -> str:
            return "  " + " ".join(f"{v:<{w}}" for w, v in zip(widths, vals))

        row_width = 2 + sum(widths) + len(widths) - 1
        rule = "=" * row_width
        thin = "  " + "-" * (row_width - 2)

        lines = [rule, "COST ESTIMATE -- SEGAL METHOD BUILD (Selco trade prices ex VAT)", rule]

        # Group priced lines by category
        cats_seen: dict[str, list[PriceLine]] = {}
        for p in self.priced:
            cats_seen.setdefault(p.category, []).append(p)

        for cat, cat_lines in cats_seen.items():
            heading = f"-- {cat.upper()} "
            lines.append("")
            lines.append(heading + "-" * max(0, row_width - len(heading)))
            lines.append(fmt_row(headers))
            lines.append(thin)
            for p in cat_lines:
                row = [
                    p.category, p.description,
                    p.qty_purchased,
                    f"{p.unit_price_ex:.2f}",
                    f"{p.total_ex:.2f}",
                    f"{p.total_inc:.2f}",
                    p.note,
                ]
                lines.append(fmt_row(row))
            cat_ex = sum(p.total_ex for p in cat_lines)
            cat_inc = sum(p.total_inc for p in cat_lines)
            lines.append(thin)
            lines.append(fmt_row(
                ["", f"  Subtotal: {cat}", "", "", f"{cat_ex:.2f}", f"{cat_inc:.2f}", ""]
            ))

        lines.append("")
        lines.append(rule)
        lines.append(fmt_row(
            ["", "TOTAL (Selco items only)", "", "", f"{self.total_ex_vat:.2f}", f"{self.total_inc_vat:.2f}", ""]
        ))
        lines.append(rule)

        if self.unpriced:
            lines.append("")
            lines.append("Items NOT priced (specialist supplier or not in catalogue):")
            for cat, desc in self.unpriced:
                lines.append(f"  [{cat}] {desc}")

        return "\n".join(lines)

    def to_csv(self, path: str) -> None:
        """Write the estimate to a CSV suitable for Google Sheets.

        - Numbers are bare numerics (no £), so Sheets can sum/format them
        - Category subtotal rows separate each section
        - Unpriced items follow in a second block at the bottom
        - UTF-8 BOM so Excel/Sheets auto-detects encoding correctly
        """
        # Group priced lines by category (preserve insertion order)
        cats: dict[str, list[PriceLine]] = {}
        for p in self.priced:
            cats.setdefault(p.category, []).append(p)

        # Deduplicate unpriced (same category+description can repeat per face)
        seen_unpriced: set[tuple[str, str]] = set()
        unique_unpriced = []
        for cat, desc in self.unpriced:
            key = (cat, desc)
            if key not in seen_unpriced:
                seen_unpriced.add(key)
                unique_unpriced.append((cat, desc))

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)

            # ── Priced section ──
            writer.writerow([
                "Category", "Description", "Qty purchased",
                "Unit price ex VAT (£)", "Total ex VAT (£)", "Total inc VAT (£)", "Note",
            ])
            for cat, lines in cats.items():
                for p in lines:
                    writer.writerow([
                        p.category, p.description, p.qty_purchased,
                        round(p.unit_price_ex, 2), round(p.total_ex, 2), round(p.total_inc, 2),
                        p.note,
                    ])
                # Subtotal row
                cat_ex = round(sum(p.total_ex for p in lines), 2)
                cat_inc = round(sum(p.total_inc for p in lines), 2)
                writer.writerow(["", f"Subtotal: {cat}", "", "", cat_ex, cat_inc, ""])

            # Grand total
            writer.writerow([])
            writer.writerow([
                "", "TOTAL (Selco items only)", "", "",
                self.total_ex_vat, self.total_inc_vat, "",
            ])

            # ── Unpriced section ──
            if unique_unpriced:
                writer.writerow([])
                writer.writerow(["NOT PRICED (specialist supplier or not in Selco catalogue)"])
                writer.writerow(["Category", "Description"])
                for cat, desc in unique_unpriced:
                    writer.writerow([cat, desc])


# ── Timber catalogue-key resolver ─────────────────────────────────────────────

def _timber_key(item: BOMItem) -> Optional[str]:
    """Map a BOM timber item to a selco_catalogue.json timber key."""
    desc = item.description.lower()
    mat_lower = item.material.lower()

    if "post" in desc:
        return "100x47mm_C16_treated"
    if "beam" in desc:
        return "200x47mm_C24_treated"
    if "joist" in desc and "bearer" not in desc:
        return "200x47mm_C16_treated"
    if "bearer" in desc or "sole plate" in desc or "wall block" in desc:
        return "50x47mm_treated"
    if "upstand" in desc:
        return "100x47mm_C16_treated"
    if "capping" in desc:
        return "50x25mm_roof_batten"
    if "batten" in desc or "brace" in desc:
        return "50x25mm_treated"
    # Fascia (200×25mm) is not stocked at Selco
    return None


# ── Main pricer ────────────────────────────────────────────────────────────────

class Pricer:
    """Prices a BOM against the Selco catalogue.

    Instantiating loads the catalogue once; call :meth:`price_bom` for each BOM.
    """

    def __init__(self, catalogue: Optional[dict] = None):
        self._cat = catalogue if catalogue is not None else _load_catalogue()

    def price_bom(self, bom: BOM) -> PriceEstimate:
        """Return a :class:`PriceEstimate` for the given BOM."""
        estimate = PriceEstimate()
        for item in bom.items:
            result = self._price_item(item)
            if result is _SKIP:
                continue
            elif result is not None:
                estimate.priced.append(result)
            else:
                estimate.unpriced.append((item.category, item.description))
        return estimate

    def _price_item(self, item: BOMItem) -> Optional[PriceLine]:
        """Try to price one BOM item. Returns None if unpriced."""
        desc = item.description.lower()
        mat_lower = item.material.lower()
        cat = self._cat

        # ── Structural timber pieces (unit="nr", length in length_or_area) ──
        if item.unit == "nr" and (piece_mm := _parse_length_mm(item.length_or_area)):
            key = _timber_key(item)
            if key:
                # Cross-brace: BOM section is 100×25mm; approximate with 50×25mm × 2
                multiplier = 2 if "brace" in desc else 1
                n_pieces = int(item.quantity) * multiplier
                note_prefix = "2× 50×25mm per BOM piece; " if multiplier == 2 else ""
                timber = cat["timber"][key]
                cost_ex, cost_inc, note = _cheapest_timber(
                    n_pieces, piece_mm, timber["lengths"]
                )
                if not note or "no stock" in note:
                    return None
                return PriceLine(
                    category=item.category,
                    description=item.description,
                    qty_purchased=note,
                    unit_price_ex=timber["lengths"][0]["price_ex_vat"],
                    total_ex=cost_ex,
                    total_inc=cost_inc,
                    note=note_prefix + f"{int(item.quantity * multiplier)} pcs × {piece_mm}mm",
                )

        # ── Pre-counted lengths (unit="lengths", already N × ~4.8m stock) ──
        if item.unit == "lengths":
            key = _timber_key(item)
            if key:
                timber = cat["timber"][key]
                # Find the entry closest to 4.8m (or next shorter available)
                target_mm = 4800
                entry = _find_closest_length(timber["lengths"], target_mm)
                if entry is None:
                    entry = timber["lengths"][-1]  # longest available
                n = int(item.quantity)
                cost_ex = round(n * entry["price_ex_vat"], 2)
                cost_inc = round(n * entry["price_inc_vat"], 2)
                return PriceLine(
                    category=item.category,
                    description=item.description,
                    qty_purchased=f"{n} lengths @ {entry['length_m']}m",
                    unit_price_ex=entry["price_ex_vat"],
                    total_ex=cost_ex,
                    total_inc=cost_inc,
                )

        # ── Frame joint bolts (M12×150mm) ──
        if "frame joint bolt" in desc:
            cost_ex, cost_inc, note = _cheapest_pack(
                int(item.quantity), cat["fixings"]["m12_150_bolt"]["packs"]
            )
            return PriceLine(
                item.category, item.description,
                qty_purchased=note,
                unit_price_ex=cat["fixings"]["m12_150_bolt"]["packs"][0]["price_per_bolt_ex"],
                total_ex=cost_ex, total_inc=cost_inc,
                note=f"{item.quantity} nr needed",
            )

        # ── Joist-to-bearer bolts (M10×100mm) ──
        if "joist-to-bearer" in desc or "joist coach" in desc.replace("-", ""):
            cost_ex, cost_inc, note = _cheapest_pack(
                int(item.quantity), cat["fixings"]["m10_100_bolt"]["packs"]
            )
            return PriceLine(
                item.category, item.description,
                qty_purchased=note,
                unit_price_ex=cat["fixings"]["m10_100_bolt"]["packs"][0]["price_per_bolt_ex"],
                total_ex=cost_ex, total_inc=cost_inc,
                note=f"{item.quantity} nr needed",
            )

        # ── Wall panel clamping bolts (M8×100mm) ──
        if "wall panel clamping" in desc or ("wall" in desc and "bolt" in desc):
            cost_ex, cost_inc, note = _cheapest_pack(
                int(item.quantity), cat["fixings"]["m8_100_bolt"]["packs"]
            )
            return PriceLine(
                item.category, item.description,
                qty_purchased=note,
                unit_price_ex=cat["fixings"]["m8_100_bolt"]["packs"][0]["price_per_bolt_ex"],
                total_ex=cost_ex, total_inc=cost_inc,
                note=f"{item.quantity} nr needed",
            )

        # ── OSB: wall panels (unit="nr") or roof deck (unit="sheets", area-based) ──
        if "osb" in mat_lower and item.unit in ("nr", "sheets"):
            sheet_info = cat["sheet_materials"]["osb3_18mm"]
            OSB_SHEET_M2 = 2.97  # 2440×1220mm sheet
            if item.unit == "sheets":
                # Derive from area so consolidate() removes per-bay rounding waste
                area = _parse_area_m2(item.length_or_area)
                if area is not None:
                    n_sheets = math.ceil(area / OSB_SHEET_M2)
                    note = f"{area:.1f} m² ÷ {OSB_SHEET_M2} m²/sheet -> {n_sheets} sheets"
                else:
                    n_sheets = int(item.quantity)
                    note = f"{n_sheets} sheets (roof deck)"
            else:
                n_sheets = math.ceil(item.quantity / 2)  # 2 BOM 600mm panels per sheet
                note = f"{int(item.quantity)} panels -> {n_sheets} sheets (2 per 1220mm sheet)"
            cost_ex = round(n_sheets * sheet_info["price_ex_vat"], 2)
            cost_inc = round(n_sheets * sheet_info["price_inc_vat"], 2)
            return PriceLine(
                item.category, item.description,
                qty_purchased=f"{n_sheets} sheets",
                unit_price_ex=sheet_info["price_ex_vat"],
                total_ex=cost_ex, total_inc=cost_inc,
                note=note,
            )

        # ── Plasterboard panels (unit="nr", 600×2400mm -> 1200×2400mm sheets) ──
        if "plasterboard" in mat_lower and item.unit == "nr":
            sheet_info = cat["sheet_materials"]["plasterboard_12_5mm"]
            n_sheets = math.ceil(item.quantity / 2)
            cost_ex = round(n_sheets * sheet_info["price_ex_vat"], 2)
            cost_inc = round(n_sheets * sheet_info["price_inc_vat"], 2)
            return PriceLine(
                item.category, item.description,
                qty_purchased=f"{n_sheets} sheets",
                unit_price_ex=sheet_info["price_ex_vat"],
                total_ex=cost_ex, total_inc=cost_inc,
                note=f"{int(item.quantity)} panels -> {n_sheets} sheets (2 per 1200mm sheet)",
            )

        # ── Plywood support panels (unit="nr", strips from 1220×2440mm sheet) ──
        if "plywood" in mat_lower and item.unit == "nr":
            sheet_info = cat["sheet_materials"]["plywood_6mm"]
            n_sheets = math.ceil(item.quantity / 2)
            cost_ex = round(n_sheets * sheet_info["price_ex_vat"], 2)
            cost_inc = round(n_sheets * sheet_info["price_inc_vat"], 2)
            return PriceLine(
                item.category, item.description,
                qty_purchased=f"{n_sheets} sheets",
                unit_price_ex=sheet_info["price_ex_vat"],
                total_ex=cost_ex, total_inc=cost_inc,
                note=f"{int(item.quantity)} strips from {n_sheets} sheets",
            )

        # ── Site offcuts (capping spacer blocks, grid position blocks) — zero cost ──
        if "offcut" in mat_lower:
            return _SKIP

        # ── PIR insulation: floor, wall, or roof (all use same Selco board) ──
        if "pir" in mat_lower or "enertherm" in mat_lower:
            board_info = cat["insulation"]["pir_100mm"]
            area = _parse_area_m2(item.length_or_area)
            if area is None:
                return None
            board_area_m2 = 2.88  # 2400×1200mm per board
            n_boards = math.ceil(area / board_area_m2)
            cost_ex = round(n_boards * board_info["price_ex_vat"], 2)
            cost_inc = round(n_boards * board_info["price_inc_vat"], 2)
            return PriceLine(
                item.category, item.description,
                qty_purchased=f"{n_boards} boards",
                unit_price_ex=board_info["price_ex_vat"],
                total_ex=cost_ex, total_inc=cost_inc,
                note=f"{area} m² ÷ {board_area_m2} m²/board",
            )

        # ── Roofing felt: layer 3 = cap sheet, layers 1-2 = underlay ──
        if "bituminous felt layer" in desc:
            layer_match = re.search(r"layer\s+(\d)", desc)
            layer = int(layer_match.group(1)) if layer_match else 0
            if layer == 3:
                felt_info = cat["roofing"]["felt_cap_sheet"]
                felt_label = "cap sheet"
            else:
                felt_info = cat["roofing"]["felt_underlay"]
                felt_label = "underlay"
            n_rolls = int(item.quantity)
            cost_ex = round(n_rolls * felt_info["price_ex_vat"], 2)
            cost_inc = round(n_rolls * felt_info["price_inc_vat"], 2)
            return PriceLine(
                item.category, item.description,
                qty_purchased=f"{n_rolls} rolls",
                unit_price_ex=felt_info["price_ex_vat"],
                total_ex=cost_ex, total_inc=cost_inc,
                note=felt_label,
            )

        # ── Hot bitumen -> BituBond cold adhesive (25L drum, ~1.5L/m² per interface) ──
        if "hot bitumen" in desc:
            drum_info = cat["roofing"]["bitubond_25l"]
            area = _parse_area_m2(item.length_or_area)
            if area is None:
                return None
            litres_per_m2 = 1.5
            interfaces = 2  # 3 felt layers = 2 bonded interfaces
            total_litres = area * litres_per_m2 * interfaces
            n_drums = math.ceil(total_litres / 25)
            cost_ex = round(n_drums * drum_info["price_ex_vat"], 2)
            cost_inc = round(n_drums * drum_info["price_inc_vat"], 2)
            return PriceLine(
                item.category, f"BituBond felt adhesive (replaces hot bitumen)",
                qty_purchased=f"{n_drums} drums",
                unit_price_ex=drum_info["price_ex_vat"],
                total_ex=cost_ex, total_inc=cost_inc,
                note=f"{area} m² × {litres_per_m2} L/m² × {interfaces} interfaces -> {total_litres:.0f}L",
            )

        # ── T&G floor boards (unit="nr", each board spans full depth) ──
        if ("t&g" in mat_lower or "floor board" in desc) and item.unit == "nr":
            board_info = cat["flooring"]["tg_board_150x25"]
            piece_mm = _parse_length_mm(item.length_or_area)
            if piece_mm is None:
                return None
            total_m = round(item.quantity * piece_mm / 1000, 2)
            cost_ex = round(total_m * board_info["price_ex_vat"], 2)
            cost_inc = round(total_m * board_info["price_inc_vat"], 2)
            return PriceLine(
                item.category, item.description,
                qty_purchased=f"{total_m} m",
                unit_price_ex=board_info["price_ex_vat"],
                total_ex=cost_ex, total_inc=cost_inc,
                note=f"{int(item.quantity)} boards × {piece_mm}mm",
            )

        # ── Raw screw counts (unit="nr") are informational duplicates; skip silently ──
        if "screw" in desc and item.unit == "nr" and not _parse_length_mm(item.length_or_area):
            return _SKIP

        # ── Board fixing screws (unit="boxes") ──
        if "screw" in desc and item.unit in ("boxes", "box"):
            screw_info = cat["fixings"]["woodscrew_5x50"]
            # BOM boxes = 200-screw boxes; catalogue sells same pack of 200
            n_packs = int(item.quantity)
            cost_ex = round(n_packs * screw_info["packs"][0]["price_ex_vat"], 2)
            cost_inc = round(n_packs * screw_info["packs"][0]["price_inc_vat"], 2)
            return PriceLine(
                item.category, item.description,
                qty_purchased=f"{n_packs}× pack-200",
                unit_price_ex=screw_info["packs"][0]["price_ex_vat"],
                total_ex=cost_ex, total_inc=cost_inc,
            )

        # ── Preservative wood stain (unit="litres") ──
        if ("stain" in desc or "treatment" in desc) and item.unit == "litres":
            tin_info = cat["sundries"]["wood_treatment"]
            n_tins = math.ceil(item.quantity / 2.5)  # 2.5L tins
            cost_ex = round(n_tins * tin_info["price_ex_vat"], 2)
            cost_inc = round(n_tins * tin_info["price_inc_vat"], 2)
            return PriceLine(
                item.category, item.description,
                qty_purchased=f"{n_tins}× 2.5L tin",
                unit_price_ex=tin_info["price_ex_vat"],
                total_ex=cost_ex, total_inc=cost_inc,
                note=f"{item.quantity} litres needed",
            )

        # ── Fibre cement weatherboard cladding ──
        # Match on brand keyword so Cedral and HardiePlank don't cross-price.
        if "hardieplank" in mat_lower:
            spec = cat["specialist_suppliers"]["hardieplank_180mm"]
            panel_area = _parse_area_m2(item.length_or_area)
            if panel_area is None:
                return None
            total_area = item.quantity * panel_area
            n_boards = math.ceil(total_area / spec["board_coverage_m2"])
            cost_ex = round(n_boards * spec["price_ex_vat"], 2)
            cost_inc = round(n_boards * spec["price_inc_vat"], 2)
            return PriceLine(
                item.category, item.description,
                qty_purchased=f"{n_boards} boards (Jewson)",
                unit_price_ex=spec["price_ex_vat"],
                total_ex=cost_ex, total_inc=cost_inc,
                note=f"{total_area:.1f} m2 / {spec['board_coverage_m2']} m2 effective per board",
            )

        if "cedral" in mat_lower:
            spec = cat["specialist_suppliers"]["cedral_lap_190mm"]
            panel_area = _parse_area_m2(item.length_or_area)
            if panel_area is None:
                return None
            total_area = item.quantity * panel_area
            n_boards = math.ceil(total_area / spec["board_coverage_m2"])
            cost_ex = round(n_boards * spec["price_ex_vat"], 2)
            cost_inc = round(n_boards * spec["price_inc_vat"], 2)
            return PriceLine(
                item.category, item.description,
                qty_purchased=f"{n_boards} boards (Cedral stockist, est.)",
                unit_price_ex=spec["price_ex_vat"],
                total_ex=cost_ex, total_inc=cost_inc,
                note=f"{total_area:.1f} m2 / {spec['board_coverage_m2']} m2 effective per board",
            )

        if "shiplap" in mat_lower:
            spec = cat["specialist_suppliers"]["selco_shiplap_125x19mm"]
            panel_area = _parse_area_m2(item.length_or_area)
            if panel_area is None:
                return None
            total_area = item.quantity * panel_area
            lm_needed = math.ceil(total_area / spec["coverage_m2_per_lm"])
            cost_ex = round(lm_needed * spec["price_ex_vat_per_lm"], 2)
            cost_inc = round(lm_needed * spec["price_inc_vat_per_lm"], 2)
            return PriceLine(
                item.category, item.description,
                qty_purchased=f"{lm_needed} lm (Selco)",
                unit_price_ex=spec["price_ex_vat_per_lm"],
                total_ex=cost_ex, total_inc=cost_inc,
                note=f"{total_area:.1f} m2 / {spec['coverage_m2_per_lm']} m2/lm -> {lm_needed} lm",
            )

        # ── Corrugated steel ('wiggly tin') cladding ──
        if "corrugated" in mat_lower or "galvanised steel" in mat_lower:
            spec = cat["specialist_suppliers"]["corrugated_steel_sheet"]
            panel_area = _parse_area_m2(item.length_or_area)
            if panel_area is None:
                return None
            total_area = item.quantity * panel_area
            total_area_waste = total_area * 1.10  # 10% for overlaps and cuts
            cost_ex = round(total_area_waste * spec["price_ex_vat_per_m2"], 2)
            cost_inc = round(total_area_waste * spec["price_inc_vat_per_m2"], 2)
            return PriceLine(
                item.category, item.description,
                qty_purchased=f"{total_area_waste:.1f} m² (TP/merchant, est.)",
                unit_price_ex=spec["price_ex_vat_per_m2"],
                total_ex=cost_ex, total_inc=cost_inc,
                note=f"{total_area:.1f} m² + 10% overlaps -> {total_area_waste:.1f} m²",
            )

        # ── Breather membrane (Travis Perkins / Cross Country) ──
        if "breather membrane" in mat_lower:
            spec = cat["specialist_suppliers"]["breather_membrane_roll"]
            area = _parse_area_m2(item.length_or_area)
            if area is None:
                return None
            area_with_waste = area * 1.10  # 10% overlap allowance
            n_rolls = math.ceil(area_with_waste / spec["roll_m2"])
            cost_ex = round(n_rolls * spec["price_ex_vat"], 2)
            cost_inc = round(n_rolls * spec["price_inc_vat"], 2)
            return PriceLine(
                item.category, item.description,
                qty_purchased=f"{n_rolls} roll(s) (TP)",
                unit_price_ex=spec["price_ex_vat"],
                total_ex=cost_ex, total_inc=cost_inc,
                note=f"{area:.2f} m2 + 10% overlap -> {area_with_waste:.1f} m2",
            )

        # ── Roof fascia (PAR 25×200mm, builders merchant) ──
        if "fascia" in desc and item.unit == "lengths":
            spec = cat["specialist_suppliers"]["fascia_board_200x25"]
            length_m = _parse_linear_m(item.length_or_area)
            if length_m is None:
                # Fall back: assume standard 4.8m stock lengths
                total_m = item.quantity * 4.8
            else:
                total_m = item.quantity * 4.8  # BOM qty = number of 4.8m lengths
            cost_ex = round(total_m * spec["price_ex_vat"], 2)
            cost_inc = round(total_m * spec["price_inc_vat"], 2)
            return PriceLine(
                item.category, item.description,
                qty_purchased=f"{int(item.quantity)}× 4.8m ({total_m:.1f} m)",
                unit_price_ex=spec["price_ex_vat"],
                total_ex=cost_ex, total_inc=cost_inc,
                note="25×200mm PAR fascia; builders merchant",
            )

        # ── Flat roof outlet (ACO 100mm, Roofing Superstore) ──
        if "roof outlet" in desc:
            spec = cat["specialist_suppliers"]["flat_roof_outlet_100mm"]
            n = int(item.quantity)
            cost_ex = round(n * spec["price_ex_vat"], 2)
            cost_inc = round(n * spec["price_inc_vat"], 2)
            return PriceLine(
                item.category, item.description,
                qty_purchased=f"{n} nr (Roofing Superstore)",
                unit_price_ex=spec["price_ex_vat"],
                total_ex=cost_ex, total_inc=cost_inc,
                note="ACO 100mm vertical spigot",
            )

        # Not matched
        return None


def _find_closest_length(lengths_data: list[dict], target_mm: int) -> Optional[dict]:
    """Return the entry whose stock length is closest to *target_mm* without going under."""
    candidates = [e for e in lengths_data if int(round(e["length_m"] * 1000)) >= target_mm]
    if candidates:
        return min(candidates, key=lambda e: e["length_m"])
    # All shorter than target — return the longest available
    if lengths_data:
        return max(lengths_data, key=lambda e: e["length_m"])
    return None
