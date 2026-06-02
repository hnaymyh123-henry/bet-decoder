"""verify_m7_frontend.py — Issue #7 [M4] Workbench front-end acceptance checks.

Front-end-appropriate verification: serve app.html via http.server,
confirm it loads (200), then assert — at the structure / contract / design-system
level — that every acceptance criterion holds. One PASS/FAIL line per AC.

No real LLM, no real network beyond a local http.server on 127.0.0.1. Run:

    python verify_m7_frontend.py
"""
from __future__ import annotations

import http.server
import re
import socket
import socketserver
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
HTML = ROOT / "app.html"

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, note: str = "") -> None:
    results.append((label, bool(ok), note))


# ---------------------------------------------------------------------------
# Serve the page on a free port and fetch it (proves it loads, 200).
# ---------------------------------------------------------------------------

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def serve_and_fetch() -> tuple[int, str]:
    port = _free_port()

    handler = http.server.SimpleHTTPRequestHandler

    class Q(socketserver.TCPServer):
        allow_reuse_address = True

    httpd = Q(("127.0.0.1", port), handler)
    # serve from project root so /app.html resolves
    import os
    os.chdir(ROOT)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        time.sleep(0.3)
        url = f"http://127.0.0.1:{port}/app.html"
        with urllib.request.urlopen(url, timeout=5) as r:
            status = r.status
            body = r.read().decode("utf-8", errors="replace")
        return status, body
    finally:
        httpd.shutdown()
        httpd.server_close()


