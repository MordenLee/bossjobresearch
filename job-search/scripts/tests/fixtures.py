from policy import binding
from workflow import build


def sample():
    policy = dict(schema_version=1, policy_id='test-policy', version='1',
                  hard_constraints=[], soft_preferences=[], exclusions=[], tradeoffs=[],
                  priority_rules=[], verification_rules=[])
    candidate = {'schema_version':3,'cv': {'projects': ['metrics analysis']},
                 'evidence': {'/cv/projects/0': {'confirmed': True, 'source': 'fixture CV'}},
                 'authorization': {'sources': ['boss'], 'actions': ['contact'],
                                   'user_instruction': 'offline fixture only'}}
    job = dict(source='boss', job_key='boss-a', company='A', job_name='Analyst', salary='10-20K',
               location='City', job_url='https://www.zhipin.com/job_detail/a.html',
               jd_description='metrics analysis', detail_state='READY', detail_identity_verified=True)
    review = dict(source='boss', job_key='boss-a', decision='apply', priority=1, reason='matching facts',
                  role_fit={'status':'pass','primary_duties':'analysis','jd_evidence':['metrics'],
                            'resume_evidence':['/cv/projects/0']},
                  hard_constraints=[], risks=[], questions=[], rule_results=[],
                  input_binding=binding(job,candidate,policy))
    screens = [dict(source='boss',job_key='boss-a',decision='keep',reason='needs review',evidence=[])]
    bundle = build('boss',[job],screens,[review],candidate,policy)
    return policy,candidate,job,review,screens,bundle
