"""Independent A-source audit/train/score/validate. Never runs a match engine.

All decisions are indexed. Expensive Q2 is available on demand for every indexed
snapshot; the offline score command also writes a reproducible stratified audit
sample. --all explicitly materialises every Q2 result when that is desired.
"""
import argparse
import gzip
import hashlib
import json
import math
import time
import subprocess
import re
from collections import Counter
from pathlib import Path

from contracts import data_version, make_snapshot
from contracts.model import digest
from enriched_rules import FEATURES
from .input_adapter import adapt_snapshot, match_actual

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = json.loads((Path(__file__).with_name('upstream.json')).read_text())
SOURCE_COMMIT = UPSTREAM['a_commit']
DEFAULT_OUTPUT = ROOT / 'data' / ('league-enriched-gen-' + SOURCE_COMMIT[:8])


def read(path):
    return json.loads(Path(path).read_text())


def lines(path):
    path = Path(path)
    with (gzip.open(path, 'rt') if path.suffix == '.gz' else path.open()) as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode()
    temp = path.with_name(path.name+'.tmp')
    temp.write_bytes(gzip.compress(raw, mtime=0) if path.suffix == '.gz' else raw)
    temp.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def guarded_output(path, source=None):
    output = Path(path).resolve()
    protected = [ROOT / 'data/league', ROOT / 'data/league-enriched-v1']
    if source is not None:
        protected.append(Path(source).resolve())
    for p in protected:
        p = p.resolve()
        if output == p or p in output.parents or output in p.parents:
            raise ValueError('Output overlaps an immutable source or legacy dataset')
    return output


def check_originals():
    anchors = read(ROOT / 'docs/role-b-original-anchors.json')
    for name, expected in anchors.items():
        if sha(ROOT / name) != expected:
            raise ValueError('Immutable B original changed: ' + name)


def ratings_for(meta, league):
    teams = {t['club_id']: t.get('rating') for t in league.get('teams', [])}
    ratings = [teams.get(t.get('club_id')) for t in meta['teams']]
    return ratings if all(ratings) else None


