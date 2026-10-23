#!/usr/bin/env python3
"""audit / train / score / validate existing data. Never import a simulator."""
import argparse
import gzip
import hashlib
import json
import math
import time
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits

from enriched_rules import (VERSION, CONFIG, FEATURES, candidate_actions, geometry, original_probability,
                            evaluate, softmax, xt_value, progress, defensive_diagnostics)

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT/'data/league'
OUTPUT = ROOT/'data/league-enriched-v1'
SOURCE_COMMIT = '7d0c193d9c47706bd96a6bff662597b8a47513d3'
BASIC = list(range(9))+list(range(20,25))


def read_json(path):
    return json.loads(Path(path).read_text())


def read_lines(path):
    with Path(path).open() as stream:
        return [json.loads(line) for line in stream if line.strip()]


def guarded_output(path, source=SOURCE):
    p = Path(path).resolve(); source = Path(source).resolve()
    if p == source or source in p.parents or p in source.parents:
        raise ValueError('Output must be independent of the immutable source dataset')
    return p


def write(path, obj):
    path = guarded_output(path); path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(obj, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n').encode()
    if path.suffix == '.gz': raw = gzip.compress(raw, mtime=0)
    temp = path.with_name(path.name+'.tmp'); temp.write_bytes(raw); temp.replace(path)


def read_gzip(path):
    return json.loads(gzip.decompress(Path(path).read_bytes()))


def fingerprints(source=SOURCE):
    source = Path(source)
    result = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(source.rglob('*')) if p.is_file()}
    result['@generate.py'] = hashlib.sha256((ROOT/'generate.py').read_bytes()).hexdigest()
    return result


def rules_hash():
    return hashlib.sha256((ROOT/'enriched_rules.py').read_bytes()).hexdigest()


def verify_anchors(root):
    expected = read_json(root/'manifest.json')['source_hashes']
    if fingerprints() != expected:
        raise ValueError('Immutable source or generator changed; refusing to reuse enrichment')


def fixtures():
    return read_lines(SOURCE/'fixtures.jsonl')


def fold_for(f):
    return f['matchweek']-1


def selected_decisions(events):
    cancelled = {e['action_id'] for e in events if e['type'] == 'action_cancelled'}
    actions = sorted((e for e in events if e['type'] in ('restart', 'carry', 'pass', 'shot')), key=lambda e: e['start_ms'])
    previous = None; nodes = []; automatic = 0
    for e in actions:
        manual = e['type'] in ('pass', 'shot') or (e['type'] == 'carry' and previous is not None and previous['type'] == 'carry' and previous['end_ms'] == e['start_ms'])
        if manual and e.get('action_id') not in cancelled: nodes.append(e)
        elif e['type'] == 'carry': automatic += 1
        previous = e
    return nodes, automatic


def pre_state(frame, event, ratings):
    """Only continuous pre-execution tracking fields. No end point or outcome."""
    team = int(event['team_id'] == 'AWAY'); actor = event['player_id']
    if frame['t_ms'] != event['start_ms'] or frame['period'] != event['period']:
        raise ValueError('Exact causal frame required')
    players = [dict(id=p[0], team=int(p[0][0]=='A'), x=p[1], y=p[2], vx=p[3], vy=p[4]) for p in frame['players']]
    if not any(p['id'] == actor for p in players): raise ValueError('Actor missing')
    if len(players) < 2 or not all(any(p['team'] == t and p['id'].endswith('01') for p in players) for t in (0,1)):
        raise ValueError('Incomplete teams or goalkeeper')
    return dict(t_ms=frame['t_ms'], period=frame['period'], team=team, actor=actor,
                score=frame['score'], ball=frame['ball'], players=players, ratings=ratings)


def actual_key(s, e):
    if e['type'] == 'pass': return 'pass:'+e['recipient_id']
    if e['type'] == 'shot': return 'shot:goal'
    # Duration and exact target were not logged as predecision intent. Evaluate
    # only a standardised carry direction; do not infer it from the future end.
    return 'carry:forward'


