import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { CURRENT_MATCHWEEK, clubDashboard } from '../club-home.js';
const league = JSON.parse(readFileSync(new URL('../data/league/league.json', import.meta.url)));

test('Week three counts only two completed rounds for every club', () => {
  assert.equal(CURRENT_MATCHWEEK, 3);
  for (const club of league.teams) {
    const dashboard = clubDashboard(league, club.club_id);
    assert.equal(dashboard.record.played, 2);
    assert.equal(dashboard.history.length, 2);
    assert.deepEqual(dashboard.upcoming.map(match => match.matchweek), [3, 4]);
    assert.ok(dashboard.history.every(match => match.matchweek < 3));
    assert.equal(dashboard.record.points, dashboard.record.won * 3 + dashboard.record.drawn);
    assert.ok(dashboard.rank >= 1 && dashboard.rank <= 20);
    for (const match of dashboard.upcoming) {
      assert.equal(match.score, undefined);
      assert.equal(match.goals, undefined);
      assert.equal(match.status, undefined);
    }
  }
});

test('Home and away results are viewed from the selected club', () => {
  for (const club of league.teams) {
    const dashboard = clubDashboard(league, club.club_id);
    let goals = 0;
    for (const match of dashboard.history) {
      const side = match.home_club_id === club.club_id ? 0 : 1;
      goals += match.score[side];
      assert.equal(match.result, match.score[side] > match.score[1-side] ? 'W' : match.score[side] < match.score[1-side] ? 'L' : 'D');
    }
    assert.equal(dashboard.record.goalsFor, goals);
  }
});

test('Future scores cannot influence the current standings', () => {
  const altered = structuredClone(league);
  for (const week of altered.weeks.filter(week => week.week >= 3)) {
    week.matches.forEach(match => { match.score = [99, 0]; });
  }
  assert.deepEqual(clubDashboard(altered, 'ARS'), clubDashboard(league, 'ARS'));
});

test('Unknown clubs, season start and end have defined empty states', () => {
  assert.equal(clubDashboard(league, 'unknown'), null);
  assert.equal(clubDashboard(league, 'ARS', 1).history.length, 0);
  assert.equal(clubDashboard(league, 'ARS', 5).upcoming.length, 0);
});
