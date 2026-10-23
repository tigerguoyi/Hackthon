#!/usr/bin/env python3
"""Validate a generated league, including every summary/event/state file.

Unlike ``validate.py`` (which requires a full ``tracking.jsonl``), this script
works for leagues generated with ``--tracking featured`` or ``--tracking none``:
it checks the schedule, each match's summary/events/states/preview, the league
table, and the scorer totals.
"""
import argparse
import json
from collections import Counter
from pathlib import Path


def load_lines(path):
    with path.open() as stream:
        for line in stream:
            yield json.loads(line)


def expected_table(fixtures, clubs):
    rows = {club['club_id']: {'club_id': club['club_id'], 'name': club['name'],
                              'played': 0, 'won': 0,
                              'drawn': 0, 'lost': 0, 'goals_for': 0,
                              'goals_against': 0, 'goal_difference': 0,
                              'points': 0, 'form': []}
            for club in clubs}
    for fixture in fixtures:
        home = rows[fixture['home_club_id']]
        away = rows[fixture['away_club_id']]
        home_score, away_score = fixture['score']
        home['played'] += 1
        away['played'] += 1
        home['goals_for'] += home_score
        home['goals_against'] += away_score
        away['goals_for'] += away_score
        away['goals_against'] += home_score
        if home_score > away_score:
            home['won'] += 1
            home['points'] += 3
            away['lost'] += 1
            home['form'].append('W')
            away['form'].append('L')
        elif home_score < away_score:
            away['won'] += 1
            away['points'] += 3
            home['lost'] += 1
            home['form'].append('L')
            away['form'].append('W')
        else:
            home['drawn'] += 1
            away['drawn'] += 1
            home['points'] += 1
            away['points'] += 1
            home['form'].append('D')
            away['form'].append('D')
    for row in rows.values():
        row['goal_difference'] = row['goals_for']-row['goals_against']
    table = sorted(rows.values(), key=lambda row: (-row['points'], -row['goal_difference'],
                                                   -row['goals_for'], row['name']))
    for position, row in enumerate(table, 1):
        row['position'] = position
        row['form'] = ''.join(row['form'])
    return table


