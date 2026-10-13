"""Versioned, read-only source/parent preflight. No SQL or actor invocation."""
import copy
import hashlib
from pathlib import Path
import re

from ..narrative_correction_v2 import validate_proposal
from ..narrative_suite.data import InputCatalog, digest, require, unique_json
from ..narrative_suite.ledger import Ledger

ROOT=Path(__file__).resolve().parents[3]
SEAL='evaluation/upgrade/U2B2_SUPPLEMENT_SOURCE_MANIFEST.json'
PROPOSAL='evaluation/upgrade/U2B2_CONTINUATION_V2.json'
CHECKPOINT='fixtures/upgrade/narrative-correction-v2/parent-checkpoint.json'
CORRECTION='evaluation/upgrade/U2B2_CORRECTION_SOURCE_MANIFEST.json'
OUTPUT='work/main-u2b2-supplement-v3'
PARENT='work/main-u2b2-1239'
LIMITS={'run_seconds':520,'slot_seconds':120,'raw_output_bytes':64*1024*1024,
        'slot_reserve_bytes':6*1024*1024,'working_ram_mib':512,
        'python_processes_including_recorder':3,'owned_client_children':1,
        'owned_loopback_listeners':1,'downloads':0,'external_calls':0,'live_model_calls':0}


def file_hash(path,maximum=64*1024*1024):
    path=Path(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size<=maximum,'supplement_file_missing_or_invalid')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path,maximum=128*1024):
    path=Path(path); file_hash(path,maximum)
    return unique_json(path.read_text(encoding='utf-8'))


def source_file(root,relative):
    require(type(relative) is str and not Path(relative).is_absolute(),'supplement_source_path_invalid')
    path=(root/relative).resolve(); require(path.is_relative_to(root.resolve()),'supplement_source_path_invalid')
    return path


