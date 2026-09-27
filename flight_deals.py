#!/usr/bin/env python3
"""
Flight deal scanner: TLV -> anywhere, weekend trips over the next N days.

Data source: Travelpayouts / Aviasales Data API (cached prices from real user
searches over the last ~7 days). Prices are ROUND-TRIP, PER PASSENGER, in USD.
Always confirm the live price via the link before booking.

Setup:
  1. Sign up free at https://www.travelpayouts.com and copy your API token
     (Profile -> API token).
  2. Set your token in `config.json` (created automatically on first run) or via
     the `TRAVELPAYOUTS_TOKEN` environment variable.
  3. python3 flight_deals.py
     -> writes deals_report.html and deals.csv next to this script.

Only the Python standard library is used - nothing to install.
"""

import csv
import datetime as dt
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = OUT_DIR / "config.json"

# ----------------------------------------------------------------------------
# CONFIG - default settings (can be overridden in config.json)
# ----------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "api_token": "",            # Travelpayouts API token (or set TRAVELPAYOUTS_TOKEN env var)
    "api_url": "https://api.travelpayouts.com/aviasales/v3/prices_for_dates",
    "cities_url": "https://api.travelpayouts.com/data/en/cities.json",
    "countries_url": "https://api.travelpayouts.com/data/en/countries.json",
    "origin": "TLV",            # Ben Gurion
    "horizon_days": 60,         # scan departures from today up to N days ahead
    "currency": "usd",

    # Israeli weekend = Friday + Saturday. Trip must cover the weekend,
    # with up to `flex_days` of stretch on either side:
    #   depart: Fri-2 .. Fri   (Wed, Thu, Fri)
    #   return: Sat .. Sat+2   (Sat, Sun, Mon)
    "weekend_start_weekday": 4,  # Mon=0 ... Fri=4
    "weekend_end_weekday": 5,    # Sat=5
    "flex_days": 2,
    "min_nights": 2,            # minimum trip duration in nights (e.g. 2 filters out same-day/next-day returns)
    "max_nights": 5,

    "max_stops": 1,              # per direction; None = any
    "direct_only": False,

    # Price segments (round-trip, per passenger)
    "segments": [
        ["Up to $100", 100],
        ["$100 - $200", 200],
        ["Over $200", None],     # None = no upper bound
    ],

    # Flexible stay search (any day departure, 3-7 nights stay, under $100)
    "flexible_min_nights": 3,
    "flexible_max_nights": 7,
    "flexible_max_price": 100,

    "show_per_segment": 50,      # max rows shown per segment in the report
    "best_per_destination": True,  # keep only the cheapest trip per city

    "pages_per_query": 3,        # 1000 results per page
}


def load_config():
    """Load configuration from config.json if present, falling back to defaults and environment variables."""
    cfg = DEFAULT_CONFIG.copy()

    # Create config.json template if it doesn't exist
    if not CONFIG_FILE.exists():
        try:
            CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            print(f"Created configuration file template at: {CONFIG_FILE}")
        except Exception as err:
            print(f"Warning: Could not create config template: {err}")

    # Read from config.json
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except Exception as err:
            print(f"Warning: Failed to read {CONFIG_FILE.name}: {err}")

    # Fallback API token from environment variable if not set in config file
    token = cfg.get("api_token") or os.environ.get("TRAVELPAYOUTS_TOKEN") or ""
    cfg["api_token"] = token.strip()

    return cfg


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
def http_json(url, token=None, retries=3):
    headers = {"Accept-Encoding": "identity", "User-Agent": "flight-deals/1.0"}
    if token:
        headers["X-Access-Token"] = token
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            if e.code in (401, 403):
                sys.exit("API rejected the token (HTTP %d). Check your API token in config.json or TRAVELPAYOUTS_TOKEN env var." % e.code)
            body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else ""
            print("HTTP Error %d for URL %s: %s" % (e.code, url, body))
            return {"success": False, "error": f"HTTP {e.code}: {body}"}
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            raise


