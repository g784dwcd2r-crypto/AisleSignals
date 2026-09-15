"""Layout geometry, authority and tenant-boundary regression tests."""

from copy import deepcopy

import pytest

from services.api.app import create_app
from services.api.pilot_identity import _new_site, _new_user, add_site, add_user
from services.api.store import ident
from test_pilot_identity import PASSWORD, client_for, pilot


def rectangle(zone_id, kind, x=0.1, y=0.1):
    return {"id": zone_id, "kind": kind, "label": kind.title(), "points": [
        {"x": x, "y": y}, {"x": x + .2, "y": y},
        {"x": x + .2, "y": y + .2}, {"x": x, "y": y + .2},
    ]}


def payload(expected=0, complete=True, layout="2x2"):
    columns, rows = {"2x2": (2, 2), "3x2": (3, 2), "2x3": (2, 3)}[layout]
    cameras = [{"camera_index": index, "label": f"Camera {index + 1}",
                "crop": {"x": (index % columns) / columns, "y": (index // columns) / rows,
                         "width": 1 / columns, "height": 1 / rows},
                "zones": []} for index in range(columns * rows)]
    cameras[0]["zones"] = [rectangle("entrance-1", "ENTRANCE")]
    if complete:
        cameras[1]["zones"] = [rectangle("exit-1", "EXIT")]
        cameras[2]["zones"] = [rectangle("cashier-1", "CASHIER")]
        cameras[3]["zones"] = [rectangle("shelf-1", "SHELF")]
        for index in range(4, len(cameras)):
            cameras[index]["zones"] = [rectangle(f"blind-{index}", "BLIND")]
    return {"schema_version": "1.0", "expected_version": expected,
            "layout": layout, "source_label": "Synthetic CCTV monitor", "cameras": cameras}


@pytest.mark.parametrize("layout,count", [("2x2", 4), ("3x2", 6), ("2x3", 6)])
def test_every_supported_four_and_six_camera_layout_is_persisted(pilot, layout, count):
    app, _ = pilot
    saved = client_for(app).put("/api/layout-calibration", json=payload(layout=layout))
    assert saved.status_code == 200, saved.text
    assert saved.json()["layout"] == layout
    assert len(saved.json()["cameras"]) == count
    assert saved.json()["readiness"]["status"] == "READY_FOR_SITE_ACCEPTANCE"


def test_manager_saves_versioned_map_and_readiness_is_server_derived(pilot):
    app, initial = pilot
    manager = client_for(app)
    empty = manager.get("/api/layout-calibration")
    assert empty.status_code == 200
    assert empty.json()["version"] == 0
    assert empty.json()["readiness"]["alarm_authority"] is False
    incomplete = manager.put("/api/layout-calibration", json=payload(complete=False))
    assert incomplete.status_code == 200
    assert incomplete.json()["readiness"]["status"] == "INCOMPLETE"
    assert set(incomplete.json()["readiness"]["missing_required_kinds"]) == {"EXIT", "CASHIER", "SHELF"}
    updated = manager.put("/api/layout-calibration", json=payload(expected=1))
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["readiness"] == {
        "status": "READY_FOR_SITE_ACCEPTANCE", "missing_required_kinds": [],
        "uncalibrated_camera_indices": [], "warnings": [], "alarm_authority": False,
    }
    with app.state.store.transaction() as conn:
        row = conn.execute("SELECT organisation_id,site_id FROM entities WHERE kind='layout_calibration'").fetchone()
        assert tuple(row) == (initial["site"]["organisation_id"], initial["site"]["id"])
        audit = conn.execute("SELECT body FROM entities WHERE kind='audit' ORDER BY created_at DESC LIMIT 1").fetchone()[0]
        assert "alarm authority remains disabled" in audit


def test_geometry_camera_set_urls_and_concurrency_fail_closed(pilot):
    app, _ = pilot
    manager = client_for(app)
    invalid = payload()
    invalid["cameras"][0]["zones"][0]["points"] = [
        {"x": .1, "y": .1}, {"x": .5, "y": .5},
        {"x": .1, "y": .5}, {"x": .5, "y": .1},
    ]
    assert manager.put("/api/layout-calibration", json=invalid).status_code == 422
    missing_camera = payload()
    missing_camera["cameras"].pop()
    assert manager.put("/api/layout-calibration", json=missing_camera).status_code == 422
    secret_address = payload()
    secret_address["source_label"] = "rtsp://operator:secret@camera.test/live"
    rejected = manager.put("/api/layout-calibration", json=secret_address)
    assert rejected.status_code == 422 and "secret" not in rejected.text
    assert manager.put("/api/layout-calibration", json=payload()).status_code == 200
    stale = manager.put("/api/layout-calibration", json=payload())
    assert stale.status_code == 409
    assert stale.json()["error"]["current_version"] == 1


def test_reviewer_can_read_but_not_change_and_branches_are_isolated(pilot):
    app, initial = pilot
    manager = client_for(app)
    assert manager.put("/api/layout-calibration", json=payload()).status_code == 200
    add_user(app.state.store, "layout.reviewer@example.test", "Synthetic Layout Reviewer", PASSWORD,
             [initial["site"]["id"]], "REVIEWER")
    reviewer = client_for(app, "layout.reviewer@example.test")
    assert reviewer.get("/api/layout-calibration").json()["version"] == 1
    denied = reviewer.put("/api/layout-calibration", json=payload(expected=1))
    assert denied.status_code == 403
    other = add_site(app.state.store, initial["site"]["organisation_id"], "Synthetic Other Branch")
    add_user(app.state.store, "layout.other@example.test", "Synthetic Other Manager", PASSWORD, [other["id"]], "MANAGER")
    outsider = client_for(app, "layout.other@example.test")
    assert outsider.get("/api/layout-calibration").json()["version"] == 0
    forged = deepcopy(payload(expected=1))
    forged["site_id"] = initial["site"]["id"]
    assert outsider.put("/api/layout-calibration", json=forged).status_code == 422


def test_saved_calibration_survives_service_restart(pilot):
    app, _ = pilot
    manager = client_for(app)
    saved = manager.put("/api/layout-calibration", json=payload()).json()
    database = app.state.store.path
    app.state.interactions.close()
    restarted = create_app(database, mode="pilot")
    try:
        reopened = client_for(restarted).get("/api/layout-calibration")
        assert reopened.status_code == 200
        assert reopened.json() == saved
        assert reopened.json()["readiness"]["alarm_authority"] is False
    finally:
        restarted.state.interactions.close()


def test_separate_organisations_cannot_read_or_overwrite_each_others_map(pilot):
    app, first = pilot
    first_manager = client_for(app)
    assert first_manager.put("/api/layout-calibration", json=payload()).status_code == 200
    second_organisation = ident()
    with app.state.store.transaction() as conn:
        second_site = _new_site(conn, app.state.store, second_organisation,
                                "Synthetic Separate Group", "Synthetic Separate Branch")
        _new_user(conn, app.state.store, "layout.separate@example.test",
                  "Synthetic Separate Manager", PASSWORD, [second_site["id"]], "MANAGER")
    second_manager = client_for(app, "layout.separate@example.test")
    assert second_manager.get("/api/layout-calibration").json()["version"] == 0
    second_saved = second_manager.put("/api/layout-calibration", json={
        **payload(), "source_label": "Separate organisation monitor",
    })
    assert second_saved.status_code == 200
    assert second_saved.json()["source_label"] == "Separate organisation monitor"
    assert first_manager.get("/api/layout-calibration").json()["source_label"] == "Synthetic CCTV monitor"
    with app.state.store.transaction() as conn:
        rows = conn.execute(
            "SELECT organisation_id,site_id FROM entities WHERE kind='layout_calibration' ORDER BY organisation_id"
        ).fetchall()
        assert {(row["organisation_id"], row["site_id"]) for row in rows} == {
            (first["site"]["organisation_id"], first["site"]["id"]),
            (second_organisation, second_site["id"]),
        }
