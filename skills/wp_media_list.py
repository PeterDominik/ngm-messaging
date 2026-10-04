#!/usr/bin/env python3
"""
wp_media_list.py - list the files in a WordPress uploads folder
(e.g. https://site.com/wp-content/uploads/2022/01/) using the site's public REST API.

WordPress hosts usually disable directory listing on /wp-content/uploads/, but the
endpoint /wp-json/wp/v2/media returns the same information for every file in the
media library.

PyCharm:  set FOLDER_URL below and press Run.
Terminal: python wp_media_list.py https://copyposse.com/wp-content/uploads/2022/01/ --sizes

Standard library only - nothing to pip install.
"""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

# ---- SETTINGS (used when you press Run with no arguments) -------------------
FOLDER_URL = "https://copyposse.com/wp-content/uploads/2022/"
INCLUDE_SIZES = False  # True = also list the resized copies WordPress generated (thumbnails etc.)
SCAN_ALL = False       # True = scan the whole media library and match by folder path
                       #        (slower, but catches files whose upload date doesn't match the folder)
SAVE_CSV = True        # writes a CSV next to this script
# -----------------------------------------------------------------------------

PER_PAGE = 100       # WordPress maximum per request
REQUEST_DELAY = 0.5  # seconds between pages, to go easy on the server
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
FIELDS = "id,date,mime_type,filesize,source_url,media_details"
FOLDER_RE = re.compile(r"^(https?://.+?)/wp-content/uploads/(\d{4})/(\d{2})/?$")


