#!/usr/bin/env node
// No remote schema loading: all references resolve to these two checked-in files.
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import {fileURLToPath} from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = p => JSON.parse(fs.readFileSync(p, 'utf8'));
const data = read(path.join(root, 'contracts/records.schema.json'));
const contract = read(path.join(root, 'contracts/schema.json'));
const ajv = new Ajv2020({allErrors: true, strict: true, strictRequired: false, allowUnionTypes: true});
addFormats(ajv);
ajv.addSchema(data).addSchema(contract);
const validators = new Map();
// Eager compilation catches broken references/keywords even for unused definitions.
for (const [namespace, schema] of [['data', data], ['contract', contract]]) {
  for (const name of Object.keys(schema.$defs)) {
    validators.set(`${namespace}.${name}`, ajv.getSchema(`${schema.$id}#/$defs/${name}`));
  }
}

export function validate(name, value) {
  const fn = validators.get(name);
  if (!fn) throw new Error(`Unknown schema ${name}`);
  const valid = fn(value);
  return {valid, errors: valid ? [] : structuredClone(fn.errors)};
}

async function dataset(base) {
  const counts = {};
  function check(name, value, location) {
    const result = validate(name, value);
    if (!result.valid) throw new Error(`${location}: ${JSON.stringify(result.errors)}`);
    counts[name] = (counts[name] || 0) + 1;
  }
  const folders = fs.existsSync(path.join(base, 'matches'))
    ? fs.readdirSync(path.join(base, 'matches')).sort().map(n => path.join(base, 'matches', n)) : [base];
  if (fs.existsSync(path.join(base, 'squads.json'))) check('data.squads', read(path.join(base, 'squads.json')), 'squads.json');
  for (const folder of folders) {
    const meta = read(path.join(folder, 'metadata.json'));
    check('data.match', meta, folder);
    const previewPath = path.join(folder, 'preview-60s.json');
    if (fs.existsSync(previewPath)) check('data.preview', read(previewPath), previewPath);
    const playerIds = new Set(meta.players.map(p => p.player_id));
    if (playerIds.size !== meta.players.length) throw new Error(`${folder}: duplicate player IDs`);
    for (const [file, name] of [['events.jsonl', 'event'], ['states.jsonl', 'state'], ['tracking.jsonl', 'tracking'], ['decisions.jsonl', 'decision']]) {
      const filename = path.join(folder, file);
      if (!fs.existsSync(filename)) {
        if (['event', 'state'].includes(name)) throw new Error(`Missing required stream: ${filename}`);
        continue; // Full tracking / decision caches are optional in a fresh clone.
      }
      const lines = readline.createInterface({input: fs.createReadStream(filename), crlfDelay: Infinity});
      let lineNumber = 0;
      for await (const line of lines) {
        ++lineNumber;
        const value = JSON.parse(line);
        const kind = name === 'decision' ? 'tracking' : name;
        const row = name === 'decision' ? value.snapshot : value;
        const location = `${filename}:${lineNumber}`;
        check(`data.${kind}`, row, location);
        if (name === 'decision') counts.decision_snapshots = (counts.decision_snapshots || 0) + 1;
        if (row.t_ms > meta.duration_ms || row.end_ms > meta.duration_ms) throw new Error(`${location}: time outside match`);
        if (kind === 'event') {
          if (row.match_id !== meta.match_id || row.start_ms > row.end_ms || row.available_at_ms < row.end_ms ||
              ('duration_ms' in row && row.duration_ms !== row.end_ms - row.start_ms)) throw new Error(`${location}: event identity/clock mismatch`);
          for (const field of ['player_id', 'recipient_id', 'player_in_id', 'player_out_id', 'target_player_id']) {
            if (row[field] && !playerIds.has(row[field])) throw new Error(`${location}: unknown ${field}`);
          }
        }
        if (kind === 'tracking') {
          const ids = new Set(row.players.map(p => p[0]));
          const period = meta.periods.find(p => p.period === row.period);
          if (ids.size !== row.players.length || [...ids].some(id => !playerIds.has(id)) ||
              (row.ball_owner_id !== null && !ids.has(row.ball_owner_id)) ||
              row.period_ms !== row.t_ms - period.start_ms ||
              Math.abs(row.frame_id * 1000 / meta.tracking_hz - row.t_ms) > 1e-6) throw new Error(`${location}: invalid frame references/clock`);
        }
      }
    }
  }
  return {dataset: base, counts};
}

async function main() {
  const [command, ...args] = process.argv.slice(2);
  if (command === '--stdin') {
    // One {schema, value} per line; used by Python tests without Python dependencies.
    for await (const line of readline.createInterface({input: process.stdin, crlfDelay: Infinity})) {
      const {schema, value} = JSON.parse(line);
      console.log(JSON.stringify(validate(schema, value)));
    }
  } else if (command === '--dataset' && args.length) {
    for (const folder of args) console.log(JSON.stringify(await dataset(path.resolve(folder))));
  } else if (command === '--file' && args.length === 2) {
    const result = validate(args[0], read(args[1]));
    console.log(JSON.stringify(result, null, 2));
    if (!result.valid) process.exitCode = 1;
  } else throw new Error('Usage: --stdin | --dataset <folders...> | --file <data.name|contract.name> <file>');
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => {console.error(error.message); process.exitCode = 1;});
}
