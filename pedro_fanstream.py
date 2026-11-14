#!/usr/bin/env python3
"""Download + subtitle the latest PEDRO fan club live stream ("生配信") from sp.pedro.tokyo/movies.

Finds the most recent movie whose title matches `pedro_fanstream.title_pattern`, logs in to the
fan club, resolves its HLS stream at the best available quality, then runs the regular
spo-translate-video pipeline (main.py) on it. See SPECIFICATIONS.md section 3.13.
"""

import argparse
import logging
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from pedro_shitsumons import PedroSite, load_config

SCRIPT_DIR = Path(__file__).resolve().parent


def list_movies(site: PedroSite, list_path: str) -> List[Dict[str, str]]:
    soup = BeautifulSoup(site._get(urljoin(site.base_url, list_path)).text, "html.parser")
    movies = []
    for li in soup.select("ul.list--contents > li"):
        a = li.find("a", href=re.compile(r"/movies/detail/\d+"))
        if not a:
            continue
        date_p, tit = li.select_one("p.date"), li.select_one("p.tit")
        movies.append({
            "id": re.search(r"/movies/detail/(\d+)", a["href"]).group(1),
            "url": urljoin(site.base_url, a["href"]),
            "date": date_p.get_text(strip=True) if date_p else "",
            "title": tit.get_text(" ", strip=True) if tit else "",
        })
    return movies


def find_stream_url(detail_html: str, detail_url: str) -> Optional[str]:
    """The video.js player declares its HLS source as <source src="/movies/detail/play/<id>" type="application/x-mpegURL">."""
    soup = BeautifulSoup(detail_html, "html.parser")
    for src in soup.find_all("source", src=True):
        if "mpegurl" in (src.get("type") or "").lower() or ".m3u8" in src["src"].lower():
            return urljoin(detail_url, src["src"])
    m = re.search(r"""["']([^"']+\.m3u8[^"']*)["']""", detail_html)
    return urljoin(detail_url, m.group(1)) if m else None


def best_variant(site: PedroSite, master_url: str, referer: str, logger: logging.Logger) -> str:
    """Return the highest-resolution (then highest-bandwidth) variant of an HLS master playlist."""
    resp = site.session.get(master_url, headers={"Referer": referer}, timeout=30)
    resp.raise_for_status()
    lines = [l.strip() for l in resp.text.splitlines()]
    variants: List[Tuple[int, int, str, str]] = []
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        uri = next((l for l in lines[i + 1:] if l and not l.startswith("#")), None)
        if not uri:
            continue
        res = re.search(r"RESOLUTION=(\d+)x(\d+)", line)
        bw = re.search(r"(?<![-A-Z])BANDWIDTH=(\d+)", line)
        height = int(res.group(2)) if res else 0
        variants.append((height, int(bw.group(1)) if bw else 0, res.group(0)[11:] if res else "?", urljoin(resp.url, uri)))
    if not variants:
        logger.info("Stream is not a master playlist: using it as-is")
        return resp.url
    variants.sort(reverse=True)
    logger.info("Available qualities: " + ", ".join(v[2] for v in variants))
    logger.info(f"Selected best quality: {variants[0][2]}")
    return variants[0][3]


