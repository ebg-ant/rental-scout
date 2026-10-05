# Enrich: read one listing page and record what it says

The `enrich` task gives you a `url`, the search `card`, a `capture` list and `helper_links`.
Open the page, read all of it (description, amenities, fee and policy sections, floor plan, every
photo, map), then hand in one JSON file. Search cards hide and misstate facts, so trust the page.

The task's `why` says what brought it here: `new`, `price_changed`, `relisted`, `config_changed`,
`recheck` (a shortlisted listing nobody has seen for a few days: is it still up?), `refresh` (the user
asked for a fresh look) or `incomplete` (reading stopped early last time and now needs finishing). For anything but `new` you also get `previous_facts`; read the page afresh
rather than copying them.

## Read the dealbreakers first, and stop early if one is missed

`check_first` lists the facts that can rule the listing out on their own. Read those first, along with any
scam signals. If one plainly fails (no cats, a sublet, over budget) or the post is plainly a scam, do not
spend time on maps, flood zones and photos. Hand in just what you saw:

```json
{
  "id": "<id from the task>", "source": "<source>", "url": "<the page you read>",
  "stop_early": true,
  "raw_text": "<the part of the page you read, word for word>",
  "facts": {"cats": "not_allowed"},
  "provenance": {"cats": {"where": "policies section", "quote": "No pets"}}
}
```

The scout checks that these facts really do rule it out (a missed dealbreaker, or a high-risk flag). If they
do not, it says so and you carry on with the full capture. If the user's criteria later change so that the
listing is back in the running, it comes back to be read in full (`why: incomplete`).

## Payload

```json
{
  "id": "<id from the task>",
  "source": "<source from the task>",
  "url": "<the page you read>",
  "address": {"raw": "245 East 10th Street #4B, New York, NY 10003", "lat": 40.72, "lng": -73.98},
  "raw_text": "<description + amenities + policies, copied word for word>",
  "photos": ["https://.../1.jpg", {"url": "https://.../2.jpg", "caption": "bedroom"}],
  "floor_plans": ["https://.../plan.png"],
  "facts": {"rent_advertised": 3950, "cats": "allowed", "floor": 3, "elevator": false},
  "provenance": {
    "cats": {"where": "policies section", "quote": "Cats allowed"},
    "floor": {"where": "description", "quote": "Third floor walk-up"},
    "elevator": "amenities list has no elevator and description says walk-up"
  },
  "unknown": {"sqft": "not on the page or the floor plan"}
}
```

A full working example is `agent/examples/enrich.json`.

## Rules the command enforces

- Every field in `capture` must appear in `facts` with a `provenance` entry, or in `unknown` with
  where you looked. There is no third option.
- A `quote` must be in `raw_text`, word for word. If you saw it in a photo, floor plan or map, leave
  out `quote` and say so in `where`.
- Leave calculated fields out (`rent_gross`, `rent_net_effective`, `in_target_area`, ...).
- You may add any other field from `python3 -m scout fields` that the page states.
- `photos` is required: the URL of every listing photo, full size, in the order the gallery shows them
  (up to 40). The order matters, because your rubric evidence will point at photos by number and the
  report shows those exact pictures. If the URLs truly cannot be read, give
  `photos_unavailable_reason` instead. Floor plans go in `floor_plans`, not in `photos`.
- No street number anywhere on the page: omit `address` and give `address_unknown_reason`. Do not
  work it out from photos or guess from cross streets.
- The page is gone, marked rented or off-market, or redirects to search results: hand in
  `{"id": "...", "source": "<source from the task>", "gone": true}`. Only that link is marked dead. If
  the home is listed elsewhere, the next task offers the other link.

## When the page says nothing

Judge first whether the listing is **thorough** (a real description plus an amenities or features list) or
**thin** (a few lines).

| Kind of fact | Thorough listing is silent | Thin listing is silent |
| --- | --- | --- |
| Selling points a listing always mentions if it has them: `elevator`, `doorman`, `package_room`, `dishwasher`, `laundry`, `outdoor`, `gym`, `parking`, `storage`, central or split `ac` | Record it as absent (`false`, `none`). Provenance: "thorough listing, not mentioned" | unknown |
| Unusual terms a listing always states if they apply: `furnished`, `sublet`, `short_term`, `room_share` | Record `false`. Same provenance | unknown |
| Things listings often leave out: `cats`, `dogs`, `tenant_pays_broker_fee`, `income_multiple_required`, `guarantor_accepted`, `rent_stabilized`, `security_deposit`, `application_fee`, `sqft`, `lease_term_months`, `listed_date`, whether window AC units are there | unknown | unknown |

