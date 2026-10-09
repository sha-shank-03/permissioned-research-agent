"""Durable metrics-only batch evidence; no actor, oracle or provider imports."""
import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid

IDENTITY_FIELDS=('case','family','principal','provider','repeat')
COUNT_FIELDS=('event_fired','probe_opportunities','probe_failures','observed_serialized_bytes',
              'entailed_claims','claims','exact_citations','citations','covered_current_topics',
              'answerable_current_topics','stale_current_claims','current_claims','permission_correct',
              'permission_opportunities','conflict_correct','conflict_opportunities',
              'stale_only_correct','stale_only_opportunities','unknown_correct','unknown_opportunities')
ROW_FIELDS=set(IDENTITY_FIELDS+COUNT_FIELDS+('outcome','counters','elapsed_seconds','harness_elapsed_seconds',
              'surface_counts','model_tokens','domain_sha256','auxiliary_runs','passed','violations'))
COUNTERS={'decisions','searches','reads','retries','restarts','repairs','cache_hits','provider_requests'}
SURFACES={'provider_request','tool_output','cache_lookup','invalidated','output','returned_result',
          'render_json','render_markdown','citation_resolution','cache_retention_scan',
          'cache_retention_evidence','cache_retention_memos','retained_context_after_invalidation',
          'persisted_audit_events','persisted_run_receipts','persisted_run_attempts','persisted_request_bindings'}
VIOLATIONS={'invalid_envelope_or_outcome','invalid_counters','unexpected_scripted_token_usage',
            'invalid_memo_shape','wrong_request_or_status','missing_duplicate_or_reordered_topic',
            'invalid_topic_shape','invalid_topic_reason','invalid_claim_cardinality','invalid_claim_shape',
            'invalid_scalar_type','invalid_citation_shape','unauthorized_citation','inexact_revision_or_span',
            'nonentailed_or_ineligible_claim','wrong_topic_state_or_value','forbidden_disclosure',
            'event_not_exercised','cross_identity_probe_failed'}
OUTCOMES={'ready_for_review','needs_follow_up','failed','cancelled'}


class InvalidMetricRow(ValueError):
    """A row cannot enter the public evidence ledger."""


def natural(value):
    return type(value) is int and value>=0


def seconds(value):
    return type(value) in (int,float) and math.isfinite(value) and value>=0


def counters_valid(value):
    return type(value) is dict and set(value)==COUNTERS and all(natural(n) for n in value.values())


def safe_metric_row(row, attempt):
    """Closed schema prevents accidental source/context/memo/exception capture."""
    if type(row) is not dict or set(row)!=ROW_FIELDS or any(row[k]!=attempt[k] for k in IDENTITY_FIELDS):
        raise InvalidMetricRow()
    if (row['outcome'] not in OUTCOMES or type(row['passed']) is not bool or
        not all(natural(row[k]) for k in COUNT_FIELDS) or not counters_valid(row['counters']) or
        not all(seconds(row[k]) for k in ('elapsed_seconds','harness_elapsed_seconds')) or
        row['model_tokens'] is not None or type(row['domain_sha256']) is not str or
        not re.fullmatch('[0-9a-f]{64}',row['domain_sha256']) or type(row['violations']) is not list or
        any(type(v) is not str or v not in VIOLATIONS for v in row['violations'])):
        raise InvalidMetricRow()
    surfaces=row['surface_counts']
    if type(surfaces) is not dict or not set(surfaces)<=SURFACES: raise InvalidMetricRow()
    for counts in surfaces.values():
        if type(counts) is not dict or set(counts)!={'opportunities','violations'} or not all(natural(v) for v in counts.values()) or counts['violations']>counts['opportunities']:
            raise InvalidMetricRow()
    if type(row['auxiliary_runs']) is not list: raise InvalidMetricRow()
    for warmup in row['auxiliary_runs']:
        if (type(warmup) is not dict or set(warmup)!={'kind','principal','outcome','counters','elapsed_seconds'} or
            warmup['kind']!='cache_warmup' or warmup['principal']!='priya' or warmup['outcome'] not in OUTCOMES or
            not counters_valid(warmup['counters']) or not seconds(warmup['elapsed_seconds'])):
            raise InvalidMetricRow()
    return copy.deepcopy(row)


