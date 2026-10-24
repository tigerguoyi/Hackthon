import { DATASETS, datasetFrom } from './datasets.js';
import { createDecisionView } from './decision-view.js?v=enriched-1.1-motion';
import { CURRENT_MATCHWEEK, HOME_CLUB_KEY, clubDashboard } from './club-home.js';
import { formatClock, sampleFrames, samplePacked, lastAtOrBefore, releasedEvents, rosterStatus } from './core.js?v=2.2.0';

const $ = selector => document.querySelector(selector);
const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const state = { metadata: null, summary: null, preview: null, packed: null, events: [], snapshots: [], time: 0, playing: false, speed: 1, selected: 'H10', homeClub: null, view: 'home', filter: 'key', eventScope: 'replay', frame: null, league: null, fixture: null, loadToken: 0, loading: false, squads: null, squadsPromise: null, squadClub: 'ARS', squadQuery: '', report: null };
const canvas = $('#pitch');
const ctx = canvas.getContext('2d');
let datasetId = datasetFrom(new URLSearchParams(location.search).get('dataset'));
let LEAGUE_ROOT = DATASETS[datasetId].root;
let leagueRevision = 0;
let analysisSection = 'report';
const decisionView = createDecisionView($('#decision-view'), time => { changeView('replay'); setPlaying(false); seek(time); });
function analysisTab(section) {
  analysisSection = section;
  $('#report-panel').hidden = section !== 'report';
  $('#decision-view').hidden = section !== 'decisions';
  for (const [id, active] of [['report-tab', section === 'report'], ['decisions-tab', section === 'decisions']]) {
    $('#'+id).classList.toggle('active', active); $('#'+id).setAttribute('aria-selected', String(active));
  }
  decisionView.show(section === 'decisions' && state.view === 'stats');
}
$('#report-tab').onclick = () => analysisTab('report');
$('#decisions-tab').onclick = () => analysisTab('decisions');
$('#dataset-select').value = datasetId;
$('#dataset-select').onchange = async e => {
  datasetId = datasetFrom(e.target.value); LEAGUE_ROOT = DATASETS[datasetId].root;
  const url = new URL(location.href); url.searchParams.set('dataset', datasetId); history.replaceState(null,'',url);
  state.loadToken++; decisionView.clear(); worker?.terminate(); state.playing = false;
  setPickerBusy(true);
  state.squads = state.squadsPromise = null;
  await init();
};
let worker = null, allFixtures = [], lastTick = 0, lastUI = 0, eventKey = '', statsKey = '', canvasWidth = 0, canvasHeight = 0;
const teamMeta = id => state.metadata?.teams.find(t => t.team_id === id);
const teamName = id => teamMeta(id)?.short_name || teamMeta(id)?.name || 'Match';
const numberOf = id => state.metadata?.players.find(p => p.player_id === id)?.jersey_number ?? '—';
const playerLabel = id => id ? `${teamName(state.metadata.players.find(p => p.player_id === id)?.team_id)} #${numberOf(id)}` : 'None';
const clock = (time, period) => formatClock(time, state.metadata, period);
const display = n => n == null ? '—' : Number.isInteger(n) ? n.toLocaleString('en-GB') : n.toFixed(1);
const phaseNames = { carry: 'Carry', pass: 'Passing', shot: 'Shooting', restart_setup: 'Dead ball · restart setup', ended: 'Full time' };
const positions = { GK: 'Goalkeeper', LB: 'Left-back', LCB: 'Centre-back', RCB: 'Centre-back', RB: 'Right-back', LCM: 'Central midfielder', DM: 'Defensive midfielder', RCM: 'Central midfielder', LW: 'Left winger', ST: 'Striker', RW: 'Right winger' };

async function fetchJson(url) {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) throw new Error(`${url} failed (${response.status})`);
  return response.json();
}

async function fetchJsonl(url) {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) throw new Error(`${url} failed (${response.status})`);
  return (await response.text()).split('\n').filter(line => line.trim()).map(line => JSON.parse(line));
}

function showError(message) {
  const box = $('#error');
  box.hidden = false;
  box.replaceChildren(document.createTextNode(message));
  const retry = document.createElement('button');
  retry.textContent = 'Reload';
  retry.onclick = () => location.reload();
  box.append(retry);
}

function clearError() {
  const box = $('#error');
  box.hidden = true;
  box.replaceChildren();
}

function formatKickoff(value) {
  return value ? value.replace('T', ' ').slice(0, 16) : 'Kick-off TBC';
}

function hexRgb(hex) {
  const value = String(hex || '').replace('#', '');
  if (!/^[0-9a-f]{6}$/i.test(value)) return [237, 116, 69];
  return [parseInt(value.slice(0, 2), 16), parseInt(value.slice(2, 4), 16), parseInt(value.slice(4, 6), 16)];
}

function isLight(hex) {
  const [r, g, b] = hexRgb(hex);
  return 0.299*r + 0.587*g + 0.114*b > 185;
}

function clubInk(club) {
  const primary = club?.colors?.primary || '#ed7445';
  const secondary = club?.colors?.secondary || '#4b5563';
  const ink = isLight(primary) ? secondary : primary;
  return isLight(ink) ? '#4b5563' : ink;
}

function applyClubCrest(element, club) {
  if (!element) return;
  const readable = clubInk(club);
  element.style.color = readable;
  element.style.background = `rgba(${hexRgb(readable).join(', ')}, .12)`;
}

function clubCrestHTML(club) {
  const readable = clubInk(club);
  return `<span class="squad-crest" style="color:${readable};background:rgba(${hexRgb(readable).join(', ')}, .12)"><svg viewBox="0 0 40 44" aria-hidden="true"><path d="M5 4h30v23L20 40 5 27Z"/><path d="M12 26V15h16v11M16 15v-4m8 4v-4M12 21h16M20 15v16"/></svg></span>`;
}

function setTimelineMode(full, preview = state.preview) {
  $('#tick-start').textContent = full ? 'Kick-off' : 'Clip start';
  $('#tick-mid').textContent = full ? 'Half-time' : 'Clip middle';
  $('#tick-end').textContent = full ? 'Full-time' : 'Clip end';
  $('#timeline-end-label').textContent = full ? formatClock(state.metadata.duration_ms, state.metadata) : formatClock(preview.end_ms, state.metadata);
}

function setPickerBusy(busy) {
  state.loading = busy;
  document.body.classList.toggle('is-loading', busy);
  for (const selector of ['#week-select', '#match-select', '#prev-match', '#next-match']) {
    $(selector).disabled = busy || !state.league;
  }
}

function buildWeekSelect() {
  $('#week-select').innerHTML = state.league.weeks.map(week =>
    `<option value="${week.week}">Matchweek ${week.week} · ${escape(week.date)}</option>`).join('');
}

function renderMatchOptions(weekNumber) {
  const week = state.league.weeks.find(item => item.week === Number(weekNumber));
  $('#match-select').innerHTML = (week?.matches || []).map(fixture =>
    `<option value="${escape(fixture.match_id)}">${fixture.featured ? '★ ' : ''}${escape(fixture.home_name)} ${fixture.score[0]}-${fixture.score[1]} ${escape(fixture.away_name)}${fixture.tracking === 'full' ? ' · Full replay' : ''}</option>`).join('');
}

function syncPicker(fixture) {
  $('#week-select').value = String(fixture.matchweek);
  renderMatchOptions(fixture.matchweek);
  $('#match-select').value = fixture.match_id;
}

