"""Normalisiert Flatfox-Inserate und filtert Hobbyraum-taugliche, günstige Objekte."""
import re

HOBBY_TYPES = {"HOBBY_ROOM", "CELLAR_COMPARTMENT", "STORAGE_ROOM", "ATELIER", "WORKSHOP"}
SMALL_COMMERCIAL = {"COMMERCIAL", "OFFICE", "PRACTICE"}
EXCLUDE = re.compile(r"regus|coworking|schreibtisch|nail|coiffeur|untermiete jeweils|LKW", re.I)
KW = re.compile(r"hobby|bastel|atelier|werkstatt|werkraum|lager|keller|proberaum|bandraum|musik|kreativ", re.I)

MAX_CHF = 750      # Monatsmiete brutto (inkl. NK, soweit bekannt)
MAX_M2 = 80
MAX_KM = 10.0

def monthly(x):
    area = x.get("surface_usable") or x.get("space_display") or x.get("surface_living")
    p = x.get("rent_gross") or x.get("price_display")
    if p is None:
        return None, area
    if x.get("price_unit") == "yearlym2":
        if not area:
            return None, area
        p = p * area / 12
        if x.get("rent_charges"):
            p += x["rent_charges"] * area / 12
    return round(p), area

def text(x):
    return " ".join(str(x.get(k) or "") for k in ("short_title", "description_title", "description"))

def is_candidate(x):
    if x.get("offer_type") != "RENT" or x.get("_km", 99) > MAX_KM:
        return False
    t = x["object_type"]
    txt = text(x)
    if EXCLUDE.search(txt):
        return False
    chf, area = monthly(x)
    if t in HOBBY_TYPES or (t in SMALL_COMMERCIAL and KW.search(txt)) or (t == "GARAGE_SLOT" and re.search(r"hobby|bastel|werkstatt", txt, re.I)):
        pass
    elif t in SMALL_COMMERCIAL and area and area <= 40:
        pass
    else:
        return False
    if chf is not None and chf > MAX_CHF:
        return False
    if area and area > MAX_M2:
        return False
    return True

def normalize(x):
    chf, area = monthly(x)
    ag = x.get("agency") or {}
    return {
        "id": f"flatfox:{x['pk']}",
        "quelle": "Flatfox" + (f" (auch Homegate/IS24 #{x['smg_id']})" if x.get("smg_id") else ""),
        "typ": x["object_type"],
        "titel": (x.get("description_title") or x.get("short_title") or "").strip(),
        "adresse": x.get("public_address"),
        "km": round(x["_km"], 1),
        "chf_monat": chf,
        "netto": x.get("rent_net"), "nk": x.get("rent_charges"),
        "preis_einheit": x.get("price_unit"),
        "m2": area,
        "verfuegbar": {"imm": "sofort", "agr": "nach Vereinbarung", "dat": x.get("moving_date")}.get(x.get("moving_date_type"), x.get("moving_date")),
        "anbieter": ag.get("name") or "Privat",
        "anbieter_adresse": " ".join(filter(None, [ag.get("street"), ag.get("zipcode"), ag.get("city")])),
        "link": f"https://flatfox.ch/de/{x['pk']}/",
        "kontaktformular": "https://flatfox.ch" + x["submit_url"].replace("/en/", "/de/"),
        "website": x.get("website_url"),
        "beschreibung": (x.get("description") or "").strip(),
        "publiziert": (x.get("published") or "")[:10],
        "reserviert": x.get("reserved"),
    }
