/* =========================================================================
   WX-20 WHISPERX RESOLVE STUDIO — FRONTEND CONTROLLER
   Clean, uncluttered UI preserving 100% of DaVinci Resolve + WhisperX features.
   ========================================================================= */

const MODELS = ['tiny', 'base', 'small', 'medium', 'large-v3'];
const MODEL_HINTS = {
  tiny: 'Сверхбыстрая · черновой вариант',
  base: 'Быстрая · для коротких заметок',
  small: 'Лёгкая · хорошее качество',
  medium: 'Баланс скорости и качества · рек. для RTX 5070',
  'large-v3': 'Максимальная точность · использует кэш'
};
const BATCH_STEPS = [1, 2, 4, 8, 16];

let SETTINGS = {
  themeMode: 'dark',
  winOsOverride: 'auto',
  sourceMode: 'timeline',
  selectedFile: '',
  selectedUrl: '',
  destination: '',
  model: 'medium',
  language: 'ru',
  batch: 4,
  align: true,
  diarize: false,
  maxSpeakers: 4,
  computeType: 'float16',
  hfToken: '',
  whisperxCmd: 'whisperx',
  ytdlpCmd: 'yt-dlp',
  lastSrt: ''
};

let ENV = { gpu: 'CPU', hasCuda: false, ffmpeg: false, whisperx: false, ytdlp: false };
let TIMELINE = { connected: false, label: 'Не подключено', duration: '00:00:00' };
let HISTORY = [];
let ACTIVE_SUB = null;
let LIVE = { running: false, statusText: 'Готово к запуску', progress: 0, stage: 'idle', logs: [] };

let activeTab = 'subs';
let rackCompact = false;
let wasRunning = false;

const shell = document.getElementById('app-shell');
const audioEl = document.getElementById('real-audio');
const osDarkMQ = window.matchMedia('(prefers-color-scheme: dark)');

function api() {
  return window.pywebview && window.pywebview.api ? window.pywebview.api : null;
}

let persistTimer = null;
function saveSettingsDebounced(patch = {}) {
  Object.assign(SETTINGS, patch);
  clearTimeout(persistTimer);
  persistTimer = setTimeout(() => {
    if (api()) api().save_settings(SETTINGS).catch(() => {});
  }, 150);
}

function saveSettingsNow(patch = {}) {
  Object.assign(SETTINGS, patch);
  clearTimeout(persistTimer);
  if (api()) api().save_settings(SETTINGS).catch(() => {});
}

window.addEventListener('beforeunload', () => {
  if (api()) api().save_settings(SETTINGS).catch(() => {});
});

// =========================================================================
// 3-THEME ENGINE (DARK / LIGHT / PROGRAMMATIC WINDOWS 11 MICA)
// =========================================================================
function resolveTheme() {
  if (SETTINGS.themeMode === 'dark') return 'dark';
  if (SETTINGS.themeMode === 'light') return 'light';
  return osDarkMQ.matches ? 'windows-dark' : 'windows-light';
}

function applyTheme() {
  const eff = resolveTheme();
  shell.setAttribute('data-theme', eff);

  ['dark', 'light', 'windows'].forEach(m => {
    const b = document.getElementById('tbtn-' + m);
    if (!b) return;
    const act = SETTINGS.themeMode === m;
    b.style.background = act ? 'var(--accent)' : 'transparent';
    b.style.color = act ? 'var(--accent-ink)' : 'var(--metal-muted)';
  });
  drawMiniWaveform();
}

function setThemeMode(m) {
  SETTINGS.themeMode = m;
  applyTheme();
  saveSettingsDebounced();
  const names = { dark: 'Тёмная (RC-20)', light: 'Светлая', windows: 'Windows 11 (системная)' };
  showToast('Тема: ' + names[m]);
}

osDarkMQ.addEventListener('change', () => {
  if (SETTINGS.themeMode === 'windows') applyTheme();
});

// =========================================================================
// RACK RENDERING & CONTROLS
// =========================================================================
function renderAllControls() {
  // 1. Source Mode Pills & Control Slot
  document.querySelectorAll('[data-smode]').forEach(b => {
    b.classList.toggle('active', b.dataset.smode === SETTINGS.sourceMode);
  });

  const slot = document.getElementById('src-control-slot');
  const baySrc = document.getElementById('bay-src-val');
  const ledRes = document.getElementById('led-resolve');
  const oledText = document.getElementById('oled-text');
  const oledTag = document.getElementById('oled-tag');
  const oledDot = document.getElementById('oled-dot');

  if (SETTINGS.sourceMode === 'timeline') {
    const conn = Boolean(TIMELINE && TIMELINE.connected);
    ledRes.classList.toggle('on', conn);
    baySrc.textContent = conn ? TIMELINE.timeline : 'Resolve выкл.';
    oledText.textContent = conn ? `Resolve: ${TIMELINE.label}` : 'DaVinci Resolve не подключён — откройте таймлайн и нажмите ↻';
    oledTag.textContent = conn ? 'ONLINE' : 'OFFLINE';
    oledDot.style.background = conn ? 'var(--success)' : 'var(--danger)';
    slot.innerHTML = `
      <button onclick="refreshTimeline(true)" class="ch-action-btn">
        <span>${conn ? '↻ ' + TIMELINE.timeline : '↻ Подключить Resolve'}</span>
      </button>
    `;
  } else if (SETTINGS.sourceMode === 'file') {
    const fname = SETTINGS.selectedFile ? SETTINGS.selectedFile.split(/[/\\]/).pop() : '';
    ledRes.classList.toggle('on', Boolean(fname));
    baySrc.textContent = fname || 'Не выбран';
    oledText.textContent = fname ? `Файл: ${fname}` : 'Выберите видео или аудио с компьютера';
    oledTag.textContent = 'FILE';
    oledDot.style.background = fname ? 'var(--led-on)' : 'var(--ch-0-acc)';
    slot.innerHTML = `
      <button onclick="pickMediaFile()" class="ch-action-btn">
        <span class="truncate">${fname || 'Выбрать видео / аудио…'}</span>
      </button>
    `;
  } else {
    const hasUrl = Boolean(SETTINGS.selectedUrl && SETTINGS.selectedUrl.startsWith('http'));
    ledRes.classList.toggle('on', hasUrl);
    baySrc.textContent = hasUrl ? 'YouTube URL' : 'Вставьте URL';
    oledText.textContent = hasUrl ? SETTINGS.selectedUrl : 'Вставьте ссылку YouTube / видео';
    oledTag.textContent = 'URL';
    oledDot.style.background = hasUrl ? 'var(--led-on)' : 'var(--ch-0-acc)';
    slot.innerHTML = `
      <input id="url-inline-input" type="text" value="${escapeAttr(SETTINGS.selectedUrl || '')}"
        placeholder="https://youtube.com/watch?v=..."
        oninput="onUrlInput(this.value)"
        class="c-input font-mono" style="height:30px;font-size:11px;background:rgba(0,0,0,0.38);color:#fff;border-color:rgba(255,255,255,0.18);" />
    `;
  }

  // 2. Model
  const mIdx = Math.max(0, MODELS.indexOf(SETTINGS.model));
  const mRatio = mIdx / (MODELS.length - 1);
  document.getElementById('model-label').textContent = SETTINGS.model.toUpperCase();
  document.getElementById('model-hint').textContent = MODEL_HINTS[SETTINGS.model] || '';
  document.getElementById('bay-model-val').textContent = SETTINGS.model;
  document.getElementById('model-knob-body').style.transform = `rotate(${-135 + mRatio * 270}deg)`;
  document.getElementById('model-knob-arc').setAttribute('stroke-dasharray', `${(mRatio * 94.2).toFixed(1)} 125.6`);

  // 3. Language & Batch
  document.querySelectorAll('[data-lang]').forEach(b => {
    b.classList.toggle('active', b.dataset.lang === SETTINGS.language);
  });
  let bIdx = BATCH_STEPS.indexOf(Number(SETTINGS.batch));
  if (bIdx < 0) bIdx = 2;
  const bRatio = bIdx / (BATCH_STEPS.length - 1);
  const bPct = Math.round(bRatio * 100);
  document.getElementById('batch-inline-val').textContent = BATCH_STEPS[bIdx];
  document.getElementById('batch-fill').style.width = bPct + '%';
  document.getElementById('batch-thumb').style.left = bPct + '%';
  document.getElementById('bay-batch-val').textContent = `batch ${BATCH_STEPS[bIdx]}`;
  document.getElementById('batch-knob-body').style.transform = `rotate(${-135 + bRatio * 270}deg)`;
  document.getElementById('batch-knob-arc').setAttribute('stroke-dasharray', `${(bRatio * 94.2).toFixed(1)} 125.6`);

  // 4. Alignment
  const alignOn = Boolean(SETTINGS.align);
  document.getElementById('led-align').classList.toggle('on', alignOn);
  document.getElementById('bay-align-val').textContent = alignOn ? 'ВКЛ' : 'ВЫКЛ';
  document.getElementById('ch-align-card').classList.toggle('dim', !alignOn);
  document.getElementById('align-desc').textContent = alignOn
    ? 'Точные таймкоды каждого слова и пауз речи для Text+ в Resolve'
    : 'Выключено (--no_align) · только базовые фразы без пословных таймкодов';
}

