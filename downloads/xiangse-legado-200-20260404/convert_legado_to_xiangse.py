#!/usr/bin/env python3
"""Prototype converter from Legado source array to Xiangse source wrapper JSON."""

from __future__ import annotations

import argparse
import ast
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_PREFERRED_INPUT = "/tmp/tickmao_novel/sources/legado/main/full.json"
DEFAULT_FALLBACK_INPUT = "/tmp/tickmao_novel/sources/legado/full.json"
DEFAULT_OUTPUT_JSON = "out/xiangse_package.json"
DEFAULT_OUTPUT_REPORT = "out/report.json"
DEFAULT_OUTPUT_SUMMARY = "out/README.summary.txt"
DEFAULT_MAX_SOURCES = 200
DEFAULT_MINI_APP_VERSION = "2.56.1"


@dataclass
class Candidate:
    idx: int
    source: dict[str, Any]
    name: str
    has_java: bool
    score: int
    notes: list[str]


def read_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def pick_input_path(preferred: Path, fallback: Path) -> tuple[Path, list[dict[str, Any]], str]:
    decisions: list[str] = []

    if preferred.exists():
        try:
            data = read_json_file(preferred)
            if isinstance(data, list) and data:
                decisions.append("preferred input selected: valid non-empty list")
                return preferred, data, "; ".join(decisions)
            decisions.append("preferred input rejected: not a non-empty list")
        except Exception as exc:
            decisions.append(f"preferred input rejected: {exc}")
    else:
        decisions.append("preferred input missing")

    if not fallback.exists():
        raise FileNotFoundError(f"Fallback input not found: {fallback}")

    data = read_json_file(fallback)
    if not isinstance(data, list) or not data:
        raise ValueError(f"Fallback input is not a non-empty JSON list: {fallback}")

    decisions.append("fallback input selected")
    return fallback, data, "; ".join(decisions)


