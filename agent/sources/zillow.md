# Sweeping Zillow

Use the user's normal, logged-in Chrome window. A "Press & Hold" page or access-denied page: stop
and run `python3 -m scout blocked zillow "<what you saw>"`.

## Steps

1. Wait `wait_seconds`, then open the task's `url`.
2. Check the Price filter shows `price_min` to `price_max`. Zillow sometimes ignores the values in the
   URL; if so, set them by hand.
3. Note the result count above the list (`total_results`).
4. Scroll the list panel to the bottom so every card on the page loads, then read each card.
5. Use the pager under the list, waiting `wait_between_pages_seconds` between pages. Start after
   `resume_after_page`. Stop at the last page or at `max_pages`.
6. Zillow stops serving new results after about 6 pages however many it says it found. If you reach
   `max_pages` and there are more, or later pages repeat earlier listings, hand in `"capped": true`.
   The scout splits the price band and asks again.

## Card

Same shape as `agent/sources/streeteasy.md`. Notes for Zillow:

- `url` is the card's link. Listing links contain `<digits>_zpid`; the scout takes the id from there.
- `address` is the full line, e.g. `245 E 10th St APT 4B, New York, NY 10003`.
- Building cards ("$3,200+ 1 bd", one card for many units) have no unit. Use the building address,
  the lowest price shown for an acceptable bedroom count, and put the price line in `snippet`.
  The unit is chosen when the page is opened.
