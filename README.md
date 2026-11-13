# Hobbyraum-Suche Winterthur

Ziel: so schnell wie möglich einen **günstigen Hobby-/Bastelraum** im Umkreis von 1–5 km (max. 10 km) um Winterthur-Neuwiesen/Lind finden, bei neuen Angeboten **sofort** benachrichtigt werden und alle Anfragen versandfertig haben.

> **Datenschutz:** Dieses Repo ist **öffentlich**. Deshalb stehen hier weder die exakte Adresse noch Name, Telefon oder E-Mail.
> Persönliche Angaben gehören nur in `profil.json`, die per `.gitignore` nie committet wird.

## Webseite «Hobbyraum-Radar»

Private Seite auf claude.ai: https://claude.ai/artifact/XapS53q2M7veruPetJk3iL
Dort sind alle Angebote und Verwaltungen zu sehen. Neue Treffer stehen oben und sind gelb markiert. Den Status setzt du per Auswahlfeld, und auf Wunsch gibt es zu jedem Eintrag einen E-Mail-Entwurf.
Deine Angaben für die Entwürfe (Tab «E-Mail-Angaben») sind nur für dich sichtbar.
Quelltext: `web/hobbyraum-radar.html`. Daten: Datenbank der Seite (`angebote`, `verwaltungen`, `meta/status`, `meta/seen`).
**Prüflauf:** ein geplanter Claude-Task («Hobbyraum-Radar Prüflauf», täglich 7:37, 12:37 und 17:37 Uhr) liest die Skripte unter `monitor/*.py` der Seite, startet `monitor.py --seen-file … --batch-dir …` und schreibt neue Treffer mit Status «neu» in die Datenbank. Benachrichtigungen sind aus; aktivierbar per Push oder E-Mail in den Einstellungen des geplanten Tasks.

## Inhalt

| Datei | Zweck |
|---|---|
| [`ANGEBOTE.md`](ANGEBOTE.md) | Übersicht aller passenden Räume: Preis, Grösse, Distanz, Verfügbarkeit, Anbieter, Kontakt, Link, Status |
| [`VERWALTUNGEN.md`](VERWALTUNGEN.md) | 38 Verwaltungen, Genossenschaften und Spezialanbieter in Winterthur mit Kontaktdaten und Quelle, für Anfragen ohne Inserat |
| [`emails/ANGEBOTE.md`](emails/ANGEBOTE.md) | individuelle E-Mail-Entwürfe zu jedem Angebot |
| [`emails/VERWALTUNGEN.md`](emails/VERWALTUNGEN.md) | individuelle E-Mail-Entwürfe an jede Verwaltung/Genossenschaft |
| `data/angebote.json`, `data/verwaltungen.json` | Datenbasis (hier den **Status** pflegen) |
| `data/seen.json` | Duplikat-Schutz für den Monitor: bereits gesehene Inserate |
| `scraper/` | Flatfox-Abfrage, Filter und Monitor für neue Angebote |
| `scripts/build.py` | erzeugt die Markdown-Listen und E-Mails aus den JSON-Daten |
| `monitoring/github-actions-monitor.yml` | **Vorlage**, noch nicht aktiv (siehe unten) |

## Status pflegen (kontaktiert, Antwort, …)

In `data/angebote.json` bzw. `data/verwaltungen.json` beim Eintrag `"status"` setzen auf
`offen` · `kontaktiert` · `antwort` · `besichtigung` · `zugesagt` · `abgesagt` · `vergeben`,
dann `python3 scripts/build.py` ausführen. Abgesagte oder vergebene Einträge verschwinden aus den E-Mail-Listen.

## E-Mails personalisieren

```bash
cp profil.example.json profil.json   # Name, Telefon, E-Mail, Hobby eintragen (bleibt lokal)
python3 scripts/build.py
```

## Neue Angebote überwachen

`python3 scraper/monitor.py` prüft:

- **Flatfox-API**: alle Kategorien Nebenraum, Gewerbe und Parkieren im 10-km-Radius, gefiltert auf Hobby/Bastel/Lager/Atelier/Werkstatt sowie kleine, günstige Gewerberäume. Flatfox spiegelt auch einen Teil der Homegate- und ImmoScout24-Inserate.
- **Websites ohne Portal-Inserat**: Gesewo, Terresta, L+B, HGW, Gewerbehaus Passant

Jedes neue Angebot wird **einzeln und sofort** gemeldet, Tageszusammenfassungen gibt es keine. Mögliche Kanäle sind ntfy-Push, Telegram, E-Mail oder ein GitHub-Issue.
Duplikate werden über die Inserat-ID **und** über Adresse + Fläche + Preis erkannt, damit ein neu aufgeschaltetes Inserat keinen zweiten Alarm auslöst.

**Grenzen:** Homegate, ImmoScout24, Comparis, Newhome und Tutti blockieren automatisierte Abrufe per Bot-Schutz. Dieser wird nicht umgangen.
Für diese Portale sind die **eigenen Suchabos der Portale** der zuverlässige und erlaubte Weg (siehe Vorschlag im Chat).
