"""After-run inspection CLI backend. No actor/provider/guard imports."""
import hashlib
from contextlib import closing
from pathlib import Path
import sqlite3
from .data import digest, load_rubric, require, unique_json
from .inspect import inspect_attempt
from .ledger import Ledger, write_exclusive
from .snapshot import raw_snapshot


def comparison_summary(rows):
    """Closed metrics over every intended row; incomplete pairs stay visible."""
    require(len(rows) in (24,48),'comparison_denominator_invalid')
    reviewed=[r for r in rows if r['inspection'] and r['inspection']['semantic_review_status']=='manual_operator_attestation']
    pairs=[]
    for task_id in {r['task_id'] for r in rows}:
        pair={r['policy']:r['inspection'] for r in reviewed if r['task_id']==task_id}
        if set(pair)=={'adaptive','fixed'} and all(pair[p]['coverage'] is not None for p in pair):pairs.append(pair['adaptive']['coverage']-pair['fixed']['coverage'])
    operational={}
    for policy in ('adaptive','fixed'):
        group=[r for r in rows if r['policy']==policy]; terminal=[r for r in group if r['accounting_state']=='terminal']
        operational[policy]={'intended':len(group),'terminal':len(terminal),
                             'interrupted_unknown':sum(r['accounting_state']=='interrupted_unknown' for r in group),
                             'unexecuted':sum(r['accounting_state']=='unexecuted' for r in group),
                             'needs_review':sum(r['operational_metrics']['outcome']=='needs_review' for r in terminal),
                             'failed':sum(r['operational_metrics']['outcome']=='failed' for r in terminal),
                             'cancelled':sum(r['operational_metrics']['outcome']=='cancelled' for r in terminal),
                             'provider_request_total_terminal_only':sum(r['operational_metrics']['provider_requests'] for r in terminal),
                             'actual_cost':None,'estimated_cost':None,'semantic_correctness':None,'coverage':None}
    return {'manual_reviewed_attempts':len(reviewed),'operational_by_policy':operational,'manually_reviewed_pairs':len(pairs),
            'observed_pair_coverage_delta':sum(pairs)/len(pairs) if pairs else None,
            'paired_quality_delta':sum(pairs)/len(pairs) if len(pairs)==len(rows)//2 else None}


def inspect_slice(ledger,catalog,*,start,count,output,manual_dir=None):
    require(type(start) is int and type(count) is int and 1<=count<=4 and 1<=start<=len(ledger.plan['attempts']) and start+count-1<=len(ledger.plan['attempts']),'inspection_slice_invalid')
    state=ledger.inspect(); selected={a['id'] for a in ledger.plan['attempts'][start-1:start-1+count]}; rows=[]
    require(ledger.plan['bindings']['pack_sha256']==catalog.freeze_sha256,'inspection_binding_invalid')
    for attempt in ledger.plan['attempts']:
        aid=attempt['id']; accounting=state['attempts'][aid]
        row={**attempt,'accounting_state':accounting['state'],'operational_metrics':accounting['metrics'],
             'inspection_status':'unexecuted','inspection':None}
        if aid in selected and accounting['state']=='terminal':
            root=ledger.root/aid; artifacts=accounting['artifacts']
            for filename,key in (('report.json','report_sha256'),('trace.json','trace_sha256'),('corpus.sqlite','corpus_sha256')):
                require(hashlib.sha256((root/filename).read_bytes()).hexdigest()==artifacts[key],'inspection_artifact_changed')
            task=catalog.load_input(attempt['task_id']); rubric=load_rubric(catalog,attempt['task_id'])
            require(attempt['input_sha256']==catalog.entries[task['id']]['input_sha256'] and attempt['rubric_sha256']==catalog.entries[task['id']]['rubric_sha256'],'inspection_binding_invalid')
            report=unique_json((root/'report.json').read_text(encoding='utf-8')); trace=unique_json((root/'trace.json').read_text(encoding='utf-8'))
            require(report['policy']==attempt['policy'] and report['bindings']==ledger.plan['bindings'] and report['config_sha256']==attempt['config_sha256'],'inspection_binding_invalid')
            # SQLite is opened read-only and queried with fixed SQL. No actor
            # fixture/gold helper is called to obtain access or source truth.
            uri=(root/'corpus.sqlite').resolve().as_uri()+'?mode=ro'
            with closing(sqlite3.connect(uri,uri=True)) as connection:snapshot=raw_snapshot(connection,task['principal'],task['fixture']['as_of'])
            require(snapshot==trace['final_snapshot'],'inspection_snapshot_changed')
            manual=None
            if manual_dir is not None and (Path(manual_dir)/(aid+'.json')).is_file():
                raw=(Path(manual_dir)/(aid+'.json')).read_bytes(); require(len(raw)<=128*1024,'manual_review_invalid'); manual=unique_json(raw.decode())
            row['inspection']=inspect_attempt(task,rubric,report,snapshot,trace,manual); row['inspection_status']='inspected'
        rows.append(row)
    comparison=comparison_summary(rows)
    result={'schema':'narrative-evaluation-slice-v1','intended_attempts':len(rows),'unique_tasks':len({r['task_id'] for r in rows}),
            'accounting_counts':state['counts'],'inspection_counts':{'inspected':sum(r['inspection_status']=='inspected' for r in rows),'unexecuted':sum(r['inspection_status']=='unexecuted' for r in rows)},
            **comparison,'rows':rows,'plan_sha256':state['plan_sha256'],'journal_sha256':state['journal_sha256'],
            'exposed_same_author_data':True,'blind_or_main_held_unseen':False,'semantic_correctness':None,'coverage':None,
            'repeats_are_independent_samples':False,'live_model_executed':False,'remote_authorization_proven':False}
    write_exclusive(output,result)
    return {'intended':len(rows),'accounting':state['counts'],'inspected':result['inspection_counts']['inspected'],'manual_reviewed':comparison['manual_reviewed_attempts'],'semantic_correctness':None,'coverage':None}
