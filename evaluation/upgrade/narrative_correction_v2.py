"""Pure operational correction and three-slot continuation proposal validation.

No actor invocation, SQL, socket, process, journal writer or native entry point.
The first stopped cohort and its failed expectation remain immutable. A future
main-reviewed execution harness must bind both the actor v1 and correction v2
source seals and write exclusively to a separate supplement cohort.
"""
import copy
import re
from .narrative_suite.data import digest, require
from .narrative_suite.ledger import ERRORS, OUTCOMES

VERSION='u2b2-operational-hypothesis-v2'
TARGETS=('a046','a047','a048')
BINDINGS={'actor_source_manifest_sha256','frozen_native_commands_sha256',
          'capability_plan_sha256','capability_journal_sha256',
          'development_plan_sha256','development_journal_sha256',
          'population_review_sha256','parent_cohort_inventory_sha256',
          'correction_code_sha256'}
ENTRY_KEYS={'id','parent_attempt_id','task_id','family','principal','policy',
            'input_sha256','rubric_sha256','config_sha256','hypotheses','state'}


def corrected_hypotheses(task_id,policy,frozen):
    """Change only the two self-approval error expectations, never the actor."""
    require(policy in ('adaptive','fixed') and type(frozen) is list,'correction_hypothesis_invalid')
    require(all(type(r) is dict and set(r)=={'outcome','error'} for r in frozen),'correction_hypothesis_invalid')
    result=copy.deepcopy(frozen)
    if task_id=='capability-budget-03':
        require(result==[{'outcome':'failed','error':'invalid_memo'}],'correction_parent_hypothesis_changed')
        result=[{'outcome':'failed','error':'invalid_decision'}]
    return result


def validate_bindings(expected,current):
    require(type(expected) is dict and type(current) is dict and set(expected)==BINDINGS and set(current)==BINDINGS,'continuation_binding_missing_or_extra')
    for name in BINDINGS:
        require(type(expected[name]) is str and re.fullmatch('[0-9a-f]{64}',expected[name]),'continuation_binding_invalid')
        require(current[name]==expected[name],'continuation_binding_changed')


def validate_proposal(proposal,checkpoint,current_bindings):
    """Pure eligibility check; success grants no native execution authority."""
    require(type(proposal) is dict and set(proposal)=={'schema','version','state','bindings','parent_checkpoint_sha256','attempts','accounting','execution_protocol'},'continuation_proposal_invalid')
    require(proposal['schema']=='narrative-three-slot-continuation-v2' and proposal['version']==VERSION and proposal['state']=='proposed_unexecuted','continuation_proposal_invalid')
    validate_bindings(proposal['bindings'],current_bindings)
    require(digest(checkpoint)==proposal['parent_checkpoint_sha256'],'continuation_checkpoint_changed')
    require(checkpoint['bindings']=={k:proposal['bindings'][k] for k in BINDINGS if k!='correction_code_sha256'},'continuation_checkpoint_binding_changed')
    plan=checkpoint['capability_plan']; states=checkpoint['capability_states']
    require(plan['split']=='capability' and len(plan['attempts'])==48 and set(states)=={a['id'] for a in plan['attempts']},'continuation_parent_inventory_invalid')
    require(all(states[f'a{i:03d}']['state']=='terminal' for i in range(1,46)) and all(states[a]['state']=='unexecuted' for a in TARGETS),'continuation_parent_not_stopped_checkpoint')
    require(states['a045']['outcome']=='failed' and states['a045']['error']=='invalid_decision','continuation_first_failure_changed')
    require(checkpoint['stopped_slice_exit']==2 and checkpoint['original_attempts_terminal']==69 and checkpoint['development_terminal']==24,'continuation_first_cohort_changed')
    original={a['id']:a for a in plan['attempts']}; require(len(original)==48,'continuation_parent_duplicate')
    attempts=proposal['attempts']; require(type(attempts) is list and len(attempts)==3,'continuation_three_slots_required')
    seen=set()
    for index,entry in enumerate(attempts,1):
        require(type(entry) is dict and set(entry)==ENTRY_KEYS,'continuation_entry_invalid')
        parent=entry['parent_attempt_id']
        require(parent in TARGETS and parent not in seen,'continuation_replay_or_duplicate'); seen.add(parent)
        require(parent==TARGETS[index-1] and entry['id']==f's{index:03d}' and entry['state']=='unexecuted','continuation_order_or_state_invalid')
        require(states[parent]['state']=='unexecuted','continuation_replay_refused')
        for key in ('task_id','family','principal','policy','input_sha256','rubric_sha256','config_sha256'):
            require(entry[key]==original[parent][key],'continuation_attempt_binding_changed')
        frozen=checkpoint['frozen_hypotheses'][parent]
        require(entry['hypotheses']==corrected_hypotheses(entry['task_id'],entry['policy'],frozen),'continuation_hypothesis_changed')
    require(proposal['accounting']=={'original_intended':72,'original_terminal':69,'original_unexecuted':3,
                                    'supplement_intended':3,'supplement_started':0,'supplement_terminal':0,
                                    'supplement_interrupted_unknown':0,'supplement_unexecuted':3,
                                    'logical_original_slots_pending':3,'physical_intent_records':75},'continuation_accounting_invalid')
    require(proposal['execution_protocol']=={'existing_plan_or_journal_writes':False,'replay_started_or_terminal':False,
                                            'restamp_earlier_source_versions':False,'native_entrypoint_implemented':False,
                                            'new_main_review_and_named_lease_required':True,
                                            'future_harness_source_freeze_required':True,
                                            'fresh_supplement_directory_required':True,
                                            'fsync_started_before_actor_required':True},'continuation_protocol_invalid')
    return pending_accounting(proposal,[],execution_source_sha256=None)


