import test from 'node:test';
import assert from 'node:assert/strict';
import {filterDecisions,preferenceText,playerActionConditions} from '../decision-view.js';
import {datasetFrom,DATASETS} from '../datasets.js';
test('Datasets share original matches but have independent analysis identity',()=>{
  assert.equal(datasetFrom('not-valid'),'enriched-v1');
  assert.equal(DATASETS.original.root,DATASETS['enriched-v1'].root);
  assert.notEqual(DATASETS.original.analysisRoot,DATASETS['enriched-v1'].analysisRoot);
});
test('Coverage gaps cannot silently become scored decisions',()=>{
  const rows=[{id:'a',coverage:'exact',actor:'H10',team:0,kind:'pass',conflicts:[],relative_score:30},
    {id:'b',coverage:'missing',actor:'A10',team:1,kind:'shot',conflicts:[],relative_score:null}];
  assert.deepEqual(filterDecisions(rows,{scope:'all'},['a'],[]).map(r=>r.id),['a']);
  assert.deepEqual(filterDecisions(rows,{scope:'coverage'},['a'],[]).map(r=>r.id),['b']);
});
test('Shares are not max-scaled or rendered as false zeros',()=>{
  assert.equal(preferenceText(null),'—');assert.equal(preferenceText(.001),'<0.1');
  assert.equal(preferenceText(47.177),'47.2');
});

const motionState=()=>({period:1,actor:'H10',team:0,ball:[10,20,0,0,0,0],players:[
  {id:'H10',team:0,x:10,y:20,vx:3,vy:4},
  {id:'H09',team:0,x:10.1,y:20,vx:0,vy:0},
  {id:'A01',team:1,x:12,y:20,vx:0,vy:0},
  {id:'A03',team:1,x:14,y:20,vx:0,vy:0},
  {id:'A04',team:1,x:20,y:20,vx:0,vy:0}]});
test('Action conditions use the selected player and include the opposing goalkeeper, not teammates',()=>{
  const s=motionState(),before=JSON.stringify(s),c=playerActionConditions(s);
  assert.equal(c.speedMps,5);assert.equal(c.speedKmh,18);
  assert.ok(Math.abs(c.headingDeg-53.13010235415598)<1e-9);
  assert.equal(c.movementLabel,'Forward-right');
  assert.deepEqual(c.nearestOpponent,{id:'A01',distance:2});
  assert.equal(c.underPressure,true);assert.equal(c.opponentsWithin5m,2);assert.equal(c.ballDistance,0);
  assert.equal(JSON.stringify(s),before);
  const defender=playerActionConditions(s,'A03');
  assert.equal(defender.nearestOpponent.id,'H09');assert.equal(defender.ballDistance,4);
});
test('Movement labels follow each team and half while pitch angles stay in one coordinate system',()=>{
  const s=motionState();s.players[0].vx=3;s.players[0].vy=0;
  assert.equal(playerActionConditions(s).movementLabel,'Forward');
  s.period=2;
  assert.equal(playerActionConditions(s).movementLabel,'Backward');
  assert.equal(playerActionConditions(s).headingDeg,0);
  s.players[0].vx=-3;
  assert.equal(playerActionConditions(s).movementLabel,'Forward');
  assert.equal(playerActionConditions(s).headingDeg,180);
});
test('Slow or missing motion is not presented as an observed body heading or a zero-speed measurement',()=>{
  const s=motionState();s.players[0].vx=.2;s.players[0].vy=0;
  assert.equal(playerActionConditions(s).headingDeg,null);
  assert.equal(playerActionConditions(s).movementLabel,'Stationary / slow');
  s.players[0].vx=null;
  assert.equal(playerActionConditions(s).speedMps,null);
  assert.equal(playerActionConditions(s).movementLabel,'Unavailable');
  assert.equal(playerActionConditions(s,'not-on-pitch'),null);
  s.players[0].x=null;s.ball=null;
  const c=playerActionConditions(s);
  assert.equal(c.nearestOpponent,null);assert.equal(c.underPressure,null);assert.equal(c.ballDistance,null);
});
test('Actual results and alternative outcomes cannot affect decision-time action conditions',()=>{
  const s=motionState(),before=playerActionConditions(s);
  s.outcome='goal';s.end_location=[105,34];s.score=[99,0];s.future_frames=[{players:[]}];
  assert.deepEqual(playerActionConditions(s),before);
});
