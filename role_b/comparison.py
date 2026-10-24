"""One ranking source for C tools and D UI; explicit public/private projection."""
import copy
import math
import hashlib
from pathlib import Path

from contracts import candidate_set_id, check_exchange
from enriched_rules import softmax
from vendor.a_decision.rollout import simulate
from .input_adapter import adapt_snapshot, validate_request
from .q2 import VERSION, evaluate, PASS_SPEED
from contracts.model import digest

CALCULATOR_ID = digest({p: hashlib.sha256((Path(__file__).resolve().parents[1]/p).read_bytes()).hexdigest()
                       for p in ('enriched_rules.py','role_b/q2.py','role_b/comparison.py')})

TEXT_RULES = {'forward_metres': 1., 'lane_pressure_high': .5, 'shot_blocking_high': .5}
LIMITATIONS = [
    'B two-step Q2 is the sole ranking engine; no language model or agent team was called.',
    'Tactical execution probabilities and short geometric successors are unvalidated assumptions, not individual skill models.',
    'The next decision uses a maximum and can overestimate achievable continuation; xT retains source-policy bias.',
    'Source radar indices and generator choice probabilities do not modify action success or Q2.',
    'Source observations and simulated profile are separate. Foot effects remain neutral; body orientation is unknown.',
    'High balls and hold are unsupported in the public contract; legacy research analysis remains available separately.',
    'One standardised route per receiver; historical carry direction is unknown unless explicitly recorded before execution.',
]


def empty_response(request, snapshot, reason):
    response = dict(status='unknown', snapshot_id=snapshot['snapshot_id'],
        candidate_set_id=candidate_set_id(snapshot, request['profile'], VERSION, [], []),
        data_version=snapshot['data_version'], profile_version='sim-1', candidates=[], evidence=[],
        explanation=None, agent_trace=[], limitations=[reason, *LIMITATIONS])
    return response


