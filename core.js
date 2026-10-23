export const PHASES = ['carry', 'pass', 'shot', 'restart_setup', 'ended'];
export const STRIDE = 94;

export function formatClock(t, metadata, periodOverride) {
  const period = periodOverride ? metadata.periods.find(p => p.period === periodOverride) : [...metadata.periods].reverse().find(p => t >= p.start_ms);
  const p = period || metadata.periods[0];
  const elapsed = Math.max(0, Math.min(t - p.start_ms, p.duration_ms));
  const regulation = p.regulation_ms;
  const base = metadata.periods.filter(item => item.period < p.period).reduce((sum, item) => sum + item.regulation_ms, 0);
  const seconds = Math.floor((elapsed > regulation ? elapsed - regulation : base + elapsed) / 1000);
  const clock = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  return elapsed > regulation ? `${(base + regulation) / 60000}+${clock}` : clock.padStart(5, '0');
}

export function lastAtOrBefore(items, time, key = 't_ms') {
  let lo = 0, hi = items.length - 1, result = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (items[mid][key] <= time) { result = mid; lo = mid + 1; }
    else hi = mid - 1;
  }
  return result;
}

export function sampleFrames(frames, time) {
  const index = Math.max(0, lastAtOrBefore(frames, time));
  const a = frames[index], b = frames[Math.min(index + 1, frames.length - 1)];
  const alpha = b.t_ms > a.t_ms ? Math.max(0, Math.min(1, (time - a.t_ms) / (b.t_ms - a.t_ms))) : 0;
  const next = new Map(b.players.map(p => [p[0], p]));
  return { ...a, players: a.players.map(p => [p[0], ...p.slice(1).map((v, i) => v + ((next.get(p[0]) || p)[i + 1] - v) * alpha)]), ball: a.ball.map((v, i) => v + (b.ball[i] - v) * alpha) };
}

export function samplePacked(data, time, metadata) {
  const stride = data.stride ?? STRIDE;
  const ballOffset = stride - 6;
  const i = Math.max(0, Math.min(data.count - 1, Math.floor(time / data.step)));
  const next = Math.min(i + 1, data.count - 1);
  const alpha = Math.max(0, Math.min(1, (time - i * data.step) / data.step));
  const at = (offset, interpolate = true) => data.positions[i * stride + offset] + (data.positions[(interpolate ? next : i) * stride + offset] - data.positions[i * stride + offset]) * alpha;
  return {
    t_ms: i * data.step,
    period: data.periods[i],
    in_play: !!data.inPlay[i],
    phase: PHASES[data.phases[i]],
    possession_team_id: data.possession[i] === 1 ? 'HOME' : data.possession[i] === 2 ? 'AWAY' : null,
    ball_owner_id: data.owners[i] >= 0 ? metadata.players[data.owners[i]].player_id : null,
    score: [data.scores[i * 2], data.scores[i * 2 + 1]],
    players: metadata.players.flatMap((p, j) => {
      if (data.active && !data.active[i * metadata.players.length + j]) return [];
      const interpolate = !data.active || !!data.active[next * metadata.players.length + j];
      return [[p.player_id, ...Array.from({ length: 4 }, (_, k) => at(j * 4 + k, interpolate))]];
    }),
    ball: Array.from({ length: 6 }, (_, j) => at(ballOffset + j))
  };
}

export function releasedEvents(events, time, filter = 'all') {
  const end = lastAtOrBefore(events, time, 'available_at_ms');
  return events.slice(0, end + 1).filter(e => filter === 'all' || (filter === 'key' ? ['shot', 'save', 'yellow_card', 'red_card', 'substitution', 'period_start', 'period_end', 'match_end'].includes(e.type) : filter === 'goal' ? e.type === 'shot' && e.outcome === 'goal' : e.type === filter));
}

export function rosterStatus(player, events, time) {
  let status = player.starter === false ? 'Not used' : 'On pitch';
  let yellow = 0;
  for (const event of releasedEvents(events, time)) {
    if (event.type === 'yellow_card' && event.player_id === player.player_id) yellow++;
    if (event.type === 'red_card' && event.player_id === player.player_id) status = 'Sent off';
    if (event.type === 'substitution') {
      if (event.player_out_id === player.player_id) status = 'Subbed off';
      if (event.player_in_id === player.player_id) status = 'Subbed on';
    }
  }
  return { status, yellow };
}
