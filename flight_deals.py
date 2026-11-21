#!/usr/bin/env python3
"""
Flight deal scanner: TLV -> anywhere, weekend & flexible trips over the next N days.

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
HISTORY_FILE = OUT_DIR / ".price_history.json"

# ----------------------------------------------------------------------------
# CONFIG - default settings (can be overridden in config.json)
# ----------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "api_token": "",            # Travelpayouts API token (or set TRAVELPAYOUTS_TOKEN env var)
    "api_url": "https://api.travelpayouts.com/aviasales/v3/prices_for_dates",
    "cities_url": "https://api.travelpayouts.com/data/en/cities.json",
    "countries_url": "https://api.travelpayouts.com/data/en/countries.json",
    "airlines_url": "https://api.travelpayouts.com/data/en/airlines.json",
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

    # Destination filtering & watchlist (IATA codes or city/country names, case-insensitive)
    "exclude_destinations": [],
    "exclude_countries": [],
    "favorite_destinations": [],

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
    "track_price_history": True,   # badge NEW deals and price drops vs previous run

    "pages_per_query": 3,        # 1000 results per page
}

# Built-in fallback names for common carriers (overridden/extended by Travelpayouts airlines.json)
DEFAULT_AIRLINE_NAMES = {
    "W6": "Wizz Air",
    "W4": "Wizz Air Malta",
    "5W": "Wizz Air Abu Dhabi",
    "FR": "Ryanair",
    "RK": "Ryanair UK",
    "U2": "easyJet",
    "LY": "El Al",
    "IZ": "Arkia",
    "6H": "Israir",
    "GQ": "SKY express",
    "A3": "Aegean Airlines",
    "PC": "Pegasus Airlines",
    "TK": "Turkish Airlines",
    "RO": "TAROM",
    "JU": "Air Serbia",
    "FB": "Bulgaria Air",
    "LH": "Lufthansa",
    "OS": "Austrian Airlines",
    "LX": "SWISS",
    "AF": "Air France",
    "KL": "KLM",
    "BA": "British Airways",
    "IB": "Iberia",
    "LO": "LOT Polish Airlines",
    "BT": "airBaltic",
    "AZ": "ITA Airways",
    "CY": "Cyprus Airways",
    "QS": "Smartwings",
    "HV": "Transavia",
    "TO": "Transavia France",
    "VY": "Vueling",
    "EW": "Eurowings",
    "FZ": "flydubai",
    "EY": "Etihad Airways",
    "EK": "Emirates",
}

# Verified pre-filled booking deep links
AIRLINE_DEEP_LINKS = {
    "W6": "https://wizzair.com/en-gb/booking/select-flight/{origin}/{dest}/{dep}/{ret}/1/0/0",
    "W4": "https://wizzair.com/en-gb/booking/select-flight/{origin}/{dest}/{dep}/{ret}/1/0/0",
    "5W": "https://wizzair.com/en-gb/booking/select-flight/{origin}/{dest}/{dep}/{ret}/1/0/0",
    "FR": "https://www.ryanair.com/gb/en/trip/flights/select?adt=1&dateOut={dep}&dateIn={ret}&originIata={origin}&destinationIata={dest}",
    "RK": "https://www.ryanair.com/gb/en/trip/flights/select?adt=1&dateOut={dep}&dateIn={ret}&originIata={origin}&destinationIata={dest}",
}

# Verified airline homepages for carriers without stable GET deep-link URLs
AIRLINE_HOMEPAGES = {
    "U2": "https://www.easyjet.com/en",
    "LY": "https://www.elal.com",
    "IZ": "https://www.arkia.com",
    "6H": "https://www.israirairlines.com",
    "GQ": "https://www.skyexpress.gr/en",
    "A3": "https://en.aegeanair.com",
    "PC": "https://www.flypgs.com/en",
    "TK": "https://www.turkishairlines.com",
    "RO": "https://www.tarom.ro/en",
    "JU": "https://www.airserbia.com/en",
    "FB": "https://www.air.bg/en",
    "LH": "https://www.lufthansa.com",
    "OS": "https://www.austrian.com",
    "LX": "https://www.swiss.com",
    "AF": "https://www.airfrance.com",
    "KL": "https://www.klm.com",
    "BA": "https://www.britishairways.com",
    "IB": "https://www.iberia.com",
    "LO": "https://www.lot.com",
    "BT": "https://www.airbaltic.com",
    "AZ": "https://www.ita-airways.com",
    "CY": "https://www.cyprusairways.com",
    "QS": "https://www.smartwings.com/en/",
    "HV": "https://www.transavia.com",
    "TO": "https://www.transavia.com",
    "VY": "https://www.vueling.com",
    "EW": "https://www.eurowings.com",
    "FZ": "https://www.flydubai.com",
    "EY": "https://www.etihad.com",
    "EK": "https://www.emirates.com",
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
# HTTP & Reference Caches
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
                sys.exit(
                    "API rejected the token (HTTP %d). Check your API token in config.json or TRAVELPAYOUTS_TOKEN env var."
                    % e.code
                )
            body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else ""
            print("HTTP Error %d for URL %s: %s" % (e.code, url, body))
            return {"success": False, "error": f"HTTP {e.code}: {body}"}
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            raise


def load_reference(cfg=None):
    """IATA city code -> [city name, country name, country code]. Cached for 7 days."""
    cities_url = (cfg and cfg.get("cities_url")) or DEFAULT_CONFIG["cities_url"]
    countries_url = (cfg and cfg.get("countries_url")) or DEFAULT_CONFIG["countries_url"]
    cache = OUT_DIR / ".cities_cache.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < 7 * 86400:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        cities = http_json(cities_url)
        countries = {c["code"]: c.get("name") or c["code"] for c in http_json(countries_url)}
        ref = {
            c["code"]: [
                c.get("name") or c["code"],
                countries.get(c.get("country_code"), ""),
                c.get("country_code") or "",
            ]
            for c in cities
            if c.get("code")
        }
        cache.write_text(json.dumps(ref), encoding="utf-8")
        return ref
    except Exception as e:  # names are cosmetic; don't fail the run
        print("warning: could not load city names (%s)" % e)
        return {}


def load_airlines(cfg=None):
    """IATA airline code -> readable airline name. Cached for 7 days."""
    airlines_url = (cfg and cfg.get("airlines_url")) or DEFAULT_CONFIG["airlines_url"]
    cache = OUT_DIR / ".airlines_cache.json"
    airlines = dict(DEFAULT_AIRLINE_NAMES)
    if cache.exists() and time.time() - cache.stat().st_mtime < 7 * 86400:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(cached, dict):
                airlines.update(cached)
                return airlines
        except Exception:
            pass
    try:
        rows = http_json(airlines_url)
        fetched = {}
        for item in rows:
            code = (item.get("code") or "").upper().strip()
            if not code:
                continue
            translations = item.get("name_translations") or {}
            name = translations.get("en") or item.get("name") or ""
            if name:
                fetched[code] = name
        if fetched:
            cache.write_text(json.dumps(fetched), encoding="utf-8")
            airlines.update(fetched)
    except Exception as e:  # names are cosmetic; fall back to defaults
        print("warning: could not load airline names (%s)" % e)
    return airlines


# ----------------------------------------------------------------------------
# Price History Tracking
# ----------------------------------------------------------------------------
def load_price_history(path=HISTORY_FILE):
    """Load previous run price snapshot if present."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print("warning: could not read price history (%s)" % e)
        return {}