def audit(source, output=DEFAULT_OUTPUT):
    start = time.monotonic(); source = Path(source).resolve(); output = guarded_output(output, source)
    check_originals()
    if subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()!=SOURCE_COMMIT:
        raise ValueError('Audit requires the pinned A checkout, not another source version')
    folders = sorted((source / 'matches').glob('*'))
    if not folders:
        raise ValueError('Expected source/matches/<match_id>')
    league = read(source / 'league.json') if (source / 'league.json').exists() else {}
    metadata = [read(f / 'metadata.json') for f in folders]
    if any(not re.fullmatch(r'[A-Za-z0-9-]+',m['match_id']) for m in metadata):
        raise ValueError('Unsafe source match ID')
    dataset_version = 'gen-' + digest({'source_commit': SOURCE_COMMIT, 'metadata': metadata})
    if (output / 'manifest.json').exists():
        existing = read(output / 'manifest.json')
        current = {str(p.relative_to(source)):sha(p) for folder in folders for p in folder.iterdir() if p.is_file()}
        for filename in ('league.json','squads.json'):
            if (source/filename).exists():current[filename]=sha(source/filename)
        if existing['dataset_version'] != dataset_version or existing['source_hashes'] != current:
            raise ValueError('Existing output belongs to different source metadata/files; use a new output path')
    matches = []; source_hashes = {'league.json':sha(source/'league.json')} if (source/'league.json').exists() else {}; all_samples = []
    for number, (folder, meta) in enumerate(zip(folders, metadata), 1):
        match_id = meta['match_id']; ratings = ratings_for(meta, league)
        target = output / 'matches' / match_id
        write(target / 'metadata.json', meta)
        counts = Counter(); compact = []; samples = []
        source_decisions = folder / 'decisions.jsonl'
        if source_decisions.exists():
            decisions = lines(source_decisions); identification = 'generator_decision_snapshot'
        else:
            # Explicitly legacy fallback; never infer post-reception choices in new data.
            from enriched_pipeline import selected_decisions
            events = list(lines(folder / 'events.jsonl')); selected, _ = selected_decisions(events)
            needed = {e['start_ms'] for e in selected}
            tracking = folder / 'tracking.jsonl'
            frames = {f['t_ms']: f for f in lines(tracking) if f['t_ms'] in needed} if tracking.exists() else {
                f['t_ms']: f for f in read(folder / 'preview-60s.json')['frames'] if f['t_ms'] in needed}
            decisions = (dict(decision_id=e['event_id'], t_ms=e['start_ms'], period=e['period'],
                              snapshot=frames.get(e['start_ms']), selected={}, candidates=[]) for e in selected)
            identification = 'legacy_event_chain_fallback'
        with gzip.GzipFile(filename=str(target / 'snapshots.jsonl.gz'), mode='wb', mtime=0) as out:
            for index, decision in enumerate(decisions):
                counts['decisions'] += 1
                frame = decision.get('snapshot')
                if frame is None:
                    compact.append(dict(decision_id=decision['decision_id'], coverage='missing', reason='No exact pre-action snapshot'))
                    counts['missing'] += 1; continue
                try:
                    snapshot = make_snapshot(meta, frame)
                    state = adapt_snapshot(snapshot, meta, ratings)
                    if state['actor'] != decision.get('player_id', state['actor']):
                        raise ValueError('Generator actor differs from current snapshot owner')
                    if state['t_ms'] != decision['t_ms'] or state['period'] != decision['period']:
                        raise ValueError('Generator decision and snapshot clocks disagree')
                except (ValueError, KeyError, StopIteration) as error:
                    compact.append(dict(decision_id=decision['decision_id'], coverage='conflict', reason=str(error)))
                    counts['conflict'] += 1; continue
                # Selection stays in a separate retrospective branch; not in make_snapshot/state.
                selected = decision.get('selected', {})
                recipient = selected.get('recipient_id')
                if selected.get('kind') == 'pass' and recipient is None:
                    recipient = next((c.get('recipient_id') for c in decision.get('candidates', [])
                                      if c.get('kind') == 'pass' and c.get('recipient') == selected.get('recipient')), None)
                actual = dict(selected={'kind': selected.get('kind'), 'recipient_id': recipient},
                              choice_probability=selected.get('probability'),
                              action_policy_revision=decision.get('action_policy_revision'),
                              intention_resolution='Carry direction not logged before execution' if selected.get('kind') == 'carry' else 'type/recipient only')
                record = dict(decision_id=decision['decision_id'], snapshot=snapshot, retrospective=actual)
                out.write((json.dumps(record, separators=(',', ':'), allow_nan=False)+'\n').encode())
                compact.append(dict(decision_id=decision['decision_id'], snapshot_id=snapshot['snapshot_id'],
                                    t_ms=snapshot['t_ms'], actor_id=state['actor'], coverage='exact',
                                    kind=selected.get('kind', 'unknown')))
                counts['covered'] += 1
                # B0/B1 are re-distilled against A's PRE-SAMPLING pass labels only.
                available = [c for c in decision.get('candidates', []) if c.get('kind') == 'pass' and c.get('recipient_id') and
                             type(c.get('success')) in (float, int)]
                chosen = []
                if available:
                    chosen.append(available[index % len(available)])
                    chosen += [c for c in available if c['recipient_id'] == recipient and c not in chosen]
                    from .q2 import features_for_keys
                    features = features_for_keys(state, {'pass:'+c['recipient_id'] for c in chosen})
                    for c in chosen:
                        key = 'pass:'+c['recipient_id']
                        if key in features:
                            samples.append(dict(match_id=match_id, features=features[key], teacher=float(c['success']),
                                observed_action_id=decision.get('action_id') if selected.get('kind')=='pass' and c['recipient_id']==recipient else None))
        all_samples.extend(samples)
        write(target / 'index.json', dict(match_id=match_id, data_version=data_version(meta),
            identification=identification, counts=counts, records=compact, ratings=ratings))
        hashes = {str(p.relative_to(source)): sha(p) for p in folder.iterdir() if p.is_file()}
        source_hashes.update(hashes)
        matches.append(dict(match_id=match_id, matchweek=next((f['matchweek'] for f in league.get('weeks', []) for f in f.get('matches', []) if f['match_id']==match_id),
                           int(match_id.split('-W')[1][:2]) if '-W' in match_id else number),
                           data_version=data_version(meta), counts=counts, identification=identification,
                           full_tracking=(folder/'tracking.jsonl').exists(), decision_cache=source_decisions.exists()))
        print(f'Audit {number}/{len(folders)} {match_id}: {dict(counts)}', flush=True)
    if (source/'squads.json').exists():
        write(output/'source-squads.json', read(source/'squads.json'))
        source_hashes['squads.json'] = sha(source/'squads.json')
    write(output / '.training/pass-labels.json.gz', all_samples)
    manifest = dict(schema_version='role-b-source-1', source_commit=SOURCE_COMMIT,
        source_repository='https://github.com/LiNTe912/NextPlay-AI', source_dataset=source.name,
        dataset_version=dataset_version, source_hashes=source_hashes, matches=matches,
        totals={k:sum(m['counts'].get(k,0) for m in matches) for k in ('decisions','covered','missing','conflict')},
        source_reads_only=True, simulator_runs=0, audit_seconds=round(time.monotonic()-start,2))
    write(output/'manifest.json', manifest)
    write(output/'reports/field-mapping.json',dict(
        features=[dict(name=name,source='current snapshot + standardised candidate geometry',future_input=False) for name in FEATURES],
        explicit_notes=dict(direction='snapshot.attacking_direction; periods come from metadata',
            duration='planned template duration, not actual duration',team_ratios='source league ratings; missing ratings labelled neutral',
            state_quality='constant 1 for verified exact snapshots',receiver_reachable='derived legacy feature, not a complete biomechanical constraint'),
        not_features=['selected.probability','candidate.success teacher label','event outcome','future endpoint','future goals','source radar as causal ability','weak-foot default']))
    write(ROOT/'role_b/catalog.json',dict(default_dataset=output.name, datasets={output.name: str(output.relative_to(ROOT))}))
    check_originals()
    return manifest


