import {formatClock} from './core.js?v=2.2.0';

const $=selector=>document.querySelector(selector);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
let catalog,bundle,response,routes=[],selected=null,lastRequest=null,rollout=null,epoch=0,controller=new AbortController(),animation=null;
const playerName=id=>bundle?.metadata.players.find(p=>p.player_id===id)?.name||id;
const actionLabel=c=>c.type==='pass'?`Pass to ${playerName(c.action_id.split(':')[1])}`:c.type==='carry'?`Carry ${c.action_id.split(':')[1]}`:c.type==='shot'?'Shoot':'Hold briefly';
const clock=snapshot=>formatClock(snapshot.t_ms,bundle.metadata,snapshot.period);

async function get(url){const r=await fetch(url,{signal:controller.signal});const data=await r.json();if(!r.ok)throw Error(data.error||'Source unavailable');return data;}
async function post(url,data){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data),signal:controller.signal});const value=await r.json();if(!r.ok)throw Error(value.error||'Comparison unavailable');return value;}
function stop(){if(animation!==null)cancelAnimationFrame(animation);animation=null;$('#play').textContent='Play hypothesis';}
function resetPlayback(){stop();rollout=null;$('#scenario').replaceChildren();for(const id of ['scenario','play','reset','hypothesis-time'])$('#'+id).disabled=true;$('#hypothesis-time').value=0;$('#rollout-status').textContent='Choose an eligible ground pass after comparison.';}
function invalidate(){
  epoch++;controller.abort();controller=new AbortController();resetPlayback();response=null;lastRequest=null;routes=[];selected=null;
  $('#candidates').replaceChildren();$('#evidence').textContent='No computed evidence yet.';$('#selected').textContent='Compare actions for this snapshot.';
  $('#model-status').textContent='Not computed';$('#actual-status').textContent='Historical choice is hidden. It is matched only after comparison.';
  $('#identity').textContent='';for(const id of ['actual','export','rollout'])$('#'+id).disabled=true;
}
function snapshotReady(next){
  bundle=next;$('#analyze').disabled=false;$('#question').value=bundle.request.question;
  $('#clock').textContent=clock(bundle.snapshot);draw();conditions();$('#status').textContent='Observed snapshot ready. Compare the available actions.';
}
function conditions(){
  const f=bundle.snapshot.frame,p=f.players.find(p=>p[0]===f.ball_owner_id),teams=new Map(bundle.metadata.players.map(p=>[p.player_id,p.team_id]));
  if(!p){$('#conditions').textContent='Current ball owner unavailable.';return;}
  const opponents=f.players.filter(q=>teams.get(q[0])!==teams.get(p[0])).map(q=>({id:q[0],d:Math.hypot(q[1]-p[1],q[2]-p[2])})).sort((a,b)=>a.d-b.d);
  const speed=Math.hypot(p[3],p[4]),heading=speed>.2?(Math.atan2(p[4],p[3])*180/Math.PI+360)%360:null;
  const facts=[['Movement speed',speed.toFixed(2)+' m/s',(speed*3.6).toFixed(1)+' km/h'],
    ['Movement direction',heading===null?'Stationary / slow':heading.toFixed(1)+'°','Velocity, not body orientation'],
    ['Nearest defender',opponents[0]?opponents[0].d.toFixed(2)+' m':'Unknown',playerName(opponents[0]?.id)],
    ['Opponents within 5 m',opponents.filter(p=>p.d<5).length,'Including the goalkeeper'],
    ['Distance to ball',Math.hypot(f.ball[0]-p[1],f.ball[1]-p[2]).toFixed(2)+' m','Observed positions'],
    ['Attacking direction',bundle.snapshot.attacking_direction[teams.get(p[0])]==='positive_x'?'→ +x':'← −x','Explicit snapshot direction']];
  $('#conditions').innerHTML=facts.map(([name,value,note])=>`<div><small>${esc(name)}</small><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`).join('');
  const profile=bundle.source_profile,observation=profile.observation;
  $('#observed-profile').innerHTML=`<p>Status: ${esc(profile.status)}. Source attributes do not change execution probabilities.</p>${profile.issues.map(i=>`<p>${esc(i)}</p>`).join('')}${observation?`<p>${esc(observation.source)} · ${esc(observation.collected_at_utc)} · ${esc(observation.attribute_kind)}</p><dl>${Object.entries(observation.attributes||{}).map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v??'Unknown')}</dd></div>`).join('')}</dl><p>Height: ${esc(observation.height_cm??'Unknown')} cm · weight: ${esc(observation.weight_kg??'Unknown')} kg · preferred foot: ${esc(observation.preferred_foot??'Unknown')}</p>`:''}<p>Source collection time is not historical pre-match evidence. Simulated foot settings never overwrite this observation.</p>`;
}
function draw(frame=null){
  if(!bundle)return;
  const observed=bundle.snapshot.frame,f=frame||observed;
  $('#pitch-title').textContent=frame?'Counterfactual playback · separate timeline':'Observed decision-time snapshot';
  let svg='<rect x="0" y="0" width="105" height="68" fill="none" stroke="#a9bba0" stroke-width=".3"/><path d="M52.5 0V68 M0 14H16.5V54H0 M105 14H88.5V54H105" fill="none" stroke="#a9bba0" stroke-width=".3"/><circle cx="52.5" cy="34" r="9.15" fill="none" stroke="#a9bba0" stroke-width=".3"/>';
  const route=routes.find(r=>r.action_id===selected);
  if(route)svg+=`<path d="M${route.start.join(' ')} L${route.target.join(' ')}" stroke="#c58b42" stroke-width=".7" stroke-dasharray="1.6 1" fill="none"/>`;
  const teams=new Map(bundle.metadata.players.map(p=>[p.player_id,p.team_id]));
  for(const p of f.players){svg+=`<circle cx="${p[1]}" cy="${p[2]}" r="1.3" fill="${teams.get(p[0])==='HOME'?'#527ca6':'#bf7d50'}" stroke="${p[0]===observed.ball_owner_id?'#263c31':'none'}" stroke-width=".4"/><text x="${p[1]}" y="${p[2]-2}" fill="#51634b" font-size="2" text-anchor="middle">${esc(p[0])}</text>`;}
  svg+=`<circle cx="${f.ball[0]}" cy="${f.ball[1]}" r=".65" fill="#202c23"/>`;
  $('#pitch').innerHTML=svg;
}
function render(){
  $('#model-status').textContent=response.status==='model_unavailable'?'Computed · model agents not connected':response.status;
  $('#candidates').innerHTML=response.candidates.map(c=>`<tr class="${c.action_id===selected?'selected':''}" aria-disabled="${!c.eligible}"><td>${c.rank??'—'}</td><td><button type="button" data-action="${esc(c.action_id)}">${esc(actionLabel(c))}<small>${c.is_actual?'Historical type / recipient · ':''}${c.eligible?'Supported':'Unsupported'}</small></button></td><td>${esc(c.opportunity)}</td><td>${esc(c.main_risk)}</td></tr>`).join('');
  const candidate=response.candidates.find(c=>c.action_id===selected);
  $('#rollout').disabled=!candidate?.rollout_supported;
  $('#selected').innerHTML=candidate?`<strong>${esc(actionLabel(candidate))}</strong><br>${esc(candidate.opportunity)}<br>${esc(candidate.main_risk)}<br><small>Assumptions: ${candidate.assumptions.map(esc).join(' · ')}</small>`:'No supported candidate.';
  const cited=new Set(candidate?.evidence_ids||[]);
  $('#evidence').innerHTML=response.evidence.filter(e=>cited.has(e.evidence_id)).map(e=>`<p><strong>${esc(e.evidence_id)} · ${esc(e.kind)}</strong><br>${esc(e.value)}<br><small>${esc(e.source)}</small></p>`).join('');
  $('#identity').textContent=JSON.stringify({snapshot_id:response.snapshot_id,candidate_set_id:response.candidate_set_id,data_version:response.data_version,
    profile_version:response.profile_version,status:response.status,agent_trace:response.agent_trace,limitations:response.limitations},null,2);
  draw();
}
async function sourceChanged(){
  invalidate();bundle=null;$('#analyze').disabled=true;$('#conditions').replaceChildren();$('#status').textContent='Loading an observed snapshot…';
  const stamp=epoch,value=$('#source').value;$('#decision-field').hidden=!value.startsWith('match:');
  try{
    if(value.startsWith('case:'))snapshotReady(catalog.cases.find(c=>c.case_id===value.slice(5)));
    else{
      const match=value.slice(6),index=await get('/api/role-b/decisions?match_id='+encodeURIComponent(match));if(stamp!==epoch)return;
      const rows=index.records.filter(r=>r.coverage==='exact');
      $('#decision').innerHTML=rows.map(r=>`<option value="${esc(r.snapshot_id)}">${(r.t_ms/1000).toFixed(1)} s · ${esc(r.actor_id)} · ${esc(r.decision_id)}</option>`).join('');
      if(!rows.length)throw Error('No exact decision-time snapshots in this match.');
      const next=await get('/api/role-b/snapshot?match_id='+encodeURIComponent(match)+'&snapshot_id='+rows[0].snapshot_id);if(stamp===epoch)snapshotReady(next);
    }
  }catch(e){if(stamp===epoch&&e.name!=='AbortError')$('#status').textContent=e.message;}
}
$('#source').onchange=sourceChanged;
$('#decision').onchange=async()=>{
  invalidate();$('#analyze').disabled=true;const stamp=epoch;
  try{const next=await get('/api/role-b/snapshot?match_id='+encodeURIComponent($('#source').value.slice(6))+'&snapshot_id='+$('#decision').value);if(stamp===epoch)snapshotReady(next);}
  catch(e){if(stamp===epoch&&e.name!=='AbortError')$('#status').textContent=e.message;}
};
for(const id of ['mode','foot','weak-foot','question'])$('#'+id).onchange=()=>{invalidate();if(bundle)draw();$('#status').textContent='Settings changed; recompute before using a route or explanation.';};
$('#analysis-form').onsubmit=async event=>{
  event.preventDefault();if(!bundle)return;invalidate();const stamp=epoch;$('#analyze').disabled=true;$('#status').textContent='Computing the isolated B comparison…';
  const request={...bundle.request,question:$('#question').value,mode:$('#mode').value,language:'en',profile:{profile_version:'sim-1',kind:'simulated',preferred_foot:$('#foot').value||null,weak_foot:$('#weak-foot').value?Number($('#weak-foot').value):null}};
  try{
    const computed=await post('/api/role-b/analysis',request);if(stamp!==epoch)return;
    if(computed.snapshot_id!==bundle.snapshot.snapshot_id||computed.data_version!==bundle.snapshot.data_version)throw Error('Stale comparison discarded; retry.');
    const geometry=await post('/api/role-b/routes',{request,candidate_set_id:computed.candidate_set_id});if(stamp!==epoch)return;
    if(geometry.candidate_set_id!==computed.candidate_set_id)throw Error('Stale routes discarded; retry.');
    response=computed;lastRequest=request;routes=geometry.routes;selected=response.candidates.find(c=>c.rank===1)?.action_id;
    render();$('#actual').disabled=!response.candidates.length;$('#export').disabled=false;$('#status').textContent='Comparison computed. Ranking and textual evidence remain available without model agents.';
  }catch(e){if(stamp===epoch&&e.name!=='AbortError')$('#status').textContent=e.message;}
  finally{if(stamp===epoch)$('#analyze').disabled=false;}
};
$('#candidates').onclick=event=>{const button=event.target.closest('[data-action]');if(!button||!response)return;resetPlayback();selected=button.dataset.action;render();};
$('#actual').onclick=async()=>{
  if(!response)return;const stamp=epoch,cid=response.candidate_set_id;
  try{const value=await post('/api/role-b/actual',{request:lastRequest,candidate_set_id:cid});if(stamp!==epoch)return;
    if(value.response.candidate_set_id!==cid)throw Error('Stale historical overlay discarded.');response=value.response;render();$('#actual-status').textContent=value.actual_action_id?'Historical choice: '+actionLabel(response.candidates.find(c=>c.action_id===value.actual_action_id))+'. '+value.resolution:value.resolution;
  }catch(e){if(stamp===epoch&&e.name!=='AbortError')$('#actual-status').textContent=e.message;}
};
function showFrame(){if(!rollout)return;const scenario=rollout.scenarios[Number($('#scenario').value)],frame=scenario.frames[Number($('#hypothesis-time').value)];draw(frame);$('#rollout-status').textContent=`Hypothesis ${scenario.id} · ${frame.elapsed_s.toFixed(2)} s · terminal result: ${scenario.outcome}. Scenario counts are not probabilities.`;}
$('#rollout').onclick=async()=>{
  if(!response)return;resetPlayback();const stamp=epoch,cid=response.candidate_set_id,action=selected;$('#rollout').disabled=true;
  try{const value=await post('/api/role-b/rollout',{request:lastRequest,candidate_set_id:cid,action_id:action});if(stamp!==epoch||selected!==action)return;
    if(value.candidate_set_id!==cid||value.snapshot_id!==bundle.snapshot.snapshot_id)throw Error('Stale hypothesis discarded.');
    rollout=value;$('#scenario').innerHTML=value.scenarios.map((s,i)=>`<option value="${i}">${esc(s.id)} · ${s.reaction_s} s reaction</option>`).join('');
    for(const id of ['scenario','play','reset','hypothesis-time'])$('#'+id).disabled=false;
    $('#hypothesis-time').max=value.scenarios[0].frames.length-1;$('#hypothesis-time').value=0;showFrame();
  }catch(e){if(stamp===epoch&&e.name!=='AbortError')$('#rollout-status').textContent=e.message;}
  finally{if(stamp===epoch)$('#rollout').disabled=!response?.candidates.find(c=>c.action_id===selected)?.rollout_supported;}
};
$('#scenario').onchange=()=>{stop();$('#hypothesis-time').max=rollout.scenarios[Number($('#scenario').value)].frames.length-1;$('#hypothesis-time').value=0;showFrame();};
$('#hypothesis-time').oninput=()=>{stop();showFrame();};
$('#play').onclick=()=>{if(!rollout)return;if(animation!==null){stop();return;}$('#play').textContent='Pause hypothesis';if(Number($('#hypothesis-time').value)>=Number($('#hypothesis-time').max))$('#hypothesis-time').value=0;const start=performance.now()-Number($('#hypothesis-time').value)*50;
  const tick=now=>{const max=Number($('#hypothesis-time').max),next=Math.min(max,Math.floor((now-start)/50));$('#hypothesis-time').value=next;showFrame();if(next<max)animation=requestAnimationFrame(tick);else{stop();$('#hypothesis-time').value=max;}};
  animation=requestAnimationFrame(tick);
};
$('#reset').onclick=()=>{stop();$('#hypothesis-time').value=0;draw();$('#rollout-status').textContent='Restored the observed snapshot. Hypothesis frames did not change the match.';};
$('#export').onclick=()=>{if(!response)return;const url=URL.createObjectURL(new Blob([JSON.stringify(response,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=response.candidate_set_id.replace(':','-')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
try{
  catalog=await get('/api/role-b/catalog');$('#source').innerHTML=catalog.cases.map(c=>`<option value="case:${c.case_id}">${esc(c.label)}</option>`).join('')+catalog.matches.map(m=>`<option value="match:${esc(m.match_id)}">${esc(m.match_id)} · ${m.covered} snapshots</option>`).join('');
  $('#source-summary').textContent=`Pinned A ${catalog.source_commit.slice(0,12)} · ${catalog.matches.length} matches · ${catalog.totals.covered.toLocaleString()} generator decision snapshots. Only four matches have complete animation; decision snapshots cover every indexed choice.`;
  $('#source').disabled=false;await sourceChanged();
}catch(e){$('#status').textContent=e.message;}