def build_history_snapshot(deals):
    """Build {destination: {price, depart, return}} using cheapest deal per destination."""
    snap = {}
    for d in deals:
        dest = d.get("destination")
        if not dest:
            continue
        existing = snap.get(dest)
        if existing is None or d["price"] < existing["price"]:
            snap[dest] = {
                "price": d["price"],
                "depart": d["depart"].isoformat(),
                "return": d["return"].isoformat(),
            }
    return snap


def save_price_history(weekend_deals, flex_deals, path=HISTORY_FILE):
    """Persist current run's best prices for next-run NEW / price-drop comparison."""
    payload = {
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "weekend": build_history_snapshot(weekend_deals),
        "flexible": build_history_snapshot(flex_deals),
    }
    try:
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as e:
        print("warning: could not save price history (%s)" % e)


def annotate_price_changes(deals, prev_section):
    """Compare deals against previous run snapshot and attach trend / prev_price / price_diff."""
    has_prev = isinstance(prev_section, dict) and len(prev_section) > 0
    for d in deals:
        if not has_prev:
            d["trend"] = ""
            d["prev_price"] = None
            d["price_diff"] = 0.0
            continue
        prev = prev_section.get(d["destination"])
        if not prev or "price" not in prev:
            d["trend"] = "new"
            d["prev_price"] = None
            d["price_diff"] = 0.0
            continue
        prev_price = float(prev["price"])
        diff = round(prev_price - d["price"], 2)
        d["prev_price"] = prev_price
        if diff >= 1.0:
            d["trend"] = "drop"
            d["price_diff"] = diff
        elif diff <= -1.0:
            d["trend"] = "up"
            d["price_diff"] = abs(diff)
        else:
            d["trend"] = "same"
            d["price_diff"] = 0.0


# ----------------------------------------------------------------------------
# Trip Window Logic
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
    max_stay = max(cfg.get("max_nights", 5), cfg.get("flexible_max_nights", 7))
    ret_months = months_between(today, end + dt.timedelta(days=cfg["flex_days"] + max_stay))
    offers = []
    api_url = cfg.get("api_url", DEFAULT_CONFIG["api_url"])
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


