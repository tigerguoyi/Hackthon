import { PHASES } from './core.js?v=2.2.0';

self.onmessage = async ({ data: payload }) => {
  try {
    const { metadata, trackingUrl = './data/tracking.jsonl' } = payload;
    const step = 1000 / metadata.tracking_hz;
    const count = Math.floor(metadata.duration_ms / step) + 1;
    const stride = metadata.players.length * 4 + 6;
    const positions = new Float32Array(count * stride);
    const active = new Uint8Array(count * metadata.players.length);
    const scores = new Uint16Array(count * 2);
    const phases = new Uint8Array(count);
    const owners = new Int8Array(count).fill(-1);
    const possession = new Uint8Array(count);
    const periods = new Uint8Array(count);
    const inPlay = new Uint8Array(count);
    const ids = new Map(metadata.players.map((p, i) => [p.player_id, i]));
    const response = await fetch(trackingUrl, { cache: 'no-store' });
    if (!response.ok) throw new Error(`Unable to read tracking file (${response.status})`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let carry = '', index = 0, lastProgress = 0;
    const append = line => {
      if (!line.trim()) return;
      const frame = JSON.parse(line);
      if (index >= count || frame.t_ms !== index * step || frame.players.length > 22 || frame.players.length < 14) throw new Error('Incomplete tracking frame or non-continuous time');
      for (const p of frame.players) {
        const offset = ids.get(p[0]) * 4;
        if (!Number.isFinite(offset)) throw new Error('Unknown player in tracking data');
        positions.set(p.slice(1), index * stride + offset);
        active[index * metadata.players.length + ids.get(p[0])] = 1;
      }
      positions.set(frame.ball, index * stride + stride - 6);
      scores.set(frame.score, index * 2);
      phases[index] = PHASES.indexOf(frame.phase);
      owners[index] = ids.get(frame.ball_owner_id) ?? -1;
      possession[index] = frame.possession_team_id === 'HOME' ? 1 : frame.possession_team_id === 'AWAY' ? 2 : 0;
      periods[index] = frame.period;
      inPlay[index] = Number(frame.in_play);
      index++;
    };
    while (true) {
      const { done, value } = await reader.read();
      carry += done ? decoder.decode() : decoder.decode(value, { stream: true });
      const lines = carry.split('\n');
      carry = lines.pop();
      for (const line of lines) append(line);
      if (index - lastProgress > 1500) { self.postMessage({ type: 'progress', progress: Math.round(index / count * 100) }); lastProgress = index; }
      if (done) break;
    }
    append(carry);
    if (index !== count) throw new Error(`Not enough tracking frames: ${index}/${count}`);
    const data = { count, step, stride, positions, active, scores, phases, owners, possession, periods, inPlay };
    self.postMessage({ type: 'ready', data }, Object.values(data).filter(v => ArrayBuffer.isView(v)).map(v => v.buffer));
  } catch (error) { self.postMessage({ type: 'error', message: error.message }); }
};