def compare_actions(request, snapshot, metadata, model, ratings=None):
    """C's tool entry. Returns public response and private diagnostics separately.

    Actual selection, context and outcomes are deliberately absent from the signature.
    """
    validate_request(request)
    state = adapt_snapshot(snapshot, metadata, ratings)
    for field in ('match_id', 'snapshot_id', 't_ms'):
        if request[field] != snapshot[field]:
            raise ValueError('Stale request identity')
    active = {p['id'] for p in state['players']}
    if request['actor_id'] not in active:
        raise ValueError('Actor is not active')
    if not state['in_play'] or state['actor'] != request['actor_id']:
        public = empty_response(request, snapshot, 'Requires an in-play snapshot and its actual current ball owner')
        check_exchange(request, public, snapshot, metadata)
        return public, {}
    if not isinstance(model, dict) or model.get('source_commit') is None or len(model.get('xt', [])) != 96:
        raise ValueError('A separately fitted, versioned source model is required; no legacy cache fallback')
    if snapshot['match_id'] in model.get('fitting_matches', []):
        raise ValueError('This match was used for fitting; refuse an in-sample public comparison')
    xt = model['xt']
    if not all(type(v) in (int, float) and math.isfinite(v) for v in xt):
        raise ValueError('Non-finite xT')
    try:
        rows = evaluate(state, xt, 2)
    except ValueError as error:
        public = empty_response(request, snapshot, str(error))
        check_exchange(request, public, snapshot, metadata)
        return public, {}
    one = {r['key']: r.get('q') for r in evaluate(state, xt, 1)}
    temperature = model.get('temperature', .05)
    rows = softmax(rows, temperature)
    evidence = [dict(evidence_id='model-context', kind='assumed', source='role_b/q2.py + fitted source model',
        value='B two-step comparison; source '+model['source_commit']+'; model '+model['model_id']+
              '; calculator '+CALCULATOR_ID+'; team-parameter binding '+digest(state['ratings'])+
              '; textual thresholds are versioned hypotheses; neutral player-profile effects.')]
    candidates = []
    rank = 0
    unknown_foot = request['profile']['preferred_foot'] is None
    foot_assumption = 'preferred_foot_not_considered' if unknown_foot else 'simulated_foot_profile_retained_but_not_used_in_success_probability'
    for index, row in enumerate(rows, 1):
        eligible = row.get('q') is not None
        if eligible:
            rank += 1
        eid = 'route-' + str(index)
        assumptions = [foot_assumption, 'radar_and_choice_style_not_execution_probability',
                       'two_step_geometric_successor_unvalidated', *state['model_assumptions']]
        kind = row['kind']
        f = row.get('features')
        if f:
            value = ('Route length '+format(f['distance'], '.2f')+' m; forward displacement '+format(f['advance'], '.2f')+
                     ' m; receiver nearest defender '+format(f['receiver_nearest_defender'], '.2f')+
                     ' m; estimated critical arrival margin '+format(f['intercept_margin'], '.3f')+' s.')
        else:
            value = row.get('reason') or 'Outside this model support'
        if not eligible:
            opportunity, risk = 'Excluded from this comparison.', row.get('reason', 'Unsupported action')
        elif kind == 'pass':
            opportunity = ('Advance possession to an active teammate and assess their next option.' if f['advance'] > TEXT_RULES['forward_metres']
                           else 'Recycle possession to an active teammate and assess their next option.')
            risk = ('A defender may reach the lane before the ball under the geometric rule.' if f['intercept_margin'] < 0
                    else 'Receiver control and defender response remain uncertain.')
            assumptions.append('ground_pass_template_14_mps_ball_origin_and_quarter_second_receiver_lead')
        elif kind == 'shot':
            opportunity = 'Use the current shooting window rather than defer to the next choice.'
            risk = ('The shooting lane is obstructed under the geometric blocking rule.' if f['blockers'] > TEXT_RULES['shot_blocking_high']
                    else 'A failed shot gives the opponent a modelled restart or controlled possession.')
        else:
            opportunity = 'Carry toward the standardised target and compare the next available choice.'
            risk = 'Retention and the opponent response are tactical assumptions; intended historical direction is not reconstructed.'
        evidence.append(dict(evidence_id=eid, kind='computed', source='enriched_rules.geometry via role_b/q2.py', value=value))
        candidates.append(dict(action_id=row['key'], type=kind, is_actual=False, eligible=eligible,
            opportunity=opportunity, main_risk=risk, rank=rank if eligible else None,
            evidence_ids=[eid, 'model-context'], assumptions=assumptions,
            rollout_supported=eligible and kind == 'pass' and row['mode'] == 'ground'))
    # Explicit unsupported entry, not a fabricated zero-valued hold action.
    evidence.append(dict(evidence_id='hold-support', kind='assumed', source='role_b/comparison.py',
        value='B has no validated hold transition; hold is not assigned a value or rank.'))
    candidates.append(dict(action_id='hold:1s', type='hold', is_actual=False, eligible=False,
        opportunity='Holding is outside this first contract adapter.', main_risk='No supported hold continuation model.',
        rank=None, evidence_ids=['hold-support'], assumptions=['hold_not_implemented', foot_assumption], rollout_supported=False))
    response = dict(status='model_unavailable' if rank else 'partial', snapshot_id=snapshot['snapshot_id'],
        candidate_set_id=candidate_set_id(snapshot, request['profile'], VERSION, candidates, evidence),
        data_version=snapshot['data_version'], profile_version='sim-1', candidates=candidates, evidence=evidence,
        explanation=None, agent_trace=[], limitations=LIMITATIONS + state['model_assumptions'])
    check_exchange(request, response, snapshot, metadata)
    private = dict(snapshot_id=snapshot['snapshot_id'], candidate_set_id=response['candidate_set_id'],
        model_id=model['model_id'], model_source=model['source_commit'], evaluation_mode='B_Q2_contract_adapter',
        temperature=temperature, options=[dict(r, one_step_q=one[r['key']]) for r in rows],
        numerical_fields='INTERNAL ONLY; not the public analysis_response')
    return response, private


def rollout_action(request, response, private, snapshot, metadata, action_id, expected_candidate_set_id):
    check_exchange(request, response, snapshot, metadata)
    if expected_candidate_set_id != response['candidate_set_id']:
        raise ValueError('Candidate set changed; discard stale rollout and retry analysis')
    option = next((c for c in response['candidates'] if c['action_id'] == action_id), None)
    if option is None or not option['rollout_supported']:
        raise ValueError('Only an eligible supported ground pass can be rolled out')
    row = next(r for r in private['options'] if r['key'] == action_id)
    start = snapshot['frame']['ball'][:2]
    bridge = dict(actor_id=request['actor_id'], team_id=snapshot['frame']['possession_team_id'],
        candidates=[dict(action_id=action_id, receiver_id=row['recipient'], start=start,
                         target=row['target'], feasible_by_rules=True)])
    result = simulate(snapshot['frame'], metadata, action_id, analysis=bridge)
    result.update(snapshot_id=snapshot['snapshot_id'], data_version=snapshot['data_version'],
                  candidate_set_id=response['candidate_set_id'], target=row['target'], start=start,
                  template_speed_mps=PASS_SPEED)
    return result


def accept_agent_response(request, response, snapshot, metadata, expected_candidate_set_id):
    """C/D must reject an old response, even if it is otherwise self-consistent."""
    if response.get('candidate_set_id') != expected_candidate_set_id:
        raise ValueError('Stale agent candidate_set_id; discard and retry')
    check_exchange(request, response, snapshot, metadata)
    return copy.deepcopy(response)
