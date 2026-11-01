#!/usr/bin/env python3
"""
KVD stängda auktioner-scraper.
Hämtar listan + detaljer och sparar full JSON + aggregerad sammanställning.
Hoppar över redan skrapade ID:n och lägger till max 50 nya per körning.
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

# Alla fält som alltid ska finnas i aggregerade rader
AGG_FIELDS = [
    "id", "slug", "url", "brand", "model", "model_name", "model_year",
    "odometer_km", "fuel", "fuel_codes", "electric_type", "is_ev",
    "is_hybrid", "is_phev", "gearbox", "closed_at", "start_bid",
    "winning_bid", "sold_for", "price", "status", "state",
    "auction_type", "is_reserved", "preliminary_price", "scraped_at",
    "error",
]


def get_already_scraped_ids() -> set:
    """Hittar alla redan skrapade auktions-ID:n genom att titta i alla details-mappar."""
    scraped = set()
    if not DATA_DIR.exists():
        return scraped
    for day_dir in DATA_DIR.iterdir():
        if not day_dir.is_dir():
            continue
        details_dir = day_dir / "details"
        if details_dir.exists():
            for f in details_dir.glob("*.json"):
                scraped.add(f.stem)
    return scraped


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


def extract_fuel_info(props: dict) -> dict:
    """Extraherar bränsletyp, EV-flagga och hybrid-info."""
    electric_type = props.get("electricType")
    fuels = props.get("fuels") or []
    fuel_codes = [f.get("fuelCode") for f in fuels if f.get("fuelCode")]

    is_electric = "Electric" in fuel_codes or (electric_type and "electric" in electric_type.lower())
    is_hybrid = electric_type and "hybrid" in electric_type.lower()
    is_phev = is_hybrid and is_electric

    if is_electric and not is_hybrid:
        fuel_display = "El"
    elif is_phev:
        fuel_display = "Plug-in hybrid"
    elif is_hybrid:
        fuel_display = "Hybrid"
    elif fuel_codes:
        fuel_display = ", ".join(fuel_codes)
    else:
        fuel_display = None

    return {
        "fuel": fuel_display,
        "fuel_codes": ", ".join(fuel_codes) if fuel_codes else None,
        "electric_type": electric_type,
        "is_ev": is_electric and not is_hybrid,
        "is_hybrid": bool(is_hybrid),
        "is_phev": bool(is_phev),
    }


def flatten_for_aggregate(full: dict) -> dict:
    """Tar ut de intressanta fälten till den aggregerade filen. Alla rader har samma nycklar."""
    base = {field: None for field in AGG_FIELDS}

    if "error" in full:
        base["id"] = full.get("id")
        base["error"] = full["error"]
        base["url"] = full.get("url")
        base["status"] = "fel vid skrapning"
        return base

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

    fuel_info = extract_fuel_info(props)

    base.update({
        "id": full.get("id"),
        "slug": full.get("slug"),
        "url": full.get("_url"),
        "brand": props.get("brand") or vehicle.get("brand"),
        "model": props.get("familyName") or vehicle.get("familyName"),
        "model_name": vehicle.get("modelName") or props.get("modelName"),
        "model_year": props.get("modelYear") or vehicle.get("year"),
        "odometer_km": props.get("odometerReading"),
        "fuel": fuel_info["fuel"],
        "fuel_codes": fuel_info["fuel_codes"],
        "electric_type": fuel_info["electric_type"],
        "is_ev": fuel_info["is_ev"],
        "is_hybrid": fuel_info["is_hybrid"],
        "is_phev": fuel_info["is_phev"],
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
        "error": None,
    })
    return base


def rebuild_aggregated(day_dir: Path):
    """Bygger om aggregated.json/csv från alla details i dagens mapp."""
    details_dir = day_dir / "details"
    aggregated = []
    for detail_file in sorted(details_dir.glob("*.json")):
        try:
            full = json.loads(detail_file.read_text(encoding="utf-8"))
            aggregated.append(flatten_for_aggregate(full))
        except Exception as e:
            print(f"Kunde inte läsa {detail_file}: {e}")

    if not aggregated:
        return

    agg_json = day_dir / "aggregated.json"
    agg_json.write_text(json.dumps(aggregated, ensure_ascii=False, indent=2), encoding="utf-8")

    import csv
    agg_csv = day_dir / "aggregated.csv"
    with agg_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=AGG_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(aggregated)

    print(f"Aggregerat uppdaterat: {len(aggregated)} rader")


def main(max_details: int | None = None):
    """max_details = max antal NYA auktioner att skrapa den här körningen."""
    today = datetime.now().strftime("%Y-%m-%d")
    day_dir = DATA_DIR / today
    details_dir = day_dir / "details"
    details_dir.mkdir(parents=True, exist_ok=True)

    print("Hämtar lista över stängda auktioner...")
    auctions = fetch_closed_list()
    print(f"Hittade {len(auctions)} auktioner i listan")

    list_path = day_dir / "closed_auctions_list.json"
    list_path.write_text(json.dumps(auctions, ensure_ascii=False, indent=2), encoding="utf-8")

    already = get_already_scraped_ids()
    print(f"Redan skrapade ID:n: {len(already)}")

    new_auctions = [a for a in auctions if str(a["id"]) not in already]
    print(f"Nya auktioner: {len(new_auctions)}")

    if max_details:
        to_scrape = new_auctions[:max_details]
    else:
        to_scrape = new_auctions

    print(f"Skrapar {len(to_scrape)} nya auktioner den här körningen")

    for i, auction in enumerate(to_scrape, 1):
        aid = auction["id"]
        detail_path = details_dir / f"{aid}.json"
        print(f"[{i}/{len(to_scrape)}] {aid} – {auction.get('slug')}")

        full = scrape_detail(auction)
        detail_path.write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(DELAY_BETWEEN_REQUESTS)

    rebuild_aggregated(day_dir)

    print(f"\nKlart. Data sparad i {day_dir}/")
    print(f"- Lista: {list_path}")
    print(f"- Detaljer: {details_dir}/ ({len(list(details_dir.glob('*.json')))} filer)")
    print(f"- Aggregerat: aggregated.json + aggregated.csv")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=None, help="Max antal NYA detaljsidor att skrapa den här körningen")
    args = parser.parse_args()
    main(max_details=args.max)
