#!/usr/bin/env python3
"""Deterministic, dependency-free synthetic football replay and league generator.

Two modes are available:

* ``league`` (default): simulate a mini Premier League season.  Twenty current
  Premier League clubs play four matchweeks; every matchweek pairs all twenty
  clubs into ten matches, so every club plays exactly once per week (40 matches
  in total).  Each match is a complete 100-minute 5 Hz replay.
* ``single``: regenerate the original two-team ``HOME / AWAY`` match used by the
  bundled single-match front-end.

Only the Python standard library is required (Python 3.9+).

Examples
--------
    python3 generate.py
    python3 generate.py --weeks 4 --tracking featured
    python3 generate.py --tracking all
    python3 generate.py --mode single --output data
"""
import argparse
import datetime as dt
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from analysis_agent import write_report

DT = 0.2
HZ = 5
DURATION = 6000
HALF = 3000
DEFAULT_MATCH = 'SYN-20261019-001'
VERSION = '2.1.0'
ROOT = Path(__file__).resolve().parent
BASE = [(6, 34), (24, 9), (27, 25), (27, 43), (24, 59),
        (43, 18), (40, 34), (43, 50), (62, 10), (65, 34), (62, 58)]
POSITIONS = ['GK', 'LB', 'LCB', 'RCB', 'RB', 'LCM', 'DM', 'RCM', 'LW', 'ST', 'RW']
NEUTRAL = {'attack': 1.0, 'defense': 1.0, 'passing': 1.0, 'pressing': 1.0}

# The twenty clubs of the 2026/27 Premier League.  Club identity and squad
# numbers are real; match events and tracking remain synthetic.
CLUBS = [
    {'club_id': 'ARS', 'name': 'Arsenal', 'short_name': 'Arsenal',
     'stadium': 'Emirates Stadium', 'colors': {'primary': '#EF0107', 'secondary': '#FFFFFF'}, 'formation': '4-3-3'},
    {'club_id': 'AVL', 'name': 'Aston Villa', 'short_name': 'Aston Villa',
     'stadium': 'Villa Park', 'colors': {'primary': '#670E36', 'secondary': '#95BFE5'}, 'formation': '4-3-3'},
    {'club_id': 'BOU', 'name': 'AFC Bournemouth', 'short_name': 'Bournemouth',
     'stadium': 'Vitality Stadium', 'colors': {'primary': '#DA291C', 'secondary': '#000000'}, 'formation': '4-3-3'},
    {'club_id': 'BRE', 'name': 'Brentford', 'short_name': 'Brentford',
     'stadium': 'Gtech Community Stadium', 'colors': {'primary': '#E30613', 'secondary': '#FFFFFF'}, 'formation': '4-3-3'},
    {'club_id': 'BHA', 'name': 'Brighton & Hove Albion', 'short_name': 'Brighton',
     'stadium': 'Amex Stadium', 'colors': {'primary': '#0057B8', 'secondary': '#FFFFFF'}, 'formation': '4-3-3'},
    {'club_id': 'CHE', 'name': 'Chelsea', 'short_name': 'Chelsea',
     'stadium': 'Stamford Bridge', 'colors': {'primary': '#034694', 'secondary': '#FFFFFF'}, 'formation': '4-3-3'},
    {'club_id': 'COV', 'name': 'Coventry City', 'short_name': 'Coventry',
     'stadium': 'CBS Arena', 'colors': {'primary': '#6CB4E4', 'secondary': '#FFFFFF'}, 'formation': '4-3-3'},
    {'club_id': 'CRY', 'name': 'Crystal Palace', 'short_name': 'Crystal Palace',
     'stadium': 'Selhurst Park', 'colors': {'primary': '#1B458F', 'secondary': '#C4122E'}, 'formation': '4-3-3'},
    {'club_id': 'EVE', 'name': 'Everton', 'short_name': 'Everton',
     'stadium': 'Hill Dickinson Stadium', 'colors': {'primary': '#003399', 'secondary': '#FFFFFF'}, 'formation': '4-3-3'},
    {'club_id': 'FUL', 'name': 'Fulham', 'short_name': 'Fulham',
     'stadium': 'Craven Cottage', 'colors': {'primary': '#FFFFFF', 'secondary': '#000000'}, 'formation': '4-3-3'},
    {'club_id': 'HUL', 'name': 'Hull City', 'short_name': 'Hull City',
     'stadium': 'MKM Stadium', 'colors': {'primary': '#F5A12D', 'secondary': '#000000'}, 'formation': '4-3-3'},
    {'club_id': 'IPS', 'name': 'Ipswich Town', 'short_name': 'Ipswich Town',
     'stadium': 'Portman Road', 'colors': {'primary': '#0044A9', 'secondary': '#FFFFFF'}, 'formation': '4-3-3'},
    {'club_id': 'LEE', 'name': 'Leeds United', 'short_name': 'Leeds',
     'stadium': 'Elland Road', 'colors': {'primary': '#FFFFFF', 'secondary': '#1D428A'}, 'formation': '4-3-3'},
    {'club_id': 'LIV', 'name': 'Liverpool', 'short_name': 'Liverpool',
     'stadium': 'Anfield', 'colors': {'primary': '#C8102E', 'secondary': '#FFFFFF'}, 'formation': '4-3-3'},
    {'club_id': 'MCI', 'name': 'Manchester City', 'short_name': 'Man City',
     'stadium': 'Etihad Stadium', 'colors': {'primary': '#6CABDD', 'secondary': '#1C2C5B'}, 'formation': '4-3-3'},
    {'club_id': 'MUN', 'name': 'Manchester United', 'short_name': 'Man United',
     'stadium': 'Old Trafford', 'colors': {'primary': '#DA291C', 'secondary': '#FBE122'}, 'formation': '4-3-3'},
    {'club_id': 'NEW', 'name': 'Newcastle United', 'short_name': 'Newcastle',
     'stadium': "St James' Park", 'colors': {'primary': '#241F20', 'secondary': '#FFFFFF'}, 'formation': '4-3-3'},
    {'club_id': 'NFO', 'name': 'Nottingham Forest', 'short_name': 'Forest',
     'stadium': 'City Ground', 'colors': {'primary': '#DD0000', 'secondary': '#FFFFFF'}, 'formation': '4-3-3'},
    {'club_id': 'SUN', 'name': 'Sunderland', 'short_name': 'Sunderland',
     'stadium': 'Stadium of Light', 'colors': {'primary': '#EB172B', 'secondary': '#FFFFFF'}, 'formation': '4-3-3'},
    {'club_id': 'TOT', 'name': 'Tottenham Hotspur', 'short_name': 'Tottenham',
     'stadium': 'Tottenham Hotspur Stadium', 'colors': {'primary': '#FFFFFF', 'secondary': '#132257'}, 'formation': '4-3-3'},
]
CLUB_BY_ID = {club['club_id']: club for club in CLUBS}