function escapeAttr(s) {
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

function setSourceMode(mode) {
  SETTINGS.sourceMode = mode;
  renderAllControls();
  saveSettingsDebounced();
  if (mode === 'timeline') refreshTimeline(false);
}

function onUrlInput(val) {
  SETTINGS.selectedUrl = val.trim();
  const hasUrl = SETTINGS.selectedUrl.startsWith('http');
  document.getElementById('led-resolve').classList.toggle('on', hasUrl);
  document.getElementById('bay-src-val').textContent = hasUrl ? 'YouTube URL' : 'Вставьте URL';
  document.getElementById('oled-text').textContent = hasUrl ? SETTINGS.selectedUrl : 'Вставьте ссылку YouTube / видео';
  saveSettingsDebounced();
}

async function pickMediaFile() {
  if (!api()) return;
  const f = await api().pick_media_file();
  if (!f) return;
  SETTINGS.selectedFile = f;
  SETTINGS.sourceMode = 'file';
  renderAllControls();
  saveSettingsDebounced();
}

function stepModel(dir) {
  const idx = Math.max(0, MODELS.indexOf(SETTINGS.model));
  const next = Math.min(MODELS.length - 1, Math.max(0, idx + dir));
  SETTINGS.model = MODELS[next];
  renderAllControls();
  saveSettingsDebounced();
  closeModelDropdown();
}

function selectModel(modelName) {
  if (!MODELS.includes(modelName)) return;
  SETTINGS.model = modelName;
  renderAllControls();
  saveSettingsDebounced();
  closeModelDropdown();
  showToast('Модель: ' + modelName.toUpperCase());
}

function toggleModelDropdown(e) {
  if (e) e.stopPropagation();
  const menu = document.getElementById('model-dropdown');
  const btn = document.getElementById('model-picker-btn');
  if (!menu || !btn) return;
  if (!menu.classList.contains('hidden')) {
    closeModelDropdown();
    return;
  }
  menu.innerHTML = MODELS.map(m => `
    <button type="button" onclick="selectModel('${m}')" class="model-opt ${m === SETTINGS.model ? 'active' : ''}">
      <span class="model-opt-name">${m}</span>
      <span class="model-opt-sub">${MODEL_HINTS[m] || ''}</span>
    </button>
  `).join('');
  const r = btn.getBoundingClientRect();
  menu.style.left = Math.max(8, Math.min(window.innerWidth - 250, r.left - 18)) + 'px';
  menu.style.top = (r.bottom + 6) + 'px';
  menu.style.width = Math.max(230, r.width + 52) + 'px';
  menu.classList.remove('hidden');
}

function closeModelDropdown() {
  const menu = document.getElementById('model-dropdown');
  if (menu) menu.classList.add('hidden');
}

document.addEventListener('click', (e) => {
  if (!e.target.closest('#model-dropdown') && !e.target.closest('#model-picker-btn')) {
    closeModelDropdown();
  }
});

function setLanguage(lang) {
  SETTINGS.language = lang;
  renderAllControls();
  saveSettingsDebounced();
}

function setBatchByIndex(idx) {
  const clamped = Math.min(BATCH_STEPS.length - 1, Math.max(0, idx));
  SETTINGS.batch = BATCH_STEPS[clamped];
  renderAllControls();
  saveSettingsDebounced();
}

function toggleAlign() {
  SETTINGS.align = !SETTINGS.align;
  renderAllControls();
  saveSettingsDebounced();
  showToast(SETTINGS.align ? 'Выравнивание слов: ВКЛ' : 'Выравнивание слов: ВЫКЛ');
}

function toggleRackCompact() {
  rackCompact = !rackCompact;
  document.getElementById('rack-channels').classList.toggle('hidden', rackCompact);
  document.getElementById('rack-knobs').classList.toggle('hidden', rackCompact);
  document.getElementById('collapse-btn').textContent = rackCompact ? '▼ Показать панель' : '▲ Свернуть панель';
  saveSettingsNow({ rackCompact });
}

// =========================================================================
// SMOOTH RECT-CACHED DRAGGING FOR BATCH SLIDER & KNOBS (ZERO JUMPING!)
// =========================================================================
let dragCtx = null;

document.addEventListener('mousedown', (e) => {
  const bs = e.target.closest('#batch-slider');
  if (bs) {
    e.preventDefault();
    const rect = bs.getBoundingClientRect();
    dragCtx = { type: 'batch-slider', rect };
    applyBatchFromRect(e, rect);
    return;
  }
  const mk = e.target.closest('#model-knob');
  if (mk) {
    e.preventDefault();
    dragCtx = { type: 'model-knob', startY: e.clientY, startIdx: Math.max(0, MODELS.indexOf(SETTINGS.model)) };
    return;
  }
  const bk = e.target.closest('#batch-knob');
  if (bk) {
    e.preventDefault();
    let idx = BATCH_STEPS.indexOf(Number(SETTINGS.batch));
    if (idx < 0) idx = 2;
    dragCtx = { type: 'batch-knob', startY: e.clientY, startIdx: idx };
  }
});

document.addEventListener('mousemove', (e) => {
  if (!dragCtx) return;
  if (dragCtx.type === 'batch-slider') {
    applyBatchFromRect(e, dragCtx.rect);
  } else if (dragCtx.type === 'model-knob') {
    const step = Math.round((dragCtx.startY - e.clientY) / 22);
    const next = Math.min(MODELS.length - 1, Math.max(0, dragCtx.startIdx + step));
    if (MODELS[next] !== SETTINGS.model) {
      SETTINGS.model = MODELS[next];
      renderAllControls();
      saveSettingsDebounced();
    }
  } else if (dragCtx.type === 'batch-knob') {
    const step = Math.round((dragCtx.startY - e.clientY) / 22);
    setBatchByIndex(dragCtx.startIdx + step);
  }
});

document.addEventListener('mouseup', () => { dragCtx = null; });

document.addEventListener('wheel', (e) => {
  const mk = e.target.closest('#model-knob');
  if (mk) { e.preventDefault(); stepModel(e.deltaY < 0 ? 1 : -1); return; }
  const bk = e.target.closest('#batch-knob, #batch-slider');
  if (bk) {
    e.preventDefault();
    let idx = BATCH_STEPS.indexOf(Number(SETTINGS.batch));
    if (idx < 0) idx = 2;
    setBatchByIndex(idx + (e.deltaY < 0 ? 1 : -1));
  }
}, { passive: false });

function applyBatchFromRect(e, r) {
  if (!r || r.width <= 0) return;
  const ratio = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  const idx = Math.round(ratio * (BATCH_STEPS.length - 1));
  setBatchByIndex(idx);
}

// =========================================================================
// 4 LIVE COLOR-CODED RC-20 OSCILLOSCOPES
// =========================================================================
let animTick = 0;
function drawVisualizers() {
  const activeEnergy = LIVE.running ? 1.2 : (!audioEl.paused ? 1.05 : 0.42);
  animTick += LIVE.running ? 0.09 : (!audioEl.paused ? 0.075 : 0.022);
  const cs = getComputedStyle(shell);

  for (let idx = 0; idx < 4; idx++) {
    const c = document.getElementById('vis-' + idx);
    if (!c) continue;
    const rect = c.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 10) continue;
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(rect.width * dpr) || c.height !== Math.round(rect.height * dpr)) {
      c.width = Math.round(rect.width * dpr);
      c.height = Math.round(rect.height * dpr);
    }
    const ctx = c.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const W = rect.width, H = rect.height;
    ctx.clearRect(0, 0, W, H);

    const col = cs.getPropertyValue(`--ch-${idx}-acc`).trim() || '#FFFFFF';
    ctx.strokeStyle = col;
    ctx.fillStyle = col;

    if (idx === 0) {
      // Source waveform (strictly inside [3, H-3])
      const on = SETTINGS.sourceMode !== 'timeline' || TIMELINE.connected;
      ctx.globalAlpha = on ? 0.9 : 0.25;
      ctx.lineWidth = 1.5;
      const mid = H * 0.5;
      const maxAmp = Math.max(4, H * 0.42);
      for (let x = 6; x < W - 6; x += 4) {
        const amp = Math.min(maxAmp, Math.abs(Math.sin(x * 0.13 + animTick * 2.1) * Math.cos(x * 0.05 - animTick)) * maxAmp * Math.min(1.0, activeEnergy));
        ctx.beginPath(); ctx.moveTo(x, mid - amp); ctx.lineTo(x, mid + amp); ctx.stroke();
      }
    } else if (idx === 1) {
      // Model dual glowing tubes (strictly inside [3, H-3])
      const mFactor = 0.25 + (Math.max(0, MODELS.indexOf(SETTINGS.model)) / 4) * 0.75;
      ctx.globalAlpha = 0.92;
      const cx = W / 2;
      const drawTube = (tx, ty, tw, th, p) => {
        ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.roundRect(tx - tw / 2, ty, tw, th, [tw / 2, tw / 2, 2, 2]); ctx.stroke();
        ctx.lineWidth = 1.0; ctx.strokeRect(tx - tw * 0.24, ty + th * 0.34, tw * 0.48, th * 0.46);
        ctx.save();
        ctx.globalAlpha = 0.22 + mFactor * p * 0.65;
        const gh = th * 0.42 * mFactor;
        ctx.fillRect(tx - tw * 0.18, ty + th * 0.78 - gh, tw * 0.36, gh);
        ctx.restore();
      };
      const pulse = 0.5 + 0.5 * Math.sin(animTick * 2.3);
      drawTube(cx - 12, 4, 15, Math.max(20, H - 8), 0.5 + pulse * 0.5);
      drawTube(cx + 12, 8, 15, Math.max(16, H - 12), 0.5 + (1 - pulse) * 0.5);
    } else if (idx === 2) {
      // Language & Batch digital wave (strictly clamped inside [4, H-4])
      ctx.globalAlpha = 0.88;
      ctx.lineWidth = 1.5;
      const bRatio = (Math.max(0, BATCH_STEPS.indexOf(Number(SETTINGS.batch))) + 1) / BATCH_STEPS.length;
      const maxDev = Math.max(6, H * 0.36);
      ctx.beginPath();
      for (let x = 6; x < W - 6; x += 4) {
        const raw = Math.sin(x * 0.08 + animTick * 1.8) * Math.sin(x * 0.025) * maxDev * (0.35 + bRatio * Math.min(1.0, activeEnergy) * 0.62);
        const y = Math.min(H - 4, Math.max(4, H * 0.5 + Math.round(raw / 4) * 4));
        if (x === 6) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();
    } else {
      // Align twin voiceprint rings (strictly inside H)
      ctx.globalAlpha = SETTINGS.align ? 0.9 : 0.2;
      ctx.lineWidth = 1.5;
      const baseR = Math.min(13, H * 0.26);
      const drawRing = (bx, by, rad, phase) => {
        ctx.beginPath();
        for (let a = 0; a <= Math.PI * 2 + 0.2; a += 0.25) {
          const w = Math.sin(a * 3 + animTick * 1.8 + phase) * 2.0 * Math.min(1.0, activeEnergy);
          const rx = bx + Math.cos(a) * (rad + w);
          const ry = by + Math.sin(a) * (rad * 0.85 - w * 0.6);
          if (a === 0) ctx.moveTo(rx, ry); else ctx.lineTo(rx, ry);
        }
        ctx.closePath(); ctx.stroke();
      };
      drawRing(W * 0.42, H * 0.5, baseR, 0);
      drawRing(W * 0.58, H * 0.5, baseR * 0.94, 2.1);
    }
  }
  requestAnimationFrame(drawVisualizers);
}

// =========================================================================
// MAIN ACTIONS (RESOLVE TIMELINE, START/STOP, CAPTION EDITOR, SRT VIEW)
// =========================================================================
async function refreshTimeline(showFeedback) {
  if (!api()) return;
  TIMELINE = await api().refresh_timeline();
  renderAllControls();
  if (showFeedback) {
    showToast(TIMELINE.connected ? `Подключено: ${TIMELINE.label}` : (TIMELINE.error || 'DaVinci Resolve не отвечает'));
  }
}

async function toggleStartStop() {
  if (!api()) return;
  if (LIVE.running) {
    await api().stop_job();
    showToast('Остановка обработки...');
    return;
  }
  const res = await api().start_job(SETTINGS);
  if (!res.ok) {
    showToast(res.error || 'Не удалось запустить обработку');
    return;
  }
  switchTab('log');
  pollLiveStatus();
}

async function openCaptionEditor(customPath = '') {
  if (!api()) return;
  const target = customPath || (ACTIVE_SUB ? ACTIVE_SUB.srtPath : '') || SETTINGS.lastSrt || '';
  const res = await api().ed_open(target);
  if (res && res.ok) {
    applyEditorSnapshot(res, 0);
    document.getElementById('view-main').classList.add('hidden');
    document.getElementById('view-editor').classList.remove('hidden');
    closeModelDropdown();
  } else {
    showToast((res && res.error) || 'Не удалось открыть редактор');
  }
}

function closeEditorView() {
  closeEdPopovers();
  document.getElementById('view-editor').classList.add('hidden');
  document.getElementById('view-main').classList.remove('hidden');
}

