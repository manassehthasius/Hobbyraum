#!/usr/bin/env python3
"""Hobbyraum-Monitor: findet NEUE passende Hobby-/Bastelräume und markiert verschwundene Inserate.

Quellen
  - Flatfox-API (spiegelt auch viele Homegate/ImmoScout24-Inserate)
  - maison.work (Gewerbe-Marktplatz, strukturierte Daten mit Koordinaten)
  - storabble.com (Lager-/Bastelraum-Vergleich, u. a. Allianz, Wincasa, Livit)
  - Websites von Gesewo, Terresta, L+B, HGW, Gewerbehaus Passant

Duplikate
  - data/seen.json: jede einmal geprüfte ID wird nie erneut gemeldet
  - zusätzlich Adresse + Fläche («addr:…»): dasselbe Objekt aus einer zweiten Quelle
    oder neu inseriert löst keinen zweiten Treffer aus

Offline-Erkennung (Repo-Modus)
  Flatfox-, maison- und storabble-Angebote, die nicht mehr online sind, bekommen
  "offline": true (+ "offline_seit"); kommen sie zurück, wird das Feld entfernt.

Benachrichtigung (optional, Umgebungsvariablen)
  NTFY_TOPIC | TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID | SMTP_HOST/SMTP_USER/SMTP_PASS/MAIL_TO

Aufruf
  python3 scraper/monitor.py                     # Repo-Modus: data/*.json aktualisieren
  python3 scraper/monitor.py --dry-run           # nur anzeigen, nichts schreiben
  python3 scraper/monitor.py --seen-file S --out N.json   # Test: Funde nach N.json, Repo unverändert
"""
import argparse, datetime, hashlib, html, json, math, os, pathlib, re, smtplib, sys, time, urllib.parse, urllib.request
from email.message import EmailMessage

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import flatfox                      # noqa: E402
from filter import is_candidate, normalize, MAX_CHF, MAX_M2  # noqa: E402

SEEN = ROOT / "data/seen.json"
ANGEBOTE = ROOT / "data/angebote.json"
META = ROOT / "data/meta.json"
UA = {"User-Agent": "Mozilla/5.0 (hobbyraum-monitor; privat)", "Accept-Language": "de-CH,de"}
KW = re.compile(r"hobby|bastel|atelier|werkraum|werkstatt|lagerraum|kellerraum|proberaum|übungsraum|musikraum", re.I)
NEAR = re.compile(r"winterthur|8400|8401|8402|8403|8404|8405|8406|8408|8409|seuzach|wiesendangen|elsau|neftenbach|brütten|zell|rickenbach|dinhard|hettlingen|pfungen|töss|veltheim|wülflingen|oberwinterthur|seen", re.I)
SMALL_KM = 10.0

# Websites ohne Portal-Inserat: neue Detail-Links mit passenden Stichworten melden
SITES = [
    {"id": "gesewo", "name": "Gesewo", "url": "https://www.gesewo.ch/zu-vermieten.html", "link": r"/details-\d+/[^\"#]+\.html"},
    {"id": "terresta", "name": "Terresta", "url": "https://www.terresta.ch/mietangebote", "link": r"/mietangebote/\d+/"},
    {"id": "lb", "name": "L+B AG", "url": "https://www.l-b.ch/immobilien/mieten/", "link": r"/immobilie/[^\"#]+/"},
    {"id": "hgw", "name": "HGW", "url": "https://www.hgw-wohnen.ch/wohnen/freie-objekte", "link": r"/wohnen/freie-objekte/[^\"#]+"},
    {"id": "passant", "name": "Gewerbehaus Passant", "url": "https://passant.ch/", "adresse": "Zürcherstrasse 320, 8406 Winterthur",
     "rows": r"(?:Büro|Gewerbefläche|Lager|Übungsraum|Atelier|Werkstatt)\w*\s+\d{3,4}\s+\d+ m2 CHF [\d'.]+"},
]
MAISON_PAGES = ["https://www.maison.work/ch/mieten/hobbyraum/winterthur", "https://www.maison.work/ch/mieten/lager/winterthur",
                "https://www.maison.work/ch/mieten/winterthur"]
