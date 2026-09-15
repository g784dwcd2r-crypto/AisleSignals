# Branch DPIA template

**Status:** draft until signed by the pharmacy controller and reviewed by the
controller's privacy adviser or DPO where applicable.

## 1. Ownership and review

| Field | Controller response |
|---|---|
| Pharmacy legal entity and address | |
| Branch and cameras in scope | |
| Controller contact | |
| DPO/privacy adviser contact | |
| AisleSignals processor legal entity/contact | |
| Assessment owner | |
| Date, version and next review | |
| Staff/data-subject consultation and outcome | |

## 2. Why an assessment is required

Describe the proposed systematic monitoring, the use of automated analysis,
the publicly accessible areas involved and whether offence-related inferences
may arise. Record the controller's conclusion on whether Article 35 requires a
DPIA. If the conclusion is no, record the evidence and approver; do not treat
an unchecked box as a decision.

## 3. Processing and data flow

For every camera, document:

- exact purpose and opening hours;
- field of view, privacy masks and excluded areas;
- source interface and local laptop processing;
- observations produced and confidence/quality fields;
- snapshots or clips created, their duration and trigger;
- transfers from the laptop to the cloud and encryption boundaries;
- staff roles that can view, review, export or delete data;
- subprocessors, locations and transfer safeguards;
- local, cloud, backup and audit retention;
- deletion, withdrawal, legal-hold and offboarding paths.

Attach a current data-flow diagram and camera-zone map. State explicitly that
the system does not use audio, face recognition or cross-visit biometric
matching and does not independently determine theft.

## 4. Necessity and proportionality

Answer with evidence:

1. What specific security problem is being addressed at this branch?
2. What less intrusive measures were considered and why were they insufficient?
3. Why is each camera, zone, data field, snapshot and clip necessary?
4. How are consultation areas, prescription screens, staff-only functions and
   neighbouring premises excluded?
5. How will false alerts, uncertainty and incomplete coverage be shown?
6. Why is the proposed retention period the shortest workable period?
7. How can people exercise access, objection, restriction and erasure rights?
8. Which human reviews occur before an incident, disclosure or staff response?

## 5. Legal and governance decisions

Record the Article 6 lawful basis and the separate analysis for any processing
that may concern alleged offences. Record the controller/processor allocation,
Article 28 terms, transparency approach, recipient rules, international
transfer position and whether prior consultation with the DPC is necessary.

The product team cannot select these answers for the pharmacy. Evidence mode
remains blocked until the controller records and approves them.

## 6. Risk register

Use one row per risk. Score inherent and residual likelihood/impact as low,
medium or high.

| ID | Risk to people | People affected | Cause | Inherent risk | Controls and owner | Residual risk | Due date/status |
|---|---|---|---|---|---|---|---|
| R1 | Innocent behaviour is treated as theft | Customers/staff | Model error or incomplete view | High | Staff review; neutral wording; no autonomous alarm | | |
| R2 | Excessive or sensitive areas are captured | Customers/staff/patients | Incorrect layout or screen capture | High | Per-camera masks; whole-desktop rejection; commissioning | | |
| R3 | Evidence is viewed by the wrong branch/person | Recorded people | Access or tenancy failure | High | Site roles; MFA; audit; revocation; private media | | |
| R4 | Data is kept too long or survives deletion | Recorded people | Missing worker, backups or holds | High | Approved schedule; deletion receipts; restore controls | | |
| R5 | Security breach exposes footage | Recorded people | Device/cloud compromise | High | Encryption; secrets management; least privilege; response plan | | |
| R6 | People are not adequately informed | Customers/staff | Missing/outdated notice | Medium | Entrance sign plus full notice; inspection checklist | | |

Add branch-specific risks, including children, accessibility, employee
monitoring, adjoining premises, camera blind spots and disclosure to third
parties. An unresolved residual high risk blocks processing and may require DPC
consultation.

## 7. Approval

| Decision | Name/role | Date | Signature/reference |
|---|---|---|---|
| Technical controls verified | | | |
| Privacy/DPO advice recorded | | | |
| Residual risks accepted by controller | | | |
| Evidence mode approved | | | |

Record dissenting advice and the controller's reason for any different decision.