def audit(root, only=None):
    guarded_output(root); start = time.monotonic(); source_hashes = fingerprints()
    league = read_json(SOURCE/'league.json'); ratings = {t['club_id']:t['rating'] for t in league['teams']}
    totals = Counter(); matches = []
    for f in fixtures():
        if only and f['match_id'] not in only: continue
        folder = SOURCE/f['paths']['match']; meta = read_json(folder/'metadata.json'); events = read_lines(folder/'events.jsonl')
        nodes, auto = selected_decisions(events); needed = {e['start_ms'] for e in nodes}; frames = {}
        full = (folder/'tracking.jsonl').exists()
        if full:
            with (folder/'tracking.jsonl').open() as stream:
                for line_no, line in enumerate(stream, 1):
                    frame = json.loads(line)
                    if frame['t_ms'] in needed: frames[frame['t_ms']] = (frame, dict(file=str((folder/'tracking.jsonl').relative_to(SOURCE)), line=line_no))
        else:
            preview = read_json(folder/'preview-60s.json')
            frames = {frame['t_ms']:(frame, dict(file=str((folder/'preview-60s.json').relative_to(SOURCE)), frame_index=i))
                      for i,frame in enumerate(preview['frames']) if frame['t_ms'] in needed}
        rs = [ratings[f['home_club_id']], ratings[f['away_club_id']]]; records = []
        for e in nodes:
            r = dict(id=e['event_id'], event_id=e['event_id'], action_id=e['action_id'], match_id=f['match_id'],
                     t=e['start_ms'], period=e['period'], actor=e['player_id'], team=int(e['team_id']=='AWAY'),
                     kind=e['type'], possession_id=e['possession_id'], source_event=dict(file=str((folder/'events.jsonl').relative_to(SOURCE)), line=e['sequence']),
                     coverage='missing', reason='No existing exact-time tracking frame',
                     retrospective=dict(outcome=e.get('outcome','complete'), recipient=e.get('recipient_id'),
                                        end_location=e['end_location'], duration_ms=e['duration_ms'], pass_height=e.get('pass_height')))
            if e['start_ms'] in frames:
                frame, ref = frames[e['start_ms']]
                try:
                    r['state'] = pre_state(frame,e,rs); r['coverage'] = 'exact'; r['reason'] = None
                    r['source_frame'] = {**ref, 't_ms':frame['t_ms'], 'alignment_ms':0}
                    r['chosen'] = actual_key(r['state'],e)
                    r['choice_resolution'] = 'type_and_recipient_standardised' if e['type']=='pass' else 'type_only_standardised' if e['type']=='carry' else 'ordinary_shot'
                except ValueError as error: r['reason'] = str(error)
            records.append(r)
        path = root/'reconstruction'/f'{f["match_id"]}.json.gz'; write(path, records)
        count = sum(r['coverage']=='exact' for r in records)
        entry = dict(match_id=f['match_id'], matchweek=f['matchweek'], full_tracking=full, decisions=len(nodes),
                     covered=count, missing=len(nodes)-count, automatic_carries_excluded=auto)
        totals.update(decisions=len(nodes), covered=count, missing=len(nodes)-count, automatic_carries_excluded=auto)
        matches.append(entry); print(f'Audited {f["match_id"]}: {count}/{len(nodes)} exact states', flush=True)
    mapping = []
    for i,name in enumerate(FEATURES):
        source = 'candidate_derived' if i in (5,6,7,8,10,13,14,17,19,25,26) else 'derived'
        mapping.append(dict(field=name, provenance=source, available_pre_decision=True,
                            limitation='Geometric heuristic or standardised action, not true intention' if source=='candidate_derived' else 'Derived from existing exact-time tracking'))
    write(root/'manifest.json',dict(schema_version=VERSION, source_commit=SOURCE_COMMIT, source_dataset='original',
          source_root='../league/', source_hashes=source_hashes, rules=CONFIG, analysis_rules_hash=rules_hash(), matches=matches,
          provenance=['observed_original','derived','candidate_derived','model_prediction','retrospective_only'],
          latent_states_generated=False, simulator_runs=0))
    write(root/'reports/coverage.json',dict(**totals, matches=matches, seconds=round(time.monotonic()-start,2),
          limitation='Only four full matches and existing previews; previews are selected around final goals and excluded from fitting/calibration/main metrics.'))
    write(root/'reports/field-mapping.json',dict(features=mapping, unavailable=['body_orientation','fatigue','attention_direction','true_intended_carry_target','true_planned_duration'],
          raw_fields='Original values are referenced; state mirrors preserve parsed source numbers without Float32 reduction.'))
    verify_anchors(root)


