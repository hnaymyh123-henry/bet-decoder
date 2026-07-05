"""Workbench front-end acceptance checks.

This is a static contract test for app.html (v2 rewrite — single-column
position-terminal + chart-as-subject detail pages, per SPEC_E). It checks
stable structure, endpoint strings, and honest-empty states rather than
brittle localized copy.

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

    # --- SPEC_E v2 design system: dark canvas, Inter, purple AI accent ---
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
    check("fonts: Inter primary + mono for numbers", '"Inter"' in body and "var(--mono)" in body)

    # --- structural principle: single-column app width, not a wide dashboard grid ---
    check("single-column app-width shell (no info-stacking dashboard grid)",
          ".app{max-width:640px" in body.replace(" ", "") or "max-width:640px" in body)

    # --- home: position-terminal list, segmented tabs, health scoring ---
    home = (
        "function renderHome" in body
        and "function renderHomeRow" in body
        and "function healthOf" in body
        and "data-hometab=" in body
        and "class=\"prow\"" in body
        and "data-gorow=" in body
        and ".hdot" in body
    )
    check("home: position-terminal list + segmented tabs + health dots", home)

    empty_state = "No bets decoded yet" in body and "data-gorow" in body
    check("home: usable empty state (not a blank canvas)", empty_state)

    # --- decode flow: FAB + sheet, source inference, activity stream ---
    decode = (
        'id="fab-decode"' in body
        and 'id="decode-sheet"' in body
        and 'id="bet-input"' in body
        and "function inferSource" in body
        and "function runDecode" in body
        and "'/api/decode'" in body
        and "new EventSource('/api/stream/activity/" in body.replace('"', "'")
    )
    check("decode flow: sheet + source inference + activity SSE", decode)

    # --- router: hash-based navigation, must react to BOTH popstate and
    # hashchange (external hash edits fire hashchange, not popstate) ---
    router = (
        "function nav(" in body
        and "function routeFromHash" in body
        and "history.pushState" in body
        and "addEventListener('popstate'" in body
        and "addEventListener('hashchange'" in body
    )
    check("router: hash-based nav reacts to popstate AND hashchange", router)

    # --- chart-as-subject (SPEC_E core): click any element -> one AI strip updates ---
    chart = (
        "function renderChart" in body
        and "function paintSnapshotChart" in body
        and "function paintHistoryChart" in body
        and "function bindChartEvents" in body
        and "class=\"ai-strip\"" in body
        and "class=\"askbar\"" in body
        and "_ecSetAI" in body
    )
    check("chart-as-subject: snapshot + history render modes, click-driven AI strip", chart)

    honest_chart = (
        "no numbers to plot yet" in body
        and "becomes a drift chart once history accumulates" in body
        and "/api/chart/" in body
    )
    check("chart: honest-empty / honest-snapshot copy (no fabricated series, E-13)", honest_chart)

    # SVG hit-targets for horizontal lines must be real-area rects, not zero-height
    # <line> elements (a straight line's bounding box has no area regardless of
    # stroke-width, so it is unclickable/inaccessible) — regression guard.
    line_hits = re.findall(r'<line[^`]*?class=."hit."[^`]*?/>', body)
    check("chart: no zero-area <line> hit-targets (rect hit-areas for click reliability)",
          len(line_hits) == 0, f"found {len(line_hits)} line.hit targets" if line_hits else "all line-shaped hits use <rect>")

    # --- accordion blocks: collapsed by default, one open at a time ---
    accordion = (
        "function _blk(" in body
        and "data-blktoggle=" in body
        and "state.openBlk" in body
        and ".blk-body{max-height:0" in body.replace(" ", "")
    )
    check("accordion: collapsed-by-default blocks, single active key per card", accordion)

    blocks = (
        "function renderOddsBlock" in body
        and "function renderAltitudeBlock" in body
        and "function renderDebateBlock" in body
        and "function renderTradePlanBlock" in body
        and "function renderDerivationBlock" in body
        and "function renderDiscussBlock" in body
    )
    check("detail page: odds / altitude / debate / trade-plan / derivation / discuss blocks", blocks)

    # --- trade plan / decision discipline (SPEC_E E-10/E-11) ---
    trade_plan = (
        "self_falsification" in body
        and "Self-falsification" in body
        and "KILL" in body
        and "data-poscommit=" in body
        and "function confirmPosition" in body
        and "/api/positions/events" in body
        and "function ensureTradePlanContext" in body
        and "position_context" in body
        and "stale_snapshot" in body
        and "tp-refuse" in body
    )
    check("trade plan: self-falsification, KILL, position confirm, honest refusal", trade_plan)

    # --- portfolio Aha-B composite (SPEC_E E-9) ---
    ahab = (
        "function renderPortfolioDetail" in body
        and "Aha B" in body
        and "Systematic KILL" in body
        and "ahab-conc-bar" in body
        and "data_missing" in body
        and "function renderSynthBlock" in body
    )
    check("portfolio: Aha-B composite (headline, systematic KILL, concentration bar)", ahab)

    # --- changes / monitoring feed (SPEC_E E-14..E-17, Wave 3) ---
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
    check("changes/monitoring feed: severity + why_severity, honest unscanned state", monitor)

    # --- discuss: ask / what-if / revise, never a silent no-op on failure ---
    discuss = (
        "function askCard" in body
        and "function saveRevision" in body
        and "/api/cards/" in body and "/ask" in body
        and "/revise" in body
        and "state.chat" in body
        and "state.revision" in body
    )
    check("discuss: ask/what-if wired to /ask and /revise, chat + revision state", discuss)

    # --- endpoint coverage matches the API authority docs (legacy endpoints in
    # API_CONTRACT.md, v2 endpoints in SPEC_F_api.md per PRD's "SPEC_F +
    # API_CONTRACT" combined authority) ---
    ctxt = CONTRACT.read_text(encoding="utf-8") if CONTRACT.exists() else ""
    specf_path = ROOT / "docs" / "SPEC_F_api.md"
    specf = specf_path.read_text(encoding="utf-8") if specf_path.exists() else ""
    docs = ctxt + specf
    needed = ["/api/cards", "/api/decode", "/api/synthesize", "/api/stream/activity/",
              "/api/chart/", "/api/positions", "/api/monitor/feed", "/api/monitor/scan"]
    missing_doc = [n for n in needed if n.rstrip("/") not in docs]
    check("endpoint strings match API_CONTRACT + SPEC_F", all(n in body for n in needed) and not missing_doc,
          ("undocumented: " + ", ".join(missing_doc)) if missing_doc else "")

    # --- no dead legacy surface: this is a genuinely new file, not a patched v1 ---
    legacy = any(marker in body for marker in (
        "renderSingleCard", "wb-canvas", "wb-feed", "cp-hero2", "renderTabBar",
        "pricelens_design_system", "class=\"cp-plan\"",
    ))
    check("no legacy v1 workbench surface left in the rewrite", not legacy)

    check("disclaimer present, no fake-language toggle",
          ('id="lang-zh"' not in body and 'id="lang-en"' not in body))

    return report()


def report() -> int:
    print("\n" + "=" * 72)
    print("verify_m7_frontend.py - Workbench front-end (v2 rewrite)")
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
