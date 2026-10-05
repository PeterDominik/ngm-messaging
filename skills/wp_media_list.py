#!/usr/bin/env python3
"""
wp_media_list.py - list the files in any WordPress uploads folder using the site's public REST API.

Works at any level of the uploads tree:
    https://site.com/wp-content/uploads/2022/01/   one month
    https://site.com/wp-content/uploads/2022/      a whole year, grouped by month
    https://site.com/wp-content/uploads/           the entire media library
    https://site.com                               same as above
(the https:// is optional, and a link to a single file lists the folder it's in)

WordPress hosts usually disable directory listing on /wp-content/uploads/, but the
endpoint /wp-json/wp/v2/media returns the same information for every file in the
media library.

PyCharm:  set FOLDER_URL (and any FILTERS) below and press Run.
Terminal: python wp_media_list.py https://copyposse.com/wp-content/uploads/2022/ --sizes
          python wp_media_list.py copyposse.com --type pdf --name slides --name workbook

Standard library only. On macOS with Python from python.org, also `pip install certifi`
(or run "Install Certificates.command") so HTTPS certificates can be verified.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import json
import mimetypes
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# ---- SETTINGS (used when you press Run with no arguments) -------------------
FOLDER_URL = "https://copyposse.com/wp-content/uploads/2023/"
INCLUDE_SIZES = False  # True = also list the resized copies WordPress generated (thumbnails etc.)
SCAN_ALL = True       # True = scan the whole media library and match by folder path
                       #        (slower, but catches files whose upload date doesn't match the folder)
SUMMARY_ONLY = False   # True = print only the per-folder counts (the CSV still gets every file)
SAVE_CSV = True        # writes a CSV next to this script

# ---- FILTERS (leave a list empty to skip that filter) ------------------------
# All matching is case-insensitive and runs on the file name (e.g. "CPLP-Module-3-Workbook.pdf").
NAME_CONTAINS = []     # keep files whose name contains ANY of these, e.g. ["slides", "workbook"]
                       #   wildcards work too: ["*module*.pdf"]
FILE_TYPES = []        # keep only these types, e.g. ["pdf"], ["pdf", "zip"]
                       #   or a group: "images", "documents", "video", "audio", "archives"
                       #   (the full lists are in TYPE_GROUPS further down - add to them freely)
NAME_EXCLUDES = []     # drop files whose name contains ANY of these, e.g. ["bg_", "-150x150"]
# -----------------------------------------------------------------------------

PER_PAGE = 100       # WordPress maximum per request
REQUEST_DELAY = 0.5  # seconds between pages, to go easy on the server
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
FIELDS = "id,date,mime_type,filesize,source_url,media_details"
UPLOADS = "/wp-content/uploads"
ROOT_LABEL = "(uploads root)"
TYPE_GROUPS = {
    "images": ["jpg", "jpeg", "png", "gif", "webp", "svg", "avif", "heic"],
    "documents": ["pdf", "doc", "docx", "ppt", "pptx", "key", "xls", "xlsx", "csv", "txt", "rtf", "pages"],
    "video": [
        # everything WordPress accepts as video uploads by default
        "mp4", "m4v", "mov", "qt", "avi", "divx", "wmv", "wmx", "wm", "asf", "asx", "flv",
        "mpeg", "mpg", "mpe", "ogv", "webm", "mkv", "3gp", "3gpp", "3g2", "3gp2",
        # other formats that turn up when upload restrictions are loosened
        "f4v", "m2v", "mts", "m2ts", "ts", "vob", "mxf", "rm", "rmvb", "hevc", "h264", "m3u8",
    ],
    "audio": ["mp3", "m4a", "wav", "ogg", "aac"],
    "archives": ["zip", "rar", "7z", "gz", "tar"],
}
# Groups also match on the file type WordPress recorded at upload, so a file with an
# unusual extension still counts (e.g. any "video/..." file is in the video group).
TYPE_GROUP_MIME = {"images": "image/", "video": "video/", "audio": "audio/"}
TYPE_GROUP_ALIASES = {
    "image": "images", "img": "images", "videos": "video", "vid": "video",
    "document": "documents", "docs": "documents",
    "archive": "archives", "sound": "audio",
}


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


# ---- Working out what to list ----------------------------------------------

def parse_target(url: str) -> tuple[str, str]:
    """Split a URL into (site base URL, folder inside uploads).

    The folder is '' for the whole uploads tree, '2022' for a year, '2022/01' for a month.
    """
    raw = url.strip()
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = "https://" + raw
    parts = urllib.parse.urlsplit(raw)
    if not parts.netloc:
        sys.exit(f"Couldn't read that URL: {url}")

    path = parts.path
    idx = path.find(UPLOADS)
    if idx == -1:
        # No uploads path: treat it as the site address and list everything.
        return f"{parts.scheme}://{parts.netloc}{path.rstrip('/')}", ""

    rest = path[idx + len(UPLOADS):]
    if rest and not rest.startswith("/"):
        sys.exit(f"Couldn't read that URL: {url}")
    folder = rest.strip("/")

    # A link to a single file (e.g. .../2022/01/guide.pdf) lists the folder that holds it.
    last = folder.rsplit("/", 1)[-1]
    if folder and "." in last and not rest.endswith("/"):
        folder = folder.rsplit("/", 1)[0] if "/" in folder else ""

    return f"{parts.scheme}://{parts.netloc}{path[:idx]}", folder


def date_range_for(folder: str) -> tuple[str, str] | None:
    """Upload-date window that fills a year (2022) or month (2022/01) folder; None for anything else.

    WordPress treats 'after' and 'before' as exclusive, so the window starts one second early.
    """
    match = re.fullmatch(r"(\d{4})(?:/(\d{2}))?", folder)
    if not match:
        return None
    year = int(match.group(1))
    if match.group(2):
        month = int(match.group(2))
        if not 1 <= month <= 12:
            sys.exit(f"Month must be 01-12, got {match.group(2)}")
        start = datetime(year, month, 1)
        end = datetime(year + (month == 12), month % 12 + 1, 1)
    else:
        start, end = datetime(year, 1, 1), datetime(year + 1, 1, 1)
    return (start - timedelta(seconds=1)).isoformat(), end.isoformat()


def uploads_relative(url: str) -> str | None:
    """'2022/01/file.jpg' for a URL inside /wp-content/uploads/, otherwise None."""
    path = urllib.parse.urlsplit(url).path
    idx = path.find(UPLOADS + "/")
    return path[idx + len(UPLOADS) + 1:] if idx != -1 else None


# ---- Talking to the API -----------------------------------------------------

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


# ---- Shaping the results ----------------------------------------------------

def build_rows(items: list[dict], folder: str, include_sizes: bool) -> tuple[list[dict], int]:
    """Turn API items into flat rows, keeping only files under `folder`.

    Returns (rows sorted by folder, number of items that were outside it).
    """
    prefix = f"{folder}/" if folder else ""
    rows: list[dict] = []
    seen: set[str] = set()
    outside = 0

    for item in items:
        source = item.get("source_url", "")
        relative = uploads_relative(source)
        if relative is None or not relative.startswith(prefix):
            outside += 1
            continue
        file_folder = relative.rsplit("/", 1)[0] if "/" in relative else ROOT_LABEL

        details = item.get("media_details") or {}
        filesize = item.get("filesize") or details.get("filesize")
        if source not in seen:
            seen.add(source)
            rows.append({
                "folder": file_folder,
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
                    "folder": file_folder,
                    "date": item.get("date", ""),
                    "kind": f"size:{name}",
                    "mime_type": mimetypes.guess_type(filename)[0] or "",
                    "size_kb": round(size_bytes / 1024, 1) if size_bytes else "",
                    "width": size.get("width", ""),
                    "height": size.get("height", ""),
                    "url": url,
                })

    rows.sort(key=lambda r: r["folder"])  # stable: keeps date order and sizes after their original
    return rows, outside


def split_terms(values: list[str]) -> list[str]:
    """Accept ["pdf", "zip"] as well as ["pdf,zip"]; trim and lowercase everything."""
    return [t.strip().lower() for v in values for t in v.split(",") if t.strip()]


def expand_types(types: list[str]) -> tuple[set[str], tuple[str, ...]]:
    """Turn ['pdf', '.ZIP', 'videos'] into ({'pdf', 'zip', 'mp4', 'mov', ...}, ('video/',))."""
    extensions: set[str] = set()
    mime_prefixes: list[str] = []
    for t in split_terms(types):
        t = t.lstrip(".")
        t = TYPE_GROUP_ALIASES.get(t, t)
        extensions.update(TYPE_GROUPS.get(t, [t]))
        if t in TYPE_GROUP_MIME:
            mime_prefixes.append(TYPE_GROUP_MIME[t])
    return extensions, tuple(mime_prefixes)


def name_matches(filename: str, term: str) -> bool:
    """Substring match, or a wildcard match when the term contains * or ?."""
    if "*" in term or "?" in term:
        return fnmatch.fnmatch(filename, term)
    return term in filename


def apply_filters(rows: list[dict], names: list[str], types: list[str], excludes: list[str]) -> list[dict]:
    names, excludes = split_terms(names), split_terms(excludes)
    extensions, mime_prefixes = expand_types(types)
    kept = []
    for row in rows:
        filename = urllib.parse.unquote(urllib.parse.urlsplit(row["url"]).path.rsplit("/", 1)[-1]).lower()
        extension = filename.rsplit(".", 1)[-1] if "." in filename else ""
        mime_type = (row.get("mime_type") or "").lower()
        if names and not any(name_matches(filename, t) for t in names):
            continue
        if extensions and extension not in extensions and not (mime_prefixes and mime_type.startswith(mime_prefixes)):
            continue
        if any(name_matches(filename, t) for t in excludes):
            continue
        kept.append(row)
    return kept


def describe_filters(names: list[str], types: list[str], excludes: list[str]) -> str:
    parts = []
    if split_terms(names):
        parts.append("name contains " + " or ".join(split_terms(names)))
    if split_terms(types):
        parts.append("type " + ", ".join(split_terms(types)))
    if split_terms(excludes):
        parts.append("excluding " + ", ".join(split_terms(excludes)))
    return " | ".join(parts)


def print_rows(rows: list[dict], summary_only: bool) -> None:
    per_folder = Counter(r["folder"] for r in rows)

    if not summary_only:
        current = None
        for row in rows:
            if row["folder"] != current:
                current = row["folder"]
                print(f"\n== {current}  ({per_folder[current]} file(s)) " + "=" * 60)
                print(f"{'DATE':<11} {'KIND':<22} {'KB':>9}  URL")
            print(f"{row['date'][:10]:<11} {row['kind']:<22} {str(row['size_kb']):>9}  {row['url']}")

    originals = defaultdict(int)
    megabytes = defaultdict(float)
    for row in rows:
        if row["kind"] == "original":
            originals[row["folder"]] += 1
            megabytes[row["folder"]] += (row["size_kb"] or 0) / 1024

    print()
    print(f"{'FOLDER':<24} {'FILES':>7} {'ORIGINALS':>10} {'MB (originals)':>15}")
    print("-" * 59)
    for name in sorted(per_folder):
        print(f"{name:<24} {per_folder[name]:>7} {originals[name]:>10} {megabytes[name]:>15.1f}")
    print("-" * 59)
    print(f"{'TOTAL':<24} {len(rows):>7} {sum(originals.values()):>10} {sum(megabytes.values()):>15.1f}")

    by_type = Counter(Path(urllib.parse.urlsplit(r["url"]).path).suffix.lower() or "?" for r in rows)
    print("By extension: " + ", ".join(f"{ext} {n}" for ext, n in by_type.most_common()))


def save_csv(rows: list[dict], site: str, folder: str, filtered: bool = False) -> Path:
    host = urllib.parse.urlsplit(site).netloc.replace(":", "_")
    suffix = (folder.replace("/", "_") if folder else "all") + ("_filtered" if filtered else "")
    path = Path(__file__).resolve().parent / f"wp_media_{host}_{suffix}.csv"
    fieldnames = ["folder", "date", "kind", "mime_type", "size_kb", "width", "height", "url"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def wayback_hint(site: str, folder: str) -> str:
    host_path = re.sub(r"^https?://", "", site)
    target = f"{host_path}{UPLOADS}/{folder + '/' if folder else ''}*"
    return (
        "Fallback - the Wayback Machine's index of archived URLs in that folder:\n"
        f"  https://web.archive.org/cdx/search/cdx?url={target}&fl=original&collapse=urlkey"
    )


# ---- Main -------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="List files in any WordPress uploads folder via the REST API.")
    parser.add_argument("url", nargs="?", default=FOLDER_URL,
                        help="an uploads folder (month, year or root), a file in one, or the site address")
    parser.add_argument("--sizes", action="store_true", default=INCLUDE_SIZES, help="include resized copies")
    parser.add_argument("--all", dest="scan_all", action="store_true", default=SCAN_ALL,
                        help="scan the whole library instead of filtering by upload date")
    parser.add_argument("--summary", dest="summary_only", action="store_true", default=SUMMARY_ONLY,
                        help="print only per-folder counts")
    parser.add_argument("--no-csv", dest="save_csv", action="store_false", default=SAVE_CSV)
    parser.add_argument("--name", action="append", default=None, metavar="TEXT",
                        help="keep files whose name contains TEXT (repeat or comma-separate for OR)")
    parser.add_argument("--type", action="append", default=None, metavar="EXT",
                        help="keep only these file types, e.g. pdf or images (repeatable)")
    parser.add_argument("--exclude", action="append", default=None, metavar="TEXT",
                        help="drop files whose name contains TEXT (repeatable)")
    args = parser.parse_args()
    names = args.name if args.name is not None else NAME_CONTAINS
    types = args.type if args.type is not None else FILE_TYPES
    excludes = args.exclude if args.exclude is not None else NAME_EXCLUDES
    filters = describe_filters(names, types, excludes)

    site, folder = parse_target(args.url)
    date_range = None if args.scan_all else date_range_for(folder)
    label = f"{UPLOADS}/{folder + '/' if folder else ''}"
    is_date_folder = re.fullmatch(r"\d{4}(/\d{2})?", folder) is not None

    print(f"Listing {site}{label}")
    if date_range:
        print(f"Querying {site}/wp-json/wp/v2/media (uploads dated {folder.replace('/', '-')})")
    else:
        print(f"Querying {site}/wp-json/wp/v2/media (whole library - can take a while on big sites)")
    if filters:
        print(f"Filters: {filters}")
    if folder and not is_date_folder:
        print("  Note: folders that aren't year/month (e.g. elementor/, woocommerce_uploads/) are usually\n"
              "  created by plugins. Their files aren't in the media library, so the API may not see them.")

    try:
        items = fetch_all(site, date_range)
    except urllib.error.HTTPError as err:
        reasons = {
            401: "the media endpoint requires a login on this site",
            403: "the site is blocking the request (security plugin or bot protection)",
            404: "no WordPress REST API at that address (use the site's home URL or an uploads folder URL)",
        }
        print(f"\nHTTP {err.code}: {reasons.get(err.code, err.reason)}")
        print(wayback_hint(site, folder))
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
        print(wayback_hint(site, folder))
        sys.exit(1)

    rows, outside = build_rows(items, folder, args.sizes)
    if not rows:
        print(f"\nNo media-library files found in {label}")
        if date_range:
            print("Try SCAN_ALL = True (or --all) in case the upload dates don't match the folder.")
        print(wayback_hint(site, folder))
        sys.exit(0)

    if filters:
        before = len(rows)
        rows = apply_filters(rows, names, types, excludes)
        if not rows:
            print(f"\nNo files matched the filters ({filters}). {before} file(s) were in the folder.")
            sys.exit(0)

    print_rows(rows, args.summary_only)
    if filters:
        print(f"Filters matched {len(rows)} of {before} file(s): {filters}")
    if outside and (date_range or not folder):
        print(f"({outside} item(s) returned by the API live outside {label} and were skipped)")
    if args.save_csv:
        print(f"Saved: {save_csv(rows, site, folder, bool(filters))}")


if __name__ == "__main__":
    main()