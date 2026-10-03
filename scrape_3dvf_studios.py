#!/usr/bin/env python3
"""Scrapes 3DVF studio directory and generates a CSV report.

This script fetches studio information from https://3dvf.com/studio/, parses
details such as name, website, offices, and specialities, and outputs the
aggregated data into a CSV file.

Usage:
    pip install requests beautifulsoup4 lxml
    python scrape_3dvf_studios.py --limit 10
    python scrape_3dvf_studios.py
    python scrape_3dvf_studios.py --all-offices
"""

import argparse
import csv
import os
import random
import re
import sys
import time
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

__author__ = "clement daures"

BASE_URL = "https://3dvf.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (studio-list-script; usage personnel)"}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Country mapping from French to English
COUNTRY_FR_TO_EN = {
    "Italie": "Italy",
    "Suède": "Sweden",
    "Finlande": "Finland",
    "Allemagne": "Germany",
    "Espagne": "Spain",
    "Royaume-Uni": "United Kingdom",
    "Belgique": "Belgium",
    "Suisse": "Switzerland",
    "Pays-Bas": "Netherlands",
    "Japon": "Japan",
    "Chine": "China",
    "Corée du Sud": "South Korea",
    "Inde": "India",
    "États-Unis": "United States",
    "Etats-Unis": "United States",
    "Irlande": "Ireland",
    "Pologne": "Poland",
    "Danemark": "Denmark",
    "Norvège": "Norway",
    "Autriche": "Austria",
    "Australie": "Australia",
    "Nouvelle-Zélande": "New Zealand",
    "Brésil": "Brazil",
    "Mexique": "Mexico",
}


def make_request(
    url: str, quiet: bool = False, **kwargs
) -> Optional[requests.Response]:
    """Performs an HTTP GET request with retries and exponential backoff.

    Args:
        url: The target URL to fetch.
        quiet: If True, suppresses status logs to stderr.
        **kwargs: Additional keyword arguments passed to `requests.Session.get`.

    Returns:
        A `requests.Response` object if successful (HTTP 200), or None if the
        request failed after retries or encountered a 404/410 status.
    """
    for attempt, pause_seconds in enumerate((5, 15, 30)):
        try:
            response = SESSION.get(url, timeout=30, **kwargs)
            if not quiet:
                print(
                    f"  GET {url} -> {response.status_code}",
                    file=sys.stderr,
                    flush=True,
                )
            if response.status_code == 200:
                return response
            if response.status_code in (404, 410):
                return None
        except requests.RequestException as error:
            print(
                f"  GET {url} -> error: {type(error).__name__}",
                file=sys.stderr,
                flush=True,
            )
        time.sleep(pause_seconds)
    return None


def fetch_urls_from_sitemaps() -> List[str]:
    """Retrieves studio page URLs from sitemap XML files.

    Returns:
        A list of unique studio page URLs found in the sitemaps.
    """
    candidates = [
        f"{BASE_URL}/wp-sitemap-posts-studio-1.xml",
        f"{BASE_URL}/wp-sitemap-posts-studio-2.xml",
        f"{BASE_URL}/wp-sitemap-posts-studio-3.xml",
        f"{BASE_URL}/studio-sitemap.xml",
        f"{BASE_URL}/studio-sitemap2.xml",
        f"{BASE_URL}/studio-sitemap3.xml",
    ]
    found_urls = []
    for candidate in candidates:
        response = make_request(candidate)
        if response:
            found_urls.extend(re.findall(r"<loc>(.*?)</loc>", response.text))

    unique_urls = list(dict.fromkeys(found_urls))
    return [
        url
        for url in unique_urls
        if "/studio/" in url and "/en/" not in url and not url.endswith("/studio/")
    ]


def fetch_urls_from_rest_api() -> List[str]:
    """Retrieves studio page URLs using the WordPress REST API endpoint.

    Returns:
        A list of studio page URLs extracted from API responses.
    """
    found_urls = []
    page = 1
    while True:
        response = make_request(
            f"{BASE_URL}/wp-json/wp/v2/studio",
            params={"per_page": 100, "page": page, "_fields": "link"},
        )
        if not response:
            break
        data = response.json()
        if not data:
            break
        found_urls.extend([item["link"] for item in data if "/en/" not in item["link"]])
        page += 1
    return found_urls


