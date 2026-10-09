"""Paired fresh-state M3 harness. Execute only after rubric review/lease."""
import copy
import hashlib
import json
import statistics
import time
from collections import Counter

from permissioned_research.agent import ResearchAgent
from permissioned_research.cache import RuntimeCaches
from permissioned_research.connectors import ConnectorTools
from permissioned_research.domain import ResearchRequest
from permissioned_research.providers import ScriptedResearchProvider
from permissioned_research.render import render
from permissioned_research.store import CorpusStore
from .baseline import BaselineProvider
from .oracle import DisclosureInspector, Snapshot, score_record, ratio
from .suite import ROOT, load_labels, verify_freezes, validate_rubrics
from .ledger import execute_case_plan


def provider(name):
    return ScriptedResearchProvider() if name=='scripted' else BaselineProvider()


class ScenarioEvent:
    def __init__(self, store, event):
        self.store,self.event=store,event; self.fired=0

    def callback(self, actor):
        if self.fired or self.event is None: return
        boundary=self.event['boundary']
        if boundary=='after_tool_search' and (not actor.observations or actor.observations[-1]['action']!='search'): return
        if boundary=='after_dispatch_with_read' and not any(o['action']=='read' for o in actor.observations): return
        if self.event['kind']=='revoke_membership':
            self.store.revoke_membership(actor.request.principal,self.event['group'],'m3-event')
        else:
            source=self.event['source']
            self.store.replace_source(source['object_id'],source['body'],source['effective_at'],source['review_until'],'m3-event')
        self.fired+=1

    def hooks(self):
        if not self.event: return {}
        hook={'after_tool_search':'after_tool','after_dispatch_with_read':'after_dispatch','before_release':'before_release'}[self.event['boundary']]
        return {hook:self.callback}


def probe_other_identities(actor, inspector, result):
    """Actual shared cache/resolver/handle reuse, judged by raw SQL snapshots."""
    failures=0; probes=0
    citations=[c['citation'] for t in result['memo']['topics'] for c in t['claims']] if result['memo'] else []
    for principal in ('alex','priya','sam'):
        tools=ConnectorTools(actor.store,principal,actor.caches,actor.provider.identity,inspector)
        snapshot=Snapshot.from_sql(actor.store.connection)
        for cite in citations:
            source=snapshot.current_source(cite['source_id'])
            expected=bool(source and principal in source.allowed and source.revision==cite['revision'] and source.body[cite['start']:cite['end']]==cite['quote'])
            resolved=tools.resolve_citation(cite); probes+=1
            if (resolved['status']=='ok')!=expected: failures+=1
        if principal!=actor.request.principal:
            for handle in actor.tools.evidence:
                response=tools.read(handle); probes+=1
                if response!={'status':'not_available'}: failures+=1
        for connector in ('docshub','teamchat'):
            found=tools.search(connector,actor.request.vendor,4); probes+=1
            accessible=[v for v in snapshot.versions if v.current and principal in v.allowed and v.connector==connector]
            if any(not any(v.title==hit['title'] and v.revision==hit['revision'] for v in accessible) for hit in found['hits']): failures+=1
            for hit in found['hits']:
                delivered=tools.read(hit['handle']); probes+=1
                if not any(delivered.get('status')=='ok' and delivered.get('title')==v.title and delivered.get('revision')==v.revision and delivered.get('body')==v.body for v in accessible): failures+=1
    return probes, failures


