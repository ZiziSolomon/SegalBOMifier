"""Selco price scraper using Playwright.

Logs in to selcobw.com and fetches the current trade price for each product URL
listed in selco_prices.md, then writes the results back to selco_prices.md as a
CSV alongside the markdown table.

Usage:
    python -m segal_method.selco_scraper

Credentials are read from environment variables to avoid hardcoding:
    SELCO_EMAIL    your Selco login email
    SELCO_PASSWORD your Selco login password

Or pass them directly:
    python -m segal_method.selco_scraper --email foo@bar.com --password secret
"""

import argparse
import csv
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, sync_playwright, TimeoutError as PWTimeout


# ── Product catalogue ──────────────────────────────────────────────────────────
# Each entry maps a BOM description to a Selco product URL.
# Add / remove as needed; None URL = not stocked (skip gracefully).

PRODUCTS = [
    # Structural timber
    ("200×47mm C24 treated (beam/joist)",
     "https://www.selcobw.com/sawn-treated-easi-edge-200-x-47mm-8-x-2-kiln-dried-c24-fsc-020062105p"),
    ("200×47mm C16 treated (joist)",
     "https://www.selcobw.com/sawn-treated-easi-edge-200-x-47mm-8-x-2-kiln-dried-c16-fsc-020061975p"),
    ("100×47mm C16 treated (post)",
     "https://www.selcobw.com/sawn-treated-easi-edge-100-x-47mm-4-x-2-kiln-dried-c16-020062124"),
    ("50×47mm treated (bearer/sole plate)",
     "https://www.selcobw.com/sawn-treated-green-easi-edge-50-x-47mm-2-x-2-kiln-dried-fsc-020062070p"),
    ("50×25mm treated batten/fascia",
     "https://www.selcobw.com/sawn-green-treated-50-x-25mm-2-x-1-020061695p"),
    ("50×25mm graded roof batten",
     "https://www.selcobw.com/graded-roof-batten-treated-50-x-25mm-2-x-1-4-8m-pefc"),

    # Sheet materials
    ("OSB/3 18mm 2440×1220 (tool walls)",
     "https://www.selcobw.com/sterlingosb-zero-osb3-board-2440-x-1220-x-18mm-fscr"),
    ("Plasterboard 12.5mm 2400×1200",
     "https://www.selcobw.com/siniat-standard-square-edge-plasterboard-2400-x-1200-x-12-5mm"),
    ("General purpose plywood 6mm 2440×1220",
     "https://www.selcobw.com/general-purpose-plywood-2440-x-1220mm"),

    # Insulation
    ("IKO Enertherm PIR 2400×1200 (floor, 100mm)",
     "https://www.selcobw.com/iko-enertherm-pir-insulation-board-2400-x-1200mm"),

    # Roofing felt
    ("IKO Trade Felt Green Mineral Cap Sheet 1×10m",
     "https://www.selcobw.com/iko-trade-roofing-felt-green-mineral-cap-sheet-1-x-10m"),
    ("IKO Roofing Shed Felt 1×10m",
     "https://www.selcobw.com/iko-roofing-shed-felt-1-x-10m"),
    ("BituBond Felt Adhesive Black 25L",
     "https://www.selcobw.com/bitubond-roofing-felt-adhesive-black-25ltr"),

    # Flooring
    ("PTG Redwood T&G floorboard 150×25mm",
     "https://www.selcobw.com/ptg-flooring-5th-redwood-150-x-25mm-nom-pefc"),

    # Fixings
    ("Unifix Cup Square Hex Bolt & Nut M12×150mm",
     "https://www.selcobw.com/unifix-cup-square-hex-bolt-nut-m12-x-150mm"),
    ("Unifix Cup Square Hex Bolt & Nut M10×100mm",
     "https://www.selcobw.com/unifix-cup-square-hex-bolt-nut-m10-x-100mm"),
    ("Unifix Cup Square Hex Bolt & Nut M8×100mm",
     "https://www.selcobw.com/unifix-cup-square-hex-bolt-nut-m8-x-100mm"),
    ("Twin Thread Woodscrew 5×50mm pack 200",
     "https://www.selcobw.com/twin-thread-woodscrew-5-x-50mm"),

    # External cladding (timber option)
    ("Softwood Shiplap Cladding 125x19mm PEFC",
     "https://www.selcobw.com/softwood-cladding-125-x-19mm-5-x-nom-pefc"),

    # Sundries
    ("Ronseal Wood Treatment Clear",
     "https://www.selcobw.com/ronseal-wood-treatment-clear"),
]


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class PriceResult:
    description: str
    url: str
    price_ex_vat: Optional[str]   # e.g. "£9.38"
    price_inc_vat: Optional[str]
    unit: Optional[str]           # e.g. "per sheet", "per length", "pack of 5"
    error: Optional[str]          # set if fetch failed


