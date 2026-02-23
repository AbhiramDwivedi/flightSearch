"""Pydantic models for the flight search agent."""

from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


# ── LLM output models ─────────────────────────────────────────────────────────

class SearchCombination(BaseModel):
    """
    One individual SerpAPI Google Flights call.
    The LLM expands date ranges / airport alternatives into separate combinations.
    """
    # Airports (IATA codes, comma-separated for multi-airport)
    departure_id: str = Field(description="Departure airport IATA code(s), comma-separated, e.g. 'AUS' or 'JFK,EWR,LGA'")
    arrival_id: str = Field(description="Arrival airport IATA code(s), comma-separated, e.g. 'LAX' or 'LAX,BUR,LGB'")

    # Dates (YYYY-MM-DD)
    outbound_date: str = Field(description="Departure date in YYYY-MM-DD format")
    return_date: Optional[str] = Field(default=None, description="Return date in YYYY-MM-DD format, None for one-way")

    # Trip type: 1=round-trip, 2=one-way, 3=multi-city
    type: int = Field(default=1, description="1=round-trip, 2=one-way, 3=multi-city")

    # Travel class: 1=economy, 2=premium economy, 3=business, 4=first
    travel_class: int = Field(default=1, description="1=economy, 2=premium economy, 3=business, 4=first")

    # Passengers
    adults: int = Field(default=1, description="Number of adult passengers")
    children: int = Field(default=0, description="Number of child passengers")

    # Stops: 0=any, 1=nonstop only, 2=1 stop or fewer, 3=2 stops or fewer
    stops: int = Field(default=0, description="0=any stops, 1=nonstop only, 2=1 stop or fewer, 3=2 stops or fewer")

    # Airlines (2-char IATA codes, comma-separated; cannot use both)
    include_airlines: Optional[str] = Field(default=None, description="Comma-separated 2-char IATA codes to include, e.g. 'NK,F9'. None means no filter.")
    exclude_airlines: Optional[str] = Field(default=None, description="Comma-separated 2-char IATA codes to exclude. None means no filter.")

    # Price / duration filters
    max_price: Optional[int] = Field(default=None, description="Maximum ticket price in USD. None means no limit.")
    max_duration: Optional[int] = Field(default=None, description="Maximum total flight duration in minutes. None means no limit.")

    # Bags
    bags: int = Field(default=0, description="Number of carry-on bags")

    # Sort: 1=top flights, 2=price, 3=departure time, 4=arrival time, 5=duration, 6=emissions
    sort_by: int = Field(default=2, description="1=top flights, 2=price, 3=departure time, 4=arrival time, 5=duration, 6=emissions")

    # Departure/arrival time windows — two formats:
    #   2-value "dep_start,dep_end"               e.g. "18,23"      = depart 6PM–midnight (any arrival)
    #   4-value "dep_start,dep_end,arr_start,arr_end" e.g. "0,23,3,8" = any departure, arrive 3AM–9AM
    # Hours are integers 0–23 representing the START of that hour.
    outbound_times: Optional[str] = Field(
        default=None,
        description=(
            "Outbound time window. "
            "2-value 'dep_start,dep_end' filters departure only (e.g. '18,23' = evening departure). "
            "4-value 'dep_start,dep_end,arr_start,arr_end' also filters arrival "
            "(e.g. '0,23,3,8' = any departure, arrive 3AM-9AM). "
            "Use 4-value when user specifies an arrival time constraint. None = no filter."
        )
    )
    return_times: Optional[str] = Field(
        default=None,
        description=(
            "Return leg time window (same format as outbound_times). "
            "2-value filters return departure only. "
            "4-value also filters return arrival. "
            "None = no filter."
        )
    )


class PostFilter(BaseModel):
    """
    A post-processing filter that the code applies after collecting SerpAPI results.
    Used for constraints that SerpAPI can't natively enforce (soft airline preferences,
    absolute arrival deadlines, cross-day arrival constraints, etc.).
    """
    filter_type: Literal["at_least_one_leg_airline", "arrival_before"] = Field(
        description=(
            "Type of post-filter to apply. "
            "'at_least_one_leg_airline': keep itineraries where at least one leg's airline "
            "matches value (substring, case-insensitive). "
            "'arrival_before': keep itineraries where the relevant leg arrives before the "
            "ISO-8601 datetime in value (e.g. '2026-03-30T08:00')."
        )
    )
    value: str = Field(
        description=(
            "The constraint value. "
            "For 'at_least_one_leg_airline': airline name or IATA code (e.g. 'Frontier' or 'F9'). "
            "For 'arrival_before': ISO-8601 datetime string (e.g. '2026-03-30T08:00')."
        )
    )
    leg: Literal["outbound", "return", "any"] = Field(
        default="any",
        description=(
            "Which leg(s) to check. "
            "'outbound' checks only the outbound leg. "
            "'return' checks only the return leg. "
            "'any' checks either leg (at least one must match)."
        )
    )


class ParsedQuery(BaseModel):
    """
    Full output from the LLM: a list of search combinations to execute,
    plus a human-readable ranking preference for sorting results.
    """
    combinations: list[SearchCombination] = Field(
        description="List of individual SerpAPI search calls to make. "
                    "Expand date ranges and airport alternatives into separate combinations."
    )
    ranking_preference: str = Field(
        default="price",
        description="How to rank results in Excel: 'price', 'duration', 'departure_time', 'arrival_time'"
    )
    query_summary: str = Field(
        description="Short human-readable summary of the query for the Excel sheet title, e.g. 'AUS→LAX, Mar 10-12, nonstop, Spirit/Frontier'"
    )
    post_filters: list[PostFilter] = Field(
        default_factory=list,
        description=(
            "Post-processing filters the code applies after collecting SerpAPI results. "
            "Use for constraints SerpAPI cannot natively enforce: soft airline preferences "
            "('at least one leg should be X'), absolute arrival deadlines, or any filter "
            "that would over-restrict results if used as a SerpAPI query parameter."
        )
    )


# ── Normalized result model (one row in Excel) ────────────────────────────────

class FlightResult(BaseModel):
    """Flat, normalized flight result ready to be written as an Excel row."""
    itinerary_type: str = "round_trip"
    origin: str
    destination: str
    airline: str
    flight_numbers: str
    depart_time: str       # "2026-03-10 06:30"
    arrive_time: str       # "2026-03-10 08:12"
    return_depart_time: Optional[str] = None
    return_arrive_time: Optional[str] = None
    stops: int
    return_stops: Optional[int] = None
    layover_info: str      # "2h 15m at LAS" or "" for nonstop
    return_layover_info: Optional[str] = None
    total_duration_mins: int
    return_total_duration_mins: Optional[int] = None
    price: int             # USD (kept for backward compatibility)
    outbound_price: Optional[int] = None
    return_price: Optional[int] = None
    total_price: Optional[int] = None
    currency: str
    travel_class: str
    emissions_kg: Optional[float]
    airplane: str
    return_airplane: Optional[str] = None
    legroom: str
    return_legroom: Optional[str] = None
    extensions: str        # comma-joined extras e.g. "In-seat USB outlet, Below average legroom"
    return_flight_numbers: Optional[str] = None
    return_airline: Optional[str] = None
    return_extensions: Optional[str] = None
    # Soft-preference flag — True when an at_least_one_leg_airline post-filter matched
    preferred: bool = False
    # Dedup key (not written to Excel)
    _dedup_key: str = ""
