"""
MLB Stats Scraper
Fetches batting and pitching season stats for all players on a given team
using the official MLB Stats API and writes them to CSV files.

Usage:
    python mlb_stats.py --team "New York Mets"
    python mlb_stats.py --team "Boston Red Sox" --season 2025 --output-dir ~/some/other/dir

If you see an SSL certificate error (e.g. self-signed cert in chain from a
corporate proxy), add the --no-verify-ssl flag:
    python mlb_stats.py --team "New York Mets" --no-verify-ssl
"""

import argparse
import csv
import re
import sys
from pathlib import Path

import requests
import urllib3
import mlbstatsapi

MLB_API_BASE = "https://statsapi.mlb.com/api/v1"

RISP_FIELDS = ["RISP_AB", "RISP_H", "RISP_RBI", "RISP_AVG", "RISP_OBP", "RISP_SLG", "RISP_OPS"]


def _disable_ssl_verification() -> None:
    """Monkey-patch requests to skip SSL verification globally."""
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    original_request = requests.Session.request

    def patched_request(self, method, url, **kwargs):
        kwargs.setdefault("verify", False)
        return original_request(self, method, url, **kwargs)

    requests.Session.request = patched_request


BATTING_FIELDS = [
    ("games_played", "G"),
    ("at_bats", "AB"),
    ("hits", "H"),
    ("doubles", "2B"),
    ("triples", "3B"),
    ("home_runs", "HR"),
    ("rbi", "RBI"),
    ("runs", "R"),
    ("stolen_bases", "SB"),
    ("base_on_balls", "BB"),
    ("strikeouts", "SO"),
    ("avg", "AVG"),
    ("obp", "OBP"),
    ("slg", "SLG"),
    ("ops", "OPS"),
]

PITCHING_FIELDS = [
    ("games_played", "G"),
    ("games_started", "GS"),
    ("wins", "W"),
    ("losses", "L"),
    ("saves", "SV"),
    ("innings_pitched", "IP"),
    ("era", "ERA"),
    ("strikeouts", "SO"),
    ("base_on_balls", "BB"),
    ("whip", "WHIP"),
    ("hits", "H"),
    ("home_runs", "HR"),
]


def slugify(name: str) -> str:
    """Convert a team name to a safe filename fragment."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def resolve_team(mlb: mlbstatsapi.Mlb, team_name: str) -> tuple[int, str]:
    """Return (team_id, canonical_team_name) for the given name string."""
    ids = mlb.get_team_id(team_name)
    if not ids:
        print(f"Error: No team found matching '{team_name}'.", file=sys.stderr)
        print("Tip: Try the full official name, e.g. 'New York Yankees'.", file=sys.stderr)
        sys.exit(1)
    if len(ids) > 1:
        teams = [mlb.get_team(tid) for tid in ids]
        names = ", ".join(t.name for t in teams)
        print(f"Multiple teams matched: {names}", file=sys.stderr)
        print("Please be more specific.", file=sys.stderr)
        sys.exit(1)
    team_id = ids[0]
    team = mlb.get_team(team_id)
    return team_id, team.name


def extract_stat_row(player_name: str, position: str, status: str, stat_obj, fields: list) -> dict:
    """Build a flat dict row from a split's stat Pydantic model."""
    stat_data = stat_obj.model_dump(exclude_none=True)
    row = {"Player": player_name, "Position": position, "Status": status}
    for api_key, csv_col in fields:
        row[csv_col] = stat_data.get(api_key, "")
    return row


def fetch_risp_stats(player_id: int, season: int) -> dict:
    """Return a dict of RISP batting stats for a player via a direct API call.

    Uses the statSplits endpoint with sitCodes=risp, which is not exposed by
    the python-mlb-statsapi wrapper. Returns empty strings for all fields when
    the player has no RISP plate appearances this season.
    """
    empty = {f: "" for f in RISP_FIELDS}
    try:
        resp = requests.get(
            f"{MLB_API_BASE}/people/{player_id}/stats",
            params={"stats": "statSplits", "group": "hitting", "season": season, "sitCodes": "risp"},
        )
        resp.raise_for_status()
        stats = resp.json().get("stats", [])
        if not stats or not stats[0].get("splits"):
            return empty
        stat = stats[0]["splits"][0]["stat"]
        return {
            "RISP_AB":  stat.get("atBats", ""),
            "RISP_H":   stat.get("hits", ""),
            "RISP_RBI": stat.get("rbi", ""),
            "RISP_AVG": stat.get("avg", ""),
            "RISP_OBP": stat.get("obp", ""),
            "RISP_SLG": stat.get("slg", ""),
            "RISP_OPS": stat.get("ops", ""),
        }
    except Exception:
        return empty