def plan_attempts(labels, providers, repeat):
    planned=[]
    for repetition in range(1,repeat+1):
        for label in labels:
            for provider in providers:
                if provider not in ('scripted','baseline') or label['principal'] not in ('alex','priya','sam') or any(type(label[k]) is not str or not re.fullmatch('[a-z][a-z0-9_-]{0,95}',label[k]) for k in ('id','family')):
                    raise ValueError('invalid_plan_identity')
                planned.append({'attempt_index':len(planned)+1,'case':label['id'],'family':label['family'],
                                'principal':label['principal'],'provider':provider,'repeat':repetition,
                                'answerable_current_topics':sum(t['state']=='supported' for t in label['topics'].values()),
                                **{state+'_opportunities':sum(t['state']==state for t in label['topics'].values()) for state in ('conflict','stale_only','unknown')}})
    return planned


def ledger_path(output):
    output=Path(output)
    return output.with_name(output.name+'.attempts.jsonl')


def ensure_new_output(output, with_ledger=False):
    paths=(Path(output),ledger_path(output)) if with_ledger else (Path(output),)
    if any(path.exists() or path.is_symlink() for path in paths): raise FileExistsError('evidence_already_exists')


def write_new_report(output, report):
    output=Path(output); output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(report,stream,indent=2,allow_nan=False); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())


class ProgressLedger:
    def __init__(self, output, initial_report):
        self.output=Path(output); self.journal=ledger_path(output); self.batch_id=uuid.uuid4().hex
        ensure_new_output(self.output,with_ledger=True)
        report={**initial_report,'batch_id':self.batch_id,'attempt_ledger':self.journal.name}
        write_new_report(self.output,report)
        # Exclusive creation protects both existing reports and sidecar evidence.
        self.stream=self.journal.open('x',encoding='utf-8',newline='\n')
        self.append('planned',{'planned_case_runs':report['planned_case_runs'],'planned_attempts':report['planned_attempts']})

    def append(self, kind, record):
        event={'event':kind,'batch_id':self.batch_id,**record}
        self.stream.write(json.dumps(event,allow_nan=False)+'\n'); self.stream.flush(); os.fsync(self.stream.fileno())

    def report(self, report):
        # Only the exclusively created batch's own report may be replaced.
        current=json.loads(self.output.read_text(encoding='utf-8'))
        if current.get('batch_id')!=self.batch_id: raise FileExistsError('evidence_ownership_changed')
        value={**report,'batch_id':self.batch_id,'attempt_ledger':self.journal.name}
        descriptor, temporary=tempfile.mkstemp(prefix=self.output.name+'.',suffix='.tmp',dir=self.output.parent)
        try:
            with os.fdopen(descriptor,'w',encoding='utf-8',newline='\n') as stream:
                json.dump(value,stream,indent=2,allow_nan=False); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary,self.output)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def close(self): self.stream.close()


def execute_case_plan(labels, providers, repeat, output, runner, report_factory, gate):
    """Injectable pure orchestration; real actor runner is supplied elsewhere.

    The complete planned denominator is fixed before any runner call. Only
    returned, validated metric rows enter results; failed/not-started attempts
    never receive invented counters, citations, outcomes or success values.
    """
    planned=plan_attempts(labels,providers,repeat); rows=[]; failures=[]; started=0
    def report(status):
        value=report_factory(rows,planned)
        value.update(batch_status=status,complete=status=='complete',planned_case_runs=len(planned),
                     planned_attempts=planned,case_runs=len(rows),started_attempts=started,
                     failed_attempts=failures,unexecuted_case_runs=len(planned)-started,passed=False)
        if value['complete']: value['passed']=gate(value)
        return value
    ledger=ProgressLedger(output,report('planned'))
    lookup={label['id']:label for label in labels}
    try:
        for attempt in planned:
            started+=1
            ledger.append('started',{'attempt':attempt}); ledger.report(report('running'))
            try:
                row=runner(copy.deepcopy(lookup[attempt['case']]),attempt['provider'],attempt['repeat'])
                row=safe_metric_row(row,attempt)
            except BaseException as error:
                # Never serialize str(error), repr(error), traceback or locals.
                failure={**{k:attempt[k] for k in ('attempt_index',*IDENTITY_FIELDS)},'exception_type':type(error).__name__}
                failures.append(failure); ledger.append('failed',{'attempt':failure})
                value=report('incomplete'); ledger.report(value); return value
            rows.append(row); ledger.append('completed',{'attempt_index':attempt['attempt_index'],'result':row})
            ledger.report(report('running'))
        value=report('complete'); ledger.append('finished',{'complete':True,'passed':value['passed']})
        ledger.report(value); return value
    finally:
        ledger.close()
