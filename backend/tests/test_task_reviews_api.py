"""Requester acceptance exercises real HTTP authentication and PostgreSQL transactions."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_marketplace_tasks_api import competing_requests as competing_requests
from test_marketplace_tasks_api import demo_body as demo_body
from test_marketplace_tasks_api import marketplace_api as marketplace_api
from test_marketplace_tasks_api import marketplace_db as marketplace_db
from test_marketplace_tasks_api import marketplace_pg as marketplace_pg

from app.services.agent_service import AgentService


@pytest.fixture
async def reviewed_task(marketplace_api, demo_body):
    client, (_, project_id, _, _, keys) = marketplace_api
    response = await client.post(
        f"/api/v1/agents/projects/{project_id}/tasks",
        json={**demo_body, "review_required": True},
        headers={"X-API-Key": keys[0]},
    )
    assert response.status_code == 200
    return f"/api/v1/agents/tasks/{response.json()['task_id']}"


async def test_reviewed_complete_cannot_self_award(marketplace_api, reviewed_task):
    client, (maker, _, _, _, keys) = marketplace_api
    headers = {"X-API-Key": keys[1]}
    assert (await client.post(reviewed_task + "/claim", headers=headers)).status_code == 200
    response = await client.post(
        reviewed_task + "/complete", json={"result": "done"}, headers=headers
    )
    assert response.status_code == 409
    async with maker() as db:
        assert await db.scalar(text("SELECT sum(karma) FROM agents")) == 0
        assert await db.scalar(text("SELECT status FROM tasks")) == "claimed"


@pytest.fixture
async def claimed_review(marketplace_api, reviewed_task):
    client, (_, _, _, _, keys) = marketplace_api
    response = await client.post(reviewed_task + "/claim", headers={"X-API-Key": keys[1]})
    assert response.status_code == 200
    return reviewed_task


@pytest.fixture
def submission():
    return {
        "result": "Synthetic reviewed result",
        "expected_version": 1,
        "idempotency_key": str(uuid4()),
    }


@pytest.fixture
def decision():
    return {
        "action": "accept",
        "reason": "Checked synthetic result",
        "expected_version": 2,
        "idempotency_key": str(uuid4()),
    }


@pytest.fixture
async def outsider(marketplace_api):
    _, (maker, _, _, _, _) = marketplace_api
    agent_id, key = uuid4(), str(uuid4())
    async with maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO agents (id, name, api_key_hash) "
                "VALUES (:id, 'Synthetic outsider', :hash)"
            ),
            {"id": agent_id, "hash": AgentService.hash_api_key(key)},
        )
    return {"X-API-Key": key}


async def test_create_mode_retry_and_list(marketplace_api, reviewed_task, demo_body):
    client, (maker, project_id, _, _, keys) = marketplace_api
    headers = {"X-API-Key": keys[0]}
    url = f"/api/v1/agents/projects/{project_id}/tasks"
    response = await client.post(url, json={**demo_body, "review_required": True}, headers=headers)
    assert response.status_code == 200 and response.json()["created"] is False
    response = await client.post(url, json=demo_body, headers=headers)
    assert response.status_code == 409
    listing = await client.get("/api/v1/agents/tasks", headers=headers)
    assert listing.json()[0]["review_required"] is True
    assert listing.json()[0]["review_version"] == 0
    self_claim = await client.post(reviewed_task + "/claim", headers=headers)
    assert self_claim.status_code == 403
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM task_review_events")) == 1


async def test_create_legacy_retry_cannot_switch_mode(marketplace_api, demo_body):
    client, (maker, project_id, _, _, keys) = marketplace_api
    url, headers = f"/api/v1/agents/projects/{project_id}/tasks", {"X-API-Key": keys[0]}
    assert (await client.post(url, json=demo_body, headers=headers)).status_code == 200
    assert (
        await client.post(url, json={**demo_body, "review_required": True}, headers=headers)
    ).status_code == 409
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM task_reviews")) == 0


@pytest.mark.parametrize(
    "ownership",
    [(False, False, "unknown"), (True, True, "internal"), (True, False, "external_unverified")],
)
async def test_ownership_labels_no_rewards(marketplace_api, reviewed_task, ownership):
    known, same, label = ownership
    client, (maker, _, owner, other, keys) = marketplace_api
    if known:
        first = uuid4()
        async with maker.begin() as db:
            await db.execute(
                text(
                    "UPDATE agents SET owner_user_id = CASE "
                    "WHEN id = :owner THEN CAST(:first AS uuid) ELSE CAST(:second AS uuid) END"
                ),
                {"owner": owner, "first": first, "second": first if same else uuid4()},
            )
    assert (
        await client.post(reviewed_task + "/claim", headers={"X-API-Key": keys[1]})
    ).status_code == 200
    snapshot = await client.get(reviewed_task + "/review", headers={"X-API-Key": keys[0]})
    assert snapshot.json()["relationship"] == label and snapshot.json()["version"] == 1
    async with maker() as db:
        assert await db.scalar(text("SELECT sum(karma) FROM agents")) == 0


async def test_changes_resubmit_accept_and_old_receipts(
    marketplace_api,
    claimed_review,
    submission,
    decision,
):
    client, (maker, _, _, _, keys) = marketplace_api
    worker, requester = {"X-API-Key": keys[1]}, {"X-API-Key": keys[0]}
    submitted = await client.post(claimed_review + "/submit", json=submission, headers=worker)
    assert submitted.status_code == 200 and submitted.json()["version"] == 2
    assert submitted.json()["task_status"] == "claimed"
    assert (await client.post(claimed_review + "/unclaim", headers=worker)).status_code == 409
    changes = {**decision, "action": "request_changes"}
    requested = await client.post(claimed_review + "/review", json=changes, headers=requester)
    assert requested.json()["state"] == "changes_requested" and requested.json()["version"] == 3
    snapshot = (await client.get(claimed_review + "/review", headers=worker)).json()["history"][0]
    assert snapshot["action"] == "request_changes" and snapshot["reason"] == changes["reason"]
    assert "payload" not in snapshot and "idempotency_key" not in snapshot
    replay = await client.post(claimed_review + "/submit", json=submission, headers=worker)
    assert replay.json() == submitted.json()
    resubmit = {**submission, "expected_version": 3, "idempotency_key": str(uuid4())}
    response = await client.post(claimed_review + "/submit", json=resubmit, headers=worker)
    assert response.json()["submission_version"] == 2 and response.json()["version"] == 4
    accepted_body = {**decision, "expected_version": 4, "idempotency_key": str(uuid4())}
    accepted = await client.post(claimed_review + "/review", json=accepted_body, headers=requester)
    assert accepted.json()["state"] == "accepted" and accepted.json()["task_status"] == "completed"
    assert (
        await client.post(claimed_review + "/review", json=accepted_body, headers=requester)
    ).json() == accepted.json()
    assert (
        await client.post(claimed_review + "/review", json=changes, headers=requester)
    ).json() == requested.json()
    assert (
        await client.post(claimed_review + "/submit", json=submission, headers=worker)
    ).json() == submitted.json()
    snapshot = (await client.get(claimed_review + "/review", headers=worker)).json()
    assert snapshot["result"] == submission["result"] and len(snapshot["history"]) == 6
    async with maker() as db:
        assert await db.scalar(text("SELECT sum(karma) FROM agents")) == 0
        assert await db.scalar(text("SELECT completed_at IS NOT NULL FROM tasks")) is True


@pytest.mark.parametrize(
    "outcome",
    [("reject", "rejected", "cancelled"), ("cancel", "cancelled", "cancelled")],
)
async def test_final_states_cannot_reopen(
    marketplace_api, claimed_review, submission, decision, outcome
):
    action, state, status = outcome
    client, (maker, _, _, _, keys) = marketplace_api
    assert (
        await client.post(
            claimed_review + "/submit", json=submission, headers={"X-API-Key": keys[1]}
        )
    ).status_code == 200
    body = {**decision, "action": action}
    response = await client.post(
        claimed_review + "/review", json=body, headers={"X-API-Key": keys[0]}
    )
    assert response.json()["state"] == state and response.json()["task_status"] == status
    response = await client.post(
        claimed_review + "/review",
        json={**body, "expected_version": 3, "idempotency_key": str(uuid4())},
        headers={"X-API-Key": keys[0]},
    )
    assert response.status_code == 409
    async with maker() as db:
        assert await db.scalar(text("SELECT sum(karma) FROM agents")) == 0


async def test_release_before_submission_then_cancel_open(marketplace_api, claimed_review):
    client, (maker, _, _, _, keys) = marketplace_api
    assert (
        await client.post(claimed_review + "/unclaim", headers={"X-API-Key": keys[1]})
    ).status_code == 200
    snapshot = (await client.get(claimed_review + "/review", headers={"X-API-Key": keys[0]})).json()
    assert snapshot["version"] == 2 and snapshot["relationship"] == "unknown"
    response = await client.post(
        claimed_review + "/review",
        json={"action": "cancel", "expected_version": 2, "idempotency_key": str(uuid4())},
        headers={"X-API-Key": keys[0]},
    )
    assert response.status_code == 200 and response.json()["state"] == "cancelled"


@pytest.mark.parametrize("actor_case", [("submit", 0), ("review", 1)])
async def test_wrong_actor(marketplace_api, claimed_review, submission, decision, actor_case):
    endpoint, identity = actor_case
    client, (_, _, _, _, keys) = marketplace_api
    response = await client.post(
        claimed_review + "/" + endpoint,
        json=submission if endpoint == "submit" else decision,
        headers={"X-API-Key": keys[identity]},
    )
    assert response.status_code == 403


async def test_outsider_cannot_read_or_replay(
    marketplace_api, claimed_review, submission, outsider
):
    client, (_, _, _, _, keys) = marketplace_api
    assert (
        await client.post(
            claimed_review + "/submit", json=submission, headers={"X-API-Key": keys[1]}
        )
    ).status_code == 200
    assert (await client.get(claimed_review + "/review", headers=outsider)).status_code == 403
    assert (
        await client.post(claimed_review + "/submit", json=submission, headers=outsider)
    ).status_code == 403


@pytest.mark.parametrize("endpoint", ["submit", "review"])
async def test_stale_version_and_key_conflict(
    marketplace_api, claimed_review, submission, decision, endpoint
):
    client, (maker, _, _, _, keys) = marketplace_api
    worker, requester = {"X-API-Key": keys[1]}, {"X-API-Key": keys[0]}
    assert (
        await client.post(claimed_review + "/submit", json=submission, headers=worker)
    ).status_code == 200
    body, headers = (submission, worker) if endpoint == "submit" else (decision, requester)
    url = claimed_review + "/" + endpoint
    if endpoint == "review":
        assert (await client.post(url, json=body, headers=headers)).status_code == 200
    changed = {**body, "result" if endpoint == "submit" else "reason": "Changed payload"}
    assert (await client.post(url, json=changed, headers=headers)).status_code == 409
    stale = {**body, "expected_version": 0, "idempotency_key": str(uuid4())}
    assert (await client.post(url, json=stale, headers=headers)).status_code == 409
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM task_review_events")) == (
            3 if endpoint == "submit" else 4
        )


@pytest.mark.parametrize(
    "patch",
    [
        {"result": " "},
        {"result": "x" * 16001},
        {"expected_version": -1},
        {"idempotency_key": "bad"},
        {"unrecognized": True},
    ],
)
async def test_submit_validation(marketplace_api, claimed_review, submission, patch):
    client, (_, _, _, _, keys) = marketplace_api
    response = await client.post(
        claimed_review + "/submit", json={**submission, **patch}, headers={"X-API-Key": keys[1]}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("action", ["accept", "request_changes", "reject"])
async def test_decision_needs_submission(marketplace_api, reviewed_task, action):
    client, (_, _, _, _, keys) = marketplace_api
    response = await client.post(
        reviewed_task + "/review",
        json={"action": action, "expected_version": 0, "idempotency_key": str(uuid4())},
        headers={"X-API-Key": keys[0]},
    )
    assert response.status_code == 409


async def test_accept_cancel_race(
    marketplace_api, claimed_review, submission, decision, competing_requests
):
    client, (maker, _, _, _, keys) = marketplace_api
    assert (
        await client.post(
            claimed_review + "/submit", json=submission, headers={"X-API-Key": keys[1]}
        )
    ).status_code == 200
    headers = {"X-API-Key": keys[0]}
    requests = [
        (claimed_review + "/review", decision, headers),
        (
            claimed_review + "/review",
            {**decision, "action": "cancel", "idempotency_key": str(uuid4())},
            headers,
        ),
    ]
    results = await competing_requests(requests, "tasks", claimed_review.rsplit("/", 1)[1])
    assert sorted(result.status_code for result in results) == [200, 409]
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM task_review_events")) == 4
        assert await db.scalar(text("SELECT sum(karma) FROM agents")) == 0


async def test_submit_unclaim_race(marketplace_api, claimed_review, submission, competing_requests):
    _, (maker, _, _, _, keys) = marketplace_api
    headers = {"X-API-Key": keys[1]}
    requests = [
        (claimed_review + "/submit", submission, headers),
        (claimed_review + "/unclaim", None, headers),
    ]
    results = await competing_requests(requests, "tasks", claimed_review.rsplit("/", 1)[1])
    assert sorted(result.status_code for result in results) == [200, 409]
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM task_review_events")) == 3
        row = await db.execute(
            text(
                "SELECT t.status, r.state, r.version "
                "FROM tasks t JOIN task_reviews r ON r.task_id = t.id"
            )
        )
        status, state, version = row.one()
        assert (status, state) in {("claimed", "submitted"), ("open", "awaiting_submission")}
        assert version == 2


async def test_failed_event_rolls_back_acceptance(
    marketplace_api, claimed_review, submission, decision
):
    client, (maker, _, _, _, keys) = marketplace_api
    assert (
        await client.post(
            claimed_review + "/submit", json=submission, headers={"X-API-Key": keys[1]}
        )
    ).status_code == 200
    async with maker.begin() as db:
        await db.execute(
            text(
                "CREATE OR REPLACE FUNCTION fail_review_event() RETURNS trigger AS $$ "
                "BEGIN RAISE EXCEPTION 'Synthetic event failure'; END; $$ LANGUAGE plpgsql"
            )
        )
        await db.execute(
            text(
                "CREATE TRIGGER fail_review_event BEFORE INSERT ON task_review_events "
                "FOR EACH ROW EXECUTE FUNCTION fail_review_event()"
            )
        )
    with pytest.raises(DBAPIError, match="Synthetic event failure"):
        await client.post(claimed_review + "/review", json=decision, headers={"X-API-Key": keys[0]})
    async with maker() as db:
        assert await db.scalar(text("SELECT state FROM task_reviews")) == "submitted"
        assert await db.scalar(text("SELECT version FROM task_reviews")) == 2
        assert await db.scalar(text("SELECT status FROM tasks")) == "claimed"
        assert await db.scalar(text("SELECT count(*) FROM task_review_events")) == 3
