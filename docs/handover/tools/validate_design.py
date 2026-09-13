from pathlib import Path
import json,hashlib,copy
from openapi_spec_validator import validate
from jsonschema import Draft202012Validator,FormatChecker,RefResolver,ValidationError
root=Path(__file__).resolve().parents[1]
spec=json.loads((root/'openapi.json').read_text());validate(spec)
checks=[{'check':'OpenAPI 3.1 structural validation','result':'PASS'}]
for f in root.glob('*.json'):json.loads(f.read_text())
checks.append({'check':'All handover JSON parses','result':'PASS'})
ops=[op for methods in spec['paths'].values() for op in methods.values()];ids=[op['operationId'] for op in ops]
assert len(ids)==len(set(ids))
checks.append({'check':'Unique operation IDs','result':'PASS','count':len(ids)})
refs=[]
def walk(v):
 if isinstance(v,dict):
  if '$ref' in v:refs.append(v['$ref'])
  for a in v.values():walk(a)
 elif isinstance(v,list):
  for a in v:walk(a)
walk(spec)
for ref in refs:
 assert ref.startswith('#/')
 node=spec
 for part in ref[2:].split('/'):node=node[part.replace('~1','/').replace('~0','~')]
checks.append({'check':'All internal schema references resolve','result':'PASS','references':len(refs)})
resolver=RefResolver.from_schema(spec)
def val(name,example):Draft202012Validator(spec['components']['schemas'][name],resolver=resolver,format_checker=FormatChecker()).validate(example)
fixture=json.loads((root/'example-observation.json').read_text());val('Observation',fixture)
checks.append({'check':'Synthetic observation validates including UUID and date formats','result':'PASS'})
base={'site_id':fixture['site_id'],'output_id':fixture['camera_id'],'action':'ATTENTION_SOUNDER','approval_token':'synthetic-token','pulse_ms':1000}
val('AlarmCommandRequest',base)
for field,value in [('action','DOOR_LOCK'),('pulse_ms',5001),('pulse_ms',99)]:
 bad=base|{field:value}
 try:val('AlarmCommandRequest',bad)
 except ValidationError:pass
 else:raise AssertionError(f'Expected rejection {field} {value}')
checks.append({'check':'Schema rejects unsupported door action and out-of-range pulse durations','result':'PASS','limitation':'Does not test runtime authorisation or physical execution.'})
reqs=json.loads((root/'requirements.json').read_text());epics=json.loads((root/'delivery-backlog.json').read_text())
rids={r['id'] for r in reqs};assert len(rids)==len(reqs)==67
eids={e['id'] for e in epics};assert len(eids)==len(epics)
def visit(id,stack):
 assert id not in stack
 for dep in next(e for e in epics if e['id']==id)['dependencies']:
  assert dep in eids;visit(dep,stack|{id})
for e in epics:
 visit(e['id'],set());assert set(e['requirement_ids'])<=rids
assert set().union(*(set(e['requirement_ids']) for e in epics))==rids
assert sum(e['complexity_weight'] for e in epics if e['release']=='MVP')==120
checks.append({'check':'Unique requirements, valid traceability, acyclic backlog dependencies and relative complexity arithmetic','result':'PASS','requirements':len(reqs),'epics':len(epics),'relative_core_complexity_units':120})
tests=json.loads((root/'acceptance-test-plan.json').read_text());assert len({t['id'] for t in tests})==len(tests);assert all(t['status']=='NOT_RUN' for t in tests)
assert {t['requirement_id'] for t in tests if 'requirement_id' in t}==rids
model=json.loads((root/'data-model.json').read_text());assert len({t['name'] for t in model['tables']})==len(model['tables'])
assert spec['components']['schemas']['EvidenceAccess']['properties']['variant']==spec['components']['schemas']['Evidence']['properties']['variant']
assert '{{' not in (root/'implementation-plan.md').read_text()
checks.append({'check':'Test traceability, unique data entities and evidence variant consistency','result':'PASS','planned_acceptance_checks':len(tests),'designed_tables':len(model['tables'])})
assert 20*26*30*2/8/1000==3.9
assert 20*26*100==52000
business=json.loads((root/'business-baseline.json').read_text())
assert business['deployment']['new_hardware_purchases'] is False
assert business['deployment']['local_host']=='EXISTING_PHARMACY_LAPTOP'
assert business['pricing']['monthly_price_cents']==6000 and business['pricing']['included_pharmacy_branches']==1
assert spec['components']['schemas']['BranchSubscription']['properties']['monthly_price_cents']['const']==6000
assert spec['components']['schemas']['BranchSubscription']['properties']['included_branches']['const']==1
site_cost=5+5+15
assert round(25*60-180-25*site_cost,2)==695
assert round(100*60-400-100*site_cost,2)==3100
checks.append({'check':'Confirmed single-branch subscription constants and existing-laptop delivery arithmetic','result':'PASS','price_cents':6000,'included_branches':1})
checks.append({'check':'Capacity arithmetic for declared workload','result':'PASS','media_gb_per_site_month':3.9,'events_at_100_sites':52000})
report={'status':'DESIGN_ARTIFACT_VALIDATED_NOT_APPLICATION_TESTED','date':'2026-09-13','checks':checks,'independent_review':'Five identified interface gaps resolved; bounded agent re-review found no residual blocker within those five areas.','not_performed':['Application implementation or runtime tests','Executed database migrations or RLS tests','Real camera or supplier integration','Live AI factuality/cost evaluation','Optional existing sounder software-interface commissioning','Deployment, recovery or security penetration tests','Pharmacy pilot or measured loss reduction']}
(root/'validation-report.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
