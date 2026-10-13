"""Bounded adaptive/fixed draft workflow with local authority gates and no cache."""
import copy
from datetime import date
import threading
import time

from ...agent import MemorySink
from ...domain import ResearchError, ResearchRequest
from ..drive.contract import DriveError
from .boundary import PROTOCOL, render_html, validate_draft
from .projection import CorpusProjection, DriveProjection, require
from .provider import NarrativeAction, NarrativeProvider

FIXED_QUERIES = ('hosting region','SSO support','onboarding lead time',
                 'priority support response','conditions qualifications','dependencies exceptions')


class NarrativeWorkflow:
    def __init__(self, projection, request, provider, config, *, policy,
                 cancellation=None, hooks=None, observer=None, sink=None, clock=time.monotonic):
        require(type(request) is ResearchRequest and type(projection) in (CorpusProjection,DriveProjection))
        require(projection.principal==request.principal and projection.request==request)
        require(type(provider) is NarrativeProvider and provider.requests==0 and provider.config==config)
        require(policy in ('adaptive','fixed'))
        self.projection,self.request,self.provider,self.config,self.policy = projection,request,provider,config,policy
        self.cancellation = cancellation if cancellation is not None else threading.Event()
        self.clock,self.hooks,self.observer = clock,hooks or {},observer
        self.sink = sink if sink is not None else MemorySink()
        self.observations,self.last_error,self.has_run = [],None,False
        self.counters = {'requests':0,'searches':0,'reads':0,'repairs':0,'restarts':0,'backend_search_calls':0}

    def _hook(self, name):
        if name in self.hooks:
            self.hooks[name](self)

    def _check(self):
        require(not self.cancellation.is_set(),'cancelled')
        require(self.clock()<self.deadline-self.config.cleanup_seconds,'budget_exhausted')

    def _tool(self, action):
        self._hook('before_tool')
        with self.projection.boundary():
            self._check(); self.projection.check_context()
            name = 'searches' if action.name=='search' else 'reads'
            cap = self.config.max_searches if action.name=='search' else self.config.max_reads
            require(self.counters[name]<cap,'budget_exhausted')
            self.counters[name] += 1
            result = getattr(self.projection,action.name)(**action.arguments)
            if action.name=='search' and result.get('backend_search_calls') is not None:
                self.counters['backend_search_calls'] += result['backend_search_calls']
            self._check()
            self.observations.append({'action':action.name,'arguments':copy.deepcopy(action.arguments),
                                      'result':copy.deepcopy(result)})
            return result

    def _select(self, hits):
        eligible = {h:m for h,m in hits.items() if m['effective_at']<=self.request.as_of}
        groups = {}
        for handle,meta in eligible.items():
            group = self.projection.diversity_key(handle)
            current = meta['effective_at']<=self.request.as_of<=meta['review_until']
            rank = (not current,-meta['matches'],-date.fromisoformat(meta['effective_at']).toordinal(),
                    self.projection.selection_key(handle))
            groups.setdefault(group,[]).append((rank,handle))
        for values in groups.values():
            values.sort()
        selected = []
        # Deterministic round robin over connector/folder groups, current before stale.
        for stale in (False,True):
            while len(selected)<min(8,self.config.max_reads):
                added = False
                for group in sorted(groups):
                    options = [item for item in groups[group] if item[0][0]==stale and item[1] not in selected]
                    if options and len(selected)<min(8,self.config.max_reads):
                        selected.append(options[0][1]); added=True
                if not added:
                    break
        return selected

    def _fixed_collect(self):
        hits = {}
        for query in FIXED_QUERIES:
            result = self._tool(NarrativeAction('search',{'query':query,'limit':4}))
            for hit in result['hits']:
                if hit['handle'] in hits:
                    hits[hit['handle']]['matches'] += 1
                else:
                    hits[hit['handle']] = {**hit,'matches':1}
        for handle in self._select(hits):
            self._tool(NarrativeAction('read',{'handle':handle}))

    def _dispatch(self):
        self._hook('before_send')
        pending = None
        try:
            with self.projection.boundary():
                self._check(); self.projection.check_context()
                require(self.counters['requests']<self.config.max_requests,'budget_exhausted')
                require(self.policy!='fixed' or self.counters['requests']<2,'budget_exhausted')
                context = {'request':{'vendor':self.request.vendor,'as_of':self.request.as_of},
                           'phase':'SYNTHESIZING' if self.policy=='fixed' else 'RESEARCHING',
                           'remaining_requests':self.config.max_requests-self.counters['requests'],
                           'last_error':self.last_error,'observations':copy.deepcopy(self.observations),
                           'collected_by_controller':self.policy=='fixed'}
                self.counters['requests'] += 1
                # Native transport returns only after body send acknowledgement.
                pending = self.provider.submit(context,self.deadline)
                if self.observer:
                    self.observer('provider_disclosure',copy.deepcopy(context))  # In-memory test hook only.
            self._hook('after_submit')
            self._check()
            response = self.provider.collect(pending,self.deadline)
            pending.close(); pending=None  # Finish owned Gateway cleanup before any Drive recheck child.
            with self.projection.boundary():
                self._check(); self.projection.check_context()
            return NarrativeAction.parse(response.decision,self.config.max_action_bytes)
        finally:
            if pending is not None:
                pending.close()

    def _release(self, action):
        self._hook('before_release')
        with self.projection.boundary():
            self._check(); self.projection.check_context()
            draft = validate_draft(action.arguments,self.request,self.projection,self.config.max_action_bytes)
            html = render_html(draft)
            require(len(html.encode('utf-8'))<=self.config.max_action_bytes,'budget_exhausted')
            self._check(); self.projection.check_context()
            self.sink.release(draft)  # Local SQL gate held; Drive remains a non-atomic remote snapshot.
            return draft,html

    def _reset(self):
        self.observations.clear(); self.last_error=None
        self.projection.clear(); self.provider.reset_history()
        require(self.counters['restarts']<1,'source_state_changed')
        self.counters['restarts'] += 1

    def run(self):
        require(not self.has_run,'attempt_state_conflict'); self.has_run=True
        started = self.clock(); self.deadline=started+self.config.total_seconds
        self.projection.restrict_deadline(self.deadline)
        draft,html,error,outcome = None,None,None,'failed'
        collected = False
        try:
            while draft is None:
                try:
                    self._check()
                    if self.policy=='fixed' and not collected:
                        self._fixed_collect(); collected=True
                    action = self._dispatch()
                    if action.name=='finish':
                        draft,html = self._release(action); outcome='needs_review'
                    else:
                        require(self.policy=='adaptive','invalid_decision')
                        self._tool(action)
                    self.last_error=None
                except ResearchError as failure:
                    if failure.code=='source_state_changed':
                        self._reset(); collected=False
                    elif failure.code in ('invalid_decision','invalid_memo') and self.counters['repairs']<1:
                        self.counters['repairs'] += 1; self.last_error=failure.code
                    else:
                        raise
        except (ResearchError,DriveError) as failure:
            error=failure.code
            outcome='cancelled' if error in ('cancelled','drive_cancelled') else 'failed'
        except KeyboardInterrupt:
            error,outcome='cancelled','cancelled'
        except (OSError,ValueError,TypeError,KeyError,UnicodeError):
            error='provider_error'
        finally:
            if error:
                self.observations.clear(); self.projection.clear()
            try:
                self.provider.close()
            finally:
                self.projection.close()
        return {'protocol':PROTOCOL,'policy':self.policy,'outcome':outcome,'error':error,
                'draft':draft,'html':html,'principal':self.request.principal,
                'counters':dict(self.counters),'provider_requests':self.provider.requests,
                'events':list(self.provider.events),'elapsed_seconds':round(self.clock()-started,6),
                'mode':self.provider.evidence_kind,'live_model_executed':False,
                'remote_authorization_proven':False,'semantic_correctness':None,
                'coverage':None,'actual_cost':None,'estimated_cost':None,'resume_supported':False}
