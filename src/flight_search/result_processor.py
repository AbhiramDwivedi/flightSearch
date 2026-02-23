"""Normalizes raw SerpAPI responses into flat FlightResult objects."""

from __future__ import annotations
from datetime import datetime
from .models import FlightResult, PostFilter


def _fmt_duration(minutes: int) -> str:
    """Format minutes as '5h 42m'."""
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m"


def _process_flight_group(group: dict) -> FlightResult | None:
    """Convert one SerpAPI FlightGroup dict into a FlightResult."""
    segments: list[dict] = group.get("flights", [])
    if not segments:
        return None

    first = segments[0]
    last = segments[-1]

    # Airlines and flight numbers (concatenate across segments)
    airlines = []
    flight_numbers = []
    airplanes = []
    legrooms = []
    extensions_all = []

    for seg in segments:
        airline = seg.get("airline", "")
        if airline and airline not in airlines:
            airlines.append(airline)
        fn = seg.get("flight_number", "")
        if fn:
            flight_numbers.append(fn)
        ap = seg.get("airplane", "")
        if ap and ap not in airplanes:
            airplanes.append(ap)
        lr = seg.get("legroom", "")
        if lr and lr not in legrooms:
            legrooms.append(lr)
        for ext in seg.get("extensions", []):
            if ext not in extensions_all:
                extensions_all.append(ext)

    # Layovers
    layovers: list[dict] = group.get("layovers", [])
    layover_parts = []
    for lv in layovers:
        dur = _fmt_duration(lv.get("duration", 0))
        code = lv.get("id", lv.get("name", "?"))
        overnight = " (overnight)" if lv.get("overnight") else ""
        layover_parts.append(f"{dur} at {code}{overnight}")

    stops = len(segments) - 1

    # Return-leg details (if available)
    return_segments: list[dict] = group.get("return_flights", [])
    return_first = return_segments[0] if return_segments else {}
    return_last = return_segments[-1] if return_segments else {}
    return_stops = (len(return_segments) - 1) if return_segments else None

    return_airlines = []
    return_flight_numbers = []
    return_airplanes = []
    return_legrooms = []
    return_extensions_all = []

    for seg in return_segments:
        airline = seg.get("airline", "")
        if airline and airline not in return_airlines:
            return_airlines.append(airline)
        fn = seg.get("flight_number", "")
        if fn:
            return_flight_numbers.append(fn)
        ap = seg.get("airplane", "")
        if ap and ap not in return_airplanes:
            return_airplanes.append(ap)
        lr = seg.get("legroom", "")
        if lr and lr not in return_legrooms:
            return_legrooms.append(lr)
        for ext in seg.get("extensions", []):
            if ext not in return_extensions_all:
                return_extensions_all.append(ext)

    return_layovers: list[dict] = group.get("return_layovers", [])
    return_layover_parts = []
    for lv in return_layovers:
        dur = _fmt_duration(lv.get("duration", 0))
        code = lv.get("id", lv.get("name", "?"))
        overnight = " (overnight)" if lv.get("overnight") else ""
        return_layover_parts.append(f"{dur} at {code}{overnight}")

    # Carbon emissions
    emissions_raw = group.get("carbon_emissions", {})
    emissions_g = emissions_raw.get("this_flight")
    emissions_kg = round(emissions_g / 1000, 1) if emissions_g is not None else None

    outbound_price = group.get("price")

    return FlightResult(
        itinerary_type="round_trip",
        origin=first.get("departure_airport", {}).get("id", ""),
        destination=last.get("arrival_airport", {}).get("id", ""),
        airline=", ".join(airlines),
        flight_numbers=" / ".join(flight_numbers),
        depart_time=first.get("departure_airport", {}).get("time", ""),
        arrive_time=last.get("arrival_airport", {}).get("time", ""),
        return_depart_time=return_first.get("departure_airport", {}).get("time") if return_segments else None,
        return_arrive_time=return_last.get("arrival_airport", {}).get("time") if return_segments else None,
        stops=stops,
        return_stops=return_stops,
        layover_info="; ".join(layover_parts),
        return_layover_info="; ".join(return_layover_parts) if return_layover_parts else None,
        total_duration_mins=group.get("total_duration", 0),
        return_total_duration_mins=group.get("return_total_duration"),
        price=outbound_price or 0,
        outbound_price=outbound_price,
        total_price=outbound_price,
        currency="USD",
        travel_class=first.get("travel_class", "Economy"),
        emissions_kg=emissions_kg,
        airplane=", ".join(airplanes),
        return_airplane=", ".join(return_airplanes) if return_airplanes else None,
        legroom=", ".join(legrooms),
        return_legroom=", ".join(return_legrooms) if return_legrooms else None,
        extensions=", ".join(extensions_all),
        return_flight_numbers=" / ".join(return_flight_numbers) if return_flight_numbers else None,
        return_airline=", ".join(return_airlines) if return_airlines else None,
        return_extensions=", ".join(return_extensions_all) if return_extensions_all else None,
    )


