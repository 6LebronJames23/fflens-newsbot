# fflens-newsbot

The [@fflensapp](https://x.com/fflensapp) news bot for [League Lens](https://fflens.app), a free fantasy football league analyzer.

Every few minutes it reads Sleeper's public player data and posts the fantasy impact when a relevant NFL player's injury status changes (Out, IR, Doubtful, PUP, suspended), when a top player returns, or when someone becomes the most-added player on Sleeper. It also posts weekly roundups: most dropped (Monday), top waiver adds (Tuesday), top projected (Wednesday), dynasty market movers (Thursday), questionable stars (Friday) and Sunday inactives. Values come from FantasyCalc's public API.

- Posts only on its own timeline: no replies, mentions, likes or follows.
- At most 3 posts per run and 15 per day; the same news is never posted twice.
- Posts contain no links (X's API charges far more for posts with URLs).

Runs on GitHub Actions; credentials live in encrypted repository secrets. Dry run locally: `python3 newsbot.py --dry`
