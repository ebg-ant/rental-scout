"""Made-up New York listings for the tests and the demo report.

Nothing here is a real listing: addresses, prices, text and URLs are invented, and
the URLs do not resolve. Three "days" exercise every path through the pipeline.
"""

from __future__ import annotations

SE = "https://streeteasy.com/building/demo-%s"
ZL = "https://www.zillow.com/homedetails/demo/%s_zpid/"
CL = "https://newyork.craigslist.org/%s/apa/d/demo/%s.html"
MAPS = "https://www.google.com/maps/search/?api=1&query=demo"
PHOTO = "https://photos.example.com/demo/%s/%s.jpg"


LOOKUPS = {"commute_minutes": "Google Maps transit directions", "flood_zone": "FEMA flood map for the address",
           "noise_sources": "Google Maps satellite and Street View, one block around"}


def page(rec_id, address, text, facts, unknown=None, quotes=None, **extra):
    """An enrich payload. Every fact gets a provenance line, as the real agent must give."""
    base = {
        "rent_advertised_is": "gross", "free_months": 0, "lease_term_months": 12,
        "furnished": False, "sublet": False, "room_share": False, "tenant_pays_broker_fee": False,
        "level": "upper", "flood_zone": "none", "noise_sources": [], "photos_recency": "recent", "photo_count": 14,
    }
    base.update(facts)
    unknown = dict(unknown or {})
    for name in list(base):
        if name in unknown or base[name] is None:
            base.pop(name)
    for name in ("sqft", "rent_stabilized", "income_multiple_required", "listed_date"):
        if name not in base:
            unknown.setdefault(name, "not stated on the page")
    provenance = dict((name, {"where": "listing page"}) for name in base)
    for name, where in LOOKUPS.items():
        if name in base:
            provenance[name] = {"where": where, "url": MAPS}
    for name, quote in (quotes or {}).items():
        provenance[name] = {"where": "description", "quote": quote}
    payload = {"id": rec_id, "raw_text": text, "facts": base, "unknown": unknown, "provenance": provenance,
               "photos": [PHOTO % (rec_id, n + 1) for n in range(base.get("photo_count", 0))],
               "floor_plans": [PHOTO % (rec_id, "plan")]}
    if address:
        payload["address"] = {"raw": address}
    payload.update(extra)
    return payload


def with_photos(day):
    """Give every search card the first photo of its listing, as real cards have."""
    from scout.pipeline import _record_id, derive_source_id
    for sweep in day["sweeps"].values():
        for card in [c for page in sweep.get("pages") or [sweep["cards"]] for c in page]:
            source = "streeteasy" if "streeteasy" in card["url"] else "zillow" if "zillow" in card["url"] else "craigslist"
            card.setdefault("first_photo", PHOTO % (_record_id(source, derive_source_id(card["url"])), 1))
    return day


def crit(score, rationale, *evidence, **extra):
    entry = {"score": score, "rationale": rationale, "evidence": list(evidence)}
    entry.update(extra)
    return entry


def ev(kind, ref=None, note=None, quote=None):
    item = {"type": kind}
    if isinstance(ref, list):
        item["photos"] = ref
    elif ref:
        item["ref"] = ref
    if note:
        item["note"] = note
    if quote:
        item["quote"] = quote
    return item


def unknown(reason):
    return {"score": None, "unknown_reason": reason}


# --------------------------------------------------------------------------
# Day 1
# --------------------------------------------------------------------------

TEXT_7TH = ("Sun-filled one bedroom on a tree-lined East Village block. South-facing living room over low rear yards. "
            "Queen-size bedroom with a full closet. Dishwasher. Laundry in building. Cats allowed. "
            "Third floor walk-up. Video intercom and package shelves in the lobby. Shared roof deck with seating. "
            "Available December 1. No fee.")
TEXT_BERRY = ("Modern one bedroom in a full-service Williamsburg elevator building. One month free on a 13-month lease. "
              "Gross rent $4,300. Washer and dryer in unit, dishwasher, central air. Private balcony. "
              "Doorman and package room. West-facing over Berry Street. Ask the leasing office about our pet policy.")
