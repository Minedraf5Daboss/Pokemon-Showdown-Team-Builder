"""Export independent candidate sets for Showdown validation or manual review.

The text output contains a separate one-Pokemon template for each candidate.
"""

import json
from pathlib import Path

from models import FORMAT_ID, PokemonSet, teampool
from team_output import format_showdown_set


def showdown_set(pokemon: PokemonSet) -> dict:
    if pokemon.evs is None or pokemon.ivs is None:
        raise ValueError(f"Explicit EVs and IVs are required for {pokemon.name}")
    return {"name": pokemon.name, "species": pokemon.name, "ability": pokemon.ability,
            "teraType": pokemon.tera_Type, "item": pokemon.held_Item, "nature": pokemon.nature,
            "level": pokemon.level,
            "moves": list(pokemon.moves),
            "evs": {stat.removesuffix("_EV"): value for stat, value in vars(pokemon.evs).items()},
            "ivs": {stat.removesuffix("_IV"): value for stat, value in vars(pokemon.ivs).items()}}


def pool_document():
    return {"format": FORMAT_ID,
            "set_ids": [pokemon.set_id for candidate in teampool for pokemon in candidate.pokemon_sets],
            "sets": [showdown_set(pokemon) for candidate in teampool for pokemon in candidate.pokemon_sets]}


def importable(pokemon: PokemonSet) -> str:
    return format_showdown_set(pokemon)


if __name__ == "__main__":
    root = Path(__file__).parent / "data" / "smogon"
    (root / "candidate_sets.json").write_text(json.dumps(pool_document(), indent=2) + "\n")
    teams = [f"=== [{FORMAT_ID}] {pokemon.name} - {pokemon.label} ({pokemon.set_id}) ===\n\n{importable(pokemon)}"
             for candidate in teampool for pokemon in candidate.pokemon_sets]
    (root / "candidate_sets.txt").write_text("\n\n".join(teams) + "\n", encoding="utf-8")
    print(f"Exported {len(teams)} independent candidate sets.")