def load_reference(cfg=None):
    """IATA city code -> (city name, country name). Cached for 7 days."""
    cities_url = (cfg and cfg.get("cities_url")) or DEFAULT_CONFIG["cities_url"]
    countries_url = (cfg and cfg.get("countries_url")) or DEFAULT_CONFIG["countries_url"]
    cache = OUT_DIR / ".cities_cache.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < 7 * 86400:
        return json.loads(cache.read_text(encoding="utf-8"))
    try:
        cities = http_json(cities_url)
        countries = {c["code"]: c.get("name") or c["code"] for c in http_json(countries_url)}
        ref = {c["code"]: [c.get("name") or c["code"], countries.get(c.get("country_code"), "")]
               for c in cities}
        cache.write_text(json.dumps(ref), encoding="utf-8")
        return ref
    except Exception as e:  # names are cosmetic; don't fail the run
        print("warning: could not load city names (%s)" % e)
        return {}


# ----------------------------------------------------------------------------
# Trip window logic
# ----------------------------------------------------------------------------
def allowed_windows(cfg, today):
    """Return list of (earliest_depart, latest_depart, earliest_return, latest_return)."""
    end = today + dt.timedelta(days=cfg["horizon_days"])
    f = cfg["flex_days"]
    wins = []
    d = today
    while d <= end + dt.timedelta(days=7):
        if d.weekday() == cfg["weekend_start_weekday"]:
            ws = d
            we = d + dt.timedelta(days=(cfg["weekend_end_weekday"] - cfg["weekend_start_weekday"]) % 7)
            dep_lo = max(ws - dt.timedelta(days=f), today)
            dep_hi = min(ws, end)
            if dep_lo <= dep_hi:
                wins.append((dep_lo, dep_hi, we, we + dt.timedelta(days=f)))
        d += dt.timedelta(days=1)
    return wins


def fits(cfg, wins, dep, ret):
    nights = (ret - dep).days
    min_nights = cfg.get("min_nights") if cfg.get("min_nights") is not None else cfg.get("min_duration_days", 2)
    max_nights = cfg.get("max_nights") if cfg.get("max_nights") is not None else cfg.get("max_duration_days", 5)
    if not (min_nights <= nights <= max_nights):
        return False
    return any(a <= dep <= b and c <= ret <= e for a, b, c, e in wins)


