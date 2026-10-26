"""linkaudit 的自检测试。用本地 HTTP 服务，不依赖外网。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import linkaudit  # noqa: E402

ENTRY = os.path.join(BASE, "linkaudit.py")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静音
        pass

    def _respond(self):
        if self.path == "/ok":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        elif self.path == "/gone":
            self.send_response(404)
            self.end_headers()
        elif self.path == "/error":
            self.send_response(500)
            self.end_headers()
        elif self.path == "/login":
            self.send_response(403)
            self.end_headers()
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
        elif self.path == "/to-home":
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
        elif self.path == "/":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"home")
        else:
            self.send_response(404)
            self.end_headers()

    def do_HEAD(self):
        self._respond()

    def do_GET(self):
        self._respond()


def _start_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def test_extract_links_from_markdown() -> None:
    text = "See [docs](https://example.com/docs) and [more](https://example.com/x)."
    links = linkaudit.extract_links(text)
    urls = [l["url"] for l in links]
    assert "https://example.com/docs" in urls, urls
    assert "https://example.com/x" in urls, urls


def test_extract_links_skips_mailto() -> None:
    links = linkaudit.extract_links("[mail](mailto:a@example.com)")
    assert links == [], links


def test_extract_links_records_line_numbers() -> None:
    text = "line1\n\n[docs](https://example.com/docs)\n"
    links = linkaudit.extract_links(text)
    assert links[0]["line"] == 3, links


def test_extract_links_deduplicates() -> None:
    text = "[a](https://example.com/x)\n[b](https://example.com/x)\n"
    links = linkaudit.extract_links(text)
    assert len(links) == 1, links


def test_extract_links_finds_bare_urls() -> None:
    links = linkaudit.extract_links("see https://example.com/bare now")
    assert any(l["url"] == "https://example.com/bare" for l in links), links


def test_check_ok_status() -> None:
    server, port = _start_server()
    try:
        result = linkaudit.check_link(f"http://127.0.0.1:{port}/ok", timeout=5, retries=0)
        assert result["status"] == linkaudit.OK, result
    finally:
        server.shutdown()


def test_check_lost_status() -> None:
    server, port = _start_server()
    try:
        result = linkaudit.check_link(f"http://127.0.0.1:{port}/gone", timeout=5, retries=0)
        assert result["status"] == linkaudit.LOST, result
        assert "404" in result["detail"], result
    finally:
        server.shutdown()


def test_check_temporarily_unreachable_on_500() -> None:
    server, port = _start_server()
    try:
        result = linkaudit.check_link(f"http://127.0.0.1:{port}/error", timeout=5, retries=0)
        assert result["status"] == linkaudit.TEMP_UNREACHABLE, result
    finally:
        server.shutdown()


def test_check_blocked_on_403() -> None:
    server, port = _start_server()
    try:
        result = linkaudit.check_link(f"http://127.0.0.1:{port}/login", timeout=5, retries=0)
        assert result["status"] == linkaudit.BLOCKED, result
    finally:
        server.shutdown()


def test_check_redirect_to_page_is_ok() -> None:
    server, port = _start_server()
    try:
        result = linkaudit.check_link(f"http://127.0.0.1:{port}/redirect", timeout=5, retries=0)
        assert result["status"] == linkaudit.REDIRECTED_OK, result
    finally:
        server.shutdown()


def test_check_redirect_to_home_is_wrong_target() -> None:
    server, port = _start_server()
    try:
        result = linkaudit.check_link(f"http://127.0.0.1:{port}/to-home", timeout=5, retries=0)
        assert result["status"] == linkaudit.WRONG_TARGET, result
        assert "首页" in result["detail"], result
    finally:
        server.shutdown()


def test_connection_refused_is_temporary_not_lost() -> None:
    # 找一个没人监听的端口
    result = linkaudit.check_link("http://127.0.0.1:9/nothing", timeout=3, retries=0)
    assert result["status"] == linkaudit.TEMP_UNREACHABLE, result
    assert result["status"] != linkaudit.LOST, result


def test_summarize_counts() -> None:
    results = [
        {"status": linkaudit.OK},
        {"status": linkaudit.REDIRECTED_OK},
        {"status": linkaudit.LOST},
        {"status": linkaudit.TEMP_UNREACHABLE},
        {"status": linkaudit.WRONG_TARGET},
        {"status": linkaudit.BLOCKED},
    ]
    summary = linkaudit.summarize(results)
    assert summary["total"] == 6, summary
    assert summary["ok_count"] == 2, summary
    assert summary["lost_count"] == 1, summary
    assert summary["temp_count"] == 1, summary


def test_lost_and_temporary_are_counted_separately() -> None:
    summary = linkaudit.summarize([
        {"status": linkaudit.LOST},
        {"status": linkaudit.TEMP_UNREACHABLE},
    ])
    assert summary["lost_count"] == 1, summary
    assert summary["temp_count"] == 1, summary


def test_cli_help_exits_zero() -> None:
    r = subprocess.run([sys.executable, ENTRY, "--help"], capture_output=True,
                       text=True, timeout=30, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    assert "linkaudit" in r.stdout


def test_cli_missing_file_exits_two() -> None:
    r = subprocess.run([sys.executable, ENTRY, "--file", "nope.md"],
                       capture_output=True, text=True, timeout=30,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 2, (r.returncode, r.stderr)


def test_cli_no_links_exits_zero() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as f:
        f.write("# 标题\n\n这里没有任何链接。\n")
        path = f.name
    try:
        r = subprocess.run([sys.executable, ENTRY, "--file", path],
                           capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert "没有找到可检查的链接" in r.stdout, r.stdout
    finally:
        os.unlink(path)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK  ] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [FAIL] {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n共 {len(tests)} 项，失败 {failed} 项")
    raise SystemExit(1 if failed else 0)
