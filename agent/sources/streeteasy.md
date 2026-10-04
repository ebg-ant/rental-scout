# Sweeping StreetEasy

StreetEasy has strong bot detection. Use the user's normal, logged-in Chrome window, one tab for this site, at
a human pace. Any "Press & Hold", "unusual activity" or access-denied page: stop and run
`python3 -m scout blocked streeteasy "<what you saw>"`.

## One task is one results page

1. Wait `wait_seconds`, then open the task's `url`. It already points at the right `page`.
2. On page 1, check the page shows the price range `price_min` to `price_max` and is sorted by
   **Newest**. If `filters` says they are not in the URL, or the page shows something else, set them
   by hand.
3. Note the total result count at the top of the list (`total_results`). Leave it out if you are not sure
   the number counts the same thing as the cards.
4. Read every rental card on the page into a card (below). "Featured" and "Sponsored" cards are real
   listings: include them. Skip only ads with no price or address.
5. Hand the page in. Set `last_page` to true if the pager has no next page.
6. Run `python3 -m scout next`. The scout decides whether to read the next page, stop, or split the
   search into narrower price bands. Do not decide that yourself.

## What to hand in

```json
{
  "sweep": "<sweep from the task>",
  "page": 1,
  "last_page": false,
  "total_results": 212,
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

- `page` is the number from the task. Pages are handed in one at a time, in order.
- Numbers are plain numbers: `3950`, not `"$3,950"`. Studio is `"beds": 0`. Leave out what the card
  does not show.
- `rent` is the number the card shows. If the card says it is net effective, put that in `snippet`.
- `url` must be the listing's own page, not the search page. `first_photo` is the address of the card's
  image; it becomes the thumbnail in the report and helps spot reposts.

## The two kinds of sweep

The task's `mode` tells you which this is. You read the page the same way either way.

- `incremental`: the daily top-up. Newest first; the scout stops at the first page with nothing new.
- `full`: every page, on the first run and about once a week. It is how price changes on older
  listings and listings that have gone are noticed. A long one is spread over several runs.
