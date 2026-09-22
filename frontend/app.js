(() => {
  "use strict";

  const state = {
    pads: [],
    sounds: [],
    soundBrowserPath: [],
    soundBrowserView: { folders: [], samples: [] },
    selectedSoundIds: new Set(),
    modalSoundPath: [],
    modalSoundView: { folders: [], samples: [] },
    knobs: [],
    knobTargets: { global: [], pads: {} },
    pendingLearn: null,
    pendingNoteLearn: null,
    selectedPad: null,
    settings: { sustain_mode: "1", velocity_sensitive: "0", kit_browse_preview_enabled: "1", looper_quantize_enabled: "1" },
    padEffects: [],
    effectsCatalog: [],
    sequencer: { running: false, current_step: 0, steps: [] },
    looper: { state: "stopped", selected_track: 0, loop_duration: null, started_at: null, tracks: [] },
    tempo: { bpm: 100 },
    metronome: { running: false, beat_in_bar: 0, signature: "4_4", style: "digital" },
    metronomeStyles: [],
    timeSignatures: [],
    master: { available: false, volume: 100, muted: false, limiter_available: false, limiter_enabled: true, limiter_threshold_db: -1 },
    engineStatus: { jack: false, modhost: false, pads: {}, metronome: false, cpu_percent: null, last_error: null },
    scenes: [],
    sceneIndex: 0,
    sceneSearch: "",
    patterns: [],
    patternIndex: 0,
    controllerActions: { bindings: {}, pending_learn: null },
    kitBrowse: { active: false },
    sceneLoadingCount: 0,
  };

  const KIT_BROWSE_RESERVED_PADS = { 13: "up", 14: "left", 15: "right", 16: "down", 1: "back", 4: "confirm" };

  const knobPicker = { step: null, scope: null, padNumber: null };
  let looperTimer = null;
  let tapTimestamps = [];
  let panicTimer = null;
  let midiNoteHitTimer = null;

  const el = {
    padGrid: document.getElementById("pad-grid"),
    perfPadGrid: document.getElementById("perf-pad-grid"),
    scenePrev: document.getElementById("scene-prev"),
    sceneNext: document.getElementById("scene-next"),
    sceneName: document.getElementById("scene-name"),
    sceneCount: document.getElementById("scene-count"),
    sceneSave: document.getElementById("scene-save"),
    sceneDelete: document.getElementById("scene-delete"),
    sceneSearch: document.getElementById("scene-search"),
    sceneList: document.getElementById("scene-list"),
    patternPrev: document.getElementById("pattern-prev"),
    patternNext: document.getElementById("pattern-next"),
    patternName: document.getElementById("pattern-name"),
    patternCount: document.getElementById("pattern-count"),
    patternSave: document.getElementById("pattern-save"),
    patternDelete: document.getElementById("pattern-delete"),
    soundList: document.getElementById("sound-list"),
    soundBreadcrumb: document.getElementById("sound-breadcrumb"),
    selectAllSounds: document.getElementById("select-all-sounds"),
    selectedSoundsCount: document.getElementById("selected-sounds-count"),
    deleteSelectedSounds: document.getElementById("delete-selected-sounds"),
    volumeList: document.getElementById("volume-list"),
    effectsList: document.getElementById("effects-list"),
    uploadZone: document.getElementById("upload-zone"),
    fileInput: document.getElementById("file-input"),
    folderInput: document.getElementById("folder-input"),
    uploadProgress: document.getElementById("upload-progress"),
    uploadProgressLabel: document.getElementById("upload-progress-label"),
    uploadProgressPercent: document.getElementById("upload-progress-percent"),
    uploadProgressBar: document.getElementById("upload-progress-bar"),
    sceneLoadingOverlay: document.getElementById("scene-loading-overlay"),
    sceneLoadingText: document.getElementById("scene-loading-text"),
    modal: document.getElementById("assign-modal"),
    modalTitle: document.getElementById("modal-pad-title"),
    modalClose: document.getElementById("modal-close"),
    modalSearch: document.getElementById("modal-search"),
    modalBreadcrumb: document.getElementById("modal-breadcrumb"),
    modalClear: document.getElementById("modal-clear"),
    modalSoundList: document.getElementById("modal-sound-list"),
    modalNoteValue: document.getElementById("modal-note-value"),
    modalLearnNoteBtn: document.getElementById("modal-learn-note-btn"),
    connStatus: document.getElementById("conn-status"),
    midiNoteIndicator: document.getElementById("midi-note-indicator"),
    globalTapBtn: document.getElementById("global-tap-btn"),
    globalBpm: document.getElementById("global-bpm"),
    globalSequencerBtn: document.getElementById("global-sequencer-btn"),
    masterVolume: document.getElementById("master-volume"),
    masterMuteBtn: document.getElementById("master-mute-btn"),
    masterVolumeLarge: document.getElementById("master-volume-large"),
    masterVolumeValue: document.getElementById("master-volume-value"),
    masterMuteLargeBtn: document.getElementById("master-mute-large-btn"),
    masterLimiterEnabled: document.getElementById("master-limiter-enabled"),
    masterLimiterThreshold: document.getElementById("master-limiter-threshold"),
    masterLimiterThresholdValue: document.getElementById("master-limiter-threshold-value"),
    masterLimiterWarning: document.getElementById("master-limiter-warning"),
    engineStatus: document.getElementById("engine-status"),
    cpuMeter: document.getElementById("cpu-meter"),
    engineStatusDetail: document.getElementById("engine-status-detail"),
    engineStatusText: document.getElementById("engine-status-text"),
    engineRestartBtn: document.getElementById("engine-restart-btn"),
    panicBtn: document.getElementById("panic-btn"),
    previewAudio: document.getElementById("preview-audio"),
    settingSustain: document.getElementById("setting-sustain"),
    settingVelocity: document.getElementById("setting-velocity"),
    settingKitBrowsePreview: document.getElementById("setting-kit-browse-preview"),
    settingLooperQuantize: document.getElementById("setting-looper-quantize"),
    settingLooperDuplicateHitWindow: document.getElementById("setting-looper-duplicate-hit-window"),
    settingLooperDuplicateHitWindowValue: document.getElementById("setting-looper-duplicate-hit-window-value"),
    fullRestartBtn: document.getElementById("full-restart-btn"),
    clearAllPadsBtn: document.getElementById("clear-all-pads-btn"),
    midiMapFilter: document.getElementById("midi-map-filter"),
    midiMapAddKnobBtn: document.getElementById("midi-map-add-knob-btn"),
    midiLearnBanner: document.getElementById("midi-learn-banner"),
    midiLearnBannerText: document.getElementById("midi-learn-banner-text"),
    midiLearnCancelBtn: document.getElementById("midi-learn-cancel-btn"),
    midiMapSections: document.getElementById("midi-map-sections"),
    kitBrowseBanner: document.getElementById("kit-browse-banner"),
    kitBrowseModal: document.getElementById("kit-browse-modal"),
    kitBrowseModalEyebrow: document.getElementById("kit-browse-modal-eyebrow"),
    kitBrowseModalKit: document.getElementById("kit-browse-modal-kit"),
    kitBrowseModalCategories: document.getElementById("kit-browse-modal-categories"),
    kitBrowseModalKitList: document.getElementById("kit-browse-modal-kit-list"),
    kitBrowseModalSoundList: document.getElementById("kit-browse-modal-sound-list"),
    sequencerPlayBtn: document.getElementById("sequencer-play-btn"),
    sequencerBpm: document.getElementById("sequencer-bpm"),
    sequencerClearBtn: document.getElementById("sequencer-clear-btn"),
    sequencerHeadRow: document.getElementById("sequencer-head-row"),
    sequencerBody: document.getElementById("sequencer-body"),
    looperStateLabel: document.getElementById("looper-state-label"),
    looperElapsed: document.getElementById("looper-elapsed"),
    looperProgress: document.getElementById("looper-progress"),
    looperProgressFill: document.getElementById("looper-progress-fill"),
    looperDetails: document.getElementById("looper-details"),
    looperTrackList: document.getElementById("looper-track-list"),
    looperRecordBtn: document.getElementById("looper-record-btn"),
    looperOverdubBtn: document.getElementById("looper-overdub-btn"),
    looperPlayBtn: document.getElementById("looper-play-btn"),
    looperStopBtn: document.getElementById("looper-stop-btn"),
    looperClearBtn: document.getElementById("looper-clear-btn"),
    metronomeBpm: document.getElementById("metronome-bpm"),
    metronomeTapBtn: document.getElementById("metronome-tap-btn"),
    metronomeBeatRow: document.getElementById("metronome-beat-row"),
    metronomePlayBtn: document.getElementById("metronome-play-btn"),
    metronomeStyleSelect: document.getElementById("metronome-style-select"),
    metronomeSignatureSelect: document.getElementById("metronome-signature-select"),
    metronomeEngineWarning: document.getElementById("metronome-engine-warning"),
    knobsList: document.getElementById("knobs-list"),
    knobAddBtn: document.getElementById("knob-add-btn"),
    knobClearAllBtn: document.getElementById("knob-clear-all-btn"),
    knobModal: document.getElementById("knob-modal"),
    knobModalTitle: document.getElementById("knob-modal-title"),
    knobModalClose: document.getElementById("knob-modal-close"),
    knobModalList: document.getElementById("knob-modal-list"),
    knobModalWaiting: document.getElementById("knob-modal-waiting"),
  };

  const VIEWS = ["pads", "performance", "sounds", "volumes", "master", "effects", "sequencer", "metronome", "looper", "knobs", "config"];
  const SUPPORTED_AUDIO_EXTENSIONS = new Set([".wav", ".mp3", ".ogg", ".flac", ".aiff", ".aif"]);
  // Uploading a big folder (or several dropped together) can mean hundreds
  // of files - firing every XHR at once used to overwhelm the connection
  // (browser socket limits, the laptop's single uvicorn worker competing
  // with JACK/sfizz for CPU) and a chunk would fail with a generic network
  // error. Capped the same way the backend caps concurrent pad respawns
  // (engine/orchestrator.py's _apply_semaphore).
  const UPLOAD_CONCURRENCY = 4;
  for (const name of VIEWS) {
    document.getElementById(`tab-${name}`).addEventListener("click", () => switchView(name));
  }
  function switchView(view) {
    for (const name of VIEWS) {
      document.getElementById(`tab-${name}`).classList.toggle("active", name === view);
      document.getElementById(`view-${name}`).classList.toggle("hidden", name !== view);
    }
  }

  // --- Controles de palco ---------------------------------------------

  function renderMaster() {
    el.masterVolume.value = Math.round(state.master.volume);
    el.masterMuteBtn.textContent = state.master.muted ? "Ativar" : "Mute";
    el.masterMuteBtn.classList.toggle("active", state.master.muted);
    el.masterVolume.disabled = !state.master.available;
    el.masterMuteBtn.disabled = !state.master.available;
    el.masterVolumeLarge.value = Math.round(state.master.volume);
    el.masterVolumeLarge.disabled = !state.master.available;
    el.masterVolumeValue.textContent = `${Math.round(state.master.volume)}%`;
    el.masterMuteLargeBtn.textContent = state.master.muted ? "Ativar saída" : "Mute";
    el.masterMuteLargeBtn.classList.toggle("active", state.master.muted);
    el.masterMuteLargeBtn.disabled = !state.master.available;
    el.masterLimiterEnabled.checked = state.master.limiter_enabled;
    el.masterLimiterEnabled.disabled = !state.master.limiter_available;
    el.masterLimiterThreshold.value = state.master.limiter_threshold_db;
    el.masterLimiterThreshold.disabled = !state.master.limiter_available || !state.master.limiter_enabled;
    el.masterLimiterThresholdValue.textContent = `${state.master.limiter_threshold_db} dB`;
    el.masterLimiterWarning.textContent = state.master.limiter_available
      ? "Protege contra clipping quando vários pads tocam ao mesmo tempo."
      : "Limiter indisponível neste motor.";
  }

  function renderEngineStatus() {
    const assignedPads = state.pads.filter((pad) => pad.has_sample);
    const stoppedPads = assignedPads.filter((pad) => !state.engineStatus.pads[String(pad.pad_number)]);
    const problems = [];
    if (!state.engineStatus.jack) problems.push("JACK indisponível");
    if (!state.engineStatus.modhost) problems.push("mod-host indisponível");
    if (!state.master.available) problems.push("master sem plugin");
    if (stoppedPads.length) problems.push(`${stoppedPads.length} pad(s) sem player`);
    if (state.engineStatus.last_error) problems.push(state.engineStatus.last_error);
    const level = problems.length === 0 ? "ok" : state.engineStatus.jack ? "warning" : "error";

    el.engineStatus.className = `engine-status ${level}`;
    el.engineStatus.textContent = level === "ok" ? "Motor OK" : "Motor";
    el.engineStatus.title = problems.length ? problems.join(" · ") : "Motor de áudio pronto";
    el.engineStatusText.textContent = problems.length ? problems.join(". ") : "JACK, sfizz e mod-host estão prontos.";
    const cpu = state.engineStatus.cpu_percent;
    el.cpuMeter.textContent = Number.isFinite(cpu) ? `CPU ${Math.round(cpu)}%` : "CPU --";
    el.cpuMeter.className = `cpu-meter${cpu >= 85 ? " high" : cpu >= 65 ? " warning" : ""}`;
  }

  async function setMaster(body) {
    const response = await fetch("/api/master", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) return;
    state.master = { ...state.master, ...body };
    renderMaster();
    if (result.engine_applied === false) {
      el.engineStatusDetail.classList.remove("hidden");
      el.engineStatusText.textContent = "Configuração salva, mas o plugin de master não está disponível.";
    }
  }

  function cancelPanic() {
    if (panicTimer !== null) clearTimeout(panicTimer);
    panicTimer = null;
    el.panicBtn.classList.remove("arming");
  }

  function armPanic(event) {
    event.preventDefault();
    if (panicTimer !== null) return;
    el.panicBtn.classList.add("arming");
    panicTimer = setTimeout(async () => {
      panicTimer = null;
      el.panicBtn.classList.remove("arming");
      const response = await fetch("/api/panic", { method: "POST" });
      if (response.ok) {
        el.panicBtn.classList.add("triggered");
        setTimeout(() => el.panicBtn.classList.remove("triggered"), 1200);
      }
    }, 650);
  }

  el.masterVolume.addEventListener("change", () => setMaster({ volume: Number(el.masterVolume.value) }));
  el.masterMuteBtn.addEventListener("click", () => setMaster({ muted: !state.master.muted }));
  el.masterVolumeLarge.addEventListener("change", () => setMaster({ volume: Number(el.masterVolumeLarge.value) }));
  el.masterMuteLargeBtn.addEventListener("click", () => setMaster({ muted: !state.master.muted }));
  el.masterLimiterEnabled.addEventListener("change", () => setMaster({ limiter_enabled: el.masterLimiterEnabled.checked }));
  el.masterLimiterThreshold.addEventListener("change", () => setMaster({ limiter_threshold_db: Number(el.masterLimiterThreshold.value) }));
  el.engineStatus.addEventListener("click", () => {
    const hidden = el.engineStatusDetail.classList.toggle("hidden");
    el.engineStatus.setAttribute("aria-expanded", String(!hidden));
  });
  el.engineRestartBtn.addEventListener("click", () => fetch("/api/engine/restart", { method: "POST" }));
  el.fullRestartBtn.addEventListener("click", async () => {
    if (!window.confirm("Reiniciar o DiakoPad e o áudio do laptop?")) return;
    el.fullRestartBtn.disabled = true;
    el.fullRestartBtn.textContent = "Reiniciando...";
    try {
      const response = await fetch("/api/system/restart", { method: "POST" });
      if (!response.ok) throw new Error("restart failed");
    } catch (_) {
      el.fullRestartBtn.disabled = false;
      el.fullRestartBtn.textContent = "Reiniciar";
      window.alert("Não foi possível iniciar a recuperação do sistema.");
    }
  });
  el.clearAllPadsBtn.addEventListener("click", async () => {
    if (!window.confirm("Limpar o som de todos os 16 pads? Volume, efeitos e knobs são mantidos.")) return;
    const response = await fetch("/api/pads/clear", { method: "POST" });
    if (!response.ok) window.alert("Não foi possível limpar os pads.");
  });
  el.panicBtn.addEventListener("pointerdown", armPanic);
  ["pointerup", "pointerleave", "pointercancel"].forEach((eventName) => {
    el.panicBtn.addEventListener(eventName, cancelPanic);
  });
  el.panicBtn.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") armPanic(event);
  });
  el.panicBtn.addEventListener("keyup", cancelPanic);
  el.globalTapBtn.addEventListener("click", tapTempo);
  el.globalSequencerBtn.addEventListener("click", toggleSequencer);

  // The physical SMC-PAD numbers pads bottom-left (1) to top-right (16), in
  // rows of 4 from the bottom: [1-4] bottom, [5-8], [9-12], [13-16] top. The
  // on-screen grid renders top-to-bottom, so row order is reversed to match
  // what the user sees when looking at the controller.
  function displayOrder(pads) {
    const byNumber = new Map(pads.map((p) => [p.pad_number, p]));
    const order = [];
    for (let row = 3; row >= 0; row--) {
      for (let col = 0; col < 4; col++) {
        order.push(byNumber.get(row * 4 + col + 1));
      }
    }
    return order;
  }

  function buildPadButton(pad) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pad" + (pad.has_sample ? " filled" : "");
    button.dataset.padNumber = String(pad.pad_number);
    button.setAttribute("aria-label", `Pad ${pad.pad_number}: ${pad.has_sample ? pad.display_name : "vazio"}`);
    button.innerHTML = `
      <div class="pad-number">PAD ${pad.pad_number}</div>
      <div class="pad-name">${pad.has_sample ? escapeHtml(pad.display_name) : "vazio"}</div>
      <div class="pad-note">nota ${pad.midi_note}</div>
    `;
    let pressTimer = null;
    let longPressed = false;
    const cancelPress = () => {
      if (pressTimer !== null) clearTimeout(pressTimer);
      pressTimer = null;
    };
    button.addEventListener("pointerdown", () => {
      longPressed = false;
      if (state.kitBrowse.active) return;
      pressTimer = setTimeout(() => {
        longPressed = true;
        pressTimer = null;
        openAssignModal(pad.pad_number);
      }, 550);
    });
    button.addEventListener("pointerup", () => {
      const shouldTrigger = !longPressed;
      cancelPress();
      if (shouldTrigger) triggerScreenPad(pad.pad_number);
    });
    ["pointercancel", "pointerleave"].forEach((eventName) => button.addEventListener(eventName, cancelPress));
    button.addEventListener("keydown", (event) => {
      if (!event.repeat && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        triggerScreenPad(pad.pad_number);
      }
    });
    return button;
  }

  function renderPads() {
    const pads = displayOrder(state.pads);
    for (const grid of [el.padGrid, el.perfPadGrid]) {
      grid.innerHTML = "";
      for (const pad of pads) {
        grid.appendChild(buildPadButton(pad));
      }
    }
    // Fresh buttons never carry the kit-browse pulse/highlight classes -
    // reapply them from the current state (harmless no-op when not browsing).
    renderKitBrowseGridState();
  }

  // The hit animation comes from the server's pad_hit broadcast, so every
  // client (including this one) flashes exactly once per hit.
  async function triggerScreenPad(padNumber) {
    if (state.kitBrowse.active) {
      await triggerKitBrowsePad(padNumber);
      return;
    }
    await fetch(`/api/pads/${padNumber}/trigger`, { method: "POST" });
  }

  // Lets the touchscreen exercise kit-browse mode end-to-end without a
  // physical SMC-PAD. Two phases, matching the hardware flow: "armed" -
  // ANY pad tap picks the target pad being edited; "browsing" - reserved
  // pads hit nav/confirm/back, everything else picks that pad-role's sound
  // in the highlighted kit as the candidate (previewed if the setting
  // allows).
  async function triggerKitBrowsePad(padNumber) {
    if (state.kitBrowse.phase === "armed") {
      await fetch("/api/kit-browse/select-target", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pad_number: padNumber }),
      });
      return;
    }
    const role = KIT_BROWSE_RESERVED_PADS[padNumber];
    if (role === "back") {
      await fetch("/api/kit-browse/back", { method: "POST" });
    } else if (role === "confirm") {
      await fetch("/api/kit-browse/confirm", { method: "POST" });
    } else if (role) {
      await fetch("/api/kit-browse/nav", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ direction: role }),
      });
    } else {
      await fetch(`/api/kit-browse/preview/${padNumber}`, { method: "POST" });
    }
  }

  function flashPadHit(padNumber) {
    for (const grid of [el.padGrid, el.perfPadGrid]) {
      const pad = grid.querySelector(`[data-pad-number="${padNumber}"]`);
      if (!pad) continue;
      pad.classList.remove("hit");
      void pad.offsetWidth;
      pad.classList.add("hit");
      setTimeout(() => pad.classList.remove("hit"), 130);
    }
  }

  function formatMidiNote(note) {
    const names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
    return `${names[note % 12]}${Math.floor(note / 12) - 2}`;
  }

  function showMidiNote(note) {
    el.midiNoteIndicator.textContent = `MIDI: ${formatMidiNote(note)} (${note}) · Oitava ${Math.floor(note / 12) - 2}`;
    clearTimeout(midiNoteHitTimer);
    el.midiNoteIndicator.classList.remove("hit");
    void el.midiNoteIndicator.offsetWidth;
    el.midiNoteIndicator.classList.add("hit");
    midiNoteHitTimer = setTimeout(() => el.midiNoteIndicator.classList.remove("hit"), 180);
  }

  // Cenas: named snapshots of the whole set (16 pad assignments, effect
  // chains, knobs, tempo/metronome and sequencer grid), for switching during
  // a show. Only ACTIVE scenes take part in the ◀ ▶ navigation; inactive ones
  // stay saved in the manager list below. Scene switching re-applies every pad
  // in the engine, so the grid shows a busy state while it runs.

  function activeScenes() {
    return state.scenes.filter((s) => s.active);
  }

  function currentScene() {
    return activeScenes()[state.sceneIndex] || null;
  }

  async function loadScenes() {
    const res = await fetch("/api/scenes");
    state.scenes = await res.json();
    if (state.sceneIndex >= activeScenes().length) state.sceneIndex = 0;
    renderSceneStrip();
    renderSceneList();
  }

  function renderSceneStrip() {
    const act = activeScenes();
    const scene = currentScene();
    el.sceneName.textContent = scene ? scene.name : "Nenhuma cena ativa";
    el.sceneCount.textContent = act.length ? `${state.sceneIndex + 1} / ${act.length}` : "";
    el.scenePrev.disabled = act.length < 2;
    el.sceneNext.disabled = act.length < 2;
    el.sceneDelete.disabled = !scene;
  }

  function renderSceneList() {
    const term = state.sceneSearch.trim().toLowerCase();
    const scenes = state.scenes.filter((s) => !term || s.name.toLowerCase().includes(term));
    el.sceneList.innerHTML = "";
    if (!scenes.length) {
      el.sceneList.innerHTML = `<li class="scene-empty">Nenhuma cena${term ? " encontrada" : " salva"}.</li>`;
      return;
    }
    const current = currentScene();
    for (const scene of scenes) {
      const li = document.createElement("li");
      li.className = "scene-row" + (current && current.id === scene.id ? " current" : "");
      li.innerHTML = `
        <label class="scene-active-toggle" title="Usar na performance">
          <input type="checkbox" ${scene.active ? "checked" : ""}>
        </label>
        <button class="scene-load-btn" type="button">${escapeHtml(scene.name)}</button>
        <button class="icon-btn delete-btn" title="Excluir">🗑</button>
      `;
      li.querySelector(".scene-active-toggle input").addEventListener("change", (e) => {
        fetch(`/api/scenes/${scene.id}/active`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ active: e.target.checked }),
        });
      });
      li.querySelector(".scene-load-btn").addEventListener("click", () => {
        const index = activeScenes().findIndex((s) => s.id === scene.id);
        if (index >= 0) loadSceneAt(index);
      });
      li.querySelector(".delete-btn").addEventListener("click", () => deleteScene(scene));
      el.sceneList.appendChild(li);
    }
  }

  function showSceneLoading(name) {
    state.sceneLoadingCount++;
    el.sceneLoadingText.textContent = name ? `Carregando "${name}"...` : "Carregando cena...";
    el.sceneLoadingOverlay.classList.remove("hidden");
  }

  function hideSceneLoading() {
    state.sceneLoadingCount = Math.max(0, state.sceneLoadingCount - 1);
    if (state.sceneLoadingCount === 0) el.sceneLoadingOverlay.classList.add("hidden");
  }

  async function loadSceneAt(index) {
    const act = activeScenes();
    if (!act.length || state.sceneLoadingCount > 0) return;
    state.sceneIndex = (index + act.length) % act.length;
    renderSceneStrip();
    renderSceneList();
    const scene = currentScene();
    el.perfPadGrid.classList.add("applying");
    showSceneLoading(scene.name);
    try {
      await fetch(`/api/scenes/${scene.id}/load`, { method: "POST" });
    } finally {
      el.perfPadGrid.classList.remove("applying");
      hideSceneLoading();
    }
  }

  async function saveCurrentScene() {
    const current = currentScene();
    const suggestion = current ? current.name : "";
    const name = window.prompt("Nome da cena:", suggestion);
    if (name === null) return;
    const trimmed = name.trim();
    if (!trimmed) return;
    const res = await fetch("/api/scenes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: trimmed }),
    });
    if (!res.ok) {
      window.alert("Não foi possível salvar a cena.");
      return;
    }
    await loadScenes();
    const saved = activeScenes().findIndex((s) => s.name === trimmed);
    if (saved >= 0) {
      state.sceneIndex = saved;
      renderSceneStrip();
    }
  }

  async function deleteScene(scene) {
    if (!scene) return;
    if (!window.confirm(`Excluir a cena "${scene.name}"?`)) return;
    await fetch(`/api/scenes/${scene.id}`, { method: "DELETE" });
    await loadScenes();
  }

  function deleteCurrentScene() {
    return deleteScene(currentScene());
  }

  el.scenePrev.addEventListener("click", () => loadSceneAt(state.sceneIndex - 1));
  el.sceneNext.addEventListener("click", () => loadSceneAt(state.sceneIndex + 1));
  el.sceneSave.addEventListener("click", saveCurrentScene);
  el.sceneDelete.addEventListener("click", deleteCurrentScene);
  el.sceneSearch.addEventListener("input", () => {
    state.sceneSearch = el.sceneSearch.value;
    renderSceneList();
  });

  // Sons: browsed one folder level at a time (see backend GET
  // /api/sounds/browse) so a huge imported library never has to render as
  // one giant flat list - state.sounds (the full flat list, fetched
  // separately) stays reserved for the pad-assign modal's cross-folder search.

  function currentSoundFolder() {
    return state.soundBrowserPath.join("/");
  }

  async function loadSoundBrowser() {
    const res = await fetch(`/api/sounds/browse?folder=${encodeURIComponent(currentSoundFolder())}`);
    state.soundBrowserView = await res.json();
    renderSoundList();
  }

  function navigateToFolder(path) {
    state.soundBrowserPath = path;
    loadSoundBrowser();
  }

  function renderBreadcrumb() {
    el.soundBreadcrumb.innerHTML = "";
    const rootBtn = document.createElement("button");
    rootBtn.type = "button";
    rootBtn.className = "breadcrumb-item";
    rootBtn.textContent = "Sons";
    rootBtn.addEventListener("click", () => navigateToFolder([]));
    el.soundBreadcrumb.appendChild(rootBtn);

    state.soundBrowserPath.forEach((segment, i) => {
      const sep = document.createElement("span");
      sep.className = "breadcrumb-sep";
      sep.textContent = "/";
      el.soundBreadcrumb.appendChild(sep);

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "breadcrumb-item";
      btn.textContent = segment;
      btn.addEventListener("click", () => navigateToFolder(state.soundBrowserPath.slice(0, i + 1)));
      el.soundBreadcrumb.appendChild(btn);
    });
  }

  // Mirrors currentSoundFolder/loadSoundBrowser/navigateToFolder/
  // renderBreadcrumb above, but for the pad-assign modal's own folder
  // position - kept separate so browsing inside the modal never disturbs
  // (or gets disturbed by) whatever folder the Sons tab is showing.
  function currentModalSoundFolder() {
    return state.modalSoundPath.join("/");
  }

  async function loadModalSoundBrowser() {
    const res = await fetch(`/api/sounds/browse?folder=${encodeURIComponent(currentModalSoundFolder())}`);
    state.modalSoundView = await res.json();
    renderModalSoundList(el.modalSearch.value);
  }

  function navigateModalToFolder(path) {
    state.modalSoundPath = path;
    el.modalSearch.value = "";
    loadModalSoundBrowser();
  }

  function renderModalBreadcrumb() {
    el.modalBreadcrumb.innerHTML = "";
    const rootBtn = document.createElement("button");
    rootBtn.type = "button";
    rootBtn.className = "breadcrumb-item";
    rootBtn.textContent = "Sons";
    rootBtn.addEventListener("click", () => navigateModalToFolder([]));
    el.modalBreadcrumb.appendChild(rootBtn);

    state.modalSoundPath.forEach((segment, i) => {
      const sep = document.createElement("span");
      sep.className = "breadcrumb-sep";
      sep.textContent = "/";
      el.modalBreadcrumb.appendChild(sep);

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "breadcrumb-item";
      btn.textContent = segment;
      btn.addEventListener("click", () => navigateModalToFolder(state.modalSoundPath.slice(0, i + 1)));
      el.modalBreadcrumb.appendChild(btn);
    });
  }

  function renderSoundList() {
    renderBreadcrumb();
    el.soundList.innerHTML = "";
    const { folders, samples } = state.soundBrowserView;
    const inUse = new Set(state.pads.filter(p => p.sample_id).map(p => p.sample_id));
    const selectableIds = selectableSoundIdsInFolder(currentSoundFolder(), inUse);
    const availableIds = new Set(state.sounds.filter(sound => !inUse.has(sound.id)).map(sound => sound.id));
    for (const id of state.selectedSoundIds) {
      if (!availableIds.has(id)) state.selectedSoundIds.delete(id);
    }
    renderSoundSelectionControls(selectableIds);

    for (const name of folders) {
      const li = document.createElement("li");
      li.className = "sound-item folder-item";
      const folderPath = [...state.soundBrowserPath, name].join("/");
      const folderSelectableIds = selectableSoundIdsInFolder(folderPath, inUse);
      const folderSelectedCount = [...folderSelectableIds].filter(id => state.selectedSoundIds.has(id)).length;
      li.innerHTML = `
        <label class="sound-select folder-select">
          <input type="checkbox" aria-label="Selecionar pasta ${escapeHtml(name)}" ${folderSelectableIds.size ? "" : "disabled"} ${folderSelectedCount === folderSelectableIds.size && folderSelectableIds.size ? "checked" : ""}>
        </label>
        <span class="sound-name">📁 ${escapeHtml(name)}</span>
      `;
      const folderCheckbox = li.querySelector(".folder-select input");
      folderCheckbox.indeterminate = folderSelectedCount > 0 && folderSelectedCount < folderSelectableIds.size;
      li.querySelector(".folder-select").addEventListener("click", (event) => event.stopPropagation());
      folderCheckbox.addEventListener("click", (event) => event.stopPropagation());
      folderCheckbox.addEventListener("change", () => {
        toggleSoundSelection(folderSelectableIds, folderCheckbox.checked);
        renderSoundList();
      });
      li.addEventListener("click", () => navigateToFolder([...state.soundBrowserPath, name]));
      el.soundList.appendChild(li);
    }

    if (folders.length === 0 && samples.length === 0) {
      el.soundList.innerHTML += `<li class="sound-item"><span class="sound-name">Pasta vazia.</span></li>`;
      return;
    }

    for (const sound of samples) {
      const li = document.createElement("li");
      li.className = "sound-item";
      const used = inUse.has(sound.id);
      li.innerHTML = `
        <label class="sound-select">
          <input type="checkbox" aria-label="Selecionar ${escapeHtml(sound.display_name)}" ${used ? "disabled" : ""} ${state.selectedSoundIds.has(sound.id) ? "checked" : ""}>
        </label>
        <button class="icon-btn play-btn" title="Ouvir">▶</button>
        <span class="sound-name">${escapeHtml(sound.display_name)}</span>
        <button class="icon-btn delete-btn" title="${used ? "Em uso, não pode remover" : "Remover"}" ${used ? "disabled" : ""}>🗑</button>
      `;
      li.querySelector(".sound-select input").addEventListener("change", (event) => {
        toggleSoundSelection([sound.id], event.target.checked);
        renderSoundList();
      });
      li.querySelector(".play-btn").addEventListener("click", () => playPreview(sound.id));
      li.querySelector(".delete-btn").addEventListener("click", () => deleteSound(sound.id));
      el.soundList.appendChild(li);
    }
  }

  function selectableSoundIdsInFolder(folder, inUse) {
    const prefix = folder ? `${folder}/` : "";
    return new Set(
      state.sounds
        .filter(sound => !inUse.has(sound.id) && (sound.folder === folder || sound.folder.startsWith(prefix)))
        .map(sound => sound.id)
    );
  }

  function toggleSoundSelection(soundIds, selected) {
    for (const soundId of soundIds) {
      if (selected) state.selectedSoundIds.add(soundId);
      else state.selectedSoundIds.delete(soundId);
    }
  }

  function renderSoundSelectionControls(selectableIds) {
    const selectedCount = state.selectedSoundIds.size;
    const selectedInFolder = [...selectableIds].filter(id => state.selectedSoundIds.has(id)).length;
    el.selectAllSounds.checked = selectableIds.size > 0 && selectedInFolder === selectableIds.size;
    el.selectAllSounds.indeterminate = selectedInFolder > 0 && selectedInFolder < selectableIds.size;
    el.selectAllSounds.disabled = selectableIds.size === 0;
    el.selectedSoundsCount.textContent = selectedCount === 1 ? "1 selecionado" : `${selectedCount} selecionados`;
    el.deleteSelectedSounds.disabled = selectedCount === 0;
  }

  // --- Volumes --------------------------------------------------------

  function renderVolumes() {
    el.volumeList.innerHTML = "";
    for (const pad of displayOrder(state.pads)) {
      const li = document.createElement("li");
      li.className = "mix-card";
      li.innerHTML = `
        <button class="mix-card-header" type="button" data-role="mix-card-toggle" aria-expanded="false">
          <span class="mix-card-identity">
            <span class="pad-label">PAD ${pad.pad_number}</span>
            <span class="pad-sound">${pad.has_sample ? escapeHtml(pad.display_name) : "Vazio"}</span>
          </span>
          <span class="mix-card-summary" data-role="mix-card-summary">${pad.volume_db} dB · ${panLabel(pad.pan)}</span>
          <span class="mix-card-chevron" aria-hidden="true">⌄</span>
        </button>
        <div class="mix-card-body">
          <div class="mix-controls">
          <div class="mix-control">
            <span class="mix-control-label">Volume</span>
            <input type="range" min="-24" max="12" step="0.5" value="${pad.volume_db}" data-role="volume">
            <span class="mix-value" data-role="volume-value">${pad.volume_db} dB</span>
          </div>
          <div class="mix-control">
            <span class="mix-control-label">Pan</span>
            <input type="range" min="-100" max="100" step="1" value="${pad.pan}" data-role="pan">
            <span class="mix-value" data-role="pan-value">${panLabel(pad.pan)}</span>
          </div>
          </div>
        </div>
      `;
      bindMixCard(li);
      const volumeInput = li.querySelector('[data-role="volume"]');
      const volumeValue = li.querySelector('[data-role="volume-value"]');
      const summary = li.querySelector('[data-role="mix-card-summary"]');
      volumeInput.addEventListener("input", () => {
        volumeValue.textContent = `${volumeInput.value} dB`;
        summary.textContent = `${volumeInput.value} dB · ${panLabel(Number(panInput.value))}`;
      });
      volumeInput.addEventListener("change", () => setPadMix(pad.pad_number, { volume_db: Number(volumeInput.value) }));

      const panInput = li.querySelector('[data-role="pan"]');
      const panValue = li.querySelector('[data-role="pan-value"]');
      panInput.addEventListener("input", () => {
        panValue.textContent = panLabel(Number(panInput.value));
        summary.textContent = `${volumeInput.value} dB · ${panLabel(Number(panInput.value))}`;
      });
      panInput.addEventListener("change", () => setPadMix(pad.pad_number, { pan: Number(panInput.value) }));

      el.volumeList.appendChild(li);
    }
  }

  function panLabel(pan) {
    if (pan === 0) return "centro";
    return pan < 0 ? `E ${Math.abs(pan)}` : `D ${pan}`;
  }

  function bindMixCard(card) {
    const toggle = card.querySelector('[data-role="mix-card-toggle"]');
    toggle.addEventListener("click", () => {
      const open = card.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(open));
    });
  }

  function toneFromCutoff(cutoffHz) {
    if (!cutoffHz) return 100;
    const frac = Math.log(cutoffHz / 200) / Math.log(20000 / 200);
    return Math.round(frac * 100);
  }

  // --- Efeitos (tom + slots configuráveis) --------------------------------

  function renderEffects() {
    el.effectsList.innerHTML = "";
    for (const pad of displayOrder(state.pads)) {
      const tone = toneFromCutoff(pad.cutoff_hz);
      const li = document.createElement("li");
      li.className = "mix-card";
      li.innerHTML = `
        <button class="mix-card-header" type="button" data-role="mix-card-toggle" aria-expanded="false">
          <span class="mix-card-identity">
            <span class="pad-label">PAD ${pad.pad_number}</span>
            <span class="pad-sound">${pad.has_sample ? escapeHtml(pad.display_name) : "Vazio"}</span>
          </span>
          <span class="mix-card-summary effect-summary">${escapeHtml(effectSummary(pad.pad_number))}</span>
          <span class="mix-card-chevron" aria-hidden="true">⌄</span>
        </button>
        <div class="mix-card-body">
          <div class="mix-controls">
          <div class="mix-control">
            <span class="mix-control-label">Tom</span>
            <input type="range" min="0" max="100" step="1" value="${tone}" data-role="tone">
            <span class="mix-value" data-role="tone-value">${tone >= 100 ? "aberto" : tone + "%"}</span>
          </div>
          </div>
          <p class="effect-card-title">Cadeia de efeitos</p>
          <div class="effect-slots"></div>
        </div>
      `;
      bindMixCard(li);
      const toneInput = li.querySelector('[data-role="tone"]');
      const toneValue = li.querySelector('[data-role="tone-value"]');
      toneInput.addEventListener("input", () => {
        const v = Number(toneInput.value);
        toneValue.textContent = v >= 100 ? "aberto" : v + "%";
      });
      toneInput.addEventListener("change", () => setPadMix(pad.pad_number, { tone: Number(toneInput.value) }));

      renderEffectSlots(li.querySelector(".effect-slots"), pad.pad_number);
      el.effectsList.appendChild(li);
    }
  }

  function effectSummary(padNumber) {
    const labels = state.padEffects
      .filter((effect) => effect.pad_number === padNumber && effect.plugin_id)
      .sort((a, b) => a.slot_index - b.slot_index)
      .map((effect) => state.effectsCatalog.find((plugin) => plugin.plugin_id === effect.plugin_id)?.label)
      .filter(Boolean);
    return labels.length ? labels.join(" · ") : "Sem efeitos";
  }

  function renderEffectSlots(container, padNumber) {
    container.innerHTML = "";
    const slots = state.padEffects
      .filter((e) => e.pad_number === padNumber)
      .sort((a, b) => a.slot_index - b.slot_index);
    for (const slot of slots) {
      const div = document.createElement("div");
      div.className = "effect-slot";
      const options = ['<option value="">Vazio</option>'].concat(
        state.effectsCatalog.map(
          (p) => `<option value="${p.plugin_id}" ${p.plugin_id === slot.plugin_id ? "selected" : ""}>${escapeHtml(p.label)}</option>`
        )
      );
      div.innerHTML = `
        <div class="effect-slot-header">
          <span class="effect-slot-label">FX ${slot.slot_index}</span>
          <select class="effect-slot-select">${options.join("")}</select>
        </div>
        <div class="effect-slot-params"></div>
      `;
      div.querySelector("select").addEventListener("change", (e) => {
        setPadEffectSlot(padNumber, slot.slot_index, e.target.value || null);
      });
      if (slot.plugin_id) {
        renderEffectParams(div.querySelector(".effect-slot-params"), padNumber, slot);
      }
      container.appendChild(div);
    }
  }

  function renderEffectParams(container, padNumber, slot) {
    const plugin = state.effectsCatalog.find((p) => p.plugin_id === slot.plugin_id);
    if (!plugin) return;
    for (const p of plugin.params) {
      const value = slot.params[p.symbol] ?? p.default;
      const step = (p.max - p.min) / 100 || 1;
      const knobParam = `slot${slot.slot_index}:${p.symbol}`;
      const mapping = state.knobs.find(
        (knob) => knob.scope === "pad" && knob.pad_number === padNumber && knob.param === knobParam
      );
      const row = document.createElement("div");
      row.className = "mix-control";
      row.innerHTML = `
        <span class="mix-control-label">${escapeHtml(p.label)}</span>
        <input type="range" min="${p.min}" max="${p.max}" step="${step}" value="${value}">
        <span class="mix-value">${formatParamValue(value, p.unit)}</span>
        <button class="effect-knob-assign" type="button" title="Atribuir knob">${mapping ? `CC ${mapping.cc_number}` : "Knob"}</button>
      `;
      const input = row.querySelector("input");
      const valueEl = row.querySelector(".mix-value");
      input.addEventListener("input", () => (valueEl.textContent = formatParamValue(Number(input.value), p.unit)));
      input.addEventListener("change", () => setPadEffectParam(padNumber, slot.slot_index, p.symbol, Number(input.value)));
      row.querySelector(".effect-knob-assign").addEventListener("click", () => {
        el.knobModal.classList.remove("hidden");
        startKnobCapture("pad", padNumber, knobParam);
      });
      container.appendChild(row);
    }
  }

  function formatParamValue(value, unit) {
    const rounded = Math.round(value * 100) / 100;
    return unit ? `${rounded}${unit}` : `${rounded}`;
  }

  async function setPadEffectSlot(padNumber, slotIndex, pluginId) {
    const response = await fetch(`/api/pads/${padNumber}/effects/${slotIndex}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plugin_id: pluginId }),
    });
    if (response.ok) refreshKnobTargets();
  }

  async function setPadEffectParam(padNumber, slotIndex, symbol, value) {
    await fetch(`/api/pads/${padNumber}/effects/${slotIndex}/param`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol, value }),
    });
  }

  async function setPadMix(padNumber, body) {
    await fetch(`/api/pads/${padNumber}/mix`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  // --- Sequencer -----------------------------------------------------

  function renderSequencerHead() {
    el.sequencerHeadRow.innerHTML = '<th class="sequencer-pad-col"></th>';
    for (let step = 0; step < 16; step++) {
      const th = document.createElement("th");
      th.textContent = String(step + 1);
      if (step % 4 === 0) th.classList.add("beat-start");
      el.sequencerHeadRow.appendChild(th);
    }
  }

  // Optimistic toggle: flipping the local state before the request keeps
  // rapid double taps meaningful (start then stop) even before the server
  // echo arrives over the WebSocket.
  function toggleSequencer() {
    const running = !state.sequencer.running;
    state.sequencer.running = running;
    renderSequencer();
    fetch("/api/sequencer/transport", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ running }),
    }).catch(() => {
      state.sequencer.running = !running;
      renderSequencer();
    });
  }

  function renderSequencer() {
    el.sequencerPlayBtn.textContent = state.sequencer.running ? "■ Parar" : "▶ Tocar";
    el.sequencerPlayBtn.classList.toggle("active", state.sequencer.running);
    el.globalSequencerBtn.textContent = state.sequencer.running ? "SEQ ■" : "SEQ ▶";
    el.globalSequencerBtn.classList.toggle("active", state.sequencer.running);

    const activeByKey = new Set(
      state.sequencer.steps.filter((s) => s.active).map((s) => `${s.pad_number}:${s.step_index}`)
    );

    el.sequencerBody.innerHTML = "";
    for (const pad of displayOrder(state.pads)) {
      const tr = document.createElement("tr");
      const labelTd = document.createElement("td");
      labelTd.className = "sequencer-pad-col";
      labelTd.textContent = `PAD ${pad.pad_number}`;
      tr.appendChild(labelTd);
      for (let step = 0; step < 16; step++) {
        const active = activeByKey.has(`${pad.pad_number}:${step}`);
        const td = document.createElement("td");
        td.className =
          "sequencer-cell" +
          (active ? " active" : "") +
          (step % 4 === 0 ? " beat-start" : "") +
          (state.sequencer.running && step === state.sequencer.current_step ? " current" : "");
        td.dataset.step = String(step);
        td.addEventListener("click", () => toggleSequencerStep(pad.pad_number, step, !active));
        tr.appendChild(td);
      }
      el.sequencerBody.appendChild(tr);
    }
  }

  function updateSequencerPlayhead(step) {
    el.sequencerBody.querySelectorAll(".sequencer-cell.current").forEach((c) => c.classList.remove("current"));
    if (!state.sequencer.running) return;
    el.sequencerBody.querySelectorAll(`.sequencer-cell[data-step="${step}"]`).forEach((c) => c.classList.add("current"));
  }

  async function toggleSequencerStep(padNumber, stepIndex, active) {
    await fetch(`/api/sequencer/steps/${padNumber}/${stepIndex}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active }),
    });
  }

  el.sequencerPlayBtn.addEventListener("click", toggleSequencer);
  el.sequencerBpm.addEventListener("change", () => {
    setTempo(Number(el.sequencerBpm.value));
  });
  el.sequencerClearBtn.addEventListener("click", () => fetch("/api/sequencer/clear", { method: "POST" }));

  // Patterns: named snapshots of the sequencer grid (same relationship as
  // scenes have to the pads), so a set can flip between programmed patterns
  // instead of only ever editing the one live grid.

  async function loadPatterns() {
    const res = await fetch("/api/patterns");
    state.patterns = await res.json();
    if (state.patternIndex >= state.patterns.length) state.patternIndex = 0;
    renderPatternStrip();
  }

  function renderPatternStrip() {
    const pattern = state.patterns[state.patternIndex];
    el.patternName.textContent = pattern ? pattern.name : "Padrão sem salvar";
    el.patternCount.textContent = state.patterns.length
      ? `${state.patternIndex + 1} / ${state.patterns.length}`
      : "";
    el.patternPrev.disabled = state.patterns.length < 2;
    el.patternNext.disabled = state.patterns.length < 2;
    el.patternDelete.disabled = !pattern;
  }

  async function loadPatternAt(index) {
    if (!state.patterns.length) return;
    state.patternIndex = (index + state.patterns.length) % state.patterns.length;
    renderPatternStrip();
    const pattern = state.patterns[state.patternIndex];
    const wrap = el.sequencerBody.closest(".sequencer-grid-wrap");
    wrap.classList.add("applying");
    try {
      await fetch(`/api/patterns/${pattern.id}/load`, { method: "POST" });
    } finally {
      wrap.classList.remove("applying");
    }
  }

  async function saveCurrentPattern() {
    const suggestion = state.patterns[state.patternIndex] ? state.patterns[state.patternIndex].name : "";
    const name = window.prompt("Nome do padrão:", suggestion);
    if (name === null) return;
    const trimmed = name.trim();
    if (!trimmed) return;
    const res = await fetch("/api/patterns", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: trimmed }),
    });
    if (!res.ok) {
      window.alert("Não foi possível salvar o padrão.");
      return;
    }
    await loadPatterns();
    const saved = state.patterns.findIndex((p) => p.name === trimmed);
    if (saved >= 0) {
      state.patternIndex = saved;
      renderPatternStrip();
    }
  }

  async function deleteCurrentPattern() {
    const pattern = state.patterns[state.patternIndex];
    if (!pattern) return;
    if (!window.confirm(`Excluir o padrão "${pattern.name}"?`)) return;
    await fetch(`/api/patterns/${pattern.id}`, { method: "DELETE" });
    await loadPatterns();
  }

  el.patternPrev.addEventListener("click", () => loadPatternAt(state.patternIndex - 1));
  el.patternNext.addEventListener("click", () => loadPatternAt(state.patternIndex + 1));
  el.patternSave.addEventListener("click", saveCurrentPattern);
  el.patternDelete.addEventListener("click", deleteCurrentPattern);

  // --- Metrônomo e tempo global ---------------------------------------

  function renderTempo() {
    const bpm = Math.round(state.tempo.bpm);
    el.sequencerBpm.value = bpm;
    el.metronomeBpm.value = bpm;
    el.globalBpm.textContent = String(bpm);
  }

  function renderMetronome() {
    const s = state.metronome;
    el.metronomePlayBtn.textContent = s.running ? "■ Parar" : "▶ Tocar";
    el.metronomePlayBtn.classList.toggle("active", s.running);
    el.metronomeStyleSelect.value = s.style;
    el.metronomeSignatureSelect.value = s.signature;
    renderMetronomeBeat(s.beat_in_bar);
  }

  function renderMetronomeStyles() {
    el.metronomeStyleSelect.innerHTML = state.metronomeStyles
      .map((style) => `<option value="${style.style}">${escapeHtml(style.label)}</option>`)
      .join("");
  }

  function renderMetronomeSignatures() {
    el.metronomeSignatureSelect.innerHTML = state.timeSignatures
      .map((sig) => `<option value="${sig.signature}">${escapeHtml(sig.label)}</option>`)
      .join("");
  }

  function currentTimeSignature() {
    return (
      state.timeSignatures.find((sig) => sig.signature === state.metronome.signature) || {
        pulses: 4,
        accents: [0],
      }
    );
  }

  function renderMetronomeBeat(activeBeat) {
    const meta = currentTimeSignature();
    el.metronomeBeatRow.innerHTML = "";
    for (let beat = 0; beat < meta.pulses; beat++) {
      const dot = document.createElement("span");
      dot.className = "metronome-beat" +
        (meta.accents.includes(beat) ? " accent" : "") +
        (state.metronome.running && beat === activeBeat ? " active" : "");
      dot.setAttribute("aria-label", `Tempo ${beat + 1}`);
      el.metronomeBeatRow.appendChild(dot);
    }
  }

  function setTempo(bpm) {
    if (!Number.isFinite(bpm) || bpm < 40 || bpm > 240) {
      renderTempo();
      return;
    }
    state.tempo.bpm = bpm;
    renderTempo();
    fetch("/api/tempo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bpm }),
    });
  }

  function tapTempo() {
    const now = performance.now();
    if (tapTimestamps.length && now - tapTimestamps[tapTimestamps.length - 1] > 2000) {
      tapTimestamps = [];
    }
    tapTimestamps.push(now);
    tapTimestamps = tapTimestamps.slice(-6);
    if (tapTimestamps.length < 2) return;

    const elapsed = tapTimestamps[tapTimestamps.length - 1] - tapTimestamps[0];
    const averageInterval = elapsed / (tapTimestamps.length - 1);
    const bpm = Math.max(40, Math.min(240, Math.round(60000 / averageInterval)));
    setTempo(bpm);
  }

  el.metronomeBpm.addEventListener("change", () => setTempo(Number(el.metronomeBpm.value)));
  el.metronomeTapBtn.addEventListener("click", tapTempo);
  el.metronomePlayBtn.addEventListener("click", () => {
    fetch("/api/metronome/transport", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ running: !state.metronome.running }),
    });
  });
  el.metronomeStyleSelect.addEventListener("change", async () => {
    const response = await fetch("/api/metronome/style", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ style: el.metronomeStyleSelect.value }),
    });
    const result = await response.json();
    el.metronomeEngineWarning.classList.toggle("hidden", result.engine_applied !== false);
  });
  el.metronomeSignatureSelect.addEventListener("change", () => {
    fetch("/api/metronome/signature", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ signature: el.metronomeSignatureSelect.value }),
    });
  });

  // --- Looper ----------------------------------------------------------

  // Bar duration at the CURRENT tempo/signature - only meaningful as an
  // approximation for a loop recorded earlier at a different BPM, but still
  // useful context ("about how many bars is this loop"). Mirrors the same
  // pulses-per-bar math as engine/looper.py's _quantize_to_bar.
  function looperBarDuration() {
    const sig = state.timeSignatures.find((s) => s.signature === state.metronome.signature);
    if (!sig || !state.tempo.bpm) return null;
    return sig.pulses * (60 / state.tempo.bpm);
  }

  function renderLooper() {
    const s = state.looper;
    const labels = {
      stopped: "Parado",
      recording: "Gravando...",
      playing: "Tocando em loop",
      overdubbing: "Sobrepondo...",
    };
    el.looperStateLabel.textContent = labels[s.state] || s.state;
    el.looperRecordBtn.textContent = s.state === "recording" ? "■ Fechar trilha" : "● Gravar";
    el.looperRecordBtn.classList.toggle("active", s.state === "recording");
    el.looperRecordBtn.disabled = s.state === "overdubbing";
    el.looperOverdubBtn.textContent = s.state === "overdubbing" ? "■ Fechar sobreposição" : "+ Sobrepor";
    el.looperOverdubBtn.classList.toggle("active", s.state === "overdubbing");
    el.looperOverdubBtn.disabled = s.state !== "playing" && s.state !== "overdubbing";
    const tracks = s.tracks || [];
    const hasContent = tracks.some((t) => t.event_count > 0);
    el.looperPlayBtn.disabled = !(s.state === "stopped" && hasContent && s.loop_duration);
    el.looperStopBtn.disabled = s.state !== "playing" && s.state !== "overdubbing";
    el.looperClearBtn.disabled = s.state === "stopped" && !hasContent;

    el.looperProgress.classList.toggle("hidden", s.state !== "playing" && s.state !== "overdubbing");

    const details = [];
    const totalEvents = tracks.reduce((sum, t) => sum + (t.event_count || 0), 0);
    const usedTracks = tracks.filter((t) => t.event_count > 0).length;
    if (totalEvents) {
      const label = totalEvents === 1 ? "1 toque gravado" : `${totalEvents} toques gravados`;
      details.push(usedTracks > 1 ? `${label} em ${usedTracks} trilhas` : label);
    }
    if (s.loop_duration) {
      const barDuration = looperBarDuration();
      const bars = barDuration ? Math.max(1, Math.round(s.loop_duration / barDuration)) : null;
      const sigLabel = state.metronome.signature
        ? (state.timeSignatures.find((sg) => sg.signature === state.metronome.signature) || {}).label
        : null;
      details.push(
        bars
          ? `Loop de ${s.loop_duration.toFixed(1)}s · ≈ ${bars} ${bars === 1 ? "compasso" : "compassos"}` +
            (sigLabel ? ` (${Math.round(state.tempo.bpm)} BPM · ${sigLabel})` : "")
          : `Loop de ${s.loop_duration.toFixed(1)}s`
      );
    }
    el.looperDetails.textContent = details.join(" · ");

    renderLooperTracks();

    clearInterval(looperTimer);
    if (s.state === "recording" || s.state === "playing" || s.state === "overdubbing") {
      looperTimer = setInterval(updateLooperElapsed, 200);
      updateLooperElapsed();
    } else {
      el.looperElapsed.textContent = "";
      el.looperProgressFill.style.width = "0%";
    }
  }

  function renderLooperTracks() {
    const s = state.looper;
    const stateLabels = { stopped: "Vazia", recording: "Gravando", playing: "Tocando", overdubbing: "Sobrepondo" };
    el.looperTrackList.innerHTML = "";
    (s.tracks || []).forEach((t, i) => {
      const card = document.createElement("div");
      card.className = "looper-track state-" + (t.state || "stopped");
      if (i === s.selected_track) card.classList.add("selected");
      if (t.muted) card.classList.add("muted");
      card.dataset.track = String(i);

      const header = document.createElement("div");
      header.className = "looper-track-header";
      const name = document.createElement("span");
      name.className = "looper-track-name";
      name.textContent = `T${i + 1}`;
      const badge = document.createElement("span");
      badge.className = "looper-track-state";
      badge.textContent = stateLabels[t.state] || t.state;
      header.append(name, badge);

      const meta = document.createElement("div");
      meta.className = "looper-track-meta";
      const parts = [];
      if (t.event_count) parts.push(t.event_count === 1 ? "1 toque" : `${t.event_count} toques`);
      if (t.overdub_event_count) parts.push(`+${t.overdub_event_count} na sobreposição`);
      meta.textContent = parts.join(" · ") || "—";

      const actions = document.createElement("div");
      actions.className = "looper-track-actions";

      const muteBtn = document.createElement("button");
      muteBtn.type = "button";
      muteBtn.className = "secondary-btn looper-mute-btn";
      muteBtn.classList.toggle("active", !!t.muted);
      muteBtn.textContent = t.muted ? "Mudo" : "Som";

      const volume = document.createElement("input");
      volume.type = "range";
      volume.className = "looper-volume";
      volume.min = "0";
      volume.max = "100";
      volume.value = String(t.volume == null ? 100 : t.volume);
      volume.title = `Volume da T${i + 1} (${t.volume == null ? 100 : t.volume}%)`;

      const clearBtn = document.createElement("button");
      clearBtn.type = "button";
      clearBtn.className = "secondary-btn looper-track-clear-btn";
      clearBtn.textContent = "Limpar";
      clearBtn.disabled = t.state === "stopped" && !t.event_count && !t.overdub_event_count;

      actions.append(muteBtn, volume, clearBtn);
      card.append(header, meta, actions);
      el.looperTrackList.append(card);
    });
  }

  function updateLooperElapsed() {
    const s = state.looper;
    if (!s.started_at) {
      el.looperElapsed.textContent = "";
      return;
    }
    const elapsed = Date.now() / 1000 - s.started_at;
    if (s.state === "recording") {
      el.looperElapsed.textContent = `${elapsed.toFixed(1)}s`;
    } else if ((s.state === "playing" || s.state === "overdubbing") && s.loop_duration) {
      const pos = ((elapsed % s.loop_duration) + s.loop_duration) % s.loop_duration;
      const selected = (s.tracks || [])[s.selected_track || 0];
      const suffix = s.state === "overdubbing" && selected ? ` · ${selected.overdub_event_count || 0} toques novos` : "";
      el.looperElapsed.textContent = `${pos.toFixed(1)}s / ${s.loop_duration.toFixed(1)}s${suffix}`;
      el.looperProgressFill.style.width = `${(pos / s.loop_duration) * 100}%`;
    }
  }

  el.looperRecordBtn.addEventListener("click", () => {
    const endpoint = state.looper.state === "recording" ? "/api/looper/record/stop" : "/api/looper/record/start";
    fetch(endpoint, { method: "POST" });
  });
  el.looperOverdubBtn.addEventListener("click", () => {
    const endpoint = state.looper.state === "overdubbing" ? "/api/looper/overdub/stop" : "/api/looper/overdub/start";
    fetch(endpoint, { method: "POST" });
  });
  el.looperPlayBtn.addEventListener("click", () => fetch("/api/looper/play", { method: "POST" }));
  el.looperStopBtn.addEventListener("click", () => fetch("/api/looper/stop", { method: "POST" }));
  el.looperClearBtn.addEventListener("click", () => fetch("/api/looper/clear", { method: "POST" }));

  el.looperTrackList.addEventListener("click", (ev) => {
    const card = ev.target.closest(".looper-track");
    if (!card) return;
    const trackIndex = Number(card.dataset.track);
    if (ev.target.closest(".looper-mute-btn")) {
      fetch(`/api/looper/tracks/${trackIndex}/mute`, { method: "POST" });
    } else if (ev.target.closest(".looper-track-clear-btn")) {
      fetch(`/api/looper/tracks/${trackIndex}/clear`, { method: "POST" });
    } else if (!ev.target.closest(".looper-volume")) {
      fetch(`/api/looper/tracks/${trackIndex}/select`, { method: "POST" });
    }
  });

  el.looperTrackList.addEventListener("change", (ev) => {
    if (!ev.target.classList.contains("looper-volume")) return;
    const card = ev.target.closest(".looper-track");
    fetch(`/api/looper/tracks/${Number(card.dataset.track)}/volume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ volume: Number(ev.target.value) }),
    });
  });

  // --- Knobs -----------------------------------------------------------

  function renderKnobs() {
    el.knobsList.innerHTML = "";
    el.knobClearAllBtn.disabled = state.knobs.length === 0;
    if (state.knobs.length === 0) {
      el.knobsList.innerHTML = `<li class="sound-item"><span class="sound-name">Nenhum knob atribuído ainda.</span></li>`;
      return;
    }
    for (const k of state.knobs) {
      const li = document.createElement("li");
      li.className = "sound-item";
      li.innerHTML = `
        <span class="sound-name">CC${k.cc_number} → ${escapeHtml(k.label)}</span>
        <button class="icon-btn delete-btn" title="Remover">🗑</button>
      `;
      li.querySelector(".delete-btn").addEventListener("click", () => fetch(`/api/knobs/${k.cc_number}`, { method: "DELETE" }));
      el.knobsList.appendChild(li);
    }
  }

  el.knobClearAllBtn.addEventListener("click", () => fetch("/api/knobs", { method: "DELETE" }));

  el.knobAddBtn.addEventListener("click", openKnobStepTarget);

  function openKnobStepTarget() {
    knobPicker.step = "target";
    el.knobModalTitle.textContent = "Qual pad (ou Global)?";
    el.knobModalWaiting.classList.add("hidden");
    el.knobModalList.classList.remove("hidden");
    el.knobModalList.innerHTML = "";

    const globalLi = document.createElement("li");
    globalLi.className = "sound-item";
    globalLi.innerHTML = `<span class="sound-name">Global</span>`;
    globalLi.querySelector(".sound-name").addEventListener("click", () => openKnobStepParam("global", null));
    el.knobModalList.appendChild(globalLi);

    for (let n = 1; n <= 16; n++) {
      const pad = state.pads.find((p) => p.pad_number === n);
      const li = document.createElement("li");
      li.className = "sound-item";
      li.innerHTML = `<span class="sound-name">Pad ${n}${pad && pad.has_sample ? " — " + escapeHtml(pad.display_name) : ""}</span>`;
      li.querySelector(".sound-name").addEventListener("click", () => openKnobStepParam("pad", n));
      el.knobModalList.appendChild(li);
    }
    el.knobModal.classList.remove("hidden");
  }

  function openKnobStepParam(scope, padNumber) {
    knobPicker.step = "param";
    knobPicker.scope = scope;
    knobPicker.padNumber = padNumber;
    el.knobModalTitle.textContent = scope === "global" ? "O que controlar (Global)?" : `O que controlar (Pad ${padNumber})?`;
    el.knobModalList.innerHTML = "";
    const params = scope === "global" ? state.knobTargets.global : state.knobTargets.pads[String(padNumber)] || [];
    if (params.length === 0) {
      el.knobModalList.innerHTML = `<li class="sound-item"><span class="sound-name">Nenhum parâmetro disponível (adicione um efeito a esse pad primeiro).</span></li>`;
      return;
    }
    for (const p of params) {
      const li = document.createElement("li");
      li.className = "sound-item";
      li.innerHTML = `<span class="sound-name">${escapeHtml(p.label)}</span>`;
      li.querySelector(".sound-name").addEventListener("click", () => startKnobCapture(scope, padNumber, p.param));
      el.knobModalList.appendChild(li);
    }
  }

  async function startKnobCapture(scope, padNumber, param) {
    knobPicker.step = "capture";
    el.knobModalTitle.textContent = "Gire o knob";
    el.knobModalList.classList.add("hidden");
    el.knobModalWaiting.classList.remove("hidden");
    await fetch("/api/knobs/learn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope, pad_number: padNumber, param }),
    });
  }

  async function refreshKnobTargets() {
    const response = await fetch("/api/knobs/targets");
    if (response.ok) state.knobTargets = await response.json();
  }

  function closeKnobModal() {
    const wasCapturing = knobPicker.step === "capture";
    el.knobModal.classList.add("hidden");
    knobPicker.step = null;
    if (wasCapturing && state.pendingLearn) fetch("/api/knobs/learn/cancel", { method: "POST" });
  }
  el.knobModalClose.addEventListener("click", closeKnobModal);
  el.knobModal.addEventListener("click", (e) => {
    if (e.target === el.knobModal) closeKnobModal();
  });

  // --- Config ---------------------------------------------------------

  function renderSettings() {
    el.settingSustain.checked = state.settings.sustain_mode === "1";
    el.settingVelocity.checked = state.settings.velocity_sensitive === "1";
    el.settingKitBrowsePreview.checked = state.settings.kit_browse_preview_enabled === "1";
    el.settingLooperQuantize.checked = state.settings.looper_quantize_enabled === "1";
    const duplicateHitWindow = Number(state.settings.looper_duplicate_hit_window_ms || 30);
    el.settingLooperDuplicateHitWindow.value = duplicateHitWindow;
    el.settingLooperDuplicateHitWindowValue.textContent = `${duplicateHitWindow} ms`;
  }

  el.settingSustain.addEventListener("change", () => {
    fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sustain_mode: el.settingSustain.checked }),
    });
  });
  el.settingVelocity.addEventListener("change", () => {
    fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ velocity_sensitive: el.settingVelocity.checked }),
    });
  });
  // Plain persistent toggle, not a MIDI-learned controller action - lets a
  // performance turn off the auto-preview sound while browsing kits and
  // forget about it (see the kit_browse_toggle row further down for the
  // physical button that opens the navigation itself).
  el.settingKitBrowsePreview.addEventListener("change", () => {
    fetch("/api/settings/kit_browse_preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: el.settingKitBrowsePreview.checked }),
    });
  });
  // Plain persistent toggle - snaps the Looper's recorded length to the
  // nearest bar on stop instead of using the raw hold time (see
  // engine/looper.py's record_stop/_quantize_to_bar).
  el.settingLooperQuantize.addEventListener("change", () => {
    fetch("/api/settings/looper_quantize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: el.settingLooperQuantize.checked }),
    });
  });
  el.settingLooperDuplicateHitWindow.addEventListener("input", () => {
    el.settingLooperDuplicateHitWindowValue.textContent = `${el.settingLooperDuplicateHitWindow.value} ms`;
  });
  el.settingLooperDuplicateHitWindow.addEventListener("change", () => {
    fetch("/api/settings/looper_duplicate_hit_window", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ milliseconds: Number(el.settingLooperDuplicateHitWindow.value) }),
    });
  });

  // Single registry for every dedicated controller action, rendered (with the
  // pads' notes and the knob CCs) in the Config tab's unified MIDI map. The
  // old design kept one static HTML row, two el.* bindings and a
  // CONTROLLER_ACTION_ELS entry per action - three places to edit by hand
  // for every new action; here it's one.
  const MIDI_ACTIONS = [
    { action: "looper_record_toggle", label: "Gravar (Looper)", desc: "Abre a aba Looper e inicia/para a gravação da trilha armada.", category: "looper" },
    { action: "looper_play_toggle", label: "Play (Looper)", desc: "Toca/para o ciclo do looper sem apagar nada — \"Limpar\" apaga.", category: "looper" },
    { action: "looper_overdub_toggle", label: "Sobrepor (Looper)", desc: "Entra/sai da sobreposição sobre a trilha armada que já está tocando.", category: "looper" },
    { action: "scene_next", label: "Próxima cena", desc: "Avança a cena da aba Performance em segundo plano, sem trocar de aba. Aceita vários botões.", category: "scenes" },
    { action: "scene_prev", label: "Cena anterior", desc: "Volta a cena da aba Performance em segundo plano. Aceita vários botões.", category: "scenes" },
    { action: "panic", label: "Panic", desc: "Silencia tudo, para transports e muta o master, igual ao botão PANIC.", category: "system" },
    { action: "tap_tempo", label: "Tap do metrônomo", desc: "Bata no botão físico no tempo da música para ajustar o BPM global.", category: "system" },
    { action: "kit_browse_toggle", label: "Navegar kits", desc: "Abre a navegação de kits: escolha o pad a editar, troque kit/som nele e confirme.", category: "kit_browse" },
    { action: "kit_browse_up", label: "Navegar kits: kit anterior", desc: "Opcional — padrão já é o canto superior-esquerdo do grid (pad 13).", category: "kit_browse" },
    { action: "kit_browse_down", label: "Navegar kits: próximo kit", desc: "Opcional — padrão é o canto superior-direito (pad 16).", category: "kit_browse" },
    { action: "kit_browse_left", label: "Navegar kits: som anterior", desc: "Opcional — padrão é o topo, segunda posição (pad 14).", category: "kit_browse" },
    { action: "kit_browse_right", label: "Navegar kits: próximo som", desc: "Opcional — padrão é o topo, terceira posição (pad 15).", category: "kit_browse" },
    { action: "kit_browse_confirm", label: "Navegar kits: confirmar", desc: "Opcional — padrão é o canto inferior-direito (pad 4).", category: "kit_browse" },
    { action: "kit_browse_back", label: "Navegar kits: voltar", desc: "Opcional — padrão é o canto inferior-esquerdo (pad 1).", category: "kit_browse" },
  ];

  const MIDI_SECTIONS = [
    { key: "looper", title: "Looper" },
    { key: "scenes", title: "Cenas" },
    { key: "system", title: "Sistema" },
    { key: "kit_browse", title: "Navegação de kits (opcional)" },
    { key: "pads", title: "Pads — notas" },
    { key: "knobs", title: "Knobs — CCs" },
  ];

  // In-memory only: the kit-browse group starts collapsed so the optional
  // navigation buttons don't push Panic/Looper/Scenes below the fold.
  const midiSectionCollapsed = { kit_browse: true };
  let midiLearnCancelHandler = null;

  // Same octave math as the topbar's live MIDI indicator (showMidiNote).
  function midiNoteChipLabel(number) {
    return `Nota ${number} · Oitava ${Math.floor(number / 12) - 2}`;
  }

  function midiFilterText() {
    return el.midiMapFilter.value.trim().toLowerCase();
  }

  function renderMidiMap() {
    const filter = midiFilterText();
    el.midiMapSections.innerHTML = "";
    for (const section of MIDI_SECTIONS) {
      const cards = midiSectionCards(section.key, filter);
      if (filter && cards.length === 0) continue; // a filter hides empty sections
      el.midiMapSections.appendChild(midiSectionEl(section, cards, filter));
    }
    updateMidiLearnBanner();
  }

  function midiSectionCards(key, filter) {
    if (key === "pads") return state.pads.map((pad) => midiPadCard(pad, filter)).filter(Boolean);
    if (key === "knobs") return state.knobs.map((k) => midiKnobCard(k, filter)).filter(Boolean);
    return MIDI_ACTIONS.filter((a) => a.category === key)
      .map((a) => midiActionCard(a, filter))
      .filter(Boolean);
  }

  function midiSectionEl(section, cards, filter) {
    const wrap = document.createElement("section");
    wrap.className = "midi-map-section";
    // While filtering, collapse is suspended: matches must stay visible.
    wrap.classList.toggle("collapsed", !filter && !!midiSectionCollapsed[section.key]);

    const header = document.createElement("button");
    header.type = "button";
    header.className = "midi-map-section-header";
    const title = document.createElement("span");
    title.textContent = section.title;
    const count = document.createElement("span");
    count.className = "midi-map-section-count";
    count.textContent = `${cards.length}`;
    const chevron = document.createElement("span");
    chevron.className = "midi-map-chevron";
    chevron.textContent = "▾";
    header.append(title, count, chevron);
    header.addEventListener("click", () => {
      midiSectionCollapsed[section.key] = !midiSectionCollapsed[section.key];
      renderMidiMap();
    });

    const grid = document.createElement("div");
    grid.className = "midi-map-cards";
    for (const card of cards) grid.appendChild(card);

    wrap.append(header, grid);
    return wrap;
  }

  function midiActionCard(entry, filter) {
    const bindings = state.controllerActions.bindings[entry.action] || [];
    const searchable = [entry.label, entry.action, ...bindings.map((b) => `${b.midi_type} ${b.number}`)]
      .join(" ")
      .toLowerCase();
    if (filter && !searchable.includes(filter)) return null;

    const card = document.createElement("div");
    card.className = "midi-card";
    const waiting = state.controllerActions.pending_learn === entry.action;
    if (waiting) card.classList.add("waiting");

    const title = document.createElement("div");
    title.className = "midi-card-title";
    title.textContent = entry.label;

    const desc = document.createElement("div");
    desc.className = "midi-card-desc";
    desc.textContent = entry.desc;

    const chips = document.createElement("div");
    chips.className = "controller-bindings-chips";
    if (!bindings.length) {
      chips.innerHTML = `<span class="controller-binding-empty">Nenhum controle vinculado.</span>`;
    } else {
      for (const b of bindings) {
        const chip = document.createElement("span");
        chip.className = "controller-binding-chip";
        chip.innerHTML = `${b.midi_type === "note" ? midiNoteChipLabel(b.number) : `CC ${b.number}`} <button class="icon-btn delete-btn" title="Remover">✕</button>`;
        chip.querySelector(".delete-btn").addEventListener("click", () => {
          fetch(`/api/controller-actions/bindings/${b.id}`, { method: "DELETE" });
        });
        chips.appendChild(chip);
      }
    }

    const actions = document.createElement("div");
    actions.className = "midi-card-actions";
    const learnBtn = document.createElement("button");
    learnBtn.type = "button";
    learnBtn.className = "secondary-btn";
    learnBtn.classList.toggle("waiting", waiting);
    learnBtn.textContent = waiting ? "Aperte o botão..." : "Aprender";
    learnBtn.addEventListener("click", () => {
      const endpoint = waiting
        ? "/api/controller-actions/learn/cancel"
        : `/api/controller-actions/${entry.action}/learn`;
      fetch(endpoint, { method: "POST" });
    });
    actions.appendChild(learnBtn);
    if (bindings.length) {
      const clearBtn = document.createElement("button");
      clearBtn.type = "button";
      clearBtn.className = "secondary-btn";
      clearBtn.textContent = "Limpar";
      clearBtn.addEventListener("click", () => {
        for (const b of bindings) fetch(`/api/controller-actions/bindings/${b.id}`, { method: "DELETE" });
      });
      actions.appendChild(clearBtn);
    }

    card.append(title, desc, chips, actions);
    return card;
  }

  function midiPadCard(pad, filter) {
    const searchable = `pad ${pad.pad_number} ${pad.display_name || ""} nota ${pad.midi_note ?? ""}`
      .trim()
      .toLowerCase();
    if (filter && !searchable.includes(filter)) return null;

    const card = document.createElement("div");
    card.className = "midi-card";
    const waiting = state.pendingNoteLearn === pad.pad_number;
    if (waiting) card.classList.add("waiting");

    const title = document.createElement("div");
    title.className = "midi-card-title";
    title.textContent = `Pad ${pad.pad_number}${pad.display_name ? ` · ${pad.display_name}` : ""}`;

    const desc = document.createElement("div");
    desc.className = "midi-card-desc";
    desc.textContent = "Nota MIDI que dispara este pad.";

    const chips = document.createElement("div");
    chips.className = "controller-bindings-chips";
    chips.innerHTML = pad.midi_note == null
      ? `<span class="controller-binding-empty">Sem nota aprendida.</span>`
      : `<span class="controller-binding-chip">${midiNoteChipLabel(pad.midi_note)}</span>`;

    const actions = document.createElement("div");
    actions.className = "midi-card-actions";
    const learnBtn = document.createElement("button");
    learnBtn.type = "button";
    learnBtn.className = "secondary-btn";
    learnBtn.classList.toggle("waiting", waiting);
    learnBtn.textContent = waiting ? "Bata o pad..." : "Aprender";
    learnBtn.addEventListener("click", () => {
      const endpoint = waiting
        ? `/api/pads/${pad.pad_number}/note/learn/cancel`
        : `/api/pads/${pad.pad_number}/note/learn`;
      fetch(endpoint, { method: "POST" });
    });
    actions.appendChild(learnBtn);

    card.append(title, desc, chips, actions);
    return card;
  }

  function midiKnobCard(k, filter) {
    const searchable = `cc ${k.cc_number} ${k.label}`.toLowerCase();
    if (filter && !searchable.includes(filter)) return null;

    const card = document.createElement("div");
    card.className = "midi-card";

    const title = document.createElement("div");
    title.className = "midi-card-title";
    title.textContent = `CC ${k.cc_number}`;

    const desc = document.createElement("div");
    desc.className = "midi-card-desc";
    desc.textContent = k.label;

    const actions = document.createElement("div");
    actions.className = "midi-card-actions";
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "secondary-btn";
    removeBtn.textContent = "Remover";
    removeBtn.addEventListener("click", () => {
      fetch(`/api/knobs/${k.cc_number}`, { method: "DELETE" });
    });
    actions.appendChild(removeBtn);

    card.append(title, desc, actions);
    return card;
  }

  // One banner for the three learn flows (controller action, pad note, knob
  // capture) so any pending assignment is always visible with a cancel at
  // hand, no matter which section started it.
  function updateMidiLearnBanner() {
    const pendingAction = state.controllerActions.pending_learn;
    const pendingPad = state.pendingNoteLearn;
    let text = null;
    let cancel = null;
    if (pendingAction) {
      const entry = MIDI_ACTIONS.find((a) => a.action === pendingAction);
      text = `Aperte o botão/pad físico para vincular: ${entry ? entry.label : pendingAction}`;
      cancel = () => fetch("/api/controller-actions/learn/cancel", { method: "POST" });
    } else if (pendingPad != null) {
      text = `Bata no pad físico para aprender a nota do Pad ${pendingPad}`;
      cancel = () => fetch(`/api/pads/${pendingPad}/note/learn/cancel`, { method: "POST" });
    } else if (state.pendingLearn && knobPicker.step === "capture") {
      text = "Gire o knob físico para concluir a atribuição";
      cancel = () => closeKnobModal();
    }
    midiLearnCancelHandler = cancel;
    el.midiLearnBanner.classList.toggle("hidden", !text);
    if (text) el.midiLearnBannerText.textContent = text;
  }

  el.midiLearnCancelBtn.addEventListener("click", () => {
    if (midiLearnCancelHandler) midiLearnCancelHandler();
  });
  el.midiMapFilter.addEventListener("input", renderMidiMap);
  // Reuses the Knobs tab's two-step picker modal (target -> param -> capture).
  el.midiMapAddKnobBtn.addEventListener("click", openKnobStepTarget);

  function playPreview(soundId) {
    el.previewAudio.src = `/api/sounds/${soundId}/audio`;
    el.previewAudio.play().catch(() => {});
  }

  async function deleteSound(soundId) {
    const res = await fetch(`/api/sounds/${soundId}`, { method: "DELETE" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      alert(body.detail || "Não foi possível remover este som.");
    }
  }

  async function deleteSelectedSounds() {
    const selectedIds = [...state.selectedSoundIds];
    if (!selectedIds.length) return;
    if (!window.confirm(`Excluir ${selectedIds.length} ${selectedIds.length === 1 ? "som selecionado" : "sons selecionados"}?`)) return;

    const results = await Promise.all(selectedIds.map(async (soundId) => {
      const res = await fetch(`/api/sounds/${soundId}`, { method: "DELETE" });
      return { soundId, ok: res.ok };
    }));
    for (const { soundId, ok } of results) {
      if (ok) state.selectedSoundIds.delete(soundId);
    }
    const failedCount = results.filter(result => !result.ok).length;
    if (failedCount) alert(`${failedCount} ${failedCount === 1 ? "som nao foi removido" : "sons nao foram removidos"}.`);
  }

  el.selectAllSounds.addEventListener("change", () => {
    const inUse = new Set(state.pads.filter(p => p.sample_id).map(p => p.sample_id));
    toggleSoundSelection(selectableSoundIdsInFolder(currentSoundFolder(), inUse), el.selectAllSounds.checked);
    renderSoundList();
  });
  el.deleteSelectedSounds.addEventListener("click", deleteSelectedSounds);

  function openAssignModal(padNumber) {
    state.selectedPad = padNumber;
    const pad = state.pads.find(p => p.pad_number === padNumber);
    el.modalTitle.textContent = `Pad ${padNumber} — escolher som`;
    el.modalSearch.value = "";
    el.modalClear.disabled = !pad || !pad.sample_id;
    state.modalSoundPath = [];
    loadModalSoundBrowser();
    renderNoteLearnButton();
    el.modal.classList.remove("hidden");
    el.modalSearch.focus();
  }

  function closeModal() {
    if (state.selectedPad !== null && state.pendingNoteLearn === state.selectedPad) {
      fetch(`/api/pads/${state.selectedPad}/note/learn/cancel`, { method: "POST" });
    }
    el.modal.classList.add("hidden");
    state.selectedPad = null;
  }
  el.modalClose.addEventListener("click", closeModal);
  el.modal.addEventListener("click", (e) => {
    if (e.target === el.modal) closeModal();
  });

  function renderNoteLearnButton() {
    const pad = state.pads.find((p) => p.pad_number === state.selectedPad);
    el.modalNoteValue.textContent = pad ? pad.midi_note : "--";
    const waiting = state.selectedPad !== null && state.pendingNoteLearn === state.selectedPad;
    el.modalLearnNoteBtn.textContent = waiting ? "Bata o pad..." : "Aprender nota";
    el.modalLearnNoteBtn.classList.toggle("waiting", waiting);
  }

  el.modalLearnNoteBtn.addEventListener("click", async () => {
    if (state.selectedPad === null) return;
    const waiting = state.pendingNoteLearn === state.selectedPad;
    const endpoint = waiting ? "learn/cancel" : "learn";
    await fetch(`/api/pads/${state.selectedPad}/note/${endpoint}`, { method: "POST" });
  });

  function renderModalSoundList(filterText) {
    el.modalSoundList.innerHTML = "";
    const query = filterText.trim().toLowerCase();

    if (query) {
      // Search mode: ignore the current folder and look across everything,
      // matching the folder path too (so typing a folder name works) -
      // each hit keeps its folder hint since results can span folders.
      el.modalBreadcrumb.innerHTML = "";
      const filtered = state.sounds.filter(s =>
        s.display_name.toLowerCase().includes(query) || s.folder.toLowerCase().includes(query)
      );
      if (filtered.length === 0) {
        el.modalSoundList.innerHTML = `<li class="sound-item"><span class="sound-name">Nenhum som encontrado.</span></li>`;
        return;
      }
      for (const sound of filtered) {
        const li = document.createElement("li");
        li.className = "sound-item";
        li.innerHTML = `
          <button class="icon-btn play-btn" title="Ouvir">▶</button>
          <span class="sound-name">
            ${escapeHtml(sound.display_name)}
            ${sound.folder ? `<span class="sound-folder-hint">${escapeHtml(sound.folder)}</span>` : ""}
          </span>
        `;
        li.querySelector(".play-btn").addEventListener("click", (e) => {
          e.stopPropagation();
          playPreview(sound.id);
        });
        li.querySelector(".sound-name").addEventListener("click", () => assignSound(sound.id));
        el.modalSoundList.appendChild(li);
      }
      return;
    }

    // Browse mode: navigate the same folder tree as the Sons tab, one
    // level at a time, via the modal's own path (state.modalSoundPath).
    renderModalBreadcrumb();
    const { folders, samples } = state.modalSoundView;
    if (folders.length === 0 && samples.length === 0) {
      el.modalSoundList.innerHTML = `<li class="sound-item"><span class="sound-name">Pasta vazia.</span></li>`;
      return;
    }

    for (const name of folders) {
      const li = document.createElement("li");
      li.className = "sound-item folder-item";
      li.innerHTML = `<span class="sound-name">📁 ${escapeHtml(name)}</span>`;
      li.addEventListener("click", () => navigateModalToFolder([...state.modalSoundPath, name]));
      el.modalSoundList.appendChild(li);
    }

    for (const sound of samples) {
      const li = document.createElement("li");
      li.className = "sound-item";
      li.innerHTML = `
        <button class="icon-btn play-btn" title="Ouvir">▶</button>
        <span class="sound-name">${escapeHtml(sound.display_name)}</span>
      `;
      li.querySelector(".play-btn").addEventListener("click", (e) => {
        e.stopPropagation();
        playPreview(sound.id);
      });
      li.querySelector(".sound-name").addEventListener("click", () => assignSound(sound.id));
      el.modalSoundList.appendChild(li);
    }
  }

  el.modalSearch.addEventListener("input", () => renderModalSoundList(el.modalSearch.value));

  async function assignSound(sampleId) {
    if (state.selectedPad === null) return;
    await fetch(`/api/pads/${state.selectedPad}/assign`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sample_id: sampleId }),
    });
    closeModal();
  }

  el.modalClear.addEventListener("click", async () => {
    if (state.selectedPad === null) return;
    await fetch(`/api/pads/${state.selectedPad}/assign`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sample_id: null }),
    });
    closeModal();
  });

  function updateUploadProgress(loaded, total, fileCount) {
    const percent = total ? Math.min(100, Math.round((loaded / total) * 100)) : 100;
    el.uploadProgressLabel.textContent = `Enviando ${fileCount} ${fileCount === 1 ? "arquivo" : "arquivos"}...`;
    el.uploadProgressPercent.textContent = `${percent}%`;
    el.uploadProgressBar.style.width = `${percent}%`;
  }

  function uploadOne(file, folder, onProgress) {
    return new Promise((resolve) => {
      const form = new FormData();
      form.append("file", file);
      form.append("folder", folder);

      const request = new XMLHttpRequest();
      request.open("POST", "/api/sounds/upload");
      request.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) onProgress(event.loaded);
      });
      request.addEventListener("load", () => {
        onProgress(file.size);
        if (request.status < 200 || request.status >= 300) {
          let detail = request.status;
          try {
            detail = JSON.parse(request.responseText).detail || detail;
          } catch (_) {}
          alert(`Falha ao enviar ${file.name}: ${detail}`);
        }
        resolve();
      });
      request.addEventListener("error", () => {
        onProgress(file.size);
        alert(`Falha ao enviar ${file.name}: erro de rede`);
        resolve();
      });
      request.send(form);
    });
  }

  function isSupportedAudioFile(file) {
    const dot = file.name.lastIndexOf(".");
    return dot >= 0 && SUPPORTED_AUDIO_EXTENSIONS.has(file.name.slice(dot).toLowerCase());
  }

  async function uploadFiles(files, folderForFile = () => currentSoundFolder()) {
    // Uploaded with bounded concurrency (see UPLOAD_CONCURRENCY) rather
    // than one-by-one, so a batch still feels reasonably immediate without
    // overwhelming the connection.
    const audioFiles = Array.from(files).filter(isSupportedAudioFile);
    return uploadEntries(audioFiles.map((file) => ({ file, folder: folderForFile(file) })));
  }

  async function uploadEntries(entries) {
    if (!entries.length) return;
    const totalBytes = entries.reduce((sum, { file }) => sum + file.size, 0);
    const uploadedBytes = new Array(entries.length).fill(0);
    el.uploadProgress.classList.remove("hidden");
    updateUploadProgress(0, totalBytes, entries.length);

    // Bounded-concurrency pool: at most UPLOAD_CONCURRENCY requests in
    // flight, the rest wait their turn - see UPLOAD_CONCURRENCY above for
    // why unbounded Promise.all isn't safe for a big batch.
    let nextIndex = 0;
    async function worker() {
      while (nextIndex < entries.length) {
        const index = nextIndex++;
        const { file, folder } = entries[index];
        await uploadOne(file, folder, (loaded) => {
          uploadedBytes[index] = loaded;
          updateUploadProgress(uploadedBytes.reduce((sum, value) => sum + value, 0), totalBytes, entries.length);
        });
      }
    }
    await Promise.all(Array.from({ length: Math.min(UPLOAD_CONCURRENCY, entries.length) }, worker));

    await loadSoundBrowser();
    setTimeout(() => el.uploadProgress.classList.add("hidden"), 450);
  }

  function folderForSelectedFile(file) {
    const relativeParts = (file.webkitRelativePath || "").split("/").filter(Boolean);
    const baseParts = currentSoundFolder().split("/").filter(Boolean);
    return [...baseParts, ...relativeParts.slice(0, -1)].join("/");
  }

  function entryFile(entry) {
    return new Promise((resolve, reject) => entry.file(resolve, reject));
  }

  function readDirectory(reader) {
    return new Promise((resolve, reject) => reader.readEntries(resolve, reject));
  }

  async function collectDroppedFiles(entry, path, uploads) {
    if (entry.isFile) {
      const file = await entryFile(entry);
      if (isSupportedAudioFile(file)) uploads.push({ file, folder: [...path].join("/") });
      return;
    }
    const reader = entry.createReader();
    let entries;
    do {
      entries = await readDirectory(reader);
      await Promise.all(entries.map((child) => collectDroppedFiles(child, [...path, entry.name], uploads)));
    } while (entries.length);
  }

  async function uploadDroppedItems(items) {
    const entries = Array.from(items)
      .map((item) => item.webkitGetAsEntry?.())
      .filter(Boolean);
    if (!entries.length) return uploadFiles(Array.from(items).map((item) => item.getAsFile()).filter(Boolean));

    const uploads = [];
    await Promise.all(entries.map((entry) => collectDroppedFiles(entry, [], uploads)));
    const baseParts = currentSoundFolder().split("/").filter(Boolean);
    return uploadEntries(
      uploads.map(({ file, folder }) => ({ file, folder: [...baseParts, folder].filter(Boolean).join("/") }))
    );
  }

  el.fileInput.addEventListener("change", () => {
    if (el.fileInput.files.length) {
      uploadFiles(el.fileInput.files);
      el.fileInput.value = "";
    }
  });
  el.folderInput.addEventListener("change", () => {
    if (el.folderInput.files.length) {
      uploadFiles(el.folderInput.files, folderForSelectedFile);
      el.folderInput.value = "";
    }
  });

  ["dragenter", "dragover"].forEach(evt =>
    el.uploadZone.addEventListener(evt, (e) => {
      e.preventDefault();
      el.uploadZone.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach(evt =>
    el.uploadZone.addEventListener(evt, (e) => {
      e.preventDefault();
      el.uploadZone.classList.remove("dragover");
    })
  );
  el.uploadZone.addEventListener("drop", (e) => {
    if (e.dataTransfer.items.length) uploadDroppedItems(e.dataTransfer.items);
    else if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
  });

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str ?? "";
    return div.innerHTML;
  }

  function connectWebSocket() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.addEventListener("open", () => el.connStatus.classList.add("connected"));
    ws.addEventListener("close", () => {
      el.connStatus.classList.remove("connected");
      setTimeout(connectWebSocket, 2000);
    });
    ws.addEventListener("error", () => ws.close());
    ws.addEventListener("message", (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === "pads") {
        state.pads = msg.pads;
        renderPads();
        renderSoundList();
        renderVolumes();
        renderEffects();
        renderMidiMap();
        if (!el.modal.classList.contains("hidden")) renderNoteLearnButton();
      } else if (msg.type === "note_learn") {
        state.pendingNoteLearn = msg.pending_pad;
        renderMidiMap();
        if (!el.modal.classList.contains("hidden")) renderNoteLearnButton();
      } else if (msg.type === "pad_hit") {
        flashPadHit(msg.pad_number);
      } else if (msg.type === "midi_note") {
        showMidiNote(msg.note);
      } else if (msg.type === "sounds") {
        state.sounds = msg.sounds;
        loadSoundBrowser();
      } else if (msg.type === "knobs") {
        state.knobs = msg.knobs;
        state.pendingLearn = msg.pending_learn;
        renderKnobs();
        renderEffects();
        renderMidiMap();
        if (knobPicker.step === "capture" && !state.pendingLearn) {
          el.knobModal.classList.add("hidden");
          knobPicker.step = null;
        }
      } else if (msg.type === "settings") {
        state.settings = msg.settings;
        renderSettings();
      } else if (msg.type === "pad_effects") {
        state.padEffects = msg.pad_effects;
        renderEffects();
        refreshKnobTargets();
      } else if (msg.type === "sequencer") {
        state.sequencer = { running: msg.running, current_step: msg.current_step, steps: msg.steps };
        renderSequencer();
      } else if (msg.type === "sequencer_tick") {
        state.sequencer.current_step = msg.current_step;
        updateSequencerPlayhead(msg.current_step);
      } else if (msg.type === "tempo") {
        state.tempo.bpm = msg.bpm;
        renderTempo();
        renderLooper(); // bar-count estimate depends on the current BPM
      } else if (msg.type === "master") {
        state.master = { available: msg.available, volume: msg.volume, muted: msg.muted };
        renderMaster();
      } else if (msg.type === "engine_status") {
        state.engineStatus = msg;
        if (msg.master) state.master = msg.master;
        renderMaster();
        renderEngineStatus();
      } else if (msg.type === "metronome") {
        state.metronome = {
          running: msg.running,
          beat_in_bar: msg.beat_in_bar,
          signature: msg.signature,
          style: msg.style,
        };
        renderMetronome();
        renderLooper(); // bar-count estimate depends on the current signature
      } else if (msg.type === "metronome_tick") {
        state.metronome.beat_in_bar = msg.beat_in_bar;
        renderMetronomeBeat(msg.beat_in_bar);
      } else if (msg.type === "scenes") {
        state.scenes = msg.scenes;
        const act = activeScenes();
        const currentIdx = act.findIndex((s) => String(s.id) === String(msg.current_scene_id));
        if (currentIdx >= 0) state.sceneIndex = currentIdx;
        else if (state.sceneIndex >= act.length) state.sceneIndex = 0;
        renderSceneStrip();
        renderSceneList();
      } else if (msg.type === "scene_loading") {
        if (msg.active) showSceneLoading(msg.scene_name);
        else hideSceneLoading();
      } else if (msg.type === "patterns") {
        state.patterns = msg.patterns;
        if (state.patternIndex >= state.patterns.length) state.patternIndex = 0;
        renderPatternStrip();
      } else if (msg.type === "looper") {
        state.looper = {
          state: msg.state,
          selected_track: msg.selected_track,
          loop_duration: msg.loop_duration,
          started_at: msg.started_at,
          tracks: msg.tracks || [],
        };
        renderLooper();
      } else if (msg.type === "controller_actions") {
        state.controllerActions = { bindings: msg.bindings, pending_learn: msg.pending_learn };
        renderMidiMap();
      } else if (msg.type === "navigate") {
        switchView(msg.view);
      } else if (msg.type === "kit_browse") {
        state.kitBrowse = msg;
        renderKitBrowseBanner();
        renderKitBrowseModal();
        renderKitBrowseGridState();
      }
    });
  }

  function renderKitBrowseBanner() {
    const kb = state.kitBrowse;
    if (!kb || !kb.active) {
      el.kitBrowseBanner.classList.add("hidden");
      el.kitBrowseBanner.textContent = "";
      return;
    }
    el.kitBrowseBanner.classList.remove("hidden");
    if (kb.phase === "armed") {
      el.kitBrowseBanner.textContent = "Selecione o pad pra editar";
      return;
    }
    const soundLabel = kb.candidate_display_name || "nenhum";
    el.kitBrowseBanner.textContent =
      `Editando pad ${kb.target_pad} — kit ${kb.kit.name} (${kb.kit_index + 1}/${kb.kit_count}) · ` +
      `som: ${soundLabel} (${kb.sound_index === null ? "-" : kb.sound_index + 1}/${kb.sound_count})`;
  }

  // Big, read-from-across-the-room overlay with the same info as the header
  // banner - doesn't intercept clicks (pointer-events:none in CSS) so the
  // pad grid underneath stays tappable for the no-hardware dev/test path.
  function renderKitBrowseModal() {
    const kb = state.kitBrowse;
    // Stays hidden while armed (waiting for the target-pad tap) - the small
    // header banner already covers that moment; the big modal only earns
    // its place once there's an actual kit/sound to show.
    if (!kb || !kb.active || kb.phase !== "browsing") {
      el.kitBrowseModal.classList.add("hidden");
      return;
    }
    el.kitBrowseModal.classList.remove("hidden");
    el.kitBrowseModalCategories.innerHTML = "";
    el.kitBrowseModalKitList.innerHTML = "";
    el.kitBrowseModalSoundList.innerHTML = "";

    el.kitBrowseModalEyebrow.textContent = `Editando pad ${kb.target_pad}`;
    el.kitBrowseModalKit.textContent = kb.kit.name;

    const soundPosition = kb.sound_index === null ? "-" : kb.sound_index + 1;
    const counter = document.createElement("span");
    counter.className = "kit-browse-modal-pill current";
    counter.textContent = `Kit ${kb.kit_index + 1}/${kb.kit_count} · Som ${soundPosition}/${kb.sound_count}`;
    el.kitBrowseModalCategories.appendChild(counter);

    let currentKitItem = null;
    kb.kits.forEach((kit, idx) => {
      const li = document.createElement("li");
      const isCurrent = idx === kb.kit_index;
      li.className = "kit-browse-modal-list-item" + (isCurrent ? " current" : "");
      li.textContent = kit.name;
      el.kitBrowseModalKitList.appendChild(li);
      if (isCurrent) currentKitItem = li;
    });

    let currentSoundItem = null;
    if (kb.sounds.length === 0) {
      const li = document.createElement("li");
      li.className = "kit-browse-modal-list-empty";
      li.textContent = "Este kit não tem sons cadastrados.";
      el.kitBrowseModalSoundList.appendChild(li);
    }
    for (const sound of kb.sounds) {
      const li = document.createElement("li");
      const isCandidate = sound.pad_number === kb.candidate_pad_number;
      li.className = "kit-browse-modal-list-item" + (isCandidate ? " current" : "");
      li.innerHTML = `<span class="kit-browse-modal-sound-pad">#${sound.pad_number}</span> ${sound.display_name}`;
      el.kitBrowseModalSoundList.appendChild(li);
      if (isCandidate) currentSoundItem = li;
    }

    // Kit browsing is driven by physical controls, so keep both highlighted
    // entries visible without moving lists whose selection is already shown.
    if (currentKitItem) currentKitItem.scrollIntoView({ block: "nearest" });
    if (currentSoundItem) currentSoundItem.scrollIntoView({ block: "nearest" });
  }

  // Purely visual aid so the pad grid reflects kit-browse mode even when
  // nobody's hands are on the touchscreen (selection happens on the
  // physical controller): every pad pulses while "armed" (waiting for a tap
  // to pick the target), then the chosen target pad flashes once and keeps
  // a persistent highlight for the rest of the session.
  let kitBrowseLastTargetPad = null;
  function renderKitBrowseGridState() {
    const kb = state.kitBrowse;
    const active = !!(kb && kb.active);
    const armed = active && kb.phase === "armed";
    const targetPad = active && kb.phase === "browsing" ? kb.target_pad : null;

    for (const grid of [el.padGrid, el.perfPadGrid]) {
      for (const pad of grid.querySelectorAll(".pad")) {
        pad.classList.toggle("awaiting-target", armed);
        pad.classList.toggle(
          "kit-browse-target",
          targetPad !== null && Number(pad.dataset.padNumber) === targetPad
        );
      }
    }

    if (targetPad !== null && targetPad !== kitBrowseLastTargetPad) {
      flashPadHit(targetPad);
    }
    kitBrowseLastTargetPad = targetPad;
  }

  async function init() {
    const [padsRes, soundsRes, knobsRes, settingsRes, padEffectsRes, catalogRes, knobTargetsRes, sequencerRes, looperRes, tempoRes, metronomeRes, metronomeStylesRes, metronomeSignaturesRes, masterRes, engineStatusRes, scenesRes, patternsRes, controllerActionsRes] =
      await Promise.all([
        fetch("/api/pads"),
        fetch("/api/sounds"),
        fetch("/api/knobs"),
        fetch("/api/settings"),
        fetch("/api/pad-effects"),
        fetch("/api/effects/catalog"),
        fetch("/api/knobs/targets"),
        fetch("/api/sequencer"),
        fetch("/api/looper"),
        fetch("/api/tempo"),
        fetch("/api/metronome"),
        fetch("/api/metronome/styles"),
        fetch("/api/metronome/time-signatures"),
        fetch("/api/master"),
        fetch("/api/engine/status"),
        fetch("/api/scenes"),
        fetch("/api/patterns"),
        fetch("/api/controller-actions"),
      ]);
    state.pads = await padsRes.json();
    state.sounds = await soundsRes.json();
    const knobsData = await knobsRes.json();
    state.knobs = knobsData.knobs;
    state.pendingLearn = knobsData.pending_learn;
    state.settings = await settingsRes.json();
    state.padEffects = await padEffectsRes.json();
    state.effectsCatalog = await catalogRes.json();
    state.knobTargets = await knobTargetsRes.json();
    const sequencerData = await sequencerRes.json();
    state.sequencer = { running: sequencerData.running, current_step: sequencerData.current_step, steps: sequencerData.steps };
    state.looper = await looperRes.json();
    state.tempo = await tempoRes.json();
    state.metronome = await metronomeRes.json();
    state.metronomeStyles = await metronomeStylesRes.json();
    state.timeSignatures = await metronomeSignaturesRes.json();
    state.master = await masterRes.json();
    state.engineStatus = await engineStatusRes.json();
    state.scenes = await scenesRes.json();
    if (state.sceneIndex >= activeScenes().length) state.sceneIndex = 0;
    state.patterns = await patternsRes.json();
    if (state.patternIndex >= state.patterns.length) state.patternIndex = 0;
    state.controllerActions = await controllerActionsRes.json();

    renderPads();
    renderSceneStrip();
    renderSceneList();
    renderPatternStrip();
    renderMidiMap();
    await loadSoundBrowser();
    renderVolumes();
    renderSettings();
    renderEffects();
    renderSequencerHead();
    renderSequencer();
    renderTempo();
    renderMetronomeStyles();
    renderMetronomeSignatures();
    renderMetronome();
    renderMaster();
    renderEngineStatus();
    renderLooper();
    renderKnobs();
    connectWebSocket();
  }

  init();
})();