def parse_int_or_none(val):
    try:
        n = int(val)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def format_duration(minutes):
    if not minutes:
        return ""
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def format_found_at(found_at, now=None):
    """Format an ISO timestamp into a compact freshness label (e.g. 'cached 3h ago')."""
    if not found_at:
        return ""
    try:
        ts = dt.datetime.fromisoformat(found_at.replace("Z", "+00:00"))
        ref_now = now or dt.datetime.now(ts.tzinfo)
        delta_sec = (ref_now - ts).total_seconds()
        if delta_sec < 0:
            return f"cached {ts.strftime('%d %b')}"
        hours = int(delta_sec // 3600)
        if hours < 1:
            mins = max(1, int(delta_sec // 60))
            return f"cached {mins}m ago"
        if hours < 48:
            return f"cached {hours}h ago"
        days = hours // 24
        return f"cached {days}d ago"
    except Exception:
        return ""


def get_google_flights_link(origin, dest, dep, ret, airline_name=""):
    dep_str = dep.strftime("%Y-%m-%d")
    ret_str = ret.strftime("%Y-%m-%d")
    q = f"Flights from {origin} to {dest} from {dep_str} to {ret_str}"
    if airline_name:
        q += f" on {airline_name}"
    return "https://www.google.com/travel/flights?q=" + urllib.parse.quote(q)


def get_skyscanner_link(origin, dest, dep, ret):
    return (
        f"https://www.skyscanner.com/transport/flights/"
        f"{origin.lower()}/{dest.lower()}/{dep.strftime('%y%m%d')}/{ret.strftime('%y%m%d')}/"
    )


def get_booking_links(airline_code, airline_name, origin, dest, airport, dep, ret, raw_aviasales_link):
    code = (airline_code or "").upper().strip()
    dep_str = dep.strftime("%Y-%m-%d")
    ret_str = ret.strftime("%Y-%m-%d")
    target_airport = (airport or dest or "").upper().strip()

    has_direct_deeplink = code in AIRLINE_DEEP_LINKS
    airline_homepage = AIRLINE_HOMEPAGES.get(code, "")
    google_link = get_google_flights_link(origin, target_airport, dep, ret)
    skyscanner_link = get_skyscanner_link(origin, target_airport, dep, ret)
    aviasales_link = (
        ("https://www.aviasales.com" + raw_aviasales_link)
        if raw_aviasales_link.startswith("/")
        else raw_aviasales_link
    )

    if has_direct_deeplink:
        direct_link = AIRLINE_DEEP_LINKS[code].format(
            origin=origin, dest=target_airport, dep=dep_str, ret=ret_str
        )
    elif airline_homepage:
        direct_link = airline_homepage
    else:
        direct_link = get_google_flights_link(origin, target_airport, dep, ret, airline_name or code)

    return {
        "has_direct_deeplink": has_direct_deeplink,
        "direct_airline_link": direct_link,
        "airline_homepage": airline_homepage,
        "google_flights_link": google_link,
        "skyscanner_link": skyscanner_link,
        "aviasales_link": aviasales_link,
    }


def _norm_set(values):
    return {str(v).strip().lower() for v in (values or []) if str(v).strip()}


def build_deals(cfg, offers, today, ref, airlines=None, weekend_only=True, min_n=None, max_n=None, max_price=None):
    end = today + dt.timedelta(days=cfg["horizon_days"])
    wins = allowed_windows(cfg, today) if weekend_only else None
    min_nights = min_n if min_n is not None else cfg.get("min_nights", 2)
    max_nights = max_n if max_n is not None else cfg.get("max_nights", 5)

    excl_dests = _norm_set(cfg.get("exclude_destinations"))
    excl_countries = _norm_set(cfg.get("exclude_countries"))
    fav_dests = _norm_set(cfg.get("favorite_destinations"))
    airline_map = airlines if airlines is not None else DEFAULT_AIRLINE_NAMES

    seen, deals = set(), []
    for o in offers:
        try:
            dep, ret = parse_date(o["departure_at"]), parse_date(o["return_at"])
            price = float(o["price"])
        except (KeyError, ValueError, TypeError):
            continue

        # Enforce configured departure horizon for both weekend and flexible deals
        if not (today <= dep <= end):
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
        stops_out = o.get("transfers", 0) or 0
        stops_back = o.get("return_transfers", 0) or 0
        if ms is not None and (stops_out > ms or stops_back > ms):
            continue

        dest_code = (o.get("destination") or "").upper().strip()
        airport_code = (o.get("destination_airport") or dest_code).upper().strip()
        ref_entry = ref.get(dest_code) or [dest_code, "", ""]
        city = ref_entry[0] if len(ref_entry) > 0 else dest_code
        country = ref_entry[1] if len(ref_entry) > 1 else ""
        country_code = ref_entry[2] if len(ref_entry) > 2 else ""

        dest_tokens = {t.lower() for t in (dest_code, airport_code, city) if t}
        country_tokens = {t.lower() for t in (country, country_code) if t}
        if excl_dests and (dest_tokens & excl_dests):
            continue
        if excl_countries and (country_tokens & excl_countries):
            continue

        airline_code = (o.get("airline") or "").upper().strip()
        key = (dest_code, o["departure_at"], o["return_at"], airline_code, price)
        if key in seen:
            continue
        seen.add(key)

        airline_name = airline_map.get(airline_code, "")
        links = get_booking_links(
            airline_code=airline_code,
            airline_name=airline_name,
            origin=cfg["origin"],
            dest=dest_code,
            airport=airport_code,
            dep=dep,
            ret=ret,
            raw_aviasales_link=o.get("link") or "",
        )

        deals.append({
            "price": price,
            "destination": dest_code,
            "airport": airport_code,
            "city": city,
            "country": country,
            "country_code": country_code,
            "depart": dep,
            "return": ret,
            "nights": nights,
            "airline": airline_code,
            "airline_name": airline_name,
            "stops_out": stops_out,
            "stops_back": stops_back,
            "duration_to": parse_int_or_none(o.get("duration_to")),
            "duration_back": parse_int_or_none(o.get("duration_back")),
            "found_at": o.get("found_at") or "",
            "is_favorite": bool(fav_dests and (dest_tokens & fav_dests)),
            "trend": "",
            "prev_price": None,
            "price_diff": 0.0,
            "link": links["direct_airline_link"],
            **links,
        })

    deals.sort(key=lambda d: (d["price"], d["depart"]))
    if cfg.get("best_per_destination", True):
        best = {}
        for d in deals:
            best.setdefault(d["destination"], d)
        deals = sorted(best.values(), key=lambda d: (d["price"], d["depart"]))
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
def write_csv(cfg, deals, segs, flex_deals, path):
    seg_of = {id(d): n for n, ds in segs.items() for d in ds}
    flex_min = cfg.get("flexible_min_nights", 3)
    flex_max = cfg.get("flexible_max_nights", 7)
    flex_price = cfg.get("flexible_max_price", 100)
    flex_section_label = f"Flexible ({flex_min}-{flex_max} Nights, Under ${flex_price})"
    flex_segment_label = f"Up to ${flex_price}"

    def row_for(section_name, seg_name, d):
        return [
            section_name,
            seg_name,
            d["price"],
            d["city"],
            d["country"],
            d["airport"],
            d["depart"],
            d["return"],
            d["nights"],
            d["airline"],
            d.get("airline_name", ""),
            d["stops_out"],
            d["stops_back"],
            d.get("duration_to") or "",
            d.get("duration_back") or "",
            d.get("found_at", ""),
            "yes" if d.get("is_favorite") else "",
            d.get("trend", ""),
            d.get("prev_price") if d.get("prev_price") is not None else "",
            d.get("direct_airline_link", d["link"]),
            d.get("google_flights_link", ""),
            d.get("skyscanner_link", ""),
            d.get("aviasales_link", ""),
        ]

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "section", "segment", "price_usd", "city", "country", "airport", "depart", "return",
            "nights", "airline", "airline_name", "stops_out", "stops_back",
            "duration_to_min", "duration_back_min", "found_at", "favorite", "price_trend",
            "prev_price_usd", "direct_airline_link", "google_flights_link",
            "skyscanner_link", "aviasales_link",
        ])
        for d in deals:
            w.writerow(row_for("Weekend Getaways", seg_of.get(id(d)), d))
        for d in flex_deals:
            w.writerow(row_for(flex_section_label, flex_segment_label, d))


def render_deal_rows(rows, max_rows, empty_message):
    """Render HTML table rows for a list of deals (shared across sections)."""
    e = html.escape
    fmt = lambda x: x.strftime("%a %d %b")
    stops_txt = lambda n: "direct" if n == 0 else "%d stop%s" % (n, "" if n == 1 else "s")

    if not rows:
        return f"<tr class=\"empty-row\"><td colspan=5 class=m>{e(empty_message)}</td></tr>"

    out = []
    for d in rows[:max_rows]:
        # Price trend badge
        trend = d.get("trend", "")
        trend_html = ""
        if trend == "new":
            trend_html = '<br><span class="pill pill-new">NEW</span>'
        elif trend == "drop":
            trend_html = f'<br><span class="pill pill-drop" title="Was ${d["prev_price"]:.0f}">&darr; ${d["price_diff"]:.0f}</span>'

        # Favorite badge & row class
        fav = bool(d.get("is_favorite"))
        fav_html = '<span class="fav-star" title="Favorite destination">&#9733;</span> ' if fav else ""
        row_class = "deal-row fav-row" if fav else "deal-row"

        # Airline display
        airline_code = d.get("airline", "")
        airline_name = d.get("airline_name", "")
        if airline_name and airline_code:
            airline_display = f"{e(airline_name)} <span class=m>({e(airline_code)})</span>"
        else:
            airline_display = e(airline_name or airline_code or "—")

        # Duration display
        dur_out = format_duration(d.get("duration_to"))
        dur_back = format_duration(d.get("duration_back"))
        dur_html = f"<br><span class=m>{e(dur_out)} / {e(dur_back)}</span>" if (dur_out or dur_back) else ""

        # Freshness display
        freshness = format_found_at(d.get("found_at", ""))
        fresh_html = f" &bull; {e(freshness)}" if freshness else ""

        # Smart action buttons
        links_parts = []
        if d.get("has_direct_deeplink"):
            links_parts.append(
                f'<a href="{e(d["direct_airline_link"])}" target=_blank rel=noopener class=btn title="Pre-filled airline booking page">Book Direct</a>'
            )
            if d.get("google_flights_link"):
                links_parts.append(
                    f'<a href="{e(d["google_flights_link"])}" target=_blank rel=noopener class=btn-sec>Google Flights</a>'
                )
        else:
            if d.get("google_flights_link"):
                links_parts.append(
                    f'<a href="{e(d["google_flights_link"])}" target=_blank rel=noopener class=btn title="Pre-filled search on Google Flights">Google Flights</a>'
                )
            if d.get("airline_homepage"):
                links_parts.append(
                    f'<a href="{e(d["airline_homepage"])}" target=_blank rel=noopener class=btn-sec title="Airline homepage">Airline Site</a>'
                )
        if d.get("aviasales_link"):
            links_parts.append(
                f'<a href="{e(d["aviasales_link"])}" target=_blank rel=noopener class=btn-sec>Aviasales</a>'
            )
        if d.get("skyscanner_link"):
            links_parts.append(
                f'<a href="{e(d["skyscanner_link"])}" target=_blank rel=noopener class=btn-sec>Skyscanner</a>'
            )

        is_direct = "1" if (d["stops_out"] == 0 and d["stops_back"] == 0) else "0"
        search_blob = " ".join(
            x for x in (d["city"], d["country"], d["airport"], d["destination"], airline_name, airline_code) if x
        ).lower()
        subtitle_meta = " • ".join(x for x in (d["country"], d["airport"]) if x)

        out.append(
            f'<tr class="{row_class}" '
            f'data-price="{d["price"]:.2f}" '
            f'data-depart="{d["depart"].isoformat()}" '
            f'data-month="{d["depart"].strftime("%Y-%m")}" '
            f'data-nights="{d["nights"]}" '
            f'data-direct="{is_direct}" '
            f'data-fav="{"1" if fav else "0"}" '
            f'data-trend="{e(trend)}" '
            f'data-search="{e(search_blob)}">'
            f'<td class=p>${d["price"]:.0f}{trend_html}</td>'
            f'<td>{fav_html}<b>{e(d["city"])}</b><br><span class=m>{e(subtitle_meta)}</span></td>'
            f'<td>{fmt(d["depart"])} &rarr; {fmt(d["return"])}<br><span class=m>{d["nights"]} night{"" if d["nights"] == 1 else "s"}{fresh_html}</span></td>'
            f'<td>{airline_display}<br><span class=m>{stops_txt(d["stops_out"])} / {stops_txt(d["stops_back"])}</span>{dur_html}</td>'
            f'<td class="links-cell">{" ".join(links_parts)}</td>'
            f'</tr>'
        )
    return "".join(out)


def write_html(cfg, segs, flex_deals, today, path, n_offers):
    e = html.escape
    fmt = lambda x: x.strftime("%a %d %b")
    show_limit = cfg.get("show_per_segment", 50)

    # Section 1: Weekend Getaways
    weekend_sections = []
    all_displayed = []
    for name, _cap in cfg["segments"]:
        rows = segs.get(name, [])
        all_displayed.extend(rows[:show_limit])
        body = render_deal_rows(rows, show_limit, "No trips found in this range.")
        weekend_sections.append(
            f'<div class="table-block" data-total="{len(rows)}">'
            f'<h3>{e(name)} <span class="c section-count">({len(rows)})</span></h3>'
            f'<div class="table-wrap"><table><thead><tr><th>Price</th><th>Destination</th>'
            f'<th>Dates</th><th>Airline</th><th>Links</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div></div>'
        )

    # Section 2: Flexible Deals
    flex_min = cfg.get("flexible_min_nights", 3)
    flex_max = cfg.get("flexible_max_nights", 7)
    flex_price = cfg.get("flexible_max_price", 100)
    all_displayed.extend(flex_deals[:show_limit])

    flex_body = render_deal_rows(
        flex_deals, show_limit, f"No flexible trips found under ${flex_price}."
    )

    # Distinct departure months for the filter dropdown
    months = sorted({d["depart"].strftime("%Y-%m") for d in all_displayed})
    month_options = "".join(
        f'<option value="{m}">{dt.date.fromisoformat(m + "-01").strftime("%B %Y")}</option>'
        for m in months
    )

    # Quick summary counters
    n_new = sum(1 for d in all_displayed if d.get("trend") == "new")
    n_drops = sum(1 for d in all_displayed if d.get("trend") == "drop")
    n_favs = sum(1 for d in all_displayed if d.get("is_favorite"))
    highlights = []
    if n_drops:
        highlights.append(f'<span class="pill pill-drop">{n_drops} price drop{"" if n_drops == 1 else "s"}</span>')
    if n_new:
        highlights.append(f'<span class="pill pill-new">{n_new} new</span>')
    if n_favs:
        highlights.append(f'<span class="pill pill-fav">&#9733; {n_favs} favorite{"" if n_favs == 1 else "s"}</span>')
    highlights_html = ('<div class="highlights">' + " ".join(highlights) + "</div>") if highlights else ""

    end = today + dt.timedelta(days=cfg["horizon_days"])

    page = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Flight Deals Scanner - {origin}</title><style>
:root{{--bg:#fafaf8;--fg:#1d1d1b;--m:#6b6b66;--line:#e4e4df;--acc:#0b6e4f;--sec:#3366cc;--card-bg:#ffffff;--fav-bg:#fffbeb;--fav-bd:#f59e0b;--new-bg:#e0f2fe;--new-fg:#0369a1;--drop-bg:#dcfce7;--drop-fg:#15803d}}
@media(prefers-color-scheme:dark){{:root{{--bg:#161615;--fg:#ececea;--m:#9a9a94;--line:#2c2c2a;--acc:#4fc79c;--sec:#6699ff;--card-bg:#1e1e1c;--fav-bg:#2a2414;--fav-bd:#d97706;--new-bg:#0c2d48;--new-fg:#7dd3fc;--drop-bg:#143523;--drop-fg:#86efac}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,BlinkMacSystemFont,sans-serif;margin:0;padding:24px 16px;max-width:1040px;margin:auto}}
.header-row{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:8px}}
h1{{font-size:24px;margin:0;letter-spacing:-0.5px}}
h2{{font-size:19px;margin:32px 0 12px;padding-bottom:6px;border-bottom:2px solid var(--acc);display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
h3{{font-size:16px;margin:20px 0 8px}}.c{{color:var(--m);font-weight:400}}
.subtitle{{color:var(--m);font-size:13px;margin-bottom:16px;background:var(--card-bg);padding:12px 16px;border-radius:8px;border:1px solid var(--line)}}
.highlights{{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}}
.section-badge{{background:var(--acc);color:#fff;font-size:12px;padding:2px 8px;border-radius:12px;font-weight:600}}
.controls{{display:flex;flex-wrap:wrap;gap:10px;align-items:center;background:var(--card-bg);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:20px}}
.controls input[type="search"],.controls input[type="number"],.controls select{{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px 10px;font:inherit;font-size:13px}}
.controls input[type="search"]{{flex:1 1 200px;min-width:160px}}
.controls input[type="number"]{{width:100px}}
.controls label.chk{{display:inline-flex;align-items:center;gap:5px;font-size:13px;cursor:pointer;user-select:none}}
.controls button{{background:transparent;color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px 10px;font-size:13px;cursor:pointer}}
.controls button:hover{{background:var(--line)}}
.filter-status{{width:100%;font-size:12px;color:var(--m);display:none}}
.table-wrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;margin-bottom:24px;background:var(--card-bg);border-radius:8px;overflow:hidden;border:1px solid var(--line)}}
th{{text-align:left;font-size:12px;color:var(--m);font-weight:600;border-bottom:1px solid var(--line);padding:8px 12px;background:var(--bg);white-space:nowrap}}
td{{border-bottom:1px solid var(--line);padding:10px 12px;vertical-align:top}}
tr:last-child td{{border-bottom:none}}
tr.fav-row td{{background:var(--fav-bg)}}
tr.fav-row td:first-child{{box-shadow:inset 3px 0 0 var(--fav-bd)}}
.fav-star{{color:var(--fav-bd);font-size:14px}}
.p{{font-weight:700;color:var(--acc);font-size:16px;white-space:nowrap}}
.m{{color:var(--m);font-size:13px}}
.pill{{display:inline-block;padding:1px 6px;border-radius:999px;font-size:11px;font-weight:600;margin-top:3px;vertical-align:middle}}
.pill-new{{background:var(--new-bg);color:var(--new-fg)}}
.pill-drop{{background:var(--drop-bg);color:var(--drop-fg)}}
.pill-fav{{background:var(--fav-bg);color:var(--fav-bd);border:1px solid var(--fav-bd)}}
.links-cell{{display:flex;flex-wrap:wrap;gap:5px;align-items:center}}
a{{color:var(--acc);text-decoration:none}}a:hover{{text-decoration:underline}}
.btn{{display:inline-block;padding:4px 10px;border-radius:4px;background:var(--acc);color:#fff!important;font-size:12px;font-weight:600;transition:opacity 0.2s;white-space:nowrap}}
.btn:hover{{opacity:0.9;text-decoration:none}}
.btn-sec{{display:inline-block;padding:3px 8px;border-radius:4px;background:transparent;border:1px solid var(--line);color:var(--fg)!important;font-size:12px;transition:background 0.2s;white-space:nowrap}}
.btn-sec:hover{{background:var(--line);text-decoration:none}}
</style></head><body>
<div class="header-row">
  <h1>&#9992;&#65039; Flight Deals from {origin}</h1>
  <a href="deals.csv" download class="btn-sec">&#11015; Download CSV</a>
</div>
<div class="subtitle">
  Departures scanned: <b>{start}</b> to <b>{end}</b> (next {horizon} days) &bull; {n} cached offers analyzed &bull; generated {now}<br>
  <span class=m>All prices are round-trip per passenger in USD. Prices are cached from recent user searches&mdash;always confirm before booking.</span>
  {highlights_html}
</div>

<div class="controls" aria-label="Filter and sort deals">
  <input id="f-search" type="search" placeholder="Search city, country, airport, airline...">
  <select id="f-month" aria-label="Filter by month">
    <option value="">All months</option>
    {month_options}
  </select>
  <input id="f-max-price" type="number" min="0" step="10" placeholder="Max $">
  <select id="f-sort" aria-label="Sort deals">
    <option value="price">Sort: Price (low &rarr; high)</option>
    <option value="depart">Sort: Departure (soonest)</option>
    <option value="nights-asc">Sort: Nights (shortest)</option>
    <option value="nights-desc">Sort: Nights (longest)</option>
    <option value="fav">Sort: Favorites first</option>
  </select>
  <label class="chk"><input id="f-direct" type="checkbox"> Direct only</label>
  <label class="chk"><input id="f-changes" type="checkbox"> New / Drops</label>
  <label class="chk"><input id="f-fav" type="checkbox"> Favorites &#9733;</label>
  <button id="f-reset" type="button">Reset</button>
  <div id="f-status" class="filter-status"></div>
</div>

<h2>&#128467;&#65039; Weekend Getaways <span class="section-badge">{mn_wknd}-{mx_wknd} Nights</span></h2>
<div class=m style="margin-bottom:12px">Depart Wed&ndash;Fri, return Sat&ndash;Mon, covering the weekend with flexible stretch days.</div>
{weekend_html}

<h2>&#127775; Flexible Deals: {flex_min} to {flex_max} Nights Stay <span class="section-badge">Under ${flex_price}</span></h2>
<div class=m style="margin-bottom:12px">Any departure day of the week, staying {flex_min}&ndash;{flex_max} nights, priced up to ${flex_price}.</div>
<div class="table-block" data-total="{flex_total}">
  <h3>All Flexible Deals <span class="c section-count">({flex_total})</span></h3>
  <div class="table-wrap"><table><thead><tr><th>Price</th><th>Destination</th><th>Dates</th><th>Airline</th><th>Links</th></tr></thead>
  <tbody>{flex_html}</tbody></table></div>
</div>

<script>
(function() {{
  const elSearch = document.getElementById('f-search');
  const elMonth = document.getElementById('f-month');
  const elMaxPrice = document.getElementById('f-max-price');
  const elSort = document.getElementById('f-sort');
  const elDirect = document.getElementById('f-direct');
  const elChanges = document.getElementById('f-changes');
  const elFav = document.getElementById('f-fav');
  const elReset = document.getElementById('f-reset');
  const elStatus = document.getElementById('f-status');
  const blocks = Array.from(document.querySelectorAll('.table-block'));

  function cmpRows(mode) {{
    return (a, b) => {{
      const pa = parseFloat(a.dataset.price), pb = parseFloat(b.dataset.price);
      const da = a.dataset.depart, db = b.dataset.depart;
      const na = parseInt(a.dataset.nights, 10), nb = parseInt(b.dataset.nights, 10);
      const fa = a.dataset.fav === '1' ? 0 : 1, fb = b.dataset.fav === '1' ? 0 : 1;
      if (mode === 'depart') return da.localeCompare(db) || (pa - pb);
      if (mode === 'nights-asc') return (na - nb) || (pa - pb);
      if (mode === 'nights-desc') return (nb - na) || (pa - pb);
      if (mode === 'fav') return (fa - fb) || (pa - pb);
      return (pa - pb) || da.localeCompare(db);
    }};
  }}

  function apply() {{
    const q = elSearch.value.trim().toLowerCase();
    const month = elMonth.value;
    const maxP = elMaxPrice.value !== '' ? parseFloat(elMaxPrice.value) : null;
    const directOnly = elDirect.checked;
    const changesOnly = elChanges.checked;
    const favOnly = elFav.checked;
    const sortMode = elSort.value;
    const filtering = Boolean(q || month || maxP !== null || directOnly || changesOnly || favOnly);

    let totalShown = 0;
    let totalRows = 0;

    blocks.forEach(block => {{
      const tbody = block.querySelector('tbody');
      const countEl = block.querySelector('.section-count');
      const rows = Array.from(tbody.querySelectorAll('tr.deal-row'));
      const total = parseInt(block.dataset.total || rows.length, 10);
      totalRows += rows.length;

      rows.sort(cmpRows(sortMode)).forEach(r => tbody.appendChild(r));

      let shown = 0;
      rows.forEach(r => {{
        const ok =
          (!q || r.dataset.search.includes(q)) &&
          (!month || r.dataset.month === month) &&
          (maxP === null || parseFloat(r.dataset.price) <= maxP) &&
          (!directOnly || r.dataset.direct === '1') &&
          (!changesOnly || r.dataset.trend === 'new' || r.dataset.trend === 'drop') &&
          (!favOnly || r.dataset.fav === '1');
        r.style.display = ok ? '' : 'none';
        if (ok) shown++;
      }});
      totalShown += shown;

      let filterEmpty = tbody.querySelector('tr.filter-empty-row');
      if (rows.length > 0 && shown === 0) {{
        if (!filterEmpty) {{
          filterEmpty = document.createElement('tr');
          filterEmpty.className = 'filter-empty-row';
          filterEmpty.innerHTML = '<td colspan="5" class="m">No trips match your current filters.</td>';
          tbody.appendChild(filterEmpty);
        }}
        filterEmpty.style.display = '';
      }} else if (filterEmpty) {{
        filterEmpty.style.display = 'none';
      }}

      if (countEl) {{
        countEl.textContent = filtering ? `(${{shown}} / ${{total}})` : `(${{total}})`;
      }}
    }});

    if (filtering) {{
      elStatus.style.display = 'block';
      elStatus.textContent = `Showing ${{totalShown}} of ${{totalRows}} listed deals`;
    }} else {{
      elStatus.style.display = 'none';
    }}
  }}

  [elSearch, elMonth, elMaxPrice, elSort, elDirect, elChanges, elFav].forEach(el => {{
    el.addEventListener('input', apply);
    el.addEventListener('change', apply);
  }});
  elReset.addEventListener('click', () => {{
    elSearch.value = '';
    elMonth.value = '';
    elMaxPrice.value = '';
    elSort.value = 'price';
    elDirect.checked = false;
    elChanges.checked = false;
    elFav.checked = false;
    apply();
  }});
}})();
</script>
</body></html>""".format(
        origin=e(cfg["origin"]),
        start=fmt(today),
        end=fmt(end),
        horizon=cfg["horizon_days"],
        mn_wknd=cfg["min_nights"],
        mx_wknd=cfg["max_nights"],
        n=n_offers,
        now=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        highlights_html=highlights_html,
        month_options=month_options,
        weekend_html="".join(weekend_sections),
        flex_min=flex_min,
        flex_max=flex_max,
        flex_price=flex_price,
        flex_total=len(flex_deals),
        flex_html=flex_body,
    )

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
    airlines = load_airlines(cfg) if fetch is http_json else dict(DEFAULT_AIRLINE_NAMES)

    # 1. Weekend deals (Upper Section)
    deals = build_deals(cfg, offers, today, ref, airlines=airlines, weekend_only=True)

    # 2. Flexible deals (Lower Section)
    flex_deals = build_deals(
        cfg,
        offers,
        today,
        ref,
        airlines=airlines,
        weekend_only=False,
        min_n=cfg.get("flexible_min_nights", 3),
        max_n=cfg.get("flexible_max_nights", 7),
        max_price=cfg.get("flexible_max_price", 100),
    )

    # 3. Price-drop & NEW deal tracking
    if cfg.get("track_price_history", True):
        prev_history = load_price_history()
        annotate_price_changes(deals, prev_history.get("weekend"))
        annotate_price_changes(flex_deals, prev_history.get("flexible"))
        save_price_history(deals, flex_deals)

    segs = segment(cfg, deals)

    write_csv(cfg, deals, segs, flex_deals, OUT_DIR / "deals.csv")
    write_html(cfg, segs, flex_deals, today, OUT_DIR / "deals_report.html", len(offers))

    print("\n[Weekend Getaways] %d matching trips" % len(deals))
    for name, _ in cfg["segments"]:
        ds = segs.get(name, [])
        print("  == %s: %d ==" % (name, len(ds)))
        for d in ds[:5]:
            badge = " [NEW]" if d.get("trend") == "new" else (
                f" [-${d['price_diff']:.0f}]" if d.get("trend") == "drop" else ""
            )
            fav = "* " if d.get("is_favorite") else ""
            print(
                "    %s$%-5.0f %-18s %s -> %s  %s (%dn)%s"
                % (fav, d["price"], d["city"][:18], d["depart"], d["return"], d["airline"], d["nights"], badge)
            )

    flex_min = cfg.get("flexible_min_nights", 3)
    flex_max = cfg.get("flexible_max_nights", 7)
    flex_price = cfg.get("flexible_max_price", 100)
    print("\n[Flexible %d-%d Nights (Under $%s)] %d matching trips" % (flex_min, flex_max, flex_price, len(flex_deals)))
    for d in flex_deals[:10]:
        badge = " [NEW]" if d.get("trend") == "new" else (
            f" [-${d['price_diff']:.0f}]" if d.get("trend") == "drop" else ""
        )
        fav = "* " if d.get("is_favorite") else ""
        print(
            "  %s$%-5.0f %-18s %s -> %s  %s (%dn)%s"
            % (fav, d["price"], d["city"][:18], d["depart"], d["return"], d["airline"], d["nights"], badge)
        )

    print("\nReport generated: %s" % (OUT_DIR / "deals_report.html"))


if __name__ == "__main__":
    main()