# Mild strength multipliers used only by league mode.  1.00 is neutral; the
# values are heuristic and deliberately compressed so that any club can win.
RATINGS = {
    'ARS': {'attack': 1.15, 'defense': 1.10, 'passing': 1.10, 'pressing': 1.08},
    'LIV': {'attack': 1.14, 'defense': 1.08, 'passing': 1.09, 'pressing': 1.10},
    'MCI': {'attack': 1.12, 'defense': 1.12, 'passing': 1.12, 'pressing': 1.06},
    'CHE': {'attack': 1.08, 'defense': 1.06, 'passing': 1.07, 'pressing': 1.04},
    'NEW': {'attack': 1.04, 'defense': 1.07, 'passing': 1.02, 'pressing': 1.08},
    'TOT': {'attack': 1.03, 'defense': 1.02, 'passing': 1.02, 'pressing': 1.01},
    'AVL': {'attack': 1.02, 'defense': 1.00, 'passing': 1.01, 'pressing': 1.00},
    'MUN': {'attack': 1.00, 'defense': 1.01, 'passing': 1.00, 'pressing': 1.02},
    'BHA': {'attack': 1.00, 'defense': 0.99, 'passing': 1.03, 'pressing': 1.03},
    'CRY': {'attack': 0.98, 'defense': 1.02, 'passing': 0.97, 'pressing': 1.05},
    'BOU': {'attack': 0.98, 'defense': 0.98, 'passing': 0.98, 'pressing': 1.04},
    'BRE': {'attack': 0.97, 'defense': 0.99, 'passing': 0.96, 'pressing': 1.03},
    'FUL': {'attack': 0.96, 'defense': 0.98, 'passing': 0.99, 'pressing': 0.98},
    'NFO': {'attack': 0.96, 'defense': 1.00, 'passing': 0.95, 'pressing': 0.99},
    'EVE': {'attack': 0.94, 'defense': 0.97, 'passing': 0.95, 'pressing': 0.98},
    'LEE': {'attack': 0.92, 'defense': 0.92, 'passing': 0.93, 'pressing': 1.00},
    'SUN': {'attack': 0.91, 'defense': 0.93, 'passing': 0.92, 'pressing': 0.99},
    'COV': {'attack': 0.90, 'defense': 0.92, 'passing': 0.91, 'pressing': 0.97},
    'IPS': {'attack': 0.90, 'defense': 0.92, 'passing': 0.92, 'pressing': 0.99},
    'HUL': {'attack': 0.89, 'defense': 0.91, 'passing': 0.90, 'pressing': 0.98},
}
COMPETITION = {
    'id': 'PL-SYN-2026',
    'name': 'Premier League (synthetic)',
    'season': '2026/27',
    'synthetic': True,
}
# Four Saturdays starting after the deck's reference date.  A full round uses
# ten fixtures, split across Saturday and Sunday with local kick-off times.
BASE_DATE = dt.date(2026, 10, 24)
SLOTS = [(0, '12:30'), (0, '15:00'), (0, '15:00'), (0, '17:30'), (0, '20:00'),
         (1, '14:00'), (1, '14:00'), (1, '16:30'), (1, '16:30'), (1, '19:00')]

LEGACY_HOME = {'club_id': 'HOME', 'name': 'Harbor FC',
               'short_name': 'Harbor', 'stadium': 'Harbor Park',
               'colors': {'primary': '#0B6E4F', 'secondary': '#FFFFFF'}, 'formation': '4-3-3'}
LEGACY_AWAY = {'club_id': 'AWAY', 'name': 'Mountain FC',
               'short_name': 'Mountain', 'stadium': 'Mountain Ground',
               'colors': {'primary': '#6B4C9A', 'secondary': '#FFFFFF'}, 'formation': '4-3-3'}


def clamp(x, low, high):
    return max(low, min(high, x))


def norm(x, y):
    return math.hypot(x, y)


