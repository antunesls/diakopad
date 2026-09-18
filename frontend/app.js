(() => {
  "use strict";

  const state = {
    pads: [],
    sounds: [],
    soundBrowserPath: [],
    soundBrowserView: { folders: [], samples: [] },
    knobs: [],
    knobTargets: { global: [], pads: {} },
    pendingLearn: null,
    selectedPad: null,
    settings: { sustain_mode: "1", velocity_sensitive: "0" },
    padEffects: [],
    effectsCatalog: [],
    sequencer: { running: false, current_step: 0, steps: [] },
    looper: { state: "stopped", loop_duration: null, event_count: 0, started_at: null },
    tempo: { bpm: 100 },
    metronome: { running: false, beat_in_bar: 0, beats_per_bar: 4, style: "digital" },
    metronomeStyles: [],
  };

  const knobPicker = { step: null, scope: null, padNumber: null };
  let looperTimer = null;
  let tapTimestamps = [];

  const el = {
    padGrid: document.getElementById("pad-grid"),
    soundList: document.getElementById("sound-list"),
    soundBreadcrumb: document.getElementById("sound-breadcrumb"),
    volumeList: document.getElementById("volume-list"),
    effectsList: document.getElementById("effects-list"),
    uploadZone: document.getElementById("upload-zone"),
    fileInput: document.getElementById("file-input"),
    modal: document.getElementById("assign-modal"),
    modalTitle: document.getElementById("modal-pad-title"),
    modalClose: document.getElementById("modal-close"),
    modalSearch: document.getElementById("modal-search"),
    modalClear: document.getElementById("modal-clear"),
    modalSoundList: document.getElementById("modal-sound-list"),
    connStatus: document.getElementById("conn-status"),
    previewAudio: document.getElementById("preview-audio"),
    settingSustain: document.getElementById("setting-sustain"),
    settingVelocity: document.getElementById("setting-velocity"),
    sequencerPlayBtn: document.getElementById("sequencer-play-btn"),
    sequencerBpm: document.getElementById("sequencer-bpm"),
    sequencerClearBtn: document.getElementById("sequencer-clear-btn"),
    sequencerHeadRow: document.getElementById("sequencer-head-row"),
    sequencerBody: document.getElementById("sequencer-body"),
    looperStateLabel: document.getElementById("looper-state-label"),
    looperElapsed: document.getElementById("looper-elapsed"),
    looperRecordBtn: document.getElementById("looper-record-btn"),
    looperStopBtn: document.getElementById("looper-stop-btn"),
    looperClearBtn: document.getElementById("looper-clear-btn"),
    metronomeBpm: document.getElementById("metronome-bpm"),
    metronomeTapBtn: document.getElementById("metronome-tap-btn"),
    metronomeBeatRow: document.getElementById("metronome-beat-row"),
    metronomePlayBtn: document.getElementById("metronome-play-btn"),
    metronomeStyleSelect: document.getElementById("metronome-style-select"),
    metronomeBeatsSelect: document.getElementById("metronome-beats-select"),
    knobsList: document.getElementById("knobs-list"),
    knobAddBtn: document.getElementById("knob-add-btn"),
    knobClearAllBtn: document.getElementById("knob-clear-all-btn"),
    knobModal: document.getElementById("knob-modal"),
    knobModalTitle: document.getElementById("knob-modal-title"),
    knobModalClose: document.getElementById("knob-modal-close"),
    knobModalList: document.getElementById("knob-modal-list"),
    knobModalWaiting: document.getElementById("knob-modal-waiting"),
  };

  const VIEWS = ["pads", "sounds", "volumes", "effects", "sequencer", "metronome", "looper", "knobs", "config"];
  for (const name of VIEWS) {
    document.getElementById(`tab-${name}`).addEventListener("click", () => switchView(name));
  }
  function switchView(view) {
    for (const name of VIEWS) {
      document.getElementById(`tab-${name}`).classList.toggle("active", name === view);
      document.getElementById(`view-${name}`).classList.toggle("hidden", name !== view);
    }
  }

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

  function renderPads() {
    el.padGrid.innerHTML = "";
    const pads = displayOrder(state.pads);
    for (const pad of pads) {
      const div = document.createElement("div");
      div.className = "pad" + (pad.has_sample ? " filled" : "");
      div.innerHTML = `
        <div class="pad-number">PAD ${pad.pad_number}</div>
        <div class="pad-name">${pad.has_sample ? escapeHtml(pad.display_name) : "vazio"}</div>
        <div class="pad-note">nota ${pad.midi_note}</div>
      `;
      div.addEventListener("click", () => openAssignModal(pad.pad_number));
      el.padGrid.appendChild(div);
    }
  }

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

    for (const name of folders) {
      const li = document.createElement("li");
      li.className = "sound-item folder-item";
      li.innerHTML = `<span class="sound-name">📁 ${escapeHtml(name)}</span>`;
      li.addEventListener("click", () => navigateToFolder([...state.soundBrowserPath, name]));
      el.soundList.appendChild(li);
    }

    if (folders.length === 0 && samples.length === 0) {
      el.soundList.innerHTML += `<li class="sound-item"><span class="sound-name">Pasta vazia.</span></li>`;
      return;
    }

    const inUse = new Set(state.pads.filter(p => p.sample_id).map(p => p.sample_id));
    for (const sound of samples) {
      const li = document.createElement("li");
      li.className = "sound-item";
      const used = inUse.has(sound.id);
      li.innerHTML = `
        <button class="icon-btn play-btn" title="Ouvir">▶</button>
        <span class="sound-name">${escapeHtml(sound.display_name)}</span>
        <button class="icon-btn delete-btn" title="${used ? "Em uso, não pode remover" : "Remover"}" ${used ? "disabled" : ""}>🗑</button>
      `;
      li.querySelector(".play-btn").addEventListener("click", () => playPreview(sound.id));
      li.querySelector(".delete-btn").addEventListener("click", () => deleteSound(sound.id));
      el.soundList.appendChild(li);
    }
  }

  // --- Volumes --------------------------------------------------------

  function renderVolumes() {
    el.volumeList.innerHTML = "";
    for (const pad of displayOrder(state.pads)) {
      const li = document.createElement("li");
      li.className = "mix-row";
      li.innerHTML = `
        <div class="mix-row-header">
          <span class="pad-label">PAD ${pad.pad_number}</span>
          <span class="pad-sound">${pad.has_sample ? escapeHtml(pad.display_name) : "vazio"}</span>
        </div>
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
      `;
      const volumeInput = li.querySelector('[data-role="volume"]');
      const volumeValue = li.querySelector('[data-role="volume-value"]');
      volumeInput.addEventListener("input", () => (volumeValue.textContent = `${volumeInput.value} dB`));
      volumeInput.addEventListener("change", () => setPadMix(pad.pad_number, { volume_db: Number(volumeInput.value) }));

      const panInput = li.querySelector('[data-role="pan"]');
      const panValue = li.querySelector('[data-role="pan-value"]');
      panInput.addEventListener("input", () => (panValue.textContent = panLabel(Number(panInput.value))));
      panInput.addEventListener("change", () => setPadMix(pad.pad_number, { pan: Number(panInput.value) }));

      el.volumeList.appendChild(li);
    }
  }

  function panLabel(pan) {
    if (pan === 0) return "centro";
    return pan < 0 ? `E ${Math.abs(pan)}` : `D ${pan}`;
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
      li.className = "mix-row";
      li.innerHTML = `
        <div class="mix-row-header">
          <span class="pad-label">PAD ${pad.pad_number}</span>
          <span class="pad-sound">${pad.has_sample ? escapeHtml(pad.display_name) : "vazio"}</span>
        </div>
        <div class="mix-controls">
          <div class="mix-control">
            <span class="mix-control-label">Tom</span>
            <input type="range" min="0" max="100" step="1" value="${tone}" data-role="tone">
            <span class="mix-value" data-role="tone-value">${tone >= 100 ? "aberto" : tone + "%"}</span>
          </div>
        </div>
        <div class="effect-slots"></div>
      `;
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
          <span class="effect-slot-label">Slot ${slot.slot_index}</span>
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

  function renderSequencer() {
    el.sequencerPlayBtn.textContent = state.sequencer.running ? "■ Parar" : "▶ Tocar";
    el.sequencerPlayBtn.classList.toggle("active", state.sequencer.running);

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

  el.sequencerPlayBtn.addEventListener("click", () => {
    fetch("/api/sequencer/transport", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ running: !state.sequencer.running }),
    });
  });
  el.sequencerBpm.addEventListener("change", () => {
    fetch("/api/sequencer/bpm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bpm: Number(el.sequencerBpm.value) }),
    });
  });
  el.sequencerClearBtn.addEventListener("click", () => fetch("/api/sequencer/clear", { method: "POST" }));

  // --- Looper ----------------------------------------------------------

  function renderLooper() {
    const s = state.looper;
    const labels = { stopped: "Parado", recording: "Gravando...", playing: "Tocando em loop" };
    el.looperStateLabel.textContent = labels[s.state] || s.state;
    el.looperRecordBtn.textContent = s.state === "recording" ? "■ Fechar loop" : "● Gravar";
    el.looperRecordBtn.classList.toggle("active", s.state === "recording");
    el.looperStopBtn.disabled = s.state !== "playing";
    el.looperClearBtn.disabled = s.state === "stopped" && s.event_count === 0;

    clearInterval(looperTimer);
    if (s.state === "recording" || s.state === "playing") {
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
    } else if (s.state === "playing" && s.loop_duration) {
      const pos = ((elapsed % s.loop_duration) + s.loop_duration) % s.loop_duration;
      el.looperElapsed.textContent = `${pos.toFixed(1)}s / ${s.loop_duration.toFixed(1)}s`;
    }
  }

  el.looperRecordBtn.addEventListener("click", () => {
    const endpoint = state.looper.state === "recording" ? "/api/looper/record/stop" : "/api/looper/record/start";
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

  function openAssignModal(padNumber) {
    state.selectedPad = padNumber;
    const pad = state.pads.find(p => p.pad_number === padNumber);
    el.modalTitle.textContent = `Pad ${padNumber} — escolher som`;
    el.modalSearch.value = "";
    el.modalClear.disabled = !pad || !pad.sample_id;
    renderModalSoundList("");
    el.modal.classList.remove("hidden");
    el.modalSearch.focus();
  }

  function closeModal() {
    el.modal.classList.add("hidden");
    state.selectedPad = null;
  }
  el.modalClose.addEventListener("click", closeModal);
  el.modal.addEventListener("click", (e) => {
    if (e.target === el.modal) closeModal();
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

  async function uploadOne(file) {
    const form = new FormData();
    form.append("file", file);
    form.append("folder", currentSoundFolder());
    const res = await fetch("/api/sounds/upload", { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      alert(`Falha ao enviar ${file.name}: ${body.detail || res.status}`);
    }
  }

  async function uploadFiles(files) {
    // Uploaded in parallel (not one-by-one) so selecting/dropping several
    // files at once feels immediate rather than queued.
    await Promise.all(Array.from(files).map(uploadOne));
  }

  el.fileInput.addEventListener("change", () => {
    if (el.fileInput.files.length) {
      uploadFiles(el.fileInput.files);
      el.fileInput.value = "";
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
    if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
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
        state.sequencer = { bpm: msg.bpm, running: msg.running, current_step: msg.current_step, steps: msg.steps };
        renderSequencer();
      } else if (msg.type === "sequencer_tick") {
        state.sequencer.current_step = msg.current_step;
        updateSequencerPlayhead(msg.current_step);
      } else if (msg.type === "looper") {
        state.looper = { state: msg.state, loop_duration: msg.loop_duration, event_count: msg.event_count, started_at: msg.started_at };
        renderLooper();
      }
    });
  }

  async function init() {
    const [padsRes, soundsRes, knobsRes, settingsRes, padEffectsRes, catalogRes, knobTargetsRes, sequencerRes, looperRes] =
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
    state.sequencer = { bpm: sequencerData.bpm, running: sequencerData.running, current_step: sequencerData.current_step, steps: sequencerData.steps };
    state.looper = await looperRes.json();

    renderPads();
    await loadSoundBrowser();
    renderVolumes();
    renderSettings();
    renderEffects();
    renderSequencerHead();
    renderSequencer();
    renderLooper();
    renderKnobs();
    connectWebSocket();
  }

  init();
})();
