from copy import deepcopy
from uuid import uuid4

import pytest

from scripts.evaluate_xai_three_clips import ManifestError, evaluate


def clip(index, *, with_xai=True):
    local = {
        "person_visible": True,
        "product_visible": True,
        "sequence_observed": True,
        "action": "POSSIBLE_CONCEALMENT",
        "evidence_frame_indices": [0, 2],
    }
    external = None
    if with_xai:
        external = {
            "status": "COMPLETED",
            "advisory_only": True,
            "alarm_decision_permitted": False,
            "requires_local_candidate": True,
            "selected_frame_indices": [0, 1, 2],
            "result": {
                "blind_observation": {"observed_action": "UNCLEAR"},
                "comparison": "INCONCLUSIVE",
                "local_action": "POSSIBLE_CONCEALMENT",
            },
        }
    return {
        "trial_id": f"private-trial-{index}",
        "interaction_id": str(uuid4()),
        "staff_label": "UNCLEAR",
        "local_person_gate": local,
        "xai_verification": external,
    }


def manifest():
    return {"schema_version": "1.0", "clips": [clip(1), clip(2), clip(3, with_xai=False)]}


def test_evaluator_compares_metadata_without_alarm_or_provider_action():
    report = evaluate(manifest())
    assert report["trial_count"] == 3
    assert report["xai_completed"] == 2
    assert report["paid_calls_requested_by_this_tool"] == 0
    assert report["alarm_effect"] == "NONE"
    assert all(row["alarm_effect"] == "NONE" for row in report["trials"])


@pytest.mark.parametrize("mutation", ["unsafe_alarm", "partial_frames", "changed_local"])
def test_evaluator_rejects_unsafe_or_inconsistent_records(mutation):
    data = deepcopy(manifest())
    external = data["clips"][0]["xai_verification"]
    if mutation == "unsafe_alarm":
        external["alarm_decision_permitted"] = True
    elif mutation == "partial_frames":
        external["selected_frame_indices"] = [0, 2, 3]
    else:
        external["result"]["local_action"] = "RETURN_PRODUCT"
    with pytest.raises(ManifestError):
        evaluate(data)