# ── Selco login ────────────────────────────────────────────────────────────────

LOGIN_URL = "https://www.selcobw.com/customer/account/login/"

def login(page: Page, email: str, password: str) -> bool:
    """Log in to Selco. Returns True on success."""
    print("  Logging in...")
    page.goto(LOGIN_URL, wait_until="networkidle", timeout=30_000)

    # Dismiss cookie consent banner — it blocks JS form initialisation
    try:
        page.locator("button:has-text('Accept all cookies')").click(timeout=5_000)
        page.wait_for_timeout(500)
    except Exception:
        pass  # no banner, fine

    # Click then type — simulates real user, fires the input events Selco's JS needs
    page.locator("input#username").click()
    page.locator("input#username").type(email, delay=40)
    page.locator("input#password").click()
    page.locator("input#password").type(password, delay=40)
    page.wait_for_timeout(800)

    # Press Enter to submit (bypasses any disabled-button issues)
    page.locator("input#password").press("Enter")

    try:
        # Wait for redirect away from login page
        page.wait_for_url(lambda url: "login" not in url, timeout=15_000)
        print("  Logged in OK.")
        return True
    except PWTimeout:
        # Check for error message
        if page.locator(".message-error").count() > 0:
            msg = page.locator(".message-error").first.inner_text()
            print(f"  Login failed: {msg}")
        else:
            print("  Login timed out — proceeding anyway.")
        return False


# ── Price extraction ───────────────────────────────────────────────────────────

# CSS selectors to try for price — Selco's DOM may vary
PRICE_EX_VAT_SELECTORS = [
    "[data-price-type='finalPrice'] .price",
    ".price-excluding-tax .price",
    ".ex-vat-price",
    ".price-box .price",
]

PRICE_INC_VAT_SELECTORS = [
    ".price-including-tax .price",
    ".inc-vat-price",
]

UNIT_SELECTORS = [
    ".product-price-unit",
    ".price-per",
    "[class*='price-unit']",
    "[class*='per-unit']",
]


def _try_selectors(page: Page, selectors: list[str]) -> Optional[str]:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=500):
                return loc.inner_text().strip()
        except Exception:
            pass
    return None


