"""Shape/access/span/freshness checks, explicitly not natural-language entailment."""
import html
import json
import re

from ...domain import ResearchError, exact_keys
from .projection import require

PROTOCOL = 'research-narrative-v2'
TOPICS = ('hosting_region','sso','onboarding_days','support_hours','conditions','dependencies','exceptions')
STATES = ('supported','conflict','stale_only','unknown')
REASONS = {'supported':'current_evidence_proposal','conflict':'unresolved_conflict_proposal',
           'stale_only':'evidence_not_current','unknown':'insufficient_accessible_evidence'}


def validate_draft(value, request, projection, max_bytes=16384):
    require(projection.request==request and projection.principal==request.principal,'invalid_memo')
    projection.check_context()
    exact_keys(value,{'topics'},'invalid_memo')
    proposals = value['topics']
    require(type(proposals) is list and len(proposals)==len(TOPICS),'invalid_memo')
    accepted,total = {},0
    for proposal in proposals:
        exact_keys(proposal,{'topic','state','claims'},'invalid_memo')
        topic,state,claims = proposal['topic'],proposal['state'],proposal['claims']
        require(type(topic) is str and topic in TOPICS and topic not in accepted,'invalid_memo')
        require(type(state) is str and state in STATES,'invalid_memo')
        require(type(claims) is list and len(claims)<=12,'invalid_memo')
        require((state=='unknown') == (len(claims)==0),'invalid_memo')
        require(state!='conflict' or len(claims)>=2,'invalid_memo')
        checked,conflict_sources = [],set()
        for claim in claims:
            total += 1
            require(total<=12,'invalid_memo')
            exact_keys(claim,{'text','quotes'},'invalid_memo')
            text,quotes = claim['text'],claim['quotes']
            require(type(text) is str and 1<=len(text)<=280 and text==text.strip()
                    and not any(ord(c)<32 for c in text),'invalid_memo')
            require(type(quotes) is list and 1<=len(quotes)<=2,'invalid_memo')
            citations,seen = [],set()
            for quote in quotes:
                exact_keys(quote,{'handle','revision','content_sha256','start','end','quote'},'invalid_memo')
                handle = quote['handle']
                require(type(handle) is str and handle in projection.opened,'invalid_memo')
                doc = projection.opened[handle]
                require(type(quote['revision']) is str and quote['revision']==doc.revision
                        and quote['content_sha256']==doc.content_sha256,'invalid_memo')
                start,end = quote['start'],quote['end']
                require(type(start) is int and type(end) is int and 0<=start<end<=len(doc.body),'invalid_memo')
                require(type(quote['quote']) is str and 1<=len(quote['quote'])<=1024
                        and doc.body[start:end]==quote['quote'],'invalid_memo')
                require(doc.effective_at<=request.as_of,'invalid_memo')
                require(doc.current(request.as_of) == (state!='stale_only'),'invalid_memo')
                identity = (handle,start,end)
                require(identity not in seen,'invalid_memo'); seen.add(identity)
                conflict_sources.add((doc.connector,projection.selection_key(handle),doc.revision))
                citations.append({'handle':handle,'source_id':projection.selection_key(handle),
                                  'connector':doc.connector,'title':doc.title,
                                  'revision':doc.revision,'content_sha256':doc.content_sha256,
                                  'start':start,'end':end,'quote':quote['quote'],
                                  'effective_at':doc.effective_at,'review_until':doc.review_until})
            checked.append({'claim_id':'c'+str(total).zfill(2),'text':text,'citations':citations,
                            'semantic_review':{'status':'pending_independent_review','support':None,
                                               'qualifiers':None,'contradictions':None,'causality':None}})
        require(state!='conflict' or len(conflict_sources)>=2,'invalid_memo')
        accepted[topic] = {'topic':topic,'state':state,'state_is_proposal':True,
                           'reason':REASONS[state],'claims':checked}
    projection.check_context()
    result = {'protocol':PROTOCOL,'vendor':request.vendor,'as_of':request.as_of,
              'outcome':'needs_review','semantic_approval':None,
              'automated_checks':['shape','identity_bound_access','revision_hash','exact_unicode_spans','freshness'],
              'topics':[accepted[t] for t in TOPICS],
              'independent_review':{'status':'pending','required_fact_coverage':None,
                                    'claim_support':None,'critical_unsupported_claims':None,
                                    'qualifier_coverage':None,'conflict_resolution':None,'reviewer_agreement':None}}
    try:
        require(len(json.dumps(result,ensure_ascii=False).encode('utf-8'))<=max_bytes,'budget_exhausted')
    except UnicodeError:
        raise ResearchError('invalid_memo') from None
    return result


def render_html(draft):
    """Render trusted guard output as literal text; no source/model links or HTML."""
    require(draft.get('protocol')==PROTOCOL and draft.get('outcome')=='needs_review')
    esc = lambda s:html.escape(str(s),quote=True)
    parts = ['<!doctype html><meta charset="utf-8"><title>Research draft</title>',
             '<h1>'+esc(draft['vendor'])+'</h1><p>Needs independent semantic review.</p>']
    for record in draft['topics']:
        parts.append('<h2>'+esc(record['topic'])+'</h2><p>'+esc(record['state'])+' (proposal)</p>')
        for claim in record['claims']:
            parts.append('<p>'+esc(claim['claim_id'])+': '+esc(claim['text'])+'</p>')
            for citation in claim['citations']:
                parts.append('<blockquote>'+esc(citation['quote'])+'</blockquote><p>'+esc(citation['title'])+
                             ' — '+esc(citation['handle'])+'; revision '+esc(citation['revision'])+
                             '; characters '+str(citation['start'])+':'+str(citation['end'])+'</p>')
    return '\n'.join(parts)
