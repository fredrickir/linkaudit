"""linkaudit —— 校验文档里的外链可达性与落点。

设计原则：
1. 只返回 200 不算通过，指向预期页面才算。
2. 暂时不可达与永久失效分开统计。
3. 逐条给结论，不只给一个数字。

仅依赖 Python 标准库。

用法:
    python linkaudit.py --help
    python linkaudit.py --file README.md
    python linkaudit.py --file README.md --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Markdown 链接：[文本](url)
MD_LINK_RE = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<url>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
# 裸链接
BARE_URL_RE = re.compile(r"(?<![(\w])(https?://[^\s<>()\[\]\"']+)")

# 明确跳过的协议
SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "data:", "#", "ftp://")

USER_AGENT = ("Mozilla/5.0 (compatible; linkaudit/1.0; "
              "+https://github.com/fredrickir/linkaudit)")

# 状态分类
OK = "ok"
REDIRECTED_OK = "redirected"
LOST = "lost"                      # 永久失效：404 / 410
TEMP_UNREACHABLE = "temporarily_unreachable"   # 暂时不可达：超时 / 5xx / 网络错
WRONG_TARGET = "wrong_target"      # 指向不明确：跳到了首页之类
BLOCKED = "blocked"                # 需要认证或被拒

STATUS_LABEL = {
    OK: "可用",
    REDIRECTED_OK: "可用（经跳转）",
    LOST: "已失效",
    TEMP_UNREACHABLE: "暂时不可达",
    WRONG_TARGET: "指向不明确",
    BLOCKED: "被拒绝访问",
}

# 指向首页的典型特征：路径只有 / 或 /index.html 之类
GENERIC_PATHS = {"/", "/index.html", "/index.htm", "/index.php", "/home"}


def extract_links(text: str) -> list[dict]:
    """从文本里抽出所有链接，保留出处行号。"""
    links: list[dict] = []
    seen: dict[str, int] = {}

    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            continue
        for m in MD_LINK_RE.finditer(line):
            url = m.group("url").strip()
            if url.lower().startswith(SKIP_SCHEMES):
                continue
            _record(links, seen, url, m.group("text").strip(), number, "markdown")
        for m in BARE_URL_RE.finditer(line):
            url = m.group(1).rstrip(".,;:")
            if url.lower().startswith(SKIP_SCHEMES):
                continue
            _record(links, seen, url, "", number, "bare")

    # 按出现顺序保留第一条，同一 URL 只查一次
    out = []
    for link in links:
        if link["url"] in seen and seen[link["url"]] != link["line"]:
            continue
        out.append(link)
    return out


def _record(links: list[dict], seen: dict, url: str, text: str,
            line: int, kind: str) -> None:
    if url in seen:
        return
    seen[url] = line
    links.append({"url": url, "text": text, "line": line, "kind": kind})


def check_link(url: str, timeout: int = 15, retries: int = 1) -> dict:
    """检查一个链接的可达性与落点。

    只发 HEAD 请求；有些站点不支持 HEAD，失败后退回 GET。
    """
    result = {
        "url": url,
        "status": TEMP_UNREACHABLE,
        "final_url": "",
        "status_code": None,
        "detail": "",
        "redirect_chain": [],
        "attempts": 0,
    }

    request = urllib.request.Request(url, method="HEAD", headers={
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
    })

    for attempt in range(retries + 1):
        result["attempts"] = attempt + 1
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result["status_code"] = response.status
                result["final_url"] = response.geturl()
                result["redirect_chain"] = list(getattr(response, "redirects", []) or [])
                return _classify(result)
        except urllib.error.HTTPError as e:
            result["status_code"] = e.code
            result["final_url"] = e.geturl() if hasattr(e, "geturl") else ""
            if e.code in (404, 410):
                result["status"] = LOST
                result["detail"] = f"HTTP {e.code}：服务器明确表示该资源不存在"
                return result
            if e.code in (401, 403, 401):
                result["status"] = BLOCKED
                result["detail"] = f"HTTP {e.code}：需要认证或被拒绝，无法判定内容是否有效"
                return result
            if 500 <= e.code < 600:
                result["status"] = TEMP_UNREACHABLE
                result["detail"] = f"HTTP {e.code}：服务端错误，可能是暂时故障"
                if attempt < retries:
                    time.sleep(1.5)
                    continue
                return result
            result["status"] = TEMP_UNREACHABLE
            result["detail"] = f"HTTP {e.code}"
            return result
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            result["status"] = TEMP_UNREACHABLE
            result["detail"] = f"网络错误：{reason}"
            if attempt < retries:
                time.sleep(1.5)
                continue
            return result
        except Exception as e:  # noqa: BLE001
            result["status"] = TEMP_UNREACHABLE
            result["detail"] = f"检查失败：{type(e).__name__}: {e}"
            return result

    return result


def _classify(result: dict) -> dict:
    """根据最终地址判断落点是否明确。"""
    final = result.get("final_url") or result["url"]
    result["status"] = OK
    result["detail"] = "正常"
    if final != result["url"]:
        result["status"] = REDIRECTED_OK
        result["detail"] = f"跳转到 {final}"
        path = _path_of(final)
        if path in GENERIC_PATHS:
            result["status"] = WRONG_TARGET
            result["detail"] = (f"跳转到站点首页（{final}），"
                                f"不是预期的那一页；链接本身可达但指向不明确")
    return result


def _path_of(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).path or "/"


def summarize(results: list[dict]) -> dict:
    """统计各状态的数量。"""
    counts: dict[str, int] = {}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "total": len(results),
        "counts": counts,
        "ok_count": counts.get(OK, 0) + counts.get(REDIRECTED_OK, 0),
        "lost_count": counts.get(LOST, 0),
        "temp_count": counts.get(TEMP_UNREACHABLE, 0),
        "wrong_target_count": counts.get(WRONG_TARGET, 0),
        "blocked_count": counts.get(BLOCKED, 0),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="linkaudit",
        description="校验文档中��外链的可达性与最终落点（仅依赖标准库）",
        epilog=(
            "示例:\n"
            "  python linkaudit.py --file README.md\n"
            "  python linkaudit.py --file README.md --timeout 20 --retries 2\n"
            "  python linkaudit.py --file README.md --json\n"
            "\n"
            "「暂时不可达」与「已失效」是两类，不会混在一起统计。\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--file", "-f", required=True, help="Markdown 文件路径")
    ap.add_argument("--timeout", type=int, default=15, help="单次请求超时秒数")
    ap.add_argument("--retries", type=int, default=1, help="失败重试次数")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--only-problems", action="store_true", help="只列出有问题的链接")
    args = ap.parse_args(argv)

    path = Path(args.file)
    if not path.is_file():
        print(f"找不到文件：{path}", file=sys.stderr)
        return 2
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"读取失败：{e}", file=sys.stderr)
        return 2

    links = extract_links(text)
    if not links:
        print("文件里没有找到可检查的链接。")
        return 0

    results = []
    for index, link in enumerate(links, 1):
        print(f"[{index}/{len(links)}] {link['url']}", file=sys.stderr)
        checked = check_link(link["url"], timeout=args.timeout, retries=args.retries)
        checked["text"] = link["text"]
        checked["line"] = link["line"]
        results.append(checked)

    summary = summarize(results)

    if args.json:
        print(json.dumps({"summary": summary, "results": results},
                         ensure_ascii=False, indent=2))
        return 0

    print()
    print(f"文件：{path}")
    print(f"链接总数：{summary['total']}")
    print(f"  可用：        {summary['ok_count']}")
    print(f"  已失效：      {summary['lost_count']}")
    print(f"  暂时不可达：  {summary['temp_count']}")
    print(f"  指向不明确：  {summary['wrong_target_count']}")
    print(f"  被拒绝访问：  {summary['blocked_count']}")
    print()

    problems = [r for r in results if r["status"] not in (OK, REDIRECTED_OK)]
    if not problems:
        print("所有链接都可用，没有发现问题。")
        return 0

    print(f"需要处理的链接（{len(problems)} 条）：")
    for item in problems:
        print(f"  行 {item['line']:>4}  [{STATUS_LABEL.get(item['status'], item['status'])}]"
              f"  {item['url']}")
        print(f"          {item['detail']}")
        if item["status"] == WRONG_TARGET:
            print(f"          建议：换成直指具体页面的链接，或确认站点是否改版")
        elif item["status"] == LOST:
            print(f"          建议：找替代来源，或在链接旁注明原始出处")
        elif item["status"] == TEMP_UNREACHABLE:
            print(f"          建议：稍后重试确认；确认前不要判为死链")
        elif item["status"] == BLOCKED:
            print(f"          建议：链接需要登录，读者可能打不开，考虑补一个公开版本")
    print()
    print("说明：暂时不可达与已失效分开统计——把网络抖动算进死链，"
          "会让检查结果慢慢没人信。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