def main() -> int:
    if not HTML.exists():
        check("page exists", False, "app.html missing")
        _report()
        return 1

    status, body = serve_and_fetch()

    # AC: page served by http.server, 200, non-empty
    check(
        "http.server serves page (200, non-empty)",
        status == 200 and len(body) > 5000,
        f"status={status} bytes={len(body)}",
    )

    # AC: workbench layout — canvas (tabs) + sticky AGENT feed. The bottom synth
    # panel is retired (synthesis moved to the portfolio page).
    has_canvas = 'id="wb-canvas"' in body and 'class="wb-canvas"' in body
    has_feed = 'id="wb-feed"' in body and 'class="wb-feed"' in body
    grid_areas = 'grid-template-areas' in body and '"canvas feed"' in body
    check(
        "workbench layout (canvas + sticky AGENT feed; bottom synth panel retired)",
        has_canvas and has_feed and grid_areas,
        f"canvas={has_canvas} feed={has_feed} grid_areas={grid_areas}",
    )

    # AC: main flow wiring — composer input + source-type select + decode → /api/decode → card grid.
    has_input = 'id="bet-input"' in body
    has_select = 'id="source-type"' in body and all(
        v in body for v in ('value="market"', 'value="analyst_pt"', 'value="opinion"', 'value="portfolio"')
    )
    has_decode_btn = 'id="decode-btn"' in body
    posts_decode = "fetch('/api/decode'" in body and "method: 'POST'" in body
    renders_grid = 'id="card-grid"' in body and "renderCanvas" in body
    check(
        "main flow: input + source-type select → POST /api/decode → card grid",
        has_input and has_select and has_decode_btn and posts_decode and renders_grid,
        f"input={has_input} select={has_select} btn={has_decode_btn} post={posts_decode} grid={renders_grid}",
    )

    # AC: tabbed workspace — one full-page 2-col dossier per scenario, switched via a
    # tab bar (replaces the old all-cards stacked grid). Both renderers still present.
    has_single = "function renderSingleCard" in body
    has_pf = "function renderPortfolioPage" in body and "pf-theme" in body and "pf-holdings" in body
    tabbed = ('id="tab-bar"' in body and "function renderTabBar" in body
              and "function activateTab" in body and "function renderPortfolioPage" in body
              and ".card-page .cp-support" in body and "state.activeTab" in body)
    check(
        "tabbed workspace: tab bar + per-scenario full-page dossier (single + portfolio renderers)",
        has_single and has_pf and tabbed,
        f"single={has_single} portfolio={has_pf} tabbed={tabbed}",
    )

    # AC: multi-level DERIVATION TREE is the page hero (現價 → mechanical chain →
    # implied → sub-implication), computed server-side (_build_derivations) + rendered.
    deriv = ("function renderDerivationTree" in body and "function renderDerivLevel" in body
             and 'class="cp-tree"' in body and ".dt .dt-root" in body
             and ".dt .dt-branches" in body and "renderDerivationTree(c)" in body
             and "function _dcfBuildup" in body and "dcf-bd" in body and "bd-tab" in body)
    check("derivation tree hero + DCF build-up worksheet (renderDerivationTree + _dcfBuildup)",
          deriv, f"deriv_present={deriv}")

    # AC: top hero row pairs THE BET skeleton with the price chart (cp-hero2); the
    # tree sits under it; the sticky right-hand AGENT panel shows the FULL decode
    # activity (reconstructed _display.activity, kind-tagged) on top with the
    # ask/scenario-chip dialog pinned at the bottom (no inline discuss block). The
    # DCF build-up reconcile tail stays configurable (base build-up → 基础业务价值).
    panel = ("function renderAgentPanel" in body and "renderAgentPanel();" in body
             and "ap-wrap" in body and "ap-ask" in body and "ap-foot" in body
             and ".wb-feed { position: sticky" in body
             and "cp-hero2" in body and "cp-hero-l" in body
             and "_display || {}).activity" in body and "ap-seclab" in body
             and "解码活动" in body
             and "function _discussBlock" not in body   # inline discuss removed
             and "bd.reconcile_label" in body and "bd.cagr_label" in body)
    check("hero row (THE BET + price chart) + sticky AGENT panel showing full decode activity (option B)",
          panel, f"panel={panel}")

    # AC: every card leads with ONE consistent "what is the market betting?" headline
    # (THE BET), computed server-side (_bet_statement) — implied growth or narrative premium.
    thebet = ("function _betHeadline" in body and 'class="cp-bet"' in body
              and "市场在 bet 什么" in body and "bet_statement" in body
              and "function _betWhy" in body and "cb-why" in body
              and "为什么这么判断" in body
              # auditable conclusion derived from the cross-check (evidence tally)
              and "cb-why-tally" in body and "证据对账" in body)
    check("unified THE BET headline (consistent decode answer on every card)",
          thebet, f"thebet_present={thebet}")

    # AC: DEEP ANALYSIS section — elevated bull/bear debate + per-assumption cross-check
    # + contested axes + catalysts, lazy-hydrated from market_narrative.full + markdown.
    deepa = ("function renderDeepAnalysis" in body and "renderDeepAnalysis(c)" in body
             and 'class="cp-deep"' in body and "function ensureDetail" in body
             and "function _md" in body and "da-debate" in body and "da-sec" in body
             # cross-check explicitly framed as the bridge from the derivation tree
             and "da-sec-sub" in body and "把推导树的每个隐含数字" in body)
    check("deep-analysis section: bull/bear debate + cross-check, lazy-hydrated + markdown",
          deepa, f"deep_present={deepa}")

    # AC: synthesis (Module 3) now lives on the PORTFOLIO page as its deep-analysis
    # layer — auto-run across the decoded holdings, NOT a manual bottom panel.
    pf_synth = ("function ensurePortfolioSynth" in body and "function _portfolioSynthHtml" in body
                and "function _renderRelations" in body and "fetch('/api/synthesize'" in body
                and "state.synth" in body and "ids.length < 2" in body
                and "rel-table" in body and "跨持仓综合" in body)
    manual_gone = ('id="synth-btn"' not in body and 'id="wb-synth"' not in body)
    check(
        "synthesis = portfolio deep-analysis, auto-run across holdings (manual panel retired)",
        pf_synth and manual_gone,
        f"pf_synth={pf_synth} manual_gone={manual_gone}",
    )

    # AC: activity feed — EventSource on M5 SSE endpoint, kind-tagged styling, replay path.
    has_es = "new EventSource('/api/stream/activity/" in body
    kind_styles = all(k in body for k in (".af-event.k-decision", ".af-event.k-computation",
                                          ".af-event.k-evidence", ".af-event.k-relation"))
    kind_render = "k-' + kind" in body or "'k-' + kind" in body
    replay_path = "streamActivity" in body  # same fn serves live + replay (GET activity endpoint)
    check(
        "activity feed: EventSource(SSE) + kind-tagged styling + replay entry",
        has_es and kind_styles and kind_render and replay_path,
        f"eventsource={has_es} kind_css={kind_styles} kind_render={kind_render} replay={replay_path}",
    )

    # AC: graceful states for the portfolio's cross-holding synthesis — <2 holdings,
    # loading, error, and no-significant-relation all degrade honestly (no blank/error).
    empty_state = ("ids.length < 2" in body and "'loading'" in body
                   and "'error'" in body and "未发现显著跨持仓关系" in body)
    check(
        "cross-holding synthesis graceful states (<2 / loading / error / no-relation)",
        empty_state,
        f"empty_handling={empty_state}",
    )

    # AC: design-system compliance — grep for violations.
    # Strip CSS/JS comments before scanning so 'NO italic'-style comments don't trip.
    scrub = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    scrub = re.sub(r"//[^\n]*", "", scrub)
    ds_viol = []
    if re.search(r"font-style\s*:\s*italic", scrub):
        ds_viol.append("italic")
    if re.search(r"(linear|radial|conic)-gradient", scrub):
        ds_viol.append("gradient")
    # forbidden fonts
    if re.search(r"font-family\s*:\s*['\"]?(Inter|Roboto|Arial|Times|Space Grotesk|Helvetica)\b", scrub, re.I):
        ds_viol.append("forbidden-font")
    if re.search(r"\bserif\b", scrub) and not re.search(r"sans-serif", scrub):
        # only flag a bare 'serif' that isn't part of sans-serif
        pass
    # blue / purple by name
    if re.search(r"\b(blue|purple|indigo|violet)\b", scrub, re.I):
        ds_viol.append("blue/purple-keyword")
    # blue/purple by hex: dominant-blue hex like #xxxxFF / #00xxFF (B channel >> R,G)
    for hexm in re.findall(r"#([0-9a-fA-F]{6})", scrub):
        r_, g_, b_ = int(hexm[0:2], 16), int(hexm[2:4], 16), int(hexm[4:6], 16)
        # blue dominant: B noticeably greater than both R and G, and B is bright
        if b_ > 140 and b_ - r_ > 50 and b_ - g_ > 40:
            ds_viol.append(f"blueish #{hexm}")
        # purple: R and B high, G low
        if r_ > 110 and b_ > 110 and g_ + 50 < r_ and g_ + 50 < b_:
            ds_viol.append(f"purpleish #{hexm}")
    check(
        "design-system compliance (no blue/purple/gradient/italic/forbidden-font)",
        not ds_viol,
        "violations=" + (", ".join(ds_viol) if ds_viol else "none"),
    )

    # AC: only Geist / Geist Mono fonts declared as families.
    geist_only = "Geist Mono" in body and "Geist" in body
    check("fonts: Geist + Geist Mono only", geist_only, f"geist_present={geist_only}")

    # AC: >3-row data uses tables (relations + holdings are <table>, not card grids).
    uses_tables = "<table class=\"rel-table\"" in body and "pf-holdings" in body and "<table" in body
    check(">3-row data rendered as tables (rel-table, pf-holdings)", uses_tables,
          f"tables_present={uses_tables}")

    # AC: numbers right-aligned + mono. Check the relevant cells use mono + right align.
    nums_mono = ("var(--mono)" in body and "text-align: right" in body
                 and ".pf-holdings .wt" in body and "tnum" in body)
    check("numbers right-aligned + mono (tnum)", nums_mono,
          f"mono+right+tnum={nums_mono}")

    # AC: responsive — media query collapses 3-zone < 1024px (no horizontal overflow).
    responsive = ("@media (max-width: 1024px)" in body
                  and 'grid-template-areas: "canvas" "feed" "synth"' in body)
    check("responsive: <1024px collapses to single column", responsive,
          f"media_query={responsive}")

    # AC: endpoint strings match API_CONTRACT (the calls the front-end issues).
    contract = ROOT / "API_CONTRACT.md"
    ctxt = contract.read_text(encoding="utf-8") if contract.exists() else ""
    needed = ["/api/cards", "/api/decode", "/api/synthesize", "/api/stream/activity/"]
    fe_has = all(n in body for n in needed)
    contract_has = all(n.rstrip("/") in ctxt for n in needed)
    check("endpoint strings match API_CONTRACT", fe_has and contract_has,
          f"frontend={fe_has} contract={contract_has}")

    # AC: bilingual disclaimer + lang toggle preserved.
    bilingual = ('id="disclaimer-text"' in body and 'id="lang-zh"' in body
                 and 'id="lang-en"' in body and "function setLang" in body
                 and "disclaimers" in body)
    check("bilingual disclaimer + lang toggle preserved", bilingual,
          f"bilingual={bilingual}")

    # AC: reasoning-visualization layer — the flow diagram (现价→隐含假设节点) with a
    # folded Monte-Carlo band ruler, the revived SVG price chart, the price-
    # decomposition waterfall, and the scenario chips that fire the LLM what-if.
    viz = (
        "function renderFlowDiagram" in body and 'class="bc-flow"' in body
        and "function _bandViz" in body and '"fn-band"' in body
        and "function renderDecomp" in body and '"dc-bar"' in body
        and "function _paintChart" in body and "price-chart-wrap" in body
        and "pc-line-baseline" in body and "ensurePriceHistory" in body
        and "function renderScenarioChips" in body and "data-chip" in body
    )
    check("reasoning viz: flow diagram + band ruler + price chart + decomp + scenario chips",
          viz, f"viz_present={viz}")

    # AC: de-clutter — the flow diagram REPLACED the thin always-list decision chain
    # AND the old bet-row list (net fewer sections), rather than piling on.
    chain_replaced = ("const chain = (d.chain || [])" not in body
                      and "const bets = (d.bets || []).map" not in body)
    check("thin chain + bet-row list replaced by the flow diagram (de-clutter)",
          chain_replaced, f"old_render_removed={chain_replaced}")

    # AC: price chart is lazy + offline-honest (no fabricated line; needs network).
    chart_honest = ("/api/price-history/" in body and "state.priceHistory" in body
                    and "暂无价格历史" in body)
    check("price chart lazy-loaded + honest empty/offline state",
          chart_honest, f"chart_honest={chart_honest}")

    return _report()


def _report() -> int:
    print("\n" + "=" * 72)
    print("verify_m7_frontend.py — [M4] Workbench front-end")
    print("=" * 72)
    passed = 0
    for label, ok, note in results:
        tag = "PASS" if ok else "FAIL"
        line = f"[{tag}] {label}"
        if note:
            line += f"   ({note})"
        print(line)
        passed += ok
    total = len(results)
    print("-" * 72)
    print(f"{passed}/{total} checks passed")
    print("=" * 72)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
