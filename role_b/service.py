"""Bounded source registry and contract HTTP handling; no remote model calls."""
import copy
import gzip
import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from contracts import check_exchange
from .comparison import compare_actions, rollout_action
from .input_adapter import NEUTRAL_PROFILE, match_actual, source_profile

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/contracts'


def read(path):
    return json.loads(Path(path).read_text())


class Repository:
    def __init__(self, root=None):
        catalog = read(ROOT/'role_b/catalog.json') if (ROOT/'role_b/catalog.json').exists() else {'datasets': {}}
        self.root = Path(root) if root else ROOT/catalog['datasets'][catalog['default_dataset']]
        self.manifest = read(self.root/'manifest.json')
        self.matches = {m['match_id']: m for m in self.manifest['matches']}
        self.case_meta = read(FIXTURES/'match.json')['metadata']

    @lru_cache(maxsize=8)
    def metadata(self, match_id):
        if match_id == self.case_meta['match_id']:
            return self.case_meta
        if match_id not in self.matches:
            raise KeyError('Unknown registered match')
        return read(self.root/'matches'/match_id/'metadata.json')

    @lru_cache(maxsize=8)
    def index(self, match_id):
        if match_id not in self.matches:raise KeyError('Unknown registered match')
        return read(self.root/'matches'/match_id/'index.json')

    @lru_cache(maxsize=4)
    def records(self, match_id):
        if match_id not in self.matches:raise KeyError('Unknown registered match')
        with gzip.open(self.root/'matches'/match_id/'snapshots.jsonl.gz','rt') as stream:
            result={}
            for line in stream:
                row=json.loads(line);sid=row['snapshot']['snapshot_id']
                if sid in result:raise ValueError('Ambiguous duplicate source snapshot; require explicit decision resolution')
                result[sid]=row
            return result

    @lru_cache(maxsize=4)
    def model_for(self, match_id):
        for path in sorted((self.root/'models').glob('fold-*.json')):
            model=read(path)
            if model['source_commit']!=self.manifest['source_commit'] or model['source_data_version']!=self.manifest['dataset_version']:
                raise ValueError('Model/source version mismatch; retrain in a separate output')
            if match_id in model['test_matches'] or match_id==self.case_meta['match_id']:
                return model
        raise KeyError('No isolated model for this match')

    def resolve(self, request):
        match_id=request['match_id'];sid=request['snapshot_id'];meta=self.metadata(match_id)
        if match_id==self.case_meta['match_id']:
            for case in 'abc':
                snap=read(FIXTURES/f'case-{case}/snapshot.json')
                if snap['snapshot_id']==sid:return snap,meta,None
            raise KeyError('Unknown fixed snapshot')
        record=self.records(match_id).get(sid)
        if record is None:raise KeyError('Unknown source snapshot')
        return record['snapshot'],meta,self.index(match_id)['ratings']

    @staticmethod
    def request_for(snapshot):
        return dict(match_id=snapshot['match_id'],snapshot_id=snapshot['snapshot_id'],t_ms=snapshot['t_ms'],
                    actor_id=snapshot['frame']['ball_owner_id'],question='Compare current choices',
                    mode='fan',language='en',profile=copy.deepcopy(NEUTRAL_PROFILE))

    def compare(self, request):
        snapshot,meta,ratings=self.resolve(request)
        return compare_actions(request,snapshot,meta,self.model_for(request['match_id']),ratings)

    def bundle(self, request):
        snapshot,meta,_=self.resolve(request)
        actor=next(p for p in meta['players'] if p['player_id']==request['actor_id'])
        return dict(snapshot=snapshot,metadata=meta,request=request,source_profile=source_profile(actor),
                    source_commit=self.manifest['source_commit'])

    def actual(self, request, response):
        """Separate presentation-only path; call after comparison is complete."""
        if request['match_id']==self.case_meta['match_id']:
            case=next(c for c in 'abc' if read(FIXTURES/f'case-{c}/snapshot.json')['snapshot_id']==request['snapshot_id'])
            context=read(FIXTURES/f'case-{case}/context.json')
            key=context['actual_action_id'];resolution='Fixed development-case presentation context only'
        else:
            record=self.records(request['match_id'])[request['snapshot_id']]
            key,resolution=match_actual(record['retrospective'],response['candidates'])
        updated=copy.deepcopy(response)
        for c in updated['candidates']:c['is_actual']=c['action_id']==key
        snapshot,metadata,_=self.resolve(request)
        check_exchange(request,updated,snapshot,metadata)
        return dict(response=updated,resolution=resolution,actual_action_id=key,
                    note='Historical selection is revealed only after computation; no future endpoint enters the ranking.')


