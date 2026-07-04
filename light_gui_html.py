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
    /* Logo styles are inline for the WLED logo replacement */
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
      max-height: 110px;
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
  </style>
</head>
<body>
  <main>
    <div class="wled-logo" style="text-align: center; margin: 12px 0 28px;">
      <img src="/wled-logo.png" alt="WLED" style="max-height: 55px; width: auto; filter: drop-shadow(0 4px 12px rgba(0,0,0,0.5)); transition: transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);" class="logo-img" onmouseover="this.style.transform='scale(1.06)'" onmouseout="this.style.transform='scale(1)'">
    </div>

    <!-- VISUALIZERS AT THE TOP: Prominent, beautiful, always-visible live preview + audio reactive viz -->
    <div class="visualizer-hero" id="visualizerHero">
      <div class="visualizer-header">
        <div><span class="title">🎨 LIVE VISUALIZERS</span> — LED Preview + Audio Reaction</div>
        <div style="display:flex;gap:8px;align-items:center;">
          <span id="vizStatus" style="font-size:11px;opacity:.7;">idle</span>
          <button class="secondary" style="padding:4px 10px;font-size:12px;" onclick="startMusicMode()" title="Start browser mic beat-reactive + music matching">▶ Start Reactive</button>
          <button class="secondary" style="padding:4px 10px;font-size:12px;background:rgba(255,100,100,.2);" onclick="stopMusicMode()" title="Stop">⏹ Stop</button>
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
          <div class="viz-label">WAVEFORM + BEAT</div>
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
    </div>

    <!-- Scrollable window for model responses between visualizers and Music Mode -->
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
          <p class="mode-subtitle">Browser-mic beat detection + now-playing AI mood matching. Visualizers above react live.</p>
        </div>
        <span class="mode-pill" id="musicModeState">Idle</span>
      </div>
      <div class="mode-actions">
        <button class="big-button" id="musicModeBtn" onclick="startMusicMode()">Start Full Music Mode</button>
        <button class="secondary stop-button" id="musicModeStopBtn" onclick="stopMusicMode()">Stop</button>
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
        </div>
      </div>
      <div class="auto-status" id="autoStatus"></div>
      <div class="smart-grid" id="smartSuggestions" aria-live="polite"></div>
    </div>

    <div class="tab-bar">
      <button class="tab-btn active" data-tab="live" onclick="switchTab('live')">Live View</button>
      <button class="tab-btn" data-tab="manual" onclick="switchTab('manual')">Manual Controls</button>
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
                <input id="aiInput" type="text" placeholder="Try: slow rainbow chase at medium brightness" onkeydown="if(event.key==='Enter')askAI()">
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
              Visualizers &amp; audio analysis are now at the top (hero section). State updates live via SSE.
            </div>
          </div>
          <div class="card">
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
          </div>
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
  </main>
  <div class="status-bar">
    <div class="indicator">
      <span id="connIndicator">🔴</span>
      <span id="connText">Disconnected</span>
    </div>
    <div class="state-summary" id="stateSummary">--</div>
    <div class="last-action" id="status">Ready</div>
  </div>
  <script>
    let audioContext, analyser, micStream, micSource, frequencyData, waveformData, rafId;
    let audioReactiveRunning = false;
    let musicModeRunning = false;
    let musicRecognitionTimer = null;
    let musicRecognitionBusy = false;
    let moodRecorder = null;
    let moodRecorderInterval = null;
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
      if (pane) pane.innerHTML = '';
    }

    function showScrollingModelResponse(text) {
      const pane = document.getElementById('modelResponses');
      if (!pane) return;
      pane.innerHTML = '';  // replace with latest scrolling response (right-to-left ticker)
      const wrapper = document.createElement('div');
      wrapper.className = 'marquee-wrapper';
      const content = document.createElement('div');
      content.className = 'marquee-content';
      // Make it one continuous line for smooth scrolling
      const clean = text.replace(/\\s+/g, ' ').trim();
      content.textContent = clean;
      // Dynamic speed: longer text = longer duration (slower per char feel)
      const duration = Math.max(10, Math.min(35, Math.floor(clean.length / 6)));
      content.style.animationDuration = duration + 's';
      wrapper.appendChild(content);
      pane.appendChild(wrapper);
    }

    async function send(action, values = {}) {
      status.textContent = 'Sending...';
      // Optimistic immediate update to LED preview for accurate live colors + motion feel
      try { optimisticPreviewFromAction(action, values); } catch(e) {}
      try {
        const res = await fetch('/api/action', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({action, ...values})
        });
        const data = await res.json();
        status.textContent = data.ok ? data.message : data.error;
      } catch (err) {
        status.textContent = 'Error: ' + err.message;
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
      addChatMessage('user', prompt);
      aiInput.value = '';
      addChatMessage('system', 'Thinking...');
      const thinkingEls = chatHistory.querySelectorAll('.chat-msg.system');
      const thinkingEl = thinkingEls[thinkingEls.length - 1];
      aiReply.textContent = '';
      aiConfirmations.textContent = '';
      try {
        const song = await refreshNowPlaying();
        const res = await fetch('/api/ai', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({prompt, now_playing: song})
        });
        const data = await res.json();
        if (thinkingEl) thinkingEl.remove();
        status.textContent = data.ok ? data.message : data.error;
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
      }
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
      setText('nextMatchState', running ? (detail || 'Every 30s') : 'Manual');
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
        const response = await fetch('/api/ai_vision', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image: base64Image })
        });
        const data = await response.json();
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
          const st = data.state;
          const onOff = st.on ? 'ON' : 'OFF';
          const bri = st.bri ?? '?';
          const seg = st.seg && st.seg[0] ? st.seg[0] : {};
          const col = seg.col && seg.col[0] ? seg.col[0] : [0,0,0,0];
          let display = `Power: ${onOff}\nBrightness: ${bri}\nColor: RGBW(${col.join(',')})\nEffect: ${seg.fx ?? '-'} Speed: ${seg.sx ?? '-'}`;
          
          const stateDisplay = document.getElementById('stateDisplay');
          const stateSummary = document.getElementById('stateSummary');
          if (stateDisplay) stateDisplay.textContent = display;
          if (stateSummary) stateSummary.textContent = `${onOff} | Bri ${bri} | Fx ${seg.fx ?? '-'} @ ${seg.sx ?? '-'}`;
          
          if (typeof updateLightPreview === 'function') {
            updateLightPreview(st, ledCapabilities);
          }
        }
      } catch (err) {
        console.error('Error fetching live state:', err);
      }
    }

    async function refreshNowPlaying() {
      try {
        const res = await fetch('/api/now-playing');
        const data = await res.json();
        const np = data.now_playing || null;
        if (np) {
          musicTitle.textContent = np.title || 'Unknown';
          musicArtist.textContent = np.artist || '';
          if (np.genre) { musicGenre.textContent = np.genre; musicGenre.style.display = 'inline-block'; }
          else { musicGenre.style.display = 'none'; }
          if (np.cover_url) { albumArt.src = np.cover_url; albumArt.classList.add('visible'); }
          else { albumArt.classList.remove('visible'); }
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
    async function ensureMicSession() {
      if (!micStream) {
        micStream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false }
        });
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
        const res = await fetch('/api/recognize', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({audio: b64})
        });
        const data = await res.json();
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
        const res = await fetch('/api/match-lights', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({now_playing: song})
        });
        const data = await res.json();
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

    async function matchLightsToSong() {
      return matchLightsFromNowPlaying();
    }

    async function refreshSmartSuggestions() {
      const box = document.getElementById('smartSuggestions');
      if (!box) return;
      try {
        const res = await fetch('/api/suggestions');
        const data = await res.json();
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
        await matchLightsFromNowPlaying();
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
        if (!musicModeRunning) return;
        chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      };
      source.connect(processor);
      processor.connect(audioCtx.destination);

      async function uploadChunk() {
        if (!musicModeRunning || chunks.length === 0) return;
        const totalLength = chunks.reduce((sum, c) => sum + c.length, 0);
        const combined = new Float32Array(totalLength);
        let idx = 0;
        for (const c of chunks) { combined.set(c, idx); idx += c.length; }
        chunks.length = 0;

        const offline = new OfflineAudioContext(1, combined.length, 44100);
        const buf = offline.createBuffer(1, combined.length, 44100);
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

    async function startMusicMode() {
      if (musicModeRunning) return;
      const btn = document.getElementById('musicModeBtn');
      const st = document.getElementById('autoStatus');
      if (btn) btn.disabled = true;
      try {
        await startAudioReactive();
        if (!audioReactiveRunning) return;
        await startMoodRecorder();
        setText('songSourceState', 'Webcam mic');
        musicModeRunning = true;
        if (btn) btn.textContent = 'Music Mode Running';
        setMusicModeUi(true, 'Checking now');
        if (st) st.textContent = 'Beat matching live audio; checking media metadata every 30 seconds.';
        await runMusicRecognitionCycle(true);
        musicRecognitionTimer = setInterval(() => runMusicRecognitionCycle(false), MUSIC_RECOGNITION_INTERVAL_MS);
      } catch (err) {
        if (st) st.textContent = 'Music mode error: ' + err.message;
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    function stopMusicMode() {
      musicModeRunning = false;
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

    async function runMusicRecognitionCycle(force = false) {
      if ((!musicModeRunning && !force) || musicRecognitionBusy) return;
      musicRecognitionBusy = true;
      try {
        status.textContent = 'Checking media metadata...';
        setText('nextMatchState', 'Checking now');
        const result = await matchLightsFromNowPlaying();
        const st = document.getElementById('autoStatus');
        if (result && result.ok && result.now_playing) {
          if (st) st.textContent = `Matched ${result.now_playing.title || 'song'}; beat mode continues.`;
        } else {
          if (st) st.textContent = 'No media metadata detected; beat matching live audio.';
          if (status.textContent === 'Checking media metadata...') status.textContent = 'No media metadata detected; beat matching live audio.';
        }
      } finally {
        if (musicModeRunning) setText('nextMatchState', 'Every 30s');
        musicRecognitionBusy = false;
      }
    }

    async function startAudioReactive() {
      if (audioReactiveRunning) return;
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
      analyser = null;
      audioContext = null;
      frequencyData = null;
      waveformData = null;
    }

    function getActiveColors() {
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

    function updateVu(energy, beat) {
      const level = Math.max(0, Math.min(100, Math.round(energy * 1.35)));
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
      updateVu(analysis.energy, analysis.beat);
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

      drawWaveform(analysis.waveform, analysis.beat);
      drawSpectrum(analysis);
    }

    function drawSpectrum(analysis) {
      const canvas = document.getElementById('spectrumCanvas');
      if (!canvas || !frequencyData || !frequencyData.length) return;
      const ctx = canvas.getContext('2d');
      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);

      const bins = 24;
      const binW = w / bins;
      const step = Math.floor(frequencyData.length / bins);
      
      const colors = getActiveColors();
      const isOn = colors.on && colors.bri > 0;

      for (let i = 0; i < bins; i++) {
        let sum = 0;
        const start = i * step;
        for (let k = 0; k < step; k++) sum += frequencyData[Math.min(frequencyData.length-1, start + k)] || 0;
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
          const clickBin = Math.floor(((ev.clientX - rect.left) / rect.width) * bins);
          creativeBoostBin(clickBin, analysis);
        });
        canvas.title = 'Click a frequency bar to creatively influence the lights';
      }
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
    function creativeBoostBin(binIndex, analysis) {
      // Click spectrum bar → creatively map that frequency energy to color/effect
      const energy = (analysis && analysis.energy) || 0.6;
      const r = Math.round(60 + binIndex * 7 + energy * 80);
      const g = Math.round(30 + (binIndex % 7) * 22);
      const b = Math.round(160 - binIndex * 4 + energy * 60);
      const w = Math.round(energy * 90);
      const spd = Math.round(80 + energy * 130);
      // Send a beat-ish color + safe effect with intensity tied to the bin
      send('beat', { red: r, green: g, blue: b, white: w, brightness: Math.round(120 + energy*90), effect: (binIndex % 5 === 0 ? 9 : 8), speed: spd });
      flashOrb(energy);
    }

    function creativeSpectrumMap() {
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
      send('color', payload); // will use merge internally in some paths
      // also nudge a nice effect
      setTimeout(() => send('fx', {effect: 67, speed: payload.speed}), 80);
    }

    function creativeEnergyPulse() {
      const a = musicAnalysis;
      const strength = Math.max(0.4, a.energy || 0.5);
      const fx = (a.bass > 0.6) ? 2 : (a.treble > 0.55 ? 8 : 12);
      send('beat', {
        red: Math.round(200 * strength), green: Math.round(80 + 90 * a.mid),
        blue: Math.round(255 * a.treble), white: Math.round(60 * strength),
        brightness: Math.round(160 + strength * 70),
        effect: fx,
        speed: Math.round(90 + strength * 120)
      });
      flashOrb(strength + 0.2);
    }

    function creativeEvolve() {
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
      send('beat', {red: color[0], green: color[1], blue: color[2], white: color[3], brightness, effect, speed});
    }

    function resetAnalysisDisplay() {
      Object.assign(musicAnalysis, {energy: 0, rms: 0, bass: 0, mid: 0, treble: 0, beatConfidence: 0, beat: false, bpm: 0, drive: 0, waveform: [], beatTimes: []});
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
      if (autoSt) { autoSt.textContent = text; }
    }

    function updateMoodStatus(mood) {
      if (!mood || !mood.running) return;
      const st = document.getElementById('autoStatus');
      const songSource = document.getElementById('songSourceState');
      const nextMatch = document.getElementById('nextMatchState');
      if (songSource) songSource.textContent = 'Webcam mic';
      if (nextMatch) nextMatch.textContent = 'Continuous';
      if (!st) return;
      if (mood.state === 'ambient') {
        st.textContent = 'No music detected — ambient fallback active.';
      } else if (mood.state === 'recognized' && mood.song) {
        const t = mood.song.title || 'song';
        const a = mood.song.artist || 'unknown artist';
        st.textContent = `Matched: ${t} by ${a}`;
      } else {
        st.textContent = 'Listening for music…';
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

    function updateLightPreview(st, ledInfo) {
      const seg = st.seg && st.seg[0] ? st.seg[0] : {};
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
        ledInfo: ledInfo || currentStripState.ledInfo || null,
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
          const st = payload.state;
          const onOff = st.on ? 'ON' : 'OFF';
          const bri = st.bri ?? '?';
          const seg = st.seg && st.seg[0] ? st.seg[0] : {};
          const col = seg.col && seg.col[0] ? seg.col[0] : [0,0,0,0];
          let display = `Power: ${onOff}\\nBrightness: ${bri}\\nColor: RGBW(${col.join(',')})\\nEffect: ${seg.fx ?? '-'} Speed: ${seg.sx ?? '-'}`;
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
          stateSummary.textContent = `${onOff} | Bri ${bri} | Fx ${seg.fx ?? '-'} @ ${seg.sx ?? '-'}`;
          updateLightPreview(st, ledCapabilities);
          connIndicator.textContent = '🟢';
          connText.textContent = 'Connected';
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

    // Init
    initLedStrip();
    stripRafId = requestAnimationFrame(updateStripFrame);
    loadChatHistory();
    refreshSmartSuggestions();
    setInterval(refreshSmartSuggestions, 30000);
    listSchedule();

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
        if (audioReactiveRunning) vz.textContent = '🎵 live reactive';
        else vz.textContent = audioReactiveRunning || musicModeRunning ? 'active' : 'idle';
      }, 900);
    }
  </script>
</body>
</html>
"""