def report_analysis(root):
    """Reproducible B0/B1/B2/B3 and population reports, separate from truth claims."""
    groups={'all':[],'representative':[],'largest':[]};comparisons=[];fixed=[]
    for f in fixtures():
        folder=root/'matches'/f['match_id'];index=read_gzip(folder/'index.json.gz');rows=[]
        for block in index['blocks']:rows.extend(read_gzip(folder/block['path']))
        by={r['id']:r for r in rows};groups['all'].extend(rows)
        groups['representative'].extend(by[k] for k in index['representative_ids'])
        groups['largest'].extend(by[k] for k in index['largest_ids'])
        for r in rows:
            if r['actual_key'] is not None:
                fixed.append(next(a['relative_score'] for a in softmax(r['options'],.05) if a['key']==r['actual_key']))
        if f['tracking']=='full':
            comparisons.append(dict(match_id=f['match_id'],n=len(rows),
                 one_two_top_agreement=sum(max((a for a in r['options'] if a['one_step_q'] is not None),key=lambda a:a['one_step_q'])['key']==r['best_key'] for r in rows)/len(rows),
                 baseline_main_top_agreement=sum(max((a for a in r['options'] if a['baseline_B1'] is not None),key=lambda a:a['baseline_B1']['q'])['key']==r['best_key'] for r in rows)/len(rows)))
    def distribution(rows):
        values=[r['relative_score'] for r in rows if r['relative_score'] is not None];losses=[r['loss'] for r in rows if r['loss'] is not None]
        return dict(n=len(rows),actual_scored=len(values),best_actions=Counter(r['best_key'].split(':')[0] for r in rows),
                    zones=Counter(r['zone'] for r in rows),assumption_sensitive=sum(r['assumption_sensitive'] for r in rows),
                    conflicts=sum(bool(r['conflicts']) for r in rows),actual_share_le1=sum(v<=1 for v in values),
                    share_quantiles=np.quantile(values,[0,.1,.5,.9,1]).tolist() if values else [],
                    loss_quantiles=np.quantile(losses,[0,.1,.5,.9,1]).tolist() if losses else [])
    write(root/'reports/ablation.json',dict(groups={k:distribution(v) for k,v in groups.items()},full_match_comparisons=comparisons,
          fixed_temperature_005_actual_share_quantiles=np.quantile(fixed,[0,.1,.5,.9,1]).tolist(),
          notes=['B0/B1 teacher MAE, actual Brier and calibration are in training.json.',
                 'B2 and B3 share tactical probabilities; outcome Brier cannot distinguish their continuation values.',
                 'Tactical validation concerns scenario direction and assumptions, not independent real football accuracy.']))


def load_records(root, f):
    return read_gzip(root/'reconstruction'/f'{f["match_id"]}.json.gz')


def cell(point, team, period):
    return min(7,max(0,int(point[1]/68*8)))*12+min(11,max(0,int(progress(point[0],team,period)/105*12)))


def learn_xt(fs):
    shots=np.zeros(96);goals=shots.copy();moves=shots.copy();trans=np.zeros((96,96))
    for f in fs:
        for e in read_lines(SOURCE/f['paths']['events']):
            if e['type'] not in ('pass','carry','shot'):continue
            team=int(e['team_id']=='AWAY');i=cell(e['start_location'],team,e['period'])
            if e['type']=='shot':shots[i]+=1;goals[i]+=e['outcome']=='goal'
            else:
                moves[i]+=1
                if e.get('outcome','complete')=='complete':trans[i,cell(e['end_location'],team,e['period'])]+=1
    shoot=(shots+2*shots.sum()/max(1,(shots+moves).sum()))/(shots+moves+2)
    goal=(goals+10*(goals.sum()+.5)/(shots.sum()+1))/(shots+10)
    transition=(trans+2*trans.sum(axis=0)/max(1,moves.sum()))/(moves[:,None]+2)
    v=np.zeros(96)
    for _ in range(1000):
        nxt=shoot*goal+(1-shoot)*(transition@v)
        if np.max(np.abs(nxt-v))<1e-6:v=nxt;break
        v=nxt
    return v,(shots+moves)


