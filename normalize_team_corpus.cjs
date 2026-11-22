'use strict';

// Normalize with the SAME pinned official simulator used by verify_showdown.cjs.
// Setup: python fetch_validator.py. Run: node normalize_team_corpus.cjs
// Raw exports remain untouched. Legality is checked in each ORIGINAL format.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { Dex } = require('./.tools/pokemon-showdown/package/dist/sim/dex');
const { Teams } = require('./.tools/pokemon-showdown/package/dist/sim/teams');
const { TeamValidator } = require('./.tools/pokemon-showdown/package/dist/sim/team-validator');
const version = require('./.tools/pokemon-showdown/package/package.json').version;
const stats = ['hp', 'atk', 'def', 'spa', 'spd', 'spe'];
const clone = value => JSON.parse(JSON.stringify(value));
const sha256 = value => crypto.createHash('sha256').update(value).digest('hex');

function rawPasteSets(text) {
  const imported = Teams.import(text);
  if (!imported) throw new Error('Cannot import paste');
  // Teams.import fills EV/IV entries. Recover which entries were actually
  // written in the source so default 31 IVs never masquerade as observed IVs.
  const blocks = text.trim().split(/\r?\n\s*\r?\n/).filter(block => /(?:^|\n)- /m.test(block));
  if (blocks.length !== imported.length) throw new Error('Ambiguous paste block boundaries');
  return imported.map((set, i) => {
    const original = clone(set);
    for (const [field, label] of [['evs', 'EVs'], ['ivs', 'IVs']]) {
      const line = blocks[i].split(/\r?\n/).find(line => line.startsWith(label + ':'));
      const written = {};
      for (const entry of (line || '').slice(label.length + 1).split('/')) {
        const match = entry.trim().match(/^(\d+)\s+(HP|Atk|Def|SpA|SpD|Spe)$/i);
        if (match) written[match[2].toLowerCase()] = Number(match[1]);
      }
      original[field] = written;
    }
    if (!/^Level:/m.test(blocks[i])) delete original.level;
    if (!/^Tera Type:/m.test(blocks[i])) delete original.teraType;
    return original;
  });
}

function normalizeSet(original, dex, defaultLevel, adjustedLevel = null) {
  const species = dex.species.get(original.species);
  if (!species.exists) throw new Error('Unknown species: ' + original.species);
  const defaults = [];
  const get = (field, fallback) => {
    if (original[field] !== undefined && original[field] !== '') return original[field];
    defaults.push(field);
    return fallback;
  };
  const set = {
    species: species.name,
    ability: get('ability', species.abilities['0']),
    item: get('item', ''),
    nature: get('nature', 'Serious'),
    level: get('level', defaultLevel),
    moves: original.moves.map(move => {
      const m = dex.moves.get(move);
      if (!m.exists) throw new Error('Unknown move: ' + move);
      return m.name;
    }),
    evs: {}, ivs: {},
  };
  if (dex.gen >= 9) set.teraType = get('teraType', species.types[0]);
  for (const extra of ['gender', 'happiness', 'shiny', 'gigantamax', 'hpType', 'pokeball']) {
    if (original[extra] !== undefined) set[extra] = original[extra];
  }
  for (const stat of stats) {
    for (const [field, fallback] of [['evs', 0], ['ivs', 31]]) {
      const value = original[field]?.[stat];
      if (value === undefined) defaults.push(`${field}.${stat}`);
      set[field][stat] = value === undefined ? fallback : value;
    }
  }
  // VGC exports can declare level 100 and battle at 50. Validating a level-50
  // Koraidon as if it were caught at 50 would falsely trigger event restrictions.
  const battleLevel = adjustedLevel || set.level;
  const nature = dex.natures.get(set.nature);
  const calculated = {};
  for (const stat of stats) {
    const core = Math.floor((2 * species.baseStats[stat] + set.ivs[stat] + Math.floor(set.evs[stat] / 4)) * battleLevel / 100);
    if (stat === 'hp') calculated[stat] = species.baseStats.hp === 1 ? 1 : core + battleLevel + 10;
    else calculated[stat] = Math.floor((core + 5) * (nature.plus === stat ? 110 : nature.minus === stat ? 90 : 100) / 100);
  }
  return {
    set, source_set: original, defaulted_fields: defaults,
    base_stats: species.baseStats, types: species.types, base_species: species.baseSpecies,
    battle_level: battleLevel, calculated_stats: calculated,
    move_details: set.moves.map(name => {
      const move = dex.moves.get(name);
      return {name, type: move.type, category: move.category, base_power: move.basePower,
        accuracy: move.accuracy, priority: move.priority, target: move.target, pp: move.pp};
    }),
  };
}

