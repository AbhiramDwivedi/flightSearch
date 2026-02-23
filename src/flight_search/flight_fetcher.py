"""SerpAPI Google Flights fetcher with usage tracking, rate limiting, and local cache."""

from __future__ import annotations
import hashlib
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from serpapi import GoogleSearch
from . import config
from .models import PostFilter, SearchCombination


# ── Usage tracking ─────────────────────────────────────────────────────────────

def _load_usage() -> dict:
    """Load monthly usage counter from disk."""
    if not config.USAGE_FILE.exists():
        return {"month": date.today().strftime("%Y-%m"), "count": 0}
    try:
        data = json.loads(config.USAGE_FILE.read_text())
        # Reset if it's a new month
        if data.get("month") != date.today().strftime("%Y-%m"):
            return {"month": date.today().strftime("%Y-%m"), "count": 0}
        return data
    except (json.JSONDecodeError, KeyError):
        return {"month": date.today().strftime("%Y-%m"), "count": 0}


def _save_usage(usage: dict) -> None:
    config.USAGE_FILE.write_text(json.dumps(usage))


def get_monthly_usage() -> int:
    return _load_usage()["count"]


def _increment_usage() -> int:
    """Increment usage counter and return the new count."""
    usage = _load_usage()
    usage["count"] += 1
    _save_usage(usage)
    return usage["count"]


# ── Local response cache ───────────────────────────────────────────────────────

def _cache_key(params: dict) -> str:
    """SHA-256 of sorted params excluding api_key (which varies but is irrelevant to content)."""
    stable = {k: v for k, v in sorted(params.items()) if k != "api_key"}
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