function updateUrlForMatch(matchId) {
  const url = new URL(location.href);
  url.searchParams.set('match', matchId);
  url.searchParams.set('dataset', datasetId);
  url.searchParams.set('view', state.view);
  history.replaceState(null, '', url);
}

function buildPlayerSelect() {
  $('#player-select').innerHTML = state.metadata.teams.map(team =>
    `<optgroup label="${escape(team.name)}">${state.metadata.players.filter(player => player.team_id === team.team_id).map(player =>
      `<option value="${escape(player.player_id)}">${escape(team.name)} · ${player.jersey_number} · ${escape(positions[player.position])}</option>`).join('')}</optgroup>`).join('');
  $('#player-select').value = state.selected;
}

function updateMatchChrome(fixture, metadata) {
  const home = metadata.teams.find(team => team.team_id === 'HOME');
  const away = metadata.teams.find(team => team.team_id === 'AWAY');
  const week = metadata.competition?.matchweek ?? fixture.matchweek;
  const kickoff = formatKickoff(metadata.competition?.kickoff_local);
  const tracking = fixture.tracking === 'full' ? 'Full replay' : '60s preview';
  $('#home-name').textContent = home.short_name || home.name;
  $('#away-name').textContent = away.short_name || away.name;
  $('#home-en').textContent = home.name.toUpperCase();
  $('#away-en').textContent = away.name.toUpperCase();
  $('#match-id').textContent = metadata.match_id;
  $('#match-competition').textContent = metadata.competition?.name || 'Premier League (sim)';
  $('#match-week').textContent = `Matchweek ${week}`;
  $('#match-kickoff').textContent = kickoff;
  if (['overview', 'replay', 'stats', 'lineup'].includes(state.view)) $('#eyebrow-code').textContent = `/ W${String(week).padStart(2, '0')}`;
  $('#legend-home').textContent = home.short_name || home.name;
  $('#legend-away').textContent = away.short_name || away.name;
  $('#full-legend-home').textContent = home.short_name || home.name;
  $('#full-legend-away').textContent = away.short_name || away.name;
  $('#stats-home-label').textContent = home.short_name || home.name;
  $('#stats-away-label').textContent = away.short_name || away.name;
  $('#match-picker-meta').innerHTML = `${escape(kickoff)}<span class="meta-divider">/</span>${escape(metadata.venue || '')}<span class="meta-divider">/</span><span class="subtle-badge">${tracking}</span>`;
  $('#export-summary').href = `${LEAGUE_ROOT}${fixture.paths.summary}`;
  $('#export-summary').download = `${metadata.match_id}-summary.json`;
  $('#dataset-summary').textContent = `${state.league.teams.length} clubs · ${state.league.format.total_matches} matches · 5 Hz`;
  applyClubCrest($('.home-crest'), home);
  applyClubCrest($('.away-crest'), away);
}

function startTracking(fixture, token) {
  if (worker) { worker.terminate(); worker = null; }
  if (fixture.tracking !== 'full' || !fixture.paths.tracking) {
    $('#load-status').textContent = '60s preview ready · full tracking is not available for this match';
    $('#timeline-caption').textContent = '60s preview · clip only';
    $('#restart').textContent = 'Back to clip start ↺';
    return;
  }
  $('#load-status').textContent = '60s preview ready · loading full replay…';
  worker = new Worker('./tracking-worker.js?v=2.2.0', { type: 'module' });
  worker.onmessage = ({ data }) => {
    if (token !== state.loadToken) return;
    if (data.type === 'progress') $('#load-status').textContent = `60s preview ready · full replay ${data.progress}%`;
    if (data.type === 'error') {
      $('#load-status').textContent = `Full replay failed: ${data.message}. Using the 60s preview.`;
      worker?.terminate(); worker = null;
    }
    if (data.type === 'ready') {
      state.packed = data.data;
      decisionView.refreshReplayAvailability();
      $('#timeline').min = 0;
      $('#timeline').max = state.metadata.duration_ms;
      $('#timeline-caption').textContent = 'Full timeline';
      $('#restart').textContent = 'Back to kick-off ↺';
      setTimelineMode(true);
      $('#load-status').textContent = `${data.data.count.toLocaleString('en-GB')} frames ready · scrub the full match`;
      eventKey = '';
      buildAnalysis();
      refresh(true);
      worker?.terminate(); worker = null;
    }
  };
  worker.onerror = () => {
    if (token !== state.loadToken) return;
    $('#load-status').textContent = 'Full replay failed · using the 60s preview';
    worker?.terminate(); worker = null;
  };
  worker.postMessage({ metadata: state.metadata, trackingUrl: `${LEAGUE_ROOT}${fixture.paths.tracking}` });
}

async function loadMatch(matchId, { updateUrl = true } = {}) {
  const fixture = allFixtures.find(item => item.match_id === matchId);
  if (!fixture) return;
  const token = ++state.loadToken;
  decisionView.clear();
  if (worker) { worker.terminate(); worker = null; }
  state.packed = null;
  state.report = null;
  $('#post-match-banner').hidden = true;
  $('#match-report').innerHTML = '<p class="empty" role="status">Loading post-match analysis…</p>';
  state.playing = false;
  setPickerBusy(true);
  clearError();
  if (state.metadata) setPlaying(false);
  $('#load-status').textContent = `Loading ${fixture.home_name} vs ${fixture.away_name}…`;
  const base = `${LEAGUE_ROOT}${fixture.paths.match}`;
  try {
    const [metadata, summary, preview, events, snapshots] = await Promise.all([
      fetchJson(`${base}metadata.json`), fetchJson(`${base}summary.json`), fetchJson(`${base}preview-60s.json`),
      fetchJsonl(`${base}events.jsonl`), fetchJsonl(`${base}states.jsonl`),
    ]);
    if (token !== state.loadToken) return;
    Object.assign(state, { metadata, summary, preview, events: events.sort((a, b) => a.available_at_ms - b.available_at_ms || a.sequence - b.sequence), snapshots, time: preview.start_ms, fixture, selected: 'H10' });
    updateMatchChrome(fixture, metadata);
    syncPicker(fixture);
    buildPlayerSelect();
    decisionView.setMatch({base: './data/league-enriched-v1/matches/'+metadata.match_id+'/', dataset: datasetId, metadata, fixture, preview, clock, fullReady: () => !!state.packed});
    buildAnalysis();
    buildRosters();
    $('#timeline').min = preview.start_ms;
    $('#timeline').max = preview.end_ms;
    $('#timeline').value = preview.start_ms;
    $('#timeline').disabled = false;
    $('#timeline-caption').textContent = '60s preview · preparing replay';
    $('#restart').textContent = 'Back to clip start ↺';
    setTimelineMode(false, preview);
    $('#play').disabled = false;
    setPlaying(false);
    eventKey = '';
    statsKey = '';
    refresh(true);
    if (updateUrl && ['overview', 'replay', 'stats', 'lineup'].includes(state.view)) updateUrlForMatch(fixture.match_id);
    startTracking(fixture, token);
    loadReport(fixture, token);
  } catch (error) {
    if (token !== state.loadToken) return;
    $('#load-status').textContent = 'Data not ready';
    if (state.fixture) syncPicker(state.fixture);
    showError(`Unable to load match: ${error.message}. Generate data/league and serve the project over HTTP.`);
  } finally {
    if (token === state.loadToken) setPickerBusy(false);
  }
}