def stringify_for_scan(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def compute_complexity(src: dict[str, Any]) -> tuple[int, list[str], bool]:
    blob = stringify_for_scan(src)
    has_java = "java." in blob
    score = 0
    notes: list[str] = []

    if has_java:
        score += 120
        notes.append("contains java.*")

    search_url = str(src.get("searchUrl") or "")
    if search_url.startswith("@js"):
        score += 45
        notes.append("searchUrl uses @js")

    if re.search(r",\s*\{", search_url):
        score += 15
        notes.append("searchUrl has legacy option object")

    token_weights = {
        "@js": 18,
        "&&": 5,
        "||": 5,
        "##": 3,
        "<js>": 15,
        "<js": 8,
        "xpath": 2,
        "regex": 1,
    }
    lowered_blob = blob.lower()
    for token, weight in token_weights.items():
        cnt = lowered_blob.count(token.lower())
        if cnt:
            score += min(30, cnt * weight)

    for key in ("ruleSearch", "ruleBookInfo", "ruleToc", "ruleContent"):
        rule = src.get(key)
        if not isinstance(rule, dict):
            score += 20
            notes.append(f"{key} missing or invalid")
            continue
        missing_core = 0
        if key == "ruleSearch":
            for field in ("bookList", "name", "bookUrl"):
                if not rule.get(field):
                    missing_core += 1
        elif key == "ruleToc":
            for field in ("chapterList", "chapterName"):
                if not rule.get(field):
                    missing_core += 1
        elif key == "ruleContent":
            if not rule.get("content"):
                missing_core += 1
        if missing_core:
            score += 10 * missing_core
            notes.append(f"{key} missing core fields: {missing_core}")

    score += min(35, len(blob) // 1500)
    return score, notes, has_java


def rank_candidates(sources: list[dict[str, Any]]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for idx, src in enumerate(sources):
        if not isinstance(src, dict):
            continue
        name = str(src.get("bookSourceName") or f"source_{idx}").strip() or f"source_{idx}"
        score, notes, has_java = compute_complexity(src)
        candidates.append(Candidate(idx=idx, source=src, name=name, has_java=has_java, score=score, notes=notes))
    candidates.sort(key=lambda c: (1 if c.has_java else 0, c.score, c.idx))
    return candidates


def ensure_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def safe_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text and re.fullmatch(r"-?\d+", text):
            return int(text)
    return default


def normalize_weight(source: dict[str, Any], fallback_weight: int) -> str:
    for key in ("weight", "customOrder"):
        if key in source:
            value = safe_int(source.get(key), fallback_weight)
            if value > 0:
                return str(value)
    return str(max(1, fallback_weight))


def parse_headers(raw_header: Any, user_agent: Any) -> dict[str, str]:
    headers: dict[str, str] = {}

    if isinstance(raw_header, dict):
        for key, value in raw_header.items():
            if key is not None and value is not None:
                headers[str(key)] = ensure_str(value)
    elif isinstance(raw_header, str):
        text = raw_header.strip()
        if text:
            parsed_obj = None
            try:
                parsed_obj = json.loads(text)
            except Exception:
                try:
                    parsed_obj = ast.literal_eval(text)
                except Exception:
                    parsed_obj = None

            if isinstance(parsed_obj, dict):
                for key, value in parsed_obj.items():
                    if key is not None and value is not None:
                        headers[str(key)] = ensure_str(value)
            else:
                for line in re.split(r"\r?\n", text):
                    line = line.strip().strip(",{}")
                    if ":" not in line:
                        continue
                    key, value = line.split(":", 1)
                    key = key.strip().strip("'\"")
                    value = value.strip().strip("'\"")
                    if key:
                        headers[key] = value

    ua = ensure_str(user_agent).strip()
    if ua and "User-Agent" not in headers:
        headers["User-Agent"] = ua
    return headers


def infer_host(url_text: str, fallback: str = "") -> str:
    text = (url_text or "").strip()
    if text.startswith("@js"):
        text = fallback.strip()
    if not text:
        return ""

    text = re.split(r",\s*\{", text, maxsplit=1)[0].strip()
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith("/") and fallback:
        base = urlparse(fallback)
        if base.scheme and base.netloc:
            return f"{base.scheme}://{base.netloc}"
    return fallback.strip()


def split_search_url_and_options(search_url: str) -> tuple[str, dict[str, Any]]:
    text = (search_url or "").strip()
    if not text or text.startswith("@js"):
        return text, {}

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


def js_quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def build_search_request_info(search_url: str, source_url: str, source_headers: dict[str, str]) -> str:
    url_part, opts = split_search_url_and_options(search_url)
    if url_part.startswith("@js"):
        return url_part

    base_host = infer_host(source_url)
    charset = ensure_str(opts.get("charset")).strip()
    method = ensure_str(opts.get("method")).strip().upper()
    body = ensure_str(opts.get("body")).strip()

    extra_headers: dict[str, str] = {}
    for hdr_key in ("headers", "header", "httpHeaders"):
        hv = opts.get(hdr_key)
        if isinstance(hv, dict):
            for key, value in hv.items():
                if key is not None and value is not None:
                    extra_headers[str(key)] = ensure_str(value)

    if charset and "Accept-Charset" not in extra_headers:
        extra_headers["Accept-Charset"] = charset

    merged_headers = dict(source_headers)
    merged_headers.update(extra_headers)

    lines = [
        "@js:",
        f"var tpl = {js_quote(url_part)};",
        "var kw = '';",
        "var pg = 1;",
        "if (params) {",
        "  kw = String(params.keyWord || params.keyword || params.key || '');",
        "  pg = params.pageIndex || params.page || params.pageNo || params.p || 1;",
        "}",
        "function renderLegadoTemplate(t, encodeKeyword) {",
        "  return String(t || '').replace(/\\{\\{([\\s\\S]*?)\\}\\}/g, function(all, expr) {",
        "    try {",
        "      var key = kw;",
        "      var keyWord = kw;",
        "      var page = pg;",
        "      var pageIndex = pg;",
        "      var value = (function(){ return eval(expr); })();",
        "      value = value == null ? '' : String(value);",
        "      if (encodeKeyword && (expr.indexOf('key') >= 0 || expr.indexOf('keyWord') >= 0)) return encodeURIComponent(value);",
        "      return value;",
        "    } catch (e) {",
        "      return all",
        "        .replace(/\\{\\{\\s*(key|keyWord)\\s*\\}\\}/g, encodeKeyword ? encodeURIComponent(kw) : kw)",
        "        .replace(/\\{\\{\\s*page\\s*\\}\\}/g, String(pg))",
        "        .replace(/\\{\\{\\s*page\\s*-\\s*1\\s*\\}\\}/g, String(pg - 1))",
        "        .replace(/\\{\\{\\s*key\\s*\\[\\s*0\\s*\\]\\s*\\}\\}/g, kw ? kw.charAt(0) : '');",
        "    }",
        "  });",
        "}",
        "var u = renderLegadoTemplate(tpl, true);",
        "if (/^\\/\\//.test(u)) { u = 'https:' + u; }",
    ]
    if base_host:
        lines.append(f"if (/^\\//.test(u)) {{ u = {js_quote(base_host)} + u; }}")

    if method == "POST" or body:
        lines.append(f"var postData = {js_quote(body)};")
        lines.append("postData = renderLegadoTemplate(postData, false);")
        lines.append(
            "var httpParams = {};\n"
            "postData.split('&').forEach(function(part){\n"
            "  if (!part) return;\n"
            "  var idx = part.indexOf('=');\n"
            "  var k = idx >= 0 ? part.slice(0, idx) : part;\n"
            "  var v = idx >= 0 ? part.slice(idx + 1) : '';\n"
            "  if (k) httpParams[k] = v;\n"
            "});"
        )
        if merged_headers:
            lines.append(f"return {{url:u,httpHeaders:{json.dumps(merged_headers, ensure_ascii=False)},POST:true,httpParams:httpParams}};")
        else:
            lines.append("return {url:u,POST:true,httpParams:httpParams};")
    else:
        if merged_headers:
            lines.append(f"return {{url:u,httpHeaders:{json.dumps(merged_headers, ensure_ascii=False)}}};")
        else:
            lines.append("return u;")

    return "\n".join(lines)


def build_toc_request_info(host: str) -> str:
    return "\n".join(
        [
            "@js:",
            "var u = '';",
            "if (params && params.queryInfo && params.queryInfo.tocUrl) u = String(params.queryInfo.tocUrl);",
            "if (!u && typeof result === 'string' && result) u = result;",
            "if (!u && result && typeof result === 'object') u = String(result.tocUrl || result.detailUrl || result.url || '');",
            "if (!u && params && params.queryInfo) u = String(params.queryInfo.detailUrl || params.queryInfo.url || '');",
            "if (/^\\/\\//.test(u)) u = 'https:' + u;",
            f"if (/^\\//.test(u)) u = {js_quote(host)} + u;",
            "return {url:u,httpHeaders:config.httpHeaders};",
        ]
    )


def build_content_request_info(host: str) -> str:
    return "\n".join(
        [
            "@js:",
            "var u = '';",
            "if (params && params.lastResponse && params.lastResponse.nextPageUrl) u = String(params.lastResponse.nextPageUrl);",
            "if (!u && params && params.lastResponse && params.lastResponse.nextContentUrl) u = String(params.lastResponse.nextContentUrl);",
            "if (!u && typeof result === 'string' && result) u = result;",
            "if (!u && result && typeof result === 'object') u = String(result.url || result.detailUrl || '');",
            "if (!u && params && params.queryInfo) u = String(params.queryInfo.url || params.queryInfo.detailUrl || '');",
            "if (/^\\/\\//.test(u)) u = 'https:' + u;",
            f"if (/^\\//.test(u)) u = {js_quote(host)} + u;",
            "return {url:u,httpHeaders:config.httpHeaders};",
        ]
    )


def infer_response_format(rule_fields: list[str]) -> str:
    text = "\n".join([field for field in rule_fields if field]).strip()
    if not text:
        return "html"
    lowered = text.lower()
    if "$." in text or "$[" in text or "@json" in lowered or "jsonpath" in lowered:
        return "json"
    if "<rss" in lowered or "<feed" in lowered or ("</" in lowered and "xpath" in lowered):
        return "xml"
    return "html"


def map_search_action(source: dict[str, Any], headers: dict[str, str], host: str) -> dict[str, Any]:
    rs = source.get("ruleSearch") if isinstance(source.get("ruleSearch"), dict) else {}
    search_url = ensure_str(source.get("searchUrl"))
    response_format = infer_response_format([
        ensure_str(rs.get("bookList")),
        ensure_str(rs.get("name")),
        ensure_str(rs.get("author")),
        ensure_str(rs.get("bookUrl")),
    ])
    return {
        "actionID": "searchBook",
        "parserID": "DOM",
        "host": host,
        "validConfig": "",
        "httpHeaders": headers,
        "responseFormatType": response_format,
        "requestInfo": build_search_request_info(search_url, ensure_str(source.get("bookSourceUrl")), headers),
        "list": ensure_str(rs.get("bookList")),
        "bookName": ensure_str(rs.get("name")),
        "author": ensure_str(rs.get("author")),
        "desc": ensure_str(rs.get("intro")),
        "cat": ensure_str(rs.get("kind")),
        "cover": ensure_str(rs.get("coverUrl")),
        "lastChapterTitle": ensure_str(rs.get("lastChapter")),
        "wordCount": ensure_str(rs.get("wordCount")),
        "detailUrl": ensure_str(rs.get("bookUrl")),
    }


def map_detail_action(source: dict[str, Any], headers: dict[str, str], host: str) -> dict[str, Any]:
    rb = source.get("ruleBookInfo") if isinstance(source.get("ruleBookInfo"), dict) else {}
    response_format = infer_response_format([
        ensure_str(rb.get("name")),
        ensure_str(rb.get("author")),
        ensure_str(rb.get("intro")),
        ensure_str(rb.get("tocUrl")),
    ])
    action = {
        "actionID": "bookDetail",
        "parserID": "DOM",
        "host": host,
        "validConfig": "",
        "httpHeaders": headers,
        "responseFormatType": response_format,
        "requestInfo": "%@result",
        "cover": ensure_str(rb.get("coverUrl")),
        "author": ensure_str(rb.get("author")),
        "desc": ensure_str(rb.get("intro")),
        "cat": ensure_str(rb.get("kind")),
        "status": ensure_str(rb.get("status")),
        "wordCount": ensure_str(rb.get("wordCount")),
        "tocUrl": ensure_str(rb.get("tocUrl")),
    }
    if rb.get("name"):
        action["bookName"] = ensure_str(rb.get("name"))
    if rb.get("lastChapter"):
        action["lastChapterTitle"] = ensure_str(rb.get("lastChapter"))
    return action


def map_toc_action(source: dict[str, Any], headers: dict[str, str], host: str) -> dict[str, Any]:
    rt = source.get("ruleToc") if isinstance(source.get("ruleToc"), dict) else {}
    response_format = infer_response_format([
        ensure_str(rt.get("chapterList")),
        ensure_str(rt.get("chapterName")),
        ensure_str(rt.get("chapterUrl")),
    ])
    return {
        "actionID": "chapterList",
        "parserID": "DOM",
        "host": host,
        "validConfig": "",
        "httpHeaders": headers,
        "responseFormatType": response_format,
        "requestInfo": build_toc_request_info(host),
        "list": ensure_str(rt.get("chapterList")),
        "title": ensure_str(rt.get("chapterName")),
        "url": ensure_str(rt.get("chapterUrl")),
        "detailUrl": ensure_str(rt.get("chapterUrl")),
        "nextPageUrl": ensure_str(rt.get("nextTocUrl")),
        "isVip": ensure_str(rt.get("isVip")),
        "isVolume": ensure_str(rt.get("isVolume")),
    }


def map_content_action(source: dict[str, Any], headers: dict[str, str], host: str) -> dict[str, Any]:
    rc = source.get("ruleContent") if isinstance(source.get("ruleContent"), dict) else {}
    response_format = infer_response_format([ensure_str(rc.get("content"))])
    action = {
        "actionID": "chapterContent",
        "parserID": "DOM",
        "host": host,
        "validConfig": "",
        "httpHeaders": headers,
        "responseFormatType": response_format,
        "requestInfo": build_content_request_info(host),
        "content": ensure_str(rc.get("content")),
    }
    if rc.get("nextContentUrl"):
        action["nextPageUrl"] = ensure_str(rc.get("nextContentUrl"))
    if rc.get("replaceRegex"):
        action["replaceRegex"] = ensure_str(rc.get("replaceRegex"))
    if rc.get("imageStyle"):
        action["imageStyle"] = ensure_str(rc.get("imageStyle"))
    if rc.get("sourceRegex"):
        action["sourceRegex"] = ensure_str(rc.get("sourceRegex"))
    return action


def make_alias(name: str, idx: int, seen: set[str]) -> str:
    base = re.sub(r"\s+", "", name).strip()
    base = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", base).strip("-")
    if not base:
        base = f"source-{idx + 1}"
    alias = f"{base}-{idx + 1:03d}"
    if alias not in seen:
        seen.add(alias)
        return alias
    n = 2
    while True:
        candidate = f"{alias}-{n}"
        if candidate not in seen:
            seen.add(candidate)
            return candidate
        n += 1


def convert_candidate(cand: Candidate, rank_idx: int, mini_app_version: str) -> tuple[str, dict[str, Any]]:
    src = cand.source
    source_name = ensure_str(src.get("bookSourceName")).strip() or f"source_{cand.idx}"
    source_url = ensure_str(src.get("bookSourceUrl")).strip()
    host = infer_host(source_url)
    headers = parse_headers(src.get("header"), src.get("httpUserAgent"))

    now_ms = int(time.time() * 1000)
    last_modify = safe_int(src.get("lastUpdateTime"), now_ms)
    if last_modify < 10_000_000_000:
        last_modify *= 1000

    source_obj = {
        "sourceName": source_name,
        "sourceUrl": source_url,
        "sourceType": "text",
        "enable": 1 if bool(src.get("enabled", True)) else 0,
        "weight": normalize_weight(src, fallback_weight=max(1, 10000 - rank_idx)),
        "miniAppVersion": mini_app_version,
        "lastModifyTime": str(last_modify),
        "searchBook": map_search_action(src, headers, host),
        "bookDetail": map_detail_action(src, headers, host),
        "chapterList": map_toc_action(src, headers, host),
        "chapterContent": map_content_action(src, headers, host),
    }
    return source_name, source_obj


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_summary(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "Legado -> Xiangse Converter Prototype Summary",
        "",
        f"Input file: {report.get('input_path', '')}",
        f"Input selection decision: {report.get('input_pick_note', '')}",
        f"Total input sources: {report.get('total_input_count', 0)}",
        f"Selected sources: {report.get('selected_count', 0)}",
        f"Selected sources with java.*: {report.get('selected_java_count', 0)}",
        f"Average selected complexity score: {report.get('selected_complexity_avg', 0)}",
        "",
        "Selection strategy:",
        "- Prefer non-java.* sources first.",
        "- Then prefer lower complexity rules and fewer missing core fields.",
        "- If non-java.* pool is insufficient, include lowest-risk java.* sources to reach target.",
        "",
        "Likely failure causes after import:",
        "- searchUrl using @js/java runtime semantics incompatible with Xiangse runtime.",
        "- advanced Legado parser expressions that Xiangse parser interprets differently.",
        "- site anti-bot/cookie/login requirements not modeled in this prototype.",
        "- legacy POST/body/charset declarations needing source-specific manual tuning.",
    ]
    top_notes = report.get("top_selection_notes", [])
    if top_notes:
        lines.extend(["", "Top selection notes:"])
        for note in top_notes[:10]:
            lines.append(f"- {note}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_report(*, input_path: Path, input_pick_note: str, candidates: list[Candidate], selected: list[Candidate], output_json_path: Path) -> dict[str, Any]:
    selected_scores = [c.score for c in selected]
    avg_score = round(sum(selected_scores) / len(selected_scores), 2) if selected_scores else 0
    note_counter: dict[str, int] = {}
    for cand in selected:
        for note in cand.notes:
            note_counter[note] = note_counter.get(note, 0) + 1
    top_notes = sorted(note_counter.items(), key=lambda item: (-item[1], item[0]))
    return {
        "input_path": str(input_path),
        "input_pick_note": input_pick_note,
        "total_input_count": len(candidates),
        "requested_max_sources": DEFAULT_MAX_SOURCES,
        "selected_count": len(selected),
        "selected_java_count": sum(1 for c in selected if c.has_java),
        "excluded_count": max(0, len(candidates) - len(selected)),
        "selected_complexity_avg": avg_score,
        "selected_complexity_min": min(selected_scores) if selected_scores else None,
        "selected_complexity_max": max(selected_scores) if selected_scores else None,
        "output_json": str(output_json_path),
        "top_selection_notes": [f"{k}: {v}" for k, v in top_notes[:20]],
        "selected_sources": [
            {
                "index": c.idx,
                "name": c.name,
                "complexity": c.score,
                "has_java": c.has_java,
                "notes": c.notes,
            }
            for c in selected
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert Legado source array to Xiangse source wrapper JSON.")
    parser.add_argument("--preferred-input", default=DEFAULT_PREFERRED_INPUT)
    parser.add_argument("--fallback-input", default=DEFAULT_FALLBACK_INPUT)
    parser.add_argument("--output-json", default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-report", default=DEFAULT_OUTPUT_REPORT)
    parser.add_argument("--output-summary", default=DEFAULT_OUTPUT_SUMMARY)
    parser.add_argument("--max-sources", type=int, default=DEFAULT_MAX_SOURCES)
    parser.add_argument("--mini-app-version", default=DEFAULT_MINI_APP_VERSION)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    preferred = Path(args.preferred_input)
    fallback = Path(args.fallback_input)
    output_json = Path(args.output_json)
    output_report = Path(args.output_report)
    output_summary = Path(args.output_summary)
    max_sources = max(1, int(args.max_sources))

    input_path, legacy_sources, pick_note = pick_input_path(preferred, fallback)
    candidates = rank_candidates(legacy_sources)
    selected = candidates[:max_sources]

    wrapper: dict[str, dict[str, Any]] = {}
    alias_seen: set[str] = set()
    for rank_idx, cand in enumerate(selected):
        source_name, source_obj = convert_candidate(cand, rank_idx, args.mini_app_version)
        alias = make_alias(source_name, cand.idx, alias_seen)
        wrapper[alias] = source_obj

    write_json(output_json, wrapper)
    report = build_report(
        input_path=input_path,
        input_pick_note=pick_note,
        candidates=candidates,
        selected=selected,
        output_json_path=output_json,
    )
    report["requested_max_sources"] = max_sources
    write_json(output_report, report)
    write_summary(output_summary, report)

    print(f"Converted {len(selected)}/{len(candidates)} sources from {input_path} -> {output_json}")
    print(f"Report written: {output_report}")
    print(f"Summary written: {output_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