So with no cooling mentioned on a thorough listing, `ac` is unknown (there may be window units), but it is
not `central` or `split`.

## How to read the tricky ones

**Address.** Street number, street and unit, then whatever locality the page gives: "318 East 9th Street #3C,
New York, NY 10003" or "95 Wythe Avenue #8F, Brooklyn, NY". Leave the neighborhood out of it; that goes in
`neighborhood`. If the page shows no zip, take it from the address line of the `map` lookup and say so in
provenance.

**Rent.** `rent_advertised` is the headline number. `rent_advertised_is` is `net_effective` if the
page labels it so (StreetEasy shows "net effective" next to the price), otherwise `gross`. Record
`free_months` and `lease_term_months` from the concession wording ("1 month free on a 13-month
lease" is 1 and 13). `free_months: 0` is right only when you read the whole page and found no
concession; say that in provenance. If you cannot tell, list it as unknown: the scout then treats the
other rent figure as unknown too, rather than assuming there is no concession.

**Pets.** `allowed` needs explicit words ("cats allowed", "pet friendly: cats and dogs").
"Pets upon approval", "case by case", "pets considered" are `case_by_case`. "No pets" is
`not_allowed`. Silence, or "ask about our pet policy", is unknown.

**Broker fee.** `tenant_pays_broker_fee` is true only if the page asks the tenant for a broker fee.
"No fee" is false. Record `broker_fee_amount` in dollars if it can be read or worked out from a
stated percentage of annual rent (say so in provenance). A site-wide notice that tenants cannot be charged
a broker fee (StreetEasy shows one on every NYC listing) is not the listing's terms: leave the fact unknown
unless the listing itself says.

**Floor and elevator.** Ground floor is 1. A unit number is a hint, not proof: "4B" with "fourth
floor" in the text is proof. `elevator: false` needs "walk-up" or a complete amenities list without
an elevator.

**Level.** `basement` (below the sidewalk), `garden` (partly below, or opens to a rear yard at the
lowest level), `ground`, `upper`.

**Commute.** Open the `directions` helper link, set it to arrive at the configured time, and take
the typical door-to-door minutes of the best route. Also record `commute_transfers`,
`walk_to_station_minutes` and `transit_lines` if shown. No address means unknown.

**Anything you looked up elsewhere** (commute, flood zone, noise, zip): give the lookup as provenance, with the
address-bar URL, e.g. `{"where": "Google Maps transit directions", "url": "https://..."}`. The report turns
it into a link, so the user can check it in one click.

**Flood zone.** Use a flood helper link. Outside any mapped flood area is `none`; the 0.2% annual
chance (500-year) area is `moderate`; the 1% annual chance (100-year) area is `high`.

**Noise.** On the `map` lookup (satellite, then drop into Street View from there), look within about a block for elevated tracks, highways,
bridge approaches, bars and clubs, firehouses and truck routes. List each by name in
`noise_sources`. An empty list means you looked and found none.

**Photo URLs.** Open the gallery and read the image addresses from the page (the `src`, or the largest
entry in `srcset`, of each gallery image). Take the large version, not the thumbnail. Skip agent
headshots, logos, maps and ads. `photo_count` is the number the listing has, even if that is over 40.

**Coordinates.** If the page's map or its link to a map shows latitude and longitude, put them in
`address.lat` and `address.lng`. They unlock the Street View link.

**Listed date.** `listed_date` is when the listing went up ("Listed 12 days ago" counts back from today).

**Photo age.** `photos_recency` is `stale` if there is a visible old date stamp, the season or
furniture conflicts between photos, the finishes differ from the description, or the same set
appears on an older listing for the building. `recent` needs some positive sign. Otherwise unknown.
A virtually staged photo shows furniture that is not there: say so in its caption and ask for the empty room
in `questions`. It is not evidence of size or layout.

**Scam signals.** Set `owner_remote`, `asks_deposit_before_viewing` or `photos_reused_elsewhere` to
true only when you saw it. Leave them out otherwise.

**Buildings with many units on one page** (common on Zillow). Pick the cheapest unit that meets the
bedroom minimum, record that unit's number in the address, and record that unit's rent and size.

## After you hand it in

The reply tells you the gate result. If `stage` is `needs_score`, the next task will be to score the
same listing, so keep the tab open. If it is `done`, close the tab and move on.
`"result": "merged"` means the address showed this is a unit already on file from another site.
