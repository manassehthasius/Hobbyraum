#!/usr/bin/env python3
"""Erzeugt ANGEBOTE.md, VERWALTUNGEN.md und die E-Mail-Entwürfe aus data/*.json + profil.json."""
import json, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parent.parent
A = json.loads((ROOT / "data/angebote.json").read_text())
V = json.loads((ROOT / "data/verwaltungen.json").read_text())
_ST = json.loads((ROOT / "data/status.json").read_text()) if (ROOT / "data/status.json").exists() else {}
for _x in A["angebote"]:
    if _x["id"] in _ST.get("angebote", {}): _x["status"] = _ST["angebote"][_x["id"]]["status"]
for _x in V["verwaltungen"]:
    if _x["id"] in _ST.get("verwaltungen", {}): _x["status"] = _ST["verwaltungen"][_x["id"]]["status"]
P = json.loads(((ROOT / "profil.json") if (ROOT / "profil.json").exists() else (ROOT / "profil.example.json")).read_text())

STATUS = {"offen": "⬜ offen", "neu": "🆕 neu", "kontaktiert": "📨 kontaktiert", "antwort": "💬 Antwort erhalten",
          "besichtigung": "👀 Besichtigung", "zugesagt": "✅ zugesagt", "abgesagt": "❌ abgesagt", "vergeben": "🚫 vergeben"}
PRIO = {1: "Top-Kandidaten (Hobby/Bastel ausdrücklich, günstig, nah)",
        2: "Gute Optionen (etwas weiter, teurer oder Details offen)",
        3: "Laut Inserat «nur Lager» – Hobbynutzung anfragen",
        4: "Grenzfälle (teurer, gross oder Büro)"}

def chf(a):
    if a.get("chf_monat") is None:
        return "auf Anfrage"
    return f"CHF {a['chf_monat']:,}".replace(",", "'")

def link_md(u, label="Inserat"):
    return f"[{label}]({u})" if u else "–"

def kontakt(a):
    parts = []
    if a.get("kontakt_person"): parts.append(a["kontakt_person"])
    if a.get("telefon"): parts.append(f"☎ {a['telefon']}")
    if a.get("email"): parts.append(f"✉ {a['email']}")
    if a.get("kontaktweg") and a["kontaktweg"].startswith("http"): parts.append(f"[Kontaktformular]({a['kontaktweg']})")
    elif a.get("kontaktweg"): parts.append(a["kontaktweg"])
    return "<br>".join(parts) or "–"

def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

def signatur():
    return "\n".join(x for x in [P["name"], P["telefon"], P["email"]] if x)

def anrede(person):
    return f"Guten Tag {person}" if person else "Guten Tag"

# ---------------------------------------------------------------- ANGEBOTE.md
ang = A["angebote"]
verified = [a for a in ang if not a["verifiziert"].startswith("Nur Suchmaschinen")]
unverified = [a for a in ang if a["verifiziert"].startswith("Nur Suchmaschinen")]
out = [f"# Hobbyräume in und um Winterthur (Radius 10 km)",
       f"",
       f"Stand: **{A['stand']}** · Distanzen = Luftlinie ab Startpunkt Winterthur-Neuwiesen/Lind · Preise = Monatsmiete brutto inkl. NK, soweit bekannt.",
       f"",
       f"**{len(ang)} Angebote** ({len(verified)} live verifiziert, {len(unverified)} nur über Suchmaschine gefunden) · "
       f"Status-Legende: " + " · ".join(STATUS.values()),
       f"",
       f"E-Mail-Entwürfe: [`emails/ANGEBOTE.md`](emails/ANGEBOTE.md) · Verwaltungen ohne Inserat: [`VERWALTUNGEN.md`](VERWALTUNGEN.md)",
       ""]
def table(rows):
    o = ["| Status | Objekt | Preis/Mt. | m² | km | Verfügbar | Anbieter / Kontakt | Link |",
         "|---|---|---|---|---|---|---|---|"]
    for a in sorted(rows, key=lambda r: (r["chf_monat"] or 9999)):
        titel = f"**{a['adresse']}**<br>{a['titel']}<br><sub>{a.get('nutzung') or ''}</sub>"
        preis = chf(a) + (f"<br><sub>{a['preis_text']}</sub>" if a.get("preis_text") else "")
        anb = f"{a['anbieter'] or '–'}<br>{kontakt(a)}"
        o.append(f"| {STATUS.get(a['status'], a['status'])} | {titel} | {preis} | {a.get('m2') or '?'} | {a.get('km') or '?'} | {a.get('verfuegbar') or '?'} | {anb} | {link_md(a.get('link'))} |")
    return o
