"""Regression cases for real set engines, slot planning and exclusions."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from models import build_pool
from recommend_sets import infer_ivs
from search import beam_search
from team_dependencies import DependencyPlan, DependencyState, set_dependencies, unmet_dependencies


@pytest.fixture
def species():
    return {c.pokemon_sets[0].name: c for c in build_pool()}


def choose(species, name, set_id=None):
    candidate = deepcopy(species[name])
    candidate.pokemon_sets = [next(s for s in candidate.pokemon_sets if set_id is None or s.set_id == set_id)]
    return candidate


def test_reviewed_catalog_fixes_survive_regeneration(species):
    ape = choose(species, "Annihilape", "annihilape:bulk-up-attacker").pokemon_sets[0]
    assert ape.ivs.atk_IV == 31 and "Physical Sweeper" in ape.role_tags
    lugia = species["Lugia"].pokemon_sets[0]
    assert lugia.ivs.spe_IV == 31
    skarmory = species["Skarmory"].pokemon_sets[0]
    assert "Stealth Rock" in skarmory.moves and "Tailwind" not in skarmory.moves
    assert "Speed Control" not in skarmory.role_tags and skarmory.ivs.spe_IV == 31
    assert species["Pawmot"].pokemon_sets[0].tera_Type == "Electric"


def test_attack_iv_uses_move_category_even_without_attack_evs():
    categories = json.loads((Path(__file__).parent / "data/smogon/battle_dex.json").read_text(encoding="utf-8"))["move_categories"]
    evs = {"hp": 252, "atk": 0, "def": 4, "spa": 0, "spd": 252, "spe": 0}
    assert infer_ivs("Annihilape", ["Rage Fist", "Drain Punch"], "Sassy", evs, categories)["atk"] == 31
    assert infer_ivs("Lugia", ["Aeroblast"], "Relaxed", evs, categories, "31")["spe"] == 31
    with pytest.raises(ValueError, match="Missing move category"):
        infer_ivs("Unknown", ["Invented Move"], "Calm", evs, categories)


@pytest.mark.parametrize("name,set_id,partner", [
    ("Sneasler", "sneasler:terrain-seed-attacker", "Miraidon"),
    ("Farigiraf", "farigiraf:priority-blocking-room-support", "Miraidon"),
    ("Calyrex-Shadow", "calyrexshadow:psychic-terrain-attacker", "Indeedee-F"),
    ("Calyrex-Ice", "calyrexice:room-coverage-attacker", "Cresselia"),
    ("Charizard", "charizard:legacy", "Groudon"),
    ("Archaludon", "archaludon:rain-assault-vest", "Kyogre"),
    ("Kyurem-White", "kyuremwhite:snow-spread-attacker", "Ninetales-Alola"),
    ("Tatsugiri", "tatsugiri:commander-support", "Dondozo"),
    ("Annihilape", "annihilape:bulk-up-attacker", "Maushold"),
])
def test_plain_search_requires_real_partners(species, name, set_id, partner):
    selected = choose(species, name, set_id)
    pool = [selected, choose(species, partner), choose(species, "Incineroar")]
    before = deepcopy(pool)
    teams = beam_search(pool, required_pokemon=name, required_set_id=set_id,
                        team_size=2, beam_width=1, top_k=1)
    assert {m.pokemon_sets[0].name for m in teams[0].members} == {name, partner}
    assert not unmet_dependencies(teams[0].members)
    assert pool == before
    with pytest.raises(ValueError, match="dependencies"):
        beam_search(pool, required_pokemon=name, required_set_id=set_id,
                    excluded_pokemon=partner, team_size=2)
    with pytest.raises(ValueError, match="dependencies"):
        beam_search(pool, required_pokemon=name, required_set_id=set_id, team_size=1)


def test_costar_commander_reserves_two_partner_slots_in_narrow_beam(species):
    pin = "flamigo:costar-partner"
    teams = beam_search(list(species.values()), required_pokemon="Flamigo", required_set_id=pin,
                        beam_width=1, top_k=1)
    names = {m.pokemon_sets[0].name for m in teams[0].members}
    assert {"Flamigo", "Dondozo"} <= names
    assert any(name.startswith("Tatsugiri") for name in names)
    assert not unmet_dependencies(teams[0].members)
    with pytest.raises(ValueError, match="dependencies"):
        beam_search(list(species.values()), required_pokemon="Flamigo", required_set_id=pin, team_size=2)


def test_costar_needs_a_boosting_variant_not_just_the_species(species):
    flamigo = choose(species, "Flamigo")
    flamigo.pokemon_sets[0].scenario_tags = ["costar"]
    groudon = species["Groudon"]
    teams = beam_search([flamigo, groudon], required_pokemon="Flamigo", team_size=2)
    assert teams
    assert all("Swords Dance" in team.members[1].pokemon_sets[0].moves for team in teams)


def test_unpinned_species_can_choose_an_independent_variant(species):
    farigiraf = species["Farigiraf"]
    teams = beam_search([farigiraf, choose(species, "Incineroar")],
                        required_pokemon="Farigiraf", team_size=2)
    assert teams and all(not unmet_dependencies(team.members) for team in teams)


def test_costar_can_copy_ability_or_seed_boosts_but_not_its_own(species):
    flamigo = choose(species, "Flamigo")
    flamigo.pokemon_sets[0].scenario_tags = ["costar"]
    assert not unmet_dependencies([flamigo, choose(species, "Zamazenta-Crowned")])
    assert not unmet_dependencies([flamigo, choose(species, "Farigiraf"), choose(species, "Miraidon")])
    flamigo.pokemon_sets[0].move4 = "Bulk Up"
    assert "boosted ally" in unmet_dependencies([flamigo])


def test_species_variants_cannot_jointly_supply_incompatible_requirements(species):
    # Only one of Tornadus's weather variants can be selected on a team.
    charizard = choose(species, "Charizard")
    archaludon = choose(species, "Archaludon")
    tornado = species["Tornadus"]
    variants = []
    for candidate in [charizard, archaludon, tornado]:
        for pokemon in candidate.pokemon_sets:
            c = deepcopy(candidate)
            c.pokemon_sets = [pokemon]
            variants.append(c)
    plan = DependencyPlan(variants)
    state = plan.add(plan.add(DependencyState(), variants[0]), variants[1])
    assert not plan.can_complete(state, 1)
    assert not plan.can_complete(state, 2)


def test_optional_weather_benefits_and_historical_team_tags_are_not_requirements(species):
    assert "rain" not in set_dependencies(species["Landorus"].pokemon_sets[0]).requires
    assert "rain" not in set_dependencies(species["Urshifu-Rapid-Strike"].pokemon_sets[0]).requires
    sample = choose(species, "Miraidon", "miraidon:sample-213e3ea68ce95c50")
    assert not set_dependencies(sample.pokemon_sets[0]).requires
    attacker = choose(species, "Maushold", "maushold:population-bomb-attacker")
    assert "Rage Fist or Justified partner" not in set_dependencies(attacker.pokemon_sets[0]).requires
    attacker = choose(species, "Maushold", "maushold:population-bomb-attacker")
    assert "Rage Fist or Justified partner" not in set_dependencies(attacker.pokemon_sets[0]).requires


def test_one_set_can_supply_its_own_field(species):
    indeedee = choose(species, "Indeedee")
    assert not unmet_dependencies([indeedee])
    assert beam_search([indeedee], required_pokemon="Indeedee", team_size=1)


@pytest.mark.parametrize("style", [None, "rain trick room", "sun", "psyspam", "commander"])
def test_finished_production_teams_always_satisfy_dependencies(species, style):
    teams = beam_search(list(species.values()), team_prompt=style, beam_width=25, top_k=3)
    assert teams
    assert all(not unmet_dependencies(team.members) for team in teams)