def fetch_price(page: Page, description: str, url: str) -> PriceResult:
    """Navigate to a product page and extract the price.

    Selco renders prices in plain <span> elements with no class inside a React
    component. We use JS text-node walking to find £X.XX values reliably,
    ignoring nav/header/footer context. Pack qty comes from the select options
    next to the "Pack Qty" label.
    """
    try:
        page.goto(url, wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(1_500)

        data = page.evaluate("""() => {
            // Find all text nodes containing a £ price, outside nav/header/footer
            const ignore = new Set(['NAV', 'HEADER', 'FOOTER', 'SCRIPT', 'STYLE']);
            const prices = [];
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let node;
            while (node = walker.nextNode()) {
                const txt = node.textContent.trim();
                if (!/^£\\d/.test(txt)) continue;
                // Walk up to check we're not inside nav/header/footer
                let el = node.parentElement;
                let inIgnored = false;
                while (el) {
                    if (ignore.has(el.tagName)) { inIgnored = true; break; }
                    el = el.parentElement;
                }
                if (!inIgnored) prices.push(txt);
            }

            // Pack qty: find the label and its nearest select's options
            let packQty = null;
            const labels = document.querySelectorAll('label');
            for (const label of labels) {
                if (label.textContent.trim().toLowerCase().includes('pack')) {
                    // Check for a <select> sibling/nearby
                    const sel = label.closest('div, li, span')?.querySelector('select');
                    if (sel) {
                        const opts = Array.from(sel.options).map(o => o.text.trim()).filter(Boolean);
                        packQty = opts.join(' / ');
                    } else {
                        // May be plain text options (radio/button list)
                        const container = label.closest('div, li');
                        if (container) {
                            const opts = Array.from(
                                container.querySelectorAll('li, button, [role=option]')
                            ).map(o => o.textContent.trim()).filter(Boolean);
                            if (opts.length) packQty = opts.join(' / ');
                        }
                    }
                    if (!packQty) packQty = label.parentElement?.textContent.trim().slice(0, 60) || null;
                    break;
                }
            }
            return { prices: [...new Set(prices)], packQty };
        }""")

        prices = data.get("prices", [])
        pack_qty = data.get("packQty")

        price_ex = prices[0] if len(prices) > 0 else None
        price_inc = prices[1] if len(prices) > 1 else None

        return PriceResult(description, url, price_ex, price_inc, pack_qty, error=None)

    except PWTimeout:
        return PriceResult(description, url, None, None, None, error="timeout")
    except Exception as exc:
        return PriceResult(description, url, None, None, None, error=str(exc)[:80])


# ── Main ───────────────────────────────────────────────────────────────────────

def scrape(email: str, password: str) -> list[PriceResult]:
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            )
        )

        # Login is blocked by reCAPTCHA in headless mode.
        # Selco shows prices to unauthenticated visitors anyway.
        _ = email, password  # reserved for future use if Selco locks prices behind login

        for i, (description, url) in enumerate(PRODUCTS):
            print(f"  [{i+1}/{len(PRODUCTS)}] {description[:55]}...")
            result = fetch_price(page, description, url)
            results.append(result)
            # Polite crawl delay
            time.sleep(0.8)

        browser.close()
    return results


def save_csv(results: list[PriceResult], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Description", "Price ex VAT", "Price inc VAT", "Unit", "Error", "URL"])
        for r in results:
            w.writerow([r.description, r.price_ex_vat or "", r.price_inc_vat or "",
                        r.unit or "", r.error or "", r.url])
    print(f"\nSaved to {path}")


def print_results(results: list[PriceResult]) -> None:
    ok = [r for r in results if not r.error and (r.price_ex_vat or r.price_inc_vat)]
    missing = [r for r in results if r.error or not (r.price_ex_vat or r.price_inc_vat)]

    col_w = max(len(r.description) for r in results) + 2
    print(f"\n{'Description':<{col_w}} {'ex VAT':<12} {'inc VAT':<12} Unit")
    print("-" * (col_w + 38))
    for r in ok:
        print(f"{r.description:<{col_w}} {(r.price_ex_vat or ''):<12} {(r.price_inc_vat or ''):<12} {r.unit or ''}")

    if missing:
        print(f"\n-- Prices not found ({len(missing)}) --")
        for r in missing:
            tag = f"[{r.error}]" if r.error else "[no price found]"
            print(f"  {tag} {r.description}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Selco trade prices")
    parser.add_argument("--email", default=os.environ.get("SELCO_EMAIL", ""))
    parser.add_argument("--password", default=os.environ.get("SELCO_PASSWORD", ""))
    args = parser.parse_args()

    if not args.email or not args.password:
        print("Provide credentials via --email / --password or SELCO_EMAIL / SELCO_PASSWORD env vars.")
        raise SystemExit(1)

    print(f"Scraping {len(PRODUCTS)} products...")
    results = scrape(args.email, args.password)
    print_results(results)

    out_path = Path(__file__).parent / "selco_prices.csv"
    save_csv(results, out_path)
