"""Raw SQL authority snapshots. No actor, controller, guard or rubric imports."""
import hashlib
from .data import require


def raw_snapshot(connection,principal,as_of):
    require(principal in ('alex','priya','sam'))
    require(connection.execute('SELECT 1 FROM principals WHERE principal=?',(principal,)).fetchone() is not None)
    memberships={r[0] for r in connection.execute('SELECT group_name FROM memberships WHERE principal=?',(principal,))}
    grants={}
    for resource,group in connection.execute('SELECT resource,group_name FROM grants'):
        grants.setdefault(resource,set()).add(group)
    rows=connection.execute('SELECT o.object_id,o.connector,o.subject,o.title,o.current_revision,o.acl_resource,v.body,v.effective_at,v.review_until FROM objects o JOIN versions v ON v.object_id=o.object_id AND v.revision=o.current_revision').fetchall()
    sources={}
    for sid,connector,subject,title,revision,resource,body,effective,review in rows:
        sources[sid]={'source_id':sid,'connector':connector,'subject':subject,'title':title,'revision':str(revision),
                      'body':body,'content_sha256':hashlib.sha256(body.encode('utf-8')).hexdigest(),
                      'effective_at':effective,'review_until':review,
                      'authorized':bool(memberships & grants.get(resource,set()))}
    epoch=connection.execute('SELECT epoch FROM control_epoch WHERE singleton=1').fetchone()
    # Epoch is diagnostic only; memberships/grants/current raw rows define access.
    return {'schema':'raw-sql-authority-snapshot-v1','principal':principal,'as_of':as_of,
            'epoch':epoch[0] if epoch else None,'sources':sources}


def check_quote(quote,state,snapshot,*,source_id=None):
    """Mechanical identity/access/version/span/time only. Never entailment."""
    if type(quote) is not dict:return ['quote_shape']
    sid=source_id if source_id is not None else quote.get('source_id')
    source=snapshot['sources'].get(sid); issues=[]
    if source is None:return ['source_missing']
    if not source['authorized']:issues.append('unauthorized_source')
    if quote.get('revision')!=source['revision']:issues.append('revision_mismatch')
    if quote.get('content_sha256')!=source['content_sha256']:issues.append('hash_mismatch')
    start,end=quote.get('start'),quote.get('end'); text=quote.get('quote')
    if not (type(start) is int and type(end) is int and 0<=start<end<=len(source['body']) and type(text) is str and source['body'][start:end]==text):issues.append('span_mismatch')
    current=source['effective_at']<=snapshot['as_of']<=source['review_until']
    if source['effective_at']>snapshot['as_of']:issues.append('future_source')
    if current!=(state!='stale_only'):issues.append('freshness_mismatch')
    return issues
