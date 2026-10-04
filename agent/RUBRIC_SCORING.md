# Score: judge one listing against the rubric

The `score` task gives you the `criteria` to score (each with `guidance`, `anchors` and
`evidence_required`), the recorded `facts`, and `helper_links`. Only listings that passed the gate
get here.

## How to score

1. For each criterion, gather the evidence first, then place it on the anchors like this:
   - An anchor is met only if **every** part of it is met. Parts joined by "or" need just one.
   - Start from the highest anchor that is fully met. That is the score.
   - Add 1 if at least half the parts of the next anchor up are also met.
   - A part that begins "With two or more bedrooms" does not apply to a studio or one-bedroom: skip it.
   - A part you could not check counts as not met. If that is most of the criterion, score `null`.

   Anchors beat instinct: the point is that the same listing gets the same score tomorrow.
2. Score what is there, not what the listing claims. "Sun-drenched" is not evidence; a south-facing
   window over a low roof on the satellite view is.
3. If you cannot verify a criterion, score it `null` and say what you could not check and why. An
   honest unknown keeps the listing on WATCH with a question for the human. A guess can send them
   across town for nothing.
4. Do not let one criterion leak into another, and do not consider price: the gate and the ranking
   already handle it.

## Payload

```json
{
  "id": "<id from the task>",
  "headline": "One sentence a friend would say about this place.",
  "questions": ["Things only a human can ask, e.g. Is the roof deck open to all tenants?"],
  "criteria": {
    "light": {
      "score": 8,
      "rationale": "3rd floor rear, living room faces south over one-story extensions; nothing taller for ~80 ft.",
      "evidence": [
        {"type": "photos", "photos": [2, 3], "note": "long shadows across the floor"},
        {"type": "satellite", "ref": "https://www.google.com/maps/@40.7262,-73.9818,120m/data=...", "note": "low extensions to the south"}
      ]
    },
    "layout": {
      "score": 6,
      "rationale": "Bedroom fits a queen but has no closet.",
      "evidence": [{"type": "photos", "photos": [5, 6, 7]}],
      "unavailable": [{"type": "floor_plan", "reason": "listing has no floor plan"}]
    },
    "commute": {"score": null, "unknown_reason": "No address, so there is nothing to route from."}
  }
}
```

A full working example is `agent/examples/score.json`.

The task carries `listing_text` (the saved page text, to quote from) and `unknown` (facts enrich could not
find), so you can score after a dropped session without re-reading the page.

## Rules the command enforces

- Every criterion in the task must be present.
- A number needs a one-line `rationale` (under 300 characters) and at least one piece of evidence.
- `null` needs an `unknown_reason`.
- Each type in `evidence_required` must be in `evidence`, or listed under `unavailable` with a
  reason. The report marks those scores as resting on limited evidence.
- Evidence is there so the human can check your work in one click. By type:

| type | what to give |
| --- | --- |
| `photos` | `"photos": [2, 3]`: numbers from the list saved by enrich (`photos_saved` in the task says how many). The report shows those pictures beside your rationale. Use `"ref": "all 14 photos"` only for a claim about all of them, such as "no outdoor space shown" |
| `floor_plan` | nothing more if a plan was saved (`floor_plans_saved` above 0); otherwise a `ref` |
| `satellite`, `street_view`, `map` | `ref` must be a URL: copy the address bar while you are looking at the exact view, so the link reopens that spot, zoom and angle. For directions, copy it after setting the arrival time |
| `text` | `quote`, word for word from the saved listing text. The report links it to those words on the listing page |
| `records` | `ref`: the URL of the building's record page if it has one, otherwise what you searched and where |

## Light and exposure: the method

This carries the most weight and listings are least honest about it.

1. Open the `map` helper link and switch to satellite. Find the building and its footprint.
2. Work out which side the unit is on: front or rear from the description or unit letter, the floor
   plan's compass or street label, and what the windows look out on in the photos.
3. Name what stands on that side, how tall it is compared with this floor, and roughly how far away.
   Count stories in Street View.
4. Watch for lot-line windows (a side wall facing an empty or low lot that could be built on),
   airshafts and enclosed courts. Note scaffolding or a construction site next door.
5. No address means `null`. Do not estimate light from how bright the photos are.
