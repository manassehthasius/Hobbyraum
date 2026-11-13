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
Webseiten-Modus (für den Claude-Prüflauf, ohne Repo):
         python3 monitor.py --seen-file seen.json --out neu.json
         -> schreibt neue Angebote als JSON-Liste nach neu.json und aktualisiert seen.json; sendet nichts.
"""
import argparse, datetime, hashlib, html, json, os, pathlib, re, smtplib, sys, time, urllib.parse, urllib.request
from email.message import EmailMessage

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
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

def to_doc(r, now):
    """Einheitliches Datenformat (wie data/angebote.json) plus Zeitpunkt der Entdeckung."""
    return {
        "id": r["id"], "status": "neu", "prio": 2, "verifiziert": f"Monitor {now[:10]}", "entdeckt": now,
        "titel": r.get("titel"), "typ": r.get("typ"), "adresse": r.get("adresse") or r.get("quelle"), "km": r.get("km"),
        "chf_monat": r.get("chf_monat"), "preis_text": None, "m2": r.get("m2"), "verfuegbar": r.get("verfuegbar"),
        "anbieter": r.get("anbieter") or r.get("quelle"), "kontakt_person": None, "telefon": None, "email": None,
        "kontaktweg": r.get("kontaktformular"), "link": r["link"], "weitere_links": [],
        "nutzung": None, "hinweise": (r.get("beschreibung") or "")[:400] or None,
        "frage": "Ist dieser Raum noch verfügbar, und wäre eine ruhige Hobbynutzung möglich? Gerne würde ich ihn besichtigen.",
        "publiziert": r.get("publiziert")}

def doc_id(i):
    """Gültige Dokument-ID für die Datenbank der Webseite (keine Schrägstriche usw.)."""
    i = i.replace("https://", "").replace("http://", "")
    return re.sub(r"[^A-Za-z0-9_\-.~:@+]", "_", i)[:200].strip(".") or "x"

def write_batch(d, found, seen, errors, intervall):
    """Erzeugt <d>/writes.json: fertige ArtifactData-batch-Einträge (max. 50), Dokumente als Dateien daneben.
    Die beiden meta-Einträge brauchen noch if_version (Platzhalter "IF_VERSION_SEEN"/"IF_VERSION_STATUS")."""
    d.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    writes, lines = [], []
    for n, r in enumerate(found[:48]):
        doc = to_doc(r, now)
        doc["id"] = doc_id(doc["id"])
        f = d / f"doc_{n}.json"; f.write_text(json.dumps(doc, ensure_ascii=False))
        writes.append({"op": "set", "collection": "angebote", "doc_id": doc["id"], "file_path": str(f.resolve())})
        lines.append(f"{doc.get('adresse') or doc.get('titel')} – " + (f"CHF {doc['chf_monat']}" if doc.get("chf_monat") else "Preis offen"))
    (d / "seen_doc.json").write_text(json.dumps({"ids": sorted(seen)}, ensure_ascii=False))
    status = {"letzte_pruefung": now, "intervall": intervall, "letzter_lauf": f"{len(found)} neue(s) Angebot(e)",
              "fehler": errors}
    if found:
        status["letzter_fund"] = now
    (d / "status_doc.json").write_text(json.dumps(status, ensure_ascii=False))
    writes.append({"op": "set", "collection": "meta", "doc_id": "seen", "file_path": str((d / "seen_doc.json").resolve()), "if_version": "IF_VERSION_SEEN"})
    writes.append({"op": "update", "collection": "meta", "doc_id": "status", "file_path": str((d / "status_doc.json").resolve()), "if_version": "IF_VERSION_STATUS"})
    (d / "writes.json").write_text(json.dumps(writes, ensure_ascii=False))
    (d / "zusammenfassung.txt").write_text((f"{len(found)} neue(s) Angebot(e):\n" + "\n".join(lines)) if found else "Keine neuen Angebote.")
    print((d / "zusammenfassung.txt").read_text())
    if errors:
        print("Fehler:", "; ".join(errors))

def add_to_list(found):
    """Repo-Modus: neue Treffer mit Status «neu» an data/angebote.json anhängen."""
    d = load(ANGEBOTE, None)
    if not d:
        return
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    ids = {a["id"] for a in d["angebote"]}
    for r in found:
        doc = to_doc(r, now)
        doc["id"] = doc_id(doc["id"])
        if doc["id"] not in ids:
            d["angebote"].append(doc); ids.add(doc["id"])
    ANGEBOTE.write_text(json.dumps(d, ensure_ascii=False, indent=1))

def write_meta(found, errors, intervall):
    """Repo-Modus: Prüfstatus für die Webseite (data/meta.json)."""
    p = ROOT / "data/meta.json"
    m = load(p, {})
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    m.update({"letzte_pruefung": now, "intervall": intervall, "letzter_lauf": f"{len(found)} neue(s) Angebot(e)", "fehler": errors})
    if found:
        m["letzter_fund"] = now
    p.write_text(json.dumps(m, ensure_ascii=False, indent=1))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="nichts senden, nur anzeigen")
    ap.add_argument("--seed", action="store_true", help="aktuellen Stand als bekannt markieren, nichts melden")
    ap.add_argument("--seen-file", help="eigene Datei für gesehene IDs (Webseiten-Modus)")
    ap.add_argument("--out", help="neue Angebote als JSON-Liste in diese Datei schreiben, nichts senden")
    ap.add_argument("--batch-dir", help="Webseiten-Modus: fertige Datenbank-Schreibvorgänge (writes.json + Dokumente) in diesen Ordner")
    ap.add_argument("--intervall", default="3× täglich (ca. 7:40, 12:40, 17:40 Uhr)")
    a = ap.parse_args()
    seen_path = pathlib.Path(a.seen_file) if a.seen_file else SEEN
    seen = load(seen_path, {})
    if isinstance(seen, list):
        seen = {k: 1 for k in seen}
    known = load(ANGEBOTE, {"angebote": [], "ausgeschlossen": []})
    for x in known["angebote"] + known.get("ausgeschlossen", []):   # bereits erfasste/ausgeschlossene nie neu melden
        seen.setdefault(x["id"], 1)
    found, errors = [], []
    try:
        found += check_flatfox(seen)
    except Exception as e:
        errors.append(f"Flatfox: {e}"); print("Flatfox-Fehler:", e, file=sys.stderr)
    for site in SITES:
        try:
            found += check_site(site, seen)
        except Exception as e:
            errors.append(f"{site['name']}: {e}"); print(f"{site['name']}-Fehler:", e, file=sys.stderr)
    seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=0, sort_keys=True))
    if a.batch_dir:
        write_batch(pathlib.Path(a.batch_dir), found, seen, errors, a.intervall)
        return
    if a.out:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        docs = [to_doc(r, now) for r in found]
        pathlib.Path(a.out).write_text(json.dumps(docs, ensure_ascii=False, indent=1))
        print(f"{len(docs)} neue(s) Angebot(e) -> {a.out}")
        return
    if a.seed:
        print(f"Seed: {len(seen)} IDs als bekannt markiert, {len(found)} würden sonst gemeldet.")
        return
    for r in found:
        try:
            notify(r, a.dry_run)
        except Exception as e:
            print("Benachrichtigung fehlgeschlagen:", e, file=sys.stderr)
    if not a.dry_run:
        if found:
            add_to_list(found)
        write_meta(found, errors, a.intervall)
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M} – {len(found)} neue(s) Angebot(e).")

if __name__ == "__main__":
    main()
