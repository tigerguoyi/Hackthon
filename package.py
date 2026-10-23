#!/usr/bin/env python3
"""Extract a preview, hash outputs, and ZIP the deliverable using stdlib."""
import hashlib
import json
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'
summary=json.loads((DATA/'summary.json').read_text())
goals=summary['goals']
center=goals[-1]['t_ms'] if goals else 120000
start=max(0,center-45000)
end=min(6000000,start+60000)
frames=[]
with (DATA/'tracking.jsonl').open() as f:
    for line in f:
        item=json.loads(line)
        if start<=item['t_ms']<=end:
            frames.append(item)
events=[json.loads(line) for line in (DATA/'events.jsonl').open()]
preview={'match_id':summary['match_id'],'start_ms':start,'end_ms':end,'tracking_hz':5,
         'metadata':json.loads((DATA/'metadata.json').read_text()),
         'frames':frames,'events':[e for e in events if start<=e['available_at_ms']<=end]}
(DATA/'preview-60s.json').write_text(json.dumps(preview,ensure_ascii=False,separators=(',',':'))+'\n')
paths=[*sorted(DATA.glob('*.json')),*sorted(DATA.glob('*.jsonl')),
       ROOT/'README.md', ROOT/'generate.py',ROOT/'validate.py',ROOT/'validate_league.py',
       ROOT/'package.py',ROOT/'schema.json',ROOT/'analysis_agent.py',ROOT/'model_service.py']
manifest={'match_id':summary['match_id'],'files':[]}
for p in paths:
    content=p.read_bytes()
    manifest['files'].append({'path':p.relative_to(ROOT).as_posix(),'bytes':len(content),
                              'sha256':hashlib.sha256(content).hexdigest()})
manifest['total_bytes_excluding_manifest']=sum(f['bytes'] for f in manifest['files'])
(ROOT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
archive=ROOT.parent/'football-simulation.zip'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in paths+[ROOT/'manifest.json']:
        z.write(p,'football-simulation/'+p.relative_to(ROOT).as_posix())
print(json.dumps({'archive':str(archive),'archive_bytes':archive.stat().st_size,
                  'uncompressed_bytes':manifest['total_bytes_excluding_manifest'],
                  'preview_frames':len(frames),'preview_start_ms':start,'preview_end_ms':end},indent=2))
