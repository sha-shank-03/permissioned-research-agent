"""Pure source contracts: memory SQL/Drive/provider doubles, no sockets or processes.

Unsupported meaning is deliberately not promoted to correctness by span checks.
"""
import copy
from dataclasses import replace
import hashlib
import json
import unittest

from permissioned_research.domain import ResearchError, ResearchRequest
from permissioned_research.store import CorpusStore
from permissioned_research.upgrade.config import DOUBLE_MODEL, DispatchConfig
from permissioned_research.upgrade.double import completion
from permissioned_research.upgrade.drive.adapter import DriveAdapter
from permissioned_research.upgrade.drive.http import DriveHTTPTransport
from permissioned_research.upgrade.narrative.boundary import TOPICS, validate_draft, render_html
from permissioned_research.upgrade.narrative.double import AuthoredPolicy, MemoryTransport, authored_draft, observations_from_request
from permissioned_research.upgrade.narrative.drive_double import memory_drive_adapter
from permissioned_research.upgrade.narrative.fixtures import demo_fixture
from permissioned_research.upgrade.narrative.projection import CorpusProjection, DriveProjection
from permissioned_research.upgrade.narrative.provider import NarrativeAction, NarrativeProvider, decode
from permissioned_research.upgrade.narrative.workflow import FIXED_QUERIES, NarrativeWorkflow


class Clock:
    def __init__(self): self.now=0.0
    def __call__(self): return self.now


class Cancel:
    def __init__(self,clock): self.clock,self.set_value=clock,False
    def is_set(self): return self.set_value
    def wait(self,seconds): self.clock.now+=seconds; return self.set_value


def empty_proposal():
    return {'topics':[{'topic':t,'state':'unknown','claims':[]} for t in TOPICS]}


