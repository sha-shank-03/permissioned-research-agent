"""Authored native HTTP U2-A batch. Requires a current main-owned heavy lease.

Do not confuse this local double with delegated-user Google Drive integration.
"""
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from permissioned_research.upgrade.drive.adapter import DriveAdapter
from permissioned_research.upgrade.drive.contract import DriveError, FIELDS, GetRequest, Limits
from permissioned_research.upgrade.drive.http import DOUBLE_TOKEN, DriveHTTPTransport, LoopbackEndpoint
from test_upgrade_drive_contract import BODY, credential, meta, scope


@contextmanager
def owned_double(mode='normal'):
    released, stopped = threading.Event(), threading.Event()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            calls.append((self.command,self.path,self.headers.get('Authorization')))
            try:
                if mode == 'headers_stall':
                    released.wait(5)
                    return
                target = urlsplit(self.path)
                query = parse_qs(target.query)
                content_type = 'application/json'
                if target.path == '/drive/v3/files':
                    body = json.dumps({'files':[meta()]}).encode()
                elif query == {'alt':['media']}:
                    content_type,body = 'text/plain',BODY
                else:
                    body = json.dumps(meta()).encode()
                status = {'unauthorized':401,'forbidden':403,'missing':404,'quota':429,'redirect':302}.get(mode,200)
                if status != 200:
                    body = b'{"error":{"message":"AUTHORED_PRIVATE_ERROR"}}'
                if mode == 'malformed':
                    body = b'{"files":[],"files":[]}'
                if mode == 'wrong_mime':
                    content_type = 'application/octet-stream'
                self.send_response(status)
                self.send_header('Content-Type',content_type)
                if mode == 'redirect':
                    self.send_header('Location','https://attacker.invalid/should-never-be-followed')
                if mode == 'gzip':
                    self.send_header('Content-Encoding','gzip')
                if mode == 'chunk_oversize':
                    self.send_header('Transfer-Encoding','chunked')
                    self.end_headers()
                    self.wfile.write(b'11170\r\n'+b'x'*70000+b'\r\n0\r\n\r\n')
                    return
                declared = 65537 if mode == 'declared_oversize' else len(body)
                self.send_header('Content-Length',str(declared))
                self.end_headers()
                if mode == 'body_stall':
                    self.wfile.flush()
                    released.wait(5)
                    return
                if mode == 'truncated':
                    self.wfile.write(body[:3])
                    return
                self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError,OSError):
                pass

        def do_POST(self):
            calls.append((self.command,self.path,'unexpected'))
            self.send_error(405)

    server = HTTPServer(('127.0.0.1',0),Handler)
    server.timeout = 0.025

    def serve():
        while not stopped.is_set():
            server.handle_request()

    thread = threading.Thread(target=serve,name='drive-authored-http-double',daemon=True)
    thread.start()
    try:
        yield LoopbackEndpoint(server.server_port),calls
    finally:
        released.set(); stopped.set()
        thread.join(timeout=0.7)
        server.server_close()
        if thread.is_alive():
            raise AssertionError('Owned Drive double thread survived cleanup')


