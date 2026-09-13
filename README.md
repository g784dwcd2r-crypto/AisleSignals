# AisleSignals

Pharmacy incident review, evidence and staff-assistance software for Ireland.

Product owner: **Jawahir Q.** Implementation: **Codex and bounded specialist agents**. Confirmed price: **EUR 60 per month for one pharmacy branch**. Additional branches require separately agreed subscriptions.

**Deployment constraint: no new hardware.** Use the pharmacy's existing laptop and existing supported cameras/recorder interfaces. The first pilot includes one Windows laptop and one Mac. Monitoring requires the laptop to remain awake; private sounds use its existing speakers.

## Current status

This repository starts with a validated implementation design and engineering handover. The application, live detector, production deployment and physical installations are not implemented by this initial commit. Acceptance cases are explicitly marked `NOT_RUN`.

The build follows six acceptance-based work packages. There is no assumed hired engineering team, fixed twelve-week coding promise or unattended agent support service.

## Read the plan

- [Implementation and build plan](docs/handover/implementation-plan.md)
- [Formatted PDF](docs/handover/aislesignals-implementation-plan-jawahir-q.pdf)
- [Handover index](docs/handover/README.md)
- [Confirmed business baseline](docs/handover/business-baseline.json)
- [Existing laptop deployment](docs/handover/existing-laptop-design.md)
- [EUR 60 branch economics](docs/handover/sixty-euro-economics.md)
- [Functional requirements](docs/handover/requirements.md)
- [OpenAPI contract](docs/handover/openapi.json)
- [Data dictionary](docs/handover/data-dictionary.md)
- [Delivery backlog](docs/handover/delivery-backlog.json)
- [Planned acceptance tests](docs/handover/acceptance-test-plan.json)
- [Validation actually performed](docs/handover/validation-report.json)

## First implementation milestone

Create a reproducible local workspace and synthetic event simulator, then build named accounts, tenant/site permissions and one persistent alert-to-reviewed-incident journey. The demonstration must include benign dismissal before case creation, evidence playback, conflicting reviews and a denied cross-tenant request. Live camera integration is accepted separately using actual supported interfaces and qualified views.

Planned stack: React/TypeScript, FastAPI, PostgreSQL, durable background workers, private evidence storage and a companion installed on the existing pharmacy laptop. The detector is a replaceable integration; a generic object model is not a validated theft detector.

## Validate the handover

Use Python 3.12 or a compatible tested version in a disposable environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r docs/handover/tools/validation-requirements.txt
python docs/handover/tools/validate_design.py
```

This checks design artifacts and arithmetic. It does not test a running application, camera or sounder.

## Release boundaries

Live operation requires site-specific processing records, exact camera and supplier access, trained reviewers and actual acceptance evidence. Existing laptop OS versions and capacity, permitted software onboarding, detector fees, tax treatment and human operational coverage remain discovery inputs. Subscription records begin as owner-managed billing; this repository does not charge customers.

There is no automated facial recognition, shared offender watchlist, person-level criminality scoring, autonomous clinical action or door-lock control. Optional sounder commands require a separate commissioned human-authorised path. Product AI is bounded report drafting, with a proposed EUR 5 monthly allowance per subscribed branch and a manual fallback.