class NarrativePureTests(unittest.TestCase):
    def projection(self, user='priya', case='normal', fixture=None):
        fixture=fixture or demo_fixture(case)
        store=CorpusStore(':memory:',fixture); self.addCleanup(store.close)
        request=ResearchRequest(user,fixture['vendor'],fixture['as_of'])
        return CorpusProjection(store,request),request,store

    def seeded(self, user='priya', case='normal', fixture=None):
        projection,request,store=self.projection(user,case,fixture)
        hits=projection.search('CedarBridge',4)['hits']; observations=[]
        for hit in hits:
            result=projection.read(hit['handle'])
            observations.append({'action':'read','arguments':{'handle':hit['handle']},'result':result})
        return projection,request,store,authored_draft(observations,request.as_of)

    def workflow(self, *, user='priya', policy='adaptive', fault=None, hooks=None,
                 responder=None, case='normal', config=None, drive=False, observer=None):
        clock=Clock(); cancellation=Cancel(clock)
        if drive:
            adapter,fixture=memory_drive_adapter(user,case,clock=clock,cancellation=cancellation)
            request=ResearchRequest(user,'CedarBridge','2026-10-12')
            projection=DriveProjection(adapter,request); state=fixture
        else:
            projection,request,state=self.projection(user,case)
        config=config or DispatchConfig(DOUBLE_MODEL)
        transport=MemoryTransport(AuthoredPolicy(fault),responder)
        provider=NarrativeProvider(config,transport)
        actor=NarrativeWorkflow(projection,request,provider,config,policy=policy,hooks=hooks,
                                observer=observer,clock=clock,cancellation=cancellation)
        return actor,transport,state,clock,cancellation

    def error(self, code, function):
        with self.assertRaises(ResearchError) as caught: function()
        self.assertEqual(code,caught.exception.code)

    def test_unicode_offsets_revision_hash_and_pending_semantics(self):
        fixture=demo_fixture(); fixture['sources'][0]['body']='Résumé 😀\r\n'+fixture['sources'][0]['body']
        p,r,_,proposal=self.seeded(fixture=fixture)
        draft=validate_draft(proposal,r,p)
        self.assertEqual('needs_review',draft['outcome']); self.assertIsNone(draft['semantic_approval'])
        citation=draft['topics'][0]['claims'][0]['citations'][0]
        self.assertEqual(len('Résumé 😀\r\n'),citation['start'])
        doc=p.opened[citation['handle']]
        self.assertEqual(doc.body[citation['start']:citation['end']],citation['quote'])
        self.assertEqual(hashlib.sha256(doc.body.encode()).hexdigest(),citation['content_sha256'])
        self.assertIsNone(draft['independent_review']['claim_support'])

    def test_unrelated_valid_quote_does_not_prove_claim_support(self):
        p,r,_,proposal=self.seeded()
        proposal['topics'][0]['claims'][0]['text']='The vendor is certified for every regulated industry.'
        draft=validate_draft(proposal,r,p)
        claim=draft['topics'][0]['claims'][0]
        self.assertEqual('needs_review',draft['outcome'])
        self.assertIsNone(claim['semantic_review']['support'])
        self.assertEqual('pending_independent_review',claim['semantic_review']['status'])
        self.assertNotIn('semantic_entailment',draft['automated_checks'])

    def test_missing_qualifier_is_manual_review_pending_not_auto_approved(self):
        actor,_,_,_,_=self.workflow(fault='omit_qualifier')
        result=actor.run(); self.assertEqual('needs_review',result['outcome'])
        claim=result['draft']['topics'][2]['claims'][0]
        self.assertIn('always',claim['text']); self.assertIn('after security approval',claim['citations'][0]['quote'])
        self.assertIsNone(claim['semantic_review']['qualifiers'])
        self.assertIsNone(result['semantic_correctness']); self.assertIsNone(result['coverage'])

    def test_actor_cannot_supply_semantic_approval_or_review_fields(self):
        p,r,_,proposal=self.seeded()
        for field in ('semantic_approval','independent_review','principal','source_url'):
            changed=copy.deepcopy(proposal); changed[field]=True
            self.error('invalid_memo',lambda:validate_draft(changed,r,p))
        proposal['topics'][0]['claims'][0]['semantic_review']={'support':True}
        self.error('invalid_memo',lambda:validate_draft(proposal,r,p))

    def test_forged_unknown_borrowed_and_url_handles_fail(self):
        p,r,_,proposal=self.seeded()
        other,_,_,_=self.seeded('alex'); borrowed=next(iter(other.opened))
        for handle in ('e_'+'0'*32,borrowed,'https://attacker.invalid/file'):
            changed=copy.deepcopy(proposal); changed['topics'][0]['claims'][0]['quotes'][0]['handle']=handle
            self.error('invalid_memo',lambda:validate_draft(changed,r,p))

    def test_direct_guard_rejects_other_principal_request(self):
        p,r,_,proposal=self.seeded()
        self.error('invalid_memo',lambda:validate_draft(proposal,replace(r,principal='alex'),p))

    def test_citation_source_identity_is_derived_outside_model_arguments(self):
        p,r,_,proposal=self.seeded()
        draft=validate_draft(proposal,r,p)
        citation=draft['topics'][0]['claims'][0]['citations'][0]
        self.assertEqual(p.selection_key(citation['handle']),citation['source_id'])
        self.assertEqual('narrative-general',citation['source_id'])
        proposal['topics'][0]['claims'][0]['quotes'][0]['source_id']='narrative-private'
        self.error('invalid_memo',lambda:validate_draft(proposal,r,p))

    def test_bad_quote_revision_hash_boolean_and_byte_offsets_fail(self):
        p,r,_,proposal=self.seeded()
        for field,value in (('revision','999'),('content_sha256','0'*64),('quote','unrelated'),
                            ('start',True),('end',99999)):
            changed=copy.deepcopy(proposal); changed['topics'][0]['claims'][0]['quotes'][0][field]=value
            self.error('invalid_memo',lambda:validate_draft(changed,r,p))
        fixture=demo_fixture(); fixture['sources'][0]['body']='😀\n'+fixture['sources'][0]['body']
        p,r,_,proposal=self.seeded(fixture=fixture)
        proposal['topics'][0]['claims'][0]['quotes'][0]['start']+=3
        self.error('invalid_memo',lambda:validate_draft(proposal,r,p))

    def test_claim_and_quote_count_and_length_caps(self):
        p,r,_,proposal=self.seeded()
        claim=copy.deepcopy(proposal['topics'][0]['claims'][0])
        for changed_claim in ({**claim,'text':'x'*281},{**claim,'quotes':[]},
                              {**claim,'quotes':claim['quotes']*3}, {**claim,'text':' trailing '}):
            changed=empty_proposal(); changed['topics'][0].update(state='supported',claims=[changed_claim])
            self.error('invalid_memo',lambda:validate_draft(changed,r,p))
        okay=empty_proposal(); okay['topics'][0].update(state='supported',claims=[copy.deepcopy(claim) for _ in range(12)])
        self.assertEqual(12,len(validate_draft(okay,r,p)['topics'][0]['claims']))
        okay['topics'][1].update(state='supported',claims=[copy.deepcopy(claim)])
        self.error('invalid_memo',lambda:validate_draft(okay,r,p))

    def test_two_distinct_exact_quotes_allowed_duplicate_quote_rejected(self):
        p,r,_,proposal=self.seeded()
        first=proposal['topics'][0]['claims'][0]
        second=proposal['topics'][1]['claims'][0]['quotes'][0]
        first['quotes'].append(copy.deepcopy(second))
        self.assertEqual(2,len(validate_draft(proposal,r,p)['topics'][0]['claims'][0]['citations']))
        first['quotes'][1]=copy.deepcopy(first['quotes'][0])
        self.error('invalid_memo',lambda:validate_draft(proposal,r,p))

    def test_conflict_requires_distinct_accessible_current_source_proposals(self):
        p,r,_,proposal=self.seeded(case='conflict')
        draft=validate_draft(proposal,r,p)
        self.assertEqual('conflict',draft['topics'][0]['state'])
        self.assertTrue(draft['topics'][0]['state_is_proposal'])
        self.assertIsNone(draft['independent_review']['conflict_resolution'])
        proposal['topics'][0]['claims'][1]=copy.deepcopy(proposal['topics'][0]['claims'][0])
        self.error('invalid_memo',lambda:validate_draft(proposal,r,p))

    def test_stale_only_cannot_be_relabelled_current(self):
        p,r,_,proposal=self.seeded(case='stale')
        draft=validate_draft(proposal,r,p)
        self.assertEqual('stale_only',draft['topics'][0]['state'])
        proposal['topics'][0]['state']='supported'
        self.error('invalid_memo',lambda:validate_draft(proposal,r,p))

    def test_current_source_cannot_be_relabelled_stale(self):
        p,r,_,proposal=self.seeded()
        proposal['topics'][0]['state']='stale_only'
        self.error('invalid_memo',lambda:validate_draft(proposal,r,p))

    def test_future_effective_claim_is_not_current_evidence(self):
        fixture=demo_fixture()
        for source in fixture['sources']: source['effective_at']='2026-11-01'
        p,r,_,_=self.seeded(fixture=fixture); doc=next(iter(p.opened.values()))
        quote=doc.body.splitlines()[0]
        proposal=empty_proposal(); proposal['topics'][0].update(state='supported',claims=[{
            'text':'Future hosting claim','quotes':[{'handle':doc.handle,'revision':doc.revision,
            'content_sha256':doc.content_sha256,'start':0,'end':len(quote),'quote':quote}]}])
        self.error('invalid_memo',lambda:validate_draft(proposal,r,p))

    def test_unknown_is_empty_and_not_a_success_score(self):
        p,r,_,_=self.seeded()
        draft=validate_draft(empty_proposal(),r,p)
        self.assertEqual('needs_review',draft['outcome']); self.assertIsNone(draft['independent_review']['required_fact_coverage'])
        bad=empty_proposal(); bad['topics'][0]['state']='supported'
        self.error('invalid_memo',lambda:validate_draft(bad,r,p))

    def test_guard_checks_all_retained_dependencies_before_handoff(self):
        p,r,store,proposal=self.seeded()
        store.revoke_membership('priya','procurement','guard_revoke')
        self.error('source_state_changed',lambda:validate_draft(proposal,r,p))

    def test_renderer_escapes_source_and_model_text_without_links(self):
        p,r,_,proposal=self.seeded()
        proposal['topics'][0]['claims'][0]['text']='<script>alert(1)</script> https://attacker.invalid'
        draft=validate_draft(proposal,r,p); rendered=render_html(draft)
        self.assertNotIn('<script>',rendered); self.assertIn('&lt;script&gt;',rendered)
        self.assertNotIn('href=',rendered); self.assertNotIn('src=',rendered)

    def test_adaptive_native_observation_loop_needs_review_and_no_scores(self):
        actor,transport,_,_,_=self.workflow()
        result=actor.run(); self.assertEqual('needs_review',result['outcome']); self.assertIsNone(result['error'])
        self.assertEqual(1,result['counters']['searches']); self.assertEqual(3,result['counters']['reads'])
        self.assertEqual(5,result['provider_requests']); self.assertTrue(all(p.closed for p in transport.pending))
        self.assertIsNone(result['actual_cost']); self.assertFalse(result['live_model_executed'])

    def test_fixed_six_queries_one_synthesis_diversified_sources_same_provider(self):
        actor,transport,_,_,_=self.workflow(policy='fixed')
        result=actor.run(); self.assertEqual('needs_review',result['outcome'])
        self.assertEqual(6,result['counters']['searches']); self.assertEqual(12,result['counters']['backend_search_calls'])
        self.assertLessEqual(result['counters']['reads'],8); self.assertEqual(1,result['provider_requests'])
        task,control,observations=observations_from_request(transport.requests[0])
        self.assertEqual(list(FIXED_QUERIES),[o['arguments']['query'] for o in observations if o['action']=='search'])
        groups={o['result']['connector'] for o in observations if o['action']=='read' and o['result']['status']=='ok'}
        self.assertEqual({'docshub','teamchat'},groups); self.assertEqual('SYNTHESIZING',control['phase'])
        self.assertEqual(actor.config.model,transport.requests[0]['model'])

    def test_fixed_repair_is_at_most_one_and_does_not_repeat_collection(self):
        actor,_,_,_,_=self.workflow(policy='fixed',fault='bad_finish_once')
        result=actor.run(); self.assertEqual('needs_review',result['outcome'])
        self.assertEqual((2,1,6),(result['provider_requests'],result['counters']['repairs'],result['counters']['searches']))

    def test_repeated_self_approval_does_not_release_draft(self):
        actor,_,_,_,_=self.workflow(policy='fixed',fault='self_approve')
        result=actor.run(); self.assertEqual('failed',result['outcome']); self.assertEqual('invalid_decision',result['error'])
        self.assertEqual(2,result['provider_requests']); self.assertIsNone(result['draft']); self.assertIsNone(actor.sink.memo)

    def test_fixed_cannot_use_provider_to_add_queries(self):
        count=[0]
        def respond(_):
            count[0]+=1; return completion('search',{'query':'extra','limit':4},'x'+str(count[0]))
        actor,_,_,_,_=self.workflow(policy='fixed',responder=respond)
        result=actor.run(); self.assertEqual('invalid_decision',result['error'])
        self.assertEqual(6,result['counters']['searches']); self.assertEqual(2,result['provider_requests'])

    def test_malformed_action_repair_preserves_native_correlation(self):
        actor,transport,_,_,_=self.workflow(fault='malformed_once')
        result=actor.run(); self.assertEqual('needs_review',result['outcome']); self.assertEqual(1,result['counters']['repairs'])
        for request in transport.requests:
            messages=request['messages']
            for i in range(2,len(messages)-1,2):
                self.assertEqual(messages[i]['tool_calls'][0]['id'],messages[i+1]['tool_call_id'])

    def test_native_replayed_call_unknown_tool_and_extra_authority_rejected(self):
        config=DispatchConfig(DOUBLE_MODEL)
        for name,args in (('read',{'handle':'e_'+'a'*32,'principal':'priya'}),
                          ('http',{'url':'https://attacker.invalid'}),('approve',{})):
            self.error('invalid_decision',lambda:decode(completion(name,args),config,set()))
        self.error('invalid_decision',lambda:decode(completion('search',{'query':'q','limit':4},'used'),config,{'used'}))

    def test_native_multiple_calls_refusal_truncation_and_usage_bounds_fail(self):
        config=DispatchConfig(DOUBLE_MODEL); normal=json.loads(completion('search',{'query':'q','limit':4}))
        variants=[]
        for mutation in ('multiple','refusal','truncated','usage'):
            v=copy.deepcopy(normal)
            if mutation=='multiple': v['choices'][0]['message']['tool_calls']*=2
            elif mutation=='refusal': v['choices'][0]['message']['refusal']='untrusted refusal'
            elif mutation=='truncated': v['choices'][0]['finish_reason']='length'
            else: v['usage']['completion_tokens']=2049
            variants.append(json.dumps(v).encode())
        for body in variants: self.error('invalid_decision',lambda:decode(body,config,set()))

    def test_actor_request_excludes_principal_fixtures_labels_and_remote_selectors(self):
        actor,transport,_,_,_=self.workflow(user='alex'); result=actor.run()
        text=json.dumps(transport.requests)
        for forbidden in ('PROCUREMENT_ONLY_NARRATIVE','allow_groups','scenario','gold','credential','account_subject'):
            # System instruction says credentials; only structural selectors must be absent.
            if forbidden=='credential': self.assertNotIn('"credential"',text)
            else: self.assertNotIn(forbidden,text)
        self.assertNotIn('"principal"',text); self.assertEqual('needs_review',result['outcome'])

    def test_cross_principal_outputs_provider_requests_and_events_do_not_share_private_text(self):
        prior,_,_,_,_=self.workflow(user='priya'); priya=prior.run()
        self.assertIn('PROCUREMENT_ONLY_NARRATIVE',json.dumps(priya['draft']))
        actor,transport,_,_,_=self.workflow(user='alex'); alex=actor.run()
        self.assertNotIn('PROCUREMENT_ONLY_NARRATIVE',json.dumps(alex))
        self.assertNotIn('PROCUREMENT_ONLY_NARRATIVE',json.dumps(transport.requests))
        self.assertNotIn('CedarBridge',json.dumps(alex['events']))

    def test_revocation_before_send_clears_entire_history_and_keeps_budgets(self):
        cut=[None]
        def revoke(actor):
            if cut[0] is None and any('PROCUREMENT_ONLY_NARRATIVE' in d.body for d in actor.projection.opened.values()):
                cut[0]=len(actor.provider.transport.requests)
                actor.projection.store.revoke_membership('priya','procurement','before_send_revoke')
        actor,transport,_,_,_=self.workflow(hooks={'before_send':revoke})
        result=actor.run(); self.assertEqual('needs_review',result['outcome']); self.assertEqual(1,result['counters']['restarts'])
        self.assertGreater(result['counters']['searches'],1)
        self.assertNotIn('PROCUREMENT_ONLY_NARRATIVE',json.dumps(transport.requests[cut[0]:]))
        self.assertNotIn('PROCUREMENT_ONLY_NARRATIVE',json.dumps(result['draft']))

    def test_revocation_before_release_blocks_old_draft_and_releases_only_new_context(self):
        once=[False]
        def revoke(actor):
            if not once[0]:
                once[0]=True; actor.projection.store.revoke_membership('priya','procurement','before_release_revoke')
        actor,_,_,_,_=self.workflow(hooks={'before_release':revoke})
        result=actor.run(); self.assertEqual('needs_review',result['outcome']); self.assertEqual(1,result['counters']['restarts'])
        self.assertNotIn('PROCUREMENT_ONLY_NARRATIVE',json.dumps(actor.sink.memo))
        self.assertEqual(result['draft'],actor.sink.memo)

    def test_revocation_during_collection_runs_outside_sql_gate(self):
        cut=[False]
        def revoke(actor):
            self.assertFalse(actor.projection.store.connection.in_transaction)
            if not cut[0] and actor.projection.opened:
                cut[0]=True; actor.projection.store.revoke_membership('priya','procurement','collect_revoke')
        actor,_,_,_,_=self.workflow(hooks={'after_submit':revoke})
        result=actor.run(); self.assertEqual('needs_review',result['outcome']); self.assertEqual(1,result['counters']['restarts'])

    def test_second_context_change_fails_with_no_release(self):
        serial=[0]
        def change(actor):
            if actor.projection.opened:
                serial[0]+=1
                source=actor.projection.store.source_for('alex','narrative-general')
                actor.projection.store.replace_source(source.object_id,source.body,source.effective_at,source.review_until,'change'+str(serial[0]))
        actor,_,_,_,_=self.workflow(user='alex',hooks={'before_send':change})
        result=actor.run(); self.assertEqual('source_state_changed',result['error'])
        self.assertEqual(1,result['counters']['restarts']); self.assertIsNone(actor.sink.memo)

    def test_restarts_do_not_reset_request_tool_or_repair_ceiling(self):
        changed=[False]
        def change(actor):
            if not changed[0] and actor.projection.opened:
                changed[0]=True; actor.projection.store.revoke_membership('priya','procurement','budget_revoke')
        actor,_,_,_,_=self.workflow(config=DispatchConfig(DOUBLE_MODEL,max_requests=3),hooks={'before_send':change})
        result=actor.run(); self.assertEqual('budget_exhausted',result['error'])
        self.assertEqual(3,result['counters']['requests']); self.assertIsNone(result['draft'])

    def test_context_and_output_bounds_fail_without_silent_truncation(self):
        actor,transport,_,_,_=self.workflow(config=DispatchConfig(DOUBLE_MODEL,max_context_bytes=100))
        result=actor.run(); self.assertEqual('budget_exhausted',result['error']); self.assertEqual([],transport.requests)
        p,r,_,proposal=self.seeded()
        self.error('budget_exhausted',lambda:validate_draft(proposal,r,p,max_bytes=100))

    def test_cancel_deadline_and_fresh_attempt_only(self):
        actor,transport,_,clock,cancel=self.workflow(); cancel.set_value=True
        result=actor.run(); self.assertEqual('cancelled',result['outcome']); self.assertEqual([],transport.requests)
        self.error('attempt_state_conflict',actor.run)
        actor,transport,_,clock,_=self.workflow(hooks={'before_send':lambda a:setattr(a.clock,'now',121)})
        self.assertEqual('budget_exhausted',actor.run()['error']); self.assertEqual([],transport.requests)

    def test_drive_to_narrative_memory_seam_for_three_principals(self):
        for user in ('alex','priya','sam'):
            actor,transport,_,_,_=self.workflow(user=user,drive=True)
            result=actor.run(); self.assertEqual('needs_review',result['outcome']); self.assertFalse(result['remote_authorization_proven'])
            for topic in result['draft']['topics']:
                for claim in topic['claims']:
                    for citation in claim['citations']: self.assertEqual('google_drive',citation['connector'])
            if user!='priya': self.assertNotIn('PROCUREMENT_ONLY_NARRATIVE',json.dumps(transport.requests))

    def test_drive_metadata_replacement_before_handoff_invalidates_all_old_spans(self):
        once=[False]
        def replace_body(actor):
            if not once[0]:
                once[0]=True
                fixture=actor.projection.adapter._transport.fixture
                source=fixture.files['narrative-general']; fixture.replace('narrative-general',source['body'].replace('ten business','eleven business'))
        actor,_,_,_,_=self.workflow(user='alex',drive=True,hooks={'before_release':replace_body})
        result=actor.run(); self.assertEqual('needs_review',result['outcome']); self.assertEqual(1,result['counters']['restarts'])
        self.assertIn('eleven business',result['draft']['topics'][2]['claims'][0]['text'])
        self.assertEqual('2',result['draft']['topics'][2]['claims'][0]['citations'][0]['revision'])

    def test_drive_revocation_before_send_drops_old_private_projection(self):
        once=[False]
        def revoke(actor):
            if not once[0] and any('PROCUREMENT_ONLY_NARRATIVE' in d.body for d in actor.projection.opened.values()):
                once[0]=True; actor.projection.adapter._transport.fixture.revoke('narrative-private')
        actor,_,_,_,_=self.workflow(drive=True,hooks={'before_send':revoke})
        result=actor.run(); self.assertEqual('needs_review',result['outcome']); self.assertEqual(1,result['counters']['restarts'])
        self.assertNotIn('PROCUREMENT_ONLY_NARRATIVE',json.dumps(result['draft']))

    def test_drive_seam_blocks_production_transport_and_principal_substitution_before_http(self):
        clock=Clock(); adapter,_=memory_drive_adapter('priya',clock=clock,cancellation=Cancel(clock))
        request=ResearchRequest('alex','CedarBridge','2026-10-12')
        self.error('invalid_request',lambda:DriveProjection(adapter,request))
        live=DriveAdapter(adapter._scope,adapter._credential,DriveHTTPTransport(),clock=clock,cancellation=Cancel(clock))
        self.error('invalid_request',lambda:DriveProjection(live,replace(request,principal='priya')))
        self.assertEqual([],live._transport.owned); adapter.close(); live.close()

    def test_fixed_drive_seam_retains_six_queries_and_same_caps(self):
        actor,_,_,_,_=self.workflow(user='alex',drive=True,policy='fixed')
        result=actor.run(); self.assertEqual('needs_review',result['outcome'])
        self.assertEqual(6,result['counters']['searches']); self.assertLessEqual(result['counters']['reads'],8)
        self.assertEqual(1,result['provider_requests'])


if __name__=='__main__': unittest.main()
