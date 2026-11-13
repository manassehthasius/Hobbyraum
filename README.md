# Hobbyraum-Suche Winterthur

Ziel: so schnell wie möglich einen **günstigen Hobby-/Bastelraum** im Umkreis von 1–5 km (max. 10 km) um Winterthur-Neuwiesen/Lind finden, neue Angebote sofort sehen und alle Anfragen versandfertig haben.

## Webseite: https://manassehthasius.github.io/Hobbyraum/

- Alle Angebote und Verwaltungen. **Neue Treffer stehen oben und sind gelb markiert.**
- Den Status (offen, kontaktiert, Antwort, Besichtigung, zugesagt, abgesagt, vergeben) setzt du direkt auf der Seite. Er wird im Browser gespeichert, mit GitHub-Schlüssel (Tab «Einstellungen») auch in `data/status.json` und damit auf allen Geräten.
- Optional zu jedem Eintrag ein **E-Mail-Entwurf**, der sich im Mailprogramm öffnen oder kopieren lässt. Name, Telefon und Hobby trägst du unter «Einstellungen» ein; diese Angaben bleiben im Browser und kommen nie ins Repo.

> **Öffentlich:** GitHub Pages ist für jeden mit dem Link erreichbar. Deshalb gibt es im Repo keine exakte Adresse (auf ca. 100 m gerundet), keinen Namen und keine Telefonnummer.

## Automatische Prüfung

`.github/workflows/monitor.yml` läuft **alle 30 Minuten** (GitHub Actions, gratis bei öffentlichem Repo; GitHub startet geplante Läufe manchmal einige Minuten später):

1. `scraper/monitor.py` fragt die **Flatfox-API** ab (Nebenräume, Gewerbe, Parkieren im 10-km-Radius; Flatfox spiegelt auch einen Teil der Homegate/ImmoScout24-Inserate) sowie die Websites von **Gesewo, Terresta, L+B, HGW und dem Gewerbehaus Passant**.
2. Neue passende Räume landen mit Status «neu» in `data/angebote.json`. Bereits gesehene Inserate stehen in `data/seen.json` und werden nie doppelt gemeldet; dasselbe Objekt, neu inseriert, erkennt der Monitor an Adresse, Fläche und Preis.
3. `data/meta.json` enthält den Zeitpunkt der letzten Prüfung (oben rechts auf der Seite).
4. `scripts/build.py` aktualisiert die Markdown-Listen.

Homegate, ImmoScout24, Comparis, Newhome und Tutti sperren automatische Abfragen. Für diese Portale sind die Suchabos der Portale selbst der Weg.

Optional sind Push-Meldungen über Repo-Secrets möglich: `NTFY_TOPIC` (App «ntfy») oder `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.

## Dateien

| Datei | Zweck |
|---|---|
| `index.html`, `.nojekyll` | die Webseite (GitHub Pages, Branch-Root) |
| `data/angebote.json`, `data/verwaltungen.json` | Angebote und Verwaltungen (Kontaktdaten mit Quellenangabe) |
| `data/status.json` | dein Status pro Eintrag (von der Webseite geschrieben) |
| `data/meta.json`, `data/seen.json` | Prüfstatus und Duplikat-Schutz |
| [`ANGEBOTE.md`](ANGEBOTE.md), [`VERWALTUNGEN.md`](VERWALTUNGEN.md), [`emails/`](emails/) | dieselben Daten und E-Mail-Entwürfe als Text |
| `scraper/`, `scripts/build.py` | Prüfung und Listen-Generator |
| `web/hobbyraum-radar.html` | frühere Claude-Version der Seite (nicht mehr aktiv) |
