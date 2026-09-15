"""Synthetic state-machine tests; these do not measure vision accuracy."""

import pytest

from services.api.custody_engine import (
    CustodyObservation,
    CustodyState,
    Decision,
    Fact,
    ProductCustodyEngine,
    sampled_window_interpretation,
)


def event(number, fact, **changes):
    return CustodyObservation(
        **{
            "event_id": f"event-{number}",
            "visit_id": "visit-ephemeral-17",
            "episode_id": "episode-1",
            "product_token": "camera-2:shelf-cell-4:product-1",
            "camera_id": "camera-2",
            "at_ms": number * 1_000,
            "fact": fact,
            "evidence_ref": f"frame-{number}",
            **changes,
        }
    )


def high_attention_sequence():
    return [
        event(1, Fact.PERSON_CONFIRMED),
        event(2, Fact.SHELF_DEPARTURE),
        event(3, Fact.HAND_CUSTODY),
        event(4, Fact.CONCEALMENT_TRANSITION),
        event(5, Fact.CAMERA_HANDOFF, camera_id="camera-exit", association="confirmed", source_kind="tracker"),
        event(6, Fact.EXIT_CROSSED, camera_id="camera-exit", source_kind="tracker"),
        event(7, Fact.CUSTODY_CONTINUOUS_TO_EXIT, camera_id="camera-exit", source_kind="tracker", coverage_start_ms=2_000, coverage_end_ms=6_000),
        event(8, Fact.CHECKOUT_PATH_OBSERVED, camera_id="camera-exit", source_kind="tracker", coverage_start_ms=2_000, coverage_end_ms=6_000),
        event(11, Fact.NO_MATCHED_CHECKOUT_EVENT, camera_id="camera-exit", source_kind="pos_reconciliation", source_health="healthy", product_linked=True, finalized_watermark_ms=6_000),
    ]


def test_requires_complete_direct_chain_before_high_attention():
    engine = ProductCustodyEngine()
    decisions = [engine.observe(item).decision for item in high_attention_sequence()]
    assert decisions == [
        Decision.OBSERVING,
        Decision.OBSERVING,
        Decision.OBSERVING,
        Decision.REVIEW_REQUIRED,
        Decision.REVIEW_REQUIRED,
        Decision.REVIEW_REQUIRED,
        Decision.ABSTAIN,
        Decision.REVIEW_REQUIRED,
        Decision.HIGH_ATTENTION,
    ]
    result = engine.observe(high_attention_sequence()[-1])
    assert result.alarm_eligible is True
    assert result.state == CustodyState.CONCEALED_OBSERVED
    assert len(result.timeline) == 9
    assert result.timeline[-1].camera_id == "camera-exit"
    assert "staff verification" in result.reasons[-1]


@pytest.mark.parametrize(
    "missing",
    [
        Fact.PERSON_CONFIRMED,
        Fact.SHELF_DEPARTURE,
        Fact.HAND_CUSTODY,
        Fact.CONCEALMENT_TRANSITION,
        Fact.CAMERA_HANDOFF,
        Fact.CUSTODY_CONTINUOUS_TO_EXIT,
        Fact.CHECKOUT_PATH_OBSERVED,
        Fact.NO_MATCHED_CHECKOUT_EVENT,
        Fact.EXIT_CROSSED,
    ],
)
def test_every_independent_stage_is_required_for_high_attention(missing):
    result = ProductCustodyEngine().observe_many(
        item for item in high_attention_sequence() if item.fact != missing
    )
    assert result.alarm_eligible is False
    assert result.decision in {Decision.OBSERVING, Decision.REVIEW_REQUIRED, Decision.ABSTAIN}


def test_return_and_checkout_are_normal_resolutions():
    for resolution, expected in [
        (Fact.RETURN_TO_SHELF, CustodyState.RETURNED),
        (Fact.CHECKOUT_COMPLETED, CustodyState.PURCHASED),
    ]:
        result = ProductCustodyEngine().observe_many(
            [
                event(1, Fact.PERSON_CONFIRMED),
                event(2, Fact.SHELF_DEPARTURE),
                event(3, Fact.HAND_CUSTODY),
                event(
                    4,
                    resolution,
                    product_linked=resolution == Fact.CHECKOUT_COMPLETED,
                    source_kind=(
                        "pos_reconciliation"
                        if resolution == Fact.CHECKOUT_COMPLETED
                        else "vision"
                    ),
                ),
            ]
        )
        assert result.state == expected
        assert result.decision == Decision.NORMAL_RESOLVED
        assert result.alarm_eligible is False


def test_partial_indirect_and_camera_gap_force_abstention():
    for broken in [
        event(2, Fact.SHELF_DEPARTURE, visibility="partial"),
        event(2, Fact.SHELF_DEPARTURE, direct=False),
        event(2, Fact.CAMERA_GAP),
    ]:
        result = ProductCustodyEngine().observe_many(
            [event(1, Fact.PERSON_CONFIRMED), broken]
            + high_attention_sequence()[2:]
        )
        assert result.decision == Decision.ABSTAIN
        assert result.alarm_eligible is False
        assert result.reasons


