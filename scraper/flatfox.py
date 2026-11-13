"""Flatfox: alle Miet-Inserate im Umkreis abrufen und nach Hobbyraum-tauglichen Objekten filtern."""
import json, math, sys, time, urllib.request, urllib.parse

ORIGIN = (47.505, 8.720)  # Startpunkt Winterthur-Neuwiesen/Lind (auf ~100 m gerundet)
MAX_KM = 10.0
UA = {"User-Agent": "Mozilla/5.0 (hobbyraum-suche)"}

def km(lat, lon):
    la1, lo1 = map(math.radians, ORIGIN); la2, lo2 = math.radians(lat), math.radians(lon)
    a = math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 6371 * 2 * math.asin(math.sqrt(a))

def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)

def pins(cat, box):
    n, s, e, w = box
    q = dict(east=e, west=w, north=n, south=s, max_count=1000, object_category=cat, offer_type="RENT")
    res = get("https://flatfox.ch/api/v1/pin/?" + urllib.parse.urlencode(q))
    if len(res) >= 1000:  # Box zu gross -> vierteln
        mlat, mlon = (n+s)/2, (e+w)/2
        out = []
        for b in [(n,mlat,e,mlon),(n,mlat,mlon,w),(mlat,s,e,mlon),(mlat,s,mlon,w)]:
            out += pins(cat, b)
        return out
    return res

def details(pks):
    out = []
    for i in range(0, len(pks), 40):
        chunk = pks[i:i+40]
        q = "&".join(f"pk={p}" for p in chunk)
        out += get(f"https://flatfox.ch/api/v1/public-listing/?{q}&limit=100")["results"]
        time.sleep(0.3)
    return out

def main():
    box = (ORIGIN[0]+0.091, ORIGIN[0]-0.091, ORIGIN[1]+0.135, ORIGIN[1]-0.135)
    cats = ["SECONDARY", "INDUSTRY", "SHARED", "PARK"]
    allp = {}
    for c in cats:
        for p in pins(c, box):
            d = km(p["latitude"], p["longitude"])
            if d <= MAX_KM:
                allp[p["pk"]] = (c, d)
    listings = details(list(allp))
    for l in listings:
        l["_cat"], l["_km"] = allp[l["pk"]]
    json.dump(listings, sys.stdout, ensure_ascii=False)

if __name__ == "__main__":
    main()