def learn_xt(matches, source):
    shots=[0.]*96; goals=[0.]*96; moves=[0.]*96; trans=[[0.]*96 for _ in range(96)]
    def cell(point, team, period):
        d=period['home_attacks']; d=d if team=='HOME' else ('negative_x' if d=='positive_x' else 'positive_x')
        x=point[0] if d=='positive_x' else 105-point[0]
        return min(7,max(0,int(point[1]/68*8)))*12+min(11,max(0,int(x/105*12)))
    for match in matches:
        folder=Path(source)/'matches'/match['match_id'];meta=read(folder/'metadata.json')
        periods={p['period']:p for p in meta['periods']}
        for e in lines(folder/'events.jsonl'):
            if e['type'] not in ('pass','carry','shot'):continue
            i=cell(e['start_location'],e['team_id'],periods[e['period']])
            if e['type']=='shot':shots[i]+=1;goals[i]+=e.get('outcome')=='goal'
            else:
                moves[i]+=1
                if e.get('outcome','complete')=='complete':trans[i][cell(e['end_location'],e['team_id'],periods[e['period']])]+=1
    global_shot=sum(shots)/max(1,sum(shots)+sum(moves));global_goal=(sum(goals)+.5)/(sum(shots)+1)
    destination=[sum(row[j] for row in trans)/max(1,sum(moves)) for j in range(96)]
    shoot=[(shots[i]+2*global_shot)/(shots[i]+moves[i]+2) for i in range(96)]
    goal=[(goals[i]+10*global_goal)/(shots[i]+10) for i in range(96)]
    transition=[[(trans[i][j]+2*destination[j])/(moves[i]+2) for j in range(96)] for i in range(96)]
    value=[0.]*96
    for iteration in range(1000):
        new=[shoot[i]*goal[i]+(1-shoot[i])*sum(t*v for t,v in zip(transition[i],value)) for i in range(96)]
        change=max(abs(a-b) for a,b in zip(new,value));value=new
        if change<1e-6:break
    return value, dict(iterations=iteration+1, residual=change, shots=sum(shots), goals=sum(goals), moves=sum(moves),
                       cell_counts=[shots[i]+moves[i] for i in range(96)])