def test_sparse_healthy_observations_are_allowed_but_out_of_order_events_are_not():
    engine = ProductCustodyEngine()
    engine.observe(event(1, Fact.PERSON_CONFIRMED))
    result = engine.observe(
        CustodyObservation(
            **{
                **event(2, Fact.SHELF_DEPARTURE).__dict__,
                "event_id": "late",
                "at_ms": 10_000,
            }
        )
    )
    assert result.decision == Decision.OBSERVING
    result = engine.observe(
        CustodyObservation(
            **{
                **event(3, Fact.HAND_CUSTODY).__dict__,
                "event_id": "old",
                "at_ms": 9_000,
            }
        )
    )
    assert result.decision == Decision.ABSTAIN


def test_duplicate_delivery_is_idempotent_and_does_not_extend_timeline():
    engine = ProductCustodyEngine()
    observation = event(1, Fact.PERSON_CONFIRMED)
    first = engine.observe(observation)
    second = engine.observe(observation)
    assert second == first
    assert len(second.timeline) == 1


def test_conflicting_duplicate_identifier_forces_abstention():
    engine = ProductCustodyEngine()
    engine.observe(event(1, Fact.PERSON_CONFIRMED))
    conflict = engine.observe(event(1, Fact.SHELF_DEPARTURE))
    assert conflict.decision == Decision.ABSTAIN
    assert "conflicting" in " ".join(conflict.reasons)


def test_cross_camera_handoff_must_be_confirmed_by_tracker():
    for handoff in [
        event(2, Fact.HAND_CUSTODY, camera_id="camera-exit"),
        event(2, Fact.CAMERA_HANDOFF, camera_id="camera-exit", association="ambiguous", source_kind="tracker"),
        event(2, Fact.CAMERA_HANDOFF, camera_id="camera-exit", association="confirmed", source_kind="vision"),
    ]:
        engine = ProductCustodyEngine()
        engine.observe(event(1, Fact.PERSON_CONFIRMED))
        assert engine.observe(handoff).decision == Decision.ABSTAIN


def test_checkout_and_no_match_require_product_linked_reconciliation():
    for terminal in [
        event(4, Fact.CHECKOUT_COMPLETED),
        event(4, Fact.NO_MATCHED_CHECKOUT_EVENT),
        event(4, Fact.NO_MATCHED_CHECKOUT_EVENT, product_linked=True, source_kind="vision"),
    ]:
        engine = ProductCustodyEngine()
        engine.observe_many(
            [
                event(1, Fact.PERSON_CONFIRMED),
                event(2, Fact.SHELF_DEPARTURE),
                event(3, Fact.HAND_CUSTODY),
            ]
        )
        assert engine.observe(terminal).decision == Decision.ABSTAIN


def test_new_episode_does_not_inherit_old_return_or_checkout_facts():
    engine = ProductCustodyEngine()
    old = [
        event(1, Fact.PERSON_CONFIRMED),
        event(2, Fact.SHELF_DEPARTURE),
        event(3, Fact.HAND_CUSTODY),
        event(4, Fact.RETURN_TO_SHELF),
    ]
    assert engine.observe_many(old).decision == Decision.NORMAL_RESOLVED
    fresh = [
        CustodyObservation(
            **{
                **item.__dict__,
                "event_id": "new-" + item.event_id,
                "episode_id": "episode-2",
                "at_ms": item.at_ms + 10_000,
                "coverage_start_ms": (
                    item.coverage_start_ms + 10_000
                    if item.coverage_start_ms is not None
                    else None
                ),
                "coverage_end_ms": (
                    item.coverage_end_ms + 10_000
                    if item.coverage_end_ms is not None
                    else None
                ),
                "finalized_watermark_ms": (
                    item.finalized_watermark_ms + 10_000
                    if item.finalized_watermark_ms is not None
                    else None
                ),
            }
        )
        for item in high_attention_sequence()
    ]
    assert engine.observe_many(fresh).decision == Decision.HIGH_ATTENTION


def test_high_attention_requires_gap_free_coverage_and_finalized_pos_watermark():
    cases = []
    missing_coverage = high_attention_sequence()
    missing_coverage[6] = event(
        7,
        Fact.CUSTODY_CONTINUOUS_TO_EXIT,
        camera_id="camera-exit",
        source_kind="tracker",
        coverage_start_ms=2_000,
        coverage_end_ms=5_000,
    )
    cases.append(missing_coverage)
    stale_watermark = high_attention_sequence()
    stale_watermark[-1] = event(
        11,
        Fact.NO_MATCHED_CHECKOUT_EVENT,
        camera_id="camera-exit",
        source_kind="pos_reconciliation",
        source_health="healthy",
        product_linked=True,
        finalized_watermark_ms=5_999,
    )
    cases.append(stale_watermark)
    unhealthy_pos = high_attention_sequence()
    unhealthy_pos[-1] = event(
        11,
        Fact.NO_MATCHED_CHECKOUT_EVENT,
        camera_id="camera-exit",
        source_kind="pos_reconciliation",
        source_health="degraded",
        product_linked=True,
        finalized_watermark_ms=6_000,
    )
    cases.append(unhealthy_pos)
    early_reconciliation = high_attention_sequence()
    early_reconciliation[-1] = event(
        10,
        Fact.NO_MATCHED_CHECKOUT_EVENT,
        camera_id="camera-exit",
        source_kind="pos_reconciliation",
        source_health="healthy",
        product_linked=True,
        finalized_watermark_ms=6_000,
    )
    cases.append(early_reconciliation)
    for observations in cases:
        result = ProductCustodyEngine().observe_many(observations)
        assert result.decision == Decision.ABSTAIN
        assert result.alarm_eligible is False