for p in (1, 2, 3, 4):
    rows = [a for a in verified if a["prio"] == p]
    if rows:
        out += [f"## {PRIO[p]}", ""] + table(rows) + [""]
out += ["## Nur über Suchmaschine gefunden (Homegate / ImmoScout24 / Comparis)", "",
        "> Diese Portale blockieren automatische Abrufe. Die Angaben stammen aus Suchmaschinen-Auszügen – "
        "**bitte Link öffnen und prüfen, ob das Inserat noch online ist**, bevor du schreibst.", ""] + table(unverified) + [""]
out += ["## Geprüft und ausgeschlossen", ""] + [f"- {x['adresse']} – {x['grund']} ({link_md(x['link'])})" for x in A["ausgeschlossen"]] + [""]
out += ["## Hinweise zu den Angeboten", ""]
for a in sorted(ang, key=lambda r: (r["prio"], r["chf_monat"] or 9999)):
    if a.get("hinweise") or a.get("weitere_links"):
        extra = " · ".join(link_md(u, f"Link {i+2}") for i, u in enumerate(a.get("weitere_links") or []))
        out.append(f"- **{a['adresse']}** – {a.get('hinweise') or ''} {extra}".rstrip())
(ROOT / "ANGEBOTE.md").write_text("\n".join(out) + "\n")

# ---------------------------------------------------------------- VERWALTUNGEN.md
o = [f"# Verwaltungen, Genossenschaften & Anbieter ohne aktuelles Inserat", "",
     f"Stand: **{V['stand']}** · {V['hinweis']}", "",
     "E-Mail-Entwürfe: [`emails/VERWALTUNGEN.md`](emails/VERWALTUNGEN.md)", "",
     "| Status | Name | Art | Adresse | Telefon | E-Mail | Quelle | Tipp |", "|---|---|---|---|---|---|---|---|"]
for v in V["verwaltungen"]:
    name = f"[{v['name']}]({v['web']})" if v.get("web") else v["name"]
    o.append(f"| {STATUS.get(v['status'], v['status'])} | {name} | {v['art']} | {v['adresse']} | {v.get('telefon') or '–'} | {v.get('email') or '– (keine gefunden)'} | {v['quelle']} | {v.get('tipp') or ''} |")
o += ["", "## Alternativen / Überbrückung", ""] + [f"- **{x['name']}**, {x['adresse']} – {x['hinweis']} ({x['email']}, {x['web']})" for x in V["alternativen"]]
(ROOT / "VERWALTUNGEN.md").write_text("\n".join(o) + "\n")

# ---------------------------------------------------------------- E-Mails Angebote
def mail_angebot(a):
    quelle = ""
    if a["id"].startswith("flatfox"): quelle = " auf Flatfox"
    elif a["id"].startswith("homegate"): quelle = " auf Homegate"
    elif a["id"].startswith("comparis"): quelle = " auf Comparis"
    elif a["id"].startswith("web:"): quelle = " auf Ihrer Website"
    betreff = f"Anfrage Hobby-/Bastelraum – {a['adresse'].split(',')[0]}"
    body = f"""{anrede(a.get('kontakt_person'))}

{(quelle.strip().replace("auf ", "Auf ", 1) + " habe ich") if quelle else "Ich habe"} Ihr Angebot «{a['titel']}» ({a['adresse']}) gesehen.

{a['frage']}

Kurz zu mir: Ich wohne in Winterthur ganz in der Nähe und suche einen kleinen Raum für {P['hobby']}. {P['nutzung_detail']} Ich bin zuverlässig und zahle pünktlich; einen aktuellen Betreibungsregisterauszug lege ich gerne bei.

Für eine Besichtigung bin ich zeitlich flexibel. Mietbeginn wäre bei mir {P['start']}.

Vielen Dank für Ihre Rückmeldung und freundliche Grüsse

{signatur()}"""
    an = a.get("email")
    kanal = (f"**An:** {an}" if an else "**Kanal:** " + (f"[Kontaktformular]({a['kontaktweg']})" if (a.get('kontaktweg') or '').startswith('http') else (a.get('kontaktweg') or 'über das Inserat')))
    if a.get("email") and (a.get("kontaktweg") or "").startswith("http"):
        kanal += f" · alternativ [Kontaktformular]({a['kontaktweg']})"
    if a.get("telefon"): kanal += f" · ☎ {a['telefon']}"
    return betreff, kanal, body

