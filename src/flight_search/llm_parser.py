"""LLM parser: converts free-text flight query into structured ParsedQuery."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from openai import OpenAI

from . import config
from .models import ParsedQuery

# Airline name → IATA code reference included in prompt so LLM has context
_COMMON_AIRLINES = """
Common low-cost airline IATA codes (2-char):
  Spirit=NK, Frontier=F9, Southwest=WN, Allegiant=G4, Sun Country=SY,
  Breeze=MX, Avelo=XP, JetBlue=B6, Alaska=AS, Hawaiian=HA,
  Ryanair=FR, EasyJet=U2, Wizz Air=W6, Vueling=VY, Norwegian=DY,
  Volaris=Y4, Vivaaerobus=VB, Interjet=4O
Full-service (for reference):
  United=UA, American=AA, Delta=DL, British Airways=BA, Lufthansa=LH,
  Air France=AF, Emirates=EK, Qatar=QR, Cathay=CX, Singapore=SQ
Alliances (use exact string): STAR_ALLIANCE, SKYTEAM, ONEWORLD
"""

_SYSTEM_PROMPT = f"""You are a flight search assistant. Convert the user's free-text flight query
into a structured list of SerpAPI Google Flights search combinations.

Today's date: {date.today().isoformat()}

────────────────────────────────────────────────
RULE 1 — DATE RANGES WITH TIME-OF-DAY LOGIC
────────────────────────────────────────────────
Expand each date range into one SearchCombination per day, but VARY the
outbound_times / return_times per day using DEPARTURE-ONLY windows (2-value).
NEVER set arrival time constraints in SerpAPI params — use arrival_before
post_filters instead (see Rule 8).

  • FIRST day in range: apply stated time floor as departure window only.
      "evening" → "18,23"   "morning" → "6,12"   "afternoon" → "12,18"
      "after 10AM" → "10,23"   "before noon" → "0,12"
  • MIDDLE days: null (no constraint).
  • LAST day in range with a HARD arrival deadline (e.g. "reaching by 8AM"):
      Set outbound_times to a reasonable departure window that could still
      make the arrival, e.g. "0,6" (depart before 7AM for a 2.5h flight).
      AND emit an arrival_before post_filter with the exact deadline datetime.
  • For overnight arrivals (flight departs day X, arrives day X+1):
      The DEPARTING day's combination gets a late-departure window (e.g. "18,23").
      The arrival_before post_filter handles enforcing the next-day deadline.

  Worked example for: "departing range 27 March evening, reaching Miami by 30 March 8AM"
    Outbound date  | outbound_times | Reasoning
    2026-03-27     | "18,23"        | First day, evening departure floor
    2026-03-28     | null           | Middle day, unconstrained
    2026-03-29     | "18,23"        | Late depart on Mar 29, overnight → arrives Mar 30 morning
    2026-03-30     | "0,6"          | Last day: only very early departures can arrive by 8AM

  Also emit: PostFilter(filter_type="arrival_before", value="2026-03-30T08:00", leg="outbound")
  This post_filter is the authoritative arrival deadline check applied to ALL outbound combos.

  Worked example for: "returning April 4 after 10AM, reaching by April 6 8AM"
    Return date    | return_times   | Reasoning
    2026-04-04     | "10,23"        | First day, post-10AM departure floor
    2026-04-05     | null           | Middle day, unconstrained
    2026-04-06     | "0,6"          | Last day: only early departures arrive by 8AM

  Also emit: PostFilter(filter_type="arrival_before", value="2026-04-06T08:00", leg="return")

  COMBINATORIAL EXPLOSION GUARD: If (outbound_days × return_days × airport_alternatives)
  would exceed 20 combinations, collapse middle days and keep only first, one middle, last.

────────────────────────────────────────────────
RULE 2 — AIRPORTS
────────────────────────────────────────────────
Use IATA codes. Multi-airport cities: comma-separated in one field.
  NYC → "JFK,EWR,LGA"     LA  → "LAX,BUR,LGB,SNA,ONT"
  Chicago → "ORD,MDW"     DC  → "DCA,IAD,BWI"
  SF Bay → "SFO,OAK,SJC"  Boston → "BOS"
For unambiguous cities (e.g. "Austin"), use the single code (AUS).
Always use individual IATA codes or comma-separated lists — do NOT use metro/SITA
codes such as "WAS", "NYC", "CHI" as SerpAPI does not accept them.

────────────────────────────────────────────────
RULE 3 — AIRLINES (CRITICAL: read carefully)
────────────────────────────────────────────────
There are TWO distinct airline intent patterns — treat them very differently:

  (a) HARD FILTER — user says "only [airline]" / "I want [airline] flights":
      → Set include_airlines to the IATA code(s). Do NOT set post_filters for this.

  (b) SOFT PREFERENCE — user says "at least one flight/leg should be [airline]"
      / "prefer [airline]" / "include [airline] if possible":
      → Leave include_airlines = null (search ALL airlines for wider coverage).
      → Emit a PostFilter: filter_type="at_least_one_leg_airline", value="<airline name>".
        Use the full airline name (e.g. "Frontier"), not the IATA code — the code
        does substring matching against SerpAPI's full name strings.

  Never populate both include_airlines and exclude_airlines on the same combination.