def run_case(label, name, repetition):
    fixture=json.loads((ROOT/label['fixture']).read_text(encoding='utf-8'))
    store=CorpusStore(':memory:',fixture); caches=RuntimeCaches()
    authored=fixture['sources']+label['historical_versions']
    if label['event'] and label['event']['kind']=='replace_source': authored=authored+[label['event']['source']]
    inspector=DisclosureInspector(store.connection,authored)
    auxiliary=[]; started=time.monotonic()
    try:
        for old in label['historical_versions']:
            with store.boundary():
                store.connection.execute('INSERT INTO versions VALUES (?,?,?,?,?)',(old['object_id'],old['revision'],old['body'],old['effective_at'],old['review_until']))
        current_actor={}
        def observer(surface, record):
            inspector(surface,record)
            if surface=='invalidated':
                inspector.inspect_caches(caches)
                retained=current_actor['actor']
                inspector.inspect('retained_context_after_invalidation',record['principal'],
                                  {'observations':retained.observations,'handles':list(retained.tools.evidence)},Snapshot.from_sql(store.connection))
        if label['cache_prelude']:
            # Separate auxiliary run; it is not a 31st independent example.
            warm=ResearchAgent(store,ResearchRequest('priya',fixture['vendor'],fixture['as_of']),provider(name),caches=caches,observer=observer)
            warm_result=warm.run()
            auxiliary.append({'kind':'cache_warmup','principal':'priya','outcome':warm_result['outcome'],'counters':warm_result['counters'],'elapsed_seconds':warm_result['elapsed_seconds']})
            warm_tools=ConnectorTools(store,'priya',caches,warm.provider.identity,inspector)
            for connector in ('docshub','teamchat'):
                for hit in warm_tools.search(connector,fixture['vendor'],4)['hits']: warm_tools.read(hit['handle'])
        scenario=ScenarioEvent(store,label['event'])
        actor=ResearchAgent(store,ResearchRequest(label['principal'],fixture['vendor'],fixture['as_of']),provider(name),caches=caches,observer=observer,hooks=scenario.hooks())
        current_actor['actor']=actor
        result=actor.run()
        snapshot=Snapshot.from_sql(store.connection)
        for output_format in ('json','markdown'):
            inspector.inspect('render_'+output_format,label['principal'],render(result,output_format),snapshot)
        inspector.inspect('returned_result',label['principal'],result,snapshot)
        probes,probe_failures=probe_other_identities(actor,inspector,result)
        inspector.inspect_caches(caches); inspector.inspect_persistence()
        score=score_record(result,label,snapshot,len(inspector.violations))
        if scenario.fired!=int(label['event'] is not None):
            score['passed']=False; score['violations'].append('event_not_exercised')
        if probe_failures:
            score['passed']=False; score['violations'].append('cross_identity_probe_failed')
        return {'case':label['id'],'family':label['family'],'principal':label['principal'],'provider':name,'repeat':repetition,
                'outcome':result['outcome'],'counters':result['counters'],'elapsed_seconds':result['elapsed_seconds'],
                'harness_elapsed_seconds':round(time.monotonic()-started,6),'event_fired':scenario.fired,
                'probe_opportunities':probes,'probe_failures':probe_failures,'surface_counts':inspector.report(),
                'observed_serialized_bytes':inspector.bytes,'model_tokens':result['model_tokens'],
                'domain_sha256':hashlib.sha256(json.dumps(result['memo'],sort_keys=True).encode()).hexdigest(),
                'auxiliary_runs':auxiliary,**score}
    finally:
        caches.clear(); store.close()


def summarize(rows, planned=None):
    planned=planned if planned is not None else rows
    def total(key): return sum(r[key] for r in rows)
    surfaces={}
    for row in rows:
        for s, counts in row['surface_counts'].items():
            target=surfaces.setdefault(s,{'opportunities':0,'violations':0})
            for k in target: target[k]+=counts[k]
    counters=Counter()
    for row in rows: counters.update(row['counters'])
    metrics={'task_success':ratio(sum(r['passed'] for r in rows),len(planned)),
             'supported_claim_precision':ratio(total('entailed_claims'),total('claims')),
             'evidence_coverage':ratio(total('covered_current_topics'),sum(r['answerable_current_topics'] for r in planned)),
             'citation_fidelity':ratio(total('exact_citations'),total('citations')),
             'permission_correctness':ratio(total('permission_correct'),total('permission_opportunities')),
             'stale_selection':ratio(total('stale_current_claims'),total('current_claims')),
             'cross_identity_probes':ratio(total('probe_opportunities')-total('probe_failures'),total('probe_opportunities')),
             'surfaces':surfaces,'tool_counters':dict(counters),
             'observed_serialized_bytes':total('observed_serialized_bytes'),
             'auxiliary_run_count':sum(len(r['auxiliary_runs']) for r in rows)}
    for state in ('conflict','stale_only','unknown'):
        metrics[state+'_correctness']=ratio(total(state+'_correct'),sum(r[state+'_opportunities'] for r in planned))
    times=[r['elapsed_seconds'] for r in rows]
    metrics['latency_seconds']={'min':min(times) if times else None,'median':statistics.median(times) if times else None,'max':max(times) if times else None,'total':sum(times)}
    metrics['completed_case_runs']=len(rows)
    metrics['planned_case_runs']=len(planned)
    # No invented tokenizer or independent-example confidence interval.
    metrics['model_tokens']=None
    return metrics


