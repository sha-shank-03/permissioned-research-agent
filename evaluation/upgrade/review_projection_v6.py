"""Read-only structural checks and aggregation of already-recorded main ratings.

No rating/scoring implementation, actor, SQL, transport or private file access.
Digest references are bindings, not authenticated reviewer identity/signatures.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
PROJECTION=ROOT/'evaluation/upgrade/U2B2_LOCAL_REVIEW_PROJECTION_V6.json'
POPULATIONS=('development','capability')
POLICIES=('adaptive','fixed')


def require(condition,code):
    if not condition:raise ValueError(code)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def sha(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


def load(path):
    def pairs(items):
        result={}
        for key,value in items:
            require(key not in result,'projection_duplicate_key'); result[key]=value
        return result
    raw=Path(path).read_bytes(); require(len(raw)<=1024*1024,'projection_file_bound')
    return json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('projection_nonfinite')))


def quality(rows):
    reviews=[r['main_review'] for r in rows if r['main_review'] is not None]
    claims=sum(r['metrics']['claims'] for r in reviews)
    return {'reviewed_drafts':len(reviews),'claims':claims,
        'eligible_relationships':sum(r['metrics']['relationships'] for r in reviews),
        'topic_decisions':len(reviews)*7,
        'mean_coverage_reviewed_drafts':sum(r['metrics']['coverage'] for r in reviews)/len(reviews) if reviews else None,
        'mean_semantic_correctness_reviewed_drafts':sum(r['metrics']['semantic_correctness'] for r in reviews)/len(reviews) if reviews else None,
        'main_reviewed_narrative_completions':sum(r['metrics']['task_completion'] is True for r in reviews),
        'scope':'existing main Codex judgments on emitted drafts only; excludes no-draft quality',
        'reviewer_agreement':None,'human_approval':False}


def group_summary(rows):
    terminal=[r for r in rows if r['state']=='terminal']; unknown=sum(r['state']=='interrupted_unknown' for r in rows)
    q=quality(rows); successes=q['main_reviewed_narrative_completions']; requests=sum(r['provider_requests'] for r in terminal)
    return {'intended':len(rows),'known_terminal':len(terminal),'interrupted_unknown':unknown,
        'operational_outcomes':dict(Counter(r['outcome'] for r in terminal)),
        'reviewed_narrative_completions':successes,'non_narrative_terminal':len(terminal)-successes,
        'known_completion_fraction_of_intended':successes/len(rows) if rows else None,
        'completion_fraction_known_terminals':successes/len(terminal) if terminal else None,
        'complete_logical_completion_fraction':successes/len(rows) if rows and not unknown else None,
        'provider_requests_known_terminals':requests,'provider_requests_complete_logical':requests if not unknown else None,
        'actual_cost':None,'estimated_cost':None,'model_tokens':None,'quality_conditional_on_reviewed_drafts':q}


def summarize(value):
    rows=value['rows']; scopes={}
    for pop in POPULATIONS:
        part=[r for r in rows if r['population']==pop]
        scopes[pop]={**group_summary(part),'by_policy':{p:group_summary([r for r in part if r['policy']==p]) for p in POLICIES}}
    terminal=[r for r in rows if r['state']=='terminal']
    scopes['known_terminal']=group_summary(terminal)
    scopes['complete_logical']={**group_summary(rows),'by_policy':{p:group_summary([r for r in rows if r['policy']==p]) for p in POLICIES}}
    task_pairs=[]
    for task in value['tasks']:
        pair={r['policy']:r for r in rows if r['population']==task['population'] and r['task_id']==task['task_id']}
        reviews=all(pair[p]['main_review'] is not None for p in POLICIES)
        task_pairs.append({'population':task['population'],'task_id':task['task_id'],
            'adaptive_slot':pair['adaptive']['slot'],'fixed_slot':pair['fixed']['slot'],
            'both_terminal':all(pair[p]['state']=='terminal' for p in POLICIES),'both_reviewed_drafts':reviews,
            'observed_reviewed_pair_coverage_delta':pair['adaptive']['main_review']['metrics']['coverage']-pair['fixed']['main_review']['metrics']['coverage'] if reviews else None,
            'narrative_completions':{p:pair[p]['main_review']['metrics']['task_completion'] if pair[p]['main_review'] else (None if pair[p]['state']=='interrupted_unknown' else False) for p in POLICIES}})
    deltas=[p['observed_reviewed_pair_coverage_delta'] for p in task_pairs if p['both_reviewed_drafts']]
    return {'scopes':scopes,'matched_pairs':{'planned_tasks':36,'terminal_pairs':sum(p['both_terminal'] for p in task_pairs),
        'reviewed_draft_pairs':len(deltas),'observed_reviewed_pair_coverage_delta':sum(deltas)/len(deltas) if deltas else None,
        'full_matrix_quality_delta':None,'pairs':task_pairs},
        'non_narrative_outcomes':{'failed':sum(r['outcome']=='failed' for r in terminal),'cancelled':sum(r['outcome']=='cancelled' for r in terminal),
            'matches_initial_frozen_contract':sum(r['matches_initial_hypothesis'] is True for r in terminal if r['outcome']!='needs_review'),
            'matches_corrected_contract':sum(r['matches_corrected_hypothesis'] is True for r in terminal if r['outcome']!='needs_review'),
            'are_successful_narrative_completion':False,'mechanical_safety_scope':'finite saved local inspection, not full safety or semantic failure success'},
        'family_reviewed_completions':dict(Counter(r['family'] for r in rows if r['main_review'] and r['main_review']['metrics']['task_completion'] is True))}


def validate(value):
    require(type(value) is dict and set(value)=={'schema','created_on','scope','gates','hash_conventions','source_receipts','review_batches','cohorts','tasks','physical_intentions','rows','summary','payload_sha256'},'projection_schema')
    require(value['schema']=='local-existing-main-review-projection-v6' and value['payload_sha256']==digest({k:v for k,v in value.items() if k!='payload_sha256'}),'projection_digest')
    require(value['gates']=={'human_signoff':False,'live_model':False,'Google_sandbox':False,'main_unseen':False,'demo_ready':False,'publication_ready':False,'final_acceptance':False,'reviewer_agreement':None,'actual_cost':None,'estimated_cost':None,'model_tokens':None,'aggregate_full_plan_safety':None},'closed_gates_or_null_usage')
    require(value['scope']['reviewer_kind']=='Codex main model; not human approval' and value['scope']['authored_exposed_local_double'] is True and value['scope']['new_actor_runs']==0,'projection_scope')
    require(all(sha(v) for v in value['source_receipts'].values()),'source_digest_invalid')
    require(len(value['review_batches'])==9 and all(set(b)=={'batch','summary_file_sha256','summary_canonical_sha256','reviewed_records'} and re.fullmatch(r'20261013T[0-9]{4}(?:-[a-z]+)?',b['batch']) and sha(b['summary_file_sha256']) and sha(b['summary_canonical_sha256']) for b in value['review_batches']),'review_batch_schema')
    batch_names={b['batch'] for b in value['review_batches']}
    require(len(batch_names)==9 and sum(b['reviewed_records'] for b in value['review_batches'])==56,'review_batch_denominator')
    tasks=value['tasks']; rows=value['rows']; physical=value['physical_intentions']
    require(type(tasks) is list and len(tasks)==36 and type(rows) is list and len(rows)==72 and type(physical) is list and len(physical)==77,'alias_inflation_or_missing_denominator')
    taskmap={}
    lineage={p:set() for p in POPULATIONS}; objects={p:set() for p in POPULATIONS}
    for task in tasks:
        require(set(task)=={'population','task_id','family','principal','lineage_id','source_object_ids','input_file_sha256','input_canonical_sha256','rubric_file_sha256','rubric_canonical_sha256','config_sha256','original_slots'},'task_schema')
        pop=task['population']; key=(pop,task['task_id'])
        require(pop in POPULATIONS and task['task_id'].startswith(pop+'-') and key not in taskmap and set(task['original_slots'])==set(POLICIES),'cross_population_task_collision')
        require(all(sha(task[k]) for k in ('input_file_sha256','input_canonical_sha256','rubric_file_sha256','rubric_canonical_sha256','config_sha256')),'task_binding')
        taskmap[key]=task; lineage[pop].add(task['lineage_id']); objects[pop].update(task['source_object_ids'])
    require(len(lineage['development'])==12 and len(lineage['capability'])==24 and not lineage['development']&lineage['capability'] and not objects['development']&objects['capability'],'source_lineage_overlap')
    require({(r['population'],r['original_attempt']) for r in rows}=={(p,f'a{i:03d}') for p,n in (('development',24),('capability',48)) for i in range(1,n+1)},'cross_population_ID_collision')
    require(len({(r['population'],r['task_id'],r['policy'],r['original_attempt']) for r in rows})==72,'logical_identity_collision')
    rowmap={r['slot']:r for r in rows}; require(len(rowmap)==72,'logical_slot_collision')
    require(Counter(p['state'] for p in physical)=={'terminal':71,'interrupted_unknown':1,'unexecuted':5},'physical_denominator')
    require(len({(p['cohort'],p['physical_id']) for p in physical})==77,'physical_alias_duplicate')
    require(all(set(p)=={'cohort','physical_id','logical_slot','state','outcome','error'} and p['cohort'] in ('original.development','original.capability','v3','v4') and re.fullmatch(r'[asc][0-9]{3}',p['physical_id']) and p['logical_slot'] in rowmap for p in physical),'physical_schema')
    for row in rows:
        require(set(row)=={'slot','population','original_attempt','task_id','family','policy','principal','state','outcome','error','provider_requests','config_sha256','physical_origin','evidence','main_review','matches_initial_hypothesis','matches_corrected_hypothesis'},'row_schema')
        pop=row['population']; task=taskmap.get((pop,row['task_id']))
        require(task is not None and row['policy'] in POLICIES and row['slot']==pop+'/'+row['original_attempt'] and task['original_slots'][row['policy']]==row['original_attempt'],'cross_population_ID_collision')
        require(row['family']==task['family'] and row['principal']==task['principal'] and row['config_sha256']==task['config_sha256'],'unequal_policy_budget_or_task')
        active=[p for p in physical if p['logical_slot']==row['slot'] and p['state']!='unexecuted']; require(len(active)==1,'alias_terminal_inflation')
        p=active[0]; require(row['physical_origin']==p['cohort']+'/'+p['physical_id'] and row['state']==p['state'] and row['outcome']==p['outcome'] and row['error']==p['error'],'historical_outcome_relabel')
        require(set(row['evidence'])=={'report_file_sha256','trace_file_sha256','corpus_file_sha256','draft_canonical_sha256','source_manifest_sha256','native_plan_sha256','native_journal_sha256','execution_source_sha256'} and all(v is None or sha(v) for v in row['evidence'].values()),'receipt_digest_invalid')
        require(row['evidence']['source_manifest_sha256']==value['source_receipts']['actor_source_manifest_sha256'],'source_binding_mismatch')
        if row['state']=='interrupted_unknown':
            require(row['slot']=='capability/a046' and row['outcome'] is None and row['error'] is None and row['provider_requests'] is None and row['main_review'] is None,'erased_unknown_or_invented_outcome')
        else:
            require(row['state']=='terminal' and row['outcome'] in ('needs_review','failed','cancelled') and type(row['provider_requests']) is int and row['provider_requests']>=0,'terminal_schema')
            if row['outcome']=='needs_review':
                review=row['main_review']; require(type(review) is dict and set(review)=={'review_batch','record_name','review_file_sha256','review_canonical_sha256','inspection_file_sha256','bindings','metrics','human_approval','reviewer_agreement'},'missing_review')
                require(all(sha(review[k]) for k in ('review_file_sha256','review_canonical_sha256','inspection_file_sha256')) and review['human_approval'] is False and review['reviewer_agreement'] is None,'reviewer_or_digest')
                require(review['review_batch'] in batch_names and review['record_name']==('capability-' if pop=='capability' else '')+row['original_attempt']+'-main-review.json','review_population_attempt_reference')
                require(review['bindings']=={'task_id':row['task_id'],'input_sha256':task['input_canonical_sha256'],'rubric_sha256':task['rubric_canonical_sha256'],'draft_sha256':row['evidence']['draft_canonical_sha256']},'review_binding_mismatch')
                metrics=review['metrics']; require(set(metrics)=={'claims','relationships','coverage','semantic_correctness','task_completion','mechanical_checks_passed'},'review_metric_schema')
                require(type(metrics['claims']) is int and metrics['claims']>0 and type(metrics['relationships']) is int and metrics['relationships']>0 and type(metrics['task_completion']) is bool and metrics['mechanical_checks_passed'] is True,'review_metrics')
                require(all(type(metrics[k]) in (int,float) and 0<=metrics[k]<=1 for k in ('coverage','semantic_correctness')),'review_metrics')
            else:require(row['main_review'] is None and row['evidence']['draft_canonical_sha256'] is None,'failure_relabel_or_fabricated_review')
    require(Counter(r['outcome'] for r in rows)=={'needs_review':56,'failed':13,'cancelled':2,None:1},'failure_or_unknown_erased')
    require(rowmap['capability/a045']['outcome']=='failed' and rowmap['capability/a045']['error']=='invalid_decision' and rowmap['capability/a045']['matches_initial_hypothesis'] is False,'a045_history_relabel')
    require(value['cohorts']['original_stopped_exit']==2 and value['cohorts']['v3_unknown_non_retryable'] is True,'unknown_recovery_or_exit_relabel')
    require(value['summary']==summarize(value),'summary_denominator_or_quality_mismatch')
    require(value['summary']['scopes']['complete_logical']['provider_requests_known_terminals']==238 and value['summary']['scopes']['complete_logical']['provider_requests_complete_logical'] is None,'unknown_total_usage')
    return {'schema':'projection-readonly-validation-v6','logical_slots':72,'physical_intentions':77,'main_reviewed_drafts':56,
        'historical_unknown':1,'original_outcomes_changed':False,'private_receipt_authentication_performed':False,
        'new_actor_runs':0,'publication_or_demo_authorized':False}


def main():
    parser=argparse.ArgumentParser(description='Validate candidate public projection only; no private receipts or runtime.')
    parser.add_argument('--projection',type=Path,default=PROJECTION); args=parser.parse_args()
    print(json.dumps(validate(load(args.projection)),sort_keys=True))


if __name__=='__main__':main()
