#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from pathlib import Path

OUT_JSON = Path('/root/legado_xiangse_batch/out/xiangse_true_small.json')

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'


def abs_url_js(host: str) -> str:
    scheme = host.split(':', 1)[0]
    return (
        "var u = Array.isArray(result) ? result[0] : result; "
        "u = String(u || '').trim(); "
        f"if (/^\\/\\//.test(u)) return '{scheme}:' + u; "
        f"if (/^\\//.test(u)) return '{host}' + u; "
        "return u;"
    )


def text_join_js(filter_regex: str = '') -> str:
    filt = f" && !/{filter_regex}/.test(s)" if filter_regex else ''
    return (
        "if (Array.isArray(result)) { "
        "return result.map(function(s){ return String(s).replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim(); })"
        f".filter(function(s){{ return s{filt}; }}).join(' '); "
        "} "
        "return String(result || '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();"
    )


def content_join_js(filter_regex: str = '') -> str:
    filt = f" && !/{filter_regex}/.test(s)" if filter_regex else ''
    return (
        "if (Array.isArray(result)) { "
        "return result.map(function(s){ return String(s).replace(/\\u00a0/g, ' ').trim(); })"
        f".filter(function(s){{ return s{filt}; }}).join('\\n\\n'); "
        "} "
        "return String(result || '').trim();"
    )


def strip_prefix_js(pattern: str) -> str:
    return (
        "var s = Array.isArray(result) ? result.join(' ') : String(result || ''); "
        f"return s.replace(/{pattern}/, '').replace(/\\s+/g, ' ').trim();"
    )


def toc_from_response_rule() -> str:
    return "//link[@rel='canonical']/@href ||@js: return String((params && params.responseUrl) || (Array.isArray(result) ? result[0] : result) || '').trim();"


def chapter_list_request(host: str) -> str:
    scheme = host.split(':', 1)[0]
    return (
        "@js:\n"
        "var u = '';\n"
        "if (params && params.queryInfo && params.queryInfo.chapterListUrl) u = String(params.queryInfo.chapterListUrl);\n"
        "if (!u && params && params.queryInfo && params.queryInfo.tocUrl) u = String(params.queryInfo.tocUrl);\n"
        "if (!u && params && params.queryInfo && params.queryInfo.detailUrl) u = String(params.queryInfo.detailUrl);\n"
        "if (!u && params && params.responseUrl) u = String(params.responseUrl);\n"
        "if (!u && typeof result === 'string' && result) u = result;\n"
        f"if (/^\\/\\//.test(u)) u = '{scheme}:' + u;\n"
        f"if (/^\\//.test(u)) u = '{host}' + u;\n"
        "return u;"
    )


def common_top(name: str, url: str, weight: str) -> dict:
    return {
        'sourceName': name,
        'sourceUrl': url,
        'sourceType': 'text',
        'enable': 1,
        'weight': weight,
        'miniAppVersion': '2.56.1',
        'lastModifyTime': str(int(time.time())),
    }


def make_kaiyan() -> dict:
    host = 'http://www.xbotaodz.com'
    d = common_top('快眼看书', host, '9999')
    d.update({
        'searchBook': {
            'actionID': 'searchBook', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html',
            'requestInfo': f'{host}/modules/article/search.php?searchkey=%@keyWord',
            'list': "//*[contains(concat(' ', normalize-space(@class), ' '), ' librarylist ')]//li",
            'bookName': f".//*[contains(concat(' ', normalize-space(@class), ' '), ' info ')]//span[1]//a/text() ||@js:{text_join_js()}",
            'author': f".//*[contains(concat(' ', normalize-space(@class), ' '), ' info ')]//span[2]//text() ||@js:{text_join_js('^作者')}" ,
            'cover': f".//*[contains(concat(' ', normalize-space(@class), ' '), ' pt-ll-l ')]//img/@src ||@js:{abs_url_js(host)}",
            'detailUrl': f".//*[contains(concat(' ', normalize-space(@class), ' '), ' info ')]//span[1]//a/@href ||@js:{abs_url_js(host)}",
            'lastChapterTitle': f".//*[contains(concat(' ', normalize-space(@class), ' '), ' last ')]//a/text() ||@js:{text_join_js()}"
        },
        'bookDetail': {
            'actionID': 'bookDetail', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html', 'requestInfo': '%@result',
            'bookName': f"//*[contains(concat(' ', normalize-space(@class), ' '), ' w-left ')]//h1/text() ||@js:{text_join_js()}",
            'author': f"//*[contains(concat(' ', normalize-space(@class), ' '), ' novelinfo-l ')]//li[1]//text() ||@js:{strip_prefix_js('作者[:：]\\s*')}",
            'desc': f"//*[contains(concat(' ', normalize-space(@class), ' '), ' novelintro ')]//text() ||@js:{text_join_js()}",
            'cover': f"//*[contains(concat(' ', normalize-space(@class), ' '), ' novelinfo-r ')]//img/@src ||@js:{abs_url_js(host)}",
            'tocUrl': toc_from_response_rule(),
        },
        'chapterList': {
            'actionID': 'chapterList', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html', 'requestInfo': chapter_list_request(host),
            'list': "//*[contains(concat(' ', normalize-space(@class), ' '), ' fulldir ')]//ul//li//a",
            'title': f".//text() ||@js:{text_join_js()}",
            'url': f".//@href ||@js:{abs_url_js(host)}",
        },
        'chapterContent': {
            'actionID': 'chapterContent', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html', 'requestInfo': '%@result',
            'content': f"//*[@id='chaptercontent']//text() ||@js:{content_join_js('快眼看书|最新网址|手机用户')}",
        },
    })
    return d


