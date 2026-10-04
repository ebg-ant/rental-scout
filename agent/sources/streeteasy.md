# Sweeping StreetEasy

StreetEasy has strong bot detection. Use the user's normal, logged-in Chrome window, one tab, at a
human pace. Any "Press & Hold", "unusual activity" or access-denied page: stop and run
`python3 -m scout blocked streeteasy "<what you saw>"`.

## Steps

1. Wait `wait_seconds`, then open the task's `url`.
2. Check the price filter on the page shows `price_min` to `price_max`. If `price_filter` says it is
   not in the URL, or the page shows something else, set it by hand in the Price filter.
3. Note the total result count at the top of the list (`total_results`).
4. Read every rental card on the page into a card (below). Skip ads for other buildings only if
   they have no price or address. "Featured" and "Sponsored" cards are real listings: include them.
5. Go to the next page with the pager at the bottom, waiting `wait_between_pages_seconds` first.
   Start after `resume_after_page` if it is above 0. Stop at the last page or at `max_pages`.
6. Hand in the cards. For a long sweep, hand in after each page with `"complete": false` so nothing
   is lost if the session drops.

## Card

```json
{
  "sweep": "<sweep from the task>",
  "total_results": 212,
  "pages_read": 3,
  "complete": true,
  "capped": false,
  "cards": [
    {
      "url": "https://streeteasy.com/building/245-east-10-street-new_york/4b",
      "title": "245 East 10th Street #4B",
      "address": "245 East 10th Street #4B",
      "neighborhood": "East Village",
      "rent": 3950, "beds": 1, "baths": 1, "sqft": 650,
      "snippet": "any badge or blurb text on the card, e.g. No fee. 1 month free. Net effective.",
      "first_photo": "https://..."
    }
  ]
}
```

- Numbers are plain numbers: `3950`, not `"$3,950"`. Studio is `"beds": 0`. Leave out what the card
  does not show.
- `rent` is the number the card shows. If the card says it is net effective, put that in `snippet`.
- `url` must be the listing's own page, not the search page. `first_photo` is the address of the card's
  image; it becomes the thumbnail in the report and helps spot reposts.
- `capped: true` means you reached `max_pages` and there were more pages. The scout then splits the
  price band in two and asks you to sweep each half, so nothing is missed.
- `complete: true` means you reached the last page of results (or were capped).