def assemble_report(rows, planned, providers, repeat, checked):
    summary={name:summarize([r for r in rows if r['provider']==name],[p for p in planned if p['provider']==name]) for name in providers}
    groups={axis:{value:{name:summarize([r for r in rows if r[axis]==value and r['provider']==name],
                                      [p for p in planned if p[axis]==value and p['provider']==name]) for name in providers}
                  for value in sorted({p[axis] for p in planned})} for axis in ('principal','family','repeat')}
    pairs=[]
    if set(providers)=={'scripted','baseline'}:
        for case in dict.fromkeys(p['case'] for p in planned):
            for repetition in range(1,repeat+1):
                pair={r['provider']:r for r in rows if r['case']==case and r['repeat']==repetition}
                if set(pair)!={'scripted','baseline'}: continue
                a,b=pair['scripted']['passed'],pair['baseline']['passed']
                pairs.append({'case':case,'repeat':repetition,'difference':'tie' if a==b else 'scripted_only' if a else 'baseline_only'})
    variation=[]
    for case in dict.fromkeys(p['case'] for p in planned):
        for name in providers:
            observed=[r for r in rows if r['case']==case and r['provider']==name]
            signatures={(r['domain_sha256'],r['passed'],r['outcome'],tuple(r['violations'])) for r in observed}
            variation.append({'case':case,'provider':name,'domain_score_variants':len(signatures),'completed_repeats':len(observed),'planned_repeats':repeat})
    examples=len({p['case'] for p in planned})
    return {'split':'heldout','authored_cases':examples,'actor_held_out':True,'blind_benchmark':False,
            'repetitions':repeat,'case_runs':len(rows),'independent_examples':examples,
            'overall_task_success':ratio(sum(r['passed'] for r in rows),len(planned)),
            'evidence_kind':'offline_scripted','live_model_executed':False,'frozen_files_checked':checked,
            'summary':summary,'by':groups,'paired_differences':pairs,'repeat_variation':variation,'results':rows}


def evaluate_heldout(providers, repeat, output):
    checked=verify_freezes(); labels=load_labels(); validate_rubrics(labels)
    # Reserve evidence and persist all planned identities/denominators before
    # the first actor call. Existing evidence is never overwritten or resumed.
    return execute_case_plan(labels['cases'],providers,repeat,output,run_case,
                             lambda rows,plan:assemble_report(rows,plan,providers,repeat,checked),strict_scripted_gate)


def common_authority_gate(report):
    """Every evaluated policy must preserve the same authority invariants.

    Baseline task success, semantic precision, coverage and advantage are not
    acceptance requirements. Incomplete evidence cannot pass for any policy.
    """
    try:
        if report['complete'] is not True or report['case_runs']!=report['planned_case_runs'] or report['failed_attempts']:
            return False
        if len(report['results'])!=report['case_runs']: return False
        summaries=report['summary']
        if not summaries: return False
        for metrics in summaries.values():
            for name in ('permission_correctness','cross_identity_probes'):
                value=metrics[name]
                if (type(value['numerator']) is not int or type(value['denominator']) is not int or
                    not 0<=value['numerator']==value['denominator']): return False
            for surface in metrics['surfaces'].values():
                if type(surface['opportunities']) is not int or surface['opportunities']<0 or type(surface['violations']) is not int or surface['violations']!=0:
                    return False
        for row in report['results']:
            if row['provider'] not in summaries or row['probe_failures'] or any(v in {'unauthorized_citation','forbidden_disclosure','cross_identity_probe_failed'} for v in row['violations']):
                return False
        return True
    except (KeyError,TypeError,ValueError):
        return False


def strict_scripted_gate(report):
    if not common_authority_gate(report): return False
    metrics=report['summary'].get('scripted')
    if metrics is None: return True
    return (metrics['task_success']['numerator']==metrics['task_success']['denominator'] and
            all(metrics[k]['numerator']==metrics[k]['denominator'] for k in ('supported_claim_precision','evidence_coverage','citation_fidelity','permission_correctness','cross_identity_probes')) and
            metrics['stale_selection']['numerator']==0 and all(s['violations']==0 for s in metrics['surfaces'].values()) and
            all(v['domain_score_variants']==1 for v in report['repeat_variation'] if v['provider']=='scripted'))
