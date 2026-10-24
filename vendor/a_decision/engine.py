"""Transparent candidate-pass baseline. No trained probabilities or future frames."""
import math

VERSION = '0.1.0'
PARAMS = {'pass_speed_mps': 14., 'reaction_s': .3, 'player_speed_mps': 7.2,
          'control_radius_m': 1., 'arrival_softness_s': .45}


def distance(a, b):
    return math.hypot(a[0]-b[0], a[1]-b[1])


def sigmoid(x):
    return 1 / (1 + math.exp(-max(-40, min(40, x))))


def arrival(player, target, params):
    # Constant velocity during reaction, then bounded-speed travel; a baseline,
    # not a calibrated model of acceleration or body orientation.
    reaction = params['reaction_s']
    predicted = [player[1]+player[3]*reaction, player[2]+player[4]*reaction]
    return reaction + max(0, distance(predicted, target)-params['control_radius_m']) / params['player_speed_mps']


def threat(point, direction):
    """Hand-defined signed spatial utility, NOT xT, xG or goal probability."""
    progress = point[0] if direction == 1 else 105-point[0]
    return .35 * (progress/105)**2 * math.exp(-abs(point[1]-34)/30)


def validate(frame, metadata):
    if frame.get('period') not in (1, 2):
        raise ValueError('Period must be 1 or 2.')
    players = frame.get('players', [])
    if not 2 <= len(players) <= 22:
        raise ValueError('Expected 2–22 active players.')
    ids = set()
    for p in players:
        if (len(p) != 5 or not isinstance(p[0], str) or p[0] in ids
                or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in p[1:])
                or not 0 <= p[1] <= 105 or not 0 <= p[2] <= 68
                or math.hypot(p[3], p[4]) > 15):
            raise ValueError('Invalid player tracking record.')
        ids.add(p[0])
    teams = {p['player_id']: p['team_id'] for p in metadata['players']}
    if any(teams.get(p[0]) not in ('HOME', 'AWAY') for p in players):
        raise ValueError('Missing player/team mapping.')
    if not isinstance(frame.get('t_ms'), (int, float)) or not math.isfinite(frame['t_ms']) or frame['t_ms'] < 0:
        raise ValueError('Invalid snapshot time.')
    return teams


