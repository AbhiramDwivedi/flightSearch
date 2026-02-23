"""Quick SerpAPI diagnostic -- run from repo root."""
from serpapi import GoogleSearch
from flight_search import config

extra_tests = [
    ("DCA,IAD,BWI", "MIA"),
    ("DCA", "MIA,FLL"),
    ("DCA,IAD,BWI", "MIA,FLL"),
]

for dep, arr in extra_tests:
    params = {
        "engine": "google_flights", "api_key": config.SERPAPI_KEY,
        "departure_id": dep, "arrival_id": arr,
        "outbound_date": "2026-03-28", "return_date": "2026-04-05",
        "type": "1", "travel_class": "1", "adults": "2", "children": "2",
        "stops": "1", "currency": "USD", "hl": "en", "gl": "us",
    }
    r = GoogleSearch(params).get_dict()
    err = r.get("error")
    groups = r.get("best_flights", []) + r.get("other_flights", [])
    if err:
        print(f"  FAIL [{dep}->{arr}]: {err}")
    else:
        fl = groups[0].get("flights", [{}])[0] if groups else {}
        print(f"  OK   [{dep}->{arr}]: {len(groups)} groups  airline={fl.get('airline')}  price=${groups[0].get('price') if groups else 'N/A'}")

    # (label, departure_id, arrival_id, stops, outbound_times, return_times)
    ("DCA->MIA nonstop no-time",   "DCA", "MIA",     "1", None,    None),
    ("WAS->MIA nonstop no-time",   "WAS", "MIA",     "1", None,    None),
    ("WAS->MIA,FLL nonstop no-time","WAS","MIA,FLL", "1", None,    None),
    ("WAS->MIA nonstop with times","WAS", "MIA",     "1", "18,23", "10,23"),
    ("WAS->MIA any stops no-time", "WAS", "MIA",     "0", None,    None),
]

for label, dep, arr, stops, ot, rt in tests:
    params = {
        "engine": "google_flights",
        "api_key": config.SERPAPI_KEY,
        "departure_id": dep,
        "arrival_id": arr,
        "outbound_date": "2026-03-28",
        "return_date": "2026-04-05",
        "type": "1",
        "travel_class": "1",
        "adults": "2",
        "children": "2",
        "stops": stops,
        "currency": "USD",
        "hl": "en",
        "gl": "us",
    }
    if ot:
        params["outbound_times"] = ot
    if rt:
        params["return_times"] = rt

    r = GoogleSearch(params).get_dict()
    err = r.get("error")
    if err:
        print(f"  FAIL [{label}]: {err}")
    else:
        groups = r.get("best_flights", []) + r.get("other_flights", [])
        print(f"  OK   [{label}]: {len(groups)} groups", end="")
        if groups:
            fl = groups[0].get("flights", [{}])[0]
            print(f" | {fl.get('airline')} ${groups[0].get('price')}", end="")
        print()