MAISON_OK = {"Hobbyraum", "Lager", "Atelier", "Werkstatt"}          # immer prüfen
MAISON_SMALL = {"Büro", "Gewerbe", "Praxis"}                        # nur kleine, günstige
STORABBLE_PAGES = ["https://storabble.com/en/storage-room/winterthur", "https://storabble.com/en/storage-room/winterthur?page=2"]


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read().decode("utf-8", "ignore")

def text(s):
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()

def load(p, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default

def km(lat, lon):
    return round(flatfox.km(float(lat), float(lon)), 1)

def geocode(addr):
    """Adresse -> (lat, lon) über den öffentlichen Geodienst des Bundes (geo.admin.ch)."""
    try:
        u = "https://api3.geo.admin.ch/rest/services/api/SearchServer?" + urllib.parse.urlencode(
            {"searchText": addr, "type": "locations", "sr": 4326, "limit": 1})
        r = json.loads(get(u)).get("results") or []
        return (r[0]["attrs"]["lat"], r[0]["attrs"]["lon"]) if r else None
    except Exception:
        return None

def addr_key(adresse, m2):
    """Strasse + Hausnummer + Fläche, normalisiert – erkennt dasselbe Objekt über Quellen hinweg."""
    a = (adresse or "").lower().split(",")[0]
    a = re.sub(r"strasse|str\.|str\b", "str", a)
    m = re.match(r"\s*(?:geb\.\s*)?([a-zäöüéè.\- ]+?)\s*(\d+[a-z]?)", a)
    if not m:
        return None
    street = re.sub(r"[^a-zäöüéè]", "", m.group(1))
    return f"addr:{street}{m.group(2)}|{round(float(m2)) if m2 else ''}"

def street_key(adresse):
    """Nur Strasse + Hausnummer: dasselbe Gebäude, egal welche Fläche."""
    k = addr_key(adresse, None)
    return "street:" + k[5:].rstrip("|") if k else None

def doc_id(i):
    """Gültige, stabile ID (keine Schrägstriche usw.)."""
    i = i.replace("https://", "").replace("http://", "")
    return re.sub(r"[^A-Za-z0-9_\-.~:@+]", "_", i)[:200].strip(".") or "x"


# ------------------------------------------------------------------ Quellen
def check_flatfox(seen, current):
    box = (flatfox.ORIGIN[0] + 0.091, flatfox.ORIGIN[0] - 0.091, flatfox.ORIGIN[1] + 0.135, flatfox.ORIGIN[1] - 0.135)
    pins = {}
    for c in ["SECONDARY", "INDUSTRY", "PARK"]:
        for p in flatfox.pins(c, box):
            d = flatfox.km(p["latitude"], p["longitude"])
            if d <= flatfox.MAX_KM:
                pins[p["pk"]] = (c, d)
    current["flatfox"] = set(pins)
    new_pks = [pk for pk in pins if f"flatfox:{pk}" not in seen]
    found = []
    for x in flatfox.details(new_pks) if new_pks else []:
        try:
            x["_cat"], x["_km"] = pins[x["pk"]]
            ok = is_candidate(x)
            r = normalize(x) if ok else None
        except Exception as e:                  # ein kaputtes Inserat blockiert nicht die anderen
            print(f"Flatfox {x.get('pk')}: {e}", file=sys.stderr)
            continue
        seen[f"flatfox:{x['pk']}"] = 1            # erst nach erfolgreicher Prüfung als gesehen markieren
        if not r:
            continue
        keys = [k for k in ("dup:" + hashlib.sha1(f"{(r['adresse'] or '').lower()}|{r['m2']}|{r['chf_monat']}".encode()).hexdigest()[:12],
                            addr_key(r["adresse"], r["m2"])) if k]
        if any(k in seen for k in keys):
            continue
        for k in keys:
            seen[k] = 1
        found.append(r)
    return found


def maison_items(html_text):
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html_text, re.S):
        try:
            j = json.loads(block)
        except Exception:
            continue
        for obj in (j if isinstance(j, list) else [j]):
            me = obj.get("mainEntity") if isinstance(obj, dict) else None
            if not isinstance(me, dict):
                continue
            for it in me.get("itemListElement", []) or []:
                x = it.get("item") if isinstance(it, dict) else None
                if isinstance(x, dict) and x.get("@type") == "RealEstateListing":
                    yield x