function stepMatch(delta) {
  if (!state.fixture || !allFixtures.length) return;
  const index = allFixtures.findIndex(fixture => fixture.match_id === state.fixture.match_id);
  loadMatch(allFixtures[(index + delta + allFixtures.length) % allFixtures.length].match_id);
}

async function loadSquads() {
  if (state.squads) return state.squads;
  if (!state.squadsPromise) {
    const squadRoot = LEAGUE_ROOT;
    state.squadsPromise = fetchJson(`${squadRoot}squads.json`).then(data => {
      if (squadRoot !== LEAGUE_ROOT) return null;
      state.squads = data;
      if (!data.teams.some(team => team.club_id === state.squadClub)) state.squadClub = data.teams[0]?.club_id || 'ARS';
      buildSquadClubSelect();
      renderSquads();
      $('#squad-season').textContent = data.season || '2026/27';
      if (data.source?.squad_lists) $('#squad-source').href = data.source.squad_lists;
      return data;
    }).catch(error => {
      $('#squad-content').innerHTML = `<div class="empty">Unable to load squads: ${escape(error.message)}</div>`;
      state.squadsPromise = null;
      return null;
    });
  }
  return state.squadsPromise;
}

function buildSquadClubSelect() {
  if (!state.squads) return;
  $('#squad-club-select').innerHTML = state.squads.teams.map(team =>
    `<option value="${escape(team.club_id)}">${escape(team.name)}</option>`).join('');
  $('#squad-club-select').value = state.squadClub;
}

function renderSquads() {
  if (!state.squads) return;
  const team = state.squads.teams.find(item => item.club_id === state.squadClub) || state.squads.teams[0];
  if (!team) return;
  const query = state.squadQuery.trim().toLowerCase();
  const players = team.players.filter(player => !query || [player.name, player.position_detail, player.nationality, player.position].join(' ').toLowerCase().includes(query));
  const groups = [['GK', 'Goalkeepers'], ['DEF', 'Defenders'], ['MID', 'Midfielders'], ['FWD', 'Forwards']];
  $('#squad-count').textContent = `${players.length} of ${team.players.length} players`;
  const rows = groups.map(([code, label]) => {
    const group = players.filter(player => player.position === code);
    if (!group.length) return '';
    return `<section class="squad-group"><div class="squad-group-head"><h4>${label}</h4><span>${group.length}</span></div><div class="squad-table"><div class="squad-row squad-header"><span>#</span><span>Player</span><span class="squad-position">Position</span><span class="squad-nationality">Nationality</span><span>Age</span></div>${group.map(player => `<div class="squad-row"><span class="squad-number">${player.shirt_number}</span><span class="squad-player"><strong>${escape(player.name)}</strong><small>${escape([player.position_detail || positions[player.position], player.nationality].filter(Boolean).join(' · '))}</small></span><span class="squad-detail squad-position">${escape(player.position_detail || positions[player.position] || '—')}</span><span class="squad-detail squad-nationality">${escape(player.nationality || '—')}</span><span class="squad-age">${player.age ?? '—'}</span></div>`).join('')}</div></section>`;
  }).join('');
  $('#squad-content').innerHTML = `<section class="card squad-card"><div class="squad-club-head"><div>${clubCrestHTML(team)}<div><h3>${escape(team.name)}</h3><p>${escape(team.stadium)} · ${team.players.length} registered players</p></div></div><span class="subtle-badge">${state.squads.season || '2026/27'}</span></div>${rows || '<div class="empty">No players match your search.</div>'}</section>`;
}

function setupHome() {
  try { state.homeClub = localStorage.getItem(HOME_CLUB_KEY); } catch { /* Session selection still works. */ }
  if (!state.league.teams.some(club => club.club_id === state.homeClub)) state.homeClub = null;
  $('#home-club-select').innerHTML = '<option value="">Select a club</option>' + state.league.teams.map(club =>
    `<option value="${escape(club.club_id)}">${escape(club.name)}</option>`).join('');
  $('#home-club-select').value = state.homeClub || '';
  $('#home-club-select').disabled = false;
  $('#save-home-club').disabled = false;
  renderHome();
}

