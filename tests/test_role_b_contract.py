"""Canonical A contracts tested against B's retained Q2 and exact pass rollout."""
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import unittest

from contracts import candidate_set_id, check_exchange, make_snapshot
from enriched_rules import evaluate as legacy_evaluate
from role_b.input_adapter import adapt_snapshot, match_actual, source_profile
from role_b.comparison import compare_actions, rollout_action, accept_agent_response
from role_b.q2 import evaluate

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures/contracts'


def read(path):return json.loads(Path(path).read_text())


def validate_batch(items):
    result=subprocess.run([os.environ.get('NODE_BINARY','node'),str(ROOT/'scripts/validate_contracts.mjs'),'--stdin'],
        input=''.join(json.dumps({'schema':name,'value':value})+'\n' for name,value in items),text=True,capture_output=True)
    if result.returncode:raise AssertionError(result.stderr)
    return [json.loads(line) for line in result.stdout.splitlines()]


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metadata=read(FIX/'match.json')['metadata']
        cls.cases={c:{k:read(FIX/f'case-{c}'/(k+'.json')) for k in ('snapshot','request','context')} for c in 'abc'}
        cls.model=dict(model_id='fixture-neutral-xt',source_commit='test-only',xt=[.02]*96,fitting_matches=[],temperature=.05)

    def result(self,case='a',request=None,snapshot=None,metadata=None,model=None):
        c=self.cases[case]
        return compare_actions(request or c['request'],snapshot or c['snapshot'],metadata or self.metadata,model or self.model)

    def test_all_three_public_responses_pass_original_schema_and_exchange(self):
        items=[]
        for case,c in self.cases.items():
            public,private=self.result(case)
            check_exchange(c['request'],public,c['snapshot'],self.metadata)
            self.assertEqual(public['status'],'model_unavailable');self.assertEqual(public['agent_trace'],[])
            self.assertIsNone(public['explanation']);self.assertTrue(private['options'])
            self.assertAlmostEqual(sum(r['relative_score'] or 0 for r in private['options']),100)
            items.extend([('contract.snapshot',c['snapshot']),('contract.analysis_request',c['request']),('contract.analysis_response',public)])
        self.assertTrue(all(r['valid'] for r in validate_batch(items)))

    def test_numeric_research_fields_are_not_public_fields(self):
        response,private=self.result();self.assertIn('q',private['options'][0])
        forbidden={'q','q2','value_gain','scenario_weight','relative_score','softmax_weight','probabilities','temperature'}
        def walk(obj):
            if isinstance(obj,dict):
                self.assertFalse(set(obj)&forbidden)
                for v in obj.values():walk(v)
            elif isinstance(obj,list):
                for v in obj:walk(v)
        walk(response);self.assertNotIn('%',json.dumps(response))
        bad=copy.deepcopy(response);bad['candidates'][0]['value_gain']=.5
        self.assertFalse(validate_batch([('contract.analysis_response',bad)])[0]['valid'])

    def test_neutral_adapter_is_numerically_identical_to_original_q2(self):
        for case in self.cases.values():
            state=adapt_snapshot(case['snapshot'],self.metadata)
            original=legacy_evaluate(state,self.model['xt'])
            adapted=evaluate(state,self.model['xt'],contract_mode=False)
            self.assertEqual([(r['key'],r['q']) for r in original],[(r['key'],r['q']) for r in adapted])

    def test_explicit_direction_and_actual_period_boundaries_override_legacy_guesses(self):
        meta=copy.deepcopy(self.metadata);meta['periods'][1]['home_attacks']='positive_x'
        frame=copy.deepcopy(self.cases['a']['snapshot']['frame']);frame.update(period=2,t_ms=3000000,period_ms=0,frame_id=15000)
        state=adapt_snapshot(make_snapshot(meta,frame),meta)
        rows=evaluate(state,self.model['xt'],contract_mode=False)
        shot=next(r for r in rows if r['kind']=='shot')
        self.assertEqual(shot['target'][0],105)
        meta=copy.deepcopy(self.metadata);meta['periods'][0]['duration_ms']=100100;meta['periods'][1]['start_ms']=100100
        frame=copy.deepcopy(self.cases['a']['snapshot']['frame']);frame.update(t_ms=100000,period_ms=100000,frame_id=500)
        state=adapt_snapshot(make_snapshot(meta,frame),meta)
        self.assertTrue(all(r['q']==0 for r in evaluate(state,self.model['xt']) if r['supported']))

    def test_substitute_keeper_keeps_real_match_id_and_departed_player_is_not_a_candidate(self):
        meta=copy.deepcopy(self.metadata);incoming=copy.deepcopy(meta['players'][0]);incoming.update(player_id='H12',jersey_number=12,starter=False,name='Incoming keeper')
        meta['players'].append(incoming)
        frame=copy.deepcopy(self.cases['a']['snapshot']['frame']);frame['players'][0][0]='H12'
        snap=make_snapshot(meta,frame);request={**self.cases['a']['request'],'snapshot_id':snap['snapshot_id']}
        public,_=self.result(request=request,snapshot=snap,metadata=meta)
        ids={c['action_id'] for c in public['candidates']}
        self.assertIn('pass:H12',ids);self.assertNotIn('pass:H01',ids)
        check_exchange(request,public,snap,meta)

    def test_red_card_removed_player_never_becomes_a_candidate(self):
        frame=copy.deepcopy(self.cases['a']['snapshot']['frame']);frame['players']=[p for p in frame['players'] if p[0]!='H09']
        snap=make_snapshot(self.metadata,frame);request={**self.cases['a']['request'],'snapshot_id':snap['snapshot_id']}
        public,_=self.result(request=request,snapshot=snap)
        self.assertNotIn('pass:H09',{c['action_id'] for c in public['candidates']})

    def test_offside_is_checked_at_release_and_hold_is_not_given_a_fake_zero_value(self):
        frame=copy.deepcopy(self.cases['a']['snapshot']['frame'])
        for p in frame['players']:
            if p[0]=='H10':p[1:3]=[85,34]
            elif p[0]=='H09':p[1:3]=[104,30]
            elif p[0].startswith('A'):p[1]=90 if p[0]!='A01' else 100
        frame['ball'][:2]=[85.35,34]
        snap=make_snapshot(self.metadata,frame);request={**self.cases['a']['request'],'snapshot_id':snap['snapshot_id']}
        public,private=self.result(request=request,snapshot=snap)
        offside=next(c for c in public['candidates'] if c['action_id']=='pass:H09')
        self.assertFalse(offside['eligible']);self.assertIsNone(offside['rank']);self.assertFalse(offside['rollout_supported'])
        hold=next(c for c in public['candidates'] if c['type']=='hold')
        self.assertFalse(hold['eligible']);self.assertNotIn('hold:1s',[r['key'] for r in private['options']])

    def test_source_radar_and_simulated_profile_are_neutral_and_separate(self):
        unknown,private=self.result();request=copy.deepcopy(self.cases['a']['request'])
        request['profile'].update(preferred_foot='left',weak_foot=1)
        known,other=self.result(request=request)
        self.assertNotEqual(unknown['candidate_set_id'],known['candidate_set_id'])
        self.assertEqual([(r['key'],r['q']) for r in private['options']],[(r['key'],r['q']) for r in other['options']])
        profile=source_profile({'position':'CB','sofascore':{'status':'partial','attribute_kind':'goalkeeper','attributes':{'SAV':99},'source_conflicts':{'position':True}}})
        self.assertEqual(profile['status'],'needs_review');self.assertFalse(profile['used_in_probability'])
        self.assertIsNone(source_profile({'position':'GK'})['observation'])

    def test_actual_overlay_is_post_computation_and_does_not_change_ids_or_ranks(self):
        public,private=self.result();before=copy.deepcopy(public)
        overlay=copy.deepcopy(public);overlay['candidates'][0]['is_actual']=True
        self.assertEqual(candidate_set_id(self.cases['a']['snapshot'],self.cases['a']['request']['profile'],'outcome-2.0',overlay['candidates'],overlay['evidence']),public['candidate_set_id'])
        self.assertEqual(before,public)
        key,resolution=match_actual({'selected':{'kind':'carry'},'end_location':[100,34]},public['candidates'])
        self.assertIsNone(key);self.assertIn('not recoverable',resolution)
        tampered=copy.deepcopy(self.cases['a']['snapshot']);tampered['frame']['ball_owner_id']='H09'
        with self.assertRaises(ValueError):self.result(snapshot=tampered)

    def test_no_future_context_or_outcome_is_accepted_as_prediction_input(self):
        base=copy.deepcopy(self.cases['a']['request']);base['outcome']='goal'
        with self.assertRaises(ValueError):self.result(request=base)
        for bad in [float('nan'),float('inf')]:
            snap=copy.deepcopy(self.cases['a']['snapshot']);snap['frame']['players'][0][1]=bad
            with self.assertRaises(ValueError):self.result(snapshot=snap)

    def test_bound_pass_rollout_reuses_exact_ranked_target_and_is_reproducible(self):
        for case,c in self.cases.items():
            public,private=self.result(case)
            action=next(x for x in public['candidates'] if x['rollout_supported'])['action_id']
            first=rollout_action(c['request'],public,private,c['snapshot'],self.metadata,action,public['candidate_set_id'])
            second=rollout_action(c['request'],public,private,c['snapshot'],self.metadata,action,public['candidate_set_id'])
            self.assertEqual(first,second);self.assertEqual(len(first['scenarios']),4)
            row=next(r for r in private['options'] if r['key']==action)
            self.assertEqual(first['target'],row['target']);self.assertEqual(first['start'],c['snapshot']['frame']['ball'][:2])
            for scenario in first['scenarios']:
                self.assertLessEqual(scenario['duration_s'],4)
                for frame in scenario['frames']:
                    if frame['elapsed_s']==0:
                        self.assertEqual(frame['players'],c['snapshot']['frame']['players'])
                    else:
                        self.assertTrue(all(math.hypot(p[3],p[4])<7.200001 for p in frame['players']))
            with self.assertRaises(ValueError):rollout_action(c['request'],public,private,c['snapshot'],self.metadata,'hold:1s',public['candidate_set_id'])
            with self.assertRaises(ValueError):rollout_action(c['request'],public,private,c['snapshot'],self.metadata,action,'outcome-2.0:0000000000000000')

    def test_stale_agent_response_is_rejected_and_missing_agent_has_no_trace(self):
        public,_=self.result();c=self.cases['a']
        self.assertEqual(accept_agent_response(c['request'],public,c['snapshot'],self.metadata,public['candidate_set_id']),public)
        with self.assertRaises(ValueError):accept_agent_response(c['request'],public,c['snapshot'],self.metadata,'outcome-2.0:0000000000000000')
        self.assertEqual(public['agent_trace'],[]);self.assertIsNone(public['explanation'])

    def test_unknown_ball_owner_and_inactive_actor_are_not_fabricated(self):
        frame=copy.deepcopy(self.cases['a']['snapshot']['frame']);frame['ball_owner_id']=None
        snap=make_snapshot(self.metadata,frame);request={**self.cases['a']['request'],'snapshot_id':snap['snapshot_id']}
        public,_=self.result(request=request,snapshot=snap)
        self.assertEqual(public['status'],'unknown');self.assertEqual(public['candidates'],[])
        self.assertTrue(validate_batch([('contract.analysis_response',public)])[0]['valid'])
        request['actor_id']='H99'
        with self.assertRaises(ValueError):self.result(request=request,snapshot=snap)

    def test_all_status_shapes_follow_original_schema(self):
        public,_=self.result();items=[]
        for status in ('ready','partial','unknown','model_unavailable','error'):
            value=copy.deepcopy(public);value['status']=status
            if status=='ready':value['explanation']={'mode':'fan','text':'Deterministic fixture only; not a model agent.','cited_evidence_ids':['model-context']}
            items.append(('contract.analysis_response',value))
        self.assertTrue(all(r['valid'] for r in validate_batch(items)))


if __name__=='__main__':unittest.main()