def check_dest_writable(dest: Path) -> Optional[str]:
    try:
        dest.mkdir(parents=True, exist_ok=True)
        probe = dest / f".write_test_{uuid.uuid4().hex}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return None
    except OSError as e:
        return (
            f"Destination folder not accessible: {dest} ({e}).\n"
            "  - Network drive disconnected? Open it once in Explorer to reconnect it.\n"
            "  - Mapped drives (Z:) are not visible from an elevated (admin) console: run it as a normal user,\n"
            "    or use a UNC path (\\\\server\\share\\...) for pedro_fanstream.output_directory in config.yaml."
        )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Download + subtitle the latest PEDRO fan club live stream (sp.pedro.tokyo/movies)")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--config-local", default="config.local.yaml")
    p.add_argument("--config-prompt", default="config.prompt.yaml")
    p.add_argument("--dest", default=None, help="Override destination folder (default: pedro_fanstream.output_directory)")
    p.add_argument("--dry-run", action="store_true", help="Find the stream and print the pipeline command, without running it")
    p.add_argument("--force", action="store_true", help="Process even if the video already exists in the destination folder")
    p.add_argument("--resume", action="store_true", help="Passed to spo-translate-video (resume a failed run)")
    p.add_argument("--download-only", action="store_true", help="Passed to spo-translate-video (download only)")
    p.add_argument("--debug-html", action="store_true", help="Save the raw detail page in temp/pedro_fanstream/")
    return p


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    args = build_arg_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logger = logging.getLogger("pedro_fanstream")

    config = load_config(args.config, args.config_local, args.config_prompt)
    fcfg = config.get("pedro_fanstream", {}) or {}
    club = config.get("pedro_education_club", {}) or {}
    login_id, password = str(club.get("login") or ""), str(club.get("password") or "")
    if not login_id or not password:
        logger.error("Missing pedro_education_club.login / pedro_education_club.password in config.local.yaml (see config.local.yaml.example).")
        return 1

    site = PedroSite(fcfg, club, logger)
    pattern = re.compile(str(fcfg.get("title_pattern") or "生配信"))
    movies = list_movies(site, str(fcfg.get("list_path") or "/movies/list/5/0/"))
    if not movies:
        logger.error("No movie found in the movie list (site layout changed?).")
        return 1
    movie = next((m for m in movies if pattern.search(m["title"])), None)
    if not movie:
        logger.error(f"No movie matching '{pattern.pattern}' on the first page of the movie list.")
        return 1
    date_digits = re.sub(r"\D", "", movie["date"])
    if len(date_digits) != 8:
        logger.error(f"Unexpected publication date format: '{movie['date']}'")
        return 1
    basename = str(fcfg.get("filename_template") or "{date} FC stream").format(date=date_digits)
    logger.info(f"Latest live stream: {movie['date']} — {movie['title']} — {movie['url']}")
    logger.info(f"Output name: {basename}")

    dest = Path(args.dest or fcfg.get("output_directory") or "./output/pedro_fanstream")
    if not args.dry_run:
        err = check_dest_writable(dest)
        if err:
            logger.error(err)
            return 1
    if (dest / f"{basename}.mp4").exists() and not args.force and not args.resume:
        logger.info(f"Already processed: {dest / (basename + '.mp4')} exists (use --force to process it again).")
        return 0

    site.login(login_id, password, movie["url"])
    detail = site._get(movie["url"])
    if site._is_login_page(detail):
        logger.error(f"Redirected to the login page while opening {movie['url']}")
        return 1
    if args.debug_html:
        debug_dir = Path("./temp/pedro_fanstream")
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"movie_{movie['id']}.html").write_text(detail.text, encoding="utf-8")
    master = find_stream_url(detail.text, detail.url)
    if not master:
        logger.error("No HLS stream found in the movie page (site layout changed? try --debug-html).")
        return 1
    logger.info(f"Stream: {master}")
    stream_url = best_variant(site, master, detail.url, logger)

    cmd = [
        sys.executable, str(SCRIPT_DIR / "main.py"), stream_url,
        "--config", args.config, "--config-local", args.config_local,
        "--dest", str(dest),
        "--output-basename", basename,
        "--prompt-key", str(fcfg.get("prompt_key") or "pedro_fanstream_prompt_extended"),
    ]
    if args.resume:
        cmd.append("--resume")
    if args.download_only:
        cmd.append("--download-only")
    logger.info("Pipeline command: " + subprocess.list2cmdline(cmd))
    if args.dry_run:
        return 0
    return subprocess.call(cmd, cwd=str(SCRIPT_DIR))


if __name__ == "__main__":
    sys.exit(main())