function renderHome() {
  if (!state.league) return;
  const dashboard = clubDashboard(state.league, state.homeClub);
  if (!dashboard) {
    $('#home-content').innerHTML = `<section class="card home-empty"><span class="tiny-label">MATCHWEEK ${CURRENT_MATCHWEEK}</span><h2>A home for your club.</h2><p>Choose one of the ${state.league.teams.length} clubs above to see your season so far and your next opponent.</p><span class="subtle-badge">Before kick-off · ${escape(state.league.weeks.find(week => week.week === CURRENT_MATCHWEEK)?.date)}</span></section>`;
    return;
  }
  const { club, record, history, upcoming, rank } = dashboard;
  const next = upcoming[0];
  const opponent = next && state.league.teams.find(team => team.club_id === (next.home_club_id === club.club_id ? next.away_club_id : next.home_club_id));
  const form = history.map(match => `<span class="form-result result-${match.result}" title="Matchweek ${match.matchweek}: ${match.result === 'W' ? 'Win' : match.result === 'D' ? 'Draw' : 'Loss'}">${match.result}</span>`).join('');
  $('#home-content').innerHTML = `
    <section class="card home-hero"><div class="home-club-identity">${clubCrestHTML(club)}<div><span class="tiny-label">MY HOME CLUB</span><h2>${escape(club.name)}</h2><p>${escape(club.stadium)} · ${escape(state.league.competition.season)}</p></div></div><div class="home-form"><span class="tiny-label">RECENT FORM · OLDEST FIRST</span><div>${form || 'No results yet'}</div></div></section>
    <div class="home-metrics">${[
      ['League position', `#${rank}`, `Ranked through week ${CURRENT_MATCHWEEK - 1}`],
      ['Points', record.points, `${record.played} matches played`],
      ['Season record', `${record.won}W · ${record.drawn}D · ${record.lost}L`, `Before matchweek ${CURRENT_MATCHWEEK}`],
      ['Goals', `${record.goalsFor} : ${record.goalsAgainst}`, 'Scored : conceded'],
    ].map(([label, value, note]) => `<article class="card"><span class="tiny-label">${label}</span><strong>${value}</strong><small>${note}</small></article>`).join('')}</div>
    <div class="home-columns"><section class="card home-next"><span class="tiny-label">NEXT MATCH · ${next ? `MATCHWEEK ${next.matchweek}` : 'SCHEDULE'}</span>
      ${next ? `<div class="home-opponent">${clubCrestHTML(opponent)}<div><span>${next.home_club_id === club.club_id ? 'Home' : 'Away'} against</span><h2>${escape(opponent.name)}</h2></div></div><p>${escape(next.home_name)} <strong>vs</strong> ${escape(next.away_name)}</p><div class="home-fixture-info"><span>${icon('clock')}${escape(formatKickoff(next.kickoff_local))} · UK time</span><span>${icon('pitch')}${escape(next.venue)}</span></div><span class="subtle-badge">Upcoming · result not yet available</span>` : '<h2>You’re all caught up.</h2><p>No further fixtures in this dataset.</p>'}
      <h3>On the horizon</h3>${upcoming.slice(1).map(match => `<div class="home-future"><span>W${match.matchweek} · ${escape(formatKickoff(match.kickoff_local))}</span><strong>${escape(match.home_name)} vs ${escape(match.away_name)}</strong><small>${escape(match.venue)}</small></div>`).join('') || '<p>No more fixtures scheduled.</p>'}
    </section><section class="card home-results"><div class="home-results-heading"><h2>Results so far</h2><span class="subtle-badge">Weeks 1–${CURRENT_MATCHWEEK - 1}</span></div><p>Completed matches before the current week.</p>
      ${[...history].reverse().map(match => `<button class="home-result" data-home-match="${escape(match.match_id)}"><span class="form-result result-${match.result}">${match.result}</span><span><small>W${match.matchweek} · ${escape(formatKickoff(match.kickoff_local))}</small><strong>${escape(match.home_name)} <b>${match.score[0]} – ${match.score[1]}</b> ${escape(match.away_name)}</strong><small>${match.home_club_id === club.club_id ? 'Home' : 'Away'} · View match report →</small></span></button>`).join('') || '<p class="empty">No completed matches yet.</p>'}
    </section></div>`;
}

$('#home-club-form').onsubmit = event => {
  event.preventDefault();
  const selected = $('#home-club-select').value;
  if (!state.league?.teams.some(club => club.club_id === selected)) return;
  state.homeClub = selected;
  state.squadClub = selected;
  try {
    localStorage.setItem(HOME_CLUB_KEY, selected);
    $('#home-club-status').textContent = 'Home club saved. Your dashboard is ready.';
  } catch { $('#home-club-status').textContent = 'Home club selected for this session. Browser storage is unavailable.'; }
  renderHome();
  const current = allFixtures.find(match => match.matchweek === CURRENT_MATCHWEEK && [match.home_club_id, match.away_club_id].includes(selected));
  if (current) loadMatch(current.match_id, { updateUrl: false });
};
$('#home-content').onclick = async event => {
  const button = event.target.closest('[data-home-match]');
  if (!button) return;
  changeView('stats');
  await loadMatch(button.dataset.homeMatch);
};

async function init() {
  const rev = ++leagueRevision;
  const root = LEAGUE_ROOT;
  try {
    const league = await fetchJson(`${root}league.json`);
    if (rev !== leagueRevision) return;
    state.league = league;
    allFixtures = state.league.weeks.flatMap(week => week.matches);
    if (!allFixtures.length) throw new Error('League schedule is empty');
    buildWeekSelect();
    setupHome();
    $('#dataset-title').textContent = 'Premier League 2026/27';
    $('#dataset-version').textContent = DATASETS[datasetId].label;
    const requested = new URLSearchParams(location.search).get('match');
    const linked = allFixtures.find(fixture => fixture.match_id === requested);
    const current = allFixtures.filter(fixture => fixture.matchweek === CURRENT_MATCHWEEK);
    const initial = linked || current.find(fixture => [fixture.home_club_id, fixture.away_club_id].includes(state.homeClub)) || current.find(fixture => fixture.featured) || current[0] || allFixtures[0];
    const requestedView = new URLSearchParams(location.search).get('view');
    changeView(['stats','replay','overview'].includes(requestedView) ? requestedView : linked ? 'overview' : 'home');
    await loadMatch(initial.match_id, { updateUrl: Boolean(linked) });
  } catch (error) {
    if (rev !== leagueRevision) return;
    $('#load-status').textContent = 'Data not ready';
    showError(`Unable to load league: ${error.message}. Run python3 generate.py and serve the project over HTTP.`);
    $('.local-indicator').textContent = 'Local data unavailable';
  }
}

function bounds() {
  if (!state.metadata || !state.preview) return [0, 1];
  return state.packed ? [0, state.metadata.duration_ms] : [state.preview.start_ms, state.preview.end_ms];
}
function seek(time) {
  if (!state.metadata) return;
  const [min, max] = bounds();
  state.time = Math.min(max, Math.max(min, time));
  if (state.time >= max) setPlaying(false);
  eventKey = '';
  refresh(true);
}
function setPlaying(playing) {
  if (!state.metadata) return;
  if (playing && state.time >= bounds()[1]) seek(bounds()[0]);
  state.playing = playing;
  $('#play').innerHTML = icon(playing ? 'pause' : 'play');
  $('#play').setAttribute('aria-label', playing ? 'Pause' : 'Play');
}
function changeView(view) {
  state.view = view;
  document.body.dataset.view = view;
  const titles = {
    home: ['Home', 'Your club. The week ahead.', 'Results, form and the next challenge — all in one place.'],
    overview: ['Overview', 'Every moment, on the pitch.', 'Explore every pass, run and goal.'],
    replay: ['Replay', 'Back to the moment.', 'Scrub the timeline and explore every movement.'],
    stats: ['Analysis', 'The story behind the score.', 'Post-match analysis, Player of the Match and the full match statistics.'],
    lineup: ['Lineups', 'Every position on the pitch.', 'Starting lineups, substitutes and simulated distance.'],
    settings: ['Settings', 'Your match summary model.', 'Configure the model service used for post-match writing.'],
    squads: ['Squads', '2026/27 Premier League squads.', 'Official squad numbers and player details from the Premier League.'],
  };
  $('#breadcrumb').textContent = titles[view][0];
  $('#page-title').textContent = titles[view][1];
  $('#page-description').textContent = titles[view][2];
  document.querySelectorAll('[data-view]').forEach(el => {
    if (el === document.body) return;
    el.classList.toggle('active', el.dataset.view === view);
    if (el.getAttribute('role') === 'tab') el.setAttribute('aria-selected', String(el.dataset.view === view));
  });
  const matchView = !['home', 'squads', 'settings'].includes(view);
  $('#match-picker').hidden = !matchView || !state.league;
  $('#match-summary-card').hidden = !matchView;
  $('#export-summary').hidden = !matchView;
  $('#load-status').hidden = !matchView;
  $('#live-view').hidden = view !== 'overview' && view !== 'replay';
  $('#stats-view').hidden = view !== 'stats';
  decisionView.show(view === 'stats' && analysisSection === 'decisions');
  $('#lineup-view').hidden = view !== 'lineup';
  $('#squads-view').hidden = view !== 'squads';
  $('#settings-view').hidden = view !== 'settings';
  $('#home-view').hidden = view !== 'home';
  $('.section-tabs').hidden = ['home', 'settings'].includes(view);
  if (view === 'home') {
    renderHome();
    const url = new URL(location.href); url.searchParams.delete('match'); url.searchParams.set('view','home'); history.replaceState(null, '', url);
  } else if (matchView && state.fixture) updateUrlForMatch(state.fixture.match_id);
  if (view === 'settings') loadModelSettings();
  if (view !== 'overview' && view !== 'replay') setPlaying(false);
  if (view === 'home') $('#eyebrow-code').textContent = `/ WEEK ${CURRENT_MATCHWEEK} · ${state.league?.weeks.find(week => week.week === CURRENT_MATCHWEEK)?.date || ''}`;
  else if (view === 'settings') $('#eyebrow-code').textContent = '/ SETTINGS';
  else if (view === 'squads') { $('#eyebrow-code').textContent = '/ 2026/27'; loadSquads(); }
  else if (state.fixture) $('#eyebrow-code').textContent = `/ W${String(state.fixture.matchweek).padStart(2, '0')}`;
  if (state.metadata && matchView) refresh(true);
}

function metricHTML(stats, isFinal = false) {
  const h = stats.HOME, a = stats.AWAY;
  const home = teamName('HOME'), away = teamName('AWAY');
  const totalPasses = h.passes + a.passes;
  const complete = h.completed_passes + a.completed_passes;
  const metrics = [
    ['Total shots', h.shots + a.shots, 'shots', `${home} ${h.shots} · ${away} ${a.shots}`, `On target ${h.shots_on_target + a.shots_on_target}`, 'ball'],
    ['Passes', totalPasses, 'passes', `${home} ${h.passes} · ${away} ${a.passes}`, totalPasses ? `${(complete / totalPasses * 100).toFixed(1)}% accuracy` : 'No passes yet', 'arrow'],
    ['Interceptions', h.interceptions + a.interceptions, 'events', `${home} ${h.interceptions} · ${away} ${a.interceptions}`, isFinal ? 'Full match' : 'To date', 'pitch'],
    ['Pressure events', h.pressure_events + a.pressure_events, 'events', `${home} ${h.pressure_events} · ${away} ${a.pressure_events}`, 'Within 3 m', 'users']
  ];
  return metrics.map(([label, value, unit, detail, note, symbol]) => `<div class="metric"><div class="metric-head">${label}<span class="metric-icon">${icon(symbol)}</span></div><div class="metric-value">${display(value)}<small>${unit}</small></div><div class="metric-bottom"><span>${detail}</span><b>${note}</b></div></div>`).join('');
}

function statRows(stats, full = false) {
  const fields = [['shots', 'Shots'], ['shots_on_target', 'On target'], ['passes', 'Passes'], ['pass_accuracy_pct', 'Pass accuracy'], ['interceptions', 'Interceptions']];
  if (full) fields.unshift(['possession_pct', 'Possession']);
  if (full) {
    fields.push(['pressure_events', 'Pressure events'], ['possession_seconds', 'Possession seconds'], ['yellow_card', 'Yellow cards'], ['red_card', 'Red cards'], ['substitution', 'Substitutions']);
    stats = Object.fromEntries(['HOME','AWAY'].map(team => [team, { ...stats[team], ...Object.fromEntries(['yellow_card','red_card','substitution'].map(type => [type, state.events.filter(e => e.type === type && e.team_id === team).length])) }]));
  }
  return fields.map(([key, label]) => {
    const h = stats.HOME[key], a = stats.AWAY[key], max = Math.max(h || 0, a || 0, 1);
    const percent = key.endsWith('_pct');
    const format = n => n == null ? '—' : percent ? `${n.toFixed(1)}%` : display(n);
    return `<div class="stat-row"><div class="stat-values"><b>${format(h)}</b><span>${label}</span><b>${format(a)}</b></div><div class="stat-bars"><span class="stat-half"><i style="width:${(h || 0) / max * 100}%"></i></span><span class="stat-half"><i style="width:${(a || 0) / max * 100}%"></i></span></div></div>`;
  }).join('');
}

function eventDescription(event) {
  const player = playerLabel(event.player_id);
  const results = { goal: 'Goal', saved: 'Shot saved', off_target: 'Off target', complete: 'Pass complete', intercepted: 'Pass intercepted' };
  const names = { shot: results[event.outcome] || 'Shot', save: 'Save', pass: results[event.outcome] || 'Pass', interception: 'Interception', carry: 'Carry', pressure: 'Pressure', restart: 'Restart', stoppage: 'Stoppage', possession_change: 'Possession change', period_start: event.period === 1 ? 'First half started' : 'Second half started', period_end: event.period === 1 ? 'First half ended' : 'Second half ended', match_end: 'Full time', action_cancelled: 'Action cancelled', yellow_card: 'Yellow card', red_card: event.dismissal === 'second_yellow' ? 'Second yellow · sent off' : 'Red card', substitution: 'Substitution' };
  const reasons = { reckless_challenge: 'Reckless challenge', serious_foul_play: 'Serious foul play', dissent: 'Dissent', second_yellow: 'Second yellow card' };
  return {
    title: `${event.type === 'substitution' ? `${teamName(event.team_id)} · ` : event.player_id ? `${player} · ` : ''}${names[event.type] || event.type}`,
    detail: event.type === 'substitution' ? `↑ ${playerLabel(event.player_in_id)} on · ↓ ${playerLabel(event.player_out_id)} off` : ['yellow_card', 'red_card'].includes(event.type) ? `${teamName(event.team_id)} · ${reasons[event.reason] || 'Foul'}` : event.type === 'pass' ? `To ${playerLabel(event.recipient_id)}` : event.type === 'shot' && event.outcome === 'goal' ? `Score ${event.score.join(' : ')}` : event.type === 'pressure' ? `Pressure on ${playerLabel(event.target_player_id)}` : `${event.period === 1 ? 'First half' : 'Second half'} · ${teamName(event.team_id || event.to_team_id)}`,
    tag: { shot: event.outcome === 'goal' ? 'Goal' : 'Shot', pass: 'Pass', save: 'Save', interception: 'Interception', carry: 'Carry', pressure: 'Pressure', yellow_card: 'Yellow', red_card: 'Red', substitution: 'Sub' }[event.type] || 'Match',
    symbol: ['yellow_card','red_card'].includes(event.type) ? 'card' : event.type === 'substitution' ? 'swap' : event.type === 'shot' ? 'ball' : event.type === 'pass' ? 'arrow' : 'clock'
  };
}

function eventHTML(event) {
  const desc = eventDescription(event), goal = event.type === 'shot' && event.outcome === 'goal';
  const unavailable = !state.packed && (event.available_at_ms < state.preview.start_ms || event.available_at_ms > state.preview.end_ms);
  return `<button class="event-row ${goal ? 'goal' : ''} ${['yellow_card','red_card','substitution'].includes(event.type) ? event.type : ''}" data-event="${escape(event.event_id)}" ${unavailable ? 'disabled title="This event is outside the current preview clip; full replay is required to jump to it"' : ''}><time class="event-time">${clock(event.available_at_ms, event.period)}</time><span class="event-symbol ${event.team_id === 'AWAY' ? 'away' : ''}">${icon(desc.symbol)}</span><span class="event-description"><strong>${escape(desc.title)}</strong><small>${escape(desc.detail)}</small></span><span class="event-tag">${desc.tag}</span>${icon('arrow')}</button>`;
}

function updateEvents() {
  const eventTime = state.eventScope === 'full' ? state.metadata.duration_ms : state.time;
  const end = lastAtOrBefore(state.events, eventTime, 'available_at_ms');
  const key = `${end}:${state.filter}:${!!state.packed}:${state.eventScope}:${lastAtOrBefore(state.events, state.time, 'available_at_ms')}`;
  if (key === eventKey) return;
  eventKey = key;
  const events = releasedEvents(state.events, eventTime, state.filter);
  $('#event-count').textContent = events.length;
  $('#event-help').textContent = state.eventScope === 'full' ? 'Full match · includes events after the replay time' : 'Up to replay time · click an event to jump';
  const visible = events.slice(-100).reverse();
  $('#event-list').innerHTML = visible.length ? visible.map(eventHTML).join('') + (events.length > 100 ? '<div class="empty">Showing the latest 100 events · scrub the timeline for earlier events</div>' : '') : '<div class="empty">No events at this moment.<br>Play the match and wait for the next moment.</div>';
  $('#discipline-strip').innerHTML = ['yellow_card','red_card','substitution'].map(type => {
    const list = releasedEvents(state.events, state.time, type);
    return `<span class="${type}">${icon(type === 'substitution' ? 'swap' : 'card')}<b>${list.filter(e => e.team_id === 'HOME').length} : ${list.filter(e => e.team_id === 'AWAY').length}</b><small>${{yellow_card:'Yellow cards',red_card:'Red cards',substitution:'Substitutions'}[type]}</small></span>`;
  }).join('');
  const [min, max] = bounds();
  $('#goal-markers').innerHTML = releasedEvents(state.events, state.time, 'goal').filter(e => e.available_at_ms >= min).map(e => `<span class="goal-marker" style="left:${(e.available_at_ms - min) / (max - min) * 100}%"></span>`).join('');
}

function updatePlayer(frame) {
  const player = state.metadata.players.find(p => p.player_id === state.selected);
  const p = frame.players.find(p => p[0] === state.selected);
  if (!p) {
    const status = rosterStatus(player, state.events, state.time);
    $('#player-detail').innerHTML = `<div class="player-identity"><div class="shirt ${player.team_id === 'AWAY' ? 'away' : ''}">${player.jersey_number}</div><div><strong>${escape(playerLabel(player.player_id))}</strong><small>${escape(positions[player.position])} · ${escape(player.player_id)}</small></div></div><div class="empty">${escape(status.status)}<br>No on-pitch tracking at this time</div>`;
    return;
  }
  const speed = Math.hypot(p[3], p[4]) * 3.6;
  $('#player-detail').innerHTML = `<div class="player-identity"><div class="shirt ${player.team_id === 'AWAY' ? 'away' : ''}">${player.jersey_number}</div><div><strong>${escape(playerLabel(player.player_id))}</strong><small>${escape(positions[player.position])} · ${escape(player.position)} · ${escape(player.player_id)}</small></div></div><div class="player-measures"><div><span>Instant speed</span><strong>${speed.toFixed(1)} <small>km/h</small></strong></div><div><span>Distance to ball</span><strong>${Math.hypot(p[1] - frame.ball[0], p[2] - frame.ball[1]).toFixed(1)} <small>m</small></strong></div><div><span>Pitch X</span><strong>${p[1].toFixed(1)} <small>m</small></strong></div><div><span>Pitch Y</span><strong>${p[2].toFixed(1)} <small>m</small></strong></div></div><div class="player-status">${frame.phase === 'ended' ? '○ Full time' : frame.ball_owner_id === player.player_id ? '● On the ball' : frame.in_play ? '○ Off the ball' : '○ Waiting for restart'} · simulated tracking</div>`;
}

function refresh(force = false) {
  if (!state.metadata || !state.preview) return;
  state.frame = state.packed ? samplePacked(state.packed, state.time, state.metadata) : sampleFrames(state.preview.frames, state.time);
  const frame = state.frame;
  const live = state.view === 'overview' || state.view === 'replay';
  $('#post-match-banner').hidden = !(state.report && state.time >= state.metadata.duration_ms);
  const score = live ? frame.score : state.summary.final_score;
  $('#home-score').textContent = score[0];
  $('#away-score').textContent = score[1];
  $('#clock').textContent = live ? clock(state.time) : 'FT';
  $('#period-label').textContent = live ? (state.time >= state.metadata.duration_ms ? 'Full time' : frame.period === 1 ? 'First half' : 'Second half') : 'Full time';
  $('#score-context').textContent = live ? 'Score synced to replay' : 'Final score';
  $('#timeline-clock').textContent = clock(state.time);
  $('#timeline').value = state.time;
  const [min, max] = bounds();
  $('#timeline').style.setProperty('--progress', `${(state.time - min) / (max - min) * 100}%`);
  $('#pitch-phase').textContent = `${phaseNames[frame.phase] || frame.phase}${frame.in_play && frame.possession_team_id ? ` · ${teamName(frame.possession_team_id)}` : ''}`;
  const period = state.metadata.periods.find(p => p.period === frame.period);
  $('#attack-direction').textContent = `${teamName('HOME')} attack ${period.home_attacks === 'positive_x' ? '→' : '←'}`;
  const idx = lastAtOrBefore(state.snapshots, state.time);
  if (idx >= 0 && (statsKey !== idx || force)) {
    statsKey = idx;
    const stats = state.snapshots[idx].stats;
    $('#metrics').innerHTML = metricHTML(stats);
    $('#live-stats').innerHTML = statRows(stats);
    $('#home-possession').textContent = stats.HOME.possession_pct == null ? '—' : `${stats.HOME.possession_pct.toFixed(1)}%`;
    $('#away-possession').textContent = stats.AWAY.possession_pct == null ? '—' : `${stats.AWAY.possession_pct.toFixed(1)}%`;
    $('#possession-home').style.width = `${stats.HOME.possession_pct ?? 50}%`;
    $('#possession-away').style.width = `${stats.AWAY.possession_pct ?? 50}%`;
  }
  updateEvents();
  updatePlayer(frame);
  if (force) drawPitch(frame);
}

function buildAnalysis() {
  $('#full-metrics').innerHTML = metricHTML(state.summary.final_stats, true);
  $('#full-stats').innerHTML = statRows(state.summary.final_stats, true);
  $('#all-goals').innerHTML = state.events.filter(e => (e.type === 'shot' && e.outcome === 'goal') || ['yellow_card','red_card','substitution'].includes(e.type)).map(eventHTML).join('') || '<div class="empty">No key events in this match.</div>';
}

async function loadReport(fixture, token) {
  const end = state.events.find(e => e.type === 'match_end');
  if (!end) {
    $('#match-report').innerHTML = '<p class="empty" role="status">Post-match analysis will be available after full time.</p>';
    return;
  }
  try {
    const report = await fetchJson(`/api/match-report?dataset=${encodeURIComponent(datasetId)}&match_id=${encodeURIComponent(fixture.match_id)}`);
    if (token !== state.loadToken) return;
    if (report.match_id !== state.metadata.match_id || report.status !== 'complete'
        || report.source.event_count !== state.events.length
        || report.source.match_end_event_id !== end.event_id
        || JSON.stringify(report.source.final_score) !== JSON.stringify(state.summary.final_score)) {
      throw new Error('The report does not match the completed match.');
    }
    renderReport(report, fixture);
    state.report = report;
    refresh(true);
  } catch {
    if (token !== state.loadToken) return;
    state.report = null;
    $('#match-report').innerHTML = '<div class="empty" role="status"><strong>Post-match report unavailable</strong><p>Match statistics are still available. The report may not be ready yet. Please retry shortly.</p><button class="button" id="retry-analysis">Retry analysis</button></div>';
    $('#retry-analysis').onclick = () => {
      $('#match-report').innerHTML = '<p class="empty" role="status">Loading post-match analysis…</p>';
      loadReport(fixture, token);
    };
  }
}

function renderReport(report, fixture) {
  const potm = report.player_of_the_match;
  const mode = report.agent.mode === 'openai' ? `Model analysis · ${report.agent.model}` : 'Local evidence agent · rule-based';
  const candidates = report.ranking.slice(0, 5);
  $('#match-report').innerHTML = `
    <div class="report-header"><div><span class="tiny-label">POST-MATCH REPORT</span><h2>${escape(report.headline)}</h2><p>${escape(mode)} · ${report.source.event_count.toLocaleString('en-GB')} events reviewed</p></div>
    <a class="button" href="${escape(`/api/match-report?dataset=${encodeURIComponent(datasetId)}&match_id=${encodeURIComponent(fixture.match_id)}`)}" download="${escape(report.match_id)}-analysis.json">${icon('download')}Export report</a></div>
    ${report.agent.notice ? `<p class="report-notice">${escape(report.agent.notice)}</p>` : ''}
    <div class="report-grid"><article class="report-story"><h3>Match summary</h3><p class="report-lead">${escape(report.summary)}</p>
      ${report.periods.map(period => `<h4>${escape(period.title)}</h4><p>${escape(period.text)}</p>`).join('')}
      <h4>What the numbers show</h4><ul>${report.insights.map(text => `<li>${escape(text)}</li>`).join('')}</ul>
    </article><aside class="potm-card" aria-label="Player of the Match">
      <span class="tiny-label">PLAYER OF THE MATCH</span><div class="potm-identity"><span class="potm-number">${escape(potm.jersey_number)}</span><div><h3>${escape(potm.name)}</h3><p>${escape(teamName(potm.team_id))} · ${escape(potm.position)}</p></div></div>
      <div class="potm-score"><strong>${potm.impact_points.toFixed(2)}</strong><span>impact points</span></div>
      <div class="potm-stats">${[['Goals', potm.goals], ['On target', potm.shots_on_target], ['Saves', potm.saves], ['Interceptions', potm.interceptions]].map(([label, value]) => `<div><strong>${value}</strong><span>${label}</span></div>`).join('')}</div>
      <h4>Why this player</h4><ul>${potm.reasons.map(reason => `<li>${escape(reason)}</li>`).join('')}</ul>
    </aside></div>
    <div class="report-details"><section><h3>Match progression</h3><ol class="report-moments">${report.key_moments.map(moment => `<li><time>${escape(moment.time)}</time><p>${escape(moment.text)}</p></li>`).join('') || '<li>No goals, dismissals or substitutions were recorded.</li>'}</ol></section>
    <section><h3>Leading performers</h3><p class="report-muted">Ranked by the same published impact formula.</p><ol class="report-ranking">${candidates.map(p => `<li><span>${escape(p.name)}<small>${escape(teamName(p.team_id))} · ${p.minutes} min</small></span><strong>${p.impact_points.toFixed(2)}<small>points</small></strong></li>`).join('')}</ol>
    <details class="report-method"><summary>How the award is decided</summary><p>${escape(report.methodology)}</p></details></section></div>
    <p class="report-disclaimer">${escape(report.disclaimer)}</p>`;
}

function buildRosters() {
  $('#rosters').innerHTML = state.metadata.teams.map(team => `<section class="card"><div class="card-heading"><div class="roster-title">${$(`.${team.team_id === 'HOME' ? 'home' : 'away'}-crest`).outerHTML}<div><strong>${escape(team.name)}</strong><small>${escape(team.short_name || team.name)}</small></div></div><span class="subtle-badge">${escape(team.formation)}</span></div><div class="roster-header"><span>No.</span><span>Player</span><span>Position</span><span>Distance / km</span></div>${state.metadata.players.filter(p => p.team_id === team.team_id).map(p => `<button class="roster-row" data-player="${escape(p.player_id)}"><span class="jersey-pill ${team.team_id === 'AWAY' ? 'away' : ''}">${p.jersey_number}</span><span>${escape(p.name)}<small class="roster-status ${rosterStatus(p, state.events, state.metadata.duration_ms).status === 'Sent off' ? 'dismissed' : ''}">${p.starter === false ? 'Bench' : 'Starter'} · ${escape(rosterStatus(p, state.events, state.metadata.duration_ms).status)}${rosterStatus(p, state.events, state.metadata.duration_ms).yellow ? ' · Yellow' : ''}</small></span><span>${escape(p.position)}</span><span>${(state.summary.distance_m[p.player_id] / 1000).toFixed(2)}</span></button>`).join('')}</section>`).join('');
}

function pitchLayout() {
  const w = canvasWidth, h = canvasHeight;
  const scale = Math.min((w - 42) / 105, (h - 69) / 68);
  return { x: (w - 105 * scale) / 2, y: (h - 68 * scale) / 2 + 3, scale, w, h };
}

function drawPitch(frame) {
  if ($('#live-view').hidden || !frame || !canvasWidth) return;
  const { x, y, scale: s, w, h } = pitchLayout();
  const X = v => x + v * s, Y = v => y + v * s;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = '#426e57'; ctx.fillRect(0, 0, w, h);
  for (let i = 0; i < 10; i++) { ctx.fillStyle = i % 2 ? '#47745c' : '#426e57'; ctx.fillRect(X(i * 10.5), y, 10.5 * s, 68 * s); }
  ctx.strokeStyle = '#b9d1bd80'; ctx.lineWidth = 1;
  const rect = (a, b, c, d) => ctx.strokeRect(X(a), Y(b), c * s, d * s);
  const circle = (a, b, r, start = 0, end = Math.PI * 2) => { ctx.beginPath(); ctx.arc(X(a), Y(b), r * s, start, end); ctx.stroke(); };
  rect(0, 0, 105, 68); rect(0, 13.84, 16.5, 40.32); rect(88.5, 13.84, 16.5, 40.32);
  rect(0, 24.84, 5.5, 18.32); rect(99.5, 24.84, 5.5, 18.32);
  rect(-2.2, 30.34, 2.2, 7.32); rect(105, 30.34, 2.2, 7.32);
  ctx.beginPath(); ctx.moveTo(X(52.5), Y(0)); ctx.lineTo(X(52.5), Y(68)); ctx.stroke();
  circle(52.5, 34, 9.15); circle(11, 34, 9.15, -.924, .924); circle(94, 34, 9.15, Math.PI - .924, Math.PI + .924);
  circle(0, 0, 1, 0, Math.PI / 2); circle(105, 0, 1, Math.PI / 2, Math.PI); circle(0, 68, 1, -Math.PI / 2, 0); circle(105, 68, 1, Math.PI, Math.PI * 1.5);
  ctx.fillStyle = '#c2d8c4a0';
  for (const px of [11, 52.5, 94]) { ctx.beginPath(); ctx.arc(X(px), Y(34), 1.6, 0, Math.PI * 2); ctx.fill(); }
  const radius = Math.max(6, Math.min(10, s * 1.65));
  for (const p of frame.players) {
    const home = p[0][0] === 'H', selected = p[0] === state.selected, owner = p[0] === frame.ball_owner_id;
    const px = X(p[1]), py = Y(p[2]);
    if (selected || owner) { ctx.beginPath(); ctx.arc(px, py, radius + (selected ? 4 : 2.5), 0, Math.PI * 2); ctx.strokeStyle = selected ? '#ffffffbc' : '#f9dc9c'; ctx.lineWidth = selected ? 1.4 : 1; ctx.stroke(); }
    ctx.shadowColor = '#183b3a65'; ctx.shadowBlur = 4; ctx.shadowOffsetY = 2;
    ctx.beginPath(); ctx.arc(px, py, radius, 0, Math.PI * 2); ctx.fillStyle = home ? '#ee8b5a' : '#83a3e0'; ctx.fill();
    ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
    ctx.strokeStyle = home ? '#ffc9a391' : '#c5d7fbb0'; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = home ? '#fff7ed' : '#fff'; ctx.font = `600 ${Math.max(7, radius * .9)}px -apple-system, sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(numberOf(p[0]), px, py + .4);
  }
  const bx = X(frame.ball[0]), by = Y(frame.ball[1]), lift = Math.min(frame.ball[2] * s, 15);
  ctx.fillStyle = '#193d3760'; ctx.beginPath(); ctx.ellipse(bx + 1, by + 2, 3.5, 2, 0, 0, Math.PI * 2); ctx.fill();
  ctx.beginPath(); ctx.arc(bx, by - lift, 3.6, 0, Math.PI * 2); ctx.fillStyle = '#fffdf4'; ctx.fill(); ctx.strokeStyle = '#e1d9bf'; ctx.lineWidth = .8; ctx.stroke();
  ctx.beginPath(); ctx.arc(bx, by - lift, 1.3, 0, Math.PI * 2); ctx.fillStyle = '#707c6d'; ctx.fill();
}

