#!/usr/bin/env python3
"""
KVD stängda auktioner-scraper.
Hämtar listan + detaljer och sparar full JSON + aggregerad sammanställning.
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://www.kvd.se"
LIST_URL = f"{BASE_URL}/stangda-auktioner"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; kvd-archive-bot/1.0)",
    "Accept-Language": "sv-SE,sv;q=0.9",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

DATA_DIR = Path("data")
DELAY_BETWEEN_REQUESTS = 0.8


def fetch_closed_list():
    """Hämtar listan över stängda auktioner (JSON inbäddad i sidan)."""
    r = SESSION.get(LIST_URL, timeout=30)
    r.raise_for_status()
    m = re.search(r'"link-graveyard":\{"auctions":(\[.*?\])\}', r.text)
    if not m:
        raise RuntimeError("Kunde inte hitta auktionslistan i HTML")
    return json.loads(m.group(1))


def extract_auction_object(html: str, auction_id: str) -> dict | None:
    """Extraherar det stora auktionsobjektet från detaljsidans Redux-state."""
    marker = f'"{auction_id}":{{'
    idx = html.find(marker)
    if idx == -1:
        return None
    try:
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(html[idx + len(f'"{auction_id}":'):])
        return obj
    except json.JSONDecodeError:
        return None


def scrape_detail(auction: dict) -> dict:
    """Hämtar och parsar en enskild auktionssida."""
    auction_id = auction["id"]
    slug = auction["slug"]
    url = f"{BASE_URL}/auktioner/{slug}-{auction_id}"

    try:
        r = SESSION.get(url, timeout=30)
        r.raise_for_status()
    except Exception as e:
        return {"id": auction_id, "error": str(e), "url": url}

    full = extract_auction_object(r.text, auction_id)
    if not full:
        return {"id": auction_id, "error": "Kunde inte extrahera auktionsobjekt", "url": url}

    full["_list_meta"] = auction
    full["_scraped_at"] = datetime.now(timezone.utc).isoformat()
    full["_url"] = url
    return full


def flatten_for_aggregate(full: dict) -> dict:
    """Tar ut de intressanta fälten till den aggregerade filen."""
    if "error" in full:
        return {
            "id": full.get("id"),
            "error": full["error"],
            "url": full.get("url"),
        }

    po = full.get("processObject") or {}
    props = po.get("properties") or {}
    vehicle = po.get("vehicle") or {}

    sold_for = full.get("soldFor")
    winning_bid = full.get("winningBid")
    if sold_for is not None:
        status = "såld"
        price = sold_for
    elif winning_bid is not None:
        status = "såld (winningBid)"
        price = winning_bid
    else:
        status = "osåld / reservationspris ej uppnått"
        price = None

    return {
        "id": full.get("id"),
        "slug": full.get("slug"),
        "url": full.get("_url"),
        "brand": props.get("brand") or vehicle.get("brand"),
        "model": props.get("familyName") or vehicle.get("familyName"),
        "model_name": vehicle.get("modelName") or props.get("modelName"),
        "model_year": props.get("modelYear") or vehicle.get("year"),
        "odometer_km": props.get("odometerReading"),
        "fuel": props.get("fuelType"),
        "gearbox": props.get("gearbox"),
        "closed_at": full.get("closedAt"),
        "start_bid": full.get("startBid"),
        "winning_bid": winning_bid,
        "sold_for": sold_for,
        "price": price,
        "status": status,
        "state": full.get("state"),
        "auction_type": full.get("auctionType"),
        "is_reserved": full.get("isReserved"),
        "preliminary_price": full.get("preliminaryPrice"),
        "scraped_at": full.get("_scraped_at"),
    }


def main(max_details: int | None = None):
    """max_details=None = alla. Sätt t.ex. 50 för test."""
    today = datetime.now().strftime("%Y-%m-%d")
    day_dir = DATA_DIR / today
    details_dir = day_dir / "details"
    details_dir.mkdir(parents=True, exist_ok=True)

    print(f"Hämtar lista över stängda auktioner...")
    auctions = fetch_closed_list()
    print(f"Hittade {len(auctions)} auktioner")

    list_path = day_dir / "closed_auctions_list.json"
    list_path.write_text(json.dumps(auctions, ensure_ascii=False, indent=2), encoding="utf-8")

    to_scrape = auctions
    if max_details:
        to_scrape = auctions[:max_details]

    aggregated = []
    for i, auction in enumerate(to_scrape, 1):
        aid = auction["id"]
        detail_path = details_dir / f"{aid}.json"
        print(f"[{i}/{len(to_scrape)}] {aid} – {auction.get('slug')}")

        full = scrape_detail(auction)
        detail_path.write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8")

        aggregated.append(flatten_for_aggregate(full))
        time.sleep(DELAY_BETWEEN_REQUESTS)

    agg_json = day_dir / "aggregated.json"
    agg_json.write_text(json.dumps(aggregated, ensure_ascii=False, indent=2), encoding="utf-8")

    if aggregated:
        import csv
        agg_csv = day_dir / "aggregated.csv"
        fieldnames = list(aggregated[0].keys())
        with agg_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(aggregated)

    print(f"\nKlart. Data sparad i {day_dir}/")
    print(f"- Lista: {list_path}")
    print(f"- Detaljer: {details_dir}/ ({len(list(details_dir.glob('*.json')))} filer)")
    print(f"- Aggregerat: aggregated.json + aggregated.csv")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=None, help="Max antal detaljsidor att skrapa (för test)")
    args = parser.parse_args()
    main(max_details=args.max)