TEXT_LIC = ("High-floor one bedroom in Long Island City with skyline views. Elevator, doorman, gym. "
            "Washer dryer in unit. Pets considered case by case. Available now.")
TEXT_FRANKLIN = ("Charming Greenpoint one bedroom, second floor rear, facing quiet gardens to the east. "
                 "Big bedroom fits a queen with room to spare, two closets. Dishwasher. Cats allowed. "
                 "No laundry in the building; laundromat on the corner. Shared backyard with a table and chairs.")
TEXT_BROOME = ("Ground floor one bedroom on Broome Street. Bedroom window faces the interior shaft. "
               "Compact living area. Laundry in building. Cats allowed. Window AC. Steps from the J/M/Z.")
TEXT_ASTORIA = ("Renovated one bedroom in Astoria, third floor of a small elevator building. North-facing but open. "
                "Dishwasher, through-wall AC, laundry in building. Cats allowed. Rent stabilized. "
                "Seven minute walk to the N/W at 30th Avenue.")
TEXT_SCAM = ("Beautiful large apartment in the East Village, all utilities included. I am currently out of the country "
             "for work so I cannot show the place, but I will mail you the keys once the deposit is received. "
             "Cats allowed. Serious inquiries only.")
TEXT_NOADDR = ("Sunny Williamsburg one bedroom near the L. Top floor of a three story house. Dishwasher. "
               "Cats allowed. Laundry in building. Shared backyard with a grill. Owner managed, no fee. "
               "Email for the address and a showing.")