new ResizeObserver(() => {
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  canvasWidth = rect.width; canvasHeight = rect.height;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  drawPitch(state.frame);
}).observe(canvas);

function tick(now) {
  const delta = lastTick ? Math.min(now - lastTick, 250) : 0;
  lastTick = now;
  if (state.playing && state.metadata && state.preview) {
    state.time = Math.min(bounds()[1], state.time + delta * state.speed);
    if (state.time >= bounds()[1]) setPlaying(false);
    state.frame = state.packed ? samplePacked(state.packed, state.time, state.metadata) : sampleFrames(state.preview.frames, state.time);
    drawPitch(state.frame);
    if (now - lastUI >= 160 || !state.playing) { refresh(); lastUI = now; }
  }
  requestAnimationFrame(tick);
}

async function modelRequest(path, data) {
  const response = await fetch(path, data ? {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  } : { cache: 'no-store' });
  let result;
  try { result = await response.json(); }
  catch { throw new Error('Model service is not running. Start the app with npm run dev.'); }
  if (!response.ok) throw new Error(result.error || 'Model service request failed.');
  return result;
}

function showModelSettings(config) {
  $('#model-base-url').value = config.base_url;
  $('#model-name').value = config.model;
  $('#model-api-key').value = '';
  $('#model-clear-key').checked = false;
  $('#model-key-state').textContent = config.has_key
    ? 'API key saved. Leave blank to keep it; enter a new key when changing the service URL.'
    : 'No API key saved. Keys are stored privately on this computer.';
}

