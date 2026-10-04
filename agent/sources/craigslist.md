# Sweeping Craigslist

Craigslist is where owner-direct, no-fee listings turn up, and where most scams do. It blocks fast
readers by IP address, so go slowly, in the user's normal browser. A "This IP has been automatically
blocked" page or an empty error page: stop and run
`python3 -m scout blocked craigslist "<what you saw>"`. Do not switch networks to get around it.

One task is one results page, handed in exactly as described in `agent/sources/streeteasy.md`.
Notes for Craigslist:

## Reading the page

1. Wait `wait_seconds`, then open the task's `url` (one borough, one price band, newest first).
2. Check the min and max price boxes show `price_min` and `price_max`.
3. For a `page` above 1 the task says how to get there in `reach_page`. Usually the tab is still on the
   page before, and it is one click on the forward arrow. If not, open the URL (page 1) and click
   forward, waiting `wait_between_clicks_seconds` before each click.
4. List view is quickest to read. `last_page` is true when the forward arrow is greyed out.
5. A normal page that says "no results" is not a block. Hand in `"cards": []` with `"last_page": true`.

## Cards

- `url` ends in `/<digits>.html`; the scout takes the id from there.
- `title` is the post title, exactly. Prefilters read it, and it is how reposts are spotted.
- `neighborhood` is the text in brackets after the title.
- Most posts have no address on the card. Leave `address` out.

## When opening a post (enrich)

- The attribute chips are reliable: "cats are OK - purrr" is cats `allowed`; "dogs are OK - wooof" is
  dogs `allowed`; "w/d in unit" is `in_unit`; "laundry in bldg" and "laundry on site" are
  `in_building`; "no laundry on site" is `none`; "furnished" is furnished. Copy the chips into
  `raw_text` so they can be quoted.
- The address, if any, is the line above the map. A cross street or a map pin is not an address.
- "This posting has expired" or "has been deleted by its author" means gone.
- Read for scam signals (`agent/ENRICH.md`). Never use the reply button.
