"""Implemented search checks; the separate exercise scaffold stays available."""

from copy import deepcopy
from itertools import permutations

import pytest

from models import Candidate, DEFAULT_TARGET_ROLES, build_pool, parse_restricted_mons
from scoring import pool_usage_bounds, score_addition
from search import (
    COMPONENTS, DEFAULT_SEED_WEIGHT, DEFAULT_WEIGHTS, PoolTooSmallError, RequiredAlsoExcludedError,
    RequiredNotInPoolError, SearchError, beam_search, name_of,
)
from scratch_runner import main
from team_dependencies import set_dependencies, unmet_dependencies


@pytest.fixture
def pool():
    # Single-set backward compatibility; multi-set cases live in test_set_variants.py.
    candidates = build_pool()
    for candidate in candidates:
        candidate.pokemon_sets = candidate.pokemon_sets[:1]
    return candidates


@pytest.fixture
def tiny_pool(pool):
    # Synthetic A/B/C data makes the arithmetic independent of Smogon updates.
    candidates = deepcopy(pool[:3])
    for candidate, name, usage in zip(candidates, "ABC", (0.8, 0.6, 0.2)):
        candidate.pokemon_sets[0].name = name
        candidate.base_species = ""
        candidate.pokemon_sets[0].role_tags = []
        candidate.pokemon_sets[0].set_id = ""
        candidate.pokemon_sets[0].ability = "Pressure"
        candidate.pokemon_sets[0].scenario_tags = []
        candidate.pokemon_sets[0].move1 = "Tackle"
        candidate.pokemon_sets[0].move2 = candidate.pokemon_sets[0].move3 = candidate.pokemon_sets[0].move4 = ""
        candidate.usage = usage
        candidate.role_tags = []
    candidates[0].teammate_values = {"B": 0.1, "C": 0.9}
    candidates[1].teammate_values = {"A": 0.1, "C": 0.2}
    candidates[2].teammate_values = {"A": 0.9, "B": 0.2}
    return candidates


TRACE_WEIGHTS = {"usage": 1.0, "synergy": 1.0, "role": 0.0, "penalty": 0.0}


@pytest.mark.parametrize("required", [None, "  amoonguss  "])
def test_finished_teams_obey_request_and_keep_stats(pool, required):
    results = beam_search(pool, required_pokemon=required, excluded_pokemon="Miraidon, Urshifu")
    assert len(results) == 3
    member_sets = []
    for team in results:
        names = frozenset(map(name_of, team.members))
        assert len(team.members) == len(names) == 6
        assert not unmet_dependencies(team.members)
        assert names.isdisjoint({"Miraidon", "Urshifu"})
        if required:
            assert name_of(team.members[0]) == "Amoonguss"
            assert team.addition_breakdowns[0] == dict.fromkeys(COMPONENTS, 0.0)
        assert all(member.pokemon_sets[0].evs is not None for member in team.members)
        assert all(member.pokemon_sets[0].ivs is not None for member in team.members)
        member_sets.append(names)
    assert len(set(member_sets)) == len(results)
    totals = [team.breakdown["total"] for team in results]
    assert totals == sorted(totals, reverse=True)


def test_breakdowns_replay_additions_and_pool_is_unchanged(pool):
    before = deepcopy(pool)
    weights, roles, excluded = dict(DEFAULT_WEIGHTS), list(DEFAULT_TARGET_ROLES), ["Urshifu"]
    settings_before = deepcopy((weights, roles, excluded))
    results = beam_search(pool, required_pokemon="Amoonguss", weights=weights,
                          target_roles=roles, excluded_pokemon=excluded)
    for team in results:
        assert len(team.addition_breakdowns) == len(team.members)
        for index in range(1, 6):
            expected = score_addition(team.members[index], team.members[:index], weights,
                                      *pool_usage_bounds(pool), roles,
                                      required_pokemon="Amoonguss", seed_weight=DEFAULT_SEED_WEIGHT)
            assert team.addition_breakdowns[index] == pytest.approx(expected)
        for component in COMPONENTS:
            assert team.breakdown[component] == pytest.approx(
                sum(step[component] for step in team.addition_breakdowns))
        assert team.breakdown["total"] == pytest.approx(
            sum(team.breakdown[component] for component in COMPONENTS[:-1]))
    assert pool == before
    assert (weights, roles, excluded) == settings_before


def test_required_seed_alone_starts_at_zero(pool):
    result = beam_search(pool, required_pokemon="Miraidon", team_size=1)
    assert len(result) == 1
    assert list(map(name_of, result[0].members)) == ["Miraidon"]
    assert result[0].breakdown == dict.fromkeys(COMPONENTS, 0.0)


def test_empty_start_scores_first_pick(tiny_pool):
    results = beam_search(tiny_pool, team_size=1, weights=TRACE_WEIGHTS)
    assert [name_of(team.members[0]) for team in results] == ["A", "B", "C"]
    assert [team.breakdown["total"] for team in results] == pytest.approx([1, 2 / 3, 0])


