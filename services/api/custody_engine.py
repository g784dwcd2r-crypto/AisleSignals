"""Deterministic product-custody reasoning over independently observed facts.

The engine deliberately does not inspect pixels or infer identity/intent. Vision
models may propose observations, but only ordered, direct observations can move a
product through the state machine. Missing continuity produces an abstention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable


class Fact(StrEnum):
    PERSON_CONFIRMED = "PERSON_CONFIRMED"
    SHELF_DEPARTURE = "SHELF_DEPARTURE"
    HAND_CUSTODY = "HAND_CUSTODY"
    RETURN_TO_SHELF = "RETURN_TO_SHELF"
    PLACE_IN_BASKET = "PLACE_IN_BASKET"
    CONCEALMENT_TRANSITION = "CONCEALMENT_TRANSITION"
    CHECKOUT_COMPLETED = "CHECKOUT_COMPLETED"
    EXIT_CROSSED = "EXIT_CROSSED"
    CAMERA_HANDOFF = "CAMERA_HANDOFF"
    CUSTODY_CONTINUOUS_TO_EXIT = "CUSTODY_CONTINUOUS_TO_EXIT"
    CHECKOUT_PATH_OBSERVED = "CHECKOUT_PATH_OBSERVED"
    NO_MATCHED_CHECKOUT_EVENT = "NO_MATCHED_CHECKOUT_EVENT"
    VISIBILITY_LOST = "VISIBILITY_LOST"
    CAMERA_GAP = "CAMERA_GAP"


class CustodyState(StrEnum):
    ON_SHELF = "ON_SHELF"
    IN_HAND = "IN_HAND"
    IN_BASKET = "IN_BASKET"
    CONCEALED_OBSERVED = "CONCEALED_OBSERVED"
    PURCHASED = "PURCHASED"
    RETURNED = "RETURNED"
    UNACCOUNTED = "UNACCOUNTED"


class Decision(StrEnum):
    OBSERVING = "OBSERVING"
    NORMAL_RESOLVED = "NORMAL_RESOLVED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    HIGH_ATTENTION = "HIGH_ATTENTION"
    ABSTAIN = "ABSTAIN"


@dataclass(frozen=True)
class CustodyObservation:
    event_id: str
    visit_id: str
    episode_id: str
    product_token: str
    camera_id: str
    at_ms: int
    fact: Fact
    visibility: str = "clear"
    direct: bool = True
    association: str = "not_applicable"
    product_linked: bool = False
    source_kind: str = "vision"
    coverage_start_ms: int | None = None
    coverage_end_ms: int | None = None
    source_health: str = "unknown"
    finalized_watermark_ms: int | None = None
    evidence_ref: str = ""

    def valid(self) -> bool:
        return (
            bool(self.event_id)
            and bool(self.visit_id)
            and bool(self.episode_id)
            and bool(self.product_token)
            and bool(self.camera_id)
            and isinstance(self.at_ms, int)
            and not isinstance(self.at_ms, bool)
            and self.at_ms >= 0
            and isinstance(self.fact, Fact)
            and self.visibility in {"clear", "partial", "poor"}
            and isinstance(self.direct, bool)
            and self.association in {"not_applicable", "confirmed", "ambiguous"}
            and isinstance(self.product_linked, bool)
            and self.source_kind in {"vision", "tracker", "pos_reconciliation"}
            and self.source_health in {"unknown", "healthy", "degraded"}
            and all(
                value is None
                or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)
                for value in (
                    self.coverage_start_ms,
                    self.coverage_end_ms,
                    self.finalized_watermark_ms,
                )
            )
            and all(
                len(value) <= 160
                for value in (
                    self.event_id,
                    self.visit_id,
                    self.episode_id,
                    self.product_token,
                    self.camera_id,
                    self.evidence_ref,
                    self.source_kind,
                )
            )
        )


@dataclass(frozen=True)
class TimelineEntry:
    event_id: str
    at_ms: int
    fact: Fact
    camera_id: str
    evidence_ref: str
    explanation: str


@dataclass(frozen=True)
class CustodyAssessment:
    visit_id: str
    episode_id: str
    product_token: str
    state: CustodyState
    decision: Decision
    alarm_eligible: bool
    system_degraded: bool
    reasons: tuple[str, ...]
    timeline: tuple[TimelineEntry, ...]


@dataclass
class _Record:
    state: CustodyState = CustodyState.ON_SHELF
    last_at_ms: int = -1
    started_at_ms: int = -1
    last_camera: str = ""
    facts: set[Fact] = field(default_factory=set)
    fact_times: dict[Fact, int] = field(default_factory=dict)
    event_ids: dict[str, tuple] = field(default_factory=dict)
    timeline: list[TimelineEntry] = field(default_factory=list)
    compromised: bool = False
    reasons: list[str] = field(default_factory=list)
    coverage: dict[Fact, list[tuple[int, int]]] = field(default_factory=dict)
    finalized_watermark_ms: int = -1
    source_health: dict[str, str] = field(default_factory=dict)


_EXPLANATIONS = {
    Fact.PERSON_CONFIRMED: "A person detector confirmed the anonymous visit track.",
    Fact.SHELF_DEPARTURE: "A product-sized shelf change was directly observed.",
    Fact.HAND_CUSTODY: "The product was directly observed in the tracked person's hand.",
    Fact.RETURN_TO_SHELF: "The product was directly observed returning to a shelf.",
    Fact.PLACE_IN_BASKET: "The product was directly observed entering a basket or trolley.",
    Fact.CONCEALMENT_TRANSITION: "The product was directly observed entering clothing or a personal bag.",
    Fact.CHECKOUT_COMPLETED: "A completed checkout transition was observed.",
    Fact.EXIT_CROSSED: "The anonymous visit track crossed the calibrated exit.",
    Fact.CAMERA_HANDOFF: "A local tracker confirmed the anonymous visit handoff between cameras.",
    Fact.CUSTODY_CONTINUOUS_TO_EXIT: "The local tracker reported continuous custody coverage to the exit.",
    Fact.CHECKOUT_PATH_OBSERVED: "The calibrated route through the checkout opportunity remained observed.",
    Fact.NO_MATCHED_CHECKOUT_EVENT: "The product-linked reconciliation source reported no matching checkout event.",
    Fact.VISIBILITY_LOST: "The relevant product transition became visually unaccounted for.",
    Fact.CAMERA_GAP: "Camera continuity was interrupted.",
}


class ProductCustodyEngine:
    """Bounded in-memory state for anonymous visit/product tokens.

    ``visit_id`` is an ephemeral track supplied by the local tracker. It must not
    be a face embedding or persistent person identity. Cross-camera association
    is accepted only when the upstream tracker has explicitly preserved the same
    visit id.
    """

    MAX_EVENTS_PER_RECORD = 128

    def __init__(
        self,
        *,
        max_records: int = 256,
        max_episode_ms: int = 2 * 60 * 60 * 1_000,
        pos_grace_ms: int = 5_000,
    ):
        if max_records < 1 or max_episode_ms < 60_000 or pos_grace_ms < 0:
            raise ValueError("Custody bounds must be positive and practical.")
        self.max_records = max_records
        self.max_episode_ms = max_episode_ms
        self.pos_grace_ms = pos_grace_ms
        self._records: dict[tuple[str, str, str], _Record] = {}
        self.capacity_degraded = False

    def reset(self) -> None:
        self._records.clear()
        self.capacity_degraded = False

    def recover_after_capacity_alarm(self) -> None:
        """Supervised recovery starts a fresh run; old custody is never reused."""
        self.reset()

    def observe(self, observation: CustodyObservation) -> CustodyAssessment:
        if not observation.valid():
            raise ValueError("Custody observation is incomplete or invalid.")
        key = (observation.visit_id, observation.episode_id, observation.product_token)
        record = self._records.get(key)
        if record is None:
            if len(self._records) >= self.max_records:
                oldest = min(self._records, key=lambda item: self._records[item].last_at_ms)
                self._records.pop(oldest)
                self.capacity_degraded = True
            record = self._records[key] = _Record()
        signature = (
            observation.visit_id,
            observation.episode_id,
            observation.product_token,
            observation.camera_id,
            observation.at_ms,
            observation.fact,
            observation.visibility,
            observation.direct,
            observation.association,
            observation.product_linked,
            observation.source_kind,
            observation.coverage_start_ms,
            observation.coverage_end_ms,
            observation.source_health,
            observation.finalized_watermark_ms,
            observation.evidence_ref,
        )
        if observation.event_id in record.event_ids:
            if record.event_ids[observation.event_id] != signature:
                record.compromised = True
                self._reason(record, "A duplicate event identifier arrived with conflicting evidence.")
            return self._assessment(observation, record)
        if observation.at_ms <= record.last_at_ms:
            record.compromised = True
            self._reason(record, "Observation time moved backwards or was duplicated.")
            return self._assessment(observation, record)
        record.last_at_ms = observation.at_ms
        if record.started_at_ms < 0:
            record.started_at_ms = observation.at_ms
        elif observation.at_ms - record.started_at_ms > self.max_episode_ms:
            record.compromised = True
            self._reason(record, "The custody episode exceeded its maximum duration.")
        record.event_ids[observation.event_id] = signature
        if record.last_camera and observation.camera_id != record.last_camera:
            if observation.fact != Fact.CAMERA_HANDOFF:
                record.compromised = True
                self._reason(record, "The camera changed without a confirmed anonymous-track handoff.")
        if observation.fact == Fact.CAMERA_HANDOFF:
            if not record.last_camera or observation.camera_id == record.last_camera:
                record.compromised = True
                self._reason(record, "The camera handoff did not move to a different camera.")
            if observation.association != "confirmed":
                record.compromised = True
                self._reason(record, "The cross-camera association was not confirmed.")
            if observation.source_kind != "tracker":
                record.compromised = True
                self._reason(record, "The camera handoff did not come from the local tracker.")
        record.last_camera = observation.camera_id
        if not observation.direct or observation.visibility != "clear":
            record.compromised = True
            self._reason(record, "At least one required transition was indirect, partial or poorly visible.")
        if observation.fact in {Fact.VISIBILITY_LOST, Fact.CAMERA_GAP}:
            record.compromised = True
            self._reason(record, _EXPLANATIONS[observation.fact])
        record.facts.add(observation.fact)
        record.fact_times[observation.fact] = observation.at_ms
        record.source_health[observation.source_kind] = observation.source_health
        if observation.fact in {
            Fact.CUSTODY_CONTINUOUS_TO_EXIT,
            Fact.CHECKOUT_PATH_OBSERVED,
        }:
            start, end = observation.coverage_start_ms, observation.coverage_end_ms
            if start is None or end is None or end < start:
                record.compromised = True
                self._reason(record, "A coverage fact omitted a valid observed interval.")
            else:
                record.coverage.setdefault(observation.fact, []).append((start, end))
        if observation.fact == Fact.NO_MATCHED_CHECKOUT_EVENT:
            record.finalized_watermark_ms = observation.finalized_watermark_ms or -1
        record.timeline.append(
            TimelineEntry(
                event_id=observation.event_id,
                at_ms=observation.at_ms,
                fact=observation.fact,
                camera_id=observation.camera_id,
                evidence_ref=observation.evidence_ref,
                explanation=_EXPLANATIONS[observation.fact],
            )
        )
        if len(record.timeline) > self.MAX_EVENTS_PER_RECORD:
            removed = record.timeline.pop(0)
            record.event_ids.pop(removed.event_id, None)
            self.capacity_degraded = True
            self._reason(record, "The visit exceeded the bounded local evidence timeline.")
        self._transition(record, observation)
        return self._assessment(observation, record)

    def observe_many(self, observations: Iterable[CustodyObservation]) -> CustodyAssessment:
        result = None
        for observation in observations:
            result = self.observe(observation)
        if result is None:
            raise ValueError("At least one observation is required.")
        return result

    @staticmethod
    def _reason(record: _Record, reason: str) -> None:
        if reason not in record.reasons:
            record.reasons.append(reason)

    def _transition(self, record: _Record, observation: CustodyObservation) -> None:
        fact = observation.fact
        if fact == Fact.SHELF_DEPARTURE:
            if record.state != CustodyState.ON_SHELF:
                record.compromised = True
                self._reason(record, "A shelf departure started outside a fresh custody episode.")
            record.state = CustodyState.UNACCOUNTED
        elif fact == Fact.HAND_CUSTODY:
            if record.state != CustodyState.UNACCOUNTED:
                record.compromised = True
                self._reason(record, "Hand custody was observed without a preceding shelf departure.")
            record.state = CustodyState.IN_HAND
        elif fact == Fact.RETURN_TO_SHELF:
            if record.state != CustodyState.IN_HAND:
                record.compromised = True
                self._reason(record, "A return was reported without direct hand custody in this episode.")
            record.state = CustodyState.RETURNED
        elif fact == Fact.PLACE_IN_BASKET:
            if record.state != CustodyState.IN_HAND:
                record.compromised = True
                self._reason(record, "Basket placement was reported without direct hand custody in this episode.")
            record.state = CustodyState.IN_BASKET
        elif fact == Fact.CONCEALMENT_TRANSITION:
            if record.state != CustodyState.IN_HAND or Fact.PERSON_CONFIRMED not in record.facts:
                record.compromised = True
                self._reason(record, "The concealment observation lacks person, shelf and hand custody prerequisites.")
            record.state = CustodyState.CONCEALED_OBSERVED
        elif fact == Fact.CHECKOUT_COMPLETED:
            if (
                not observation.product_linked
                or observation.source_kind != "pos_reconciliation"
            ):
                record.compromised = True
                self._reason(record, "Checkout was not linked to this product custody episode.")
            if record.state not in {
                CustodyState.IN_HAND,
                CustodyState.IN_BASKET,
                CustodyState.CONCEALED_OBSERVED,
            }:
                record.compromised = True
                self._reason(record, "Checkout was reported without active product custody.")
            record.state = CustodyState.PURCHASED
        elif fact == Fact.CUSTODY_CONTINUOUS_TO_EXIT:
            if observation.source_kind != "tracker" or record.state not in {
                CustodyState.IN_HAND,
                CustodyState.CONCEALED_OBSERVED,
            }:
                record.compromised = True
                self._reason(record, "Continuous custody coverage was not supplied by the tracker for active custody.")
        elif fact == Fact.CHECKOUT_PATH_OBSERVED:
            if observation.source_kind != "tracker":
                record.compromised = True
                self._reason(record, "Checkout-path coverage was not supplied by the calibrated tracker.")
        elif fact == Fact.NO_MATCHED_CHECKOUT_EVENT:
            exit_at = record.fact_times.get(Fact.EXIT_CROSSED, -1)
            if (
                observation.source_kind != "pos_reconciliation"
                or not observation.product_linked
                or observation.source_health != "healthy"
                or exit_at < 0
                or observation.at_ms < exit_at + self.pos_grace_ms
                or observation.finalized_watermark_ms is None
                or observation.finalized_watermark_ms < exit_at
            ):
                record.compromised = True
                self._reason(record, "The missing checkout event was not finalized by a healthy product-linked reconciliation source after the exit grace period.")
        elif fact == Fact.EXIT_CROSSED and observation.source_kind != "tracker":
            record.compromised = True
            self._reason(record, "The exit crossing did not come from the calibrated local tracker.")

    def _assessment(self, observation: CustodyObservation, record: _Record) -> CustodyAssessment:
        facts = record.facts
        reasons = list(record.reasons)
        if self.capacity_degraded:
            decision = Decision.ABSTAIN
            reasons.append("Local custody capacity was exceeded; restart the calibrated monitoring run.")
        elif record.compromised:
            decision = Decision.ABSTAIN
        elif record.state in {CustodyState.RETURNED, CustodyState.PURCHASED}:
            decision = Decision.NORMAL_RESOLVED
        elif record.state == CustodyState.IN_BASKET:
            decision = Decision.OBSERVING
        elif record.state == CustodyState.CONCEALED_OBSERVED:
            decision = Decision.REVIEW_REQUIRED
        elif Fact.EXIT_CROSSED in facts and record.state in {
            CustodyState.IN_HAND,
            CustodyState.UNACCOUNTED,
        }:
            decision = Decision.REVIEW_REQUIRED
        else:
            decision = Decision.OBSERVING

        core_chain = (
            Fact.PERSON_CONFIRMED,
            Fact.SHELF_DEPARTURE,
            Fact.HAND_CUSTODY,
            Fact.CONCEALMENT_TRANSITION,
            Fact.EXIT_CROSSED,
        )
        ordered_chain = all(
            left < right
            for left, right in zip(
                (record.fact_times.get(fact, -1) for fact in core_chain),
                (record.fact_times.get(fact, -1) for fact in core_chain[1:]),
            )
        )
        required_facts = set(core_chain) | {
            Fact.CUSTODY_CONTINUOUS_TO_EXIT,
            Fact.CHECKOUT_PATH_OBSERVED,
            Fact.NO_MATCHED_CHECKOUT_EVENT,
        }
        shelf_at = record.fact_times.get(Fact.SHELF_DEPARTURE, -1)
        exit_at = record.fact_times.get(Fact.EXIT_CROSSED, -1)
        coverage_complete = shelf_at >= 0 and exit_at >= shelf_at and all(
            self._covers(record.coverage.get(fact, ()), shelf_at, exit_at)
            for fact in (
                Fact.CUSTODY_CONTINUOUS_TO_EXIT,
                Fact.CHECKOUT_PATH_OBSERVED,
            )
        )
        pos_finalized = (
            record.source_health.get("pos_reconciliation") == "healthy"
            and record.finalized_watermark_ms >= exit_at >= 0
            and record.fact_times.get(Fact.NO_MATCHED_CHECKOUT_EVENT, -1)
            >= exit_at + self.pos_grace_ms
        )
        if (
            Fact.EXIT_CROSSED in facts
            and facts.intersection(
                {Fact.CUSTODY_CONTINUOUS_TO_EXIT, Fact.CHECKOUT_PATH_OBSERVED}
            )
            and not coverage_complete
        ):
            decision = Decision.ABSTAIN
            reasons.append("The declared tracker coverage leaves part of the custody or checkout path unobserved.")
        resolved_at = max(
            record.fact_times.get(Fact.CHECKOUT_COMPLETED, -1),
            record.fact_times.get(Fact.RETURN_TO_SHELF, -1),
        )
        if (
            not record.compromised
            and not self.capacity_degraded
            and required_facts.issubset(facts)
            and ordered_chain
            and coverage_complete
            and pos_finalized
            and resolved_at < record.fact_times[Fact.SHELF_DEPARTURE]
        ):
            decision = Decision.HIGH_ATTENTION
        alarm_eligible = decision == Decision.HIGH_ATTENTION and not self.capacity_degraded
        if decision == Decision.OBSERVING:
            reasons.append("The observable chain of custody is still incomplete.")
        elif decision == Decision.NORMAL_RESOLVED:
            reasons.append("A directly observed return or completed checkout resolved custody.")
        elif decision == Decision.REVIEW_REQUIRED:
            reasons.append("The sequence warrants staff review but is insufficient for an automatic alarm.")
        elif decision == Decision.HIGH_ATTENTION:
            reasons.append("All required observed and reconciled stages agree; staff verification is still required.")
        return CustodyAssessment(
            visit_id=observation.visit_id,
            episode_id=observation.episode_id,
            product_token=observation.product_token,
            state=record.state,
            decision=decision,
            alarm_eligible=alarm_eligible,
            system_degraded=self.capacity_degraded,
            reasons=tuple(dict.fromkeys(reasons)),
            timeline=tuple(record.timeline),
        )

    @staticmethod
    def _covers(intervals: Iterable[tuple[int, int]], start: int, end: int) -> bool:
        cursor = start
        for left, right in sorted(intervals):
            if right < cursor:
                continue
            if left > cursor:
                return False
            cursor = max(cursor, right)
            if cursor >= end:
                return True
        return cursor >= end


def sampled_window_interpretation(
    *,
    action: str,
    visibility: str,
    person_visible: bool,
    product_visible: bool,
    sequence_observed: bool,
    evidence_frame_indices: list[int],
) -> dict:
    """Explain what one sampled VLM window can and cannot establish.

    A four-frame window has no trustworthy cross-camera visit or product token,
    so it can request review but can never satisfy the complete custody chain.
    """
    supported = (
        visibility == "clear"
        and person_visible
        and product_visible
        and sequence_observed
        and len(evidence_frame_indices) >= 2
    )
    timeline = (
        [
            {
                "frame_indices": list(evidence_frame_indices),
                "assertion": action,
                "source": "single_local_vlm_response",
                "explanation": "One model response interpreted the cited sampled frames as this action; its component claims are correlated, not independent evidence.",
            }
        ]
        if supported
        else []
    )

    if action == "NORMAL_SHOPPING" and visibility == "clear" and person_visible:
        decision, state = Decision.OBSERVING, CustodyState.ON_SHELF
        reasons = ["No directly supported product transition was found in this sampled window."]
    elif action == "RETURN_PRODUCT" and supported:
        decision, state = Decision.NORMAL_RESOLVED, CustodyState.RETURNED
        reasons = ["A return was observed in this window; the anonymous visit remains untracked outside it."]
    elif action == "PLACE_IN_BASKET" and supported:
        decision, state = Decision.OBSERVING, CustodyState.IN_BASKET
        reasons = ["Basket placement is ordinary custody; later checkout or return is not observed here."]
    elif action in {"TAKE_PRODUCT", "POSSIBLE_CONCEALMENT"} and supported:
        decision = Decision.REVIEW_REQUIRED
        state = (
            CustodyState.IN_HAND
            if action == "TAKE_PRODUCT"
            else CustodyState.CONCEALED_OBSERVED
        )
        reasons = [
            "This sampled window lacks a continuous anonymous visit through checkout and exit."
        ]
    else:
        decision, state = Decision.ABSTAIN, CustodyState.UNACCOUNTED
        reasons = [
            "The sampled window lacks clear, direct and chronological evidence for a custody transition."
        ]
    return {
        "schema_version": "sampled-window-v1",
        "decision": decision,
        "state": state,
        "complete_custody_alarm_eligible": False,
        "reasons": reasons,
        "timeline": timeline,
        "limitations": [
            "No cross-window anonymous visit continuity.",
            "No cross-camera product token continuity.",
            "No checkout-to-exit chain in this sampled window.",
            "A supported window may enter review history, but it cannot request an audible alarm.",
        ],
    }