def pass_samples(records):
    x=[];y=[];outcomes=[];observed=[]
    for r in records:
        if r['coverage']!='exact':continue
        s=r['state']; opts=[a for a in candidate_actions(s) if a['kind']=='pass' and a['supported'] and not a['recipient'].endswith('01')]
        if not opts:continue
        selected={opts[int(r['id'][1:])%len(opts)]['key']:opts[int(r['id'][1:])%len(opts)]}
        if r['kind']=='pass':
            actual=next((a for a in opts if a['key']==r['chosen']),None)
            if actual:selected[actual['key']]=actual
        for a in selected.values():
            f=geometry(s,a)[0];x.append(f);y.append(original_probability(s,a,f))
            outcomes.append(r['retrospective']['outcome']=='complete');observed.append(r['kind']=='pass' and a['key']==r['chosen'])
    return np.asarray(x,dtype=float),np.asarray(y),np.asarray(outcomes),np.asarray(observed,dtype=bool)


def train(root):
    verify_anchors(root)
    if len(read_json(root/'manifest.json')['matches']) != 40:
        raise ValueError('Audit all 40 matches before fitting; preview-only/partial audit is not training coverage')
    start=time.monotonic();fs=fixtures();covered={f['match_id']:load_records(root,f) for f in fs if f['tracking']=='full'}
    reports=[];(root/'.models').mkdir(exist_ok=True)
    for fold in range(4):
        test=[f for f in fs if fold_for(f)==fold];cal=[f for f in fs if fold_for(f)==(fold+1)%4];fit=[f for f in fs if fold_for(f) not in (fold,(fold+1)%4)]
        xt,counts=learn_xt(fit);samples=pass_samples([r for f in fit if f['tracking']=='full' for r in covered[f['match_id']]])
        x,y,_,_=samples;models={};metrics={}
        for name,cols in [('B0',BASIC),('B1',list(range(len(FEATURES))))]:
            model=HistGradientBoostingRegressor(max_iter=150,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=50,l2_regularization=1,early_stopping=False,random_state=20261021)
            model.fit(x[:,cols],y);models[name]=model;metrics[name]={}
            for label,group in [('calibration',cal),('test',test)]:
                xx,yy,out,mask=pass_samples([r for f in group if f['tracking']=='full' for r in covered[f['match_id']]])
                p=np.clip(model.predict(xx[:,cols]),0,1)
                metrics[name][label]=dict(n=len(p),observed_n=int(mask.sum()),teacher_mae=float(np.mean(np.abs(p-yy))),
                      brier=float(np.mean((p[mask]-out[mask])**2)),analytic_brier=float(np.mean((yy[mask]-out[mask])**2)),
                      bins=[dict(n=int(((p[mask]>=i/5)&(p[mask] <= (i+1)/5 if i==4 else p[mask]<(i+1)/5)).sum()),
                            predicted=float(p[mask][(p[mask]>=i/5)&(p[mask] <= (i+1)/5 if i==4 else p[mask]<(i+1)/5)].mean()) if ((p[mask]>=i/5)&(p[mask] <= (i+1)/5 if i==4 else p[mask]<(i+1)/5)).any() else None,
                            actual=float(out[mask][(p[mask]>=i/5)&(p[mask] <= (i+1)/5 if i==4 else p[mask]<(i+1)/5)].mean()) if ((p[mask]>=i/5)&(p[mask] <= (i+1)/5 if i==4 else p[mask]<(i+1)/5)).any() else None) for i in range(5)])
        report=dict(fold=fold,fitting=[f['match_id'] for f in fit],calibration=[f['match_id'] for f in cal],test=[f['match_id'] for f in test],
                    fitting_spatial=[f['match_id'] for f in fit if f['tracking']=='full'],metrics=metrics,
                    features=FEATURES,xt_values=xt.tolist(),xt_counts=counts.tolist(),
                    carry='Original retention is 1 by construction; no negative examples invented',shot='Original analytic goal formula; unconditional save probability .43')
        joblib.dump(dict(version=VERSION,models=models,xt=xt,counts=counts,report=report),root/'.models'/f'fold-{fold}.joblib',compress=3)
        reports.append(report);print(f'Trained fold {fold+1}/4: 20 fit, 10 calibration, 10 test; 2 full spatial fit matches',flush=True)
    write(root/'reports/training.json',dict(version=VERSION,folds=reports,training_seconds=round(time.monotonic()-start,2),
          method='Original-rule pass distillation only; tactical priors and two-step Q are hypotheses, not learned real football probabilities.'))


