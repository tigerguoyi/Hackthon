"""Read-only analysis assumptions, not a match generator or observed football truth."""
import copy
import math

VERSION = 'enriched-analysis-1.0'
CONFIG = dict(ground_max=35., lofted_max=60., pass_speed=14.5, lofted_speed=16.,
              carry_seconds=2., carry_metres=6., discount_seconds=120.,
              max_speed=7.2, acceleration=3.5, probability_epsilon=1e-9,
              pass_bias=3.2, pass_distance=.035, pass_lane=1.1, pass_receiver=.45,
              pass_source=.2, carry_bias=2.4, carry_source=.8, carry_target=.4,
              carry_closing=.16, shot_bias=-1.5, shot_distance=.085,
              shot_angle=1.1, shot_pressure=.25, block_rate=.6)
FEATURES = ['progress', 'y', 'vx', 'vy', 'ball_speed', 'distance', 'advance',
            'lateral', 'duration', 'source_pressure', 'receiver_pressure',
            'nearest_defender', 'closing_speed', 'lane_pressure', 'intercept_margin',
            'goal_distance', 'goal_angle', 'blockers', 'keeper_offset', 'target_speed',
            'attack_ratio', 'passing_ratio', 'time_fraction', 'score_difference',
            'player_difference', 'receiver_nearest_defender', 'receiver_reachable',
            'keeper_depth', 'keeper_cover', 'state_quality']


def clip(x, lo, hi):
    return min(hi, max(lo, x))


def sigmoid(x):
    return 1/(1+math.exp(-clip(x, -40, 40)))


def direction(team, period):
    return 1 if (team == 0) == (period == 1) else -1


def progress(x, team, period):
    return x if direction(team, period) == 1 else 105-x


def distance(a, b):
    return math.hypot(a[0]-b[0], a[1]-b[1])


def candidate_actions(s):
    """One standardised pass per recipient. No result-dependent targets."""
    roster = s['players']; p = next(p for p in roster if p['id'] == s['actor'])
    source = s['ball'][:2]; out = []; d = direction(s['team'], s['period'])
    for q in roster:
        if q['team'] != s['team'] or q['id'] == s['actor']:
            continue
        dt = max(.6, distance(source, [q['x'], q['y']])/CONFIG['pass_speed'])
        target = [q['x']+.25*q['vx'], q['y']+.25*q['vy']]
        length = distance(source, target); mode = 'ground' if length <= CONFIG['ground_max'] else 'high'
        supported = length <= CONFIG['lofted_max'] and 0 <= target[0] <= 105 and 0 <= target[1] <= 68
        speed = CONFIG['pass_speed'] if mode == 'ground' else CONFIG['lofted_speed']
        out.append(dict(key='pass:'+q['id'], kind='pass', recipient=q['id'], target=target,
                        duration=max(.6, length/speed), mode=mode, supported=supported,
                        reason=None if supported else 'Outside pass-distance or pitch support',
                        reachable=distance([q['x'], q['y']], target) <= 7.2*dt+.5))
    for name, dy in [('forward', 0), ('left', -4), ('right', 4)]:
        target = [p['x']+d*CONFIG['carry_metres'], p['y']+dy]
        supported = 1 <= target[0] <= 104 and 1 <= target[1] <= 67
        out.append(dict(key='carry:'+name, kind='carry', recipient=p['id'], target=target,
                        duration=CONFIG['carry_seconds'], mode='ground', supported=supported,
                        reason=None if supported else 'Carry target outside pitch support', reachable=True))
    out.append(dict(key='shot:goal', kind='shot', recipient=None,
                    target=[105. if d == 1 else 0., 34.], duration=max(.4, distance(source, [105 if d == 1 else 0, 34])/24),
                    mode='ordinary', supported=True, reason=None, reachable=True))
    return out