def discover_studio_urls() -> List[str]:
    """Finds all available studio URLs via sitemaps or REST API fallback.

    Returns:
        A list of studio page URLs.

    Raises:
        SystemExit: If no URLs could be discovered from either source.
    """
    print("Searching for studio list (sitemaps)...", file=sys.stderr, flush=True)
    urls = fetch_urls_from_sitemaps()
    if urls:
        print(f"{len(urls)} profiles found via sitemaps", file=sys.stderr)
        return urls

    print(
        "Sitemaps unavailable, attempting via REST API...",
        file=sys.stderr,
        flush=True,
    )
    urls = fetch_urls_from_rest_api()
    if urls:
        print(f"{len(urls)} profiles found via REST API", file=sys.stderr)
        return urls

    sys.exit(
        "Unable to list studio profiles (sitemaps and REST API unavailable)."
    )


def parse_studio_page(html_content: str) -> Tuple[str, str, List[Tuple[str, str]], str]:
    """Parses studio details from a studio profile HTML page.

    Args:
        html_content: The HTML source of the studio profile page.

    Returns:
        A tuple containing:
            - Studio name (str).
            - Website URL (str).
            - List of office tuples formatted as `(city, country)`.
            - Domain/specialities formatted as a pipe-separated string.
    """
    soup = BeautifulSoup(html_content, "lxml")
    h1_tag = soup.find("h1")
    studio_name = h1_tag.get_text(strip=True) if h1_tag else ""

    website_url = ""
    for anchor in soup.find_all("a"):
        if "voir le site" in anchor.get_text(strip=True).lower():
            website_url = anchor.get("href", "")
            break

    offices: List[Tuple[str, str]] = []
    stop_pattern = re.compile(r"^(Nos articles|Nos dossiers|En savoir plus)", re.I)
    offices_marker = soup.find(string=re.compile(r"^\s*Les bureaux\s*$", re.I))

    if offices_marker:
        groups: List[List[str]] = []
        for element in offices_marker.find_all_next(string=True):
            text = element.strip()
            if not text or element.parent.name in ("script", "style"):
                continue
            if stop_pattern.match(text):
                break
            if element.find_parent("h4"):
                groups.append([])
            if groups:
                groups[-1].append(text)

        for group in groups:
            if len(group) >= 3:
                offices.append((group[-2], group[-1]))

    # Fallback to metadata section if no offices were found via block structure
    if not offices:
        def extract_section(title: str, stop_titles: Tuple[str, ...]) -> List[str]:
            marker = soup.find(string=re.compile(rf"^\s*{title}\s*$", re.I))
            results = []
            if marker:
                for element in marker.find_all_next(string=True):
                    text = element.strip()
                    if not text or element.parent.name in ("script", "style"):
                        continue
                    if text in stop_titles:
                        break
                    results.append(text)
            return results

        countries = extract_section("Pays", ("Villes", "Spécialités"))
        cities = extract_section("Villes", ("Spécialités", "Les bureaux"))
        if len(countries) == 1 and len(cities) == 1:
            offices.append((cities[0], countries[0]))

    domains: List[str] = []
    specialities_marker = soup.find(string=re.compile(r"^\s*Spécialités\s*$", re.I))
    if specialities_marker:
        for element in specialities_marker.find_all_next(string=True):
            text = element.strip()
            if not text or element.parent.name in ("script", "style"):
                continue
            if re.match(r"^(Les bureaux|Nos articles|Nos dossiers|En savoir plus)", text, re.I):
                break
            if text.casefold() == studio_name.casefold():
                continue
            if text not in domains:
                domains.append(text)

    return studio_name, website_url, offices, " | ".join(domains)


