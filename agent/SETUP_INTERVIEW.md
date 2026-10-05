# First run: the setup interview

Goal: turn a conversation into a valid `scout.config.yaml`. The user may not be technical and should
never need to open the file.

## Before you start

Check the package works on this computer. Run these from the folder that contains `README.md`:

```
python3 --version                                # 3.8 or newer
python3 -m unittest discover -s tests -t .       # a minute or two; the last line should be OK
```

If Python is missing, tell the user how to install it for their system and stop until it is there. If a
test fails, tell the user which one; do not try to repair the package.

Also confirm you can control the user's Chrome, and ask them to make sure it is logged in to StreetEasy
and Zillow. You never log in for them.

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

Then set `search.price_max` to the ceiling plus 10% (if that is not a multiple of 100, round it up to one) if
they chose the average, or exactly the ceiling if they chose the monthly payment. Set `search.price_min` to about
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
| Cat? Dog? Planning to get one? | `gate.rules[cats].enabled`, `gate.rules[dogs].enabled`, and `prefilters[no_pets].enabled` is true if either is. On Craigslist also add `pets_cat: 1` or `pets_dog: 1` under `params` |
| (if yes) Many listings say pets are "upon approval" or "case by case". Rule those out [default], or keep them as a maybe for you to ask about? | rule out: leave as is. Maybe: `dealbreaker: false` on that pet's rule, so it is a soft miss and lands on WATCH |

**Building**

| Ask | Goes to |
| --- | --- |
| With no elevator, what is the highest floor you would climb to? [3] | `gate.rules[walkup].value` |
| Laundry: must be in the unit, in the building is fine [default], or do not care? | `gate.rules[laundry].value`, or `enabled: false` |
| (if in the unit) Is a listing without it an instant no, or a mark against it [default]? | instant no: `dealbreaker: true` on `laundry` |
| Anything else that is an absolute must or an absolute no? | A new gate rule (example below). `python3 -m scout fields` lists what can be tested (dishwasher, outdoor, doorman, level, ...). If nothing fits, add a `custom_fields` entry and a rule on it |

A new rule looks like this. "Absolute" means `dealbreaker: true`; "I would really like" means leave that line
out. Use `on_unknown: flag` unless they say otherwise: a listing that does not say is kept on WATCH with the
question, rather than thrown away or waved through.

```yaml
    - id: dishwasher
      label: Dishwasher
      field: dishwasher
      op: is
      value: true
      on_unknown: flag
      dealbreaker: true
```

**What matters most**

Read out the six rubric criteria with their weights. Ask them to pick the one or two that matter
most and any they do not care about. Then, in this order:

1. "Do not care at all": set it to 0. "Do not care much": halve it, rounding down to a multiple of 0.05.
2. Share what that freed equally among the ones that "matter most", in steps of 0.05. If nothing was freed,
   give each of them 0.05, taken from the largest of the criteria they did not mention.
3. Check the weights add up to 1.0, and read the new percentages back.

For example, "light matters most, outdoor space not much": outdoor 0.10 becomes 0.05 and light 0.25 becomes 0.30.
If they describe a taste the anchors do not capture ("I need a real kitchen"), add a criterion with
its own guidance and at least three concrete anchors, and take its weight from the others.

**Money checks (optional; stays on this computer)**

| Ask | Goes to |
| --- | --- |
| Landlords usually want yearly income of 40x the rent. Want me to flag listings where that is tight? If so, roughly what is your household income? | `profile.annual_income`, or leave null |
| Do you have a guarantor? | `profile.has_guarantor` |

**Searches**

Ask the user to do this in their own browser, logged in, and paste you the address-bar URLs:

- StreetEasy: Rentals, pick the neighborhoods, bedrooms, and pets if relevant. Copy the URL. It should start
  with `https://streeteasy.com/for-rent/`. Saving the search is optional but turns on email alerts.
- Zillow: For Rent, bedrooms and pets, and zoom or draw the map so it covers only their neighborhoods. Then copy
  the long URL containing `searchQueryState=`.
- Craigslist needs no URL. Set `params.min_bedrooms` to their minimum.

Put every instant-no must-have that a site can filter on into that site's search as well: washer/dryer in
the unit, elevator, dishwasher (StreetEasy's Amenities menu, Zillow's More filters). Search cards do not show
these, so without the filter every listing that lacks them costs a page open before the gate can reject it.

Zillow searches are map rectangles. An area that sits at an angle to the map (most of Manhattan) picks up the
far shore in one box; use two or more boxes, one URL each.

Price and sort order do not matter: the scout sets both itself. **The area does matter:** the scout cannot add
it, and a search of the whole city would take many runs to read. Look at each URL or open it, and check it is
limited to their neighborhoods before you accept it. Put each URL in "quotes" in the config.

A site they do not use: `enabled: false`.

**Pace and freshness**

Defaults per run: 10 listings opened in all, 6 results pages read on each site, 20 seconds between pages on a
site, 60 minutes. Every
page of every search about once a week, newest-first otherwise. TOURs re-checked after 2 days unseen,
WATCHes after 4. Mention these; change them only if asked. Never set the wait below 5 seconds.

Open each search once and add up the result counts. If the total is many times the listings opened per run,
say how many runs the first pass will take (300 listings at 10 a run is a month) and offer to narrow the
searches before raising the limit.

Tell the user what to expect from the first few runs: with a broad search, reading every page the first
time can take two or three runs. The newest listings are read and judged first, so the shortlist is
useful from day one and fills in behind.
