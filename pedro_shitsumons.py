#!/usr/bin/env python3
"""Extract and translate the "NEW" Q&A messages of the PEDRO fan club site (sp.pedro.tokyo).

See SPECIFICATIONS.md section 3.12.
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests
import yaml
from bs4 import BeautifulSoup

from translator import TranslatorFactory

_BR_TOKEN = "<br>"
_BR_RE = re.compile(r"\s*<br\s*/?>\s*", re.IGNORECASE)


# --------------------------------------------------------------------------
# Config (same files as main.py: config.yaml + config.local.yaml + config.prompt.yaml)
# --------------------------------------------------------------------------

def _load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(config_path: str, config_local_path: str, prompt_path: str) -> Dict[str, Any]:
    cfg = _load_yaml(Path(config_path))
    if Path(config_local_path).exists():
        cfg = _deep_merge(cfg, _load_yaml(Path(config_local_path)))

    prompt_cfg = _load_yaml(Path(prompt_path)) if Path(prompt_path).exists() else {}
    custom_prompts = cfg.setdefault("translation", {}).setdefault("custom_prompts", {})
    custom_prompts["system_prompt"] = prompt_cfg.get("pedro_shitsumons_system_prompt", "") or ""
    custom_prompts["system_prompt_extended"] = ""
    custom_prompts["user_prompt_template"] = "{text}"
    return cfg


# --------------------------------------------------------------------------
# Site access
# --------------------------------------------------------------------------

class PedroSite:
    def __init__(self, cfg: Dict[str, Any], club: Dict[str, Any], logger: logging.Logger):
        # cfg = pedro_shitsumons section, club = shared pedro_education_club section
        self.cfg = cfg
        self.logger = logger
        self.base_url = club.get("base_url", "https://sp.pedro.tokyo").rstrip("/")
        self.list_path = cfg.get("list_path", "/qa/list/1/")
        self.delay = float(cfg.get("request_delay_seconds", 0.5))
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": club.get("user_agent") or (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
            ),
            "Accept-Language": "ja,en;q=0.8",
        })

    def _get(self, url: str) -> requests.Response:
        time.sleep(self.delay)
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding if not resp.encoding or resp.encoding.lower() == "iso-8859-1" else resp.encoding
        return resp

    @staticmethod
    def _is_login_page(resp: requests.Response) -> bool:
        return "/login" in resp.url and "plusmember" in resp.url

    def login(self, login_id: str, password: str, return_url: str) -> None:
        # Opening a protected page redirects to the login form with the right "url=" return parameter.
        login_page = self._get(return_url)
        if not self._is_login_page(login_page):
            self.logger.info("Already logged in.")
            return
        soup = BeautifulSoup(login_page.text, "html.parser")
        form = soup.find("input", attrs={"name": "form[pass]"})
        form = form.find_parent("form") if form else None
        action = urljoin(login_page.url, form.get("action") or login_page.url) if form else login_page.url
        data = {
            inp["name"]: inp.get("value") or ""
            for inp in (form.find_all("input") if form else [])
            if inp.get("name") and (inp.get("type") or "").lower() != "submit"
        }
        data.update({"form[id]": login_id, "form[pass]": password})
        time.sleep(self.delay)
        resp = self.session.post(action, data=data, headers={"Referer": login_page.url}, timeout=30)
        resp.raise_for_status()
        check = self._get(return_url)
        if self._is_login_page(check):
            raise RuntimeError(
                "Login failed: still redirected to the login page. "
                "Check pedro_education_club.login / pedro_education_club.password in config.local.yaml."
            )
        self.logger.info("Logged in.")

    def list_new_items(self, max_pages: int) -> List[Dict[str, str]]:
        items: List[Dict[str, str]] = []
        for page in range(1, max_pages + 1):
            url = urljoin(self.base_url, self.list_path) + (f"?page={page}" if page > 1 else "")
            soup = BeautifulSoup(self._get(url).text, "html.parser")
            lis = soup.select("ul.list--qa > li")
            if not lis:
                break
            reached_old = False
            for li in lis:
                if "new" not in (li.get("class") or []):
                    reached_old = True
                    continue
                a = li.find("a", href=True)
                if not a:
                    continue
                date_p = li.select_one("p.date")
                name = li.select_one("p.date span.name")
                tit = li.select_one("p.tit")
                name_text = name.get_text(strip=True) if name else ""
                date_text = date_p.get_text(" ", strip=True) if date_p else ""
                if name_text:
                    date_text = date_text.replace(name_text, "").strip()
                href = urljoin(self.base_url, a["href"])
                m = re.search(r"/detail/(\d+)", href)
                items.append({
                    "id": m.group(1) if m else href,
                    "url": href,
                    "date": date_text,
                    "author": re.sub(r"^From\s*", "", name_text),
                    "question": _clean_text(tit.get_text("\n")) if tit else "",
                })
            self.logger.info(f"Page {page}: {len(items)} NEW message(s) so far")
            if reached_old:
                break
        return items

    def fetch_detail(self, url: str, debug_dir: Optional[Path]) -> Dict[str, str]:
        resp = self._get(url)
        if self._is_login_page(resp):
            raise RuntimeError(f"Redirected to login page while opening {url} (session expired?)")
        if debug_dir is not None:
            debug_dir.mkdir(parents=True, exist_ok=True)
            m = re.search(r"/detail/(\d+)", url)
            (debug_dir / f"detail_{m.group(1) if m else 'page'}.html").write_text(resp.text, encoding="utf-8")
        return parse_detail(resp.text)


def _clean_text(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").replace("\r", "").split("\n")]
    out: List[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def _element_text(el) -> str:
    for br in el.find_all("br"):
        br.replace_with("\n")
    return _clean_text(el.get_text(""))


def parse_detail(html: str) -> Dict[str, str]:
    """Return {"question", "answer"} from a /qa/detail/<id>/ page.

    Question: `p.tit--q` (without its "QUESTION" label). Answer: `div.block__answer div.txt`,
    falling back to the container of the "ANSWER" label if the layout changes.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    question = ""
    q_el = soup.select_one("p.tit--q")
    if q_el is not None:
        label = q_el.find("span")
        if label is not None and label.get_text(strip=True).upper() == "QUESTION":
            label.decompose()
        question = _element_text(q_el)

    a_el = soup.select_one("div.block__answer div.txt")
    if a_el is not None:
        return {"question": question, "answer": _element_text(a_el)}

    # Fallback if the site layout changes: container of the "ANSWER" label.
    answer = ""
    labels = [
        el for el in soup.find_all(True)
        if el.get_text(strip=True).upper() == "ANSWER"
    ]
    # Deepest element whose whole text is just "ANSWER" is the label itself.
    labels = [el for el in labels if not any(c in labels for c in el.find_all(True))]
    for label in labels:
        node = label
        while node.parent is not None and node.parent.name not in ("body", "html", "[document]"):
            node = node.parent
            text = _element_text(BeautifulSoup(str(node), "html.parser"))
            text = re.sub(r"^\s*ANSWER\s*", "", text, flags=re.IGNORECASE).strip()
            if text:
                answer = text
                break
        if answer:
            break
    return {"question": question, "answer": answer}


