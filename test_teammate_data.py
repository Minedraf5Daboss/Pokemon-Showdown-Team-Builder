from pathlib import Path
import json
import unittest

from models import teampool
from verify_teammates import audit, usage_rows
from verify_sets import verify_sets


class TeammateDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).parent / "data" / "smogon"
        cls.report = audit(cls.root)
        cls.application = json.loads((cls.root / "applied_teammate_values.json").read_text())
        cls.recommendations = json.loads((cls.root / "set_recommendations.json").read_text())
        cls.evidence = verify_sets(cls.root)
        cls.usage_tables = [usage_rows((cls.root / month / "gen9doublesubers-0.txt").read_text())
                            for month in cls.report["months"]]

    def test_imported_values_match_verified_counts(self):
        self.assertEqual(self.report["battles"], 194198)
        self.assertGreater(self.report["published_moveset_percentage_checks"], 3000)
        count = len(teampool)
        self.assertEqual(sum(map(len, self.application["verified_values"].values())) +
                         sum(map(len, self.application["unfilled"].values())), count * (count - 1))
        for candidate in teampool:
            name = candidate.pokemon_sets[0].name
            for teammate, value in self.application["verified_values"][name].items():
                pair = self.report["pairs"][name][teammate]
                self.assertEqual(pair["status"], "verified")
                self.assertEqual(candidate.teammate_values[teammate], value)
                self.assertAlmostEqual(value, pair["pair_count"] / pair["candidate_count"], places=9)
            self.assertEqual(self.application["preserved_manual"][name], {})
            unavailable = {other for other, pair in self.report["pairs"][name].items()
                           if pair["status"] == "unavailable"}
            self.assertEqual(set(self.application["unfilled"][name]), unavailable)
            self.assertTrue(unavailable.isdisjoint(candidate.teammate_values))

    def test_direction_uses_each_candidates_own_denominator(self):
        forward = self.report["pairs"]["Garchomp"]["Tyranitar"]
        reverse = self.report["pairs"]["Tyranitar"]["Garchomp"]
        self.assertEqual(forward["pair_count"], 1919)
        self.assertEqual(reverse["pair_count"], 1919)
        self.assertEqual(forward["candidate_count"], 11866)
        self.assertEqual(reverse["candidate_count"], 8247)
        self.assertNotEqual(forward["value"], reverse["value"])

    def test_complete_expanded_species_coverage_and_every_pair_direction(self):
        names = {candidate.pokemon_sets[0].name for candidate in teampool}
        qualifying = set()
        for month in self.recommendations["months"]:
            for cutoff in (1500, 1630):
                qualifying.update(name for name, row in usage_rows((self.root / month / f"gen9doublesubers-{cutoff}.txt").read_text()).items()
                                  if row["usage"] >= .0001)
        self.assertEqual(len(qualifying), 535)
        self.assertEqual(qualifying, names)
        self.assertTrue({"Urshifu-Rapid-Strike", "Calyrex-Ice", "Zamazenta-Crowned", "Koraidon", "Magearna"} <= names)
        self.assertTrue({"Iron Crown", "Pikachu", "Squirtle"} <= names)
        self.assertTrue(names.isdisjoint({"Bulbasaur", "Togekiss"}))
        for candidate in teampool:
            name = candidate.pokemon_sets[0].name
            self.assertEqual(set(candidate.teammate_values) | set(self.application["unfilled"][name]), names - {name})

    def test_every_variant_matches_recommendations_and_real_species_frequency(self):
        pool = {candidate.pokemon_sets[0].name: candidate for candidate in teampool}
        self.assertEqual(set(self.evidence), set(pool))
        ids = set()
        for name, candidate in pool.items():
            selected = self.recommendations["species"][name]
            self.assertEqual(candidate.usage_reports, selected["usage_reports"])
            self.assertEqual(len(candidate.pokemon_sets), len(selected["sets"]))
            for pokemon, build in zip(candidate.pokemon_sets, selected["sets"]):
                self.assertNotIn(pokemon.set_id, ids)
                ids.add(pokemon.set_id)
                for field in ("ability", "tera_Type", "held_Item", "nature", "set_id", "label", "scenario_tags", "role_tags", "source_urls", "team_notes"):
                    self.assertEqual(getattr(pokemon, field), build[field])
                self.assertEqual(list(pokemon.moves), build["moves"])
                self.assertEqual({key.removesuffix("_EV"): value for key, value in vars(pokemon.evs).items()}, build["evs"])
                self.assertEqual({key.removesuffix("_IV"): value for key, value in vars(pokemon.ivs).items()}, build["ivs"])
                self.assertEqual(pokemon.level, 100)
            appearances = sum(rows.get(name, {}).get("raw", 0) for rows in self.usage_tables)
            self.assertAlmostEqual(candidate.usage, appearances / (2 * self.report["battles"]), places=9)
            self.assertEqual(candidate.usage_appearances, appearances)
        self.assertEqual(len(ids), sum(len(record["sets"]) for record in self.recommendations["species"].values()))

    def test_new_species_have_mechanically_distinct_weather_and_room_options(self):
        pool = {candidate.pokemon_sets[0].name: candidate for candidate in teampool}
        for name, scenario, check in (
            ("Bronzong", "trick_room", lambda s: "Trick Room" in s.moves),
            ("Kingdra", "rain", lambda s: s.ability == "Swift Swim"),
            ("Ninetales", "sun", lambda s: s.ability == "Drought"),
        ):
            self.assertTrue(any(scenario in s.scenario_tags and check(s) for s in pool[name].pokemon_sets))
        self.assertGreater(len(pool["Iron Crown"].pokemon_sets), 1)

    def test_sample_and_inferred_evidence_are_distinguished(self):
        self.assertTrue(any(s["evidence_status"] == "published_smogon_dubers_sample"
                            for row in self.evidence.values() for s in row["sets"]))
        self.assertTrue(any(s["evidence_status"] == "dubers_usage_informed"
                            for row in self.evidence.values() for s in row["sets"]))
        for name in ("Charizard", "Gardevoir", "Gengar", "Lucario"):
            self.assertEqual(self.evidence[name]["sets"][0]["evidence_status"], "experimental")


if __name__ == "__main__":
    unittest.main()
