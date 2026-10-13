"""Four exposed authored walkthrough fixtures, not the planned12/24 evaluation sets."""
import copy

from .projection import require


def demo_fixture(case='normal'):
    require(case in ('normal','conflict','stale','missing'))
    body = ('hosting_region: Hosting uses only EU primary regions; backup archives are excluded.\n'
            'sso: SSO requires identity-provider validation.\n'
            'onboarding_days: Onboarding takes ten business days after security approval.\n'
            'support_hours: Priority response is within four business hours for severity one.\n'
            'conditions: A signed contract is required before onboarding.\n'
            'dependencies: Security approval depends on completing the vendor assessment.\n'
            'exceptions: Custom migration timelines are excluded from the standard schedule.\n')
    if case=='missing':
        body=''.join(line for line in body.splitlines(keepends=True) if not line.startswith('support_hours:'))
    sources = [{'object_id':'narrative-general','connector':'docshub','revision':1,
                'subject':'CedarBridge','title':'CedarBridge authored vendor briefing','body':body,
                'effective_at':'2026-10-01','review_until':'2026-12-31','allow_groups':['employees']},
               {'object_id':'narrative-peer','connector':'teamchat','channel':'general','revision':1,
                'subject':'CedarBridge','title':'CedarBridge pilot hosting confirmation',
                'body':body.splitlines()[0]+'\n','effective_at':'2026-10-02',
                'review_until':'2026-12-31','allow_groups':['employees']},
               {'object_id':'narrative-private','connector':'docshub','revision':1,
                'subject':'CedarBridge','title':'PROCUREMENT_ONLY_NARRATIVE CedarBridge agreement',
                'body':'conditions: Procurement signoff is required. PROCUREMENT_ONLY_NARRATIVE\n',
                'effective_at':'2026-10-03','review_until':'2026-12-31','allow_groups':['procurement']}]
    if case=='conflict':
        sources[1]['body']='hosting_region: Hosting uses only US primary regions; backup archives are excluded.\n'
    if case=='stale':
        for source in sources:
            source['review_until']='2026-10-05'
    return copy.deepcopy({'schema_version':1,'fictional':True,'scenario':'narrative-demo-'+case,
                          'vendor':'CedarBridge','as_of':'2026-10-12',
                          'principals':{'alex':['employees'],'priya':['employees','procurement'],
                                        'sam':['employees','security']},'sources':sources})
