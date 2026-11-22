from copy import deepcopy
import io
import contextlib
import unittest

from models import Candidate, teampool

from scoring import (
    normalize, penalty_component, pool_usage_bounds, role_component,
    score_addition, synergy_component, usage_component,
)


class ScoringTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(normalize(15, 10, 20), 0.5)
        self.assertEqual(normalize(10, 10, 20), 0.0)
        self.assertEqual(normalize(20, 10, 20), 1.0)
        self.assertEqual(normalize(5, 10, 20), 0.0)
        self.assertEqual(normalize(25, 10, 20), 1.0)
        self.assertEqual(normalize(10, 10, 10), 0.0)
        with self.assertRaises(ValueError):
            normalize(15, 20, 10)

    def test_bounds_and_usage_across_real_pool(self):
        bounds = pool_usage_bounds(iter(teampool))
        self.assertLess(bounds[0], 0.0034655352)
        self.assertEqual(bounds[1], 0.2508367748)
        pool = {candidate.pokemon_sets[0].name: candidate for candidate in teampool}
        self.assertEqual(usage_component(pool["Incineroar"], *bounds), 1.0)
        self.assertEqual(usage_component(min(teampool, key=lambda candidate: candidate.usage), *bounds), 0.0)
        self.assertEqual(pool_usage_bounds([Candidate(usage=0.2)]), (0.2, 0.2))
        with self.assertRaises(ValueError):
            pool_usage_bounds([])

    def test_synergy_empty_missing_and_negative(self):
        miraidon = deepcopy(teampool[0])
        miraidon.teammate_values = {"Charizard": 0.5}
        charizard = deepcopy(teampool[1])
        charizard.teammate_values = {"Miraidon": 0.5}
        self.assertEqual(synergy_component(miraidon, []), 0.0)
        self.assertEqual(synergy_component(miraidon, [charizard]), 0.5)
        self.assertEqual(synergy_component(miraidon, [charizard, Candidate()]), 0.25)
        unrelated = deepcopy(teampool[2])
        unrelated.teammate_values = {}
        self.assertEqual(synergy_component(miraidon, [unrelated]), 0.0)
        negative = Candidate(teammate_values={"Miraidon": -0.3})
        self.assertEqual(synergy_component(negative, [miraidon]), -0.3)

    def test_role_rewards_only_uncovered_targets(self):
        candidate = Candidate(role_tags=["Lead", "Support", "Support", "Other"])
        team = [Candidate(role_tags=["Lead"])]
        self.assertEqual(role_component(candidate, team, ["Lead", "Support"]), 1.0)
        self.assertEqual(role_component(candidate, [], ["Lead", "Support"]), 2.0)
        self.assertEqual(role_component(candidate, team, []), 0.0)

    def test_penalty_counts_shared_roles_per_member(self):
        candidate = Candidate(role_tags=["Lead", "Support", "Support"])
        team = [Candidate(role_tags=["Lead", "Support", "Support"]),
                Candidate(role_tags=["Support"])]
        self.assertEqual(penalty_component(candidate, team), -3.0)
        self.assertEqual(penalty_component(candidate, []), 0.0)

    def test_weighted_components_purity_and_no_output(self):
        candidate = Candidate(usage=0.3, role_tags=["Lead", "Support"],
                              teammate_values={"Charizard": 0.5})
        team = [deepcopy(teampool[1])]
        team[0].role_tags = ["Lead"]
        weights = {"usage": 0.3, "synergy": 0.7, "role": 0.5, "penalty": 0.2}
        targets = ["Lead", "Support"]
        before = deepcopy((candidate, team, weights, targets))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = score_addition(candidate, team, weights, 0.0, 0.3, targets)
        self.assertEqual(set(result), {"usage", "synergy", "role", "penalty", "total"})
        for key, expected in {"usage": 0.3, "synergy": 0.35,
                              "role": 0.5, "penalty": -0.2, "total": 0.95}.items():
            self.assertIsInstance(result[key], float)
            self.assertAlmostEqual(result[key], expected)
        self.assertEqual((candidate, team, weights, targets), before)
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