def geometry(s, a):
    p = next(p for p in s['players'] if p['id'] == s['actor'])
    opponents = [q for q in s['players'] if q['team'] != s['team']]
    field = [q for q in opponents if not q['id'].endswith('01')]
    keeper = next((q for q in opponents if q['id'].endswith('01')), opponents[0])
    source = s['ball'][:2]; target = a['target']; dx = target[0]-source[0]; dy = target[1]-source[1]
    length = math.hypot(dx, dy)
    def pressure(point):
        return sum(math.exp(-distance(point, [q['x'], q['y']])/3) for q in field)
    nearest = min(field, key=lambda q: distance(source, [q['x'], q['y']]))
    nd = max(.01, distance(source, [nearest['x'], nearest['y']]))
    closing = ((nearest['vx']-p['vx'])*(source[0]-nearest['x'])+(nearest['vy']-p['vy'])*(source[1]-nearest['y']))/nd
    risks = []; blockers = 0.
    for q in field:
        u = clip(((q['x']-source[0])*dx+(q['y']-source[1])*dy)/max(.01, length*length), .05, .95)
        point = [source[0]+u*dx, source[1]+u*dy]; gap = distance(point, [q['x'], q['y']])
        margin = .3+2*gap/(7.2+math.hypot(q['vx'], q['vy']))-u*a['duration']
        risks.append((math.exp(-gap/4)*sigmoid(-margin), margin, q['id'], point))
        blockers += math.exp(-gap/1.8)
    risks.sort(reverse=True)
    goal_x = 105 if direction(s['team'], s['period']) == 1 else 0
    gd = distance(source, [goal_x, 34]); gx = abs(goal_x-source[0])
    angle = abs(math.atan2(37.66-source[1], gx+.1)-math.atan2(30.34-source[1], gx+.1))
    # Keeper's distance from the ball-to-goal segment, not body orientation.
    gdx = goal_x-source[0]; gdy = 34-source[1]
    u = clip(((keeper['x']-source[0])*gdx+(keeper['y']-source[1])*gdy)/max(.01, gd*gd), 0, 1)
    line_gap = distance([keeper['x'], keeper['y']], [source[0]+u*gdx, source[1]+u*gdy])
    cover = math.exp(-line_gap/1.5)*math.exp(-distance(source, [keeper['x'], keeper['y']])/12)
    receiver = next((q for q in s['players'] if q['id'] == a['recipient']), p)
    rn = min(distance([receiver['x'], receiver['y']], [q['x'], q['y']]) for q in field)
    own, other = s['ratings'][s['team']], s['ratings'][1-s['team']]
    f = [progress(p['x'], s['team'], s['period']), p['y'], p['vx']*direction(s['team'], s['period']), p['vy'],
         math.sqrt(sum(v*v for v in s['ball'][3:])), length, dx*direction(s['team'], s['period']), dy,
         a['duration'], pressure(source), pressure(target), nd, closing, sum(r[0] for r in risks), risks[0][1],
         gd, angle, blockers, abs(keeper['y']-34), math.hypot(receiver['vx'], receiver['vy']),
         own['attack']/other['defense'], own['passing']/other['pressing'], s['t_ms']/6000000,
         s['score'][s['team']]-s['score'][1-s['team']], sum(q['team'] == s['team'] for q in s['players'])-sum(q['team'] != s['team'] for q in s['players']),
         rn, float(a['reachable']), abs(goal_x-keeper['x']), cover, 1.]
    return f, risks[0][2], risks[0][3]


def original_probability(s, a, f):
    """Teacher derived exclusively from 7d0c193's published mechanism."""
    if a['kind'] == 'pass':
        if a['recipient'].endswith('01'):
            return None
        threshold = clip(.32/f[21], .16, .50)
        return 1-threshold if f[25] < 8 else 1.
    if a['kind'] == 'carry':
        return None if s['actor'].endswith('01') else 1.
    if s['actor'].endswith('01') or f[0] <= 78 or abs(f[1]-34) >= 24:
        return None
    return clip(.12*f[20], .05, .22)


