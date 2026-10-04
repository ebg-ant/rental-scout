# Reading saved-search alert emails

Optional and cheap: StreetEasy and Zillow email new matches for a saved search. Needs the user's
Gmail connector, read-only. Turn it on with `search.sources.email_alerts.enabled: true`.

## Steps

1. Search the inbox for alerts since the last run, e.g.
   `from:(streeteasy.com OR zillow.com) newer_than:2d`.
2. Turn each listing in each email into a card, in the same shape as
   `agent/sources/streeteasy.md`: address, rent, beds, baths, neighborhood if shown, and the link.
3. Hand them in with the task's `sweep` value and `"complete": true`.

## Notes

- Do not open the links at this stage. Emails are only for discovery; the page is read later, if the
  listing gets that far.
- Links in emails are often tracking links. Use the plain listing URL if the email shows one.
  Otherwise use the link as it is: the address is what matches the listing to one already on file.
- Skip entries with no address and no plain listing URL.
- Never send, draft, label, archive or delete anything.
