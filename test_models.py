"""Stat guards and regression binding to the executed official legality audit."""

import contextlib
import hashlib
import io
import json
from pathlib import Path
import runpy
import unittest

import models
from export_sets import importable, pool_document, showdown_set
from models import PokemonSet


class ModelTests(unittest.TestCase):
    def test_ev_limits_and_integer_stats(self):
        for values in [(-4, 0, 0, 0, 0, 0), (256, 0, 0, 0, 0, 0),
                       (252, 252, 252, 0, 0, 0), (4.0, 0, 0, 0, 0, 0),
                       (True, 0, 0, 0, 0, 0)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                PokemonSet.EV(*values)
        self.assertEqual(PokemonSet.EV(252, 252, 6, 0, 0, 0).def_EV, 6)

    def test_iv_limits(self):
        for value in [-1, 32, 31.0, True]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                PokemonSet.IV(atk_IV=value)
        self.assertEqual(PokemonSet.IV(atk_IV=0, spe_IV=0).spe_IV, 0)

    def test_export_requires_explicit_spreads(self):
        missing = PokemonSet("Miraidon", "Hadron Engine", "Fairy", "Choice Scarf",
                             "Electro Drift", "Dazzling Gleam", "Volt Switch", "Draco Meteor", "Timid")
        with self.assertRaisesRegex(ValueError, "Explicit EVs and IVs"):
            showdown_set(missing)

    def test_current_models_match_executed_showdown_legality_audit(self):
        root = Path(__file__).parent / "data" / "smogon"
        source = (root / "candidate_sets.json").read_bytes()
        text = (root / "candidate_sets.txt").read_bytes()
        self.assertEqual(pool_document(), json.loads(source))
        expected_text = "\n\n".join(
            f"=== [{models.FORMAT_ID}] {pokemon.name} - {pokemon.label} ({pokemon.set_id}) ===\n\n{importable(pokemon)}"
            for candidate in models.teampool for pokemon in candidate.pokemon_sets) + "\n"
        self.assertEqual(text.decode().replace("\r\n", "\n"), expected_text)
        report = json.loads((root / "legality_report.json").read_text())
        self.assertEqual(report["format"], models.FORMAT_ID)
        self.assertEqual(report["input_sha256"], hashlib.sha256(source).hexdigest())
        self.assertEqual(report["text_sha256"], hashlib.sha256(text).hexdigest())
        set_count = sum(len(candidate.pokemon_sets) for candidate in models.teampool)
        self.assertEqual(len(report["results"]), set_count)
        self.assertTrue(all(not entry["errors"] for entry in report["results"]))
        self.assertEqual(report["six_member_teams_checked"], set_count)
        base_species = [candidate.base_species for candidate in models.teampool]
        expected_pairs = sum(first != second for i, first in enumerate(base_species)
                             for second in base_species[i + 1:])
        self.assertEqual(report["cross_species_pairs_checked"], expected_pairs)
        self.assertTrue(report["importable_roundtrip"])
        self.assertTrue(any("Pursuit" in error for error in report["controls"]["unavailable_pursuit"]))
        self.assertTrue(any("510" in error for error in report["controls"]["excessive_evs"]))

    def test_import_has_no_stdout_side_effect(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            runpy.run_path(str(Path(models.__file__)))
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
