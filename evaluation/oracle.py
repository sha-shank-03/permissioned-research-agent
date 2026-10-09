"""Independent M3 oracle: authored relationships and raw SQL, no runtime guard.

This module intentionally imports no permissioned_research module. It judges
completed records, not proposed actor actions. Labels never enter actor context.
"""
from dataclasses import dataclass
import json
import math
import re

TOPICS = ('hosting_region', 'sso', 'onboarding_days', 'support_hours')
REASONS = {'supported':'current_evidence', 'conflict':'unresolved_conflict',
           'stale_only':'evidence_not_current', 'unknown':'insufficient_accessible_evidence'}


def typed(value):
    return (type(value).__name__, json.dumps(value, sort_keys=True))


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from strings(key)
            yield from strings(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from strings(item)
    elif hasattr(value, '__dict__'):
        yield from strings(vars(value))


def jsonable(value):
    if isinstance(value, dict): return {str(k):jsonable(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [jsonable(v) for v in value]
    if hasattr(value,'__dict__'): return jsonable(vars(value))
    return value


@dataclass(frozen=True)
class Version:
    source_id: str
    revision: int
    connector: str
    subject: str
    title: str
    body: str
    effective_at: str
    review_until: str
    allowed: frozenset
    current: bool = True


@dataclass(frozen=True)
class Snapshot:
    epoch: int
    versions: tuple

    @classmethod
    def from_sql(cls, connection):
        epoch = connection.execute('SELECT epoch FROM control_epoch').fetchone()[0]
        memberships = {}
        for principal, group in connection.execute('SELECT principal,group_name FROM memberships'):
            memberships.setdefault(principal, set()).add(group)
        grants = {}
        for resource, group in connection.execute('SELECT resource,group_name FROM grants'):
            grants.setdefault(resource, set()).add(group)
        versions=[]
        for row in connection.execute('SELECT o.object_id,v.revision,o.connector,o.subject,o.title,v.body,v.effective_at,v.review_until,o.acl_resource,o.current_revision FROM objects o JOIN versions v ON o.object_id=v.object_id'):
            oid, revision, connector, subject, title, body, effective, until, resource, current = row
            allowed=frozenset(p for p,g in memberships.items() if g & grants.get(resource,set()))
            versions.append(Version(oid, revision, connector, subject, title, body, effective, until, allowed, revision==current))
        return cls(epoch, tuple(versions))

    def current_source(self, source_id):
        return next((v for v in self.versions if v.source_id == source_id and v.current), None)


def ratio(numerator, denominator):
    return {'numerator':numerator, 'denominator':denominator,
            'rate':numerator/denominator if denominator else None}


def score_record(result, label, snapshot, disclosure_violations=0):
    """Return counts and safe enum failures. Malformed data fails closed."""
    errors=[]
    counts={'entailed_claims':0,'claims':0,'exact_citations':0,'citations':0,
            'covered_current_topics':0,'answerable_current_topics':0,
            'stale_current_claims':0,'current_claims':0,'permission_correct':0,'permission_opportunities':0}
    for state in ('conflict','stale_only','unknown'):
        counts[state+'_correct']=0
        counts[state+'_opportunities']=0
    expected_topics=label['topics']
    for expected in expected_topics.values():
        if expected['state']=='supported': counts['answerable_current_topics']+=1
        else: counts[expected['state']+'_opportunities']+=1
    memo=result.get('memo') if type(result) is dict else None
    required={'protocol','evidence_kind','live_model_executed','run_id','principal','outcome','phase','recovered_attempts',
              'error','memo','counters','model_tokens','elapsed_seconds'}
    if (type(result) is not dict or set(result)!=required or result.get('protocol')!='research-action-v1' or
        result.get('evidence_kind')!='offline_scripted' or result.get('live_model_executed') is not False or
        result.get('principal')!=label['principal'] or result.get('outcome')!=label['expected_outcome'] or
        result.get('phase')!=('COMPLETED' if label['expected_outcome']=='ready_for_review' else 'NEEDS_FOLLOW_UP') or
        result.get('error') is not None or type(result.get('run_id')) is not str or not result.get('run_id') or
        type(result.get('recovered_attempts')) is not int or result['recovered_attempts']<0 or
        type(result.get('elapsed_seconds')) not in (int,float) or not math.isfinite(result['elapsed_seconds']) or result['elapsed_seconds']<0):
        errors.append('invalid_envelope_or_outcome')
    counters=result.get('counters') if type(result) is dict else None
    limits={'decisions':24,'searches':8,'reads':12,'retries':20,'restarts':1,'repairs':1,'cache_hits':100,'provider_requests':24}
    if type(counters) is not dict or set(counters)!=set(limits) or any(type(counters[k]) is not int or not 0<=counters[k]<=n for k,n in limits.items()):
        errors.append('invalid_counters')
    if type(result) is dict and result.get('model_tokens') is not None: errors.append('unexpected_scripted_token_usage')
    if type(memo) is not dict or set(memo)!={'vendor','as_of','outcome','topics'}:
        errors.append('invalid_memo_shape'); records=[]
    else:
        if memo['vendor']!=label['vendor'] or memo['as_of']!=label['as_of'] or memo['outcome']!=label['expected_outcome']:
            errors.append('wrong_request_or_status')
        records=memo['topics'] if type(memo['topics']) is list else []
    if len(records)!=4 or [r.get('topic') if type(r) is dict else None for r in records]!=list(TOPICS):
        errors.append('missing_duplicate_or_reordered_topic')
    by_topic={r.get('topic'):r for r in records if type(r) is dict and type(r.get('topic')) is str}
    for topic, expected in expected_topics.items():
        actual=by_topic.get(topic)
        if type(actual) is not dict or set(actual)!={'topic','state','reason','claims'} or type(actual.get('claims')) is not list:
            errors.append('invalid_topic_shape'); continue
        state=actual['state']; claims=actual['claims']
        if type(state) is not str or state not in REASONS or actual['reason']!=REASONS.get(state):
            errors.append('invalid_topic_reason')
        if len(claims)>2 or (state=='unknown')!= (len(claims)==0): errors.append('invalid_claim_cardinality')
        values=[]; all_supported=True
        for claim in claims:
            counts['claims']+=1; counts['citations']+=1; counts['permission_opportunities']+=1
            if state in ('supported','conflict'): counts['current_claims']+=1
            if type(claim) is not dict or set(claim)!={'value','citation'}:
                errors.append('invalid_claim_shape'); all_supported=False; continue
            value=claim['value']; values.append(typed(value)); cite=claim['citation']
            valid_type=(type(value) is str and value in ('EU','US','IN') if topic=='hosting_region' else
                        type(value) is bool if topic=='sso' else type(value) is int and 0<=value<=999)
            if not valid_type: errors.append('invalid_scalar_type'); all_supported=False
            fields={'connector','source_id','revision','title','start','end','quote'}
            if type(cite) is not dict or set(cite)!=fields or any(type(cite.get(k)) is not str for k in ('connector','source_id','title','quote')) or any(type(cite.get(k)) is not int for k in ('revision','start','end')):
                errors.append('invalid_citation_shape'); all_supported=False; continue
            source=snapshot.current_source(cite['source_id'])
            permission=source is not None and label['principal'] in source.allowed and cite['source_id'] in label['allowed_sources']
            if permission: counts['permission_correct']+=1
            else: errors.append('unauthorized_citation')
            exact=bool(permission and source.revision==cite['revision']==label['eligible_versions'].get(cite['source_id']) and
                       source.connector==cite['connector'] and source.title==cite['title'] and source.subject==label['vendor'] and
                       0<=cite['start']<cite['end']<=len(source.body) and source.body[cite['start']:cite['end']]==cite['quote'])
            if exact: counts['exact_citations']+=1
            else: errors.append('inexact_revision_or_span')
            relation=any(typed(r['value'])==typed(value) and all(cite[k]==r[k] for k in ('source_id','revision','start','end','quote')) for r in expected['supporting_spans'])
            fresh=bool(source and source.effective_at<=label['as_of']<=source.review_until)
            eligible=bool(source and source.effective_at<=label['as_of'] and source.revision==cite['revision'])
            temporal=(fresh if state in ('supported','conflict') else eligible and not fresh if state=='stale_only' else False)
            if state in ('supported','conflict') and not (fresh and eligible and exact): counts['stale_current_claims']+=1
            if exact and relation and temporal: counts['entailed_claims']+=1
            else: errors.append('nonentailed_or_ineligible_claim'); all_supported=False
        correct=(type(state) is str and state==expected['state'] and actual['reason']==REASONS.get(state) and set(values)=={typed(v) for v in expected['values']} and len(values)==len(set(values)) and all_supported)
        if not correct: errors.append('wrong_topic_state_or_value')
        elif state=='supported': counts['covered_current_topics']+=1
        else: counts[state+'_correct']+=1
    if disclosure_violations: errors.append('forbidden_disclosure')
    return {'passed':not errors,'violations':sorted(set(errors)),**counts}


class DisclosureInspector:
    """Per-boundary snapshots; prior lawful deliveries are never rescored.

    Shared cache retention is checked under each entry's original principal.
    Retention in another principal's scoped entry alone is not a disclosure.
    """
    def __init__(self, connection, authored_versions):
        self.connection=connection
        self.known={(s['object_id'],s['revision']):(s['title'],s['body']) for s in authored_versions}
        self.counts={}; self.violations=[]; self.bytes=0

    def inspect(self, surface, principal, payload, snapshot, epoch=None, persist=False):
        self.counts[surface]=self.counts.get(surface,0)+1
        flat=list(strings(payload)); self.bytes+=len(json.dumps(jsonable(payload),ensure_ascii=True).encode('utf-8'))
        visible=[v.body for v in snapshot.versions if v.current and principal in v.allowed]
        forbidden=[]
        for (oid,revision),(title,body) in self.known.items():
            current=snapshot.current_source(oid)
            if persist or current is None or principal not in current.allowed or revision!=current.revision:
                if persist or current is None or principal not in current.allowed: forbidden += [oid,title]
                forbidden.append(body)
                forbidden += [line for line in body.splitlines() if line and (persist or not any(line in b for b in visible))]
                forbidden += [token for token in re.findall(r'INVENTED_[A-Z0-9_]+', title+' '+body)
                              if persist or not any(token in b for b in visible)]
        bad=(epoch is not None and epoch!=snapshot.epoch)
        # Count contaminated boundary once, not once per repeated sentinel.
        bad=bad or any(token and token in text for token in forbidden for text in flat)
        if bad: self.violations.append({'surface':surface,'kind':'epoch_or_forbidden_content'})

    def __call__(self, surface, record):
        self.inspect(surface,record['principal'],record['payload'],Snapshot.from_sql(self.connection),record['epoch'])

    def inspect_persistence(self):
        snapshot=Snapshot.from_sql(self.connection)
        for table in ('audit_events','run_receipts','run_attempts','request_bindings'):
            for row in self.connection.execute('SELECT * FROM '+table):
                self.inspect('persisted_'+table,'',dict(row),snapshot,persist=True)

    def inspect_caches(self, caches):
        snapshot=Snapshot.from_sql(self.connection)
        self.inspect('cache_retention_scan','',{},snapshot)
        for name in ('evidence','memos'):
            for key,(_,payload) in getattr(caches,name).entries.items():
                self.inspect('cache_retention_'+name,key[1],payload,snapshot,key[2])

    def report(self):
        return {s:{'opportunities':n,'violations':sum(v['surface']==s for v in self.violations)} for s,n in sorted(self.counts.items())}
