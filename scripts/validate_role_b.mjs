// Reuse A's canonical schemas and validator; do not define a parallel contract.
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import zlib from 'node:zlib';
import {validate} from './validate_contracts.mjs';

const root=path.resolve(process.argv[2]||'data/league-enriched-gen-96d13a37');
const counts={metadata:0,snapshots:0,responses:0};
function check(schema,value,location){const result=validate(schema,value);if(!result.valid)throw Error(location+': '+JSON.stringify(result.errors));}
for(const match of fs.readdirSync(path.join(root,'matches')).sort()){
  const folder=path.join(root,'matches',match),metadata=JSON.parse(fs.readFileSync(path.join(folder,'metadata.json')));
  check('data.match',metadata,match);counts.metadata++;
  for await(const line of readline.createInterface({input:fs.createReadStream(path.join(folder,'snapshots.jsonl.gz')).pipe(zlib.createGunzip()),crlfDelay:Infinity})){
    const record=JSON.parse(line);check('contract.snapshot',record.snapshot,match+'/'+record.decision_id);counts.snapshots++;
  }
  const filename=path.join(folder,'scored.json.gz');
  if(fs.existsSync(filename)){
    for(const row of JSON.parse(zlib.gunzipSync(fs.readFileSync(filename)))){
      check('contract.analysis_response',row.response,match+'/'+row.decision_id);counts.responses++;
    }
  }
}
console.log(JSON.stringify({status:'PASS',dataset:root,counts},null,2));
