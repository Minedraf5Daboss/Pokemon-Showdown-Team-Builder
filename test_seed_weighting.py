"""The required Pokemon gets extra votes when combining pair compatibility."""

from copy import deepcopy

import pytest

from models import build_pool
from scoring import score_addition, synergy_component
from search import DEFAULT_SEED_WEIGHT, beam_search, name_of
from scratch_runner import main


@pytest.fixture
def pool():
    candidates = deepcopy(build_pool()[:4])
    pairs = {
        "A": {"B": .9, "C": .8, "D": .3},
        "B": {"A": .9, "C": 0, "D": .6},
        "C": {"A": .8, "B": 0, "D": 0},
        "D": {"A": .3, "B": .6, "C": 0},
    }
    for name, candidate in zip("ABCD", candidates):
        candidate.pokemon_sets = candidate.pokemon_sets[:1]
        candidate.base_species = ""
        candidate.pokemon_sets[0].role_tags = []
        candidate.pokemon_sets[0].set_id = ""
        candidate.pokemon_sets[0].name = name
        candidate.pokemon_sets[0].ability = "Pressure"
        candidate.pokemon_sets[0].scenario_tags = []
        candidate.pokemon_sets[0].move1 = "Tackle"
        candidate.pokemon_sets[0].move2 = candidate.pokemon_sets[0].move3 = candidate.pokemon_sets[0].move4 = ""
        candidate.usage = .5
        candidate.role_tags = []
        candidate.teammate_values = pairs[name]
    return candidates


def test_seed_weight_matches_hand_calculation_and_is_not_position_based(pool):
    seed, other, candidate, _ = pool
    # The seed is deliberately second: (2 * .8 + 1 * 0) / 3 = 8/15.
    before = deepcopy(pool)
    assert synergy_component(candidate, [other, seed], required_pokemon=" a ", seed_weight=2) == pytest.approx(8 / 15)
    assert synergy_component(candidate, [other, seed], required_pokemon="A", seed_weight=1) == pytest.approx(.4)
    result = score_addition(candidate, [other, seed],
                            {"usage": .3, "synergy": .7, "role": .5, "penalty": .2},
                            .5, .5, [], required_pokemon="A", seed_weight=2)
    assert result == pytest.approx({"usage": 0, "synergy": .7 * 8 / 15,
                                   "role": 0, "penalty": 0, "total": .7 * 8 / 15})
    assert pool == before


def test_default_seed_weight_changes_pick_toward_seed_compatibility(pool):
    weights = {"usage": 0, "synergy": 1, "role": 0, "penalty": 0}
    plain = beam_search(pool, required_pokemon="A", team_size=3, beam_width=1,
                        weights=weights, seed_weight=1)[0]
    anchored = beam_search(pool, required_pokemon="A", team_size=3, beam_width=1,
                           weights=weights)[0]
    assert DEFAULT_SEED_WEIGHT == 2
    # Both pick B first. Equal weighting favors D (.45 versus C's .4).
    # Two seed votes favor C (8/15 versus D's .4).
    assert list(map(name_of, plain.members)) == ["A", "B", "D"]
    assert list(map(name_of, anchored.members)) == ["A", "B", "C"]
    assert anchored.addition_breakdowns[0]["total"] == 0
    assert anchored.addition_breakdowns[2]["synergy"] == pytest.approx(8 / 15)
    assert anchored.breakdown["total"] == pytest.approx(.9 + 8 / 15)


def test_unseeded_search_and_seed_only_average_do_not_change(pool):
    assert beam_search(pool, team_size=3, seed_weight=1) == beam_search(pool, team_size=3, seed_weight=7)
    seed, other, candidate, _ = pool
    assert synergy_component(candidate, [seed], required_pokemon="A", seed_weight=7) == pytest.approx(.8)
    assert synergy_component(candidate, [other, seed], seed_weight=7) == pytest.approx(.4)
    assert synergy_component(candidate, [], required_pokemon="A", seed_weight=7) == 0


@pytest.mark.parametrize("value", [0, -1, .5, float('inf'), float('nan'), True, "2", None])
def test_invalid_seed_weights_are_rejected_even_without_additions(pool, value):
    with pytest.raises(ValueError, match="seed_weight"):
        beam_search(pool, required_pokemon="A", team_size=1, seed_weight=value)
    with pytest.raises(ValueError, match="seed_weight"):
        synergy_component(pool[0], [], seed_weight=value)


def test_runner_accepts_seed_weight_option_and_reports_invalid_values(capsys):
    assert main(["--seed-weight", "1"]) == 0
    output = capsys.readouterr().out
    expected = beam_search(build_pool(), required_pokemon="Amoonguss", seed_weight=1)[0]
    assert f"Total:   {expected.breakdown['total']:.6f}" in output
    assert main(["--seed-weight", "0"]) == 2
    assert "seed_weight" in capsys.readouterr().err
