# GitHub Star Classifier with Jev

Classifies your starred GitHub repositories with TypeSafe Jev and organizes them into GitHub Lists through `gh api graphql`.

## Requirements

- Python 3.13+
- GitHub CLI (`gh`) authenticated with GitHub
- A TypeSafe API key in `TYPESAFE_API_KEY`
- `uv` (recommended) or another Python package manager

## Setup

```bash
uv sync
export TYPESAFE_API_KEY="..."
gh auth status
```

If GitHub list mutations later report insufficient authorization:

```bash
gh auth refresh -s user
```

## Safe first run

Dry-run is the default:

```bash
uv run python src/main.py
```

Test a small sample first:

```bash
uv run python src/main.py --limit 20
```

Or target one repository:

```bash
uv run python src/main.py --repo owner/repository
```

Nothing is created or changed without `--apply`.

## Apply

```bash
uv run python src/main.py --apply
```

To create any missing managed GitHub Lists as private:

```bash
uv run python src/main.py --apply --private-lists
```

## Reclassify existing managed stars

Normal runs skip repositories that already belong to one of the taxonomy lists. To classify them again:

```bash
uv run python src/main.py --reclassify
```

Preview first, then apply:

```bash
uv run python src/main.py --reclassify --apply
```

When reclassifying, the script replaces only memberships in its managed taxonomy lists. Memberships in unrelated/manual GitHub Lists are preserved.

## Confidence threshold

Default confidence threshold: `0.50`.

```bash
uv run python src/main.py --threshold 0.65
uv run python src/main.py --threshold 0.65 --apply
```

Results below the threshold are reported and left untouched.

## Taxonomy

> **Customize this before you run it.** The included taxonomy was built from one specific star collection (about 800 stars, mostly web frontend, React Native/Expo, PHP/Laravel/WordPress and a recent wave of AI coding agents). It is sized and shaped for that collection, so it probably will not fit yours. Before running on your own stars, look at what you actually star (languages, topics, descriptions), then split the lists that would be too big, merge the ones that would be nearly empty, and add the domains you care about that are missing.

Edit `CATEGORIES` near the top of `src/main.py`. The category dictionary key is the Jev `Choice` value; `name` is the GitHub List name managed by the script. Each category's `covers` / `not_for` criteria guide Jev between neighbouring categories, so rewrite them along with the names. GitHub allows at most 32 lists per account, including the ones you create by hand.

Tip: test a new taxonomy with a dry-run on a sample (`--limit 40` or several `--repo` flags) and look at how many results fall below `--threshold`. Lots of low-confidence results usually mean two categories overlap.

The included taxonomy is:

- React Native & Expo
- Native Mobile
- UI Components & Interactions
- CSS, Design & Icons
- Templates & Starters
- JS Tooling & Runtimes
- Backend & APIs
- PHP, Laravel & WordPress
- Data & Databases
- AI & Agents
- Editors, Terminal & Git
- Desktop & Browser Utilities
- Self-Hosted & Business Apps
- Security & Privacy
- Infra & DevOps
- Learning & Reference
- Media, Games & Fun
- Other

## Drop suggestions

While classifying, Jev also estimates whether each star is worth keeping (archived, deprecated, superseded, obsolete technology, duplicates...). Repositories at or above the drop threshold (default `0.70`) are collected and shown at the end of the run.

- Dry-run: the suggestions are printed; nothing is unstarred.
- `--apply`: an interactive checklist opens with every suggestion pre-selected. Toggle with space (`a` toggles all), validate with enter, then confirm. Only the selected repositories are unstarred.

```bash
uv run python src/main.py --drop-threshold 0.5          # more suggestions
uv run python src/main.py --reclassify --apply          # review drops for already-categorized stars too
```

Only repositories classified in the current run are evaluated, so already-categorized stars need `--reclassify` to get drop suggestions.

## Idempotency behavior

- Existing list names are matched case-insensitively before list creation.
- A missing list is checked again immediately before creation.
- Normal runs skip already categorized repositories.
- Applying the same classification twice does not duplicate membership.
- Reclassification removes old script-managed category memberships, adds the new one, and preserves manual memberships.
- List item and star retrieval are paginated.
