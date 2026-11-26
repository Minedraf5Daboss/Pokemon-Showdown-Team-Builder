'use strict';

// Reproducible names and Species Clause metadata from the pinned validator.
const fs = require('node:fs');
const { Dex } = require('./.tools/pokemon-showdown/package/dist/sim/dex');
const snapshot = {
  source: 'https://github.com/smogon/pokemon-showdown',
  version: require('./.tools/pokemon-showdown/package/package.json').version,
};
for (const section of ['moves', 'items', 'abilities', 'natures', 'types']) {
  snapshot[section] = Object.fromEntries(Dex[section].all().map(entry => [entry.id, entry.name]));
}
snapshot.species = Object.fromEntries(Dex.species.all().filter(entry => entry.gen <= 9).map(entry => [entry.name, {
  baseSpecies: entry.baseSpecies, baseStats: entry.baseStats, types: entry.types,
}]));
snapshot.move_categories = Object.fromEntries(Dex.moves.all().map(entry => [entry.id, entry.category]));
fs.writeFileSync(process.argv[2] || 'data/smogon/battle_dex.json', JSON.stringify(snapshot, null, 2) + '\n');
console.log(`Exported official Showdown ${snapshot.version} names and species metadata.`);
