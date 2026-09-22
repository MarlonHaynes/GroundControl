"""Generate the labeled dataset, label-first.

Two stages, and the order is the whole point:

1. **Author the truth.** `build_ground_truths()` is pure and seeded: it picks a
   customer, services, quantities, and site conditions, then asks the *real*
   pricing engine for the expected total. No LLM involved, so the labels cannot
   be wrong.
2. **Render the mess.** For each record, the LLM is asked to write the email a
   human would actually have sent — with typos, vague sizing, rambling, a
   forwarded thread wrapper, or whatever mess profile was sampled.

Generating in the other direction (write emails, then label them) would make
label quality a function of the model's annotation accuracy, and the extraction
metric would be measuring the labeller as much as the pipeline.

Usage:
    python -m scripts.generate_dataset --count 180
    python -m scripts.generate_dataset --count 20 --dry-run   # truths only, no API calls
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent.llm import LLMClient, build_llm_client
from evals.dataset import (
    CASES_PATH,
    CATALOG_PATH,
    CUSTOMERS_PATH,
    EvalCase,
    FixtureCustomer,
    GroundTruth,
    GroundTruthService,
)
from observability.cost import Usage, cost_microcents, fmt_microcents
from pricing.catalog_data import CATALOG, CATALOG_BY_CODE
from pricing.engine import compute_quote
from pricing.types import (
    AccessDifficulty,
    PricingContext,
    PricingError,
    ProposedLineItem,
    Season,
    TravelZone,
    TrunkDiameterBand,
    Unit,
    Urgency,
)
from scripts.customers_data import CUSTOMERS

SEED = 20260922

# Service bundles that co-occur in a real inbox. A landscaping customer asks
# for "mow and edge", not for two independent services sampled uniformly.
BUNDLES: list[tuple[str, ...]] = [
    ("MOW_STD",),
    ("MOW_STD", "EDGE_TRIM"),
    ("MOW_STD", "EDGE_TRIM", "HEDGE_TRIM"),
    ("MOW_ROUGH",),
    ("MOW_ROUGH", "DEBRIS_HAUL"),
    ("LEAF_REMOVAL",),
    ("LEAF_REMOVAL", "DEBRIS_HAUL"),
    ("LEAF_REMOVAL", "GUTTER_CLEAN"),
    ("PROPERTY_CLEANUP",),
    ("PROPERTY_CLEANUP", "MULCH_INSTALL"),
    ("PROPERTY_CLEANUP", "DEBRIS_HAUL"),
    ("TREE_TRIM",),
    ("TREE_TRIM", "DEBRIS_HAUL"),
    ("TREE_REMOVAL",),
    ("TREE_REMOVAL", "STUMP_GRIND"),
    ("TREE_REMOVAL", "STUMP_GRIND", "DEBRIS_HAUL"),
    ("STUMP_GRIND",),
    ("FERT_APP",),
    ("FERT_APP", "WEED_CTRL"),
    ("WEED_CTRL",),
    ("MULCH_INSTALL",),
    ("SOD_INSTALL",),
    ("SOD_INSTALL", "MULCH_INSTALL"),
    ("IRRIGATION_REPAIR",),
    ("IRRIGATION_WINTERIZE",),
    ("SNOW_REMOVAL",),
    ("GUTTER_CLEAN",),
    ("HEDGE_TRIM",),
    ("HEDGE_TRIM", "MULCH_INSTALL"),
    ("EDGE_TRIM", "WEED_CTRL"),
]

# Services whose quantity IS the property's lawn area. SOD_INSTALL is per_sqft
# too, but its quantity is the size of a patch being re-turfed, which says
# nothing about the property. Labeling a property size from a sod job produced
# ground truth no email could support.
PROPERTY_SIZE_SERVICES = frozenset(
    {"MOW_STD", "MOW_ROUGH", "LEAF_REMOVAL", "FERT_APP", "WEED_CTRL"}
)

MESS_PROFILES: list[tuple[str, int]] = [
    ("plain", 14),
    ("typos", 16),
    ("vague_size", 18),
    ("rambling", 14),
    ("forwarded_thread", 10),
    ("terse_mobile", 12),
    ("all_caps", 4),
    ("partial_info", 12),
]

SPECIAL_REQUESTS = [
    "please don't let the crew use the side gate, the dog gets out",
    "we need this done before the family reunion on the 14th",
    "avoid the flower bed along the fence, those are my mother's roses",
    "please text before arriving, I work from home and take calls",
    "the back gate code is 4417",
    "bill the management company, not me directly",
    "no work on Sundays please",
    "watch out for the sprinkler heads near the driveway",
    "please haul everything away, last company left piles",
    "we have bees in the shed corner, give it a wide berth",
    "kids' playset in the yard, please don't move it",
    "the neighbor is particular about the property line",
    "invoice needs a PO number, I'll send it over",
    "please use the parking lot entrance off Albany Ave",
]


class RenderedEmail(BaseModel):
    """What the LLM writes for a given ground truth."""

    model_config = ConfigDict(extra="forbid")

    subject: str
    body: str


# ---------------------------------------------------------------------------
# Stage 1 — author the truth (pure, seeded, no LLM)
# ---------------------------------------------------------------------------


def _quantity_for(code: str, rng: random.Random, property_sqft: int) -> float:
    item = CATALOG_BY_CODE[code]
    if item.unit is Unit.FLAT:
        return 1.0
    if item.unit is Unit.PER_SQFT:
        if code in {"SOD_INSTALL"}:
            return float(rng.randrange(400, 4_000, 100))
        if code in {"LEAF_REMOVAL", "FERT_APP", "WEED_CTRL"}:
            return float(property_sqft)
        return float(property_sqft)
    if item.unit is Unit.PER_HOUR:
        return float(rng.choice([2, 3, 4, 5, 6, 8]))
    # per_unit — the unit means something different for each service
    return float(
        {
            "HEDGE_TRIM": rng.randint(4, 22),
            "TREE_TRIM": rng.randint(1, 4),
            "TREE_REMOVAL": rng.randint(1, 3),
            "STUMP_GRIND": rng.randint(1, 3),
            "DEBRIS_HAUL": rng.randint(2, 10),
            "MULCH_INSTALL": rng.randint(2, 12),
            "GUTTER_CLEAN": rng.randrange(80, 320, 20),
        }.get(code, rng.randint(1, 5))
    )


def _weighted_choice(rng: random.Random, options: list[tuple[str, int]]) -> str:
    population = [name for name, _ in options]
    weights = [w for _, w in options]
    return rng.choices(population, weights=weights, k=1)[0]


def _new_customer_identity(rng: random.Random, idx: int) -> dict[str, str | None]:
    first = rng.choice(
        ["Tessa", "Miguel", "Priyanka", "Dev", "Colleen", "Ravi", "Noor", "Brett",
         "Yusuf", "Lena", "Oscar", "Hana", "Declan", "Nadia", "Kwame", "Sofia",
         "Ivan", "Maeve", "Tariq", "Jodie"]
    )
    last = rng.choice(
        ["Abernathy", "Quintero", "Vasquez", "Lindqvist", "Beaumont", "Achebe",
         "Ferris", "Nowicki", "Halloran", "Castellanos", "Bergstrom", "Adeyemi",
         "Trombley", "Sandoval", "McCaffrey", "Rasmussen", "Duplessis", "Okafor"]
    )
    domain = rng.choice(["gmail.com", "yahoo.com", "outlook.com", "comcast.net", "icloud.com"])
    return {
        "customer_name": f"{first} {last}",
        "contact_name": f"{first} {last}",
        "customer_email": f"{first.lower()}.{last.lower()}{idx}@{domain}",
        "customer_phone": f"(860) 555-{rng.randint(1000, 9999):04d}",
    }


NEW_ADDRESSES = [
    ("29 Quarry Road", "Canton", "06019", TravelZone.ZONE_2),
    ("415 Meadowbrook Drive", "Glastonbury", "06033", TravelZone.ZONE_2),
    ("7 Lantern Hill Way", "Burlington", "06013", TravelZone.ZONE_3),
    ("1163 Boulevard", "West Hartford", "06119", TravelZone.ZONE_1),
    ("88 Stagecoach Road", "Avon", "06001", TravelZone.ZONE_2),
    ("342 Buckingham Street", "Hartford", "06106", TravelZone.ZONE_1),
    ("56 Tolland Turnpike", "Manchester", "06042", TravelZone.ZONE_3),
    ("904 Cottage Grove Road", "Bloomfield", "06002", TravelZone.ZONE_2),
    ("21 Waterside Lane", "Rocky Hill", "06067", TravelZone.ZONE_3),
    ("178 Hayes Road", "South Windsor", "06074", TravelZone.ZONE_3),
]


def build_ground_truths(count: int, seed: int = SEED) -> list[GroundTruth]:
    rng = random.Random(seed)
    customers: list[FixtureCustomer] = CUSTOMERS
    truths: list[GroundTruth] = []

    idx = 0
    attempts = 0
    while len(truths) < count and attempts < count * 20:
        attempts += 1
        idx += 1
        case_id = f"case-{idx:04d}"

        # --- who -----------------------------------------------------------
        is_new = rng.random() < 0.35
        if is_new:
            ident = _new_customer_identity(rng, idx)
            line1, city, postal, zone = rng.choice(NEW_ADDRESSES)
            address = f"{line1}, {city}, CT {postal}"
            expected_customer_id = None
        else:
            cust = rng.choice(customers)
            ident = {
                "customer_name": cust.name,
                "contact_name": cust.contact_name,
                "customer_email": cust.email,
                "customer_phone": cust.phone,
            }
            a = cust.address
            address = f"{a.line1}, {a.city}, {a.state} {a.postal_code}"
            zone = a.travel_zone
            expected_customer_id = cust.id

        # --- what ----------------------------------------------------------
        bundle = rng.choice(BUNDLES)
        property_sqft = rng.randrange(3_000, 60_000, 500)

        services: list[GroundTruthService] = []
        for code in bundle:
            band = None
            if code == "TREE_REMOVAL":
                band = rng.choice(list(TrunkDiameterBand)).value
            services.append(
                GroundTruthService(
                    catalog_code=code,
                    quantity=_quantity_for(code, rng, property_sqft),
                    trunk_diameter_band=band,
                )
            )

        urgency = _weighted_choice(
            rng, [(Urgency.STANDARD, 72), (Urgency.URGENT, 22), (Urgency.EMERGENCY, 6)]
        )
        access = _weighted_choice(
            rng,
            [(AccessDifficulty.EASY, 68), (AccessDifficulty.MODERATE, 24),
             (AccessDifficulty.DIFFICULT, 8)],
        )
        season = _weighted_choice(
            rng, [(Season.SHOULDER, 50), (Season.PEAK, 34), (Season.OFF_PEAK, 16)]
        )

        specials = rng.sample(SPECIAL_REQUESTS, k=rng.choice([0, 0, 1, 1, 2]))
        mess = _weighted_choice(rng, MESS_PROFILES)

        # A size label only means something if a service consumes the property
        # size as its quantity.
        size_relevant = any(s.catalog_code in PROPERTY_SIZE_SERVICES for s in services)

        context = PricingContext(
            urgency=Urgency(urgency),
            access_difficulty=AccessDifficulty(access),
            travel_zone=TravelZone(zone),
            season=Season(season),
        )

        # --- the expected total comes from the real engine ------------------
        try:
            quote = compute_quote(
                [
                    ProposedLineItem(
                        catalog_code=s.catalog_code,
                        quantity=Decimal(str(s.quantity)),
                        trunk_diameter_band=(
                            TrunkDiameterBand(s.trunk_diameter_band)
                            if s.trunk_diameter_band
                            else None
                        ),
                    )
                    for s in services
                ],
                context,
            )
        except PricingError:
            # Sampled a combination the engine refuses (e.g. over the ceiling).
            # Drop it and draw again rather than shipping an unlabelable case.
            continue

        truths.append(
            GroundTruth(
                case_id=case_id,
                tags=["generated", mess] + (["new_customer"] if is_new else ["existing_customer"]),
                mess_profile=mess,
                customer_name=str(ident["customer_name"]),
                contact_name=ident["contact_name"],
                customer_email=ident["customer_email"],
                customer_phone=ident["customer_phone"],
                expected_customer_id=expected_customer_id,
                expect_new_customer=is_new,
                property_address=address,
                property_size_sqft=property_sqft if size_relevant else None,
                services=services,
                special_requests=specials,
                urgency=Urgency(urgency),
                access_difficulty=AccessDifficulty(access),
                travel_zone=TravelZone(zone),
                season=Season(season),
                expect_quote=True,
                expect_route_to_human=False,
                expected_total_cents=quote.total_cents,
            )
        )

    return truths


# ---------------------------------------------------------------------------
# Stage 2 — render the email (LLM)
# ---------------------------------------------------------------------------

RENDER_SYSTEM = """\
You write realistic customer emails for a landscaping company's inbox. You are \
generating test data, so the emails must look like real people wrote them in a \
hurry, not like polished marketing copy.

