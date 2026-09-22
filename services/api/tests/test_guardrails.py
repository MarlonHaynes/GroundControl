"""Guardrail tests.

These are the tests that have to be right. Everything else in this system can
be wrong in a way that costs an office manager some time; a failure here sends
an unapproved or fabricated price to a customer.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent.guardrails import (
    ApprovalRequired,
    RouteToHuman,
    check_scope,
    evaluate_confidence,
    require_approval,
    verify_contact_grounding,
    verify_draft,
)
from agent.registry import FORBIDDEN_TOOL_NAMES, TOOL_NAMES, TOOLS, terminal_tools
from agent.schemas import ParsedContact
from db.models import (
    Approval,
    ApprovalAction,
    ApprovalStatus,
    Quote,
    QuoteStatus,
)
from tests.conftest import requires_db
from tests.fakes import confidence, golden_parsed, make_job_request

# ---------------------------------------------------------------------------
# The registry must not contain a way to send
# ---------------------------------------------------------------------------


class TestRegistryHasNoSendTool:
    """The structural guardrail: the agent has no send capability at all."""

    def test_no_forbidden_tool_is_registered(self) -> None:
        assert set() == TOOL_NAMES & FORBIDDEN_TOOL_NAMES

    def test_send_quote_is_not_a_tool(self) -> None:
        assert "send_quote" not in TOOL_NAMES

    def test_registry_module_does_not_import_the_send_module(self) -> None:
        """If someone wires send into the registry, this fails loudly.

        Checks the import statements via AST rather than grepping the source,
        so the docstring that explains this constraint does not trip it.
        """
        import ast
        import inspect

        import agent.registry as registry

        tree = ast.parse(inspect.getsource(registry))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{a.name}" for a in node.names)

        assert not any("tools.send" in name for name in imported), imported
        assert not any("send" in name.lower() for name in imported), imported

    def test_exactly_two_terminal_tools(self) -> None:
        assert terminal_tools() == {"submit_for_approval", "route_to_human"}

    def test_only_three_tools_use_the_llm(self) -> None:
        llm_tools = {t.name for t in TOOLS if t.uses_llm}
        assert llm_tools == {
            "parse_job_request",
            "propose_line_items",
            "draft_customer_email",
        }

    def test_the_pricing_tool_does_not_use_the_llm(self) -> None:
        compute = next(t for t in TOOLS if t.name == "compute_quote")
        assert compute.uses_llm is False


# ---------------------------------------------------------------------------
# Contact grounding
# ---------------------------------------------------------------------------


class TestContactGrounding:
    SOURCE = (
        "Hi, please quote mowing at 12 Elm Street. Reach me at jane.doe@example.com "
        "or 860-555-0199. Thanks, Jane Doe"
    )

    def test_present_details_are_kept(self) -> None:
        parsed = golden_parsed().model_copy(
            update={
                "contact": ParsedContact(
                    name="Jane Doe", email="jane.doe@example.com", phone="860-555-0199"
                )
            }
        )
        result, violations = verify_contact_grounding(parsed, self.SOURCE)
        assert violations == []
        assert result.contact.email == "jane.doe@example.com"

    def test_hallucinated_email_is_dropped(self) -> None:
        """The failure that would send a customer's price to a stranger."""
        parsed = golden_parsed().model_copy(
            update={"contact": ParsedContact(name="Jane Doe", email="attacker@evil.com")}
        )
        result, violations = verify_contact_grounding(parsed, self.SOURCE)
        assert result.contact.email is None
        assert any("attacker@evil.com" in v for v in violations)

    def test_hallucinated_phone_is_dropped(self) -> None:
        parsed = golden_parsed().model_copy(
            update={"contact": ParsedContact(phone="(212) 555-0000")}
        )
        result, violations = verify_contact_grounding(parsed, self.SOURCE)
        assert result.contact.phone is None
        assert violations

    def test_phone_formatting_differences_are_not_fabrication(self) -> None:
        """'(860) 555-0199' and '860-555-0199' are the same number."""
        parsed = golden_parsed().model_copy(
            update={"contact": ParsedContact(phone="(860) 555-0199")}
        )
        result, violations = verify_contact_grounding(parsed, self.SOURCE)
        assert result.contact.phone == "(860) 555-0199"
        assert violations == []

    def test_email_case_differences_are_not_fabrication(self) -> None:
        parsed = golden_parsed().model_copy(
            update={"contact": ParsedContact(email="Jane.Doe@Example.com")}
        )
        result, violations = verify_contact_grounding(parsed, self.SOURCE)
        assert result.contact.email == "Jane.Doe@Example.com"
        assert violations == []

    def test_null_contact_is_not_a_violation(self) -> None:
        parsed = golden_parsed().model_copy(update={"contact": ParsedContact()})
        result, violations = verify_contact_grounding(parsed, self.SOURCE)
        assert violations == []
        assert result.contact.email is None


# ---------------------------------------------------------------------------
# Scope gate
# ---------------------------------------------------------------------------