def maison_parse(x):
    loc = x.get("contentLocation") or {}
    a, g = loc.get("address") or {}, loc.get("geo") or {}
    desc = x.get("description") or ""
    m2 = re.search(r"über\s*([\d'.]+)\s*m²", desc)
    m2 = float(m2.group(1).replace("'", "")) if m2 else None
    chf = None
    pm = re.search(r"CHF\s*([\d'.]+)\s*pro Monat", desc)
    py = re.search(r"CHF\s*([\d'.]+)\s*pro m²\s*/\s*Jahr", desc)
    if pm:
        chf = round(float(pm.group(1).replace("'", "")))
    elif py and m2:
        chf = round(float(py.group(1).replace("'", "")) * m2 / 12)
    avail = re.search(r"Verfügbar ab ([^.]+)\.", desc)
    street = (a.get("streetAddress") or "").strip()
    return {
        "mid": x["url"].rstrip("/").split("/")[-1], "url": x["url"],
        "kat": (x.get("offers") or {}).get("category") or "",
        "adresse": ", ".join(filter(None, [street, f"{a.get('postalCode', '')} {a.get('addressLocality', '')}".strip()])),
        "street": street, "m2": m2, "chf": chf,
        "km": km(g["latitude"], g["longitude"]) if g.get("latitude") else None,
        "verfuegbar": avail.group(1).strip() if avail else None,
    }

def check_maison(seen, current):
    items, ok_pages = {}, 0
    for base in MAISON_PAGES:
        for page in (1, 2, 3, 4):
            try:
                s = get(base + (f"?page={page}" if page > 1 else ""))
            except Exception:
                break
            ok_pages += 1
            got = 0
            for x in maison_items(s):
                p = maison_parse(x)
                if p["mid"] not in items:
                    items[p["mid"]] = p; got += 1
            if got == 0:
                break
            time.sleep(0.5)
    if ok_pages:
        current["maison"] = set(items)
    found = []
    for mid, p in items.items():
        key = f"maison:{mid}"
        if key in seen:
            continue
        seen[key] = 1
        if p["km"] is None or p["km"] > SMALL_KM:
            continue
        if p["kat"] in MAISON_OK:
            if (p["chf"] and p["chf"] > MAX_CHF) or (p["m2"] and p["m2"] > MAX_M2):
                continue
            if p["chf"] is None and not p["m2"] and p["kat"] != "Hobbyraum":
                continue                          # «Preis/Fläche auf Anfrage» bei Lager = meist Grossfläche
        elif p["kat"] in MAISON_SMALL:
            if not (p["m2"] and p["m2"] <= 40 and p["chf"] and p["chf"] <= 500):
                continue
        else:
            continue
        ak, sk = addr_key(p["street"], p["m2"]), street_key(p["street"])
        if (ak and ak in seen) or (sk and sk in seen):
            continue
        for k in (ak, sk):
            if k: seen[k] = 1
        found.append({"id": key, "quelle": "maison.work", "typ": p["kat"].upper(), "titel": f"{p['kat']} {int(p['m2']) if p['m2'] else '?'} m²",
                      "adresse": p["adresse"], "km": p["km"], "chf_monat": p["chf"], "m2": p["m2"], "verfuegbar": p["verfuegbar"],
                      "anbieter": "über maison.work", "link": p["url"], "kontaktformular": p["url"]})
    return found


def storabble_detail(url):
    t = text(get(url))
    chf = re.search(r"CHF\s*([\d'.]+)\s*/\s*Month", t)
    m2 = re.search(r"CHF\s*[\d'.]+\s*/\s*Month\s*([\d.]+)\s*m²", t) or re.search(r"Details\s*([\d.]+)\s*m²", t)
    place = re.search(r"Place (.+?) Information about the provider", t)
    prov = re.search(r"Offered by (.+?) (?:Report this listing|What is self storage|Contact provider|To the provider)", t)
    desc = re.search(r"Description (.+?) (?:Contract details|Information about the provider)", t)
    title = re.search(r"Storage rooms in [^ ]+ (.+?) (?:Leisure space|Storage room|Self storage unit|Previous image|Reply within)", t)
    return {"chf": round(float(chf.group(1).replace("'", ""))) if chf else None, "m2": float(m2.group(1)) if m2 else None,
            "adresse": place.group(1).strip() if place else None, "anbieter": prov.group(1).strip() if prov else None,
            "beschreibung": desc.group(1).strip() if desc else "", "titel": title.group(1).strip() if title else None,
            "selfstorage": "Self storage unit" in t or "Self storage verified" in t}