m = ["# E-Mail-Entwürfe zu konkreten Angeboten", "",
     "Platzhalter in `[…]` werden automatisch ersetzt, sobald `profil.json` (Kopie von `profil.example.json`, wird **nicht** committet) ausgefüllt ist und `python3 scripts/build.py` läuft.",
     "Bei Flatfox-Inseraten den Text ins **Kontaktformular** kopieren (die Verwaltungen geben dort meist keine E-Mail an).", ""]
for a in sorted(ang, key=lambda r: (r["prio"], r["chf_monat"] or 9999)):
    if a["status"] in ("abgesagt", "vergeben", "zugesagt"):
        continue
    b, k, body = mail_angebot(a)
    m += [f"## {STATUS.get(a['status'], a['status'])} · {a['adresse']} · {chf(a)}", "", k, "", f"**Betreff:** {b}", "", "```text", body, "```", ""]
(ROOT / "emails/ANGEBOTE.md").write_text("\n".join(m) + "\n")

# ---------------------------------------------------------------- E-Mails Verwaltungen
def mail_verw(v):
    genos = v["art"] in ("Genossenschaft",)
    betreff = "Anfrage: kleiner Bastel-/Hobbyraum in Winterthur gesucht"
    body = f"""{anrede(v.get('kontakt_person'))}

Ich suche in Winterthur – idealerweise im Raum Bahnhof / Lind / Veltheim, maximal einige Kilometer entfernt – einen kleinen Bastel- oder Hobbyraum (ca. 10–30 m², mit Stromanschluss) für {P['hobby']}. {P['nutzung_detail']}

{v.get('mail_hinweis') or 'Ich weiss, dass solche Räume oft gar nicht öffentlich ausgeschrieben werden, deshalb frage ich direkt bei Ihnen nach.'}

Haben Sie in einer Ihrer Liegenschaften aktuell einen solchen Raum frei, oder wird demnächst einer frei? Ein Keller- oder Lagerraum, ein kleines Atelier oder ein kleiner Gewerberaum kommen ebenfalls in Frage, sofern eine ruhige Hobbynutzung erlaubt ist. Mein Budget beträgt {P['budget']}. Gerne können Sie mich auch auf Ihre Interessentenliste setzen.

Ich bin zuverlässig und zahle pünktlich; einen aktuellen Betreibungsregisterauszug lege ich gerne bei.

Vielen Dank und freundliche Grüsse

{signatur()}"""
    if v.get("email"):
        kanal = f"**An:** {v['email']}" + (f" · ☎ {v['telefon']}" if v.get("telefon") else "")
    else:
        kanal = f"**Kein E-Mail gefunden** → {v.get('tipp') or 'anrufen'} · ☎ {v.get('telefon') or '–'}" + (f" · {v['web']}" if v.get("web") else "")
    return betreff, kanal, body

m = ["# E-Mail-Entwürfe an Verwaltungen & Genossenschaften (ohne konkretes Inserat)", "",
     "Platzhalter in `[…]` werden automatisch ersetzt, sobald `profil.json` (Kopie von `profil.example.json`, wird **nicht** committet) ausgefüllt ist und `python3 scripts/build.py` läuft.", ""]
for v in V["verwaltungen"]:
    if v["status"] in ("abgesagt", "zugesagt"):
        continue
    b, k, body = mail_verw(v)
    tipp = [f"_Tipp: {v['tipp']}_", ""] if v.get("tipp") and v.get("email") else []
    m += [f"## {STATUS.get(v['status'], v['status'])} · {v['name']}", "", k, ""] + tipp + [f"**Betreff:** {b}", "", "```text", body, "```", ""]
(ROOT / "emails/VERWALTUNGEN.md").write_text("\n".join(m) + "\n")
print("ok:", len(ang), "Angebote,", len(V["verwaltungen"]), "Verwaltungen")
