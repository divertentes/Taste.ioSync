#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests

TASTE_LABELS = {1: "awful", 2: "meh", 3: "good", 4: "amazing"}
# letterboxd 0.5–5 stelle → scala 10 punti → Taste.io 1–4
TEN_POINT_TO_TASTE = [None, 1, 1, 1, 2, 2, 3, 3, 4, 4, 4]


@dataclass(frozen=True)
class Movie:
    title: str
    year: int | None
    letterboxd_rating: float | None
    source: str

    @property
    def key(self) -> tuple[str, int | None]:
        return (self.title.strip().lower(), self.year)


def letterboxd_stars_to_taste(stars: float) -> int | None:
    if stars is None or stars <= 0:
        return None
    ten_point = max(1, min(10, round(stars * 2)))
    return TEN_POINT_TO_TASTE[ten_point]


def taste_label(rating: int) -> str:
    return TASTE_LABELS.get(rating, str(rating))


def parse_rating(raw: str) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def load_letterboxd_csv(path: Path) -> list[Movie]:
    movies: list[Movie] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{path} has no header row")

        name_col = "Name" if "Name" in reader.fieldnames else "Film"
        year_col = "Year" if "Year" in reader.fieldnames else None
        rating_col = "Rating" if "Rating" in reader.fieldnames else None

        for row in reader:
            title = (row.get(name_col) or "").strip()
            if not title:
                continue

            year: int | None = None
            if year_col and row.get(year_col, "").strip().isdigit():
                year = int(row[year_col].strip())

            rating = parse_rating(row.get(rating_col, "") if rating_col else "")

            movies.append(
                Movie(
                    title=title,
                    year=year,
                    letterboxd_rating=rating,
                    source=path.name,
                )
            )
    return movies


def merge_movies(sources: Iterable[list[Movie]]) -> list[Movie]:
    merged: dict[tuple[str, int | None], Movie] = {}
    for batch in sources:
        for movie in batch:
            existing = merged.get(movie.key)
            if existing is None:
                merged[movie.key] = movie
                continue
            if (movie.letterboxd_rating or 0) >= (existing.letterboxd_rating or 0):
                merged[movie.key] = movie
    return sorted(merged.values(), key=lambda m: (m.title.lower(), m.year or 0))


