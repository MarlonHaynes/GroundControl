"""Riverside Grounds' customer list, as exported from their spreadsheet.

Forty accounts across the Hartford County service area. Three groups matter for
the customer-matching eval:

* **Near-duplicates** — `Hillcrest Property Management` vs `Hillcrest Properties LLC`
  are two genuinely different customers. A matcher that collapses them is wrong,
  and so is one that refuses to match either.
* **Name drift** — `Kowalski Landscaping Holdings` trades as `K&S Holdings`; mail
  arrives under both.
* **Ordinary accounts** — the bulk, where matching should be unambiguous.
"""

from __future__ import annotations

from evals.dataset import FixtureAddress, FixtureCustomer

_C = FixtureCustomer
_A = FixtureAddress

CUSTOMERS: list[FixtureCustomer] = [
    # --- Near-duplicate pair #1: similar name, different company ------------
    _C(
        id="cust-001",
        name="Hillcrest Property Management",
        contact_name="Dana Whitfield",
        email="dana@hillcrestpm.com",
        phone="(860) 555-0142",
        notes="Manages 6 condo associations. Invoices must reference the property name.",
        address=_A(
            line1="18 Ridgeview Terrace",
            city="West Hartford",
            postal_code="06107",
            travel_zone="zone_1",
            property_notes="Common areas only; units are owner-maintained.",
        ),
    ),
    _C(
        id="cust-002",
        name="Hillcrest Properties LLC",
        contact_name="Greg Hillcrest",
        email="greg@hillcrestllc.net",
        phone="(860) 555-0187",
        notes="Unrelated to Hillcrest Property Management despite the name. Single rental duplex.",
        address=_A(
            line1="402 Farmington Avenue",
            city="Farmington",
            postal_code="06032",
            travel_zone="zone_2",
        ),
    ),
    # --- Near-duplicate pair #2: same surname, different households ---------
    _C(
        id="cust-003",
        name="Robert Feeney",
        contact_name="Robert Feeney",
        email="bob.feeney@gmail.com",
        phone="(860) 555-0203",
        address=_A(line1="77 Bramble Lane", city="Avon", postal_code="06001"),
    ),
    _C(
        id="cust-004",
        name="Roberta Feeney-Marsh",
        contact_name="Roberta Feeney-Marsh",
        email="rfmarsh@outlook.com",
        phone="(860) 555-0209",
        notes="Bob Feeney's sister. Different property, frequently confused in the office.",
        address=_A(line1="12 Bramble Lane", city="Avon", postal_code="06001"),
    ),
    # --- Name drift ---------------------------------------------------------
    _C(
        id="cust-005",
        name="Kowalski Landscaping Holdings",
        contact_name="Stef Kowalski",
        email="stef@kandsholdings.com",
        phone="(860) 555-0311",
        notes="Trades as 'K&S Holdings'. Mail arrives under both names.",
        address=_A(
            line1="1140 Silas Deane Highway",
            city="Wethersfield",
            postal_code="06109",
            travel_zone="zone_2",
        ),
    ),
    # --- Ordinary commercial accounts --------------------------------------
    _C(
        id="cust-006",
        name="Talcott Ridge Dental",
        contact_name="Priya Raman",
        email="office@talcottridgedental.com",
        phone="(860) 555-0118",
        address=_A(line1="55 Talcott Notch Road", city="Farmington", postal_code="06032", travel_zone="zone_2"),
    ),
    _C(
        id="cust-007",
        name="Bishop's Corner Plaza",
        contact_name="Marcus Bell",
        email="mbell@bishopscornerplaza.com",
        phone="(860) 555-0155",
        notes="Retail plaza. Work must happen before 7am.",
        address=_A(
            line1="2450 Albany Avenue",
            city="West Hartford",
            postal_code="06117",
            property_notes="Parking islands and perimeter strip. No weekend access.",
        ),
    ),
    _C(
        id="cust-008",
        name="Elm Street Veterinary Clinic",
        contact_name="Dr. Alan Petrosky",
        email="frontdesk@elmstvet.com",
        phone="(860) 555-0166",
        address=_A(line1="309 Elm Street", city="Windsor", postal_code="06095", travel_zone="zone_2"),
    ),
    _C(
        id="cust-009",
        name="Nutmeg Valley Storage",
        contact_name="Cheryl Dombrowski",
        email="cheryl@nutmegvalleystorage.com",
        phone="(860) 555-0177",
        address=_A(
            line1="88 Industrial Park Road",
            city="Bloomfield",
            postal_code="06002",
            travel_zone="zone_2",
            property_notes="Large gravel perimeter, steep embankment at the rear fence.",
        ),
    ),
    _C(
        id="cust-010",
        name="Saint Brigid Parish",
        contact_name="Fr. Thomas Quill",
        email="parishoffice@stbrigidwh.org",
        phone="(860) 555-0190",
        notes="Non-profit rate applies. Cemetery grounds included.",
        address=_A(line1="1088 New Britain Avenue", city="West Hartford", postal_code="06110"),
    ),
    # --- Residential --------------------------------------------------------
    _C(id="cust-011", name="Amara Osei", contact_name="Amara Osei", email="amara.osei@gmail.com",
       phone="(860) 555-0221", address=_A(line1="34 Wampanoag Drive", city="West Hartford", postal_code="06117")),
    _C(id="cust-012", name="Jim Hollenbeck", contact_name="Jim Hollenbeck", email="jhollenbeck54@aol.com",
       phone="(860) 555-0232", notes="Pays by check. Prefers phone calls.",
       address=_A(line1="9 Sunset Farm Road", city="West Hartford", postal_code="06107")),
    _C(id="cust-013", name="The Ferraro Residence", contact_name="Nina Ferraro", email="nina.ferraro@icloud.com",
       phone="(860) 555-0244", address=_A(line1="215 Mountain Road", city="Simsbury", postal_code="06070",
       travel_zone="zone_2", property_notes="Steep front slope, stone retaining wall.")),
    _C(id="cust-014", name="Derek Vance", contact_name="Derek Vance", email="dvance@protonmail.com",
       phone="(860) 555-0256", address=_A(line1="41 Old Mill Lane", city="Bloomfield", postal_code="06002", travel_zone="zone_2")),
    _C(id="cust-015", name="Yolanda Briggs", contact_name="Yolanda Briggs", email="ybriggs@snet.net",
       phone="(860) 555-0267", address=_A(line1="6 Tunxis Village", city="Farmington", postal_code="06032", travel_zone="zone_2")),
    _C(id="cust-016", name="Patrick and Gail Mulready", contact_name="Gail Mulready", email="gmulready@comcast.net",
       phone="(860) 555-0278", address=_A(line1="127 Hopmeadow Street", city="Simsbury", postal_code="06070", travel_zone="zone_2")),
    _C(id="cust-017", name="Sandra Okonkwo", contact_name="Sandra Okonkwo", email="s.okonkwo@yahoo.com",
       phone="(860) 555-0289", address=_A(line1="88 Steele Road", city="West Hartford", postal_code="06119")),
    _C(id="cust-018", name="Henry Lamontagne", contact_name="Henry Lamontagne", email="hlamontagne@gmail.com",
       phone="(860) 555-0290", notes="Elderly; daughter Beth sometimes emails on his behalf.",
       address=_A(line1="22 Brookfield Road", city="Newington", postal_code="06111", travel_zone="zone_2")),
    _C(id="cust-019", name="Tomasz Wierzbicki", contact_name="Tomasz Wierzbicki", email="t.wierzbicki@gmail.com",
       phone="(860) 555-0301", address=_A(line1="55 Prospect Hill Road", city="Windsor", postal_code="06095", travel_zone="zone_2")),
    _C(id="cust-020", name="Marguerite Delacroix", contact_name="Marguerite Delacroix", email="mdelacroix@mac.com",
       phone="(860) 555-0312", address=_A(line1="301 Bloomfield Avenue", city="West Hartford", postal_code="06117")),
    # --- More commercial ----------------------------------------------------
    _C(id="cust-021", name="Copper Beech Montessori", contact_name="Helen Tran", email="admin@copperbeechmontessori.org",
       phone="(860) 555-0323", notes="Playground safety inspection required before crews arrive.",
       address=_A(line1="740 Park Road", city="West Hartford", postal_code="06107")),
    _C(id="cust-022", name="Griffin Brook Apartments", contact_name="Luis Ortega", email="lortega@griffinbrookapts.com",
       phone="(860) 555-0334", notes="112 units. Weekly mowing contract Apr-Oct.",
       address=_A(line1="1500 Blue Hills Avenue", city="Bloomfield", postal_code="06002", travel_zone="zone_2")),
    _C(id="cust-023", name="Wethersfield Cove Marina", contact_name="Doug Prazak", email="doug@wcovemarina.com",
       phone="(860) 555-0345", address=_A(line1="4 Marina Way", city="Wethersfield", postal_code="06109",
       travel_zone="zone_2", property_notes="Waterfront; soft ground in spring.")),
    _C(id="cust-024", name="Aetna Ridge Office Park", contact_name="Renata Silva", email="rsilva@aetnaridgeop.com",
       phone="(860) 555-0356", address=_A(line1="200 Corporate Place", city="Hartford", postal_code="06103")),
    _C(id="cust-025", name="Blue Back Physical Therapy", contact_name="Kevin Nowak", email="kevin@bluebackpt.com",
       phone="(860) 555-0367", address=_A(line1="65 Memorial Road", city="West Hartford", postal_code="06107")),
    _C(id="cust-026", name="Cedar Hollow HOA", contact_name="Beverly Ashcroft", email="board@cedarhollowhoa.org",
       phone="(860) 555-0378", notes="Board approval needed above $2,000.",
       address=_A(line1="Cedar Hollow Common", city="Avon", postal_code="06001", travel_zone="zone_2")),
    _C(id="cust-027", name="Riverbend Auto Body", contact_name="Sal Marchetti", email="sal@riverbendautobody.com",
       phone="(860) 555-0389", address=_A(line1="912 Windsor Avenue", city="Windsor", postal_code="06095", travel_zone="zone_2")),
    _C(id="cust-028", name="Granby Hills Country Club", contact_name="Ellen Prentice", email="eprentice@granbyhillscc.com",
       phone="(860) 555-0390", notes="Only perimeter and clubhouse beds. Course has its own crew.",
       address=_A(line1="1 Country Club Lane", city="Granby", postal_code="06035", travel_zone="zone_3")),
    _C(id="cust-029", name="Simsbury Free Library", contact_name="Gordon Aiello", email="gaiello@simsburylibrary.org",
       phone="(860) 555-0401", address=_A(line1="749 Hopmeadow Street", city="Simsbury", postal_code="06070", travel_zone="zone_2")),
    _C(id="cust-030", name="Northwest Park Condominiums", contact_name="Trish Bhatt", email="manager@nwparkcondos.com",
       phone="(860) 555-0412", address=_A(line1="330 Deerfield Road", city="Windsor", postal_code="06095", travel_zone="zone_2")),
    # --- Remaining residential ----------------------------------------------
    _C(id="cust-031", name="Curtis Bhandari", contact_name="Curtis Bhandari", email="cbhandari@gmail.com",
       phone="(860) 555-0423", address=_A(line1="14 Stonegate Drive", city="Avon", postal_code="06001", travel_zone="zone_2")),
    _C(id="cust-032", name="Eleanor Whitcomb", contact_name="Eleanor Whitcomb", email="ewhitcomb@verizon.net",
       phone="(860) 555-0434", address=_A(line1="503 Wolcott Hill Road", city="Wethersfield", postal_code="06109", travel_zone="zone_2")),
    _C(id="cust-033", name="Andre Boudreaux", contact_name="Andre Boudreaux", email="aboudreaux@gmail.com",
       phone="(860) 555-0445", address=_A(line1="76 Ridgewood Road", city="West Hartford", postal_code="06107")),
    _C(id="cust-034", name="Mei-Ling Chao", contact_name="Mei-Ling Chao", email="mlchao@gmail.com",
       phone="(860) 555-0456", address=_A(line1="19 Tumblebrook Drive", city="Bloomfield", postal_code="06002", travel_zone="zone_2")),
    _C(id="cust-035", name="Franklin Odum", contact_name="Franklin Odum", email="fodum@hotmail.com",
       phone="(860) 555-0467", notes="Large wooded lot, frequent storm damage.",
       address=_A(line1="240 Nod Road", city="Simsbury", postal_code="06070", travel_zone="zone_2",
       property_notes="Mature oaks over the driveway.")),
    _C(id="cust-036", name="Bridget Nakamura", contact_name="Bridget Nakamura", email="bnakamura@gmail.com",
       phone="(860) 555-0478", address=_A(line1="8 Fernwood Road", city="Newington", postal_code="06111", travel_zone="zone_2")),
    _C(id="cust-037", name="Walter Krzeminski", contact_name="Walter Krzeminski", email="wkrz@sbcglobal.net",
       phone="(860) 555-0489", address=_A(line1="61 Maple Hill Avenue", city="Newington", postal_code="06111", travel_zone="zone_2")),
    _C(id="cust-038", name="Imani Fitzgerald", contact_name="Imani Fitzgerald", email="imani.fitz@gmail.com",
       phone="(860) 555-0490", address=_A(line1="127 Scarborough Street", city="Hartford", postal_code="06105")),
    _C(id="cust-039", name="Gustavo Reyes-Pinto", contact_name="Gustavo Reyes-Pinto", email="greyespinto@gmail.com",
       phone="(860) 555-0501", address=_A(line1="45 Sherbrooke Avenue", city="Hartford", postal_code="06106")),
    _C(id="cust-040", name="Dorothy Ainsworth", contact_name="Dorothy Ainsworth", email="dainsworth1938@aol.com",
       phone="(860) 555-0512", notes="Long-time account, since 2009.",
       address=_A(line1="92 Mohawk Drive", city="West Hartford", postal_code="06117")),
]

CUSTOMERS_BY_ID = {c.id: c for c in CUSTOMERS}
