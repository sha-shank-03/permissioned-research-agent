"""Explicit future plan/native/after-run operations; no default task execution."""
import argparse
import json
from pathlib import Path
from .data import InputCatalog, SuiteError
from .ledger import Ledger


def main(argv=None):
    parser=argparse.ArgumentParser(description='Future local-only narrative evaluation; source/pure work is not a native lease.')
    sub=parser.add_subparsers(dest='command',required=True)
    plan=sub.add_parser('plan'); plan.add_argument('--split',choices=['development','capability'],required=True)
    plan.add_argument('--provider',choices=['gateway-double'],required=True); plan.add_argument('--model',choices=['test/synthetic-scalar'],required=True)
    for command in (plan,):command.add_argument('--out',type=Path,required=True)
    run=sub.add_parser('run-slice'); run.add_argument('--out',type=Path,required=True)
    run.add_argument('--start',type=int,required=True); run.add_argument('--count',type=int,choices=[1,2,3,4],required=True)
    run.add_argument('--slice-seconds',type=int,choices=[520],required=True)
    inspect=sub.add_parser('inspect-slice'); inspect.add_argument('--out',type=Path,required=True)
    inspect.add_argument('--start',type=int,required=True); inspect.add_argument('--count',type=int,choices=[1,2,3,4],required=True)
    inspect.add_argument('--evaluation-out',type=Path,required=True); inspect.add_argument('--manual-dir',type=Path)
    account=sub.add_parser('account'); account.add_argument('--out',type=Path,required=True)
    sub.add_parser('verify-source')
    args=parser.parse_args(argv)
    try:
        if args.command=='account':
            state=Ledger(args.out).inspect(); result={k:state[k] for k in ('intended','counts','torn_tail','plan_sha256','journal_sha256')}
        elif args.command=='inspect-slice':
            from .evaluate import inspect_slice
            result=inspect_slice(Ledger(args.out),InputCatalog(),start=args.start,count=args.count,output=args.evaluation_out,manual_dir=args.manual_dir)
        else:
            from .runner import intended_plan,public_account,run_slice,source_identity
            catalog=InputCatalog()
            if args.command=='verify-source':result={'source_manifest_sha256':source_identity(),'actor_or_native_runs':0,'status':'source_hashes_verified'}
            elif args.command=='plan':result=public_account(Ledger.create(args.out,intended_plan(catalog,args.split,source_identity())))
            else:result=run_slice(Ledger(args.out),catalog,start=args.start,count=args.count,slice_seconds=args.slice_seconds)
        print(json.dumps(result,allow_nan=False)); return 0
    except (SuiteError,OSError,ValueError,KeyError,TypeError):
        # Keep raw artifacts/journal intact. No exception string or source text.
        print(json.dumps({'status':'stopped','error':'suite_operation_failed','rerun_started_attempts':False})); return 2


if __name__=='__main__':raise SystemExit(main())