def baseline_values(s, options, xt, model, cols):
    eligible=[a for a in options if a.get('q') is not None];features=np.asarray([list(a['features'].values()) for a in eligible])
    preds=np.clip(model.predict(features[:,cols]),0,1) if len(eligible) else []
    out={}
    for a,pred in zip(eligible,preds):
        f=list(a['features'].values());p=original_probability(s,a,f)
        if p is None:out[a['key']]=None;continue
        if a['kind']=='pass':p=float(pred)
        if a['kind']=='shot':
            keeper=next(q for q in s['players'] if q['team']!=s['team'] and q['id'].endswith('01'))
            restart=[5.5 if progress(0,1-s['team'],s['period'])==0 else 99.5,34]
            q=p-.43*xt_value(s,xt,[keeper['x'],keeper['y']],1-s['team'])-(1-p-.43)*xt_value(s,xt,restart,1-s['team'])
        else:q=p*xt_value(s,xt,a['target'])-(1-p)*xt_value(s,xt,a['failure_target'],1-s['team'])
        out[a['key']]=dict(probability=p,q=float(q),provenance='original_mechanism_distillation' if a['kind']=='pass' else 'original_mechanism')
    return out


def representative(rows):
    eligible=[r for r in rows if r.get('relative_score') is not None and not r.get('assumption_sensitive')]
    chosen=[];players=Counter();teams=Counter();categories=Counter();possessions=set()
    def allowed(r):
        return (r not in chosen and players[r['actor']]<2 and teams[r['team']]<10 and r['possession_id'] not in possessions
                and categories[r['category']]<{'opportunity':10,'favoured':5,'similar':5}[r['category']]
                and all(r['actor']!=q['actor'] or abs(r['t']-q['t'])>=30000 for q in chosen))
    def pick(team,zone=None):
        pool=[r for r in eligible if r['team']==team and (zone is None or r['zone']==zone) and allowed(r)]
        if not pool:return False
        # Rotate actors before considering value loss. Stable deterministic ties.
        r=min(pool,key=lambda r:(players[r['actor']],categories[r['category']],-r['loss'] if r['category']=='opportunity' else -r['best_q'],r['t'],r['id']))
        chosen.append(r);players[r['actor']]+=1;teams[team]+=1;categories[r['category']]+=1;possessions.add(r['possession_id']);return True
    for zone,n in [(0,3),(1,3),(2,4)]:
        for _ in range(n):
            for team in (0,1):pick(team,zone)
    for _ in range(10):
        for team in (0,1):
            if len(chosen)<20:pick(team)
    return [r['id'] for r in sorted(chosen,key=lambda r:r['t'])]


def analyse_record(r,bundle,defense=True):
    s=r['state'];xt=bundle['xt'];options=evaluate(s,xt,2)
    alternatives=[evaluate(s,xt,2,scale) for scale in (.8,1.2)]
    supported=[a for a in options if a['q'] is not None];best=supported[0]
    sensitive=any(next(a['key'] for a in variant if a['q'] is not None)!=best['key'] for variant in alternatives)
    b0=baseline_values(s,options,xt,bundle['models']['B0'],BASIC)
    b1=baseline_values(s,options,xt,bundle['models']['B1'],list(range(len(FEATURES))))
    one={a['key']:a['q'] for a in evaluate(s,xt,1)}
    for a in options:
        a['baseline_B0']=b0.get(a['key']);a['baseline_B1']=b1.get(a['key']);a['one_step_q']=one[a['key']]
        a['sensitivity_q']=[next(b['q'] for b in v if b['key']==a['key']) for v in alternatives]
    actual=next((a for a in supported if a['key']==r['chosen']),None)
    conflicts=[]
    if actual is None:conflicts.append('Actual choice cannot be mapped to supported standardised candidates')
    if actual and r['kind']=='pass' and r['retrospective']['pass_height']=='ground' and actual['mode']=='high':
        conflicts.append('Historical ground pass exceeds 35m ground support; high-ball alternative is not the same actual action');actual=None
    if r['kind']=='carry':
        conflicts.append('Only action type is recoverable; forward is a standardised carry, not observed intended direction')
    r={**r,'options':options,'assumption_sensitive':sensitive,'conflicts':conflicts,
       'actual_evaluable':actual is not None,'actual_key':actual['key'] if actual else None,'best_key':best['key'],'best_q':best['q'],
       'loss':max(0,best['q']-actual['q']) if actual else None,
       'zone':min(2,int(progress(s['ball'][0],s['team'],s['period'])/35)),
       'evaluation_mode':'tactical_assumption_two_step','defense':defensive_diagnostics(s,xt,options) if defense else []}
    gaps=[best['q']-a['q'] for a in supported];epsilon=max(.002,max(abs(a['sensitivity_q'][j]-a['q']) for a in supported for j in (0,1)))
    r['epsilon_q']=epsilon
    r['category']='similar' if (r['loss'] is not None and r['loss']<=epsilon and len(gaps)>1 and gaps[1]<=epsilon) else 'favoured' if r['loss'] is not None and r['loss']<=epsilon else 'opportunity'
    return r