def months_between(a, b):
    out, y, m = [], a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append("%04d-%02d" % (y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


# ----------------------------------------------------------------------------
# Fetch
# ----------------------------------------------------------------------------
def fetch_offers(cfg, token, today, fetch=http_json):
    end = today + dt.timedelta(days=cfg["horizon_days"])
    dep_months = months_between(today, end)
    ret_months = months_between(today, end + dt.timedelta(days=cfg["flex_days"] + cfg.get("flexible_max_nights", cfg["max_nights"])))
    offers = []
    api_url = cfg.get("api_url", "https://api.travelpayouts.com/aviasales/v3/prices_for_dates")
    for dm in dep_months:
        for rm in ret_months:
            if rm < dm:
                continue
            # Travelpayouts API limits diff between departure_at and return_at to max 30 days
            dm_y, dm_m = map(int, dm.split("-"))
            rm_y, rm_m = map(int, rm.split("-"))
            if (rm_y * 12 + rm_m) - (dm_y * 12 + dm_m) > 1:
                continue
            for page in range(1, cfg["pages_per_query"] + 1):
                q = {
                    "origin": cfg["origin"],
                    "departure_at": dm,
                    "return_at": rm,
                    "one_way": "false",
                    "direct": "true" if cfg["direct_only"] else "false",
                    "currency": cfg["currency"],
                    "sorting": "price",
                    "limit": 1000,
                    "page": page,
                }
                url = api_url + "?" + urllib.parse.urlencode(q)
                resp = fetch(url, token)
                if not resp or not resp.get("success", True):
                    print("warning: query %s/%s failed: %s" % (dm, rm, resp and resp.get("error")))
                    break
                data = resp.get("data") or []
                offers.extend(data)
                print("  %s -> %s page %d: %d offers" % (dm, rm, page, len(data)))
                if len(data) < 1000:
                    break
                time.sleep(0.3)
    return offers


def parse_date(s):
    return dt.date.fromisoformat(s[:10])


AIRLINE_URLS = {
    "W6": "https://wizzair.com/en-gb/booking/select-flight/{origin}/{dest}/{dep}/{ret}/1/0/0",
    "W4": "https://wizzair.com/en-gb/booking/select-flight/{origin}/{dest}/{dep}/{ret}/1/0/0",
    "5W": "https://wizzair.com/en-gb/booking/select-flight/{origin}/{dest}/{dep}/{ret}/1/0/0",
    "FR": "https://www.ryanair.com/gb/en/trip/flights/select?adt=1&dateOut={dep}&dateIn={ret}&originIata={origin}&destinationIata={dest}",
    "RK": "https://www.ryanair.com/gb/en/trip/flights/select?adt=1&dateOut={dep}&dateIn={ret}&originIata={origin}&destinationIata={dest}",
    "U2": "https://www.easyjet.com/en",
    "LY": "https://www.elal.com",
    "IZ": "https://www.israirairlines.com",
    "6H": "https://www.arkia.com",
    "GQ": "https://www.skyexpress.gr/en",
    "A3": "https://en.aegeanair.com",
    "PC": "https://www.flypgs.com/en",
    "TK": "https://www.turkishairlines.com",
    "RO": "https://www.tarom.ro/en",
    "JU": "https://www.airserbia.com/en",
    "FB": "https://www.air.bg/en",
}


def get_direct_airline_link(airline, origin, dest, dep, ret):
    code = (airline or "").upper().strip()
    dep_str = dep.strftime("%Y-%m-%d")
    ret_str = ret.strftime("%Y-%m-%d")
    if code in AIRLINE_URLS:
        return AIRLINE_URLS[code].format(origin=origin, dest=dest, dep=dep_str, ret=ret_str)
    return f"https://www.google.com/travel/flights?q=Flights%20from%20{origin}%20to%20{dest}%20from%20{dep_str}%20to%20{ret_str}%20on%20{code}"


def get_google_flights_link(origin, dest, dep, ret):
    dep_str = dep.strftime("%Y-%m-%d")
    ret_str = ret.strftime("%Y-%m-%d")
    return f"https://www.google.com/travel/flights?q=Flights%20from%20{origin}%20to%20{dest}%20from%20{dep_str}%20to%20{ret_str}"


def build_deals(cfg, offers, today, ref, weekend_only=True, min_n=None, max_n=None, max_price=None):
    wins = allowed_windows(cfg, today) if weekend_only else None
    min_nights = min_n if min_n is not None else cfg.get("min_nights", 2)
    max_nights = max_n if max_n is not None else cfg.get("max_nights", 5)
    seen, deals = set(), []
    for o in offers:
        try:
            dep, ret = parse_date(o["departure_at"]), parse_date(o["return_at"])
            price = float(o["price"])
        except (KeyError, ValueError, TypeError):
            continue
        nights = (ret - dep).days
        if not (min_nights <= nights <= max_nights):
            continue
        if max_price is not None and price > max_price:
            continue
        if weekend_only:
            if not any(a <= dep <= b and c <= ret <= e for a, b, c, e in wins):
                continue
        ms = cfg["max_stops"]
        if ms is not None and (o.get("transfers", 0) > ms or o.get("return_transfers", 0) > ms):
            continue
        key = (o.get("destination"), o["departure_at"], o["return_at"], o.get("airline"), price)
        if key in seen:
            continue
        seen.add(key)
        city, country = ref.get(o.get("destination"), [o.get("destination"), ""])
        link = o.get("link") or ""
        aviasales_link = ("https://www.aviasales.com" + link) if link.startswith("/") else link
        airline_code = o.get("airline", "")
        direct_link = get_direct_airline_link(airline_code, cfg["origin"], o.get("destination", ""), dep, ret)
        google_link = get_google_flights_link(cfg["origin"], o.get("destination", ""), dep, ret)

        deals.append({
            "price": price,
            "destination": o.get("destination"),
            "airport": o.get("destination_airport") or o.get("destination"),
            "city": city,
            "country": country,
            "depart": dep,
            "return": ret,
            "nights": nights,
            "airline": airline_code,
            "stops_out": o.get("transfers", 0),
            "stops_back": o.get("return_transfers", 0),
            "link": direct_link,
            "direct_airline_link": direct_link,
            "google_flights_link": google_link,
            "aviasales_link": aviasales_link,
        })
    deals.sort(key=lambda d: d["price"])
    if cfg.get("best_per_destination", True):
        best = {}
        for d in deals:
            best.setdefault(d["destination"], d)
        deals = sorted(best.values(), key=lambda d: d["price"])
    return deals


def segment(cfg, deals):
    out = defaultdict(list)
    for d in deals:
        for name, cap in cfg["segments"]:
            if cap is None or d["price"] <= cap:
                out[name].append(d)
                break
    return out


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------
def write_csv(deals, segs, flex_deals, path):
    seg_of = {id(d): n for n, ds in segs.items() for d in ds}
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["section", "segment", "price_usd", "city", "country", "airport", "depart", "return",
                    "nights", "airline", "stops_out", "stops_back", "direct_airline_link",
                    "google_flights_link", "aviasales_link"])
        for d in deals:
            w.writerow(["Weekend Getaways", seg_of.get(id(d)), d["price"], d["city"], d["country"], d["airport"],
                        d["depart"], d["return"], d["nights"], d["airline"],
                        d["stops_out"], d["stops_back"], d.get("direct_airline_link", d["link"]),
                        d.get("google_flights_link", ""), d.get("aviasales_link", "")])
        for d in flex_deals:
            w.writerow(["Flexible (3-7 Days, Under $100)", f"Up to ${d['price']:.0f}", d["price"], d["city"], d["country"], d["airport"],
                        d["depart"], d["return"], d["nights"], d["airline"],
                        d["stops_out"], d["stops_back"], d.get("direct_airline_link", d["link"]),
                        d.get("google_flights_link", ""), d.get("aviasales_link", "")])


def write_html(cfg, segs, flex_deals, today, path, n_offers):
    e = html.escape
    fmt = lambda x: x.strftime("%a %d %b")
    stops = lambda n: "direct" if n == 0 else "%d stop%s" % (n, "" if n == 1 else "s")
    
    # Section 1: Weekend Getaways
    weekend_sections = []
    for name, _cap in cfg["segments"]:
        rows = segs.get(name, [])
        body = "".join(
            "<tr><td class=p>${:.0f}</td><td><b>{}</b><br><span class=m>{}</span></td>"
            "<td>{} &rarr; {}<br><span class=m>{} night{}</span></td>"
            "<td>{}<br><span class=m>{} / {}</span></td>"
            "<td><a href=\"{}\" target=_blank class=btn>Direct</a> "
            "<a href=\"{}\" target=_blank class=btn-sec>Google Flights</a> "
            "<a href=\"{}\" target=_blank class=btn-sec>Aviasales</a></td></tr>".format(
                d["price"], e(d["city"]), e(" • ".join(x for x in (d["country"], d["airport"]) if x)),
                fmt(d["depart"]), fmt(d["return"]), d["nights"], "" if d["nights"] == 1 else "s",
                e(d["airline"]), stops(d["stops_out"]), stops(d["stops_back"]),
                e(d.get("direct_airline_link", d["link"])),
                e(d.get("google_flights_link", "")),
                e(d.get("aviasales_link", "")))
            for d in rows[: cfg["show_per_segment"]])
        if not body:
            body = "<tr><td colspan=5 class=m>No trips found in this range.</td></tr>"
        weekend_sections.append("<h3>{} <span class=c>({})</span></h3><table><thead><tr><th>Price</th>"
                                "<th>Destination</th><th>Dates</th><th>Airline</th><th>Links</th></tr></thead>"
                                "<tbody>{}</tbody></table>".format(e(name), len(rows), body))
    
    # Section 2: Flexible Deals under $100 (3-7 nights)
    flex_min = cfg.get("flexible_min_nights", 3)
    flex_max = cfg.get("flexible_max_nights", 7)
    flex_price = cfg.get("flexible_max_price", 100)
    
    flex_body = "".join(
        "<tr><td class=p>${:.0f}</td><td><b>{}</b><br><span class=m>{}</span></td>"
        "<td>{} &rarr; {}<br><span class=m>{} night{}</span></td>"
        "<td>{}<br><span class=m>{} / {}</span></td>"
        "<td><a href=\"{}\" target=_blank class=btn>Direct</a> "
        "<a href=\"{}\" target=_blank class=btn-sec>Google Flights</a> "
        "<a href=\"{}\" target=_blank class=btn-sec>Aviasales</a></td></tr>".format(
            d["price"], e(d["city"]), e(" • ".join(x for x in (d["country"], d["airport"]) if x)),
            fmt(d["depart"]), fmt(d["return"]), d["nights"], "" if d["nights"] == 1 else "s",
            e(d["airline"]), stops(d["stops_out"]), stops(d["stops_back"]),
            e(d.get("direct_airline_link", d["link"])),
            e(d.get("google_flights_link", "")),
            e(d.get("aviasales_link", "")))
        for d in flex_deals[: cfg.get("show_per_segment", 50)])
    if not flex_body:
        flex_body = f"<tr><td colspan=5 class=m>No flexible trips found under ${flex_price}.</td></tr>"

    end = today + dt.timedelta(days=cfg["horizon_days"])
    
    page = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Flight Deals Scanner - {origin}</title><style>
:root{{--bg:#fafaf8;--fg:#1d1d1b;--m:#6b6b66;--line:#e4e4df;--acc:#0b6e4f;--sec:#3366cc;--card-bg:#ffffff}}
@media(prefers-color-scheme:dark){{:root{{--bg:#161615;--fg:#ececea;--m:#9a9a94;--line:#2c2c2a;--acc:#4fc79c;--sec:#6699ff;--card-bg:#1e1e1c}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,BlinkMacSystemFont,sans-serif;margin:0;padding:24px 16px;max-width:960px;margin:auto}}
h1{{font-size:24px;margin:0 0 6px;letter-spacing:-0.5px}}
h2{{font-size:19px;margin:32px 0 12px;padding-bottom:6px;border-bottom:2px solid var(--acc);display:flex;align-items:center;gap:8px}}
h3{{font-size:16px;margin:20px 0 8px}}.c{{color:var(--m);font-weight:400}}
.subtitle{{color:var(--m);font-size:13px;margin-bottom:24px;background:var(--card-bg);padding:12px 16px;border-radius:8px;border:1px solid var(--line)}}
.section-badge{{background:var(--acc);color:#fff;font-size:12px;padding:2px 8px;border-radius:12px;font-weight:600}}
table{{width:100%;border-collapse:collapse;margin-bottom:24px;background:var(--card-bg);border-radius:8px;overflow:hidden;border:1px solid var(--line)}}
th{{text-align:left;font-size:12px;color:var(--m);font-weight:600;border-bottom:1px solid var(--line);padding:8px 12px;background:var(--bg)}}
td{{border-bottom:1px solid var(--line);padding:10px 12px;vertical-align:top}}
tr:last-child td{{border-bottom:none}}
.p{{font-weight:700;color:var(--acc);font-size:16px;white-space:nowrap}}
.m{{color:var(--m);font-size:13px}}
a{{color:var(--acc);text-decoration:none}}a:hover{{text-decoration:underline}}
.btn{{display:inline-block;padding:4px 10px;border-radius:4px;background:var(--acc);color:#fff!important;font-size:12px;font-weight:600;transition:opacity 0.2s}}
.btn:hover{{opacity:0.9;text-decoration:none}}
.btn-sec{{display:inline-block;padding:3px 8px;border-radius:4px;background:transparent;border:1px solid var(--line);color:var(--fg)!important;font-size:12px;transition:background 0.2s}}
.btn-sec:hover{{background:var(--line);text-decoration:none}}
</style></head><body>
<h1>✈️ Flight Deals from {origin}</h1>
<div class="subtitle">
  Departures scanned: <b>{start}</b> to <b>{end}</b> (next {horizon} days) • {n} cached offers analyzed • generated {now}<br>
  <span class=m>All prices are round-trip per passenger in USD. Prices are cached from recent user searches—always confirm before booking.</span>
</div>

<h2>🗓️ Weekend Getaways <span class="section-badge">{mn_wknd}-{mx_wknd} Nights</span></h2>
<div class=m style="margin-bottom:12px">Depart Wed–Fri, return Sat–Mon, covering the weekend with flexible stretch days.</div>
{weekend_html}

<h2>🌟 Flexible Deals: 3 to 7 Days Stay <span class="section-badge">Under ${flex_price}</span></h2>
<div class=m style="margin-bottom:12px">Any departure day of the week, staying {flex_min}–{flex_max} nights, priced up to ${flex_price}.</div>
<table><thead><tr><th>Price</th><th>Destination</th><th>Dates</th><th>Airline</th><th>Links</th></tr></thead>
<tbody>{flex_html}</tbody></table>
</body></html>""".format(
        origin=e(cfg["origin"]), start=fmt(today), end=fmt(end), horizon=cfg["horizon_days"],
        mn_wknd=cfg["min_nights"], mx_wknd=cfg["max_nights"], n=n_offers,
        now=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        weekend_html="".join(weekend_sections),
        flex_min=flex_min, flex_max=flex_max, flex_price=flex_price,
        flex_html=flex_body)

    Path(path).write_text(page, encoding="utf-8")


def main(fetch=http_json, today=None):
    cfg = load_config()
    token = cfg["api_token"]
    if fetch is http_json and not token:
        sys.exit(
            f"API token not found!\n"
            f"Please set your 'api_token' in '{CONFIG_FILE.name}' or set the TRAVELPAYOUTS_TOKEN environment variable.\n"
            f"(Get a free token at https://www.travelpayouts.com -> Profile -> API token)"
        )
    today = today or dt.date.today()
    print("Scanning %s -> anywhere, next %d days..." % (cfg["origin"], cfg["horizon_days"]))
    offers = fetch_offers(cfg, token, today, fetch)
    ref = load_reference(cfg) if fetch is http_json else {}

    # 1. Weekend deals (Upper Section)
    deals = build_deals(cfg, offers, today, ref, weekend_only=True)
    segs = segment(cfg, deals)

    # 2. Flexible deals (Lower Section: 3-7 nights, under $100)
    flex_deals = build_deals(
        cfg, offers, today, ref,
        weekend_only=False,
        min_n=cfg.get("flexible_min_nights", 3),
        max_n=cfg.get("flexible_max_nights", 7),
        max_price=cfg.get("flexible_max_price", 100)
    )

    write_csv(deals, segs, flex_deals, OUT_DIR / "deals.csv")
    write_html(cfg, segs, flex_deals, today, OUT_DIR / "deals_report.html", len(offers))

    print("\n[Weekend Getaways] %d matching trips" % len(deals))
    for name, _ in cfg["segments"]:
        ds = segs.get(name, [])
        print("  == %s: %d ==" % (name, len(ds)))
        for d in ds[:5]:
            print("    $%-5.0f %-18s %s -> %s  %s (%dn)" % (d["price"], d["city"][:18], d["depart"],
                                                           d["return"], d["airline"], d["nights"]))

    print("\n[Flexible 3-7 Days (Under $100)] %d matching trips" % len(flex_deals))
    for d in flex_deals[:10]:
        print("  $%-5.0f %-18s %s -> %s  %s (%dn)" % (d["price"], d["city"][:18], d["depart"],
                                                       d["return"], d["airline"], d["nights"]))

    print("\nReport generated: %s" % (OUT_DIR / "deals_report.html"))


if __name__ == "__main__":
    main()