def check_storabble(seen, current):
    links, ok = set(), 0
    for u in STORABBLE_PAGES:
        try:
            s = get(u); ok += 1
        except Exception:
            continue
        links |= {urllib.parse.urljoin(u, h) for h in re.findall(r'href="(/en/listing/[^"#?]+)"', s)}
    if ok:
        current["storabble"] = {l.rstrip("/").rsplit("-", 1)[-1] for l in links}
    found = []
    for l in sorted(links):
        sid = l.rstrip("/").rsplit("-", 1)[-1]
        key = f"storabble:{sid}"
        if key in seen:
            continue
        try:
            d = storabble_detail(l)
        except Exception:
            continue                              # nicht als gesehen markieren -> nächster Lauf versucht es erneut
        seen[key] = 1
        time.sleep(0.5)
        if not d["adresse"] or (d["chf"] and d["chf"] > MAX_CHF) or (d["m2"] and d["m2"] > MAX_M2):
            continue
        hobbyish = KW.search(d["titel"] or "") or re.search(r"hobby|bastel|werkstatt|atelier", d["beschreibung"], re.I)
        if d["selfstorage"] and not hobbyish:
            continue                              # reine Self-Storage-Boxen: Hobbynutzung meist verboten
        if d["chf"] is None and (not d["m2"] or d["m2"] > 40) and not hobbyish:
            continue                              # «Preis auf Anfrage» bei grosser Lagerfläche
        ak, sk = addr_key(d["adresse"], d["m2"]), street_key(d["adresse"])
        if (ak and ak in seen) or (sk and sk in seen):
            continue
        g = geocode(d["adresse"])
        dist = km(*g) if g else None
        if dist is None or dist > SMALL_KM:
            continue
        for k in (ak, sk):
            if k: seen[k] = 1
        found.append({"id": key, "quelle": "storabble", "typ": "STORAGE_ROOM", "titel": d["titel"] or "Lager-/Bastelraum",
                      "adresse": d["adresse"], "km": dist, "chf_monat": d["chf"], "m2": d["m2"], "anbieter": d["anbieter"],
                      "beschreibung": d["beschreibung"], "link": l, "kontaktformular": l})
    return found


def check_site(site, seen):
    s = get(site["url"])
    found = []
    if "rows" in site:
        for row in set(re.findall(site["rows"], text(s))):
            key = f"{site['id']}:" + hashlib.sha1(row.encode()).hexdigest()[:12]
            if key in seen:
                continue
            seen[key] = 1
            m2 = int(re.search(r"(\d+) m2", row).group(1))
            chf = float(re.search(r"CHF ([\d'.]+)", row).group(1).replace("'", ""))
            if m2 <= 40 or chf <= 900:            # nur kleine/günstige Räume melden
                found.append({"id": key, "quelle": site["name"], "titel": row, "adresse": site.get("adresse", ""),
                              "chf_monat": round(chf), "m2": m2, "link": site["url"]})
        return found
    hrefs = {urllib.parse.urljoin(site["url"], h) for h in re.findall(r'href="([^"]+)"', s) if re.search(site["link"], h)}
    for h in sorted(hrefs):
        key = f"{site['id']}:{h}"
        if key in seen:
            continue
        try:
            t = text(get(h))[:4000]
        except Exception:
            continue                              # erst bei Erfolg als gesehen markieren
        seen[key] = 1
        if KW.search(t) and NEAR.search(t):
            title = re.split(r" [-|–] ", t, maxsplit=1)[0][:120]
            found.append({"id": key, "quelle": site["name"], "titel": title, "adresse": "", "link": h,
                          "beschreibung": t[:600]})
        time.sleep(0.5)
    return found


