"""
Flight Search Agent — entry point.

Usage:
    python -m flight_search              # reads query.txt in project root
    python -m flight_search my_trip.txt  # reads a custom file
    python -m flight_search --no-cache   # skip local SerpAPI response cache
    python -m flight_search --reparse    # force fresh GPT parse (ignore .last_parse.json)
"""

from __future__ import annotations
import sys
from pathlib import Path

from . import config
from .llm_parser import parse_query
from .flight_fetcher import fetch_all, get_monthly_usage
from .result_processor import apply_post_filters, process_results, sort_results
from .excel_exporter import export


def main() -> None:
    # ── 0. Parse flags ─────────────────────────────────────────────────────────
    argv = sys.argv[1:]
    if "--no-cache" in argv:
        config.NO_CACHE = True
        argv = [a for a in argv if a != "--no-cache"]
    force_reparse = "--reparse" in argv
    if force_reparse:
        argv = [a for a in argv if a != "--reparse"]
    # ── 1. Validate API keys ───────────────────────────────────────────────────
    config.validate_keys()

    # ── 2. Determine query file ────────────────────────────────────────────────
    query_file = Path(argv[0]) if argv else config.DEFAULT_QUERY_FILE

    if not query_file.exists():
        print(f"\n❌  Query file not found: {query_file}")
        print(f"    Create the file and add your flight search criteria.\n")
        sys.exit(1)

    raw_text = query_file.read_text(encoding="utf-8").strip()
    if not raw_text:
        print(f"\n❌  Query file is empty: {query_file}\n")
        sys.exit(1)

    cache_status = (
        f"disabled (--no-cache)" if config.NO_CACHE
        else f"enabled ({config.SERPAPI_CACHE_TTL_HOURS}h TTL)"
    )

    print("\n" + "═" * 60)
    print("✈️   Flight Search Agent")
    print("═" * 60)
    print(f"\n📄  Query file : {query_file}")
    print(f"🗄️   Cache      : {cache_status}")
    print(f"\n📝  Query text :\n")
    for line in raw_text.splitlines():
        print(f"    {line}")
    print()

    # ── 3. Parse query with GPT ────────────────────────────────────────────────
    parsed = parse_query(raw_text, force=force_reparse)

    print(f"\n📋  Summary    : {parsed.query_summary}")
    print(f"🔢  Combinations: {len(parsed.combinations)} search call(s) planned")
    print(f"📊  Ranking by : {parsed.ranking_preference}")
    if parsed.post_filters:
        for pf in parsed.post_filters:
            leg_label = f" ({pf.leg} leg)" if pf.leg != "any" else ""
            print(f"🔍  Post-filter : {pf.filter_type}={pf.value}{leg_label}")

    # ── 4. Combination explosion guard ────────────────────────────────────────
    if len(parsed.combinations) > config.MAX_COMBINATIONS:
        print(f"\n⚠️   {len(parsed.combinations)} combinations detected (max is {config.MAX_COMBINATIONS}).")
        print(f"    This would use {len(parsed.combinations)} of your {config.SERPAPI_MONTHLY_LIMIT} monthly SerpAPI calls.")
        try:
            input("    Press Enter to continue anyway, or Ctrl+C to abort: ")
        except KeyboardInterrupt:
            print("\n\nAborted.")
            sys.exit(0)

    # Show current usage
    current_usage = get_monthly_usage()
    remaining = config.SERPAPI_MONTHLY_LIMIT - current_usage
    print(f"\n📡  SerpAPI usage this month: {current_usage}/{config.SERPAPI_MONTHLY_LIMIT} ({remaining} remaining)")

    if remaining < len(parsed.combinations):
        print(f"\n⚠️   Not enough remaining calls ({remaining}) for all {len(parsed.combinations)} combinations.")
        try:
            input("    Some combinations will be skipped. Press Enter to continue or Ctrl+C to abort: ")
        except KeyboardInterrupt:
            print("\n\nAborted.")
            sys.exit(0)

    # ── 5. Fetch flights ───────────────────────────────────────────────────────
    print(f"\n🔍  Fetching flights...\n")
    raw_responses = fetch_all(parsed.combinations, post_filters=parsed.post_filters)

    fatal_error = next((r.get("__fatal_error__") for r in raw_responses if isinstance(r, dict) and r.get("__fatal_error__")), None)
    if fatal_error:
        print(f"\n⛔  Stopped due to fatal API error: {fatal_error}")
        print("    Update SERPAPI_KEY in .env and run again.\n")
        sys.exit(1)

    # ── 6. Process results ─────────────────────────────────────────────────────
    results = process_results(raw_responses)

    if not results:
        print("\n😕  No flights found matching your criteria.")
        print("    Try relaxing filters (stops, airlines, price) in query.txt.\n")
        sys.exit(0)

    # ── 6b. Apply post-filters ─────────────────────────────────────────────────
    if parsed.post_filters:
        print(f"\n🔍  Applying {len(parsed.post_filters)} post-filter(s)...")
        results = apply_post_filters(results, parsed.post_filters)
        if not results:
            print("\n😕  No flights remain after post-filtering.")
            print("    The preferred airline may not operate any of the searched routes/dates.\n")
            sys.exit(0)

    results = sort_results(results, parsed.ranking_preference)

    # ── 7. Export to Excel ─────────────────────────────────────────────────────
    output_dir = query_file.parent
    print(f"\n📊  Exporting {len(results)} flights to Excel...")
    xlsx_path = export(results, parsed.query_summary, output_dir=output_dir)

    # ── 8. Final summary ───────────────────────────────────────────────────────
    new_usage = get_monthly_usage()
    calls_used = new_usage - current_usage

    print(f"\n{'═' * 60}")
    print(f"✅  Done!")
    print(f"    Flights found  : {len(results)}")
    print(f"    Searches run   : {calls_used}")
    print(f"    Monthly usage  : {new_usage}/{config.SERPAPI_MONTHLY_LIMIT}")
    print(f"    Results saved  : {xlsx_path}")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    main()


    # ── 2. Determine query file ────────────────────────────────────────────────
    if len(sys.argv) > 1:
        query_file = Path(sys.argv[1])
    else:
        query_file = config.DEFAULT_QUERY_FILE

    if not query_file.exists():
        print(f"\n❌  Query file not found: {query_file}")
        print(f"    Create the file and add your flight search criteria.\n")
        sys.exit(1)

    raw_text = query_file.read_text(encoding="utf-8").strip()
    if not raw_text:
        print(f"\n❌  Query file is empty: {query_file}\n")
        sys.exit(1)

    print("\n" + "═" * 60)
    print("✈️   Flight Search Agent")
    print("═" * 60)
    print(f"\n📄  Query file : {query_file}")
    print(f"\n📝  Query text :\n")
    for line in raw_text.splitlines():
        print(f"    {line}")
    print()

    # ── 3. Parse query with GPT ────────────────────────────────────────────────
    parsed = parse_query(raw_text)

    print(f"\n📋  Summary    : {parsed.query_summary}")
    print(f"🔢  Combinations: {len(parsed.combinations)} search call(s) planned")
    print(f"📊  Ranking by : {parsed.ranking_preference}")

    # ── 4. Combination explosion guard ────────────────────────────────────────
    if len(parsed.combinations) > config.MAX_COMBINATIONS:
        print(f"\n⚠️   {len(parsed.combinations)} combinations detected (max is {config.MAX_COMBINATIONS}).")
        print(f"    This would use {len(parsed.combinations)} of your {config.SERPAPI_MONTHLY_LIMIT} monthly SerpAPI calls.")
        try:
            confirm = input("    Press Enter to continue anyway, or Ctrl+C to abort: ")
        except KeyboardInterrupt:
            print("\n\nAborted.")
            sys.exit(0)

    # Show current usage
    current_usage = get_monthly_usage()
    remaining = config.SERPAPI_MONTHLY_LIMIT - current_usage
    print(f"\n📡  SerpAPI usage this month: {current_usage}/{config.SERPAPI_MONTHLY_LIMIT} ({remaining} remaining)")

    if remaining < len(parsed.combinations):
        print(f"\n⚠️   Not enough remaining calls ({remaining}) for all {len(parsed.combinations)} combinations.")
        try:
            confirm = input("    Some combinations will be skipped. Press Enter to continue or Ctrl+C to abort: ")
        except KeyboardInterrupt:
            print("\n\nAborted.")
            sys.exit(0)

    # ── 5. Fetch flights ───────────────────────────────────────────────────────
    print(f"\n🔍  Fetching flights...\n")
    raw_responses = fetch_all(parsed.combinations)

    fatal_error = next((r.get("__fatal_error__") for r in raw_responses if isinstance(r, dict) and r.get("__fatal_error__")), None)
    if fatal_error:
        print(f"\n⛔  Stopped due to fatal API error: {fatal_error}")
        print("    Update SERPAPI_KEY in .env and run again.\n")
        sys.exit(1)

    # ── 6. Process results ─────────────────────────────────────────────────────
    results = process_results(raw_responses)

    if not results:
        print("\n😕  No flights found matching your criteria.")
        print("    Try relaxing filters (stops, airlines, price) in query.txt.\n")
        sys.exit(0)

    results = sort_results(results, parsed.ranking_preference)

    # ── 7. Export to Excel ─────────────────────────────────────────────────────
    output_dir = query_file.parent
    print(f"\n📊  Exporting {len(results)} flights to Excel...")
    xlsx_path = export(results, parsed.query_summary, output_dir=output_dir)

    # ── 8. Final summary ───────────────────────────────────────────────────────
    new_usage = get_monthly_usage()
    calls_used = new_usage - current_usage

    print(f"\n{'═' * 60}")
    print(f"✅  Done!")
    print(f"    Flights found  : {len(results)}")
    print(f"    Searches run   : {calls_used}")
    print(f"    Monthly usage  : {new_usage}/{config.SERPAPI_MONTHLY_LIMIT}")
    print(f"    Results saved  : {xlsx_path}")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    main()
