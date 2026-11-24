"""Récupère les prix One Piece (TCGCSV, catégorie 68) une fois par jour et calcule les variations."""
import datetime, json, os, time
import requests

BASE = "https://tcgcsv.com/tcgplayer/68"
HEADERS = {"User-Agent": "OPTracker/1.0 (usage personnel)"}
MIN_PRICE = 1.0
SNAP_DIR = "data/snapshots"


def get(url):
    time.sleep(0.15)  # politesse envers TCGCSV
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.json()["results"]


def find_snapshot(today, back, tol):
    for d in range(back, back + tol + 1):
        path = f"{SNAP_DIR}/{today - datetime.timedelta(days=d)}.json"
        if os.path.exists(path):
            return json.load(open(path))
    return None


def kind(number, group_name):
    if "promo" in group_name.lower():
        return "Promo"
    for prefix in ("PRB", "EB", "OP", "ST"):
        if number.upper().startswith(prefix):
            return prefix
    return "Autre"


def pct(now, old):
    return round((now - old) / old * 100, 1) if old else None


def main():
    today = datetime.date.today()
    os.makedirs(SNAP_DIR, exist_ok=True)
    cards = {}
    for g in get(f"{BASE}/groups"):
        gid = g["groupId"]
        products = {p["productId"]: p for p in get(f"{BASE}/{gid}/products")}
        for pr in get(f"{BASE}/{gid}/prices"):
            p = products.get(pr["productId"])
            price = pr.get("marketPrice")
            if not p or price is None or price < MIN_PRICE:
                continue
            ext = {e["name"]: e["value"] for e in p.get("extendedData", [])}
            if not ext.get("Number"):
                continue  # produit scellé, pas une carte
            if p["productId"] in cards and pr["subTypeName"] != "Normal":
                continue
            cards[p["productId"]] = {
                "c": ext["Number"], "n": p["name"],
                "s": g.get("abbreviation") or g["name"],
                "p": round(price, 2), "u": p["url"],
                "r": ext.get("Rarity", ""), "t": kind(ext["Number"], g["name"]),
            }
    if not cards:
        raise SystemExit("Aucune carte récupérée : on n'écrit rien.")

    json.dump({str(k): v["p"] for k, v in cards.items()},
              open(f"{SNAP_DIR}/{today}.json", "w"))
    old1, old7 = find_snapshot(today, 1, 2), find_snapshot(today, 7, 2)
    rows = []
    for pid, c in cards.items():
        c["d1"] = pct(c["p"], old1.get(str(pid))) if old1 else None
        c["d7"] = pct(c["p"], old7.get(str(pid))) if old7 else None
        rows.append(c)
    json.dump({"updated": str(today), "source": "TCGCSV / TCGplayer", "rows": rows},
              open("signals.json", "w"), separators=(",", ":"))

    for f in os.listdir(SNAP_DIR):  # garde 30 jours
        if f[:10] < str(today - datetime.timedelta(days=30)):
            os.remove(f"{SNAP_DIR}/{f}")
    print(f"{len(rows)} cartes enregistrées")


if __name__ == "__main__":
    main()
