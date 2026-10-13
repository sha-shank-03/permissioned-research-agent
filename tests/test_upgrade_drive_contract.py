"""Authored in-memory U2-A contracts: no sockets, child processes or credentials."""
from dataclasses import replace
import hashlib
import json
import unittest

from permissioned_research.upgrade.drive.adapter import DriveAdapter, TextSnapshot
from permissioned_research.upgrade.drive.contract import (
    DriveError, FIELDS, GetRequest, Limits, OperatorScope, FilePolicy, READONLY,
    Reply, UserCredential, search_terms)

BODY = 'Hosting region is EU.\r\nSupport: café 😀.\n'.encode('utf-8')


def scope(principal='priya'):
    number = {'alex':'101','priya':'102','sam':'103'}[principal]
    return OperatorScope(principal,number,('general',),
                         (FilePolicy('text_1','general','2026-10-01','2027-10-01'),))


def credential(principal='priya'):
    return UserCredential(principal,scope(principal).account_subject,'AUTHORED_FAKE',1000,
                          frozenset({READONLY}))


def meta(body=BODY, **changes):
    value = {'id':'text_1','name':'Authored text','mimeType':'text/plain',
             'parents':['general'],'trashed':False,'version':'1','size':str(len(body)),
             'md5Checksum':hashlib.md5(body).hexdigest(),'capabilities':{'canDownload':True}}
    value.update(changes)
    return value


def reply(value, status=200):
    return Reply(status,json.dumps(value).encode())


def listing(*files, **values):
    return reply({'files':list(files), **values})


class Clock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now


class Cancellation:
    def __init__(self, clock):
        self.clock, self.cancelled = clock, False
    def is_set(self):
        return self.cancelled
    def wait(self, seconds):
        self.clock.now += seconds
        return self.cancelled


class Script:
    evidence_kind = 'authored_in_memory_contract'
    def __init__(self, *responses):
        self.responses, self.calls, self.closed = list(responses), [], False
    def get(self, request, cred, deadline, cancel, cleanup):
        self.calls.append((request,cred.subject))
        if not self.responses:
            raise AssertionError('Unexpected GET in pure contract')
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value(request,cred,deadline,cancel,cleanup) if callable(value) else value
    def close(self):
        self.closed = True