def finish_record(r,temperature):
    r['options']=softmax(r['options'],temperature)
    actual=next((a for a in r['options'] if a['key']==r['actual_key']),None)
    r['relative_score']=actual['relative_score'] if actual else None
    r['temperature']=temperature;r['candidate_count']=sum(a['relative_score'] is not None for a in r['options'])
    r['model_version']=VERSION;r['provenance']='model_prediction'
    return r


def score(root,only=None):
    verify_anchors(root);start=time.monotonic();fs=fixtures();processed={};temperatures={};models={};gaps_by_fold={}
    for fold in range(4):
        bundle=joblib.load(root/'.models'/f'fold-{fold}.joblib');models[fold]=bundle
        cal_full=next(f for f in fs if fold_for(f)==(fold+1)%4 and f['tracking']=='full')
        cache=root/'.work'/f'{cal_full["match_id"]}-fold-{fold}.json.gz'
        model_hash=hashlib.sha256((root/'.models'/f'fold-{fold}.joblib').read_bytes()).hexdigest()
        if cache.exists() and read_gzip(cache).get('model_hash')==model_hash and read_gzip(cache).get('rules_hash')==rules_hash():
            rows=read_gzip(cache)['rows']
        else:
            rows=[]
            for n,r in enumerate(load_records(root,cal_full)):
                if r['coverage']=='exact':rows.append(analyse_record(r,bundle))
                if n%200==0:print(f'Calibration fold {fold+1}: {n} states',flush=True)
            write(cache,dict(model_hash=model_hash,rules_hash=rules_hash(),rows=rows))
        processed[(cal_full['match_id'],fold)]=rows
        gaps=[r['best_q']-a['q'] for r in rows for a in r['options'] if a['q'] is not None]
        tau=float(np.clip(np.quantile(gaps,.9)/math.log(9),.02,.20)) if gaps and max(gaps)>0 else .05
        temperatures[fold]=tau;gaps_by_fold[fold]=dict(temperature=tau,source_match=cal_full['match_id'],n=len(rows),candidate_gaps=len(gaps))
    summary=[]
    for f in fs:
        if only and f['match_id'] not in only:continue
        fold=fold_for(f);bundle=models[fold];tau=temperatures[fold];records=load_records(root,f);rows=[];compact=[];by_id={}
        for n,r in enumerate(records):
            if r['coverage']=='exact':
                item=finish_record(analyse_record(r,bundle),tau);rows.append(item);by_id[item['id']]=item
            if n%250==0 and f['tracking']=='full':print(f'Scoring {f["match_id"]}: {n}/{len(records)}',flush=True)
        rep=representative(rows);largest=[r['id'] for r in sorted((r for r in rows if r['loss'] is not None),key=lambda r:-r['loss'])[:20]]
        folder=root/'matches'/f['match_id'];blocks=[]
        for p in folder.glob('scores-*.json.gz'):p.unlink()
        for i,begin in enumerate(range(0,len(rows),32)):
            group=rows[begin:begin+32];write(folder/f'scores-{i:03}.json.gz',group);blocks.append(dict(path=f'scores-{i:03}.json.gz',count=len(group)))
            for r in group:r['block']=i
        for r in records:
            scored=by_id.get(r['id'])
            compact.append(dict(id=r['id'],t=r['t'],period=r['period'],actor=r['actor'],team=r['team'],kind=r['kind'],
                   coverage=r['coverage'],reason=r['reason'],block=scored['block'] if scored else None,
                   relative_score=scored['relative_score'] if scored else None,loss=scored['loss'] if scored else None,
                   category=scored['category'] if scored else None,sensitive=scored['assumption_sensitive'] if scored else False,
                   conflicts=scored['conflicts'] if scored else [],possession_id=r['possession_id']))
        full=f['tracking']=='full';counts=Counter(r['best_key'].split(':')[0] for r in rows)
        m=dict(match_id=f['match_id'],total=len(records),covered=len(rows),missing=len(records)-len(rows),
               scored=sum(r['relative_score'] is not None for r in rows),conflicts=sum(bool(r['conflicts']) for r in rows),
               best_actions=counts,representative_players=len({by_id[k]['actor'] for k in rep}),representative_count=len(rep),
               representative_best_actions=Counter(by_id[k]['best_key'].split(':')[0] for k in rep),
               largest_best_actions=Counter(by_id[k]['best_key'].split(':')[0] for k in largest),full_tracking=full)
        summary.append(m)
        write(folder/'index.json.gz',dict(schema_version=VERSION,dataset='enriched-v1',source_dataset='original',match_id=f['match_id'],
            model_fold=fold,temperature=tau,calibration_source=gaps_by_fold[fold],summary=m,records=compact,blocks=blocks,
            model_hash=hashlib.sha256((root/'.models'/f'fold-{fold}.joblib').read_bytes()).hexdigest(),rules_hash=rules_hash(),
            representative_ids=rep,largest_ids=largest,full_tracking=full,source_hash=read_json(root/'manifest.json')['source_hashes'][f['paths']['metadata']],
            disclaimer='Tactical assumptions, not real football probabilities. Softmax scores sum to 100 per state. Previews are outcome-selected.'))
        print(f'Scored {f["match_id"]}: {len(rows)} covered, {len(rep)} representative; best {dict(counts)}',flush=True)
    elapsed=round(time.monotonic()-start,2)
    previous=read_json(root/'reports/scoring.json') if (root/'reports/scoring.json').exists() else {}
    summary=[read_gzip(root/'matches'/f['match_id']/'index.json.gz')['summary'] for f in fs if (root/'matches'/f['match_id']/'index.json.gz').exists()]
    write(root/'reports/scoring.json',dict(version=VERSION,matches=summary,temperatures=gaps_by_fold,
          scoring_seconds=elapsed if only is None else previous.get('scoring_seconds'),last_run_seconds=elapsed,last_run_matches=only or 'all',
          totals=dict(covered=sum(m['covered'] for m in summary),missing=sum(m['missing'] for m in summary),scored=sum(m['scored'] for m in summary)),
          bytes=sum(p.stat().st_size for p in root.rglob('*') if p.is_file() and '.work' not in p.parts),
          simulator_runs=0,latent_states=0,notes='Main Q is finite two-step geometry, not FQI. No preview used for training or temperature.'))
    verify_anchors(root)
    if only is None:report_analysis(root)


