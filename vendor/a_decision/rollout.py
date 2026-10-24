"""Bounded deterministic counterfactuals; scenarios are not probability samples."""
import math
from .engine import analyze, distance

SCENARIOS = [('quick', .1, 0.), ('baseline', .3, 0.), ('delayed', .65, 0.),
             ('wide_error', .3, 1.5)]
DT = .05
MAX_SPEED = 7.2
MAX_ACCEL = 3.5


def advance(p, target, reactive):
    vx, vy = p[3:5]
    if reactive:
        dx, dy = target[0]-p[1], target[1]-p[2]
        d = math.hypot(dx, dy)
        speed = min(MAX_SPEED, math.sqrt(2*MAX_ACCEL*d))
        desired = [dx/max(d, 1e-9)*speed, dy/max(d, 1e-9)*speed]
        ax, ay = desired[0]-vx, desired[1]-vy
        scale = min(1, MAX_ACCEL*DT/max(math.hypot(ax, ay), 1e-9))
        vx, vy = vx+ax*scale, vy+ay*scale
    speed = math.hypot(vx, vy)
    if speed > MAX_SPEED:
        vx, vy = vx*MAX_SPEED/speed, vy*MAX_SPEED/speed
    # Boundary truncation is documented; no physical collision solver.
    x, y = max(0, min(105, p[1]+vx*DT)), max(0, min(68, p[2]+vy*DT))
    return [p[0], x, y, (x-p[1])/DT, (y-p[2])/DT]


def simulate(frame, metadata, action_id, *, analysis=None):
    # B integration: inject the exact ranked pass template; do not re-rank with
    # A's separate utility engine. All four physical scenario loops are unchanged.
    analysis = analyze(frame, metadata) if analysis is None else analysis
    candidate = next((c for c in analysis['candidates'] if c['action_id'] == action_id), None)
    if candidate is None:
        raise ValueError('Unknown action or unavailable snapshot.')
    if not candidate['feasible_by_rules']:
        raise ValueError('Cannot simulate a receiver who is offside at release.')
    teams = {p['player_id']: p['team_id'] for p in metadata['players']}
    result = {'action_id': action_id, 'source_t_ms': frame['t_ms'], 'kind': 'counterfactual',
              'horizon_s': 4., 'step_s': DT, 'scenarios': [],
              'assumptions': ['Four illustrative scenarios; counts are NOT outcome probabilities.',
                              'Ground ball at 14 m/s; receiver pursues intended landing point.',
                              'Defenders pursue a projected intercept point after their reaction delay.',
                              'Players accelerate at up to 3.5 m/s², with a 7.2 m/s model speed cap.',
                              'First player within 1 m controls the ball; no contact, tackle or goalkeeper skill model.',
                              'Stops at first touch, target arrival or four seconds; no subsequent play.',
                              'Boundary clamping is geometric, not physical. Only current snapshot is consumed.']}
    for name, delay, error in SCENARIOS:
        players = [p[:] for p in frame['players']]
        start = candidate['start'][:]
        target = candidate['target'][:]
        dx, dy = target[0]-start[0], target[1]-start[1]
        length = math.hypot(dx,dy)
        target = [max(0,min(105,target[0]-dy/max(length,1e-9)*error)),
                  max(0,min(68,target[1]+dx/max(length,1e-9)*error))]
        travel = distance(start,target)/14.
        intercept_targets = {}
        for p in players:
            if teams[p[0]] == analysis['team_id']:
                continue
            # Earliest approximately reachable point, otherwise the landing spot.
            aim = target
            for step in range(1, 81):
                t = step*DT
                u = min(1,t/max(travel,DT))
                spot = [start[j]+u*(target[j]-start[j]) for j in (0,1)]
                if distance(p[1:3],spot) <= max(0,t-delay)*MAX_SPEED+1:
                    aim = spot
                    break
            intercept_targets[p[0]] = aim
        frames = [{'elapsed_s':0., 'players':[p[:] for p in players], 'ball':start[:]}]
        outcome, controller = 'unresolved', None
        for step in range(1,81):
            t = step*DT
            moved = []
            for p in players:
                defender = teams[p[0]] != analysis['team_id']
                active = p[0] == candidate['receiver_id'] or (defender and t > delay)
                aim = intercept_targets.get(p[0],target)
                moved.append(advance(p,aim,active))
            players = moved
            u = min(1,t/max(travel,DT))
            ball = [start[j]+u*(target[j]-start[j]) for j in (0,1)]
            # Exclude passer until release separation; all other teammates can receive.
            touches = [(distance(p[1:3],ball), p[0]) for p in players
                       if p[0] != analysis['actor_id'] and distance(p[1:3],ball) <= 1.]
            if touches:
                _, controller = min(touches)
                outcome = 'received' if teams[controller] == analysis['team_id'] else 'intercepted'
            elif t >= travel:
                outcome = 'unreceived'
            frames.append({'elapsed_s':round(t,3),'players':[p[:] for p in players],'ball':ball[:]})
            if outcome != 'unresolved': break
        result['scenarios'].append({'id':name,'reaction_s':delay,'lateral_error_m':error,
                                    'outcome':outcome,'controller_id':controller,
                                    'duration_s':frames[-1]['elapsed_s'],'frames':frames})
    outcomes = {s['outcome'] for s in result['scenarios']}
    result['summary'] = ('Outcomes differ across assumptions: this option is sensitive to the tested conditions.'
                         if len(outcomes)>1 else 'Same outcome in these four cases; this does not establish reliability outside them.')
    return result