def tactical_probabilities(f, kind, risk_scale=1.):
    """Versioned hypotheses. NEVER trained on these values as real-world labels."""
    c = CONFIG; r = risk_scale
    if kind == 'pass':
        p = sigmoid(c['pass_bias']-r*(c['pass_distance']*f[5]+c['pass_lane']*f[13]+c['pass_receiver']*f[10]+c['pass_source']*f[9])+.5*math.log(f[21]))
        return {'success': clip(p, 1e-9, 1-1e-9), 'loss': 1-clip(p, 1e-9, 1-1e-9)}
    if kind == 'carry':
        p = sigmoid(c['carry_bias']-r*(c['carry_source']*f[9]+c['carry_target']*f[10]+c['carry_closing']*max(0, f[12])))
        return {'success': p, 'loss': 1-p}
    # Conditional execution: blocking appears exactly once, before an unblocked shot.
    blocked = 1-math.exp(-r*c['block_rate']*f[17])
    goal_unblocked = sigmoid(c['shot_bias']-c['shot_distance']*(f[15]-12)+c['shot_angle']*math.log(max(f[16], .001)/.45)
                             -r*c['shot_pressure']*f[9]+.4*math.log(f[20])-.7*f[28]+.1*min(f[18], 5))
    saved_conditional = sigmoid(.5-.02*f[15]+.5*f[28])
    return dict(goal=(1-blocked)*goal_unblocked, blocked=blocked,
                saved=(1-blocked)*(1-goal_unblocked)*saved_conditional,
                off_target=(1-blocked)*(1-goal_unblocked)*(1-saved_conditional))


def projected_state(s, a, outcome, defender, loss_point):
    """Short geometric prediction; no simulator, observed future or replay involved."""
    dt = a['duration']; other = 1-s['team']; source_players = s['players']
    players = [{**p, 'x': clip(p['x']+p['vx']*dt, .5, 104.5), 'y': clip(p['y']+p['vy']*dt, .5, 67.5)} for p in source_players]
    if outcome == 'success':
        team = s['team']; actor = a['recipient']; point = a['target']
    elif outcome in ('loss', 'blocked'):
        team = other; actor = defender; point = loss_point
    else:
        team = other; actor = next(p['id'] for p in players if p['team'] == other and p['id'].endswith('01'))
        keeper = next(p for p in players if p['id'] == actor)
        point = [keeper['x'], keeper['y']] if outcome == 'saved' else [5.5 if direction(other, s['period']) == 1 else 99.5, 34.]
    players = [{**p, 'x': point[0], 'y': point[1]} if p['id'] == actor else p for p in players]
    return {**s, 't_ms': s['t_ms']+dt*1000, 'team': team, 'actor': actor,
            'ball': [*point, 0., 0., 0., 0.], 'players': players}


def xt_value(s, xt, point=None, team=None):
    team = s['team'] if team is None else team; point = s['ball'][:2] if point is None else point
    x = progress(point[0], team, s['period']); i = min(7, max(0, int(point[1]/68*8)))*12+min(11, max(0, int(x/105*12)))
    return float(xt[i])


def evaluate(s, xt, horizon=2, risk_scale=1., terminal_ms=None):
    rows = []
    for a in candidate_actions(s):
        if not a['supported']:
            rows.append({**a, 'q': None}); continue
        f, defender, point = geometry(s, a); probs = tactical_probabilities(f, a['kind'], risk_scale)
        benefit = risk = immediate = 0.
        for outcome, p in probs.items():
            # A shot still travelling at the period boundary cannot earn a goal.
            if s['t_ms']+a['duration']*1000 >= (terminal_ms or (3000000 if s['period'] == 1 else 6000000)):
                continue
            if outcome == 'goal':
                immediate += p; continue
            ns = projected_state(s, a, outcome, defender, point)
            if ns['t_ms'] >= (terminal_ms or (3000000 if s['period'] == 1 else 6000000)):
                v = 0.
            elif horizon == 1:
                v = xt_value(ns, xt)
            else:
                v = max((r['q'] for r in evaluate(ns, xt, horizon-1, risk_scale, terminal_ms) if r['q'] is not None), default=0.)
            contribution = p*math.exp(-a['duration']/CONFIG['discount_seconds'])*v
            if ns['team'] == s['team']:
                benefit += contribution
            else:
                risk += contribution
        rows.append({**a, 'q': immediate+benefit-risk, 'immediate': immediate, 'continuation': benefit,
                     'risk': risk, 'probabilities': probs, 'features': dict(zip(FEATURES, f)),
                     'failure_target': point, 'defender': defender})
    return sorted(rows, key=lambda a: a['q'] if a['q'] is not None else -math.inf, reverse=True)


