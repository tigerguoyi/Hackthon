import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from enriched_rules import (candidate_actions, geometry, tactical_probabilities, original_probability,
                            evaluate, softmax, reachable_projection, FEATURES)


def scene():
    players=[]
    for team,prefix in enumerate('HA'):
        for i in range(1,12):
            players.append(dict(id=f'{prefix}{i:02}',team=team,x=25+(i%4)*8 if team==0 else 45+(i%4)*6,
                                y=5+(i%6)*10,vx=0.,vy=0.))
    players[9].update(x=90.,y=34.);players[0].update(x=5.,y=34.)
    players[11].update(x=100.,y=34.)
    return dict(t_ms=120000,period=1,team=0,actor='H10',score=[0,0],ball=[90.,34.,0.,0.,0.,0.],players=players,
                ratings=[dict(attack=1.,defense=1.,passing=1.,pressing=1.) for _ in range(2)])


class RuleTests(unittest.TestCase):
    def test_softmax_total_ties_duplicates_and_unsupported(self):
        rows=[dict(key='a',q=.12,supported=True),dict(key='b',q=.10,supported=True),dict(key='c',q=.08,supported=True)]
        scored=softmax(rows+[rows[0],dict(key='bad',q=99,supported=False)],.05)
        self.assertEqual(len(scored),4);self.assertAlmostEqual(sum(a['relative_score'] or 0 for a in scored),100)
        self.assertAlmostEqual(scored[0]['relative_score'],47.177622,places=4)
        self.assertIsNone(scored[-1]['relative_score'])
        tied=softmax([dict(key=str(i),q=.1,supported=True) for i in range(4)],.05)
        self.assertEqual([a['relative_score'] for a in tied],[25]*4)

    def test_original_shot_save_is_unconditional_and_carry_has_no_negatives(self):
        s=scene();shot=candidate_actions(s)[-1];f=geometry(s,shot)[0];g=original_probability(s,shot,f)
        self.assertAlmostEqual(g,.12);self.assertAlmostEqual(g+.43+(1-g-.43),1)
        carry=next(a for a in candidate_actions(s) if a['kind']=='carry')
        self.assertEqual(original_probability(s,carry,geometry(s,carry)[0]),1)

    def test_no_future_labels_in_geometry_or_value(self):
        s=scene();xt=[.02]*96;before=evaluate(s,xt)
        s.update(final_score=[9,1],outcome='goal',end_location=[105,34],future_goal=True)
        after=evaluate(s,xt)
        self.assertEqual([(a['key'],a['q']) for a in before],[(a['key'],a['q']) for a in after])

    def test_blocks_reduce_goal_without_double_count_and_sum_to_one(self):
        s=scene();a=candidate_actions(s)[-1];clear=tactical_probabilities(geometry(s,a)[0],'shot')
        s['players'][12].update(x=96.,y=34.)
        blocked=tactical_probabilities(geometry(s,a)[0],'shot')
        self.assertLess(blocked['goal'],clear['goal']);self.assertGreater(blocked['blocked'],clear['blocked'])
        self.assertAlmostEqual(sum(blocked.values()),1)

    def test_distant_shot_has_no_point002_floor(self):
        s=scene();s['players'][9]['x']=10;s['ball'][0]=10
        shot=candidate_actions(s)[-1];p=tactical_probabilities(geometry(s,shot)[0],'shot')
        self.assertLess(p['goal'],.002);self.assertTrue(shot['supported'])

    def test_pass_pressure_and_carry_closing_change_probability(self):
        s=scene();a=next(a for a in candidate_actions(s) if a['kind']=='pass' and a['supported'])
        f=geometry(s,a)[0];clear=f.copy();crowded=f.copy();clear[10]=0;crowded[10]=3
        self.assertLess(tactical_probabilities(crowded,'pass')['success'],tactical_probabilities(clear,'pass')['success'])
        carry=next(a for a in candidate_actions(s) if a['kind']=='carry');f=geometry(s,carry)[0]
        fast=f.copy();fast[12]=8
        self.assertLess(tactical_probabilities(fast,'carry')['success'],tactical_probabilities(f,'carry')['success'])

    def test_two_step_values_receiver_opportunity(self):
        s=scene();s['players'][8].update(x=100.,y=30.);xt=[.005]*96
        one={a['key']:a for a in evaluate(s,xt,1)};two={a['key']:a for a in evaluate(s,xt,2)}
        self.assertGreater(two['pass:H09']['q'],one['pass:H09']['q'])
        self.assertTrue(all(math.isfinite(a['q']) for a in two.values() if a['q'] is not None))

    def test_clear_near_shot_and_blocked_cutback_are_different(self):
        s=scene();s['players'][9]['x']=100;s['ball'][0]=100
        self.assertEqual(evaluate(s,[.005]*96)[0]['kind'],'shot')
        s=scene();s['players'][12].update(x=96,y=34);s['players'][8].update(x=98,y=24)
        options={a['key']:a for a in evaluate(s,[.005]*96)}
        self.assertGreater(options['pass:H09']['q'],options['shot:goal']['q'])

    def test_opponent_danger_increases_turnover_cost(self):
        s=scene();s['players'][8].update(x=100,y=30)
        a=next(a for a in candidate_actions(s) if a['key']=='pass:H09')
        from enriched_rules import progress
        _,_,point=geometry(s,a)
        cell=min(7,int(point[1]/68*8))*12+min(11,int(progress(point[0],1,s['period'])/105*12))
        low=[.005]*96;high=low.copy();high[cell]=.5
        before=next(r for r in evaluate(s,low,1) if r['key']==a['key'])
        after=next(r for r in evaluate(s,high,1) if r['key']==a['key'])
        self.assertGreater(after['risk'],before['risk']);self.assertLess(after['q'],before['q'])

    def test_pass_distance_support_and_continuous_acceleration(self):
        s=scene();keeper=next(a for a in candidate_actions(s) if a['key']=='pass:H01')
        self.assertFalse(keeper['supported'])
        p=dict(x=50.,y=34.,vx=0.,vy=0.);end=reachable_projection(p,[100,34])
        self.assertAlmostEqual(end['x']-p['x'],1.75);self.assertAlmostEqual(end['vx'],3.5)

    def test_unfinished_shot_at_period_boundary_gets_no_reward(self):
        s=scene();s['t_ms']=2999900
        self.assertTrue(all(a['q']==0 for a in evaluate(s,[.01]*96) if a['supported']))