def encode(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def public_club(club):
    keys = ('club_id', 'name', 'short_name', 'stadium', 'colors', 'formation')
    return {key: club[key] for key in keys}


def load_squads(path=None):
    """Load the checked-in 2026/27 squad file, if present."""
    path = Path(path or ROOT/'data'/'league'/'squads.json')
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {team['club_id']: team for team in data.get('teams', [])}


def select_lineup(club_id, squads):
    """Pick 11 starters and three substitutes from a club's real squad.

    The simulation uses a 4-3-3 shape. Players are assigned to the closest
    available slot by position, then any gaps are filled from the remaining
    squad so that every team always has 14 unique players.
    """
    team = squads.get(club_id)
    if not team or len(team.get('players', [])) < 14:
        return None
    pool = sorted(team['players'], key=lambda p: p['shirt_number'])
    used = set()

    def detail(player):
        return (player.get('position_detail') or '').lower()

    def take(predicate=None):
        for player in pool:
            if player['player_id'] in used:
                continue
            if predicate is None or predicate(player):
                used.add(player['player_id'])
                return player
        return None

    goalkeeper = take(lambda p: p['position'] == 'GK') or take()
    left_back = take(lambda p: p['position'] == 'DEF' and 'left' in detail(p)) or take(lambda p: p['position'] == 'DEF') or take()
    right_back = take(lambda p: p['position'] == 'DEF' and 'right' in detail(p)) or take(lambda p: p['position'] == 'DEF') or take()
    centre_back_1 = take(lambda p: p['position'] == 'DEF') or take()
    centre_back_2 = take(lambda p: p['position'] == 'DEF') or take()
    defensive_mid = take(lambda p: p['position'] == 'MID' and 'defensive' in detail(p)) or take(lambda p: p['position'] == 'MID') or take()
    central_mid_1 = take(lambda p: p['position'] == 'MID') or take()
    central_mid_2 = take(lambda p: p['position'] == 'MID') or take()
    left_wing = take(lambda p: p['position'] == 'FWD' and 'left' in detail(p)) or take(lambda p: p['position'] == 'FWD') or take()
    right_wing = take(lambda p: p['position'] == 'FWD' and 'right' in detail(p)) or take(lambda p: p['position'] == 'FWD') or take()
    striker = take(lambda p: p['position'] == 'FWD' and any(word in detail(p) for word in ('striker', 'forward', 'centre'))) or take(lambda p: p['position'] == 'FWD') or take()

    starters = [goalkeeper, left_back, centre_back_1, centre_back_2, right_back,
                central_mid_1, defensive_mid, central_mid_2, left_wing, striker, right_wing]
    slots = ['GK', 'LB', 'LCB', 'RCB', 'RB', 'LCM', 'DM', 'RCM', 'LW', 'ST', 'RW']
    lineup = []
    for slot, position, player in zip(range(1, 12), slots, starters):
        if player is None:
            player = {'name': None, 'shirt_number': slot, 'position': position,
                      'position_detail': position, 'nationality': None,
                      'date_of_birth': None, 'age': None, 'player_id': None}
        lineup.append({'slot': slot, 'slot_position': position, **player})

    # Bench: one defender, one midfielder and one forward where possible.
    bench = []
    for position, slot, slot_position in (('DEF', 14, 'LB'), ('MID', 12, 'LCM'), ('FWD', 13, 'RW')):
        player = take(lambda p, pos=position: p['position'] == pos) or take()
        if player:
            bench.append({'slot': slot, 'slot_position': slot_position, **player})
    while len(bench) < 3:
        player = take()
        if not player:
            break
        bench.append({'slot': 12 + len(bench), 'slot_position': ['LCM', 'RW', 'LB'][len(bench)], **player})
    return lineup + sorted(bench, key=lambda p: p['slot'])


class Match:
    """One complete 100-minute match between a HOME and an AWAY side."""

    def __init__(self, seed, match_id=DEFAULT_MATCH, home=None, away=None,
                 ratings=None, competition=None, lineups=None):
        self.rng = random.Random(seed)
        self.seed = seed
        self.match_id = match_id
        self.legacy = home is None and away is None
        self.home = home or LEGACY_HOME
        self.away = away or LEGACY_AWAY
        self.ratings = ratings
        self.competition = competition
        self.t = 0
        self.period = 1
        self.lineups = lineups
        self.players = []
        for team in range(2):
            lineup = lineups[team] if lineups else None
            for number, (x, y) in enumerate(BASE, 1):
                real = lineup[number-1] if lineup and len(lineup) >= number else None
                self.players.append({
                    'id': f'{"H" if team == 0 else "A"}{number:02}',
                    'team': team, 'number': number,
                    'x': x if team == 0 else 105-x, 'y': y,
                    'vx': 0., 'vy': 0., 'distance': 0., 'active': True,
                    'position': POSITIONS[number-1], 'starter': True,
                    'position_detail': real.get('position_detail') if real else POSITIONS[number-1],
                    'name': real.get('name') if real else None,
                    'shirt_number': real.get('shirt_number') if real else number,
                    'nationality': real.get('nationality') if real else None,
                    'real_player_id': real.get('player_id') if real else None,
                })
        self.player_by_id = {p['id']: p.copy() for p in self.players}
        if not self.legacy:
            for team in range(2):
                lineup = lineups[team] if lineups else None
                for index, (number, position) in enumerate(((12, 'LCM'), (13, 'RW'), (14, 'LB'))):
                    pid = f'{"H" if team == 0 else "A"}{number:02}'
                    real = lineup[11+index] if lineup and len(lineup) > 11+index else None
                    self.player_by_id[pid] = {
                        'id': pid, 'team': team, 'number': number,
                        'position': position, 'starter': False,
                        'position_detail': real.get('position_detail') if real else position,
                        'name': real.get('name') if real else None,
                        'shirt_number': real.get('shirt_number') if real else number,
                        'nationality': real.get('nationality') if real else None,
                        'real_player_id': real.get('player_id') if real else None,
                    }
        self.distances = {pid: 0. for pid in self.player_by_id}
        # A separate RNG leaves the football decisions independent of scheduling.
        administrative_rng = random.Random(seed + 909)
        self.administrative_events = [] if self.legacy else sorted([
            (1200 + administrative_rng.randrange(300), 'yellow_card', 3, None),
            (1900 + administrative_rng.randrange(300), 'yellow_card', 15, None),
            (3700 + administrative_rng.randrange(180), 'substitution', 5, 12),
            (3900 + administrative_rng.randrange(180), 'substitution', 21, 13),
            (4400 + administrative_rng.randrange(180), 'substitution', 1, 14),
            (4700 + administrative_rng.randrange(180), 'substitution', 16, 12),
            (5100 + administrative_rng.randrange(180), 'red_card', 15, None),
        ])
        self.position_records = 0
        self.ball = [52.5, 34., 0.]
        self.last_ball = self.ball[:]
        self.ball_v = [0., 0., 0.]
        self.owner = None
        self.possession_team = None
        self.possession = 0
        self.score = [0, 0]
        self.stats = [{'passes': 0, 'completed_passes': 0, 'shots': 0,
                       'shots_on_target': 0, 'interceptions': 0,
                       'pressure_events': 0, 'possession_seconds': 0.} for _ in range(2)]
        self.events = []
        self.event_seq = 0
        self.action_seq = 0
        self.pressure_last = {}
        self.action = None
        self.emit('period_start', period=1)
        self.start_restart(0, (52.5, 34), 'kick_off', minimum=12)

    def direction(self, team):
        return 1 if (team == 0) == (self.period == 1) else -1

    def progress(self, x, team):
        return x if self.direction(team) == 1 else 105-x

    def club(self, team):
        return self.home if team == 0 else self.away

    def club_id(self, team):
        return self.club(team).get('club_id', self.team_id(team))

    def player_name(self, player_id):
        player = self.player_by_id[player_id]
        if player.get('name'):
            return player['name']
        club = self.club(player['team'])
        if self.legacy:
            prefix = 'Harbor' if player['team'] == 0 else 'Mountain'
            return f'{prefix} Player {player["number"]:02}'
        return f'{club["short_name"]} {player["number"]:02}'

    def rating(self, team, key):
        return 1.0 if not self.ratings else self.ratings[team][key]

    def emit(self, kind, start=None, **kwargs):
        self.event_seq += 1
        ms = int(round(self.t * 1000))
        ev = {'event_id': f'e{self.event_seq:06}', 'sequence': self.event_seq,
              'match_id': self.match_id, 'type': kind, 'period': self.period,
              'period_ms': ms - (self.period-1)*HALF*1000,
              'start_ms': ms if start is None else int(round(start*1000)),
              'end_ms': ms, 'available_at_ms': ms,
              'possession_id': self.possession}
        ev.update(kwargs)
        self.events.append(ev)
        return ev['event_id']

    def new_action(self, kind, duration, **kwargs):
        self.action_seq += 1
        self.action = {'id': f'a{self.action_seq:06}', 'kind': kind,
                       'start': self.t, 'end': round(self.t+math.ceil(duration/DT)*DT, 4),
                       'start_ball': self.ball[:], **kwargs}

    def set_possession(self, team, reason, cause=None):
        old = self.possession_team
        if team != old:
            self.possession += 1
            self.possession_team = team
            self.emit('possession_change', from_team_id=self.team_id(old),
                      to_team_id=self.team_id(team), reason=reason,
                      related_event_ids=[cause] if cause else [])

    @staticmethod
    def team_id(team):
        return None if team is None else ['HOME', 'AWAY'][team]

    def start_restart(self, team, spot, reason, minimum=6):
        self.owner = None
        if reason == 'kick_off':
            candidates = [team*11+9]
        elif reason in ('goal_kick', 'keeper_restart'):
            candidates = [team*11]
        else:
            candidates = [i for i in range(team*11+1, team*11+11) if self.players[i]['active']]
        taker = min(candidates, key=lambda i: norm(self.players[i]['x']-spot[0],
                                                  self.players[i]['y']-spot[1]))
        distance = norm(self.players[taker]['x']-spot[0], self.players[taker]['y']-spot[1])
        ball_distance = norm(self.ball[0]-spot[0], self.ball[1]-spot[1])
        duration = max(minimum, distance/3+3, ball_distance/6+1)
        # A short, explicitly dead-ball transport to the restart spot; not a pass.
        self.new_action('restart_setup', duration, team=team, receiver=taker,
                        target=list(spot), reason=reason)

    def target_position(self, index):
        p = self.players[index]
        team, slot = p['team'], index % 11
        direction = self.direction(team)
        bx, by = self.ball[:2]
        local_ball = self.progress(bx, team)
        own = self.possession_team == team
        base_x, base_y = BASE[slot]
        if slot == 0:
            x = clamp(5 + .05*(local_ball-45), 2.5, 11)
            y = clamp(34 + .15*(by-34), 26, 42)
        else:
            x = clamp(base_x + .35*(local_ball-52.5) + (7 if own else -3), 7, 97)
            y = clamp(base_y + .18*(by-34) + 1.5*math.sin(self.t*.18+index*1.4), 2, 66)
            x += 1.4*math.sin(self.t*.13+index*.9)
        target = [x if direction == 1 else 105-x, y]
        a = self.action
        if a['kind'] == 'restart_setup' and index == a['receiver']:
            return a['target'][:]
        if a['kind'] == 'pass' and index == a['receiver']:
            return a['target'][:]
        if a['kind'] == 'carry' and index == self.owner:
            return a['target'][:]
        if a['kind'] == 'shot' and index == a['actor']:
            return [p['x'], p['y']]
        # Two nearest opposition outfield players move to press the carrier.
        if self.owner is not None and p['team'] != self.players[self.owner]['team'] and slot != 0:
            carrier = self.players[self.owner]
            opponents = [i for i in range(team*11+1, team*11+11) if self.players[i]['active']]
            near = sorted(opponents, key=lambda i: norm(self.players[i]['x']-carrier['x'],
                                                       self.players[i]['y']-carrier['y']))[:2]
            if index in near:
                rank = near.index(index)
                target = [carrier['x']+direction*(1.0+rank*1.4),
                          clamp(carrier['y']+(1 if rank else -1)*1.2, 1, 67)]
        return target

    def move_players(self):
        targets = [self.target_position(i) for i in range(22)]
        for i, p in enumerate(self.players):
            if not p['active']:
                continue
            tx, ty = targets[i]
            dx, dy = tx-p['x'], ty-p['y']
            dist = norm(dx, dy)
            cap = 6.5 if i == self.owner else 7.2
            desired_speed = min(cap, dist*1.4)
            desired_vx = dx/max(dist, 1e-9)*desired_speed
            desired_vy = dy/max(dist, 1e-9)*desired_speed
            # Brake before touchlines rather than abruptly clamping a moving player.
            # 2 m/s² per axis leaves headroom under the vector acceleration bound.
            desired_vx = clamp(desired_vx, -math.sqrt(4*max(0, p['x']-1.5)),
                               math.sqrt(4*max(0, 103.5-p['x'])))
            desired_vy = clamp(desired_vy, -math.sqrt(4*max(0, p['y']-1.5)),
                               math.sqrt(4*max(0, 66.5-p['y'])))
            speed_now = norm(p['vx'], p['vy'])
            stop_x = abs(p['vx'])*speed_now/7 + abs(p['vx'])*DT + .8
            stop_y = abs(p['vy'])*speed_now/7 + abs(p['vy'])*DT + .8
            if ((p['vx'] < 0 and p['x']-.5 < stop_x) or
                (p['vx'] > 0 and 104.5-p['x'] < stop_x) or
                (p['vy'] < 0 and p['y']-.5 < stop_y) or
                (p['vy'] > 0 and 67.5-p['y'] < stop_y)):
                desired_vx = desired_vy = 0.
            ax, ay = desired_vx-p['vx'], desired_vy-p['vy']
            delta = norm(ax, ay)
            factor = min(1., 3.5*DT/max(delta, 1e-9))
            vx, vy = p['vx']+ax*factor, p['vy']+ay*factor
            x, y = clamp(p['x']+vx*DT, .5, 104.5), clamp(p['y']+vy*DT, .5, 67.5)
            p['vx'], p['vy'] = (x-p['x'])/DT, (y-p['y'])/DT
            p['distance'] += norm(x-p['x'], y-p['y'])
            self.distances[p['id']] += norm(x-p['x'], y-p['y'])
            p['x'], p['y'] = x, y

    def foot(self, owner):
        p = self.players[owner]
        return [p['x']+.35*self.direction(p['team']), p['y'], 0.]

    def start_carry(self):
        p = self.players[self.owner]
        d = self.direction(p['team'])
        duration = self.rng.uniform(1.6, 4.8)
        advance = self.rng.uniform(3, 12)
        self.new_action('carry', duration, actor=self.owner, team=p['team'],
                        target=[clamp(p['x']+d*advance, 3, 102),
                                clamp(p['y']+self.rng.uniform(-7, 7), 3, 65)])

    def choose_action(self):
        p = self.players[self.owner]
        team = p['team']
        progress = self.progress(p['x'], team)
        shot_chance = .06
        if self.ratings:
            shot_chance = clamp(.06*self.rating(team, 'attack') /
                                self.rating(1-team, 'defense'), .02, .14)
        if p['number'] != 1 and progress > 78 and abs(p['y']-34) < 24 and self.rng.random() < shot_chance:
            self.start_shot()
            return
        if p['number'] != 1 and self.rng.random() < .36:
            self.start_carry()
            return
        candidates = [i for i in range(team*11+1, team*11+11) if i != self.owner and self.players[i]['active']]
        weights = []
        for i in candidates:
            q = self.players[i]
            distance = norm(q['x']-p['x'], q['y']-p['y'])
            advance = self.progress(q['x'], team)-progress
            weights.append(math.exp(-abs(distance-17)/18+clamp(advance/23, -1, 1.3)))
        intended = self.rng.choices(candidates, weights)[0]
        q = self.players[intended]
        defender = min((i for i in range((1-team)*11+1, (1-team)*11+11) if self.players[i]['active']),
                       key=lambda i: norm(self.players[i]['x']-q['x'], self.players[i]['y']-q['y']))
        near = norm(self.players[defender]['x']-q['x'], self.players[defender]['y']-q['y'])
        pressure_threshold = .32
        if self.ratings:
            pressure_threshold = clamp(.32*self.rating(1-team, 'pressing') /
                                       self.rating(team, 'passing'), .16, .50)
        success = not (near < 8 and self.rng.random() < pressure_threshold)
        receiver = intended if success else defender
        r = self.players[receiver]
        target = [clamp(r['x']+r['vx']*.25, 1, 104), clamp(r['y']+r['vy']*.25, 1, 67)]
        distance = norm(target[0]-self.ball[0], target[1]-self.ball[1])
        duration = max(.6, distance/self.rng.uniform(11, 18))
        under_pressure = any(norm(o['x']-p['x'], o['y']-p['y']) < 3
                             for o in self.players if o['team'] != team and o['active'])
        self.new_action('pass', duration, actor=self.owner, team=team,
                        intended=intended, receiver=receiver, target=target,
                        success=success, under_pressure=under_pressure,
                        height=1.8 if distance > 25 and self.rng.random() < .3 else 0.)
        self.owner = None

    def start_shot(self):
        p = self.players[self.owner]
        team = p['team']
        goal_x = 105. if self.direction(team) == 1 else 0.
        if self.ratings:
            goal_p = clamp(.12*self.rating(team, 'attack') /
                           self.rating(1-team, 'defense'), .05, .22)
            probabilities = [goal_p, .43, 1-goal_p-.43]
        else:
            probabilities = [.12, .43, .45]
        outcome = self.rng.choices(['goal', 'saved', 'off_target'], probabilities)[0]
        y = 34+self.rng.uniform(-2.8, 2.8) if outcome != 'off_target' else 34+self.rng.choice([-1, 1])*self.rng.uniform(4.2, 8)
        # Saved shots terminate at the keeper's current location (in front of goal).
        receiver = (1-team)*11
        keeper = self.players[receiver]
        target = [keeper['x'], keeper['y'], .6] if outcome == 'saved' else [goal_x, y, self.rng.uniform(.2, 1.7)]
        distance = norm(target[0]-self.ball[0], target[1]-self.ball[1])
        self.new_action('shot', max(.4, distance/self.rng.uniform(18, 28)),
                        actor=self.owner, team=team, receiver=receiver,
                        target=target, outcome=outcome)
        self.owner = None

    def update_ball(self):
        a = self.action
        u = clamp((self.t-a['start'])/(a['end']-a['start']), 0, 1)
        if a['kind'] == 'carry':
            self.ball = self.foot(self.owner)
        elif a['kind'] == 'pass':
            target = self.foot(a['receiver'])
            self.ball = [(1-u)*a['start_ball'][j]+u*target[j] for j in range(3)]
            self.ball[2] = a['height']*4*u*(1-u)
        elif a['kind'] == 'shot':
            target = a['target']
            if a['outcome'] == 'saved':
                q = self.players[a['receiver']]
                target = [q['x'], q['y'], .6]
            self.ball = [(1-u)*a['start_ball'][j]+u*target[j] for j in range(3)]
            self.ball[2] += .8*4*u*(1-u)
        elif a['kind'] == 'restart_setup':
            # Ball is moved only while play is stopped, with a smooth trajectory.
            b = min(1., u*1.8)
            b = b*b*(3-2*b)
            target = self.foot(a['receiver']) if u > .9 else a['target']+[0.]
            self.ball = [(1-b)*a['start_ball'][j]+b*target[j] for j in range(3)]

    def finish_action(self):
        a = self.action
        common = {'action_id': a['id'], 'team_id': self.team_id(a['team']),
                  'start_location': [round(v, 3) for v in a['start_ball'][:2]],
                  'end_location': [round(v, 3) for v in self.ball[:2]],
                  'duration_ms': int(round((self.t-a['start'])*1000))}
        if a['kind'] == 'restart_setup':
            self.owner = a['receiver']
            self.set_possession(a['team'], a['reason'])
            self.emit('restart', start=a['start'], player_id=self.players[self.owner]['id'],
                      restart_type=a['reason'], **common)
            self.start_carry()
        elif a['kind'] == 'carry':
            self.emit('carry', start=a['start'], player_id=self.players[a['actor']]['id'], **common)
            self.choose_action()
        elif a['kind'] == 'pass':
            self.stats[a['team']]['passes'] += 1
            self.stats[a['team']]['completed_passes'] += int(a['success'])
            eid = self.emit('pass', start=a['start'], player_id=self.players[a['actor']]['id'],
                            recipient_id=self.players[a['intended']]['id'],
                            outcome='complete' if a['success'] else 'intercepted',
                            under_pressure=a['under_pressure'],
                            pass_height='high' if a['height'] else 'ground', **common)
            self.owner = a['receiver']
            if not a['success']:
                other = self.players[self.owner]['team']
                self.stats[other]['interceptions'] += 1
                self.emit('interception', team_id=self.team_id(other),
                          player_id=self.players[self.owner]['id'],
                          location=[round(v, 3) for v in self.ball[:2]], related_event_ids=[eid])
                self.set_possession(other, 'interception', eid)
            self.start_carry()
        elif a['kind'] == 'shot':
            team = a['team']
            self.stats[team]['shots'] += 1
            self.stats[team]['shots_on_target'] += int(a['outcome'] != 'off_target')
            if a['outcome'] == 'goal':
                self.score[team] += 1
            eid = self.emit('shot', start=a['start'], player_id=self.players[a['actor']]['id'],
                            outcome=a['outcome'], score=self.score[:], **common)
            if a['outcome'] == 'goal':
                self.emit('stoppage', reason='goal', related_event_ids=[eid])
                self.start_restart(1-team, (52.5, 34), 'kick_off', minimum=20)
            elif a['outcome'] == 'saved':
                self.emit('save', team_id=self.team_id(1-team),
                          player_id=self.players[a['receiver']]['id'], related_event_ids=[eid])
                q = self.players[a['receiver']]
                self.start_restart(1-team, (q['x'], q['y']), 'keeper_restart', minimum=4)
            else:
                self.emit('stoppage', reason='ball_out', related_event_ids=[eid])
                self.start_restart(1-team, (5.5 if self.direction(1-team) == 1 else 99.5, 34),
                                   'goal_kick', minimum=8)

    def apply_administrative_events(self):
        """Apply decisions between completed actions, before the next frame."""
        while self.administrative_events and self.administrative_events[0][0] <= self.t:
            _, kind, slot, incoming_number = self.administrative_events.pop(0)
            player = self.players[slot]
            if not player['active']:
                continue
            common = {'team_id': self.team_id(player['team']), 'player_id': player['id']}
            if kind == 'yellow_card':
                self.emit(kind, reason='reckless_challenge', **common)
                continue
            # Cancel the newly chosen action before it can start, then restart
            # with a legal active taker. The ball remains on a smooth dead-ball path.
            self.emit('action_cancelled', action_id=self.action['id'], reason=kind)
            restart_team = self.possession_team if self.possession_team is not None else 0
            if kind == 'substitution':
                incoming = f'{"H" if player["team"] == 0 else "A"}{incoming_number:02}'
                self.emit(kind, player_out_id=player['id'], player_in_id=incoming,
                          reason='tactical', **common)
                incoming_player = self.player_by_id[incoming]
                player.update(id=incoming, number=incoming_number, distance=0.,
                              name=incoming_player.get('name'),
                              shirt_number=incoming_player.get('shirt_number', incoming_number),
                              position=incoming_player.get('position', player['position']),
                              position_detail=incoming_player.get('position_detail'),
                              nationality=incoming_player.get('nationality'),
                              real_player_id=incoming_player.get('real_player_id'))
            else:
                self.emit(kind, reason='serious_foul_play', dismissal='straight_red', **common)
                player['active'] = False
                restart_team = 1-player['team']
            self.start_restart(restart_team, tuple(self.ball[:2]), kind, minimum=8)

    def step(self, frame):
        self.t = frame/HZ
        if frame == HALF*HZ:
            self.emit('action_cancelled', action_id=self.action['id'], reason='half_time')
            self.emit('period_end', period=1)
            self.period = 2
            self.emit('period_start', period=2)
            self.possession_team = None
            # Players walk into the switched-end formation; coordinates never teleport.
            self.start_restart(1, (52.5, 34), 'kick_off', minimum=32)
        self.move_players()
        self.update_ball()
        self.ball_v = [(self.ball[j]-self.last_ball[j])/DT for j in range(3)]
        self.last_ball = self.ball[:]
        if self.action['kind'] != 'restart_setup' and self.possession_team is not None:
            self.stats[self.possession_team]['possession_seconds'] += DT
        if self.owner is not None and self.action['kind'] == 'carry':
            carrier = self.players[self.owner]
            for q in self.players:
                if q['active'] and q['team'] != carrier['team'] and norm(q['x']-carrier['x'], q['y']-carrier['y']) < 3:
                    pair = (q['id'], carrier['id'])
                    if self.t-self.pressure_last.get(pair, -100) > 6:
                        self.pressure_last[pair] = self.t
                        self.stats[q['team']]['pressure_events'] += 1
                        self.emit('pressure', team_id=self.team_id(q['team']), player_id=q['id'],
                                  target_player_id=carrier['id'], radius_m=3,
                                  location=[round(q['x'], 3), round(q['y'], 3)],
                                  method='distance_threshold', note='Instantaneous proximity tag; not tactical ground truth')
        if self.t >= self.action['end']-1e-6:
            self.finish_action()
            self.apply_administrative_events()
        if frame == DURATION*HZ:
            self.emit('action_cancelled', action_id=self.action['id'], reason='full_time')
            self.emit('period_end', period=2)
            self.emit('match_end', score=self.score[:])

    def tracking(self, frame):
        a = self.action
        return {'frame_id': frame, 't_ms': frame*200, 'period': self.period,
                'period_ms': frame*200-(self.period-1)*HALF*1000,
                'in_play': a['kind'] != 'restart_setup' and frame < DURATION*HZ,
                'phase': a['kind'] if frame < DURATION*HZ else 'ended',
                'action_id': a['id'], 'possession_team_id': self.team_id(self.possession_team),
                'ball_owner_id': None if self.owner is None else self.players[self.owner]['id'],
                'score': self.score[:],
                'players': [[p['id'], round(p['x'], 3), round(p['y'], 3), round(p['vx'], 3), round(p['vy'], 3)] for p in self.players if p['active']],
                'ball': [round(v, 3) for v in self.ball+self.ball_v]}

    def snapshot(self, frame):
        total = sum(s['possession_seconds'] for s in self.stats)
        stats = {self.team_id(i): {**s,
                 'possession_seconds': round(s['possession_seconds'], 1),
                 'possession_pct': round(s['possession_seconds']/total*100, 2) if total else None,
                 'pass_accuracy_pct': round(s['completed_passes']/s['passes']*100, 2) if s['passes'] else None}
                 for i, s in enumerate(self.stats)}
        return {'t_ms': frame*200, 'period': self.period, 'score': self.score[:],
                'in_play': self.action['kind'] != 'restart_setup' and frame < DURATION*HZ,
                'last_event_sequence': self.event_seq, 'stats': stats}


def build_metadata(sim):
    """Build per-match metadata; the two-team format is kept for compatibility."""
    if sim.legacy:
        teams = [{'team_id': 'HOME', 'name': 'Harbor FC', 'formation': '4-3-3'},
                 {'team_id': 'AWAY', 'name': 'Mountain FC', 'formation': '4-3-3'}]
    else:
        teams = []
        for team, club in enumerate((sim.home, sim.away)):
            entry = {'team_id': ['HOME', 'AWAY'][team], **public_club(club)}
            teams.append(entry)
    players = [{'player_id': p['id'], 'team_id': sim.team_id(p['team']),
                'jersey_number': p.get('shirt_number', p['number']),
                'name': p.get('name') or sim.player_name(p['id']),
                'position': p['position'],
                'position_detail': p.get('position_detail', p['position']),
                'nationality': p.get('nationality'),
                'starter': p['starter']}
               for p in sim.player_by_id.values()]
    metadata = {
        'schema_version': '1.0.0',
        'match_id': sim.match_id,
        'synthetic': True,
        'seed': sim.seed,
        'generator': f'generate.py v{VERSION}',
        'duration_ms': DURATION*1000,
        'tracking_hz': HZ,
        'pitch': {'length_m': 105, 'width_m': 68, 'origin': 'top_left',
                  'x_axis': 'right', 'y_axis': 'down', 'z_axis': 'up'},
        'periods': [{'period': 1, 'start_ms': 0, 'duration_ms': 3000000,
                     'regulation_ms': 2700000, 'home_attacks': 'positive_x'},
                    {'period': 2, 'start_ms': 3000000, 'duration_ms': 3000000,
                     'regulation_ms': 2700000, 'home_attacks': 'negative_x'}],
        'clock_note': 'Two 45+5 minute periods; halftime break omitted. Use period and period_ms for displayed clock.',
        'teams': teams,
        'players': players,
        'columns': {'tracking.players': ['player_id', 'x_m', 'y_m', 'vx_mps', 'vy_mps'],
                    'tracking.ball': ['x_m', 'y_m', 'z_m', 'vx_mps', 'vy_mps', 'vz_mps']},
        'limitations': ['Simplified procedural motion, not a professional football or physics simulator.',
                        'Club names, squad numbers and player names follow the public 2026/27 Premier League squad lists; match events and tracking are synthetic.',
                        'No offsides, physical collisions or realistic fatigue. Cards and tactical substitutions follow a synthetic schedule.',
                        'Incoming substitutes inherit the outgoing formation slot and position; no touchline entrance animation is modeled.',
                        'Pressure is a proximity heuristic; ball transport during restart_setup is non-playing animation.',
                        'Outcomes exist only in resolved events at available_at_ms; do not expose future events during replay.'],
    }
    if not sim.legacy:
        metadata['competition'] = sim.competition
        metadata['home_club'] = public_club(sim.home)
        metadata['away_club'] = public_club(sim.away)
        metadata['venue'] = sim.home['stadium']
    return metadata


def build_summary(sim):
    goals = []
    for event in sim.events:
        if event['type'] == 'shot' and event['outcome'] == 'goal':
            team = 0 if event['team_id'] == 'HOME' else 1
            club = sim.club(team)
            goals.append({'t_ms': event['end_ms'], 'period': event['period'],
                          'period_ms': event['period_ms'], 'team_id': event['team_id'],
                          'club_id': club['club_id'], 'club_name': club['name'],
                          'player_id': event['player_id'],
                          'player_name': sim.player_name(event['player_id']),
                          'shirt_number': sim.player_by_id[event['player_id']].get('shirt_number', 0),
                          'score': event['score']})
    summary = {
        'match_id': sim.match_id,
        'seed': sim.seed,
        'duration_seconds': DURATION,
        'tracking_hz': HZ,
        'tracking_frames': DURATION*HZ+1,
        'player_position_records': sim.position_records,
        'state_snapshots': DURATION+1,
        'event_count': len(sim.events),
        'event_counts': dict(Counter(e['type'] for e in sim.events)),
        'final_score': sim.score,
        'final_stats': sim.snapshot(DURATION*HZ)['stats'],
        'distance_m': {pid: round(distance, 1) for pid, distance in sim.distances.items()},
        'goals': goals,
    }
    if not sim.legacy:
        summary.update({'competition': sim.competition,
                        'home_club': public_club(sim.home),
                        'away_club': public_club(sim.away)})
    return summary


def write_preview(out, summary, frames, events, metadata):
    goals = summary['goals']
    center = goals[-1]['t_ms'] if goals else 120000
    start = max(0, center-45000)
    end = min(DURATION*1000, start+60000)
    preview = {'match_id': summary['match_id'], 'start_ms': start, 'end_ms': end,
               'tracking_hz': HZ, 'metadata': metadata,
               'frames': [frame for frame in frames if start <= frame['t_ms'] <= end],
               'events': [event for event in events if start <= event['available_at_ms'] <= end]}
    (out/'preview-60s.json').write_text(
        json.dumps(preview, ensure_ascii=False, separators=(',', ':'))+'\n')


def write_match(sim, out, metadata, tracking=False, preview=True):
    """Write one match directory and return its summary."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if not tracking and (out/'tracking.jsonl').exists():
        (out/'tracking.jsonl').unlink()
    (out/'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+'\n')
    frames = [] if preview else None
    tracking_handle = (out/'tracking.jsonl').open('w') if tracking else None
    try:
        with (out/'states.jsonl').open('w') as states:
            first = sim.tracking(0)
            sim.position_records += len(first['players'])
            if tracking_handle:
                tracking_handle.write(encode(first)+'\n')
            states.write(encode(sim.snapshot(0))+'\n')
            if frames is not None:
                frames.append(first)
            for frame in range(1, DURATION*HZ+1):
                sim.step(frame)
                data = sim.tracking(frame)
                sim.position_records += len(data['players'])
                if tracking_handle:
                    tracking_handle.write(encode(data)+'\n')
                if frame % HZ == 0:
                    states.write(encode(sim.snapshot(frame))+'\n')
                if frames is not None:
                    frames.append(data)
    finally:
        if tracking_handle:
            tracking_handle.close()
    with (out/'events.jsonl').open('w') as stream:
        for event in sim.events:
            stream.write(encode(event)+'\n')
    summary = build_summary(sim)
    (out/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    if preview:
        write_preview(out, summary, frames, sim.events, metadata)
    write_report(out, metadata, summary, sim.events)
    return summary


def generate_single(out, seed):
    """Legacy two-team mode used by the bundled single-match front-end."""
    out = Path(out)
    sim = Match(seed, DEFAULT_MATCH)
    metadata = build_metadata(sim)
    summary = write_match(sim, out, metadata, tracking=True, preview=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def round_robin_weeks(weeks):
    """Return ``weeks`` rounds of ten pairings, balanced for home/away."""
    ids = [club['club_id'] for club in CLUBS]
    rotation = ids[:]
    pairings = []
    for _ in range(len(ids)-1):
        pairings.append([(rotation[i], rotation[-1-i]) for i in range(len(ids)//2)])
        rotation = [rotation[0]] + [rotation[-1]] + rotation[1:-1]
    selected = pairings[:weeks]

    # Greedy home/away assignment, then local swaps until the venue split is as
    # even as possible.  For four matchweeks every club ends with 2 home / 2 away.
    home_count = defaultdict(int)
    away_count = defaultdict(int)
    schedule = []
    for pairs in selected:
        week = []
        for pair_index, (a, b) in enumerate(pairs):
            if home_count[a] < home_count[b] or (home_count[a] == home_count[b] and pair_index % 2 == 0):
                home, away = a, b
            else:
                home, away = b, a
            home_count[home] += 1
            away_count[away] += 1
            week.append([home, away])
        schedule.append(week)

    def imbalance():
        home = defaultdict(int)
        away = defaultdict(int)
        for week in schedule:
            for home_id, away_id in week:
                home[home_id] += 1
                away[away_id] += 1
        return sum(abs(home[club] - away[club]) for club in ids)

    improved = True
    while improved:
        improved = False
        current = imbalance()
        for week in schedule:
            for fixture in week:
                fixture[0], fixture[1] = fixture[1], fixture[0]
                value = imbalance()
                if value < current:
                    current = value
                    improved = True
                else:
                    fixture[0], fixture[1] = fixture[1], fixture[0]
    return schedule


def build_week_plans(weeks):
    plans = []
    for week_index, pairs in enumerate(round_robin_weeks(weeks), 1):
        matches = []
        for match_index, (home_id, away_id) in enumerate(pairs):
            home = CLUB_BY_ID[home_id]
            away = CLUB_BY_ID[away_id]
            day_offset, kickoff = SLOTS[match_index]
            match_date = BASE_DATE + dt.timedelta(weeks=week_index-1, days=day_offset)
            matches.append({
                'match_id': f'PL26-W{week_index:02d}-{home_id}-{away_id}',
                'matchweek': week_index,
                'home_club_id': home_id,
                'away_club_id': away_id,
                'home_name': home['name'],
                'away_name': away['name'],
                'kickoff_local': f'{match_date.isoformat()}T{kickoff}:00',
                'time_zone': 'Europe/London',
                'venue': home['stadium'],
                'featured': False,
                'status': 'scheduled',
            })
        # The marquee fixture of each week gets the optional full replay file.
        featured = max(matches, key=lambda m: sum(
            RATINGS[m[side]]['attack'] + RATINGS[m[side]]['defense']
            for side in ('home_club_id', 'away_club_id')))
        featured['featured'] = True
        plans.append({'week': week_index, 'label': f'Matchweek {week_index}',
                      'date': (BASE_DATE + dt.timedelta(weeks=week_index-1)).isoformat(),
                      'matches': matches})
    return plans


def build_standings(fixtures):
    rows = {}
    for club in CLUBS:
        rows[club['club_id']] = {
            'club_id': club['club_id'], 'name': club['name'],
            'short_name': club['short_name'], 'played': 0, 'won': 0, 'drawn': 0, 'lost': 0,
            'goals_for': 0, 'goals_against': 0, 'goal_difference': 0, 'points': 0,
            'form': [],
        }
    for fixture in fixtures:
        home_id, away_id = fixture['home_club_id'], fixture['away_club_id']
        home, away = rows[home_id], rows[away_id]
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


def build_scorers(fixtures, limit=None):
    scorers = {}
    for fixture in fixtures:
        for goal in fixture['goals']:
            # Player IDs are match-local (H01/A01).  Aggregate a club's shirt
            # number across matches so a player is not split by home/away status.
            key = (goal['club_id'], goal['player_name'])
            row = scorers.setdefault(key, {
                'club_id': goal['club_id'], 'club_name': goal['club_name'],
                'player_id': goal['player_id'], 'player_name': goal['player_name'],
                'shirt_number': goal.get('shirt_number', 0),
                'goals': 0, 'matches': set(),
            })
            row['goals'] += 1
            row['matches'].add(fixture['match_id'])
    table = sorted(scorers.values(), key=lambda row: (-row['goals'], row['club_name'],
                                                      row['player_name']))
    selected = table if limit is None else table[:limit]
    result = []
    for rank, row in enumerate(selected, 1):
        row['matches'] = len(row['matches'])
        row['rank'] = rank
        result.append(row)
    return result


def build_totals(fixtures):
    goals = sum(sum(fixture['score']) for fixture in fixtures)
    home_wins = sum(1 for fixture in fixtures if fixture['score'][0] > fixture['score'][1])
    away_wins = sum(1 for fixture in fixtures if fixture['score'][1] > fixture['score'][0])
    draws = len(fixtures)-home_wins-away_wins
    clean_sheets = sum(1 for fixture in fixtures if 0 in fixture['score'])
    if fixtures:
        biggest = max(fixtures, key=lambda fixture: (abs(fixture['score'][0]-fixture['score'][1]),
                                                      sum(fixture['score'])))
        biggest_win = {'match_id': biggest['match_id'], 'score': biggest['score'],
                       'home_name': biggest['home_name'],
                       'away_name': biggest['away_name'],
                       'margin': abs(biggest['score'][0]-biggest['score'][1])}
    else:
        biggest_win = None
    return {'matches': len(fixtures), 'goals': goals,
            'average_goals_per_match': round(goals/len(fixtures), 2) if fixtures else None,
            'home_wins': home_wins, 'draws': draws, 'away_wins': away_wins,
            'clean_sheets': clean_sheets, 'biggest_win': biggest_win}


def generate_league(out, seed, weeks=4, tracking_mode='featured', full_matches=()):
    if not 1 <= weeks <= len(CLUBS)-1:
        raise ValueError(f'weeks must be between 1 and {len(CLUBS)-1}')
    if tracking_mode not in ('featured', 'all', 'none'):
        raise ValueError("tracking_mode must be 'featured', 'all' or 'none'")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    squads = load_squads()
    squads_source = ROOT/'data'/'league'/'squads.json'
    if squads and squads_source.exists():
        (out/'squads.json').write_text(squads_source.read_text())
    plans = build_week_plans(weeks)
    fixtures = []
    for plan in plans:
        for index, fixture in enumerate(plan['matches']):
            home = CLUB_BY_ID[fixture['home_club_id']]
            away = CLUB_BY_ID[fixture['away_club_id']]
            ratings = [RATINGS[home['club_id']], RATINGS[away['club_id']]]
            competition = {**COMPETITION, 'matchweek': fixture['matchweek'],
                           'kickoff_local': fixture['kickoff_local'],
                           'time_zone': fixture['time_zone'], 'venue': fixture['venue']}
            lineups = [select_lineup(home['club_id'], squads),
                       select_lineup(away['club_id'], squads)] if squads else None
            sim = Match(seed + plan['week']*1000 + index, fixture['match_id'],
                        home, away, ratings, competition, lineups=lineups)
            metadata = build_metadata(sim)
            match_dir = out/'matches'/fixture['match_id']
            tracking = tracking_mode == 'all' or (tracking_mode == 'featured' and fixture['featured']) or fixture['match_id'] in full_matches
            summary = write_match(sim, match_dir, metadata, tracking=tracking, preview=True)
            base = f'matches/{fixture["match_id"]}'
            fixture.update({
                'status': 'finished',
                'score': summary['final_score'],
                'goals': summary['goals'],
                'stats': summary['final_stats'],
                'tracking': 'full' if tracking else 'preview',
                'paths': {
                    'match': base+'/',
                    'metadata': base+'/metadata.json',
                    'summary': base+'/summary.json',
                    'analysis': base+'/analysis.json',
                    'events': base+'/events.jsonl',
                    'states': base+'/states.jsonl',
                    'preview': base+'/preview-60s.json',
                    'tracking': base+'/tracking.jsonl' if tracking else None,
                },
            })
            fixtures.append(fixture)
            print(f'Generated {len(fixtures)}/{weeks*10}: {fixture["match_id"]}', flush=True)
    standings = build_standings(fixtures)
    scorers = build_scorers(fixtures)
    totals = build_totals(fixtures)
    league = {
        'schema_version': '2.0.0',
        'competition': {**COMPETITION, 'generator': f'generate.py v{VERSION}',
                        'seed': seed, 'weeks': weeks},
        'format': {'team_count': len(CLUBS), 'matchweeks': weeks,
                   'matches_per_week': len(CLUBS)//2, 'total_matches': len(fixtures),
                   'points': {'win': 3, 'draw': 1, 'loss': 0},
                   'schedule': 'single round-robin rotation, first N rounds',
                   'tracking_mode': tracking_mode,
                   'matches_with_full_tracking': sum(1 for f in fixtures if f['tracking'] == 'full'),
                   'matches_with_preview': len(fixtures)},
        'teams': [{**public_club(club), 'rating': RATINGS[club['club_id']]} for club in CLUBS],
        'weeks': plans,
        'standings': standings,
        'top_scorers': scorers,
        'featured_matches': [fixture['match_id'] for fixture in fixtures if fixture['featured']],
        'totals': totals,
        'paths': {'matches': 'matches/', 'fixtures': 'fixtures.jsonl',
                  'standings': 'standings.json', 'squads': 'squads.json'},
    }
    (out/'league.json').write_text(json.dumps(league, ensure_ascii=False, indent=2)+'\n')
    with (out/'fixtures.jsonl').open('w') as stream:
        for fixture in fixtures:
            stream.write(encode(fixture)+'\n')
    (out/'standings.json').write_text(
        json.dumps({'competition': league['competition'], 'table': standings},
                   ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({
        'mode': 'league', 'output': str(out), 'competition': COMPETITION['name'],
        'teams': len(CLUBS), 'weeks': weeks, 'matches': len(fixtures),
        'goals': totals['goals'], 'tracking_mode': tracking_mode,
        'full_tracking_matches': league['format']['matches_with_full_tracking'],
        'featured_matches': league['featured_matches'],
        'standings_leader': standings[0]['name'] if standings else None,
        'top_scorer': scorers[0] if scorers else None,
    }, ensure_ascii=False, indent=2))
    return league


def main():
    parser = argparse.ArgumentParser(description='Synthetic football replay / league generator')
    parser.add_argument('--mode', choices=['league', 'single'], default='league',
                        help='league (default) or legacy single match')
    parser.add_argument('--output', type=Path, default=None,
                        help='output directory (default: data/league or data for single)')
    parser.add_argument('--seed', type=int, default=20261019)
    parser.add_argument('--weeks', type=int, default=4,
                        help='league mode: number of matchweeks (1-19)')
    parser.add_argument('--tracking', choices=['featured', 'all', 'none'], default='featured',
                        help='full tracking: featured marquee matches, all matches, or none')
    parser.add_argument('--full-match', action='append', default=[],
                        help='add full tracking for a specific match ID (repeatable)')
    args = parser.parse_args()
    if args.mode == 'single':
        output = args.output or ROOT/'data'
        generate_single(output, args.seed)
    else:
        if not 1 <= args.weeks <= len(CLUBS)-1:
            parser.error(f'--weeks must be between 1 and {len(CLUBS)-1}')
        output = args.output or ROOT/'data'/'league'
        generate_league(output, args.seed, args.weeks, args.tracking, args.full_match)


if __name__ == '__main__':
    main()
