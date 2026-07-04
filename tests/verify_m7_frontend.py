"""Workbench front-end acceptance checks.

This is a static contract test for app.html. It intentionally checks stable
structure, endpoint strings, and honest-empty states rather than brittle
localized copy.

Run from repo root:
    python tests/verify_m7_frontend.py
"""
from __future__ import annotations

import http.server
import re
import socketserver
import threading
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).parent.parent
HTML = ROOT / "app.html"
CONTRACT = ROOT / "API_CONTRACT.md"
results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, note: str = "") -> None:
    results.append((label, bool(ok), note))


def serve_once() -> tuple[int, str]:
    class Handler(http.server.SimpleHTTPRequestHandler):
        pass

    class QueueServer(socketserver.TCPServer):
        allow_reuse_address = True

    import os

    os.chdir(ROOT)
    srv = QueueServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        time.sleep(0.2)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/app.html", timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    finally:
        srv.shutdown()
        srv.server_close()


def main() -> int:
    if not HTML.exists():
        check("page exists", False, "app.html missing")
        return report()

    status, body = serve_once()
    check("http.server serves page", status == 200 and len(body) > 1000, f"status={status} bytes={len(body)}")

    has_canvas = 'id="wb-canvas"' in body and 'class="wb-canvas"' in body
    has_feed = 'id="wb-feed"' in body and 'class="wb-feed"' in body
    layout = "grid-template-areas:" in body and "canvas" in body and "feed" in body
    check("workbench layout", has_canvas and has_feed and layout, f"canvas={has_canvas} feed={has_feed}")

    flow = (
        'id="bet-input"' in body
        and 'id="decode-btn"' in body
        and "function inferSource" in body
        and "fetch('/api/decode'" in body
        and 'id="card-grid"' in body
    )
    check("main decode flow wiring", flow)

    tabs = (
        'id="tab-bar"' in body
        and "function renderTabBar" in body
        and "function activateTab" in body
        and "state.activeTab" in body
        and "function renderSingleCard" in body
        and "function renderPortfolioPage" in body
    )
    check("tabbed workspace", tabs)

    hero = (
        "cp-hero2" in body
        and "cp-hero-l" in body
        and "cp-hero-r" in body
        and "function _reasoningTree" in body
        and "price-chart-wrap" in body
        and "function renderAgentPanel" in body
        and "Decode Activity" in body
        and "((c._display || {}).activity)" in body
        and "ap-foot" in body
        and "ap-ask" in body
    )
    check("hero row + sticky agent activity", hero)

    reasoning = (
        'class="cp-rtree"' in body
        and "Reasoning chain" in body
        and "function renderXrayTop" in body
        and "Decode conclusion" in body
        and "verdict_zh" in body
    )
    check("unified reasoning-chain lead", reasoning)

    deep = (
        "function renderDeepAnalysis" in body
        and "renderDeepAnalysis(c)" in body
        and 'class="cp-deep"' in body
        and "function ensureDetail" in body
        and "function _md" in body
        and "da-debate" in body
        and "da-sec" in body
        and "per-assumption cross-validation" in body
    )
    check("deep-analysis section", deep)

    limits = (
        "function _methodLimits" in body
        and "cp-limits" in body
        and "Method & Limitations" in body
        and "_methodLimits(c)" in body
        and "_methodLimits(pf)" in body
        and "not company-specific consensus" in body
        and "CAPM cost of equity" in body
        and "Does not constitute investment advice" in body
    )
    check("method and limits disclosure", limits)

    citations = (
        "function _citationsBlock" in body
        and "function _domain" in body
        and "da-sources" in body
        and "da-cite" in body
        and "ct-tier" in body
        and 'rel="noopener noreferrer"' in body
        and "af-reveal" in body
        and "@keyframes afReveal" in body
    )
    check("source citations and activity reveal", citations)

    portfolio = (
        "function ensurePortfolioSynth" in body
        and "function _portfolioSynthHtml" in body
        and "function _renderRelations" in body
        and "fetch('/api/synthesize'" in body
        and "state.synth" in body
        and "ids.length < 2" in body
        and "'loading'" in body
        and "'error'" in body
        and "No significant cross-holding relations found" in body
        and "rel-table" in body
        and "Cross-Holding Synthesis" in body
        and 'id="synth-btn"' not in body
        and 'id="wb-synth"' not in body
    )
    check("portfolio synthesis and graceful states", portfolio)

    activity = (
        "new EventSource('/api/stream/activity/" in body
        and all(k in body for k in (".af-event.k-decision", ".af-event.k-computation", ".af-event.k-evidence", ".af-event.k-relation"))
        and ("k-' + kind" in body or "'k-' + kind" in body)
        and "streamActivity" in body
    )
    check("activity feed SSE and replay", activity)

    scrub = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    scrub = re.sub(r"//[^\n]*", "", scrub)
    violations: list[str] = []
    if re.search(r"font-style\s*:\s*italic", scrub):
        violations.append("italic")
    if re.search(r"(linear|radial|conic)-gradient", scrub):
        violations.append("gradient")
    if re.search(r"font-family\s*:\s*['\"]?(Inter|Roboto|Arial|Times|Space Grotesk|Helvetica)\b", scrub, re.I):
        violations.append("forbidden-font")
    if re.search(r"\b(blue|purple|indigo|violet)\b", scrub, re.I):
        violations.append("blue/purple-keyword")
    for hexm in re.findall(r"#([0-9a-fA-F]{6})", scrub):
        r, g, b = int(hexm[0:2], 16), int(hexm[2:4], 16), int(hexm[4:6], 16)
        if b > 140 and b - r > 50 and b - g > 40:
            violations.append(f"blueish #{hexm}")
        if r > 110 and b > 110 and g + 50 < r and g + 50 < b:
            violations.append(f"purpleish #{hexm}")
    check("design-system compliance", not violations, ", ".join(violations) if violations else "none")

    check("fonts use Geist family", "Geist Mono" in body and "Geist" in body)
    check("tables for dense data", "<table class=\"rel-table\"" in body and "pf-holdings" in body and "<table" in body)
    check("numeric cells mono/right aligned", "var(--mono)" in body and "text-align: right" in body and ".pf-holdings .wt" in body and "tnum" in body)
    check("responsive single-column collapse", "@media (max-width: 1024px)" in body and 'grid-template-areas: "canvas" "feed" "synth"' in body)

    ctxt = CONTRACT.read_text(encoding="utf-8") if CONTRACT.exists() else ""
    needed = ["/api/cards", "/api/decode", "/api/synthesize", "/api/stream/activity/"]
    check("endpoint strings match API_CONTRACT", all(n in body for n in needed) and all(n.rstrip("/") in ctxt for n in needed))

    check("disclaimer present and no fake language toggle", 'id="disclaimer-text"' in body and "Disclaimer." in body and 'id="lang-zh"' not in body and 'id="lang-en"' not in body)

    viz = (
        "function renderFlowDiagram" in body
        and 'class="bc-flow"' in body
        and "function _bandViz" in body
        and '"fn-band"' in body
        and "function _paintChart" in body
        and "price-chart-wrap" in body
        and "pc-line-baseline" in body
        and "ensurePriceHistory" in body
        and "function renderScenarioChips" in body
        and "data-chip" in body
    )
    check("reasoning visualization layer", viz)
    check("old thin chain removed", "const chain = (d.chain || [])" not in body and "const bets = (d.bets || []).map" not in body)
    check("price chart lazy + honest empty", "/api/chart/" in body and "state.priceHistory" in body and ("no price history yet" in body or "No price history available" in body))

    return report()


def report() -> int:
    print("\n" + "=" * 72)
    print("verify_m7_frontend.py - Workbench front-end")
    print("=" * 72)
    passed = 0
    for label, ok, note in results:
        tag = "PASS" if ok else "FAIL"
        print(f"[{tag}] {label}" + (f"  ({note})" if note else ""))
        if ok:
            passed += 1
    print("-" * 72)
    print(f"{passed}/{len(results)} checks passed")
    print("=" * 72)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