@lru_cache(maxsize=1)
def repository():
    return Repository()


def handle_get(handler):
    route=urlsplit(handler.path)
    if not route.path.startswith('/api/role-b/'):
        return False
    if not handler.trusted():
        handler.reply(403,{'error':'Only same-origin local requests are accepted.'});return True
    try:
        repo=repository();query=parse_qs(route.query)
        if route.path=='/api/role-b/catalog':
            cases=[]
            for c in 'abc':
                request=read(FIXTURES/f'case-{c}/request.json')
                cases.append(dict(case_id=c,label={'a':'A · Open receiver, intercepted lane','b':'B · Shoot or pass again','c':'C · Recycle under pressure'}[c],
                                  **repo.bundle(request)))
            handler.reply(200,dict(cases=cases,dataset=repo.root.name,source_commit=repo.manifest['source_commit'],
                                  totals=repo.manifest['totals'],matches=[dict(match_id=m['match_id'],covered=m['counts'].get('covered',0)) for m in repo.matches.values()]))
        elif route.path=='/api/role-b/decisions':
            match=query.get('match_id',[''])[0]
            handler.reply(200,repo.index(match))
        elif route.path=='/api/role-b/snapshot':
            match=query.get('match_id',[''])[0];sid=query.get('snapshot_id',[''])[0]
            row=repo.records(match).get(sid)
            if row is None:raise KeyError('Unknown snapshot')
            handler.reply(200,repo.bundle(repo.request_for(row['snapshot'])))
        else:
            handler.reply(404,{'error':'Unknown role-B route'})
    except (KeyError,StopIteration,FileNotFoundError):handler.reply(404,{'error':'Registered dataset, match, snapshot or model unavailable'})
    except (ValueError,TypeError):handler.reply(400,{'error':'Invalid source or request identity'})
    return True


def handle_post(handler, route, data):
    aliases={'/api/decision':'/api/role-b/analysis','/api/compare-actions':'/api/role-b/analysis','/api/rollout':'/api/role-b/rollout'}
    route=aliases.get(route,route)
    if not route.startswith('/api/role-b/'):
        return False
    try:
        repo=repository()
        if route=='/api/role-b/analysis':
            public,_=repo.compare(data);handler.reply(200,public)
        elif route in ('/api/role-b/rollout','/api/role-b/routes','/api/role-b/actual','/api/role-b/agents'):
            expected={'request','candidate_set_id'} | ({'action_id'} if route.endswith('/rollout') else set())
            if not isinstance(data,dict) or set(data)!=expected:raise ValueError('Invalid bound-action request')
            request=data['request'];public,private=repo.compare(request)
            if data['candidate_set_id']!=public['candidate_set_id']:raise ValueError('Stale candidate set')
            snapshot,metadata,_=repo.resolve(request)
            if route.endswith('/rollout'):
                handler.reply(200,rollout_action(request,public,private,snapshot,metadata,data['action_id'],data['candidate_set_id']))
            elif route.endswith('/routes'):
                legal={c['action_id'] for c in public['candidates'] if c['eligible']}
                handler.reply(200,dict(snapshot_id=snapshot['snapshot_id'],candidate_set_id=public['candidate_set_id'],
                    routes=[dict(action_id=a['key'],start=snapshot['frame']['ball'][:2],target=a['target'])
                            for a in private.get('options',[]) if a['key'] in legal]))
            elif route.endswith('/actual'):
                handler.reply(200,repo.actual(request,public))
            else:
                # No fabricated model trace or silent deterministic impersonation.
                handler.reply(200,public)
        else:handler.reply(404,{'error':'Unknown role-B route'})
    except (KeyError,StopIteration,FileNotFoundError):handler.reply(404,{'error':'Unknown registered match/snapshot/model/action'})
    except (ValueError,TypeError):handler.reply(400,{'error':'Invalid or stale contract request; recompute comparison'})
    return True
