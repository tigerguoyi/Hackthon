"""Scoped dependency adapter around the unchanged B Q2 function bodies.

Each call owns its globals dictionary: no monkey-patching, shared direction state,
or outcome-model replacement. Legacy evaluate() and its cached hashes stay intact.
"""
import copy
from types import FunctionType

import enriched_rules as legacy

VERSION = 'outcome-2.0'
PASS_SPEED = 14.0  # A rollout's exact ground-ball template; contract mode only.


def _scope(state, contract_mode=True):
    s = copy.deepcopy(state)
    # B v1 identifies keepers by suffix. Resolve actual on-pitch roles first, and
    # keep the temporary aliases private; all returned IDs map back to A's IDs.
    aliases = {}
    for team, prefix in enumerate('HA'):
        active = [p for p in s['players'] if p['team'] == team]
        keepers = [p for p in active if p.get('position') == 'GK']
        if not keepers:
            keepers = [p for p in active if p['id'] == prefix + '01']
        if len(keepers) != 1 or not any(p not in keepers for p in active):
            raise ValueError('Q2 requires a known active goalkeeper and outfield player per team')
        keeper = keepers[0]
        if keeper['id'] != prefix + '01':
            if any(p['id'] == prefix + '01' for p in active):
                spare = next(prefix + str(i) for i in range(99, 49, -1) if all(p['id'] != prefix + str(i) for p in active))
                aliases[prefix + '01'] = spare
            aliases[keeper['id']] = prefix + '01'
    reverse = {v: k for k, v in aliases.items()}
    for p in s['players']:
        p['id'] = aliases.get(p['id'], p['id'])
    s['actor'] = aliases.get(s['actor'], s['actor'])
    ns = dict(vars(legacy))
    def direction(team, period):
        explicit = s.get('attacking_direction')
        if explicit is None:
            return legacy.direction(team, period)
        return 1 if explicit['AWAY' if team else 'HOME'] == 'positive_x' else -1
    ns['direction'] = direction
    for name in ('progress', 'candidate_actions', 'geometry', 'projected_state', 'xt_value', 'evaluate'):
        f = getattr(legacy, name)
        ns[name] = FunctionType(f.__code__, ns, f.__name__, f.__defaults__, f.__closure__)
    original_candidates, original_geometry = ns['candidate_actions'], ns['geometry']
    def candidates(current):
        rows = original_candidates(current)
        if not contract_mode:
            return rows
        actor = next(p for p in current['players'] if p['id'] == current['actor'])
        d = direction(current['team'], current['period'])
        opponents = [p for p in current['players'] if p['team'] != current['team']]
        to_progress = lambda x: x if d == 1 else 105-x
        line = max(52.5, to_progress(current['ball'][0]), sorted((to_progress(p['x']) for p in opponents), reverse=True)[1])
        for a in rows:
            if a['kind'] == 'pass':
                receiver = next(p for p in current['players'] if p['id'] == a['recipient'])
                offside = to_progress(receiver['x']) > line + 1e-6
                a['offside_at_release'] = offside
                a['duration'] = max(.05, legacy.distance(current['ball'][:2], a['target']) / PASS_SPEED)
                if a['mode'] != 'ground':
                    a.update(supported=False, reason='High-ball Q2 exists in legacy research mode; ground-pass contract rollout only')
                elif offside:
                    a.update(supported=False, reason='Receiver is offside at release')
            elif a['kind'] == 'shot' and actor['id'].endswith('01'):
                a.update(supported=False, reason='Goalkeeper shooting is outside this contract model support')
        return rows
    def geometry(current, action):
        features, defender, point = original_geometry(current, action)
        if 'duration_ms' in current:
            features[22] = current['t_ms'] / current['duration_ms']
        return features, defender, point
    ns['candidate_actions'], ns['geometry'] = candidates, geometry
    return s, ns, reverse


def evaluate(state, xt, horizon=2, risk_scale=1., contract_mode=True):
    s, ns, reverse = _scope(state, contract_mode)
    rows = ns['evaluate'](s, xt, horizon, risk_scale, s.get('period_end_ms'))
    for a in rows:
        for field in ('recipient', 'defender'):
            if field in a:
                a[field] = reverse.get(a[field], a[field])
        if a['kind'] == 'pass':
            a['key'] = 'pass:' + a['recipient']
    return rows


def features_for_keys(state, keys):
    s, ns, reverse = _scope(state)
    result = {}
    for action in ns['candidate_actions'](s):
        key = 'pass:' + reverse.get(action['recipient'], action['recipient']) if action['kind'] == 'pass' else action['key']
        if key in keys and action['supported']:
            result[key] = ns['geometry'](s, action)[0]
    return result