class DriveNativeHTTPTests(unittest.TestCase):
    def adapter(self, endpoint, user='priya', **options):
        transport = DriveHTTPTransport(test_endpoint=endpoint,**options)
        a = DriveAdapter(scope(user),replace(credential(user),token=DOUBLE_TOKEN,expires_at=time.time()+60),
                         transport,limits=Limits(total_seconds=8,request_seconds=2))
        return a,transport

    def cleaned(self, transport):
        transport.close()
        self.assertEqual(0,transport.owned_children_remaining)
        self.assertEqual(0,transport.owned_threads_remaining)

    def test_three_principals_get_only_stable_text_and_recheck(self):
        for user in ('alex','priya','sam'):
            with owned_double() as (endpoint,calls):
                a,t = self.adapter(endpoint,user)
                try:
                    h = a.search('hosting')['hits'][0]['handle']
                    self.assertEqual(BODY.decode(),a.read(h).text)
                    a.check_context()
                    self.assertEqual('local_http_double',t.evidence_kind)
                    self.assertEqual(5,len(calls))
                    self.assertTrue(all(method == 'GET' and auth == 'Bearer '+DOUBLE_TOKEN
                                        for method,_,auth in calls))
                    self.assertTrue(all(path.startswith('/drive/v3/files?') or
                                        path.startswith('/drive/v3/files/text_1?') for _,path,_ in calls))
                finally:
                    a.close(); self.cleaned(t)

    def test_auth_and_permission_denials_are_sanitized_without_retry(self):
        for mode,code in (('unauthorized','drive_authentication_required'),
                          ('forbidden','drive_not_available'),('missing','drive_not_available')):
            with owned_double(mode) as (endpoint,calls):
                a,t = self.adapter(endpoint)
                try:
                    with self.assertRaises(DriveError) as caught:
                        a.search('hosting')
                    self.assertEqual(code,caught.exception.code)
                    self.assertEqual(1,len(calls)); self.assertEqual({},a._handles)
                    self.assertNotIn('AUTHORED_PRIVATE_ERROR',json.dumps(a.events))
                finally:
                    a.close(); self.cleaned(t)

    def test_quota_retry_cap_uses_get_only(self):
        with owned_double('quota') as (endpoint,calls):
            a,t = self.adapter(endpoint)
            try:
                with self.assertRaises(DriveError) as caught:
                    a.search('hosting')
                self.assertEqual('drive_rate_limited',caught.exception.code)
                self.assertEqual(3,len(calls)); self.assertEqual(2,a.retries)
            finally:
                a.close(); self.cleaned(t)

    def test_redirect_is_not_followed(self):
        with owned_double('redirect') as (endpoint,calls):
            a,t = self.adapter(endpoint)
            try:
                with self.assertRaises(DriveError) as caught:
                    a.search('hosting')
                self.assertEqual('drive_schema_invalid',caught.exception.code)
                self.assertEqual(1,len(calls))
            finally:
                a.close(); self.cleaned(t)

    def test_malformed_mime_encoding_and_bounded_body_fail_closed(self):
        for mode in ('malformed','wrong_mime','gzip','declared_oversize','chunk_oversize','truncated'):
            with owned_double(mode) as (endpoint,calls):
                a,t = self.adapter(endpoint)
                try:
                    with self.assertRaises(DriveError) as caught:
                        a.search('hosting')
                    self.assertIn(caught.exception.code,('drive_schema_invalid','drive_transport_error'))
                    self.assertEqual(1,len(calls)); self.assertEqual({},a._handles)
                finally:
                    a.close(); self.cleaned(t)

    def test_header_and_body_deadlines_cleanup_owned_process(self):
        request = GetRequest('/drive/v3/files/text_1',(('fields',FIELDS),),65536,'application/json')
        for mode in ('headers_stall','body_stall'):
            with owned_double(mode) as (endpoint,_):
                t = DriveHTTPTransport(test_endpoint=endpoint)
                began = time.monotonic()
                try:
                    with self.assertRaises(DriveError) as caught:
                        t.get(request,replace(credential(),token=DOUBLE_TOKEN),began+0.8,threading.Event(),1)
                    self.assertEqual('drive_timeout',caught.exception.code)
                    self.assertLess(time.monotonic()-began,2.0)  # Explicit scheduling/cleanup tolerance.
                finally:
                    self.cleaned(t)

    def test_simulated_dns_tls_send_stalls_are_parent_supervision_only(self):
        request = GetRequest('/drive/v3/files/text_1',(('fields',FIELDS),),65536,'application/json')
        for phase in ('dns','tls','send'):
            with owned_double() as (endpoint,calls):
                t = DriveHTTPTransport(test_endpoint=endpoint,test_stall=phase)
                began = time.monotonic()
                try:
                    with self.assertRaises(DriveError) as caught:
                        t.get(request,replace(credential(),token=DOUBLE_TOKEN),began+0.6,threading.Event(),1)
                    self.assertEqual('drive_timeout',caught.exception.code)
                    self.assertEqual([],calls); self.assertLess(time.monotonic()-began,1.8)
                finally:
                    self.cleaned(t)

    def test_cancellation_stops_collection_and_owned_resources(self):
        request = GetRequest('/drive/v3/files/text_1',(('fields',FIELDS),),65536,'application/json')
        with owned_double('body_stall') as (endpoint,_):
            t = DriveHTTPTransport(test_endpoint=endpoint)
            cancel = threading.Event()
            timer = threading.Timer(0.4,cancel.set)
            timer.start()
            began = time.monotonic()
            try:
                with self.assertRaises(DriveError) as caught:
                    t.get(request,replace(credential(),token=DOUBLE_TOKEN),began+3,cancel,1)
                self.assertEqual('drive_cancelled',caught.exception.code)
                self.assertLess(time.monotonic()-began,1.6)
            finally:
                timer.cancel(); timer.join(timeout=0.2)
                self.assertFalse(timer.is_alive()); self.cleaned(t)

    def test_loopback_refuses_any_token_other_than_authored_placeholder(self):
        with owned_double() as (endpoint,calls):
            t = DriveHTTPTransport(test_endpoint=endpoint)
            request = GetRequest('/drive/v3/files/text_1',(('fields',FIELDS),),65536,'application/json')
            try:
                with self.assertRaises(DriveError) as caught:
                    t.get(request,credential(),time.monotonic()+1,threading.Event(),1)
                self.assertEqual('drive_authentication_required',caught.exception.code)
                self.assertEqual([],calls); self.assertEqual([],t.owned)
            finally:
                self.cleaned(t)


if __name__ == '__main__':
    unittest.main()
