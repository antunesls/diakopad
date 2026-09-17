(() => {
  "use strict";

  const state = {
    pads: [],
    sounds: [],
    selectedPad: null,
  };

  const el = {
    tabPads: document.getElementById("tab-pads"),
    tabSounds: document.getElementById("tab-sounds"),
    viewPads: document.getElementById("view-pads"),
    viewSounds: document.getElementById("view-sounds"),
    padGrid: document.getElementById("pad-grid"),
    soundList: document.getElementById("sound-list"),
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
  };

  function switchView(view) {
    const isPads = view === "pads";
    el.viewPads.classList.toggle("hidden", !isPads);
    el.viewSounds.classList.toggle("hidden", isPads);
    el.tabPads.classList.toggle("active", isPads);
    el.tabSounds.classList.toggle("active", !isPads);
  }
  el.tabPads.addEventListener("click", () => switchView("pads"));
  el.tabSounds.addEventListener("click", () => switchView("sounds"));

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

  async function uploadFiles(files) {
    for (const file of files) {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch("/api/sounds/upload", { method: "POST", body: form });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        alert(`Falha ao enviar ${file.name}: ${body.detail || res.status}`);
      }
    }
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
      } else if (msg.type === "sounds") {
        state.sounds = msg.sounds;
        renderSoundList();
      }
    });
  }

  async function init() {
    const [padsRes, soundsRes] = await Promise.all([
      fetch("/api/pads"),
      fetch("/api/sounds"),
    ]);
    state.pads = await padsRes.json();
    state.sounds = await soundsRes.json();
    renderPads();
    renderSoundList();
    connectWebSocket();
  }

  init();
})();
