"""API contract tests.

Runs against the real FastAPI app with a transactional database session
injected, so these exercise the same code paths the frontend hits.

The test that matters most is `test_direct_send_endpoint_always_refuses`: an
HTTP caller who goes looking for a send endpoint finds one that says no.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent.loop import run_pipeline
from app.main import app
from db.models import QuoteStatus, SentMessage
from db.session import get_db
from tests.conftest import requires_db
from tests.fakes import golden_client, make_customer, make_job_request

pytestmark = requires_db


@pytest.fixture
def client(db):
    """TestClient bound to the test transaction, so nothing is committed."""
    # The routers call db.commit(); inside the outer transaction that is a
    # savepoint release, and the conftest fixture still rolls everything back.
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def queued(db):
    from tests.test_pipeline import _seed_catalog

    _seed_catalog(db)
    make_customer(db)
    jr = make_job_request(db)
    return run_pipeline(db, job_request=jr, client=golden_client())


class TestHealth:
    def test_health(self, client) -> None:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestJobRequests:
    def test_list(self, client, queued) -> None:
        r = client.get("/api/job-requests")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["quote_number"] == queued.quote.quote_number
        assert body[0]["quote_total"]["display"] == "$378.61"

    def test_detail_includes_raw_text_and_confidence(self, client, queued) -> None:
        r = client.get(f"/api/job-requests/{queued.job_request.id}")
        assert r.status_code == 200
        body = r.json()
        assert "Wampanoag" in body["raw_source_text"]
        assert body["field_confidence"]["services_requested"] > 0

    def test_unknown_id_is_404(self, client) -> None:
        import uuid

        assert client.get(f"/api/job-requests/{uuid.uuid4()}").status_code == 404

    def test_filter_by_needs_review(self, client, queued) -> None:
        r = client.get("/api/job-requests", params={"needs_review": "true"})
        assert r.status_code == 200
        assert r.json() == []  # golden path is a clean, matched, high-confidence run


class TestApprovalQueue:
    def test_queue_returns_everything_a_reviewer_needs(self, client, queued) -> None:
        r = client.get("/api/approvals")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 1

        item = items[0]
        assert item["quote"]["status"] == "pending_approval"
        assert len(item["quote"]["line_items"]) == 2
        assert item["quote"]["draft"]["body"]
        assert item["job_request"]["raw_source_text"]
        assert item["customer"]["name"] == "Amara Osei"

    def test_money_is_preformatted(self, client, queued) -> None:
        item = client.get("/api/approvals").json()[0]
        assert item["quote"]["total"] == {"cents": 37861, "display": "$378.61"}
        assert item["quote"]["line_items"][0]["subtotal"]["display"] == "$240.00"


class TestApprovalActions:
    def test_approve_sends(self, client, db, queued) -> None:
        r = client.post(
            f"/api/quotes/{queued.quote.id}/approve",
            json={"actor": "office@riversidegrounds.com", "notes": "Looks right."},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "sent"
        assert body["adapter"] == "mock"
        assert body["to_email"] == "amara.osei@gmail.com"
        assert db.query(SentMessage).count() == 1

    def test_approving_twice_is_a_conflict(self, client, queued) -> None:
        client.post(f"/api/quotes/{queued.quote.id}/approve", json={"actor": "a"})
        r = client.post(f"/api/quotes/{queued.quote.id}/approve", json={"actor": "a"})
        assert r.status_code == 409

    def test_reject_does_not_send(self, client, db, queued) -> None:
        r = client.post(
            f"/api/quotes/{queued.quote.id}/reject",
            json={"actor": "office@riversidegrounds.com", "notes": "Duplicate request."},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"
        assert db.query(SentMessage).count() == 0

    def test_edit_reprices_and_stays_pending(self, client, queued) -> None:
        r = client.post(
            f"/api/quotes/{queued.quote.id}/edit",
            json={
                "line_items": [{"catalog_code": "MOW_STD", "quantity": "12000"}],
                "actor": "office@riversidegrounds.com",
                "notes": "Re-measured.",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "pending_approval"
        assert body["total"] == {"cents": 15314, "display": "$153.14"}
        assert body["line_items"][0]["source"] == "human_edited"

    def test_edit_that_cannot_be_priced_is_409(self, client, queued) -> None:
        r = client.post(
            f"/api/quotes/{queued.quote.id}/edit",
            json={
                "line_items": [{"catalog_code": "TREE_REMOVAL", "quantity": "1"}],
                "actor": "office@riversidegrounds.com",
            },
        )
        assert r.status_code == 409
        assert "could not be priced" in r.json()["detail"]

    def test_edit_rejects_an_empty_line_item_list(self, client, queued) -> None:
        r = client.post(
            f"/api/quotes/{queued.quote.id}/edit",
            json={"line_items": [], "actor": "a"},
        )
        assert r.status_code == 422  # schema-level: min_length=1


class TestSendGateOverHttp:
    def test_direct_send_endpoint_always_refuses(self, client, db, queued) -> None:
        """There is no unapproved send path, and the API says so explicitly."""
        r = client.post(f"/api/quotes/{queued.quote.id}/send")
        assert r.status_code == 403
        assert "no unapproved send path" in r.json()["detail"]
        assert db.query(SentMessage).count() == 0

    def test_a_queued_quote_is_never_sent_by_reading_it(self, client, db, queued) -> None:
        client.get("/api/approvals")
        client.get(f"/api/quotes/{queued.quote.id}")
        assert db.query(SentMessage).count() == 0
        assert queued.quote.status is QuoteStatus.PENDING_APPROVAL


class TestObservability:
    def test_runs_list_and_detail(self, client, queued) -> None:
        runs = client.get("/api/runs").json()
        assert len(runs) == 1
        assert runs[0]["n_steps"] == 8
        assert runs[0]["total_cost_microcents"] > 0

        detail = client.get(f"/api/runs/{runs[0]['id']}").json()
        assert [s["tool_name"] for s in detail["steps"]][0] == "parse_job_request"
        assert detail["steps"][0]["uses_llm"] is True
        assert detail["steps"][0]["cost_display"].startswith("$")

    def test_stats(self, client, queued) -> None:
        s = client.get("/api/stats").json()
        assert s["total_job_requests"] == 1
        assert s["pending_approval"] == 1
        assert s["sent"] == 0
        assert s["total_spend_display"].startswith("$")


class TestReference:
    def test_catalog(self, client, queued) -> None:
        body = client.get("/api/catalog").json()
        assert len(body["services"]) == 18
        codes = {s["code"] for s in body["services"]}
        assert {"MOW_STD", "TREE_REMOVAL"} <= codes

    def test_customers(self, client, queued) -> None:
        body = client.get("/api/customers").json()
        assert body[0]["name"] == "Amara Osei"
        assert body[0]["addresses"][0]["line1"] == "34 Wampanoag Drive"

    def test_latest_eval_404s_when_none_recorded(self, client) -> None:
        r = client.get("/api/evals/latest")
        assert r.status_code == 404
        assert "make eval-full" in r.json()["detail"]


class TestOpenAPI:
    def test_spec_is_generated(self, client) -> None:
        spec = client.get("/openapi.json").json()
        assert spec["info"]["title"] == "GroundControl API"
        assert "/api/approvals" in spec["paths"]

    def test_every_route_declares_a_response_model(self, client) -> None:
        """Keeps generated TS types meaningful rather than `unknown`."""
        spec = client.get("/openapi.json").json()
        missing = []
        for path, ops in spec["paths"].items():
            for method, op in ops.items():
                ok = op.get("responses", {}).get("200") or op.get("responses", {}).get("201")
                if ok and "content" not in ok and path != "/api/quotes/{quote_id}/send":
                    missing.append(f"{method.upper()} {path}")
        assert missing == []
