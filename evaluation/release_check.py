"""Static shipping inventory and explicitly requested clean-copy execution."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from .suite import ROOT, verify_freezes, validate_rubrics, digest

DIRECTORIES=('permissioned_research','evaluation','fixtures','tests','docs','.github')
FILES=('README.md','LICENSE','DATA_LICENSE.md','THIRD_PARTY_NOTICES.md','EVIDENCE.md','.gitignore')


def shipping_files(root=ROOT):
    paths=[root/name for name in FILES]
    for name in DIRECTORIES:
        paths.extend(p for p in (root/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo'))
    if any(p.is_symlink() or root.resolve() not in p.resolve().parents for p in paths): raise ValueError('unsafe_shipping_path')
    if len({p.relative_to(root).as_posix().casefold() for p in paths})!=len(paths): raise ValueError('case_collision')
    return sorted(paths,key=lambda p:p.relative_to(root).as_posix())


def static_check():
    paths=shipping_files(); parsed=0
    for path in paths:
        raw=path.read_text(encoding='utf-8')
        if re.search(r'(?i)[a-z]:[\\/]+Users[\\/]|/(?:Users|home)/[^ /]+/',raw): raise ValueError('personal_absolute_path')
        if re.search(r'(?i)sk-proj-[a-z0-9]{12,}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----',raw): raise ValueError('credential_like_material')
        if path.suffix=='.py': ast.parse(raw,filename=path.name); parsed+=1
        if path.suffix=='.json': json.loads(raw)
    return {'shipping_files':len(paths),'bytes':sum(p.stat().st_size for p in paths),'python_parsed':parsed,
            'frozen_files_checked':verify_freezes(),'rubrics':validate_rubrics(),'actor_executions':0}


def run_clean_copy():
    if sys.version_info[:2]!=(3,11): raise ValueError('python_minor')
    static=static_check(); work=(ROOT/'work').resolve(); work.mkdir(exist_ok=True)
    receipts=[]
    # The cleanup target is an owned random child checked inside this workspace.
    with tempfile.TemporaryDirectory(prefix='release-',dir=work) as temporary:
        destination=Path(temporary).resolve()
        if work not in destination.parents: raise ValueError('unsafe_cleanup_target')
        for path in shipping_files():
            relative=path.relative_to(ROOT); target=destination/relative
            target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(path,target)
            if digest(path)!=digest(target): raise ValueError('copy_hash_mismatch')
        env=os.environ.copy(); env.pop('PYTHONPATH',None); env['PYTHONNOUSERSITE']='1'
        commands=[['-m','permissioned_research','doctor'],
                  ['-m','permissioned_research','demo','--scenario','vendor-review','--principal','priya','--provider','scripted','--state-dir','.local/release-demo','--format','markdown'],
                  ['-m','evaluation.evaluate','--verify-freeze'],
                  ['-m','evaluation.evaluate','--verify-corruptions','--output','.local/release-controls.json']]
        for args in commands:
            completed=subprocess.run([sys.executable,*args],cwd=destination,env=env,text=True,capture_output=True,timeout=120)
            # Raw output remains in memory; receipt never retains source/memo text.
            receipts.append({'arguments':args,'exit_code':completed.returncode,'stdout_bytes':len(completed.stdout.encode()),'stderr_bytes':len(completed.stderr.encode())})
            if completed.returncode: raise RuntimeError('clean_copy_command_failed')
    return {'static':static,'clean_copy_commands':receipts,'owned_directory_removed':not destination.exists(),
            'heldout_case_runs':0,'live_model_executed':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Standalone release inventory; --run executes owned clean-copy commands.')
    parser.add_argument('--run',action='store_true'); parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    report=run_clean_copy() if args.run else static_check()
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2)); return 0


if __name__=='__main__': raise SystemExit(main())