async function openExistingSrt() {
  if (!api()) return;
  const res = await api().pick_existing_srt(false);
  if (!res || !res.ok) return;
  ACTIVE_SUB = res.subtitle;
  SETTINGS.lastSrt = ACTIVE_SUB.srtPath;
  setupMiniPlayer(ACTIVE_SUB);
  renderSubtitles();
  switchTab('subs');
  showToast('Открыт SRT: ' + ACTIVE_SUB.title);
}

async function openDestFolder() {
  if (!api()) return;
  const res = await api().open_destination_folder();
  if (res && !res.ok) showToast(res.error);
}

function switchTab(tab) {
  activeTab = tab;
  document.getElementById('tab-subs').classList.toggle('hidden', tab !== 'subs');
  document.getElementById('tab-log').classList.toggle('hidden', tab !== 'log');
  document.querySelectorAll('[data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  document.getElementById('sub-search').classList.toggle('hidden', tab !== 'subs');
}

function fmtSec(s) {
  const v = Math.max(0, s || 0);
  const m = Math.floor(v / 60).toString().padStart(2, '0');
  const r = Math.floor(v % 60).toString().padStart(2, '0');
  return `${m}:${r}`;
}

function renderSubtitles() {
  const wrap = document.getElementById('tab-subs');
  const countEl = document.getElementById('sub-count');
  const copyBtn = document.getElementById('copy-srt-btn');

  if (!ACTIVE_SUB || !ACTIVE_SUB.segments || !ACTIVE_SUB.segments.length) {
    countEl.textContent = '';
    copyBtn.classList.add('hidden');
    wrap.innerHTML = `
      <div class="empty-hero">
        <div class="font-display" style="font-size:17px;font-weight:800;color:var(--text-main);">Готово к созданию субтитров</div>
        <div style="font-size:12.5px;">Выберите источник сверху (открытый таймлайн DaVinci Resolve или файл с ПК) и нажмите <b>«▶ Создать SRT»</b> — результат появится здесь и сохранится в папку SRT.</div>
        <div style="display:flex;gap:8px;margin-top:6px;">
          <button onclick="refreshTimeline(true)" class="btn-secondary">↻ Подключить Resolve</button>
          <button onclick="openExistingSrt()" class="btn-secondary">Открыть готовый SRT</button>
        </div>
      </div>
    `;
    return;
  }

  const q = (document.getElementById('sub-search').value || '').trim().toLowerCase();
  const segs = ACTIVE_SUB.segments.filter(s => !q || (s.text || '').toLowerCase().includes(q));
  countEl.textContent = `(${segs.length})`;
  copyBtn.classList.remove('hidden');

  wrap.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;padding-bottom:6px;border-bottom:1px solid var(--border-line);">
      <div style="font-size:12px;color:var(--text-sec);">
        <b style="color:var(--text-main);">${ACTIVE_SUB.title}.srt</b>
        ${ACTIVE_SUB.hasWordsJson ? '<span class="font-mono" style="margin-left:8px;color:var(--success);font-size:11px;">● пословные таймкоды готовы</span>' : ''}
      </div>
      <button onclick="openCaptionEditor()" class="etch-btn sm">Открыть в Редакторе субтитров →</button>
    </div>
  ` + segs.map(s => `
    <div class="sub-row">
      <button onclick="seekAudio(${s.start})" class="sub-tc" title="Перейти к ${fmtSec(s.start)}">${fmtSec(s.start)} – ${fmtSec(s.end)}</button>
      <div class="sub-text">${s.speaker ? `<b style="color:var(--accent);margin-right:6px;">${s.speaker}:</b>` : ''}${s.text}</div>
    </div>
  `).join('');
}

function copyCurrentSubtitles() {
  if (!ACTIVE_SUB) return;
  const txt = ACTIVE_SUB.segments.map(s => s.text).join('\n');
  navigator.clipboard?.writeText(txt).catch(() => {});
  showToast('Текст субтитров скопирован');
}

// =========================================================================
// OPTIONAL MINI AUDIO PLAYER (ONLY SHOWN WHEN LOCAL AUDIO FILE EXISTS)
// =========================================================================
function setupMiniPlayer(sub) {
  const bar = document.getElementById('mini-player');
  audioEl.pause();
  document.getElementById('play-btn').textContent = '▶ PLAY';
  if (sub && sub.audioUrl) {
    bar.classList.remove('hidden');
    audioEl.src = sub.audioUrl;
    audioEl.load();
    requestAnimationFrame(drawMiniWaveform);
  } else {
    bar.classList.add('hidden');
    audioEl.removeAttribute('src');
  }
}

function togglePlay() {
  if (!audioEl.src) return;
  if (audioEl.paused) {
    audioEl.play().then(() => { document.getElementById('play-btn').textContent = '❚❚ PAUSE'; }).catch(() => {});
  } else {
    audioEl.pause();
    document.getElementById('play-btn').textContent = '▶ PLAY';
  }
}

function seekAudio(sec) {
  if (!audioEl.src || !isFinite(audioEl.duration)) return;
  audioEl.currentTime = Math.min(audioEl.duration, Math.max(0, sec));
  drawMiniWaveform();
}

function drawMiniWaveform() {
  const c = document.getElementById('wave-canvas');
  const w = document.getElementById('wave-wrap');
  if (!c || !w || document.getElementById('mini-player').classList.contains('hidden')) return;
  const rect = w.getBoundingClientRect();
  if (rect.width < 10) return;
  const dpr = window.devicePixelRatio || 1;
  c.width = Math.round(rect.width * dpr);
  c.height = Math.round(rect.height * dpr);
  const ctx = c.getContext('2d');
  ctx.scale(dpr, dpr);
  const W = rect.width, H = rect.height;
  ctx.clearRect(0, 0, W, H);
  const bars = (ACTIVE_SUB && ACTIVE_SUB.bars && ACTIVE_SUB.bars.length) ? ACTIVE_SUB.bars : Array(90).fill(0.35);
  const ratio = (isFinite(audioEl.duration) && audioEl.duration > 0) ? audioEl.currentTime / audioEl.duration : 0;
  const px = ratio * W;
  const acc = getComputedStyle(shell).getPropertyValue('--accent').trim() || '#6BC7FF';
  const step = W / bars.length;
  bars.forEach((a, i) => {
    const x = i * step;
    const bh = Math.max(3, a * (H - 6));
    ctx.fillStyle = x <= px ? acc : 'rgba(140,150,168,0.3)';
    ctx.fillRect(x, (H - bh) / 2, Math.max(1.5, step - 1.5), bh);
  });
}

document.getElementById('wave-wrap').addEventListener('click', (e) => {
  if (!isFinite(audioEl.duration) || audioEl.duration <= 0) return;
  const r = e.currentTarget.getBoundingClientRect();
  seekAudio(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * audioEl.duration);
});

audioEl.addEventListener('timeupdate', () => {
  document.getElementById('player-time').textContent = `${fmtSec(audioEl.currentTime)} / ${fmtSec(audioEl.duration || 0)}`;
  drawMiniWaveform();
});
audioEl.addEventListener('ended', () => { document.getElementById('play-btn').textContent = '▶ PLAY'; });

// =========================================================================
// SETTINGS MODAL (ALL SECONDARY / ADVANCED FUNCTIONS NEATLY TUCKED HERE)
// =========================================================================
function renderDiagnostics() {
  const b = (ok, txt) => `<span style="color:${ok ? 'var(--success)' : 'var(--danger)'};font-weight:700;">${ok ? '● ' : '○ '}${txt}</span>`;
  document.getElementById('diag-grid').innerHTML = `
    <div class="c-card"><div style="color:var(--text-muted);">GPU / CUDA</div><div class="font-mono" style="margin-top:2px;">${b(ENV.hasCuda, ENV.gpu)}</div></div>
    <div class="c-card"><div style="color:var(--text-muted);">WhisperX</div><div class="font-mono" style="margin-top:2px;">${b(ENV.whisperx, ENV.whisperx ? 'Подключён' : 'Не найден')}</div></div>
    <div class="c-card"><div style="color:var(--text-muted);">FFmpeg</div><div class="font-mono" style="margin-top:2px;">${b(ENV.ffmpeg, ENV.ffmpeg ? 'Подключён' : 'Не найден')}</div></div>
    <div class="c-card"><div style="color:var(--text-muted);">yt-dlp (YouTube)</div><div class="font-mono" style="margin-top:2px;">${b(ENV.ytdlp, ENV.ytdlp ? 'Подключён' : 'Не найден')}</div></div>
  `;
}

function renderHistoryInModal() {
  const wrap = document.getElementById('history-list');
  if (!HISTORY.length) {
    wrap.innerHTML = `<div style="font-size:11.5px;color:var(--text-muted);">История пока пуста</div>`;
    return;
  }
  wrap.innerHTML = HISTORY.slice(0, 10).map(h => `
    <button onclick="openHistoryItem('${escapeAttr(h.srtPath)}', '${escapeAttr(h.audioPath || '')}')" class="c-card" style="display:flex;justify-content:space-between;align-items:center;cursor:pointer;text-align:left;padding:7px 10px;">
      <span class="truncate" style="font-size:12px;font-weight:600;">${h.title}.srt</span>
      <span class="font-mono" style="font-size:10.5px;color:var(--text-muted);flex-shrink:0;margin-left:8px;">${h.model} · ${h.date}</span>
    </button>
  `).join('');
}

async function openHistoryItem(srtPath, audioPath) {
  if (!api()) return;
  const res = await api().load_history_item(srtPath, audioPath);
  if (!res.ok) { showToast(res.error); return; }
  ACTIVE_SUB = res.subtitle;
  setupMiniPlayer(ACTIVE_SUB);
  renderSubtitles();
  closeSettingsModal();
  switchTab('subs');
}

function openSettingsModal() {
  document.getElementById('set-dest').value = SETTINGS.destination || '';
  document.getElementById('set-diarize').checked = Boolean(SETTINGS.diarize);
  document.getElementById('set-hf-token').value = SETTINGS.hfToken || '';
  document.getElementById('set-max-spk').value = SETTINGS.maxSpeakers || 4;
  document.getElementById('set-compute').value = SETTINGS.computeType || 'float16';
  document.getElementById('set-wx-cmd').value = (SETTINGS.whisperxCmd && SETTINGS.whisperxCmd !== 'whisperx') ? SETTINGS.whisperxCmd : (ENV.whisperxPath || 'whisperx');
  document.getElementById('set-yt-cmd').value = (SETTINGS.ytdlpCmd && SETTINGS.ytdlpCmd !== 'yt-dlp') ? SETTINGS.ytdlpCmd : (ENV.ytdlpPath || 'yt-dlp');
  renderDiagnostics();
  renderHistoryInModal();
  document.getElementById('modal-settings').classList.remove('hidden');
}

function closeSettingsModal() { document.getElementById('modal-settings').classList.add('hidden'); }

async function browseDestination() {
  if (!api()) return;
  const f = await api().pick_destination_folder();
  if (f) document.getElementById('set-dest').value = f;
}

async function browseExe(inputId) {
  if (!api()) return;
  const p = await api().pick_executable();
  if (p) document.getElementById(inputId).value = p;
}

async function refreshDiagnostics() {
  if (!api()) return;
  ENV = await api().refresh_env();
  renderDiagnostics();
  showToast('Диагностика обновлена');
}

async function saveSettingsModal() {
  if (!api()) return;
  const patch = {
    destination: document.getElementById('set-dest').value.trim(),
    diarize: document.getElementById('set-diarize').checked,
    hfToken: document.getElementById('set-hf-token').value.trim(),
    maxSpeakers: Math.max(1, Number(document.getElementById('set-max-spk').value || 4)),
    computeType: document.getElementById('set-compute').value,
    whisperxCmd: document.getElementById('set-wx-cmd').value.trim() || 'whisperx',
    ytdlpCmd: document.getElementById('set-yt-cmd').value.trim() || 'yt-dlp'
  };
  const res = await api().save_settings(patch);
  SETTINGS = res.settings || SETTINGS;
  ENV = res.env || ENV;
  closeSettingsModal();
  showToast('Настройки сохранены');
}

// =========================================================================
// LIVE POLLING & BOOTSTRAP
// =========================================================================
async function pollLiveStatus() {
  if (!api()) return;
  try {
    const live = await api().get_live_status();
    LIVE = live;
    HISTORY = live.history || HISTORY;

    const btn = document.getElementById('main-run-btn');
    btn.textContent = live.running ? '■ Остановить' : '▶ Создать SRT';
    btn.classList.toggle('stop', live.running);

    document.getElementById('status-text').textContent = live.statusText || 'Готово к запуску';
    document.getElementById('status-pct').textContent = live.progress > 0 ? `${live.progress}%` : '';
    document.getElementById('progress-fill').style.width = `${live.progress || 0}%`;

    if (live.logs && live.logs.length) {
      const pre = document.getElementById('log-output');
      pre.textContent = live.logs.join('\n');
      pre.scrollTop = pre.scrollHeight;
    }

    if (wasRunning && !live.running && live.stage === 'done' && live.activeSubtitle) {
      ACTIVE_SUB = live.activeSubtitle;
      SETTINGS.lastSrt = ACTIVE_SUB.srtPath;
      setupMiniPlayer(ACTIVE_SUB);
      renderSubtitles();
      switchTab('subs');
      showToast('Субтитры созданы: ' + ACTIVE_SUB.title + '.srt');
    }
    wasRunning = live.running;
  } catch (_) {}
}

let toastTimer = null;
function showToast(msg) {
  const el = document.getElementById('toast');
  document.getElementById('toast-msg').textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2500);
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { closeSettingsModal(); closeModelDropdown(); closeEdPopovers(); closeOrthoModal(); }
});

document.addEventListener('click', (e) => {
  if (!e.target.closest('.ed-popover') && !e.target.closest('#ed-save-menu-btn') && !e.target.closest('#ed-style-menu-btn')) {
    closeEdPopovers();
  }
});

// =========================================================================
// INTEGRATED RC-20 CAPTION EDITOR CONTROLLER
// =========================================================================
let ED = {
  rows: [],
  activeIdx: -1,
  fonts: {},
  path: '',
  behindPerson: true,
  busy: false
};

function closeEdPopovers() {
  document.getElementById('ed-pop-save')?.classList.add('hidden');
  document.getElementById('ed-pop-style')?.classList.add('hidden');
}

function toggleEdPopover(kind, e) {
  if (e) e.stopPropagation();
  const id = kind === 'save' ? 'ed-pop-save' : 'ed-pop-style';
  const btnId = kind === 'save' ? 'ed-save-menu-btn' : 'ed-style-menu-btn';
  const menu = document.getElementById(id);
  const wasOpen = !menu.classList.contains('hidden');
  closeEdPopovers();
  if (wasOpen) return;
  const r = document.getElementById(btnId).getBoundingClientRect();
  menu.style.top = (r.bottom + 6) + 'px';
  menu.style.left = Math.max(8, Math.min(window.innerWidth - 260, r.right - 240)) + 'px';
  menu.classList.remove('hidden');
}

function setEdStatus(msg) {
  document.getElementById('ed-status-text').textContent = msg || '';
}

function applyEditorSnapshot(snap, preferredIdx = null) {
  if (!snap || !snap.ok) {
    if (snap && snap.error) { setEdStatus('Ошибка: ' + snap.error); showToast(snap.error); }
    return;
  }
  ED.rows = snap.rows || [];
  ED.path = snap.path || '';
  if (snap.fonts && Object.keys(snap.fonts).length) {
    ED.fonts = snap.fonts;
    populateEdFontList();
  }
  document.getElementById('ed-undo-layout-btn').classList.toggle('hidden', !snap.canUndoLayout);
  document.getElementById('ed-undo-words-btn').classList.toggle('hidden', !snap.canUndoWords);
  document.getElementById('ortho-undo-btn')?.classList.toggle('hidden', !snap.canUndoOrtho);

  const fname = ED.path ? ED.path.split(/[/\\]/).pop() : 'Новый проект';
  document.getElementById('ed-oled-title').textContent = fname;
  document.getElementById('ed-rows-count').textContent = `${ED.rows.length} реплик`;
  if (snap.message) setEdStatus(snap.message);

  let nextIdx = preferredIdx !== null ? preferredIdx : (snap.selectIndex !== undefined ? snap.selectIndex : ED.activeIdx);
  if (ED.rows.length === 0) nextIdx = -1;
  else if (nextIdx < 0 || nextIdx >= ED.rows.length) nextIdx = 0;

  ED.activeIdx = nextIdx;
  renderEdList();
  loadEdInspector();
}

function populateEdFontList() {
  const dl = document.getElementById('ed-fonts-datalist');
  if (!dl) return;
  const names = Object.keys(ED.fonts).sort((a, b) => a.localeCompare(b));
  dl.innerHTML = names.map(f => `<option value="${escapeAttr(f)}"></option>`).join('');
}

function populateEdStyleList(fontName) {
  const dl = document.getElementById('ed-styles-datalist');
  if (!dl) return;
  const styles = ED.fonts[fontName] || ['Regular', 'Bold', 'Italic', 'Bold Italic'];
  dl.innerHTML = styles.map(s => `<option value="${escapeAttr(s)}"></option>`).join('');
}

function renderEdList() {
  const wrap = document.getElementById('ed-list');
  const q = (document.getElementById('ed-search').value || '').trim().toLowerCase();
  if (!ED.rows.length) {
    wrap.innerHTML = `<div style="margin:auto;text-align:center;padding:24px;color:var(--text-muted);font-size:12px;">Нет реплик. Нажмите <b>«Открыть»</b> или <b>«+ Добавить»</b>.</div>`;
    return;
  }
  wrap.innerHTML = ED.rows.map((r, i) => {
    if (q && !(r.text || '').toLowerCase().includes(q)) return '';
    const act = i === ED.activeIdx;
    const badges = [];
    if (r.reveal && r.reveal.length) badges.push(`<span style="color:var(--ch-1-acc);">накопление</span>`);
    if (r.reference_preset) badges.push(`<span style="color:var(--ch-3-acc);">фактура</span>`);
    return `
      <button type="button" onclick="edSelectRow(${i})" class="ed-item ${act ? 'active' : ''}">
        <div class="font-mono" style="display:flex;justify-content:space-between;font-size:10.5px;color:var(--text-muted);">
          <span style="color:var(--accent);">#${i + 1} · ${r.startText} → ${r.endText}</span>
          <span>${badges.join(' · ')}</span>
        </div>
        <div class="truncate" style="font-size:12.5px;color:var(--text-main);">${(r.text || '').replace(/\n/g, ' / ')}</div>
      </button>
    `;
  }).join('');
}

function loadEdInspector() {
  const badge = document.getElementById('ed-active-badge');
  const presetPill = document.getElementById('ed-preset-pill');
  if (ED.activeIdx < 0 || ED.activeIdx >= ED.rows.length) {
    badge.textContent = 'Не выбрана';
    presetPill.textContent = 'Стандарт';
    document.getElementById('ed-f-text').value = '';
    replayEdPreview();
    return;
  }
  const r = ED.rows[ED.activeIdx];
  badge.textContent = `Реплика #${ED.activeIdx + 1} из ${ED.rows.length}`;
  presetPill.textContent = r.presetLabel || 'Стандарт';
  document.getElementById('ed-f-text').value = r.text || '';
  document.getElementById('ed-f-start').value = r.startText || '';
  document.getElementById('ed-f-end').value = r.endText || '';
  document.getElementById('ed-f-font').value = r.font || 'Arial';
  populateEdStyleList(r.font || 'Arial');
  document.getElementById('ed-f-style').value = r.style || 'Bold';
  document.getElementById('ed-f-color').value = r.color || '#F5F3ED';
  if (/^#[0-9a-fA-F]{6}$/.test(r.color || '')) document.getElementById('ed-f-color-pick').value = r.color;
  document.getElementById('ed-f-size').value = r.size ?? 0.07;
  document.getElementById('ed-f-side').value = r.side || 'auto';
  document.getElementById('ed-f-x').value = r.x ?? 0.5;
  document.getElementById('ed-f-y').value = r.y ?? 0.22;
  document.getElementById('ed-f-anim').value = r.animation || 'Без анимации';
  document.getElementById('ed-f-frames').value = r.frames ?? 5;
  replayEdPreview();
}

// =========================================================================
// DAVINCI RESOLVE-ACCURATE 16:9 LIVE PREVIEW CANVAS (ANIMATIONS + TEXTURE!)
// =========================================================================
const ED_PREV = {
  playing: true,
  centerMode: true,
  startMs: performance.now(),
  pausedElapsedMs: 0,
  offscreen: document.createElement('canvas'),
  distressOff: document.createElement('canvas')
};

function toggleEdPreviewCenter() {
  ED_PREV.centerMode = !ED_PREV.centerMode;
  const btn = document.getElementById('ed-prev-pos-btn');
  if (btn) btn.textContent = ED_PREV.centerMode ? 'Центр' : 'Поз. X/Y';
  saveSettingsNow({ edPreviewCenter: ED_PREV.centerMode });
  updateEdPreview();
}

function replayEdPreview() {
  ED_PREV.startMs = performance.now();
  ED_PREV.pausedElapsedMs = 0;
  updateEdPreview();
}

function toggleEdPreviewPlay() {
  if (ED_PREV.playing) {
    ED_PREV.pausedElapsedMs = performance.now() - ED_PREV.startMs;
    ED_PREV.playing = false;
    document.getElementById('ed-prev-play').textContent = '▶';
  } else {
    ED_PREV.startMs = performance.now() - ED_PREV.pausedElapsedMs;
    ED_PREV.playing = true;
    document.getElementById('ed-prev-play').textContent = '❚❚';
  }
}

function updateEdPreview() {
  // Forces immediate frame paint on edit
  renderEdPreviewFrame(performance.now());
}

function _rgbToHex(inputs, fallback = '#F5F3ED') {
  if (!inputs) return fallback;
  const r = Math.round(Math.max(0, Math.min(1, Number(inputs.Red1 ?? 0.96))) * 255);
  const g = Math.round(Math.max(0, Math.min(1, Number(inputs.Green1 ?? 0.95))) * 255);
  const b = Math.round(Math.max(0, Math.min(1, Number(inputs.Blue1 ?? 0.93))) * 255);
  return `rgb(${r},${g},${b})`;
}

function _applyDistressTexture(ctx, W, H, seedVal, strength) {
  // Procedural stepped AnimatedInkWear / DistressLettering alpha erosion matching Fusion FastNoise
  ctx.save();
  ctx.globalCompositeOperation = 'destination-out';
  let s = Math.floor(seedVal * 9301 + 49297) % 233280;
  const rnd = () => { s = (s * 9301 + 49297) % 233280; return s / 233280; };
  const speckles = Math.round(W * H * 0.011 * strength);
  for (let i = 0; i < speckles; i++) {
    const px = rnd() * W;
    const py = rnd() * H;
    const rad = 0.55 + rnd() * 1.45;
    ctx.fillStyle = `rgba(0,0,0,${(0.28 + rnd() * 0.52).toFixed(2)})`;
    ctx.fillRect(px, py, rad, rad * (0.7 + rnd() * 0.8));
  }
  // Fine horizontal paper/ink wear scratches
  const scratches = Math.round(14 * strength);
  for (let i = 0; i < scratches; i++) {
    const px = rnd() * W;
    const py = rnd() * H;
    const len = 4 + rnd() * 16;
    ctx.fillStyle = `rgba(0,0,0,${(0.18 + rnd() * 0.35).toFixed(2)})`;
    ctx.fillRect(px, py, len, 0.8);
  }
  ctx.restore();
}

function renderEdPreviewFrame(nowMs) {
  const edView = document.getElementById('view-editor');
  if (!edView || edView.classList.contains('hidden')) return;
  const canvas = document.getElementById('ed-preview-canvas');
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  if (rect.width < 20 || rect.height < 20) return;

  const dpr = window.devicePixelRatio || 1;
  const cw = Math.round(rect.width * dpr);
  const ch = Math.round(rect.height * dpr);
  if (canvas.width !== cw || canvas.height !== ch) {
    canvas.width = cw;
    canvas.height = ch;
  }
  const W = rect.width;
  const H = rect.height;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  // 1. Subtle 16:9 Resolve composition grid & safe-title margins
  ctx.save();
  ctx.strokeStyle = 'rgba(255,255,255,0.08)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(W / 3, 0); ctx.lineTo(W / 3, H);
  ctx.moveTo((W * 2) / 3, 0); ctx.lineTo((W * 2) / 3, H);
  ctx.moveTo(0, H / 3); ctx.lineTo(W, H / 3);
  ctx.moveTo(0, (H * 2) / 3); ctx.lineTo(W, (H * 2) / 3);
  ctx.stroke();
  ctx.setLineDash([4, 4]);
  ctx.strokeStyle = 'rgba(255,255,255,0.11)';
  ctx.strokeRect(W * 0.05, H * 0.05, W * 0.90, H * 0.90);
  ctx.restore();

  const row = (ED.activeIdx >= 0 && ED.activeIdx < ED.rows.length) ? ED.rows[ED.activeIdx] : null;
  const textRaw = (document.getElementById('ed-f-text')?.value || '').trim();
  const badgeEl = document.getElementById('ed-prev-badge');
  const barFill = document.getElementById('ed-prev-bar-fill');

  if (!row || !textRaw) {
    ctx.fillStyle = 'rgba(180,192,210,0.45)';
    ctx.font = '600 12px "Segoe UI", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Выберите реплику для превью Resolve', W / 2, H / 2);
    if (badgeEl) badgeEl.textContent = 'DaVinci Resolve 16:9';
    if (barFill) barFill.style.width = '0%';
    return;
  }

  // Read live form values so edits show instantaneously
  const font = document.getElementById('ed-f-font').value || row.font || 'Arial';
  const styleStr = (document.getElementById('ed-f-style').value || row.style || 'Bold').toLowerCase();
  const colorHex = document.getElementById('ed-f-color').value || row.color || '#F5F3ED';
  const sizeVal = Math.max(0.015, Math.min(0.4, Number(document.getElementById('ed-f-size').value) || row.size || 0.07));
  const sideVal = document.getElementById('ed-f-side').value || row.side || 'auto';
  const xVal = Math.max(0.0, Math.min(1.0, Number(document.getElementById('ed-f-x').value) ?? row.x ?? 0.5));
  const yVal = Math.max(0.0, Math.min(1.0, Number(document.getElementById('ed-f-y').value) ?? row.y ?? 0.22));
  const animName = document.getElementById('ed-f-anim').value || row.animation || 'Без анимации';
  const animFrames = Math.max(1, Number(document.getElementById('ed-f-frames').value) || row.frames || 5);
  const preset = row.reference_preset || null;
  const revealMs = Array.isArray(row.reveal) ? row.reveal : [];

  const fps = 24;
  const clipDurMs = Math.max(1400, Math.min(4500, (row.end - row.start) || 2200));
  const loopPeriodMs = clipDurMs + 450;
  const rawElapsed = ED_PREV.playing ? (nowMs - ED_PREV.startMs) : ED_PREV.pausedElapsedMs;
  const elapsedMs = rawElapsed % loopPeriodMs;
  const curFrame = (elapsedMs / 1000) * fps;
  if (barFill) barFill.style.width = `${Math.min(100, (elapsedMs / clipDurMs) * 100).toFixed(1)}%`;

  const weight = styleStr.includes('black') || styleStr.includes('super') || styleStr.includes('heavy') ? '900'
    : styleStr.includes('bold') ? '800'
    : styleStr.includes('semibold') || styleStr.includes('medium') ? '600' : '400';
  const italic = styleStr.includes('italic') ? 'italic ' : '';

  // Offscreen text layer so distress grain & shadow composite cleanly
  const off = ED_PREV.offscreen;
  if (off.width !== cw || off.height !== ch) { off.width = cw; off.height = ch; }
  const octx = off.getContext('2d');
  octx.setTransform(dpr, 0, 0, dpr, 0, 0);
  octx.clearRect(0, 0, W, H);

  if (preset && preset.id === 'css_melting_text') {
    // ---- LIVE 60FPS MELTING TEXT RENDER (GRADIENT + DRIP + BLURRED GLOW) ----
    const textCfg = preset.text || {};
    const motion = preset.motion || {};
    const gradStops = Array.isArray(preset.gradient) && preset.gradient.length >= 2
      ? preset.gradient : ['#FF6F61', '#FFBD44', '#FF6F61'];

    let displayText = textRaw;
    if (revealMs.length) {
      const words = textRaw.split(/(\s+)/);
      let wIdx = 0, built = '';
      for (const part of words) {
        if (/^\s+$/.test(part)) {
          if (wIdx > 0 && (wIdx >= revealMs.length || elapsedMs >= revealMs[Math.min(wIdx, revealMs.length - 1)])) built += part;
        } else {
          if (wIdx < revealMs.length && elapsedMs >= revealMs[wIdx]) built += part;
          wIdx++;
        }
      }
      displayText = built || textRaw.split(/\s+/)[0];
    }
    if (textCfg.uppercase) displayText = displayText.toUpperCase();

    const lines = displayText.split(/\r?\n/);
    const fontPx = Math.max(12, sizeVal * W * 0.68);
    const lineH = fontPx * 1.15;
    const hAlign = ED_PREV.centerMode ? 0 : (sideVal === 'left' ? -1 : sideVal === 'right' ? 1 : 0);

    octx.font = `${italic}${weight} ${fontPx}px "${font}", sans-serif`;
    const maxLineW = Math.max(1, ...lines.map(l => octx.measureText(l).width));
    const fitScale = maxLineW > W * 0.86 ? (W * 0.86) / maxLineW : 1;
    const drawW = maxLineW * fitScale;

    const ax = ED_PREV.centerMode ? (W * 0.5) : (xVal * W);
    const ay = ED_PREV.centerMode ? (H * 0.44) : ((1 - yVal) * H);

    const periodMs = Math.max(400, Number(motion.period_seconds || 3) * 1000);
    const wave = (1 - Math.cos((2 * Math.PI * rawElapsed) / periodMs)) / 2;
    const dropPx = (Number(motion.drop_pixels || 20) / 1080) * H * 2.4 * wave;
    const sy0 = Number((motion.drip_scale_y && motion.drip_scale_y[0]) ?? 1.0);
    const sy1 = Number((motion.drip_scale_y && motion.drip_scale_y[1]) ?? 1.5);
    const dripScaleY = sy0 + (sy1 - sy0) * wave;
    const op0 = Number((motion.drip_opacity && motion.drip_opacity[0]) ?? 0.5);
    const op1 = Number((motion.drip_opacity && motion.drip_opacity[1]) ?? 0.7);
    const dripOpacity = op0 + (op1 - op0) * wave;
    const blurPx = Math.max(0.5, (Number(motion.blur_pixels || 10) / 1080) * H * 1.8);
    const blurOpacity = Number(motion.blurred_drip_opacity ?? 0.3);

    const drawGradientLines = (targetCtx, scaleYMul, yOffset, alpha, filterStr) => {
      targetCtx.save();
      if (filterStr) targetCtx.filter = filterStr;
      targetCtx.globalAlpha = alpha;
      targetCtx.translate(ax, ay + yOffset);
      targetCtx.scale(fitScale, fitScale * scaleYMul);
      targetCtx.font = `${italic}${weight} ${fontPx}px "${font}", sans-serif`;
      targetCtx.textAlign = hAlign < 0 ? 'left' : hAlign > 0 ? 'right' : 'center';
      targetCtx.textBaseline = 'middle';

      const gx0 = hAlign < 0 ? 0 : hAlign > 0 ? -maxLineW : -maxLineW / 2;
      const gx1 = gx0 + maxLineW;
      const grad = targetCtx.createLinearGradient(gx0, 0, gx1, 0);
      gradStops.forEach((col, i) => grad.addColorStop(i / Math.max(1, gradStops.length - 1), col));
      targetCtx.fillStyle = grad;

      const startY = -((lines.length - 1) * lineH) / 2;
      lines.forEach((ln, idxL) => targetCtx.fillText(ln, 0, startY + idxL * lineH));
      targetCtx.restore();
    };

    // Layer 1: Blurred melting glow drip
    drawGradientLines(ctx, dripScaleY, dropPx, blurOpacity, `blur(${blurPx.toFixed(1)}px)`);
    // Layer 2: Sharp stretched melting drip
    drawGradientLines(ctx, dripScaleY, dropPx * 0.85, dripOpacity, '');
    // Layer 3: Crisp gradient main text on top
    drawGradientLines(ctx, 1.0, 0, 1.0, '');

    if (badgeEl) badgeEl.textContent = `${preset.title || 'Melting Text'} · градиент`;
  } else if (preset && preset.text_plus && preset.layout) {
    // ---- EXACT REFERENCE PRESET RENDER (FUSION STYLE + VARIANT MOTION + INK WEAR) ----
    const ts = preset.text_plus;
    const layout = preset.layout;
    const motion = preset.line_motion || null;
    const defSize = (ts.supporting_line && ts.supporting_line.size) || 0.07;
    const sizeScale = sizeVal / Math.max(0.01, defSize);
    const lines = textRaw.split(/\r?\n/).map(s => s.trim()).filter(Boolean);

    let side = sideVal;
    if (side !== 'left' && side !== 'right') side = xVal < 0.5 ? 'left' : 'right';
    const hAlign = ED_PREV.centerMode
      ? 0
      : ((layout[side] && layout[side].HorizontalLeftCenterRight !== undefined)
          ? layout[side].HorizontalLeftCenterRight
          : (side === 'left' ? -1 : 1));

    const anchorX = ED_PREV.centerMode ? (W * 0.5) : (xVal * W);
    const capFactor = Number(layout.cap_height_factor || 0.485);
    const lineGapY = Number(layout.line_gap_y || 0.008);
    const maxWFrac = ED_PREV.centerMode ? 0.86 : Number(layout.max_width_fraction || 0.58);
    const charStretch = Number((ts.inputs && ts.inputs.CharacterSizeX) || 1.0);
    const firstOpacity = Number((preset.reveal && preset.reveal.first_visible_frame_opacity) || 0.7);

    // Precompute total block height so centerMode places the entire stack dead-center at H * 0.46
    const lineHeights = lines.map((lineStr, i) => {
      const role = (i === 0 ? ts.accent_line : ts.supporting_line) || { size: 0.07 };
      const disp = ts.uppercase ? lineStr.toUpperCase() : lineStr;
      let fs = Number(role.size || 0.07);
      if (i === 0 && role.short_max_characters && disp.length <= role.short_max_characters && role.short_size) {
        fs = Number(role.short_size);
      }
      fs *= sizeScale;
      return fs * capFactor * (W / H);
    });
    const totalBlockNormH = lineHeights.reduce((a, b) => a + b, 0) + lineGapY * Math.max(0, lines.length - 1);
    let topY = ED_PREV.centerMode ? (0.54 + totalBlockNormH / 2) : yVal;

    let wordCursor = 0;
    const totalFrames = Math.max(1, Math.round((clipDurMs / 1000) * fps));

    lines.forEach((lineStr, i) => {
      const role = (i === 0 ? ts.accent_line : ts.supporting_line) || { size: 0.07 };
      const dispText = ts.uppercase ? lineStr.toUpperCase() : lineStr;
      let fSizeNorm = Number(role.size || 0.07);
      if (i === 0 && role.short_max_characters && dispText.length <= role.short_max_characters && role.short_size) {
        fSizeNorm = Number(role.short_size);
      }
      fSizeNorm *= sizeScale;
      const heightInFrame = fSizeNorm * capFactor * (W / H);
      const centerYNorm = topY - heightInFrame / 2;
      const drawY = (1 - centerYNorm) * H;
      const fontPx = Math.max(10, fSizeNorm * W * 0.72);

      // Compute onset frame (matches render_reference_style lines 632-636)
      let onsetFrame = 0;
      if (wordCursor < revealMs.length) {
        onsetFrame = (revealMs[wordCursor] / 1000) * fps;
      } else {
        onsetFrame = Math.round((i * totalFrames) / Math.max(1, lines.length) * 0.35);
      }
      wordCursor += lineStr.split(/\s+/).length;

      const age = curFrame - onsetFrame;
      if (age < 0) {
        topY -= heightInFrame + lineGapY;
        return;
      }

      const mDur = motion ? Math.max(1, Number(motion.frames || 5)) : 4;
      const prog = Math.min(1, Math.max(0, age / mDur));
      const rest = 1 - prog;
      const lineAlpha = age < 1 ? firstOpacity : 1.0;

      let lineColor = _rgbToHex(role.inputs, colorHex);
      if (i > 0 && colorHex.toUpperCase() !== '#F5F3ED') lineColor = colorHex;

      octx.save();
      octx.font = `${italic}${weight} ${fontPx}px "${font}", sans-serif`;
      const natW = octx.measureText(dispText).width * charStretch;
      const maxW = maxWFrac * W;
      let scaleX = charStretch * (natW > maxW && natW > 0 ? maxW / natW : 1);
      let scaleY = 1;
      let offX = 0;
      let offY = 0;

      const kind = motion ? motion.type : null;
      const amount = motion ? Number(motion.amount || 0.08) : 0;

      if (kind === 'stamp') {
        const sc = 1 + amount * rest * rest;
        scaleX *= sc; scaleY *= sc;
      } else if (kind === 'film') {
        offX = amount * Math.sin(age * 2.4) * rest * W;
        offY = -amount * Math.cos(age * 1.7) * rest * H;
      } else if (kind === 'masked_rise') {
        const cutoffY = (1 - Math.max(0, centerYNorm - fSizeNorm * (W / H) * 0.27)) * H;
        octx.beginPath();
        octx.rect(0, 0, W, cutoffY);
        octx.clip();
        offY = amount * rest * rest * H;
      } else if (kind === 'paper' && i === 0) {
        offX = hAlign * 0.025 * rest * rest * W;
        const pW = Math.min(W * 0.63, Math.max(W * 0.14, Math.min(natW, maxW) + W * 0.028));
        const pH = Math.max(fontPx * 1.15, fSizeNorm * W * 0.65);
        const pX = anchorX + offX - hAlign * (pW / 2 - W * 0.014);
        octx.save();
        octx.translate(pX, drawY);
        octx.rotate((-2 * Math.PI) / 180);
        octx.globalAlpha = lineAlpha;
        octx.fillStyle = _rgbToHex(motion.paper, '#E8DCC4');
        octx.fillRect(-pW / 2, -pH / 2, pW, pH);
        octx.restore();
      }

      octx.textAlign = hAlign < 0 ? 'left' : hAlign > 0 ? 'right' : 'center';
      octx.textBaseline = 'middle';

      // Film ghost pass
      if (kind === 'film' && age < 3.2) {
        octx.save();
        octx.globalAlpha = 0.32 * Math.max(0, 1 - age / 3);
        octx.translate(anchorX - amount * W, drawY - (amount * H) / 2);
        octx.scale(scaleX, scaleY);
        octx.fillStyle = lineColor;
        octx.fillText(dispText, 0, 0);
        octx.restore();
      }

      octx.translate(anchorX + offX, drawY + offY);
      octx.scale(scaleX, scaleY);
      octx.globalAlpha = lineAlpha;

      if (kind === 'outline_fill' && i === 0) {
        octx.strokeStyle = lineColor;
        octx.lineWidth = Math.max(1.2, fontPx * 0.055);
        octx.strokeText(dispText, 0, 0);
        const visChars = Math.max(0, Math.round(dispText.length * prog));
        octx.fillStyle = lineColor;
        octx.fillText(dispText.slice(0, visChars), 0, 0);
      } else {
        octx.fillStyle = lineColor;
        octx.fillText(dispText, 0, 0);
      }
      octx.restore();

      topY -= heightInFrame + lineGapY;
    });

    // Stepped AnimatedInkWear distress grain
    const stepPeriod = 4;
    const grainSeed = Math.floor(curFrame / stepPeriod) * 0.37 + (ED.activeIdx + 1) * 1.13;
    _applyDistressTexture(octx, W, H, grainSeed, 0.95);

    // ReadingShadow + composite onto main canvas
    ctx.save();
    ctx.shadowColor = 'rgba(0, 0, 0, 0.78)';
    ctx.shadowBlur = 7;
    ctx.shadowOffsetX = 1.5;
    ctx.shadowOffsetY = 2.5;
    ctx.drawImage(off, 0, 0, W, H);
    ctx.restore();

    if (badgeEl) {
      const mLabel = motion ? ` · ${motion.type}` : '';
      badgeEl.textContent = `Фактура + Тень${mLabel}`;
    }
  } else {
    // ---- STANDARD TEXT+ RENDER (FUSION ALIGN + ZOOM/FADE + WORD REVEAL) ----
    let displayText = textRaw;
    if (revealMs.length) {
      const words = textRaw.split(/(\s+)/);
      let wIdx = 0;
      let built = '';
      for (const part of words) {
        if (/^\s+$/.test(part)) {
          if (wIdx > 0 && (wIdx >= revealMs.length || elapsedMs >= revealMs[Math.min(wIdx, revealMs.length - 1)])) {
            built += part;
          }
        } else {
          if (wIdx < revealMs.length && elapsedMs >= revealMs[wIdx]) {
            built += part;
          }
          wIdx++;
        }
      }
      displayText = built || textRaw.split(/\s+/)[0];
    }

    const prog = Math.min(1, curFrame / animFrames);
    let scale = 1.0;
    let alpha = 1.0;
    if (animName === 'Увеличение') scale = 0.82 + 0.18 * prog;
    else if (animName === 'Плавное появление') alpha = prog;

    const hAlign = ED_PREV.centerMode ? 0 : (sideVal === 'left' ? -1 : sideVal === 'right' ? 1 : (xVal < 0.44 ? -1 : xVal > 0.56 ? 1 : 0));
    const fontPx = Math.max(10, sizeVal * W * 0.72);
    const lines = displayText.split(/\r?\n/);
    const lineH = fontPx * 1.18;

    octx.save();
    octx.font = `${italic}${weight} ${fontPx}px "${font}", sans-serif`;
    const maxLineW = Math.max(1, ...lines.map(l => octx.measureText(l).width));
    const maxAllowedW = W * 0.88;
    const fitScale = maxLineW > maxAllowedW ? maxAllowedW / maxLineW : 1;

    const blockH = lines.length * lineH * fitScale;
    let ax = ED_PREV.centerMode ? (W * 0.5) : (xVal * W);
    let ay = ED_PREV.centerMode ? (H * 0.46) : ((1 - yVal) * H);
    if (!ED_PREV.centerMode) {
      if (hAlign === 0) ax = Math.max(maxLineW * fitScale / 2 + 8, Math.min(W - maxLineW * fitScale / 2 - 8, ax));
      else if (hAlign < 0) ax = Math.max(8, Math.min(W - maxLineW * fitScale - 8, ax));
      else ax = Math.max(maxLineW * fitScale + 8, Math.min(W - 8, ax));
      ay = Math.max(blockH / 2 + 8, Math.min(H - blockH / 2 - 14, ay));
    }

    octx.translate(ax, ay);
    octx.scale(scale * fitScale, scale * fitScale);
    octx.globalAlpha = alpha;
    octx.fillStyle = colorHex;
    octx.textAlign = hAlign < 0 ? 'left' : hAlign > 0 ? 'right' : 'center';
    octx.textBaseline = 'middle';

    const startY = -((lines.length - 1) * lineH) / 2;
    lines.forEach((ln, idxL) => octx.fillText(ln, 0, startY + idxL * lineH));
    octx.restore();

    ctx.save();
    ctx.shadowColor = 'rgba(0, 0, 0, 0.82)';
    ctx.shadowBlur = 6;
    ctx.shadowOffsetY = 2;
    ctx.drawImage(off, 0, 0, W, H);
    ctx.restore();

    if (badgeEl) {
      const tags = ['Text+'];
      if (animName !== 'Без анимации') tags.push(animName);
      if (revealMs.length) tags.push('накопление');
      badgeEl.textContent = tags.join(' · ');
    }
  }
}

function edPreviewLoop(nowMs) {
  renderEdPreviewFrame(nowMs);
  requestAnimationFrame(edPreviewLoop);
}
requestAnimationFrame(edPreviewLoop);

// =========================================================================
// MULTI-SELECT ORTHOGRAPHY & CASE CLEANER MODAL
// =========================================================================
function openOrthoModal() {
  closeEdPopovers();
  document.getElementById('modal-ortho').classList.remove('hidden');
  renderOrthoPreview();
}

function closeOrthoModal() {
  document.getElementById('modal-ortho')?.classList.add('hidden');
}

function setOrthoPunctPreset(kind) {
  document.getElementById('ortho-punct-on').checked = true;
  const qe = document.getElementById('ortho-keep-qe');
  const dots = document.getElementById('ortho-keep-dots');
  const commas = document.getElementById('ortho-keep-commas');
  const stripAll = document.getElementById('ortho-strip-all');

  qe.checked = kind === 'qe';
  dots.checked = kind === 'dots' || kind === 'dotcomma';
  commas.checked = kind === 'commas' || kind === 'dotcomma';
  stripAll.checked = kind === 'none';
  renderOrthoPreview();
}

function onOrthoCheckChange() {
  document.getElementById('ortho-punct-on').checked = true;
  const anyKeep = document.getElementById('ortho-keep-qe').checked
    || document.getElementById('ortho-keep-dots').checked
    || document.getElementById('ortho-keep-commas').checked;
  document.getElementById('ortho-strip-all').checked = !anyKeep;
  renderOrthoPreview();
}

function onOrthoStripAllToggle(checked) {
  if (checked) {
    document.getElementById('ortho-punct-on').checked = true;
    document.getElementById('ortho-keep-qe').checked = false;
    document.getElementById('ortho-keep-dots').checked = false;
    document.getElementById('ortho-keep-commas').checked = false;
  }
  renderOrthoPreview();
}

function getOrthoOptions() {
  const caseRadio = document.querySelector('input[name="ortho-case"]:checked');
  return {
    punctMode: document.getElementById('ortho-punct-on').checked,
    keepQe: document.getElementById('ortho-keep-qe').checked,
    keepDots: document.getElementById('ortho-keep-dots').checked,
    keepCommas: document.getElementById('ortho-keep-commas').checked,
    caseMode: caseRadio ? caseRadio.value : 'keep'
  };
}

function previewTransformText(rawText, opts) {
  const allPunct = ".,!?:;…—–-«»\"'()[]{}/\\|@#$%^&*_~`";
  let allowed = '';
  if (opts.keepQe) allowed += '?!';
  if (opts.keepDots) allowed += '.…';
  if (opts.keepCommas) allowed += ',';

  return rawText.split(/\r?\n/).map(line => {
    const out = [];
    for (let tok of line.split(/\s+/).filter(Boolean)) {
      if (opts.punctMode) {
        if (tok === '-' || tok === '—' || tok === '–') tok = '';
        else {
          tok = [...tok].filter(ch => !allPunct.includes(ch) || allowed.includes(ch) || (ch === '-' && tok.length > 1)).join('').replace(/^-+|-+$/g, '');
        }
      }
      if (!tok) continue;
      if (opts.caseMode === 'title') {
        const chars = [...tok.toLowerCase()];
        for (let i = 0; i < chars.length; i++) {
          if (/\p{L}/u.test(chars[i])) { chars[i] = chars[i].toUpperCase(); break; }
        }
        tok = chars.join('');
      } else if (opts.caseMode === 'lower') {
        tok = tok.toLowerCase();
      } else if (opts.caseMode === 'upper') {
        tok = tok.toUpperCase();
      }
      out.push(tok);
    }
    return out.join(' ');
  }).filter(Boolean).join(' / ');
}

function renderOrthoPreview() {
  const sample = (ED.activeIdx >= 0 && ED.rows[ED.activeIdx])
    ? ED.rows[ED.activeIdx].text
    : 'Привет, мир! Не понимаю — сколько же времени прошло?..';
  const opts = getOrthoOptions();
  saveSettingsDebounced({
    orthoPunctOn: opts.punctMode,
    orthoStripAll: document.getElementById('ortho-strip-all').checked,
    orthoKeepQe: opts.keepQe,
    orthoKeepDots: opts.keepDots,
    orthoKeepCommas: opts.keepCommas,
    orthoCase: opts.caseMode
  });
  document.getElementById('ortho-preview-before').textContent = sample.replace(/\n/g, ' / ');
  document.getElementById('ortho-preview-after').textContent = previewTransformText(sample, opts) || '(пусто)';
}

async function edApplyOrtho(selectedOnly) {
  if (!api() || !(await edCommitActive())) return;
  const o = getOrthoOptions();
  const res = await api().ed_apply_orthography(
    o.punctMode, o.keepQe, o.keepDots, o.keepCommas, o.caseMode, selectedOnly, ED.activeIdx
  );
  applyEditorSnapshot(res, ED.activeIdx);
  if (res.ok) {
    closeOrthoModal();
    showToast(selectedOnly ? 'Орфография применена к реплике' : 'Орфография применена ко всем репликам');
  }
}

async function edUndoOrtho() {
  if (!api()) return;
  const res = await api().ed_undo_orthography();
  applyEditorSnapshot(res, ED.activeIdx);
  if (res.ok) {
    closeOrthoModal();
    showToast('Изменения орфографии отменены');
  }
}

function onEdFieldLive() {
  updateEdPreview();
}

function onEdColorPick(val) {
  document.getElementById('ed-f-color').value = val.toUpperCase();
  updateEdPreview();
  edCommitActive();
}

function onEdFontChange() {
  const font = document.getElementById('ed-f-font').value.trim();
  populateEdStyleList(font);
  const styles = ED.fonts[font];
  if (styles && styles.length && !styles.includes(document.getElementById('ed-f-style').value)) {
    document.getElementById('ed-f-style').value = styles.includes('Regular') ? 'Regular' : styles[0];
  }
  updateEdPreview();
  edCommitActive();
}

function onEdSideChange() {
  const side = document.getElementById('ed-f-side').value;
  const row = ED.rows[ED.activeIdx];
  if (row && row.reference_preset && (side === 'left' || side === 'right')) {
    const layout = row.reference_preset.layout || {};
    if (layout[side] && layout[side].x !== undefined) {
      document.getElementById('ed-f-x').value = layout[side].x;
    }
  }
  updateEdPreview();
  edCommitActive();
}

function collectEdForm() {
  return {
    text: document.getElementById('ed-f-text').value,
    startText: document.getElementById('ed-f-start').value,
    endText: document.getElementById('ed-f-end').value,
    font: document.getElementById('ed-f-font').value,
    style: document.getElementById('ed-f-style').value,
    color: document.getElementById('ed-f-color').value,
    size: document.getElementById('ed-f-size').value,
    side: document.getElementById('ed-f-side').value,
    x: document.getElementById('ed-f-x').value,
    y: document.getElementById('ed-f-y').value,
    animation: document.getElementById('ed-f-anim').value,
    frames: document.getElementById('ed-f-frames').value
  };
}

async function edCommitActive() {
  if (!api() || ED.activeIdx < 0 || ED.activeIdx >= ED.rows.length) return true;
  const res = await api().ed_update_row(ED.activeIdx, collectEdForm());
  if (!res.ok) {
    setEdStatus('Ошибка: ' + res.error);
    showToast(res.error);
    return false;
  }
  ED.rows[ED.activeIdx] = res.row;
  renderEdList();
  return true;
}

async function edSelectRow(idx) {
  if (idx === ED.activeIdx) return;
  if (!(await edCommitActive())) return;
  ED.activeIdx = idx;
  renderEdList();
  loadEdInspector();
}

async function edPickFile() {
  if (!api()) return;
  const res = await api().ed_pick_and_load();
  if (res && !res.cancelled) applyEditorSnapshot(res, 0);
}

async function edRefreshFonts() {
  if (!api()) return;
  const res = await api().ed_refresh_fonts();
  if (res.ok) {
    ED.fonts = res.fonts;
    populateEdFontList();
    setEdStatus(`Шрифты DaVinci Resolve обновлены (${res.count}).`);
    showToast(`Загружено шрифтов Resolve: ${res.count}`);
  } else {
    showToast(res.error || 'Откройте DaVinci Resolve для списка шрифтов');
  }
}

async function edAddRow() {
  if (!api() || !(await edCommitActive())) return;
  const res = await api().ed_add_row(ED.activeIdx);
  applyEditorSnapshot(res);
}

async function edSplitRow() {
  if (!api() || ED.activeIdx < 0 || !(await edCommitActive())) return;
  const ta = document.getElementById('ed-f-text');
  const offset = ta.selectionStart || 0;
  const res = await api().ed_split_row(ED.activeIdx, offset);
  applyEditorSnapshot(res);
}

async function edMergeNext() {
  if (!api() || ED.activeIdx < 0 || !(await edCommitActive())) return;
  const res = await api().ed_merge_next(ED.activeIdx);
  applyEditorSnapshot(res);
}

async function edDeleteRow() {
  if (!api() || ED.activeIdx < 0) return;
  const res = await api().ed_delete_row(ED.activeIdx);
  applyEditorSnapshot(res);
}

async function edApplyStyleAll() {
  if (!api() || ED.activeIdx < 0 || !(await edCommitActive())) return;
  const res = await api().ed_apply_style_all(ED.activeIdx);
  applyEditorSnapshot(res, ED.activeIdx);
  if (res.ok) showToast('Оформление применено ко всем репликам');
}

async function edApplyLayout() {
  if (!api() || !(await edCommitActive())) return;
  const perLine = Number(document.getElementById('ed-per-line').value || 3);
  const lineCnt = Number(document.getElementById('ed-line-count').value || 2);
  const res = await api().ed_apply_layout(perLine, lineCnt);
  applyEditorSnapshot(res, 0);
}

async function edUndoLayout() {
  if (!api()) return;
  applyEditorSnapshot(await api().ed_undo_layout(), 0);
}

async function edApplyWords(cumulative) {
  if (!api() || !(await edCommitActive())) return;
  const perLine = Number(document.getElementById('ed-per-line').value || 3);
  const lineCnt = Number(document.getElementById('ed-line-count').value || 2);
  const holdMode = document.getElementById('ed-hold-mode').value;
  const manualSec = document.getElementById('ed-hold-sec').value;
  const res = await api().ed_apply_word_timing(cumulative, perLine, lineCnt, holdMode, manualSec, ED.activeIdx);
  if (res && !res.cancelled) applyEditorSnapshot(res, 0);
}

async function edUndoWords() {
  if (!api()) return;
  applyEditorSnapshot(await api().ed_undo_words(), 0);
}

function onEdHoldModeChange() {
  const mode = document.getElementById('ed-hold-mode').value;
  const isManual = mode === 'Вручную (сек)';
  const inp = document.getElementById('ed-hold-sec');
  inp.disabled = !isManual;
  inp.style.opacity = isManual ? '1' : '0.45';
  saveSettingsNow({ edHoldMode: mode, edHoldSec: inp.value });
}

async function edApplyHold(selectedOnly) {
  if (!api() || !(await edCommitActive())) return;
  const holdMode = document.getElementById('ed-hold-mode').value;
  const manualSec = document.getElementById('ed-hold-sec').value;
  saveSettingsNow({ edHoldMode: holdMode, edHoldSec: manualSec });
  const res = await api().ed_apply_hold(holdMode, manualSec, selectedOnly, ED.activeIdx);
  applyEditorSnapshot(res, ED.activeIdx);
}

function toggleBehindPerson() {
  ED.behindPerson = !ED.behindPerson;
  document.getElementById('ed-led-matte').classList.toggle('on', ED.behindPerson);
  saveSettingsNow({ edBehindPerson: ED.behindPerson });
  showToast(ED.behindPerson ? 'Текст за персонажем (ИИ-маска): ВКЛ' : 'Текст за персонажем: ВЫКЛ');
}

async function edSaveSrt() {
  closeEdPopovers();
  if (!api() || !(await edCommitActive())) return;
  const res = await api().ed_save_srt();
  if (res && !res.cancelled) {
    applyEditorSnapshot(res, ED.activeIdx);
    if (res.ok) showToast('SRT сохранён');
  }
}

async function edSaveProject() {
  closeEdPopovers();
  if (!api() || !(await edCommitActive())) return;
  const res = await api().ed_save_project();
  if (res && !res.cancelled) {
    applyEditorSnapshot(res, ED.activeIdx);
    if (res.ok) showToast('Проект сохранён');
  }
}

async function edImportResolveStyle() {
  closeEdPopovers();
  if (!api() || ED.activeIdx < 0 || !(await edCommitActive())) return;
  const res = await api().ed_import_resolve_style(ED.activeIdx);
  applyEditorSnapshot(res, ED.activeIdx);
  if (res.ok) showToast('Стиль импортирован из Resolve Text+');
}

async function edImportStyleJson() {
  closeEdPopovers();
  if (!api() || ED.activeIdx < 0 || !(await edCommitActive())) return;
  const res = await api().ed_import_style_json(ED.activeIdx);
  if (res && !res.cancelled) {
    applyEditorSnapshot(res, ED.activeIdx);
    if (res.ok) showToast('Пресет оформления применён');
  }
}

async function edSaveStyleJson() {
  closeEdPopovers();
  if (!api() || ED.activeIdx < 0 || !(await edCommitActive())) return;
  const res = await api().ed_save_style_json(ED.activeIdx);
  if (res && !res.cancelled) {
    applyEditorSnapshot(res, ED.activeIdx);
    if (res.ok) showToast('Пресет оформления сохранён');
  }
}

async function edClearRefStyle() {
  closeEdPopovers();
  if (!api() || ED.activeIdx < 0 || !(await edCommitActive())) return;
  const res = await api().ed_clear_reference_style(ED.activeIdx);
  applyEditorSnapshot(res, ED.activeIdx);
}

function formatEdBusyBtnLabel(statusText, mode) {
  const s = String(statusText || '').toLowerCase();
  const numMatch = String(statusText || '').match(/(\d+\s*\/\s*\d+|\d+\s*%)/);
  const suffix = numMatch ? (' ' + numMatch[1].replace(/\s+/g, '')) : '…';
  if (mode === 'sides' || s.includes('сторон') || s.includes('анализ')) {
    return 'Анализ' + suffix;
  }
  if (s.includes('маск') || s.includes('персонаж') || s.includes('сегмент') || s.includes('рендер')) {
    return 'ИИ-маска' + suffix;
  }
  return 'Экспорт' + suffix;
}

function setEdExportBtnBusy(isBusy, label) {
  const expBtn = document.getElementById('ed-export-btn');
  if (!expBtn) return;
  if (isBusy) {
    expBtn.disabled = true;
    expBtn.classList.add('busy');
    let lbl = expBtn.querySelector('.btn-busy-label');
    if (!lbl) {
      expBtn.textContent = '';
      const spin = document.createElement('span');
      spin.className = 'btn-spin';
      spin.setAttribute('aria-hidden', 'true');
      lbl = document.createElement('span');
      lbl.className = 'btn-busy-label';
      expBtn.appendChild(spin);
      expBtn.appendChild(lbl);
    }
    lbl.textContent = label || 'Экспорт…';
  } else {
    expBtn.disabled = false;
    expBtn.classList.remove('busy');
    expBtn.textContent = '▶ Text+ в Resolve';
  }
}

async function edAutoPlaceSides() {
  closeEdPopovers();
  if (ED.busy || !api()) return;
  if (!ED.rows.length) { showToast('Нет реплик для расстановки'); return; }
  setEdExportBtnBusy(true, 'Анализ…');
  if (!(await edCommitActive())) { setEdExportBtnBusy(false); return; }
  const res = await api().ed_start_auto_place_sides(ED.behindPerson);
  if (!res.ok) {
    setEdExportBtnBusy(false);
    showToast(res.error);
    return;
  }
  setEdStatus('⏳ Анализ сторон кадра в DaVinci Resolve…');
  showToast('Анализ сторон кадра в DaVinci Resolve запущен…');
  pollEdBusy('sides');
}

async function edExportResolve() {
  if (ED.busy || !api()) return;
  if (!ED.rows.length) { showToast('Нет реплик для экспорта'); return; }
  setEdExportBtnBusy(true, 'Экспорт…');
  if (!(await edCommitActive())) { setEdExportBtnBusy(false); return; }
  const res = await api().ed_start_export_resolve(ED.behindPerson);
  if (!res.ok) {
    setEdExportBtnBusy(false);
    showToast(res.error);
    return;
  }
  setEdStatus('⏳ Подключение к DaVinci Resolve…');
  showToast('Экспорт Text+ в DaVinci Resolve запущен…');
  pollEdBusy('export');
}

let edBusyTimer = null;
function pollEdBusy(mode) {
  ED.busy = true;
  const fallbackLabel = mode === 'sides' ? 'Анализ…' : 'Экспорт…';
  setEdExportBtnBusy(true, fallbackLabel);
  clearInterval(edBusyTimer);
  edBusyTimer = setInterval(async () => {
    try {
      const st = await api().ed_get_busy_state();
      if (st.busy) {
        const msg = st.busyStatus || 'Выполняется операция Resolve…';
        setEdStatus('⏳ ' + msg);
        setEdExportBtnBusy(true, formatEdBusyBtnLabel(msg, mode));
        return;
      }
      clearInterval(edBusyTimer);
      ED.busy = false;
      setEdExportBtnBusy(false);
      if (st.rows) {
        ED.rows = st.rows;
        renderEdList();
        loadEdInspector();
      }
      const r = st.lastResult || {};
      if (r.ok) {
        setEdStatus('✅ ' + (r.message || 'Готово'));
        showToast(r.message || 'Готово');
      } else if (r.error) {
        setEdStatus('❌ ' + r.error);
        showToast(r.error);
      }
    } catch (_) {
      clearInterval(edBusyTimer);
      ED.busy = false;
      setEdExportBtnBusy(false);
    }
  }, 450);
}

function restoreRememberedUiPrefs() {
  // Rack collapsed state
  rackCompact = Boolean(SETTINGS.rackCompact);
  document.getElementById('rack-channels').classList.toggle('hidden', rackCompact);
  document.getElementById('rack-knobs').classList.toggle('hidden', rackCompact);
  document.getElementById('collapse-btn').textContent = rackCompact ? '▼ Показать панель' : '▲ Свернуть панель';

  // Caption Editor preferences
  const perLineEl = document.getElementById('ed-per-line');
  const lineCntEl = document.getElementById('ed-line-count');
  const holdModeEl = document.getElementById('ed-hold-mode');
  const holdSecEl = document.getElementById('ed-hold-sec');

  if (perLineEl) {
    perLineEl.value = SETTINGS.edPerLine ?? 3;
    perLineEl.addEventListener('change', () => saveSettingsNow({ edPerLine: Math.max(1, Number(perLineEl.value || 3)) }));
  }
  if (lineCntEl) {
    lineCntEl.value = SETTINGS.edLineCount ?? 2;
    lineCntEl.addEventListener('change', () => saveSettingsNow({ edLineCount: Math.max(1, Number(lineCntEl.value || 2)) }));
  }
  if (holdModeEl) {
    holdModeEl.value = SETTINGS.edHoldMode || 'До конца речи';
  }
  if (holdSecEl) {
    holdSecEl.value = SETTINGS.edHoldSec || '2,0';
    const isManual = (SETTINGS.edHoldMode || '') === 'Вручную (сек)';
    holdSecEl.disabled = !isManual;
    holdSecEl.style.opacity = isManual ? '1' : '0.45';
    holdSecEl.addEventListener('change', () => saveSettingsNow({ edHoldSec: holdSecEl.value.trim() || '2,0' }));
  }

  ED.behindPerson = SETTINGS.edBehindPerson !== undefined ? Boolean(SETTINGS.edBehindPerson) : true;
  document.getElementById('ed-led-matte')?.classList.toggle('on', ED.behindPerson);

  ED_PREV.centerMode = SETTINGS.edPreviewCenter !== undefined ? Boolean(SETTINGS.edPreviewCenter) : true;
  const posBtn = document.getElementById('ed-prev-pos-btn');
  if (posBtn) posBtn.textContent = ED_PREV.centerMode ? 'Центр' : 'Поз. X/Y';

  // Orthography modal remembered checkboxes & case radio
  const pOn = document.getElementById('ortho-punct-on');
  const sAll = document.getElementById('ortho-strip-all');
  const kQe = document.getElementById('ortho-keep-qe');
  const kDot = document.getElementById('ortho-keep-dots');
  const kCom = document.getElementById('ortho-keep-commas');
  if (pOn) pOn.checked = SETTINGS.orthoPunctOn !== undefined ? Boolean(SETTINGS.orthoPunctOn) : true;
  if (sAll) sAll.checked = SETTINGS.orthoStripAll !== undefined ? Boolean(SETTINGS.orthoStripAll) : true;
  if (kQe) kQe.checked = Boolean(SETTINGS.orthoKeepQe);
  if (kDot) kDot.checked = Boolean(SETTINGS.orthoKeepDots);
  if (kCom) kCom.checked = Boolean(SETTINGS.orthoKeepCommas);
  const caseVal = SETTINGS.orthoCase || 'keep';
  const radio = document.querySelector(`input[name="ortho-case"][value="${caseVal}"]`);
  if (radio) radio.checked = true;
}

async function bootstrap() {
  const a = api();
  if (!a) return;
  const snap = await a.get_bootstrap();
  SETTINGS = Object.assign(SETTINGS, snap.settings || {});
  ENV = snap.env || ENV;
  HISTORY = snap.history || [];
  LIVE = snap.live || LIVE;
  TIMELINE = LIVE.timeline || TIMELINE;
  ACTIVE_SUB = LIVE.activeSubtitle || null;

  restoreRememberedUiPrefs();
  applyTheme();
  renderAllControls();
  setupMiniPlayer(ACTIVE_SUB);
  renderSubtitles();
  requestAnimationFrame(drawVisualizers);

  setInterval(() => {
    if (LIVE.running) pollLiveStatus();
  }, 450);
  setInterval(() => {
    if (!LIVE.running) pollLiveStatus();
  }, 2200);
}

window.addEventListener('pywebviewready', bootstrap);
if (window.pywebview && window.pywebview.api) bootstrap();
