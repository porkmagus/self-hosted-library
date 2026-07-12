import { C } from "./types"

export const GLOBAL_CSS = `
  @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@500;700&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap');

  @keyframes spin { to { transform: rotate(360deg); } }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
  @keyframes fadeInScale { from { opacity: 0; transform: scale(0.98); } to { opacity: 1; transform: scale(1); } }
  @keyframes shimmer {
    0% { background-position: -240px 0; }
    100% { background-position: 240px 0; }
  }
  @keyframes pulse { 0%, 100% { opacity: 0.45; } 50% { opacity: 1; } }
  @keyframes glowPulse {
    0%, 100% { box-shadow: 0 0 0 0 ${C.goldGlow}; }
    50% { box-shadow: 0 0 24px 2px ${C.goldGlow}; }
  }

  :root {
    color-scheme: dark;
    --bg: ${C.bg};
    --bg-light: ${C.bgLight};
    --card: ${C.card};
    --card-hover: ${C.cardHover};
    --border: ${C.border};
    --gold: ${C.gold};
    --gold-light: ${C.goldLight};
    --text: ${C.text};
    --text-dim: ${C.textDim};
    --text-muted: ${C.textMuted};
    --radius: 8px;
    --font-display: 'Cinzel', Georgia, serif;
    --font-body: 'Source Serif 4', Georgia, 'Times New Roman', serif;
    --font-ui: 'Source Serif 4', Georgia, serif;
  }

  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body {
    margin: 0;
    min-height: 100vh;
    background:
      radial-gradient(ellipse 90% 55% at 50% -10%, #1a2240 0%, transparent 55%),
      radial-gradient(ellipse 50% 40% at 100% 100%, #1a1530 0%, transparent 45%),
      ${C.bg};
    color: ${C.text};
    font-family: var(--font-body);
    line-height: 1.55;
    -webkit-font-smoothing: antialiased;
  }

  ::-webkit-scrollbar { width: 8px; height: 8px; }
  ::-webkit-scrollbar-track { background: ${C.bgLight}; }
  ::-webkit-scrollbar-thumb {
    background: linear-gradient(180deg, ${C.goldDim}, ${C.borderLight});
    border-radius: 4px;
  }
  ::-webkit-scrollbar-thumb:hover { background: ${C.gold}; }
  ::selection { background: ${C.goldDim}; color: ${C.text}; }

  .text-wrap { overflow-wrap: break-word; word-break: break-word; overflow-x: hidden; }
  a { color: inherit; text-decoration: none; }

  button, input, select, textarea { font-family: inherit; }
  button { font-family: var(--font-ui); }
  button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible, a:focus-visible {
    outline: 2px solid ${C.gold};
    outline-offset: 2px;
  }

  /* Layout */
  .app-shell {
    max-width: 1180px;
    margin: 0 auto;
    padding: 16px 20px 96px;
    min-height: 100vh;
  }
  .app-header {
    text-align: center;
    padding: 28px 0 18px;
    margin-bottom: 8px;
    position: relative;
  }
  .app-header::after {
    content: "";
    display: block;
    height: 1px;
    margin-top: 18px;
    background: linear-gradient(90deg, transparent, ${C.gold}55, transparent);
  }
  .app-title {
    font-family: var(--font-display);
    font-size: clamp(28px, 5vw, 40px);
    font-weight: 700;
    color: ${C.gold};
    letter-spacing: 0.28em;
    margin: 0 0 8px;
    text-shadow: 0 0 40px ${C.goldGlow}, 0 0 2px ${C.goldDim};
  }
  .app-subtitle {
    font-size: 12px;
    color: ${C.textMuted};
    letter-spacing: 0.35em;
    text-transform: uppercase;
    margin: 0;
  }
  .app-nav {
    display: flex;
    gap: 2px;
    margin: 8px 0 28px;
    padding: 4px;
    border-radius: 12px;
    background: rgba(13, 19, 32, 0.72);
    border: 1px solid ${C.border};
    backdrop-filter: blur(12px);
    position: sticky;
    top: 10px;
    z-index: 50;
    overflow-x: auto;
  }
  .app-nav a {
    flex: 1 0 auto;
    text-align: center;
    padding: 10px 18px;
    border-radius: 8px;
    font-size: 12px;
    letter-spacing: 0.16em;
    color: ${C.textMuted};
    transition: color 0.18s ease, background 0.18s ease, box-shadow 0.18s ease;
  }
  .app-nav a:hover { color: ${C.goldLight}; background: rgba(212,175,87,0.06); }
  .app-nav a.active {
    color: ${C.gold};
    background: linear-gradient(180deg, rgba(212,175,87,0.14), rgba(212,175,87,0.05));
    box-shadow: inset 0 0 0 1px rgba(212,175,87,0.28);
  }
  .app-footer-hint {
    margin-top: 40px;
    padding-top: 16px;
    border-top: 1px solid ${C.border};
    color: ${C.textMuted};
    font-size: 11px;
    letter-spacing: 0.08em;
    text-align: center;
    opacity: 0.75;
  }
  .app-footer-hint kbd {
    display: inline-block;
    padding: 1px 6px;
    margin: 0 2px;
    border: 1px solid ${C.borderLight};
    border-bottom-width: 2px;
    border-radius: 4px;
    background: ${C.card};
    color: ${C.textDim};
    font-family: ui-monospace, monospace;
    font-size: 10px;
  }

  /* Primitives */
  .card {
    background: linear-gradient(165deg, ${C.card} 0%, ${C.bgElevated} 100%);
    border: 1px solid ${C.border};
    border-radius: var(--radius);
    padding: 18px 20px;
    transition: border-color 0.18s ease, transform 0.18s ease, box-shadow 0.18s ease;
  }
  .card:hover {
    border-color: rgba(212,175,87,0.28);
    box-shadow: 0 8px 28px rgba(0,0,0,0.28);
  }
  .card-interactive { cursor: pointer; }
  .card-interactive:hover { transform: translateY(-1px); }

  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 11px 22px;
    font-size: 13px;
    letter-spacing: 0.12em;
    border-radius: 8px;
    border: 1px solid ${C.gold};
    background: linear-gradient(180deg, rgba(212,175,87,0.12), rgba(212,175,87,0.04));
    color: ${C.gold};
    cursor: pointer;
    transition: all 0.18s ease;
  }
  .btn:hover:not(:disabled) {
    background: linear-gradient(180deg, rgba(212,175,87,0.22), rgba(212,175,87,0.08));
    box-shadow: 0 0 18px ${C.goldGlow};
    color: ${C.goldLight};
  }
  .btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-ghost {
    border-color: ${C.border};
    background: transparent;
    color: ${C.textDim};
  }
  .btn-ghost:hover:not(:disabled) {
    border-color: ${C.borderLight};
    color: ${C.text};
    box-shadow: none;
    background: rgba(255,255,255,0.03);
  }
  .btn-sm { padding: 7px 14px; font-size: 12px; letter-spacing: 0.1em; }
  .btn-icon {
    width: 34px; height: 34px; padding: 0;
    border-radius: 50%;
  }

  .input, .select {
    width: 100%;
    padding: 13px 16px;
    font-size: 15px;
    color: ${C.text};
    background: ${C.card};
    border: 1px solid ${C.border};
    border-radius: 8px;
    transition: border-color 0.18s ease, box-shadow 0.18s ease;
  }
  .input::placeholder { color: ${C.textMuted}; }
  .input:hover, .select:hover { border-color: ${C.borderLight}; }
  .input:focus, .select:focus {
    border-color: ${C.gold};
    box-shadow: 0 0 0 3px ${C.goldGlow};
    outline: none;
  }

  .page-title {
    font-family: var(--font-display);
    color: ${C.gold};
    letter-spacing: 0.14em;
    font-size: 18px;
    margin: 0 0 18px;
    font-weight: 500;
  }
  .section-label {
    color: ${C.gold};
    font-size: 13px;
    letter-spacing: 0.14em;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .muted { color: ${C.textMuted}; font-size: 13px; }
  .chip-row { display: flex; gap: 0; flex-wrap: wrap; border-bottom: 1px solid ${C.border}; margin-bottom: 20px; }
  .chip {
    padding: 8px 16px;
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: ${C.textMuted};
    font-size: 12px;
    letter-spacing: 0.12em;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .chip:hover { color: ${C.goldLight}; }
  .chip.active {
    color: ${C.gold};
    border-bottom-color: ${C.gold};
    background: linear-gradient(180deg, transparent, rgba(212,175,87,0.06));
  }

  .stat-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
    margin-bottom: 20px;
  }
  .stat-card {
    background: linear-gradient(160deg, ${C.card}, ${C.bgElevated});
    border: 1px solid ${C.border};
    border-radius: 10px;
    padding: 14px 16px;
  }
  .stat-card .label {
    font-size: 10px;
    letter-spacing: 0.16em;
    color: ${C.textMuted};
    margin-bottom: 6px;
  }
  .stat-card .value {
    font-family: var(--font-display);
    font-size: 22px;
    color: ${C.gold};
  }

  .overlay {
    position: fixed; inset: 0; z-index: 1000;
    background: rgba(4, 7, 14, 0.92);
    backdrop-filter: blur(6px);
    animation: fadeIn 0.18s ease;
  }
  .modal-panel {
    background: ${C.card};
    border: 1px solid ${C.border};
    border-radius: 12px;
    padding: 24px;
    max-width: 520px;
    width: 92%;
    box-shadow: 0 24px 80px rgba(0,0,0,0.55), 0 0 0 1px rgba(212,175,87,0.08);
    animation: fadeInScale 0.2s ease;
  }

  .img-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 12px;
  }
  .img-card {
    background: ${C.card};
    border: 1px solid ${C.border};
    border-radius: 10px;
    overflow: hidden;
    cursor: pointer;
    transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
  }
  .img-card:hover {
    transform: translateY(-2px);
    border-color: rgba(212,175,87,0.4);
    box-shadow: 0 12px 28px rgba(0,0,0,0.35);
  }
  .img-card img {
    width: 100%;
    height: 148px;
    object-fit: cover;
    display: block;
    background: ${C.bgLight};
  }

  .search-bar {
    display: flex;
    gap: 10px;
    margin-bottom: 12px;
  }
  .search-bar .input { flex: 1; font-size: 16px; padding: 14px 18px; }

  .toolbar {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 18px;
  }

  .progress {
    width: 100%;
    height: 8px;
    background: ${C.bgLight};
    border: 1px solid ${C.border};
    border-radius: 999px;
    overflow: hidden;
  }
  .progress > span {
    display: block;
    height: 100%;
    background: linear-gradient(90deg, ${C.goldDim}, ${C.gold}, ${C.goldLight});
    border-radius: 999px;
    transition: width 0.25s ease;
  }

  .dropzone {
    border: 2px dashed ${C.border};
    border-radius: 12px;
    padding: 36px 20px;
    text-align: center;
    background: linear-gradient(180deg, ${C.card}, ${C.bgElevated});
    transition: all 0.15s ease;
  }
  .dropzone.active {
    border-color: ${C.gold};
    background: rgba(212,175,87,0.08);
    animation: glowPulse 1.6s ease-in-out infinite;
  }

  .media-item {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px;
    border-radius: 10px;
    background: ${C.card};
    border: 1px solid ${C.border};
    transition: border-color 0.15s ease, transform 0.15s ease;
  }
  .media-item:hover {
    border-color: rgba(212,175,87,0.35);
    transform: translateY(-1px);
  }

  @media (max-width: 720px) {
    .app-shell { padding: 12px 12px 88px; }
    .app-nav a { padding: 10px 12px; letter-spacing: 0.1em; font-size: 11px; }
    .search-bar { flex-direction: column; }
    .img-grid { grid-template-columns: repeat(2, 1fr); }
  }
`

export { C }
