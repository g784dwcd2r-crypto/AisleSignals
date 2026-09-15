# Product custody reasoning increment

This increment adds a deterministic reasoning boundary between visual model
observations and an alarm. It does not claim that the current visual models can
reliably produce every required observation.

## Decision contract

The engine accepts anonymous, visit-scoped and product-scoped observations. A
high-attention decision requires all of these directly observed facts in order:

1. a person track was confirmed;
2. a product-sized shelf departure was observed;
3. the product was observed in that track's hand;
4. the product was observed entering clothing or a personal bag;
5. custody coverage remained continuous to the exit;
6. the calibrated checkout path remained observed;
7. a product-linked reconciliation source reported no matching checkout event;
8. the same anonymous visit track crossed a calibrated exit; and
9. no return or completed checkout resolved custody.

Partial visibility, indirect observations, explicit camera gaps and out-of-order
events force abstention. Sparse facts do not imply a camera gap; upstream
monitors must explicitly report coverage loss. Returns and product-linked
checkout events resolve custody without an alarm. Duplicate event delivery is
idempotent, while a reused ID with changed content forces abstention. Records are
isolated by visit, custody episode and product token. Capacity exhaustion is
surfaced as degraded state and suppresses alarms until monitoring is reset.

The current four-frame VLM integration cannot establish cross-window or
cross-camera continuity. Its response now includes a
`sampled_window_interpretation` that states this explicitly and shows the one
correlated model assertion in the desktop UI. It does not expand one model
answer into independent facts. A single concealment window remains a review
signal under the existing experimental attention rule; it is never a complete
custody alarm decision.

## Required upstream adapters

- A dedicated person detector must gate pose and hand models. The geometric pose
  gate in the prerequisite branch is only a conservative short-term filter.
- A local tracker must issue random visit and custody-episode IDs that expire
  when the visit ends.
  Do not use faces or persistent biometric templates.
- Shelf-change and hand-object models must produce product tokens scoped to a
  shelf cell and visit. A token expresses continuity, not SKU identity.
- Camera calibration must project tracks into a shared floor map and explicitly
  report any gap. A camera change requires a confirmed tracker handoff;
  ambiguous associations force abstention.
- Checkout/POS reconciliation must be product-linked. Absence of a checkout
  event is accepted only as an explicit reconciliation fact while the checkout
  path and custody coverage remained observed.

## Laptop operating profile

Run inexpensive person and shelf-region detectors continuously on small frames.
Schedule four or six cameras in a staggered loop and burst hand/object inference
only after activity in a calibrated shelf region. The custody state machine has
negligible cost compared with visual inference; run
`python scripts/benchmark_custody_engine.py` to measure it on the target laptop.

## Validation

Synthetic unit tests validate ordering, isolation, abstention and duplicate
handling. They do not validate pharmacy footage. Release enablement still needs
staged scenarios from every branch, measured false alerts per camera-hour,
scenario recall, alert latency, camera-gap tests and staff acceptance testing.
