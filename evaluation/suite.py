"""Frozen input integrity checks; this module does not execute any actor."""
import hashlib
import json
from pathlib import Path
from .oracle import TOPICS, typed

ROOT=Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(rows):
    return hashlib.sha256('\n'.join(r['path']+' '+r['sha256'] for r in sorted(rows,key=lambda r:r['path'])).encode()).hexdigest()


def load_labels():
    return json.loads((ROOT/'evaluation/labels/heldout.json').read_text(encoding='utf-8'))


def verify_actor_anchor(actor, suite, actor_file_sha256):
    if (suite['accepted_actor_manifest_sha256']!=actor['actor']['manifest_sha256'] or
        suite['accepted_baseline_manifest_sha256']!=actor['baseline']['manifest_sha256'] or
        suite['accepted_actor_freeze_file_sha256']!=actor_file_sha256):
        raise ValueError('accepted_actor_or_baseline_anchor_mismatch')


def verify_freezes():
    actor=json.loads((ROOT/'evaluation/FROZEN_ACTOR.json').read_text(encoding='utf-8'))
    suite=json.loads((ROOT/'evaluation/FROZEN_M3.json').read_text(encoding='utf-8'))
    verify_actor_anchor(actor,suite,digest(ROOT/'evaluation/FROZEN_ACTOR.json'))
    checked=0
    for section in (actor['actor'],actor['baseline'],actor['development_fixtures'],suite['rubrics'],suite['evaluation_source']):
        if manifest(section['files'])!=section['manifest_sha256']: raise ValueError('invalid_manifest')
        for row in section['files']:
            target=(ROOT/row['path']).resolve()
            if ROOT.resolve() not in target.parents or digest(target)!=row['sha256']:
                raise ValueError('frozen_bytes_changed')
            checked+=1
    return checked


def validate_rubrics(labels=None):
    """Structural/relationship consistency, never inferred answer scoring."""
    labels=labels or load_labels(); cases=labels['cases']
    if labels['actor_accessible'] is not False or len(cases)!=30 or len({c['id'] for c in cases})!=30:
        raise ValueError('case_inventory')
    families={c['family'] for c in cases}
    if len(families)!=10 or any({c['principal'] for c in cases if c['family']==f}!={'alex','priya','sam'} for f in families):
        raise ValueError('paired_identity_inventory')
    fixtures={}
    for case in cases:
        fixture=fixtures.setdefault(case['fixture'],json.loads((ROOT/case['fixture']).read_text(encoding='utf-8')))
        sources={s['object_id']:s for s in fixture['sources']}
        event=case['event']
        if event and event['kind']=='replace_source': sources[event['source']['object_id']]=event['source']
        if case['vendor']!=fixture['vendor'] or case['as_of']!=fixture['as_of'] or list(case['topics'])!=list(TOPICS):
            raise ValueError('request_or_topic_inventory')
        groups=set(fixture['principals'][case['principal']])
        if event and event['kind']=='revoke_membership': groups.remove(event['group'])
        allowed={oid for oid,s in sources.items() if groups & set(s['allow_groups'])}
        if allowed!=set(case['allowed_sources']) or case['eligible_versions']!={oid:sources[oid]['revision'] for oid in allowed}:
            raise ValueError('authored_acl_inconsistent')
        for topic,expected in case['topics'].items():
            relations=expected['supporting_spans']
            if {typed(r['value']) for r in relations}!={typed(v) for v in expected['values']}:
                raise ValueError('authored_relationship_values')
            for relation in relations:
                source=sources[relation['source_id']]
                if relation['topic']!=topic or relation['source_id'] not in allowed or source['revision']!=relation['revision'] or source['body'][relation['start']:relation['end']]!=relation['quote']:
                    raise ValueError('authored_span_inconsistent')
        outcome='ready_for_review' if all(t['state']=='supported' for t in case['topics'].values()) else 'needs_follow_up'
        if case['expected_outcome']!=outcome: raise ValueError('authored_outcome_inconsistent')
    return {'cases':30,'families':10,'fixtures':len(fixtures),'initial_sources':sum(len(f['sources']) for f in fixtures.values()),'actor_executions':0,'scores':0}