def normalize_title(title: str) -> str:
    lowered = title.lower().strip()
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _dedupe_queries(queries: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for query in queries:
        query = query.strip()
        if query and query not in seen:
            seen.add(query)
            result.append(query)
    return result


def build_search_queries(title: str, year: int | None) -> list[str]:
    # taste.io risponde meglio al titolo SOLO ("titolo anno" spesso fallisce)
    queries: list[str] = [title]
    if year:
        queries.append(f"{title} {year}")

    if ":" in title:
        queries.append(title.replace(":", " "))
        digits = re.sub(r"\D", "", title)
        if digits:
            queries.append(digits)
            if year:
                queries.append(f"{digits} {year}")

    cleaned = re.sub(r"[^\w\s:]", " ", title).strip()
    if cleaned != title:
        queries.append(cleaned)
        if year:
            queries.append(f"{cleaned} {year}")

    normalized = normalize_title(title)
    if normalized and normalized != title.lower():
        queries.append(normalized)
        if year:
            queries.append(f"{normalized} {year}")

    return _dedupe_queries(queries)


def build_deep_search_queries(title: str, year: int | None) -> list[str]:
    queries: list[str] = []
    queries.append(title.lower())
    queries.append(title.upper())

    compact = re.sub(r"\s+", "", title)
    if compact:
        queries.append(compact)
        queries.append(compact.lower())

    normalized = normalize_title(title)
    queries.append(normalized)

    no_spaces = normalized.replace(" ", "")
    if no_spaces:
        queries.append(no_spaces)

    for word in normalized.split():
        if len(word) >= 2:
            queries.append(word)

    stripped = re.sub(r"^(the|a|an)\s+", "", normalized, flags=re.I)
    if stripped != normalized:
        queries.append(stripped)
        queries.append(stripped.replace(" ", ""))

    if year:
        queries.append(f"{normalized} {year}")
        queries.append(f"{no_spaces} {year}")

    return _dedupe_queries(queries)


def slugify_title(title: str) -> str:
    return re.sub(r"\s+", "-", normalize_title(title))


def build_slug_guesses(title: str, year: int | None) -> list[str]:
    base = slugify_title(title)
    if not base:
        return []

    slugs = [base]
    if year:
        slugs.append(f"{base}-{year}")
        slugs.append(f"{base}-{year % 100:02d}")

    for index in range(1, 11):
        slugs.append(f"{base}-{index}")

    return _dedupe_queries(slugs)


def titles_match(expected: str, actual: str) -> bool:
    return normalize_title(expected) == normalize_title(actual)


def is_valid_match(candidate: dict, title: str, year: int | None) -> bool:
    name = candidate.get("name") or ""
    cand_year = candidate.get("year")

    if not titles_match(title, name):
        return False

    if year is not None:
        if cand_year is None:
            return False
        return int(cand_year) == year

    return True


def pick_best_match(candidates: list[dict], title: str, year: int | None) -> dict | None:
    valid = [candidate for candidate in candidates if is_valid_match(candidate, title, year)]
    if not valid:
        return None

    if year is None:
        # senza anno da Letterboxd, scartiamo se ci sono titoli uguali con anni diversi
        years = {candidate.get("year") for candidate in valid}
        if len(years) > 1:
            return None

    return valid[0]


def movie_from_payload(payload: dict | None) -> dict | None:
    if not payload:
        return None
    if payload.get("name") or payload.get("slug"):
        return payload
    nested = payload.get("movie")
    if isinstance(nested, dict):
        return nested
    return None


class TasteIOClient:
    BASE_URL = "https://www.taste.io/api"

    def __init__(self, token: str) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "letterboxd-to-taste/1.0",
            }
        )

    def _search_query(self, query: str) -> list[dict]:
        response = self.session.get(
            f"{self.BASE_URL}/movies/search",
            params={"q": query},
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("movies") or []

    def _fetch_by_slug(self, slug: str) -> dict | None:
        try:
            response = self.session.get(
                f"{self.BASE_URL}/movies/{slug}",
                timeout=30,
            )
        except requests.RequestException:
            return None
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            return None
        try:
            return movie_from_payload(response.json())
        except ValueError:
            return None

    def search(self, title: str, year: int | None = None) -> tuple[dict | None, str | None]:
        seen_slugs: set[str] = set()
        candidates: list[dict] = []

        for query in build_search_queries(title, year):
            for candidate in self._search_query(query):
                slug = candidate.get("slug")
                if slug and slug in seen_slugs:
                    continue
                if slug:
                    seen_slugs.add(slug)
                candidates.append(candidate)

            match = pick_best_match(candidates, title, year)
            if match is not None:
                return match, query

        for query in build_deep_search_queries(title, year):
            if query in build_search_queries(title, year):
                continue
            for candidate in self._search_query(query):
                slug = candidate.get("slug")
                if slug and slug in seen_slugs:
                    continue
                if slug:
                    seen_slugs.add(slug)
                candidates.append(candidate)

            match = pick_best_match(candidates, title, year)
            if match is not None:
                return match, query

        for slug in build_slug_guesses(title, year):
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            candidate = self._fetch_by_slug(slug)
            if candidate is None:
                continue
            candidates.append(candidate)
            if is_valid_match(candidate, title, year):
                return candidate, f"slug:{slug}"

        return None, None

    def existing_rating(self, movie: dict) -> int | None:
        user = movie.get("user") or {}
        rating = user.get("rating")
        return int(rating) if rating else None

    def submit_rating(self, slug: str, rating: int) -> None:
        # Laravel: il POST richiede il cookie XSRF-TOKEN come header
        xsrf = self.session.cookies.get("XSRF-TOKEN")
        headers = {"X-XSRF-TOKEN": xsrf} if xsrf else {}
        response = self.session.post(
            f"{self.BASE_URL}/movies/{slug}/rating",
            json={"rating": rating},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()


def all_search_attempts(title: str, year: int | None) -> str:
    queries = build_search_queries(title, year)
    queries.extend(q for q in build_deep_search_queries(title, year) if q not in queries)
    slugs = build_slug_guesses(title, year)
    parts = queries + [f"slug:{slug}" for slug in slugs]
    return ", ".join(parts)


def resolve_csv_paths(csv_arg: str | None, export_dir: Path | None) -> list[Path]:
    if csv_arg:
        path = Path(csv_arg)
        if not path.is_file():
            raise FileNotFoundError(f"CSV not found: {path}")
        return [path]

    if export_dir is None:
        raise ValueError("Provide --csv or --export-dir")

    candidates = ["ratings.csv", "diary.csv"]
    found = [export_dir / name for name in candidates if (export_dir / name).is_file()]
    if not found:
        raise FileNotFoundError(
            f"No ratings.csv or diary.csv found in {export_dir}. "
            "Export your data from https://letterboxd.com/settings/data/"
        )
    return found


def import_movies(
    movies: list[Movie],
    client: TasteIOClient | None,
    *,
    update_existing: bool,
    skip_unrated: bool,
    delay: float,
    dry_run: bool,
    verbose: bool,
) -> dict[str, int]:
    stats = {
        "imported": 0,
        "skipped_existing": 0,
        "skipped_unrated": 0,
        "not_found": 0,
        "errors": 0,
    }

    total = len(movies)
    for index, movie in enumerate(movies, start=1):
        prefix = f"[{index}/{total}]"

        taste_rating = letterboxd_stars_to_taste(movie.letterboxd_rating or 0)
        if taste_rating is None:
            if skip_unrated:
                print(f"{prefix} SKIP (no rating): {movie.title} ({movie.year or '?'})")
                stats["skipped_unrated"] += 1
                continue
            print(f"{prefix} SKIP (Taste.io requires a rating): {movie.title}")
            stats["skipped_unrated"] += 1
            continue

        stars = movie.letterboxd_rating
        print(
            f"{prefix} {movie.title} ({movie.year or '?'}) "
            f"— {stars}★ → {taste_label(taste_rating)} ({taste_rating})"
        )

        if dry_run:
            stats["imported"] += 1
            continue

        try:
            match, matched_query = client.search(movie.title, movie.year)
            if match is None:
                print(f"         NOT FOUND on Taste.io (tried: {all_search_attempts(movie.title, movie.year)})")
                stats["not_found"] += 1
                continue

            slug = match.get("slug")
            name = match.get("name") or movie.title
            existing = client.existing_rating(match)

            if existing and not update_existing:
                print(
                    f"         SKIP (already rated as {taste_label(existing)}): {name}"
                )
                stats["skipped_existing"] += 1
                continue

            if not slug:
                print(f"         ERROR: search result missing slug for {name}")
                stats["errors"] += 1
                continue

            client.submit_rating(slug, taste_rating)
            action = "Updated" if existing else "Imported"
            suffix = f" via '{matched_query}'" if verbose and matched_query else ""
            print(f"         {action}: {name}{suffix}")
            stats["imported"] += 1
        except requests.HTTPError as exc:
            print(f"         HTTP ERROR: {exc.response.status_code} {exc.response.text[:200]}")
            stats["errors"] += 1
        except requests.RequestException as exc:
            print(f"         ERROR: {exc}")
            stats["errors"] += 1

        if delay > 0 and index < total:
            time.sleep(delay)

    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import Letterboxd CSV ratings into Taste.io",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python import_letterboxd_to_taste.py --export-dir ./letterboxd-export --token YOUR_TOKEN
  python import_letterboxd_to_taste.py --csv ratings.csv --dry-run
        """.strip(),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", help="Path to a Letterboxd CSV file")
    source.add_argument(
        "--export-dir",
        type=Path,
        help="Folder with Letterboxd export (reads ratings.csv + diary.csv)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("TASTE_TOKEN"),
        help="Taste.io bearer token (or set TASTE_TOKEN env var)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Overwrite movies already rated on Taste.io",
    )
    parser.add_argument(
        "--include-unrated",
        action="store_true",
        help="Include unrated entries (still skipped — Taste.io needs a rating)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.35,
        help="Seconds between API calls (default: 0.35)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without API calls")
    parser.add_argument("--verbose", action="store_true", help="Show matched search query")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.dry_run and not args.token:
        print(
            "Error: Taste.io token required. Pass --token or set TASTE_TOKEN.\n"
            "Get it from DevTools → Network → /me → Authorization (without 'Bearer ').",
            file=sys.stderr,
        )
        return 1

    try:
        csv_paths = resolve_csv_paths(args.csv, args.export_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    batches = [load_letterboxd_csv(path) for path in csv_paths]
    movies = merge_movies(batches)
    if not args.include_unrated:
        movies = [m for m in movies if m.letterboxd_rating]

    print(f"Loaded {len(movies)} unique movies from: {', '.join(p.name for p in csv_paths)}")
    print()

    client = None if args.dry_run else TasteIOClient(args.token)

    stats = import_movies(
        movies,
        client,
        update_existing=args.update,
        skip_unrated=not args.include_unrated,
        delay=args.delay,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    print()
    print("Done.")
    print(f"  Imported:          {stats['imported']}")
    print(f"  Skipped (exists):  {stats['skipped_existing']}")
    print(f"  Skipped (unrated): {stats['skipped_unrated']}")
    print(f"  Not found:         {stats['not_found']}")
    print(f"  Errors:            {stats['errors']}")

    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
