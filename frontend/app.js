(() => {
  "use strict";

  const state = {
    pads: [],
    sounds: [],
    soundBrowserPath: [],
    soundBrowserView: { folders: [], samples: [] },
    selectedSoundIds: new Set(),
    knobs: [],
    knobTargets: { global: [], pads: {} },
    pendingLearn: null,
    pendingNoteLearn: null,
    selectedPad: null,
    settings: { sustain_mode: "1", velocity_sensitive: "0" },
    padEffects: [],
    effectsCatalog: [],
    sequencer: { running: false, current_step: 0, steps: [] },
    looper: { state: "stopped", loop_duration: null, event_count: 0, overdub_event_count: 0, started_at: null },
    tempo: { bpm: 100 },
    metronome: { running: false, beat_in_bar: 0, signature: "4_4", style: "digital" },
    metronomeStyles: [],
    timeSignatures: [],
    master: { available: false, volume: 100, muted: false },
    engineStatus: { jack: false, modhost: false, pads: {}, metronome: false, last_error: null },
    kits: [],
    kitIndex: 0,
    patterns: [],
    patternIndex: 0,
    controllerActions: { bindings: {}, pending_learn: null },
  };

  const knobPicker = { step: null, scope: null, padNumber: null };
  let looperTimer = null;
  let tapTimestamps = [];
  let panicTimer = null;
  let midiNoteHitTimer = null;

  const el = {
    padGrid: document.getElementById("pad-grid"),
    perfPadGrid: document.getElementById("perf-pad-grid"),
    kitPrev: document.getElementById("kit-prev"),
    kitNext: document.getElementById("kit-next"),
    kitName: document.getElementById("kit-name"),
    kitCount: document.getElementById("kit-count"),
    kitSave: document.getElementById("kit-save"),
    kitDelete: document.getElementById("kit-delete"),
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
    modal: document.getElementById("assign-modal"),
    modalTitle: document.getElementById("modal-pad-title"),
    modalClose: document.getElementById("modal-close"),
    modalSearch: document.getElementById("modal-search"),
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
    engineStatus: document.getElementById("engine-status"),
    engineStatusDetail: document.getElementById("engine-status-detail"),
    engineStatusText: document.getElementById("engine-status-text"),
    engineRestartBtn: document.getElementById("engine-restart-btn"),
    panicBtn: document.getElementById("panic-btn"),
    previewAudio: document.getElementById("preview-audio"),
    settingSustain: document.getElementById("setting-sustain"),
    settingVelocity: document.getElementById("setting-velocity"),
    controllerBindingsLooperRecordToggle: document.getElementById("controller-bindings-looper_record_toggle"),
    controllerLearnBtnLooperRecordToggle: document.getElementById("controller-learn-btn-looper_record_toggle"),
    controllerBindingsKitNext: document.getElementById("controller-bindings-kit_next"),
    controllerLearnBtnKitNext: document.getElementById("controller-learn-btn-kit_next"),
    sequencerPlayBtn: document.getElementById("sequencer-play-btn"),
    sequencerBpm: document.getElementById("sequencer-bpm"),
    sequencerClearBtn: document.getElementById("sequencer-clear-btn"),
    sequencerHeadRow: document.getElementById("sequencer-head-row"),
    sequencerBody: document.getElementById("sequencer-body"),
    looperStateLabel: document.getElementById("looper-state-label"),
    looperElapsed: document.getElementById("looper-elapsed"),
    looperRecordBtn: document.getElementById("looper-record-btn"),
    looperOverdubBtn: document.getElementById("looper-overdub-btn"),
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

  const VIEWS = ["pads", "performance", "sounds", "volumes", "effects", "sequencer", "metronome", "looper", "knobs", "config"];
  const SUPPORTED_AUDIO_EXTENSIONS = new Set([".wav", ".mp3", ".ogg", ".flac", ".aiff", ".aif"]);
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
  el.engineStatus.addEventListener("click", () => {
    const hidden = el.engineStatusDetail.classList.toggle("hidden");
    el.engineStatus.setAttribute("aria-expanded", String(!hidden));
  });
  el.engineRestartBtn.addEventListener("click", () => fetch("/api/engine/restart", { method: "POST" }));
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
  }

  // The hit animation comes from the server's pad_hit broadcast, so every
  // client (including this one) flashes exactly once per hit.
  async function triggerScreenPad(padNumber) {
    await fetch(`/api/pads/${padNumber}/trigger`, { method: "POST" });
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

  // Kits: named snapshots of the 16 pad assignments + effect chains, for
  // switching the whole set during a show. Kit switching re-applies every
  // pad in the engine, so the grid shows a busy state while it runs.

  async function loadKits() {
    const res = await fetch("/api/kits");
    state.kits = await res.json();
    if (state.kitIndex >= state.kits.length) state.kitIndex = 0;
    renderKitStrip();
  }

  function renderKitStrip() {
    const kit = state.kits[state.kitIndex];
    el.kitName.textContent = kit ? kit.name : "Nenhum kit salvo";
    el.kitCount.textContent = state.kits.length ? `${state.kitIndex + 1} / ${state.kits.length}` : "";
    el.kitPrev.disabled = state.kits.length < 2;
    el.kitNext.disabled = state.kits.length < 2;
    el.kitDelete.disabled = !kit;
  }

  async function loadKitAt(index) {
    if (!state.kits.length) return;
    state.kitIndex = (index + state.kits.length) % state.kits.length;
    renderKitStrip();
    const kit = state.kits[state.kitIndex];
    el.perfPadGrid.classList.add("applying");
    try {
      await fetch(`/api/kits/${kit.id}/load`, { method: "POST" });
    } finally {
      el.perfPadGrid.classList.remove("applying");
    }
  }

  async function saveCurrentKit() {
    const suggestion = state.kits[state.kitIndex] ? state.kits[state.kitIndex].name : "";
    const name = window.prompt("Nome do kit:", suggestion);
    if (name === null) return;
    const trimmed = name.trim();
    if (!trimmed) return;
    const res = await fetch("/api/kits", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: trimmed }),
    });
    if (!res.ok) {
      window.alert("Não foi possível salvar o kit.");
      return;
    }
    await loadKits();
    const saved = state.kits.findIndex((k) => k.name === trimmed);
    if (saved >= 0) {
      state.kitIndex = saved;
      renderKitStrip();
    }
  }

  async function deleteCurrentKit() {
    const kit = state.kits[state.kitIndex];
    if (!kit) return;
    if (!window.confirm(`Excluir o kit "${kit.name}"?`)) return;
    await fetch(`/api/kits/${kit.id}`, { method: "DELETE" });
    await loadKits();
  }

  el.kitPrev.addEventListener("click", () => loadKitAt(state.kitIndex - 1));
  el.kitNext.addEventListener("click", () => loadKitAt(state.kitIndex + 1));
  el.kitSave.addEventListener("click", saveCurrentKit);
  el.kitDelete.addEventListener("click", deleteCurrentKit);

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
      const row = document.createElement("div");
      row.className = "mix-control";
      row.innerHTML = `
        <span class="mix-control-label">${escapeHtml(p.label)}</span>
        <input type="range" min="${p.min}" max="${p.max}" step="${step}" value="${value}">
        <span class="mix-value">${formatParamValue(value, p.unit)}</span>
      `;
      const input = row.querySelector("input");
      const valueEl = row.querySelector(".mix-value");
      input.addEventListener("input", () => (valueEl.textContent = formatParamValue(Number(input.value), p.unit)));
      input.addEventListener("change", () => setPadEffectParam(padNumber, slot.slot_index, p.symbol, Number(input.value)));
      container.appendChild(row);
    }
  }

  function formatParamValue(value, unit) {
    const rounded = Math.round(value * 100) / 100;
    return unit ? `${rounded}${unit}` : `${rounded}`;
  }

  async function setPadEffectSlot(padNumber, slotIndex, pluginId) {
    await fetch(`/api/pads/${padNumber}/effects/${slotIndex}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plugin_id: pluginId }),
    });
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
  // Kits have to the pads), so a set can flip between programmed patterns
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

  function renderLooper() {
    const s = state.looper;
    const labels = {
      stopped: "Parado",
      recording: "Gravando...",
      playing: "Tocando em loop",
      overdubbing: "Sobrepondo...",
    };
    el.looperStateLabel.textContent = labels[s.state] || s.state;
    el.looperRecordBtn.textContent = s.state === "recording" ? "■ Fechar loop" : "● Gravar";
    el.looperRecordBtn.classList.toggle("active", s.state === "recording");
    el.looperRecordBtn.disabled = s.state === "overdubbing";
    el.looperOverdubBtn.textContent = s.state === "overdubbing" ? "■ Fechar sobreposição" : "+ Sobrepor";
    el.looperOverdubBtn.classList.toggle("active", s.state === "overdubbing");
    el.looperOverdubBtn.disabled = s.state !== "playing" && s.state !== "overdubbing";
    el.looperStopBtn.disabled = s.state !== "playing" && s.state !== "overdubbing";
    el.looperClearBtn.disabled = s.state === "stopped" && s.event_count === 0;

    clearInterval(looperTimer);
    if (s.state === "recording" || s.state === "playing" || s.state === "overdubbing") {
      looperTimer = setInterval(updateLooperElapsed, 200);
      updateLooperElapsed();
    } else {
      el.looperElapsed.textContent = "";
    }
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
      const suffix = s.state === "overdubbing" ? ` · ${s.overdub_event_count || 0} toques novos` : "";
      el.looperElapsed.textContent = `${pos.toFixed(1)}s / ${s.loop_duration.toFixed(1)}s${suffix}`;
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
  el.looperStopBtn.addEventListener("click", () => fetch("/api/looper/stop", { method: "POST" }));
  el.looperClearBtn.addEventListener("click", () => fetch("/api/looper/clear", { method: "POST" }));

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

  const CONTROLLER_ACTION_ELS = {
    looper_record_toggle: {
      chips: el.controllerBindingsLooperRecordToggle,
      btn: el.controllerLearnBtnLooperRecordToggle,
    },
    kit_next: {
      chips: el.controllerBindingsKitNext,
      btn: el.controllerLearnBtnKitNext,
    },
  };

  function renderControllerActions() {
    for (const [action, els] of Object.entries(CONTROLLER_ACTION_ELS)) {
      const bindings = state.controllerActions.bindings[action] || [];
      els.chips.innerHTML = bindings.length
        ? ""
        : `<span class="controller-binding-empty">Nenhum controle vinculado ainda.</span>`;
      for (const b of bindings) {
        const chip = document.createElement("span");
        chip.className = "controller-binding-chip";
        chip.innerHTML = `${b.midi_type === "note" ? "Nota" : "CC"} ${b.number} <button class="icon-btn delete-btn" title="Remover">✕</button>`;
        chip.querySelector(".delete-btn").addEventListener("click", () => {
          fetch(`/api/controller-actions/bindings/${b.id}`, { method: "DELETE" });
        });
        els.chips.appendChild(chip);
      }
      const waiting = state.controllerActions.pending_learn === action;
      els.btn.textContent = waiting ? "Aperte o botão..." : "Aprender";
      els.btn.classList.toggle("waiting", waiting);
    }
  }

  for (const [action, els] of Object.entries(CONTROLLER_ACTION_ELS)) {
    els.btn.addEventListener("click", () => {
      const waiting = state.controllerActions.pending_learn === action;
      const endpoint = waiting ? "/api/controller-actions/learn/cancel" : `/api/controller-actions/${action}/learn`;
      fetch(endpoint, { method: "POST" });
    });
  }

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
    renderModalSoundList("");
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
    const filtered = state.sounds.filter(s =>
      s.display_name.toLowerCase().includes(filterText.toLowerCase())
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
    // Uploaded in parallel (not one-by-one) so selecting/dropping several
    // files at once feels immediate rather than queued.
    const audioFiles = Array.from(files).filter(isSupportedAudioFile);
    return uploadEntries(audioFiles.map((file) => ({ file, folder: folderForFile(file) })));
  }

  async function uploadEntries(entries) {
    if (!entries.length) return;
    const totalBytes = entries.reduce((sum, { file }) => sum + file.size, 0);
    const uploadedBytes = new Array(entries.length).fill(0);
    el.uploadProgress.classList.remove("hidden");
    updateUploadProgress(0, totalBytes, entries.length);

    await Promise.all(
      entries.map(({ file, folder }, index) => uploadOne(file, folder, (loaded) => {
        uploadedBytes[index] = loaded;
        updateUploadProgress(uploadedBytes.reduce((sum, value) => sum + value, 0), totalBytes, entries.length);
      }))
    );
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
        if (!el.modal.classList.contains("hidden")) renderNoteLearnButton();
      } else if (msg.type === "note_learn") {
        state.pendingNoteLearn = msg.pending_pad;
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
      } else if (msg.type === "sequencer") {
        state.sequencer = { running: msg.running, current_step: msg.current_step, steps: msg.steps };
        renderSequencer();
      } else if (msg.type === "sequencer_tick") {
        state.sequencer.current_step = msg.current_step;
        updateSequencerPlayhead(msg.current_step);
      } else if (msg.type === "tempo") {
        state.tempo.bpm = msg.bpm;
        renderTempo();
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
      } else if (msg.type === "metronome_tick") {
        state.metronome.beat_in_bar = msg.beat_in_bar;
        renderMetronomeBeat(msg.beat_in_bar);
      } else if (msg.type === "kits") {
        state.kits = msg.kits;
        const currentIdx = state.kits.findIndex((k) => String(k.id) === String(msg.current_kit_id));
        if (currentIdx >= 0) state.kitIndex = currentIdx;
        else if (state.kitIndex >= state.kits.length) state.kitIndex = 0;
        renderKitStrip();
      } else if (msg.type === "patterns") {
        state.patterns = msg.patterns;
        if (state.patternIndex >= state.patterns.length) state.patternIndex = 0;
        renderPatternStrip();
      } else if (msg.type === "looper") {
        state.looper = {
          state: msg.state,
          loop_duration: msg.loop_duration,
          event_count: msg.event_count,
          overdub_event_count: msg.overdub_event_count,
          started_at: msg.started_at,
        };
        renderLooper();
      } else if (msg.type === "controller_actions") {
        state.controllerActions = { bindings: msg.bindings, pending_learn: msg.pending_learn };
        renderControllerActions();
      } else if (msg.type === "navigate") {
        switchView(msg.view);
      }
    });
  }

  async function init() {
    const [padsRes, soundsRes, knobsRes, settingsRes, padEffectsRes, catalogRes, knobTargetsRes, sequencerRes, looperRes, tempoRes, metronomeRes, metronomeStylesRes, metronomeSignaturesRes, masterRes, engineStatusRes, kitsRes, patternsRes, controllerActionsRes] =
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
        fetch("/api/kits"),
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
    state.kits = await kitsRes.json();
    if (state.kitIndex >= state.kits.length) state.kitIndex = 0;
    state.patterns = await patternsRes.json();
    if (state.patternIndex >= state.patterns.length) state.patternIndex = 0;
    state.controllerActions = await controllerActionsRes.json();

    renderPads();
    renderKitStrip();
    renderPatternStrip();
    renderControllerActions();
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