def train(source, output=DEFAULT_OUTPUT):
    import numpy as np
    import joblib
    from sklearn.ensemble import HistGradientBoostingRegressor
    from threadpoolctl import threadpool_limits
    start=time.monotonic();output=guarded_output(output,source);manifest=read(output/'manifest.json')
    # Recheck the exact source, rather than fitting against an upgraded checkout.
    for name,expected in manifest['source_hashes'].items():
        if sha(Path(source)/name)!=expected:raise ValueError('Source changed after audit: '+name)
    samples=json.loads(gzip.decompress((output/'.training/pass-labels.json.gz').read_bytes()))
    matches=manifest['matches'];weeks=sorted({m['matchweek'] for m in matches})
    if len(weeks)<3:raise ValueError('Need at least three match groups for isolated fitting/calibration/test')
    outcomes={}
    for match in matches:
        for event in lines(Path(source)/'matches'/match['match_id']/'events.jsonl'):
            if event['type']=='pass' and event.get('action_id'):
                outcomes[(match['match_id'],event['action_id'])]=float(event.get('outcome','complete')=='complete')
    reports=[]
    for fold,week in enumerate(weeks):
        cal_week=weeks[(fold+1)%len(weeks)];test=[m for m in matches if m['matchweek']==week]
        cal=[m for m in matches if m['matchweek']==cal_week]
        fit=[m for m in matches if m['matchweek'] not in (week,cal_week)]
        xt,stats=learn_xt(fit,source)
        model=dict(model_id='B-Q2-'+digest({'dataset':manifest['dataset_version'],'xt':xt,'fit':[m['match_id'] for m in fit],'fold':fold}),
            source_commit=manifest['source_commit'], source_data_version=manifest['dataset_version'], xt=xt,
            temperature=.05, temperature_source='provisional fixed display scale; replaced by held-out calibration during score',
            fitting_matches=[m['match_id'] for m in fit],calibration_matches=[m['match_id'] for m in cal],
            test_matches=[m['match_id'] for m in test],xt_training=stats,
            features=FEATURES, probability_role='A-rule teacher distillation baseline only; NOT main Q2 probability')
        metrics={};fit_ids=set(model['fitting_matches']);train_samples=[s for s in samples if s['match_id'] in fit_ids]
        xx=np.asarray([s['features'] for s in train_samples]);yy=np.asarray([s['teacher'] for s in train_samples])
        trees={}
        for name,cols in [('B0',list(range(9))+list(range(20,25))),('B1',list(range(30)))]:
            if not len(xx):continue
            tree=HistGradientBoostingRegressor(max_iter=150,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=50,
                l2_regularization=1,early_stopping=False,random_state=20261024)
            with threadpool_limits(limits=2):tree.fit(xx[:,cols],yy)
            trees[name]=tree;metrics[name]={}
            for label,ids in [('calibration',model['calibration_matches']),('test',model['test_matches'])]:
                rows=[s for s in samples if s['match_id'] in ids]
                if rows:
                    with threadpool_limits(limits=2):p=tree.predict(np.asarray([s['features'] for s in rows])[:,cols])
                    targets=np.asarray([s['teacher'] for s in rows])
                    observed=[(i,outcomes[(s['match_id'],s['observed_action_id'])]) for i,s in enumerate(rows)
                              if s.get('observed_action_id') and (s['match_id'],s['observed_action_id']) in outcomes]
                    metrics[name][label]=dict(samples=len(rows),teacher_mae=float(np.mean(np.abs(p-targets))),
                        observed_n=len(observed),brier=float(np.mean([(p[i]-y)**2 for i,y in observed])) if observed else None,
                        analytic_brier=float(np.mean([(targets[i]-y)**2 for i,y in observed])) if observed else None)
        (output/'.models').mkdir(exist_ok=True)
        joblib.dump(trees,output/'.models'/f'fold-{fold}.joblib',compress=3)
        write(output/'models'/f'fold-{fold}.json',model)
        reports.append(dict(fold=fold,fit=model['fitting_matches'],calibration=model['calibration_matches'],test=model['test_matches'],metrics=metrics,xt=stats))
        print(f'Train fold {fold}: {len(fit)} fit, {len(cal)} calibration, {len(test)} test; {len(train_samples)} pass labels',flush=True)
    write(output/'reports/training.json',dict(folds=reports,seconds=round(time.monotonic()-start,2),
          future_labels_used_as_features=False,method='New source xT + new A soft-label pass baselines; main tactical probabilities remain B assumptions'))


