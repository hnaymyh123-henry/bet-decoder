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

    # SPEC_E §E-19/E-20 v2 design system: dark canvas #0B0E11, Inter, purple
    # #7B61FF AI/agent accent, up-green / down-red, tabular numbers. The old v1
    # linter (oxblood/Geist-only, no-purple, no-gradient) is retired — it forbade
    # exactly the palette the v2 spec it was meant to guard now mandates.
    ds_tokens = {
        "dark canvas #0B0E11": "#0B0E11",
        "card surface #161A1F": "#161A1F",
        "AI/agent accent #7B61FF (purple)": "#7B61FF",
        "up-green #16C784": "#16C784",
        "down-red #F6465D": "#F6465D",
        "warn amber #F0B90B": "#F0B90B",
    }
    missing_tokens = [name for name, hexv in ds_tokens.items() if hexv not in body]
    check("design-system compliance (SPEC_E v2 palette)", not missing_tokens,
          ("missing " + ", ".join(missing_tokens)) if missing_tokens else "dark + #7B61FF purple accent present")

    check("fonts: Inter primary + mono for numbers (E-19)",
          '"Inter"' in body and ("Geist Mono" in body or "ui-monospace" in body))
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

    # --- v2 product surfaces (SPEC_E E-9/E-10/E-11/E-14..E-17 · Waves 2-3) ---
    trade_plan = (
        "function renderTradePlan" in body
        and "cp-plan" in body
        and "self_falsification" in body
        and "self-falsification" in body.lower()
        and "KILL line" in body
        and "function ensureTradePlanContext" in body
        and "/plan" in body and "method: 'POST'" in body
        and "function confirmPosition" in body
        and "/api/positions/events" in body
        and "data-poscommit" in body
        and "position_context" in body
        and "stale_snapshot" in body
    )
    check("trade plan / decision-discipline section (E-10/E-11)", trade_plan)

    ahab = (
        "function _portfolioAhaB" in body
        and "_portfolioAhaB(pf, decoded)" in body
        and "ahab-hd" in body
        and "Systematic KILL" in body
        and "ahab-conc-bar" in body
        and "data_missing" in body
    )
    check("portfolio Aha-B composite (E-9)", ahab)

    monitor = (
        "function renderChangesFeed" in body
        and "/api/monitor/feed" in body
        and "/api/monitor/scan" in body
        and 'id="changes-panel"' in body
        and 'id="changes-btn"' in body
        and "why_severity" in body
        and "sev-unknown" in body
        and "not reported as calm" in body
    )
    check("changes / monitoring feed (E-14..E-17, W3)", monitor)

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