────────────────────────────────────────────────
RULE 4 — DEFAULTS
────────────────────────────────────────────────
When not specified by user:
  - type=1 (round-trip) unless "one way" mentioned
  - travel_class=1 (economy)
  - adults=1, children=0
  - stops=0 (any) unless "nonstop"/"direct" → stops=1
  - sort_by=2 (price) unless user says "fastest"→5, "earliest"→3
  - max_price=null, max_duration=null unless stated
  - bags=0

────────────────────────────────────────────────
RULE 5 — RELATIVE DATES
────────────────────────────────────────────────
Resolve against today ({date.today().isoformat()}).
"next Friday" = upcoming Friday from today.

────────────────────────────────────────────────
RULE 6 — QUERY SUMMARY
────────────────────────────────────────────────
Write a short title like "DCA→MIA · Mar 27-30 / Apr 4-6 · Nonstop · Frontier pref."

────────────────────────────────────────────────
RULE 7 — TIME WINDOW FORMAT (outbound_times / return_times)
────────────────────────────────────────────────
ALWAYS use the 2-value departure-only format: "dep_start,dep_end"
Hours are integers 0-23 representing the START of that hour.

  IMPORTANT: NEVER use the 4-value arrival format. SerpAPI arrival filtering
  causes "no results" errors for nonstop flights and overnight arrivals.
  ALL arrival constraints must be expressed as arrival_before post_filters (Rule 8).

  Examples:
    "18,23"  = depart between 6PM and midnight
    "10,23"  = depart after 10AM
    "0,6"    = depart between midnight and 7AM
    null     = no time filter

────────────────────────────────────────────────
RULE 8 — POST_FILTERS (the code's escape hatch)
────────────────────────────────────────────────
post_filters are applied by the code AFTER collecting all SerpAPI results.
Use them for constraints that would over-restrict results if used as SerpAPI params,
or that SerpAPI cannot express:

  filter_type="at_least_one_leg_airline"
    Keep itineraries where at least one leg's airline contains the value string.
    value = airline name (e.g. "Frontier"). leg = "any".

  filter_type="arrival_before"
    Keep itineraries where the specified leg arrives before the given datetime.
    value = ISO-8601 datetime (e.g. "2026-03-30T08:00"). leg = "outbound"|"return"|"any".

Emit post_filters whenever:
  - User says "at least one leg should be [airline]" (Rule 3b)
  - User says "reaching by [datetime]" (ALWAYS — regardless of whether you also set a departure window)
  - Any other constraint that would hard-filter SerpAPI results incorrectly

{_COMMON_AIRLINES}

Return valid JSON matching the ParsedQuery schema exactly.
"""


def _query_hash(text: str) -> str:
    """Stable hash of the query text for cache invalidation."""
    return hashlib.sha256(text.strip().encode()).hexdigest()[:16]


def _save_parse(parsed: ParsedQuery, query_text: str) -> None:
    """Persist parsed output to disk so subsequent runs reuse it without calling GPT."""
    try:
        data = {
            "query_hash": _query_hash(query_text),
            "timestamp": datetime.now(timezone.utc).timestamp(),
            "parsed": parsed.model_dump(),
        }
        config.PARSED_CACHE_FILE.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # Non-critical


def _load_parse(query_text: str) -> ParsedQuery | None:
    """
    Return a previously persisted ParsedQuery if it matches the current query
    text. Otherwise None.
    """
    try:
        if not config.PARSED_CACHE_FILE.exists():
            return None
        data = json.loads(config.PARSED_CACHE_FILE.read_text(encoding="utf-8"))
        if data.get("query_hash") != _query_hash(query_text):
            return None
        return ParsedQuery.model_validate(data["parsed"])
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return None


def parse_query(free_text: str, *, force: bool = False) -> ParsedQuery:
    """
    Return a ParsedQuery for the given free-text.
    Reuses the last persisted parse if the query text matches.
    Pass force=True to skip the cache and always call GPT (equivalent to --reparse flag).
    """
    if not force:
        cached = _load_parse(free_text)
        if cached is not None:
            print("🗄️   Using cached GPT parse (use --reparse to force a fresh parse)")
            return cached

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    print("🤖  Sending query to GPT for parsing...")

    # Prefer Responses API for forward compatibility with newer models.
    try:
        response = client.responses.parse(
            model=config.OPENAI_MODEL,
            input=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": free_text.strip()},
            ],
            text_format=ParsedQuery,
            reasoning={"effort": "high"},
        )
        parsed_response = response.output_parsed
        if parsed_response is not None:
            _save_parse(parsed_response, free_text)
            return parsed_response
        raise ValueError("Model did not return parsed structured output.")
    except Exception:
        # Fallback for environments where chat.completions.parse is the stable path.
        completion = client.beta.chat.completions.parse(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": free_text.strip()},
            ],
            response_format=ParsedQuery,
            reasoning_effort="high",
        )

        message = completion.choices[0].message
        if message.refusal:
            raise ValueError(f"GPT refused the request: {message.refusal}")

        parsed: ParsedQuery = message.parsed  # type: ignore[assignment]
        _save_parse(parsed, free_text)
        return parsed