DAY1 = {
    "sweeps": {
        "streeteasy:0:2800-4400": {"total_results": 7, "cards": [
            {"url": SE % "212-east-7-street/4b", "title": "212 East 7th Street #4B", "address": "212 East 7th Street #4B",
             "rent": 3950, "beds": 1, "baths": 1, "neighborhood": "East Village", "snippet": "No fee. Laundry in building."},
            {"url": SE % "88-berry-street/6c", "title": "88 Berry Street #6C", "address": "88 Berry Street #6C",
             "rent": 4300, "beds": 1, "baths": 1, "neighborhood": "Williamsburg", "snippet": "1 month free"},
            {"url": SE % "41-15-29-street/12f", "title": "41-15 29th Street #12F", "address": "41-15 29th Street #12F",
             "rent": 3600, "beds": 1, "baths": 1, "neighborhood": "Long Island City"},
            {"url": SE % "9-avenue-b/2", "title": "9 Avenue B #2", "address": "9 Avenue B #2", "rent": 3700, "beds": 1,
             "neighborhood": "East Village", "snippet": "Fully furnished, bring your suitcase"},
            {"url": SE % "1-north-4-place/30a", "title": "1 North 4th Place #30A", "address": "1 North 4th Place #30A",
             "rent": 5200, "beds": 1, "neighborhood": "Williamsburg", "snippet": "Featured listing"},
            {"url": SE % "150-franklin-street/2r", "title": "150 Franklin Street #2R", "address": "150 Franklin Street #2R",
             "rent": 3200, "beds": 1, "baths": 1, "neighborhood": "Greenpoint"},
            {"url": SE % "305-broome-street/1a", "title": "305 Broome Street #1A", "address": "305 Broome Street #1A",
             "rent": 2900, "beds": 1, "baths": 1, "neighborhood": "Lower East Side"},
        ]},
        # Zillow reports far more results than it will page through, so the scout splits the price
        # band in two and sweeps each half.
        "zillow:0:2800-4400": {"total_results": 800, "pages": [[
            {"url": ZL % "900001", "title": "212 E 7th St APT 4B, New York, NY 10009",
             "address": "212 E 7th St APT 4B, New York, NY 10009", "rent": 4000, "beds": 1, "baths": 1},
        ], []]},
        "zillow:0:2800-3600": {"cards": [
            {"url": ZL % "900002", "title": "23-05 30th Ave #3C, Astoria, NY 11102",
             "address": "23-05 30th Ave #3C, Astoria, NY 11102", "rent": 3100, "beds": 1, "baths": 1},
        ]},
        "zillow:0:3601-4400": {"cards": [
            {"url": ZL % "900001", "title": "212 E 7th St APT 4B, New York, NY 10009",
             "address": "212 E 7th St APT 4B, New York, NY 10009", "rent": 4000, "beds": 1, "baths": 1},
        ]},
        "craigslist:0:2800-4400": {"cards": [
            {"url": CL % ("mnh", "7900000001"), "title": "HUGE East Village apartment all utilities included", "rent": 2800,
             "beds": 1, "neighborhood": "East Village"},
            {"url": CL % ("mnh", "7900000002"), "title": "Sublet my LES 1BR Jan-June", "rent": 3000, "beds": 1,
             "neighborhood": "Lower East Side"},
        ]},
        "craigslist:1:2800-4400": {"cards": [
            {"url": CL % ("brk", "7900000003"), "title": "Sunny Williamsburg 1BR near the L - no fee", "rent": 3400,
             "beds": 1, "neighborhood": "Williamsburg"},
        ]},
    },
    "pages": {
        "streeteasy-demo-212-east-7-street-4b": page(
            "streeteasy-demo-212-east-7-street-4b", "212 East 7th Street #4B, New York, NY 10009", TEXT_7TH,
            {"rent_advertised": 3950, "beds": 1, "baths": 1, "neighborhood": "East Village", "zip": "10009",
             "cats": "allowed", "floor": 3, "elevator": False, "laundry": "in_building", "commute_minutes": 24,
             "available_date": "2026-12-01", "dishwasher": True, "ac": "window", "outdoor": "shared"},
            quotes={"cats": "Cats allowed", "laundry": "Laundry in building", "floor": "Third floor walk-up"}),
        "streeteasy-demo-88-berry-street-6c": page(
            "streeteasy-demo-88-berry-street-6c", "88 Berry Street #6C, Brooklyn, NY 11249", TEXT_BERRY,
            {"rent_advertised": 4300, "free_months": 1, "lease_term_months": 13, "beds": 1, "baths": 1,
             "neighborhood": "Williamsburg", "zip": "11249", "floor": 6, "elevator": True, "laundry": "in_unit",
             "commute_minutes": 31, "available_date": "2026-11-15", "dishwasher": True, "ac": "central",
             "outdoor": "private", "doorman": True},
            unknown={"cats": "page only says to ask the leasing office; no pet policy stated"},
            quotes={"free_months": "One month free on a 13-month lease", "rent_advertised": "Gross rent $4,300"}),
        "streeteasy-demo-41-15-29-street-12f": page(
            "streeteasy-demo-41-15-29-street-12f", "41-15 29th Street #12F, Long Island City, NY 11101", TEXT_LIC,
            {"rent_advertised": 3600, "beds": 1, "baths": 1, "neighborhood": "Long Island City", "zip": "11101",
             "cats": "case_by_case", "floor": 12, "elevator": True, "laundry": "in_unit", "commute_minutes": 18,
             "available_date": "2026-10-05", "dishwasher": True, "ac": "central", "outdoor": "none"},
            quotes={"cats": "Pets considered case by case"}),
        "streeteasy-demo-150-franklin-street-2r": page(
            "streeteasy-demo-150-franklin-street-2r", "150 Franklin Street #2R, Brooklyn, NY 11222", TEXT_FRANKLIN,
            {"rent_advertised": 3200, "beds": 1, "baths": 1, "neighborhood": "Greenpoint", "zip": "11222",
             "cats": "allowed", "floor": 2, "elevator": False, "laundry": "none", "commute_minutes": 38,
             "available_date": "2026-12-01", "dishwasher": True, "ac": "window", "outdoor": "shared"},
            quotes={"laundry": "No laundry in the building"}),
        "streeteasy-demo-305-broome-street-1a": page(
            "streeteasy-demo-305-broome-street-1a", "305 Broome Street #1A, New York, NY 10002", TEXT_BROOME,
            {"rent_advertised": 2900, "beds": 1, "baths": 1, "neighborhood": "Lower East Side", "zip": "10002",
             "cats": "allowed", "floor": 1, "elevator": False, "laundry": "in_building", "commute_minutes": 22,
             "available_date": "2026-11-01", "dishwasher": False, "ac": "window", "outdoor": "none", "level": "ground",
             "noise_sources": ["Heavy traffic on Broome toward the Williamsburg Bridge"]}, floor_plans=[]),
        "zillow-900002": page(
            "zillow-900002", "23-05 30th Ave #3C, Astoria, NY 11102", TEXT_ASTORIA,
            {"rent_advertised": 3100, "beds": 1, "baths": 1, "neighborhood": "Astoria", "zip": "11102",
             "cats": "allowed", "floor": 3, "elevator": True, "laundry": "in_building", "commute_minutes": 29,
             "available_date": "2026-12-10", "dishwasher": True, "ac": "through_wall", "outdoor": "none",
             "rent_stabilized": True},
            quotes={"rent_stabilized": "Rent stabilized"}),
        "craigslist-7900000001": page(
            "craigslist-7900000001", None, TEXT_SCAM,
            {"rent_advertised": 2800, "beds": 1, "neighborhood": "East Village", "cats": "allowed", "owner_remote": True,
             "asks_deposit_before_viewing": True, "photo_count": 4},
            unknown=dict((name, "not stated in the post") for name in (
                "baths", "zip", "floor", "elevator", "laundry", "commute_minutes", "available_date", "dishwasher", "ac",
                "outdoor", "level", "flood_zone", "noise_sources", "photos_recency", "lease_term_months",
                "tenant_pays_broker_fee")),
            address_unknown_reason="Post gives no address or cross streets", floor_plans=[]),
        "craigslist-7900000003": page(
            "craigslist-7900000003", None, TEXT_NOADDR,
            {"rent_advertised": 3400, "beds": 1, "baths": 1, "neighborhood": "Williamsburg", "cats": "allowed", "floor": 3,
             "elevator": False, "laundry": "in_building", "dishwasher": True, "photo_count": 9, "outdoor": "shared",
             "available_date": "2026-11-20"},
            unknown={"zip": "no address given", "commute_minutes": "no address to route from",
                     "flood_zone": "no address to look up", "noise_sources": "no address to check",
                     "ac": "not mentioned and not visible in photos"},
            address_unknown_reason="Post says to email for the address", floor_plans=[]),
    },
    "scores": {
        "streeteasy-demo-212-east-7-street-4b": {
            "id": "streeteasy-demo-212-east-7-street-4b",
            "headline": "Bright, well laid out third-floor walk-up on a quiet block; roof deck is a bonus.",
            "questions": ["Is the roof deck open to all tenants year round?"],
            "criteria": {
                "light": crit(9, "3rd floor rear, living room faces south over one-story extensions; nothing taller for ~80 ft.",
                              ev("photos", [2, 3], "long shadows across the living room floor"),
                              ev("satellite", MAPS, "rear yards and single-story extensions to the south")),
                "layout": crit(8, "Bedroom fits a queen with a full closet; living room takes a sofa and a small table.",
                               ev("floor_plan", "floor plan image", "bedroom about 10x12"), ev("photos", [5, 6, 7])),
                "building": crit(6, "Third-floor walk-up, video intercom, package shelves; 3 open class A violations.",
                                 ev("text", quote="Video intercom and package shelves in the lobby"),
                                 ev("records", "HPD Online lookup", "3 open class A, none B or C")),
                "commute": crit(8, "24 minutes door to door, L to the 4/5/6 with one transfer, 6 minute walk.",
                                ev("map", MAPS, "transit directions, weekday 9am arrival")),
                "practical": crit(6, "Laundry in building and dishwasher, but window AC only.",
                                  ev("text", quote="Dishwasher. Laundry in building.")),
                "outdoor": crit(5, "Shared roof deck with seating.", ev("text", quote="Shared roof deck with seating"),
                                ev("photos", [12])),
            }},
        "streeteasy-demo-88-berry-street-6c": {
            "id": "streeteasy-demo-88-berry-street-6c",
            "headline": "Best-equipped unit seen so far; everything hinges on the cat policy.",
            "questions": ["Are cats allowed, in writing?"],
            "criteria": {
                "light": crit(8, "6th floor facing west over Berry Street; buildings opposite are 4 stories.",
                              ev("photos", [1, 4]), ev("satellite", MAPS, "4-story row across a 60 ft street")),
                "layout": crit(8, "True one bedroom with window and closet; open living and kitchen fits sofa and table.",
                               ev("floor_plan", "floor plan image"), ev("photos", [3, 4, 5, 6])),
                "building": crit(10, "Elevator, doorman, package room; no open HPD violations.",
                                 ev("text", quote="Doorman and package room"), ev("records", "HPD Online lookup", "0 open")),
                "commute": crit(6, "31 minutes, L then one transfer, 9 minute walk to Bedford Av.", ev("map", MAPS)),
                "practical": crit(10, "In-unit washer and dryer, dishwasher, central air.",
                                  ev("text", quote="Washer and dryer in unit, dishwasher, central air")),
                "outdoor": crit(8, "Private balcony fits two chairs.", ev("photos", [9]),
                                ev("text", quote="Private balcony")),
            }},
        "streeteasy-demo-150-franklin-street-2r": {
            "id": "streeteasy-demo-150-franklin-street-2r",
            "headline": "Lovely garden-facing unit; the only real miss is laundry.",
            "criteria": {
                "light": crit(8, "2nd floor rear facing east over open gardens; no building within ~70 ft.",
                              ev("photos", [2, 6]), ev("satellite", MAPS, "contiguous rear gardens")),
                "layout": crit(9, "Large bedroom with two closets, separate from the living room.",
                               ev("photos", [4, 5]), ev("floor_plan", "floor plan image")),
                "building": crit(8, "One flight up, tidy halls, 1 open class A violation.",
                                 ev("text", quote="second floor rear"), ev("records", "HPD Online lookup", "1 open class A")),
                "commute": crit(6, "38 minutes, G to the 7 with one transfer, 8 minute walk.", ev("map", MAPS)),
                "practical": crit(4, "No laundry in the building; dishwasher; window AC.",
                                  ev("text", quote="No laundry in the building; laundromat on the corner")),
                "outdoor": crit(5, "Shared backyard, furnished.", ev("text", quote="Shared backyard with a table and chairs")),
            }},
        "streeteasy-demo-305-broome-street-1a": {
            "id": "streeteasy-demo-305-broome-street-1a",
            "headline": "Cheap for the area, and it shows: dark and tight.",
            "criteria": {
                "light": crit(2, "Ground floor; bedroom window opens onto an interior shaft.",
                              ev("photos", [5]), ev("satellite", MAPS, "enclosed light well"),
                              ev("text", quote="Bedroom window faces the interior shaft")),
                "layout": crit(4, "Living area too small for both a sofa and a table.", ev("photos", [1, 2, 3]),
                               unavailable=[{"type": "floor_plan", "reason": "listing has no floor plan"}]),
                "building": crit(6, "Ground floor so no stairs; dated halls; 5 open violations, none class C.",
                                 ev("text", quote="Ground floor one bedroom"), ev("records", "HPD Online lookup")),
                "commute": crit(8, "22 minutes, one train, 4 minute walk.", ev("map", MAPS)),
                "practical": crit(4, "Laundry in building; no dishwasher; window AC.",
                                  ev("text", quote="Laundry in building")),
                "outdoor": crit(0, "No outdoor space in 14 photos or the amenities list.", ev("photos", "all 14 photos")),
            }},
        "zillow-900002": {
            "id": "zillow-900002",
            "headline": "Solid, stabilized and good value, if a little plain.",
            "criteria": {
                "light": crit(6, "3rd floor facing north, but open across a wide avenue.",
                              ev("photos", [1, 2]), ev("satellite", MAPS, "30th Ave is about 80 ft wide here")),
                "layout": crit(8, "True bedroom with closet and window; shares a wall with the living room.",
                               ev("photos", [3, 4, 5]), ev("floor_plan", "floor plan image")),
                "building": crit(8, "Small elevator building, secure entry, 2 open class A violations.",
                                 ev("text", quote="third floor of a small elevator building"),
                                 ev("records", "HPD Online lookup")),
                "commute": crit(8, "29 minutes, N/W direct, 7 minute walk.", ev("map", MAPS),
                                ev("text", quote="Seven minute walk to the N/W at 30th Avenue")),
                "practical": crit(8, "Laundry in building, dishwasher, through-wall AC.",
                                  ev("text", quote="Dishwasher, through-wall AC, laundry in building")),
                "outdoor": crit(0, "None shown or mentioned in a thorough listing.", ev("photos", "all 14 photos")),
            }},
        "craigslist-7900000003": {
            "id": "craigslist-7900000003",
            "headline": "Promising owner-direct unit, but nothing about the location can be checked yet.",
            "questions": ["What is the street address?"],
            "criteria": {
                "light": unknown("No address, so what faces the windows cannot be checked on satellite or Street View"),
                "layout": crit(8, "Bedroom fits a queen with a closet and window; living room fits a sofa and table.",
                               ev("photos", [2, 3, 4, 5, 6]),
                               unavailable=[{"type": "floor_plan", "reason": "post has no floor plan"}]),
                "building": crit(7, "Top floor of a three-story house, two flights up; owner managed and tidy.",
                                 ev("text", quote="Top floor of a three story house"),
                                 unavailable=[{"type": "records", "reason": "no address to look up on HPD Online"}]),
                "commute": unknown("No address to route from; 'near the L' is not enough to time a trip"),
                "practical": crit(7, "Laundry in building and dishwasher; cooling not shown.",
                                  ev("text", quote="Laundry in building")),
                "outdoor": crit(5, "Shared backyard with a grill.", ev("text", quote="Shared backyard with a grill"),
                                ev("photos", [8])),
            }},
    },
}

