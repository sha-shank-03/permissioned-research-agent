"""Authored observation-only policies; neither a live model nor a semantic judge."""
import copy
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading

from ...domain import ResearchError, strict_json_loads
from ..config import DOUBLE_MODEL
from ..double import completion
from ..transport import DoubleEndpoint
from .boundary import TOPICS
from .provider import TOOLS
from .projection import require


def observations_from_request(request):
    messages = request['messages']
    initial = strict_json_loads(messages[1]['content'])
    observations = copy.deepcopy(initial.get('controller_collected_evidence',[]))
    for index in range(2,len(messages)-1,2):
        assistant,tool = messages[index:index+2]
        call = assistant['tool_calls'][0]
        require(tool['role']=='tool' and tool['tool_call_id']==call['id'],'invalid_decision')
        result = strict_json_loads(tool['content'])
        if result.get('status')!='error':
            observations.append({'action':call['function']['name'],
                                 'arguments':strict_json_loads(call['function']['arguments']),'result':result})
    return initial['request'],strict_json_loads(messages[-1]['content'])['control'],observations


def authored_draft(observations, as_of):
    """Extract the visible authored demo line format, with no fixture or label access.

    Topic/conflict proposals remain unreviewed. This is not a general-language
    extractor or an independent entailment/coverage evaluator.
    """
    lines = {topic:[] for topic in TOPICS}
    for observation in observations:
        if observation['action']!='read' or observation['result'].get('status')!='ok':
            continue
        doc = observation['result']
        offset = 0
        for line in doc['body'].splitlines(keepends=True):
            quote = line.rstrip('\r\n')
            topic,separator,text = quote.partition(': ')
            if topic in TOPICS and separator and len(text)<=280 and doc['effective_at']<=as_of:
                citation = {'handle':doc['handle'],'revision':doc['revision'],
                            'content_sha256':doc['content_sha256'],'start':offset,
                            'end':offset+len(quote),'quote':quote}
                lines[topic].append((doc['effective_at']<=as_of<=doc['review_until'],text,citation))
            offset += len(line)
    topics = []
    for topic in TOPICS:
        items = lines[topic]
        current = [item for item in items if item[0]]
        selected = current or items
        dedup = {}
        for _,text,citation in selected:
            dedup.setdefault(text,citation)
        claims = [{'text':text,'quotes':[citation]} for text,citation in list(dedup.items())[:2]]
        state = ('unknown' if not claims else 'stale_only' if not current else
                 'conflict' if topic in TOPICS[:4] and len(claims)==2 else 'supported')
        topics.append({'topic':topic,'state':state,'claims':claims})
    # Demo sources keep the whole proposal below12; the real guard owns the cap.
    return {'topics':topics}


class AuthoredPolicy:
    def __init__(self, fault=None):
        require(fault in (None,'malformed_once','bad_finish_once','omit_qualifier','self_approve'))
        self.fault,self.requests,self.finishes = fault,0,0

    def respond(self, request):
        self.requests += 1
        if self.fault=='malformed_once' and self.requests==1:
            return b'{"choices":[]}'
        task,control,observations = observations_from_request(request)
        if control['phase']!='SYNTHESIZING':
            searches = [o for o in observations if o['action']=='search']
            if not searches:
                return completion('search',{'query':task['vendor'],'limit':4},'n'+str(self.requests),usage=False)
            read = {o['arguments']['handle'] for o in observations if o['action']=='read'}
            for search in searches:
                for hit in search['result']['hits']:
                    if hit['handle'] not in read:
                        return completion('read',{'handle':hit['handle']},'n'+str(self.requests),usage=False)
        proposal = authored_draft(observations,task['as_of'])
        self.finishes += 1
        if self.fault=='bad_finish_once' and self.finishes==1:
            for topic in proposal['topics']:
                if topic['claims']:
                    topic['claims'][0]['quotes'][0]['end'] += 1
                    break
        if self.fault=='omit_qualifier':
            for topic in proposal['topics']:
                if topic['topic']=='onboarding_days' and topic['claims']:
                    topic['claims'][0]['text']='Onboarding always takes ten business days.'
        if self.fault=='self_approve':
            proposal['semantic_approval']=True
        return completion('finish',proposal,'n'+str(self.requests),usage=False)


class MemoryPending:
    sent = True
    def __init__(self, request):
        self.request,self.closed = copy.deepcopy(request),False
    def close(self):
        self.closed=True


class MemoryTransport:
    evidence_kind = 'authored_in_memory_contract'
    live_model_executed = False
    network_enabled = False
    def __init__(self, policy=None, responder=None):
        self.policy = policy if policy is not None else AuthoredPolicy()
        self.responder,self.requests,self.pending = responder,[],[]
    def submit(self, request, deadline):
        # Pure tests still exercise serialization/native decoding and correlation.
        request = strict_json_loads(json.dumps(request,ensure_ascii=False).encode('utf-8'))
        self.requests.append(copy.deepcopy(request))
        p = MemoryPending(request); self.pending.append(p); return p
    def collect(self, pending, deadline):
        return self.responder(pending.request) if self.responder else self.policy.respond(pending.request)
    def close(self):
        for pending in self.pending:
            pending.close()
    @property
    def owned_children_remaining(self):
        return 0
    @property
    def owned_threads_remaining(self):
        return 0


@contextmanager
def narrative_http_double(*, fault=None):
    """Instantiate only under a fresh native integration lease."""
    policy,captured,failures,stop = AuthoredPolicy(fault),[],[],threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):
            pass
        def do_POST(self):
            try:
                self.connection.settimeout(1)
                length = int(self.headers.get('Content-Length','-1'))
                require(self.path=='/v1/chat/completions' and 0<=length<=49152)
                request = strict_json_loads(self.rfile.read(length))
                require(request['model']==DOUBLE_MODEL and request['tools']==TOOLS
                        and request['tool_choice']=='required' and request['parallel_tool_calls'] is False
                        and request['stream'] is False)
                captured.append(request)
                body = policy.respond(request)
                self.send_response(200); self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):
                pass
            except (ResearchError,ValueError,TypeError,KeyError,OSError):
                failures.append('authored_request_invalid'); self.close_connection=True
    server = HTTPServer(('127.0.0.1',0),Handler); server.timeout=0.05
    def serve():
        while not stop.is_set():
            server.handle_request()
    thread = threading.Thread(target=serve,name='narrative-gateway-double',daemon=True); thread.start()
    try:
        yield DoubleEndpoint(server.server_port),captured
    finally:
        stop.set(); thread.join(timeout=1.2); server.server_close()
        require(not thread.is_alive() and not failures,'provider_error')