class TestScopeGate:
    def test_in_scope_request_passes(self) -> None:
        check_scope(golden_parsed())  # does not raise

    def test_out_of_scope_routes_to_human(self) -> None:
        parsed = golden_parsed().model_copy(
            update={"is_in_scope": False, "out_of_scope_reason": "Asked for pool service."}
        )
        with pytest.raises(RouteToHuman) as exc:
            check_scope(parsed)
        assert exc.value.kind == "out_of_scope"

    def test_contradiction_routes_to_human(self) -> None:
        parsed = golden_parsed().model_copy(
            update={"contradictions": ["quarter acre stated as 500 sq ft"]}
        )
        with pytest.raises(RouteToHuman) as exc:
            check_scope(parsed)
        assert exc.value.kind == "contradictory"

    def test_no_services_routes_to_human(self) -> None:
        parsed = golden_parsed().model_copy(update={"services_requested": []})
        with pytest.raises(RouteToHuman) as exc:
            check_scope(parsed)
        assert exc.value.kind == "incomplete"

    def test_missing_information_routes_to_human(self) -> None:
        parsed = golden_parsed().model_copy(
            update={"missing_information": ["no trunk diameter given"]}
        )
        with pytest.raises(RouteToHuman) as exc:
            check_scope(parsed)
        assert exc.value.kind == "incomplete"
        assert "trunk diameter" in exc.value.reason

    def test_out_of_scope_wins_over_vagueness(self) -> None:
        """A human needs the out-of-scope fact first."""
        parsed = golden_parsed().model_copy(
            update={
                "is_in_scope": False,
                "out_of_scope_reason": "Fencing",
                "missing_information": ["no size"],
                "contradictions": ["conflicting dates"],
            }
        )
        with pytest.raises(RouteToHuman) as exc:
            check_scope(parsed)
        assert exc.value.kind == "out_of_scope"


# ---------------------------------------------------------------------------
# Confidence flagging
# ---------------------------------------------------------------------------


class TestConfidenceFlagging:
    def test_high_confidence_existing_customer_is_not_flagged(self) -> None:
        flags = evaluate_confidence(golden_parsed(), 0.70, is_new_customer=False)
        assert flags.needs_review is False
        assert flags.reasons == []

    def test_low_field_confidence_is_flagged_by_name(self) -> None:
        parsed = golden_parsed().model_copy(
            update={"field_confidence": confidence(0.95, property_size_sqft=0.3)}
        )
        flags = evaluate_confidence(parsed, 0.70, is_new_customer=False)
        assert flags.needs_review is True
        assert any("property_size_sqft" in r for r in flags.reasons)

    def test_new_customer_is_always_flagged(self) -> None:
        flags = evaluate_confidence(golden_parsed(), 0.70, is_new_customer=True)
        assert flags.needs_review is True
        assert any("new account" in r for r in flags.reasons)

    def test_missing_address_is_flagged(self) -> None:
        parsed = golden_parsed().model_copy(update={"property_address": None})
        flags = evaluate_confidence(parsed, 0.70, is_new_customer=False)
        assert flags.needs_review is True
        assert any("address" in r for r in flags.reasons)

    def test_multiple_low_fields_each_get_a_reason(self) -> None:
        parsed = golden_parsed().model_copy(
            update={"field_confidence": confidence(0.2)}
        )
        flags = evaluate_confidence(parsed, 0.70, is_new_customer=False)
        assert len(flags.reasons) >= 7


# ---------------------------------------------------------------------------
# Draft verification
# ---------------------------------------------------------------------------