def test_slot_two_matches_hand_trace_and_deduplicates_before_pruning(tiny_pool):
    results = beam_search(tiny_pool, team_size=2, beam_width=2, weights=TRACE_WEIGHTS)
    # Slot 1 keeps A=1 and B=2/3. Expansions: AC=1.9, AB=BA=1+2/3+.1,
    # BC=2/3+.2. AB and BA share one slot, leaving two distinct teams.
    assert [list(map(name_of, team.members)) for team in results] == [["A", "C"], ["A", "B"]]
    assert [team.breakdown["total"] for team in results] == pytest.approx([1.9, 1 + 2 / 3 + .1])


def test_duplicate_full_team_keeps_highest_running_score(tiny_pool):
    results = beam_search(tiny_pool, team_size=3, beam_width=6, weights=TRACE_WEIGHTS)
    assert len(results) == 1
    # AC then B: 1 + .9 + 2/3 + mean(.1, .2) = 163/60.
    assert results[0].breakdown["total"] == pytest.approx(163 / 60)
    all_scores = []
    for ordering in permutations(tiny_pool):
        score = sum(score_addition(candidate, ordering[:index], TRACE_WEIGHTS,
                                   *pool_usage_bounds(tiny_pool), [])['total']
                    for index, candidate in enumerate(ordering))
        all_scores.append(score)
    assert results[0].breakdown["total"] == pytest.approx(max(all_scores))


def test_duplicate_best_paths_do_not_consume_two_beam_slots(tiny_pool):
    tiny_pool[0].teammate_values = {"B": 0.9, "C": 0.1}
    tiny_pool[1].teammate_values["A"] = 0.9
    tiny_pool[2].teammate_values["A"] = 0.1
    results = beam_search(tiny_pool, team_size=2, beam_width=2, weights=TRACE_WEIGHTS)
    # AB and BA are the top two paths. Merging after trimming would lose AC.
    assert [set(map(name_of, team.members)) for team in results] == [{"A", "B"}, {"A", "C"}]


@pytest.mark.parametrize("required", [None, "Miraidon"])
def test_width_one_matches_independent_greedy_search(pool, required):
    # Isolate score ordering from the separately tested prerequisite planner.
    pool = [c for c in pool if not set_dependencies(c.pokemon_sets[0]).requires]
    members = [] if required is None else [next(c for c in pool if name_of(c) == required)]
    greedy_total = 0.0
    while len(members) < 6:
        choices = []
        for candidate in pool:
            if any((candidate.base_species or name_of(candidate)) == (member.base_species or name_of(member)) for member in members):
                continue
            addition = score_addition(candidate, members, DEFAULT_WEIGHTS,
                                      *pool_usage_bounds(pool), DEFAULT_TARGET_ROLES,
                                      required_pokemon=required, seed_weight=DEFAULT_SEED_WEIGHT)
            choices.append((addition['total'], name_of(candidate), candidate))
        gain, _, chosen = sorted(choices, key=lambda choice: (-choice[0], choice[1]))[0]
        members.append(chosen)
        greedy_total += gain
    results = beam_search(pool, required_pokemon=required, beam_width=1)
    assert len(results) == 1
    assert list(map(name_of, results[0].members)) == list(map(name_of, members))
    assert results[0].breakdown['total'] == pytest.approx(greedy_total)


def test_runs_are_deterministic_including_ties(tiny_pool):
    for candidate in tiny_pool:
        candidate.usage = 0.5
        candidate.teammate_values = {}
    expected = beam_search(tiny_pool, team_size=2)
    assert expected == beam_search(tiny_pool, team_size=2)
    assert expected == beam_search(reversed(tiny_pool), team_size=2)
    assert [list(map(name_of, team.members)) for team in expected] == [["A", "B"], ["A", "C"], ["B", "C"]]


def test_restrictions_normalize_names_and_reject_typos(pool):
    assert parse_restricted_mons("  miraidon, URSHIFU, ,Miraidon ", pool) == {"Miraidon", "Urshifu"}
    assert parse_restricted_mons(" , ", pool) == set()
    with pytest.raises(ValueError, match="miradon"):
        beam_search(pool, excluded_pokemon="miradon")


def test_specific_request_errors(pool):
    with pytest.raises(RequiredNotInPoolError, match="Missing"):
        beam_search(pool, required_pokemon="Missing")
    with pytest.raises(RequiredAlsoExcludedError, match="Amoonguss"):
        beam_search(pool, required_pokemon="amoonguss", excluded_pokemon=["AMOONGUSS"])
    with pytest.raises(PoolTooSmallError) as error:
        beam_search(pool[:5])
    assert (error.value.available, error.value.required) == (5, 6)
    with pytest.raises(PoolTooSmallError):
        beam_search(pool, excluded_pokemon=[name_of(c) for c in pool[5:]])
    with pytest.raises(PoolTooSmallError):
        beam_search([])
    assert issubclass(RequiredNotInPoolError, SearchError)