class Context:
    def __init__(self,root,execution_sha256,population_review,lease_id):
        self.root=Path(root).resolve(); self.execution_sha256=execution_sha256
        require(type(execution_sha256) is str and re.fullmatch('[0-9a-f]{64}',execution_sha256),'supplement_execution_version_invalid')
        require(type(lease_id) is str and re.fullmatch('[a-z][a-z0-9_-]{2,79}',lease_id),'supplement_lease_label_invalid')
        self.population_review=Path(population_review).resolve(); self.lease_id=lease_id
        require(file_hash(self.root/SEAL)==execution_sha256,'supplement_source_changed')
        self.manifest=read_json(self.root/SEAL)
        require(self.manifest['manifest_id']=='u2b2-supplement-execution-source-v3','supplement_source_invalid')
        require(self.manifest['code_sha256']==digest(self.manifest['files']) and len(self.manifest['preserved_files'])==184,'supplement_source_invalid')
        require(self.manifest['preserved_sha256']==digest(self.manifest['preserved_files']),'supplement_source_invalid')
        self.proposal=read_json(self.root/PROPOSAL); self.checkpoint=read_json(self.root/CHECKPOINT,512*1024)
        self.correction=read_json(self.root/CORRECTION)
        self.catalog=InputCatalog(self.root/'fixtures/upgrade/narrative-v2')
        self.out=source_file(self.root,OUTPUT)
        require(not self.out.is_relative_to(source_file(self.root,PARENT)),'supplement_output_parent_overlap')
        self.blueprint={'schema':'narrative-supplement-plan-v3','execution_source_manifest_sha256':execution_sha256,
            'execution_code_sha256':self.manifest['code_sha256'],
            'actor_source_manifest_sha256':self.proposal['bindings']['actor_source_manifest_sha256'],
            'correction_source_manifest_sha256':file_hash(self.root/CORRECTION),
            'proposal_file_sha256':file_hash(self.root/PROPOSAL),'proposal_content_sha256':digest(self.proposal),
            'parent_bindings':copy.deepcopy(self.proposal['bindings']),
            'original_actor_bindings':copy.deepcopy(self.checkpoint['capability_plan']['bindings']),
            'attempts':copy.deepcopy(self.proposal['attempts']),'limits':copy.deepcopy(LIMITS),
            'parent_relative_root':PARENT,'output_relative_root':OUTPUT,'lease_label':lease_id,
            'lease_label_is_not_authorization':True,'hypothesis_version':self.proposal['version'],
            'semantic_quality':None,'aggregate_full_plan_safety':None}

    def verify(self):
        require(file_hash(self.root/SEAL)==self.execution_sha256,'supplement_source_changed')
        require(read_json(self.root/PROPOSAL)==self.proposal and read_json(self.root/CHECKPOINT,512*1024)==self.checkpoint,'supplement_proposal_changed')
        for entry in self.manifest['files']+self.manifest['preserved_files']:
            require(file_hash(source_file(self.root,entry['path']))==entry['sha256'],'supplement_source_changed')
        parent=source_file(self.root,PARENT)
        entries=[]
        for path in sorted(parent.rglob('*')):
            require(not path.is_symlink(),'supplement_parent_evidence_changed')
            if path.is_file():entries.append({'path':path.relative_to(parent).as_posix(),'sha256':file_hash(path),'bytes':path.stat().st_size})
        require(entries==self.checkpoint['parent_cohort_inventory'],'supplement_parent_evidence_changed')
        cap=Ledger(parent/'capability'); dev=Ledger(parent/'development')
        cs=cap.inspect(); ds=dev.inspect()
        code_files=[{'path':e['path'],'sha256':file_hash(source_file(self.root,e['path']))} for e in self.correction['correction_code_files']]
        bindings={'actor_source_manifest_sha256':file_hash(self.root/'evaluation/upgrade/U2B2_SOURCE_MANIFEST.json'),
            'frozen_native_commands_sha256':file_hash(self.root/'evaluation/upgrade/U2B2_NATIVE_COMMANDS.json'),
            'capability_plan_sha256':cap.plan_sha256,'capability_journal_sha256':cs['journal_sha256'],
            'development_plan_sha256':dev.plan_sha256,'development_journal_sha256':ds['journal_sha256'],
            'population_review_sha256':file_hash(self.population_review),
            'parent_cohort_inventory_sha256':digest(entries),'correction_code_sha256':digest(code_files)}
        validate_proposal(self.proposal,self.checkpoint,bindings)
        require(cap.plan==self.checkpoint['capability_plan'] and cs['counts']=={'terminal':45,'interrupted_unknown':0,'unexecuted':3} and ds['counts']=={'terminal':24,'interrupted_unknown':0,'unexecuted':0},'supplement_parent_checkpoint_changed')
        require(not self.out.is_symlink(),'supplement_output_invalid')
        if self.out.exists():
            require(all(not p.is_symlink() for p in self.out.rglob('*')),'supplement_output_invalid')
        for entry in self.blueprint['attempts']:self.load_task(entry)
        return True

    def load_task(self,entry):
        from permissioned_research.upgrade.config import DispatchConfig, DOUBLE_MODEL
        task=self.catalog.load_input(entry['task_id']); indexed=self.catalog.entries[entry['task_id']]
        require(entry['input_sha256']==indexed['input_sha256'] and entry['rubric_sha256']==indexed['rubric_sha256'],'supplement_task_binding_changed')
        require(task['principal']==entry['principal'] and task['family']==entry['family'],'supplement_task_binding_changed')
        # Hash the rubric bytes; its contents never enter actor construction.
        require(file_hash(self.catalog.root/indexed['rubric_path'])==entry['rubric_sha256'],'supplement_rubric_changed')
        config=DispatchConfig(DOUBLE_MODEL,**task['execution']['config_overrides'])
        require(config.sha256==entry['config_sha256'] and config.total_seconds==120,'supplement_budget_changed')
        return task

    def generated_bytes(self):
        parent_bytes=sum(e['bytes'] for e in self.checkpoint['parent_cohort_inventory'])
        return parent_bytes+sum(p.stat().st_size for p in self.out.rglob('*') if p.is_file()) if self.out.exists() else parent_bytes