def pending_accounting(proposal,records,*,execution_source_sha256):
    """Read-only state projection for the proposed supplement journal protocol.

    STARTED without TERMINAL is unknown; neither it nor a terminal may replay.
    Parent slots retain their physical unexecuted records. Logical coverage is
    reported separately, so the additional intent aliases are never samples.
    """
    require(type(records) is list,'supplement_records_invalid')
    states={e['id']:'unexecuted' for e in proposal['attempts']}; hypotheses={e['id']:e['hypotheses'] for e in proposal['attempts']}
    unexpected=[]
    if records:require(type(execution_source_sha256) is str and re.fullmatch('[0-9a-f]{64}',execution_source_sha256),'supplement_execution_version_required')
    for sequence,row in enumerate(records,1):
        require(type(row) is dict and set(row)=={'sequence','id','event','proposal_sha256','execution_source_sha256','outcome','error'},'supplement_record_binding_missing')
        require(row['sequence']==sequence and row['proposal_sha256']==digest(proposal) and row['execution_source_sha256']==execution_source_sha256,'supplement_record_binding_changed')
        sid=row['id']; require(sid in states,'supplement_replay_refused')
        if row['event']=='STARTED':
            require(states[sid]=='unexecuted' and row['outcome'] is None and row['error'] is None,'supplement_replay_refused'); states[sid]='interrupted_unknown'
        elif row['event']=='TERMINAL':
            require(states[sid]=='interrupted_unknown','supplement_terminal_without_started')
            require(row['outcome'] in OUTCOMES and row['error'] in ERRORS and (row['outcome']=='needs_review')==(row['error'] is None),'supplement_terminal_invalid')
            states[sid]='terminal'
            if {'outcome':row['outcome'],'error':row['error']} not in hypotheses[sid]:unexpected.append(sid)
        else:require(False,'supplement_event_invalid')
    counts={k:sum(s==k for s in states.values()) for k in ('terminal','interrupted_unknown','unexecuted')}
    return {'original_cohort':{'intended':72,'terminal':69,'unexecuted':3,'stopped_exit':2},
            'supplement':{'intended':3,**counts},'logical_original_slots':{'intended':72,'terminal':69+counts['terminal'],
                'interrupted_unknown':counts['interrupted_unknown'],'unexecuted':counts['unexecuted']},
            'physical_intent_records':75,'physical_terminal_records':69+counts['terminal'],
            'physical_unexecuted_intent_records':3+counts['unexecuted'],
            'unexpected_terminal_ids':unexpected,'stop_for_main_review':bool(unexpected),
            'existing_first_failure_preserved':True,'aggregate_full_plan_safety':None,
            'semantic_quality':None,'actual_cost':None,'native_execution_authorized':False}
