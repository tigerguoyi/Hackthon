"""Local research CLI; numerical diagnostics are never an HTTP response."""
import argparse
import json

from .input_adapter import adapt_snapshot
from .q2 import evaluate
from .service import Repository, FIXTURES, read


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',choices=['a','b','c'],default='a')
    parser.add_argument('--match');parser.add_argument('--snapshot')
    args=parser.parse_args();repo=Repository()
    if args.match:
        record=repo.records(args.match).get(args.snapshot)
        if record is None:parser.error('Provide a registered --match and --snapshot')
        request=repo.request_for(record['snapshot'])
    else:request=read(FIXTURES/f'case-{args.case}/request.json')
    public,private=repo.compare(request)
    snapshot,metadata,ratings=repo.resolve(request);state=adapt_snapshot(snapshot,metadata,ratings)
    xt=repo.model_for(request['match_id'])['xt']
    private['risk_sensitivity']={str(scale):[{k:r.get(k) for k in ('key','q','risk','continuation')} for r in evaluate(state,xt,risk_scale=scale)] for scale in (.8,1.2)}
    private['sensitivity_note']='Parameter assumptions, not confidence intervals or football success validation'
    print(json.dumps(private,ensure_ascii=False,indent=2,allow_nan=False))


if __name__=='__main__':main()
