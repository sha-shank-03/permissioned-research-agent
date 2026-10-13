"""Bounded future CLI; no provider/model/output/attempt overrides."""
import argparse
import json

from ..narrative_suite.data import require
from .contract import Context, OUTPUT, ROOT
from .report import reconcile
from .runner import prepare, run


def main(argv=None):
    parser=argparse.ArgumentParser(description='Three-slot supplement; external main authorization and named lease required for prepare/run.')
    parser.add_argument('command',choices=('verify-source','prepare','run','report'))
    parser.add_argument('--execution-source-sha256',required=True)
    parser.add_argument('--population-review',required=True)
    parser.add_argument('--lease-id',required=True,help='Recorded label; does not acquire or authorize a lease.')
    parser.add_argument('--provider',choices=('gateway-double',))
    parser.add_argument('--model',choices=('test/synthetic-scalar',))
    args=parser.parse_args(argv)
    try:
        context=None
        try:context=Context(ROOT,args.execution_source_sha256,args.population_review,args.lease_id)
        except Exception:
            if args.command!='report':raise
        if args.command=='report':
            output=reconcile(ROOT/OUTPUT,args.execution_source_sha256,context=context); code=0
        else:
            context.verify()
            if args.command=='verify-source':
                output={'schema':'narrative-supplement-source-check-v3','source_parent_proposal_match':True,
                    'execution_source_manifest_sha256':args.execution_source_sha256,'native_runs':0,'dispatch_authorized':False}; code=0
            else:
                require(args.provider=='gateway-double' and args.model=='test/synthetic-scalar','supplement_local_route_required')
                if args.command=='prepare':prepare(context); code=0
                else:code=run(context)
                output=reconcile(context.out,args.execution_source_sha256,context=context)
        print(json.dumps(output,sort_keys=True,allow_nan=False)); return code
    except Exception:
        print(json.dumps({'schema':'narrative-supplement-command-error-v3','error':'supplement_command_refused','dispatch_authorized':False},sort_keys=True)); return 2


if __name__=='__main__':raise SystemExit(main())
