#!/usr/bin/env python3
"""Hobbyraum-Monitor: meldet NEUE passende Angebote sofort (keine Tageszusammenfassung).

Quellen:  Flatfox-API (spiegelt auch viele Homegate/IS24-Inserate) + Websites von Verwaltungen/Genossenschaften.
Duplikate: data/seen.json merkt sich jede gemeldete ID; Flatfox-Treffer werden zusätzlich nach Adresse+Fläche dedupliziert.
Benachrichtigung (optional, über Umgebungsvariablen):
  NTFY_TOPIC                 -> Push aufs Handy via ntfy.sh (App «ntfy», Thema abonnieren)
  TELEGRAM_BOT_TOKEN/_CHAT_ID-> Telegram-Nachricht
  SMTP_HOST/SMTP_USER/SMTP_PASS/MAIL_TO -> E-Mail
  GITHUB_ISSUE=1 (+ GITHUB_TOKEN, GITHUB_REPOSITORY, GH_MENTION) -> GitHub-Issue pro Fund
Aufruf:  python3 scraper/monitor.py [--dry-run] [--seed]
"""
import argparse, datetime, hashlib, html, json, os, pathlib, re, smtplib, sys, time, urllib.parse, urllib.request
from email.message import EmailMessage

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import flatfox                      # noqa: E402
from filter import is_candidate, normalize  # noqa: E402

SEEN = ROOT / "data/seen.json"
ANGEBOTE = ROOT / "data/angebote.json"
UA = {"User-Agent": "Mozilla/5.0 (hobbyraum-monitor; privat)", "Accept-Language": "de-CH,de"}
KW = re.compile(r"hobby|bastel|atelier|werkraum|werkstatt|lagerraum|kellerraum|proberaum|übungsraum|musikraum", re.I)
NEAR = re.compile(r"winterthur|8400|8401|8402|8403|8404|8405|8406|8408|8409|seuzach|wiesendangen|elsau|neftenbach|brütten|zell|rickenbach|dinhard|hettlingen|pfungen|töss|veltheim|wülflingen|oberwinterthur|seen", re.I)

# Websites ohne Portal-Inserat: neue Detail-Links mit passenden Stichworten melden
SITES = [
    {"id": "gesewo", "name": "Gesewo", "url": "https://www.gesewo.ch/zu-vermieten.html", "link": r"/details-\d+/[^\"#]+\.html"},
    {"id": "terresta", "name": "Terresta", "url": "https://www.terresta.ch/mietangebote", "link": r"/mietangebote/\d+/"},
    {"id": "lb", "name": "L+B AG", "url": "https://www.l-b.ch/immobilien/mieten/", "link": r"/immobilie/[^\"#]+/"},
    {"id": "hgw", "name": "HGW", "url": "https://www.hgw-wohnen.ch/wohnen/freie-objekte", "link": r"/wohnen/freie-objekte/[^\"#]+"},
    {"id": "passant", "name": "Gewerbehaus Passant", "url": "https://passant.ch/", "adresse": "Zürcherstrasse 320, 8406 Winterthur", "rows": r"(?:Büro|Gewerbefläche|Lager|Übungsraum|Atelier|Werkstatt)\w*\s+\d{3,4}\s+\d+ m2 CHF [\d'.]+"},
]

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

# ------------------------------------------------------------------ Quellen
def check_flatfox(seen):
    box = (flatfox.ORIGIN[0] + 0.091, flatfox.ORIGIN[0] - 0.091, flatfox.ORIGIN[1] + 0.135, flatfox.ORIGIN[1] - 0.135)
    pins = {}
    for c in ["SECONDARY", "INDUSTRY", "PARK"]:
        for p in flatfox.pins(c, box):
            d = flatfox.km(p["latitude"], p["longitude"])
            if d <= flatfox.MAX_KM:
                pins[p["pk"]] = (c, d)
    new_pks = [pk for pk in pins if f"flatfox:{pk}" not in seen]
    found = []
    for x in flatfox.details(new_pks) if new_pks else []:
        x["_cat"], x["_km"] = pins[x["pk"]]
        seen[f"flatfox:{x['pk']}"] = 1          # jede ID nur einmal prüfen
        if not is_candidate(x):
            continue
        r = normalize(x)
        dkey = "dup:" + hashlib.sha1(f"{(r['adresse'] or '').lower()}|{r['m2']}|{r['chf_monat']}".encode()).hexdigest()[:12]
        if dkey in seen:                        # gleiches Objekt neu inseriert -> kein Alarm
            continue
        seen[dkey] = 1
        found.append(r)
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
        seen[key] = 1
        try:
            t = text(get(h))[:4000]
        except Exception:
            continue
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
    if os.getenv("GITHUB_ISSUE") and os.getenv("GITHUB_TOKEN"):
        body = msg + (f"\n\n@{os.environ['GH_MENTION']}" if os.getenv("GH_MENTION") else "")
        req = urllib.request.Request(f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/issues",
                                     data=json.dumps({"title": f"🆕 Hobbyraum: {r.get('adresse') or r.get('titel')}", "body": body, "labels": ["neues-angebot"]}).encode(),
                                     headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"})
        urllib.request.urlopen(req, timeout=20)

def add_to_list(found):
    d = load(ANGEBOTE, None)
    if not d:
        return
    ids = {a["id"] for a in d["angebote"]}
    for r in found:
        if r["id"] in ids:
            continue
        d["angebote"].append({
            "id": r["id"], "status": "neu", "prio": 2, "verifiziert": f"Monitor {datetime.date.today()}",
            "titel": r.get("titel"), "typ": r.get("typ"), "adresse": r.get("adresse") or r.get("quelle"), "km": r.get("km"),
            "chf_monat": r.get("chf_monat"), "preis_text": None, "m2": r.get("m2"), "verfuegbar": r.get("verfuegbar"),
            "anbieter": r.get("anbieter") or r.get("quelle"), "kontakt_person": None, "telefon": None, "email": None,
            "kontaktweg": r.get("kontaktformular"), "link": r["link"], "weitere_links": [],
            "nutzung": None, "hinweise": (r.get("beschreibung") or "")[:300] or None,
            "frage": "Ist dieser Raum noch verfügbar, und wäre eine ruhige Hobbynutzung möglich? Gerne würde ich ihn besichtigen.",
            "publiziert": r.get("publiziert")})
    ANGEBOTE.write_text(json.dumps(d, ensure_ascii=False, indent=1))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="nichts senden, nur anzeigen")
    ap.add_argument("--seed", action="store_true", help="aktuellen Stand als bekannt markieren, nichts melden")
    a = ap.parse_args()
    seen = load(SEEN, {})
    known = load(ANGEBOTE, {"angebote": [], "ausgeschlossen": []})
    for x in known["angebote"] + known.get("ausgeschlossen", []):   # bereits erfasste/ausgeschlossene nie neu melden
        seen.setdefault(x["id"], 1)
    found = []
    try:
        found += check_flatfox(seen)
    except Exception as e:
        print("Flatfox-Fehler:", e, file=sys.stderr)
    for site in SITES:
        try:
            found += check_site(site, seen)
        except Exception as e:
            print(f"{site['name']}-Fehler:", e, file=sys.stderr)
    SEEN.write_text(json.dumps(seen, ensure_ascii=False, indent=0, sort_keys=True))
    if a.seed:
        print(f"Seed: {len(seen)} IDs als bekannt markiert, {len(found)} würden sonst gemeldet.")
        return
    for r in found:
        notify(r, a.dry_run)
    if found:
        add_to_list(found)
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M} – {len(found)} neue(s) Angebot(e).")

if __name__ == "__main__":
    main()