class DriveContractTests(unittest.TestCase):
    def adapter(self, *responses, user='priya', limits=None, cred=None):
        clock = Clock()
        cancel = Cancellation(clock)
        transport = Script(*responses)
        adapter = DriveAdapter(scope(user),cred or credential(user),transport,
                               limits=limits,clock=clock,wall_clock=lambda:100,
                               cancellation=cancel)
        return adapter,transport,clock,cancel

    def error(self, code, action):
        with self.assertRaises(DriveError) as caught:
            action()
        self.assertEqual(code,caught.exception.code)
        self.assertEqual(code,str(caught.exception))

    def test_three_delegated_bindings_use_their_own_subject(self):
        for user in ('alex','priya','sam'):
            a,t,_,_ = self.adapter(listing(meta()),user=user)
            self.assertEqual('ok',a.search('hosting')['status'])
            self.assertEqual([scope(user).account_subject],[subject for _,subject in t.calls])

    def test_missing_credential_blocks_before_any_get(self):
        t = Script()
        self.error('drive_authentication_required',lambda:DriveAdapter(scope(),None,t))
        self.assertEqual([],t.calls)

    def test_owner_service_and_other_user_do_not_substitute(self):
        for bad in (replace(credential(),kind='owner'), replace(credential(),kind='service_account'),
                    credential('alex'),replace(credential(),subject='999')):
            t = Script()
            self.error('drive_authentication_required',lambda:DriveAdapter(scope(),bad,t,wall_clock=lambda:100))
            self.assertEqual([],t.calls)

    def test_scope_expiry_issuer_and_header_injection_block(self):
        for bad in (replace(credential(),scopes=frozenset({'https://www.googleapis.com/auth/drive'})),
                    replace(credential(),expires_at=100), replace(credential(),expires_at=float('nan')),
                    replace(credential(),issuer='https://attacker.invalid'),
                    replace(credential(),token='fake\r\nOther: bad')):
            self.error('drive_authentication_required',lambda:DriveAdapter(scope(),bad,Script(),wall_clock=lambda:100))

    def test_expired_credential_on_subsequent_request_clears_context(self):
        a,t,_,_ = self.adapter(listing(meta()))
        a.search('hosting')
        a.wall_clock = lambda:1000
        self.error('drive_authentication_required',a.check_context)
        self.assertEqual(1,len(t.calls)); self.assertEqual({},a._handles)

    def test_operator_scope_rejects_mutable_and_unpinned_files(self):
        self.error('drive_invalid_request',lambda:replace(scope(),folders=['general']))
        self.error('drive_invalid_request',lambda:replace(scope(),files=(FilePolicy('id','other','2026-01-01','2027-01-01'),)))
        self.error('drive_invalid_request',lambda:FilePolicy('https://attacker','general','2026-01-01','2027-01-01'))

    def test_search_escapes_query_and_pins_fields_folder_mime(self):
        a,t,_,_ = self.adapter(listing(meta()))
        result = a.search("a'b c\\d")
        params = dict(t.calls[0][0].params)
        self.assertIn("'general' in parents",params['q'])
        self.assertIn("fullText contains 'a\\'b'",params['q'])
        self.assertIn("fullText contains 'c\\\\d'",params['q'])
        self.assertIn("trashed = false",params['q']); self.assertIn("mimeType = 'text/plain'",params['q'])
        self.assertNotIn('excerpt',result['hits'][0]); self.assertNotIn('id',result['hits'][0])

    def test_query_and_limits_reject_unbounded_or_control_input(self):
        for text in ('', 'a '*9, 'x'*513, 'hello\x00world'):
            self.error('drive_invalid_request',lambda:search_terms(text))
        for values in ({'max_attempts':181},{'max_pages':3},{'max_get_retries':3},
                       {'max_file_bytes':32769},{'total_seconds':float('inf')}):
            self.error('drive_invalid_request',lambda:Limits(**values))

    def test_returned_unallowlisted_file_never_gets_a_handle(self):
        a,t,_,_ = self.adapter(listing(meta(id='other',name='PRIVATE_UNAPPROVED_TITLE')))
        self.assertEqual([],a.search('hosting')['hits'])
        self.assertEqual(1,len(t.calls)); self.assertNotIn('PRIVATE',json.dumps(a.events))

    def test_no_raw_id_url_or_borrowed_handle_authority(self):
        a,t,_,_ = self.adapter(listing(meta()))
        handle = a.search('hosting')['hits'][0]['handle']
        b,u,_,_ = self.adapter()
        for raw in ('text_1','https://www.googleapis.com/drive/v3/files/text_1',handle):
            self.assertEqual({'status':'not_available'},b.read(raw))
        self.assertEqual([],u.calls)

    def test_exact_unicode_text_version_and_hash_are_preserved(self):
        a,t,_,_ = self.adapter(listing(meta()),reply(meta()),Reply(200,BODY,'text/plain'),reply(meta()),reply(meta()))
        h = a.search('hosting')['hits'][0]['handle']
        s = a.read(h)
        self.assertIsInstance(s,TextSnapshot)
        self.assertEqual(BODY.decode('utf-8'),s.text)
        self.assertEqual(hashlib.sha256(BODY).hexdigest(),s.content_sha256)
        self.assertEqual(('1','2026-10-01','2027-10-01'),(s.version,s.effective_at,s.review_until))
        self.assertNotIn(s.text,repr(s)); a.check_context()
        self.assertEqual(5,len(t.calls))

    def test_parent_mime_trash_and_download_denial_stop_before_media(self):
        for changed in ({'parents':['other']},{'mimeType':'application/vnd.google-apps.document'},
                        {'trashed':True},{'capabilities':{'canDownload':False}}):
            a,t,_,_ = self.adapter(listing(meta()),reply(meta(**changed)))
            h = a.search('hosting')['hits'][0]['handle']
            self.error('drive_not_available',lambda:a.read(h))
            self.assertEqual(2,len(t.calls)); self.assertEqual({},a._handles)

    def test_version_change_before_read_discards_all_handles(self):
        a,t,_,_ = self.adapter(listing(meta()),reply(meta(version='2')))
        h = a.search('hosting')['hits'][0]['handle']
        self.error('drive_state_changed',lambda:a.read(h))
        self.assertEqual({},a._handles); self.assertEqual(2,a.attempts)
        self.assertEqual({'status':'not_available'},a.read(h))

    def test_version_change_during_content_does_not_release_text(self):
        a,_,_,_ = self.adapter(listing(meta()),reply(meta()),Reply(200,BODY,'text/plain'),reply(meta(version='2')))
        h = a.search('hosting')['hits'][0]['handle']
        self.error('drive_state_changed',lambda:a.read(h)); self.assertEqual({},a._opened)

    def test_content_hash_mismatch_fails_even_if_version_stable(self):
        a,_,_,_ = self.adapter(listing(meta()),reply(meta()),Reply(200,b'x'*len(BODY),'text/plain'),reply(meta()))
        h = a.search('hosting')['hits'][0]['handle']
        self.error('drive_state_changed',lambda:a.read(h)); self.assertEqual({},a._opened)

    def test_invalid_utf8_not_replaced_or_normalized(self):
        bad = b'\xff\xfe'
        a,_,_,_ = self.adapter(listing(meta(bad)),reply(meta(bad)),Reply(200,bad,'text/plain'),reply(meta(bad)))
        h = a.search('hosting')['hits'][0]['handle']
        self.error('drive_schema_invalid',lambda:a.read(h))

    def test_recheck_denial_after_read_discards_retained_text(self):
        for status in (403,404):
            a,t,_,_ = self.adapter(listing(meta()),reply(meta()),Reply(200,BODY,'text/plain'),reply(meta()),reply({'secret':'PRIVATE'},status))
            h = a.search('hosting')['hits'][0]['handle']; a.read(h)
            self.error('drive_not_available',a.check_context)
            self.assertEqual({},a._opened); self.assertEqual({},a._handles)
            self.assertEqual(5,a.attempts); self.assertNotIn('PRIVATE',json.dumps(a.events))

    def test_final_recheck_version_drift_fails(self):
        a,_,_,_ = self.adapter(listing(meta()),reply(meta()),Reply(200,BODY,'text/plain'),reply(meta()),reply(meta(version='3')))
        h = a.search('hosting')['hits'][0]['handle']; a.read(h)
        self.error('drive_state_changed',a.check_context)

    def test_401_never_retries_or_changes_credentials(self):
        a,t,_,_ = self.adapter(reply({'error':{'message':'SECRET_FAKE'}},401))
        self.error('drive_authentication_required',lambda:a.search('hosting'))
        self.assertEqual(1,len(t.calls)); self.assertEqual(0,a.retries)
        self.assertNotIn('SECRET_FAKE',json.dumps(a.events)); self.assertNotIn('AUTHORED_FAKE',repr(credential()))

    def test_quota_get_retry_bound_is_cumulative(self):
        quota = reply({'error':{'errors':[{'reason':'userRateLimitExceeded','message':'SECRET'}]}},403)
        a,t,_,_ = self.adapter(quota,reply({},503),listing(meta()),quota)
        a.search('hosting'); self.assertEqual(2,a.retries)
        self.error('drive_rate_limited',a.check_context)
        self.assertEqual(4,len(t.calls)); self.assertEqual(2,a.retries)
        self.assertNotIn('SECRET',json.dumps(a.events))

    def test_unknown_or_mixed_403_reasons_do_not_retry(self):
        for reasons in ([{'reason':'unknown'}],[{'reason':'rateLimitExceeded'},{'reason':'insufficientFilePermissions'}]):
            a,t,_,_ = self.adapter(reply({'error':{'errors':reasons}},403))
            self.error('drive_not_available',lambda:a.search('hosting'))
            self.assertEqual(1,len(t.calls))

    def test_429_and_5xx_stop_after_two_safe_get_retries(self):
        for status,code in ((429,'drive_rate_limited'),(503,'drive_unavailable')):
            a,t,_,_ = self.adapter(*[reply({},status)]*3)
            self.error(code,lambda:a.search('hosting')); self.assertEqual(3,len(t.calls))

    def test_page_cap_and_incomplete_flag_preserve_non_exhaustiveness(self):
        a,t,_,_ = self.adapter(listing(meta(),nextPageToken='p1'),listing(nextPageToken='p2',incompleteSearch=True))
        result = a.search('hosting')
        self.assertFalse(result['complete']); self.assertEqual(1,len(result['hits']))
        self.assertEqual('p1',dict(t.calls[1][0].params)['pageToken'])

    def test_rejected_page_discards_partial_results_and_restarts_once(self):
        a,t,_,_ = self.adapter(listing(meta(),nextPageToken='p1'),reply({},400),listing())
        result = a.search('hosting')
        self.assertTrue(result['pagination_restarted']); self.assertEqual([],result['hits'])
        self.assertNotIn('pageToken',dict(t.calls[2][0].params))

    def test_second_rejected_page_stops_without_handles(self):
        a,_,_,_ = self.adapter(listing(meta(),nextPageToken='p1'),reply({},400),
                               listing(meta(),nextPageToken='p2'),reply({},400))
        self.error('drive_page_rejected',lambda:a.search('hosting')); self.assertEqual({},a._handles)

    def test_schema_extra_url_missing_version_duplicate_json_and_types_fail(self):
        bad = meta(); del bad['version']
        for response in (listing(meta(webContentLink='https://attacker.invalid')),
                         listing(bad),Reply(200,b'{"files":[],"files":[]}'),
                         listing(incompleteSearch=1),listing(meta(version=1))):
            a,t,_,_ = self.adapter(response)
            self.error('drive_schema_invalid',lambda:a.search('hosting')); self.assertEqual(1,len(t.calls))

    def test_repeated_page_token_fails(self):
        a,_,_,_ = self.adapter(listing(meta(),nextPageToken='same'),listing(nextPageToken='same'))
        self.error('drive_schema_invalid',lambda:a.search('hosting'))

    def test_request_budget_survives_invalidation(self):
        a,_,_,_ = self.adapter(reply({},404),limits=Limits(max_attempts=1))
        self.error('drive_not_available',lambda:a.search('hosting'))
        self.error('drive_budget_exhausted',lambda:a.search('hosting')); self.assertEqual(1,a.attempts)

    def test_response_file_and_total_content_budgets(self):
        a,_,_,_ = self.adapter(Reply(200,b'x'*101),limits=Limits(max_response_bytes=100))
        self.error('drive_schema_invalid',lambda:a.search('hosting'))
        a,_,_,_ = self.adapter(listing(meta(size='32769')))
        self.error('drive_budget_exhausted',lambda:a.search('hosting'))
        a,_,_,_ = self.adapter(listing(meta()),reply(meta()),Reply(200,BODY,'text/plain'),
                               limits=Limits(max_content_bytes=len(BODY)-1))
        h = a.search('hosting')['hits'][0]['handle']
        self.error('drive_budget_exhausted',lambda:a.read(h)); self.assertEqual(len(BODY),a.content_bytes)

    def test_cancel_and_deadline_stop_before_new_get(self):
        a,t,clock,cancel = self.adapter()
        cancel.cancelled = True
        self.error('drive_cancelled',lambda:a.search('hosting')); self.assertEqual([],t.calls)
        cancel.cancelled = False; clock.now = 120
        self.error('drive_timeout',lambda:a.search('hosting')); self.assertEqual([],t.calls)

    def test_rate_spacing_and_deadline_include_gets_and_waits(self):
        a,t,clock,_ = self.adapter(listing(meta()),reply(meta()),Reply(200,BODY,'text/plain'),reply(meta()))
        h = a.search('hosting')['hits'][0]['handle']; a.read(h)
        self.assertAlmostEqual(0.6,clock.now)
        self.assertEqual(4,len(t.calls))

    def test_transport_timeout_has_no_automatic_retry(self):
        a,t,_,_ = self.adapter(DriveError('drive_timeout'))
        self.error('drive_timeout',lambda:a.search('hosting')); self.assertEqual(1,len(t.calls))

    def test_http_request_contract_rejects_arbitrary_origin_export_and_extra_params(self):
        for path in ('https://attacker.invalid','/drive/v3/files/id/export','/drive/v3/files/../id'):
            self.error('drive_invalid_request',lambda:GetRequest(path,(('fields',FIELDS),),1024,'application/json'))
        self.error('drive_invalid_request',lambda:GetRequest('/drive/v3/files/id',(('fields',FIELDS),('key','fake')),1024,'application/json'))

    def test_close_clears_all_session_data(self):
        a,t,_,_ = self.adapter(listing(meta()))
        a.search('hosting'); a.close()
        self.assertTrue(t.closed); self.assertEqual({},a._handles)
        self.error('drive_not_available',lambda:a.search('hosting'))


if __name__ == '__main__':
    unittest.main()
