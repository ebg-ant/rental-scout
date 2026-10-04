# Sweeping Craigslist

Craigslist is where owner-direct, no-fee listings turn up, and where most scams do. It blocks fast
readers by IP address, so go slowly, in the user's normal browser. A "This IP has been automatically
blocked" page or an empty error page: stop and run
`python3 -m scout blocked craigslist "<what you saw>"`. Do not switch networks to get around it.

## Steps

1. Wait `wait_seconds`, then open the task's `url` (one borough, one price band, newest first).
2. Check the min and max price boxes show `price_min` and `price_max`.
3. Read each result into a card. List view is quickest to read.
4. Page on with the arrow at the top, waiting `wait_between_pages_seconds`. Stop at the last page or
   at `max_pages`; if there were more pages, hand in `"capped": true`.
5. Results are newest first. Once the reply to a page you handed in shows `"new": 0`, stop and hand
   in an empty `cards` list with `"stopped_early": true`. (So on Craigslist, hand in page by page with
   `"complete": false`.) Shortlisted posts that drop out of view get re-opened after a few days.

## Card

Same shape as `agent/sources/streeteasy.md`. Notes for Craigslist:

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
- Read for scam signals (`agent/ENRICH.md`). Never use the reply button.
