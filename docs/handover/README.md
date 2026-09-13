# AisleSignals Engineering Handover

Prepared by Jawahir Q. Design baseline 2.2, 13 September 2026.

This pack describes the approved project's implementation. It is not an installed application, a validated detector, executed migrations or a production configuration. No credentials, real client identities, customer footage or live output connection are included.

## Start here

1. Read implementation-plan.md for scope, roles, architecture, release gates and schedule.
2. Read requirements.json (67 requirements) and delivery-backlog.json (14 epics, 120 relative core complexity units).
3. Use openapi.json as the revised design contract; api-catalogue.md is its readable endpoint index. Its schema validity was checked; runtime permission enforcement remains implementation work.
4. Implement reviewed migrations from data-model.json and data-dictionary.md. These are design artefacts, not a claim that SQL has run.
5. Implement acceptance-test-plan.json (90 planned checks) and applicable legacy-scenarios.json (46 scenarios). NOT_RUN is intentional.
6. Use commissioning-and-release-form.md and runbooks.md before operational activation.

## Build order

Repository and simulator -> identity and tenant/site isolation -> incidents and evidence -> qualified companion/detector -> private routing -> bounded AI -> retention/rights/recovery -> site acceptance -> live pilot.

OPTION attention-sounder work and P2 stock/procedure work have separate release gates. No biometric recognition, shared watchlist, clinical decision or door-lock interface is exposed.

## Auth and adapter boundaries

Browser uses an opaque server session with CSRF/origin checks; no local-storage bearer token. Companion uses mTLS and a short-lived site-bound token; certificate-only token exchange bootstraps and renews authentication. Candidate-scoped media grants support review before incident creation. Upload owners receive a verified completion acknowledgement before releasing spool media. Optional control uses outbound nonce-bound frame challenges and signed, bounded command payloads. The normalised supplier intake is called only after the configured raw adapter verifies the actual vendor signature/replay contract. Raw vendor payloads, media endpoints and pricing remain G1 procurement inputs.

The OIDC redirect/callback and local existing site IT contact credential flow are protocol-specific implementation surfaces; do not interpret their absence from the business OpenAPI as permission to improvise authentication. The plan defines their state/nonce/PKCE, secret-storage and scope requirements.

Support/media grants, database roles, runtime model versions, model weights and vendor terms require explicit implementation and tests. OpenAPI validation does not prove tenant isolation, retention, loss detection or safe physical execution.

## Fixtures and verification

example-observation.json and example-ai-input.json are synthetic. validation-report.json records only checks performed on this design pack. A production build must run the planned integration, browser, media, recovery and hardware tests with actual selected components.

The source research/specification remains historical context. This plan changes the integration contract to version 2.2, makes sessions/device enrolment and safe UNKNOWN command handling explicit, and adopts a bounded capture lease. Do not mix the older API schemas into a new implementation without reconciling them.


## Confirmed deployment and commercial baseline

Version 2.2: Codex plus bounded specialist agents; Jawahir Q. owns product and site decisions. EUR 60 monthly covers one pharmacy branch. Additional branches require separately agreed subscriptions. No new hardware purchases, appliance, capture card, accelerator, camera, relay or wiring work are included.

The local component is installed software on the pharmacy's existing laptop. It uses existing supported camera/recorder interfaces and private sounds through existing laptop speakers. Pilot platforms are confirmed: one Windows laptop and one Mac. Both installers must pass the first-pilot checks. Monitoring stops while the host sleeps or is powered off. See business-baseline.json, existing-laptop-design.md and sixty-euro-economics.md. Camera access and useful detection are separate acceptance checks.

The PDF is the formatted plan, the DOCX is editable and implementation-plan.md is the text counterpart. Contract and data files remain designs. All acceptance tests are NOT_RUN until actual implementation and site evidence exist.

## Reproduce design validation

Use a disposable Python environment, install tools/validation-requirements.txt and run `python tools/validate_design.py`. This checks design schemas, examples, references, requirement/backlog coverage and arithmetic. It does not test a running application, detector, laptop or sounder.