def make_23us() -> dict:
    host = 'http://www.23uswx.la'
    d = common_top('顶点小说', 'http://www.23us.tw', '9998')
    d.update({
        'searchBook': {
            'actionID': 'searchBook', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html',
            'requestInfo': 'http://www.23uswx.la/modules/article/search.php?q=%@keyWord',
            'list': "//*[contains(concat(' ', normalize-space(@class), ' '), ' grid ')]//tr[position()>1]",
            'bookName': f".//td[1]//a/text() ||@js:{text_join_js('免费阅读|全文|最新章节|笔趣阁|小说')}",
            'author': f".//td[3]//text() ||@js:{text_join_js()}",
            'detailUrl': f".//td[1]//a/@href ||@js:{abs_url_js(host)}",
            'lastChapterTitle': f".//td[2]//a/text() ||@js:{text_join_js()}",
        },
        'bookDetail': {
            'actionID': 'bookDetail', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html', 'requestInfo': '%@result',
            'bookName': f"//*[@id='info']//h1[1]//text() ||@js:{text_join_js('免费阅读|全文|最新章节|笔趣阁|小说')}",
            'author': f"//*[@id='info']//p[1]//text() ||@js:{strip_prefix_js('作\\s*者[:：]\\s*')}",
            'desc': f"//*[@id='intro']//text() ||@js:{text_join_js()}",
            'cover': f"//*[@id='fmimg']//img/@src ||@js:{abs_url_js(host)}",
            'tocUrl': toc_from_response_rule(),
        },
        'chapterList': {
            'actionID': 'chapterList', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html', 'requestInfo': chapter_list_request(host),
            'list': "//*[@id='list']//dd",
            'title': f".//a/text() ||@js:{text_join_js()}",
            'url': f".//a/@href ||@js:{abs_url_js(host)}",
        },
        'chapterContent': {
            'actionID': 'chapterContent', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html', 'requestInfo': '%@result',
            'content': f"//*[@id='content']//text() ||@js:{content_join_js('23us|顶点小说|最新网址|百度里搜索|请记住本书首发')}",
        },
    })
    return d


def make_swsk() -> dict:
    host = 'http://www.35ge.info'
    d = common_top('空白小说', 'http://www.swsk.org', '9997')
    d.update({
        'searchBook': {
            'actionID': 'searchBook', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html',
            'requestInfo': 'http://www.swsk.org/modules/article/search.php?searchkey=%@keyWord',
            'list': "//*[contains(concat(' ', normalize-space(@class), ' '), ' novelslist2 ')]//ul/li[position()>1]",
            'bookName': f".//*[contains(concat(' ', normalize-space(@class), ' '), ' s2 ')]//a/text() ||@js:{text_join_js()}",
            'author': f".//*[contains(concat(' ', normalize-space(@class), ' '), ' s4 ')]//text() ||@js:{text_join_js()}",
            'detailUrl': f".//*[contains(concat(' ', normalize-space(@class), ' '), ' s2 ')]//a/@href ||@js:{abs_url_js(host)}",
            'lastChapterTitle': f".//*[contains(concat(' ', normalize-space(@class), ' '), ' s3 ')]//a/text() ||@js:{text_join_js()}",
        },
        'bookDetail': {
            'actionID': 'bookDetail', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html', 'requestInfo': '%@result',
            'bookName': f"//meta[@property='og:title']/@content ||@js:{text_join_js()}",
            'author': f"//meta[@property='og:novel:author']/@content ||@js:{text_join_js()}",
            'desc': f"//meta[@property='og:description']/@content ||@js:{text_join_js()}",
            'cover': f"//meta[@property='og:image']/@content ||@js:{abs_url_js(host)}",
            'tocUrl': toc_from_response_rule(),
        },
        'chapterList': {
            'actionID': 'chapterList', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html', 'requestInfo': chapter_list_request(host),
            'list': "//*[@id='list']//dd",
            'title': f".//a/text() ||@js:{text_join_js()}",
            'url': f".//a/@href ||@js:{abs_url_js(host)}",
        },
        'chapterContent': {
            'actionID': 'chapterContent', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html', 'requestInfo': '%@result',
            'content': f"//*[@id='content']//text() ||@js:{content_join_js('最新网址|请记住|手机用户')}",
        },
    })
    return d