def _cache_load() -> dict:
    """Load the local cache file, returning an empty dict on any error."""
    try:
        if config.CACHE_FILE.exists():
            return json.loads(config.CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _cache_save(cache: dict) -> None:
    """Save cache to disk, pruning entries older than TTL first."""
    ttl_seconds = config.SERPAPI_CACHE_TTL_HOURS * 3600
    now = datetime.now(timezone.utc).timestamp()
    pruned = {k: v for k, v in cache.items() if now - v.get("timestamp", 0) < ttl_seconds}
    try:
        config.CACHE_FILE.write_text(json.dumps(pruned), encoding="utf-8")
    except OSError:
        pass  # Non-critical — cache is best-effort


def _cache_lookup(params: dict) -> dict | None:
    """
    Return cached response if fresh (within TTL), otherwise None.
    Always returns None when config.NO_CACHE is True.
    """
    if config.NO_CACHE:
        return None
    key = _cache_key(params)
    cache = _cache_load()
    entry = cache.get(key)
    if not entry:
        return None
    age_hours = (datetime.now(timezone.utc).timestamp() - entry.get("timestamp", 0)) / 3600
    if age_hours > config.SERPAPI_CACHE_TTL_HOURS:
        return None
    return entry.get("response")


def _cache_store(params: dict, response: dict) -> None:
    """Persist a fresh SerpAPI response to the local cache."""
    if config.NO_CACHE:
        return
    key = _cache_key(params)
    cache = _cache_load()
    cache[key] = {
        "timestamp": datetime.now(timezone.utc).timestamp(),
        "response": response,
    }
    _cache_save(cache)


# ── SerpAPI call ───────────────────────────────────────────────────────────────

# Metro/SITA codes that SerpAPI's Google Flights engine does NOT accept.
# Map them to comma-separated individual IATA codes.
_METRO_EXPANSION: dict[str, str] = {
    "WAS": "DCA,IAD,BWI",
    "NYC": "JFK,EWR,LGA",
    "CHI": "ORD,MDW",
    "YTO": "YYZ,YTZ",
    "BJS": "PEK,PKX",
    "SEA": "SEA",  # SEA is fine as-is; listed to avoid accidental expansion
}


def _expand_airports(code: str) -> str:
    """Expand known metro codes that SerpAPI rejects into comma-separated IATA codes."""
    return _METRO_EXPANSION.get(code.upper(), code)


def _departure_only(time_str: str) -> str:
    """Strip arrival portion from a time window string — SerpAPI only accepts 2 values."""
    parts = time_str.split(",")
    return f"{parts[0].strip()},{parts[1].strip()}" if len(parts) >= 2 else time_str


def _build_params(combo: SearchCombination) -> dict:
    """Convert a SearchCombination into SerpAPI query params."""
    params: dict = {
        "engine": "google_flights",
        "api_key": config.SERPAPI_KEY,
        "departure_id": _expand_airports(combo.departure_id),
        "arrival_id": _expand_airports(combo.arrival_id),
        "outbound_date": combo.outbound_date,
        "type": str(combo.type),
        "travel_class": str(combo.travel_class),
        "adults": str(combo.adults),
        "children": str(combo.children),
        "stops": str(combo.stops),
        "sort_by": str(combo.sort_by),
        "bags": str(combo.bags),
        "currency": "USD",
        "hl": "en",
        "gl": "us",
        "deep_search": "true",
    }

    if combo.return_date:
        params["return_date"] = combo.return_date

    if combo.include_airlines:
        params["include_airlines"] = combo.include_airlines
    elif combo.exclude_airlines:
        params["exclude_airlines"] = combo.exclude_airlines

    if combo.max_price is not None:
        params["max_price"] = str(combo.max_price)

    if combo.max_duration is not None:
        params["max_duration"] = str(combo.max_duration)

    if combo.outbound_times:
        params["outbound_times"] = _departure_only(combo.outbound_times)

    if combo.return_times:
        params["return_times"] = _departure_only(combo.return_times)

    return params


def _lookup_return_group(base_params: dict, departure_token: str) -> dict:
    """
    Lookup return-flight options for a selected outbound flight via departure_token.
    Returns the first available return group or {} if unavailable.
    """
    params = dict(base_params)
    params["departure_token"] = departure_token

    # Check local cache first
    cached = _cache_lookup(params)
    if cached is not None:
        groups = cached.get("best_flights", []) + cached.get("other_flights", [])
        return groups[0] if groups else {}

    try:
        usage = _load_usage()
        if usage["count"] >= config.SERPAPI_MONTHLY_LIMIT:
            return {}

        search = GoogleSearch(params)
        results = search.get_dict()
        error = results.get("error")
        if error:
            return {}

        groups = results.get("best_flights", []) + results.get("other_flights", [])
        if not groups:
            return {}

        _increment_usage()
        _cache_store(params, results)
        return groups[0]
    except Exception:
        return {}


def _fetch_one_way_groups(params: dict, label: str = "") -> list[dict]:
    """Fetch one-way groups for a given parameter set, using local cache when available."""
    # Check local cache first
    cached = _cache_lookup(params)
    if cached is not None:
        if label:
            print(f"    {label} (cached)", flush=True)
        groups = cached.get("best_flights", []) + cached.get("other_flights", [])
        return groups

    try:
        usage = _load_usage()
        if usage["count"] >= config.SERPAPI_MONTHLY_LIMIT:
            return []

        search = GoogleSearch(params)
        results = search.get_dict()
        error = results.get("error")
        if error:
            return []

        groups = results.get("best_flights", []) + results.get("other_flights", [])
        if groups:
            _increment_usage()
            _cache_store(params, results)
        return groups
    except Exception:
        return []


def fetch_combination(combo: SearchCombination, index: int, total: int) -> dict:
    """
    Execute one SerpAPI call (or return from local cache) and return the raw response dict.
    Returns an empty dict on error (caller should skip it).
    """
    usage = _load_usage()
    if usage["count"] >= config.SERPAPI_MONTHLY_LIMIT:
        print(f"\n⛔  Monthly SerpAPI limit of {config.SERPAPI_MONTHLY_LIMIT} searches reached. Stopping.")
        return {}

    if usage["count"] >= config.SERPAPI_MONTHLY_LIMIT - 10:
        remaining = config.SERPAPI_MONTHLY_LIMIT - usage["count"]
        print(f"⚠️   Warning: only {remaining} SerpAPI searches remaining this month.")

    route = f"{combo.departure_id} → {combo.arrival_id} on {combo.outbound_date}"
    print(f"  [{index}/{total}] Searching {route}...", end=" ", flush=True)

    params = _build_params(combo)

    # ── Cache lookup ───────────────────────────────────────────────────────────
    cached = _cache_lookup(params)
    if cached is not None:
        flights_found = len(cached.get("best_flights", [])) + len(cached.get("other_flights", []))
        print(f"🗄️  {flights_found} from cache  (monthly usage: {get_monthly_usage()}/{config.SERPAPI_MONTHLY_LIMIT})")
        # Still enrich with return-leg data (also cached if available)
        if combo.type == 1 and combo.return_date:
            _enrich_return_legs(cached, params, combo)
        return cached

    try:
        search = GoogleSearch(params)
        results = search.get_dict()

        error = results.get("error")
        if error:
            if "invalid api key" in str(error).lower():
                print(f"⛔  API error: {error}")
                return {"__fatal_error__": str(error)}
            print(f"⚠️  API error: {error}")
            return {}

        _increment_usage()
        _cache_store(params, results)

        # For round-trip searches, fetch corresponding return-leg details
        # for each outbound option using departure_token.
        if combo.type == 1 and combo.return_date:
            _enrich_return_legs(results, params, combo)

        new_count = get_monthly_usage()
        flights_found = len(results.get("best_flights", [])) + len(results.get("other_flights", []))
        print(f"✅  {flights_found} flight options found  (monthly usage: {new_count}/{config.SERPAPI_MONTHLY_LIMIT})")
        return results

    except Exception as exc:
        print(f"❌  Failed: {exc}")
        return {}


def _enrich_return_legs(results: dict, params: dict, combo: SearchCombination) -> None:
    """
    Mutates `results` in-place: for the top 5 outbound groups, fetches the
    corresponding return-leg details via departure_token and attaches them.
    """
    all_groups = results.get("best_flights", []) + results.get("other_flights", [])
    for group in all_groups[:5]:
        token = group.get("departure_token")
        if not token:
            continue
        return_group = _lookup_return_group(params, token)
        if not return_group:
            continue
        group["return_flights"] = return_group.get("flights", [])
        group["return_layovers"] = return_group.get("layovers", [])
        group["return_total_duration"] = return_group.get("total_duration")
        group["return_airline_logo"] = return_group.get("airline_logo")
        group["return_extensions"] = return_group.get("extensions", [])


def fetch_all(
    combinations: list[SearchCombination],
    post_filters: list[PostFilter] | None = None,
) -> list[dict]:
    """
    Fetch all search combinations sequentially with rate-limit delay.
    Returns list of raw SerpAPI response dicts (empty dicts skipped by processor).

    If post_filters contains an 'at_least_one_leg_airline' filter, an additional
    targeted one-way search is run with include_airlines set to ensure the preferred
    airline's flights are captured even if they appear late in SerpAPI pagination.
    """
    post_filters = post_filters or []
    results = []
    total = len(combinations)

    for i, combo in enumerate(combinations, start=1):
        raw = fetch_combination(combo, i, total)
        if raw.get("__fatal_error__"):
            results.append(raw)
            break
        results.append(raw)

        # Also collect independent one-way options for round-trip queries
        # so we can build combined itineraries with total pricing.
        if combo.type == 1 and combo.return_date:
            base = _build_params(combo)
            # Strip airline filters from independent one-way params —
            # post-filters handle airline preferences, not SerpAPI params.
            base.pop("include_airlines", None)
            base.pop("exclude_airlines", None)

            outbound_params = dict(base)
            outbound_params["type"] = "2"
            outbound_params.pop("return_date", None)
            outbound_params.pop("return_times", None)
            # Strip arrival portion from outbound_times for one-way (SerpAPI quirk)
            if outbound_params.get("outbound_times"):
                parts = outbound_params["outbound_times"].split(",")
                if len(parts) == 4:
                    outbound_params["outbound_times"] = f"{parts[0]},{parts[1]}"

            return_params = dict(base)
            return_params["type"] = "2"
            return_params["departure_id"] = _expand_airports(combo.arrival_id)
            return_params["arrival_id"] = _expand_airports(combo.departure_id)
            return_params["outbound_date"] = combo.return_date
            return_params.pop("return_date", None)
            return_params.pop("outbound_times", None)
            # For return one-way, use return time window as outbound window.
            if combo.return_times:
                rt_parts = combo.return_times.split(",")
                # Use only departure portion for one-way
                return_params["outbound_times"] = f"{rt_parts[0]},{rt_parts[1]}" if len(rt_parts) >= 2 else combo.return_times

            outbound_groups = _fetch_one_way_groups(outbound_params)
            return_groups = _fetch_one_way_groups(return_params)

            results.append({
                "__independent_one_way__": True,
                "combo": {
                    "departure_id": combo.departure_id,
                    "arrival_id": combo.arrival_id,
                    "outbound_date": combo.outbound_date,
                    "return_date": combo.return_date,
                },
                "outbound_groups": outbound_groups,
                "return_groups": return_groups,
            })

        if i < total:
            time.sleep(config.SERPAPI_CALL_DELAY)

    # ── Dual-fetch for soft airline preferences ────────────────────────────────
    # For each "at_least_one_leg_airline" post-filter, run one targeted one-way
    # search per unique route/date pair with include_airlines set. This ensures
    # the preferred airline's flights appear even if paginated out above.
    airline_filters = [f for f in post_filters if f.filter_type == "at_least_one_leg_airline"]
    if airline_filters and combinations:
        # Build IATA lookup from common airlines (name → code)
        _NAME_TO_IATA = {
            "spirit": "NK", "frontier": "F9", "southwest": "WN", "allegiant": "G4",
            "sun country": "SY", "breeze": "MX", "avelo": "XP", "jetblue": "B6",
            "alaska": "AS", "hawaiian": "HA", "united": "UA", "american": "AA",
            "delta": "DL", "british airways": "BA", "lufthansa": "LH",
        }

        seen_routes: set[str] = set()
        for combo in combinations:
            route_key = f"{combo.departure_id}|{combo.arrival_id}|{combo.outbound_date}|{combo.return_date}"
            if route_key in seen_routes or combo.type != 1 or not combo.return_date:
                continue
            seen_routes.add(route_key)

            for af in airline_filters:
                iata = _NAME_TO_IATA.get(af.value.lower(), af.value.upper()[:2])
                print(f"  [dual-fetch] {combo.departure_id}→{combo.arrival_id} on {combo.outbound_date} with airline={iata}...", end=" ", flush=True)

                base = _build_params(combo)
                base["include_airlines"] = iata
                base.pop("exclude_airlines", None)

                out_p = dict(base)
                out_p["type"] = "2"
                out_p.pop("return_date", None)
                out_p.pop("return_times", None)
                if out_p.get("outbound_times"):
                    parts = out_p["outbound_times"].split(",")
                    if len(parts) == 4:
                        out_p["outbound_times"] = f"{parts[0]},{parts[1]}"

                ret_p = dict(base)
                ret_p["type"] = "2"
                ret_p["departure_id"] = _expand_airports(combo.arrival_id)
                ret_p["arrival_id"] = _expand_airports(combo.departure_id)
                ret_p["outbound_date"] = combo.return_date
                ret_p.pop("return_date", None)
                ret_p.pop("outbound_times", None)
                if combo.return_times:
                    rt_parts = combo.return_times.split(",")
                    ret_p["outbound_times"] = f"{rt_parts[0]},{rt_parts[1]}" if len(rt_parts) >= 2 else combo.return_times

                out_g = _fetch_one_way_groups(out_p)
                ret_g = _fetch_one_way_groups(ret_p)
                print(f"✅  {len(out_g)} out / {len(ret_g)} ret")

                results.append({
                    "__independent_one_way__": True,
                    "combo": {
                        "departure_id": combo.departure_id,
                        "arrival_id": combo.arrival_id,
                        "outbound_date": combo.outbound_date,
                        "return_date": combo.return_date,
                    },
                    "outbound_groups": out_g,
                    "return_groups": ret_g,
                })

            time.sleep(config.SERPAPI_CALL_DELAY)

    return results