try:
    from enriched_pipeline import pre_state, selected_decisions, representative, guarded_output, SOURCE, learn_xt
except ImportError:
    pre_state=None


@unittest.skipIf(pre_state is None,'Install requirements-enrichment.txt for pipeline tests')
class PipelineTests(unittest.TestCase):
    def test_readonly_guard_rejects_source_and_ancestors(self):
        for path in [SOURCE,SOURCE/'new.json',SOURCE.parent]:
            with self.assertRaises(ValueError):guarded_output(path)

    def test_reconstruction_ignores_future_outcome_and_endpoint(self):
        s=scene();frame=dict(t_ms=s['t_ms'],period=1,score=[0,0],ball=s['ball'],players=[[p['id'],p['x'],p['y'],p['vx'],p['vy']] for p in s['players']])
        event=dict(start_ms=s['t_ms'],period=1,team_id='HOME',player_id='H10',outcome='complete')
        before=pre_state(frame,event,s['ratings']);event.update(outcome='intercepted',end_location=[0,0],final_score=[0,9])
        self.assertEqual(before,pre_state(frame,event,s['ratings']))
        frame['t_ms']+=200
        with self.assertRaises(ValueError):pre_state(frame,event,s['ratings'])

    def test_automatic_carries_are_not_decisions(self):
        actions=[dict(type=k,start_ms=t,end_ms=end,action_id=str(i)) for i,(k,t,end) in enumerate([
            ('restart',0,12000),('carry',12000,14000),('pass',14000,16000),('carry',16000,18000),('carry',18000,20000)])]
        nodes,automatic=selected_decisions(actions)
        self.assertEqual([e['start_ms'] for e in nodes],[14000,18000]);self.assertEqual(automatic,2)

    def test_representatives_limit_actors_possessions_and_categories(self):
        rows=[]
        for i in range(100):
            rows.append(dict(id=str(i),t=i*31000,actor='H'+str(i%11) if i%2==0 else 'A'+str(i%11),team=i%2,
                possession_id=i,zone=i%3,relative_score=10,loss=.01*i,best_q=.2,category=['opportunity','favoured','similar'][i%3],assumption_sensitive=False))
        ids=representative(rows);chosen=[r for r in rows if r['id'] in ids]
        from collections import Counter
        self.assertLessEqual(max(Counter(r['actor'] for r in chosen).values()),2)
        self.assertLessEqual(max(Counter(r['team'] for r in chosen).values()),10)
        self.assertEqual(len(chosen),20)


if __name__=='__main__':unittest.main()