async function loadModelSettings() {
  $('#save-model-settings').disabled = true;
  $('#model-settings-status').textContent = 'Loading settings…';
  try {
    showModelSettings(await modelRequest('/api/model-settings'));
    $('#model-settings-status').textContent = '';
    $('#save-model-settings').disabled = false;
  } catch (error) { $('#model-settings-status').textContent = error.message; }
}

$('#model-settings-form').onsubmit = async event => {
  event.preventDefault();
  $('#save-model-settings').disabled = true;
  $('#model-settings-status').textContent = 'Saving…';
  try {
    showModelSettings(await modelRequest('/api/model-settings', {
      base_url: $('#model-base-url').value, model: $('#model-name').value,
      api_key: $('#model-api-key').value, clear_key: $('#model-clear-key').checked,
    }));
    $('#model-settings-status').textContent = 'Settings saved. Open Analysis to generate a match summary.';
  } catch (error) { $('#model-settings-status').textContent = error.message; }
  finally { $('#save-model-settings').disabled = false; }
};

$('#generate-model-summary').onclick = async () => {
  const fixture = state.fixture, token = state.loadToken;
  if (!fixture || state.loading || !state.events.some(event => event.type === 'match_end')) {
    $('#model-summary-status').textContent = 'Select a completed match first.';
    return;
  }
  $('#generate-model-summary').disabled = true;
  $('#model-summary-status').textContent = 'Generating summary… This may take up to a minute.';
  try {
    const report = await modelRequest('/api/match-summary', { match_id: state.metadata.match_id, dataset: datasetId });
    if (token === state.loadToken) {
      state.report = report;
      renderReport(report, fixture);
      refresh(true);
      $('#model-summary-status').textContent = 'Model summary saved.';
    } else $('#model-summary-status').textContent = 'Summary saved for the previous match. Select it again to read it.';
  } catch (error) { $('#model-summary-status').textContent = error.message; }
  finally { $('#generate-model-summary').disabled = false; }
};

