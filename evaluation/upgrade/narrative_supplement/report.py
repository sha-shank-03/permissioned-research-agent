"""Read-only physical/logical reconciliation; never authorizes dispatch."""
from ..narrative_suite.data import require
from .contract import file_hash
from .journal import Journal
from .runner import RAW_FILES


def reconcile(root,execution_sha256,*,context=None):
    journal=Journal(root); state=journal.inspect()
    require(journal.plan['execution_source_manifest_sha256']==execution_sha256,'supplement_report_version_changed')
    current=False
    if context is not None:
        try:current=context.verify() is True and context.blueprint==journal.plan
        except Exception:current=False
    rows=[]; receipt_errors=[]; unexpected=[]
    for entry in journal.plan['attempts']:
        sid=entry['id']; slot=state['slots'][sid]; evidence=None
        if slot['state']=='terminal':
            try:
                actual={key:file_hash(journal.root/sid/name,maximum) for key,(name,maximum) in RAW_FILES.items()}
                for key,suffix in (('returned_sha256','returned'),('usage_sha256','usage'),('execution_sha256','execution')):
                    actual[key]=file_hash(journal.root/(sid+'.'+suffix+'.json'),128*1024)
                evidence=actual==slot['artifacts']
            except Exception:evidence=False
            if not evidence:receipt_errors.append(sid)
            if {'outcome':slot['metrics']['outcome'],'error':slot['metrics']['error']} not in entry['hypotheses']:unexpected.append(sid)
        rows.append({'supplement_id':sid,'original_slot':entry['parent_attempt_id'],'state':slot['state'],
            'metrics':slot['metrics'],'artifacts':slot['artifacts'],'classification_acknowledged':slot['classified'],
            'raw_evidence_hashes_match':evidence})
    counts=state['counts']
    claimed=(journal.root/'run.claim.json').exists()
    partial_batch=claimed and (counts['terminal']!=3 or any(s['classified'] is not True for s in state['slots'].values()))
    return {'schema':'narrative-supplement-reconciliation-v3','dispatch_authorized':False,
        'original_cohort':{'intended':72,'terminal':69,'interrupted_unknown':0,'unexecuted':3,'stopped_exit':2,
            'a045':{'outcome':'failed','error':'invalid_decision','original_expected_error':'invalid_memo'},
            'source_manifest_sha256':journal.plan['actor_source_manifest_sha256'],'bindings':journal.plan['parent_bindings']},
        'supplement':{'intended':3,**counts,'execution_source_manifest_sha256':execution_sha256,
            'plan_sha256':state['plan_sha256'],'journal_sha256':state['journal_sha256'],
            'persisted_intention_records':len(state['intended_ids']),'intended_complete':state['intended_complete']},
        'logical_original_slots':{'intended':72,'terminal':69+counts['terminal'],
            'interrupted_unknown':counts['interrupted_unknown'],'unexecuted':counts['unexecuted']},
        'physical_intent_records':75,'physical_terminal_records':69+counts['terminal'],
        'physical_interrupted_unknown_records':counts['interrupted_unknown'],
        'physical_unexecuted_intent_records':3+counts['unexecuted'],
        'aliases_are_additional_samples':False,'slots':rows,'unexpected_terminal_ids':unexpected,
        'raw_evidence_error_ids':receipt_errors,'current_source_parent_proposal_match':current,
        'halt_codes':state['halt_codes'],'torn_tail':state['torn_tail'],
        'batch_claim_present':claimed,'incomplete_claimed_batch':partial_batch,
        'stop_for_main_review':bool(state['halt_codes'] or state['unresolved'] or not current or receipt_errors or unexpected or not state['intended_complete'] or partial_batch),
        'aggregate_full_plan_safety':None,'semantic_quality':None,'actual_cost':None,
        'live_or_google_acceptance':False}
