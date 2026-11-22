'use strict';

// Validate generated candidates and return the first legal choice for each role.
const fs = require('node:fs');
const { TeamValidator } = require('./.tools/pokemon-showdown/package/dist/sim/team-validator');
const validator = new TeamValidator('gen9doublesubers');
const groups = JSON.parse(fs.readFileSync(0, 'utf8'));
const stats = ['hp', 'atk', 'def', 'spa', 'spd', 'spe'];

function legal(option) {
  const original = option.set;
  const set = structuredClone(original);
  return !validator.validateSet(set, {}) &&
    ['species', 'ability', 'item', 'nature', 'teraType', 'level'].every(key => set[key] === original[key]) &&
    set.moves.every((move, i) => move === original.moves[i]) &&
    set.moves.length === original.moves.length &&
    stats.every(stat => set.evs[stat] === original.evs[stat] && set.ivs[stat] === original.ivs[stat]);
}

const all = process.argv.includes('--all');
process.stdout.write(JSON.stringify(groups.map(options => all
  ? options.flatMap((option, index) => legal(option) ? [index] : [])
  : options.findIndex(legal))));