Rules:
- Write ONLY what the customer would write. Never mention prices, quotes, \
service codes, square-foot rates, or anything the customer would not know.
- The customer does not know the company's internal service names. They describe \
work in plain English ("the grass is getting out of hand", "that big maple by \
the driveway needs to come down").
- Include every service the brief lists, but phrased naturally.
- Never invent an email address or phone number other than the ones given.
- Keep it between 2 and 12 sentences depending on the mess profile.
"""

MESS_INSTRUCTIONS = {
    "plain": "Clear and well organized. Correct spelling. Gets to the point.",
    "typos": "Several genuine typos and misspellings, a missing apostrophe or two, "
             "maybe a doubled word. Still readable.",
    "vague_size": "The customer gives NO square-foot figure. They describe the area "
                  "loosely — 'about half an acre I think', 'a bit under an acre', "
                  "'a decent sized yard'. Use the SIZE GUIDANCE below so the vague "
                  "phrasing still describes the real area: a reader converting it "
                  "should land near the true number.",
    "rambling": "Polite and chatty. Two or three sentences of small talk or "
                "backstory before and after the actual request.",
    "forwarded_thread": "Formatted as a forwarded or replied-to email thread, with "
                        "a '---------- Forwarded message ----------' header, quoted "
                        "'>' lines from an earlier message, and the real request at the top.",
    "terse_mobile": "Very short, typed on a phone. Lowercase, minimal punctuation, "
                    "abbreviations. Possibly a 'Sent from my iPhone' sign-off.",
    "all_caps": "MOSTLY IN CAPITAL LETTERS, as an older customer with caps lock on. "
                "Still polite.",
    "partial_info": "The customer omits their address, or any contact detail beyond "
                    "the email itself, assuming the company already has it on file. "
                    "They still describe the work and its size — it is the "
                    "identifying detail they leave out, not the job.",
}


SQFT_PER_ACRE = 43_560

# Colloquial areas a customer would actually say, with what each one literally
# means. The descriptor has to be vague AND accurate: a reader converting
# "roughly a quarter acre" lands on 10,890 sq ft, so using that phrase for a
# 14,000 sq ft lawn bakes a 22% error into the label — wider than the 15%
# tolerance the extraction metric allows, which would score correct
# extractions as failures.
_NATURAL_ACREAGES: tuple[tuple[float, str], ...] = (
    (0.125, "about an eighth of an acre"),
    (0.25, "about a quarter acre"),
    (0.333, "about a third of an acre"),
    (0.5, "about half an acre"),
    (0.667, "about two thirds of an acre"),
    (0.75, "about three quarters of an acre"),
    (1.0, "about an acre"),
    (1.25, "about an acre and a quarter"),
    (1.5, "about an acre and a half"),
    (2.0, "about two acres"),
)

# How far a colloquial phrase may sit from the truth before we prefer a plain
# decimal. Well inside the metric's 15% tolerance, leaving room for the
# model's own conversion error.
_DESCRIPTOR_TOLERANCE = 0.08


def _vague_size_hint(sqft: int | None) -> str:
    """Give the renderer an accurate way to be vague.

    Left to itself the model invents a descriptor that may be nowhere near the
    labeled size, and the extraction metric then measures the generator's
    imprecision rather than the pipeline's.
    """
    if not sqft:
        return ""

    acres = sqft / SQFT_PER_ACRE
    value, phrase = min(_NATURAL_ACREAGES, key=lambda p: abs(p[0] - acres) / acres)

    if abs(value - acres) / acres > _DESCRIPTOR_TOLERANCE:
        # No colloquial phrase is close enough; a decimal acreage is still
        # vague to a customer's ear and stays accurate.
        phrase = f"about {acres:.1f} acres" if acres >= 1 else f"about {acres:.2f} acres"

    return (
        f"\nSIZE GUIDANCE: the true area is {sqft:,} sq ft (~{acres:.2f} acres). "
        f"Describe it as {phrase}, or in equivalent loose terms. Never write a "
        f"square-foot number, and never describe an area of a different size."
    )


def _render_prompt(gt: GroundTruth) -> str:
    services_desc = []
    for s in gt.services:
        item = CATALOG_BY_CODE[s.catalog_code]
        qty = f"{s.quantity:g}"
        unit_desc = {
            Unit.PER_SQFT: f"about {qty} square feet",
            Unit.PER_HOUR: f"roughly {qty} hours of work",
            Unit.PER_UNIT: f"{qty} of them",
            Unit.FLAT: "one system",
        }[item.unit]
        extra = ""
        if s.trunk_diameter_band:
            extra = f" (trunk diameter {s.trunk_diameter_band.replace('_', ' ')})"
        services_desc.append(f"- {item.name} — {unit_desc}{extra}")

    urgency_desc = {
        Urgency.STANDARD: "No particular rush.",
        Urgency.URGENT: "They need it done within a couple of days.",
        Urgency.EMERGENCY: "It is an emergency — storm damage or a hazard.",
    }[gt.urgency]

    access_desc = {
        AccessDifficulty.EASY: "",
        AccessDifficulty.MODERATE: "Mention in passing that the yard is sloped, "
                                   "or gated, or awkward to get equipment into.",
        AccessDifficulty.DIFFICULT: "Make clear the site is hard to access — steep "
                                    "grade, no truck access, everything hand-carried.",
    }[gt.access_difficulty]

    size_hint = (
        _vague_size_hint(gt.property_size_sqft) if gt.mess_profile == "vague_size" else ""
    )

    specials = (
        "\n".join(f"- {s}" for s in gt.special_requests)
        if gt.special_requests
        else "(none)"
    )

    return f"""\
