"""Actual pilot job pipeline with synthetic images/model output, never accuracy evidence."""
import pytest
from test_cloud_connection import paired_workspace, configured, action
from test_interactions import MockProvider, sample, submit, wait_job


@pytest.mark.parametrize('source_kind,queued', [('SCREEN_CAPTURE',True),('RECORDED_VIDEO',False)])
def test_job_completion_atomically_queues_only_admitted_live_review_metadata(paired_workspace, source_kind, queued):
    app, initial, client, remote = paired_workspace
    saved = configured(client)
    resumed = action(client, saved, 'resume')
    assert resumed.status_code == 200, resumed.text
    app.state.interactions.provider = MockProvider()
    response = submit(client, sample(source_kind=source_kind))
    assert response.status_code == 202, response.text
    item_id = response.json()['id']
    result = wait_job(client, item_id)
    assert result['status'] == 'completed', result
    assert result['result']['action'] == 'POSSIBLE_CONCEALMENT'
    assert result['result']['cloud_sync']['state'] == ('QUEUED' if queued else 'NOT_QUEUED')
    assert '_cloud_admission' not in result['result']
    with app.state.store.transaction() as conn:
        rows = conn.execute('SELECT * FROM cloud_sync_items').fetchall()
        assert len(rows) == int(queued)
        if queued:
            ctx = app.state.cloud_connection.delivery_context(conn)
            assert app.state.interactions.cloud_hooks.source_state(conn, ctx, 'interaction', item_id) == 'AVAILABLE'
    deleted = client.delete('/api/interactions/' + item_id)
    assert deleted.status_code == 200, deleted.text
    with app.state.store.transaction() as conn:
        if queued:
            ctx = app.state.cloud_connection.delivery_context(conn)
            assert app.state.interactions.cloud_hooks.source_state(conn, ctx, 'interaction', item_id) == 'DELETED'
