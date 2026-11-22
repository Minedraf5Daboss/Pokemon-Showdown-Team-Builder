"""Run the beam search and print up to three finished teams."""

import argparse
import sys

from models import DEFAULT_TARGET_ROLES, build_pool, parse_restricted_mons
from search import DEFAULT_BEAM_WIDTH, DEFAULT_SEED_WEIGHT, DEFAULT_WEIGHTS, SearchError, beam_search
from team_output import print_teams
from team_styles import STYLES
from team_dependencies import set_dependencies


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--required", default=None,
                        help="Required species; 'none' starts empty (default: empty with --style, otherwise Amoonguss)")
    parser.add_argument("--exclude", default="", help="Comma-separated Pokemon to restrict")
    parser.add_argument("--required-set", default=None, help="Pin a set ID of the required species")
    parser.add_argument("--list-sets", metavar="SPECIES", help="List set IDs and scenarios for a species")
    parser.add_argument("--interactive", action="store_true", help="Prompt for restricted Pokemon")
    parser.add_argument("--beam-width", type=int, default=DEFAULT_BEAM_WIDTH)
    parser.add_argument("--unique-items", action="store_true", help="Optional builder policy; Doubles Ubers allows duplicate items")
    parser.add_argument("--seed-weight", type=float, default=DEFAULT_SEED_WEIGHT,
                        help="Compatibility weight for the required Pokemon (default: 2; 1 means equal weight)")
    parser.add_argument("--style", default="", help="Describe the team, e.g. 'rain with Tailwind' or 'defensive setup'")
    parser.add_argument("--list-styles", action="store_true", help="List supported styles and their intended strengths")
    parser.add_argument("--prompt", help="Use the validated natural-language pipeline (JSON output)")
    parser.add_argument("--request-file", help="Use a structured request JSON envelope")
    args = parser.parse_args(argv)
    if args.prompt is not None or args.request_file is not None:
        if args.prompt is not None and args.request_file is not None:
            parser.error("Choose --prompt or --request-file")
        if args.required is not None or args.exclude or args.required_set or args.style or args.interactive or args.unique_items:
            parser.error("Put constraints in the request envelope when using --prompt or --request-file")
        from team_api import main as pipeline_main
        return pipeline_main(['--prompt', args.prompt] if args.prompt is not None else ['--request-file', args.request_file])
    if args.list_styles:
        for label, style in STYLES.items():
            print(f"{label.replace('_', ' ')}: {style.excels_at}")
        return 0

    pool = build_pool()
    if args.list_sets:
        matches = [candidate for candidate in pool if candidate.pokemon_sets[0].name.casefold() == args.list_sets.strip().casefold()]
        if not matches:
            print(f"Species not in pool: {args.list_sets}", file=sys.stderr)
            return 2
        for pokemon in matches[0].pokemon_sets:
            profile = set_dependencies(pokemon)
            needs = sorted(profile.requires - profile.provides)
            suffix = "; requires: " + ", ".join(needs) if needs else ""
            print(f"{pokemon.set_id}: {pokemon.label} ({', '.join(pokemon.scenario_tags)}){suffix}")
        return 0
    restricted_mons = args.exclude
    required_arg = args.required if args.required is not None else ("none" if args.style else "Amoonguss")
    required = None if required_arg.strip().casefold() in ("", "none") else required_arg
    team_prompt = args.style
    if args.interactive:
        restricted_mons += "," + input("Enter any Pokemon you want to restrict (comma-separated): ")
        if required is None and not team_prompt:
            team_prompt = input("What kind of team would you like? (sun, rain, Trick Room, setup, etc.; blank for any): ")
    try:
        excluded_pokemon = parse_restricted_mons(restricted_mons, pool)
        teams = beam_search(
            pool,
            required_pokemon=required,
            required_set_id=args.required_set,
            excluded_pokemon=excluded_pokemon,
            team_size=6,
            beam_width=args.beam_width,
            top_k=3,
            weights=DEFAULT_WEIGHTS,
            target_roles=DEFAULT_TARGET_ROLES,
            team_prompt=team_prompt,
            seed_weight=args.seed_weight,
            unique_items=args.unique_items,
        )
    except (SearchError, ValueError) as error:
        print(f"Cannot build teams: {error}", file=sys.stderr)
        return 2
    print_teams(teams)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