def _extract_group_leg(group: dict) -> dict | None:
    """Extract core details from a one-way group for pairing."""
    segments: list[dict] = group.get("flights", [])
    if not segments:
        return None

    first = segments[0]
    last = segments[-1]
    airlines = []
    flight_numbers = []
    for seg in segments:
        airline = seg.get("airline", "")
        if airline and airline not in airlines:
            airlines.append(airline)
        fn = seg.get("flight_number", "")
        if fn:
            flight_numbers.append(fn)

    layover_parts = []
    for lv in group.get("layovers", []):
        dur = _fmt_duration(lv.get("duration", 0))
        code = lv.get("id", lv.get("name", "?"))
        layover_parts.append(f"{dur} at {code}")

    return {
        "origin": first.get("departure_airport", {}).get("id", ""),
        "destination": last.get("arrival_airport", {}).get("id", ""),
        "airline": ", ".join(airlines),
        "flight_numbers": " / ".join(flight_numbers),
        "depart_time": first.get("departure_airport", {}).get("time", ""),
        "arrive_time": last.get("arrival_airport", {}).get("time", ""),
        "stops": len(segments) - 1,
        "layover_info": "; ".join(layover_parts),
        "duration": group.get("total_duration", 0),
        "price": group.get("price", 0),
        "travel_class": first.get("travel_class", "Economy"),
    }


def _build_independent_pairs(response: dict) -> list[FlightResult]:
    """Build combined itineraries from independent one-way outbound/return results."""
    outbound_groups: list[dict] = response.get("outbound_groups", [])
    return_groups: list[dict] = response.get("return_groups", [])
    if not outbound_groups or not return_groups:
        return []

    # Keep API and Excel sizes manageable.
    top_out = outbound_groups[:3]
    top_ret = return_groups[:3]

    pairs: list[FlightResult] = []
    for out_g in top_out:
        out_leg = _extract_group_leg(out_g)
        if not out_leg:
            continue
        for ret_g in top_ret:
            ret_leg = _extract_group_leg(ret_g)
            if not ret_leg:
                continue

            total_price = int((out_leg.get("price") or 0) + (ret_leg.get("price") or 0))
            pairs.append(
                FlightResult(
                    itinerary_type="independent_one_way",
                    origin=out_leg["origin"],
                    destination=out_leg["destination"],
                    airline=out_leg["airline"],
                    flight_numbers=out_leg["flight_numbers"],
                    depart_time=out_leg["depart_time"],
                    arrive_time=out_leg["arrive_time"],
                    return_depart_time=ret_leg["depart_time"],
                    return_arrive_time=ret_leg["arrive_time"],
                    stops=int(out_leg["stops"]),
                    return_stops=int(ret_leg["stops"]),
                    layover_info=out_leg["layover_info"],
                    return_layover_info=ret_leg["layover_info"],
                    total_duration_mins=int(out_leg["duration"]),
                    return_total_duration_mins=int(ret_leg["duration"]),
                    price=total_price,
                    outbound_price=int(out_leg["price"]),
                    return_price=int(ret_leg["price"]),
                    total_price=total_price,
                    currency="USD",
                    travel_class=str(out_leg["travel_class"]),
                    emissions_kg=None,
                    airplane="",
                    return_airplane=None,
                    legroom="",
                    return_legroom=None,
                    extensions="",
                    return_flight_numbers=ret_leg["flight_numbers"],
                    return_airline=ret_leg["airline"],
                    return_extensions=None,
                )
            )
    return pairs


