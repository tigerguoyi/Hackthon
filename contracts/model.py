"""Reference hashing and cross-field checks; structural schemas live beside this file.

These helpers do not run the model, read future events, or change the legacy HTTP API.
Validate JSON structure with scripts/validate_contracts.mjs before semantic checks.
"""
import copy
import hashlib
import json
import re


def digest(value):
    """Python canonical JSON v1: UTF-8, sorted keys, ASCII escapes, no whitespace/NaN.

    Integral floats normalize to integers (including -0.0). Array order is significant.
    This is a project-specific encoding, not RFC 8785; consumers use server-issued IDs.
    """
    def normalize(item):
        if isinstance(item, dict):
            return {k: normalize(v) for k, v in item.items()}
        if isinstance(item, list):
            return [normalize(v) for v in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item
    encoded = json.dumps(normalize(value), sort_keys=True, ensure_ascii=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()[:16]


def data_version(metadata):
    return 'gen-' + digest(metadata)


def make_match(metadata):
    return {'schema_version': 'contract-1', 'data_version': data_version(metadata),
            'metadata': copy.deepcopy(metadata)}


def make_snapshot(metadata, frame):
    period = next(p for p in metadata['periods'] if p['period'] == frame['period'])
    home = period['home_attacks']
    payload = {'schema_version': 'contract-1', 'match_id': metadata['match_id'],
               'data_version': data_version(metadata), 'seed': metadata['seed'],
               't_ms': frame['t_ms'], 'period': frame['period'],
               'attacking_direction': {'HOME': home, 'AWAY': (
                   'negative_x' if home == 'positive_x' else 'positive_x')},
               'frame': copy.deepcopy(frame)}
    return {'snapshot_id': digest(payload), **payload}


def candidate_set_id(snapshot, profile, revision, candidates, evidence):
    if not re.fullmatch(r'outcome-\d+\.\d+(?:\.\d+)?', revision):
        raise ValueError('Invalid comparison revision')
    # Revealing the actual action is presentation state, never an input to ranking.
    options = [{k: v for k, v in c.items() if k != 'is_actual'} for c in candidates]
    return revision + ':' + digest({'snapshot_id': snapshot['snapshot_id'],
        'data_version': snapshot['data_version'], 'profile': profile,
        'candidates': options, 'evidence': evidence})


def check_snapshot(snapshot, metadata):
    frame = snapshot['frame']
    if snapshot != make_snapshot(metadata, frame):
        raise ValueError('Snapshot hash, metadata version, seed, time or direction mismatch')
    if len({p['period'] for p in metadata['periods']}) != len(metadata['periods']):
        raise ValueError('Duplicate period')
    period = next(p for p in metadata['periods'] if p['period'] == frame['period'])
    if (frame['period_ms'] != frame['t_ms'] - period['start_ms']
            or not 0 <= frame['period_ms'] <= period['duration_ms']
            or frame['t_ms'] > metadata['duration_ms']
            or abs(frame['frame_id'] * 1000 / metadata['tracking_hz'] - frame['t_ms']) > 1e-6):
        raise ValueError('Invalid frame clock')
    roster = {p['player_id']: p for p in metadata['players']}
    ids = [p[0] for p in frame['players']]
    if len(roster) != len(metadata['players']) or len(set(ids)) != len(ids):
        raise ValueError('Duplicate player ID')
    if not set(ids) <= roster.keys():
        raise ValueError('Active player missing from metadata')
    owner = frame['ball_owner_id']
    if owner is not None and (owner not in ids or
                             roster[owner]['team_id'] != frame['possession_team_id']):
        raise ValueError('Ball owner is inactive or on the wrong team')


def check_exchange(request, response, snapshot, metadata):
    """Reject stale/forged candidates and dangling citations after schema validation."""
    check_snapshot(snapshot, metadata)
    for key in ('match_id', 'snapshot_id', 't_ms'):
        if request[key] != snapshot[key]:
            raise ValueError('Request does not reference this snapshot: ' + key)
    active = {p[0] for p in snapshot['frame']['players']}
    if request['actor_id'] not in active:
        raise ValueError('Actor must be an active player')
    if response['status'] in ('ready', 'partial', 'model_unavailable') and (
            not snapshot['frame']['in_play'] or
            request['actor_id'] != snapshot['frame']['ball_owner_id']):
        raise ValueError('Computed candidates require an in-play snapshot and current ball owner')
    if response['snapshot_id'] != snapshot['snapshot_id'] or response['data_version'] != snapshot['data_version']:
        raise ValueError('Stale response: retry analysis')
    if response['profile_version'] != request['profile']['profile_version']:
        raise ValueError('Profile version mismatch')
    revision = response['candidate_set_id'].split(':')[0]
    expected = candidate_set_id(snapshot, request['profile'], revision,
                                response['candidates'], response['evidence'])
    if response['candidate_set_id'] != expected:
        raise ValueError('Candidate set mismatch: discard explanation and retry')
    options = response['candidates']
    ids = [c['action_id'] for c in options]
    evidence_ids = [e['evidence_id'] for e in response['evidence']]
    if len(set(ids)) != len(ids) or len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError('Duplicate action or evidence ID')
    ranks = [c['rank'] for c in options if c['eligible']]
    if sorted(ranks) != list(range(1, len(ranks) + 1)):
        raise ValueError('Eligible ranks must be unique and contiguous from one')
    if sum(c['is_actual'] for c in options) > 1:
        raise ValueError('At most one actual action')
    teams = {p['player_id']: p['team_id'] for p in metadata['players']}
    for option in options:
        if not set(option['evidence_ids']) <= set(evidence_ids):
            raise ValueError('Candidate references unknown evidence')
        if option['type'] == 'pass':
            receiver = option['action_id'].split(':', 1)[1]
            if (receiver not in active or receiver == request['actor_id'] or
                    teams[receiver] != teams[request['actor_id']]):
                raise ValueError('Pass receiver must be an active teammate')
    explanation = response['explanation']
    if explanation is not None:
        if explanation['mode'] != request['mode']:
            raise ValueError('Explanation audience mismatch')
        if not set(explanation['cited_evidence_ids']) <= set(evidence_ids):
            raise ValueError('Explanation references unknown evidence')
