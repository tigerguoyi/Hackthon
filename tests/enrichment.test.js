import test from 'node:test';
import assert from 'node:assert/strict';
import {filterDecisions,preferenceText} from '../decision-view.js';
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
