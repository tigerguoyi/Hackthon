"""Contract snapshots -> B state. No event outcome or decision selection is input."""
import copy
import math
import re

from contracts import check_snapshot
from vendor.a_decision.engine import validate as validate_tracking

NEUTRAL_PROFILE = {'profile_version': 'sim-1', 'kind': 'simulated',
                   'preferred_foot': None, 'weak_foot': None}
REQUEST_KEYS = {'match_id', 'snapshot_id', 't_ms', 'actor_id', 'question', 'mode', 'language', 'profile'}


def validate_request(request):
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        raise ValueError('Expected exactly the canonical analysis_request fields')
    if not isinstance(request['match_id'], str) or not request['match_id']:
        raise ValueError('Invalid match ID')
    if not isinstance(request['snapshot_id'], str) or not re.fullmatch('[a-f0-9]{16}', request['snapshot_id']):
        raise ValueError('Invalid snapshot ID')
    if type(request['t_ms']) is not int or request['t_ms'] < 0:
        raise ValueError('Invalid cumulative time')
    if not isinstance(request['actor_id'], str) or not re.fullmatch('[HA][0-9]{2}', request['actor_id']):
        raise ValueError('Invalid match-local actor ID')
    if not isinstance(request['question'], str) or not request['question'].strip() or len(request['question']) > 4000:
        raise ValueError('Invalid question')
    if request['mode'] not in ('fan', 'analyst') or request['language'] not in ('en', 'zh'):
        raise ValueError('Unsupported audience or language')
    profile = request['profile']
    if not isinstance(profile, dict) or set(profile) != set(NEUTRAL_PROFILE):
        raise ValueError('Expected simulated profile, not source observations')
    if profile['profile_version'] != 'sim-1' or profile['kind'] != 'simulated':
        raise ValueError('Invalid simulated profile identity')
    if profile['preferred_foot'] not in ('left', 'right', 'both', None):
        raise ValueError('Invalid preferred foot')
    foot = profile['weak_foot']
    if foot is not None and (type(foot) is not int or not 1 <= foot <= 5):
        raise ValueError('Weak foot must be an integer from one to five or null')


def source_profile(player):
    """Keep observed indices separate, with no conversion to execution ability."""
    observed = copy.deepcopy(player.get('sofascore'))
    issues = []
    if not observed:
        return {'status': 'unavailable', 'observation': None, 'issues': ['No source profile'], 'used_in_probability': False}
    expected = 'goalkeeper' if player.get('position') == 'GK' else 'outfield'
    if observed.get('attribute_kind') != expected:
        issues.append('Source radar kind does not match match role')
    if observed.get('status') not in ('complete', 'partial'):
        issues.append('Source identity/status requires review')
    if observed.get('source_conflicts'):
        issues.append('Source conflicts retained; no ability inference')
    return {'status': 'needs_review' if issues else observed.get('status'),
            'observation': observed, 'issues': issues, 'used_in_probability': False,
            'time_note': 'Collection time is not historical pre-match evidence; indices are descriptive, not probabilities.'}


def adapt_snapshot(snapshot, metadata, ratings=None):
    check_snapshot(snapshot, metadata)
    frame = snapshot['frame']
    validate_tracking(frame, metadata)
    if len(frame.get('ball', [])) != 6 or not all(type(v) in (int, float) and math.isfinite(v) for v in frame['ball']):
        raise ValueError('Invalid ball vector')
    if not 0 <= frame['ball'][0] <= 105 or not 0 <= frame['ball'][1] <= 68:
        raise ValueError('Ball outside supported pitch')
    roster = {p['player_id']: p for p in metadata['players']}
    players = [dict(id=p[0], team=int(roster[p[0]]['team_id'] == 'AWAY'),
                    x=p[1], y=p[2], vx=p[3], vy=p[4], position=roster[p[0]].get('position')) for p in frame['players']]
    period = next(p for p in metadata['periods'] if p['period'] == snapshot['period'])
    assumptions = []
    if ratings is None:
        ratings = [dict(attack=1., defense=1., passing=1., pressing=1.) for _ in range(2)]
        assumptions.append('neutral_team_ratings_missing')
    if len(ratings) != 2 or any(not all(type(r.get(k)) in (int, float) and math.isfinite(r[k]) and r[k] > 0
                                      for k in ('attack', 'defense', 'passing', 'pressing')) for r in ratings):
        raise ValueError('Invalid team parameters')
    owner = frame['ball_owner_id']
    team = int(frame['possession_team_id'] == 'AWAY')
    if owner is not None:
        team = int(roster[owner]['team_id'] == 'AWAY')
    return dict(t_ms=frame['t_ms'], period=frame['period'], team=team, actor=owner,
                score=copy.deepcopy(frame['score']), ball=copy.deepcopy(frame['ball']), players=players,
                ratings=copy.deepcopy(ratings), attacking_direction=copy.deepcopy(snapshot['attacking_direction']),
                period_end_ms=period['start_ms'] + period['duration_ms'], duration_ms=metadata['duration_ms'],
                in_play=frame['in_play'], model_assumptions=assumptions,
                data_version=snapshot['data_version'], snapshot_id=snapshot['snapshot_id'],
                match_id=snapshot['match_id'], provenance='contract_snapshot_observed')


def match_actual(decision, candidates):
    """Called AFTER comparison. Never derive intent from end locations."""
    selected = (decision or {}).get('selected', {})
    kind = selected.get('kind')
    if kind == 'shot':
        key = 'shot:goal'
    elif kind == 'pass':
        receiver = selected.get('recipient_id')
        if receiver is None:
            receiver = next((c.get('recipient_id') for c in decision.get('candidates', [])
                             if c.get('kind') == 'pass' and c.get('recipient') == selected.get('recipient')), None)
        key = 'pass:' + receiver if isinstance(receiver, str) else None
    elif kind == 'carry':
        # A's choose_action records only the type; start_carry draws the direction later.
        key = selected.get('action_key') if selected.get('intent_recorded_before_execution') is True else None
    else:
        key = None
    exists = any(c['action_id'] == key for c in candidates)
    return (key if exists else None), ('matched_standardised_type_or_recipient' if exists else
                                      'Actual pre-action intention is not recoverable; no score or forced forward-carry match')