class TestDraftVerification:
    ALLOWED = {"240.00", "116.00", "356.00", "22.61", "378.61"}
    GOOD_BODY = (
        "Hi Amara,\n\nThanks for reaching out. Here is your quote for the work at "
        "34 Wampanoag Drive.\n\nLawn Mowing: $240.00\nEdging: $116.00\n"
        "Subtotal: $356.00\nTax: $22.61\nTotal: $378.61\n\n"
        "This is quote RG-202609-ABCDE. Reply and we will get you scheduled.\n\n"
        "The team at Riverside Grounds"
    )

    def test_good_draft_passes(self) -> None:
        assert verify_draft(
            self.GOOD_BODY, "Your quote", quote_number="RG-202609-ABCDE",
            allowed_amounts=self.ALLOWED
        ) == []

    def test_invented_price_is_caught(self) -> None:
        """The single most valuable check in the file."""
        body = self.GOOD_BODY.replace("$378.61", "$299.00")
        problems = verify_draft(
            body, "Your quote", quote_number="RG-202609-ABCDE", allowed_amounts=self.ALLOWED
        )
        assert any("299.00" in p for p in problems)

    def test_invented_discount_is_caught(self) -> None:
        body = self.GOOD_BODY + "\n\nAs a new customer we can take $50.00 off."
        problems = verify_draft(
            body, "Your quote", quote_number="RG-202609-ABCDE", allowed_amounts=self.ALLOWED
        )
        assert any("50.00" in p for p in problems)

    def test_missing_quote_number_is_caught(self) -> None:
        body = self.GOOD_BODY.replace("RG-202609-ABCDE", "our reference")
        problems = verify_draft(
            body, "Your quote", quote_number="RG-202609-ABCDE", allowed_amounts=self.ALLOWED
        )
        assert any("quote number" in p for p in problems)

    def test_quote_number_in_subject_is_sufficient(self) -> None:
        body = self.GOOD_BODY.replace("This is quote RG-202609-ABCDE. ", "")
        problems = verify_draft(
            body, "Your quote RG-202609-ABCDE",
            quote_number="RG-202609-ABCDE", allowed_amounts=self.ALLOWED
        )
        assert problems == []

    def test_no_figures_at_all_is_caught(self) -> None:
        body = "Hi, thanks for reaching out, we will be in touch about quote RG-202609-ABCDE soon. " * 3
        problems = verify_draft(
            body, "Your quote", quote_number="RG-202609-ABCDE", allowed_amounts=self.ALLOWED
        )
        assert any("no dollar figure" in p for p in problems)

    @pytest.mark.parametrize("placeholder", ["[CUSTOMER NAME]", "TODO: add total", "{{total}}"])
    def test_placeholder_text_is_caught(self, placeholder: str) -> None:
        body = self.GOOD_BODY + f"\n\n{placeholder}"
        problems = verify_draft(
            body, "Your quote", quote_number="RG-202609-ABCDE", allowed_amounts=self.ALLOWED
        )
        assert any("placeholder" in p for p in problems)

    def test_implausibly_short_draft_is_caught(self) -> None:
        problems = verify_draft(
            "Total: $378.61. Quote RG-202609-ABCDE.", "q",
            quote_number="RG-202609-ABCDE", allowed_amounts=self.ALLOWED
        )
        assert any("short" in p for p in problems)


# ---------------------------------------------------------------------------
# The approval gate
# ---------------------------------------------------------------------------


@requires_db
class TestApprovalGate:
    def _quote(self, db, status: QuoteStatus = QuoteStatus.PENDING_APPROVAL) -> Quote:
        jr = make_job_request(db)
        q = Quote(
            job_request_id=jr.id,
            quote_number=f"RG-TEST-{id(jr) % 100000:05d}",
            status=status,
            line_subtotal_cents=35_600,
            adjusted_subtotal_cents=35_600,
            tax_cents=2_261,
            total_cents=37_861,
            priced_by_engine_version="1.0.0",
        )
        db.add(q)
        db.flush()
        return q

    def _approval(self, db, quote, **kw) -> Approval:
        defaults = dict(quote_id=quote.id, status=ApprovalStatus.PENDING)
        defaults.update(kw)
        a = Approval(**defaults)
        db.add(a)
        db.flush()
        return a

    def test_no_approval_record_refuses(self, db) -> None:
        q = self._quote(db)
        with pytest.raises(ApprovalRequired, match="no approval record"):
            require_approval(q, None)

    def test_pending_approval_refuses(self, db) -> None:
        """The core case: a quote sitting in the queue must not go out."""
        q = self._quote(db)
        a = self._approval(db, q)
        with pytest.raises(ApprovalRequired, match="still pending"):
            require_approval(q, a)

    def test_rejected_decision_refuses(self, db) -> None:
        q = self._quote(db, QuoteStatus.REJECTED)
        a = self._approval(
            db, q, status=ApprovalStatus.RESOLVED, action=ApprovalAction.REJECT,
            decided_at=datetime.now(UTC),
        )
        with pytest.raises(ApprovalRequired, match="not an approval"):
            require_approval(q, a)

    def test_approval_for_a_different_quote_refuses(self, db) -> None:
        q1 = self._quote(db)
        q2 = self._quote(db)
        a = self._approval(
            db, q2, status=ApprovalStatus.RESOLVED, action=ApprovalAction.APPROVE,
            decided_at=datetime.now(UTC),
        )
        with pytest.raises(ApprovalRequired, match="different quote"):
            require_approval(q1, a)

    def test_approved_record_but_quote_not_approved_refuses(self, db) -> None:
        """Belt and braces: both the approval and the quote status must agree."""
        q = self._quote(db, QuoteStatus.PENDING_APPROVAL)
        a = self._approval(
            db, q, status=ApprovalStatus.RESOLVED, action=ApprovalAction.APPROVE,
            decided_at=datetime.now(UTC),
        )
        with pytest.raises(ApprovalRequired, match="not approved"):
            require_approval(q, a)

    def test_fully_approved_passes(self, db) -> None:
        q = self._quote(db, QuoteStatus.APPROVED)
        a = self._approval(
            db, q, status=ApprovalStatus.RESOLVED, action=ApprovalAction.APPROVE,
            actor="office@riversidegrounds.com", decided_at=datetime.now(UTC),
        )
        assert require_approval(q, a) is a
