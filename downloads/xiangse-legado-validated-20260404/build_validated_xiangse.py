#!/usr/bin/env python3
"""Validate Legado sources over real HTTP and build a Xiangse package from only working sources."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import requests
import urllib3
from lxml import html as lxml_html

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_PREFERRED_INPUT = "/tmp/tickmao_novel/sources/legado/main/full.json"
DEFAULT_FALLBACK_INPUT = "/tmp/tickmao_novel/sources/legado/full.json"
DEFAULT_OUT_DIR = "/root/legado_xiangse_batch/out"
DEFAULT_KEYWORDS = [
    "斗罗大陆",
    "凡人修仙传",
    "诡秘之主",
    "快穿",
    "总裁",
    "盗墓笔记",
]
DEFAULT_TARGET_MIN = 20
DEFAULT_MAX_KEEP = 50
DEFAULT_CANDIDATE_LIMIT = 120
DEFAULT_TIMEOUT = 12
DEFAULT_WORKERS = 8
DEFAULT_MIN_CONTENT_CHARS = 80
DEFAULT_MIN_CONTENT_CJK = 20
DEFAULT_PACKAGE_BASENAME = "xiangse_package_validated"
DEFAULT_MINI_APP_VERSION = "2.56.1"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

SUPPORTED_RULE_KINDS = {"simple", "empty", "replace"}


@dataclass
class StepResult:
    ok: bool
    url: str = ""
    http_status: int | None = None
    count: int | None = None
    note: str = ""


@dataclass
class ValidationResult:
    index: int
    name: str
    source_url: str
    complexity: int
    keyword: str = ""
    selected_book_name: str = ""
    detail_url: str = ""
    toc_url: str = ""
    chapter_url: str = ""
    supported: bool = True
    support_note: str = ""
    search: StepResult | None = None
    detail: StepResult | None = None
    toc: StepResult | None = None
    content: StepResult | None = None
    passed: bool = False
    pass_level: str = ""
    note: str = ""


def load_converter_module(script_path: Path):
    spec = importlib.util.spec_from_file_location("convert_legado_to_xiangse", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load converter module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def rule_kind(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "empty"
    text = value.strip()
    lowered = text.lower()
    if lowered.startswith("xpath://") or lowered.startswith("//"):
        return "xpath"
    if "$." in text or "$[" in text or "jsonpath" in lowered or "@json" in lowered:
        return "json"
    if "@js" in lowered or "<js" in lowered:
        return "js"
    if "##" in text:
        return "replace"
    if "&&" in text or "||" in text:
        return "compose"
    return "simple"


def sanitize_source_url(source_url: str) -> str:
    text = (source_url or "").strip()
    if not text:
        return text
    text = text.replace("\r", "").replace("\n", "").strip()
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        return parsed._replace(fragment="").geturl()
    return text.split("#", 1)[0].strip()


def supported_source_note(source: dict[str, Any]) -> tuple[bool, str]:
    try:
        blob = json.dumps(source, ensure_ascii=False)
    except Exception:
        blob = str(source)
    if "java." in blob:
        return False, "contains java.* runtime"

    search_url = str(source.get("searchUrl") or "").strip()
    if not search_url:
        return False, "searchUrl empty"
    if search_url.startswith("@js"):
        return False, "searchUrl uses @js"

    if any(marker in search_url for marker in ("source.getKey", "cookie.removeCookie", "baseUrl", "java.")):
        return False, "searchUrl contains runtime-only prelude"

    list_fields = [
        ("ruleSearch", "bookList"),
        ("ruleToc", "chapterList"),
    ]
    for section, key in list_fields:
        rule = source.get(section) if isinstance(source.get(section), dict) else {}
        kind = rule_kind(rule.get(key, ""))
        if kind not in {"simple", "empty"}:
            return False, f"unsupported {section}.{key}: {kind}"

    value_fields = [
        ("ruleSearch", "name"),
        ("ruleSearch", "bookUrl"),
        ("ruleToc", "chapterName"),
        ("ruleToc", "chapterUrl"),
        ("ruleContent", "content"),
        ("ruleBookInfo", "tocUrl"),
    ]
    for section, key in value_fields:
        rule = source.get(section) if isinstance(source.get(section), dict) else {}
        kind = rule_kind(rule.get(key, ""))
        if kind not in SUPPORTED_RULE_KINDS:
            return False, f"unsupported {section}.{key}: {kind}"

    return True, "supported simple-rule source"


def split_search_url_and_options(search_url: str) -> tuple[str, dict[str, Any]]:
    text = (search_url or "").strip()
    matched = re.match(r"(?s)^(.*?),(\s*\{.*\})\s*$", text)
    if not matched:
        return text, {}

    prefix, raw_obj = matched.group(1), matched.group(2)
    for loader in (json.loads, ast.literal_eval):
        try:
            obj = loader(raw_obj)
            if isinstance(obj, dict):
                return prefix.strip(), obj
        except Exception:
            pass
    return text, {}


def strip_runtime_prelude(text: str) -> str:
    s = (text or "").strip()
    while s.startswith("{{"):
        end = s.find("}}")
        if end < 0:
            break
        prelude = s[: end + 2]
        if any(token in prelude for token in ("cookie.", "source.", "url=", "baseUrl", "java.")):
            s = s[end + 2 :].lstrip()
            continue
        break
    return s


def render_template(text: str, keyword: str, page: int, *, charset: str = "utf-8", encode_keyword: bool) -> str:
    def repl(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        if expr in {"key", "keyWord", "keyword"}:
            if encode_keyword:
                return quote(keyword.encode(charset, errors="ignore"))
            return keyword
        if expr in {"page", "pageIndex", "pageNo", "p"}:
            return str(page)
        if expr == "page-1":
            return str(page - 1)
        if expr in {"key[0]", "keyWord[0]"}:
            first = keyword[:1]
            if encode_keyword:
                return quote(first.encode(charset, errors="ignore"))
            return first
        return ""

    return re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", repl, text or "")


def pick_encoding(resp: requests.Response, preferred: str = "") -> str:
    candidates: list[str] = []
    if preferred:
        candidates.append(preferred)
    if resp.encoding and resp.encoding.lower() not in {"iso-8859-1", "ascii"}:
        candidates.append(resp.encoding)
    if getattr(resp, "apparent_encoding", None):
        candidates.append(str(resp.apparent_encoding))
    if resp.encoding:
        candidates.append(resp.encoding)
    candidates.extend(["utf-8", "gb18030", "gbk"])

    seen: set[str] = set()
    for item in candidates:
        if not item:
            continue
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        try:
            "".encode(item)
            return item
        except LookupError:
            continue
    return "utf-8"


def decode_response(resp: requests.Response, preferred: str = "") -> str:
    encoding = pick_encoding(resp, preferred)
    try:
        return resp.content.decode(encoding, errors="ignore")
    except Exception:
        return resp.text


def collapse_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def plain_text_from_node(node: Any) -> str:
    if isinstance(node, str):
        return collapse_text(node)
    try:
        return collapse_text("".join(node.itertext()))
    except Exception:
        return collapse_text(str(node))


def inner_html(node: Any) -> str:
    if isinstance(node, str):
        return str(node)
    try:
        return "".join(lxml_html.tostring(child, encoding="unicode") for child in node)
    except Exception:
        return ""


def html_fragment_to_text(fragment: str) -> str:
    fragment = (fragment or "").strip()
    if not fragment:
        return ""
    try:
        wrapper = lxml_html.fromstring(f"<div>{fragment}</div>")
        return collapse_text(wrapper.text_content())
    except Exception:
        return collapse_text(fragment)


def cjk_count(text: str) -> int:
    return len(re.findall(r"[\u3400-\u9fff]", text or ""))


def parse_general_selector(token: str) -> tuple[str, str | None, list[str], int | None]:
    idx: int | None = None
    token = token.strip()
    if token.startswith("."):
        raw = token[1:].split(".") if token[1:] else []
        if raw and re.fullmatch(r"-?\d+", raw[-1]):
            idx = int(raw.pop())
        return "*", None, [part for part in raw if part], idx
    if token.startswith("#"):
        body = token[1:]
        id_value = body
        classes: list[str] = []
        if "." in body:
            first, *rest = body.split(".")
            id_value = first
            if rest and re.fullmatch(r"-?\d+", rest[-1]):
                idx = int(rest.pop())
            classes = [part for part in rest if part]
        return "*", id_value, classes, idx

    matched = re.match(r"^([a-zA-Z0-9_\-*]+)(.*)$", token)
    if not matched:
        return "*", None, [], None
    tag = matched.group(1)
    rest = matched.group(2)
    id_value: str | None = None
    classes: list[str] = []
    while rest:
        if rest.startswith("#"):
            rest = rest[1:]
            id_match = re.match(r"^([a-zA-Z0-9_\-]+)(.*)$", rest)
            if not id_match:
                break
            id_value = id_match.group(1)
            rest = id_match.group(2)
        elif rest.startswith("."):
            rest = rest[1:]
            cls_match = re.match(r"^([a-zA-Z0-9_\-]+)(.*)$", rest)
            if not cls_match:
                break
            classes.append(cls_match.group(1))
            rest = cls_match.group(2)
        else:
            break
    if classes and re.fullmatch(r"-?\d+", classes[-1]):
        idx = int(classes.pop())
    return tag, id_value, classes, idx


def apply_component(nodes: list[Any], raw_token: str) -> list[Any]:
    token = raw_token.strip()
    if not token:
        return []

    if token.startswith("text."):
        needle = token[5:]
        found: list[Any] = []
        for node in nodes:
            try:
                iterator = node.iter()
            except Exception:
                iterator = []
            for element in iterator:
                text = "".join(element.itertext()) if hasattr(element, "itertext") else str(element)
                if needle and needle in text:
                    found.append(element)
        return found

    forced_prefix: str | None = None
    body = token
    if token.startswith("class."):
        forced_prefix = "class"
        body = token[6:]
    elif token.startswith("id."):
        forced_prefix = "id"
        body = token[3:]
    elif token.startswith("tag."):
        forced_prefix = "tag"
        body = token[4:]

    parts = [part for part in body.split() if part]
    current = nodes
    for part in parts:
        current = apply_single_part(current, part, forced_prefix if len(parts) == 1 else None)
        forced_prefix = None
    return current


def apply_single_part(nodes: list[Any], part: str, forced_prefix: str | None = None) -> list[Any]:
    idx: int | None = None
    found: list[Any] = []

    if forced_prefix == "class":
        body = part
        matched = re.match(r"^(.*)\.(-?\d+)$", body)
        if matched:
            body, idx = matched.group(1), int(matched.group(2))
        wanted = [seg for seg in body.split() if seg]
        for node in nodes:
            matches: list[Any] = []
            try:
                iterator = node.iter()
            except Exception:
                iterator = []
            for element in iterator:
                classes = set((element.get("class") or "").split()) if hasattr(element, "get") else set()
                if all(cls in classes for cls in wanted):
                    matches.append(element)
            if idx is None:
                found.extend(matches)
            elif matches:
                try:
                    found.append(matches[idx])
                except IndexError:
                    pass
        return found

    if forced_prefix == "id":
        body = part
        matched = re.match(r"^(.*)\.(-?\d+)$", body)
        if matched:
            body, idx = matched.group(1), int(matched.group(2))
        for node in nodes:
            matches: list[Any] = []
            try:
                iterator = node.iter()
            except Exception:
                iterator = []
            for element in iterator:
                if hasattr(element, "get") and element.get("id") == body:
                    matches.append(element)
            if idx is None:
                found.extend(matches)
            elif matches:
                try:
                    found.append(matches[idx])
                except IndexError:
                    pass
        return found

    if forced_prefix == "tag":
        tag, id_value, classes, idx = parse_general_selector(part)
    else:
        tag, id_value, classes, idx = parse_general_selector(part)

    for node in nodes:
        matches: list[Any] = []
        try:
            iterator = node.iter() if tag == "*" else node.iter(tag)
        except Exception:
            iterator = []
        for element in iterator:
            if id_value and (not hasattr(element, "get") or element.get("id") != id_value):
                continue
            element_classes = set((element.get("class") or "").split()) if hasattr(element, "get") else set()
            if any(cls not in element_classes for cls in classes):
                continue
            matches.append(element)
        if idx is None:
            found.extend(matches)
        elif matches:
            try:
                found.append(matches[idx])
            except IndexError:
                pass
    return found


def split_rule_base_and_replacements(rule: str) -> tuple[str, list[tuple[str, str]]]:
    text = (rule or "").strip()
    if "##" not in text:
        return text, []
    base, tail = text.split("##", 1)
    ops: list[tuple[str, str]] = []
    chunks = [chunk for chunk in tail.split("###") if chunk != ""] or [tail]
    for chunk in chunks:
        bits = chunk.split("##")
        if not bits:
            continue
        pattern = bits[0]
        repl = "##".join(bits[1:]) if len(bits) > 1 else ""
        if pattern:
            ops.append((pattern, repl))
    return base.strip(), ops


def apply_replacements(values: list[str], replacements: list[tuple[str, str]]) -> list[str]:
    if not replacements:
        return values
    replaced: list[str] = []
    for value in values:
        item = value
        for pattern, repl in replacements:
            py_repl = re.sub(r"\$(\d+)", r"\\\1", repl)
            try:
                item = re.sub(pattern, py_repl, item)
            except re.error:
                item = item.replace(pattern, repl)
        replaced.append(item)
    return replaced


def split_rule_tokens(rule: str) -> list[str]:
    base, _ = split_rule_base_and_replacements(rule)
    return [part.strip() for part in base.split("@") if part.strip()]


def select_nodes(context_nodes: list[Any], rule: str) -> list[Any]:
    tokens = split_rule_tokens(rule)
    current = context_nodes
    for token in tokens:
        current = apply_component(current, token)
    return current


def extract_values(context_nodes: list[Any], rule: str) -> list[str]:
    base_rule, replacements = split_rule_base_and_replacements(rule)
    tokens = [part.strip() for part in base_rule.split("@") if part.strip()]
    if not tokens:
        return []
    current = context_nodes
    for token in tokens[:-1]:
        current = apply_component(current, token)

    last = tokens[-1]
    values: list[str] = []
    if last in {"text", "textNodes"}:
        values = [plain_text_from_node(node) for node in current]
    elif last == "html":
        values = [inner_html(node) for node in current]
    elif re.fullmatch(r"[A-Za-z_:][-A-Za-z0-9_:.]*", last):
        for node in current:
            if hasattr(node, "get"):
                value = node.get(last)
                if value is not None:
                    values.append(str(value).strip())
    if not values and last not in {"text", "textNodes", "html"}:
        current = apply_component(current, last)
        values = [plain_text_from_node(node) for node in current]
    values = [value for value in values if value not in {"", None}]
    return apply_replacements(values, replacements)


def first_value(context_nodes: list[Any], rule: str) -> str:
    values = extract_values(context_nodes, rule)
    return values[0] if values else ""


def resolve_url(base_url: str, value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if text.startswith("javascript:") or text.startswith("mailto:"):
        return ""
    if text.startswith("//"):
        parsed = urlparse(base_url)
        scheme = parsed.scheme or "https"
        return f"{scheme}:{text}"
    return urljoin(base_url, text)


def build_headers(converter_module: Any, source: dict[str, Any], opts: dict[str, Any]) -> dict[str, str]:
    headers = converter_module.parse_headers(source.get("header"), source.get("httpUserAgent"))
    extra_headers: dict[str, str] = {}
    for key in ("headers", "header", "httpHeaders"):
        value = opts.get(key)
        if isinstance(value, dict):
            for header_key, header_value in value.items():
                if header_key is not None and header_value is not None:
                    extra_headers[str(header_key)] = str(header_value)
    headers.update(extra_headers)
    if "User-Agent" not in headers:
        headers["User-Agent"] = USER_AGENT
    return headers


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    referer: str = "",
) -> requests.Response:
    req_headers = dict(headers or {})
    if referer and "Referer" not in req_headers:
        req_headers["Referer"] = referer
    last_error: Exception | None = None
    for _ in range(2):
        try:
            return session.request(
                method=method,
                url=url,
                headers=req_headers,
                data=body,
                timeout=(6, timeout),
                allow_redirects=True,
                verify=False,
            )
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(0.3)
    if last_error is None:
        raise RuntimeError(f"request failed for unknown reason: {url}")
    raise last_error


def pick_search_hit(item_nodes: list[Any], source: dict[str, Any], base_url: str, keyword: str) -> tuple[str, str, int] | None:
    rs = source.get("ruleSearch") if isinstance(source.get("ruleSearch"), dict) else {}
    hits: list[tuple[int, str, str]] = []
    for item_node in item_nodes[:15]:
        name = first_value([item_node], str(rs.get("name") or ""))
        book_url = resolve_url(base_url, first_value([item_node], str(rs.get("bookUrl") or "")))
        if not name or not book_url:
            continue
        score = 0
        if keyword and keyword in name:
            score += 100
        if book_url.startswith("http"):
            score += 20
        score -= max(0, len(name) // 20)
        hits.append((score, name, book_url))
    if not hits:
        return None
    hits.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    score, name, book_url = hits[0]
    return name, book_url, score


def pick_chapter_candidates(chapter_nodes: list[Any], source: dict[str, Any], base_url: str) -> list[tuple[str, str]]:
    rt = source.get("ruleToc") if isinstance(source.get("ruleToc"), dict) else {}
    candidates: list[tuple[str, str]] = []
    for node in chapter_nodes[:20]:
        title = first_value([node], str(rt.get("chapterName") or ""))
        chapter_url = resolve_url(base_url, first_value([node], str(rt.get("chapterUrl") or "")))
        if not chapter_url:
            continue
        title = title or chapter_url.rsplit("/", 1)[-1]
        lowered = title.lower()
        if any(bad in lowered for bad in ("目录", "返回", "上一页")):
            continue
        candidates.append((title, chapter_url))
    return candidates[:5]


def content_is_good(text: str, *, min_chars: int, min_cjk: int) -> bool:
    plain = collapse_text(text)
    if len(plain) >= min_chars:
        return True
    return cjk_count(plain) >= min_cjk


def fetch_tree(resp: requests.Response, preferred_encoding: str = "") -> tuple[str, Any]:
    text = decode_response(resp, preferred_encoding)
    root = lxml_html.fromstring(text, base_url=resp.url)
    return text, root


def validate_one_source(
    candidate: Any,
    converter_module: Any,
    keywords: list[str],
    timeout: int,
    min_content_chars: int,
    min_content_cjk: int,
) -> ValidationResult:
    source = candidate.source
    result = ValidationResult(
        index=candidate.idx,
        name=str(source.get("bookSourceName") or candidate.name),
        source_url=sanitize_source_url(str(source.get("bookSourceUrl") or "")),
        complexity=int(candidate.score),
        search=StepResult(ok=False),
        detail=StepResult(ok=False),
        toc=StepResult(ok=False),
        content=StepResult(ok=False),
    )

    supported, note = supported_source_note(source)
    result.supported = supported
    result.support_note = note
    if not supported:
        result.note = note
        return result

    raw_search_url = str(source.get("searchUrl") or "")
    search_tpl, opts = split_search_url_and_options(raw_search_url)
    search_tpl = strip_runtime_prelude(search_tpl)
    if not search_tpl:
        result.note = "searchUrl empty after prelude stripping"
        return result

    charset = str(opts.get("charset") or "utf-8").strip() or "utf-8"
    method = str(opts.get("method") or "GET").strip().upper() or "GET"
    base_source_url = sanitize_source_url(str(source.get("bookSourceUrl") or ""))
    headers = build_headers(converter_module, source, opts)
    if method == "POST" and "Content-Type" not in headers:
        headers["Content-Type"] = f"application/x-www-form-urlencoded; charset={charset}"

    search_exception: str = ""
    for keyword in keywords:
        session = make_session()
        session.headers.update(headers)
        search_url = render_template(search_tpl, keyword, 1, charset=charset, encode_keyword=True)
        search_url = resolve_url(base_source_url, search_url)
        if not search_url:
            continue

        body_bytes = None
        if method == "POST":
            rendered_body = render_template(str(opts.get("body") or ""), keyword, 1, charset=charset, encode_keyword=False)
            body_bytes = rendered_body.encode(charset, errors="ignore")

        try:
            search_resp = request_with_retry(
                session,
                method,
                search_url,
                headers=headers,
                body=body_bytes,
                timeout=timeout,
                referer=base_source_url,
            )
            _, search_root = fetch_tree(search_resp, preferred_encoding=charset)
        except Exception as exc:
            search_exception = str(exc)
            continue

        try:
            search_items = select_nodes([search_root], str((source.get("ruleSearch") or {}).get("bookList") or ""))
            search_hit = pick_search_hit(search_items, source, search_resp.url, keyword)
        except Exception as exc:
            result.search = StepResult(ok=False, url=search_url, http_status=getattr(search_resp, "status_code", None), note=f"search parse failed: {exc}")
            continue

        if not search_hit:
            result.search = StepResult(ok=False, url=search_resp.url, http_status=search_resp.status_code, count=len(search_items), note="search empty or missing name/url")
            continue

        book_name, detail_url, _ = search_hit
        result.keyword = keyword
        result.selected_book_name = book_name
        result.detail_url = detail_url
        result.search = StepResult(ok=True, url=search_resp.url, http_status=search_resp.status_code, count=len(search_items), note=f"matched {book_name}")

        try:
            detail_resp = request_with_retry(session, "GET", detail_url, headers=headers, timeout=timeout, referer=search_resp.url)
            detail_text, detail_root = fetch_tree(detail_resp)
        except Exception as exc:
            result.detail = StepResult(ok=False, url=detail_url, note=f"detail request failed: {exc}")
            result.note = result.detail.note
            continue

        rb = source.get("ruleBookInfo") if isinstance(source.get("ruleBookInfo"), dict) else {}
        toc_url_value = ""
        toc_rule = str(rb.get("tocUrl") or "")
        if toc_rule:
            try:
                toc_url_value = resolve_url(detail_resp.url, first_value([detail_root], toc_rule))
            except Exception:
                toc_url_value = ""
        if not toc_url_value:
            toc_url_value = detail_resp.url
        result.toc_url = toc_url_value
        result.detail = StepResult(
            ok=detail_resp.status_code < 400 and len(detail_text) > 200,
            url=detail_resp.url,
            http_status=detail_resp.status_code,
            note="detail fetched",
        )

        try:
            toc_resp = request_with_retry(session, "GET", toc_url_value, headers=headers, timeout=timeout, referer=detail_resp.url)
            _, toc_root = fetch_tree(toc_resp)
            chapter_nodes = select_nodes([toc_root], str((source.get("ruleToc") or {}).get("chapterList") or ""))
            chapter_candidates = pick_chapter_candidates(chapter_nodes, source, toc_resp.url)
        except Exception as exc:
            result.toc = StepResult(ok=False, url=toc_url_value, note=f"toc request/parse failed: {exc}")
            result.note = result.toc.note
            continue

        if not chapter_candidates:
            result.toc = StepResult(ok=False, url=toc_resp.url, http_status=toc_resp.status_code, count=len(chapter_nodes), note="chapter list empty")
            result.note = result.toc.note
            continue

        result.toc = StepResult(ok=True, url=toc_resp.url, http_status=toc_resp.status_code, count=len(chapter_candidates), note=f"sample {chapter_candidates[0][0]}")

        content_ok = False
        content_note = ""
        for chapter_title, chapter_url in chapter_candidates:
            result.chapter_url = chapter_url
            try:
                chapter_resp = request_with_retry(session, "GET", chapter_url, headers=headers, timeout=timeout, referer=toc_resp.url)
                _, chapter_root = fetch_tree(chapter_resp)
                content_rule = str((source.get("ruleContent") or {}).get("content") or "")
                raw_values = extract_values([chapter_root], content_rule)
                if not raw_values:
                    content_note = f"content empty: {chapter_title}"
                    continue
                if content_rule.endswith("@html") or split_rule_tokens(content_rule)[-1] == "html":
                    plain = collapse_text("\n".join(html_fragment_to_text(value) for value in raw_values))
                else:
                    plain = collapse_text("\n".join(raw_values))
                if not content_is_good(plain, min_chars=min_content_chars, min_cjk=min_content_cjk):
                    content_note = f"content too short: {chapter_title}"
                    continue
                result.content = StepResult(ok=True, url=chapter_resp.url, http_status=chapter_resp.status_code, count=len(plain), note=f"{chapter_title} ({len(plain)} chars)")
                content_ok = True
                break
            except Exception as exc:
                content_note = f"content request/parse failed: {exc}"
                continue

        if content_ok:
            result.passed = True
            result.pass_level = "content"
            result.note = "full chain ok"
            return result

        result.content = StepResult(ok=False, url=result.chapter_url, note=content_note or "content validation failed")
        result.note = result.content.note

    if not result.search or not result.search.ok:
        result.search = StepResult(ok=False, note=search_exception or result.note or "search validation failed")
        result.note = result.search.note
    return result


def summarize_report(report: dict[str, Any]) -> str:
    lines = [
        "Legado -> Xiangse 实测验证版",
        "",
        f"输入文件: {report.get('input_path', '')}",
        f"候选验证数: {report.get('candidate_count', 0)}",
        f"实测通过数: {report.get('passed_count', 0)}",
        f"收录数: {report.get('selected_count', 0)}",
        f"通过标准: {report.get('pass_standard', '')}",
        "",
        "收录源：",
    ]
    for item in report.get("selected_sources", []):
        lines.append(
            f"- {item['name']} | 搜索={item['search_ok']} 详情={item['detail_ok']} 目录={item['toc_ok']} 正文={item['content_ok']} | {item.get('content_note', '')}"
        )
    lines.extend(["", "未通过样例："])
    for item in report.get("failed_sources", [])[:20]:
        lines.append(
            f"- {item['name']} | 搜索={item['search_ok']} 详情={item['detail_ok']} 目录={item['toc_ok']} 正文={item['content_ok']} | {item.get('note', '')}"
        )
    return "\n".join(lines) + "\n"


def build_package(
    converter_module: Any,
    selected_candidates: list[Any],
    output_json: Path,
    mini_app_version: str,
) -> dict[str, Any]:
    wrapper: dict[str, dict[str, Any]] = {}
    alias_seen: set[str] = set()
    for rank_idx, candidate in enumerate(selected_candidates):
        source_name, source_obj = converter_module.convert_candidate(candidate, rank_idx, mini_app_version)
        alias = converter_module.make_alias(source_name, candidate.idx, alias_seen)
        wrapper[alias] = source_obj
    output_json.parent.mkdir(parents=True, exist_ok=True)
    with output_json.open("w", encoding="utf-8") as f:
        json.dump(wrapper, f, ensure_ascii=False, indent=2)
    return wrapper


def result_to_public_dict(result: ValidationResult) -> dict[str, Any]:
    return {
        "index": result.index,
        "name": result.name,
        "source_url": result.source_url,
        "complexity": result.complexity,
        "keyword": result.keyword,
        "selected_book_name": result.selected_book_name,
        "detail_url": result.detail_url,
        "toc_url": result.toc_url,
        "chapter_url": result.chapter_url,
        "supported": result.supported,
        "support_note": result.support_note,
        "search": asdict(result.search) if result.search else None,
        "detail": asdict(result.detail) if result.detail else None,
        "toc": asdict(result.toc) if result.toc else None,
        "content": asdict(result.content) if result.content else None,
        "passed": result.passed,
        "pass_level": result.pass_level,
        "note": result.note,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Legado sources over HTTP and build a verified Xiangse package.")
    parser.add_argument("--preferred-input", default=DEFAULT_PREFERRED_INPUT)
    parser.add_argument("--fallback-input", default=DEFAULT_FALLBACK_INPUT)
    parser.add_argument("--converter-script", default="/root/legado_xiangse_batch/convert_legado_to_xiangse.py")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--package-basename", default=DEFAULT_PACKAGE_BASENAME)
    parser.add_argument("--candidate-limit", type=int, default=DEFAULT_CANDIDATE_LIMIT)
    parser.add_argument("--target-min", type=int, default=DEFAULT_TARGET_MIN)
    parser.add_argument("--max-keep", type=int, default=DEFAULT_MAX_KEEP)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--min-content-chars", type=int, default=DEFAULT_MIN_CONTENT_CHARS)
    parser.add_argument("--min-content-cjk", type=int, default=DEFAULT_MIN_CONTENT_CJK)
    parser.add_argument("--keywords", nargs="*", default=DEFAULT_KEYWORDS)
    parser.add_argument("--mini-app-version", default=DEFAULT_MINI_APP_VERSION)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    converter_module = load_converter_module(Path(args.converter_script))
    input_path, legacy_sources, pick_note = converter_module.pick_input_path(Path(args.preferred_input), Path(args.fallback_input))
    candidates = converter_module.rank_candidates(legacy_sources)

    supported_candidates: list[Any] = []
    unsupported_candidates: list[ValidationResult] = []
    for candidate in candidates:
        supported, note = supported_source_note(candidate.source)
        if supported:
            supported_candidates.append(candidate)
        else:
            unsupported_candidates.append(
                ValidationResult(
                    index=candidate.idx,
                    name=candidate.name,
                    source_url=sanitize_source_url(str(candidate.source.get("bookSourceUrl") or "")),
                    complexity=int(candidate.score),
                    supported=False,
                    support_note=note,
                    search=StepResult(ok=False, note=note),
                    detail=StepResult(ok=False),
                    toc=StepResult(ok=False),
                    content=StepResult(ok=False),
                    passed=False,
                    note=note,
                )
            )

    candidate_limit = max(1, int(args.candidate_limit))
    to_validate = supported_candidates[:candidate_limit]

    results: list[ValidationResult] = []
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as executor:
        future_map = {
            executor.submit(
                validate_one_source,
                candidate,
                converter_module,
                list(args.keywords),
                int(args.timeout),
                int(args.min_content_chars),
                int(args.min_content_cjk),
            ): candidate
            for candidate in to_validate
        }
        for future in as_completed(future_map):
            results.append(future.result())

    results.sort(key=lambda item: item.index)
    passed_results = [item for item in results if item.passed]
    passed_results.sort(key=lambda item: (item.complexity, item.index))

    if len(passed_results) >= args.target_min:
        selected_results = passed_results[: max(1, min(int(args.max_keep), len(passed_results)))]
    else:
        selected_results = passed_results[: max(1, min(int(args.max_keep), len(passed_results)))]

    selected_indices = {item.index for item in selected_results}
    candidate_by_idx = {candidate.idx: candidate for candidate in candidates}
    selected_candidates = [candidate_by_idx[idx] for idx in sorted(selected_indices)]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    package_json = out_dir / f"{args.package_basename}.json"
    package_report = out_dir / f"{args.package_basename}.report.json"
    package_summary = out_dir / f"{args.package_basename}.summary.txt"
    selected_legacy_json = out_dir / f"{args.package_basename}.legado.json"

    build_package(converter_module, selected_candidates, package_json, args.mini_app_version)
    with selected_legacy_json.open("w", encoding="utf-8") as f:
        json.dump([candidate.source for candidate in selected_candidates], f, ensure_ascii=False, indent=2)

    selected_result_map = {item.index: item for item in selected_results}
    failed_results = [item for item in results if not item.passed]
    failed_results.extend(unsupported_candidates)
    failed_results.sort(key=lambda item: (item.index, item.name))

    report = {
        "generated_at": int(time.time()),
        "input_path": str(input_path),
        "input_pick_note": pick_note,
        "candidate_count": len(to_validate),
        "supported_candidate_count": len(supported_candidates),
        "unsupported_candidate_count": len(unsupported_candidates),
        "total_input_count": len(candidates),
        "passed_count": len(passed_results),
        "selected_count": len(selected_results),
        "target_min": int(args.target_min),
        "max_keep": int(args.max_keep),
        "keywords": list(args.keywords),
        "pass_standard": f"search non-empty + detail fetch + toc non-empty + content >= {int(args.min_content_chars)} chars or >= {int(args.min_content_cjk)} CJK chars",
        "artifacts": {
            "xiangse_json": str(package_json),
            "selected_legado_json": str(selected_legacy_json),
            "report_json": str(package_report),
            "summary_txt": str(package_summary),
        },
        "selected_sources": [
            {
                "index": item.index,
                "name": item.name,
                "keyword": item.keyword,
                "selected_book_name": item.selected_book_name,
                "detail_url": item.detail_url,
                "toc_url": item.toc_url,
                "chapter_url": item.chapter_url,
                "search_ok": bool(item.search and item.search.ok),
                "detail_ok": bool(item.detail and item.detail.ok),
                "toc_ok": bool(item.toc and item.toc.ok),
                "content_ok": bool(item.content and item.content.ok),
                "content_note": item.content.note if item.content else "",
                "complexity": item.complexity,
            }
            for item in selected_results
        ],
        "failed_sources": [
            {
                "index": item.index,
                "name": item.name,
                "search_ok": bool(item.search and item.search.ok),
                "detail_ok": bool(item.detail and item.detail.ok),
                "toc_ok": bool(item.toc and item.toc.ok),
                "content_ok": bool(item.content and item.content.ok),
                "note": item.note or item.support_note,
                "complexity": item.complexity,
            }
            for item in failed_results
        ],
        "all_results": [result_to_public_dict(item) for item in sorted(results + unsupported_candidates, key=lambda x: (x.index, x.name))],
    }

    with package_report.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    package_summary.write_text(summarize_report(report), encoding="utf-8")

    print(f"Validated {len(to_validate)} candidate sources from {input_path}")
    print(f"Passed full-chain validation: {len(passed_results)}")
    print(f"Selected for package: {len(selected_results)}")
    print(f"JSON package: {package_json}")
    print(f"Validation report: {package_report}")
    print(f"Summary: {package_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