# ------------------------------------------------------------------ Benachrichtigung
def fmt(r):
    parts = [f"🆕 Hobbyraum: {r.get('titel')}"]
    if r.get("adresse"): parts.append(f"📍 {r['adresse']}" + (f" ({r['km']} km)" if r.get("km") is not None else ""))
    if r.get("chf_monat"): parts.append(f"💰 CHF {r['chf_monat']}/Monat" + (f" · {r['m2']} m²" if r.get("m2") else ""))
    if r.get("verfuegbar"): parts.append(f"📅 {r['verfuegbar']}")
    if r.get("anbieter"): parts.append(f"🏢 {r['anbieter']}")
    parts.append(f"🔗 {r['link']}")
    return "\n".join(parts)

def notify(r, dry):
    msg = fmt(r)
    print(msg + "\n")
    if dry:
        return
    if os.getenv("NTFY_TOPIC"):
        req = urllib.request.Request(f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}", data=msg.encode(),
                                     headers={"Title": "Neuer Hobbyraum", "Click": r["link"], "Priority": "high", "Tags": "house"})
        urllib.request.urlopen(req, timeout=20)
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        data = urllib.parse.urlencode({"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": msg}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage", data=data, timeout=20)
    if os.getenv("SMTP_HOST") and os.getenv("MAIL_TO"):
        em = EmailMessage(); em["Subject"] = f"Neuer Hobbyraum: {r.get('adresse') or r.get('titel')}"
        em["From"] = os.environ["SMTP_USER"]; em["To"] = os.environ["MAIL_TO"]; em.set_content(msg)
        with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "465"))) as s:
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"]); s.send_message(em)


# ------------------------------------------------------------------ Daten schreiben
def to_doc(r, now):
    """Einheitliches Datenformat wie data/angebote.json, plus Zeitpunkt der Entdeckung."""
    return {
        "id": doc_id(r["id"]), "status": "neu", "prio": 2, "verifiziert": f"Monitor {now[:10]} ({r.get('quelle') or 'Flatfox'})",
        "entdeckt": now, "titel": r.get("titel"), "typ": r.get("typ"), "adresse": r.get("adresse") or r.get("quelle"),
        "km": r.get("km"), "chf_monat": r.get("chf_monat"), "preis_text": None, "m2": r.get("m2"), "verfuegbar": r.get("verfuegbar"),
        "anbieter": r.get("anbieter") or r.get("quelle"), "kontakt_person": None, "telefon": None, "email": None,
        "kontaktweg": r.get("kontaktformular"), "link": r["link"], "weitere_links": [],
        "nutzung": None, "hinweise": (r.get("beschreibung") or "")[:400] or None,
        "frage": "Ist dieser Raum noch verfügbar, und wäre eine ruhige Hobbynutzung möglich? Gerne würde ich ihn besichtigen.",
        "publiziert": r.get("publiziert")}

def source_ids(a):
    """Welche Inserate (Quelle -> IDs) gehören zu einem Angebot? Für die Offline-Erkennung."""
    urls = [a.get("link") or ""] + list(a.get("weitere_links") or [])
    out = {"flatfox": {int(m) for u in urls for m in re.findall(r"flatfox\.ch/de/(\d+)/", u)},
           "maison": {m for u in urls for m in re.findall(r"maison\.work/.+/(\d+)/?$", u)},
           "storabble": {m for u in urls for m in re.findall(r"storabble\.com/en/listing/.+-(\d+)/?$", u)}}
    return {k: v for k, v in out.items() if v}

def update_offline(d, current, now):
    """Markiert Angebote als offline, deren Inserate bei keiner geprüften Quelle mehr auftauchen."""
    sane = {k: v for k, v in current.items() if v is not None and len(v) >= (100 if k == "flatfox" else 3)}
    changed = []
    for a in d["angebote"]:
        ids = source_ids(a)
        checkable = {k: v for k, v in ids.items() if k in sane}
        if not checkable or set(ids) - set(sane):   # nur entscheiden, wenn alle Quellen des Angebots geprüft wurden
            continue
        online = any(v & sane[k] for k, v in checkable.items())
        if not online and not a.get("offline"):
            a["offline"] = True; a["offline_seit"] = now; changed.append(("offline", a["id"]))
        elif online and a.get("offline"):
            a.pop("offline", None); a.pop("offline_seit", None); changed.append(("wieder online", a["id"]))
    return changed

