"""Self-contained file-only contract inputs, never evaluation/actor receipts.

Temporary graphs use the frozen schema/version identifiers for compatibility.
Their own hashes bind synthetic inputs, not historical native evidence. No
private sibling read, SQL connection, actor, server or child construction.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from .narrative_suite.data import InputCatalog, digest, require
from .narrative_suite.ledger import Ledger, validate_metrics
from .narrative_supplement.contract import Context as V3Context, file_hash, read_json
from .narrative_supplement.journal import Journal as V3Journal

ROOT=Path(__file__).resolve().parents[2]
PROJECTIONS='fixtures/upgrade/publication-contract-v5/reference-projections.json'
V3_SEAL='evaluation/upgrade/U2B2_SUPPLEMENT_SOURCE_MANIFEST.json'
V4_SEAL='evaluation/upgrade/U2B2_CONTINUATION_SOURCE_MANIFEST_V4.json'
POPULATION='public-contract-inputs/population.json'


def write(path,value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8',newline='\n')


def projections(source_root=ROOT):
    value=read_json(Path(source_root)/PROJECTIONS)
    require(value['schema']=='public-contract-projections-v5' and len(value['entries'])==3,'public_projection_invalid')
    require(all(e['is_full_receipt'] is False for e in value['entries']),'public_projection_invalid')
    checkpoint=read_json(Path(source_root)/'fixtures/upgrade/narrative-continuation-v4/partial-checkpoint.json')
    require([e['private_source_sha256'] for e in value['entries']]==[e['sha256'] for e in checkpoint['main_references']],'public_projection_provenance_changed')
    return [copy.deepcopy(e['data']) for e in value['entries']]


def pin(root,relative):
    path=Path(root)/relative
    return {'path':relative,'sha256':file_hash(path),'bytes':path.stat().st_size}


def repin_manifest(root,relative):
    value=read_json(root/relative)
    for group in ('files','preserved_files','correction_code_files'):
        if group in value:value[group]=[pin(root,e['path']) for e in value[group]]
    if 'code_sha256' in value:value['code_sha256']=digest(value['files'])
    if 'preserved_sha256' in value:value['preserved_sha256']=digest(value['preserved_files'])
    value['fixture_provenance']='synthetic_public_contract_graph_v5_not_native_evidence'
    write(root/relative,value)
    return value


def synthetic_metrics(outcome,error):
    value={key:None for key in ('model_tokens','actual_cost','estimated_cost','unsafe_attempts','unsafe_effects','semantic_correctness','coverage')}
    value.update(outcome=outcome,error=error,provider_requests=0,searches=0,reads=0,repairs=0,
        restarts=0,backend_search_calls=0,elapsed_seconds=0,owned_processes_remaining=0)
    validate_metrics(value); return value


def synthetic_journal(root,plan,terminal_count,states=None):
    """Construct closed test rows directly; does not run/replay any actor."""
    write(root/'plan.json',plan); plan_sha=file_hash(root/'plan.json'); rows=[]; previous='0'*64
    def row(event,sid,**details):
        nonlocal previous
        value={'sequence':len(rows)+1,'previous_sha256':previous,'plan_sha256':plan_sha,
            'timestamp_utc':'2026-10-13T00:00:00+00:00','event':event,'attempt_id':sid,**details}
        value['sha256']=digest(value); previous=value['sha256']; rows.append(value)
    for entry in plan['attempts']:row('INTENDED',entry['id'])
    for entry in plan['attempts'][:terminal_count]:
        status=(states or {}).get(entry['id'],{'outcome':'failed','error':'budget_exhausted'})
        row('STARTED',entry['id'])
        row('TERMINAL',entry['id'],metrics=synthetic_metrics(status['outcome'],status['error']),
            artifacts={k:digest({'fixture':'synthetic_contract_only','id':entry['id'],'kind':k}) for k in ('report_sha256','trace_sha256','corpus_sha256')})
    (root/'journal.jsonl').write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows),encoding='utf-8',newline='\n')
    return Ledger(root)


def build_contract_root(root,source_root=ROOT,*,include_v4=False):
    """Create a fresh, owned synthetic graph from repo-local source only."""
    root=Path(root).resolve(); source_root=Path(source_root).resolve()
    require(not root.exists() and not root.is_relative_to(source_root/'work/main-u2b2-1239') and not root.is_relative_to(source_root/'work/main-u2b2-supplement-v3'),'public_fixture_target_invalid')
    root.mkdir(parents=True)
    v4_seed=read_json(source_root/V4_SEAL)
    paths={e['path'] for e in v4_seed['files']+v4_seed['preserved_files']}|{V4_SEAL,'evaluation/upgrade/U2B2_CONTINUATION_COMMANDS_V4.json'}
    for relative in sorted(paths):
        path=Path(relative); require(not path.is_absolute() and (source_root/path).resolve().is_relative_to(source_root),'public_fixture_source_invalid')
        target=root/path; target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source_root/path,target)
    write(root/'SYNTHETIC_PROVENANCE.json',{'schema':'public-contract-inputs-v5','native_evidence':False,
        'actor_runs':0,'SQL_connections':0,'semantics':None,'purpose':'Temporary input graph for unmodified version/binding/refusal validators; not accepted main receipts or evaluation results.'})
    cp_path='fixtures/upgrade/narrative-correction-v2/parent-checkpoint.json'
    cp=read_json(root/cp_path,512*1024); cap_plan=cp['capability_plan']
    catalog=InputCatalog(root/'fixtures/upgrade/narrative-v2'); dev=[]
    from permissioned_research.upgrade.config import DispatchConfig, DOUBLE_MODEL
    for entry in catalog.freeze['entries']:
        if entry['split']!='development':continue
        task=catalog.load_input(entry['id'])
        for policy in ('adaptive','fixed'):
            dev.append({'ordinal':len(dev)+1,'id':f'a{len(dev)+1:03d}','task_id':entry['id'],
                'family':entry['family'],'principal':entry['principal'],'policy':policy,
                'input_sha256':entry['input_sha256'],'rubric_sha256':entry['rubric_sha256'],
                'config_sha256':DispatchConfig(DOUBLE_MODEL,**task['execution']['config_overrides']).sha256})
    parent=root/'work/main-u2b2-1239'
    cap=synthetic_journal(parent/'capability',cap_plan,45,cp['capability_states'])
    dev_ledger=synthetic_journal(parent/'development',{'schema':cap_plan['schema'],'split':'development','bindings':cap_plan['bindings'],'attempts':dev},24)
    # Needed by the existing raw-file drift contract; not a historical report.
    write(parent/'capability/a045/report.json',{'synthetic_contract_input':True,'native_evidence':False})
    write(root/POPULATION,{'schema':'synthetic_population_contract_v5','native_evidence':False,'semantic_quality':None})
    items=[pin(parent,p.relative_to(parent).as_posix()) for p in sorted(parent.rglob('*')) if p.is_file()]
    cp['parent_cohort_inventory']=items
    cp['bindings'].update(capability_plan_sha256=cap.plan_sha256,capability_journal_sha256=cap.inspect()['journal_sha256'],
        development_plan_sha256=dev_ledger.plan_sha256,development_journal_sha256=dev_ledger.inspect()['journal_sha256'],
        population_review_sha256=file_hash(root/POPULATION),parent_cohort_inventory_sha256=digest(items))
    write(root/cp_path,cp)
    correction=repin_manifest(root,'evaluation/upgrade/U2B2_CORRECTION_SOURCE_MANIFEST.json')
    proposal=read_json(root/'evaluation/upgrade/U2B2_CONTINUATION_V2.json')
    proposal['bindings']={**cp['bindings'],'correction_code_sha256':digest([{'path':e['path'],'sha256':file_hash(root/e['path'])} for e in correction['correction_code_files']])}
    proposal['parent_checkpoint_sha256']=digest(cp); write(root/'evaluation/upgrade/U2B2_CONTINUATION_V2.json',proposal)
    repin_manifest(root,'evaluation/upgrade/U2B2_CORRECTION_SOURCE_MANIFEST.json')
    repin_manifest(root,V3_SEAL)
    if not include_v4:return root
    context=V3Context(root,file_hash(root/V3_SEAL),root/POPULATION,'public-synthetic-v3')
    context.verify(); prior=V3Journal.create(root/'work/main-u2b2-supplement-v3',context.blueprint)
    # A file-only unknown fixture with durable ordering, never actor execution.
    with prior.writer():prior.start('s001')
    write(prior.root/'run.claim.json',{'schema':'narrative-supplement-claim-v3','plan_sha256':prior.plan_sha256,
        'execution_source_manifest_sha256':file_hash(root/V3_SEAL),'single_invocation':True})
    write(prior.root/'s001.execution.json',{'synthetic_contract_input':True,'native_evidence':False})
    (prior.root/'s001').mkdir(); (prior.root/'s001/corpus.sqlite').write_bytes(b'opaque synthetic contract input, not a SQLite database')
    (prior.root/'writer.lock').write_text('synthetic interrupted writer evidence\n',encoding='ascii')
    from .narrative_continuation_v4 import inventory
    snapshot=read_json(root/'fixtures/upgrade/narrative-continuation-v4/partial-checkpoint.json'); state=prior.inspect(); raw=inventory(prior.root)
    snapshot.update(v3_plan=prior.plan,v3_state={k:state[k] for k in ('counts','slots','intended_complete','torn_tail')},
        parent_source_manifest_sha256=file_hash(root/V3_SEAL),parent_command_manifest_sha256=file_hash(root/'evaluation/upgrade/U2B2_SUPPLEMENT_COMMANDS_V3.json'),
        parent_plan_sha256=prior.plan_sha256,parent_journal_sha256=state['journal_sha256'],partial_inventory=raw,partial_inventory_sha256=digest(raw))
    refs=projections(source_root)
    refs[0]['raw_partial_inventory']={e['path']:e['sha256'] for e in raw}
    refs[1]['supplement'].update(plan_sha256=prior.plan_sha256,journal_sha256=state['journal_sha256'])
    snapshot['main_references']=[]
    for index,value in enumerate(refs):
        value['synthetic_contract_input']=True; value['native_evidence']=False
        relative=f'public-contract-inputs/reference-{index}.json'; write(root/relative,value)
        snapshot['main_references'].append({'path':root.name+'/'+relative,'sha256':file_hash(root/relative),'bytes':(root/relative).stat().st_size})
    checkpoint_path='fixtures/upgrade/narrative-continuation-v4/partial-checkpoint.json'; write(root/checkpoint_path,snapshot)
    proposal4=read_json(root/'evaluation/upgrade/U2B2_CONTINUATION_V4.json')
    proposal4['checkpoint_file_sha256']=file_hash(root/checkpoint_path)
    proposal4['parent_bindings']={'v3_source_manifest_sha256':file_hash(root/V3_SEAL),
        'v3_command_manifest_sha256':file_hash(root/'evaluation/upgrade/U2B2_SUPPLEMENT_COMMANDS_V3.json'),
        'v3_plan_sha256':prior.plan_sha256,'v3_journal_sha256':state['journal_sha256'],'v3_inventory_sha256':digest(raw),
        'main_review_sha256':snapshot['main_references'][0]['sha256'],'main_read_only_report_sha256':snapshot['main_references'][1]['sha256'],
        'main_execution_summary_sha256':snapshot['main_references'][2]['sha256'],'original_parent_bindings_sha256':digest(prior.plan['parent_bindings'])}
    write(root/'evaluation/upgrade/U2B2_CONTINUATION_V4.json',proposal4)
    repin_manifest(root,V4_SEAL); return root


def main():
    parser=argparse.ArgumentParser(description='File-only synthetic public contract demo; not native evidence.')
    parser.add_argument('--summary',action='store_true',required=True); parser.parse_args()
    scratch=ROOT/'work'; scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='public-contract-v5-',dir=scratch) as temp:
        base=Path(temp).resolve(); require(base.is_relative_to(scratch.resolve()),'public_fixture_cleanup_invalid')
        root=build_contract_root(base/'checkout',ROOT,include_v4=True)
        from .narrative_continuation_v4 import Context
        context=Context(root,file_hash(root/V4_SEAL),root/POPULATION,'public-contract-v5-demo'); context.verify()
        print(json.dumps({'mode':'synthetic_file_contract_demo','v3_v4_binding_checks':True,'actor_runs':0,
            'historical_native_evidence_validated':False,'SQL_connections':0,'private_sibling_reads':0,'semantic_quality':None,'native_authorized':False},sort_keys=True))
        require(base.is_relative_to(scratch.resolve()),'public_fixture_cleanup_invalid')


if __name__=='__main__':main()
