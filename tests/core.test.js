import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { formatClock, sampleFrames, samplePacked, releasedEvents, lastAtOrBefore, rosterStatus, STRIDE, PHASES } from '../core.js';

const metadata = JSON.parse(readFileSync(new URL('../data/metadata.json', import.meta.url)));
const events = readFileSync(new URL('../data/events.jsonl', import.meta.url), 'utf8').trim().split('\n').map(JSON.parse);
const preview = JSON.parse(readFileSync(new URL('../data/preview-60s.json', import.meta.url)));

test('Match clock handles stoppage time, half-time and event period', () => {
  for (const [time, value] of [[0, '00:00'], [2700000, '45:00'], [2761000, '45+1:01'], [2999000, '45+4:59'], [3000000, '45:00'], [5700000, '90:00'], [5932000, '90+3:52'], [6000000, '90+5:00']]) assert.equal(formatClock(time, metadata), value);
  assert.equal(formatClock(3000000, metadata, 1), '45+5:00');
});

test('Goals are hidden before availability and future events disappear when rewinding', () => {
  const goals = events.filter(e => e.type === 'shot' && e.outcome === 'goal');
  for (const goal of goals) {
    assert.ok(!releasedEvents(events, goal.available_at_ms - 1, 'goal').includes(goal));
    assert.ok(releasedEvents(events, goal.available_at_ms, 'goal').includes(goal));
  }
  assert.equal(releasedEvents(events, 6000000, 'goal').length, 4);
  assert.equal(releasedEvents(events, 0, 'goal').length, 0);
});

test('Snapshot lookup never uses future state', () => {
  const states = [{ t_ms: 0 }, { t_ms: 1000 }, { t_ms: 2000 }];
  assert.equal(lastAtOrBefore(states, 999), 0);
  assert.equal(lastAtOrBefore(states, 1000), 1);
  assert.equal(lastAtOrBefore(states, -1), -1);
});

test('Cards and substitutions are key events that replay cannot reveal early', () => {
  const list = ['yellow_card', 'substitution', 'red_card'].map((type, i) => ({type, available_at_ms:(i+1)*1000}));
  assert.deepEqual(releasedEvents(list, 1999, 'key').map(e => e.type), ['yellow_card']);
  assert.deepEqual(releasedEvents(list, 3000, 'key').map(e => e.type), ['yellow_card', 'substitution', 'red_card']);
  for (const type of ['yellow_card','substitution','red_card']) assert.equal(releasedEvents(list,3000,type).length,1);
});

test('Squad status follows substitutions and red cards, and rewinding restores it', () => {
  const list = [{type:'yellow_card',player_id:'H03',available_at_ms:500},{type:'substitution',player_out_id:'H03',player_in_id:'H12',available_at_ms:1000},{type:'red_card',player_id:'H12',available_at_ms:2000}];
  const starter={player_id:'H03',starter:true}, bench={player_id:'H12',starter:false};
  assert.equal(rosterStatus(bench,list,999).status,'Not used');
  assert.equal(rosterStatus(starter,list,1000).status,'Subbed off');
  assert.equal(rosterStatus(bench,list,1000).status,'Subbed on');
  assert.equal(rosterStatus(bench,list,2000).status,'Sent off');
  assert.deepEqual(rosterStatus(starter,list,999),{status:'On pitch',yellow:1});
});

test('Dynamic squad sampling: incoming players appear at the boundary and departed players do not interpolate to the origin', () => {
  const meta={players:[{player_id:'H01'},{player_id:'H12'}]};
  const data={count:2,step:200,stride:14,positions:new Float32Array(28),active:new Uint8Array([1,0,0,1]),scores:new Uint16Array(4),phases:new Uint8Array(2),owners:new Int8Array(2).fill(-1),possession:new Uint8Array(2),periods:new Uint8Array([1,1]),inPlay:new Uint8Array(2)};
  data.positions.set([50,30,1,1],0); data.positions.set([50,30,1,1],18);
  assert.deepEqual(samplePacked(data,199,meta).players,[['H01',50,30,1,1]]);
  assert.deepEqual(samplePacked(data,200,meta).players,[['H12',50,30,1,1]]);
});

test('Players interpolate by stable ID while score and ball owner stay on the previous frame', () => {
  const a = preview.frames[0], b = preview.frames[1];
  const reordered = { ...b, players: [...b.players].reverse(), score: [9, 9], ball_owner_id: 'H01' };
  const sample = sampleFrames([a, reordered], a.t_ms + 100);
  assert.deepEqual(sample.score, a.score);
  assert.equal(sample.ball_owner_id, a.ball_owner_id);
  assert.equal(sample.players[0][1], (a.players[0][1] + b.players[0][1]) / 2);
});

test('Packed tracking keeps discrete scores around goals and stays finite at the end', () => {
  const a = preview.frames.findIndex(f => f.score[0] === 2);
  const frames = preview.frames.slice(a - 1, a + 1);
  const data = { count: 2, step: 200, positions: new Float32Array(2 * STRIDE), scores: new Uint16Array(4), phases: new Uint8Array(2), owners: new Int8Array(2).fill(-1), possession: new Uint8Array(2), periods: new Uint8Array([2, 2]), inPlay: new Uint8Array(2) };
  frames.forEach((f, i) => {
    f.players.forEach((p, j) => data.positions.set(p.slice(1), i * STRIDE + j * 4));
    data.positions.set(f.ball, i * STRIDE + 88); data.scores.set(f.score, i * 2); data.phases[i] = PHASES.indexOf(f.phase);
  });
  assert.deepEqual(samplePacked(data, 199, metadata).score, [1, 2]);
  assert.deepEqual(samplePacked(data, 200, metadata).score, [2, 2]);
  assert.ok(samplePacked(data, 10000, metadata).players.every(p => p.slice(1).every(Number.isFinite)));
});