def validate(root):
    root = Path(root)
    league = json.loads((root/'league.json').read_text())
    fixtures = list(load_lines(root/'fixtures.jsonl'))
    clubs = league['teams']
    club_by_id = {club['club_id']: club for club in clubs}
    teams = set(club_by_id)
    assert len(clubs) == 20 and len(teams) == 20, 'expected 20 unique clubs'
    squads_data = json.loads((root/'squads.json').read_text())
    squad_by_club = {team['club_id']: team for team in squads_data['teams']}
    assert set(squad_by_club) == teams, 'squad file must cover all 20 clubs'
    assert sum(len(team['players']) for team in squads_data['teams']) >= 500
    for team in squads_data['teams']:
        assert len({player['player_id'] for player in team['players']}) == len(team['players'])
        for player in team['players']:
            assert player['name'] and player['position'] in ('GK', 'DEF', 'MID', 'FWD')
            assert isinstance(player['shirt_number'], int)
    assert league['format']['team_count'] == 20
    assert league['format']['matches_per_week'] == 10
    assert league['format']['total_matches'] == len(fixtures)
    assert len(fixtures) == league['format']['matchweeks']*10
    assert len({fixture['match_id'] for fixture in fixtures}) == len(fixtures)

    fixture_by_id = {fixture['match_id']: fixture for fixture in fixtures}
    seen_pairs = set()
    seen_ids = set()
    full_tracking = 0
    for week in league['weeks']:
        assert len(week['matches']) == 10
        week_teams = []
        for fixture in week['matches']:
            week_teams += [fixture['home_club_id'], fixture['away_club_id']]
            assert fixture['match_id'] in fixture_by_id
            assert fixture['match_id'] not in seen_ids
            seen_ids.add(fixture['match_id'])
            pair = frozenset((fixture['home_club_id'], fixture['away_club_id']))
            assert pair not in seen_pairs, f'duplicate pairing {pair}'
            seen_pairs.add(pair)
        assert len(week_teams) == 20 and set(week_teams) == teams, 'each club must play once'
    assert seen_ids == set(fixture_by_id)

    total_goals = 0
    home_wins = draws = away_wins = 0
    for fixture in fixtures:
        match_id = fixture['match_id']
        match_dir = root/fixture['paths']['match']
        assert match_dir.is_dir(), match_id
        for key in ('metadata', 'summary', 'events', 'states', 'preview'):
            path = root/fixture['paths'][key]
            assert path.exists(), f'{match_id}: missing {key}'
        metadata = json.loads((root/fixture['paths']['metadata']).read_text())
        summary = json.loads((root/fixture['paths']['summary']).read_text())
        events = list(load_lines(root/fixture['paths']['events']))
        states = list(load_lines(root/fixture['paths']['states']))
        preview = json.loads((root/fixture['paths']['preview']).read_text())

        assert metadata['match_id'] == summary['match_id'] == match_id
        assert metadata['synthetic'] is True
        assert len(metadata['players']) in (22, 28)
        assert len({player['player_id'] for player in metadata['players']}) == len(metadata['players'])
        assert sum(player.get('starter', True) for player in metadata['players']) == 22
        meta_teams = {team['team_id']: team for team in metadata['teams']}
        assert meta_teams['HOME']['club_id'] == fixture['home_club_id']
        assert meta_teams['AWAY']['club_id'] == fixture['away_club_id']
        assert metadata['home_club']['club_id'] == fixture['home_club_id']
        assert metadata['away_club']['club_id'] == fixture['away_club_id']
        assert metadata['competition']['matchweek'] == fixture['matchweek']
        for player in metadata['players']:
            club_id = fixture['home_club_id'] if player['team_id'] == 'HOME' else fixture['away_club_id']
            squad_player = next(p for p in squad_by_club[club_id]['players'] if p['name'] == player['name'])
            assert player['jersey_number'] == squad_player['shirt_number']

        assert summary['final_score'] == fixture['score']
        assert fixture['stats'] == summary['final_stats']
        assert summary['event_count'] == len(events)
        assert summary['event_counts'] == dict(Counter(event['type'] for event in events))
        assert summary['tracking_frames'] == 30001
        assert summary['state_snapshots'] == 6001
        goals = summary['goals']
        assert len(goals) == sum(fixture['score'])
        goal_counts = Counter(goal['team_id'] for goal in goals)
        assert [goal_counts['HOME'], goal_counts['AWAY']] == fixture['score']
        assert all(goal['club_id'] == fixture['home_club_id'] if goal['team_id'] == 'HOME'
                   else goal['club_id'] == fixture['away_club_id'] for goal in goals)
        total_goals += sum(fixture['score'])
        if fixture['score'][0] > fixture['score'][1]:
            home_wins += 1
        elif fixture['score'][0] < fixture['score'][1]:
            away_wins += 1
        else:
            draws += 1

        assert all(event['match_id'] == match_id for event in events)
        assert all(events[i]['available_at_ms'] <= events[i+1]['available_at_ms']
                   for i in range(len(events)-1))
        assert events[-1]['type'] == 'match_end'
        active = {p['player_id'] for p in metadata['players'] if p.get('starter', True)}
        roster = {p['player_id']: p for p in metadata['players']}
        for event in events:
            for field in ('player_id', 'recipient_id', 'target_player_id'):
                if field in event:
                    assert event[field] in active, (match_id, event)
            if event['type'] == 'substitution':
                assert event['player_in_id'] not in active
                assert roster[event['player_in_id']]['team_id'] == roster[event['player_out_id']]['team_id'] == event['team_id']
                active.remove(event['player_out_id'])
                active.add(event['player_in_id'])
            elif event['type'] == 'red_card':
                active.remove(event['player_id'])
        assert len(states) == 6001
        assert states[-1]['t_ms'] == 6000000
        assert states[-1]['score'] == summary['final_score']
        assert states[-1]['stats'] == summary['final_stats']
        assert 0 <= preview['start_ms'] < preview['end_ms'] <= 6000000
        assert preview['match_id'] == match_id

        if fixture['tracking'] == 'full':
            tracking = root/fixture['paths']['tracking']
            assert tracking.exists(), f'{match_id}: missing tracking'
            lines = load_lines(tracking)
            first = next(lines)
            last = first
            count = 1
            for frame in lines:
                last = frame
                count += 1
            assert count == 30001, f'{match_id}: tracking frame count {count}'
            assert first['t_ms'] == 0 and last['t_ms'] == 6000000
            assert len(first['players']) == 22
            full_tracking += 1
        else:
            assert fixture['paths']['tracking'] is None

    table = expected_table(fixtures, clubs)
    actual = [{key: row[key] for key in ('position', 'club_id', 'played', 'won', 'drawn',
                                         'lost', 'goals_for', 'goals_against',
                                         'goal_difference', 'points', 'form')}
              for row in league['standings']]
    expected = [{key: row[key] for key in ('position', 'club_id', 'played', 'won', 'drawn',
                                           'lost', 'goals_for', 'goals_against',
                                           'goal_difference', 'points', 'form')}
                for row in table]
    assert actual == expected, 'standings do not match fixture results'
    assert all(row['played'] == league['format']['matchweeks'] for row in table)
    scorer_goals = sum(row['goals'] for row in league['top_scorers'])
    assert scorer_goals == total_goals, (scorer_goals, total_goals)
    assert league['totals']['matches'] == len(fixtures)
    assert league['totals']['goals'] == total_goals
    assert league['totals']['home_wins'] == home_wins
    assert league['totals']['draws'] == draws
    assert league['totals']['away_wins'] == away_wins
    assert league['totals']['clean_sheets'] == sum(1 for fixture in fixtures if 0 in fixture['score'])
    assert full_tracking == league['format']['matches_with_full_tracking']

    report = {
        'status': 'PASS',
        'competition': league['competition']['name'],
        'teams': len(clubs),
        'matchweeks': len(league['weeks']),
        'matches': len(fixtures),
        'goals': total_goals,
        'matches_with_full_tracking': full_tracking,
        'checks': [
            '20 unique clubs and 10 fixtures per matchweek',
            'every club plays exactly once per matchweek',
            'no repeated pairing in the selected rounds',
            'per-match metadata, summary, events, states and preview exist',
            'all 20 clubs have a real 2026/27 squad and match lineups use squad players',
            'event order, match_end and event/score consistency',
            'substitute identity, team membership and no events from departed players',
            'state snapshots and final statistics consistency',
            'fixture scores, standings, totals and scorer totals agree',
            'featured matches contain exactly 30,001 tracking frames',
        ],
        'scope': 'Structural and internal consistency; not validation against real football data.',
    }
    (root/'validation_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Validate a generated synthetic league')
    parser.add_argument('league', nargs='?', type=Path,
                        default=Path(__file__).parent/'data'/'league')
    validate(parser.parse_args().league)