# --------------------------------------------------------------------------
# Day 2: a price drop, a repost, a new listing, one gone quiet, and a CAPTCHA
# --------------------------------------------------------------------------

TEXT_BERRY_2 = TEXT_BERRY.replace("Gross rent $4,300", "Gross rent $4,150").replace(
    "Ask the leasing office about our pet policy.", "Cats allowed. Dogs upon approval.")
TEXT_NORMAN = ("Corner one bedroom in Greenpoint with windows south and west over McGolrick Park. Second floor. "
               "Queen bedroom, two closets. Washer and dryer in unit, dishwasher, split-system AC. Cats allowed. "
               "Private terrace off the living room. Virtual doorman.")

DAY2 = {
    "blocks": {"zillow": "press-and-hold CAPTCHA"},
    "sweeps": {
        "streeteasy:0:new": {"cards": [
            DAY1["sweeps"]["streeteasy:0:2800-4400"]["cards"][0],
            dict(DAY1["sweeps"]["streeteasy:0:2800-4400"]["cards"][1], rent=4150),
            dict(DAY1["sweeps"]["streeteasy:0:2800-4400"]["cards"][2], rent=3450),
            DAY1["sweeps"]["streeteasy:0:2800-4400"]["cards"][3],
            # 150 Franklin is missing from today's results.
            DAY1["sweeps"]["streeteasy:0:2800-4400"]["cards"][6],
            {"url": SE % "77-norman-avenue/2a", "title": "77 Norman Avenue #2A", "address": "77 Norman Avenue #2A",
             "rent": 3900, "beds": 1, "baths": 1, "neighborhood": "Greenpoint"},
        ]},
        "craigslist:0:new": {"cards": DAY1["sweeps"]["craigslist:0:2800-4400"]["cards"]},
        "craigslist:1:new": {"cards": [
            # Same post, new id: a repost.
            {"url": CL % ("brk", "7900000099"), "title": "Sunny Williamsburg 1BR near the L - no fee", "rent": 3400,
             "beds": 1, "neighborhood": "Williamsburg"},
        ]},
    },
    "pages": {
        "streeteasy-demo-88-berry-street-6c": page(
            "streeteasy-demo-88-berry-street-6c", "88 Berry Street #6C, Brooklyn, NY 11249", TEXT_BERRY_2,
            {"rent_advertised": 4150, "free_months": 1, "lease_term_months": 13, "beds": 1, "baths": 1,
             "neighborhood": "Williamsburg", "zip": "11249", "floor": 6, "elevator": True, "laundry": "in_unit",
             "commute_minutes": 31, "available_date": "2026-11-15", "dishwasher": True, "ac": "central",
             "outdoor": "private", "doorman": True, "cats": "allowed"},
            quotes={"cats": "Cats allowed"}),
        "streeteasy-demo-77-norman-avenue-2a": page(
            "streeteasy-demo-77-norman-avenue-2a", "77 Norman Avenue #2A, Brooklyn, NY 11222", TEXT_NORMAN,
            {"rent_advertised": 3900, "beds": 1, "baths": 1, "neighborhood": "Greenpoint", "zip": "11222",
             "cats": "allowed", "floor": 2, "elevator": False, "laundry": "in_unit", "commute_minutes": 36,
             "available_date": "2026-12-01", "dishwasher": True, "ac": "split", "outdoor": "private"},
            quotes={"cats": "Cats allowed"}),
    },
    "scores": {
        "streeteasy-demo-77-norman-avenue-2a": {
            "id": "streeteasy-demo-77-norman-avenue-2a",
            "headline": "Corner unit over the park with a private terrace: the one to see first.",
            "criteria": {
                "light": crit(10, "Corner unit, windows south and west over McGolrick Park; nothing in front.",
                              ev("photos", [1, 2, 3, 4]), ev("satellite", MAPS, "park to the south and west")),
                "layout": crit(8, "Queen bedroom with two closets; bedroom shares a wall with the living room.",
                               ev("photos", [5, 6, 7]), ev("floor_plan", "floor plan image")),
                "building": crit(8, "One flight up, virtual doorman, no open violations.",
                                 ev("text", quote="Virtual doorman"), ev("records", "HPD Online lookup", "0 open")),
                "commute": crit(6, "36 minutes, G to the 7, one transfer, 9 minute walk.", ev("map", MAPS)),
                "practical": crit(10, "In-unit laundry, dishwasher, split AC, two closets.",
                                  ev("text", quote="Washer and dryer in unit, dishwasher, split-system AC")),
                "outdoor": crit(10, "Private terrace off the living room, fits a table and four chairs.",
                                ev("photos", [9, 10]), ev("text", quote="Private terrace off the living room")),
            }},
    },
}

