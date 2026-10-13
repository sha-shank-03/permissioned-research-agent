"""Input-only actor loader; rubric loading is a separate inspection operation."""
import copy
import hashlib
import json
from pathlib import Path
import re

PACK=Path(__file__).resolve().parents[3]/'fixtures/upgrade/narrative-v2'
TOPICS=('hosting_region','sso','onboarding_days','support_hours','conditions','dependencies','exceptions')
FAMILIES=('qualifiers_dependencies','conflicts','temporal_missing','scope_injection','revocation_replacement','budget_failure')


class SuiteError(ValueError):
    def __init__(self,code):
        super().__init__(code); self.code=code


def require(condition,code='suite_invalid'):
    if not condition:raise SuiteError(code)


def unique_json(raw):
    def pairs(items):
        value={}
        for k,v in items:
            if k in value:raise SuiteError('suite_invalid_json')
            value[k]=v
        return value
    try:return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(SuiteError('suite_invalid_json')))
    except (ValueError,UnicodeError,RecursionError):raise SuiteError('suite_invalid_json') from None


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def read_bound(root,relative,expected):
    require(type(relative) is str and not Path(relative).is_absolute(),'suite_path_invalid')
    path=(root/relative).resolve(); require(path.is_relative_to(root.resolve()) and path.is_file(),'suite_path_invalid')
    raw=path.read_bytes(); require(len(raw)<=128*1024 and hashlib.sha256(raw).hexdigest()==expected,'suite_hash_mismatch')
    return unique_json(raw.decode('utf-8'))


def validate_execution(task):
    """Trusted controls are closed, local and bounded; never model instructions."""
    c=task['execution']; require(type(c) is dict and set(c)=={'events','provider_fault','transport_fault','cancel_before_send','config_overrides'})
    require(c['provider_fault'] in (None,'malformed_once','self_approve'))
    require(c['transport_fault'] in (None,401,429) and type(c['cancel_before_send']) is bool)
    require(c['config_overrides'] in ({},{'max_requests':2}))
    require(not c['config_overrides'] or type(c['config_overrides']['max_requests']) is int)
    require(type(c['events']) is list and len(c['events'])<=2)
    sources={s['object_id']:s for s in task['fixture']['sources']}; ids=set()
    for e in c['events']:
        require(type(e) is dict and e.get('kind') in ('revoke_membership','replace_source'))
        keys={'id','target','phase','trigger','kind','principal','group'}
        if e['kind']=='replace_source':keys|={'body','effective_at','review_until'}
        require(set(e)==keys and e['target'] in sources and e['principal']==task['principal'])
        require(type(e['id']) is str and e['id'].startswith(task['id']+'-event') and e['id'] not in ids); ids.add(e['id'])
        require(e['phase'] in ('before_send','after_submit','before_release') and e['trigger'] in ('once','target_opened'))
        require(e['group'] in ('employees','procurement',None))
        if e['kind']=='revoke_membership':require(e['group'] is not None)
        else:
            require(type(e['body']) is str and len(e['body'].encode())<=2048)
            require(e['effective_at']==sources[e['target']]['effective_at'] and e['review_until']==sources[e['target']]['review_until'])
    return c


class InputCatalog:
    """Never loads relationship rubrics in load_input/actor_cases."""
    def __init__(self,root=PACK):
        self.root=Path(root).resolve(); raw=(self.root/'FREEZE.json').read_bytes()
        require(len(raw)<=64*1024)
        self.freeze=unique_json(raw.decode('utf-8')); self.freeze_sha256=hashlib.sha256(raw).hexdigest()
        require(self.freeze['schema']=='narrative-task-pack-freeze-v1' and self.freeze['counts']=={'development':12,'capability':24})
        entries=self.freeze['entries']; require(type(entries) is list and len(entries)==36)
        self.entries={e['id']:e for e in entries}; require(len(self.entries)==36)
        require(all(re.fullmatch(r'(development|capability)-[a-z]+-[0-9]{2}',i) for i in self.entries))

    def actor_cases(self,split):
        require(split in ('development','capability'))
        return [self.load_input(e['id']) for e in self.freeze['entries'] if e['split']==split]

    def load_input(self,task_id):
        require(task_id in self.entries)
        e=self.entries[task_id]; value=read_bound(self.root,e['input_path'],e['input_sha256'])
        require(set(value)=={'schema','id','split','family','principal','fixture','execution','provenance'})
        require(value['schema']=='narrative-task-input-v1' and value['id']==task_id and value['principal']==e['principal'])
        require(value['split']==e['split'] and value['family']==e['family'])
        require('rubric' not in value and 'required_relationships' not in value)
        validate_execution(value)
        return copy.deepcopy(value)


def load_rubric(catalog,task_id):
    """Inspection-only entry point; do not call while constructing actor context."""
    e=catalog.entries[task_id]; value=read_bound(catalog.root,e['rubric_path'],e['rubric_sha256'])
    require(value['schema']=='narrative-independent-rubric-v1' and value['task_id']==task_id)
    require(value['input_sha256']==digest(catalog.load_input(task_id)))
    require(value['semantic_review_status']=='pending_independent_manual' and value['actor_or_response_derived'] is False)
    return value


def verify_pack(catalog):
    """Pure data inspection; no actor, provider, SQL or semantic scoring."""
    entities={}; lineages={}; source_ids={}; semantic={}; counts={}
    for split in ('development','capability'):
        entities[split]=set(); lineages[split]=set(); source_ids[split]=set(); semantic[split]=set()
        for task in catalog.actor_cases(split):
            rubric=load_rubric(catalog,task['id']); key=(split,task['family']); counts[key]=counts.get(key,0)+1
            entities[split].add(task['fixture']['vendor']); lineages[split].add(task['provenance']['lineage_id'])
            source_ids[split].update(s['object_id'] for s in task['fixture']['sources'])
            semantic[split].update(f['semantic_signature'] for f in rubric['required_relationships'])
            require(all(f['semantic_signature']==digest({'topic':f['topic'],'meaning':f['required_summary']}) for f in rubric['required_relationships']))
            require(task['provenance']['license']=='MIT' and task['provenance']['blind_or_main_held_unseen'] is False)
            require(len({s['object_id'] for s in task['fixture']['sources']})==len(task['fixture']['sources']))
            require(all(len(s['body'].encode())<=2048 for s in task['fixture']['sources']))
    require(len(entities['development'])==12 and len(entities['capability'])==24)
    for sets in (entities,lineages,source_ids,semantic):require(not sets['development'].intersection(sets['capability']),'suite_split_overlap')
    require(all(counts.get((split,f))==n for split,n in (('development',2),('capability',4)) for f in FAMILIES))
    return {'kind':'pure_data_inspection','tasks':36,'development':12,'capability':24,'families':6,
            'disjoint_entities_lineages_semantic_signatures':True,'actor_or_native_runs':0,
            'semantic_judgments':None,'freeze_sha256':catalog.freeze_sha256}
