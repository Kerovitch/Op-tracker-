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


CT_API = "https://api.cardtrader.com/api/v2"
LANGS = {"en": "EN", "jp": "JP", "ja": "JP", "fr": "FR", "de": "DE", "es": "ES", "it": "IT", "kr": "KR", "zh": "ZH"}


def ct_get(path, token):
    time.sleep(1.1)  # CardTrader limite le marché à 1 appel par seconde
    r = requests.get(CT_API + path, headers={**HEADERS, "Authorization": f"Bearer {token}"}, timeout=180)
    r.raise_for_status()
    return r.json()


def as_list(x):
    return x.get("array", []) if isinstance(x, dict) else x


def cardtrader(token):
    """Prix plancher des annonces CardTrader (EUR) par langue, pour chaque carte One Piece.
    Renvoie une liste de (ids_cardmarket, id_tcgplayer, {langue: [prix_NM, nb_exemplaires]})."""
    games = as_list(ct_get("/games", token))
    gid = next((g["id"] for g in games
                if re.search(r"one\s*piece", f"{g.get('display_name', '')} {g.get('name', '')}", re.I)), None)
    if gid is None:
        raise RuntimeError("Jeu One Piece introuvable chez CardTrader")
    links = []
    for e in [x for x in as_list(ct_get("/expansions", token)) if x.get("game_id") == gid]:
        bps = {b["id"]: b for b in as_list(ct_get(f"/blueprints/export?expansion_id={e['id']}", token))}
        offers = ct_get(f"/marketplace/products?expansion_id={e['id']}", token)
        if isinstance(offers, list):
            grouped = {}
            for o in offers:
                grouped.setdefault(o.get("blueprint_id"), []).append(o)
            offers = grouped
        for bid, lst in offers.items():
            floors = {}
            for o in lst:
                pr = o.get("price") or {}
                if pr.get("currency") != "EUR":
                    continue
                props = o.get("properties_hash") or {}
                lk = next((k for k in props if "language" in k), None)
                raw = str(props.get(lk, "")).lower() if lk else ""
                lang = LANGS.get(raw, raw.upper() or "?")
                f = floors.setdefault(lang, [None, 0])
                f[1] += int(o.get("quantity") or 1)
                price = round(pr["cents"] / 100, 2)
                if props.get("condition") in ("Mint", "Near Mint") and (f[0] is None or price < f[0]):
                    f[0] = price
            floors = {k: v for k, v in floors.items() if v[0] is not None}
            bp = bps.get(int(bid)) if str(bid).isdigit() else None
            if floors and bp:
                links.append((bp.get("card_market_ids") or [], str(bp.get("tcg_player_id") or ""), floors))
    return links


def attach_ct(links, tcg_rows, cm_rows, fx=None):
    """Relie CardTrader, TCGplayer et Cardmarket par leurs identifiants (variantes comprises)."""
    tcg_by_id = {str(c["id"]): c for c in tcg_rows if "id" in c}
    cm_by_id = {str(m["i"]): m for m in cm_rows}
    for ids, tp, floors in links:
        t = tcg_by_id.get(tp)
        if t:
            t["ct"] = floors
        for i in ids:
            m = cm_by_id.get(str(i))
            if not m:
                continue
            m["ct"] = floors
            if t:  # lien exact : prix US et variation 7 j de la même carte
                m["us"], m["ud7"] = t["p"], t.get("d7")
                t["mi"] = m["i"]
                if fx:
                    m["x"] = round((m["e"] / (t["p"] * fx) - 1) * 100, 1)


HIST_FILE = "history.json"
KEEP_DAYS = 120


def update_history(today, cm_rows, fx):
    """Ajoute les prix du jour à history.json : une série par carte (clé = id Cardmarket).
    cm = tendance Cardmarket (EUR), ct = plancher CardTrader EN Near Mint (EUR), us = prix TCGplayer converti en EUR."""
    h = {"dates": [], "cm": {}, "ct": {}, "us": {}}
    if os.path.exists(HIST_FILE):
        h = json.load(open(HIST_FILE))
    day = str(today)
    if day not in h["dates"]:
        h["dates"].append(day)
    idx, n = h["dates"].index(day), len(h["dates"])

    def put(group, key, val):
        arr = h[group].setdefault(key, [])
        arr.extend([None] * (n - len(arr)))
        arr[idx] = val

    for m in cm_rows:
        k = str(m["i"])
        put("cm", k, m["e"])
        ct = (m.get("ct") or {}).get("EN")
        if ct:
            put("ct", k, ct[0])
        if fx and m.get("us") is not None:
            put("us", k, round(m["us"] * fx, 2))
    cut = max(0, n - KEEP_DAYS)
    h["dates"] = h["dates"][cut:]
    for g in ("cm", "ct", "us"):
        for k in list(h[g]):
            arr = h[g][k]
            arr.extend([None] * (n - len(arr)))
            arr = arr[cut:]
            if all(v is None for v in arr):
                del h[g][k]
            else:
                h[g][k] = arr
    tmp = HIST_FILE + ".tmp"
    json.dump(h, open(tmp, "w"), separators=(",", ":"))
    os.replace(tmp, HIST_FILE)
    return len(h["dates"]), len(h["cm"])


STATE_FILE = "data/notified.json"
SHIP, FEE = 1.0, 0.05


def ntfy(topic, title, message, tags=None, click=None, priority=3):
    body = {"topic": topic, "title": title, "message": message, "priority": priority}
    if tags:
        body["tags"] = tags
    if click:
        body["click"] = click
    requests.post("https://ntfy.sh", json=body, timeout=30).raise_for_status()