# --------------------------------------------------------------------------
# Day 3: a quiet day. Newest-first sweeps find nothing new and stop.
# --------------------------------------------------------------------------

DAY3 = {
    "sweeps": {
        "streeteasy:0:new": {"pages": [DAY2["sweeps"]["streeteasy:0:new"]["cards"], []]},
        "zillow:0:new": {"cards": [DAY1["sweeps"]["zillow:0:2800-4400"]["pages"][0][0],
                                   DAY1["sweeps"]["zillow:0:2800-3600"]["cards"][0]]},
        "craigslist:0:new": DAY2["sweeps"]["craigslist:0:new"],
        "craigslist:1:new": DAY2["sweeps"]["craigslist:1:new"],
    },
    "pages": {}, "scores": {},
}

# --------------------------------------------------------------------------
# A week on: the full sweep comes round again. 150 Franklin (WATCH) and 305 Broome (PASS) are
# no longer in the results.
# --------------------------------------------------------------------------

WEEK2 = {
    "sweeps": {
        "streeteasy:0:2800-4400": {"cards": [card for card in DAY2["sweeps"]["streeteasy:0:new"]["cards"]
                                             if "broome" not in card["url"]]},
        "zillow:0:2800-4400": DAY3["sweeps"]["zillow:0:new"],
        "craigslist:0:2800-4400": DAY2["sweeps"]["craigslist:0:new"],
        "craigslist:1:2800-4400": DAY2["sweeps"]["craigslist:1:new"],
    },
    "pages": {"streeteasy-demo-150-franklin-street-2r": {"id": "streeteasy-demo-150-franklin-street-2r", "gone": True}},
    "scores": {},
}

for _day in (DAY1, DAY2, DAY3, WEEK2):
    with_photos(_day)