def process_results(raw_responses: list[dict]) -> list[FlightResult]:
    """
    Process all SerpAPI responses into a deduplicated list of FlightResult objects.
    """
    seen: set[str] = set()
    results: list[FlightResult] = []

    for response in raw_responses:
        if not response:
            continue

        if response.get("__independent_one_way__"):
            for result in _build_independent_pairs(response):
                dedup_key = (
                    f"{result.itinerary_type}|{result.flight_numbers}|{result.depart_time}|"
                    f"{result.return_flight_numbers}|{result.return_depart_time}"
                )
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                results.append(result)
            continue

        all_groups = response.get("best_flights", []) + response.get("other_flights", [])

        for group in all_groups:
            result = _process_flight_group(group)
            if result is None:
                continue

            # Dedup key: flight numbers + depart time
            dedup_key = (
                f"{result.itinerary_type}|{result.flight_numbers}|{result.depart_time}|"
                f"{result.return_flight_numbers}|{result.return_depart_time}"
            )
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            results.append(result)

    return results


def sort_results(results: list[FlightResult], ranking_preference: str) -> list[FlightResult]:
    """Sort flight results according to the ranking preference."""
    pref = ranking_preference.lower()
    if "duration" in pref:
        return sorted(results, key=lambda r: (r.total_duration_mins + (r.return_total_duration_mins or 0)))
    elif "departure" in pref or "depart" in pref:
        return sorted(results, key=lambda r: r.depart_time)
    elif "arrival" in pref or "arrive" in pref:
        return sorted(results, key=lambda r: r.arrive_time)
    else:
        # Default: price
        return sorted(results, key=lambda r: (r.total_price if r.total_price is not None else r.price))


def _airline_matches(r: FlightResult, needle: str, leg: str) -> bool:
    """Return True if the flight result matches the airline needle on the specified leg(s)."""
    out_match = needle in (r.airline or "").lower()
    ret_match = needle in (r.return_airline or "").lower()
    if leg == "outbound":
        return out_match
    if leg == "return":
        return ret_match
    return out_match or ret_match  # "any"


def apply_post_filters(
    results: list[FlightResult],
    post_filters: list[PostFilter],
) -> list[FlightResult]:
    """
    Apply the LLM-specified post-processing filters to the result list.

    - at_least_one_leg_airline: SOFT preference — matching results are sorted
      to the top; non-matching results are still included.
    - arrival_before: HARD filter — results arriving after the deadline are removed.
    """
    for pf in post_filters:
        before = len(results)

        if pf.filter_type == "at_least_one_leg_airline":
            needle = pf.value.lower()
            # Stable sort: preferred-airline results first, rest appended after.
            preferred = [r for r in results if _airline_matches(r, needle, pf.leg)]
            others = [r for r in results if not _airline_matches(r, needle, pf.leg)]
            for r in preferred:
                r.preferred = True
            results = preferred + others
            after = len(results)
            print(
                f"  🔍  Post-filter 'prefer_airline={pf.value}': "
                f"{len(preferred)} preferred / {len(others)} other ({after} total)"
            )
            continue

        elif pf.filter_type == "arrival_before":
            try:
                deadline = datetime.fromisoformat(pf.value)
            except ValueError:
                print(f"  ⚠️  Post-filter 'arrival_before': invalid datetime '{pf.value}', skipping.")
                continue
            filtered = []
            for r in results:
                keep = True
                if pf.leg in ("outbound", "any") and r.arrive_time:
                    try:
                        arr = datetime.fromisoformat(r.arrive_time)
                        if arr > deadline:
                            keep = False
                    except ValueError:
                        pass
                if pf.leg in ("return", "any") and r.return_arrive_time and keep:
                    try:
                        ret_arr = datetime.fromisoformat(r.return_arrive_time)
                        if ret_arr > deadline:
                            keep = False
                    except ValueError:
                        pass
                if keep:
                    filtered.append(r)
            results = filtered

        after = len(results)
        leg_label = f" ({pf.leg} leg)" if pf.leg != "any" else ""
        print(
            f"  🔍  Post-filter '{pf.filter_type}={pf.value}'{leg_label}: "
            f"{before} → {after} results ({before - after} removed)"
        )

    return results
