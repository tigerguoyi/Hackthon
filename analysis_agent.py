#!/usr/bin/env python3
"""Post-match evidence agent. Standard library only; optional OpenAI narration."""
import argparse
import hashlib
import json
import os
import tempfile
from model_service import narrate
from pathlib import Path

VERSION = '1.0.0'
METHOD = (
    'Impact points: goal +4, non-goal shot on target +0.6, save +0.7, '
    'interception +0.12 (max 2), completed pass +0.01 (max 1), '
    'incomplete pass -0.02 (max penalty 1), yellow card -0.5, red card -2. '
    'Ties: goals, saves, interceptions, completed passes, then player ID. '
    'Only players who appeared are eligible. These are demo impact points, not an official rating.'
)


def fingerprint(metadata, summary, events):
    data = json.dumps([VERSION, metadata, summary, events], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


def clock(event, metadata):
    period = next(p for p in metadata['periods'] if p['period'] == event['period'])
    elapsed = event['end_ms'] - period['start_ms']
    base = sum(p['regulation_ms'] for p in metadata['periods'] if p['period'] < period['period'])
    if elapsed > period['regulation_ms']:
        seconds = (elapsed - period['regulation_ms']) // 1000
        return f"{(base + period['regulation_ms']) // 60000}+{seconds // 60}:{seconds % 60:02}"
    seconds = (base + elapsed) // 1000
    return f'{seconds // 60:02}:{seconds % 60:02}'


def build_report(metadata, summary, events):
    """Validate completion, inspect the whole event stream, rank and explain."""
    match_id = metadata['match_id']
    if summary['match_id'] != match_id or any(e['match_id'] != match_id for e in events):
        raise ValueError('Match IDs do not agree.')
    ordered = sorted(events, key=lambda e: (e['available_at_ms'], e['sequence']))
    if len({e['event_id'] for e in ordered}) != len(ordered):
        raise ValueError('Duplicate event IDs.')
    ends = [e for e in ordered if e['type'] == 'match_end']
    if not ends:
        return None
    end = ends[-1]
    if (len(ends) != 1 or end != ordered[-1] or end['available_at_ms'] < metadata['duration_ms']
            or end['score'] != summary['final_score'] or len(events) != summary['event_count']):
        raise ValueError('Incomplete or inconsistent final match data.')
    teams = {t['team_id']: t['name'] for t in metadata['teams']}
    players = {}
    active = {}
    for p in metadata['players']:
        pid = p['player_id']
        active[pid] = 0 if p.get('starter', True) else None
        players[pid] = {**p, 'minutes': 0, 'goals': 0, 'shots': 0, 'shots_on_target': 0,
                        'saves': 0, 'interceptions': 0, 'passes': 0, 'completed_passes': 0,
                        'yellow_cards': 0, 'red_cards': 0, 'evidence_event_ids': []}
    score = [0, 0]
    moments = []
    half_reports = []
    for event in ordered:
        typ = event['type']
        pid = event.get('player_id')
        p = players.get(pid)
        if typ == 'substitution':
            outgoing, incoming = event['player_out_id'], event['player_in_id']
            if active[outgoing] is not None:
                players[outgoing]['minutes'] += (event['end_ms'] - active[outgoing]) / 60000
            active[outgoing] = None
            active[incoming] = event['end_ms']
        if p is not None:
            if typ == 'shot':
                p['shots'] += 1
                p['shots_on_target'] += event['outcome'] in ('goal', 'saved')
                p['goals'] += event['outcome'] == 'goal'
            elif typ == 'pass':
                p['passes'] += 1
                p['completed_passes'] += event['outcome'] == 'complete'
            elif typ in ('save', 'interception', 'yellow_card', 'red_card'):
                p[{'save': 'saves', 'interception': 'interceptions',
                   'yellow_card': 'yellow_cards', 'red_card': 'red_cards'}[typ]] += 1
            if typ in ('shot', 'save', 'interception', 'yellow_card', 'red_card', 'pass'):
                p['evidence_event_ids'].append(event['event_id'])
            if typ == 'red_card' and active[pid] is not None:
                p['minutes'] += (event['end_ms'] - active[pid]) / 60000
                active[pid] = None
        text = None
        if typ == 'shot' and event['outcome'] == 'goal':
            side = 0 if event['team_id'] == 'HOME' else 1
            previous = score[:]
            score[side] += 1
            if score != event['score']:
                raise ValueError('Goal sequence does not match the recorded score.')
            situation = ('opened the scoring' if sum(previous) == 0 else
                         'equalised' if score[0] == score[1] else
                         'took the lead' if previous[side] <= previous[1-side] < score[side] else
                         'extended the lead' if score[side] > score[1-side] else 'reduced the deficit')
            text = f"{p['name']} {situation} for {teams[p['team_id']]} ({score[0]}–{score[1]})."
        elif typ == 'red_card':
            text = f"{teams[p['team_id']]} lost {p['name']} to a red card."
        elif typ == 'substitution':
            text = f"{players[event['player_in_id']]['name']} replaced {players[event['player_out_id']]['name']} for {teams[event['team_id']]}."
        if text:
            moments.append({'event_id': event['event_id'], 'period': event['period'],
                            'time': clock(event, metadata), 'type': typ, 'text': text})
    if score != summary['final_score']:
        raise ValueError('Goals do not match the final score.')
    for pid, started in active.items():
        if started is not None:
            players[pid]['minutes'] += (end['end_ms'] - started) / 60000
    for p in players.values():
        p['minutes'] = round(p['minutes'], 1)
        p['impact_points'] = round(
            4 * p['goals'] + .6 * (p['shots_on_target'] - p['goals']) + .7 * p['saves']
            + min(2, .12 * p['interceptions']) + min(1, .01 * p['completed_passes'])
            - min(1, .02 * (p['passes'] - p['completed_passes']))
            - .5 * p['yellow_cards'] - 2 * p['red_cards'], 2)
    ranking = sorted((p for p in players.values() if p['minutes'] > 0),
                     key=lambda p: (-p['impact_points'], -p['goals'], -p['saves'],
                                    -p['interceptions'], -p['completed_passes'], p['player_id']))
    if not ranking:
        raise ValueError('No eligible players.')
    winner = ranking[0]
    def count(value, label):
        return f"{value} {label}{'' if value == 1 else 's'}"

    reasons = [f"{winner['name']} recorded {count(winner['goals'], 'goal')}, "
               f"{count(winner['shots_on_target'], 'shot')} on target, "
               f"{count(winner['saves'], 'save')} and {count(winner['interceptions'], 'interception')}.",
               f"Completed {winner['completed_passes']} of {winner['passes']} passes in {winner['minutes']:g} minutes; "
               f"received {count(winner['yellow_cards'], 'yellow card')} and {count(winner['red_cards'], 'red card')}."]
    tied = [p for p in ranking if p['impact_points'] == winner['impact_points']]
    reasons.append(f"Top impact score: {winner['impact_points']:.2f} points."
                   + (' The published tie-break order decided the award.' if len(tied) > 1 else ''))
    half_score = [0, 0]
    for period in metadata['periods']:
        half_events = [e for e in ordered if e['period'] == period['period']]
        shots = [sum(e['type'] == 'shot' and e.get('team_id') == team for e in half_events)
                 for team in ('HOME', 'AWAY')]
        goals = [e for e in half_events if e['type'] == 'shot' and e['outcome'] == 'goal']
        for goal in goals:
            half_score[0 if goal['team_id'] == 'HOME' else 1] += 1
        half_moments = [m for m in moments if m['period'] == period['period'] and m['type'] != 'substitution']
        half_reports.append({
            'title': 'First half' if period['period'] == 1 else 'Second half',
            'text': f"{teams['HOME']} had {shots[0]} shots and {teams['AWAY']} had {shots[1]}. "
                    + (' '.join(f"{m['time']} — {m['text']}" for m in half_moments)
                       if half_moments else 'Neither side scored and there were no red cards.')
                    + f" The period ended at {half_score[0]}–{half_score[1]}.",
            'shots': shots, 'score_at_end': half_score[:],
            'evidence_event_ids': [e['event_id'] for e in half_events if e['type'] in ('shot', 'red_card')],
        })
    home, away = teams['HOME'], teams['AWAY']
    h, a = score
    headline = f'{home} {h}–{a} {away}'
    outcome = (f'{home} and {away} drew {h}–{a}.' if h == a else
               f"{home if h > a else away} won {max(score)}–{min(score)}.")
    stats = summary['final_stats']
    insights = []
    for team in ('HOME', 'AWAY'):
        s = stats[team]
        possession = s.get('possession_pct')
        insights.append(f"{teams[team]}: {s['shots']} shots, {s['shots_on_target']} on target, "
                        f"{s['completed_passes']} of {s['passes']} passes completed"
                        + (f", {possession:.1f}% possession." if possession is not None else '.'))
    return {
        'schema_version': VERSION, 'match_id': match_id, 'status': 'complete', 'language': 'en',
        'source': {'fingerprint': fingerprint(metadata, summary, events),
                   'event_count': len(events), 'match_end_event_id': end['event_id'],
                   'completed_at_ms': end['available_at_ms'], 'final_score': score},
        'agent': {'name': 'Touchline Match Analyst', 'mode': 'local', 'model': None},
        'headline': headline, 'summary': f"{outcome} There were {sum(p['shots'] for p in players.values())} shots "
        f"across the match. {winner['name']} earns Player of the Match on the published impact scoring.",
        'periods': half_reports, 'insights': insights, 'key_moments': moments,
        'player_of_the_match': {**winner, 'reasons': reasons}, 'ranking': ranking,
        'methodology': METHOD,
        'disclaimer': 'Simulated match data. Local scoring is heuristic; pressure tags and distance do not establish tactical intent. Assists and xG are not recorded.',
    }



def write_report(out, metadata, summary, events, *, mode=None, force=False):
    """Persist after match_end; repeated calls reuse matching reports."""
    out = Path(out)
    path = out / 'analysis.json'
    mode = mode or os.getenv('TOUCHLINE_ANALYSIS_MODE', 'local')
    if mode not in ('local', 'openai'):
        raise ValueError('TOUCHLINE_ANALYSIS_MODE must be local or openai.')
    model = os.getenv('OPENAI_MODEL')
    source_hash = fingerprint(metadata, summary, events)
    if path.exists() and not force:
        try:
            cached = json.loads(path.read_text())
            if (cached['source']['fingerprint'] == source_hash and cached['agent']['mode'] == mode
                    and (mode != 'openai' or cached['agent']['model'] == model)):
                return cached
        except (ValueError, KeyError, TypeError):
            pass
    report = build_report(metadata, summary, events)
    if report is None:
        path.unlink(missing_ok=True)
        return None
    if mode == 'openai':
        key = os.getenv('OPENAI_API_KEY')
        if not key or not model:
            report['agent']['notice'] = 'Model analysis was requested but OPENAI_API_KEY or OPENAI_MODEL is missing. Local evidence analysis is shown.'
        else:
            try:
                narrate(report, model, key)
            except Exception:
                # Do not persist credentials, response bodies or network internals.
                report['agent']['notice'] = 'Model analysis was unavailable. Local evidence analysis is shown; rerun analysis to retry.'
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=out, suffix='.tmp', delete=False, encoding='utf-8') as f:
        temp = Path(f.name)
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write('\n')
    temp.replace(path)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', nargs='?', default='data/league', type=Path)
    parser.add_argument('--mode', choices=['local', 'openai'], default=None)
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    paths = [args.path] if (args.path / 'metadata.json').exists() else sorted((args.path / 'matches').glob('*'))
    if not paths:
        parser.error('No match directories found.')
    for path in paths:
        metadata = json.loads((path / 'metadata.json').read_text())
        summary = json.loads((path / 'summary.json').read_text())
        events = [json.loads(line) for line in (path / 'events.jsonl').read_text().splitlines() if line.strip()]
        report = write_report(path, metadata, summary, events, mode=args.mode, force=args.force)
        print(f"{metadata['match_id']}: {report['agent']['mode'] if report else 'awaiting full time'}")
        if report and report['agent'].get('notice'):
            print(report['agent']['notice'])


if __name__ == '__main__':
    main()