# --------------------------------------------------------------------------
# Translation
# --------------------------------------------------------------------------

def _encode(text: str) -> str:
    return f" {_BR_TOKEN} ".join(text.split("\n")) if text else ""


def _decode(text: str) -> str:
    return _BR_RE.sub("\n", text or "").strip()


def translate_items(items: List[Dict[str, str]], config: Dict[str, Any], cache: Dict[str, Any],
                    source_lang: str, target_lang: str, logger: logging.Logger) -> None:
    todo = []
    for it in items:
        cached = cache.get(it["id"])
        if (cached and cached.get("target_lang") == target_lang
                and cached.get("question") == it["question"] and cached.get("answer") == it["answer"]):
            it["question_tr"] = cached["question_tr"]
            it["answer_tr"] = cached["answer_tr"]
        else:
            todo.append(it)
    if not todo:
        logger.info("All messages already translated (cache).")
        return

    logger.info(f"Translating {len(todo)} message(s) ({len(items) - len(todo)} from cache)")
    segments: List[str] = []
    refs = []
    for it in todo:
        for field in ("question", "answer"):
            if it[field]:
                refs.append((it, field))
                segments.append(_encode(it[field]))

    translator = TranslatorFactory.create_translator(config)
    result = translator.translate_segments(segments, source_lang, target_lang)
    translated = [s.translated_text for s in (result.segments or [])]
    if not result.success:
        logger.error(f"Translation failed: {result.error}")

    done = {}
    for (it, field), text in zip(refs, translated):
        done[(it["id"], field)] = _decode(text)
    for it in todo:
        if any(it[f] and (it["id"], f) not in done for f in ("question", "answer")):
            continue
        it["question_tr"] = done.get((it["id"], "question"), "")
        it["answer_tr"] = done.get((it["id"], "answer"), "")
        cache[it["id"]] = {
            "target_lang": target_lang,
            "question": it["question"],
            "answer": it["answer"],
            "question_tr": it["question_tr"],
            "answer_tr": it["answer_tr"],
        }


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def write_markdown(items: List[Dict[str, str]], path: Path, source_lang: str, target_lang: str) -> None:
    src, tgt = source_lang.upper(), target_lang.upper()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# PEDRO SHITSUMONS — messages NEW ({len(items)}) — extrait le {now}", ""]
    for it in items:
        lines += [f"## {it['date']} — {it['author']}", "", it["url"], ""]
        if it.get("question_tr") is not None:
            lines += [f"**Question ({tgt})**", "", it["question_tr"], "", f"**Réponse ({tgt})**", "",
                      it.get("answer_tr") or "(pas de réponse)", ""]
        lines += [f"<details><summary>Original ({src})</summary>", "", "**Question**", "", it["question"], "",
                  "**ANSWER**", "", it["answer"] or "(pas de réponse)", "", "</details>", "", "---", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

class _ProgressBar:
    """One-line console progress bar (falls back to one log line per step when not a console)."""

    def __init__(self, label: str, total: int, logger: logging.Logger, width: int = 30):
        self.label, self.total, self.logger, self.width = label, total, logger, width
        self.start = time.time()
        self.interactive = sys.stderr.isatty()

    def update(self, done: int, detail: str = "") -> None:
        if not self.interactive:
            if done:
                self.logger.info(f"{self.label} [{done}/{self.total}] {detail}")
            return
        filled = int(self.width * done / self.total) if self.total else self.width
        bar = "#" * filled + "." * (self.width - filled)
        eta = ""
        if 0 < done < self.total:
            remaining = (time.time() - self.start) / done * (self.total - done)
            eta = f" ~{int(remaining // 60):02d}:{int(remaining % 60):02d} restant"
        line = f"{self.label} [{bar}] {done}/{self.total} ({100 * done // max(self.total, 1)}%){eta}"
        sys.stderr.write("\r" + line.ljust(90))
        sys.stderr.flush()

    def finish(self) -> None:
        if self.interactive:
            sys.stderr.write("\n")
            sys.stderr.flush()


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Extract + translate NEW Q&A messages from sp.pedro.tokyo")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--config-local", default="config.local.yaml")
    p.add_argument("--config-prompt", default="config.prompt.yaml")
    p.add_argument("--max-pages", type=int, default=None, help="Max list pages to scan (default: pedro_shitsumons.max_pages)")
    p.add_argument("--target-lang", default=None, help="Override target language, e.g. en")
    p.add_argument("--dest", default=None, help="Override output folder")
    p.add_argument("--no-translate", action="store_true", help="Only extract, do not translate")
    p.add_argument("--no-open", action="store_true", help="Do not open the result file at the end")
    p.add_argument("--debug-html", action="store_true", help="Save raw detail pages in temp/pedro_shitsumons/html")
    return p


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    args = build_arg_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logger = logging.getLogger("pedro_shitsumons")

    config = load_config(args.config, args.config_local, args.config_prompt)
    pcfg = config.get("pedro_shitsumons", {}) or {}
    club = config.get("pedro_education_club", {}) or {}
    login_id, password = str(club.get("login") or ""), str(club.get("password") or "")
    if not login_id or not password:
        logger.error("Missing pedro_education_club.login / pedro_education_club.password in config.local.yaml (see config.local.yaml.example).")
        return 1

    tcfg = config.get("translation", {}) or {}
    source_lang = tcfg.get("source_language", "ja")
    target_lang = args.target_lang or tcfg.get("target_language", "fr")
    temp_dir = Path(pcfg.get("temp_directory") or "./temp/pedro_shitsumons")
    out_dir = Path(args.dest or pcfg.get("output_directory") or "./output/pedro_shitsumons")

    site = PedroSite(pcfg, club, logger)
    items = site.list_new_items(args.max_pages or int(pcfg.get("max_pages", 10)))
    if not items:
        logger.info("No NEW message found.")
        return 0
    logger.info(f"{len(items)} NEW message(s) found")

    site.login(login_id, password, items[0]["url"])
    debug_dir = temp_dir / "html" if args.debug_html else None
    progress = _ProgressBar("Messages", len(items), logger)
    progress.update(0)
    try:
        for i, it in enumerate(items, 1):
            detail = site.fetch_detail(it["url"], debug_dir)
            if detail["question"] and len(detail["question"]) >= len(it["question"]):
                it["question"] = detail["question"]
            it["answer"] = detail["answer"]
            progress.update(i, it["url"])
    finally:
        progress.finish()
    for it in items:
        if not it["answer"]:
            logger.warning(f"No ANSWER block found on {it['url']} (use --debug-html to inspect the page)")

    if not args.no_translate:
        cache_path = temp_dir / "translation_cache.json"
        cache: Dict[str, Any] = {}
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                cache = {}
        try:
            translate_items(items, config, cache, source_lang, target_lang, logger)
        finally:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    out_path = out_dir / f"pedro_shitsumons_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.md"
    write_markdown(items, out_path, source_lang, target_lang)
    logger.info(f"Written: {out_path.resolve()}")

    if not args.no_open and pcfg.get("open_output", True) and os.name == "nt":
        os.startfile(str(out_path.resolve()))
    missing = sum(1 for it in items if not args.no_translate and it.get("question_tr") is None)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
