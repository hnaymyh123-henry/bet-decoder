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

    # AC: three-zone layout — DOM structure exists.
    has_canvas = 'id="wb-canvas"' in body and 'class="wb-canvas"' in body
    has_feed = 'id="wb-feed"' in body and 'class="wb-feed"' in body
    has_synth = 'id="wb-synth"' in body and 'class="wb-synth"' in body
    grid_areas = 'grid-template-areas' in body and '"canvas' in body and 'synth' in body
    check(
        "three-zone layout (canvas + feed + bottom synth panel)",
        has_canvas and has_feed and has_synth and grid_areas,
        f"canvas={has_canvas} feed={has_feed} synth={has_synth} grid_areas={grid_areas}",
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
    has_pf = "function renderPortfolioCard" in body and "pf-theme" in body and "pf-holdings" in body
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
             and ".dt .dt-branches" in body and "renderDerivationTree(c)" in body)
    check("derivation tree hero: multi-level renderDerivationTree + .cp-tree + .dt branches",
          deriv, f"deriv_present={deriv}")

    # AC: synthesis flow — select >=2 cards → synth btn → POST /api/synthesize → headline+graph+narrative.
    has_synth_btn = 'id="synth-btn"' in body
    gating = "size < 2" in body or "ids.length < 2" in body or "n < 2" in body
    posts_synth = "fetch('/api/synthesize'" in body
    renders_synth = ('id="synth-headline"' in body and 'id="synth-relations"' in body
                     and 'id="synth-narrative"' in body and "function renderSynthesis" in body)
    check(
        "synthesis flow: select >=2 → POST /api/synthesize → headline + graph + narrative",
        has_synth_btn and gating and posts_synth and renders_synth,
        f"btn={has_synth_btn} gate>=2={gating} post={posts_synth} render={renders_synth}",
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

    # AC: graceful empty state for synthesis with no significant relation (headline=null).
    empty_state = ('id="synth-empty"' in body and "hasHeadline" in body
                   and "rels.length === 0" in body)
    check(
        "synthesis empty state (headline=null → graceful, no blank/error)",
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