def validate(root):
    verify_anchors(root);manifest=read_json(root/'manifest.json');training=read_json(root/'reports/training.json');counts=Counter()
    for report in training['folds']:
        groups=[set(report[k]) for k in ('fitting','calibration','test')]
        assert list(map(len,groups))==[20,10,10]
        assert not(groups[0]&groups[1] or groups[0]&groups[2] or groups[1]&groups[2])
        assert len(report['fitting_spatial'])==2
    state_issues=[]
    for f in fixtures():
        folder=root/'matches'/f['match_id'];index=read_gzip(folder/'index.json.gz');rows=[]
        assert f['match_id'] in training['folds'][index['model_fold']]['test']
        assert index['rules_hash']==rules_hash()
        assert index['model_hash']==hashlib.sha256((root/'.models'/f'fold-{index["model_fold"]}.joblib').read_bytes()).hexdigest()
        assert index['calibration_source']['source_match'] in training['folds'][index['model_fold']]['calibration']
        assert .02 <= index['temperature'] <= .20
        for b in index['blocks']:rows+=read_gzip(folder/b['path'])
        assert len(rows)==index['summary']['covered']
        raw={r['id']:r for r in load_records(root,f)}
        needed={r['t'] for r in rows}
        source_folder=SOURCE/f['paths']['match']
        if f['tracking']=='full':
            source_frames={}
            with (source_folder/'tracking.jsonl').open() as stream:
                for line in stream:
                    frame=json.loads(line)
                    if frame['t_ms'] in needed:source_frames[frame['t_ms']]=frame
        else:
            source_frames={frame['t_ms']:frame for frame in read_json(source_folder/'preview-60s.json')['frames'] if frame['t_ms'] in needed}
        source_events={e['event_id']:e for e in read_lines(source_folder/'events.jsonl')}
        for r in rows:
            assert raw[r['id']]['coverage']=='exact' and r['source_frame']['alignment_ms']==0
            assert r['state']==raw[r['id']]['state']
            assert r['state']==pre_state(source_frames[r['t']],source_events[r['event_id']],r['state']['ratings'])
            state=r['state']
            assert all(math.isfinite(v) for p in state['players'] for v in (p['x'],p['y'],p['vx'],p['vy']))
            assert all(math.isfinite(v) for v in state['ball'])
            for p in state['players']:
                if not (0<=p['x']<=105 and 0<=p['y']<=68 and math.hypot(p['vx'],p['vy'])<=7.202):
                    state_issues.append(dict(match_id=f['match_id'],decision=r['id'],player=p['id'],reason='Original state outside nominal pitch/speed envelope; preserved'))
            opts=[a for a in r['options'] if a['relative_score'] is not None]
            assert abs(sum(a['relative_score'] for a in opts)-100)<1e-8
            assert len({a['key'] for a in r['options']})==len(r['options'])
            assert all(math.isfinite(a['q']) and a['relative_score']>=0 and abs(sum(a['probabilities'].values())-1)<1e-9 for a in opts)
            assert all(opts[i]['q']>=opts[i+1]['q'] and opts[i]['relative_score']>=opts[i+1]['relative_score'] for i in range(len(opts)-1))
            assert r['relative_score'] is None if not r['actual_evaluable'] else r['relative_score'] is not None
            assert len(r['defense'])<=3
        by={r['id']:r for r in rows};rep=[by[k] for k in index['representative_ids']]
        assert max(Counter(r['actor'] for r in rep).values(),default=0)<=2
        assert max(Counter(r['team'] for r in rep).values(),default=0)<=10
        assert len({r['possession_id'] for r in rep})==len(rep)
        categories=Counter(r['category'] for r in rep)
        assert categories['opportunity']<=10 and categories['favoured']<=5 and categories['similar']<=5
        for r in rep:
            assert r['relative_score'] is not None and not r['assumption_sensitive']
            assert all(r['actor']!=q['actor'] or r['id']==q['id'] or abs(r['t']-q['t'])>=30000 for q in rep)
        counts.update(matches=1,covered=len(rows),missing=index['summary']['missing'],representative=len(rep))
    write(root/'validation/result.json',dict(status='PASS',**counts,source_files=len(manifest['source_hashes'])-1,
          checks=['Source and generator SHA-256 unchanged','Exact-time existing tracking only','No simulator import or replay',
                  '4-fold match isolation','No preview fitting/calibration','Softmax sum, bounds and ranking','Representative player/team/possession/time constraints']))
    write(root/'validation/state-quality.json',dict(original_state_issues=state_issues,modified_original_values=0))
    print(json.dumps(dict(status='PASS',**counts),indent=2))