document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => changeView(button.dataset.view)));
document.querySelectorAll('[role=tab]').forEach(tab => tab.addEventListener('keydown', e => {
  const tabs = [...document.querySelectorAll('[role=tab]')];
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
  e.preventDefault();
  const next = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (tabs.indexOf(tab) + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  tabs[next].focus(); tabs[next].click();
}));
$('#week-select').onchange = () => {
  const week = Number($('#week-select').value);
  const plan = state.league?.weeks.find(item => item.week === week);
  renderMatchOptions(week);
  if (plan?.matches[0]) loadMatch(plan.matches[0].match_id);
};
$('#match-select').onchange = event => loadMatch(event.target.value);
$('#prev-match').onclick = () => stepMatch(-1);
$('#next-match').onclick = () => stepMatch(1);
$('#squad-club-select').onchange = event => { state.squadClub = event.target.value; renderSquads(); };
$('#squad-search').oninput = event => { state.squadQuery = event.target.value; renderSquads(); };
$('#play').onclick = () => setPlaying(!state.playing);
$('#back').onclick = () => seek(state.time - 10000);
$('#forward').onclick = () => seek(state.time + 10000);
$('#restart').onclick = () => seek(bounds()[0]);
$('#timeline').oninput = e => seek(Number(e.target.value));
$('#speed').onchange = e => { state.speed = Number(e.target.value); };
$('#event-scope').onchange = e => { state.eventScope = e.target.value; eventKey = ''; if (state.metadata) updateEvents(); };
$('#event-filter').onchange = e => { state.filter = e.target.value; if (state.metadata) updateEvents(); };
$('#player-select').onchange = e => { state.selected = e.target.value; refresh(true); };
document.addEventListener('click', e => {
  const eventButton = e.target.closest('[data-event]');
  if (eventButton && !eventButton.disabled) {
    const event = state.events.find(event => event.event_id === eventButton.dataset.event);
    setPlaying(false); changeView('replay'); seek(event.available_at_ms);
    if (event.player_in_id || event.player_id) { state.selected = event.player_in_id || event.player_id; $('#player-select').value = state.selected; refresh(true); }
    $('.pitch-card').scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  const playerButton = e.target.closest('[data-player]');
  if (playerButton) { state.selected = playerButton.dataset.player; $('#player-select').value = state.selected; changeView('overview'); $('.pitch-card').scrollIntoView({ behavior: 'smooth', block: 'center' }); }
});
canvas.onclick = e => {
  if (!state.frame) return;
  const box = canvas.getBoundingClientRect(), { x, y, scale } = pitchLayout();
  const px = (e.clientX - box.left - x) / scale, py = (e.clientY - box.top - y) / scale;
  const nearest = state.frame.players.map(p => ({ id: p[0], distance: Math.hypot(p[1] - px, p[2] - py) })).sort((a, b) => a.distance - b.distance)[0];
  if (nearest.distance * scale < 20) { state.selected = nearest.id; $('#player-select').value = nearest.id; refresh(true); }
};
document.addEventListener('keydown', e => {
  if (!state.metadata || ['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON', 'A'].includes(e.target.tagName) || $('#live-view').hidden) return;
  if (e.code === 'Space') { e.preventDefault(); setPlaying(!state.playing); }
  if (e.code === 'ArrowLeft') { e.preventDefault(); seek(state.time - 10000); }
  if (e.code === 'ArrowRight') { e.preventDefault(); seek(state.time + 10000); }
});
document.addEventListener('visibilitychange', () => { if (document.hidden) setPlaying(false); });
init();
requestAnimationFrame(tick);