@pytest.mark.parametrize("options", [
    {"beam_width": 0}, {"beam_width": True}, {"beam_width": 1.5},
    {"top_k": 0}, {"team_size": 0}, {"team_size": 7},
    {"weights": {}}, {"weights": {**DEFAULT_WEIGHTS, "usage": float('nan')}},
    {"weights": {**DEFAULT_WEIGHTS, "penalty": -1}}, {"target_roles": ["Fake role"]},
])
def test_invalid_search_settings_are_rejected(pool, options):
    with pytest.raises(ValueError):
        beam_search(pool, **options)


def test_malformed_and_duplicate_candidates_are_rejected(pool):
    with pytest.raises(ValueError, match="named"):
        beam_search([Candidate()])
    with pytest.raises(ValueError, match="duplicate"):
        beam_search(pool + [deepcopy(pool[0])])


@pytest.mark.parametrize("required", [None, "A"])
def test_duplicate_items_are_skipped_before_scoring(tiny_pool, required):
    tiny_pool[0].pokemon_sets[0].held_Item = "Choice Scarf"
    tiny_pool[1].pokemon_sets[0].held_Item = "  CHOICE SCARF  "
    tiny_pool[2].pokemon_sets[0].held_Item = "Leftovers"
    before = deepcopy(tiny_pool)
    results = beam_search(tiny_pool, required_pokemon=required, team_size=2,
                          weights=TRACE_WEIGHTS, unique_items=True)
    expected = [{"A", "C"}] if required else [{"A", "C"}, {"B", "C"}]
    assert [set(map(name_of, team.members)) for team in results] == expected
    assert tiny_pool == before


def test_conflicting_items_cannot_produce_a_finished_team(tiny_pool):
    for candidate in tiny_pool:
        candidate.pokemon_sets[0].held_Item = "Leftovers"
    with pytest.raises(ValueError, match="item policy"):
        beam_search(tiny_pool, team_size=2, unique_items=True)


def test_doubles_ubers_allows_duplicate_items_by_default(tiny_pool):
    for candidate in tiny_pool:
        candidate.pokemon_sets[0].held_Item = "Leftovers"
    teams = beam_search(tiny_pool, team_size=3)
    assert len(teams) == 1 and len(teams[0].members) == 3


def test_multiple_pokemon_can_hold_no_item(tiny_pool):
    for candidate in tiny_pool:
        candidate.pokemon_sets[0].held_Item = ""
    assert len(beam_search(tiny_pool, team_size=3)) == 1


def test_duplicate_pokemon_names_ignore_case_and_surrounding_spaces(tiny_pool):
    duplicate = deepcopy(tiny_pool[0])
    duplicate.pokemon_sets[0].name = "  a  "
    with pytest.raises(ValueError, match="duplicate Pokemon"):
        beam_search(tiny_pool + [duplicate], team_size=2)


def test_exactly_six_eligible_candidates_produce_one_team(pool):
    # Keep six self-contained real builds so the restricted pool is feasible.
    selected = {}
    for candidate in pool:
        if not set_dependencies(candidate.pokemon_sets[0]).requires:
            selected.setdefault(candidate.base_species, candidate)
    names = {name_of(c) for c in list(selected.values())[:6]}
    results = beam_search(pool, excluded_pokemon=[name_of(c) for c in pool if name_of(c) not in names])
    assert len(results) == 1
    assert set(map(name_of, results[0].members)) == names


def test_runner_prints_real_results_and_restrictions(capsys):
    assert main(["--required", "none", "--exclude", "Miraidon,Urshifu"]) == 0
    output = capsys.readouterr().out
    assert output.count("Team ") == 3
    assert "Miraidon" not in output and "Urshifu @" not in output
    for label in ("Total:", "Usage:", "Synergy:", "Role:", "Penalty:"):
        assert label in output


def test_runner_interactive_restrictions_and_clear_errors(monkeypatch, capsys):
    monkeypatch.setattr('builtins.input', lambda prompt: 'Miraidon,Urshifu')
    assert main(["--interactive"]) == 0
    assert "Miraidon" not in capsys.readouterr().out
    assert main(["--exclude", "Amoonguss"]) == 2
    assert "Required Pokemon also excluded: Amoonguss" in capsys.readouterr().err


def test_old_exception_imports_catch_the_same_errors(pool):
    from exceptions import RequiredPokemonNotInPoolError
    assert RequiredPokemonNotInPoolError is RequiredNotInPoolError
    with pytest.raises(RequiredPokemonNotInPoolError):
        beam_search(pool, required_pokemon="Missing")