def app_url():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"


def notify_all(topic, cm_rows, today, send=ntfy):
    """Envoie : (1) les objectifs de prix atteints sur ta liste de suivi, (2) les nouvelles opportunités d'arbitrage vérifiées."""
    cfg = {}
    if os.path.exists("watchlist.json"):
        try:
            cfg = json.load(open("watchlist.json"))
        except Exception:
            cfg = {}
    bud = {"min": 5, "max": 70, "mar": 10, **(cfg.get("budget") or {})}
    st = {"arb": {}, "watch": {}}
    if os.path.exists(STATE_FILE):
        try:
            st.update(json.load(open(STATE_FILE)))
        except Exception:
            pass
    by_i = {m["i"]: m for m in cm_rows}
    click, sent, reached = app_url(), 0, set()
    for w in cfg.get("items", []):
        m = by_i.get(w.get("i"))
        if not m or not isinstance(w.get("t"), (int, float)):
            continue
        above = w.get("d") == "above"
        ct = (m.get("ct") or {}).get("EN")
        price = m["e"] if above else (ct[0] if ct else m["e"])
        if (price >= w["t"]) if above else (price <= w["t"]):
            key = f'{w["i"]}|{w.get("d")}|{w["t"]}'
            reached.add(key)
            if key not in st["watch"]:
                send(topic, "🔔 Objectif atteint",
                     f'{w.get("n", m["n"])} : {price:.2f} € ({"≥" if above else "≤"} cible {w["t"]} €)', ["bell"], click)
                st["watch"][key] = str(today)
                sent += 1
    st["watch"] = {k: v for k, v in st["watch"].items() if k in reached}
    hidden = set(cfg.get("hidden") or [])
    cands = []
    for m in cm_rows:
        ct = (m.get("ct") or {}).get("EN")
        if m["i"] in hidden or not ct or not (bud["min"] <= m["e"] <= bud["max"]) or ct[0] >= m["e"]:
            continue
        cost = ct[0] + SHIP
        margin = (m["e"] * (1 - FEE) - cost) / cost * 100
        if margin >= bud["mar"]:
            cands.append((margin, m, ct[0]))
    cands.sort(key=lambda c: -c[0])
    fresh = [c for c in cands
             if (today - datetime.date.fromisoformat(st["arb"].get(str(c[1]["i"]), "1970-01-01"))).days >= 7][:5]
    if fresh:
        lines = [f'{m["n"]} {m["v"]}'.strip() + f' : achat {buy:.2f} € → {m["e"]:.2f} € ({margin:+.0f} %)'
                 for margin, m, buy in fresh]
        send(topic, f"💡 {len(fresh)} opportunité(s) d’arbitrage", "\n".join(lines), ["moneybag"], click)
        for _, m, _ in fresh:
            st["arb"][str(m["i"])] = str(today)
        sent += 1
    cutoff = str(today - datetime.timedelta(days=30))
    st["arb"] = {k: v for k, v in st["arb"].items() if v >= cutoff}
    os.makedirs("data", exist_ok=True)
    json.dump(st, open(STATE_FILE, "w"))
    return sent


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
                "p": round(price, 2), "u": p["url"], "id": p["productId"],
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
        us = {}  # repli : rapprochement par code quand il est unique des deux côtés
        for c in rows:
            if len(tcg_codes.get(c["c"], ())) == 1:
                us[c["c"]] = c
        for m in cm_rows:
            if m["c"] in us and cm_codes.get(m["c"]) == 1:
                t = us[m["c"]]
                m["us"], m["ud7"] = t["p"], t.get("d7")
                t["mi"] = m["i"]
                if fx:
                    m["x"] = round((m["e"] / (t["p"] * fx) - 1) * 100, 1)
        cm = {"updated": cm_date, "rows": cm_rows}
        print(f"Cardmarket : {len(cm_rows)} cartes")
    except Exception as e:  # on garde les données TCGplayer quoi qu'il arrive
        print(f"ATTENTION Cardmarket indisponible : {e}")
    token = os.environ.get("CARDTRADER_TOKEN", "").strip()
    if token:
        try:
            links = cardtrader(token)
            attach_ct(links, rows, cm["rows"] if cm else [], fx)
            print(f"CardTrader : {len(links)} cartes avec annonces")
        except Exception as e:
            print(f"ATTENTION CardTrader indisponible : {e}")
    else:
        print("CardTrader ignoré (aucun jeton CARDTRADER_TOKEN)")
    if cm:
        try:
            days, cards_n = update_history(today, cm["rows"], fx)
            print(f"Historique : {cards_n} cartes sur {days} jour(s)")
        except Exception as e:
            print(f"ATTENTION historique non mis à jour : {e}")
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if cm and topic:
        try:
            print(f"Notifications envoyées : {notify_all(topic, cm['rows'], today)}")
        except Exception as e:
            print(f"ATTENTION notifications non envoyées : {e}")
    json.dump({"updated": str(today), "source": "TCGCSV / TCGplayer", "fx": fx, "sets": sets, "rows": rows, "cm": cm},
              open("signals.json", "w"), separators=(",", ":"))

    for f in os.listdir(SNAP_DIR):  # garde 30 jours
        if f[:10] < str(today - datetime.timedelta(days=30)):
            os.remove(f"{SNAP_DIR}/{f}")
    print(f"{len(rows)} cartes enregistrées")


if __name__ == "__main__":
    main()