def get_decision_context(root,match_id,decision_id):
    if not all(c.isalnum() or c=='-' for c in match_id+decision_id):raise ValueError('Invalid stable key')
    folder=root/'matches'/match_id;idx=read_gzip(folder/'index.json.gz');row=next(r for r in idx['records'] if r['id']==decision_id)
    if row['block'] is None:return row
    return next(r for r in read_gzip(folder/idx['blocks'][row['block']]['path']) if r['id']==decision_id)


def compare_candidates(root,match_id,decision_id):
    r=get_decision_context(root,match_id,decision_id);return dict(id=decision_id,options=r.get('options',[]),temperature=r.get('temperature'))


def get_constraints(root,match_id,decision_id):
    r=get_decision_context(root,match_id,decision_id);return dict(coverage=r.get('coverage'),reason=r.get('reason'),conflicts=r.get('conflicts',[]),rules=CONFIG)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['audit','train','score','validate','all','explain'])
    p.add_argument('--output',type=Path,default=OUTPUT);p.add_argument('--match',action='append');p.add_argument('--decision')
    args=p.parse_args();root=guarded_output(args.output)
    with threadpool_limits(limits=2):
        if args.command in ('audit','all'):audit(root,args.match)
        if args.command in ('train','all'):train(root)
        if args.command in ('score','all'):score(root,args.match)
        if args.command in ('validate','all'):validate(root)
        if args.command=='explain':print(json.dumps(get_decision_context(root,args.match[0],args.decision),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
