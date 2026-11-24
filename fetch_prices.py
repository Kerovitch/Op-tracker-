"""Récupère les prix One Piece (TCGCSV, catégorie 68) une fois par jour et calcule les variations."""
import datetime, json, os, re, time
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


CM_BASE = "https://downloads.s3.cardmarket.com/productCatalog"
CM_GAME = 18          # One Piece chez Cardmarket
CM_MIN = 3.0          # on garde les cartes à 3 € et plus
CODE = r"(?:(?:OP|EB|PRB|ST)\d{2}-\d{3}|P-\d{3})"
CODE_RE = re.compile(r"\((" + CODE + r")\)")
VER_RE = re.compile(r"\(V\.(\d+)\)")
CLEAN_RE = re.compile(r"\s*\((?:" + CODE + r"|V\.\d+)\)")


def get_json(url):
    r = requests.get(url, headers=HEADERS, timeout=180)
    r.raise_for_status()
    return r.json()


def r2(v):
    return round(v, 2) if isinstance(v, (int, float)) else None


def cardmarket():
    """Prix Cardmarket (EUR) depuis les fichiers publics quotidiens. Renvoie (date, lignes, codes_par_nb)."""
    guide = get_json(f"{CM_BASE}/priceGuide/price_guide_{CM_GAME}.json")
    catalog = get_json(f"{CM_BASE}/productList/products_singles_{CM_GAME}.json")
    prices = {g["idProduct"]: g for g in guide.get("priceGuides", [])}
    products = catalog.get("products", [])
    with_code = sum(1 for p in products if CODE_RE.search(p.get("name", "")))
    if not products or with_code < len(products) / 2:
        raise RuntimeError("Le fichier Cardmarket ne ressemble pas à One Piece (game 18) : abandon.")
    per_code, rows = {}, []
    for p in products:
        name = p.get("name", "")
        m, v = CODE_RE.search(name), VER_RE.search(name)
        code = m.group(1) if m else ""
        if code:
            per_code[code] = per_code.get(code, 0) + 1
        g = prices.get(p["idProduct"])
        trend = g.get("trend") if g else None
        if trend is None or trend < CM_MIN:
            continue
        rows.append({
            "i": p["idProduct"], "n": CLEAN_RE.sub("", name).strip(), "c": code,
            "v": f"V.{v.group(1)}" if v else "", "e": r2(trend), "l": r2(g.get("low")),
            "a1": r2(g.get("avg1")), "a7": r2(g.get("avg7")), "a30": r2(g.get("avg30")),
            "u": f"https://www.cardmarket.com/en/OnePiece/Products?idProduct={p['idProduct']}",
        })
    return str(guide.get("createdAt", ""))[:10], rows, per_code


def usd_to_eur():
    try:
        r = requests.get("https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR", timeout=30)
        return float(r.json()["rates"]["EUR"])
    except Exception:
        return None


PAREN_RE = re.compile(r"\(([^)]*)\)")


def variant(name, number):
    """Variante lue dans le nom TCGplayer : Alternate Art, Manga, etc. (hors code de carte)."""
    toks = [t.strip() for t in PAREN_RE.findall(name)]
    toks = [t for t in toks if t and t.upper() != number.upper()
            and not re.fullmatch(r"[A-Za-z]{0,3}\d{0,2}-?\d{1,3}", t)]
    joined = " / ".join(toks)
    low = joined.lower()
    if "manga" in low:
        return "Manga"
    if "alternate art" in low or "parallel" in low:
        return "Alternate Art"
    return joined or "Standard"


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
    cards, tcg_codes, sets = {}, {}, {}
    for g in get(f"{BASE}/groups"):
        gid = g["groupId"]
        sets[str(gid)] = {"n": g["name"], "a": g.get("abbreviation") or "",
                          "d": str(g.get("publishedOn") or "")[:10]}
        products = {p["productId"]: p for p in get(f"{BASE}/{gid}/products")}
        for p in products.values():
            num = {e["name"]: e["value"] for e in p.get("extendedData", [])}.get("Number")
            if num:
                tcg_codes.setdefault(num, set()).add(p["productId"])
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
                "g": gid, "q": variant(p["name"], ext["Number"]),
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
    cm = None
    fx = usd_to_eur()
    try:
        cm_date, cm_rows, cm_codes = cardmarket()
        by_code = {m["c"]: m for m in cm_rows if m["c"] and cm_codes.get(m["c"]) == 1}
        for c in rows:  # prix Cardmarket ajouté au catalogue si la carte est unique des deux côtés
            if len(tcg_codes.get(c["c"], ())) == 1 and c["c"] in by_code:
                c["e"] = by_code[c["c"]]["e"]
        if fx:  # écart EUR vs USD seulement si le code est unique des deux côtés
            us = {}
            for c in rows:
                if len(tcg_codes.get(c["c"], ())) == 1:
                    us[c["c"]] = c["p"]
            for m in cm_rows:
                if m["c"] in us and cm_codes.get(m["c"]) == 1:
                    m["us"] = us[m["c"]]
                    m["x"] = round((m["e"] / (us[m["c"]] * fx) - 1) * 100, 1)
        cm = {"updated": cm_date, "rows": cm_rows}
        print(f"Cardmarket : {len(cm_rows)} cartes")
    except Exception as e:  # on garde les données TCGplayer quoi qu'il arrive
        print(f"ATTENTION Cardmarket indisponible : {e}")
    json.dump({"updated": str(today), "source": "TCGCSV / TCGplayer", "fx": fx, "sets": sets, "rows": rows, "cm": cm},
              open("signals.json", "w"), separators=(",", ":"))

    for f in os.listdir(SNAP_DIR):  # garde 30 jours
        if f[:10] < str(today - datetime.timedelta(days=30)):
            os.remove(f"{SNAP_DIR}/{f}")
    print(f"{len(rows)} cartes enregistrées")


if __name__ == "__main__":
    main()