def add_to_list(d, found, now):
    ids = {a["id"] for a in d["angebote"]}
    for r in found:
        doc = to_doc(r, now)
        if doc["id"] not in ids:
            d["angebote"].append(doc); ids.add(doc["id"])

def write_meta(found, errors, intervall, offline_changes):
    m = load(META, {})
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    m.update({"letzte_pruefung": now, "intervall": intervall, "letzter_lauf": f"{len(found)} neue(s) Angebot(e)", "fehler": errors,
              "quellen": ["Flatfox", "maison.work", "storabble", "Gesewo", "Terresta", "L+B", "HGW", "Passant"]})
    if found:
        m["letzter_fund"] = now
    if offline_changes:
        m["letzte_offline_aenderung"] = now
    META.write_text(json.dumps(m, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="nichts senden oder schreiben, nur anzeigen")
    ap.add_argument("--seen-file", help="eigene Datei für gesehene IDs (Test)")
    ap.add_argument("--out", help="Funde als JSON-Liste in diese Datei schreiben; Repo-Daten bleiben unverändert")
    ap.add_argument("--intervall", default="alle 30 Minuten")
    a = ap.parse_args()
    seen_path = pathlib.Path(a.seen_file) if a.seen_file else SEEN
    seen = load(seen_path, {})
    if isinstance(seen, list):
        seen = {k: 1 for k in seen}
    d = load(ANGEBOTE, {"angebote": [], "ausgeschlossen": []})
    for x in d["angebote"] + d.get("ausgeschlossen", []):     # bereits erfasste/ausgeschlossene nie neu melden
        seen.setdefault(x["id"], 1)
        for k, v in source_ids(x).items():
            for i in v:
                seen.setdefault(f"{k}:{i}", 1)
        for k in (addr_key(x.get("adresse"), x.get("m2")), street_key(x.get("adresse"))):
            if k:
                seen.setdefault(k, 1)
    for v in load(ROOT / "data/verwaltungen.json", {}).get("verwaltungen", []):
        k = street_key(v.get("adresse"))
        if k:
            seen.setdefault(k, 1)

    found, errors, current = [], [], {"flatfox": None, "maison": None, "storabble": None}
    for name, fn in (("Flatfox", lambda: check_flatfox(seen, current)), ("maison.work", lambda: check_maison(seen, current)),
                     ("storabble", lambda: check_storabble(seen, current))):
        try:
            found += fn()
        except Exception as e:
            errors.append(f"{name}: {e}"); print(f"{name}-Fehler:", e, file=sys.stderr)
    for site in SITES:
        try:
            found += check_site(site, seen)
        except Exception as e:
            errors.append(f"{site['name']}: {e}"); print(f"{site['name']}-Fehler:", e, file=sys.stderr)

    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    if a.dry_run:
        for r in found:
            notify(r, True)
        print(f"Probelauf – {len(found)} neue(s) Angebot(e), Fehler: {errors or 'keine'}")
        return
    seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=0, sort_keys=True))
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps([to_doc(r, now) for r in found], ensure_ascii=False, indent=1))
        print(f"{len(found)} neue(s) Angebot(e) -> {a.out}; Fehler: {errors or 'keine'}")
        return
    for r in found:
        try:
            notify(r, False)
        except Exception as e:
            print("Benachrichtigung fehlgeschlagen:", e, file=sys.stderr)
    add_to_list(d, found, now)
    changes = update_offline(d, current, now)
    for c in changes:
        print("Status:", *c)
    ANGEBOTE.write_text(json.dumps(d, ensure_ascii=False, indent=1))
    write_meta(found, errors, a.intervall, changes)
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M} – {len(found)} neue(s) Angebot(e), {len(changes)} Statuswechsel, Fehler: {errors or 'keine'}")


if __name__ == "__main__":
    main()