function normalizeCorpus(root = path.join(__dirname, 'data', 'team_corpus')) {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'download_manifest.json'), 'utf8'));
  const accepted = [], rejected = [];
  const validators = new Map();
  for (const source of manifest.sources) {
    const bytes = fs.readFileSync(path.join(root, source.file));
    if (sha256(bytes) !== source.sha256) throw new Error('Source checksum mismatch: ' + source.id);
    const data = JSON.parse(bytes);
    const rows = source.kind === 'pokepaste' ? [data] : data;
    for (let index = 0; index < rows.length; index++) {
      const row = rows[index];
      try {
        const format = Dex.formats.get(source.format);
        const dex = Dex.mod(format.exists ? format.mod : source.format.slice(0, 4));
        let validator = null;
        if (format.exists && format.effectType === 'Format') {
          if (!validators.has(source.format)) validators.set(source.format, new TeamValidator(source.format));
          validator = validators.get(source.format);
        }
        const original = source.kind === 'pokepaste' ? rawPasteSets(row.paste) : clone(row.data);
        if (original.length !== 6) throw new Error(`Expected six Pokemon, found ${original.length}`);
        const statedFormat = source.kind === 'pokepaste' ? /Format:\s*(gen\w+)/i.exec(row.notes || '')?.[1] : null;
        if (statedFormat && statedFormat !== source.format) throw new Error('Paste format conflicts with source format: ' + statedFormat);
        const defaultLevel = validator?.ruleTable.defaultLevel || 100;
        const adjustedLevel = validator?.ruleTable.adjustLevel || null;
        const members = original.map(set => normalizeSet(set, dex, defaultLevel, adjustedLevel));
        const sets = members.map(member => member.set);
        if (new Set(members.map(member => member.base_species)).size !== 6) throw new Error('Duplicate base species');
        const errors = validator ? (validator.validateTeam(clone(sets)) || []) : ['Original format unavailable in pinned validator'];
        // An error is retained for audit, not quietly repaired or counted as a
        // usable training team. Unknown historical formats also need review.
        if (errors.length) {
          rejected.push({source_id: source.id, index, title: row.title || row.name || '', format: source.format, errors});
          continue;
        }
        accepted.push({format: source.format, generation: dex.gen, game_type: 'doubles',
          title: source.source_title || row.title || row.name || null,
          author: source.credited_author || row.author || null,
          paste_author: row.author || null,
          source: {...source, row_index: index}, members,
          validation: {status: 'passed', validator: 'pokemon-showdown', version, format: source.format},
          stats_note: 'Unboosted stats of the specified forme at battle level; exclude items, abilities, weather, boosts and transformations.'});
      } catch (error) {
        rejected.push({source_id: source.id, index, format: source.format, errors: [error.message]});
      }
    }
  }
  const snapshot = {source: 'https://github.com/smogon/pokemon-showdown', version, species: {}};
  for (const species of Dex.species.all()) {
    snapshot.species[species.name] = {baseStats: species.baseStats, types: species.types, baseSpecies: species.baseSpecies};
  }
  fs.writeFileSync(path.join(root, 'dex.json'), JSON.stringify(snapshot) + '\n');
  fs.writeFileSync(path.join(root, 'normalized.json'), JSON.stringify({schema_version: 1, teams: accepted}, null, 2) + '\n');
  fs.writeFileSync(path.join(root, 'rejected.json'), JSON.stringify({schema_version: 1, rejected}, null, 2) + '\n');
  const counts = {};
  for (const team of accepted) counts[team.format] = (counts[team.format] || 0) + 1;
  return {accepted: accepted.length, rejected: rejected.length, by_format: counts};
}

module.exports = {normalizeCorpus, normalizeSet, rawPasteSets};
if (require.main === module) console.log(JSON.stringify(normalizeCorpus(), null, 2));
