// The dashboard's simulation clock is independent of the computer's date.
export const CURRENT_MATCHWEEK = 3;
export const HOME_CLUB_KEY = 'touchline.homeClub';

export function clubDashboard(league, clubId, week = CURRENT_MATCHWEEK) {
  const club = league.teams.find(team => team.club_id === clubId);
  if (!club) return null;
  const fixtures = league.weeks.flatMap(item => item.matches)
    .sort((a, b) => a.matchweek - b.matchweek || a.kickoff_local.localeCompare(b.kickoff_local));
  const completed = fixtures.filter(match => match.matchweek < week && match.status === 'finished');
  const table = league.teams.map(team => ({ club_id: team.club_id, name: team.name,
    played: 0, won: 0, drawn: 0, lost: 0, goalsFor: 0, goalsAgainst: 0, points: 0 }));
  const rows = new Map(table.map(row => [row.club_id, row]));
  const points = league.format.points;
  for (const match of completed) {
    for (const [side, id] of [match.home_club_id, match.away_club_id].entries()) {
      const row = rows.get(id), scored = match.score[side], conceded = match.score[1 - side];
      row.played++; row.goalsFor += scored; row.goalsAgainst += conceded;
      if (scored > conceded) { row.won++; row.points += points.win; }
      else if (scored === conceded) { row.drawn++; row.points += points.draw; }
      else { row.lost++; row.points += points.loss; }
    }
  }
  table.sort((a, b) => b.points - a.points || (b.goalsFor - b.goalsAgainst) - (a.goalsFor - a.goalsAgainst)
    || b.goalsFor - a.goalsFor || a.name.localeCompare(b.name));
  const involves = match => [match.home_club_id, match.away_club_id].includes(clubId);
  const history = completed.filter(involves).map(match => {
    const home = match.home_club_id === clubId;
    const scored = match.score[home ? 0 : 1], conceded = match.score[home ? 1 : 0];
    return { ...match, result: scored > conceded ? 'W' : scored < conceded ? 'L' : 'D' };
  });
  // Explicit projection prevents future scores/goals from leaking into the homepage.
  const upcoming = fixtures.filter(match => match.matchweek >= week && involves(match)).map(match => ({
    match_id: match.match_id, matchweek: match.matchweek, kickoff_local: match.kickoff_local,
    venue: match.venue, home_club_id: match.home_club_id, away_club_id: match.away_club_id,
    home_name: match.home_name, away_name: match.away_name,
  }));
  return { club, history, upcoming, record: rows.get(clubId), rank: table.findIndex(row => row.club_id === clubId) + 1 };
}
