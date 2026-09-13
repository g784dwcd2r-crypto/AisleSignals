# Scenario coverage

The following scenarios guide the first prototype. The verification report records the tests actually run; this table does not claim that every eventual production scenario is complete.

| Situation | Required prototype behaviour |
|---|---|
| Ordinary shelf interaction | Show a synthetic observation for human review; infer no offence. |
| Item returned | Allow benign dismissal without creating an incident. |
| Image missing | Display the limitation; manual assessment and assistance remain available. |
| Delayed observation | Preserve occurrence/receipt times and mark historical; no urgent sound. |
| Frozen/disconnected source | Show unavailable simulated coverage; never show a healthy live detector. |
| Source recovers | Change only the simulated health state; it does not qualify a real camera. |
| Duplicate event delivery | Same source-event ID and payload produce one record. |
| Same ID, changed event | Conflict; preserve original content. |
| Double click or network retry | Reuse idempotency identity; no duplicate case/help/task. |
| Two reviewers edit | Stale expected_version returns conflict; no silent overwrite. |
| Poll arrives while typing | Preserve unsaved form values and their original base version. |
| API becomes unavailable | Show connection loss, keep unsaved input and disable uncertain writes. |
| Session expires | Clear authenticated state/drafts; require login. |
| Reviewer confirms financial loss | Denied by server, regardless of hidden UI fields. |
| Unknown amount | Preserve null; display unknown rather than €0. |
| Invalid money, negative or fractional cents | Reject; money uses integer cents. |
| Record marked benign | Do not retain contradictory loss figures. |
| Case has open tasks/outcome | Block closure with an actionable explanation. |
| Corrected case | Manager can reopen with reason; retain earlier activity. |
| Draft generated then facts change | Invalidate the old draft/approval. |
| Export by another organisation | Return not found without disclosing the record. |
| Evidence URL copied to another account | Require the original authorised site/session. |
| Form includes script-like text | Render as text; never execute HTML. |
| Cross-origin request or forged host | Reject before exposing customer/demo records. |
| Assistance without a case | Permit an independent request; receipt is not arrival. |
| Database process restarts | Retain prior changes; do not reseed over existing state. |
| Laptop is asleep | No background-monitoring promise; readiness documentation makes this explicit. |
| Existing camera cannot be reached | Report unsupported/unreachable; no hardware-purchase fallback. |
| Camera URL contains credentials or unsafe target | Reject/redact; do not log or probe an unapproved destination. |
| AI budget or provider unavailable | Template/manual workflow continues; this release makes no paid model calls. |
| Alarm or recognition requested | Capability remains unavailable until separately scoped; no hidden actuation route. |

The simulator uses named scenarios and no real customer footage. Production detection accuracy, retention, offboarding, privacy masking and hardware sleep/resume acceptance require additional implementation and site evidence.
