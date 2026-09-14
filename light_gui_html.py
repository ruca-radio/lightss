"""HTML template for the light_gui web interface."""

HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WLED</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #060712;
      --card: rgba(18, 22, 42, 0.72);
      --card-strong: rgba(25, 31, 58, 0.88);
      --text: #f7fbff;
      --text-secondary: #aab7d6;
      --muted: #7380a4;
      --accent: #8b5cf6;
      --accent-2: #06b6d4;
      --accent-3: #f472b6;
      --accent-hover: #7c3aed;
      --danger: #fb4666;
      --danger-hover: #e11d48;
      --secondary: rgba(255,255,255,0.10);
      --secondary-hover: rgba(255,255,255,0.16);
      --border: rgba(180, 205, 255, 0.16);
      --input-bg: rgba(4, 8, 20, 0.70);
      --success: #34d399;
      --warning: #fbbf24;
      --user-bubble: linear-gradient(135deg, #2563eb, #7c3aed);
      --ai-bubble: linear-gradient(135deg, rgba(6,182,212,.22), rgba(52,211,153,.18));
      --radius-lg: 22px;
      --radius-md: 14px;
      --radius-sm: 10px;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 10% 0%, rgba(139,92,246,.42), transparent 32rem),
        radial-gradient(circle at 88% 8%, rgba(6,182,212,.36), transparent 30rem),
        radial-gradient(circle at 50% 100%, rgba(244,114,182,.20), transparent 34rem),
        linear-gradient(180deg, #070817 0%, #050712 100%);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      display: flex;
      flex-direction: column;
    }
    body::before {
      content: '';
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image: linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px);
      background-size: 44px 44px;
      mask-image: linear-gradient(to bottom, rgba(0,0,0,.75), transparent 72%);
    }
    main {
      flex: 1;
      padding: 24px;
      max-width: 1280px;
      margin: 0 auto;
      width: 100%;
      position: relative;
      z-index: 1;
    }
    .music-identity { text-align:center; margin:12px 0 22px; min-height:58px; min-width:0; max-width:100%; overflow:hidden; }
    .music-identity h1, .music-identity p, .music-identity-source { min-width:0; max-width:100%; overflow-wrap:anywhere; word-break:break-word; }
    .music-identity h1 { margin:0; font-size:clamp(24px,4vw,42px); line-height:1.08; }
    .music-identity p { margin:5px 0 0; color:var(--text-secondary); }
    .music-identity-source { display:inline-block; margin-top:6px; padding:3px 9px; border:1px solid var(--border); border-radius:999px; color:var(--accent-2); font-size:11px; }
    h2 { font-size: 16px; margin: 0 0 14px; font-weight: 800; display: flex; align-items: center; gap: 8px; letter-spacing: .01em; }
    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1.35fr) minmax(300px, .95fr);
      gap: 18px;
    }
    @media (max-width: 820px) {
      main { padding: 16px; }
      .grid { grid-template-columns: 1fr; }
      .status-bar { flex-direction: column; align-items: flex-start; }
    }
    .card {
      background: linear-gradient(145deg, rgba(255,255,255,.10), rgba(255,255,255,.045));
      border-radius: var(--radius-lg);
      padding: 18px;
      border: 1px solid var(--border);
      box-shadow: 0 24px 80px rgba(0,0,0,.32), inset 0 1px 0 rgba(255,255,255,.12);
      margin-bottom: 18px;
      backdrop-filter: blur(18px) saturate(135%);
      position: relative;
      overflow: hidden;
      transition: border-color 0.3s ease, box-shadow 0.3s ease, transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    }
    .card:hover {
      border-color: rgba(6, 182, 212, 0.35);
      box-shadow: 0 30px 90px rgba(0,0,0,.45), 0 0 20px rgba(6, 182, 212, 0.12), inset 0 1px 0 rgba(255,255,255,0.18);
      transform: translateY(-2px);
    }
    .card::after {
      content: '';
      position: absolute;
      inset: 0;
      background: radial-gradient(circle at 0 0, rgba(255,255,255,.10), transparent 18rem);
      pointer-events: none;
    }
    .card > * { position: relative; z-index: 1; }
    .card:last-child { margin-bottom: 0; }
    .auto-card {
      background: linear-gradient(135deg, rgba(139,92,246,.32), rgba(6,182,212,.16) 48%, rgba(244,114,182,.18));
      border-color: rgba(255,255,255,.22);
      transition: border-color 0.4s ease, box-shadow 0.4s ease, background 0.4s ease;
    }
    @keyframes pulse-auto-card {
      0% {
        border-color: rgba(255,255,255,.22);
        box-shadow: 0 24px 80px rgba(0,0,0,.32), inset 0 1px 0 rgba(255,255,255,.12);
      }
      50% {
        border-color: rgba(6, 182, 212, 0.5);
        box-shadow: 0 24px 80px rgba(6, 182, 212, 0.25), inset 0 1px 0 rgba(255,255,255,.2);
      }
      100% {
        border-color: rgba(255,255,255,.22);
        box-shadow: 0 24px 80px rgba(0,0,0,.32), inset 0 1px 0 rgba(255,255,255,.12);
      }
    }
    .auto-card.running {
      animation: pulse-auto-card 3.5s ease-in-out infinite;
      background: linear-gradient(135deg, rgba(139,92,246,.45), rgba(6,182,212,.28) 48%, rgba(244,114,182,.3));
    }
    .auto-card h2 { color: #e9d5ff; }
    .manual-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
      gap: 18px;
    }
    .manual-grid .card {
      margin-bottom: 0;
    }
    .smart-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; margin-top: 12px; }
    .smart-chip { text-align:left; padding: 12px; border: 1px solid rgba(255,255,255,.14); background: rgba(255,255,255,.07); border-radius: 16px; min-height: 76px; }
    .smart-chip b { display:block; color:#fff; margin-bottom:4px; }
    .smart-chip span { color: var(--text-secondary); font-size: 12px; line-height: 1.3; }
    .tab-bar {
      display: flex;
      gap: 8px;
      margin: 18px 0;
      padding: 6px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,.06);
      border-radius: 999px;
      width: fit-content;
    }
    .tab-btn {
      background: transparent;
      color: var(--text-secondary);
      border: none;
      padding: 10px 18px;
      font-size: 13px;
      font-weight: 800;
      border-radius: 999px;
      cursor: pointer;
    }
    .tab-btn.active {
      color: white;
      background: linear-gradient(135deg, var(--accent), var(--accent-2));
      box-shadow: 0 10px 30px rgba(6,182,212,.25);
    }
    .tab-btn:hover:not(.active) { color: var(--text); background: rgba(255,255,255,.08); }
    .tab-content { display: none; }
    @keyframes tab-fade-in {
      from { opacity: 0; transform: translateY(10px); }
      to { opacity: 1; transform: translateY(0); }
    }
    .tab-content.active {
      display: block;
      animation: tab-fade-in 0.35s cubic-bezier(0.16, 1, 0.3, 1) forwards;
    }
    .target-bar {
      display: flex;
      gap: 10px;
      align-items: center;
      margin: 0 0 14px;
      padding: 10px 14px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,.06);
      border-radius: var(--radius-md);
      width: fit-content;
      font-size: 13px;
      font-weight: 800;
      color: var(--text-secondary);
    }
    .target-bar select { min-width: 190px; }
    .target-bar .target-hint { font-size: 11px; color: var(--muted); font-weight: 650; }
    .target-bar .firetv-bar { display: flex; gap: 8px; align-items: center; margin-left: 10px; padding-left: 12px; border-left: 1px solid var(--border); }
    .target-bar .firetv-toggle { display: flex; gap: 5px; align-items: center; cursor: pointer; white-space: nowrap; }
    .target-bar .firetv-toggle input { cursor: pointer; }
    .target-bar .firetv-open { padding: 3px 9px; font-size: 13px; line-height: 1.4; }
    .tv-observation { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 14px; padding: 10px 14px; border: 1px solid var(--border); border-radius: var(--radius-md); font-size: 13px; }
    .tv-observation label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
    .tv-observation button { padding: 5px 10px; }
    .tv-observation .observation-status { flex: 1 1 280px; min-width: 0; overflow-wrap: anywhere; color: var(--text-secondary); }
    .tv-observation small { flex-basis: 100%; color: var(--muted); }
    .smart-director-card { margin-bottom: 18px; padding: 16px; border: 1px solid rgba(6,182,212,.32); border-radius: var(--radius-md); background: linear-gradient(135deg, rgba(6,182,212,.10), rgba(139,92,246,.08)); }
    .smart-director-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
    .smart-director-header h2 { margin: 0 0 4px; font-size: 17px; }
    .smart-director-header p { margin: 0; color: var(--text-secondary); font-size: 12px; }
    .smart-director-toggle { display: flex; align-items: center; gap: 7px; font-weight: 800; white-space: nowrap; }
    .smart-director-grid { display: grid; grid-template-columns: repeat(2, minmax(150px, 1fr)); gap: 10px; }
    .smart-director-grid label, .smart-brightness-grid label { margin: 0; }
    .smart-director-grid select { width: 100%; margin-top: 5px; }
    .smart-brightness-grid { display: grid; grid-template-columns: repeat(4, minmax(110px, 1fr)); gap: 10px; margin-top: 12px; }
    .smart-brightness-label { display: block; white-space: nowrap; line-height: 1.25; }
    .smart-brightness-grid input, .smart-brightness-grid select { width: 100%; }
    .smart-brightness-grid select { margin-top: 5px; }
    .live-show-help { color: var(--text-secondary); font-size: 12px; margin: 12px 0 0; }
    .smart-director-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 9px; margin-top: 14px; }
    .smart-director-status { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }
    .smart-director-status div { min-width: 0; padding: 9px 10px; border-radius: var(--radius-sm); background: rgba(4,8,20,.42); }
    .smart-director-status span { display: block; color: var(--muted); font-size: 10px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; }
    .smart-director-status strong { display: block; margin-top: 3px; color: var(--text-secondary); font-size: 12px; overflow-wrap: anywhere; }
    @media (max-width: 640px) {
      .target-bar { max-width: 100%; flex-wrap: wrap; }
      .target-bar .firetv-bar { margin-left: 0; padding-left: 0; border-left: 0; }
      .smart-director-header { flex-direction: column; }
      .smart-director-grid, .smart-brightness-grid, .smart-director-status { grid-template-columns: 1fr; }
    }
    /* Custom scrollbars for a premium feel */
    ::-webkit-scrollbar {
      width: 8px;
      height: 8px;
    }
    ::-webkit-scrollbar-track {
      background: rgba(4, 8, 20, 0.5);
    }
    ::-webkit-scrollbar-thumb {
      background: rgba(139, 92, 246, 0.3);
      border-radius: 999px;
    }
    ::-webkit-scrollbar-thumb:hover {
      background: rgba(139, 92, 246, 0.5);
    }
    .full-bleed-preview {
      width: 100vw;
      margin-left: calc(-50vw + 50%);
      margin-right: calc(-50vw + 50%);
      background: linear-gradient(180deg, rgba(5,7,18,.30), rgba(5,7,18,.76));
      padding: 18px 0 12px;
      border-block: 1px solid rgba(255,255,255,.10);
      box-shadow: 0 25px 80px rgba(0, 0, 0, 0.55);
      position: relative;
      z-index: 10;
      backdrop-filter: blur(16px);
    }
    .strip-wrapper { width: 100%; max-width: none; padding: 0 8px; box-sizing: border-box; }
    .visualizer-hero .strip-wrapper { padding: 0 4px; }
    .led-strip {
      display: flex;
      width: 100%;
      gap: 0.5px;
      justify-content: flex-start;
      align-items: stretch;
      height: clamp(24px, 3.5vw, 45px);
      padding: 2px 1px;
      border-radius: 24px;
      background: linear-gradient(180deg, #111827 0%, #050712 54%, #02030a 100%);
      border: 1px solid rgba(255,255,255,.18);
      box-shadow: inset 0 0 42px rgba(0,0,0,.92), 0 0 0 8px rgba(255,255,255,.035), 0 20px 70px rgba(6,182,212,.18);
      position: relative;
      overflow: hidden;
    }
    .led-strip::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 45%; background: linear-gradient(180deg, rgba(255,255,255,.12) 0%, transparent 100%); pointer-events: none; z-index: 2; }
    .led-segment { flex: 1; min-width: 1px; background: #0d0d0d; border-radius: 999px; box-shadow: 0 0 10px currentColor, inset 0 0 5px rgba(255,255,255,.20), inset 0 -4px 6px rgba(0,0,0,.55); transition: background-color 16ms linear, box-shadow 16ms linear; position: relative; z-index: 1; }
    .led-strip.off .led-segment { background: #0b1020 !important; box-shadow: inset 0 0 2px rgba(0,0,0,0.8) !important; }
    .preview-meta { max-width: 1200px; margin: 10px auto 0; padding: 0 16px; text-align: center; }
    .light-info { font-size: 12px; color: var(--text-secondary); text-align: center; line-height: 1.4; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .light-info .label { display: inline-block; background: rgba(255,255,255,.08); padding: 3px 8px; border-radius: 999px; margin: 2px; border: 1px solid rgba(255,255,255,.12); font-size: 11px; }
    .big-button { width: 100%; padding: 16px 20px; font-size: 16px; border-radius: 18px; display: flex; align-items: center; justify-content: center; gap: 10px; }
    .big-button.secondary { background: linear-gradient(135deg, rgba(6,182,212,.42), rgba(59,130,246,.30)); }
    .big-button.secondary:hover { background: linear-gradient(135deg, rgba(6,182,212,.56), rgba(59,130,246,.44)); }
    .auto-status { text-align: center; font-size: 13px; color: var(--text-secondary); min-height: 20px; margin-top: 10px; }
    .mode-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
    .mode-header h2 { margin-bottom: 6px; }
    .mode-subtitle { font-size: 13px; color: var(--text-secondary); margin: 0; line-height: 1.4; max-width: 680px; }
    .mode-pill { flex: 0 0 auto; padding: 7px 11px; border-radius: 999px; border: 1px solid rgba(255,255,255,.16); background: rgba(4,8,20,.54); color: var(--text-secondary); font-size: 12px; font-weight: 850; }
    .mode-pill.running { color: #d1fae5; border-color: rgba(52,211,153,.45); background: rgba(16,185,129,.16); }
    .mode-actions { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: stretch; }
    .mode-actions .stop-button { min-width: 112px; padding-inline: 16px; }
    .mode-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: 12px; }
    .mode-summary-item { padding: 10px 12px; border-radius: var(--radius-sm); background: rgba(4,8,20,.46); border: 1px solid rgba(255,255,255,.10); }
    .mode-summary-item span { display: block; font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; margin-bottom: 5px; }
    .mode-summary-item strong { display: block; color: var(--text); font-size: 13px; line-height: 1.2; }
    button { border: 0; border-radius: var(--radius-md); padding: 10px 14px; background: linear-gradient(135deg, var(--accent), var(--accent-2)); color: white; font-weight: 800; cursor: pointer; transition: transform .16s ease, filter .16s ease, box-shadow .16s ease; font-size: 13px; box-shadow: 0 10px 28px rgba(139,92,246,.22); }
    button:hover { filter: brightness(1.12); transform: translateY(-1px); }
    button:focus { outline: 2px solid rgba(255,255,255,.7); outline-offset: 2px; }
    button:disabled { opacity: .6; cursor: wait; transform: none; }
    button.secondary { background: var(--secondary); box-shadow: none; }
    button.secondary:hover { background: var(--secondary-hover); }
    button.danger { background: linear-gradient(135deg, var(--danger), #f97316); }
    button.danger:hover { background: linear-gradient(135deg, var(--danger-hover), #ea580c); }
    label { display: grid; gap: 6px; margin: 10px 0; font-size: 13px; color: var(--text-secondary); font-weight: 650; }
    input, select, textarea { padding: 10px 12px; border-radius: var(--radius-sm); border: 1px solid rgba(255,255,255,.14); background: var(--input-bg); color: white; font-family: inherit; transition: all .15s ease; }
    input:focus, select:focus, textarea:focus { outline: 2px solid rgba(6,182,212,.8); outline-offset: 2px; border-color: var(--accent-2); }
    input[type="range"] { width: 100%; padding: 0; accent-color: var(--accent-2); }
    input[type="number"] { width: 86px; }
    select { min-width: 160px; }
    .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    .card-note { margin: 0 0 12px; font-size: 12px; line-height: 1.45; color: var(--text-secondary); }
    .status-output { margin-top: 10px; min-height: 18px; font-size: 12px; line-height: 1.45; color: var(--text-secondary); }
    .memory-output {
      margin-top: 10px;
      min-height: 96px;
      padding: 12px 14px;
      border-radius: 16px;
      border: 1px solid rgba(255,255,255,.10);
      background: rgba(4,8,20,.52);
      color: var(--text-secondary);
      white-space: pre-wrap;
      font-size: 12px;
      line-height: 1.45;
    }
    .score-row { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
    .swatch { width: 44px; height: 44px; border-radius: 16px; border: 1px solid rgba(255,255,255,.28); padding: 0; cursor: pointer; transition: transform .15s ease; box-shadow: 0 0 26px currentColor; }
    .swatch:hover { transform: scale(1.08) rotate(-2deg); }

    .meter-row { display: grid; grid-template-columns: 40px 1fr 40px; gap: 10px; align-items: center; margin-top: 10px; }
    .meter { position: relative; height: 16px; border-radius: var(--radius-sm); overflow: hidden; background: #0b0b0b; border: 1px solid #444; }
    .meter-fill { width: 0%; height: 100%; background: linear-gradient(90deg, #20c997, #ffd43b 65%, #ff6b6b); transition: width 65ms linear; }
    .meter-peak { position: absolute; top: 0; bottom: 0; left: 0%; width: 2px; background: white; opacity: .8; transition: left 120ms linear; }
    .beat-lamp { width: 16px; height: 16px; border-radius: 50%; background: #333; border: 1px solid #555; transition: background 80ms linear, box-shadow 80ms linear; }
    .beat-lamp.on { background: #ffe066; box-shadow: 0 0 14px #ffd43b; }
    .analysis-panel { margin-top: 14px; display: grid; gap: 12px; }
    .waveform-canvas { width: 100%; height: 50px; border-radius: var(--radius-md); background: #05070f; border: 1px solid rgba(255,255,255,.13); display: block; }
    .spectrum-canvas { width: 100%; height: 40px; border-radius: var(--radius-md); background: #05070f; border: 1px solid rgba(255,255,255,.13); display: block; }
    .visualizer-hero {
      margin: 0 0 18px;
      border-radius: 24px;
      overflow: hidden;
      border: 1px solid var(--border);
      background: linear-gradient(145deg, rgba(18,22,42,.95), rgba(8,10,24,.92));
      box-shadow: 0 30px 90px rgba(0,0,0,.6);
    }
    .player-stage {
      margin: 0 0 14px;
    }
    .player-stage-main {
      display: grid;
      grid-template-columns: 148px minmax(0, 1fr) minmax(180px, 220px);
      gap: 18px;
      align-items: stretch;
      padding: 16px 16px 10px;
    }
    .player-art {
      width: 148px;
      height: 148px;
      border-radius: 18px;
      overflow: hidden;
      background: radial-gradient(circle at 30% 20%, rgba(139,92,246,.45), #0b1020 62%);
      border: 1px solid rgba(255,255,255,.14);
      box-shadow: 0 18px 40px rgba(0,0,0,.45);
      position: relative;
    }
    .player-stage .album-art {
      width: 100%;
      height: 100%;
      margin: 0;
      display: none;
      border: 0;
      border-radius: 0;
    }
    .player-stage .album-art.visible { display: block; }
    .player-art-fallback {
      position: absolute; inset: 0;
      display: flex; align-items: center; justify-content: center;
      font-size: 42px; color: rgba(247,251,255,.72);
    }
    .player-art.has-art .player-art-fallback { display: none; }
    .player-now { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
    .player-kicker { font-size: 11px; font-weight: 800; color: var(--text-secondary); }
    .player-title {
      margin: 0;
      font-size: 22px;
      font-weight: 800;
      letter-spacing: -.02em;
      line-height: 1.15;
      color: #fff;
    }
    .player-artist { margin: 0; font-size: 14px; color: var(--text-secondary); min-height: 1.2em; }
    .player-transport { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 8px; }
    .player-transport select { min-width: 150px; }
    .player-play { min-width: 52px; }
    .player-auth {
      padding: 12px;
      border-radius: 16px;
      background: rgba(4,8,20,.54);
      border: 1px solid rgba(255,255,255,.10);
      display: flex;
      flex-direction: column;
      gap: 8px;
      justify-content: center;
    }
    .player-auth[hidden], #applePairing[hidden], #appleSignedIn[hidden] { display: none; }
    .apple-qr { width: 180px; height: 180px; margin: 0 auto; background: #fff; border-radius: 12px; padding: 8px; }
    .apple-qr img { width: 100%; height: 100%; display: block; image-rendering: pixelated; }
    .player-code { margin: 0; text-align: center; font-size: 12px; color: var(--text-secondary); }
    .player-code strong { color: #fff; letter-spacing: .12em; }
    .player-hint { margin: 0; text-align: center; font-size: 11px; color: var(--muted); line-height: 1.35; }
    .player-auth a { color: #67e8f9; font-size: 12px; text-align: center; }
    .player-deck {
      display: grid;
      grid-template-columns: minmax(280px, 1.1fr) minmax(0, .9fr);
      gap: 14px;
      margin: 0 0 14px;
    }
    .player-library {
      padding: 14px;
      border-radius: 20px;
      border: 1px solid var(--border);
      background: linear-gradient(145deg, rgba(18,22,42,.95), rgba(8,10,24,.92));
      min-height: 220px;
    }
    .player-library h3 {
      margin: 0 0 8px;
      font-size: 12px;
      font-weight: 800;
      color: var(--text-secondary);
    }
    .library-search { display: flex; gap: 8px; margin-bottom: 12px; }
    .library-search input { flex: 1; min-width: 0; }
    .library-cols { display: grid; grid-template-columns: 1fr 1.2fr; gap: 12px; }
    .library-list { display: flex; flex-direction: column; gap: 4px; max-height: 220px; overflow-y: auto; }
    .library-row {
      display: grid;
      grid-template-columns: 36px minmax(0, 1fr);
      gap: 8px;
      align-items: center;
      padding: 6px 8px;
      border-radius: 10px;
      border: 0;
      background: transparent;
      box-shadow: none;
      text-align: left;
      color: inherit;
      width: 100%;
    }
    .library-row:hover { background: rgba(255,255,255,.08); }
    .library-row img, .library-thumb {
      width: 36px; height: 36px; border-radius: 8px; object-fit: cover; background: #12182c;
    }
    .library-row b { display: block; font-size: 13px; color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .library-row span { display: block; font-size: 11px; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .library-empty { font-size: 12px; color: var(--muted); padding: 8px; }
    .player-follow {
      padding: 14px;
      border-radius: 20px;
      border: 1px solid var(--border);
      background: linear-gradient(135deg, rgba(139,92,246,.22), rgba(6,182,212,.10));
    }
    @media (max-width: 900px) {
      .player-stage-main, .player-deck, .library-cols { grid-template-columns: 1fr; }
      .player-art { width: 120px; height: 120px; }
    }
    .visualizer-header {
      display: flex; align-items: center; justify-content: space-between;
      padding: 4px 12px 2px; background: rgba(0,0,0,.25); border-bottom: 1px solid rgba(255,255,255,.08);
      font-size: 12px; font-weight: 700; letter-spacing: .04em; color: var(--text-secondary);
    }
    .visualizer-header .title { color: #fff; font-weight: 800; }
    .viz-row { display: grid; grid-template-columns: 2fr 3fr; gap: 8px; padding: 6px 14px 4px; }
    .model-responses-pane {
      background: linear-gradient(145deg, rgba(18,22,42,.95), rgba(8,10,24,.92));
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      margin: 0 0 12px;
      overflow: hidden;
      box-shadow: 0 10px 30px rgba(0,0,0,.3);
    }
    .responses-header {
      padding: 5px 10px;
      font-size: 10px;
      font-weight: 700;
      background: rgba(0,0,0,.3);
      display: flex;
      justify-content: space-between;
      align-items: center;
      color: var(--text-secondary);
    }
    .responses-header button {
      font-size: 9px;
      padding: 1px 6px;
      background: var(--secondary);
      color: var(--text-secondary);
      border: none;
      border-radius: 3px;
      cursor: pointer;
    }
    .responses-scroll {
      max-height: 260px;
      overflow-y: auto;
      padding: 6px 10px;
      font-size: 11px;
      line-height: 1.35;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      background: rgba(0,0,0,.15);
    }
    .responses-scroll .resp {
      margin-bottom: 4px;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .responses-scroll .ai { color: #a5f3fc; }
    .responses-scroll .user { color: #c4b5fd; opacity: 0.9; }
    .responses-scroll .decision { color:#fde68a; }
    .responses-scroll .error { color:#fda4af; }
    .responses-scroll .resp-meta { display:block; color:var(--muted); font-size:9px; margin-bottom:2px; }
    .band-gains { display:grid; grid-template-columns:repeat(8,minmax(44px,1fr)); gap:8px; margin-top:10px; }
    .band-gains label { font-size:10px; text-align:center; }
    .band-gains input { width:100%; }
    @media (max-width:700px) { .band-gains { grid-template-columns:repeat(4,minmax(44px,1fr)); } }

    /* Marquee / right-to-left scrolling for model plan responses */
    .marquee-wrapper {
      overflow: hidden;
      white-space: nowrap;
      position: relative;
      background: rgba(0,0,0,0.3);
      border-radius: 4px;
      margin: 2px 0;
      height: 28px;
      line-height: 28px;
    }
    .marquee-content {
      display: inline-block;
      padding-left: 100%;
      color: #67e8f9;
      font-size: 12px;
      animation: marquee-scroll 18s linear infinite;
      will-change: transform;
    }
    @keyframes marquee-scroll {
      0% { transform: translateX(0); }
      100% { transform: translateX(-100%); }
    }
    @media (max-width: 820px) { .viz-row { grid-template-columns: 1fr; } }
    .viz-panel { display: flex; flex-direction: column; gap: 8px; }
    .viz-label { font-size: 10px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); padding-left: 4px; }
    .energy-orb {
      width: 55px; height: 55px; border-radius: 999px; margin: 0 auto;
      background: radial-gradient(circle at 40% 30%, #fff, #334, #111);
      box-shadow: 0 0 0 8px rgba(255,255,255,.06), inset 0 -20px 30px rgba(0,0,0,.6);
      position: relative; transition: transform .06s linear, box-shadow .08s linear;
      border: 1px solid rgba(255,255,255,.2);
    }
    .energy-orb.beat { box-shadow: 0 0 0 8px rgba(255,224,102,.35), 0 0 42px #ffe066, inset 0 -20px 30px rgba(0,0,0,.6); transform: scale(1.06); }
    .energy-orb .inner { position:absolute; inset: 10px; border-radius: 999px; background: radial-gradient(circle, transparent 30%, rgba(0,0,0,.6)); }
    .band-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
    .band-label { display: flex; justify-content: space-between; gap: 6px; font-size: 11px; color: var(--text-secondary); margin-bottom: 6px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .band-meter { height: 10px; border-radius: 999px; background: rgba(255,255,255,.08); overflow: hidden; border: 1px solid rgba(255,255,255,.10); }
    .band-fill { width: 0%; height: 100%; border-radius: inherit; transition: width 55ms linear; }
    #bassFill { background: linear-gradient(90deg, #22d3ee, #2563eb); }
    #midFill { background: linear-gradient(90deg, #34d399, #fbbf24); }
    #trebleFill { background: linear-gradient(90deg, #f472b6, #fb7185); }
    .analysis-readouts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
    .readout { padding: 9px 10px; border-radius: var(--radius-sm); background: rgba(4,8,20,.58); border: 1px solid rgba(255,255,255,.10); }
    .readout .label { display: block; font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; margin-bottom: 4px; }
    .readout .value { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 15px; color: #f7fbff; }
    .confidence-meter { height: 5px; border-radius: 999px; background: rgba(255,255,255,.08); overflow: hidden; margin-top: 7px; }
    .confidence-fill { width: 0%; height: 100%; background: linear-gradient(90deg, #34d399, #fbbf24, #fb7185); transition: width 60ms linear; }
    @media (max-width: 560px) {
      .band-grid, .analysis-readouts { grid-template-columns: 1fr; }
      .mode-header { display: grid; }
      .mode-actions, .mode-summary, .music-toolbar { grid-template-columns: 1fr; }
    }
    .chat-container { display: flex; flex-direction: column; height: 360px; }
    .chat-history {
      flex: 1;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 8px;
      background: #0f0f0f;
      border-radius: var(--radius-md);
      border: 1px solid var(--border);
      margin-bottom: 10px;
    }
    .chat-msg { max-width: 85%; padding: 10px 12px; border-radius: 14px; font-size: 13px; line-height: 1.4; word-wrap: break-word; animation: fadeIn 0.2s ease; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
    .chat-msg.user { align-self: flex-end; background: var(--user-bubble); color: #fff; border-bottom-right-radius: 4px; }
    .chat-msg.ai { align-self: flex-start; background: var(--ai-bubble); color: #d5f5e3; border-bottom-left-radius: 4px; }
    .chat-msg.system { align-self: center; background: #222; color: #888; font-size: 11px; padding: 4px 10px; border-radius: 10px; }
    .chat-input-row { display: grid; grid-template-columns: 1fr auto; gap: 8px; align-items: center; }
    .chat-input-row input { width: 100%; margin: 0; }
    .chat-input-row button { padding: 10px 14px; font-size: 16px; }
    .chat-controls { display: flex; gap: 8px; margin-bottom: 8px; justify-content: flex-end; }
    .music-display { margin-top: 10px; padding: 12px; background: #0f0f0f; border-radius: var(--radius-md); border: 1px solid var(--border); min-height: 60px; }
    .music-title { font-size: 16px; font-weight: 600; color: #d6e4ff; margin: 0; }
    .music-artist { font-size: 13px; color: var(--text-secondary); margin: 4px 0 0; }
    .music-genre { font-size: 11px; color: #8fa7bd; margin-top: 4px; display: inline-block; background: #1a2a3a; padding: 2px 8px; border-radius: 10px; }
    .album-art { width: 80px; height: 80px; border-radius: var(--radius-md); background: #222; display: none; object-fit: cover; margin-top: 10px; border: 1px solid var(--border); }
    .album-art.visible { display: block; }
    .music-toolbar { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .state-display { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: var(--text-secondary); background: #0b0b0b; padding: 10px; border-radius: var(--radius-sm); border: 1px solid var(--border); white-space: pre-wrap; line-height: 1.5; }
    .schedule-list { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: var(--text-secondary); background: #0b0b0b; padding: 10px; border-radius: var(--radius-sm); border: 1px solid var(--border); min-height: 30px; white-space: pre-wrap; }
    .status-bar {
      position: sticky;
      bottom: 0;
      background: rgba(13,13,13,0.95);
      backdrop-filter: blur(8px);
      border-top: 1px solid var(--border);
      padding: 10px 16px;
      display: flex;
      gap: 16px;
      align-items: center;
      justify-content: space-between;
      font-size: 13px;
      z-index: 100;
    }
    .status-bar .indicator { display: flex; align-items: center; gap: 6px; }
    .status-bar .state-summary { color: var(--text-secondary); }
    .status-bar .last-action { color: #a8d6ff; flex-shrink: 0; }
    .example { margin-top: 8px; font-size: 12px; color: var(--text-secondary); cursor: pointer; padding: 6px 8px; background: #1a1a1a; border-radius: var(--radius-sm); border: 1px dashed #444; transition: all 0.15s ease; }
    .example:hover { background: #222; color: #ccc; }
    .knowledge { margin-top: 10px; color: #8fa7bd; font-size: 12px; line-height: 1.35; }
    .now-playing { display: none; }
    #aiReply, #aiConfirmations { display: none; }

    /* AI Vision Eye styles */
    .vision-card {
      background: rgba(255, 255, 255, 0.05);
      backdrop-filter: blur(10px);
      border-radius: 16px;
      border: 1px solid rgba(255, 255, 255, 0.1);
      padding: 24px;
      margin-top: 20px;
      position: relative;
      overflow: hidden;
    }
    .webcam-container {
      position: relative;
      width: 100%;
      max-width: 480px;
      border-radius: 12px;
      overflow: hidden;
      margin: 12px auto;
      border: 2px solid #00f0ff;
      aspect-ratio: 4/3;
      background: #000;
    }
    .webcam-video {
      width: 100%;
      height: 100%;
      object-fit: cover;
    }
    .scanning-laser {
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 3px;
      background: linear-gradient(to right, transparent, #00f0ff, transparent);
      box-shadow: 0 0 12px #00f0ff;
      animation: laserScan 2s infinite linear;
      display: none;
    }
    @keyframes laserScan {
      0% { top: 0%; }
      50% { top: 100%; }
      100% { top: 0%; }
    }
    .btn-vision {
      background: linear-gradient(135deg, #00f0ff, #0072ff);
      color: #fff;
      border: none;
      padding: 10px 20px;
      border-radius: 8px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.3s ease;
    }
    .btn-vision:hover {
      box-shadow: 0 0 15px rgba(0, 240, 255, 0.5);
    }

    /* Settings tab */
    .settings-msg { min-height: 18px; font-size: 12px; margin-top: 8px; font-weight: 650; }
    .settings-msg.ok { color: var(--success); }
    .settings-msg.err { color: var(--danger); }
    .settings-msg.info { color: var(--text-secondary); }
    .settings-badge { padding: 6px 12px; border-radius: 999px; font-size: 12px; font-weight: 800; border: 1px solid rgba(251,70,102,.45); background: rgba(251,70,102,.12); color: #fda4af; white-space: nowrap; }
    .settings-badge.set { border-color: rgba(52,211,153,.45); background: rgba(16,185,129,.14); color: #6ee7b7; }
    .agent-role { margin: 10px 0; padding: 10px; border-radius: var(--radius-sm); background: rgba(4,8,20,.46); border: 1px solid rgba(255,255,255,.10); }
    .agent-role h3 { margin: 0 0 8px; font-size: 13px; font-weight: 800; color: var(--text-secondary); }
    .agent-role .row { gap: 8px; }
    .agent-role input[type="text"] { width: 100%; }
    .controller-row { display: grid; grid-template-columns: auto minmax(90px,.8fr) minmax(120px,1.2fr) minmax(120px,1.2fr) minmax(0,1fr) auto; gap: 8px; align-items: center; margin: 8px 0; padding: 8px; border-radius: var(--radius-sm); background: rgba(4,8,20,.46); border: 1px solid rgba(255,255,255,.10); }
    .controller-row input { width: 100%; padding: 7px 9px; font-size: 12px; }
    .ctl-info { font-size: 11px; color: var(--text-secondary); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .ctl-remove { padding: 6px 10px; font-size: 11px; }
    .dot { width: 12px; height: 12px; border-radius: 50%; background: #64748b; box-shadow: 0 0 6px rgba(100,116,139,.6); flex: 0 0 auto; }
    .dot.ok { background: var(--success); box-shadow: 0 0 8px rgba(52,211,153,.8); }
    .dot.fail { background: var(--danger); box-shadow: 0 0 8px rgba(251,70,102,.8); }
    .sys-default { max-height: 160px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; font-size: 11px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: rgba(4,8,20,.58); border: 1px solid rgba(255,255,255,.10); border-radius: var(--radius-sm); padding: 10px; color: var(--text-secondary); }
    @media (max-width: 640px) {
      .controller-row { grid-template-columns: auto 1fr auto; }
      .controller-row .ctl-host, .controller-row .ctl-segments, .controller-row .ctl-info { grid-column: 2 / -1; }
    }
  </style>
</head>
<body>
  <main>
    <div class="music-identity" aria-live="polite">
      <h1 id="musicIdentityTitle">Nothing playing</h1>
      <p id="musicIdentityArtist"></p>
      <span class="music-identity-source" id="musicIdentitySource">No active source</span>
    </div>

__PLAYER_STAGE__

__PLAYER_DECK__

    <div class="target-bar">
      <span>🎯 Target</span>
      <select id="targetSelect" onchange="setTarget(this.value)" title="Which controller or channel actions apply to">
        <option value="all">All 4 strips</option>
      </select>
      <span class="target-hint" id="targetHint">applies to every action below</span>
      <span class="firetv-bar">
        <label class="firetv-toggle" title="Let the TV mirror the wall visuals">
          <input type="checkbox" id="firetvEnabled" onchange="toggleFiretv(this.checked)"> 📺 TV control
        </label>
        <button class="secondary firetv-open" onclick="openFiretvVisuals()" title="Open the ambient wall visuals on the TV">↗</button>
        <span class="target-hint" id="firetvMsg"></span>
      </span>
      <span class="firetv-bar">
        <label class="firetv-toggle" title="Legacy AI music mode; use Smart lighting for the controller-microphone renderer">
          <input type="checkbox" id="musicDirectorEnabled" onchange="toggleMusicDirector(this.checked)"> 🎵 Legacy AI music
        </label>
        <span class="target-hint" id="musicDirectorTrack"></span>
        <span class="target-hint" id="musicDirectorMsg"></span>
      </span>
    </div>

    <div class="tv-observation" role="region" aria-label="Read-only TV observation">
      <label><input type="checkbox" id="tvObservationEnabled" onchange="toggleTvObservation(this.checked)"> Observe TV (read-only)</label>
      <button class="secondary" id="tvObservationRefresh" onclick="refreshTvObservation()">Refresh TV status</button>
      <span class="observation-status" id="tvObservationStatus" role="status">Loading observation setting...</span>
      <small>This switch only permits observation; Smart lighting uses TV context when enabled. Refresh to read current TV activity.</small>
    </div>

    <section class="smart-director-card" id="smartDirectorCard" aria-labelledby="smartDirectorTitle">
      <div class="smart-director-header">
        <div>
          <h2 id="smartDirectorTitle">Smart lighting</h2>
          <p>Live music performance from the controller mic. Steady lighting when you watch TV.</p>
        </div>
        <label class="smart-director-toggle"><input type="checkbox" id="smartDirectorEnabled" onchange="markSmartDirectorDirty()"> Enabled</label>
      </div>
      <div class="smart-director-grid">
        <label>Mode
          <select id="smartDirectorMode" onchange="markSmartDirectorDirty()">
            <option value="auto">Auto</option>
            <option value="music">Music</option>
            <option value="tv">TV</option>
            <option value="manual">Manual</option>
          </select>
        </label>
        <label>TV theme
          <select id="smartDirectorTheme" onchange="markSmartDirectorDirty()">
            <option value="warm">Warm</option>
            <option value="neutral">Neutral</option>
            <option value="blue">Blue</option>
          </select>
        </label>
      </div>
      <div class="smart-brightness-grid">
        <label><span class="smart-brightness-label">Music <span id="smartDirectorMusicBrightnessText">65</span>%</span>
          <input id="smartDirectorMusicBrightness" type="range" min="0" max="100" value="65" oninput="smartDirectorMusicBrightnessText.textContent=this.value;queueLiveShowControl('music_brightness',Number(this.value)/100)">
        </label>
        <label><span class="smart-brightness-label">Day <span id="smartDirectorDayBrightnessText">30</span>%</span>
          <input id="smartDirectorDayBrightness" type="range" min="0" max="100" value="30" oninput="smartDirectorDayBrightnessText.textContent=this.value;markSmartDirectorDirty()">
        </label>
        <label><span class="smart-brightness-label">Evening <span id="smartDirectorEveningBrightnessText">15</span>%</span>
          <input id="smartDirectorEveningBrightness" type="range" min="0" max="100" value="15" oninput="smartDirectorEveningBrightnessText.textContent=this.value;markSmartDirectorDirty()">
        </label>
        <label><span class="smart-brightness-label">Night <span id="smartDirectorNightBrightnessText">5</span>%</span>
          <input id="smartDirectorNightBrightness" type="range" min="0" max="100" value="5" oninput="smartDirectorNightBrightnessText.textContent=this.value;markSmartDirectorDirty()">
        </label>
      </div>
      <div class="smart-brightness-grid" aria-label="Live music performance controls">
        <label>Motion
          <select id="smartDirectorMotion" onchange="queueLiveShowControl('music_motion',this.value)">
            <option value="auto">Auto · follow the music</option>
            <option value="flow">Flow · ribbons</option>
            <option value="punch">Punch · bass blooms</option>
            <option value="chase">Chase · traveling peaks</option>
            <option value="spectrum">Spectrum · frequency colors</option>
            <option value="comet">Comet · sweeping trail</option>
            <option value="ripple">Ripple · expanding waves</option>
          </select>
        </label>
        <label><span class="smart-brightness-label">Speed <span id="smartDirectorSpeedText">1.00×</span></span>
          <input id="smartDirectorSpeed" type="range" min="25" max="400" value="100" oninput="smartDirectorSpeedText.textContent=(Number(this.value)/100).toFixed(2)+'×';queueLiveShowControl('music_speed',Number(this.value)/100)">
        </label>
        <label><span class="smart-brightness-label">Intensity <span id="smartDirectorIntensityText">85</span>%</span>
          <input id="smartDirectorIntensity" type="range" min="0" max="100" value="85" oninput="smartDirectorIntensityText.textContent=this.value;queueLiveShowControl('music_intensity',Number(this.value)/100)">
        </label>
        <label><span class="smart-brightness-label">Color <span id="smartDirectorColorfulnessText">90</span>%</span>
          <input id="smartDirectorColorfulness" type="range" min="0" max="100" value="90" oninput="smartDirectorColorfulnessText.textContent=this.value;queueLiveShowControl('music_colorfulness',Number(this.value)/100)">
        </label>
      </div>
      <div class="band-gains" aria-label="16 band gains">
        <label>1<input class="band-gain" type="number" min="0" max="3" step="0.1" value="1" onchange="tuneBandGains()"></label>
        <label>2<input class="band-gain" type="number" min="0" max="3" step="0.1" value="1" onchange="tuneBandGains()"></label>
        <label>3<input class="band-gain" type="number" min="0" max="3" step="0.1" value="1" onchange="tuneBandGains()"></label>
        <label>4<input class="band-gain" type="number" min="0" max="3" step="0.1" value="1" onchange="tuneBandGains()"></label>
        <label>5<input class="band-gain" type="number" min="0" max="3" step="0.1" value="1" onchange="tuneBandGains()"></label>
        <label>6<input class="band-gain" type="number" min="0" max="3" step="0.1" value="1" onchange="tuneBandGains()"></label>
        <label>7<input class="band-gain" type="number" min="0" max="3" step="0.1" value="1" onchange="tuneBandGains()"></label>
        <label>8<input class="band-gain" type="number" min="0" max="3" step="0.1" value="1" onchange="tuneBandGains()"></label>
        <label>9<input class="band-gain" type="number" min="0" max="3" step="0.1" value="1" onchange="tuneBandGains()"></label>
        <label>10<input class="band-gain" type="number" min="0" max="3" step="0.1" value="1" onchange="tuneBandGains()"></label>
        <label>11<input class="band-gain" type="number" min="0" max="3" step="0.1" value="1" onchange="tuneBandGains()"></label>
        <label>12<input class="band-gain" type="number" min="0" max="3" step="0.1" value="1" onchange="tuneBandGains()"></label>
        <label>13<input class="band-gain" type="number" min="0" max="3" step="0.1" value="1" onchange="tuneBandGains()"></label>
        <label>14<input class="band-gain" type="number" min="0" max="3" step="0.1" value="1" onchange="tuneBandGains()"></label>
        <label>15<input class="band-gain" type="number" min="0" max="3" step="0.1" value="1" onchange="tuneBandGains()"></label>
        <label>16<input class="band-gain" type="number" min="0" max="3" step="0.1" value="1" onchange="tuneBandGains()"></label>
      </div>
      <button class="secondary" id="resetBandGains" onclick="resetBandGains()">Reset band gains</button>
      <p class="live-show-help" id="liveShowMessage" role="status">Music controls apply live without stopping the show. Color: original → vivid. Mode and TV settings use Apply.</p>
      <p class="live-show-help">Effective show: <strong id="smartDirectorEffectiveShow">waiting for renderer</strong></p>
      <div class="smart-director-actions">
        <button onclick="applySmartDirectorSettings(this)">Apply smart lighting</button>
        <button class="secondary" onclick="startBrowserMusicMode()" title="Use this browser's microphone instead of the controller microphone">Advanced browser mic</button>
        <span class="observation-status" id="smartDirectorMessage" role="status">Loading smart lighting...</span>
      </div>
      <div class="smart-director-status" aria-live="polite">
        <div><span>Controller mic</span><strong id="smartDirectorMicStatus">Idle</strong></div>
        <div><span>Renderer</span><strong id="smartDirectorRendererStatus">Idle</strong></div>
        <div><span>Decision</span><strong id="smartDirectorDecisionStatus">Waiting</strong></div>
      </div>
    </section>

    <div class="tab-bar">
      <button class="tab-btn" onclick="openCalibration()">Calibrate LEDs</button>
      <button class="tab-btn active" data-tab="live" onclick="switchTab('live')">Live View</button>
      <button class="tab-btn" data-tab="manual" onclick="switchTab('manual')">Manual Controls</button>
      <button class="tab-btn" data-tab="settings" onclick="switchTab('settings')">⚙ Settings</button>
    </div>

    <div id="tab-live" class="tab-content active">
      <div class="grid">
        <div class="left-col">
          <div class="card">
            <h2>AI Chat</h2>
            <div class="chat-controls">
              <button class="secondary" onclick="clearChat()" title="Clear all chat messages">Clear Chat</button>
            </div>
            <div class="chat-container">
              <div id="chatHistory" class="chat-history"></div>
              <div class="chat-input-row">
                <input id="aiInput" type="text" placeholder="Try: make the live music show faster with vivid colors" onkeydown="if(event.key==='Enter')askAI()">
                <button onclick="askAI()" title="Send message to AI">➤</button>
              </div>
            </div>
            <div class="example" onclick="useExamplePrompt()">Run effects with the beat. Make it interesting without strobes or harsh transitions.</div>
            <div class="knowledge">AI can control power, brightness, full RGBW color, safe effects, scenes, and browser mic beat mode.</div>
            <div id="aiReply" class="ai-reply"></div>
            <div id="aiConfirmations" class="confirmations"></div>
          </div>
          <div class="vision-card">
            <h3 style="color:#00f0ff; margin-top:0; display:flex; align-items:center; gap:8px;">
              <span>👁️</span> AI Vision Eye
            </h3>
            <p style="font-size:0.9rem; color:#aaa;">Let the AI lighting director look at your room and set the mood.</p>
            <div style="display:flex; gap:12px; justify-content:center; margin-bottom:12px;">
              <button id="toggleWebcamBtn" class="btn-vision" onclick="toggleWebcam()">Toggle Camera</button>
              <button id="analyzeRoomBtn" class="btn-vision" style="background: linear-gradient(135deg, #ff007f, #7f00ff); display:none;" onclick="analyzeRoom()">Analyze Room</button>
            </div>
            <div id="webcamContainer" class="webcam-container" style="display:none;">
              <video id="webcamVideo" class="webcam-video" autoplay playsinline></video>
              <div id="scanningLaser" class="scanning-laser"></div>
            </div>
            <div id="visionStatus" style="text-align:center; font-size:0.9rem; color:#00f0ff; margin-top:8px;"></div>
          </div>
        </div>
        <div class="right-col">
          <div class="card">
            <h2>Controller State</h2>
            <div class="state-display" id="stateDisplay">Connecting...</div>
            <div class="row" style="margin-top:12px;">
              <button onclick="send('on')" title="Turn lights on">On</button>
              <button class="danger" onclick="send('off')" title="Turn lights off">Off</button>
              <button class="danger" onclick="restartController()" title="Reboot the WLED controller — device will be offline briefly">Restart Device</button>
            </div>
            <div style="margin-top:10px;font-size:12px;color:var(--text-secondary);">
              Player, visualizers, and audio analysis sit at the top. State updates live via SSE.
            </div>
          </div>__NOW_PLAYING_CARD__
        </div>
      </div>
    </div>
  </div>

  <div id="tab-manual" class="tab-content">
    <div class="manual-grid">
      <div class="card">
        <h2>🎨 Color</h2>
      <div class="row">
        <button class="swatch" style="background:#f00" onclick="setColor(255,0,0,0)" title="Red (255,0,0,0)"></button>
        <button class="swatch" style="background:#00f" onclick="setColor(0,0,255,0)" title="Blue (0,0,255,0)"></button>
        <button class="swatch" style="background:#ff66bf" onclick="setColor(255,100,0,255)" title="Pink white (255,100,0,255)"></button>
        <button class="swatch" style="background:#00ff80" onclick="setColor(0,255,120,0)" title="Green (0,255,120,0)"></button>
      </div>
      <div class="row">
        <label>R <input id="r" type="number" min="0" max="255" value="255"></label>
        <label>G <input id="g" type="number" min="0" max="255" value="100"></label>
        <label>B <input id="b" type="number" min="0" max="255" value="0"></label>
        <label>W <input id="w" type="number" min="0" max="255" value="255"></label>
        <button onclick="setCustom()" title="Apply custom RGBW color">Set Color</button>
      </div>
      <div class="row" style="margin-top:12px;">
        <input id="hexColor" type="text" placeholder="#ff6600" maxlength="9" style="flex:1;">
        <button onclick="send('hex', {color: hexColor.value, transition: Number(transition.value)})" title="Set color from hex value">Set Hex</button>
      </div>
    </div>
    <div class="card">
      <h2>🌡️ Temperature</h2>
      <div class="row">
        <button class="secondary" onclick="send('temp', {kelvin: 2700, transition: Number(transition.value)})" title="Set warm 2700K temperature">Warm 2700K</button>
        <button class="secondary" onclick="send('temp', {kelvin: 5000, transition: Number(transition.value)})" title="Set daylight 5000K temperature">Daylight 5000K</button>
        <button class="secondary" onclick="send('temp', {kelvin: 6500, transition: Number(transition.value)})" title="Set cool 6500K temperature">Cool 6500K</button>
      </div>
      <label style="margin-top:12px;">Kelvin <span id="kelvinText">4000</span>K
        <input id="kelvin" type="range" min="2000" max="6500" value="4000" oninput="kelvinText.textContent=this.value" onchange="send('temp', {kelvin: Number(this.value), transition: Number(transition.value)})">
      </label>
      <label style="margin-top:8px;">CCT (white temp 0-255) <span id="cctText">127</span>
        <input id="cct" type="range" min="0" max="255" value="127" oninput="cctText.textContent=this.value" onchange="send('cct', {cct: Number(this.value), transition: Number(transition.value)})">
      </label>
    </div>
    <div class="card">
      <h2>✨ Effects</h2>
      <div class="row">
        <label>Effect
          <select id="fx">
            __SAFE_EFFECT_OPTIONS__
          </select>
        </label>
        <label>Speed <input id="speed" type="number" min="0" max="255" value="128"></label>
        <button onclick="send('fx', {effect: Number(fx.value), speed: Number(speed.value), transition: Number(transition.value)})" title="Apply selected effect and speed">Set Effect</button>
      </div>
      <label style="margin-top:10px;">Brightness <span id="briText">200</span>
        <input id="bri" type="range" min="0" max="255" value="200" oninput="briText.textContent=this.value" onchange="send('bri', {value: Number(this.value), transition: Number(transition.value)})">
      </label>
      <label>Transition <span id="transText">0</span>ms
        <input id="transition" type="range" min="0" max="2000" value="0" oninput="transText.textContent=this.value">
      </label>
    </div>
    <div class="card">
      <h2>🧱 Wall (4 columns)</h2>
      <div class="row">
        <label>Effect
          <select id="wallFx">
            __SAFE_EFFECT_OPTIONS__
          </select>
        </label>
        <label>Palette <input id="wallPal" type="number" min="0" max="70" value="6"></label>
        <label>Speed <input id="wallSpeed" type="number" min="0" max="255" value="140"></label>
      </div>
      <div class="row">
        <button onclick="wallMode('wall_span')" title="Same effect spanned across all four columns">Span</button>
        <button onclick="wallMode('wall_mirror')" title="Left half mirrors the right half">Mirror</button>
        <button onclick="wallMode('wall_chase')" title="Effect chase staggered along the wall">Chase</button>
      </div>
      <div class="row" style="margin-top:10px;">
        <label>Left FX
          <select id="wallFxL">
            __SAFE_EFFECT_OPTIONS__
          </select>
        </label>
        <label>Right FX
          <select id="wallFxR">
            __SAFE_EFFECT_OPTIONS__
          </select>
        </label>
        <button onclick="wallVersus()" title="Left half vs right half with different effects">Left vs Right</button>
      </div>
      <div class="row" style="margin-top:10px;">
        <label>Channel
          <select id="wallChannel">
            <option value="far-left">far-left</option>
            <option value="middle-left">middle-left</option>
            <option value="middle-right">middle-right</option>
            <option value="far-right">far-right</option>
          </select>
        </label>
        <button class="secondary" onclick="applyWallChannel()" title="Apply the effect/palette above to one channel only">Set Channel</button>
      </div>
    </div>
    <div class="card">
      <h2>🎬 Scenes</h2>
      <div class="row">
        <button onclick="send('scene', {name: 'warm'})" title="Apply warm scene">Warm</button>
        <button onclick="send('scene', {name: 'night'})" title="Apply night scene">Night</button>
        <button onclick="send('scene', {name: 'focus'})" title="Apply focus scene">Focus</button>
        <button onclick="send('scene', {name: 'ocean'})" title="Apply ocean scene">Ocean</button>
        <button onclick="send('scene', {name: 'party'})" title="Apply party scene">Party</button>
        <button class="secondary" onclick="send('random')" title="Apply random scene">Random</button>
      </div>
      <div class="row" style="margin-top:12px;">
        <input id="saveSceneName" type="text" placeholder="Scene name" style="flex:1;">
        <button class="secondary" onclick="send('save_scene', {name: saveSceneName.value})" title="Save current state as named scene">Save Scene</button>
        <button class="danger" onclick="send('delete_scene', {name: saveSceneName.value})" title="Delete named scene">Delete</button>
      </div>
    </div>
    <div class="card">
      <h2>🧬 Dynamic AI Scenes</h2>
      <p class="card-note">Generated mode is not a stock WLED effect. It paints the wall directly with exact-length per-strip frames and knows the top/bottom strip layout.</p>
      <label>Mood
        <input id="dynMood" type="text" placeholder="ocean calm, smoky neon, soft sunrise" style="width:100%;">
      </label>
      <div class="row">
        <label style="flex:1; margin:0;">Energy
          <select id="dynEnergy" style="width:100%;">
            <option value="">Auto</option>
            <option value="subtle">Subtle</option>
            <option value="low">Low</option>
            <option value="medium" selected>Medium</option>
            <option value="high">High</option>
            <option value="party">Party</option>
          </select>
        </label>
        <label style="flex:1; margin:0;">Motion
          <select id="dynMotion" style="width:100%;">
            <option value="">Auto</option>
            <option value="still">Still</option>
            <option value="drift">Drift</option>
            <option value="flow" selected>Flow</option>
            <option value="rise">Rise</option>
            <option value="chase">Chase</option>
            <option value="pulse">Pulse</option>
            <option value="shimmer">Shimmer</option>
          </select>
        </label>
      </div>
      <label>Strategy
        <input id="dynStrategy" type="text" placeholder="vertical_rise, center_bloom, left_to_right" style="width:100%;">
      </label>
      <div class="row">
        <label style="flex:1; margin:0;">Engine
          <select id="dynEngine" style="width:100%;">
            <option value="generated" selected>generated</option>
            <option value="effect">effect</option>
          </select>
        </label>
        <label style="flex:1; margin:0;">Composition
          <select id="dynComposition" style="width:100%;">
            <option value="unison" selected>unison</option>
            <option value="independent">independent</option>
            <option value="pairs">pairs</option>
            <option value="center_vs_outer">center_vs_outer</option>
            <option value="left_vs_right">left_vs_right</option>
            <option value="alternating">alternating</option>
            <option value="random_groups">random_groups</option>
          </select>
        </label>
      </div>
      <label>Intensity <span id="dynIntensityText">72</span>%
        <input id="dynIntensity" type="range" min="0" max="100" value="72" oninput="dynIntensityText.textContent=this.value">
      </label>
      <label>Unique colors
        <input id="dynColors" type="text" placeholder="#00c8b4, #003c64, #7a3cff" style="width:100%;">
      </label>
      <label>Seed
        <input id="dynSeed" type="text" placeholder="optional unique seed" style="width:100%;">
      </label>
      <div class="row" style="margin-top:8px;">
        <button onclick="applyDynamicScene()" title="Apply a dynamic AI scene">Apply Dynamic Scene</button>
      </div>
      <div class="card-note">Effect mode stays within the safe stock WLED effect surface. Generated mode is the new pixel-frame painter for this wall layout.</div>
      <div class="status-output" id="dynSceneStatus">Ready.</div>
    </div>
    <div class="card">
      <h2>⚡ Realtime Direct Mode</h2>
      <p class="card-note">Realtime streams unique palettes and motion over DDP and overrides WLED effects until you stop it or the timeout ends. Pass your own colors and a seed to build a look, or leave them blank and let mood + seed invent one.</p>
      <div class="row">
        <label style="flex:1; margin:0;">Shader
          <select id="rtShader" style="width:100%;">
            <option value="auto">auto from mood</option>
            <option value="red_rocks">red_rocks</option>
            <option value="aurora_flow">aurora_flow</option>
            <option value="bass_bloom">bass_bloom</option>
            <option value="liquid_gradient" selected>liquid_gradient</option>
            <option value="center_wave">center_wave</option>
            <option value="vertical_scan">vertical_scan</option>
            <option value="ember_rise">ember_rise</option>
            <option value="tide_pull">tide_pull</option>
            <option value="comet_fall">comet_fall</option>
            <option value="dusk_bloom">dusk_bloom</option>
            <option value="magma_column">magma_column</option>
            <option value="twin_helix">twin_helix</option>
            <option value="ribbon_drift">ribbon_drift</option>
          </select>
        </label>
        <label style="flex:1; margin:0;">Mood
          <input id="rtMood" type="text" placeholder="nocturne, storm, neon, sunrise" style="width:100%;">
        </label>
      </div>
      <div class="row">
        <label style="flex:1; margin:0;">Composition
          <select id="rtComposition" style="width:100%;">
            <option value="unison" selected>unison</option>
            <option value="independent">independent</option>
            <option value="pairs">pairs</option>
            <option value="center_vs_outer">center_vs_outer</option>
            <option value="left_vs_right">left_vs_right</option>
            <option value="alternating">alternating</option>
            <option value="random_groups">random_groups</option>
          </select>
        </label>
        <label style="flex:1; margin:0;">Intensity <span id="rtIntensityText">60</span>%
          <input id="rtIntensity" type="range" min="0" max="100" value="60" oninput="rtIntensityText.textContent=this.value">
        </label>
      </div>
      <div class="row">
        <label style="margin:0;">FPS
          <input id="rtFps" type="number" min="1" max="40" value="24" style="width:90px;">
        </label>
        <label style="margin:0;">Duration (s)
          <input id="rtDuration" type="number" min="1" max="900" value="60" style="width:100px;">
        </label>
        <label style="margin:0;">Seed
          <input id="rtSeed" type="text" placeholder="unique-look" style="width:110px;">
        </label>
      </div>
      <label>Unique colors
        <input id="rtColors" type="text" placeholder="#c8320c, #501004, #f0a028" style="width:100%;">
      </label>
      <div class="row" style="margin-top:8px;">
        <button onclick="startRealtime()" title="Start direct DDP realtime rendering">Start Realtime</button>
        <button onclick="designLook()" title="Design a unique look with the colorist/motion/critic agents">Design unique look</button>
        <button class="danger" onclick="stopRealtime()" title="Stop the realtime stream">Stop Realtime</button>
        <button class="secondary" onclick="refreshRealtimeStatus()" title="Fetch realtime status">Status</button>
      </div>
      <div class="status-output" id="realtimeStatus">Idle.</div>
    </div>
    <div class="card">
      <h2>🗣 Feedback & Memory</h2>
      <p class="card-note">Save a quick reaction so the next generated look can repeat what worked and avoid what missed.</p>
      <label>Notes
        <input id="feedbackNotes" type="text" placeholder="too dim on top, loved the teal, wrong strip" style="width:100%;">
      </label>
      <label>Tags
        <input id="feedbackTags" type="text" placeholder="liked-colors, too-dim, wrong-strip" style="width:100%;">
      </label>
      <label>Look ID (optional)
        <input id="feedbackLookId" type="text" placeholder="last look or paste an id" style="width:100%;">
      </label>
      <div class="score-row">
        <button onclick="submitLookFeedback(1)" title="Mark the look as working">That worked</button>
        <button class="danger" onclick="submitLookFeedback(-1)" title="Mark the look as bad">Bad look</button>
        <button class="secondary" onclick="submitLookFeedback(0)" title="Send notes without a positive or negative score">Note only</button>
      </div>
      <div class="row" style="margin-top:8px;">
        <button class="secondary" onclick="refreshLookMemory()" title="Load the remembered look summary">Show memory summary</button>
      </div>
      <div class="status-output" id="feedbackStatus">Ready.</div>
      <pre class="memory-output" id="lookMemorySummary">No look memory loaded yet.</pre>
    </div>
    <div class="card">
      <h2>⏱️ Timers & Simulations</h2>
      <div class="row" style="margin-bottom:8px;">
        <label style="margin:0;">Preset ID (1-250)
          <input id="presetId" type="number" min="1" max="250" value="1" style="width:90px;">
        </label>
        <button onclick="send('preset', {id: Number(presetId.value), transition: Number(transition.value)})" title="Load WLED preset by ID">Load Preset</button>
      </div>
      <div class="row" style="margin-bottom:8px;">
        <label style="margin:0;">Interval (s)
          <input id="cycleInterval" type="number" min="5" max="3600" value="60" style="width:90px;">
        </label>
        <button onclick="send('cycle_start', {interval: Number(cycleInterval.value)})" title="Start automatic scene cycling">Start Cycle</button>
        <button class="secondary" onclick="send('cycle_stop')" title="Stop scene cycling">Stop Cycle</button>
      </div>
      <div class="row" style="margin-bottom:8px;">
        <label style="margin:0;">Sunrise (min)
          <input id="sunriseMinutes" type="number" min="1" max="120" value="30" style="width:90px;">
        </label>
        <button onclick="send('sunrise_start', {minutes: Number(sunriseMinutes.value)})" title="Start sunrise wake-up simulation">Start Sunrise</button>
        <button class="secondary" onclick="send('sunrise_stop')" title="Stop sunrise simulation">Stop</button>
      </div>
      <div class="row">
        <label style="margin:0;">Fade (min)
          <input id="fadeMinutes" type="number" min="1" max="120" value="30" style="width:90px;">
        </label>
        <button onclick="send('fade_off', {minutes: Number(fadeMinutes.value)})" title="Start gradual fade-off timer">Start Fade</button>
      </div>
    </div>
    <div class="card">
      <h2>📅 Schedule</h2>
      <div class="row">
        <label style="margin:0;">Time
          <input id="schedTime" type="time" style="padding:8px;">
        </label>
        <label style="margin:0;">Action
          <select id="schedAction">
            <option value="on">On</option>
            <option value="off">Off</option>
            <option value="scene">Scene</option>
          </select>
        </label>
        <input id="schedScene" type="text" placeholder="Scene name (if scene)" style="flex:1;">
        <button onclick="addSchedule()" title="Add schedule entry">Add</button>
      </div>
        <div class="schedule-list" id="scheduleList" style="margin-top:10px;">No schedules.</div>
        <button class="secondary" style="margin-top:8px;" onclick="listSchedule()" title="Refresh schedule list">Refresh List</button>
      </div>
    </div>
  </div>

  <div id="tab-settings" class="tab-content">
    <div class="manual-grid">
      <div class="card">
        <h2>🤖 AI Provider</h2>
        <label>Provider
          <select id="setAiProvider" onchange="aiProviderChanged()">
            <option value="openai">OpenAI</option>
            <option value="openrouter">OpenRouter</option>
            <option value="ollama">Ollama (local)</option>
            <option value="custom">Custom</option>
          </select>
        </label>
        <label>Base URL
          <input id="setAiBaseUrl" type="text" placeholder="https://api.openai.com/v1" style="width:100%;">
        </label>
        <div class="row">
          <label style="flex:1;">Model
            <input id="setAiModel" type="text" placeholder="gpt-4o-mini" style="width:100%;">
          </label>
          <label style="flex:1;">Vision model
            <input id="setAiVisionModel" type="text" placeholder="gpt-4o" style="width:100%;">
          </label>
        </div>
        <div class="row" style="align-items:flex-end;">
          <label style="flex:1;">API key env var
            <input id="setAiKeyEnv" type="text" placeholder="OPENAI_API_KEY" style="width:100%;">
          </label>
          <span class="settings-badge" id="setAiKeyBadge">API key: unknown</span>
        </div>
        <div class="row" style="margin-top:10px;">
          <button onclick="saveAiSettings(this)" title="Save AI provider settings">Save</button>
          <button class="secondary" onclick="testAiConnection(this)" title="Test the configured AI connection">Test connection</button>
        </div>
        <div class="settings-msg" id="aiSettingsMsg"></div>
      </div>

      <div class="card">
        <h2>🎨 Look Agents</h2>
        <p class="card-note">Optional per-role models for colorist, motion, and critic. Leave provider/base URL blank to inherit the main AI provider.</p>
        <div class="agent-role">
          <h3>Colorist</h3>
          <label><input type="checkbox" id="agentColoristEnabled"> Enabled</label>
          <label>Model
            <input id="agentColoristModel" type="text" placeholder="inherit or e.g. gpt-4o-mini" style="width:100%;">
          </label>
          <div class="row">
            <label style="flex:1;">Provider
              <input id="agentColoristProvider" type="text" placeholder="optional" style="width:100%;">
            </label>
            <label style="flex:1;">Base URL
              <input id="agentColoristBaseUrl" type="text" placeholder="optional" style="width:100%;">
            </label>
          </div>
        </div>
        <div class="agent-role">
          <h3>Motion</h3>
          <label><input type="checkbox" id="agentMotionEnabled"> Enabled</label>
          <label>Model
            <input id="agentMotionModel" type="text" placeholder="inherit or e.g. gpt-4o-mini" style="width:100%;">
          </label>
          <div class="row">
            <label style="flex:1;">Provider
              <input id="agentMotionProvider" type="text" placeholder="optional" style="width:100%;">
            </label>
            <label style="flex:1;">Base URL
              <input id="agentMotionBaseUrl" type="text" placeholder="optional" style="width:100%;">
            </label>
          </div>
        </div>
        <div class="agent-role">
          <h3>Critic</h3>
          <label><input type="checkbox" id="agentCriticEnabled"> Enabled</label>
          <label>Model
            <input id="agentCriticModel" type="text" placeholder="inherit or e.g. gpt-4o-mini" style="width:100%;">
          </label>
          <div class="row">
            <label style="flex:1;">Provider
              <input id="agentCriticProvider" type="text" placeholder="optional" style="width:100%;">
            </label>
            <label style="flex:1;">Base URL
              <input id="agentCriticBaseUrl" type="text" placeholder="optional" style="width:100%;">
            </label>
          </div>
        </div>
      </div>

      <div class="card">
        <h2>🧠 System Prompt</h2>
        <textarea id="setSysPrompt" rows="9" style="width:100%; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px;" placeholder="Loading..."></textarea>
        <div class="row" style="margin-top:10px;">
          <button onclick="saveSystemPrompt(this)" title="Save a custom system prompt">Save custom</button>
          <button class="secondary" onclick="resetSystemPrompt(this)" title="Reset to the built-in default prompt">Reset to default</button>
        </div>
        <details style="margin-top:10px; font-size:12px; color:var(--text-secondary);">
          <summary style="cursor:pointer;">View default prompt</summary>
          <pre id="setSysDefault" class="sys-default"></pre>
        </details>
        <div style="font-size:12px; color:var(--muted); margin-top:8px;">Changes apply to new AI requests immediately.</div>
        <div class="settings-msg" id="sysPromptMsg"></div>
      </div>

      <div class="card">
        <h2>🔌 Controllers</h2>
        <div id="controllerRows"></div>
        <div class="row" style="margin-top:10px;">
          <button class="secondary" onclick="addControllerRow()" title="Add a controller row">+ Add controller</button>
          <button class="secondary" onclick="verifyControllers(this)" title="Ping every configured controller">Verify connections</button>
          <button onclick="saveControllers(this)" title="Save controllers and hot-reload the fleet">Save &amp; reload</button>
        </div>
        <div class="settings-msg" id="controllersMsg"></div>
      </div>

      <div class="card">
        <h2>🎙 Audio Input</h2>
        <label>Audio source
          <select id="setAudioSource" onchange="audioSourceChanged()">
            <option value="monitor">Monitor (system output bus — what the PC plays)</option>
            <option value="mic">Microphone / webcam</option>
            <option value="wled_mic">WLED controller mic (GPIO — listens over UDP)</option>
            <option value="custom">Custom device</option>
          </select>
        </label>
        <label id="micDeviceRow">Device name
          <input id="setMicDevice" type="text" placeholder="e.g. alsa_input.pci-0000_00_1f.3.analog-stereo" style="width:100%;">
        </label>
        <div class="row" style="margin-top:10px;">
          <button onclick="saveAudioSettings(this)" title="Save audio input settings">Save</button>
        </div>
        <div class="settings-msg" id="audioMsg"></div>
      </div>
__PLAYER_SETTINGS_CARD__
    </div>
  </div>
  </main>
  <div class="status-bar">
    <div class="indicator">
      <span id="connIndicator">🔴</span>
      <span id="connText">Disconnected</span>
    </div>
    <div class="state-summary" id="stateSummary">--</div>
    <div class="last-action" id="status">Ready</div>
  </div>
  __DESKTOP_DIALOGS__
  <script>
    __DESKTOP_SCRIPT__
    let audioContext, analyser, micStream, micSource, frequencyData, waveformData, rafId;
    let playerMediaSource = null;
    let musicKitReady = null;
    let applePairTimer = null;
    let appleUserToken = '';
    let lastPlayerStatus = null;
    let playerEnabled = true;
    let playerPollTimer = null;
    let audioReactiveRunning = false;
    let musicModeRunning = false;
    let controllerVizStatus = null, controllerVizAt = 0;
    let controllerLevelHistory = [], controllerLastSequence = null, controllerLastBeatCount = 0;
    let musicRecognitionTimer = null;
    let musicRecognitionBusy = false;
    let musicMetadataKey = '';
    let musicMetadataActive = false;
    let musicModeGeneration = 0;
    let moodRecorder = null;
    let moodRecorderInterval = null;
    let beatRequestInFlight = false;
    const MUSIC_RECOGNITION_INTERVAL_MS = 30000;
    let baseline = 18;
    let lastBeat = 0;
    let paletteIndex = 0;
    let effectIndex = 0;
    let peakLevel = 0;
    let lastWledBeatSent = 0;
    let ledCapabilities = null;  // from device info.leds to know rgbw, cct, lc etc for accurate preview
    const musicAnalysis = {
      energy: 0,
      rms: 0,
      bass: 0,
      mid: 0,
      treble: 0,
      beatConfidence: 0,
      beat: false,
      bpm: 0,
      drive: 0,
      waveform: [],
      beatTimes: []
    };
    const palette = [
      [255, 0, 0, 0],
      [255, 90, 0, 0],
      [255, 0, 180, 0],
      [0, 80, 255, 0],
      [0, 255, 120, 0],
      [255, 120, 0, 180],
      [0, 180, 255, 0],
      [255, 255, 120, 80]
    ];
    const beatEffects = __BEAT_EFFECTS__;

    function encodeWav(audioBuffer) {
      const numOfChan = audioBuffer.numberOfChannels;
      const length = audioBuffer.length * numOfChan * 2 + 44;
      const buffer = new ArrayBuffer(length);
      const view = new DataView(buffer);
      const channels = [];
      let sample = 0;
      let offset = 0;
      let pos = 0;

      function setUint16(data) { view.setUint16(pos, data, true); pos += 2; }
      function setUint32(data) { view.setUint32(pos, data, true); pos += 4; }

      setUint32(0x46464952); // 'RIFF'
      setUint32(length - 8);
      setUint32(0x45564157); // 'WAVE'
      setUint32(0x20746d66); // 'fmt '
      setUint32(16);
      setUint16(1);
      setUint16(numOfChan);
      setUint32(audioBuffer.sampleRate);
      setUint32(audioBuffer.sampleRate * 2 * numOfChan);
      setUint16(numOfChan * 2);
      setUint16(16);
      setUint32(0x61746164); // 'data'
      setUint32(length - pos - 4);

      for (let i = 0; i < audioBuffer.numberOfChannels; i++) {
        channels.push(audioBuffer.getChannelData(i));
      }

      while (pos < length) {
        for (let i = 0; i < numOfChan; i++) {
          sample = Math.max(-1, Math.min(1, channels[i][offset]));
          sample = sample < 0 ? sample * 0x8000 : sample * 0x7FFF;
          view.setInt16(pos, sample, true);
          pos += 2;
        }
        offset++;
      }
      return new Blob([view], { type: 'audio/wav' });
    }

    async function blobToBase64(blob) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => resolve(reader.result.split(',')[1]);
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
    }

    function switchTab(tab) {
      document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
      const content = document.getElementById('tab-' + tab);
      if (content) content.classList.add('active');
      // activate the corresponding button using data attribute
      const activeBtn = document.querySelector('.tab-btn[data-tab="' + tab + '"]');
      if (activeBtn) activeBtn.classList.add('active');
      if (tab === 'settings') loadSettings();
    }

    function addChatMessage(role, text) {
      const div = document.createElement('div');
      div.className = 'chat-msg ' + role;
      div.textContent = text;
      chatHistory.appendChild(div);
      chatHistory.scrollTop = chatHistory.scrollHeight;
      saveChatHistory();
    }

    function saveChatHistory() {
      const messages = [];
      chatHistory.querySelectorAll('.chat-msg').forEach(el => {
        const role = el.classList.contains('user') ? 'user' : el.classList.contains('ai') ? 'ai' : 'system';
        messages.push({ role, text: el.textContent });
      });
      sessionStorage.setItem('light_chat_history', JSON.stringify(messages));
    }

    function loadChatHistory() {
      try {
        const raw = sessionStorage.getItem('light_chat_history');
        if (!raw) return;
        const messages = JSON.parse(raw);
        chatHistory.innerHTML = '';
        for (const m of messages) {
          const div = document.createElement('div');
          div.className = 'chat-msg ' + m.role;
          div.textContent = m.text;
          chatHistory.appendChild(div);
        }
        chatHistory.scrollTop = chatHistory.scrollHeight;
      } catch (e) { /* ignore */ }
    }

    function clearChat() {
      chatHistory.innerHTML = '';
      sessionStorage.removeItem('light_chat_history');
    }

    function clearModelResponses() {
      const pane = document.getElementById('modelResponses');
      if (pane) pane.textContent = '';
      try { localStorage.removeItem('light_model_responses'); } catch (_) { /* storage is optional */ }
    }

    function savedModelResponses() {
      try {
        const value = JSON.parse(localStorage.getItem('light_model_responses') || '[]');
        return Array.isArray(value) ? value.slice(-50) : [];
      } catch (_) { return []; }
    }

    function appendModelResponse(kind, text, persist = true) {
      const pane = document.getElementById('modelResponses');
      if (!pane || !text) return;
      const entry = {kind: String(kind || 'ai'), text: String(text), at: new Date().toISOString()};
      const wrapper = document.createElement('div');
      wrapper.className = 'resp ' + entry.kind;
      const meta = document.createElement('span');
      meta.className = 'resp-meta';
      meta.textContent = entry.kind === 'decision' ? 'AUTOMATED DECISION' : entry.kind.toUpperCase();
      const content = document.createElement('div');
      content.textContent = text;
      wrapper.appendChild(meta);
      wrapper.appendChild(content);
      pane.appendChild(wrapper);
      while (pane.children.length > 50) pane.removeChild(pane.firstElementChild);
      pane.scrollTop = pane.scrollHeight;
      if (persist) {
        const entries = savedModelResponses();
        entries.push(entry);
        try { localStorage.setItem('light_model_responses', JSON.stringify(entries.slice(-50))); }
        catch (_) { /* keep the visible response when storage is unavailable */ }
      }
    }

    function loadModelResponses() {
      const entries = savedModelResponses();
      for (const entry of entries) appendModelResponse(entry.kind, entry.text, false);
      const lastSummary = entries.filter(entry => entry.kind === 'decision' || entry.kind === 'recipe').at(-1);
      if (lastSummary && typeof smartDecisionSummary === 'function') {
        smartDecisionSummary.lastKey = lastSummary.kind + '|' + lastSummary.text;
      }
    }

    function showScrollingModelResponse(text) {
      appendModelResponse('ai', text);
    }

    let currentTarget = 'all';
    let fleetInfo = {fleet: false, targets: ['all'], channels: {}};

    function setTarget(value) {
      currentTarget = value || 'all';
    }

    function applyFleetInfo(data) {
      if (!data) return;
      if (Array.isArray(data.targets) && data.targets.length) fleetInfo.targets = data.targets;
      if (data.channels && typeof data.channels === 'object') fleetInfo.channels = data.channels;
      fleetInfo.fleet = !!data.fleet;
      const sel = document.getElementById('targetSelect');
      if (!sel) return;
      if (fleetInfo.targets.length <= 1) {
        // Fleet shrank to a single controller: drop stale controller options.
        const current = Array.from(sel.options).map(o => o.value).join('|');
        if (current !== 'all') {
          sel.innerHTML = '';
          const opt = document.createElement('option');
          opt.value = 'all';
          opt.textContent = 'All controllers';
          sel.appendChild(opt);
        }
        sel.value = 'all';
        currentTarget = 'all';
        return;
      }
      const wanted = Array.from(sel.options).map(o => o.value).join('|');
      const offered = fleetInfo.targets.join('|');
      if (wanted === offered) return;  // already populated
      const channels = fleetInfo.channels || {};
      sel.innerHTML = '';
      for (const name of fleetInfo.targets) {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name === 'all' ? 'All 4 strips'
          : name === 'outer' ? 'Group: outer (far pair)'
          : (name === 'inner' || name === 'center') ? 'Group: inner (middle pair)'
          : (name in channels ? 'Strip: ' + name : 'Controller: ' + name);
        sel.appendChild(opt);
      }
      sel.value = fleetInfo.targets.includes(currentTarget) ? currentTarget : 'all';
      currentTarget = sel.value;
    }

    // Resolve the state blob to display: raw single-controller state, or the
    // controller matching the current target (first controller otherwise).
    // Channel targets also carry the segment id to preview (mapping[1]).
    function displayState(st) {
      if (!st || typeof st !== 'object') return {state: {}, name: '', segId: 0};
      if ('seg' in st || 'on' in st) return {state: st, name: '', segId: 0};
      const names = Object.keys(st);
      if (!names.length) return {state: {}, name: '', segId: 0};
      let name = names[0];
      let segId = 0;
      const mapping = fleetInfo.channels || {};
      if (currentTarget in mapping && names.includes(mapping[currentTarget][0])) {
        name = mapping[currentTarget][0];
        segId = Number(mapping[currentTarget][1]) || 0;
      } else if (names.includes(currentTarget)) {
        name = currentTarget;
      }
      return {state: st[name] || {}, name, segId};
    }

    function fleetConnectionStatus(st) {
      if (!st || typeof st !== 'object') return {connected: [], total: 0, fleet: false};
      if ('seg' in st || 'on' in st) {
        return {connected: st.error ? [] : ['controller'], total: 1, fleet: false};
      }
      const names = Object.keys(st);
      const connected = names.filter(function (name) {
        const value = st[name];
        return value && typeof value === 'object' && !value.error;
      });
      return {connected, total: names.length, fleet: true};
    }

    function updateConnectionStatusFromState(st) {
      const health = fleetConnectionStatus(st);
      if (health.connected.length) {
        connIndicator.textContent = health.connected.length === health.total ? '🟢' : '🟡';
        connText.textContent = health.fleet
          ? `Connected: ${health.connected.join(', ')}`
          : 'Connected';
      } else {
        connIndicator.textContent = '🔴';
        connText.textContent = health.fleet ? 'Controllers disconnected' : 'Disconnected';
      }
      return health;
    }

    // --- FireTV companion visuals ------------------------------------------
    function firetvMsg(text, kind) {
      const el = document.getElementById('firetvMsg');
      if (!el) return;
      el.textContent = text || '';
      el.style.color = kind === 'err' ? 'var(--danger)' : (kind === 'ok' ? 'var(--success)' : '');
    }

    async function loadFiretvState() {
      try {
        const data = await fetchJsonWithTimeout('/api/firetv', {}, 5000);
        const cb = document.getElementById('firetvEnabled');
        if (cb && typeof data.enabled === 'boolean') cb.checked = data.enabled;
      } catch (err) {
        console.warn('FireTV state unavailable:', err);
      }
    }

    async function toggleFiretv(enabled) {
      const cb = document.getElementById('firetvEnabled');
      firetvMsg('Saving...', '');
      try {
        const data = await postJson('/api/firetv', {enabled: !!enabled});
        if (data.ok === false) throw new Error(data.error || 'Save failed.');
        firetvMsg(enabled ? 'TV visuals enabled' : 'TV visuals disabled', 'ok');
      } catch (err) {
        if (cb) cb.checked = !enabled;  // revert on error
        firetvMsg(err.message || 'Error saving FireTV setting.', 'err');
      }
    }

    async function openFiretvVisuals() {
      firetvMsg('Opening visuals on TV...', '');
      try {
        const url = location.protocol + '//' + location.host + '/tv';
        const data = await postJson('/api/firetv', {action: 'open_url', url: url});
        if (data.ok === false) throw new Error(data.error || 'Failed to open TV.');
      } catch (err) {
        firetvMsg(err.message || 'Error reaching FireTV.', 'err');
      }
    }

    // --- Read-only TV observation: separate from TV control -----------------
    let tvObservationBusy = false;

    function setTvObservationBusy(busy) {
      tvObservationBusy = busy;
      for (const id of ['tvObservationEnabled', 'tvObservationRefresh']) {
        const el = document.getElementById(id);
        if (el) el.disabled = busy;
      }
    }

    function showTvObservation(data) {
      if (!data || data.ok === false) throw new Error((data && data.error) || 'TV observation unavailable.');
      const cb = document.getElementById('tvObservationEnabled');
      if (cb) cb.checked = data.observation_enabled === true;
      let message;
      if (!data.observation_enabled) message = 'Observation off. TV control is unchanged.';
      else if (!data.connected) message = 'TV unavailable. Activity is unknown; no lighting changes.';
      else if (data.awake === false) message = 'TV asleep. No lighting changes.';
      else {
        const label = data.awake === null ? 'Power state unknown' :
          data.activity_hint === 'music' ? 'Music app playing' :
          data.activity_hint === 'tv' ? 'Video app open' :
          data.activity_hint === 'idle' ? 'Playback idle' : 'Content type ambiguous';
        const media = data.media_session;
        message = label + (data.foreground_app ? ' — ' + data.foreground_app : '') +
          (media && media.description ? ' — ' + media.description : '') +
          (data.media_error ? ' — Playback metadata unavailable.' : '');
      }
      setText('tvObservationStatus', message);
    }

    async function refreshTvObservation() {
      if (tvObservationBusy) return;
      setTvObservationBusy(true);
      setText('tvObservationStatus', 'Reading TV context...');
      try {
        showTvObservation(await fetchJsonWithTimeout('/api/tv-observation', {}, 20000));
      } catch (err) {
        setText('tvObservationStatus', 'Observation unavailable: ' + err.message);
      } finally {
        setTvObservationBusy(false);
      }
    }

    async function toggleTvObservation(enabled) {
      if (tvObservationBusy) return;
      const cb = document.getElementById('tvObservationEnabled');
      setTvObservationBusy(true);
      let saved = false;
      try {
        const data = await postJson('/api/tv-observation', {enabled: !!enabled});
        if (!data || data.ok === false) throw new Error((data && data.error) || 'Save failed.');
        if (cb) cb.checked = data.observation_enabled === true;
        saved = true;
      } catch (err) {
        if (cb) cb.checked = !enabled;
        setText('tvObservationStatus', 'Observation setting not saved: ' + err.message);
      } finally {
        setTvObservationBusy(false);
      }
      if (saved) await refreshTvObservation();
    }

    // --- Smart Director: one output owner for TV and controller mic ---------
    let smartDirectorDirty = false;
    let smartDirectorLoaded = false;
    let smartDirectorBusy = false;
    let smartDirectorWriting = false;
    let smartDirectorRevision = 0;
    let smartDirectorPriorityWrites = 0;
    let liveShowPending = {};
    let liveShowTimer = null;

    function queueLiveShowControl(key, value) {
      // Shortcuts and sliders share the same form state. A later TV Apply must
      // not undo a live shortcut using stale, unsaved music control values.
      const fields = {
        music_brightness: ['smartDirectorMusicBrightness', 'smartDirectorMusicBrightnessText'],
        music_speed: ['smartDirectorSpeed', 'smartDirectorSpeedText'],
        music_intensity: ['smartDirectorIntensity', 'smartDirectorIntensityText'],
        music_colorfulness: ['smartDirectorColorfulness', 'smartDirectorColorfulnessText']
      };
      if (key === 'music_motion') {
        const element = document.getElementById('smartDirectorMotion');
        if (element) element.value = value;
      } else if (fields[key]) {
        const [id, textId] = fields[key];
        const element = document.getElementById(id);
        if (element) element.value = String(Math.round(Number(value) * 100));
        setText(textId, key === 'music_speed' ? Number(value).toFixed(2) + '×' : String(Math.round(Number(value) * 100)));
      }
      liveShowPending[key] = value;
      clearTimeout(liveShowTimer);
      setText('liveShowMessage', 'Updating live show…');
      liveShowTimer = setTimeout(flushLiveShowControls, 250);
    }

    async function flushLiveShowControls() {
      if (smartDirectorPriorityWrites || !Object.keys(liveShowPending).length) return;
      if (smartDirectorWriting) {
        liveShowTimer = setTimeout(flushLiveShowControls, 150);
        return;
      }
      const updates = {...liveShowPending};
      smartDirectorWriting = true;
      smartDirectorRevision++;
      let succeeded = false;
      try {
        const data = await postJson('/api/smart-director', updates);
        if (!data || data.ok === false) throw new Error(data?.error || 'Update failed.');
        for (const [key, value] of Object.entries(updates)) {
          if (liveShowPending[key] === value) delete liveShowPending[key];
        }
        renderSmartDirectorResponse(data, false);
        succeeded = true;
        const running = data.status?.mode === 'music' && data.status?.renderer?.running;
        setText('liveShowMessage', running ? 'Live show updated · music keeps running' : 'Saved for the next music show');
      } catch (err) {
        setText('liveShowMessage', 'Live update failed: ' + err.message + ' · adjust a control to retry');
      } finally {
        smartDirectorWriting = false;
        if (succeeded && Object.keys(liveShowPending).length) {
          liveShowTimer = setTimeout(flushLiveShowControls, 150);
        }
      }
    }

    function markSmartDirectorDirty() {
      smartDirectorDirty = true;
      setText('smartDirectorMessage', 'Unsaved changes');
    }

    function smartDirectorFraction(id) {
      const value = Number((document.getElementById(id) || {}).value);
      return Math.max(0, Math.min(1, (Number.isFinite(value) ? value : 0) / 100));
    }

    function readSmartDirectorForm() {
      return {
        enabled: !!document.getElementById('smartDirectorEnabled').checked,
        mode: document.getElementById('smartDirectorMode').value,
        tv_theme: document.getElementById('smartDirectorTheme').value,
        music_brightness: smartDirectorFraction('smartDirectorMusicBrightness'),
        music_motion: document.getElementById('smartDirectorMotion').value,
        music_speed: Number(document.getElementById('smartDirectorSpeed').value) / 100,
        music_intensity: smartDirectorFraction('smartDirectorIntensity'),
        music_colorfulness: smartDirectorFraction('smartDirectorColorfulness'),
        day_brightness: smartDirectorFraction('smartDirectorDayBrightness'),
        evening_brightness: smartDirectorFraction('smartDirectorEveningBrightness'),
        night_brightness: smartDirectorFraction('smartDirectorNightBrightness')
      };
    }

    function populateSmartDirectorSettings(settings) {
      if (!settings) return;
      const setValue = (id, value) => {
        const element = document.getElementById(id);
        if (element && value !== undefined && value !== null) element.value = String(value);
      };
      const setPercent = (id, textId, fraction, cap = 100) => {
        const percent = Math.max(0, Math.min(cap, Math.round(Number(fraction || 0) * 100)));
        setValue(id, percent);
        setText(textId, String(percent));
      };
      const enabled = document.getElementById('smartDirectorEnabled');
      if (enabled) enabled.checked = settings.enabled === true;
      setValue('smartDirectorMode', settings.mode || 'auto');
      setValue('smartDirectorTheme', settings.tv_theme || 'warm');
      if (!('music_brightness' in liveShowPending)) setPercent('smartDirectorMusicBrightness', 'smartDirectorMusicBrightnessText', settings.music_brightness, 100);
      if (!('music_motion' in liveShowPending)) setValue('smartDirectorMotion', settings.music_motion || 'auto');
      if (!('music_speed' in liveShowPending)) {
        const speed = Number(settings.music_speed ?? 1);
        setValue('smartDirectorSpeed', Math.round(speed * 100));
        setText('smartDirectorSpeedText', speed.toFixed(2) + '×');
      }
      if (!('music_intensity' in liveShowPending)) setPercent('smartDirectorIntensity', 'smartDirectorIntensityText', settings.music_intensity ?? .85);
      if (!('music_colorfulness' in liveShowPending)) setPercent('smartDirectorColorfulness', 'smartDirectorColorfulnessText', settings.music_colorfulness ?? .9);
      setPercent('smartDirectorDayBrightness', 'smartDirectorDayBrightnessText', settings.day_brightness);
      setPercent('smartDirectorEveningBrightness', 'smartDirectorEveningBrightnessText', settings.evening_brightness);
      setPercent('smartDirectorNightBrightness', 'smartDirectorNightBrightnessText', settings.night_brightness);
      if (!bandGainWriting && !bandGainWritePending && !bandGainDirty && Array.isArray(settings.band_gains) && settings.band_gains.length === 16) {
        document.querySelectorAll('.band-gain').forEach((input, index) => input.value = String(settings.band_gains[index]));
      }
    }

    function readBandGains() {
      return Array.from(document.querySelectorAll('.band-gain')).map(input => {
        const value = Number(input.value);
        const gain = Number.isFinite(value) ? Math.max(0, Math.min(3, value)) : 1;
        input.value = String(gain);
        return gain;
      });
    }

    let bandGainWriteQueue = [], bandGainWritePending = 0, bandGainWriting = false, bandGainDirty = false;

    async function flushBandGainWrites() {
      if (bandGainWriting) return;
      bandGainWriting = true;
      while (bandGainWriteQueue.length) {
        const entry = bandGainWriteQueue.shift();
        smartDirectorRevision++;
        setText('liveShowMessage', 'Updating 16-band response…');
        try {
          const data = await postJson('/api/music-show', {action:'tune',band_gains:entry.gains});
          if (!data || data.ok === false) throw new Error(data?.error || 'Band tuning failed.');
          if (data.status) renderSmartDirectorStatus(data.status);
          bandGainDirty = bandGainWriteQueue.length > 0;
          setText('liveShowMessage', bandGainDirty ? 'Applying latest band gains…' : 'Band gains updated live');
          entry.resolve(data);
        } catch (err) {
          bandGainDirty = true;
          setText('liveShowMessage', 'Band tuning failed: ' + err.message + ' · change a gain to retry');
          entry.resolve({ok:false,error:err.message});
        } finally { bandGainWritePending--; }
      }
      bandGainWriting = false;
    }

    function tuneBandGains() {
      const gains = readBandGains();
      bandGainDirty = true;
      bandGainWritePending++;
      const completion = new Promise(resolve => bandGainWriteQueue.push({gains, resolve}));
      flushBandGainWrites();
      return completion;
    }

    async function resetBandGains() {
      document.querySelectorAll('.band-gain').forEach(input => input.value = '1');
      await tuneBandGains();
    }

    function controllerVizActive() {
      return !!(controllerVizStatus && controllerVizStatus.running &&
        controllerVizStatus.selected_mode !== 'manual' && performance.now() - controllerVizAt < 2500);
    }

    function controllerPreviewColors() {
      if (!controllerVizActive() || !controllerVizStatus.renderer?.running) return [];
      const preview = controllerVizStatus.renderer.preview || [];
      return preview.filter(item => currentTarget === 'all' || item.channel === currentTarget ||
        item.controller === currentTarget ||
        (['inner','center'].includes(currentTarget) && item.channel.startsWith('middle-')) ||
        (currentTarget === 'outer' && item.channel.startsWith('far-'))).flatMap(item => item.colors || []);
    }

    function expireControllerVisualization() {
      if (!controllerVizStatus || controllerVizActive() || audioReactiveRunning) return;
      const wasRunning = controllerVizStatus.running;
      const manual = controllerVizStatus.selected_mode === 'manual';
      controllerVizStatus = null;
      controllerLevelHistory = []; controllerLastSequence = null; controllerLastBeatCount = 0;
      resetAnalysisDisplay(); setMusicModeUi(false);
      setText('vizStatus', manual ? 'Manual lighting' : wasRunning ? 'Controller feed unavailable' : 'idle');
      setText('waveformLabel', 'WAVEFORM + BEAT');
      setText('musicModeBtn', 'Start Smart Music');
      updateMusicIdentity(null);
    }

    function parseMediaIdentity(tv) {
      const media = tv && tv.media_session;
      if (!media || tv.connected === false || tv.awake === false || media.active !== true || Number(media.state) !== 3 ||
          !tv.foreground_app || media.package !== tv.foreground_app) return null;
      const parts = String(media.description || '').split(',').map(value => value.trim());
      const clean = value => value && value.toLowerCase() !== 'null' ? value : '';
      const album = clean(parts.pop());
      const artist = clean(parts.pop());
      const title = clean(parts.join(', '));
      if (!title) return null;
      return {title, artist, album, source: 'TV · ' + tv.foreground_app};
    }

    function updateMusicIdentity(tv) {
      const local = lastPlayerStatus && lastPlayerStatus.playing && (lastPlayerStatus.title || lastPlayerStatus.artist)
        ? {title:lastPlayerStatus.title || 'Unknown', artist:lastPlayerStatus.artist || '', source:'Local player · ' + currentPlayerSource()}
        : null;
      const identity = local || parseMediaIdentity(tv);
      setText('musicIdentityTitle', identity ? identity.title : 'Nothing playing');
      setText('musicIdentityArtist', identity ? identity.artist : '');
      setText('musicIdentitySource', identity ? identity.source : 'No active source');
    }

    function renderControllerVisualization(status) {
      controllerVizStatus = status; controllerVizAt = performance.now();
      updateMusicIdentity(status.tv || null);
      if (!controllerVizActive()) { expireControllerVisualization(); return; }
      if (audioReactiveRunning) return;
      const audio = status.audio || {}, renderer = status.renderer || {};
      const unit = value => Math.max(0, Math.min(1, Number(value) || 0));
      const active = audio.active === true;
      const fft = Array.from({length:16}, (_, i) => active ? unit((audio.fft || [])[i]) : 0);
      const level = active ? unit(audio.level) : 0;
      const sequence = audio.receive_sequence;
      const fresh = sequence !== controllerLastSequence;
      if (fresh || !active) {
        controllerLastSequence = sequence;
        controllerLevelHistory.push(level);
        if (controllerLevelHistory.length > 100) controllerLevelHistory.shift();
      }
      const beats = Number(renderer.beat_count) || 0;
      const beat = active && ((fresh && !!audio.beat) || beats > controllerLastBeatCount);
      controllerLastBeatCount = beats;
      const band = (start, end) => fft.slice(start,end).reduce((sum,n) => sum+n,0)/(end-start);
      Object.assign(musicAnalysis, {source:'controller', energy:level, rms:level,
        bass:band(0,4), mid:band(4,10), treble:band(10,16), drive:level,
        beatConfidence:active ? unit(renderer.bpm_confidence) : 0, beat,
        bpm:active ? Math.round(Number(renderer.bpm) || 0) : 0,
        spectrum:fft.map(value => Math.round(value*255)), levelHistory:controllerLevelHistory.slice(), waveform:[]});
      renderAnalyzerFrame(musicAnalysis);
      const playing = status.mode === 'music' && !!renderer.running;
      setMusicModeUi(playing, 'TV metadata');
      if (status.mode === 'tv') setText('musicModeState', 'Steady TV');
      setText('musicModeBtn', playing ? 'Controller Music Running' : 'Start Smart Music');
      setText('micPipelineState', active ? 'Controller mic' : 'Controller mic unavailable');
      setText('songSourceState', 'TV metadata');
      setText('nextMatchState', status.selected_mode === 'auto' ? 'Automatic TV / music' : 'Manual mode selection');
      setText('autoStatus', status.reason || 'Controller audio connected');
      setText('vizStatus', active ? `Controller mic · ${playing ? 'Music' : 'Steady TV'}` : 'Controller mic unavailable');
      setText('waveformLabel', 'CONTROLLER LEVEL HISTORY + BEAT');
    }

    function populateEffectiveShowStatus(status) {
      const renderer = status.renderer || {};
      if (!renderer.running) return;
      const overrides = status.show_overrides || {};
      if (!bandGainWriting && !bandGainWritePending && !bandGainDirty && Array.isArray(overrides.band_gains) && overrides.band_gains.length === 16 && document.querySelectorAll) {
        document.querySelectorAll('.band-gain').forEach((input, index) => {
          if (document.activeElement !== input) input.value = String(overrides.band_gains[index]);
        });
      }
      const effective = [
        Number.isFinite(Number(status.brightness_percent)) ? Math.round(Number(status.brightness_percent)) + '%' : '',
        renderer.motion || '', Number.isFinite(Number(renderer.speed)) ? Number(renderer.speed).toFixed(2) + '×' : '',
        Number.isFinite(Number(renderer.intensity)) ? 'intensity ' + Math.round(Number(renderer.intensity) * 100) + '%' : '',
        Object.keys(overrides).some(key => key !== 'band_gains') ? 'AI/session override active' : ''
      ].filter(Boolean).join(' · ');
      setText('smartDirectorEffectiveShow', effective || 'renderer active');
    }

    function smartDecisionSummary(status) {
      const error = status.last_error || status.startup_error || status.renderer?.last_error;
      if (error) return {kind:'error', text:'Smart lighting error: ' + error};
      const intelligence = status.intelligence || {};
      if (status.mode === 'music' && intelligence.kind === 'music') {
        const media = status.tv?.media_session?.description;
        const recipe = [intelligence.composition_mode,
          Array.isArray(intelligence.colors) ? intelligence.colors.length + ' colors' : '',
          intelligence.show?.motion].filter(Boolean).join(' · ');
        return {kind:'recipe', text:['Automated recipe', media, recipe,
          intelligence.reason || status.reason].filter(Boolean).join(' · ')};
      }
      if (status.selected_mode !== 'auto') return null;
      const mode = String(status.mode || 'waiting');
      const label = mode.charAt(0).toUpperCase() + mode.slice(1);
      const reason = status.reason || status.intelligence?.reason || 'Waiting for a confident signal.';
      return {kind:'decision', text:'Auto → ' + label + ' · ' + reason};
    }

    function renderSmartDirectorStatus(status) {
      status = status || {};
      populateEffectiveShowStatus(status);
      const audio = status.audio || {};
      const renderer = status.renderer || {};
      const intelligence = status.intelligence || {};
      const level = Number(audio.level);
      const mic = audio.active ? `Active${Number.isFinite(level) ? ' · ' + Math.round(level * 100) + '%' : ''}` : 'Idle';
      const frames = Number(renderer.sent_frames);
      const beats = Number(renderer.beat_count);
      const bpm = Number(renderer.bpm);
      const renderProof = [
        renderer.motion ? renderer.motion + ' · ' + Number(renderer.speed || 1).toFixed(2) + '×' : '',
        Number.isFinite(frames) ? frames + ' frames' : '',
        Number.isFinite(beats) ? beats + ' beats' : '',
        Number.isFinite(bpm) && bpm > 0 ? Math.round(bpm) + ' BPM' : ''
      ].filter(Boolean).join(' · ');
      const renderState = renderer.running ? `Running${renderProof ? ' · ' + renderProof : ''}` : 'Idle';
      let decision = status.reason || intelligence.reason || intelligence.kind || status.mode || 'Waiting';
      if (status.last_error || status.startup_error || renderer.last_error) decision = `Error: ${status.last_error || status.startup_error || renderer.last_error}`;
      setText('smartDirectorMicStatus', mic);
      setText('smartDirectorRendererStatus', renderState);
      setText('smartDirectorDecisionStatus', decision);
      const summary = smartDecisionSummary(status);
      if (summary) {
        const key = summary.kind + '|' + summary.text;
        if (smartDecisionSummary.lastKey !== key) appendModelResponse(summary.kind, summary.text);
        smartDecisionSummary.lastKey = key;
      }
      renderControllerVisualization(status);
    }

    function renderSmartDirectorResponse(data, allowSettings) {
      if (!data || data.ok === false) throw new Error((data && data.error) || 'Smart lighting unavailable.');
      if (allowSettings && !smartDirectorDirty) populateSmartDirectorSettings(data.settings || {});
      renderSmartDirectorStatus(data.status || {});
      const status = data.status || {};
      if (!smartDirectorDirty) setText('smartDirectorMessage', status.running ? `Active · ${status.mode || 'starting'}` : 'Not running');
    }

    async function pollSmartDirector() {
      if (document.hidden) return;
      if (!controllerVizStatus?.running && performance.now() - controllerVizAt < 2000) return;
      if (smartDirectorBusy) return;
      if (smartDirectorWriting) return;
      if (bandGainWriting || bandGainWritePending) return;
      const revision = smartDirectorRevision;
      smartDirectorBusy = true;
      try {
        const data = await fetchJsonWithTimeout('/api/smart-director', {}, 4000);
        if (revision === smartDirectorRevision && !smartDirectorWriting) renderSmartDirectorResponse(data, true);
      } catch (err) {
        setText('smartDirectorMessage', 'Status unavailable: ' + err.message);
      } finally {
        smartDirectorBusy = false;
      }
    }

    async function loadSmartDirector() {
      if (smartDirectorBusy) return;
      if (smartDirectorWriting) return;
      const revision = smartDirectorRevision;
      smartDirectorBusy = true;
      try {
        const data = await fetchJsonWithTimeout('/api/smart-director', {}, 4000);
        if (revision === smartDirectorRevision && !smartDirectorWriting) renderSmartDirectorResponse(data, !smartDirectorLoaded);
        smartDirectorLoaded = true;
      } catch (err) {
        setText('smartDirectorMessage', 'Smart lighting unavailable: ' + err.message);
      } finally {
        smartDirectorBusy = false;
      }
    }

    async function applySmartDirectorSettings(button) {
      smartDirectorPriorityWrites++;
      if (button) button.disabled = true;
      while (smartDirectorWriting) await new Promise(resolve => setTimeout(resolve, 25));
      smartDirectorWriting = true;
      smartDirectorRevision++;
      setText('smartDirectorMessage', 'Applying...');
      try {
        const data = await postJson('/api/smart-director', readSmartDirectorForm());
        if (!data || data.ok === false) throw new Error((data && data.error) || 'Apply failed.');
        smartDirectorDirty = false;
        renderSmartDirectorResponse(data, true);
      } catch (err) {
        setText('smartDirectorMessage', 'Apply failed: ' + err.message);
      } finally {
        smartDirectorWriting = false;
        smartDirectorPriorityWrites--;
        if (button) button.disabled = false;
        if (Object.keys(liveShowPending).length) liveShowTimer = setTimeout(flushLiveShowControls, 150);
      }
    }

    async function setSmartDirectorMode(updates) {
      // Explicit Stop/Mode changes must not be dropped behind a slider POST.
      smartDirectorPriorityWrites++;
      clearTimeout(liveShowTimer);
      liveShowPending = {};
      while (smartDirectorWriting) await new Promise(resolve => setTimeout(resolve, 25));
      smartDirectorWriting = true;
      smartDirectorRevision++;
      try {
        const data = await postJson('/api/smart-director', updates);
        if (!data || data.ok === false) throw new Error((data && data.error) || 'Smart lighting update failed.');
        smartDirectorDirty = false;
        renderSmartDirectorResponse(data, true);
        return data;
      } finally {
        smartDirectorWriting = false;
        smartDirectorPriorityWrites--;
        if (Object.keys(liveShowPending).length) liveShowTimer = setTimeout(flushLiveShowControls, 150);
      }
    }

    async function stopSmartMusicMode() {
      try {
        await setSmartDirectorMode({enabled: false});
        stopMusicMode();
      } catch (err) {
        setText('autoStatus', 'Could not stop smart music: ' + err.message);
      }
    }

    // --- Music Director (mood-matching music mode) ---------------------------
    let musicDirectorPollId = null;

    function musicDirectorMsg(text, kind) {
      const el = document.getElementById('musicDirectorMsg');
      if (!el) return;
      el.textContent = text || '';
      el.style.color = kind === 'err' ? 'var(--danger)' : (kind === 'ok' ? 'var(--success)' : '');
    }

    function musicDirectorTrack(text) {
      const el = document.getElementById('musicDirectorTrack');
      if (el) el.textContent = text || '';
    }

    async function pollMusicDirector() {
      try {
        const data = await fetchJsonWithTimeout('/api/music-director', {}, 5000);
        const track = data && data.status && data.status.track;
        musicDirectorTrack(track ? '♪ ' + track : '');
      } catch (err) {
        console.warn('Music Director poll failed:', err);
      }
    }

    function setMusicDirectorPolling(enabled) {
      if (musicDirectorPollId) {
        clearInterval(musicDirectorPollId);
        musicDirectorPollId = null;
      }
      if (enabled) {
        pollMusicDirector();
        musicDirectorPollId = setInterval(pollMusicDirector, 10000);
      } else {
        musicDirectorTrack('');
      }
    }

    async function loadMusicDirectorState() {
      try {
        const data = await fetchJsonWithTimeout('/api/music-director', {}, 5000);
        const cb = document.getElementById('musicDirectorEnabled');
        const enabled = !!(data && data.enabled);
        if (cb) cb.checked = enabled;
        setMusicDirectorPolling(enabled);
      } catch (err) {
        console.warn('Music Director state unavailable:', err);
      }
    }

    async function toggleMusicDirector(enabled) {
      const cb = document.getElementById('musicDirectorEnabled');
      musicDirectorMsg('Saving...', '');
      try {
        const data = await postJson('/api/music-director', {enabled: !!enabled});
        if (data.ok === false) throw new Error(data.error || 'Save failed.');
        musicDirectorMsg(enabled ? 'Music mode on' : 'Music mode off', 'ok');
        setMusicDirectorPolling(enabled);
      } catch (err) {
        if (cb) cb.checked = !enabled;  // revert on error
        musicDirectorMsg(err.message || 'Error saving Music setting.', 'err');
      }
    }

    async function wallMode(mode) {
      await send(mode, {fx: Number(wallFx.value), pal: Number(wallPal.value), speed: Number(wallSpeed.value)});
    }

    async function wallVersus() {
      await send('wall_versus', {
        fx_left: Number(wallFxL.value),
        fx_right: Number(wallFxR.value),
        pal: Number(wallPal.value),
        speed: Number(wallSpeed.value)
      });
    }

    async function applyWallChannel() {
      await send('set_channel', {
        channel: wallChannel.value,
        fx: Number(wallFx.value),
        pal: Number(wallPal.value),
        speed: Number(wallSpeed.value)
      });
    }

    async function postAction(action, values = {}, timeoutMs = 4000) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const res = await fetch('/api/action', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({action, target: currentTarget, ...values}),
          signal: controller.signal
        });
        return await res.json();
      } finally {
        clearTimeout(timeout);
      }
    }

    async function fetchJsonWithTimeout(url, options = {}, timeoutMs = 15000) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const res = await fetch(url, {...options, signal: controller.signal});
        return await res.json();
      } finally {
        clearTimeout(timeout);
      }
    }

    async function send(action, values = {}) {
      // Explicit controller actions take ownership from the continuous Music
      // Mode beat loop, otherwise the next beat immediately overwrites them.
      if (musicModeRunning) stopMusicMode();
      status.textContent = 'Sending...';
      // Optimistic immediate update to LED preview for accurate live colors + motion feel
      try { optimisticPreviewFromAction(action, values); } catch(e) {}
      try {
        const data = await postAction(action, values);
        status.textContent = data.ok ? data.message : data.error;
        return data;
      } catch (err) {
        status.textContent = 'Error: ' + err.message;
        return {ok: false, error: err.message};
      }
    }

    async function sendBeatUpdate(values) {
      if (beatRequestInFlight) return;
      beatRequestInFlight = true;
      try {
        optimisticPreviewFromAction('beat', values);
        const data = await postAction('beat', values, 900);
        if (!data.ok) console.warn('Beat update rejected:', data.error);
      } catch (err) {
        console.warn('Beat update skipped:', err.message);
      } finally {
        beatRequestInFlight = false;
      }
    }

    function parseTagList(value) {
      const tags = [];
      for (const part of String(value || '').split(/[\\n,]+/)) {
        for (const tag of part.trim().split(/\\s+/)) {
          const clean = tag.trim();
          if (clean && !tags.includes(clean)) tags.push(clean);
        }
      }
      return tags;
    }

    function parseColorList(value) {
      return String(value || '').split(/[,;|]+/).map((part) => part.trim()).filter(Boolean);
    }

    function currentPlayerSource() {
      return (document.getElementById('playerSource') || {}).value || 'youtube_music';
    }

    function applyPlayerNowPlaying(data) {
      lastPlayerStatus = data || lastPlayerStatus;
      updateMusicIdentity(controllerVizStatus?.tv || null);
      const titleEl = document.getElementById('musicTitle');
      const artistEl = document.getElementById('musicArtist');
      const art = document.getElementById('albumArt');
      const wrap = document.getElementById('playerArtWrap');
      const playBtn = document.getElementById('playerPlayBtn');
      if (data && (data.title || data.artist)) {
        if (titleEl) titleEl.textContent = data.title || 'Unknown';
        if (artistEl) artistEl.textContent = data.artist || '';
      }
      if (art && data && data.artwork) {
        art.src = data.artwork;
        art.classList.add('visible');
        if (wrap) wrap.classList.add('has-art');
      }
      if (playBtn && data) playBtn.textContent = data.playing ? '❚❚' : '▶';
      const seek = document.getElementById('playerSeek');
      if (seek && data) {
        const duration = Number(data.duration || data.duration_s || 0);
        seek.disabled = !Number.isFinite(duration) || duration <= 0;
        seek.max = seek.disabled ? 0 : duration;
        if (document.activeElement !== seek) seek.value = Number(data.position || data.position_s || 0);
      }
      if (wrap && data && !data.artwork) wrap.classList.remove('has-art');
    }

    async function refreshPlayerStatus() {
      if (!playerEnabled) return null;
      const source = currentPlayerSource();
      try {
        const data = await fetchJsonWithTimeout('/api/player?source=' + encodeURIComponent(source), {}, 4000);
        const line = data.connected
          ? ((data.playing ? '▶ ' : '❚❚ ') + (data.title || 'Unknown') + (data.artist ? ' — ' + data.artist : ''))
          : (data.message || 'Disconnected');
        setText('playerNowPlaying', line);
        applyPlayerNowPlaying(data);
        if (!data.desktop) updateAppleAuthPanel(data);
        else hideApplePairing();
        maybeAdvanceLocalQueue(data);
        return data;
      } catch (err) {
        setText('playerNowPlaying', 'Player error: ' + err.message);
        lastPlayerStatus = null;
        updateMusicIdentity(controllerVizStatus?.tv || null);
        return null;
      }
    }

    async function playerCommand(command, extra) {
      if (localQueueRun && ['next','previous'].includes(command)) return playLocalQueue(Math.max(0,localQueueRun.index + (command === 'next' ? 1 : -1)));
      const source = currentPlayerSource();
      try {
        if (source === 'apple_music' && !lastPlayerStatus?.desktop && window.MusicKit && MusicKit.getInstance) {
          const handled = await handleAppleClientCommand(command, extra || {});
          if (handled) {
            await refreshPlayerStatus();
            return handled;
          }
        }
        const body = Object.assign({source, command}, extra || {});
        const data = await postJson('/api/player', body);
        if (data && data.client_play) await handleAppleClientCommand('playItem', data.client_play);
        setText('playerNowPlaying', data.message || (data.ok ? 'Command sent.' : 'Command failed.'));
        if (data && data.ok && !data.queued && ['play','playPause','playItem'].includes(command) && !musicModeRunning) startMusicMode();
        await refreshPlayerStatus();
        return data;
      } catch (err) {
        setText('playerNowPlaying', 'Player error: ' + err.message);
        return null;
      }
    }

    async function onPlayerSourceChange() {
      const source = currentPlayerSource();
      const status = await refreshPlayerStatus();
      if (source === 'apple_music' && !status?.desktop) await ensureApplePairing();
      else hideApplePairing();
      await loadPlayerPlaylists();
    }

    function hideApplePairing() {
      const panel = document.getElementById('appleAuthPanel');
      if (panel) panel.hidden = true;
      if (applePairTimer) { clearInterval(applePairTimer); applePairTimer = null; }
    }

    function updateAppleAuthPanel(data) {
      const source = currentPlayerSource();
      const panel = document.getElementById('appleAuthPanel');
      const signed = document.getElementById('appleSignedIn');
      const pairing = document.getElementById('applePairing');
      if (!panel) return;
      if (source !== 'apple_music') {
        panel.hidden = true;
        return;
      }
      panel.hidden = false;
      const authorized = !!(data && data.authorized);
      if (signed) signed.hidden = !authorized;
      if (pairing) pairing.hidden = authorized;
    }

    async function ensureApplePairing() {
      const source = currentPlayerSource();
      if (source !== 'apple_music') return;
      const status = await refreshPlayerStatus();
      updateAppleAuthPanel(status);
      if (status && status.authorized) {
        appleUserToken = appleUserToken || '';
        await configureMusicKit();
        return;
      }
      try {
        const session = await fetchJsonWithTimeout('/api/player/apple/session', {}, 4000);
        const qr = document.getElementById('appleQr');
        const code = document.getElementById('applePairCode');
        const link = document.getElementById('appleLoginLink');
        if (qr && session.qr_png) {
          qr.innerHTML = '';
          const img = document.createElement('img');
          img.src = session.qr_png;
          img.alt = 'Apple ID login QR code';
          qr.appendChild(img);
        }
        if (code) code.textContent = session.code || '······';
        if (link && session.login_url) link.href = session.login_url;
        updateAppleAuthPanel({authorized: false});
        if (applePairTimer) clearInterval(applePairTimer);
        applePairTimer = setInterval(() => pollAppleSession(session.session), 2000);
      } catch (err) {
        setText('playerNowPlaying', 'Apple login error: ' + err.message);
      }
    }

    async function pollAppleSession(sessionId) {
      if (!sessionId) return;
      try {
        const data = await fetchJsonWithTimeout('/api/player/apple/session?id=' + encodeURIComponent(sessionId), {}, 4000);
        if (data && data.authorized) {
          appleUserToken = data.music_user_token || appleUserToken;
          if (applePairTimer) { clearInterval(applePairTimer); applePairTimer = null; }
          await configureMusicKit();
          updateAppleAuthPanel({authorized: true});
          setText('playerNowPlaying', 'Apple ID connected.');
          await loadPlayerPlaylists();
          await refreshPlayerStatus();
        }
      } catch (err) {
        console.warn('Apple session poll failed', err);
      }
    }

    async function loadMusicKit() {
      if (window.MusicKit) return window.MusicKit;
      await new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'https://js-cdn.music.apple.com/musickit/v3/musickit.js';
        s.onload = () => resolve(window.MusicKit);
        s.onerror = () => reject(new Error('MusicKit failed to load'));
        document.head.appendChild(s);
      });
      return window.MusicKit;
    }

    async function configureMusicKit() {
      try {
        await loadMusicKit();
        const cfg = await fetchJsonWithTimeout('/api/player/apple/config', {}, 4000);
        if (!cfg || !cfg.developerToken || !window.MusicKit) return null;
        if (!musicKitReady) {
          musicKitReady = MusicKit.configure({
            developerToken: cfg.developerToken,
            app: { name: cfg.appName || 'Lightss', build: cfg.appBuild || '0.1.0' },
            musicUserToken: appleUserToken || undefined
          });
        }
        const music = await musicKitReady;
        if (appleUserToken && music && !music.isAuthorized) {
          try { music.musicUserToken = appleUserToken; } catch (e) {}
        }
        if (music && music.addEventListener) {
          music.addEventListener('playbackStateDidChange', syncAppleNowPlaying);
          music.addEventListener('nowPlayingItemDidChange', syncAppleNowPlaying);
        }
        return music;
      } catch (err) {
        console.warn('MusicKit configure failed', err);
        return null;
      }
    }

    async function handleAppleClientCommand(command, extra) {
      const music = await configureMusicKit();
      if (!music) return null;
      try {
        if (command === 'playItem') {
          const values = Object.assign({}, extra.data || {}, extra);
          const kind = values.kind || 'song';
          const song = values.song || values.songId || (kind === 'song' ? values.id : null);
          const album = values.album || values.albumId || (kind === 'album' ? values.id : null);
          const playlist = values.playlist || values.playlistId || (kind === 'playlist' ? values.id : null);
          if (values.artist || values.artistId || kind === 'artist') throw new Error('Open this artist in the desktop provider tab and choose a track or album.');
          if (song) await music.setQueue({ song });
          else if (album) await music.setQueue({ album });
          else if (playlist) await music.setQueue({ playlist });
          else throw new Error('Choose a playable track, album, or playlist.');
          await music.play();
        } else if (command === 'seek') await music.seekToTime(Number((extra.data || extra).position));
        else if (command === 'volume') music.volume = Math.max(0,Math.min(1,Number((extra.data || extra).volume)));
        else if (command === 'play') await music.play();
        else if (command === 'pause') await music.pause();
        else if (command === 'playPause') {
          if (music.isPlaying) await music.pause();
          else await music.play();
        } else if (command === 'next') await music.skipToNextItem();
        else if (command === 'previous') await music.skipToPreviousItem();
        else return null;
        await ensurePlayerAnalyser();
        if (!musicModeRunning) startMusicMode();
        await syncAppleNowPlaying();
        return {ok: true, client: true, command};
      } catch (err) {
        setText('playerNowPlaying', 'Apple playback error: ' + err.message);
        return {ok: false, command, message: err.message};
      }
    }

    async function syncAppleNowPlaying() {
      try {
        const music = window.MusicKit && MusicKit.getInstance && MusicKit.getInstance();
        const item = music && (music.nowPlayingItem || (music.player && music.player.nowPlayingItem));
        const attrs = (item && (item.attributes || item)) || {};
        const payload = {
          title: attrs.name || attrs.title || '',
          artist: attrs.artistName || attrs.artist || '',
          album: attrs.albumName || '',
          artwork: attrs.artwork && attrs.artwork.url ? String(attrs.artwork.url).replace('{w}', '240').replace('{h}', '240') : '',
          playing: !!(music && (music.isPlaying || (music.player && music.player.isPlaying)))
        };
        applyPlayerNowPlaying(payload);
        await postJson('/api/player/apple/now', payload);
      } catch (err) {
        console.warn('Apple now-playing sync failed', err);
      }
    }

    async function signOutApple() {
      appleUserToken = '';
      try {
        const music = window.MusicKit && MusicKit.getInstance && MusicKit.getInstance();
        if (music && music.unauthorize) await music.unauthorize();
      } catch (e) {}
      await ensureApplePairing();
    }

    function renderLibraryRows(el, items, kind) {
      if (!el) return;
      const rows = (items || []).filter((item) => item && (item.id || item.title));
      if (!rows.length) {
        el.innerHTML = '<div class="library-empty">Nothing here yet.</div>';
        return;
      }
      el.innerHTML = '';
      for (const item of rows) {
        const id = String(item.id || '');
        const title = String(item.title || 'Untitled');
        const artist = String(item.artist || item.kind || '');
        const art = String(item.artwork || '');
        const playKind = item.kind || kind || 'song';
        const btn = document.createElement('button');
        btn.className = 'library-row';
        btn.title = 'Play ' + title;
        btn.onclick = () => playLibraryItem(playKind, id);
        if (art && /^https?:[/][/]/i.test(art)) {
          const img = document.createElement('img');
          img.src = art;
          img.alt = '';
          btn.appendChild(img);
        } else {
          const thumb = document.createElement('span');
          thumb.className = 'library-thumb';
          btn.appendChild(thumb);
        }
        const meta = document.createElement('span');
        const strong = document.createElement('b');
        strong.textContent = title;
        const sub = document.createElement('span');
        sub.textContent = artist;
        meta.append(strong, sub);
        btn.appendChild(meta);
        el.appendChild(btn);
      }
    }

    async function searchPlayerLibrary() {
      const q = ((document.getElementById('playerSearchInput') || {}).value || '').trim();
      const box = document.getElementById('playerSearchResults');
      if (box) box.innerHTML = '<div class="library-empty">Searching…</div>';
      try {
        const data = await fetchJsonWithTimeout('/api/player/search?source=' + encodeURIComponent(currentPlayerSource()) + '&q=' + encodeURIComponent(q), {}, 8000);
        renderLibraryRows(box, data.items || [], null);
        if (data.message && !(data.items || []).length) setText('playerNowPlaying', data.message);
      } catch (err) {
        if (box) box.textContent = 'Search failed: ' + err.message;
      }
    }

    function applyPlayerEnabled(enabled) {
      const wasEnabled = playerEnabled;
      playerEnabled = enabled !== false;
      const deck = document.querySelector('.player-deck');
      if (deck) deck.style.display = playerEnabled ? '' : 'none';
      if (!playerEnabled && playerPollTimer) { clearInterval(playerPollTimer); playerPollTimer = null; }
      if (playerEnabled && !wasEnabled && !playerPollTimer) {
        refreshPlayerStatus();
        loadPlayerPlaylists();
        playerPollTimer = setInterval(refreshPlayerStatus, 4000);
      }
    }

    async function loadPlayerPlaylists() {
      if (!playerEnabled) return;
      const box = document.getElementById('playerPlaylists');
      if (box) box.innerHTML = '<div class="library-empty">Loading playlists…</div>';
      try {
        const data = await fetchJsonWithTimeout('/api/player/playlists?source=' + encodeURIComponent(currentPlayerSource()), {}, 8000);
        renderLibraryRows(box, data.playlists || [], 'playlist');
        if (data.message && !(data.playlists || []).length) {
          if (box) box.innerHTML = '<div class="library-empty">' + String(data.message).replace(/</g, '') + '</div>';
        }
      } catch (err) {
        if (box) box.innerHTML = '<div class="library-empty">Playlists unavailable.</div>';
      }
    }

    async function playLibraryItem(kind, id) {
      if (!id) return;
      const extra = {id, kind, data:{id,kind}};
      await playerCommand('playItem', extra);
    }

    async function ensurePlayerAnalyser() {
      const audio = document.querySelector('audio');
      if (currentPlayerSource() !== 'apple_music' || !audio || audio.paused || audio.ended) return false;
      if (!audioContext || audioContext.state === 'closed') {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
      }
      if (audioContext.state === 'suspended') await audioContext.resume();
      if (!playerMediaSource) {
        try { playerMediaSource = audioContext.createMediaElementSource(audio); }
        catch (err) { console.warn('Player analyser already attached', err.message); }
      }
      if (!analyser) {
        analyser = audioContext.createAnalyser();
        analyser.fftSize = 2048;
        analyser.smoothingTimeConstant = 0.72;
        frequencyData = new Uint8Array(analyser.frequencyBinCount);
        waveformData = new Uint8Array(analyser.fftSize);
      }
      if (playerMediaSource) {
        try { playerMediaSource.connect(analyser); } catch (e) {}
        try { analyser.connect(audioContext.destination); } catch (e) {}
      }
      if (!audioReactiveRunning) {
        audioReactiveRunning = true;
        setText('micPipelineState', 'Player tap');
        analyzeAudio();
      }
      return true;
    }

    async function applyDynamicScene() {
      const data = await send('dynamic_scene', {
        mood: dynMood.value.trim(),
        energy: dynEnergy.value,
        motion: dynMotion.value,
        strategy: dynStrategy.value.trim(),
        engine: dynEngine.value,
        composition_mode: dynComposition.value,
        intensity: Number(dynIntensity.value) / 100,
        colors: parseColorList(dynColors.value),
        seed: dynSeed.value.trim() || null
      });
      setText('dynSceneStatus', data && data.ok ? (data.message || 'Dynamic scene applied.') : (data && data.error ? ('Dynamic scene error: ' + data.error) : 'Dynamic scene request sent.'));
    }

    async function startRealtime() {
      const data = await send('realtime_start', {
        shader: rtShader.value,
        mood: rtMood.value.trim(),
        composition_mode: rtComposition.value,
        intensity: Number(rtIntensity.value) / 100,
        fps: Number(rtFps.value),
        duration_s: Number(rtDuration.value),
        colors: parseColorList(rtColors.value),
        seed: rtSeed.value.trim() || null
      });
      await refreshRealtimeStatus();
      setText('realtimeStatus', data && data.ok ? (data.message || 'Realtime stream started.') : (data && data.error ? ('Realtime error: ' + data.error) : 'Realtime request sent.'));
    }

    async function designLook() {
      const data = await send('design_look', {
        prompt: rtMood.value.trim() || 'unique wall look',
        mood: rtMood.value.trim(),
        colors: parseColorList(rtColors.value),
        seed: rtSeed.value.trim() || null,
        run: true,
        fps: Number(rtFps.value),
        duration_s: Number(rtDuration.value),
        composition_mode: rtComposition.value,
        intensity: Number(rtIntensity.value)/100
      });
      await refreshRealtimeStatus();
      setText('realtimeStatus', data && data.ok ? (data.message || 'Look design started.') : (data && data.error ? ('Look design error: ' + data.error) : 'Look design request sent.'));
    }

    async function stopRealtime() {
      const data = await send('realtime_stop');
      setText('realtimeStatus', data && data.ok ? (data.message || 'Realtime stopped.') : (data && data.error ? ('Realtime error: ' + data.error) : 'Realtime stop request sent.'));
    }

    async function refreshRealtimeStatus() {
      try {
        const data = await postAction('realtime_status', {}, 4000);
        const message = data && data.message ? data.message : JSON.stringify(data || {});
        setText('realtimeStatus', message || 'No realtime status available.');
        return data;
      } catch (err) {
        setText('realtimeStatus', 'Status error: ' + err.message);
        return null;
      }
    }

    async function submitLookFeedback(score) {
      const payload = {
        score: Number(score),
        notes: feedbackNotes.value.trim(),
        tags: parseTagList(feedbackTags.value),
        applies_to: 'last'
      };
      const lookId = feedbackLookId.value.trim();
      if (lookId) payload.look_id = lookId;
      try {
        const data = await postAction('look_feedback', payload, 6000);
        setText('feedbackStatus', data && data.message ? data.message : 'Feedback saved.');
        return data;
      } catch (err) {
        setText('feedbackStatus', 'Feedback error: ' + err.message);
        return null;
      }
    }

    async function refreshLookMemory() {
      try {
        const data = await postAction('look_memory_summary', {limit: 8}, 6000);
        setText('lookMemorySummary', data && data.message ? data.message : 'Look memory unavailable.');
        return data;
      } catch (err) {
        setText('lookMemorySummary', 'Memory error: ' + err.message);
        return null;
      }
    }

    function optimisticPreviewFromAction(action, values) {
      if (!currentStripState) currentStripState = {on: true, bri: 200, fx: 0, sx: 128, ix: 128, colors: [[255,255,255,0]], seg: {}, segments: STRIP_SEGMENTS};
      const v = values || {};
      const trans = v.transition || 0; // ignore for preview
      if (action === 'on') currentStripState.on = true;
      if (action === 'off') currentStripState.on = false;
      if (action === 'bri' || action === 'brightness') currentStripState.bri = clamp(v.value ?? v.brightness ?? currentStripState.bri, 0, 255);
      if (action === 'color' || action === 'rgbw_bri') {
        const col0 = [clamp(v.red,0,255), clamp(v.green,0,255), clamp(v.blue,0,255), clamp(v.white||0,0,255)];
        currentStripState.colors[0] = col0;
        if (v.brightness != null) currentStripState.bri = clamp(v.brightness,0,255);
        if (!currentStripState.seg) currentStripState.seg = {};
        if (!currentStripState.seg.col) currentStripState.seg.col = [col0];
        currentStripState.seg.col[0] = col0;
      }
      if (action === 'hex') {
        try {
          const c = hexToRgbwLocal(String(v.color || '#ffffff'));
          currentStripState.colors[0] = c;
        } catch(e){}
      }
      if (action === 'fx' || action === 'effect') {
        const newFx = clamp(v.effect ?? v.fx ?? currentStripState.fx, 0, 255);
        currentStripState.fx = newFx;
        if (v.speed != null) currentStripState.sx = clamp(v.speed, 0, 255);
        if (!currentStripState.seg) currentStripState.seg = {};
        currentStripState.seg.fx = newFx;
        if (v.speed != null) currentStripState.seg.sx = currentStripState.sx;
      }
      if (action === 'temp') {
        // rough kelvin -> rgbw approx for preview
        const k = v.kelvin || 4000;
        const t = Math.max(2000, Math.min(6500, k)) / 100;
        let r,g,b;
        if (t <= 66) { r=255; g = 99.47*Math.log(t)-161.12; b = (t<=19?0: 138.5*Math.log(t-10)-305) } else { r=329.7*Math.pow(t-60,-0.133); g=288.1*Math.pow(t-60,-0.0755); b=255; }
        currentStripState.colors[0] = [clamp(r,0,255), clamp(g,0,255), clamp(b,0,255), 80];
      }
      if (action === 'cct') {
        // For preview, treat high cct as cooler (more blue), low as warm (more red/yellow tint)
        const val = clamp(v.cct || 127, 0, 255);
        const warm = Math.max(0, 255 - val);
        currentStripState.colors[0] = [clamp(200 + warm/2,0,255), clamp(180 + warm/3 ,0,255), clamp(220 - warm/2,0,255), val];
      }
      if (action === 'scene') {
        // scenes set specific; for preview just leave or set a representative
        currentStripState.fx = 0;
      }
      if (action === 'beat') {
        currentStripState.colors[0] = [clamp(v.red,0,255), clamp(v.green,0,255), clamp(v.blue,0,255), clamp(v.white||0,0,255)];
        if (v.brightness) currentStripState.bri = clamp(v.brightness,0,255);
        if (v.effect) currentStripState.fx = clamp(v.effect,0,255);
        if (v.speed) currentStripState.sx = clamp(v.speed,0,255);
      }
      // nudge the frame
      if (currentStripState.on) ledStrip && ledStrip.classList && ledStrip.classList.remove('off');
    }

    function clamp(n, lo, hi) { n = Number(n); return Math.max(lo, Math.min(hi, isFinite(n) ? Math.round(n) : lo)); }
    function hexToRgbwLocal(hex) {
      hex = String(hex || '').replace('#','').toLowerCase();
      if (hex.length === 6) return [parseInt(hex.slice(0,2),16), parseInt(hex.slice(2,4),16), parseInt(hex.slice(4,6),16), 0];
      if (hex.length === 8) return [parseInt(hex.slice(0,2),16), parseInt(hex.slice(2,4),16), parseInt(hex.slice(4,6),16), parseInt(hex.slice(6,8),16)];
      return [255,200,100,0];
    }

    async function askAI() {
      const prompt = aiInput.value.trim();
      if (!prompt) return;
      // Freeze the continuously-writing beat loop before the AI snapshots and
      // changes WLED so its result remains visible and controllable.
      if (musicModeRunning) stopMusicMode();
      addChatMessage('user', prompt);
      aiInput.value = '';
      addChatMessage('system', 'Thinking...');
      const thinkingEls = chatHistory.querySelectorAll('.chat-msg.system');
      const thinkingEl = thinkingEls[thinkingEls.length - 1];
      aiReply.textContent = '';
      aiConfirmations.textContent = '';
      try {
        const song = await refreshNowPlaying();
        const res = await fetchJsonWithTimeout('/api/ai', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({prompt, now_playing: song, target: currentTarget, async: true})
        }, 180000);
        let data = res;
        if (data.ok && data.job_id) {
          status.textContent = 'AI planning...';
          data = await waitForAiJob(data.job_id);
        }
        if (thinkingEl) thinkingEl.remove();
        status.textContent = data.ok ? data.message : data.error;
        if (!data.ok) appendModelResponse('error', data.error || 'AI request failed.');
        if (data.response) {
          // Instead of cluttering AI Chat tab, scroll descriptive model responses right-to-left in the top box
          showScrollingModelResponse(data.response);
        }
        if (data.confirmations && data.confirmations.length) {
          addChatMessage('ai', data.confirmations.join(' '));
        }
        // Keep aiReply/confirmations in tab for those who open the full chat, but main long response is marquee above
        aiReply.textContent = data.response || '';
        aiConfirmations.textContent = (data.confirmations || []).join(' ');
        for (const action of data.client_actions || []) {
          if (action === 'startAudioReactive') await startAudioReactive();
          if (action === 'stopAudioReactive') stopAudioReactive();
          if (typeof action === 'object' && action.action === 'fadeOff') await send('fade_off', {minutes: action.minutes || 30});
          if (typeof action === 'object' && action.action === 'startCycle') await send('cycle_start', {interval: action.interval || 60});
          if (typeof action === 'object' && action.action === 'stopCycle') await send('cycle_stop');
          if (typeof action === 'object' && action.action === 'startSunrise') await send('sunrise_start', {minutes: action.minutes || 30, brightness: action.brightness || 255});
          if (typeof action === 'object' && action.action === 'stopSunrise') await send('sunrise_stop');
          if (typeof action === 'object' && action.action === 'detectSong') await refreshNowPlaying();
          if (typeof action === 'object' && action.action === 'matchLightsFromNowPlaying') await matchLightsFromNowPlaying();
          if (typeof action === 'object' && action.action === 'matchLightsToSong') await matchLightsFromNowPlaying();
        }
      } catch (err) {
        if (thinkingEl) thinkingEl.remove();
        status.textContent = 'AI error: ' + err.message;
        addChatMessage('ai', 'Error: ' + err.message);
        appendModelResponse('error', 'AI error: ' + err.message);
      }
    }

    async function waitForAiJob(jobId) {
      // Tool-calling AI loops (multi-round function calls) routinely take
      // 1-4 minutes on slower providers — wait up to 5 minutes.
      const started = Date.now();
      while (Date.now() - started < 300000) {
        const data = await fetchJsonWithTimeout(`/api/ai/jobs/${encodeURIComponent(jobId)}`);
        const job = data.job || {};
        if (job.status === 'complete') return job.result || {ok: false, error: 'AI job completed without a result.'};
        if (job.status === 'error') return {ok: false, error: job.error || 'AI job failed.'};
        status.textContent = `AI planning... ${Math.floor((Date.now() - started) / 1000)}s`;
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
      return {ok: false, error: 'AI job timed out.'};
    }

    function useExamplePrompt() {
      aiInput.value = 'Run effects with the beat. Make it interesting without strobes or harsh transitions.';
      aiInput.focus();
    }

    function setText(id, value) {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    }

    function setMusicModeUi(running, detail = '') {
      const pill = document.getElementById('musicModeState');
      if (pill) {
        pill.textContent = running ? 'Running' : 'Idle';
        pill.classList.toggle('running', running);
      }
      const card = document.querySelector('.auto-card');
      if (card) {
        card.classList.toggle('running', running);
      }
      setText('micPipelineState', running ? 'Listening' : 'Idle');
      setText('nextMatchState', running ? (detail || 'On song change') : 'Manual');
    }

    let webcamStream = null;

    async function toggleWebcam() {
      const video = document.getElementById('webcamVideo');
      const container = document.getElementById('webcamContainer');
      const analyzeBtn = document.getElementById('analyzeRoomBtn');
      const toggleBtn = document.getElementById('toggleWebcamBtn');

      if (webcamStream) {
        // Stop webcam
        webcamStream.getTracks().forEach(track => track.stop());
        webcamStream = null;
        video.srcObject = null;
        container.style.display = 'none';
        analyzeBtn.style.display = 'none';
        toggleBtn.innerText = 'Start Camera';
        document.getElementById('scanningLaser').style.display = 'none';
      } else {
        // Start webcam
        try {
          webcamStream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 } });
          video.srcObject = webcamStream;
          container.style.display = 'block';
          analyzeBtn.style.display = 'inline-block';
          toggleBtn.innerText = 'Stop Camera';
        } catch (err) {
          document.getElementById('visionStatus').innerText = 'Error opening camera: ' + err.message;
        }
      }
    }

    async function analyzeRoom() {
      const video = document.getElementById('webcamVideo');
      const laser = document.getElementById('scanningLaser');
      const status = document.getElementById('visionStatus');

      if (!webcamStream) return;

      laser.style.display = 'block';
      status.innerText = 'Scanning room layout & lighting...';

      // Capture frame
      const canvas = document.createElement('canvas');
      canvas.width = 640;
      canvas.height = 480;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const base64Image = canvas.toDataURL('image/jpeg', 0.8).split(',')[1];

      try {
        const data = await fetchJsonWithTimeout('/api/ai_vision', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image: base64Image })
        }, 60000);
        laser.style.display = 'none';
        if (data.error) {
          status.innerText = 'Analysis failed: ' + data.error;
        } else {
          status.innerText = 'Vibe parsed! Mood: ' + data.response;
          // Refresh live UI
          await fetchLiveState();
        }
      } catch (err) {
        laser.style.display = 'none';
        status.innerText = 'Network error: ' + err.message;
      }
    }

    async function fetchLiveState() {
      try {
        const response = await fetch('/api/state');
        const data = await response.json();
        if (data.ok && data.state) {
          applyFleetInfo(data);
          updateConnectionStatusFromState(data.state);
          const picked = displayState(data.state);
          const st = picked.state;
          const onOff = st.on ? 'ON' : 'OFF';
          const bri = st.bri ?? '?';
          const seg = st.seg && st.seg[0] ? st.seg[0] : {};
          const col = seg.col && seg.col[0] ? seg.col[0] : [0,0,0,0];
          let display = `Power: ${onOff}\nBrightness: ${bri}\nColor: RGBW(${col.join(',')})\nEffect: ${seg.fx ?? '-'} Speed: ${seg.sx ?? '-'}`;
          if (picked.name) display = `Controller: ${picked.name}\n` + display;
          
          const stateDisplay = document.getElementById('stateDisplay');
          const stateSummary = document.getElementById('stateSummary');
          if (stateDisplay) stateDisplay.textContent = display;
          if (stateSummary) stateSummary.textContent = `${onOff} | Bri ${bri} | Fx ${seg.fx ?? '-'} @ ${seg.sx ?? '-'}`;
          
          if (typeof updateLightPreview === 'function') {
            updateLightPreview(st, ledCapabilities, picked.segId);
          }
        }
      } catch (err) {
        console.error('Error fetching live state:', err);
      }
    }

    async function refreshNowPlaying() {
      try {
        const data = await fetchJsonWithTimeout('/api/now-playing');
        const np = data.now_playing || null;
        if (np) {
          musicTitle.textContent = np.title || 'Unknown';
          musicArtist.textContent = np.artist || '';
          if (np.genre) { musicGenre.textContent = np.genre; musicGenre.style.display = 'inline-block'; }
          else { musicGenre.style.display = 'none'; }
          if (np.cover_url) {
            albumArt.src = np.cover_url;
            albumArt.classList.add('visible');
            const wrap = document.getElementById('playerArtWrap');
            if (wrap) wrap.classList.add('has-art');
          } else {
            albumArt.classList.remove('visible');
            const wrap = document.getElementById('playerArtWrap');
            if (wrap) wrap.classList.remove('has-art');
          }
          setText('songSourceState', np.source ? np.source : 'Media metadata');
        } else {
          musicTitle.textContent = data.text || data.error || 'No song detected.';
          musicArtist.textContent = '';
          musicGenre.style.display = 'none';
          albumArt.classList.remove('visible');
          setText('songSourceState', 'No metadata');
        }
        nowPlaying.textContent = data.text || data.error || 'No song detected.';
        return np;
      } catch (err) {
        musicTitle.textContent = 'Error fetching song.';
        musicArtist.textContent = '';
        musicGenre.style.display = 'none';
        albumArt.classList.remove('visible');
        nowPlaying.textContent = 'Error fetching song.';
        setText('songSourceState', 'Unavailable');
        return null;
      }
    }

    // --- browser webcam/mic song ID support ---
    function encodeWAV(samples, sampleRate) {
      const buf = new ArrayBuffer(44 + samples.length * 2);
      const view = new DataView(buf);
      function w(s, o) { for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i)); }
      function ws(o, v) { view.setUint16(o, v, true); }
      function wi(o, v) { view.setUint32(o, v, true); }
      w("RIFF", 0); wi(4, 36 + samples.length * 2); w("WAVE", 8);
      w("fmt ", 12); wi(16, 16); ws(20, 1); ws(22, 1); wi(24, sampleRate);
      wi(28, sampleRate * 2); ws(32, 2); ws(34, 16);
      w("data", 36); wi(40, samples.length * 2);
      let o = 44;
      for (let i = 0; i < samples.length; i++, o += 2) {
        let s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
      }
      return buf;
    }
    async function getPreferredMicStream() {
      const audio = { echoCancellation: false, noiseSuppression: false, autoGainControl: false };
      const initial = await navigator.mediaDevices.getUserMedia({ audio });
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const preferred = devices.find((device) =>
          device.kind === 'audioinput' && /c920|webcam/i.test(device.label || '')
        );
        const currentTrack = initial.getAudioTracks()[0];
        const currentDeviceId = currentTrack && currentTrack.getSettings
          ? currentTrack.getSettings().deviceId
          : '';
        if (!preferred || !preferred.deviceId || preferred.deviceId === currentDeviceId) return initial;

        const webcamStream = await navigator.mediaDevices.getUserMedia({
          audio: {
            ...audio,
            deviceId: { exact: preferred.deviceId }
          }
        });
        initial.getTracks().forEach((track) => track.stop());
        return webcamStream;
      } catch (err) {
        console.warn('Could not select webcam microphone; using browser default:', err.message);
        return initial;
      }
    }

    async function ensureMicSession() {
      if (!micStream) {
        micStream = await getPreferredMicStream();
      }
      if (!audioContext || audioContext.state === 'closed') {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        micSource = audioContext.createMediaStreamSource(micStream);
      }
      if (audioContext.state === 'suspended') await audioContext.resume();
      if (!analyser) {
        analyser = audioContext.createAnalyser();
        analyser.fftSize = 2048;
        analyser.smoothingTimeConstant = 0.72;
        frequencyData = new Uint8Array(analyser.frequencyBinCount);
        waveformData = new Uint8Array(analyser.fftSize);
        micSource.connect(analyser);
      }
      return {ctx: audioContext, source: micSource};
    }

    async function captureMicWav(seconds = 5) {
      const {ctx, source} = await ensureMicSession();
      const proc = ctx.createScriptProcessor(4096, 1, 1);
      const chunks = [];
      proc.onaudioprocess = (e) => { chunks.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
      // Connect for processing but route to silent gain to prevent audible feedback/echo into the recording
      const silent = ctx.createGain();
      silent.gain.value = 0.0;
      source.connect(proc);
      proc.connect(silent);
      silent.connect(ctx.destination);
      await new Promise(r => setTimeout(r, seconds * 1000));
      // cleanup without breaking the analyser viz connection
      try { proc.disconnect(silent); } catch(e){}
      try { source.disconnect(proc); } catch(e){}
      try { silent.disconnect(ctx.destination); } catch(e){}
      let len = 0; for (const c of chunks) len += c.length;
      const samples = new Float32Array(len);
      let off = 0; for (const c of chunks) { samples.set(c, off); off += c.length; }
      const wav = encodeWAV(samples, ctx.sampleRate);
      const u8 = new Uint8Array(wav);
      let bin = ""; for (let i = 0; i < u8.length; i++) bin += String.fromCharCode(u8[i]);
      return btoa(bin);
    }

    async function recognizeSongOnce() {
      musicTitle.textContent = '🎤 Listening... (6s)';
      musicArtist.textContent = '';
      musicGenre.style.display = 'none';
      albumArt.classList.remove('visible');
      try {
        const b64 = await captureMicWav(6);
        const data = await fetchJsonWithTimeout('/api/recognize', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({audio: b64})
        }, 20000);
        if (data.ok && data.now_playing) {
          const np = data.now_playing;
          musicTitle.textContent = np.title || 'Song recognized!';
          musicArtist.textContent = np.artist || '';
          if (np.genre) { musicGenre.textContent = np.genre; musicGenre.style.display = 'inline-block'; }
          nowPlaying.textContent = data.text || 'Song recognized!';
          return np;
        }
        musicTitle.textContent = data.error || 'No match found.';
        nowPlaying.textContent = data.error || 'No match found.';
        return null;
      } catch (err) {
        musicTitle.textContent = 'Recognition error: ' + err.message;
        nowPlaying.textContent = 'Recognition error: ' + err.message;
        return null;
      }
    }

    function applyMatchedSongResponse(data) {
      status.textContent = data.message || 'Lights matched!';
      const np = data.now_playing;
      if (np) {
        musicTitle.textContent = np.title || 'Unknown';
        musicArtist.textContent = np.artist || '';
        if (np.genre) { musicGenre.textContent = np.genre; musicGenre.style.display = 'inline-block'; }
        else { musicGenre.style.display = 'none'; }
        if (np.cover_url) { albumArt.src = np.cover_url; albumArt.classList.add('visible'); }
        else { albumArt.classList.remove('visible'); }
      }
      if (data.response) {
        showScrollingModelResponse(data.response);
      }
      const confirmations = Array.isArray(data.confirmations) ? data.confirmations.join(' ') : (data.confirmations || '');
      if (confirmations) addChatMessage('ai', confirmations);
      aiReply.textContent = data.response || '';
      aiConfirmations.textContent = confirmations;
    }

    async function matchLightsFromNowPlaying() {
      status.textContent = 'Reading media metadata...';
      try {
        const song = await refreshNowPlaying();
        if (!song) {
          const message = 'No media metadata detected; beat analyzer still running.';
          status.textContent = message;
          const st = document.getElementById('autoStatus');
          if (st) st.textContent = message;
          return {ok: false, now_playing: null, error: message};
        }
        const data = await fetchJsonWithTimeout('/api/match-lights', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({now_playing: song})
        });
        if (data.ok) {
          applyMatchedSongResponse(data);
        } else {
          status.textContent = data.error || 'Could not match lights.';
        }
        return data;
      } catch (err) {
        status.textContent = 'Match lights error: ' + err.message;
        return {ok: false, now_playing: null, error: err.message};
      }
    }

    async function matchLightsFromRecognizedSong(song) {
      if (!song) return {ok: false, now_playing: null, error: 'No song recognized.'};
      try {
        const data = await fetchJsonWithTimeout('/api/match-lights', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({now_playing: song})
        });
        if (data.ok) {
          applyMatchedSongResponse(data);
        } else {
          status.textContent = data.error || 'Could not match recognized song.';
        }
        return data;
      } catch (err) {
        status.textContent = 'Recognized song match error: ' + err.message;
        return {ok: false, now_playing: song, error: err.message};
      }
    }

    async function matchLightsToSong() {
      return matchLightsFromNowPlaying();
    }

    async function refreshSmartSuggestions() {
      const box = document.getElementById('smartSuggestions');
      if (!box) return;
      try {
        const data = await fetchJsonWithTimeout('/api/suggestions');
        const suggestions = data.suggestions || [];
        box.innerHTML = suggestions.map((item) => {
          const click = item.action === 'music_match'
            ? 'matchLightsFromNowPlaying()'
            : `send('${item.action}', ${JSON.stringify(item.payload || {}).replace(/"/g, '&quot;')})`;
          return `
            <button class="smart-chip" onclick="${click}">
              <b>${item.title}</b><span>${item.reason}</span>
            </button>
          `;
        }).join('');
      } catch (err) {
        box.innerHTML = '<div class="smart-chip"><b>Smart picks offline</b><span>Suggestions will appear when the controller responds.</span></div>';
      }
    }

    async function autoMatchSong() {
      const btn = document.getElementById('autoMatchBtn');
      const st = document.getElementById('autoStatus');
      if (!btn) {
        await runMusicRecognitionCycle(true);
        return;
      }
      btn.disabled = true;
      st.textContent = 'Detecting song and choosing lights…';
      try {
        await runMusicRecognitionCycle(true);
        st.textContent = status.textContent || 'Done';
      } catch (err) {
        st.textContent = 'Error: ' + err.message;
      } finally {
        btn.disabled = false;
      }
    }

    async function startMoodRecorder() {
      if (!micStream) return;
      if (moodRecorderInterval) clearInterval(moodRecorderInterval);

      const audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 44100 });
      const source = audioCtx.createMediaStreamSource(micStream);
      const processor = audioCtx.createScriptProcessor(4096, 1, 1);
      const chunks = [];

      processor.onaudioprocess = (e) => {
        if (!musicModeRunning || musicMetadataActive) return;
        chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      };
      source.connect(processor);
      processor.connect(audioCtx.destination);

      async function uploadChunk() {
        if (!musicModeRunning || musicMetadataActive) { chunks.length = 0; return; }
        if (chunks.length === 0) return;
        const totalLength = chunks.reduce((sum, c) => sum + c.length, 0);
        const combined = new Float32Array(totalLength);
        let idx = 0;
        for (const c of chunks) { combined.set(c, idx); idx += c.length; }
        chunks.length = 0;

        const offline = new OfflineAudioContext(1, combined.length, audioCtx.sampleRate);
        const buf = offline.createBuffer(1, combined.length, audioCtx.sampleRate);
        buf.getChannelData(0).set(combined);
        const offlineSource = offline.createBufferSource();
        offlineSource.buffer = buf;
        offlineSource.connect(offline.destination);
        offlineSource.start();
        const rendered = await offline.startRendering();
        const wavBlob = encodeWav(rendered);
        const b64 = await blobToBase64(wavBlob);

        try {
          await fetch('/api/mood/sample', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({audio_b64: b64})
          });
        } catch (err) {
          console.warn('Mood sample upload failed:', err);
        }
      }

      await uploadChunk();
      moodRecorderInterval = setInterval(uploadChunk, 5000);
      moodRecorder = { audioCtx, source, processor, stop: () => {
        clearInterval(moodRecorderInterval);
        moodRecorderInterval = null;
        try { processor.disconnect(); source.disconnect(); audioCtx.close(); } catch(e){}
      }};
    }

    function stopMoodRecorder() {
      if (moodRecorder) {
        moodRecorder.stop();
        moodRecorder = null;
      }
    }

    async function setMoodSessionRunning(running) {
      try {
        const data = await fetchJsonWithTimeout('/api/mood/control', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({command: running ? 'start' : 'stop'})
        });
        if (!data.ok) console.warn('Mood session control failed:', data.error);
        return data.ok;
      } catch (err) {
        console.warn('Mood session control failed:', err.message);
        return false;
      }
    }

    async function startMusicMode(options = {}) {
      const useBrowserMic = options && options.browserMic === true;
      if (!useBrowserMic) {
        const btn = document.getElementById('musicModeBtn');
        const st = document.getElementById('autoStatus');
        if (btn) btn.disabled = true;
        try {
          // Clean up only a legacy browser analyzer. stopMusicMode() is
          // intentionally local and cannot disable the new server director.
          if (musicModeRunning || audioReactiveRunning) stopMusicMode();
          await setSmartDirectorMode({enabled: true, mode: 'music'});
          if (btn) btn.textContent = 'Controller Music Running';
          setMusicModeUi(true, 'Controller mic');
          setText('micPipelineState', 'Controller mic');
          setText('songSourceState', 'TV context');
          if (st) st.textContent = 'Controller microphone is driving the direct renderer.';
        } catch (err) {
          stopAudioReactive();
          if (st) st.textContent = 'Music mode error: ' + err.message;
        } finally {
          if (btn) btn.disabled = false;
        }
        return;
      }
      if (musicModeRunning) return;
      const btn = document.getElementById('musicModeBtn');
      const st = document.getElementById('autoStatus');
      if (btn) btn.disabled = true;
      try {
        await startAudioReactive();
        if (!audioReactiveRunning) return;
        musicModeRunning = true;
        musicModeGeneration++;
        musicMetadataKey = '';
        musicMetadataActive = false;
        // Server-side MoodSession.sample() drops chunks unless the session is
        // running — start it before the recorder uploads the first sample.
        await setMoodSessionRunning(true);
        await startMoodRecorder();
        setText('songSourceState', 'Webcam mic');
        if (btn) btn.textContent = 'Music Mode Running';
        setMusicModeUi(true, 'Checking now');
        if (st) st.textContent = 'Beat matching live audio; checking media metadata and browser mic.';
        musicRecognitionTimer = setInterval(() => runMusicRecognitionCycle(false), MUSIC_RECOGNITION_INTERVAL_MS);
        await runMusicRecognitionCycle(false);
      } catch (err) {
        musicModeRunning = false;
        setMoodSessionRunning(false);
        stopAudioReactive();  // close the mic and cancel the analyser rAF
        if (st) st.textContent = 'Music mode error: ' + err.message;
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    function startBrowserMusicMode() {
      return startMusicMode({browserMic: true});
    }

    function stopMusicMode() {
      musicModeGeneration++;
      musicModeRunning = false;
      musicMetadataActive = false;
      musicMetadataKey = '';
      setMoodSessionRunning(false);
      stopMoodRecorder();
      if (musicRecognitionTimer) clearInterval(musicRecognitionTimer);
      musicRecognitionTimer = null;
      stopAudioReactive();
      const btn = document.getElementById('musicModeBtn');
      const st = document.getElementById('autoStatus');
      if (btn) btn.textContent = 'Start Music Mode';
      setMusicModeUi(false);
      if (st) st.textContent = 'Music mode stopped.';
    }

    let playbackClock = null;
    let playbackClockReceivedAt = 0;
    let playbackClockBusy = false;

    function renderPlaybackClock() {
      if (!playbackClock) return;
      const age = Math.max(0, (performance.now() - playbackClockReceivedAt) / 1000);
      const stamp = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
      if (Number.isFinite(playbackClock.position_s)) {
        let position = playbackClock.position_s + (playbackClock.playing ? age : 0);
        if (Number.isFinite(playbackClock.duration_s)) position = Math.min(position, playbackClock.duration_s);
        setText('songClockState', `${stamp(position)}${playbackClock.duration_s ? ' / ' + stamp(playbackClock.duration_s) : ''} · external clock${playbackClock.playing ? '' : ' · paused'}`);
      } else {
        const observed = Number(playbackClock.observed_for_s) || 0;
        setText('songClockState', `Position unknown${observed > 0 ? ' · observed for ' + stamp(observed + age) : ''}`);
      }
    }

    async function refreshPlaybackClock() {
      if (document.hidden || playbackClockBusy) return;
      playbackClockBusy = true;
      try {
        const data = await fetchJsonWithTimeout('/api/playback-clock', {}, 6000);
        if (data.ok && data.clock) {
          playbackClock = data.clock;
          playbackClockReceivedAt = performance.now();
          renderPlaybackClock();
        }
      } catch (err) {
        playbackClock = null;
        setText('songClockState', 'Clock unavailable');
      } finally { playbackClockBusy = false; }
    }

    async function runMusicRecognitionCycle(force = false) {
      if ((!musicModeRunning && !force) || musicRecognitionBusy) return;
      const generation = musicModeGeneration;
      musicRecognitionBusy = true;
      try {
        const song = await refreshNowPlaying();
        if (generation !== musicModeGeneration || (!force && !musicModeRunning)) return;
        const st = document.getElementById('autoStatus');
        musicMetadataActive = !!(song && (song.title || song.artist));
        if (musicMetadataActive) {
          const key = JSON.stringify([song.source || '', song.artist || '', song.title || '', song.album || '']);
          if (force || key !== musicMetadataKey) {
            const result = await matchLightsFromRecognizedSong(song);
            if (result && result.ok) musicMetadataKey = key;
          }
          if (st) st.textContent = `Following ${song.title || 'song'}; beat mode continues.`;
        } else if (force) {
          // Only explicit Identify calls use this path. Automatic microphone
          // identification has one owner: the server's local change detector.
          const song = await recognizeSongOnce();
          if (song && generation === musicModeGeneration) await matchLightsFromRecognizedSong(song);
        } else {
          musicMetadataKey = '';
          if (st) st.textContent = 'Listening locally for a song change; no scheduled Shazam calls.';
        }
      } finally {
        if (musicModeRunning) setText('nextMatchState', 'On song change');
        musicRecognitionBusy = false;
      }
    }

    async function startAudioReactive() {
      if (audioReactiveRunning) return;
      const tapped = await ensurePlayerAnalyser();
      if (tapped) {
        status.textContent = 'Listening to the player...';
        setText('micPipelineState', 'Player tap');
        return;
      }
      try {
        await ensureMicSession();
      } catch (err) {
        status.textContent = 'Mic error: ' + err.name;
        return;
      }
      audioReactiveRunning = true;
      status.textContent = 'Mode 1 listening...';
      setText('micPipelineState', 'Listening');
      analyzeAudio();
    }

    function stopAudioReactive() {
      if (!audioReactiveRunning) return;
      musicModeRunning = false;
      musicMetadataActive = false;
      musicMetadataKey = '';
      setMoodSessionRunning(false);
      stopMoodRecorder();
      if (musicRecognitionTimer) clearInterval(musicRecognitionTimer);
      musicRecognitionTimer = null;
      audioReactiveRunning = false;
      if (rafId) cancelAnimationFrame(rafId);
      rafId = null;
      closeMicSession();
      peakLevel = 0;
      resetAnalysisDisplay();
      setMusicModeUi(false);
      status.textContent = 'Mode 1 stopped.';
    }

    function closeMicSession() {
      if (micStream) micStream.getTracks().forEach(track => track.stop());
      if (audioContext && audioContext.state !== 'closed') audioContext.close();
      micStream = null;
      micSource = null;
      playerMediaSource = null;
      analyser = null;
      audioContext = null;
      frequencyData = null;
      waveformData = null;
    }

    function getActiveColors() {
      if (controllerVizActive()) {
        const pixels = controllerPreviewColors().filter(rgb => rgb.some(value => value > 0));
        const palette = controllerVizStatus.intelligence?.colors || [];
        const c1 = pixels[0] || palette[0] || [45,180,210];
        const c2 = pixels[Math.floor(pixels.length/2)] || palette[1] || [150,80,210];
        return {c1:`rgb(${c1.join(',')})`, c2:`rgb(${c2.join(',')})`, c3:`rgb(${c1.join(',')})`, raw1:c1,raw2:c2,raw3:c1,on:true,bri:255};
      }
      if (typeof currentStripState !== 'undefined' && currentStripState && currentStripState.on && currentStripState.colors && currentStripState.colors[0]) {
        const c1 = currentStripState.colors[0];
        const c2 = currentStripState.colors[1] || c1;
        const c3 = currentStripState.colors[2] || c1;
        return {
          c1: `rgb(${c1[0]}, ${c1[1]}, ${c1[2]})`,
          c2: `rgb(${c2[0]}, ${c2[1]}, ${c2[2]})`,
          c3: `rgb(${c3[0]}, ${c3[1]}, ${c3[2]})`,
          raw1: c1,
          raw2: c2,
          raw3: c3,
          on: true,
          bri: currentStripState.bri ?? 255
        };
      }
      return {
        c1: 'rgb(34, 211, 238)',
        c2: 'rgb(251, 191, 36)',
        c3: 'rgb(236, 72, 153)',
        raw1: [34, 211, 238, 0],
        raw2: [251, 191, 36, 0],
        raw3: [236, 72, 153, 0],
        on: typeof currentStripState !== 'undefined' && currentStripState ? currentStripState.on : false,
        bri: typeof currentStripState !== 'undefined' && currentStripState ? (currentStripState.bri ?? 0) : 0
      };
    }

    function updateVu(energy, beat, normalized = false) {
      const level = Math.max(0, Math.min(100, Math.round(energy * (normalized ? 100 : 1.35))));
      peakLevel = Math.max(level, peakLevel * 0.94);
      if (peakLevel < 0.5) peakLevel = 0;
      const vuF = document.getElementById('vuFill');
      const vuP = document.getElementById('vuPeak');
      const bl = document.getElementById('beatLamp');
      const orb = document.getElementById('energyOrb');
      
      const colors = getActiveColors();
      const isOn = colors.on && colors.bri > 0;

      if (vuF) {
        vuF.style.width = `${level}%`;
        if (isOn) {
          vuF.style.background = `linear-gradient(90deg, ${colors.c1}, ${colors.c2})`;
        } else {
          vuF.style.background = '#475569';
        }
      }
      if (vuP) vuP.style.left = `${Math.max(0, Math.min(99, peakLevel))}%`;
      if (bl) {
        bl.classList.toggle('on', beat && isOn);
        if (isOn) {
          bl.style.backgroundColor = colors.c2;
          bl.style.boxShadow = `0 0 15px ${colors.c2}`;
        } else {
          bl.style.backgroundColor = '#334155';
          bl.style.boxShadow = 'none';
        }
        if (beat && isOn) setTimeout(() => {
          bl.classList.remove('on');
          bl.style.boxShadow = 'none';
        }, 90);
      }
      if (orb) {
        const pulse = Math.max(0.85, Math.min(1.35, 0.9 + energy * 0.6));
        orb.style.transform = (beat && isOn) ? `scale(${pulse + 0.15})` : `scale(${pulse})`;
        if (!isOn) {
          orb.style.background = `radial-gradient(circle at 40% 30%, #555, #222, #050505)`;
          orb.style.boxShadow = 'none';
        } else {
          orb.style.background = `radial-gradient(circle at 40% 30%, #fff, ${beat ? colors.c2 : colors.c1}, #000)`;
          orb.style.boxShadow = `0 0 ${20 + energy * 30}px ${beat ? colors.c2 : colors.c1}`;
        }
        if (beat && isOn) orb.classList.add('beat');
        setTimeout(() => orb && orb.classList.remove('beat'), 140);
      }
    }

    function averageBand(data, startRatio, endRatio) {
      const start = Math.max(0, Math.floor(data.length * startRatio));
      const end = Math.max(start + 1, Math.min(data.length, Math.floor(data.length * endRatio)));
      let sum = 0;
      for (let i = start; i < end; i++) sum += data[i];
      return sum / (end - start);
    }

    function estimateBpm(beatTimes) {
      if (beatTimes.length < 4) return 0;
      const intervals = [];
      for (let i = 1; i < beatTimes.length; i++) {
        const interval = beatTimes[i] - beatTimes[i - 1];
        if (interval >= 260 && interval <= 1600) intervals.push(interval);
      }
      if (intervals.length < 3) return 0;
      intervals.sort((a, b) => a - b);
      const median = intervals[Math.floor(intervals.length / 2)];
      return Math.round(60000 / median);
    }

    function analyzeMicFrame() {
      musicAnalysis.source = 'browser';
      musicAnalysis.spectrum = frequencyData;
      analyser.getByteFrequencyData(frequencyData);
      analyser.getByteTimeDomainData(waveformData);

      let total = 0;
      let squareSum = 0;
      for (let i = 0; i < frequencyData.length; i++) total += frequencyData[i];
      for (let i = 0; i < waveformData.length; i++) {
        const centered = (waveformData[i] - 128) / 128;
        squareSum += centered * centered;
      }

      const energy = total / frequencyData.length;
      const rms = Math.sqrt(squareSum / waveformData.length);
      const bass = averageBand(frequencyData, 0.00, 0.10);
      const mid = averageBand(frequencyData, 0.10, 0.42);
      const treble = averageBand(frequencyData, 0.42, 1.00);
      const bassRatio = bass / 255;
      const midRatio = mid / 255;
      const trebleRatio = treble / 255;
      const drive = Math.max(rms * 1.7, energy / 255, bassRatio * 0.9);

      baseline = baseline * 0.93 + energy * 0.07;
      const now = performance.now();
      const transient = Math.max(0, (energy - baseline) / Math.max(18, baseline));
      const bassLift = Math.max(0, bass - Math.max(mid, treble) * 0.72) / 255;
      const beatConfidence = Math.max(0, Math.min(1, transient * 0.82 + bassLift * 0.95 + rms * 0.55));
      const beat = energy > 18 && beatConfidence > 0.52 && now - lastBeat > 145;

      if (beat) {
        lastBeat = now;
        musicAnalysis.beatTimes.push(now);
        while (musicAnalysis.beatTimes.length > 12) musicAnalysis.beatTimes.shift();
      }

      musicAnalysis.energy = energy;
      musicAnalysis.rms = rms;
      musicAnalysis.bass = bassRatio;
      musicAnalysis.mid = midRatio;
      musicAnalysis.treble = trebleRatio;
      musicAnalysis.beatConfidence = beatConfidence;
      musicAnalysis.beat = beat;
      musicAnalysis.bpm = estimateBpm(musicAnalysis.beatTimes);
      musicAnalysis.drive = Math.max(0, Math.min(1, drive));
      musicAnalysis.waveform = waveformData;
      return musicAnalysis;
    }

    function renderAnalyzerFrame(analysis) {
      updateVu(analysis.energy, analysis.beat, analysis.source === 'controller');
      const pct = (value) => `${Math.max(0, Math.min(100, Math.round(value * 100)))}%`;
      // Safe updates for elements that may have moved to hero or been removed
      const setW = (id, val) => { const e = document.getElementById(id); if (e) e.style.width = val; };
      const setT = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };

      setW('bassFill', pct(analysis.bass));
      setW('midFill', pct(analysis.mid));
      setW('trebleFill', pct(analysis.treble));
      setW('beatConfidenceFill', pct(analysis.beatConfidence));

      setT('bassReadout', pct(analysis.bass));
      setT('midReadout', pct(analysis.mid));
      setT('trebleReadout', pct(analysis.treble));
      setT('driveReadout', pct(analysis.drive));

      const bm = document.getElementById('beatMixReadout');
      if (bm) bm.textContent = analysis.beat ? 'hit' : `${Math.round(analysis.beatConfidence * 100)}%`;
      const bp = document.getElementById('bpmReadout');
      if (bp) bp.textContent = analysis.bpm ? String(analysis.bpm) : '--';

      if (analysis.source === 'controller') drawControllerLevelHistory(analysis.levelHistory || [], analysis.beat);
      else drawWaveform(analysis.waveform, analysis.beat);
      drawSpectrum(analysis);
    }

    function drawSpectrum(analysis) {
      const canvas = document.getElementById('spectrumCanvas');
      const spectrum = analysis.spectrum || frequencyData;
      if (!canvas || !spectrum || !spectrum.length) return;
      const ctx = canvas.getContext('2d');
      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);

      const bins = Math.min(24, spectrum.length);
      canvas._latestAnalysis = analysis;
      const binW = w / bins;
      const step = Math.max(1, Math.floor(spectrum.length / bins));
      
      const colors = getActiveColors();
      const isOn = colors.on && colors.bri > 0;

      for (let i = 0; i < bins; i++) {
        let sum = 0;
        const start = i * step;
        for (let k = 0; k < step; k++) sum += spectrum[Math.min(spectrum.length-1, start + k)] || 0;
        const val = sum / (step * 255);
        const barH = Math.max(3, val * h * 0.98);
        const x = i * binW + 1;

        if (!isOn) {
          ctx.fillStyle = `rgba(100, 116, 139, ${0.1 + val * 0.15})`;
          ctx.fillRect(x, h - barH, binW - 2, barH);
        } else {
          const r = Math.round(colors.raw1[0] * (1 - i / bins) + colors.raw2[0] * (i / bins));
          const g = Math.round(colors.raw1[1] * (1 - i / bins) + colors.raw2[1] * (i / bins));
          const b = Math.round(colors.raw1[2] * (1 - i / bins) + colors.raw2[2] * (i / bins));
          const alpha = 0.45 + val * 0.55;
          ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha})`;
          ctx.fillRect(x, h - barH, binW - 2, barH);
          ctx.fillStyle = 'rgba(255,255,255,.35)';
          ctx.fillRect(x, h - barH, binW - 2, 2);
        }
      }

      if (!canvas._creativeClickBound) {
        canvas._creativeClickBound = true;
        canvas.addEventListener('click', (ev) => {
          const rect = canvas.getBoundingClientRect();
          const clickBin = Math.max(0, Math.min(15, Math.floor(((ev.clientX - rect.left) / rect.width) * 16)));
          creativeBoostBin(clickBin, canvas._latestAnalysis);
        });
        canvas.title = 'Click a frequency bar to creatively influence the lights';
      }
    }

    function drawControllerLevelHistory(samples, beat) {
      const canvas = document.getElementById('waveformCanvas');
      if (!canvas) return;
      const ctx = canvas.getContext('2d'), width = canvas.width, height = canvas.height;
      ctx.clearRect(0,0,width,height); ctx.fillStyle='#05070f'; ctx.fillRect(0,0,width,height);
      ctx.strokeStyle=beat ? '#e0f2fe' : '#22d3ee'; ctx.lineWidth=2;
      ctx.beginPath();
      const values = samples.length > 1 ? samples : [samples[0] || 0, samples[0] || 0];
      values.forEach((value,i) => {
        const x=i*width/(values.length-1), y=height-3-Math.max(0,Math.min(1,value))*(height-6);
        if (i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
      });
      ctx.stroke();
    }

    function drawWaveform(samples, beat) {
      const canvas = waveformCanvas;
      const ctx = canvas.getContext('2d');
      const width = canvas.width;
      const height = canvas.height;
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = '#05070f';
      ctx.fillRect(0, 0, width, height);

      const colors = getActiveColors();
      const isOn = colors.on && colors.bri > 0;

      if (!isOn) {
        ctx.strokeStyle = 'rgba(100, 116, 139, 0.25)';
        ctx.lineWidth = 1.5;
      } else {
        ctx.strokeStyle = beat ? colors.c2 : colors.c1;
        ctx.lineWidth = beat ? 3 : 2;
      }

      ctx.beginPath();
      const step = Math.max(1, Math.floor(samples.length / width));
      for (let x = 0; x < width; x++) {
        const sample = samples[Math.min(samples.length - 1, x * step)] || 128;
        const y = (sample / 255) * height;
        if (x === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();

      ctx.strokeStyle = isOn ? 'rgba(255,255,255,.12)' : 'rgba(255,255,255,.05)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, height / 2);
      ctx.lineTo(width, height / 2);
      ctx.stroke();
    }

    // --- Creative + Capable Visualizer-driven features ---
    function steerControllerShow(updates) {
      if (!controllerVizActive() || controllerVizStatus.mode !== 'music' || !controllerVizStatus.renderer?.running) return false;
      for (const [key, value] of Object.entries(updates)) queueLiveShowControl(key, value);
      return true;
    }

    async function creativeBoostBin(binIndex, analysis) {
      if (controllerVizActive() && controllerVizStatus.mode === 'music' && controllerVizStatus.renderer?.running) {
        const band = Math.max(0, Math.min(15, Math.floor(Number(binIndex) || 0)));
        setText('liveShowMessage', `Accenting band ${band + 1}…`);
        try {
          const data = await postJson('/api/music-show', {action:'accent',band,strength:1});
          if (!data || data.ok === false) throw new Error(data?.error || 'Accent failed.');
          if (data.status) renderSmartDirectorStatus(data.status);
          setText('liveShowMessage', `Band ${band + 1} accented`);
          flashOrb(.25);
        } catch (err) { setText('liveShowMessage', 'Accent failed: ' + err.message); }
        return;
      }
      // Click spectrum bar → creatively map that frequency energy to color/effect
      const energy = (analysis && analysis.energy) || 0.6;
      const r = Math.round(60 + binIndex * 7 + energy * 80);
      const g = Math.round(30 + (binIndex % 7) * 22);
      const b = Math.round(160 - binIndex * 4 + energy * 60);
      const w = Math.round(energy * 90);
      const spd = Math.round(80 + energy * 130);
      // Send a beat-ish color + safe effect with intensity tied to the bin.
      // Goes through sendBeatUpdate (not send) so a click during reactive
      // mode doesn't stop the music-mode beat loop.
      sendBeatUpdate({ red: r, green: g, blue: b, white: w, brightness: Math.round(120 + energy*90), effect: (binIndex % 5 === 0 ? 9 : 8), speed: spd });
      flashOrb(energy);
    }

    function creativeSpectrumMap() {
      if (steerControllerShow({music_motion: 'auto', music_colorfulness: 1, music_intensity: .9})) return;
      // Take current spectrum energy distribution and turn it into a beautiful multi-color scene
      const a = musicAnalysis;
      const bri = Math.max(140, Math.round((a.energy || 0.5) * 200));
      // Map bass→warm, mids→greens/cyans, treble→pinks/blues
      const payload = {
        red: Math.round(120 + a.bass * 110),
        green: Math.round(80 + a.mid * 140),
        blue: Math.round(90 + a.treble * 150),
        white: Math.round((a.bass + a.mid) * 60),
        brightness: bri,
        effect: 67, // Colorwaves - very creative & smooth
        speed: Math.round(70 + (a.drive || 0.5) * 140)
      };
      // Beat-shaped payload (color + effect + speed in one call) via
      // sendBeatUpdate so a click during reactive mode keeps the loop alive.
      sendBeatUpdate(payload);
    }

    function creativeEnergyPulse() {
      if (steerControllerShow({music_motion: 'punch', music_intensity: 1})) { flashOrb(.3); return; }
      const a = musicAnalysis;
      const strength = Math.max(0.4, a.energy || 0.5);
      const fx = (a.bass > 0.6) ? 2 : (a.treble > 0.55 ? 8 : 12);
      sendBeatUpdate({
        red: Math.round(200 * strength), green: Math.round(80 + 90 * a.mid),
        blue: Math.round(255 * a.treble), white: Math.round(60 * strength),
        brightness: Math.round(160 + strength * 70),
        effect: fx,
        speed: Math.round(90 + strength * 120)
      });
      flashOrb(strength + 0.2);
    }

    function creativeEvolve() {
      if (steerControllerShow({music_motion: controllerVizStatus?.renderer?.motion === 'chase' ? 'flow' : 'chase',
                               music_colorfulness: 1, music_speed: 1.25})) return;
      // "Evolve" the current scene in a creative direction using analysis + a prompt to AI
      const a = musicAnalysis;
      const hint = `Current energy ${(a.energy||0).toFixed(2)}, bass ${(a.bass||0).toFixed(2)}, drive ${(a.drive||0).toFixed(2)}. Evolve the lighting into something more surprising but still tasteful and beat-reactive.`;
      const input = document.getElementById('aiInput');
      if (input) input.value = hint;
      askAI();
    }

    function flashOrb(extra = 0) {
      const orb = document.getElementById('energyOrb');
      if (!orb) return;
      orb.classList.add('beat');
      const scale = 1.0 + Math.min(0.35, extra);
      orb.style.transform = `scale(${scale})`;
      setTimeout(() => {
        if (orb) { orb.classList.remove('beat'); orb.style.transform = ''; }
      }, 280);
    }

    function sendAnalysisBeat(analysis) {
      const now = performance.now();
      if (!analysis.beat || now - lastWledBeatSent < 170) return;
      lastWledBeatSent = now;
      const color = palette[paletteIndex++ % palette.length];
      const effect = beatEffects[effectIndex++ % beatEffects.length];
      const brightness = Math.max(90, Math.min(255, Math.round(80 + analysis.drive * 175)));
      const speed = Math.max(96, Math.min(255, Math.round(90 + analysis.beatConfidence * 110 + analysis.treble * 55)));
      sendBeatUpdate({red: color[0], green: color[1], blue: color[2], white: color[3], brightness, effect, speed});
    }

    function resetAnalysisDisplay() {
      Object.assign(musicAnalysis, {source:'browser', spectrum:Array(16).fill(0), energy: 0, rms: 0, bass: 0, mid: 0, treble: 0, beatConfidence: 0, beat: false, bpm: 0, drive: 0, waveform: [], beatTimes: []});
      renderAnalyzerFrame(musicAnalysis);
    }

    function analyzeAudio() {
      if (!audioReactiveRunning) return;
      const analysis = analyzeMicFrame();
      renderAnalyzerFrame(analysis);
      sendAnalysisBeat(analysis);
      rafId = requestAnimationFrame(analyzeAudio);
    }

    document.addEventListener('visibilitychange', () => {
      if (document.hidden && audioReactiveRunning) {
        if (rafId) cancelAnimationFrame(rafId);
        rafId = null;
        if (audioContext && audioContext.state === 'running') audioContext.suspend();
      } else if (!document.hidden && audioReactiveRunning && !rafId) {
        if (audioContext && audioContext.state === 'suspended') audioContext.resume();
        analyzeAudio();
      }
    });

    function setColor(red, green, blue, white) {
      r.value = red; g.value = green; b.value = blue; w.value = white;
      send('color', {red, green, blue, white, transition: Number(transition.value)});
    }

    function setCustom() {
      setColor(Number(r.value), Number(g.value), Number(b.value), Number(w.value));
    }

    async function addSchedule() {
      const timeStr = schedTime.value;
      const action = schedAction.value;
      const sceneName = schedScene.value;
      if (!timeStr) { status.textContent = 'Please select a time.'; return; }
      await send('schedule', {subaction: 'add', time: timeStr, action, scene_name: sceneName});
      listSchedule();
    }

    async function listSchedule() {
      try {
        const res = await fetch('/api/action', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({action: 'schedule', subaction: 'list'})
        });
        const data = await res.json();
        if (data.ok && data.entries) {
          scheduleList.textContent = data.entries.length ? data.entries.map((e,i) => `${i}: ${e.time} -> ${e.action} ${e.data.scene||''}`).join('\\n') : 'No schedules.';
        } else {
          scheduleList.textContent = data.error || 'No schedules.';
        }
      } catch (err) {
        scheduleList.textContent = 'Error loading schedules.';
      }
    }

    function updateAutonomousStatus(auto) {
      if (controllerVizActive()) return;
      const autoSt = document.getElementById('autoStatus');
      const running = !!(auto && auto.running);
      if (!running) {
        if (autoSt && !musicModeRunning) autoSt.textContent = 'Inactive';
        return;
      }
      let text = '🔍 Listening for music...';
      let color = '#ffd43b';
      if (auto.quiet) {
        text = '🌙 Ambient — quiet detected';
        color = '#aaa';
      } else if (auto.song && auto.song.title) {
        const genre = auto.song.genre ? ` · ${auto.song.genre}` : '';
        text = `🎵 ${auto.song.title} — ${auto.song.artist || ''}${genre}`;
        color = 'var(--success)';
      }
      if (autoSt) { autoSt.textContent = text; autoSt.style.color = color; }
    }

    function updateMoodStatus(mood) {
      if (controllerVizActive()) return;
      if (!mood || !mood.running) return;
      const st = document.getElementById('autoStatus');
      const songSource = document.getElementById('songSourceState');
      const nextMatch = document.getElementById('nextMatchState');
      if (songSource) songSource.textContent = 'Webcam mic';
      if (nextMatch) nextMatch.textContent = 'On song change';
      if (!st) return;
      const details = [];
      if (mood.last_error) details.push(`Error: ${mood.last_error}`);
      if (mood.last_cache_hit === true) details.push('cached mood');
      if (mood.last_cache_hit === false) details.push('fresh mood');
      if ((!mood.recognition || mood.recognition.mode !== 'on_change') && typeof mood.next_recognition_in === 'number' && mood.next_recognition_in > 0) {
        details.push(`next check ${Math.ceil(mood.next_recognition_in)}s`);
      }
      const suffix = details.length ? ` (${details.join(' · ')})` : '';
      if (mood.state === 'ambient') {
        st.textContent = `No music detected — ambient fallback active.${suffix}`;
      } else if (mood.state === 'recognized' && mood.song) {
        const t = mood.song.title || 'song';
        const a = mood.song.artist || 'unknown artist';
        st.textContent = `Matched: ${t} by ${a}${suffix}`;
      } else {
        st.textContent = `Listening for music…${suffix}`;
      }
    }

    async function restartController() {
      if (!confirm('Reboot the WLED controller? It will be offline for a few seconds.')) return;
      status.textContent = 'Restarting controller...';
      connIndicator.textContent = '🟡';
      connText.textContent = 'Restarting...';
      await send('restart');
    }

    const effectNameMap = __EFFECT_NAME_MAP__;
    const STRIP_SEGMENTS = 120;
    let currentStripState = null;
    let stripRafId = null;

    function initLedStrip() {
      ledStrip.innerHTML = '';
      for (let i = 0; i < STRIP_SEGMENTS; i++) {
        const seg = document.createElement('div');
        seg.className = 'led-segment';
        ledStrip.appendChild(seg);
      }
    }

    function rgbToHsl(r, g, b) {
      r /= 255; g /= 255; b /= 255;
      const max = Math.max(r, g, b), min = Math.min(r, g, b);
      let h = 0, s = 0, l = (max + min) / 2;
      if (max !== min) {
        const d = max - min;
        s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
        switch (max) {
          case r: h = (g - b) / d + (g < b ? 6 : 0); break;
          case g: h = (b - r) / d + 2; break;
          case b: h = (r - g) / d + 4; break;
        }
        h /= 6;
      }
      return [h, s, l];
    }

    function hslToRgb(h, s, l) {
      let r, g, b;
      if (s === 0) {
        r = g = b = l;
      } else {
        const hue2rgb = (p, q, t) => {
          if (t < 0) t += 1;
          if (t > 1) t -= 1;
          if (t < 1 / 6) return p + (q - p) * 6 * t;
          if (t < 1 / 2) return q;
          if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
          return p;
        };
        const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
        const p = 2 * l - q;
        r = hue2rgb(p, q, h + 1 / 3);
        g = hue2rgb(p, q, h);
        b = hue2rgb(p, q, h - 1 / 3);
      }
      return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
    }

    function interpolateColor(c1, c2, t) {
      return c1.map((v, i) => Math.round(v + (c2[i] - v) * t));
    }

    function scaleBrightness(color, factor) {
      return color.map(v => Math.round(v * factor));
    }

    function rgbwToCss(r, g, b, w, bri, ledInfo) {
      const brightness = Math.max(0, Math.min(1, (bri || 0) / 255));
      let ww = 0;
      let hasWhite = true;
      if (ledInfo) {
        // respect device capabilities so the visualizer matches the actual LEDs on the WLED controller
        if (ledInfo.rgbw === false || ledInfo.wv === false) {
          hasWhite = false;
        }
        if (typeof ledInfo.lc === 'number') {
          // WLED lc bitfield: 0x01=RGB, 0x02=W, 0x04=CCT
          if ((ledInfo.lc & 0x02) === 0) {
            hasWhite = false;
          }
        }
      }
      if (hasWhite) {
        ww = Math.max(0, Math.min(255, Math.round(((w || 0) * brightness))));
      }
      const rr = Math.min(255, Math.round(((r || 0) * brightness) + ww));
      const gg = Math.min(255, Math.round(((g || 0) * brightness) + ww));
      const bb = Math.min(255, Math.round(((b || 0) * brightness) + ww));
      return `rgb(${rr}, ${gg}, ${bb})`;
    }

    function getSpeedMultiplier(sx) {
      // sx 0 -> very slow, 128 -> normal, 255 -> fast
      return 0.15 + (Math.max(0, Math.min(255, sx || 128)) / 255) * 2.85;
    }

    function renderSegment(i, t, state) {
      const seg = state.seg || {};
      const fx = seg.fx ?? 0;
      const sx = seg.sx ?? 128;
      const colors = state.colors || [[255, 255, 255, 0]];
      const segments = state.segments || 1;
      const speed = getSpeedMultiplier(sx);
      const pos = (t * speed) % 1;
      const c1 = colors[0] || [0, 0, 0, 0];
      const c2 = colors[1] || c1;
      const c3 = colors[2] || c1;
      const ratio = i / (segments - 1 || 1);

      // === Always base colors on the live state col[] from the controller ===
      // This ensures the visualizer shows the *exact colors* the device was told to use
      // (or is currently configured with in the polled state).
      // Motion is a secondary visual cue only.

      // Solid: exact match to commanded color
      if (fx === 0) {
        return c1;
      }

      // Simple categories that mix the configured colors with time/position
      const isColorCycle = [8, 9, 12, 63, 67, 108, 163, 179, 183].includes(fx); // colorloop, rainbow, fade, colorwaves etc.
      const isChase = [28, 30, 33, 37, 52, 54, 90, 92].includes(fx);
      const isPulse = [2, 62, 105, 115, 162, 172].includes(fx); // breathe, oscillate, sine, drift

      if (isColorCycle) {
        // Mix between the configured c1 and c2 based on effect position
        const phase = 0.5 + 0.5 * Math.sin((ratio * 2 - pos * 1.5) * Math.PI * 2);
        return interpolateColor(c1, c2, phase);
      }

      if (isChase) {
        // Moving "head" of brighter c1 / c2 on a dimmed base of c1
        const dist = Math.min(Math.abs(ratio - pos), 1 - Math.abs(ratio - pos));
        const head = dist < 0.08;
        const base = scaleBrightness(c1, 0.25);
        if (head) {
          return scaleBrightness(c1, 1.1);  // bright head using the actual c1
        }
        const tail = Math.max(0.2, 1 - dist * 5);
        return scaleBrightness(c1, tail);
      }

      if (isPulse) {
        const pulse = 0.4 + 0.6 * Math.sin((ratio * 3 + t * speed * 2) * Math.PI);
        return scaleBrightness(c1, pulse);
      }

      // Default / everything else: use the actual primary color from state with gentle motion shimmer
      // Guarantees the LED colors in the visualizer match the colors the controller has
      const shimmer = 0.65 + 0.35 * Math.sin((ratio * 4 - t * speed * 1.8) * Math.PI);
      return scaleBrightness(c1, shimmer);
    }

    function updateStripFrame(timestamp) {
      const pixels = controllerPreviewColors();
      if (pixels.length) {
        ledStrip.classList.remove('off');
        Array.from(ledStrip.children).forEach((child,i) => {
          const rgb = pixels[Math.min(pixels.length-1, Math.floor(i*pixels.length/ledStrip.children.length))];
          const css = `rgb(${rgb.join(',')})`;
          child.style.backgroundColor=css; child.style.boxShadow=`0 0 6px ${css}`;
        });
        const message = 'Live DDP preview · controller mic · actual rendered colors';
        if (lightInfo.textContent !== message) lightInfo.textContent=message;
        stripRafId=requestAnimationFrame(updateStripFrame); return;
      }
      if (!currentStripState || !currentStripState.on) {
        ledStrip.classList.add('off');
        stripRafId = requestAnimationFrame(updateStripFrame);
        return;
      }

      ledStrip.classList.remove('off');
      const t = timestamp / 1000;
      const children = ledStrip.children;
      const bri = currentStripState.bri ?? 255;
      for (let i = 0; i < children.length; i++) {
        const [r, g, b, w] = renderSegment(i, t, currentStripState);
        const css = rgbwToCss(r, g, b, w, bri, currentStripState.ledInfo);
        children[i].style.backgroundColor = css;
        children[i].style.boxShadow = `0 0 6px ${css}`;
      }
      stripRafId = requestAnimationFrame(updateStripFrame);
    }

    function updateLightPreview(st, ledInfo, segId = 0) {
      // Channel targets map to a specific segment; fall back to the first.
      const segs = Array.isArray(st.seg) ? st.seg : [];
      const seg = segs[segId] || segs[0] || {};
      function normCol(c) {
        if (!c) return [0, 0, 0, 0];
        const out = c.slice ? c.slice(0, 4) : [c[0] || 0, c[1] || 0, c[2] || 0, c[3] || 0];
        while (out.length < 4) out.push(0);
        return out;
      }
      const colors = [
        normCol(seg.col && seg.col[0]),
        normCol(seg.col && seg.col[1]),
        normCol(seg.col && seg.col[2]),
      ];
      if (ledInfo) {
        const hasW = ledInfo.rgbw !== false && (typeof ledInfo.lc !== 'number' || (ledInfo.lc & 0x02) !== 0);
        if (!hasW) {
          for (let c of colors) if (c) c[3] = 0;
        }
      }
      currentStripState = {
        on: !!st.on,
        bri: st.bri ?? 0,
        fx: seg.fx ?? 0,
        sx: seg.sx ?? 128,
        ix: seg.ix ?? 128,
        seg: seg,
        colors: colors,
        segments: STRIP_SEGMENTS,
        ledInfo: ledInfo || (currentStripState && currentStripState.ledInfo) || null,
      };

      const on = currentStripState.on;
      const bri = currentStripState.bri;
      const fx = currentStripState.fx;
      const sx = currentStripState.sx;
      const effectName = effectNameMap[String(fx)] || `Effect ${fx}`;
      const [r, g, b, w] = colors[0];
      let colorInfo = `RGBW(${r},${g},${b},${w})`;
      if (colors[1] && (colors[1][0] || colors[1][1] || colors[1][2] || colors[1][3])) {
        colorInfo += ` | 2:RGBW(${colors[1].join(',')})`;
      }
      if (colors[2] && (colors[2][0] || colors[2][1] || colors[2][2] || colors[2][3])) {
        colorInfo += ` | 3:RGBW(${colors[2].join(',')})`;
      }

      let extra = '';
      if (seg.grp != null || seg.spc != null) extra += ` Grp:${seg.grp||1} Spc:${seg.spc||0}`;
      if (seg.cct != null) extra += ` CCT:${seg.cct}`;
      if (seg.on !== undefined) extra += ` SegOn:${seg.on}`;

      lightInfo.innerHTML =
        `<span class="label">${on ? 'ON' : 'OFF'}</span>` +
        `<span class="label">Bri ${bri}</span>` +
        `<span class="label">${fx === 0 ? 'Solid' : effectName}</span>` +
        (fx !== 0 ? `<span class="label">Spd ${sx}</span>` : '') +
        `<br><span style="color:#666;font-size:11px;">${colorInfo}${extra}</span>`;
    }

    // SSE state updates — polls the device every 2 seconds
    const evtSource = new EventSource('/api/events');
    evtSource.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        updateAutonomousStatus(payload.autonomous || null);
        if (payload.mood) {
          updateMoodStatus(payload.mood);
        }
        if (payload.state) {
          applyFleetInfo(payload);
          const health = updateConnectionStatusFromState(payload.state);
          const picked = displayState(payload.state);
          const st = picked.state;
          const onOff = st.on ? 'ON' : 'OFF';
          const bri = st.bri ?? '?';
          const seg = st.seg && st.seg[0] ? st.seg[0] : {};
          const col = seg.col && seg.col[0] ? seg.col[0] : [0,0,0,0];
          let display = `Power: ${onOff}\\nBrightness: ${bri}\\nColor: RGBW(${col.join(',')})\\nEffect: ${seg.fx ?? '-'} Speed: ${seg.sx ?? '-'}`;
          if (picked.name) display = `Controller: ${picked.name}\\n` + display;
          if (payload.intel && Object.keys(payload.intel).length > 0) {
            const i = payload.intel;
            if (i.leds) ledCapabilities = i.leds;
            if (i.ver) display += `\\nVer: ${i.ver}`;
            if (i.leds && i.leds.count) display += `\\nLEDs: ${i.leds.count}`;
            if (i.fps) display += ` FPS:${i.fps}`;
            if (i.pwr) display += ` Pwr:${i.pwr}mA`;
            if (i.ps != null) display += `\\nPreset: ${i.ps}`;
            if (i.pl != null) display += ` Playlist: ${i.pl}`;
            if (i.cct != null) display += ` CCT:${i.cct}`;
            if (i.wifi && i.wifi.signal) display += `\\nWiFi: ${i.wifi.signal}%`;
            if (i.uptime) display += ` Uptime:${Math.floor(i.uptime/60)}m`;
          }
          stateDisplay.textContent = display;
          const controllerSummary = health.fleet
            ? ` | ${health.connected.length}/${health.total} controllers`
            : '';
          stateSummary.textContent = `${onOff} | Bri ${bri} | Fx ${seg.fx ?? '-'} @ ${seg.sx ?? '-'}${controllerSummary}`;
          updateLightPreview(st, ledCapabilities, picked.segId);
        } else if (payload.error) {
          stateDisplay.textContent = 'Device offline — reconnecting...';
          stateSummary.textContent = '--';
          ledStrip.classList.add('off');
          currentStripState = { on: false, bri: 0, colors: [[0,0,0,0]], segments: STRIP_SEGMENTS, seg: {}, fx: 0, sx: 128, ix: 128, ledInfo: currentStripState ? currentStripState.ledInfo : null };
          lightInfo.innerHTML = '<span class="label">Disconnected</span>';
          connIndicator.textContent = '🔴';
          connText.textContent = 'Disconnected';
        }
      } catch (e) {
        stateDisplay.textContent = 'State update error';
        connIndicator.textContent = '🔴';
        connText.textContent = 'Disconnected';
      }
    };
    evtSource.onerror = () => {
      stateDisplay.textContent = 'State connection lost. Retrying...';
      connIndicator.textContent = '🔴';
      connText.textContent = 'Disconnected';
    };

    // ---------- Settings tab ----------
    const AI_PROVIDER_PRESETS = {
      openai: 'https://api.openai.com/v1',
      openrouter: 'https://openrouter.ai/api/v1',
      ollama: 'http://localhost:11434/v1',
      custom: ''
    };

    function setMsg(el, text, kind) {
      if (!el) return;
      el.textContent = text || '';
      el.classList.remove('ok', 'err', 'info');
      if (text) el.classList.add(kind || 'info');
    }

    async function postJson(url, body) {
      return fetchJsonWithTimeout(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body || {})
      });
    }

    function aiProviderChanged() {
      const preset = AI_PROVIDER_PRESETS[document.getElementById('setAiProvider').value];
      if (preset) document.getElementById('setAiBaseUrl').value = preset;
    }

    async function loadSettings() {
      const aiMsg = document.getElementById('aiSettingsMsg');
      try {
        const data = await fetchJsonWithTimeout('/api/settings');
        const s = (data && data.settings) || {};
        const ai = s.ai || {};
        const knownProviders = ['openai', 'openrouter', 'ollama', 'custom'];
        document.getElementById('setAiProvider').value = knownProviders.includes(ai.provider) ? ai.provider : 'custom';
        document.getElementById('setAiBaseUrl').value = ai.base_url || '';
        document.getElementById('setAiModel').value = ai.model || '';
        document.getElementById('setAiVisionModel').value = ai.vision_model || '';
        document.getElementById('setAiKeyEnv').value = ai.api_key_env || '';
        const agents = ai.agents || {};
        const colorist = agents.colorist || {};
        const motion = agents.motion || {};
        const critic = agents.critic || {};
        document.getElementById('agentColoristEnabled').checked = !!colorist.enabled;
        document.getElementById('agentColoristModel').value = colorist.model || '';
        document.getElementById('agentColoristProvider').value = colorist.provider || '';
        document.getElementById('agentColoristBaseUrl').value = colorist.base_url || '';
        document.getElementById('agentMotionEnabled').checked = !!motion.enabled;
        document.getElementById('agentMotionModel').value = motion.model || '';
        document.getElementById('agentMotionProvider').value = motion.provider || '';
        document.getElementById('agentMotionBaseUrl').value = motion.base_url || '';
        document.getElementById('agentCriticEnabled').checked = !!critic.enabled;
        document.getElementById('agentCriticModel').value = critic.model || '';
        document.getElementById('agentCriticProvider').value = critic.provider || '';
        document.getElementById('agentCriticBaseUrl').value = critic.base_url || '';
        const badge = document.getElementById('setAiKeyBadge');
        badge.textContent = ai.api_key_set ? 'API key: set ✓' : 'API key: not set';
        badge.classList.toggle('set', !!ai.api_key_set);
        const rows = document.getElementById('controllerRows');
        rows.innerHTML = '';
        (Array.isArray(s.controllers) ? s.controllers : []).forEach(c => addControllerRow(c));
        if (!rows.children.length) addControllerRow();
        const knownSources = ['monitor', 'mic', 'custom', 'wled_mic'];
        document.getElementById('setAudioSource').value = knownSources.includes(s.audio_source) ? s.audio_source : 'custom';
        document.getElementById('setMicDevice').value = s.mic_device || '';
        audioSourceChanged();
        const player = s.audio_player || {};
        const enabledBox = document.getElementById('setPlayerEnabled');
        if (enabledBox) enabledBox.checked = player.enabled !== false;
        applyPlayerEnabled(player.enabled);
        const ytHost = document.getElementById('setYoutubeHost');
        if (ytHost) ytHost.value = player.youtube_host || '';
        const appleBadge = document.getElementById('setAppleTokenBadge');
        if (appleBadge) {
          appleBadge.textContent = player.apple_developer_token_set ? (player.apple_authorized ? 'Apple: token + signed in' : 'Apple token: set') : 'Apple token: not set';
          appleBadge.classList.toggle('set', !!player.apple_developer_token_set);
        }
        await loadSystemPrompt();
        setMsg(aiMsg, '', 'info');
      } catch (e) {
        setMsg(aiMsg, 'Failed to load settings: ' + e.message, 'err');
      }
    }

    async function saveAiSettings(btn) {
      const msg = document.getElementById('aiSettingsMsg');
      const ai = {
        provider: document.getElementById('setAiProvider').value,
        base_url: document.getElementById('setAiBaseUrl').value.trim(),
        model: document.getElementById('setAiModel').value.trim(),
        vision_model: document.getElementById('setAiVisionModel').value.trim(),
        api_key_env: document.getElementById('setAiKeyEnv').value.trim(),
        agents: {
          colorist: {
            enabled: document.getElementById('agentColoristEnabled').checked,
            model: document.getElementById('agentColoristModel').value.trim(),
            provider: document.getElementById('agentColoristProvider').value.trim(),
            base_url: document.getElementById('agentColoristBaseUrl').value.trim()
          },
          motion: {
            enabled: document.getElementById('agentMotionEnabled').checked,
            model: document.getElementById('agentMotionModel').value.trim(),
            provider: document.getElementById('agentMotionProvider').value.trim(),
            base_url: document.getElementById('agentMotionBaseUrl').value.trim()
          },
          critic: {
            enabled: document.getElementById('agentCriticEnabled').checked,
            model: document.getElementById('agentCriticModel').value.trim(),
            provider: document.getElementById('agentCriticProvider').value.trim(),
            base_url: document.getElementById('agentCriticBaseUrl').value.trim()
          }
        }
      };
      btn.disabled = true;
      setMsg(msg, 'Saving...', 'info');
      try {
        const data = await postJson('/api/settings', {ai: ai});
        setMsg(msg, data.ok ? 'AI settings saved.' : (data.error || 'Save failed.'), data.ok ? 'ok' : 'err');
      } catch (e) {
        setMsg(msg, 'Error: ' + e.message, 'err');
      } finally {
        btn.disabled = false;
      }
    }

    async function testAiConnection(btn) {
      const msg = document.getElementById('aiSettingsMsg');
      btn.disabled = true;
      setMsg(msg, 'Testing connection...', 'info');
      try {
        const data = await postJson('/api/ai/test', {});
        setMsg(msg, data.message || (data.ok ? 'Connection OK.' : 'Connection failed.'), data.ok ? 'ok' : 'err');
      } catch (e) {
        setMsg(msg, 'Error: ' + e.message, 'err');
      } finally {
        btn.disabled = false;
      }
    }

    async function loadSystemPrompt() {
      const data = await fetchJsonWithTimeout('/api/system-prompt');
      if (data && data.ok) {
        document.getElementById('setSysPrompt').value = data.current || '';
        document.getElementById('setSysDefault').textContent = data.default || '';
      }
    }

    async function saveSystemPrompt(btn) {
      const msg = document.getElementById('sysPromptMsg');
      btn.disabled = true;
      setMsg(msg, 'Saving...', 'info');
      try {
        const data = await postJson('/api/system-prompt', {prompt: document.getElementById('setSysPrompt').value});
        setMsg(msg, data.ok ? 'Custom prompt saved.' : (data.error || 'Save failed.'), data.ok ? 'ok' : 'err');
      } catch (e) {
        setMsg(msg, 'Error: ' + e.message, 'err');
      } finally {
        btn.disabled = false;
      }
    }

    async function resetSystemPrompt(btn) {
      const msg = document.getElementById('sysPromptMsg');
      btn.disabled = true;
      setMsg(msg, 'Resetting...', 'info');
      try {
        const data = await postJson('/api/system-prompt', {prompt: null});
        if (data.ok) {
          await loadSystemPrompt();
          setMsg(msg, 'Reset to default.', 'ok');
        } else {
          setMsg(msg, data.error || 'Reset failed.', 'err');
        }
      } catch (e) {
        setMsg(msg, 'Error: ' + e.message, 'err');
      } finally {
        btn.disabled = false;
      }
    }

    function addControllerRow(c) {
      const rows = document.getElementById('controllerRows');
      const row = document.createElement('div');
      row.className = 'controller-row';
      const dot = document.createElement('span');
      dot.className = 'dot';
      dot.title = 'Not verified yet';
      const name = document.createElement('input');
      name.type = 'text';
      name.className = 'ctl-name';
      name.placeholder = 'name';
      name.value = c && c.name ? c.name : '';
      const host = document.createElement('input');
      host.type = 'text';
      host.className = 'ctl-host';
      host.placeholder = '192.168.1.50';
      host.value = c && c.host ? c.host : '';
      const segs = document.createElement('input');
      segs.type = 'text';
      segs.className = 'ctl-segments';
      segs.placeholder = 'segments JSON';
      segs.value = c && c.segments != null ? JSON.stringify(c.segments) : '';
      const info = document.createElement('span');
      info.className = 'ctl-info';
      const rm = document.createElement('button');
      rm.className = 'danger ctl-remove';
      rm.textContent = '✕';
      rm.title = 'Remove this controller';
      rm.onclick = () => row.remove();
      row.append(dot, name, host, segs, info, rm);
      rows.appendChild(row);
    }

    async function verifyControllers(btn) {
      const msg = document.getElementById('controllersMsg');
      btn.disabled = true;
      setMsg(msg, 'Verifying connections...', 'info');
      try {
        const data = await postJson('/api/controllers/verify', {});
        const results = (data && data.results) || {};
        document.querySelectorAll('#controllerRows .controller-row').forEach(row => {
          const name = row.querySelector('.ctl-name').value.trim();
          const dot = row.querySelector('.dot');
          const info = row.querySelector('.ctl-info');
          const r = results[name];
          dot.classList.remove('ok', 'fail');
          if (!r) {
            info.textContent = '';
            dot.title = 'Not in saved config';
            return;
          }
          if (r.ok) {
            dot.classList.add('ok');
            dot.title = 'OK';
            info.textContent = 'WLED ' + (r.version || '?') + (r.effects != null ? ' · ' + r.effects + ' effects' : '');
          } else {
            dot.classList.add('fail');
            dot.title = r.error || 'failed';
            info.textContent = r.error || 'unreachable';
          }
        });
        setMsg(msg, data.ok ? 'Verification complete.' : (data.error || 'Verification failed.'), data.ok ? 'ok' : 'err');
      } catch (e) {
        setMsg(msg, 'Error: ' + e.message, 'err');
      } finally {
        btn.disabled = false;
      }
    }

    async function saveControllers(btn) {
      const msg = document.getElementById('controllersMsg');
      const controllers = [];
      const rows = document.querySelectorAll('#controllerRows .controller-row');
      for (const row of rows) {
        const name = row.querySelector('.ctl-name').value.trim();
        const host = row.querySelector('.ctl-host').value.trim();
        const segsRaw = row.querySelector('.ctl-segments').value.trim();
        if (!name && !host) continue;
        let segments = null;
        if (segsRaw) {
          try {
            segments = JSON.parse(segsRaw);
          } catch (e) {
            setMsg(msg, 'Invalid segments JSON for "' + (name || host) + '".', 'err');
            return;
          }
        }
        controllers.push({name: name, host: host, segments: segments});
      }
      btn.disabled = true;
      setMsg(msg, 'Saving...', 'info');
      try {
        const saved = await postJson('/api/settings', {controllers: controllers});
        if (!saved.ok) {
          setMsg(msg, saved.error || 'Save failed.', 'err');
          return;
        }
        setMsg(msg, 'Saved. Reloading fleet...', 'info');
        const reloaded = await postJson('/api/settings/reload', {});
        if (reloaded.ok) {
          const names = (reloaded.controllers || []).join(', ') || 'none';
          setMsg(msg, 'Saved & reloaded: ' + names, 'ok');
        } else {
          setMsg(msg, reloaded.error || 'Reload failed.', 'err');
        }
      } catch (e) {
        setMsg(msg, 'Error: ' + e.message, 'err');
      } finally {
        btn.disabled = false;
      }
    }

    function audioSourceChanged() {
      const v = document.getElementById('setAudioSource').value;
      document.getElementById('micDeviceRow').style.display = (v === 'monitor' || v === 'wled_mic') ? 'none' : '';
    }

    async function savePlayerSettings(btn) {
      const msg = document.getElementById('playerSettingsMsg');
      const audio_player = {
        enabled: (document.getElementById('setPlayerEnabled') || {}).checked !== false
      };
      // Minimal card (player disabled) has no host/token inputs — only send what's there.
      const youtubeHost = (document.getElementById('setYoutubeHost') || {}).value;
      if (typeof youtubeHost === 'string') audio_player.youtube_host = youtubeHost.trim();
      const token = ((document.getElementById('setAppleDevToken') || {}).value || '').trim();
      if (token) audio_player.apple_developer_token = token;
      btn.disabled = true;
      setMsg(msg, 'Saving...', 'info');
      try {
        const data = await postJson('/api/settings', {audio_player});
        setMsg(msg, data.ok ? 'Player settings saved.' : (data.error || 'Save failed.'), data.ok ? 'ok' : 'err');
        if (data.ok) {
          const tokenInput = document.getElementById('setAppleDevToken');
          if (tokenInput) tokenInput.value = '';
          await loadSettings();
        }
      } catch (e) {
        setMsg(msg, 'Error: ' + e.message, 'err');
      } finally {
        btn.disabled = false;
      }
    }

    async function saveAudioSettings(btn) {
      const msg = document.getElementById('audioMsg');
      const body = {
        audio_source: document.getElementById('setAudioSource').value,
        mic_device: document.getElementById('setMicDevice').value.trim() || null
      };
      btn.disabled = true;
      setMsg(msg, 'Saving...', 'info');
      try {
        const data = await postJson('/api/settings', body);
        setMsg(msg, data.ok ? 'Audio settings saved.' : (data.error || 'Save failed.'), data.ok ? 'ok' : 'err');
      } catch (e) {
        setMsg(msg, 'Error: ' + e.message, 'err');
      } finally {
        btn.disabled = false;
      }
    }

    // Init
    initLedStrip();
    stripRafId = requestAnimationFrame(updateStripFrame);
    loadChatHistory();
    loadModelResponses();
    refreshSmartSuggestions();
    setInterval(refreshSmartSuggestions, 30000);
    refreshPlaybackClock();
    setInterval(refreshPlaybackClock, 5000);
    setInterval(renderPlaybackClock, 1000);
    listSchedule();
    loadFiretvState();
    refreshTvObservation();
    loadSmartDirector();
    setInterval(pollSmartDirector, 100);
    setInterval(expireControllerVisualization, 500);
    loadMusicDirectorState();

    // Extra wiring for top visualizers + creative features
    const orbEl2 = document.getElementById('energyOrb');
    if (orbEl2) {
      orbEl2.onclick = () => creativeEnergyPulse();
      orbEl2.style.cursor = 'pointer';
    }
    const spec = document.getElementById('spectrumCanvas');
    if (spec) { spec.width = 720; spec.height = 78; }
    const wv = document.getElementById('waveformCanvas');
    if (wv) { wv.height = Math.max(wv.height || 110, 110); }

    const vz = document.getElementById('vizStatus');
    if (vz) {
      setInterval(() => {
        if (controllerVizActive()) return;
        if (audioReactiveRunning) vz.textContent = '🎵 live reactive';
        else vz.textContent = audioReactiveRunning || musicModeRunning ? 'active' : 'idle';
      }, 900);
    }
    const sourceParam = new URLSearchParams(location.search).get('source');
    if (sourceParam && document.getElementById('playerSource')) {
      document.getElementById('playerSource').value = sourceParam;
    }
    document.getElementById('calibrationDialog').addEventListener('close', stopCalibrationCamera);
    document.getElementById('calibrationDialog').addEventListener('cancel', stopCalibrationCamera);
    window.addEventListener('beforeunload', stopCalibrationCamera);
    // Player bootstrap: respect the enabled flag before polling.
    (async () => {
      try {
        const data = await fetchJsonWithTimeout('/api/settings', {}, 5000);
        const playerCfg = ((data || {}).settings || {}).audio_player || {};
        applyPlayerEnabled(playerCfg.enabled);
      } catch (err) { /* player stays enabled by default */ }
      if (!playerEnabled) return;
      const status = await refreshPlayerStatus();
      loadPlayerPlaylists();
      loadLocalLibrary();
      playerPollTimer = setInterval(refreshPlayerStatus, 4000);
      if (currentPlayerSource() === 'apple_music' && !status?.desktop) ensureApplePairing();
    })();
  </script>
</body>
</html>
"""

PLAYER_STAGE_ENABLED = """    <section class="visualizer-hero player-stage" id="playerStage">
      <div class="player-stage-main">
        <div class="player-art" id="playerArtWrap">
          <div class="player-art-fallback" aria-hidden="true">♪</div>
          <img class="album-art" id="albumArt" alt="Album art">
        </div>
        <div class="player-now">
          <div class="player-kicker">Audio Player</div>
          <h2 class="player-title" id="musicTitle">Nothing playing</h2>
          <p class="player-artist" id="musicArtist"></p>
          <span class="music-genre" id="musicGenre" style="display:none;"></span>
          <div class="player-transport">
            <select id="playerSource" onchange="onPlayerSourceChange()" title="Music source">
              <option value="youtube_music">YouTube Music</option>
              <option value="apple_music">Apple Music</option>
            </select>
            <button onclick="playerCommand('previous')" title="Previous track">⏮</button>
            <button class="player-play" id="playerPlayBtn" onclick="playerCommand('playPause')" title="Play or pause">▶</button>
            <button onclick="playerCommand('next')" title="Next track">⏭</button>
            <label style="display:flex;align-items:center;gap:6px;">Seek <input id="playerSeek" type="range" min="0" max="0" step="1" value="0" disabled onchange="playerCommand('seek',{data:{position:Number(this.value)}})" aria-label="Track position"></label>
            <label style="display:flex;align-items:center;gap:6px;">Volume <input id="playerVolume" type="range" min="0" max="1" step="0.01" value="0.7" onchange="playerCommand('volume',{data:{volume:Number(this.value)}})" aria-label="Set player volume"></label>
            <button class="secondary" onclick="matchLightsFromNowPlaying()" title="Apply a song-aware light mood once">Apply Mood</button>
          </div>
          <div class="status-output" id="playerNowPlaying">Idle.</div>
          <span class="now-playing" id="nowPlaying"></span>
        </div>
        <aside class="player-auth" id="appleAuthPanel" hidden>
          <div id="appleSignedIn" hidden>
            <p class="player-kicker">Apple ID</p>
            <p style="margin:6px 0 10px;font-size:13px;">Signed in. Playback stays on this controller.</p>
            <button class="secondary" onclick="signOutApple()" title="Forget the Apple Music user token">Sign out</button>
          </div>
          <div id="applePairing">
            <p class="player-kicker" style="text-align:center;">Sign in with Apple</p>
            <div id="appleQr" class="apple-qr" aria-label="Apple ID login QR code"></div>
            <p class="player-code">Code <strong id="applePairCode">······</strong></p>
            <p class="player-hint">Scan with your phone, then sign in with your Apple ID. The wall keeps this session.</p>
            <a id="appleLoginLink" href="/apple-login" target="_blank" rel="noopener">Open sign-in on this device</a>
          </div>
        </aside>
      </div>

      <div class="full-bleed-preview" style="width:100%; margin:0; margin-left:0; margin-right:0; border:none; box-shadow:none; padding:3px 0 1px; background:transparent;">
        <div class="strip-wrapper">
          <div class="led-strip off" id="ledStrip" style="height: clamp(24px, 3.5vw, 45px);"></div>
        </div>
        <div class="preview-meta" style="margin-top:1px;">
          <div class="light-info" id="lightInfo">Waiting for state...</div>
        </div>
      </div>

      <!-- Audio Visualizers row: Waveform + Spectrum + Energy/Beat orb -->
      <div class="viz-row">
        <div class="viz-panel">
          <div class="viz-label" id="waveformLabel">WAVEFORM + BEAT</div>
          <canvas class="waveform-canvas" id="waveformCanvas" width="680" height="50" style="height:50px;"></canvas>
          <div style="display:flex; gap:8px; align-items:center; margin-top:4px;" id="vuMeter">
            <div class="meter" style="flex:1;height:14px;">
              <div class="meter-fill" id="vuFill"></div>
              <div class="meter-peak" id="vuPeak"></div>
            </div>
            <div class="beat-lamp" id="beatLamp" title="Beat detected"></div>
          </div>
          <!-- legacy ids for tests / compatibility (visuals are in the spectrum + readouts above) -->
          <div style="display:none">
            <div class="band-fill" id="bassFill"></div>
            <div class="band-fill" id="midFill"></div>
            <div class="band-fill" id="trebleFill"></div>
            <div class="confidence-fill" id="beatConfidenceFill"></div>
            <span id="beatMixReadout"></span>
            <span id="bpmReadout"></span>
          </div>
        </div>

        <div class="viz-panel">
          <div class="viz-label">FREQUENCY SPECTRUM (click bars to boost lights creatively)</div>
          <canvas class="spectrum-canvas" id="spectrumCanvas" width="720" height="40"></canvas>

          <div style="display:grid; grid-template-columns: auto 1fr; gap:10px; align-items:center; margin-top:6px;">
            <!-- Energy orb (pulses on beat, colored by current light) -->
            <div>
              <div class="viz-label" style="margin-bottom:4px;">ENERGY / BEAT</div>
              <div class="energy-orb" id="energyOrb" title="Click to trigger a creative energy pulse">
                <div class="inner"></div>
              </div>
            </div>

            <div>
              <div class="analysis-readouts" style="grid-template-columns: repeat(4,1fr); font-size:12px;">
                <div class="readout" style="padding:6px 8px;">
                  <span class="label">BASS</span>
                  <span class="value" id="bassReadout" style="font-size:13px;">0%</span>
                </div>
                <div class="readout" style="padding:6px 8px;">
                  <span class="label">MID</span>
                  <span class="value" id="midReadout" style="font-size:13px;">0%</span>
                </div>
                <div class="readout" style="padding:6px 8px;">
                  <span class="label">TREBLE</span>
                  <span class="value" id="trebleReadout" style="font-size:13px;">0%</span>
                </div>
                <div class="readout" style="padding:6px 8px;">
                  <span class="label">DRIVE</span>
                  <span class="value" id="driveReadout" style="font-size:13px;">0%</span>
                </div>
              </div>
              <div style="margin-top:6px; display:flex; gap:6px; flex-wrap:wrap;">
                <button onclick="creativeSpectrumMap()" style="font-size:11px;padding:5px 10px;">Spectrum → Colors</button>
                <button onclick="creativeEnergyPulse()" style="font-size:11px;padding:5px 10px;">Energy Pulse</button>
                <button onclick="creativeEvolve()" style="font-size:11px;padding:5px 10px;">Evolve Scene</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>"""


PLAYER_STAGE_DISABLED = """    <!-- VISUALIZERS AT THE TOP: Prominent, beautiful, always-visible live preview + audio reactive viz -->
    <div class="visualizer-hero" id="visualizerHero">
      <div class="visualizer-header">
        <div><span class="title">🎨 LIVE VISUALIZERS</span> — LED Preview + Audio Reaction</div>
        <div style="display:flex;gap:8px;align-items:center;">
          <span id="vizStatus" style="font-size:11px;opacity:.7;">idle</span>
          <button class="secondary" style="padding:4px 10px;font-size:12px;" onclick="startMusicMode()" title="Start controller-microphone smart music rendering">▶ Start Reactive</button>
          <button class="secondary" style="padding:4px 10px;font-size:12px;background:rgba(255,100,100,.2);" onclick="stopSmartMusicMode()" title="Stop Smart Music">⏹ Stop</button>
        </div>
      </div>

      <!-- LED Strip Preview (creative simulation of current effect + colors) -->
      <div class="full-bleed-preview" style="width:100%; margin:0; margin-left:0; margin-right:0; border:none; box-shadow:none; padding:3px 0 1px; background:transparent;">
        <div class="strip-wrapper">
          <div class="led-strip off" id="ledStrip" style="height: clamp(24px, 3.5vw, 45px);"></div>
        </div>
        <div class="preview-meta" style="margin-top:1px;">
          <div class="light-info" id="lightInfo">Waiting for state...</div>
        </div>
      </div>

      <!-- Audio Visualizers row: Waveform + Spectrum + Energy/Beat orb -->
      <div class="viz-row">
        <div class="viz-panel">
          <div class="viz-label" id="waveformLabel">WAVEFORM + BEAT</div>
          <canvas class="waveform-canvas" id="waveformCanvas" width="680" height="50" style="height:50px;"></canvas>
          <div style="display:flex; gap:8px; align-items:center; margin-top:4px;" id="vuMeter">
            <div class="meter" style="flex:1;height:14px;">
              <div class="meter-fill" id="vuFill"></div>
              <div class="meter-peak" id="vuPeak"></div>
            </div>
            <div class="beat-lamp" id="beatLamp" title="Beat detected"></div>
          </div>
          <!-- legacy ids for tests / compatibility (visuals are in the spectrum + readouts above) -->
          <div style="display:none">
            <div class="band-fill" id="bassFill"></div>
            <div class="band-fill" id="midFill"></div>
            <div class="band-fill" id="trebleFill"></div>
            <div class="confidence-fill" id="beatConfidenceFill"></div>
            <span id="beatMixReadout"></span>
            <span id="bpmReadout"></span>
          </div>
        </div>

        <div class="viz-panel">
          <div class="viz-label">FREQUENCY SPECTRUM (click bars to boost lights creatively)</div>
          <canvas class="spectrum-canvas" id="spectrumCanvas" width="720" height="40"></canvas>

          <div style="display:grid; grid-template-columns: auto 1fr; gap:10px; align-items:center; margin-top:6px;">
            <!-- Energy orb (pulses on beat, colored by current light) -->
            <div>
              <div class="viz-label" style="margin-bottom:4px;">ENERGY / BEAT</div>
              <div class="energy-orb" id="energyOrb" title="Click to trigger a creative energy pulse">
                <div class="inner"></div>
              </div>
            </div>

            <div>
              <div class="analysis-readouts" style="grid-template-columns: repeat(4,1fr); font-size:12px;">
                <div class="readout" style="padding:6px 8px;">
                  <span class="label">BASS</span>
                  <span class="value" id="bassReadout" style="font-size:13px;">0%</span>
                </div>
                <div class="readout" style="padding:6px 8px;">
                  <span class="label">MID</span>
                  <span class="value" id="midReadout" style="font-size:13px;">0%</span>
                </div>
                <div class="readout" style="padding:6px 8px;">
                  <span class="label">TREBLE</span>
                  <span class="value" id="trebleReadout" style="font-size:13px;">0%</span>
                </div>
                <div class="readout" style="padding:6px 8px;">
                  <span class="label">DRIVE</span>
                  <span class="value" id="driveReadout" style="font-size:13px;">0%</span>
                </div>
              </div>
              <div style="margin-top:6px; display:flex; gap:6px; flex-wrap:wrap;">
                <button onclick="creativeSpectrumMap()" style="font-size:11px;padding:5px 10px;">Spectrum → Colors</button>
                <button onclick="creativeEnergyPulse()" style="font-size:11px;padding:5px 10px;">Energy Pulse</button>
                <button onclick="creativeEvolve()" style="font-size:11px;padding:5px 10px;">Evolve Scene</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>"""


PLAYER_DECK_ENABLED = """    <div class="player-deck">
      <section class="player-library" id="playerLibrary">
        <div class="library-search">
          <input id="playerSearchInput" type="search" placeholder="Search tracks, artists, albums, playlists" onkeydown="if(event.key==='Enter')searchPlayerLibrary()">
          <button onclick="searchPlayerLibrary()" title="Search this source">Search</button>
        </div>
        <div class="library-cols">
          <div>
            <h3>Playlists</h3>
            <div id="playerPlaylists" class="library-list"><div class="library-empty">Playlists appear after the source is connected.</div></div>
          </div>
          <div>
            <h3>Results</h3>
            <div id="playerSearchResults" class="library-list"><div class="library-empty">Search this player to fill the queue pane.</div></div>
          </div>
        </div>
        __LOCAL_LIBRARY__
      </section>
      <div>
        <div class="player-follow auto-card" id="visualizerHero">
          <div class="mode-header">
            <div>
              <h2>Lights follow the player</h2>
              <p class="mode-subtitle">Desktop playback reports track identity and timing locally. Audio analysis uses the configured system output or room microphone.</p>
            </div>
            <span class="mode-pill" id="musicModeState">Idle</span>
          </div>
          <div class="mode-actions">
            <button class="big-button" id="musicModeBtn" onclick="startMusicMode()">Start Smart Music</button>
            <button class="secondary stop-button" id="musicModeStopBtn" onclick="stopSmartMusicMode()">Stop</button>
          </div>
          <div class="visualizer-header" style="background:transparent;border:0;padding:8px 0 0;">
            <span id="vizStatus" style="font-size:11px;opacity:.7;">idle</span>
          </div>
      <div class="mode-summary">
        <div class="mode-summary-item">
          <span>Mic Pipeline</span>
          <strong id="micPipelineState">Idle</strong>
        </div>
        <div class="mode-summary-item">
          <span>Song Source</span>
          <strong id="songSourceState">Media metadata</strong>
        </div>
        <div class="mode-summary-item">
          <span>Next Match</span>
          <strong id="nextMatchState">Manual</strong>
          <small id="songClockState">Position unknown</small>
        </div>
      </div>
      <div class="auto-status" id="autoStatus"></div>
      <div class="smart-grid" id="smartSuggestions" aria-live="polite"></div>
        </div>
        <div class="model-responses-pane" id="modelResponsesPane">
          <div class="responses-header">
            <span>Model Responses</span>
            <button onclick="clearModelResponses()" title="Clear">clear</button>
          </div>
          <div id="modelResponses" class="responses-scroll" aria-live="polite"></div>
        </div>
      </div>
    </div>"""


PLAYER_DECK_DISABLED = """    <!-- Scrollable window for model responses between visualizers and Music Mode -->
    <div class="model-responses-pane" id="modelResponsesPane">
      <div class="responses-header">
        <span>Model Responses</span>
        <button onclick="clearModelResponses()" title="Clear">clear</button>
      </div>
      <div id="modelResponses" class="responses-scroll" aria-live="polite"></div>
    </div>

    <!-- Music / Beat Mode summary card (kept but smaller) -->
    <div class="card auto-card">
      <div class="mode-header">
        <div>
          <h2>Music Mode</h2>
          <p class="mode-subtitle">Controller-microphone rendering with TV-aware automatic decisions. Use Advanced browser mic above only when needed.</p>
        </div>
        <span class="mode-pill" id="musicModeState">Idle</span>
      </div>
      <div class="mode-actions">
        <button class="big-button" id="musicModeBtn" onclick="startMusicMode()">Start Smart Music</button>
        <button class="secondary stop-button" id="musicModeStopBtn" onclick="stopSmartMusicMode()">Stop</button>
      </div>
      <div class="mode-summary">
        <div class="mode-summary-item">
          <span>Mic Pipeline</span>
          <strong id="micPipelineState">Idle</strong>
        </div>
        <div class="mode-summary-item">
          <span>Song Source</span>
          <strong id="songSourceState">Media metadata</strong>
        </div>
        <div class="mode-summary-item">
          <span>Next Match</span>
          <strong id="nextMatchState">Manual</strong>
          <small id="songClockState">Position unknown</small>
        </div>
      </div>
      <div class="auto-status" id="autoStatus"></div>
      <div class="smart-grid" id="smartSuggestions" aria-live="polite"></div>
    </div>"""


NOW_PLAYING_CARD_DISABLED = """          <div class="card">
            <h2>Now Playing</h2>
            <div class="music-toolbar">
              <button class="secondary" onclick="refreshNowPlaying()" title="Refresh media-player metadata">Refresh Song</button>
              <button class="secondary" onclick="matchLightsFromNowPlaying()" title="Apply a song-aware light mood once">Apply Mood</button>
            </div>
            <div class="music-display">
              <p class="music-title" id="musicTitle">No song detected yet.</p>
              <p class="music-artist" id="musicArtist"></p>
              <span class="music-genre" id="musicGenre" style="display:none;"></span>
              <img class="album-art" id="albumArt" alt="Album art">
            </div>
            <span class="now-playing" id="nowPlaying"></span>
          </div>"""


PLAYER_SETTINGS_CARD_ENABLED = """      <div class="card">
        <h2>Player connections</h2>
        <p class="card-note">Desktop: sign in directly in each service tab. Browser-only legacy mode: optional companion connection or Apple MusicKit token below.</p>
        <label><input type="checkbox" id="setPlayerEnabled" checked> Enable onboard audio player</label>
        <label>Youtopia host
          <input id="setYoutubeHost" type="text" placeholder="http://127.0.0.1:9863" style="width:100%;">
        </label>
        <label>Apple Music developer token
          <input id="setAppleDevToken" type="password" placeholder="MusicKit JWT" style="width:100%;" autocomplete="off">
        </label>
        <div class="row" style="margin-top:10px;">
          <button onclick="savePlayerSettings(this)" title="Save player connection settings">Save player settings</button>
          <span class="settings-badge" id="setAppleTokenBadge">Apple token: unknown</span>
        </div>
        <div class="settings-msg" id="playerSettingsMsg"></div>
      </div>"""


PLAYER_SETTINGS_CARD_DISABLED = """      <div class="card">
        <h2>Onboard audio player</h2>
        <p class="card-note">Player UI is hidden while disabled.</p>
        <label><input type="checkbox" id="setPlayerEnabled"> Enable onboard audio player</label>
        <div class="row" style="margin-top:10px;">
          <button onclick="savePlayerSettings(this)" title="Save player settings">Save</button>
        </div>
        <div class="settings-msg" id="playerSettingsMsg"></div>
      </div>"""


def render_main_html(player_enabled: bool = True) -> str:
    """Return HTML_TEMPLATE with the player regions swapped per the toggle.

    Enabled (default) reproduces the player UI; disabled restores the
    pre-player layout (visualizer hero, model responses, Music Mode card,
    Now Playing card) plus a minimal settings card to re-enable.
    """
    import desktop_ui
    if player_enabled:
        stage = PLAYER_STAGE_ENABLED
        deck = PLAYER_DECK_ENABLED
        now_playing_card = ""
        settings_card = PLAYER_SETTINGS_CARD_ENABLED
    else:
        stage = PLAYER_STAGE_DISABLED
        deck = PLAYER_DECK_DISABLED
        now_playing_card = "\n" + NOW_PLAYING_CARD_DISABLED
        settings_card = PLAYER_SETTINGS_CARD_DISABLED
    return (
        HTML_TEMPLATE.replace("__PLAYER_STAGE__", stage)
        .replace("__PLAYER_DECK__", deck)
        .replace("__NOW_PLAYING_CARD__", now_playing_card)
        .replace("__PLAYER_SETTINGS_CARD__", settings_card)
        .replace("__DESKTOP_DIALOGS__", desktop_ui.DIALOGS)
        .replace("__DESKTOP_SCRIPT__", desktop_ui.SCRIPT)
        .replace("__LOCAL_LIBRARY__", desktop_ui.LIBRARY)
    )


APPLE_LOGIN_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lightss — Sign in with Apple ID</title>
  <script src="https://js-cdn.music.apple.com/musickit/v3/musickit.js" data-web-components></script>
  <style>
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
      background: #070817; color: #f7fbff; padding: 24px;
    }
    main {
      width: min(420px, 100%);
      padding: 28px 24px;
      border: 1px solid rgba(180,205,255,.16);
      border-radius: 20px;
      background: linear-gradient(145deg, rgba(18,22,42,.95), rgba(8,10,24,.92));
    }
    h1 { font-size: 22px; margin: 0 0 8px; }
    p { color: #aab7d6; font-size: 14px; line-height: 1.45; }
    button {
      width: 100%; margin-top: 16px; border: 0; border-radius: 14px; padding: 14px 16px;
      background: #fff; color: #111; font-weight: 800; font-size: 15px; cursor: pointer;
    }
    button:disabled { opacity: .6; }
    .status { min-height: 20px; margin-top: 14px; font-size: 13px; color: #67e8f9; }
    .status.err { color: #fb4666; }
  </style>
</head>
<body>
  <main>
    <h1>Sign in with Apple ID</h1>
    <p>Authorize Apple Music on this phone. The Lightss controller keeps the session and plays on the wall.</p>
    <button id="signInBtn" onclick="signIn()">Sign in with Apple ID</button>
    <p class="status" id="status">Waiting for MusicKit…</p>
  </main>
  <script>
    const params = new URLSearchParams(location.search);
    const session = params.get('session') || '';
    async function configure() {
      const cfg = await fetch('/api/player/apple/config').then((r) => r.json());
      if (!cfg.developerToken) throw new Error('Set an Apple Music developer token in Lightss settings first.');
      await MusicKit.configure({
        developerToken: cfg.developerToken,
        app: { name: cfg.appName || 'Lightss', build: cfg.appBuild || '0.1.0' }
      });
      document.getElementById('status').textContent = session ? 'Ready. Sign in to connect the controller.' : 'Missing session. Scan the QR from Lightss.';
    }
    async function signIn() {
      const btn = document.getElementById('signInBtn');
      const st = document.getElementById('status');
      btn.disabled = true;
      st.className = 'status';
      st.textContent = 'Opening Apple ID…';
      try {
        const music = MusicKit.getInstance();
        const token = await music.authorize();
        const body = {session, musicUserToken: token || music.musicUserToken};
        const res = await fetch('/api/player/apple/complete', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body)
        }).then((r) => r.json());
        if (!res.ok) throw new Error(res.message || 'Controller rejected the session.');
        st.textContent = 'Connected. You can return to Lightss.';
        btn.textContent = 'Signed in';
      } catch (err) {
        st.className = 'status err';
        st.textContent = err.message || String(err);
        btn.disabled = false;
      }
    }
    configure().catch((err) => {
      const st = document.getElementById('status');
      st.className = 'status err';
      st.textContent = err.message || String(err);
    });
  </script>
</body>
</html>
"""


TV_AMBIENT_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>WLED Wall Ambient</title>
  <style>
    html, body {
      margin: 0;
      height: 100%;
      background: #000;
      overflow: hidden;
      cursor: none;
    }
    #stage {
      position: fixed;
      inset: 0;
      display: block;
    }
    #footer {
      position: fixed;
      left: 0;
      right: 0;
      bottom: 2.2vh;
      height: 1.6em;
      overflow: hidden;
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
      font-size: clamp(11px, 1.5vh, 16px);
      font-weight: 500;
      letter-spacing: .22em;
      text-transform: uppercase;
      color: rgba(255, 255, 255, .34);
      text-shadow: 0 0 8px rgba(0, 0, 0, .9);
      white-space: nowrap;
      pointer-events: none;
    }
    #marquee {
      display: inline-block;
      padding-left: 100vw;
      will-change: transform;
      animation: marquee 26s linear infinite;
    }
    #marquee.still {
      padding-left: 0;
      width: 100%;
      text-align: center;
      animation: none;
    }
    @keyframes marquee {
      from { transform: translateX(0); }
      to   { transform: translateX(-100%); }
    }
    #dot {
      position: fixed;
      top: 14px;
      right: 16px;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #2f6;
      opacity: .35;
      transition: background-color .5s, opacity .5s;
      pointer-events: none;
    }
    #dot.bad {
      background: #f44;
      opacity: .8;
    }
  </style>
</head>
<body>
  <canvas id="stage"></canvas>
  <div id="footer"><span id="marquee" class="still"></span></div>
  <div id="dot"></div>
  <script>
  'use strict';
  // Four luminous columns rise out of a dark room, mirroring the physical
  // wall: far-left, middle-left, middle-right, far-right. /api/state is
  // polled twice a second; the canvas eases toward each sample so motion
  // stays liquid at ~60fps. Energy (brightness + color deltas between
  // polls) drives pulse, shimmer and particles, so the page breathes with
  // the audio-reactive wall.
  const WALL_ORDER = ['far-left', 'middle-left', 'middle-right', 'far-right'];
  const AUDIO_FX = new Set([68, 132, 135, 136, 137, 139, 143, 144, 145,
    155, 156, 157, 158, 159, 175, 185]);
  const POLL_MS = 500;
  const MUSIC_MS = 10000;
  const MAX_DPR = 1.5;

  const canvas = document.getElementById('stage');
  const ctx = canvas.getContext('2d');
  const marquee = document.getElementById('marquee');
  const dot = document.getElementById('dot');

  let W = 0, H = 0, DPR = 1;
  let vignette = null, grain = null, grainPat = null;

  function clampByte(v) {
    v = Math.round(Number(v) || 0);
    return v < 0 ? 0 : (v > 255 ? 255 : v);
  }

  function mixColor(a, b, t) {
    return [a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t];
  }

  function css(c, alpha) {
    return 'rgba(' + Math.round(c[0]) + ',' + Math.round(c[1]) + ',' +
      Math.round(c[2]) + ',' + alpha + ')';
  }

  // Per-column render state: current values ease toward targets each frame.
  const cols = WALL_ORDER.map(function (name) {
    return {
      name: name,
      cur: [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
      tgt: [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
      bri: 0, tgtBri: 0,
      fx: 0, on: false, audio: false,
      energy: 0,        // decaying pulse derived from poll deltas
      phase: Math.random() * Math.PI * 2,
      particles: []
    };
  });

  function buildGrain() {
    grain = document.createElement('canvas');
    grain.width = 96;
    grain.height = 96;
    const g = grain.getContext('2d');
    const img = g.createImageData(96, 96);
    for (let i = 0; i < img.data.length; i += 4) {
      const v = (Math.random() * 255) | 0;
      img.data[i] = v;
      img.data[i + 1] = v;
      img.data[i + 2] = v;
      img.data[i + 3] = 255;
    }
    g.putImageData(img, 0, 0);
    grainPat = ctx.createPattern(grain, 'repeat');
  }

  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    W = Math.round(window.innerWidth * DPR);
    H = Math.round(window.innerHeight * DPR);
    canvas.width = W;
    canvas.height = H;
    canvas.style.width = window.innerWidth + 'px';
    canvas.style.height = window.innerHeight + 'px';
    vignette = document.createElement('canvas');
    vignette.width = W;
    vignette.height = H;
    const v = vignette.getContext('2d');
    const rg = v.createRadialGradient(W / 2, H * 0.45, Math.min(W, H) * 0.35,
      W / 2, H * 0.5, Math.max(W, H) * 0.75);
    rg.addColorStop(0, 'rgba(0,0,0,0)');
    rg.addColorStop(1, 'rgba(0,0,0,0.55)');
    v.fillStyle = rg;
    v.fillRect(0, 0, W, H);
  }

  function parseSegColors(seg, briScale) {
    const out = [];
    const raw = seg && Array.isArray(seg.col) ? seg.col : [];
    for (let i = 0; i < 3; i++) {
      const col = Array.isArray(raw[i]) ? raw[i] : (Array.isArray(raw[0]) ? raw[0] : [0, 0, 0]);
      const w = clampByte(col[3] || 0);
      out.push([
        clampByte((col[0] || 0) + w) * briScale,
        clampByte((col[1] || 0) + w) * briScale,
        clampByte((col[2] || 0) + w) * briScale
      ]);
    }
    return out;
  }

  function applyState(data) {
    const state = (data && data.state) || {};
    const channels = (data && data.channels) || {};
    const single = 'seg' in state || 'on' in state;
    for (const col of cols) {
      let cst = null, segId = 0;
      const mapping = channels[col.name];
      if (!single && Array.isArray(mapping) && state[mapping[0]]) {
        cst = state[mapping[0]];
        segId = Number(mapping[1]) || 0;
      } else if (single) {
        cst = state;
      }
      const segs = (cst && Array.isArray(cst.seg)) ? cst.seg : [];
      const seg = segs[segId] || segs[0] || null;
      const on = cst ? cst.on !== false : false;
      const bri = seg && seg.bri != null ? seg.bri
        : (cst && cst.bri != null ? cst.bri : 255);
      const scale = on ? Math.max(0, Math.min(1, bri / 255)) : 0;
      const next = parseSegColors(seg, scale);
      // Pulse energy: how far the sample jumped since the last target.
      let delta = Math.abs(scale - col.tgtBri) * 255;
      for (let i = 0; i < 3; i++) {
        delta += Math.abs(next[i][0] - col.tgt[i][0]) +
                 Math.abs(next[i][1] - col.tgt[i][1]) +
                 Math.abs(next[i][2] - col.tgt[i][2]);
      }
      col.energy = Math.max(col.energy, Math.min(1, delta / 220));
      col.tgt = next;
      col.tgtBri = scale;
      col.fx = seg && seg.fx != null ? Number(seg.fx) || 0 : 0;
      col.on = !!on;
      col.audio = AUDIO_FX.has(col.fx);
    }
    refreshIdleFooter();
  }

  function refreshIdleFooter() {
    if (currentTrack) return;
    const text = cols.map(function (c) {
      return c.name + '  \u00b7  fx ' + c.fx;
    }).join('      ');
    setFooter(text, false);
  }

  let currentTrack = null;
  let footerText = '';
  function setFooter(text, scroll) {
    if (text === footerText) return;
    footerText = text;
    marquee.textContent = text;
    marquee.className = scroll ? '' : 'still';
  }

  // Rotating ticker: while a track plays the footer cycles through the
  // now-playing line and the AI trivia items, one line per marquee pass.
  const TRIVIA_MS = 30000;
  let trivia = { artist: null, items: [] };
  let nowLine = '';
  let rotList = [];
  let rotIdx = 0;

  function showRotItem() {
    if (!rotList.length) return;
    footerText = rotList[rotIdx % rotList.length];
    marquee.textContent = footerText;
    // Restart the scroll so each line gets a full pass across the screen.
    marquee.className = '';
    marquee.style.animation = 'none';
    void marquee.offsetWidth;
    marquee.style.animation = '';
  }

  function rebuildRotation(reset) {
    if (!currentTrack) return;
    rotList = [nowLine].concat(trivia.items);
    if (reset || rotIdx >= rotList.length) rotIdx = 0;
    if (reset) showRotItem();
  }

  marquee.addEventListener('animationiteration', function () {
    if (!rotList.length) return;
    rotIdx = (rotIdx + 1) % rotList.length;
    showRotItem();
  });

  async function fetchJsonWithTimeout(url, options = {}, timeoutMs = 5000) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(url, Object.assign({}, options, { signal: controller.signal }));
      return await res.json();
    } finally {
      clearTimeout(timeout);
    }
  }

  async function pollTrivia() {
    if (!currentTrack) return;   // no trivia fetches while idle
    try {
      const data = await fetchJsonWithTimeout('/api/tv-trivia');
      if (data && data.ok && Array.isArray(data.items)) {
        trivia = {
          artist: data.artist || null,
          items: data.items.filter(function (s) {
            return typeof s === 'string' && s;
          })
        };
        rebuildRotation(false);
      }
    } catch (err) {
      /* trivia is best-effort */
    }
  }

  async function pollState() {
    try {
      const data = await fetchJsonWithTimeout('/api/state');
      if (data && data.ok !== false) {
        dot.classList.remove('bad');
        applyState(data);
        if (data.state && typeof applyFleetInfo === 'function' && typeof displayState === 'function') {
          applyFleetInfo(data);
          const health = typeof updateConnectionStatusFromState === 'function'
            ? updateConnectionStatusFromState(data.state)
            : {fleet: false, connected: [], total: 0};
          const picked = displayState(data.state);
          const st = picked.state || {};
          const seg = st.seg && st.seg[0] ? st.seg[0] : {};
          const bri = st.bri ?? '?';
          const onOff = st.on ? 'ON' : 'OFF';
          const col = seg.col && seg.col[0] ? seg.col[0] : [0,0,0,0];
          const stateDisplayEl = document.getElementById('stateDisplay');
          const stateSummaryEl = document.getElementById('stateSummary');
          if (stateDisplayEl) {
            stateDisplayEl.textContent = `${picked.name ? 'Controller: ' + picked.name + '\n' : ''}Power: ${onOff}\nBrightness: ${bri}\nColor: RGBW(${col.join(',')})\nEffect: ${seg.fx ?? '-'} Speed: ${seg.sx ?? '-'}`;
          }
          if (stateSummaryEl) {
            const controllerSummary = health.fleet ? ` | ${health.connected.length}/${health.total} controllers` : '';
            stateSummaryEl.textContent = `${onOff} | Bri ${bri} | Fx ${seg.fx ?? '-'} @ ${seg.sx ?? '-'}${controllerSummary}`;
          }
          if (typeof updateLightPreview === 'function') updateLightPreview(st, ledCapabilities, picked.segId);
        }
      } else {
        dot.classList.add('bad');
      }
    } catch (err) {
      dot.classList.add('bad');
    }
  }

  async function pollMusic() {
    try {
      const data = await fetchJsonWithTimeout('/api/music-director');
      const st = (data && data.status) || {};
      if (st.running && st.track) {
        const isNew = st.track !== currentTrack;
        currentTrack = st.track;
        nowLine = '\u266a  ' + st.track + (st.mood ? '   \u00b7   ' + st.mood : '');
        rebuildRotation(isNew);
        if (isNew) pollTrivia();
      } else {
        currentTrack = null;
        trivia = { artist: null, items: [] };
        rotList = [];
        rotIdx = 0;
        refreshIdleFooter();
      }
    } catch (err) {
      /* keep the last footer on failure */
    }
  }

  function spawnParticles(col, cx, colW, floorY, topY, dt) {
    if (!col.audio && col.energy < 0.25) return;
    const rate = (col.audio ? 14 : 4) * (0.3 + col.energy) * dt;
    if (Math.random() < rate && col.particles.length < 34) {
      col.particles.push({
        x: cx + (Math.random() - 0.5) * colW * 0.8,
        y: floorY - Math.random() * (floorY - topY) * 0.25,
        vy: -(20 + Math.random() * 55) * DPR,
        life: 1,
        decay: 0.35 + Math.random() * 0.5,
        r: (0.8 + Math.random() * 1.8) * DPR
      });
    }
  }

  function drawColumn(col, idx, t, dt) {
    const cx = W * (idx + 0.5) / 4;
    const colW = Math.min(W * 0.085, 110 * DPR);
    const floorY = H * 0.86;
    const topY = H * 0.07;
    const height = floorY - topY;
    const glow = Math.max(0, Math.min(1, col.bri)) * (1 + col.energy * 0.55);
    if (glow < 0.004) return;

    // Plasma drift: the three sampled colors slide past each other so the
    // gradient is never static even when the wall holds a steady look.
    const drift = (Math.sin(t * 0.6 + col.phase) + 1) / 2;
    const drift2 = (Math.sin(t * 0.9 + col.phase * 1.7) + 1) / 2;
    const c0 = mixColor(col.cur[0], col.cur[1], drift * 0.35);
    const c1 = mixColor(col.cur[1], col.cur[2], drift2 * 0.4);
    const c2 = mixColor(col.cur[2], col.cur[0], drift * 0.3);
    const a = Math.min(1, 0.28 + glow * 0.72);

    // Column body: gradient rising from the floor, dissolving at the top.
    const grad = ctx.createLinearGradient(0, floorY, 0, topY);
    grad.addColorStop(0, css(c0, a));
    grad.addColorStop(0.45, css(c1, a * 0.9));
    grad.addColorStop(0.8, css(c2, a * 0.55));
    grad.addColorStop(1, css(c2, 0));
    ctx.fillStyle = grad;
    const wobble = Math.sin(t * 1.3 + col.phase) * colW * 0.04;
    ctx.beginPath();
    ctx.moveTo(cx - colW / 2 + wobble, floorY);
    ctx.lineTo(cx - colW / 2, topY);
    ctx.lineTo(cx + colW / 2, topY);
    ctx.lineTo(cx + colW / 2 + wobble, floorY);
    ctx.closePath();
    ctx.fill();

    // Ceiling bend: a soft fan of light rolling back over the ceiling.
    ctx.save();
    ctx.translate(cx, topY + H * 0.005);
    ctx.scale(2.6, 0.55);
    const bend = ctx.createRadialGradient(0, 0, 0, 0, 0, colW * 1.15);
    bend.addColorStop(0, css(c2, 0.30 * glow));
    bend.addColorStop(1, css(c2, 0));
    ctx.fillStyle = bend;
    ctx.beginPath();
    ctx.arc(0, 0, colW * 1.15, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    // Floor pool: light spilling onto the ground beneath the column.
    ctx.save();
    ctx.translate(cx, floorY + H * 0.012);
    ctx.scale(1.9, 0.28);
    const pool = ctx.createRadialGradient(0, 0, 0, 0, 0, colW);
    pool.addColorStop(0, css(c0, 0.35 * glow));
    pool.addColorStop(1, css(c0, 0));
    ctx.fillStyle = pool;
    ctx.beginPath();
    ctx.arc(0, 0, colW, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    // Audio-reactive shimmer: bright beads racing up the column.
    if (col.audio || col.energy > 0.2) {
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      const streaks = col.audio ? 3 : 1;
      for (let s = 0; s < streaks; s++) {
        const sy = topY + height *
          ((Math.sin(t * (2.1 + s * 0.7) + col.phase + s * 2.1) + 1) / 2);
        const sa = (0.05 + col.energy * 0.22) * glow;
        if (sa > 0.01) {
          const sg = ctx.createLinearGradient(0, sy - colW, 0, sy + colW);
          sg.addColorStop(0, css(c1, 0));
          sg.addColorStop(0.5, css(mixColor(c1, [255, 255, 255], 0.35), sa));
          sg.addColorStop(1, css(c1, 0));
          ctx.fillStyle = sg;
          ctx.fillRect(cx - colW / 2, sy - colW, colW, colW * 2);
        }
      }
      // Particles: sparse rising sparks, additive, tightly capped.
      spawnParticles(col, cx, colW, floorY, topY, dt);
      for (let i = col.particles.length - 1; i >= 0; i--) {
        const p = col.particles[i];
        p.y += p.vy * dt;
        p.life -= p.decay * dt;
        if (p.life <= 0 || p.y < topY) {
          col.particles.splice(i, 1);
          continue;
        }
        ctx.fillStyle = css(mixColor(c1, [255, 255, 255], 0.4),
          0.5 * p.life * glow);
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    } else if (col.particles.length) {
      col.particles.length = 0;
    }
  }

  // Dynamic background: instead of clearing to flat black, the canvas is
  // filled from the columns' own eased state. (1) An ambient wash mixes
  // the average active color into a vertical gradient held near-black so
  // the columns still pop. (2) Three aurora blobs drift on slow Lissajous
  // paths, each tinted from a different column, blended additively. (3)
  // Both breathe with the average column energy (beat pulse). An emphasis
  // mode — wash / aurora / pulse dominant — rotates every ~45s with a
  // ~5s smoothstep crossfade.
  const BG_MODE_S = 45;
  const BG_FADE_S = 5;
  const blobs = [];
  for (let i = 0; i < 3; i++) {
    blobs.push({
      fx: 0.045 + i * 0.013,   // Lissajous frequencies, cycles per second
      fy: 0.060 + i * 0.011,
      px: Math.random() * Math.PI * 2,
      py: Math.random() * Math.PI * 2,
      r: 0.30 + i * 0.07       // base radius as a fraction of min(W, H)
    });
  }

  function bgWeights(t) {
    const pos = (t % (BG_MODE_S * 3)) / BG_MODE_S;
    const idx = Math.floor(pos) % 3;
    const frac = pos - Math.floor(pos);
    const fadeFrac = BG_FADE_S / BG_MODE_S;
    let blend = 0;
    if (frac > 1 - fadeFrac) {
      blend = (frac - (1 - fadeFrac)) / fadeFrac;
      blend = blend * blend * (3 - 2 * blend);   // smoothstep crossfade
    }
    const w = [0, 0, 0];
    w[idx] = 1 - blend;
    w[(idx + 1) % 3] = blend;
    return w;
  }

  function drawBackground(t) {
    // Average the eased colors and energy of every lit column.
    let ar = 0, ag = 0, ab = 0, n = 0, energy = 0, lit = 0;
    for (const col of cols) {
      const glow = Math.max(0, Math.min(1, col.bri));
      if (!col.on || glow < 0.004) continue;
      lit++;
      energy += col.energy;
      for (let i = 0; i < 3; i++) {
        ar += col.cur[i][0];
        ag += col.cur[i][1];
        ab += col.cur[i][2];
        n++;
      }
    }
    const avg = n ? [ar / n, ag / n, ab / n] : [0, 0, 0];
    energy = lit ? energy / lit : 0;

    const w = bgWeights(t);
    const washBoost = 0.55 + 0.75 * w[0];
    const auroraBoost = 0.5 + 0.9 * w[1];
    const pulse = energy * (0.35 + 1.1 * w[2]);

    // (1) Ambient wash: the average color scaled to ~8-14% brightness,
    // slightly darker toward the floor. Black when nothing is lit.
    const ws = Math.min(0.14, (0.08 + 0.05 * pulse) * washBoost);
    const wash = ctx.createLinearGradient(0, 0, 0, H);
    wash.addColorStop(0, css([avg[0] * ws, avg[1] * ws, avg[2] * ws], 1));
    wash.addColorStop(1, css([avg[0] * ws * 0.5, avg[1] * ws * 0.5,
      avg[2] * ws * 0.5], 1));
    ctx.fillStyle = wash;
    ctx.fillRect(0, 0, W, H);

    // (2) Aurora blobs, (3) radii and alpha breathing with the pulse.
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (let i = 0; i < blobs.length; i++) {
      const b = blobs[i];
      const col = cols[i % cols.length];
      const glow = Math.max(0, Math.min(1, col.bri));
      if (!col.on || glow < 0.004) continue;
      const tint = mixColor(mixColor(col.cur[0], col.cur[1], 0.5),
        col.cur[2], 0.35);
      const bx = W * (0.5 + 0.38 * Math.sin(t * b.fx * Math.PI * 2 + b.px));
      const by = H * (0.48 + 0.34 * Math.sin(t * b.fy * Math.PI * 2 + b.py));
      const rad = Math.min(W, H) * b.r * (0.6 + 0.4 * glow) *
        (1 + 0.3 * pulse);
      const alpha = (0.05 + 0.08 * pulse) * auroraBoost * glow;
      if (alpha < 0.008) continue;
      const g = ctx.createRadialGradient(bx, by, 0, bx, by, rad);
      g.addColorStop(0, css(tint, alpha));
      g.addColorStop(1, css(tint, 0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(bx, by, rad, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  let last = performance.now();
  let frame = 0;
  function tick(now) {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    const t = now / 1000;
    frame++;

    // Ease current colors/brightness toward the latest poll targets. The
    // ~0.9s time constant turns mood switches into crossfades, never cuts.
    const k = Math.min(1, dt * 3.2);
    for (const col of cols) {
      for (let i = 0; i < 3; i++) {
        col.cur[i] = mixColor(col.cur[i], col.tgt[i], k);
      }
      col.bri += (col.tgtBri - col.bri) * k;
      col.energy *= Math.pow(0.25, dt); // decay to a quarter per second
    }

    drawBackground(t);
    for (let i = 0; i < cols.length; i++) {
      drawColumn(cols[i], i, t, dt);
    }

    // Vignette every frame (single drawImage), grain every third frame.
    if (vignette) ctx.drawImage(vignette, 0, 0);
    if (grainPat && frame % 3 === 0) {
      ctx.save();
      ctx.globalAlpha = 0.045;
      ctx.translate(-((Math.random() * 96) | 0), -((Math.random() * 96) | 0));
      ctx.fillStyle = grainPat;
      ctx.fillRect(0, 0, W + 96, H + 96);
      ctx.restore();
    }

    requestAnimationFrame(tick);
  }

  window.addEventListener('resize', resize);
  resize();
  buildGrain();
  pollState();
  pollMusic();
  setInterval(pollState, POLL_MS);
  setInterval(pollMusic, MUSIC_MS);
  setInterval(pollTrivia, TRIVIA_MS);
  refreshIdleFooter();
  requestAnimationFrame(tick);
  </script>
</body>
</html>
"""