def make_yetianlian() -> dict:
    host = 'http://www.yetianlian.net'
    d = common_top('何以生肖', host, '9996')
    d.update({
        'searchBook': {
            'actionID': 'searchBook', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html',
            'requestInfo': f'{host}/s.php?ie=utf-8&q=%@keyWord',
            'list': "//*[contains(concat(' ', normalize-space(@class), ' '), ' bookbox ')]",
            'bookName': f".//*[contains(concat(' ', normalize-space(@class), ' '), ' bookname ')]//a/text() ||@js:{text_join_js()}",
            'author': f".//*[contains(concat(' ', normalize-space(@class), ' '), ' author ')]//text() ||@js:{strip_prefix_js('作者[:：]\\s*')}",
            'cover': f".//img/@src ||@js:{abs_url_js(host)}",
            'detailUrl': f".//*[contains(concat(' ', normalize-space(@class), ' '), ' bookname ')]//a/@href ||@js:{abs_url_js(host)}",
            'lastChapterTitle': f".//*[contains(concat(' ', normalize-space(@class), ' '), ' update ')]//a/text() ||@js:{text_join_js()}",
        },
        'bookDetail': {
            'actionID': 'bookDetail', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html', 'requestInfo': '%@result',
            'bookName': f"//h2/text() ||@js:{text_join_js()}",
            'author': f"//*[contains(concat(' ', normalize-space(@class), ' '), ' small ')]//span[1]//text() ||@js:{strip_prefix_js('作者[:：]\\s*')}",
            'desc': f"//*[contains(concat(' ', normalize-space(@class), ' '), ' intro ')]//text() ||@js:{strip_prefix_js('简介[:：]?\\s*')}",
            'cover': f"//img/@src ||@js:{abs_url_js(host)}",
            'tocUrl': toc_from_response_rule(),
        },
        'chapterList': {
            'actionID': 'chapterList', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html', 'requestInfo': chapter_list_request(host),
            'list': "//*[contains(concat(' ', normalize-space(@class), ' '), ' listmain ')]//dd",
            'title': f".//a/text() ||@js:{text_join_js()}",
            'url': f".//a/@href ||@js:{abs_url_js(host)}",
        },
        'chapterContent': {
            'actionID': 'chapterContent', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html', 'requestInfo': '%@result',
            'content': f"//*[@id='content']//text() ||@js:{content_join_js('请记住本书首发|yetianlian')}",
        },
    })
    return d


def make_rulianshi() -> dict:
    host = 'http://www.rulianshi.net'
    d = common_top('殓师灵异', 'http://www.rulianshi.org', '9995')
    d.update({
        'searchBook': {
            'actionID': 'searchBook', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html',
            'requestInfo': f'{host}/s.php?ie=utf-8&q=%@keyWord',
            'list': "//*[contains(concat(' ', normalize-space(@class), ' '), ' bookbox ')]",
            'bookName': f".//h4//a/text() ||@js:{text_join_js()}",
            'author': f".//*[contains(concat(' ', normalize-space(@class), ' '), ' author ')]//text() ||@js:{strip_prefix_js('作者[:：]\\s*')}",
            'cover': f".//img/@src ||@js:{abs_url_js(host)}",
            'detailUrl': f".//h4//a/@href ||@js:{abs_url_js(host)}",
            'lastChapterTitle': f".//*[contains(concat(' ', normalize-space(@class), ' '), ' update ')]//a/text() ||@js:{text_join_js()}",
        },
        'bookDetail': {
            'actionID': 'bookDetail', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html', 'requestInfo': '%@result',
            'bookName': f"//h2/text() ||@js:{text_join_js()}",
            'author': f"//*[contains(concat(' ', normalize-space(@class), ' '), ' small ')]//span[1]//text() ||@js:{strip_prefix_js('作者[:：]\\s*')}",
            'desc': f"//*[contains(concat(' ', normalize-space(@class), ' '), ' intro ')]//text() ||@js:{strip_prefix_js('简介[:：]?\\s*')}",
            'cover': f"//*[contains(concat(' ', normalize-space(@class), ' '), ' cover ')]//img/@src ||@js:{abs_url_js(host)}",
            'tocUrl': toc_from_response_rule(),
        },
        'chapterList': {
            'actionID': 'chapterList', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html', 'requestInfo': chapter_list_request(host),
            'list': "//*[contains(concat(' ', normalize-space(@class), ' '), ' listmain ')]//dd",
            'title': f".//a/text() ||@js:{text_join_js()}",
            'url': f".//a/@href ||@js:{abs_url_js(host)}",
        },
        'chapterContent': {
            'actionID': 'chapterContent', 'parserID': 'DOM', 'host': host, 'validConfig': '', 'httpHeaders': {'User-Agent': UA},
            'responseFormatType': 'html', 'requestInfo': '%@result',
            'content': f"//*[@id='content']//text() ||@js:{content_join_js('请记住本书首发|rulianshi')}",
        },
    })
    return d


def build_pack() -> dict:
    return {
        '快眼看书-true': make_kaiyan(),
        '顶点小说-true': make_23us(),
        '空白小说-true': make_swsk(),
        '何以生肖-true': make_yetianlian(),
        '殓师灵异-true': make_rulianshi(),
    }


def main() -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(build_pack(), ensure_ascii=False, indent=2), encoding='utf-8')
    print(OUT_JSON)


if __name__ == '__main__':
    main()
