"""Finished-team results, Showdown import text and a scored result printer.

This format is only for finished results; it does not prescribe how search.py
represents partial teams or accumulates their scores.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TextIO

from models import Candidate, PokemonSet


@dataclass
class FinishedTeam:
    members: list[Candidate]
    # Weighted components and total, calculated by your search. Keys match
    # score_addition: usage, synergy, role, penalty, total.
    breakdown: dict[str, float]
    # One entry per member in addition order; a required seed contributes zeros.
    addition_breakdowns: list[dict[str, float]] = field(default_factory=list)
    requested_styles: list[str] = field(default_factory=list)
    excluded_styles: list[str] = field(default_factory=list)


def format_showdown_set(pokemon: PokemonSet) -> str:
    """Return one importable set, omitting default level and IV values.

    Require explicit stats rather than silently exporting a different build.
    Showdown defaults omitted EVs to zero, IVs to 31 and level to 100.
    """
    if pokemon.evs is None or pokemon.ivs is None:
        raise ValueError(f"Explicit EVs and IVs are required for {pokemon.name}")

    stats = (("hp", "HP"), ("atk", "Atk"), ("def", "Def"),
             ("spa", "SpA"), ("spd", "SpD"), ("spe", "Spe"))
    header = pokemon.name
    if pokemon.held_Item:
        header += f" @ {pokemon.held_Item}"
    lines = [header, f"Ability: {pokemon.ability}"]
    if pokemon.level != 100:
        lines.append(f"Level: {pokemon.level}")
    lines.append(f"Tera Type: {pokemon.tera_Type}")
    evs = " / ".join(
        f"{value} {label}" for stat, label in stats
        if (value := getattr(pokemon.evs, f"{stat}_EV")) != 0
    )
    if evs:
        lines.append(f"EVs: {evs}")
    lines.append(f"{pokemon.nature} Nature")
    ivs = " / ".join(
        f"{value} {label}" for stat, label in stats
        if (value := getattr(pokemon.ivs, f"{stat}_IV")) != 31
    )
    if ivs:
        lines.append(f"IVs: {ivs}")
    lines.extend(f"- {move}" for move in pokemon.moves)
    return "\n".join(lines)


def format_showdown_team(team: FinishedTeam) -> str:
    """Return just the selected sets, separated by a blank line for import.

    Like search.py, use the first (selected) set of each candidate.
    Scores and ranking labels stay outside this copyable text.
    """
    if any(not member.pokemon_sets for member in team.members):
        raise ValueError("Each finished member must have a selected Pokemon set")
    return "\n\n".join(format_showdown_set(member.pokemon_sets[0])
                       for member in team.members)


def print_showdown_team(team: FinishedTeam, *, stream: TextIO | None = None) -> None:
    """Print a single team without score or ranking labels."""
    print(format_showdown_team(team), file=stream)


def print_teams(teams: Sequence[FinishedTeam], *, stream: TextIO | None = None) -> None:
    """Print ranked scores followed by each team's Showdown import text."""
    if not teams:
        print("No finished teams.", file=stream)
        return

    for rank, team in enumerate(teams, start=1):
        names = ", ".join(
            member.pokemon_sets[0].name + (f" [{member.pokemon_sets[0].label}]" if member.pokemon_sets[0].label else "")
            for member in team.members)
        print(f"Team {rank}: {names}", file=stream)
        print(f"  Total:   {team.breakdown['total']:.6f}", file=stream)
        for component in ("usage", "synergy", "role", "penalty"):
            print(f"  {component.capitalize() + ':':9}{team.breakdown[component]: .6f}", file=stream)
        if "style" in team.breakdown:
            print(f"  Style:   {team.breakdown['style']: .6f}", file=stream)
            if team.requested_styles:
                print("  Requested styles: " + ", ".join(team.requested_styles), file=stream)
            if team.excluded_styles:
                print("  Excluded styles: " + ", ".join(team.excluded_styles), file=stream)
        print(file=stream)
        print_showdown_team(team, stream=stream)
        print(file=stream)