def analyze(frame, metadata, params=None):
    teams = validate(frame, metadata)
    params = {**PARAMS, **(params or {})}
    if set(params) != set(PARAMS) or any(not math.isfinite(v) or v <= 0 for v in params.values()):
        raise ValueError('Invalid model parameters.')
    result = {'model_version': VERSION, 't_ms': frame['t_ms'], 'parameters': params,
              'score_kind': 'uncalibrated_expected_spatial_utility_gain', 'candidates': [],
              'limitations': ['Spatial utility is hand-defined, not learned xT/VAEP or xG.',
                              'Feasibility is an uncalibrated heuristic, not a measured success probability.',
                              'Preferred foot and body orientation are unknown and not scored.',
                              'Ground passes only; no full opponent-response or ball-physics simulation.',
                              'Failure value approximates an immediate turnover at the target.'],
              'status': 'ready'}
    owner = next((p for p in frame['players'] if p[0] == frame.get('ball_owner_id')), None)
    if not frame.get('in_play') or owner is None:
        result.update(status='unavailable', reason='Requires an in-play snapshot with a known ball owner.')
        return result
    team = teams[owner[0]]
    direction = 1 if (team == 'HOME') == (frame['period'] == 1) else -1
    opponents = [p for p in frame['players'] if teams[p[0]] != team]
    teammates = [p for p in frame['players'] if teams[p[0]] == team and p[0] != owner[0]]
    if len(opponents) < 2:
        raise ValueError('At least two defenders are needed for the offside check.')
    to_progress = lambda x: x if direction == 1 else 105-x
    second_last = sorted((to_progress(p[1]) for p in opponents), reverse=True)[1]
    offside_line = max(52.5, to_progress(owner[1]), second_last)
    current = threat(owner[1:3], direction)
    result.update(actor_id=owner[0], team_id=team, direction=direction, current_value=current,
                  offside_line_x=offside_line if direction == 1 else 105-offside_line)
    for receiver in teammates:
        start = owner[1:3]
        travel = distance(start, receiver[1:3])/params['pass_speed_mps']
        target = [max(0, min(105, receiver[1]+receiver[3]*travel)),
                  max(0, min(68, receiver[2]+receiver[4]*travel))]
        travel = distance(start, target)/params['pass_speed_mps']
        # Offside is checked at release using receiver location, not landing spot.
        offside = to_progress(receiver[1]) > offside_line + 1e-6
        margins = []
        for defender in opponents:
            for step in range(1, 21):
                fraction = step/20
                spot = [start[j]+fraction*(target[j]-start[j]) for j in (0, 1)]
                margins.append((arrival(defender, spot, params)-fraction*travel, defender[0], spot))
        margin, blocker, interception_point = min(margins)
        receiver_margin = travel-arrival(receiver, target, params)
        feasibility = sigmoid(margin/params['arrival_softness_s']) * sigmoid(receiver_margin/.45)
        if offside:
            feasibility = 0.
        success_value = threat(target, direction)
        failure_value = -threat(target, -direction)
        gain = feasibility*success_value+(1-feasibility)*failure_value-current
        cid = 'pass:' + receiver[0]
        evidence = [{'id': cid+':lane', 'metric': 'minimum_defender_arrival_margin_s', 'value': round(margin, 4),
                     'player_id': blocker, 'location': interception_point},
                    {'id': cid+':receiver', 'metric': 'receiver_arrival_margin_s', 'value': round(receiver_margin, 4)},
                    {'id': cid+':offside', 'metric': 'offside_at_release', 'value': offside}]
        result['candidates'].append({'action_id': cid, 'type': 'pass', 'receiver_id': receiver[0],
                                    'start': start, 'target': target, 'feasible_by_rules': not offside,
                                    'feasibility_index': feasibility, 'success_value': success_value,
                                    'failure_value': failure_value, 'value_gain': gain,
                                    'ball_travel_s': travel, 'evidence': evidence})
    result['candidates'].sort(key=lambda c: (not c['feasible_by_rules'], -c['value_gain']))
    legal = [c for c in result['candidates'] if c['feasible_by_rules']]
    result['recommended_action_id'] = legal[0]['action_id'] if legal else None
    result['recommendation_scope'] = 'Best among evaluated passes only; shooting/carrying not compared.'
    return result


def review(result, mode='fan'):
    """Inspectable deterministic review stages; explicitly NOT LLM agents."""
    trace = []
    for name in ('candidate_evaluation', 'defensive_lane_review', 'evidence_validation'):
        trace.append({'stage': name, 'executor': 'deterministic_python', 'status': 'complete'})
    if result['status'] != 'ready':
        return {'trace': trace, 'text': result.get('reason', 'Unavailable'), 'is_multi_agent': False}
    candidates = result['candidates']
    best = next((c for c in candidates if c['action_id'] == result['recommended_action_id']), None)
    if best is None:
        text = 'No legal pass candidate in this snapshot. Other action types are not evaluated.'
    else:
        blocker = best['evidence'][0]['player_id']
        text = (f"Among evaluated passes, {best['receiver_id']} has the highest model value. "
                f"{blocker} is the critical defender along that route. "
                'This compares modelled options, not what would certainly happen.')
        if best['value_gain'] < 0:
            text += ' Even the highest-ranked pass has negative estimated gain; holding, carrying and shooting have not been evaluated.'
        if mode == 'analyst':
            text += (f" Gain {best['value_gain']:.4f}; feasibility index {best['feasibility_index']:.3f}; "
                     f"minimum defender arrival margin {best['evidence'][0]['value']:.3f} s.")
    return {'trace': trace, 'text': text, 'is_multi_agent': False}