def _score_match(arguments):
    output, match, all_decisions = arguments
    from .service import Repository
    repo=Repository(output);match_id=match['match_id'];records=list(repo.records(match_id).values())
    selected=records if all_decisions else records[::max(1,len(records)//9)][:9]
    result=[];best=Counter();unmatched=0
    for record in selected:
        snap=record['snapshot'];request=repo.request_for(snap)
        public,private=repo.compare(request)
        actual,resolution=match_actual(record['retrospective'],public['candidates'])
        if actual is None:unmatched+=1
        top=next((c for c in public['candidates'] if c['rank']==1),None)
        if top:best[top['type']]+=1
        result.append(dict(decision_id=record['decision_id'],snapshot_id=snap['snapshot_id'],response=public,
                           actual_action_id=actual,actual_resolution=resolution))
    write(Path(output)/'matches'/match_id/'scored.json.gz',result)
    print(f'Score {match_id}: {len(result)} precomputed / {len(records)} available',flush=True)
    return dict(match_id=match_id,available=len(records),precomputed=len(result),best_actions=best,actual_unmatched=unmatched)


def score(output=DEFAULT_OUTPUT, all_decisions=False):
    from .comparison import compare_actions
    from .input_adapter import NEUTRAL_PROFILE
    from .service import Repository
    output=guarded_output(output);repo=Repository(output);manifest=repo.manifest;start=time.monotonic()
    stats=[]
    # Independently calibrate the display scale. Preview sampling is never used.
    for path in sorted((output/'models').glob('fold-*.json')):
        model=read(path);gaps=[];calibration_ids=[]
        for match_id in model['calibration_matches']:
            rows=repo.records(match_id)
            # Deterministic periodic sample from every complete decision stream.
            values=list(rows.values());step=max(1,len(values)//8)
            for record in values[::step][:8]:
                snap=record['snapshot'];meta=repo.metadata(match_id)
                request=dict(match_id=match_id,snapshot_id=snap['snapshot_id'],t_ms=snap['t_ms'],actor_id=snap['frame']['ball_owner_id'],
                    question='Compare current choices',mode='analyst',language='en',profile=NEUTRAL_PROFILE)
                public,private=compare_actions(request,snap,meta,model,repo.index(match_id)['ratings'])
                qs=[r['q'] for r in private.get('options',[]) if r.get('q') is not None]
                if qs:gaps.extend(max(qs)-q for q in qs);calibration_ids.append(snap['snapshot_id'])
        if gaps:
            ordered=sorted(gaps);pos=.9*(len(ordered)-1);lo=int(pos);hi=min(lo+1,len(ordered)-1)
            percentile=ordered[lo]*(hi-pos)+ordered[hi]*(pos-lo) if hi!=lo else ordered[lo]
            model['temperature']=min(.20,max(.02,percentile/math.log(9))) if max(gaps)>0 else .05
        model['temperature_source']='periodic pre-action snapshots from calibration matches only'
        model['temperature_snapshot_ids']=calibration_ids
        write(path,model);print(f'Calibrated {path.name}: {len(calibration_ids)} states',flush=True)
    repo=Repository(output)
    jobs=[(str(output),m,all_decisions) for m in manifest['matches']]
    if all_decisions:
        import multiprocessing
        with multiprocessing.get_context('spawn').Pool(4) as pool:
            stats=list(pool.imap_unordered(_score_match,jobs))
    else:
        stats=[_score_match(job) for job in jobs]
    stats.sort(key=lambda row:row['match_id'])
    write(output/'reports/scoring.json',dict(seconds=round(time.monotonic()-start,2),matches=stats,
        available=sum(s['available'] for s in stats),precomputed=sum(s['precomputed'] for s in stats),
        mode='all' if all_decisions else 'periodic audit sample + full on-demand Q2',
        note='Sample proportions are descriptive; no recommendation quotas or accuracy claim. No candidate future trajectories stored.'))


def validate(output=DEFAULT_OUTPUT):
    from contracts import check_exchange
    from .service import Repository
    check_originals();repo=Repository(output);counts=Counter()
    for match in repo.manifest['matches']:
        match_id=match['match_id'];meta=repo.metadata(match_id);model=repo.model_for(match_id)
        assert match_id in model['test_matches'] and match_id not in model['fitting_matches']+model['calibration_matches']
        count=0
        for record in lines(repo.root/'matches'/match_id/'snapshots.jsonl.gz'):
            snapshot=record['snapshot'];assert snapshot['data_version']==data_version(meta)
            # Recomputed IDs validate pre-action state integrity, not counterfactual truth.
            assert snapshot==make_snapshot(meta,snapshot['frame']);count+=1
        assert count==match['counts'].get('covered',0);counts.update(matches=1,covered=count)
        scored=repo.root/'matches'/match_id/'scored.json.gz'
        if scored.exists():
            for row in json.loads(gzip.decompress(scored.read_bytes())):
                record=repo.records(match_id)[row['snapshot_id']];snap=record['snapshot']
                check_exchange(repo.request_for(snap),row['response'],snap,meta);counts['public_scored']+=1
    write(repo.root/'validation/result.json',dict(status='PASS',**counts,originals_unchanged=True,simulator_runs=0))
    return dict(counts)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['audit','train','score','validate','all'])
    p.add_argument('--source',type=Path);p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--all-decisions',action='store_true',help='Materialise every Q2; default is a reproducible audit sample plus on-demand evaluation')
    args=p.parse_args();output=guarded_output(args.output,args.source)
    if args.command in ('audit','train','all') and args.source is None:p.error('--source is required for source audit/training')
    if args.command in ('audit','all'):audit(args.source,output)
    if args.command in ('train','all'):train(args.source,output)
    if args.command in ('score','all'):score(output,args.all_decisions)
    if args.command in ('validate','all'):print(validate(output))


if __name__=='__main__':main()
