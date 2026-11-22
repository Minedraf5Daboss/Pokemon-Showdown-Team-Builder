"""Regression coverage for real multi-set production data and selected builds."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from models import build_pool, set_key, set_variants, validate_pool
from search import beam_search, PoolTooSmallError
from export_sets import showdown_set
from scratch_runner import main
from team_styles import set_features, speed_mode


@pytest.fixture
def species():
    return {candidate.pokemon_sets[0].name: candidate for candidate in build_pool()}


def test_variants_share_species_evidence_but_have_distinct_set_identity(species):
    original = species["Miraidon"]
    before = deepcopy(original)
    variants = set_variants([original])
    assert len(variants) >= 3
    assert len({set_key(choice) for choice in variants}) == len(variants)
    for choice in variants:
        assert len(choice.pokemon_sets) == 1
        assert choice.usage == original.usage
        assert choice.teammate_values == original.teammate_values
        assert choice.role_tags == choice.pokemon_sets[0].role_tags
    variants[0].pokemon_sets[0].move1 = "Protect"
    assert original == before


def test_search_can_choose_a_nonfirst_set_to_resolve_item_conflict(species):
    miraidon = species["Miraidon"]
    miraidon.pokemon_sets = [s for s in miraidon.pokemon_sets if s.set_id in {
        "miraidon:choice-specs-attacker", "miraidon:choice-scarf-pivot"}]
    gholdengo = species["Gholdengo"]
    gholdengo.pokemon_sets = [s for s in gholdengo.pokemon_sets if s.held_Item == "Choice Specs"]
    assert miraidon.pokemon_sets[0].held_Item == gholdengo.pokemon_sets[0].held_Item == "Choice Specs"
    before = deepcopy([miraidon, gholdengo])
    teams = beam_search([miraidon, gholdengo], required_pokemon="Miraidon", team_size=2, unique_items=True)
    assert teams
    assert all(team.members[0].pokemon_sets[0].set_id == "miraidon:choice-scarf-pivot" for team in teams)
    assert [miraidon, gholdengo] == before


def test_required_species_explores_variants_and_optional_set_pin(species):
    miraidon = species["Miraidon"]
    teams = beam_search([miraidon], required_pokemon="Miraidon", team_size=1, top_k=20)
    assert len(teams) == len(miraidon.pokemon_sets)
    assert len({set_key(team.members[0]) for team in teams}) == len(teams)
    selected = "miraidon:flexible-attacker"
    pinned = beam_search([miraidon], required_pokemon="Miraidon", required_set_id=selected, team_size=1)
    assert len(pinned) == 1 and pinned[0].members[0].pokemon_sets[0].set_id == selected
    with pytest.raises(ValueError, match="Required set ID"):
        beam_search([miraidon], required_pokemon="Miraidon", required_set_id="missing", team_size=1)
    with pytest.raises(ValueError, match="requires required_pokemon"):
        beam_search([miraidon], required_set_id=selected, team_size=1)


def test_forms_and_variants_cannot_bypass_species_clause(species):
    pool = [species[name] for name in ("Calyrex-Shadow", "Calyrex-Ice", "Incineroar")]
    with pytest.raises(PoolTooSmallError):
        beam_search(pool, team_size=3)
    teams = beam_search(pool, team_size=2, top_k=20)
    assert all(len({member.base_species for member in team.members}) == 2 for team in teams)


def test_set_roles_remain_specific_to_selected_build(species):
    variants = set_variants([species["Groudon"]])
    by_id = {choice.pokemon_sets[0].set_id: choice for choice in variants}
    assert "Setup" not in by_id["groudon:assault-vest-attacker"].role_tags
    assert "Setup" in by_id["groudon:swords-dance-attacker"].role_tags


def test_rain_room_search_selects_slow_variant_and_exports_only_that_set(species):
    teams = beam_search(list(species.values()), required_pokemon="Kyogre", required_set_id="kyogre:slow-rain-attacker",
                        team_prompt="rain trick room", beam_width=30, top_k=1)
    kyogre = teams[0].members[0].pokemon_sets[0]
    assert kyogre.ivs.spe_IV == 0 and kyogre.nature == "Quiet"
    assert showdown_set(kyogre)["item"] == "Mystic Water"
    assert any("Trick Room" in member.pokemon_sets[0].moves for member in teams[0].members)
    assert all(len(member.pokemon_sets) == 1 for member in teams[0].members)


def test_ditto_exports_one_move_and_duplicate_ids_are_rejected(species):
    assert showdown_set(species["Ditto"].pokemon_sets[0])["moves"] == ["Transform"]
    pool = list(species.values())
    species["Ditto"].pokemon_sets.append(deepcopy(species["Ditto"].pokemon_sets[0]))
    with pytest.raises(ValueError, match="duplicate set IDs"):
        validate_pool(pool)


def test_list_sets_exposes_usable_ids(capsys):
    assert main(["--list-sets", "Kyogre"]) == 0
    assert "kyogre:slow-rain-attacker" in capsys.readouterr().out


def test_armarouge_has_standard_and_trick_room_psychic_variants(species):
    psyspam = [showdown_set(s) for s in species["Armarouge"].pokemon_sets if "Expanding Force" in s.moves]
    groups = {}
    for build in psyspam:
        groups.setdefault(speed_mode(build, set_features(build)), []).append(build)

    assert any(build["evs"]["spe"] >= 180 and "Trick Room" not in build["moves"] for build in groups["standard"])
    assert any(build["ivs"]["spe"] == 0 for build in groups["trick_room_setter"])
    assert any(build["ivs"]["spe"] == 0 for build in groups["trick_room_attacker"])


def test_production_data_and_exports_do_not_import_toy_data():
    root = Path(__file__).parent
    for name in ("models.py", "refresh_pool.py", "recommend_sets.py", "search.py"):
        assert "import toy_data" not in (root / name).read_text()
    recommendations = json.loads((root / "data/smogon/set_recommendations.json").read_text())
    for species in recommendations["species"].values():
        for build in species["sets"]:
            assert build["source_urls"]
            if build["held_Item"] == "Assault Vest":
                assert not set(build["moves"]) & {"Protect", "Trick Room", "Swords Dance", "Calm Mind"}