def make_ssl_context() -> ssl.SSLContext:
    """Use certifi's certificate bundle when it's installed.

    Python from python.org on macOS ships without access to the system certificates,
    which causes CERTIFICATE_VERIFY_FAILED. certifi provides a bundle that works everywhere.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_CONTEXT = make_ssl_context()


def parse_folder_url(url: str) -> tuple[str, int, int]:
    """Split an uploads folder URL into (site base URL, year, month)."""
    match = FOLDER_RE.match(url.strip())
    if not match:
        sys.exit(
            f"Couldn't read that URL: {url}\n"
            "Expected something like https://site.com/wp-content/uploads/2022/01/"
        )
    site, year, month = match.group(1), int(match.group(2)), int(match.group(3))
    if not 1 <= month <= 12:
        sys.exit(f"Month must be 01-12, got {month:02d}")
    return site, year, month


def month_bounds(year: int, month: int) -> tuple[str, str]:
    """'after' and 'before' timestamps covering the whole month (WordPress treats both as exclusive)."""
    start = datetime(year, month, 1)
    end = datetime(year + (month == 12), month % 12 + 1, 1)
    return (start - timedelta(seconds=1)).isoformat(), end.isoformat()


def fetch_page(site: str, page: int, date_range: tuple[str, str] | None):
    """Fetch one page of media items. Returns (items, total_items, total_pages)."""
    params = {
        "per_page": PER_PAGE,
        "page": page,
        "orderby": "date",
        "order": "asc",
        "_fields": FIELDS,
    }
    if date_range:
        params["after"], params["before"] = date_range
    url = f"{site}/wp-json/wp/v2/media?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
        total = int(response.headers.get("X-WP-Total", 0))
        pages = int(response.headers.get("X-WP-TotalPages", 1))
        items = json.load(response)
    return items, total, pages


def fetch_all(site: str, date_range: tuple[str, str] | None) -> list[dict]:
    """Walk every page of results."""
    items: list[dict] = []
    page, pages = 1, 1
    while page <= pages:
        try:
            batch, total, pages = fetch_page(site, page, date_range)
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", "replace")
            if err.code == 400 and "rest_post_invalid_page_number" in body:
                break  # ran past the last page
            raise
        if page == 1:
            print(f"  API reports {total} item(s) across {pages} page(s)")
        print(f"  page {page}/{pages}: {len(batch)} item(s)")
        items.extend(batch)
        page += 1
        if page <= pages:
            time.sleep(REQUEST_DELAY)
    return items


def build_rows(items: list[dict], folder_marker: str, include_sizes: bool) -> tuple[list[dict], int]:
    """Turn API items into flat rows. Returns (rows, number of items outside the folder)."""
    rows: list[dict] = []
    seen: set[str] = set()
    outside = 0

    for item in items:
        source = item.get("source_url", "")
        if folder_marker not in source:
            outside += 1
            continue

        details = item.get("media_details") or {}
        filesize = item.get("filesize") or details.get("filesize")
        if source not in seen:
            seen.add(source)
            rows.append({
                "date": item.get("date", ""),
                "kind": "original",
                "mime_type": item.get("mime_type", ""),
                "size_kb": round(filesize / 1024, 1) if filesize else "",
                "width": details.get("width", ""),
                "height": details.get("height", ""),
                "url": source,
            })

        if include_sizes:
            base = source.rsplit("/", 1)[0]
            for name, size in (details.get("sizes") or {}).items():
                # Jetpack/Photon adds query strings to file names; strip them to get the file on disk
                filename = (size.get("file") or "").split("?")[0]
                url = f"{base}/{filename}"
                if not filename or url in seen:
                    continue
                seen.add(url)
                size_bytes = size.get("filesize")
                rows.append({
                    "date": item.get("date", ""),
                    "kind": f"size:{name}",
                    "mime_type": mimetypes.guess_type(filename)[0] or "",
                    "size_kb": round(size_bytes / 1024, 1) if size_bytes else "",
                    "width": size.get("width", ""),
                    "height": size.get("height", ""),
                    "url": url,
                })

    return rows, outside


def print_rows(rows: list[dict]) -> None:
    print()
    print(f"{'DATE':<11} {'KIND':<22} {'KB':>9}  URL")
    print("-" * 110)
    for row in rows:
        print(f"{row['date'][:10]:<11} {row['kind']:<22} {str(row['size_kb']):>9}  {row['url']}")

    by_type = Counter(Path(urllib.parse.urlparse(r["url"]).path).suffix.lower() or "?" for r in rows)
    originals = sum(1 for r in rows if r["kind"] == "original")
    print("-" * 110)
    print(f"{len(rows)} file(s), {originals} original(s). By extension: "
          + ", ".join(f"{ext} {n}" for ext, n in by_type.most_common()))


def save_csv(rows: list[dict], site: str, year: int, month: int) -> Path:
    host = urllib.parse.urlparse(site).netloc.replace(":", "_")
    path = Path(__file__).resolve().parent / f"wp_media_{host}_{year}_{month:02d}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["date", "kind", "mime_type", "size_kb", "width", "height", "url"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def wayback_hint(site: str, year: int, month: int) -> str:
    host_path = re.sub(r"^https?://", "", site)
    return (
        "Fallback - the Wayback Machine's index of archived URLs in that folder:\n"
        f"  https://web.archive.org/cdx/search/cdx?url={host_path}/wp-content/uploads/"
        f"{year}/{month:02d}/*&fl=original&collapse=urlkey"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="List files in a WordPress uploads folder via the REST API.")
    parser.add_argument("url", nargs="?", default=FOLDER_URL, help="e.g. https://site.com/wp-content/uploads/2022/01/")
    parser.add_argument("--sizes", action="store_true", default=INCLUDE_SIZES, help="include resized copies")
    parser.add_argument("--all", dest="scan_all", action="store_true", default=SCAN_ALL,
                        help="scan the whole library instead of filtering by upload date")
    parser.add_argument("--no-csv", dest="save_csv", action="store_false", default=SAVE_CSV)
    args = parser.parse_args()

    site, year, month = parse_folder_url(args.url)
    folder_marker = f"/wp-content/uploads/{year:04d}/{month:02d}/"
    date_range = None if args.scan_all else month_bounds(year, month)

    mode = "whole library, matched by folder" if args.scan_all else f"uploads dated {year}-{month:02d}"
    print(f"Querying {site}/wp-json/wp/v2/media ({mode})")

    try:
        items = fetch_all(site, date_range)
    except urllib.error.HTTPError as err:
        reasons = {
            401: "the media endpoint requires a login on this site",
            403: "the site is blocking the request (security plugin or bot protection)",
            404: "no WordPress REST API at that address",
        }
        print(f"\nHTTP {err.code}: {reasons.get(err.code, err.reason)}")
        print(wayback_hint(site, year, month))
        sys.exit(1)
    except urllib.error.URLError as err:
        if "CERTIFICATE_VERIFY_FAILED" in str(err.reason):
            sys.exit(
                "\nPython can't verify HTTPS certificates on this machine.\n"
                "Fix (either one):\n"
                "  1. Install certifi into this interpreter:  pip install certifi\n"
                "     (PyCharm: Settings > Project > Python Interpreter > + > certifi)\n"
                "  2. macOS with Python from python.org: run 'Install Certificates.command'\n"
                "     in /Applications/Python 3.x/ (one-time fix for all scripts)"
            )
        sys.exit(f"\nCouldn't reach {site}: {err.reason}")
    except json.JSONDecodeError:
        print("\nThe site answered with something other than JSON (probably a bot-check page).")
        print(wayback_hint(site, year, month))
        sys.exit(1)

    rows, outside = build_rows(items, folder_marker, args.sizes)
    if not rows:
        print(f"\nNo files found in {folder_marker}")
        if not args.scan_all:
            print("Try SCAN_ALL = True (or --all) in case the upload dates don't match the folder.")
        sys.exit(0)

    print_rows(rows)
    if outside:
        print(f"({outside} item(s) from that month live in other folders and were skipped)")
    if args.save_csv:
        print(f"Saved: {save_csv(rows, site, year, month)}")


if __name__ == "__main__":
    main()