def test_episode_duration_is_bounded_and_capacity_recovery_is_explicit():
    engine = ProductCustodyEngine(max_records=1, max_episode_ms=60_000)
    engine.observe(event(1, Fact.PERSON_CONFIRMED))
    expired = engine.observe(
        CustodyObservation(
            **{
                **event(2, Fact.SHELF_DEPARTURE).__dict__,
                "event_id": "expired",
                "at_ms": 62_000,
            }
        )
    )
    assert expired.decision == Decision.ABSTAIN
    engine.observe(
        CustodyObservation(
            **{
                **event(1, Fact.PERSON_CONFIRMED).__dict__,
                "event_id": "capacity",
                "visit_id": "other",
            }
        )
    )
    assert engine.capacity_degraded is True
    engine.recover_after_capacity_alarm()
    assert engine.capacity_degraded is False
    assert engine.observe(event(1, Fact.PERSON_CONFIRMED)).decision == Decision.OBSERVING


def test_records_are_isolated_by_anonymous_visit_and_product():
    engine = ProductCustodyEngine()
    engine.observe_many(high_attention_sequence()[:-1])
    unrelated_exit = CustodyObservation(
        **{
            **event(5, Fact.EXIT_CROSSED).__dict__,
            "event_id": "unrelated-exit",
            "visit_id": "visit-ephemeral-99",
            "episode_id": "episode-99",
            "source_kind": "tracker",
        }
    )
    result = engine.observe(unrelated_exit)
    assert result.decision == Decision.OBSERVING
    assert result.alarm_eligible is False


def test_missing_prerequisites_abstains_instead_of_inventing_custody():
    result = ProductCustodyEngine().observe_many(
        [
            event(1, Fact.PERSON_CONFIRMED),
            event(2, Fact.CONCEALMENT_TRANSITION),
            event(3, Fact.EXIT_CROSSED),
        ]
    )
    assert result.decision == Decision.ABSTAIN
    assert "prerequisites" in " ".join(result.reasons)


def test_bounded_state_evicts_oldest_record_and_rejects_invalid_input():
    engine = ProductCustodyEngine(max_records=2)
    for index in range(3):
        engine.observe(
            CustodyObservation(
                event_id=f"person-{index}",
                visit_id=f"visit-{index}",
                episode_id="episode",
                product_token="product",
                camera_id="camera",
                at_ms=index,
                fact=Fact.PERSON_CONFIRMED,
            )
        )
    # Eviction means the oldest visit starts afresh and cannot inherit evidence.
    restarted = engine.observe(
        CustodyObservation(
            event_id="old-exit",
            visit_id="visit-0",
            episode_id="episode",
            product_token="product",
            camera_id="exit",
            at_ms=10,
            fact=Fact.EXIT_CROSSED,
        )
    )
    assert len(restarted.timeline) == 1
    assert restarted.system_degraded is True
    assert restarted.alarm_eligible is False
    with pytest.raises(ValueError):
        engine.observe(event(4, Fact.HAND_CUSTODY, visibility="unknown"))
    with pytest.raises(ValueError):
        engine.observe_many([])


def test_sampled_window_never_claims_complete_custody_or_alarm_eligibility():
    result = sampled_window_interpretation(
        action="POSSIBLE_CONCEALMENT",
        visibility="clear",
        person_visible=True,
        product_visible=True,
        sequence_observed=True,
        evidence_frame_indices=[0, 3],
    )
    assert result["decision"] == Decision.REVIEW_REQUIRED
    assert result["state"] == CustodyState.CONCEALED_OBSERVED
    assert result["complete_custody_alarm_eligible"] is False
    assert result["timeline"] == [
        {
            "frame_indices": [0, 3],
            "assertion": "POSSIBLE_CONCEALMENT",
            "source": "single_local_vlm_response",
            "explanation": "One model response interpreted the cited sampled frames as this action; its component claims are correlated, not independent evidence.",
        }
    ]
    assert len(result["limitations"]) == 4


def test_unclear_sampled_window_abstains_with_no_fabricated_transition():
    result = sampled_window_interpretation(
        action="UNCLEAR",
        visibility="poor",
        person_visible=False,
        product_visible=False,
        sequence_observed=False,
        evidence_frame_indices=[],
    )
    assert result["decision"] == Decision.ABSTAIN
    assert result["timeline"] == []