def write_csv(rows: list[dict], fieldnames: list[str], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Saved {len(rows)} row(s) → {path}")


def _handle_connection_error(exc: Exception) -> None:
    """Print a friendly message if the error looks like an SSL or network issue."""
    msg = str(exc).lower()
    if "ssl" in msg or "certificate" in msg:
        print(
            "\nSSL Error: Could not connect to the MLB Stats API.\n"
            "This usually happens when your network uses a proxy with a self-signed certificate.\n"
            "\nFix: re-run with the --no-verify-ssl flag:\n"
            "  python mlb_stats.py --team <name> --no-verify-ssl\n",
            file=sys.stderr,
        )
    elif "connection" in msg or "timeout" in msg or "max retries" in msg:
        print(
            "\nNetwork Error: Could not reach the MLB Stats API (statsapi.mlb.com).\n"
            "Check your internet connection and try again.\n",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape MLB player stats for a team.")
    parser.add_argument("--team", help="Team name (e.g. 'New York Yankees')")
    parser.add_argument("--season", type=int, default=2026, help="Season year (default: 2026)")
    parser.add_argument(
        "--output-dir",
        default=str(Path.home() / "Desktop"),
        help="Directory to write CSV files (default: ~/Desktop)",
    )
    parser.add_argument(
        "--no-verify-ssl",
        action="store_true",
        help="Disable SSL certificate verification (use when behind a corporate proxy)",
    )
    args = parser.parse_args()

    if args.no_verify_ssl:
        _disable_ssl_verification()
        print("Note: SSL certificate verification is disabled.")

    team_name = args.team
    if not team_name:
        if len(sys.argv) > 1:
            team_name = " ".join(sys.argv[1:]).strip()
        else:
            team_name = input("Enter team name (e.g. 'New York Mets'): ").strip()
        if not team_name:
            print("Error: Team name is required.", file=sys.stderr)
            sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    mlb = mlbstatsapi.Mlb()

    print(f"\nLooking up team: {team_name!r} ...")
    try:
        team_id, canonical_name = resolve_team(mlb, team_name)
    except Exception as exc:
        _handle_connection_error(exc)
        raise
    print(f"  Found: {canonical_name} (ID: {team_id})")

    print(f"Fetching {args.season} 40-man roster ...")
    roster = mlb.get_team_roster(team_id, rosterType="40Man", season=args.season)
    if not roster:
        print(f"No roster data found for {canonical_name} ({args.season}).", file=sys.stderr)
        sys.exit(1)
    print(f"  {len(roster)} players on roster (40-man, including IL).")

    batting_rows: list[dict] = []
    pitching_rows: list[dict] = []
    errors: list[str] = []

    print("Fetching player stats ...")
    for i, roster_player in enumerate(roster, 1):
        player_id = roster_player.id
        player_name = roster_player.full_name
        position = getattr(roster_player.primary_position, "abbreviation", "")
        status = getattr(roster_player.status, "description", "Active")

        print(f"  [{i:>2}/{len(roster)}] {player_name} ({position}) [{status}] ...", end=" ", flush=True)

        try:
            stat_dict = mlb.get_player_stats(
                player_id,
                stats=["season"],
                groups=["hitting", "pitching"],
                season=args.season,
            )
        except Exception as exc:
            _handle_connection_error(exc)
            errors.append(f"{player_name}: {exc}")
            print("ERROR")
            continue

        hit_added = pit_added = False

        hitting = stat_dict.get("hitting", {})
        season_hitting = hitting.get("season")
        if season_hitting and season_hitting.splits:
            risp = fetch_risp_stats(player_id, args.season)
            for split in season_hitting.splits:
                row = extract_stat_row(player_name, position, status, split.stat, BATTING_FIELDS)
                row.update(risp)
                batting_rows.append(row)
            hit_added = True

        pitching = stat_dict.get("pitching", {})
        season_pitching = pitching.get("season")
        if season_pitching and season_pitching.splits:
            for split in season_pitching.splits:
                row = extract_stat_row(player_name, position, status, split.stat, PITCHING_FIELDS)
                pitching_rows.append(row)
            pit_added = True

        tags = []
        if hit_added:
            tags.append("batting")
        if pit_added:
            tags.append("pitching")
        print(", ".join(tags) if tags else "no stats")

    slug = slugify(canonical_name)
    batting_path = output_dir / f"{slug}_batting_{args.season}.csv"
    pitching_path = output_dir / f"{slug}_pitching_{args.season}.csv"

    batting_headers = ["Player", "Position", "Status"] + [col for _, col in BATTING_FIELDS] + RISP_FIELDS
    pitching_headers = ["Player", "Position", "Status"] + [col for _, col in PITCHING_FIELDS]

    print("\nWriting CSV files ...")
    write_csv(batting_rows, batting_headers, batting_path)
    write_csv(pitching_rows, pitching_headers, pitching_path)

    if errors:
        print(f"\nWarnings — {len(errors)} player(s) had errors:")
        for msg in errors:
            print(f"  {msg}")

    print(f"\nDone. Stats for {canonical_name} ({args.season}) exported.")


if __name__ == "__main__":
    main()
