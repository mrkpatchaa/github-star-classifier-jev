#!/usr/bin/env python3
"""Classify GitHub stars with Jev and organize them into GitHub Lists.

Safety / idempotency rules:
- Dry-run is the default. Nothing is mutated without --apply.
- Existing GitHub Lists are matched case-insensitively before creating anything.
- Lists whose names appear in CATEGORIES are considered script-managed.
- Normal runs skip repositories already in a managed category.
- --reclassify may move a repository between managed categories, but preserves
  every unrelated/manual list membership.
- Low-confidence classifications are skipped.
- Jev also flags stars that look worth dropping. With --apply they are shown in
  an interactive checklist; nothing is unstarred until the selection is confirmed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any, Iterable

import questionary
from typesafe_sdk import Choice, Noul, TypeSafeClient


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

# This taxonomy was built from the author's own star collection; customize it
# to fit yours (see README "Taxonomy"). The dict key is the Jev
# Choice value; "name" is the exact GitHub List name the script manages.
CATEGORIES: dict[str, dict[str, Any]] = {
    "react_native_expo": {
        "name": "React Native & Expo",
        "description": "React Native and Expo libraries, config plugins, native modules and RN tooling.",
        "criteria": {
            "covers": "Libraries, components, native modules, config plugins, OTA/update tooling, or dev tools built specifically for React Native or Expo.",
            "not_for": "Boilerplates and starter apps (Templates & Starters), native-only iOS/Android/Flutter code (Native Mobile), or React web libraries that merely also support React Native.",
        },
    },
    "native_mobile": {
        "name": "Native Mobile",
        "description": "Native Android and iOS development, Flutter, and other non-React-Native mobile tech.",
        "criteria": {
            "covers": "Android (Kotlin/Java) or iOS (Swift/SwiftUI/Objective-C) libraries and apps, Flutter/Dart mobile, PhoneGap/Cordova, and mobile SDKs that are not React Native.",
            "not_for": "React Native or Expo projects, macOS desktop utilities, or responsive web projects.",
        },
    },
    "ui_components": {
        "name": "UI Components & Interactions",
        "description": "JavaScript/React widgets, drag and drop, forms, animation, charts and embeddable editors.",
        "criteria": {
            "covers": "Front-end code that ships JavaScript behaviour for the browser: UI components, jQuery plugins, drag and drop, sliders, pickers, forms, animation, charts/data-viz components, embeddable rich-text or code editors, state management.",
            "not_for": "Pure CSS/style/icon/font projects (CSS, Design & Icons), full page templates (Templates & Starters), React Native components, or build/test tooling.",
        },
    },
    "css_design": {
        "name": "CSS, Design & Icons",
        "description": "CSS frameworks, icon sets, fonts, themes, design systems and CSS art.",
        "criteria": {
            "covers": "Projects whose main output is styling or visual assets: CSS frameworks and utilities, icon packs, typefaces, color tools, editor/OS/UI themes, design kits, CSS drawings and effects.",
            "not_for": "Components that ship meaningful JavaScript behaviour (UI Components & Interactions) or complete page/site templates (Templates & Starters).",
        },
    },
    "templates_starters": {
        "name": "Templates & Starters",
        "description": "Boilerplates, starter kits, admin dashboards, landing, blog and site templates.",
        "criteria": {
            "covers": "Something you copy or clone to start a new project: boilerplates, seed projects, starter kits for any stack (web, React Native, Electron, Laravel...), HTML/Jekyll/WordPress themes, admin dashboard templates, landing page templates.",
            "not_for": "Frameworks or libraries you install as a dependency, or example apps meant for learning (Learning & Reference).",
        },
    },
    "js_tooling": {
        "name": "JS Tooling & Runtimes",
        "description": "JavaScript/TypeScript build, test, lint and codemod tools, runtimes and small utility libraries.",
        "criteria": {
            "covers": "Tools and low-level libraries of the JavaScript ecosystem: bundlers, test frameworks, linters/formatters, transpilers and codemods, Node.js and JS engines, language-level utility libraries.",
            "not_for": "Browser UI components, React Native-specific tooling, backend web frameworks, or general-purpose editors/terminals/git tools.",
        },
    },
    "backend_apis": {
        "name": "Backend & APIs",
        "description": "Server frameworks, GraphQL/REST tooling, auth, real-time and backend-as-a-service platforms.",
        "criteria": {
            "covers": "Building the server side of applications in any language except PHP: web/API frameworks, GraphQL, REST, WebSockets/real-time, authentication/OAuth libraries, job queues, headless CMSs and backend-as-a-service platforms (Supabase, Parse, nhost).",
            "not_for": "PHP/Laravel/WordPress projects, databases and storage engines (Data & Databases), or deployment infrastructure (Infra & DevOps).",
        },
    },
    "php_ecosystem": {
        "name": "PHP, Laravel & WordPress",
        "description": "PHP libraries, Laravel and Symfony packages, WordPress plugins and PHP tooling.",
        "criteria": {
            "covers": "Anything whose primary ecosystem is PHP: Laravel/Symfony/CodeIgniter packages, WordPress plugins and tooling, PHP libraries, testing and code-quality tools for PHP.",
            "not_for": "Complete end-user applications that happen to be written in PHP (Self-Hosted & Business Apps) or WordPress/HTML themes used as starting points (Templates & Starters).",
        },
    },
    "data_databases": {
        "name": "Data & Databases",
        "description": "Databases, Postgres extensions, storage engines, search, ID generators and data tools.",
        "criteria": {
            "covers": "Databases and storage engines, Postgres extensions, SQL tooling, search engines, unique ID schemes (UUID/ULID/nanoid/ksuid), data formats, data processing and data exploration tools.",
            "not_for": "Backend-as-a-service platforms (Backend & APIs), front-end chart components (UI Components & Interactions), or ML frameworks (AI & Agents).",
        },
    },
    "ai_agents": {
        "name": "AI & Agents",
        "description": "Machine learning frameworks, LLM tooling, AI coding agents, Claude Code/Codex skills and plugins.",
        "criteria": {
            "covers": "AI/ML is the primary purpose: ML and deep-learning frameworks, models and inference, computer vision, LLM apps, AI agents and agent orchestration, skills/plugins/prompts for Claude Code, Codex or other coding agents.",
            "not_for": "Ordinary apps or tools that merely include an AI feature, or ML learning material (Learning & Reference).",
        },
    },
    "dev_environment": {
        "name": "Editors, Terminal & Git",
        "description": "Code editors and IDEs, VS Code extensions, shells, terminal and CLI utilities, git tools.",
        "criteria": {
            "covers": "The developer's working environment: editors and IDEs, VS Code extensions/configs, shells and zsh plugins, terminal emulators, general CLI utilities, git and GitHub tools, dotfiles, coding fonts setup.",
            "not_for": "JavaScript-specific build/test tooling (JS Tooling & Runtimes), CI/CD (Infra & DevOps), or AI coding agents (AI & Agents).",
        },
    },
    "desktop_browser": {
        "name": "Desktop & Browser Utilities",
        "description": "macOS/Windows/Linux utilities, desktop apps, browser extensions and personal productivity tools.",
        "criteria": {
            "covers": "Software that runs on your own computer or browser: macOS menu-bar and system utilities, Windows/Linux desktop tools, browsers, browser extensions and userscripts, personal productivity apps (notes, timers, snippets).",
            "not_for": "Server applications you self-host (Self-Hosted & Business Apps), developer editors/terminals (Editors, Terminal & Git), or privacy/ad-blocking tools (Security & Privacy).",
        },
    },
    "self_hosted_apps": {
        "name": "Self-Hosted & Business Apps",
        "description": "Complete server applications: self-hosted services, e-commerce platforms, CRMs and management systems.",
        "criteria": {
            "covers": "Complete applications deployed on a server: self-hosted services (bookmarks, photos, recipes, newsletters, file sharing), e-commerce platforms, CRMs, forums, status pages, school/hotel/gym/hospital management systems.",
            "not_for": "Libraries or frameworks used to build such apps, starter templates, or infrastructure components like databases and proxies.",
        },
    },
    "security_privacy": {
        "name": "Security & Privacy",
        "description": "Security scanners, password managers, encryption, VPNs, ad-blocking and anti-tracking.",
        "criteria": {
            "covers": "Security or privacy is the primary purpose: vulnerability scanners, pentest/offensive research tools, password managers, encryption, secure transfer, VPNs and proxies for privacy, ad/tracker blocking, hardening guides' tooling.",
            "not_for": "General auth libraries (Backend & APIs) or security checklists and reading lists (Learning & Reference).",
        },
    },
    "infra_devops": {
        "name": "Infra & DevOps",
        "description": "Docker, CI/CD and GitHub Actions, cloud, servers, networking and sysadmin tooling.",
        "criteria": {
            "covers": "Running and shipping software: containers, CI/CD and GitHub Actions, deployment, cloud platforms, server administration, nginx, monitoring/observability, networking tools, chaos engineering, backup/sync tools like rclone.",
            "not_for": "Self-hosted end-user applications (Self-Hosted & Business Apps) or databases (Data & Databases).",
        },
    },
    "learning_reference": {
        "name": "Learning & Reference",
        "description": "Awesome lists, books, courses, interview prep, system design, cheatsheets and guides.",
        "criteria": {
            "covers": "The repository primarily teaches, documents, or curates rather than shipping software: awesome lists, free books, courses and tutorials, interview questions, system-design material, cheatsheets, style guides, architecture examples, code from books.",
            "not_for": "Production libraries or applications that merely have good documentation or examples.",
        },
    },
    "media_fun": {
        "name": "Media, Games & Fun",
        "description": "Media downloaders and players, games, creative coding, retro computing and joke projects.",
        "criteria": {
            "covers": "Audio/video/music tools and players, media downloaders, games and game engines, creative coding and pixel art, retro computing/emulation, hardware toys, humorous or novelty projects.",
            "not_for": "Serious developer libraries that happen to deal with images or media inside another category's domain.",
        },
    },
    "other": {
        "name": "Other",
        "description": "Repositories that do not clearly fit any other managed category.",
        "criteria": {
            "covers": "Use when none of the other categories describes the repository's primary purpose well.",
            "not_for": "Do not use as a fallback merely because two categories are close; choose the category that best captures the primary purpose when possible.",
        },
    },
}

CATEGORY_CRITERIA = {
    key: value["criteria"]
    for key, value in CATEGORIES.items()
}

DEFAULT_CONFIDENCE_THRESHOLD = 0.50
DEFAULT_DROP_THRESHOLD = 0.70

DROP_CRITERIA = {
    "true": (
        "The star is no longer worth keeping: the project is archived, deprecated or "
        "unmaintained for years; it points to a successor or has been superseded by a "
        "well-known alternative; it targets obsolete technology (e.g. jQuery-era plugins, "
        "PhoneGap, CoffeeScript tooling, Angular 1); it is a stranger's personal, course or "
        "homework project with little reuse value; or it duplicates another star (mirror, "
        "old copy, fork of an original)."
    ),
    "false": (
        "The star is still worth keeping: the project is maintained or still widely used, "
        "it is a lasting reference or learning resource, or it is a notable/historical "
        "project whose value does not depend on recent activity."
    ),
}


# ---------------------------------------------------------------------------
# GitHub CLI helpers
# ---------------------------------------------------------------------------

class GitHubError(RuntimeError):
    pass


def run_gh(*args: str) -> dict[str, Any]:
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        details = proc.stderr.strip() or proc.stdout.strip() or "unknown gh error"
        raise GitHubError(details)
    if not proc.stdout.strip():
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise GitHubError(f"gh returned invalid JSON: {exc}") from exc


def graphql(
    query: str,
    *,
    variables: dict[str, Any] | None = None,
    array_variables: dict[str, Iterable[str]] | None = None,
) -> dict[str, Any]:
    args = ["api", "graphql", "-f", f"query={query}"]

    for key, value in (variables or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value)
        args.extend(["-F", f"{key}={rendered}"])

    for key, values in (array_variables or {}).items():
        for value in values:
            args.extend(["-F", f"{key}[]={value}"])

    return run_gh(*args)


LISTS_QUERY = """
query($cursor: String) {
  viewer {
    lists(first: 100, after: $cursor) {
      nodes {
        id
        name
        description
        isPrivate
        slug
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""


def get_user_lists() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        data = graphql(LISTS_QUERY, variables={"cursor": cursor})
        connection = data["data"]["viewer"]["lists"]
        result.extend(connection["nodes"])
        if not connection["pageInfo"]["hasNextPage"]:
            return result
        cursor = connection["pageInfo"]["endCursor"]


STARS_QUERY = """
query($cursor: String) {
  viewer {
    starredRepositories(first: 100, after: $cursor) {
      nodes {
        id
        nameWithOwner
        description
        url
        isArchived
        isFork
        pushedAt
        stargazerCount
        primaryLanguage { name }
        repositoryTopics(first: 30) {
          nodes { topic { name } }
        }
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""


def get_starred_repositories() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        data = graphql(STARS_QUERY, variables={"cursor": cursor})
        connection = data["data"]["viewer"]["starredRepositories"]
        result.extend(connection["nodes"])
        if not connection["pageInfo"]["hasNextPage"]:
            return result
        cursor = connection["pageInfo"]["endCursor"]


LIST_ITEMS_QUERY = """
query($listId: ID!, $cursor: String) {
  node(id: $listId) {
    ... on UserList {
      items(first: 100, after: $cursor) {
        nodes {
          ... on Repository {
            id
            nameWithOwner
          }
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
  }
}
"""


def get_list_items(list_id: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    cursor: str | None = None

    while True:
        data = graphql(
            LIST_ITEMS_QUERY,
            variables={"listId": list_id, "cursor": cursor},
        )
        node = data["data"]["node"]
        if node is None:
            raise GitHubError(f"GitHub list no longer exists: {list_id}")
        connection = node["items"]
        result.extend(item for item in connection["nodes"] if item)
        if not connection["pageInfo"]["hasNextPage"]:
            return result
        cursor = connection["pageInfo"]["endCursor"]


CREATE_LIST_MUTATION = """
mutation($name: String!, $description: String, $isPrivate: Boolean) {
  createUserList(
    input: {
      name: $name
      description: $description
      isPrivate: $isPrivate
    }
  ) {
    list {
      id
      name
      description
      isPrivate
      slug
    }
  }
}
"""


def create_list(name: str, description: str, is_private: bool) -> dict[str, Any]:
    data = graphql(
        CREATE_LIST_MUTATION,
        variables={
            "name": name,
            "description": description,
            "isPrivate": is_private,
        },
    )
    return data["data"]["createUserList"]["list"]


UPDATE_MEMBERSHIPS_MUTATION = """
mutation($itemId: ID!, $listIds: [ID!]!) {
  updateUserListsForItem(
    input: {
      itemId: $itemId
      listIds: $listIds
    }
  ) {
    lists { id name }
  }
}
"""


def set_repository_memberships(repo_id: str, list_ids: set[str]) -> None:
    graphql(
        UPDATE_MEMBERSHIPS_MUTATION,
        variables={"itemId": repo_id},
        array_variables={"listIds": sorted(list_ids)},
    )


REMOVE_STAR_MUTATION = """
mutation($starrableId: ID!) {
  removeStar(input: { starrableId: $starrableId }) {
    starrable { id }
  }
}
"""


def unstar_repository(repo_id: str) -> None:
    graphql(REMOVE_STAR_MUTATION, variables={"starrableId": repo_id})


# ---------------------------------------------------------------------------
# List registry and memberships
# ---------------------------------------------------------------------------

def normalize_name(value: str) -> str:
    return " ".join(value.split()).casefold()


MANAGED_NAMES = {
    normalize_name(category["name"])
    for category in CATEGORIES.values()
}


class ListRegistry:
    def __init__(self, lists: list[dict[str, Any]]) -> None:
        self.by_id: dict[str, dict[str, Any]] = {}
        self.by_name: dict[str, dict[str, Any]] = {}
        self.planned_names: set[str] = set()
        self.created = 0
        self._merge(lists)

    def _merge(self, lists: list[dict[str, Any]]) -> None:
        for item in lists:
            normalized = normalize_name(item["name"])
            previous = self.by_name.get(normalized)
            if previous and previous["id"] != item["id"]:
                raise GitHubError(
                    "More than one GitHub List has the same normalized name "
                    f"({item['name']!r}). Rename one before running this script."
                )
            self.by_id[item["id"]] = item
            self.by_name[normalized] = item

    def get(self, name: str) -> dict[str, Any] | None:
        return self.by_name.get(normalize_name(name))

    def managed_ids(self) -> set[str]:
        return {
            item["id"]
            for normalized, item in self.by_name.items()
            if normalized in MANAGED_NAMES
        }

    def ensure(
        self,
        *,
        name: str,
        description: str,
        is_private: bool,
        apply: bool,
    ) -> dict[str, Any] | None:
        existing = self.get(name)
        if existing:
            return existing

        normalized = normalize_name(name)

        if not apply:
            if normalized not in self.planned_names:
                print(f"    list: would create {name!r}")
                self.planned_names.add(normalized)
            return None

        # Double-check immediately before creation. This reduces the chance of
        # creating a duplicate if another run/process created it after startup.
        self._merge(get_user_lists())
        existing = self.get(name)
        if existing:
            print(f"    list: found after refresh {name!r}")
            return existing

        print(f"    list: creating {name!r}")
        created = create_list(name, description, is_private)
        self._merge([created])
        self.created += 1
        return created

    def names_for_ids(self, ids: set[str]) -> list[str]:
        names: list[str] = []
        for list_id in ids:
            item = self.by_id.get(list_id)
            names.append(item["name"] if item else list_id)
        return sorted(names, key=str.casefold)


def load_repository_memberships(
    lists: list[dict[str, Any]],
) -> dict[str, set[str]]:
    memberships: dict[str, set[str]] = defaultdict(set)

    for index, user_list in enumerate(lists, start=1):
        print(f"  memberships [{index}/{len(lists)}]: {user_list['name']}")
        for repo in get_list_items(user_list["id"]):
            memberships[repo["id"]].add(user_list["id"])

    return dict(memberships)


# ---------------------------------------------------------------------------
# Jev classification
# ---------------------------------------------------------------------------

def days_since(timestamp: str | None) -> int | None:
    if not timestamp:
        return None
    pushed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return (datetime.now(UTC) - pushed).days


def build_state(repo: dict[str, Any]) -> str:
    topics = [
        node["topic"]["name"]
        for node in repo["repositoryTopics"]["nodes"]
    ]
    primary_language = repo["primaryLanguage"]["name"] if repo["primaryLanguage"] else None

    return json.dumps(
        {
            "repository": repo["nameWithOwner"],
            "description": repo["description"],
            "primary_language": primary_language,
            "topics": topics,
            "is_fork": repo["isFork"],
            "is_archived": repo["isArchived"],
            "last_pushed": repo["pushedAt"],
            "days_since_last_push": days_since(repo["pushedAt"]),
            "stargazers": repo["stargazerCount"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def classify_repository(client: TypeSafeClient, repo: dict[str, Any]):
    response = client.system_one(
        state=build_state(repo),
        questions={
            "category": Choice(
                instructions=(
                    "Choose the single category that best represents the primary purpose "
                    "of this GitHub repository. Prefer what the repository fundamentally "
                    "is over incidental features or implementation details. Use 'other' "
                    "only when no category fits well."
                ),
                criteria=CATEGORY_CRITERIA,
            ),
            "drop": Noul(
                instructions=(
                    "Should the owner of this star list unstar this repository? Judge from "
                    "the description, archive status, time since the last push, and whether "
                    "the technology is obsolete. A long-inactive but still classic or "
                    "reference project should be kept."
                ),
                criteria=DROP_CRITERIA,
            ),
        },
    )
    return response.answers["category"], response.answers["drop"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def probability(value: str) -> float:
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify GitHub stars with Jev and organize them into GitHub Lists.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually create lists and update memberships. Default is dry-run.",
    )
    parser.add_argument(
        "--reclassify",
        action="store_true",
        help="Re-run Jev for repos already in a managed category and move them if needed.",
    )
    parser.add_argument(
        "--threshold",
        type=probability,
        default=DEFAULT_CONFIDENCE_THRESHOLD,
        metavar="0..1",
        help=f"Minimum Jev confidence required to act (default: {DEFAULT_CONFIDENCE_THRESHOLD:.2f}).",
    )
    parser.add_argument(
        "--drop-threshold",
        type=probability,
        default=DEFAULT_DROP_THRESHOLD,
        metavar="0..1",
        help=f"Minimum Jev drop probability to suggest unstarring (default: {DEFAULT_DROP_THRESHOLD:.2f}).",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        help="Process at most N selected starred repositories.",
    )
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="OWNER/REPO",
        help="Only process this repository. Repeat the flag for multiple repos.",
    )
    parser.add_argument(
        "--private-lists",
        action="store_true",
        help="Create missing managed lists as private. Existing list privacy is unchanged.",
    )
    return parser.parse_args()


def verify_environment() -> None:
    if shutil.which("gh") is None:
        raise SystemExit("error: GitHub CLI 'gh' is not installed or not on PATH")
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise SystemExit("error: TYPESAFE_API_KEY is not set")

    proc = subprocess.run(
        ["gh", "auth", "status"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        details = proc.stderr.strip() or proc.stdout.strip()
        raise SystemExit(f"error: GitHub CLI is not authenticated\n{details}")


def describe_drop_candidate(candidate: dict[str, Any]) -> str:
    repo = candidate["repo"]
    signals = [f"drop={candidate['probability']:.2f}"]
    if repo["isArchived"]:
        signals.append("archived")
    if repo["pushedAt"]:
        signals.append(f"last push {repo['pushedAt'][:4]}")
    description = (repo["description"] or "").strip()
    if len(description) > 60:
        description = description[:57] + "..."
    return f"{repo['nameWithOwner']}  [{', '.join(signals)}]  {description}"


def review_drop_candidates(
    candidates: list[dict[str, Any]],
    stats: Counter[str],
    apply: bool,
) -> None:
    if not candidates:
        return

    candidates = sorted(candidates, key=lambda item: item["probability"], reverse=True)
    print(f"\nJev suggests dropping {len(candidates)} star(s):")

    if not apply:
        for candidate in candidates:
            print(f"  - {describe_drop_candidate(candidate)}")
        print("Run with --apply to review them interactively and unstar the ones you select.")
        return

    if not sys.stdin.isatty():
        for candidate in candidates:
            print(f"  - {describe_drop_candidate(candidate)}")
        print("skip: drop review needs an interactive terminal; nothing was unstarred.")
        return

    selected = questionary.checkbox(
        "Select the stars to drop (space toggles, a toggles all, enter validates):",
        choices=[
            questionary.Choice(describe_drop_candidate(candidate), value=candidate, checked=True)
            for candidate in candidates
        ],
    ).ask()

    # ask() returns None when the prompt is cancelled (Ctrl-C).
    if not selected:
        print("No stars selected; nothing was unstarred.")
        return

    confirmed = questionary.confirm(
        f"Unstar {len(selected)} repositor{'y' if len(selected) == 1 else 'ies'}? This cannot be undone from this script.",
        default=False,
    ).ask()
    if not confirmed:
        print("Cancelled; nothing was unstarred.")
        return

    for candidate in selected:
        name = candidate["repo"]["nameWithOwner"]
        try:
            unstar_repository(candidate["repo"]["id"])
            print(f"    unstarred: {name}")
            stats["unstarred"] += 1
        except Exception as exc:
            print(f"    ERROR: could not unstar {name}: {exc}", file=sys.stderr)
            stats["errors"] += 1


def print_summary(stats: Counter[str], registry: ListRegistry, apply: bool) -> None:
    print("\nSummary")
    print("-------")
    print(f"mode:                 {'APPLY' if apply else 'DRY-RUN'}")
    print(f"selected stars:       {stats['selected']}")
    print(f"already categorized:  {stats['already_categorized']}")
    print(f"classified:           {stats['classified']}")
    print(f"low confidence:       {stats['low_confidence']}")
    print(f"drop suggestions:     {stats['drop_suggested']}")
    print(f"unchanged:            {stats['unchanged']}")
    if apply:
        print(f"assigned/moved:       {stats['assigned']}")
        print(f"lists created:        {registry.created}")
        print(f"unstarred:            {stats['unstarred']}")
    else:
        print(f"would assign/move:    {stats['would_assign']}")
        print(f"would create lists:   {len(registry.planned_names)}")
    print(f"errors:               {stats['errors']}")


def main() -> int:
    args = parse_args()
    verify_environment()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Mode: {mode}")
    if not args.apply:
        print("No GitHub data will be changed. Pass --apply to mutate.\n")

    print("Fetching GitHub Lists...")
    initial_lists = get_user_lists()
    registry = ListRegistry(initial_lists)
    print(f"Found {len(initial_lists)} lists.\n")

    print("Loading existing list memberships...")
    memberships = load_repository_memberships(initial_lists) if initial_lists else {}
    print()

    print("Fetching starred repositories...")
    stars = get_starred_repositories()
    print(f"Found {len(stars)} starred repositories.\n")

    if args.repo:
        requested = {name.casefold() for name in args.repo}
        stars = [repo for repo in stars if repo["nameWithOwner"].casefold() in requested]
        found = {repo["nameWithOwner"].casefold() for repo in stars}
        missing = sorted(requested - found)
        if missing:
            print("warning: requested starred repo(s) not found: " + ", ".join(missing), file=sys.stderr)

    if args.limit:
        stars = stars[: args.limit]

    stats: Counter[str] = Counter()
    stats["selected"] = len(stars)
    drop_candidates: list[dict[str, Any]] = []

    with TypeSafeClient() as client:
        for index, repo in enumerate(stars, start=1):
            repo_id = repo["id"]
            name = repo["nameWithOwner"]
            current_ids = set(memberships.get(repo_id, set()))
            current_managed_ids = current_ids & registry.managed_ids()

            print(f"[{index}/{len(stars)}] {name}")

            if current_managed_ids and not args.reclassify:
                managed_names = registry.names_for_ids(current_managed_ids)
                print(f"    skip: already categorized in {', '.join(managed_names)}")
                stats["already_categorized"] += 1
                continue

            try:
                answer, drop_answer = classify_repository(client, repo)
            except Exception as exc:  # SDK/network errors should not lose the whole batch.
                print(f"    ERROR: Jev classification failed: {exc}", file=sys.stderr)
                stats["errors"] += 1
                continue

            stats["classified"] += 1

            drop_probability = float(drop_answer.noul)
            if drop_probability >= args.drop_threshold:
                print(f"    drop suggested (probability={drop_probability:.2f})")
                drop_candidates.append({"repo": repo, "probability": drop_probability})
                stats["drop_suggested"] += 1

            category_key = answer.choice
            confidence = float(answer.confidence)
            probability_for_choice = float(answer.probabilities.get(category_key, 0.0))

            if category_key not in CATEGORIES:
                print(f"    ERROR: Jev returned unknown category {category_key!r}", file=sys.stderr)
                stats["errors"] += 1
                continue

            category = CATEGORIES[category_key]
            print(
                f"    Jev: {category['name']} "
                f"(confidence={confidence:.2f}, probability={probability_for_choice:.2f})"
            )

            if confidence < args.threshold:
                alternatives = sorted(
                    answer.probabilities.items(),
                    key=lambda item: item[1],
                    reverse=True,
                )[:3]
                rendered = ", ".join(f"{key}={value:.2f}" for key, value in alternatives)
                print(f"    skip: confidence below {args.threshold:.2f}; top probabilities: {rendered}")
                stats["low_confidence"] += 1
                continue

            try:
                target_list = registry.ensure(
                    name=category["name"],
                    description=category["description"],
                    is_private=args.private_lists,
                    apply=args.apply,
                )

                manual_ids = current_ids - registry.managed_ids()
                manual_names = registry.names_for_ids(manual_ids)

                if not args.apply:
                    previous_managed_names = registry.names_for_ids(current_managed_ids)
                    if previous_managed_names:
                        print(
                            "    plan: replace managed category "
                            f"{', '.join(previous_managed_names)} -> {category['name']}"
                        )
                    else:
                        print(f"    plan: add managed category {category['name']}")
                    if manual_names:
                        print(f"    preserve manual lists: {', '.join(manual_names)}")
                    stats["would_assign"] += 1
                    continue

                assert target_list is not None
                target_id = target_list["id"]

                # Remove only script-managed taxonomy memberships, preserve every
                # unrelated/manual list, then add the selected managed category.
                desired_ids = (current_ids - registry.managed_ids()) | {target_id}

                if desired_ids == current_ids:
                    print("    unchanged: memberships already match")
                    stats["unchanged"] += 1
                    continue

                set_repository_memberships(repo_id, desired_ids)
                memberships[repo_id] = desired_ids

                preserved_names = registry.names_for_ids(desired_ids - {target_id})
                print(f"    applied: {category['name']}")
                if preserved_names:
                    print(f"    preserved manual lists: {', '.join(preserved_names)}")
                stats["assigned"] += 1

            except Exception as exc:
                print(f"    ERROR: GitHub update failed: {exc}", file=sys.stderr)
                if args.apply:
                    print(
                        "    hint: if GitHub reports an authorization problem, try "
                        "`gh auth refresh -s user`.",
                        file=sys.stderr,
                    )
                stats["errors"] += 1

    review_drop_candidates(drop_candidates, stats, args.apply)
    print_summary(stats, registry, args.apply)
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
