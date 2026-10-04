# First run: the setup interview

Goal: turn a conversation into a valid `scout.config.yaml`. The user may not be technical and should
never need to open the file.

## How to run it

1. `cp configs/nyc.template.yaml scout.config.yaml` (skip if it exists; then only ask about TODOs
   and anything the user wants changed).
2. Ask the questions below in small groups, in plain English. Offer the default in brackets.
   Accept "don't care" and use the default.
3. Edit `scout.config.yaml` as you go. Keep the comments. Switch things off with `enabled: false`
   rather than deleting them.
4. `python3 -m scout validate`. Fix every error.
5. Read the plain-English summary it prints back to the user and ask "Is that right?". Adjust and
   repeat until they say yes.
6. Offer to do the first run now (`agent/RUNBOOK.md`). Suggest scheduling only after one good run
   by hand (`agent/SCHEDULED_TASK_PROMPT.md`).

Do not fill in anything the user did not tell you, apart from the defaults already in the template.

## Questions and where the answers go

**The basics**

| Ask | Goes to |
| --- | --- |
| What should I call you? | `profile.name` |
| What is the most you want to pay per month? | `gate.rules[rent].value` |
| Some listings offer a free month, so the advertised "net effective" rent is lower than what you pay each month. Is your limit about the average over the lease [default], or the actual monthly payment? | average: `field: rent_net_effective`. Payment: `field: rent_gross` |
| Fewest bedrooms? (0 = studio is fine) | `gate.rules[beds].value` |
| Latest move-in date? Earliest? | `profile.move_in_latest`, `profile.move_in_earliest` |

Then set `search.price_max` to the ceiling plus 10% (rounded up to the next 100) if they chose the
average, or exactly the ceiling if they chose the monthly payment. Set `search.price_min` to about
60% of the ceiling unless they name a floor: very cheap listings are mostly rooms and scams.

**Where**

| Ask | Goes to |
| --- | --- |
| Which neighborhoods? | `search.areas.neighborhoods`, spelled the way StreetEasy spells them. Add other spellings under `aliases` if missing |
| Is a listing just outside those an instant no, or just a mark against it [default]? | instant no: `dealbreaker: true` on `area` |
| Where do you commute to? (full address) What should I call it? | `search.commute_anchor` |
| Longest acceptable door-to-door commute by transit? [45] | `gate.rules[commute].value`. No commute: `enabled: false`, and set the `commute` rubric weight to 0, sharing it among the rest |

Set `craigslist.subareas` to the boroughs those neighborhoods are in.

**Pets**

| Ask | Goes to |
| --- | --- |
| Cat? Dog? Planning to get one? | `gate.rules[cats].enabled`, `gate.rules[dogs].enabled`, and `prefilters[no_pets].enabled` is true if either is |

**Building**

| Ask | Goes to |
| --- | --- |
| With no elevator, what is the highest floor you would climb to? [3] | `gate.rules[walkup].value` |
| Laundry: must be in the unit, in the building is fine [default], or do not care? | `gate.rules[laundry].value`, or `enabled: false` |
| Anything else that is an absolute must or an absolute no? | A new gate rule. `python3 -m scout fields` lists what can be tested (dishwasher, outdoor, doorman, level, ...). If nothing fits, add a `custom_fields` entry and a rule on it |

**What matters most**

Read out the six rubric criteria with their weights. Ask them to pick the one or two that matter
most and any they do not care about. Shift weight in steps of 0.05. Weights must add up to 1.0.
If they describe a taste the anchors do not capture ("I need a real kitchen"), add a criterion with
its own guidance and at least three concrete anchors, and take its weight from the others.

**Money checks (optional; stays on this computer)**

| Ask | Goes to |
| --- | --- |
| Landlords usually want yearly income of 40x the rent. Want me to flag listings where that is tight? If so, roughly what is your household income? | `profile.annual_income`, or leave null |
| Do you have a guarantor? | `profile.has_guarantor` |

**Searches**

Ask the user to do this in their own browser, logged in, and paste you the address-bar URLs:

- StreetEasy: Rentals, pick the neighborhoods, bedrooms, max price (use `search.price_max`), pets if
  relevant. Sort by newest. Copy the URL. It should start with `https://streeteasy.com/for-rent/`.
  Saving the search is optional but turns on email alerts.
- Zillow: For Rent, same filters, then copy the long URL containing `searchQueryState=`.
- Craigslist needs no URL.

A site they do not use: `enabled: false`.

**Pace**

Defaults are 10 listings per run, 20 seconds between pages, 60 minutes. Only lower the wait if the
user insists, and never below 5 seconds.
