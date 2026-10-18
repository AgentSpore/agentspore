"""Educational credits exercise actual authenticated HTTP and PostgreSQL effects."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_marketplace_tasks_api import competing_requests as competing_requests
from test_marketplace_tasks_api import marketplace_api as marketplace_api
from test_marketplace_tasks_api import marketplace_db as marketplace_db
from test_marketplace_tasks_api import marketplace_pg as marketplace_pg

from app.repositories.learning_budget_repo import LearningBudgetRepository
from app.services.agent_service import AgentService

BUDGET = "/api/v1/agents/learning-budget"


async def test_read_does_not_grant_and_post_grants_once(marketplace_api):
    client, (maker, _, _, _, keys) = marketplace_api
    headers = {"X-API-Key": keys[0]}
    empty = {"available": 0, "reserved": 0, "initial_grant_applied": False}
    assert (await client.get(BUDGET, headers=headers)).json() == empty
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM learning_budget_accounts")) == 0
    for _ in range(2):
        response = await client.post(BUDGET, headers=headers)
        assert response.status_code == 200
        assert response.json() == {**empty, "available": 100, "initial_grant_applied": True}


@pytest.mark.parametrize("amount", [True, 1.5, "30", 0, -1, 1000000001])
async def test_price_requires_strict_bounded_integer(marketplace_api, amount):
    client, (_, project, _, _, keys) = marketplace_api
    response = await client.post(
        f"/api/v1/agents/projects/{project}/tasks",
        json={
            "idempotency_key": str(uuid4()),
            "title": "Credits",
            "description": "Test",
            "review_required": True,
            "learning_credit_amount": amount,
        },
        headers={"X-API-Key": keys[0]},
    )
    assert response.status_code == 422


async def test_reservation_transfer_and_replays(marketplace_api):
    client, (_, project, _, _, keys) = marketplace_api
    requester, worker = ({"X-API-Key": key} for key in keys)
    assert (await client.post(BUDGET, headers=requester)).status_code == 200
    body = {
        "idempotency_key": str(uuid4()),
        "title": "Credits",
        "description": "Test",
        "review_required": True,
        "learning_credit_amount": 30,
    }
    created = await client.post(
        f"/api/v1/agents/projects/{project}/tasks", json=body, headers=requester
    )
    assert created.status_code == 200
    url = f"/api/v1/agents/tasks/{created.json()['task_id']}"
    assert (await client.get(BUDGET, headers=requester)).json()["reserved"] == 30
    assert (await client.post(url + "/claim", headers=worker)).status_code == 200
    submit = {"result": "Test", "expected_version": 1, "idempotency_key": str(uuid4())}
    assert (await client.post(url + "/submit", json=submit, headers=worker)).status_code == 200
    decision = {"action": "accept", "expected_version": 2, "idempotency_key": str(uuid4())}
    for _ in range(2):
        assert (
            await client.post(url + "/review", json=decision, headers=requester)
        ).status_code == 200
    assert (await client.get(BUDGET, headers=requester)).json() == {
        "available": 70,
        "reserved": 0,
        "initial_grant_applied": True,
    }
    assert (await client.get(BUDGET, headers=worker)).json() == {
        "available": 30,
        "reserved": 0,
        "initial_grant_applied": False,
    }
    assert (await client.post(BUDGET, headers=worker)).json()["available"] == 130
    assert (await client.post(BUDGET, headers=worker)).json()["available"] == 130


@pytest.fixture
async def funded_task(marketplace_api):
    client, (_, project, _, _, keys) = marketplace_api
    requester = {"X-API-Key": keys[0]}
    assert (await client.post(BUDGET, headers=requester)).status_code == 200
    body = {
        "idempotency_key": str(uuid4()),
        "title": "Credits",
        "description": "Test",
        "review_required": True,
        "learning_credit_amount": 30,
    }
    create_url = f"/api/v1/agents/projects/{project}/tasks"
    created = await client.post(create_url, json=body, headers=requester)
    assert created.status_code == 200
    return f"/api/v1/agents/tasks/{created.json()['task_id']}", create_url, body


@pytest.mark.parametrize("amount", [None, 31])
async def test_creation_replay_rejects_price_changes(marketplace_api, funded_task, amount):
    client, (maker, _, _, _, keys) = marketplace_api
    _, create_url, body = funded_task
    headers = {"X-API-Key": keys[0]}
    assert (await client.post(create_url, json=body, headers=headers)).json()["created"] is False
    changed = await client.post(
        create_url, json={**body, "learning_credit_amount": amount}, headers=headers
    )
    assert changed.status_code == 409
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM learning_budget_reservations")) == 1
    assert (await client.get(BUDGET, headers=headers)).json()["available"] == 70


@pytest.mark.parametrize("funded", [False, True])
async def test_insufficient_rolls_back_task_review_and_account(marketplace_api, funded):
    client, (maker, project, _, _, keys) = marketplace_api
    headers = {"X-API-Key": keys[0]}
    if funded:
        assert (await client.post(BUDGET, headers=headers)).status_code == 200
    response = await client.post(
        f"/api/v1/agents/projects/{project}/tasks",
        headers=headers,
        json={
            "idempotency_key": str(uuid4()),
            "title": "Credits",
            "description": "Test",
            "review_required": True,
            "learning_credit_amount": 101,
        },
    )
    assert response.status_code == 409
    async with maker() as db:
        for table in (
            "tasks",
            "task_reviews",
            "task_review_events",
            "learning_budget_reservations",
        ):
            assert await db.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        assert await db.scalar(text("SELECT count(*) FROM learning_budget_accounts")) == int(funded)


async def test_requires_review_and_legacy_prices_remain_absent(marketplace_api):
    client, (_, project, _, _, keys) = marketplace_api
    headers = {"X-API-Key": keys[0]}
    body = {"idempotency_key": str(uuid4()), "title": "Free", "description": "Test"}
    url = f"/api/v1/agents/projects/{project}/tasks"
    assert (
        await client.post(url, json={**body, "learning_credit_amount": 1}, headers=headers)
    ).status_code == 422
    assert (await client.post(url, json=body, headers=headers)).status_code == 200
    assert (
        await client.post(url, json={**body, "learning_credit_amount": None}, headers=headers)
    ).json()["created"] is False
    listing = (await client.get("/api/v1/agents/tasks")).json()
    assert listing[0]["learning_credit_amount"] is None


@pytest.mark.parametrize("action", ["reject", "cancel"])
async def test_refund_once_retention_and_detail(marketplace_api, funded_task, action):
    client, (_, _, _, _, keys) = marketplace_api
    url, _, _ = funded_task
    requester, worker = ({"X-API-Key": key} for key in keys)
    listing = (await client.get("/api/v1/agents/tasks")).json()
    assert listing[0]["learning_credit_amount"] == 30
    assert (await client.post(url + "/claim", headers=worker)).status_code == 200
    assert (await client.post(url + "/unclaim", headers=worker)).status_code == 200
    assert (await client.get(BUDGET, headers=requester)).json()["reserved"] == 30
    assert (await client.post(url + "/claim", headers=worker)).status_code == 200
    submit = {"result": "Test", "expected_version": 3, "idempotency_key": str(uuid4())}
    assert (await client.post(url + "/submit", json=submit, headers=worker)).status_code == 200
    changes = {"action": "request_changes", "expected_version": 4, "idempotency_key": str(uuid4())}
    assert (await client.post(url + "/review", json=changes, headers=requester)).status_code == 200
    assert (await client.get(BUDGET, headers=requester)).json()["reserved"] == 30
    submit.update(expected_version=5, idempotency_key=str(uuid4()))
    assert (await client.post(url + "/submit", json=submit, headers=worker)).status_code == 200
    decision = {"action": action, "expected_version": 6, "idempotency_key": str(uuid4())}
    for _ in range(2):
        assert (
            await client.post(url + "/review", json=decision, headers=requester)
        ).status_code == 200
    new_key = {**decision, "expected_version": 7, "idempotency_key": str(uuid4())}
    assert (await client.post(url + "/review", json=new_key, headers=requester)).status_code == 409
    assert (await client.get(BUDGET, headers=requester)).json()["available"] == 100
    assert (await client.get(BUDGET, headers=worker)).json()["available"] == 0
    detail = (await client.get(url + "/review", headers=worker)).json()
    assert detail["learning_reservation"] == {"amount": 30, "status": "refunded"}
    assert all("learning_reservation" not in event["response"] for event in detail["history"])


@pytest.mark.parametrize("method,expected", [("get", 422), ("post", 422)])
async def test_budget_authentication_required(marketplace_api, method, expected):
    client, _ = marketplace_api
    assert (await getattr(client, method)(BUDGET)).status_code == expected
    assert (
        await getattr(client, method)(BUDGET, headers={"X-API-Key": str(uuid4())})
    ).status_code == 401


async def test_other_principal_cannot_settle_or_view(marketplace_api, funded_task):
    client, (maker, _, _, _, keys) = marketplace_api
    url, _, _ = funded_task
    third, key = uuid4(), str(uuid4())
    async with maker.begin() as db:
        await db.execute(
            text("INSERT INTO agents(id, name, api_key_hash) VALUES (:id, 'Other', :hash)"),
            {"id": third, "hash": AgentService.hash_api_key(key)},
        )
    outsider = {"X-API-Key": key}
    assert (await client.get(url + "/review", headers=outsider)).status_code == 403
    response = await client.post(
        url + "/review",
        headers=outsider,
        json={"action": "cancel", "expected_version": 0, "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 403
    assert (await client.get(BUDGET, headers=outsider)).json()["available"] == 0
    assert (await client.get(BUDGET, headers={"X-API-Key": keys[0]})).json()["reserved"] == 30


async def test_parallel_grant_is_once(marketplace_api, competing_requests):
    client, (maker, _, owner, _, keys) = marketplace_api
    headers = {"X-API-Key": keys[0]}
    responses = await competing_requests([(BUDGET, {}, headers)] * 2, "agents", owner)
    assert all(response.json()["available"] == 100 for response in responses)
    async with maker() as db:
        assert await db.scalar(text("SELECT sum(available) FROM learning_budget_accounts")) == 100


async def test_parallel_reserves_cannot_overspend(marketplace_api, funded_task):
    client, (maker, project, owner, _, keys) = marketplace_api
    _, create_url, body = funded_task
    second = uuid4()
    async with maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO projects(id, title, creator_agent_id) VALUES (:id, 'Second', :owner)"
            ),
            {"id": second, "owner": owner},
        )
    headers = {"X-API-Key": keys[0]}
    async with asyncio.timeout(5), asyncio.TaskGroup() as group:
        jobs = [
            group.create_task(
                client.post(
                    url,
                    headers=headers,
                    json={**body, "learning_credit_amount": 50, "idempotency_key": str(uuid4())},
                )
            )
            for url in (create_url, f"/api/v1/agents/projects/{second}/tasks")
        ]
    assert sorted(job.result().status_code for job in jobs) == [200, 409]
    assert (await client.get(BUDGET, headers=headers)).json() == {
        "available": 20,
        "reserved": 80,
        "initial_grant_applied": True,
    }
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM tasks")) == 2


@pytest.fixture
async def submitted_paid(marketplace_api, funded_task):
    client, (_, _, _, _, keys) = marketplace_api
    url, _, _ = funded_task
    worker = {"X-API-Key": keys[1]}
    assert (await client.post(url + "/claim", headers=worker)).status_code == 200
    assert (
        await client.post(
            url + "/submit",
            headers=worker,
            json={"result": "Test", "expected_version": 1, "idempotency_key": str(uuid4())},
        )
    ).status_code == 200
    return url


@pytest.mark.parametrize("action", ["accept", "reject"])
async def test_competing_decisions_settle_once(
    marketplace_api, submitted_paid, competing_requests, action
):
    client, (_, _, _, _, keys) = marketplace_api
    body = {"action": action, "expected_version": 2, "idempotency_key": str(uuid4())}
    request = (submitted_paid + "/review", body, {"X-API-Key": keys[0]})
    task_id = submitted_paid.rsplit("/", 1)[1]
    responses = await competing_requests([request, request], "tasks", task_id)
    assert [response.status_code for response in responses] == [200, 200]
    assert (await client.get(BUDGET, headers=request[2])).json()["reserved"] == 0
    assert (await client.get(BUDGET, headers={"X-API-Key": keys[1]})).json()["available"] == (
        30 if action == "accept" else 0
    )


async def test_receipt_failure_rolls_back_settlement(marketplace_api, submitted_paid):
    client, (maker, _, _, _, keys) = marketplace_api
    async with maker.begin() as db:
        await db.execute(
            text(
                "CREATE FUNCTION fail_paid_receipt() RETURNS TRIGGER AS $$ "
                "BEGIN IF NEW.action = 'accept' THEN RAISE EXCEPTION 'Synthetic receipt failure'; "
                "END IF; RETURN NEW; END; $$ LANGUAGE plpgsql"
            )
        )
        await db.execute(
            text(
                "CREATE TRIGGER fail_paid_receipt BEFORE INSERT ON task_review_events "
                "FOR EACH ROW EXECUTE FUNCTION fail_paid_receipt()"
            )
        )
    try:
        with pytest.raises(DBAPIError):
            await client.post(
                submitted_paid + "/review",
                headers={"X-API-Key": keys[0]},
                json={"action": "accept", "expected_version": 2, "idempotency_key": str(uuid4())},
            )
        async with maker() as db:
            assert await db.scalar(text("SELECT state FROM task_reviews")) == "submitted"
            assert (
                await db.scalar(text("SELECT status FROM learning_budget_reservations"))
                == "reserved"
            )
            assert (
                await db.scalar(text("SELECT sum(available) FROM learning_budget_accounts")) == 70
            )
            assert await db.scalar(text("SELECT sum(reserved) FROM learning_budget_accounts")) == 30
            assert await db.scalar(text("SELECT count(*) FROM learning_budget_accounts")) == 1
    finally:
        async with maker.begin() as db:
            await db.execute(text("DROP FUNCTION fail_paid_receipt() CASCADE"))


@pytest.mark.parametrize(
    "query",
    [
        "UPDATE learning_budget_accounts SET available = -1",
        "UPDATE learning_budget_accounts SET reserved = -1",
        "UPDATE learning_budget_reservations SET amount = 31",
        "DELETE FROM tasks",
        "DELETE FROM learning_budget_accounts",
        "DELETE FROM agents",
    ],
)
async def test_database_guards_preserve_accounting(marketplace_api, funded_task, query):
    _, (maker, _, _, _, _) = marketplace_api
    with pytest.raises(DBAPIError):
        async with maker.begin() as db:
            await db.execute(text(query))
    async with maker() as db:
        assert await db.scalar(text("SELECT amount FROM learning_budget_reservations")) == 30
        assert await db.scalar(text("SELECT reserved FROM learning_budget_accounts")) == 30


async def test_cross_agent_accepts_use_consistent_account_order(marketplace_api, submitted_paid):
    client, (maker, _, owner, other, keys) = marketplace_api
    first = submitted_paid
    second_project = uuid4()
    async with maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO projects(id, title, creator_agent_id) VALUES (:id, 'Reverse', :owner)"
            ),
            {"id": second_project, "owner": other},
        )
    first_headers, second_headers = ({"X-API-Key": key} for key in keys)
    assert (await client.post(BUDGET, headers=second_headers)).status_code == 200
    created = await client.post(
        f"/api/v1/agents/projects/{second_project}/tasks",
        headers=second_headers,
        json={
            "idempotency_key": str(uuid4()),
            "title": "Reverse",
            "description": "Test",
            "review_required": True,
            "learning_credit_amount": 20,
        },
    )
    second = f"/api/v1/agents/tasks/{created.json()['task_id']}"
    assert (await client.post(second + "/claim", headers=first_headers)).status_code == 200
    assert (
        await client.post(
            second + "/submit",
            headers=first_headers,
            json={"result": "Reverse", "expected_version": 1, "idempotency_key": str(uuid4())},
        )
    ).status_code == 200
    async with asyncio.timeout(5), asyncio.TaskGroup() as group:
        jobs = [
            group.create_task(
                client.post(
                    url + "/review",
                    headers=headers,
                    json={
                        "action": "accept",
                        "expected_version": 2,
                        "idempotency_key": str(uuid4()),
                    },
                )
            )
            for url, headers in ((first, first_headers), (second, second_headers))
        ]
    assert [job.result().status_code for job in jobs] == [200, 200]
    assert (await client.get(BUDGET, headers=first_headers)).json()["available"] == 90
    assert (await client.get(BUDGET, headers=second_headers)).json()["available"] == 110
    async with maker() as db:
        assert await db.scalar(text("SELECT sum(reserved) FROM learning_budget_accounts")) == 0
        assert await db.scalar(text("SELECT sum(karma) FROM agents")) == 0


@pytest.fixture
async def account_pair(marketplace_api):
    _, (maker, _, owner, other, _) = marketplace_api
    barrier = asyncio.Barrier(2)

    async def acquire(ids):
        async with maker.begin() as db:
            repo = LearningBudgetRepository(db)
            original = db.execute
            count = 0

            async def execute(statement, params=None, **kwargs):
                nonlocal count
                if "INSERT INTO learning_budget_accounts" in str(statement):
                    count += 1
                    if count == 1:
                        await barrier.wait()
                result = await original(statement, params, **kwargs)
                if count == 1:
                    await asyncio.sleep(0.05)
                return result

            db.execute = execute
            return await repo.lock_accounts(ids)

    return acquire, [owner, other]


async def test_first_account_creation_opposite_order_cannot_deadlock(account_pair):
    acquire, ids = account_pair
    async with asyncio.timeout(5), asyncio.TaskGroup() as group:
        jobs = [group.create_task(acquire(order)) for order in (ids, list(reversed(ids)))]
    assert all(set(job.result()) == {str(agent_id) for agent_id in ids} for job in jobs)
    assert all(
        row["available"] == 0 and not row["initial_grant_applied"]
        for row in jobs[0].result().values()
    )


async def test_cancel_before_claim_refunds(marketplace_api, funded_task):
    client, (_, _, _, _, keys) = marketplace_api
    url, _, _ = funded_task
    headers = {"X-API-Key": keys[0]}
    response = await client.post(
        url + "/review",
        headers=headers,
        json={"action": "cancel", "expected_version": 0, "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 200
    assert (await client.get(BUDGET, headers=headers)).json()["available"] == 100


@pytest.mark.parametrize("amount", [0, -1, 1000000001])
async def test_database_price_bounds(marketplace_api, funded_task, amount):
    _, (maker, _, _, _, _) = marketplace_api
    with pytest.raises(DBAPIError):
        async with maker.begin() as db:
            await db.execute(text("DELETE FROM learning_budget_reservations"))
            await db.execute(
                text(
                    "INSERT INTO learning_budget_reservations "
                    "(task_id, requester_agent_id, amount) "
                    "SELECT task_id, requester_agent_id, :amount "
                    "FROM task_reviews"
                ),
                {"amount": amount},
            )


async def test_paid_project_deletion_returns_conflict(marketplace_api, funded_task):
    client, (maker, project, _, _, keys) = marketplace_api
    async with maker.begin() as db:
        await db.execute(
            text(
                "ALTER TABLE projects ADD COLUMN repo_url TEXT, "
                "ADD COLUMN vcs_provider TEXT DEFAULT 'github'"
            )
        )
    response = await client.delete(
        f"/api/v1/agents/projects/{project}", headers={"X-API-Key": keys[0]}
    )
    assert response.status_code == 409
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM projects")) == 1
