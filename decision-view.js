// Views load precomputed analysis only. Tactical assumptions are not observed truth.
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(x,n=3)=>x==null?'—':Number(x).toFixed(n);
export const preferenceText=x=>x==null?'—':x<.1?'<0.1':fmt(x,1);
// Snapshot facts only: do not use candidate features, outcomes or projected states.
export function playerActionConditions(state, playerId=state?.actor){
  const player=state?.players?.find(p=>p.id===playerId);
  if(!player)return null;
  const hasPosition=p=>Number.isFinite(p?.x)&&Number.isFinite(p?.y);
  const speed=Number.isFinite(player.vx)&&Number.isFinite(player.vy)?Math.hypot(player.vx,player.vy):null;
  const moving=speed!==null&&speed>.2;
  const heading=moving?(Math.atan2(player.vy,player.vx)*180/Math.PI+360)%360:null;
  const attackDirection=(player.team===0)===(state.period===1)?1:-1;
  const relativeHeading=moving?(Math.atan2(player.vy*attackDirection,player.vx*attackDirection)*180/Math.PI+360)%360:null;
  const directions=['Forward','Forward-right','Right','Backward-right','Backward','Backward-left','Left','Forward-left'];
  const opponents=hasPosition(player)?state.players.filter(p=>p.team!==player.team&&hasPosition(p))
    .map(p=>({id:p.id,distance:Math.hypot(p.x-player.x,p.y-player.y)})).sort((a,b)=>a.distance-b.distance||a.id.localeCompare(b.id)):[];
  const nearest=opponents[0]??null;
  const ball=state.ball;
  return {playerId:player.id,speedMps:speed,speedKmh:speed===null?null:speed*3.6,headingDeg:heading,
    movementLabel:speed===null?'Unavailable':moving?directions[Math.round(relativeHeading/45)%8]:'Stationary / slow',
    nearestOpponent:nearest,underPressure:nearest?nearest.distance<3:null,
    opponentsWithin5m:hasPosition(player)?opponents.filter(p=>p.distance<5).length:null,
    ballDistance:hasPosition(player)&&Number.isFinite(ball?.[0])&&Number.isFinite(ball?.[1])?Math.hypot(ball[0]-player.x,ball[1]-player.y):null};
}
export function filterDecisions(rows,filters,representative,largest){
  const selected=new Set(filters.scope==='representative'?representative:largest);
  return rows.filter(r=>(!filters.team||String(r.team)===filters.team)&&(!filters.player||r.actor===filters.player)&&
    (!filters.kind||r.kind===filters.kind)&&(filters.scope==='all'?r.coverage==='exact':filters.scope==='coverage'?
      r.coverage!=='exact'||r.conflicts.length||r.relative_score==null:selected.has(r.id)));
}
const names={pass:'Pass',carry:'Carry',shot:'Shoot',hold:'Current movement',press:'Press ball',cover:'Cover lane',mark:'Mark receiver',drop:'Drop back'};
export function createDecisionView(container,jump){
  container.innerHTML=`<div class="decision-header"><div><span class="tiny-label">INTERNAL RESEARCH · ORIGINAL MATCH</span><h2>Compare the choices, keep the facts.</h2><p>Two-step tactical hypotheses alongside the original simulator baseline.</p></div><span class="subtle-badge">EXISTING TRACKING ONLY</span></div>
    <div class="decision-toolbar"><label>View<select id="decision-mode"><option value="ball">On-ball choices</option><option value="baseline">Baseline comparison</option><option value="defense">Defensive diagnostics</option></select></label><label>Possession team<select id="decision-team"><option value="">Both teams</option></select></label><label>Decision player<select id="decision-player"><option value="">All players</option></select></label><label>Action<select id="decision-kind"><option value="">All actions</option><option value="pass">Pass</option><option value="carry">Carry</option><option value="shot">Shot</option></select></label><label>Scope<select id="decision-scope"><option value="representative">Representative decisions</option><option value="all">All covered decisions</option><option value="largest">Largest value losses</option><option value="coverage">Coverage / conflicts</option></select></label></div>
    <div id="decision-status" role="status" aria-live="polite"></div><div id="decision-summary" class="decision-summary"></div><p id="decision-coverage" class="decision-disclaimer"></p>
    <div class="decision-workspace"><aside class="card decision-list-card"><div class="card-heading"><h3>Decision timeline</h3><span id="decision-count"></span></div><div id="decision-list"></div><button class="button" id="decision-more" hidden>Show more decisions</button></aside><div id="decision-detail"><p class="empty">Choose a covered decision.</p></div></div>
    <p class="decision-disclaimer">Relative preferences sum to 100 across supported candidates at one moment. They are not success probabilities, player ability or historical percentiles. Tactical rules and predicted next states are hypotheses. Original matches are unchanged.</p>`;
  const $=s=>container.querySelector(s);
  let context=null,index=null,detail=null,selected=null,defender=null,defenseTarget=null,shown=false,pageSize=30,revision=0,request=0,loading=null;
  let controller=new AbortController();const cache=new Map();
  const label=id=>context?.metadata.players.find(p=>p.player_id===id)?.name||id;
  const clock=r=>context.clock(r.t,r.period);
  const filters=()=>Object.fromEntries(['team','player','kind','scope'].map(k=>[k,$('#decision-'+k).value]));
  const optionLabel=a=>a.kind==='pass'?`Pass to ${label(a.recipient)}`:a.kind==='carry'?`Carry ${a.key.split(':')[1]}`:'Shoot';
  function actionConditions(playerId){
    const conditions=playerActionConditions(detail.state,playerId);
    if(!conditions)return '<section class="action-conditions"><h3>Player action conditions</h3><p>Player tracking is unavailable at this moment.</p></section>';
    const c=conditions,nearest=c.nearestOpponent;
    const opponentLabel=$('#decision-mode').value==='defense'?'Nearest attacker':'Nearest defender';
    const pressure=c.underPressure===null?'Unavailable':c.underPressure?'Opponent within 3 m':'No opponent within 3 m';
    return `<section class="action-conditions" aria-labelledby="action-conditions-title"><div class="action-conditions-heading"><div><span class="tiny-label">DECISION-TIME TRACKING</span><h3 id="action-conditions-title">Player action conditions</h3><p>${esc(label(playerId))} · ${esc(clock(detail))}</p></div><span class="action-conditions-badge">Original snapshot</span></div>
      <dl class="action-conditions-grid">
        <div><dt>Movement speed</dt><dd>${fmt(c.speedMps,2)} <span>m/s</span></dd><small>${fmt(c.speedKmh,1)} km/h</small></div>
        <div><dt>Movement direction</dt><dd>${c.headingDeg===null?'—':`<span class="movement-arrow" aria-hidden="true" style="transform:rotate(${c.headingDeg.toFixed(1)}deg)">→</span> ${fmt(c.headingDeg,1)}<span>°</span>`}</dd><small>${esc(c.movementLabel)}${c.headingDeg===null?'':' · relative to attack'}</small></div>
        <div><dt>${opponentLabel}</dt><dd>${fmt(nearest?.distance,2)} <span>m</span></dd><small>${nearest?esc(label(nearest.id)):'Unavailable'}</small></div>
        <div class="${c.underPressure?'condition-under-pressure':''}"><dt>3 m proximity</dt><dd class="condition-status">${c.underPressure===null?'Unknown':c.underPressure?'Under pressure':'Space available'}</dd><small>${pressure}</small></div>
        <div><dt>Opponents within 5 m</dt><dd>${c.opponentsWithin5m??'—'}</dd><small>All on-pitch opponents, including the goalkeeper</small></div>
        <div><dt>Distance to ball</dt><dd>${fmt(c.ballDistance,2)} <span>m</span></dd><small>Player position to ball position</small></div>
      </dl><p class="action-conditions-note">Movement follows the velocity vector, not body orientation. Pitch angles: 0° right, 90° down; forward/left/right follow this player's attacking direction. Direction is hidden at ≤0.2 m/s. Body orientation, preferred foot and touch quality are not recorded. Proximity is a distance indicator, not a measured tackling threat.</p></section>`;
  }
  async function fetchData(url){
    const response=await fetch(url,{signal:controller.signal});
    if(!response.ok)throw new Error(`Analysis unavailable (${response.status}).`);
    if(!globalThis.DecompressionStream)throw new Error('This browser needs gzip stream support.');
    return new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).json();
  }
  function renderList(){
    if(!index)return;
    const rows=filterDecisions(index.records,filters(),index.representative_ids,index.largest_ids);
    const counts=new Map();for(const r of index.records)if(r.coverage==='exact')counts.set(r.actor,(counts.get(r.actor)||0)+1);
    $('#decision-count').textContent=rows.length.toLocaleString()+' moments';
    $('#decision-list').innerHTML=rows.slice(0,pageSize).map(r=>`<button class="decision-row ${detail?.id===r.id?'selected':''}" data-decision="${esc(r.id)}"><time>${esc(clock(r))}</time><span><strong>${esc(label(r.actor))}</strong><small>${esc(names[r.kind])} · ${r.coverage!=='exact'?'No tracking':r.relative_score==null?'Not scored':r.sensitive?'Assumption-sensitive':r.category==='similar'?'Similar value':r.category==='favoured'?'Model-favoured':'Opportunity loss'} · ${counts.get(r.actor)||0} covered</small></span><b>${$('#decision-mode').value==='defense'?'1s':preferenceText(r.relative_score)}<small>${$('#decision-mode').value==='defense'?'local':'share /100'}</small></b></button>`).join('')||'<p class="empty">No eligible moments in this selection. Try All covered decisions or Coverage / conflicts.</p>';
    $('#decision-more').hidden=rows.length<=pageSize;
    if(rows.length&&!rows.some(r=>r.id===detail?.id))selectDecision(rows[0]);
    else if(!rows.length){detail=null;$('#decision-detail').innerHTML='<p class="empty">No matching decisions. Coverage and diversity rules can result in fewer than 20 representatives.</p>';}
  }
  async function selectDecision(row){
    const mine=++request,rev=revision;
    if(row.block==null){detail=row;$('#decision-status').textContent='';$('#decision-detail').innerHTML=`<section class="card decision-detail-card"><span class="tiny-label">${esc(clock(row))} · COVERAGE GAP</span><h2>${esc(label(row.actor))}</h2><p>${esc(row.reason)}</p><p>There is no existing exact-time team snapshot here. No candidate ranking, synthetic player locations or score has been generated.</p><small>Source event ${esc(row.id)} · original event preserved.</small></section>`;renderList();return;}
    $('#decision-status').textContent='Loading referenced decision state…';
    try{
      if(!cache.has(row.block))cache.set(row.block,fetchData(context.base+index.blocks[row.block].path));
      const block=await cache.get(row.block);if(rev!==revision||mine!==request)return;
      detail=block.find(r=>r.id===row.id);if(!detail)throw new Error('Decision missing from result block.');
      selected=detail.best_key;defender=detail.defense[0]?.player_id;defenseTarget=null;
      $('#decision-status').textContent='';renderDetail();renderList();
    }catch(error){if(rev===revision&&mine===request&&error.name!=='AbortError'){cache.delete(row.block);$('#decision-status').textContent=error.message+' Select this moment to retry.';}}
  }
  function renderDetail(){
    if(!detail?.state)return;
    const mode=$('#decision-mode').value,opts=detail.options,actual=opts.find(a=>a.key===detail.actual_key),chosen=opts.find(a=>a.key===selected)||opts[0];
    const best=opts.find(a=>a.q!=null),supported=opts.filter(a=>a.relative_score!=null);
    const canJump=(context.fixture.tracking==='full'&&context.fullReady())||(detail.t>=context.preview.start_ms&&detail.t<=context.preview.end_ms);
    let table,explanation;
    if(mode==='defense'){
      if(!detail.defense.some(d=>d.player_id===defender))defender=detail.defense[0]?.player_id;
      const diagnostic=detail.defense.find(d=>d.player_id===defender);
      table=`<label class="defender-picker">Defender<select id="diagnostic-player">${detail.defense.map(d=>`<option value="${d.player_id}" ${d.player_id===defender?'selected':''}>${esc(label(d.player_id))}</option>`).join('')}</select></label><div class="decision-table-wrap"><table class="decision-table"><thead><tr><th>Local option</th><th>Threat reduction</th></tr></thead><tbody>${(diagnostic?.options||[]).map((d,i)=>`<tr><td><button data-defense-option="${i}">${esc(names[d.action])}</button></td><td>${fmt(d.reduction,4)}</td></tr>`).join('')}</tbody></table></div>`;
      explanation='<h3>One-second local opportunity</h3><p>Move one defender using continuous acceleration-bounded displacement, then re-evaluate one-step tactical threat. Positive reduction is locally preferable. This is separate from the two-step on-ball value; it is not goals prevented or a player ability score.</p>';
      defenseTarget=defenseTarget||diagnostic?.options[0]?.target;
    }else if(mode==='baseline'){
      const rank=(field,key)=>{const valid=opts.filter(a=>a[field]!=null).sort((a,b)=>b[field].q-a[field].q);const i=valid.findIndex(a=>a.key===key);return i<0?'Unsupported':String(i+1);};
      table=`<div class="decision-table-wrap"><table class="decision-table"><thead><tr><th>Candidate</th><th>B0 Q / rank</th><th>B1 Q / rank</th><th>Tactical Q / rank</th></tr></thead><tbody>${opts.map((a,i)=>`<tr><td><button data-option="${esc(a.key)}">${esc(optionLabel(a))}</button></td><td>${fmt(a.baseline_B0?.q,4)} / ${rank('baseline_B0',a.key)}</td><td>${fmt(a.baseline_B1?.q,4)} / ${rank('baseline_B1',a.key)}</td><td>${fmt(a.q,4)} / ${a.q==null?'—':i+1}</td></tr>`).join('')}</tbody></table></div>`;
      explanation='<h3>Different evaluation assumptions</h3><p>B0 is a basic-feature pass distillation; B1 adds spatial features. Their carry retention is 1 by original construction, and shots use the original team-based formula. The tactical model adds geometric risk and one further action. Disagreement is an assumption difference, not proof of real-world accuracy.</p>';
    }else{
      table=`<div class="decision-table-wrap"><table class="decision-table"><thead><tr><th>Candidate</th><th>Success / goal</th><th>Two-step Q</th><th>Risk</th><th>Preference</th></tr></thead><tbody>${opts.map(a=>`<tr class="${a.key===selected?'selected':''}"><td><button data-option="${esc(a.key)}">${esc(optionLabel(a))}<small>${a.key===detail.actual_key?'ACTUAL · STANDARDISED':a.key===detail.best_key?'HIGHEST ESTIMATE':a.supported?'ALTERNATIVE':'UNSUPPORTED'} · ${esc(a.mode)}</small></button></td><td>${a.probabilities?fmt((a.probabilities.success??a.probabilities.goal)*100,1)+'%':'—'}</td><td>${fmt(a.q,4)}</td><td>${fmt(a.risk,4)}</td><td><strong>${preferenceText(a.relative_score)}</strong></td></tr>`).join('')}</tbody></table></div>`;
      const f=chosen.features;
      explanation=`<h3>${detail.assumption_sensitive?'Assumption-sensitive ranking':detail.category==='similar'?'Several options have similar value':'Why this option ranks here'}</h3><p>Highest estimate: <strong>${esc(optionLabel(best))}</strong>. Historical choice: <strong>${esc(optionLabel(opts.find(a=>a.key===detail.chosen)||{kind:detail.kind,key:detail.chosen,recipient:detail.retrospective.recipient}))}</strong>. ${actual?'Value loss '+fmt(detail.loss,4)+'.':'No supported actual-action score is available.'}</p>${f?`<p>Selected: ${esc(optionLabel(chosen))}; ${fmt(f.distance,1)} m, source pressure ${fmt(f.source_pressure,2)}, target pressure ${fmt(f.receiver_pressure,2)}, nearest receiver defender ${fmt(f.receiver_nearest_defender,1)} m. These are geometry-derived heuristics.</p><p>Immediate goal contribution ${fmt(chosen.immediate,4)} + own continuation ${fmt(chosen.continuation,4)} − opponent risk contribution ${fmt(chosen.risk,4)} = Q ${fmt(chosen.q,4)}. One-step Q ${fmt(chosen.one_step_q,4)}.</p><p>Result branches: ${Object.entries(chosen.probabilities).map(([k,v])=>esc(k)+' '+fmt(v*100,1)+'%').join(' · ')}.</p><p>Risk coefficients ±20% give Q ${chosen.sensitivity_q.map(x=>fmt(x,4)).join(' / ')}. This is assumption sensitivity, not a confidence interval.</p>`:`<p>${esc(chosen.reason)}</p>`}<p>${supported.length} supported options · equal-choice share ${fmt(100/Math.max(1,supported.length),1)} · preference weights total ${fmt(supported.reduce((n,a)=>n+a.relative_score,0),1)} · temperature ${fmt(detail.temperature,4)}. ${supported.length===1?'Only one supported option: no meaningful comparison.':''}</p>`;
    }
    $('#decision-detail').innerHTML=`<section class="card decision-detail-card"><div class="decision-detail-heading"><div><span class="tiny-label">${esc(clock(detail))} · ${mode==='defense'?'DEFENSIVE DIAGNOSTIC':'EXACT ORIGINAL SNAPSHOT'}</span><h2>${esc(label(mode==='defense'?defender:detail.actor))}</h2></div><div class="decision-grade"><strong>${mode==='defense'?'1s':preferenceText(detail.relative_score)}</strong><small>${mode==='defense'?'local horizon':'preference share /100'}</small></div></div><canvas id="decision-pitch" role="img" aria-label="Original decision-time positions with actual and hypothetical targets"></canvas><p class="decision-legend"><span>● Home</span><span>● Away</span><span>Solid: standardised actual choice</span><span>Dashed: selected hypothesis</span></p>${actionConditions(mode==='defense'?defender:detail.actor)}<div class="decision-jump"><button class="button" id="decision-jump" ${canJump?'':'disabled'}>Open in replay</button><span>${canJump?'Jump to original replay.':context.fixture.tracking==='full'?'Full replay is still loading.':'Original preview does not include this moment.'}</span></div>${table}<div class="decision-reason">${explanation}<details><summary>Source and constraints</summary><p>Observed original: ${esc(detail.source_frame.file)} · exact time ${detail.source_frame.t_ms} ms · alignment ${detail.source_frame.alignment_ms} ms. Event ${esc(detail.event_id)}; possession ${detail.possession_id}.</p><p>Choice resolution: ${esc(detail.choice_resolution)}. Planned duration and carry direction were not fully recorded as original predecision intention.</p><p>${detail.conflicts.length?detail.conflicts.map(esc).join('<br>'):'No additional constraint conflict detected.'}</p><p>Positions: observed_original; geometry: derived; target and duration: candidate_derived; probability and Q: model_prediction. No latent states generated.</p></details><details><summary>Actual outcome · retrospective only</summary><p>${esc(detail.retrospective.outcome)}; end ${esc(JSON.stringify(detail.retrospective.end_location))}. These future values were not inputs to candidate scoring.</p></details><small>${esc(detail.model_version)} · fold ${index.model_fold+1}/4 · match excluded from fitting/calibration · ${index.full_tracking?'full tracking':'outcome-selected preview; descriptive inference only'}.</small></div></section>`;
    $('#decision-jump').onclick=()=>jump(detail.t);
    $('#diagnostic-player')?.addEventListener('change',e=>{defender=e.target.value;defenseTarget=null;renderDetail();});
    draw();
  }
  function draw(){
    const c=$('#decision-pitch');if(!c||!detail?.state)return;
    const width=Math.max(240,c.clientWidth),height=width*.64,ratio=devicePixelRatio||1;
    c.width=width*ratio;c.height=height*ratio;c.style.height=height+'px';const ctx=c.getContext('2d');ctx.scale(ratio,ratio);
    const scale=Math.min((width-24)/105,(height-24)/68),ox=(width-105*scale)/2,oy=(height-68*scale)/2,xy=(x,y)=>[ox+x*scale,oy+y*scale];
    ctx.fillStyle='#e9f0e7';ctx.fillRect(0,0,width,height);ctx.strokeStyle='#a4b6a0';ctx.lineWidth=1.2;ctx.strokeRect(ox,oy,105*scale,68*scale);
    ctx.beginPath();ctx.moveTo(...xy(52.5,0));ctx.lineTo(...xy(52.5,68));ctx.stroke();ctx.beginPath();ctx.arc(...xy(52.5,34),9.15*scale,0,Math.PI*2);ctx.stroke();for(const x of[0,88.5])ctx.strokeRect(...xy(x,13.84),16.5*scale,40.32*scale);
    for(const p of detail.state.players){const [x,y]=xy(p.x,p.y);ctx.beginPath();ctx.arc(x,y,p.id===detail.actor?9:7,0,Math.PI*2);ctx.fillStyle=p.team===0?'#405d83':'#b56046';ctx.fill();if(p.id===detail.actor){ctx.strokeStyle='#223c31';ctx.lineWidth=2;ctx.stroke();}ctx.fillStyle='white';ctx.font='9px sans-serif';ctx.textAlign='center';ctx.fillText(String(context.metadata.players.find(q=>q.player_id===p.id)?.jersey_number||p.id.slice(1)),x,y+3);}
    const [bx,by]=xy(...detail.state.ball);ctx.beginPath();ctx.arc(bx,by,3,0,Math.PI*2);ctx.fillStyle='#111';ctx.fill();
    function arrow(p,target,color,dashed){ctx.strokeStyle=color;ctx.lineWidth=2.5;ctx.setLineDash(dashed?[6,4]:[]);const [x,y]=xy(p.x,p.y),[tx,ty]=xy(...target);ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(tx,ty);ctx.stroke();ctx.setLineDash([]);const a=Math.atan2(ty-y,tx-x);ctx.beginPath();ctx.moveTo(tx,ty);ctx.lineTo(tx-8*Math.cos(a-.4),ty-8*Math.sin(a-.4));ctx.moveTo(tx,ty);ctx.lineTo(tx-8*Math.cos(a+.4),ty-8*Math.sin(a+.4));ctx.stroke();}
    const actor=detail.state.players.find(p=>p.id===detail.actor),actual=detail.options.find(a=>a.key===detail.actual_key),sel=detail.options.find(a=>a.key===selected);
    if(actual)arrow(actor,actual.target,'#404b53',false);
    if($('#decision-mode').value==='defense'){const p=detail.state.players.find(p=>p.id===defender);if(p&&defenseTarget)arrow(p,defenseTarget,'#6954a0',true);}
    else if(sel?.supported)arrow(actor,sel.target,'#d37438',true);
  }
  async function load(){
    if(!shown||!context||index||loading)return;
    if(context.dataset==='original'){$('#decision-status').textContent='Choose Enriched analysis to inspect the original matches with independent analysis sidecars.';return;}
    const rev=revision;$('#decision-status').textContent='Loading coverage and precomputed analysis…';loading=fetchData(context.base+'index.json.gz');
    try{const data=await loading;if(rev!==revision)return;if(data.dataset!==context.dataset||data.match_id!==context.metadata.match_id)throw new Error('Mismatched dataset or match.');index=data;const s=data.summary;
      $('#decision-status').textContent='';$('#decision-summary').innerHTML=`<article><span>Existing snapshots</span><strong>${s.covered.toLocaleString()} / ${s.total.toLocaleString()}</strong></article><article><span>Actual choices scored</span><strong>${s.scored.toLocaleString()}</strong></article><article><span>Representative players</span><strong>${s.representative_players}</strong></article>`;
      $('#decision-coverage').textContent=data.full_tracking?'Full original tracking available. Representatives balance teams, actors, field zones and possessions.':'Only the original 60-second preview is available. Missing moments have no synthetic reconstruction or score. The clip was selected around the final goal and is excluded from fitting and calibration.';
      renderList();
    }catch(error){if(rev===revision&&error.name!=='AbortError')$('#decision-status').textContent=error.message+' Run the enrichment pipeline or reselect this match to retry.';}
    finally{if(rev===revision)loading=null;}
  }
  container.addEventListener('click',e=>{
    const row=e.target.closest('[data-decision]');if(row&&index)selectDecision(index.records.find(r=>r.id===row.dataset.decision));
    const option=e.target.closest('[data-option]');if(option){selected=option.dataset.option;renderDetail();}
    const def=e.target.closest('[data-defense-option]');if(def){defenseTarget=detail.defense.find(d=>d.player_id===defender).options[Number(def.dataset.defenseOption)].target;draw();}
  });
  for(const field of ['team','player','kind','scope'])$('#decision-'+field).onchange=()=>{request++;pageSize=30;renderList();};
  $('#decision-mode').onchange=()=>{defenseTarget=null;renderList();renderDetail();};
  $('#decision-more').onclick=()=>{pageSize+=30;renderList();};
  new ResizeObserver(()=>{if(shown)draw();}).observe(container);
  function clear(){revision++;request++;controller.abort();controller=new AbortController();context=null;index=detail=loading=null;cache.clear();$('#decision-summary').innerHTML='';$('#decision-list').innerHTML='';$('#decision-detail').innerHTML='<p class="empty">Select a covered decision.</p>';$('#decision-status').textContent='';$('#decision-coverage').textContent='';}
  return{clear,show(value){shown=value;if(value)load();},refreshReplayAvailability(){if(shown)renderDetail();},setMatch(value){clear();context=value;pageSize=30;$('#decision-team').innerHTML='<option value="">Both teams</option>'+value.metadata.teams.map((t,i)=>`<option value="${i}">${esc(t.name)}</option>`).join('');$('#decision-player').innerHTML='<option value="">All players</option>'+value.metadata.players.map(p=>`<option value="${p.player_id}">${esc(p.name)}</option>`).join('');load();}};
}
