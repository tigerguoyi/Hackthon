#!/usr/bin/env python3
"""Check temporal, motion, identity and event/stat consistency without dependencies."""
import argparse
import json
import math
from collections import Counter
from pathlib import Path


def load_lines(path):
    with path.open() as f:
        for line in f:
            yield json.loads(line)


def validate(root):
    meta=json.loads((root/'metadata.json').read_text())
    summary=json.loads((root/'summary.json').read_text())
    events=list(load_lines(root/'events.jsonl'))
    ids={p['player_id'] for p in meta['players']}
    teams={p['player_id']:p['team_id'] for p in meta['players']}
    starters={p['player_id'] for p in meta['players'] if p.get('starter', True)}
    seen=set()
    goals={}
    last_time=-1
    counts={'HOME':Counter(),'AWAY':Counter()}
    event_by_id={e['event_id']:e for e in events}
    for i,e in enumerate(events,1):
        assert e['event_id'] not in seen
        seen.add(e['event_id'])
        assert e['sequence']==i
        assert e['start_ms']<=e['end_ms']==e['available_at_ms']
        assert e['available_at_ms']>=last_time
        last_time=e['available_at_ms']
        assert 0<=last_time<=meta['duration_ms']
        for field in ('player_id','recipient_id','target_player_id','player_in_id','player_out_id'):
            if field in e:
                assert e[field] in ids,(field,e)
        for related in e.get('related_event_ids',[]):
            assert related in event_by_id
            assert event_by_id[related]['available_at_ms']<=e['available_at_ms']
        team=e.get('team_id')
        if e['type']=='pass':
            assert teams[e['player_id']]==teams[e['recipient_id']]==team
            counts[team]['passes']+=1
            counts[team]['completed_passes']+=int(e['outcome']=='complete')
        elif e['type']=='shot':
            counts[team]['shots']+=1
            counts[team]['shots_on_target']+=int(e['outcome']!='off_target')
            if e['outcome']=='goal':
                goals[e['end_ms']]=team
        elif e['type']=='interception':
            counts[team]['interceptions']+=1
        elif e['type']=='pressure':
            counts[team]['pressure_events']+=1
        elif e['type'] in ('yellow_card', 'red_card', 'substitution'):
            assert teams[e['player_id']] == team
            if e['type'] == 'substitution':
                assert teams[e['player_in_id']] == teams[e['player_out_id']] == team
    prev=None
    score=[0,0]
    max_speed=0.
    max_acceleration=0.
    max_ball_speed=0.
    frame_count=0
    goal_event_by_time={e['end_ms']:e for e in events if e['type']=='shot' and e['outcome']=='goal'}
    snapshots={s['t_ms']:s for s in load_lines(root/'states.jsonl')}
    active_seconds=0.
    expected_active=set(starters)
    roster_events=iter(e for e in events if e['type'] in ('substitution','red_card'))
    next_roster_event=next(roster_events,None)
    position_records=0
    for f in load_lines(root/'tracking.jsonl'):
        frame_count+=1
        assert f['frame_id']==frame_count-1
        assert f['t_ms']==f['frame_id']*200
        assert f['period']==(1 if f['t_ms']<3000000 else 2)
        assert f['period_ms']==f['t_ms']-(f['period']-1)*3000000
        while next_roster_event and next_roster_event['available_at_ms']<=f['t_ms']:
            e=next_roster_event
            if e['type']=='substitution':
                assert e['player_out_id'] in expected_active and e['player_in_id'] not in expected_active
                expected_active.remove(e['player_out_id'])
                expected_active.add(e['player_in_id'])
            else:
                assert e['player_id'] in expected_active
                expected_active.remove(e['player_id'])
            next_roster_event=next(roster_events,None)
        assert {p[0] for p in f['players']}==expected_active
        assert len(f['players'])==len(expected_active)
        position_records+=len(f['players'])
        if f['ball_owner_id'] is not None:
            assert f['ball_owner_id'] in expected_active
            assert teams[f['ball_owner_id']]==f['possession_team_id']
        if f['t_ms'] in goals:
            score[0 if goals[f['t_ms']]=='HOME' else 1]+=1
            assert goal_event_by_time[f['t_ms']]['score']==score
        assert f['score']==score,(f['t_ms'],f['score'],score)
        if f['t_ms'] in snapshots:
            assert snapshots[f['t_ms']]['score']==score
        prev_by_id={p[0]:p for p in prev['players']} if prev else {}
        for p in f['players']:
            pid,x,y,vx,vy=p
            assert all(math.isfinite(v) for v in p[1:])
            assert .49<=x<=104.51 and .49<=y<=67.51
            speed=math.hypot(vx,vy)
            assert speed<=7.21,(pid,speed,f['t_ms'])
            max_speed=max(max_speed,speed)
            if prev and pid in prev_by_id:
                q=prev_by_id[pid]
                assert math.hypot(x-q[1],y-q[2])<=7.21*.2+.003
                assert abs((x-q[1])/.2-vx)<=.006
                assert abs((y-q[2])/.2-vy)<=.006
                acceleration=math.hypot(vx-q[3],vy-q[4])/.2
                max_acceleration=max(max_acceleration,acceleration)
                assert acceleration<=3.52,(pid,acceleration,f['t_ms'])
        bx,by,bz,bvx,bvy,bvz=f['ball']
        assert all(math.isfinite(v) for v in f['ball'])
        assert 0<=bx<=105 and 0<=by<=68 and bz>=0
        ball_speed=math.sqrt(bvx*bvx+bvy*bvy+bvz*bvz)
        max_ball_speed=max(max_ball_speed,ball_speed)
        assert ball_speed<=45,(ball_speed,f['t_ms'])
        if prev:
            for j in range(3):
                assert abs((f['ball'][j]-prev['ball'][j])/.2-f['ball'][j+3])<=.006
        if f['ball_owner_id'] is not None:
            p=next(p for p in f['players'] if p[0]==f['ball_owner_id'])
            assert math.hypot(bx-p[1],by-p[2])<=.6
        prev=f
    assert frame_count==30001
    assert len(snapshots)==6001
    assert score==summary['final_score']
    assert frame_count==summary['tracking_frames']
    assert position_records==summary['player_position_records']
    assert len(events)==summary['event_count']
    final_state=snapshots[6000000]
    for team in ('HOME','AWAY'):
        for key,value in counts[team].items():
            assert final_state['stats'][team][key]==value,(team,key,value)
    total_possession=sum(s['possession_seconds'] for s in final_state['stats'].values())
    assert 0<total_possession<=6000
    assert abs(sum(s['possession_pct'] for s in final_state['stats'].values())-100)<.02
    report={'status':'PASS','tracking_frames':frame_count,'events':len(events),
            'state_snapshots':len(snapshots),'final_score':score,
            'max_player_speed_mps':round(max_speed,3),
            'max_player_acceleration_mps2':round(max_acceleration,3),
            'max_ball_speed_mps':round(max_ball_speed,3),
            'checks':['unique identities and event IDs','5 Hz frame continuity',
                      'pitch bounds and finite coordinates','player speed and acceleration limits',
                      'velocity/coordinate agreement','ball speed and attachment',
                      'event availability and causal references','goals and scoreboard agreement',
                      'event counts and final statistics','period clocks and state snapshots'],
            'scope':'Internal consistency only; not validation against real football distributions.'}
    (root/'validation_report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('data',nargs='?',type=Path,default=Path(__file__).parent/'data')
    validate(p.parse_args().data)