Write the email this customer sent.

FROM: {gt.contact_name or gt.customer_name} ({gt.customer_name})
EMAIL: {gt.customer_email or "(not given)"}
PHONE: {gt.customer_phone or "(not given)"}
PROPERTY: {gt.property_address or "(not stated in the email)"}

WORK THEY WANT:
{chr(10).join(services_desc)}

URGENCY: {urgency_desc}
SITE ACCESS: {access_desc or "Nothing notable."}

SPECIAL REQUESTS they should mention:
{specials}

MESS PROFILE — {gt.mess_profile}:
{MESS_INSTRUCTIONS[gt.mess_profile]}{size_hint}

Write the subject line and the body.
"""


def render_emails(
    truths: list[GroundTruth],
    client: LLMClient,
    *,
    verbose: bool = True,
    stream_to: Path | None = None,
) -> tuple[list[EvalCase], Usage]:
    """Render an email per ground truth.

    Each case is appended to `stream_to` as it completes rather than held until
    the end. A 17-minute render that writes nothing until the final case is
    all-or-nothing: a crash at case 179 loses everything, and from the outside
    a working run is indistinguishable from a hung one. The LLM cache makes a
    resumed run nearly free, but only if the partial output survived.

    Progress is flushed explicitly because stdout is block-buffered when this
    runs under `docker compose exec`, which is why a healthy run looks silent.
    """
    cases: list[EvalCase] = []
    total = Usage()
    base_time = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)

    handle = None
    if stream_to is not None:
        stream_to.parent.mkdir(parents=True, exist_ok=True)
        handle = stream_to.open("w", encoding="utf-8")

    try:
        for i, gt in enumerate(truths):
            result = client.structured(
                system=RENDER_SYSTEM,
                prompt=_render_prompt(gt),
                schema=RenderedEmail,
                temperature_key=gt.case_id,  # keeps cache entries distinct per case
            )
            total = total + result.usage
            email: RenderedEmail = result.parsed  # type: ignore[assignment]

            case = EvalCase(
                ground_truth=gt,
                email_subject=email.subject,
                email_body=email.body,
                sender_email=gt.customer_email,
                received_at=(base_time + timedelta(hours=i * 3)).isoformat(),
            )
            cases.append(case)

            if handle is not None:
                handle.write(case.model_dump_json() + "\n")
                handle.flush()

            done = i + 1
            if verbose and (done % 20 == 0 or done == len(truths)):
                cached = getattr(client, "hits", None)
                suffix = f" ({cached} from cache)" if cached else ""
                print(f"  rendered {done}/{len(truths)}{suffix}", flush=True)
    finally:
        if handle is not None:
            handle.close()

    return cases, total


# ---------------------------------------------------------------------------
# Supporting fixtures
# ---------------------------------------------------------------------------


def write_customers() -> None:
    CUSTOMERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CUSTOMERS_PATH.write_text(
        json.dumps([c.model_dump(mode="json") for c in CUSTOMERS], indent=2),
        encoding="utf-8",
    )


def write_catalog() -> None:
    from pricing.catalog_data import (
        ACCESS_MULTIPLIER,
        GLOBAL_JOB_MINIMUM_CENTS,
        SEASON_MULTIPLIER,
        TAX_RATE,
        TRAVEL_SURCHARGE_CENTS,
        TREE_REMOVAL_SURCHARGE_CENTS,
        URGENCY_MULTIPLIER,
    )

    payload = {
        "services": [
            {
                "code": i.code,
                "name": i.name,
                "service_type": i.service_type,
                "unit": i.unit.value,
                "base_rate_cents": str(i.base_rate_cents),
                "min_charge_cents": i.min_charge_cents,
                "description": i.description,
                "labor_sensitive": i.labor_sensitive,
                "keywords": list(i.keywords),
            }
            for i in CATALOG
        ],
        "rules": {
            "tree_removal_surcharge_cents": {k.value: v for k, v in TREE_REMOVAL_SURCHARGE_CENTS.items()},
            "access_multiplier": {k.value: str(v) for k, v in ACCESS_MULTIPLIER.items()},
            "urgency_multiplier": {k.value: str(v) for k, v in URGENCY_MULTIPLIER.items()},
            "season_multiplier": {k.value: str(v) for k, v in SEASON_MULTIPLIER.items()},
            "travel_surcharge_cents": {k.value: v for k, v in TRAVEL_SURCHARGE_CENTS.items()},
            "global_job_minimum_cents": GLOBAL_JOB_MINIMUM_CENTS,
            "tax_rate": str(TAX_RATE),
        },
    }
    CATALOG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=180, help="number of generated cases")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="build ground truths and supporting fixtures only; make no API calls",
    )
    args = ap.parse_args()

    print("Writing supporting fixtures...")
    write_customers()
    write_catalog()
    print(f"  customers -> {CUSTOMERS_PATH.name} ({len(CUSTOMERS)})")
    print(f"  catalog   -> {CATALOG_PATH.name} ({len(CATALOG)} services)")

    print(f"Authoring {args.count} ground truths (seeded, no LLM)...")
    truths = build_ground_truths(args.count, args.seed)
    print(f"  built {len(truths)}")

    profile_counts: dict[str, int] = {}
    for t in truths:
        profile_counts[t.mess_profile] = profile_counts.get(t.mess_profile, 0) + 1
    print(f"  mess profiles: {dict(sorted(profile_counts.items()))}")
    new_count = sum(1 for t in truths if t.expect_new_customer)
    print(f"  new customers: {new_count} / existing: {len(truths) - new_count}")

    if args.dry_run:
        print("\nDry run — stopping before the LLM stage. No API calls made.")
        return

    print(f"\nRendering {len(truths)} emails via the LLM...")
    print(f"  streaming to {CASES_PATH.name} as each case completes")
    client = build_llm_client()
    # render_emails writes each case as it lands; no separate write_cases call,
    # so an interrupted run leaves a usable partial dataset behind.
    cases, usage = render_emails(truths, client, stream_to=CASES_PATH)

    model = getattr(client, "model", "unknown")
    spend = cost_microcents(model, usage)
    print(f"\n  wrote {len(cases)} cases -> {CASES_PATH.name}")
    print(f"  tokens: {usage.input_tokens:,} in / {usage.output_tokens:,} out")
    print(f"  spend:  {fmt_microcents(spend)} on {model}")


if __name__ == "__main__":
    main()