def softmax(rows, temperature):
    """Normalise supported unique options; not max-normalisation or min-max."""
    unique = {}; result = []
    for a in rows:
        if a['key'] not in unique:
            unique[a['key']] = True; result.append(a.copy())
    eligible = [a for a in result if a.get('supported') and a.get('q') is not None]
    best = max((a['q'] for a in eligible), default=0.)
    weights = [math.exp((a['q']-best)/temperature) for a in eligible]; total = sum(weights)
    for a in result:
        a['softmax_weight'] = a['relative_score'] = None
    for a, w in zip(eligible, weights):
        a.update(softmax_weight=w/total, relative_score=100*w/total)
    return result


def reachable_projection(p, target, seconds=1.):
    """Continuous constant-acceleration displacement, clipped by speed cap."""
    dx, dy = target[0]-p['x'], target[1]-p['y']; n = max(.001, math.hypot(dx, dy))
    desired = [dx/n*min(CONFIG['max_speed'], n/seconds), dy/n*min(CONFIG['max_speed'], n/seconds)]
    dv = [desired[0]-p['vx'], desired[1]-p['vy']]; scale = min(1, CONFIG['acceleration']*seconds/max(.001, math.hypot(*dv)))
    end_v = [p['vx']+dv[0]*scale, p['vy']+dv[1]*scale]
    return dict(x=clip(p['x']+.5*(p['vx']+end_v[0])*seconds, .5, 104.5),
                y=clip(p['y']+.5*(p['vy']+end_v[1])*seconds, .5, 67.5), vx=end_v[0], vy=end_v[1])


def defensive_diagnostics(s, xt, scored):
    field = [p for p in s['players'] if p['team'] != s['team'] and not p['id'].endswith('01')]
    selected = sorted(field, key=lambda p: distance([p['x'], p['y']], s['ball'][:2]))[:2]
    lane = next((r.get('defender') for r in scored if r.get('q') is not None), None)
    selected += [p for p in field if p['id'] == lane and p not in selected]
    base = copy.deepcopy(s)
    for p in base['players']:
        p['x'] = clip(p['x']+p['vx'], .5, 104.5); p['y'] = clip(p['y']+p['vy'], .5, 67.5)
    carrier = next(p for p in base['players'] if p['id'] == s['actor']); base['ball'][:2] = [carrier['x'], carrier['y']]
    threat = max(r['q'] for r in evaluate(base, xt, 1) if r['q'] is not None)
    dangerous = next((r for r in scored if r['kind'] == 'pass' and r.get('q') is not None), scored[0])
    result = []
    for p in selected:
        options = []
        goal = [0 if direction(p['team'], s['period']) == 1 else 105, 34]
        lane_point = [(s['ball'][j]+dangerous['target'][j])/2 for j in range(2)]
        targets = [('hold', [p['x']+p['vx'], p['y']+p['vy']]), ('press', base['ball'][:2]),
                   ('cover', lane_point), ('mark', dangerous['target']), ('drop', goal)]
        for action, target in targets:
            v = copy.deepcopy(base)
            q = next(q for q in v['players'] if q['id'] == p['id'])
            if action != 'hold': q.update(reachable_projection(p, target))
            after = max(r['q'] for r in evaluate(v, xt, 1) if r['q'] is not None)
            options.append(dict(action=action, reduction=threat-after, target=[q['x'], q['y']]))
        result.append(dict(player_id=p['id'], options=sorted(options, key=lambda a: -a['reduction'])))
    return result