def format_location(city: str, country: str) -> str:
    """Formats city and country names into a standard string representation.

    Args:
        city: The city name.
        country: The country name (in French or English).

    Returns:
        A formatted string in the format `CITY (COUNTRY)`.
    """
    translated_country = COUNTRY_FR_TO_EN.get(country, country).upper()
    return f"{city} ({translated_country})"


def main() -> None:
    """Main execution function to parse CLI flags and process studios."""
    parser = argparse.ArgumentParser(
        description="Scrape studio listings from 3DVF into CSV."
    )
    parser.add_argument(
        "--out",
        default="studios_3dvf.csv",
        help="Path to the output CSV file.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit processing to a specific number of studios.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
        help="Base delay in seconds between HTTP requests.",
    )
    parser.add_argument(
        "--all-offices",
        action="store_true",
        help="Output a separate row for each studio office location.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume scraping from a previous run using the state tracking file.",
    )
    args = parser.parse_args()

    done_file = f"{args.out}.done"
    studio_urls = discover_studio_urls()
    if args.limit:
        studio_urls = studio_urls[: args.limit]

    completed_urls = set()
    if args.resume and os.path.exists(args.out) and os.path.exists(done_file):
        with open(done_file, encoding="utf-8") as file:
            completed_urls = {line.strip() for line in file if line.strip()}
        file_mode = "a"
        print(
            f"Resuming: {len(completed_urls)} profiles already processed.",
            file=sys.stderr,
            flush=True,
        )
    else:
        file_mode = "w"
        open(done_file, "w").close()

    pending_urls = [url for url in studio_urls if url not in completed_urls]
    failed_urls: List[str] = []

    def process_url(
        url: str,
        csv_file,
        writer: csv.writer,
        progress_file,
        label: str,
    ) -> bool:
        """Helper to fetch, parse, and write a single studio profile to CSV."""
        response = make_request(url, quiet=True)
        if not response:
            print(f"{label} FAILED {url}", file=sys.stderr, flush=True)
            return False

        name, site, offices, domain = parse_studio_page(response.text)
        if not offices:
            writer.writerow([name, "", site, domain])
        elif args.all_offices:
            for city, country in offices:
                writer.writerow([name, format_location(city, country), site, domain])
        else:
            writer.writerow([name, format_location(*offices[0]), site, domain])

        progress_file.write(f"{url}\n")
        csv_file.flush()
        progress_file.flush()
        print(f"{label} {name}", file=sys.stderr, flush=True)
        return True

    with open(args.out, file_mode, newline="", encoding="utf-8-sig") as csv_file, open(
        done_file, "a", encoding="utf-8"
    ) as progress_file:
        writer = csv.writer(csv_file)
        if file_mode == "w":
            writer.writerow(["STUDIO", "CITY", "WEBSITE", "DOMAIN"])

        consecutive_failures = 0
        for index, url in enumerate(pending_urls, 1):
            label = f"[{index}/{len(pending_urls)}]"
            success = process_url(url, csv_file, writer, progress_file, label)
            if success:
                consecutive_failures = 0
            else:
                failed_urls.append(url)
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    print(
                        "Rate limit detected. Pausing for 3 minutes...",
                        file=sys.stderr,
                        flush=True,
                    )
                    time.sleep(180)
                    consecutive_failures = 0

            time.sleep(args.delay + random.uniform(0, 1))

        # Retry pass for failed requests
        if failed_urls:
            print(
                f"\nSecond pass on {len(failed_urls)} failed profiles...",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(60)
            still_failed = []
            for index, url in enumerate(failed_urls, 1):
                label = f"[retry {index}/{len(failed_urls)}]"
                if not process_url(url, csv_file, writer, progress_file, label):
                    still_failed.append(url)
                time.sleep(args.delay + 3 + random.uniform(0, 2))

            if still_failed:
                failed_file_path = f"{args.out}.failed.txt"
                with open(failed_file_path, "w", encoding="utf-8") as failed_file:
                    failed_file.write("\n".join(still_failed))
                print(
                    f"{len(still_failed)} profiles still failing -> {failed_file_path} "
                    "(re-run using --resume)",
                    file=sys.stderr,
                )

    print(f"Finished -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()