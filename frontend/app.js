(() => {
  "use strict";

  const state = {
    pads: [],
    sounds: [],
    knobs: [],
    pendingLearn: null,
    selectedPad: null,
    settings: { sustain_mode: "1", velocity_sensitive: "0" },
  };

  const el = {
    padGrid: document.getElementById("pad-grid"),
    soundList: document.getElementById("sound-list"),
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
    knobsCountLabel: document.getElementById("knobs-count-label"),
    clearKnobsBtn: document.getElementById("clear-knobs-btn"),
  };

  const VIEWS = ["pads", "sounds", "volumes", "effects", "config"];
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

  function renderSoundList() {
    el.soundList.innerHTML = "";
    if (state.sounds.length === 0) {
      el.soundList.innerHTML = `<li class="sound-item"><span class="sound-name">Nenhum som enviado ainda.</span></li>`;
      return;
    }
    const inUse = new Set(state.pads.filter(p => p.sample_id).map(p => p.sample_id));
    for (const sound of state.sounds) {
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

  // --- Volumes / Efeitos --------------------------------------------------

  function findKnobCc(padNumber, param) {
    const m = state.knobs.find((k) => k.pad_number === padNumber && k.param === param);
    return m ? m.cc_number : null;
  }

  function isLearning(padNumber, param) {
    return (
      state.pendingLearn &&
      state.pendingLearn.pad_number === padNumber &&
      state.pendingLearn.param === param
    );
  }

  async function toggleLearn(padNumber, param) {
    if (isLearning(padNumber, param)) {
      await fetch("/api/knobs/learn/cancel", { method: "POST" });
    } else {
      await fetch("/api/knobs/learn", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pad_number: padNumber, param }),
      });
    }
  }

  function learnButtonHtml(padNumber, param) {
    const cc = findKnobCc(padNumber, param);
    if (isLearning(padNumber, param)) return `<button class="learn-btn waiting" data-param="${param}">gire o knob...</button>`;
    if (cc !== null) return `<button class="learn-btn bound" data-param="${param}">CC${cc} ✕</button>`;
    return `<button class="learn-btn" data-param="${param}">atribuir knob</button>`;
  }

  function wireLearnButton(row, padNumber, param) {
    const btn = row.querySelector(`.learn-btn[data-param="${param}"]`);
    btn.addEventListener("click", async () => {
      const cc = findKnobCc(padNumber, param);
      if (cc !== null && !isLearning(padNumber, param)) {
        await fetch(`/api/knobs/${cc}`, { method: "DELETE" });
        return;
      }
      toggleLearn(padNumber, param);
    });
  }

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
            ${learnButtonHtml(pad.pad_number, "volume")}
          </div>
          <div class="mix-control">
            <span class="mix-control-label">Pan</span>
            <input type="range" min="-100" max="100" step="1" value="${pad.pan}" data-role="pan">
            <span class="mix-value" data-role="pan-value">${panLabel(pad.pan)}</span>
            ${learnButtonHtml(pad.pad_number, "pan")}
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

      wireLearnButton(li, pad.pad_number, "volume");
      wireLearnButton(li, pad.pad_number, "pan");
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
            ${learnButtonHtml(pad.pad_number, "cutoff")}
          </div>
        </div>
      `;
      const toneInput = li.querySelector('[data-role="tone"]');
      const toneValue = li.querySelector('[data-role="tone-value"]');
      toneInput.addEventListener("input", () => {
        const v = Number(toneInput.value);
        toneValue.textContent = v >= 100 ? "aberto" : v + "%";
      });
      toneInput.addEventListener("change", () => setPadMix(pad.pad_number, { tone: Number(toneInput.value) }));
      wireLearnButton(li, pad.pad_number, "cutoff");
      el.effectsList.appendChild(li);
    }
  }

  async function setPadMix(padNumber, body) {
    await fetch(`/api/pads/${padNumber}/mix`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  // --- Config ---------------------------------------------------------

  function renderSettings() {
    el.settingSustain.checked = state.settings.sustain_mode === "1";
    el.settingVelocity.checked = state.settings.velocity_sensitive === "1";
    const n = state.knobs.length;
    el.knobsCountLabel.textContent = n === 0 ? "nenhum knob atribuído" : `${n} knob${n > 1 ? "s" : ""} atribuído${n > 1 ? "s" : ""}`;
    el.clearKnobsBtn.disabled = n === 0;
  }

  el.clearKnobsBtn.addEventListener("click", () => fetch("/api/knobs", { method: "DELETE" }));

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

  async function uploadOne(file) {
    const form = new FormData();
    form.append("file", file);
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
        renderSoundList();
      } else if (msg.type === "knobs") {
        state.knobs = msg.knobs;
        state.pendingLearn = msg.pending_learn;
        renderVolumes();
        renderEffects();
        renderSettings();
      } else if (msg.type === "settings") {
        state.settings = msg.settings;
        renderSettings();
      }
    });
  }

  async function init() {
    const [padsRes, soundsRes, knobsRes, settingsRes] = await Promise.all([
      fetch("/api/pads"),
      fetch("/api/sounds"),
      fetch("/api/knobs"),
      fetch("/api/settings"),
    ]);
    state.pads = await padsRes.json();
    state.sounds = await soundsRes.json();
    const knobsData = await knobsRes.json();
    state.knobs = knobsData.knobs;
    state.pendingLearn = knobsData.pending_learn;
    state.settings = await settingsRes.json();
    renderPads();
    renderSoundList();
    renderVolumes();
    renderSettings();
    renderEffects();
    connectWebSocket();
  }

  init();
})();
