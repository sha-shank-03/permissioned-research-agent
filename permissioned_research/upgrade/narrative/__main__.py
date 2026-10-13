"""Explicit future local-double walkthrough. No live/external route or credentials."""
import argparse
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

from ...domain import ResearchError, ResearchRequest
from ...store import CorpusStore
from ..config import DispatchConfig, require_local_model
from ..drive.contract import DriveError
from ..transport import GatewayTransport
from .double import narrative_http_double
from .drive_double import drive_http_double
from .fixtures import demo_fixture
from .projection import CorpusProjection, DriveProjection
from .provider import NarrativeProvider, PROMPT_SHA256
from .workflow import NarrativeWorkflow


def source_identity():
    try:
        root=Path(__file__).resolve().parents[3]
        manifest_path=root/'evaluation/upgrade/U2B1_SOURCE_MANIFEST.json'
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        for entry in manifest['files']:
            path=(root/entry['path']).resolve()
            if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:
                raise ResearchError('state_incompatible')
        return manifest['manifest_id'],manifest['code_sha256'],hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    except (OSError,ValueError,TypeError,KeyError):
        raise ResearchError('state_incompatible') from None


def main(argv=None):
    parser=argparse.ArgumentParser(description='Authored native narrative draft; lease required, semantic review pending.')
    parser.add_argument('command',choices=['demo'])
    parser.add_argument('--provider',choices=['gateway-double'],required=True)
    parser.add_argument('--model',required=True)
    parser.add_argument('--policy',choices=['adaptive','fixed'],required=True)
    parser.add_argument('--connector',choices=['corpus','drive-double'],required=True)
    parser.add_argument('--principal',choices=['alex','priya','sam'],required=True)
    parser.add_argument('--case',choices=['normal','conflict','stale','missing'],required=True)
    parser.add_argument('--fault',choices=['malformed_once','bad_finish_once','omit_qualifier','self_approve'])
    parser.add_argument('--change',choices=['revoke-before-send','revoke-before-release','replace-before-release'])
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args(argv)
    output=None
    try:
        if sys.version_info[:2]!=(3,11):
            raise ResearchError('invalid_request')
        config=DispatchConfig(args.model); require_local_model(config)
        manifest_id,code_hash,manifest_hash=source_identity()
        if args.change and (args.policy!='adaptive' or (args.change.startswith('revoke') and args.principal!='priya')):
            raise ResearchError('invalid_request')
        path=args.out.resolve(); html_path=path.with_suffix('.html')
        if path.exists() or html_path.exists():
            raise ResearchError('attempt_state_conflict')
        path.parent.mkdir(parents=True,exist_ok=True)
        output=path.open('x',encoding='utf-8')
        fixture=demo_fixture(args.case)
        request=ResearchRequest(args.principal,fixture['vendor'],fixture['as_of'])
        with ExitStack() as stack:
            if args.connector=='corpus':
                store=CorpusStore(':memory:',fixture); stack.callback(store.close)
                projection=CorpusProjection(store,request); drive_transport=None
            else:
                adapter,drive_fixture,drive_calls=stack.enter_context(drive_http_double(args.principal,args.case))
                drive_transport=adapter._transport
                projection=DriveProjection(adapter,request)
            endpoint,captured=stack.enter_context(narrative_http_double(fault=args.fault))
            transport=GatewayTransport(config,endpoint=endpoint)
            provider=NarrativeProvider(config,transport)
            changed=[False]
            def change(actor):
                if changed[0]:
                    return
                if args.change=='revoke-before-send' and not any('PROCUREMENT_ONLY_NARRATIVE' in d.body
                                                                  for d in actor.projection.opened.values()):
                    return
                changed[0]=True
                if args.connector=='corpus':
                    if args.change.startswith('revoke'):
                        store.revoke_membership(args.principal,'procurement','authored_narrative_revocation')
                    else:
                        source=store.source_for(args.principal,'narrative-general')
                        store.replace_source(source.object_id,source.body.replace('ten business','eleven business'),
                                             source.effective_at,source.review_until,'authored_narrative_replacement')
                elif args.change.startswith('revoke'):
                    drive_fixture.revoke('narrative-private')
                else:
                    source=drive_fixture.files['narrative-general']
                    drive_fixture.replace('narrative-general',source['body'].replace('ten business','eleven business'))
            hooks={('before_send' if args.change=='revoke-before-send' else 'before_release'):change} if args.change else None
            report=NarrativeWorkflow(projection,request,provider,config,policy=args.policy,hooks=hooks).run()
            report['authored_control_plane_change']=args.change
            report.update(config=asdict(config),config_sha256=config.sha256,prompt_sha256=PROMPT_SHA256,
                          schema='research-run-u2b1-v1',source_manifest_id=manifest_id,
                          source_manifest_sha256=manifest_hash,code_sha256=code_hash,
                          model=config.model,provider='owned_narrative_gateway_double',generation_id=None,
                          usage_kind='authored_double_unknown',model_tokens=None,
                          dataset_sha256=hashlib.sha256(json.dumps(fixture,sort_keys=True).encode()).hexdigest(),
                          observed_gateway_double_requests=len(captured),
                          observed_drive_double_requests=len(drive_calls) if drive_transport else 0,
                          drive_http_attempts=adapter.attempts if drive_transport else 0,
                          drive_received_content_bytes=adapter.content_bytes if drive_transport else 0,
                          owned_gateway_children_remaining=transport.owned_children_remaining,
                          owned_gateway_threads_remaining=transport.owned_threads_remaining,
                          owned_drive_children_remaining=drive_transport.owned_children_remaining if drive_transport else 0,
                          owned_drive_threads_remaining=drive_transport.owned_threads_remaining if drive_transport else 0)
        report['owned_loopback_doubles_remaining']=0  # Both contexts explicitly joined/closed.
        output.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n'); output.flush()
        if report['html'] is not None:
            with html_path.open('x',encoding='utf-8') as h:
                h.write(report['html'])
        print(json.dumps({'protocol':report['protocol'],'outcome':report['outcome'],'error':report['error'],
                          'semantic_correctness':None,'coverage':None,'provider_requests':report['provider_requests'],
                          'live_model_executed':False,'remote_authorization_proven':False}))
        return 0 if report['outcome']=='needs_review' else 3
    except (ResearchError,DriveError,OSError) as error:
        safe={'protocol':'research-narrative-v2','outcome':'failed','error':getattr(error,'code','storage_error'),
              'live_model_executed':False,'remote_authorization_proven':False,'semantic_correctness':None}
        if output is not None and output.tell()==0:
            output.write(json.dumps(safe)+'\n')
        print(json.dumps(safe),file=sys.stderr); return 2 if output is None else 3
    finally:
        if output:
            output.close()


if __name__=='__main__':
    raise SystemExit(main())
