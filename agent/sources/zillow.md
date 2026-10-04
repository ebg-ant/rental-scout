# Sweeping Zillow

Use the user's normal, logged-in Chrome window. A "Press & Hold" page or access-denied page: stop
and run `python3 -m scout blocked zillow "<what you saw>"`.

One task is one results page, handed in exactly as described in `agent/sources/streeteasy.md`.
Notes for Zillow:

## Reading the page

1. Wait `wait_seconds`, then open the task's `url`.
2. On page 1, check the Price filter shows `price_min` to `price_max` and the sort is **Newest**.
   Zillow sometimes ignores the values in the URL; if so, set them by hand.
3. Note the result count above the list (`total_results`). The scout uses it to split a search that is
   too big for Zillow to page through, which stops serving new results after about 6 pages.
4. Scroll the list panel to the bottom so every card on the page loads, then read each card.
5. `last_page` is true if the pager under the list has no next page.

## Cards

- `url` is the card's link. Listing links contain `<digits>_zpid`; the scout takes the id from there.
- `address` is the full line, e.g. `245 E 10th St APT 4B, New York, NY 10003`.
- Building cards ("$3,200+ 1 bd", one card for many units) have no unit. Use the building address,
  the lowest price shown for an acceptable bedroom count, and put the price line in `snippet`.
  The unit is chosen when the page is opened.
