'use strict';

// Optional developer QA using the pinned official Pokemon Showdown npm distribution.
// Setup: python fetch_validator.py; python export_sets.py; node verify_showdown.cjs
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { TeamValidator } = require('./.tools/pokemon-showdown/package/dist/sim/team-validator');
const { Teams } = require('./.tools/pokemon-showdown/package/dist/sim/teams');
const { Dex } = require('./.tools/pokemon-showdown/package/dist/sim/dex');
const root = process.argv[2] || path.join(__dirname, 'data', 'smogon');
const input = fs.readFileSync(path.join(root, 'candidate_sets.json'));
const document = JSON.parse(input);
if (document.format !== 'gen9doublesubers') throw new Error('Wrong format');
const validator = new TeamValidator(document.format);
const clone = value => JSON.parse(JSON.stringify(value));
const results = document.sets.map((set, index) => ({
  set_id: document.set_ids[index],
  species: set.species,
  errors: validator.validateSet(clone(set), {}) || [],
}));
// Regression controls prove the validator is enforcing move legality and EV limits.
const pursuit = clone(document.sets.find(set => set.species === 'Tyranitar') || document.sets[0]);
pursuit.moves[0] = 'Pursuit';
const tooManyEvs = clone(document.sets[0]);
tooManyEvs.evs.hp = 252;
tooManyEvs.evs.def = 252;
tooManyEvs.evs.spd = 252;
const controls = {
  unavailable_pursuit: validator.validateSet(pursuit, {}) || [],
  excessive_evs: validator.validateSet(tooManyEvs, {}) || [],
};
// Also verify that every separate text team preserves the same set after import.
const text = fs.readFileSync(path.join(root, 'candidate_sets.txt'), 'utf8');
const imported = text.split(/^=== .* ===\s*$/m).map(block => Teams.import(block.trim())).filter(team => team?.length);
const roundtrip = imported.length === document.sets.length && imported.every((team, index) => {
  if (team.length !== 1) return false;
  const expected = document.sets[index];
  const actual = team[0];
  return ['species', 'ability', 'teraType', 'item', 'nature', 'level'].every(field => actual[field] === expected[field]) &&
    JSON.stringify(actual.moves) === JSON.stringify(expected.moves) &&
    ['hp', 'atk', 'def', 'spa', 'spd', 'spe'].every(stat => actual.evs[stat] === expected.evs[stat] && actual.ivs[stat] === expected.ivs[stat]) &&
    !validator.validateSet(clone(team[0]), {});
});
const illegal = results.filter(result => result.errors.length);
if (illegal.length) throw new Error(JSON.stringify(illegal, null, 2));
// Cover every variant in a six-member context and each pair of species
// representatives. Cross-variant combinations grow quadratically.
let teamsChecked = 0, pairsChecked = 0;
const base = set => Dex.species.get(set.species).baseSpecies;
const representatives = [...new Map(document.sets.map(set => [set.species, set])).values()];
for (let i = 0; i < document.sets.length; i++) {
  const team = [document.sets[i]], used = new Set([base(document.sets[i])]);
  for (const set of document.sets) {
    if (team.length === 6) break;
    if (!used.has(base(set))) { team.push(set); used.add(base(set)); }
  }
  const errors = validator.validateTeam(clone(team));
  if (errors) throw new Error(JSON.stringify(errors));
  teamsChecked++;
}
for (let i = 0; i < representatives.length; i++) {
  for (let j = i + 1; j < representatives.length; j++) {
    if (base(representatives[i]) === base(representatives[j])) continue;
    const errors = validator.validateTeam(clone([representatives[i], representatives[j]]));
    if (errors) throw new Error(JSON.stringify(errors));
    pairsChecked++;
  }
}
const report = {
  format: document.format,
  validator_version: require('./.tools/pokemon-showdown/package/package.json').version,
  input_sha256: crypto.createHash('sha256').update(input).digest('hex'),
  text_sha256: crypto.createHash('sha256').update(text).digest('hex'),
  results, controls, importable_roundtrip: roundtrip, six_member_teams_checked: teamsChecked,
  cross_species_pairs_checked: pairsChecked,
  team_check_method: 'Every variant in a six-member context; each cross-base-species pair of species/form representatives.',
};
fs.writeFileSync(path.join(root, 'legality_report.json'), JSON.stringify(report, null, 2) + '\n');
if (results.some(result => result.errors.length) || Object.values(controls).some(errors => !errors.length) || !roundtrip) {
  throw new Error(JSON.stringify(report, null, 2));
}
console.log(`Validated ${results.length} sets, ${pairsChecked} cross-species pairs and ${teamsChecked} six-member contexts in ${document.format}; rejected both invalid controls; text round trip passed.`);
