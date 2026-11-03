// Aba "SMCPAD Config": lê e edita a configuração do M-Vave SMC-PAD pela API /api/smcpad.
// A gravação no aparelho é opt-in (botão "Gravação") e só envia os campos que mudaram.
(() => {
  "use strict";
  const view = document.getElementById("view-smcpad");
  const tab = document.getElementById("tab-smcpad");
  if (!view || !tab) return;

  const state = { cfg: null, preset: null, section: "pads", group: 3, pad: 32, error: null, loading: false, msg: null };
  const SECTIONS = [
    ["pads", "Pads"], ["buttons", "Botões"], ["knobs", "Knobs"], ["globals", "Globais"], ["calibration", "Calibração"], ["scenes", "Cores por cena"],
  ];

  // ---------- utilidades ----------
  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (key === "style") node.setAttribute("style", value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat()) {
      if (child !== null && child !== undefined && child !== false) node.append(child);
    }
    return node;
  }

  async function request(path, options) {
    let response;
    try {
      response = await fetch("/api/smcpad" + path, options);
    } catch (err) {
      return { ok: false, status: 0, data: { detail: "sem conexão com o DiakoPad" } };
    }
    let data = null;
    try { data = await response.json(); } catch (_) { /* sem corpo JSON */ }
    return { ok: response.ok, status: response.status, data };
  }

  const post = (path, body) => request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const detail = (res) => (res.data && res.data.detail) || `erro ${res.status}`;

  function setMessage(text, kind) { state.msg = text ? { text, kind: kind || "info" } : null; renderMessage(); }

  // ---------- campos de formulário ----------
  // Cada campo devolve { node, value() } e conhece o valor original para detectar mudanças.
  function numberField(label, value, min, max, opts = {}) {
    const input = el("input", { type: "number", min, max, step: 1, value, disabled: opts.disabled });
    return { label, original: value, node: input, value: () => (input.value === "" ? NaN : Number(input.value)), hint: opts.hint };
  }
  function selectField(label, value, options) {
    const input = el("select", {}, options.map((name, i) => el("option", { value: i, text: name, selected: i === value })));
    return { label, original: value, node: input, value: () => Number(input.value) };
  }
  function checkField(label, value) {
    const input = el("input", { type: "checkbox", checked: !!value });
    return { label, original: value ? 1 : 0, node: input, value: () => (input.checked ? 1 : 0) };
  }
  function colorField(label, value) {
    const input = el("input", { type: "color", value });
    return { label, original: value, node: input, value: () => input.value.toLowerCase() };
  }
  function textField(label, value, opts = {}) {
    const input = el("input", { type: "text", value, placeholder: opts.placeholder, maxlength: 80, class: "smcp-text" });
    return { label, original: value, node: input, value: () => input.value.trim() };
  }

  function formGrid(fields) {
    return el("div", { class: "smcp-form" }, Object.entries(fields).map(([, f]) => el("label", { class: "smcp-field" }, el("span", { text: f.label }), f.node)));
  }

  function limit(scope, field) { return state.cfg.limits[scope][field]; }

  // Lê os campos, compara com o original e devolve só as edições que mudaram.
  function collectEdits(fields, make) {
    const edits = [];
    for (const [name, f] of Object.entries(fields)) {
      const value = f.value();
      if (typeof value === "number" && Number.isNaN(value)) throw new Error(`${f.label}: valor inválido`);
      if (value !== f.original) edits.push(make(name, value, f.original));
    }
    return edits;
  }

  async function applyEdits(edits) {
    if (!edits.length) { setMessage("Nada mudou.", "info"); return; }
    if (!state.cfg.write_enabled) { setMessage("A gravação no aparelho está desligada. Ligue em “Gravação” para aplicar.", "err"); return; }
    setMessage("Gravando no aparelho…", "info");
    const res = await post("/edits", { edits, commit: true });
    if (!res.ok) { setMessage(`Não gravou: ${detail(res)}`, "err"); return; }
    const backup = String(res.data.backup || "").split(/[\\/]/).pop();
    setMessage(`Gravado e conferido (${res.data.applied.length} alteração(ões)). Backup: ${backup}`, "ok");
    await load(state.preset, true);
  }

  function applyBar(getEdits, disabledReason) {
    const button = el("button", { type: "button", class: "accent-btn", disabled: !!disabledReason, text: "Aplicar alterações" });
    button.addEventListener("click", () => {
      let edits;
      try { edits = getEdits(); } catch (err) { setMessage(err.message, "err"); return; }
      applyEdits(edits);
    });
    return el("div", { class: "smcp-apply" }, button, disabledReason ? el("small", { text: disabledReason }) : null);
  }

  function lockReason() {
    if (!state.cfg.write_enabled) return "Gravação desligada";
    if (state.preset !== state.cfg.current_preset) return `Ative o preset ${state.preset} no aparelho para editar`;
    return null;
  }

  // ---------- seções ----------
  function renderPads(root) {
    const cfg = state.cfg, pads = cfg.preset.pads;
    const chips = el("div", { class: "smcp-chips" });
    for (let g = 1; g <= 8; g++) {
      const first = pads[(g - 1) * 16], last = pads[(g - 1) * 16 + 15];
      const active = g === 3 + (cfg.globals.pad_bank || 0);
      chips.append(el("button", {
        type: "button", class: "smcp-chip" + (g === state.group ? " on" : ""),
        title: active ? "Banco de pads ativo no aparelho (inferido do banco A/B)" : "",
        onclick: () => { state.group = g; state.pad = (g - 1) * 16; render(); },
        text: `G${g} · ${first.note}–${last.note}${active ? " ●" : ""}`,
      }));
    }
    const grid = el("div", { class: "smcp-pads" });
    for (const row of [3, 2, 1, 0]) {
      for (let col = 0; col < 4; col++) {
        const n = row * 4 + col;
        const pad = pads[(state.group - 1) * 16 + n];
        const opacity = Math.max(0.25, pad.led / 255);
        grid.append(el("button", {
          type: "button", class: "smcp-pad" + (pad.index === state.pad ? " on" : ""),
          style: `--pad-color:${pad.color};--pad-opacity:${opacity}`,
          onclick: () => { state.pad = pad.index; render(); },
        }, el("span", { class: "smcp-pad-num", text: `PAD ${pad.number}` }), el("b", { text: String(pad.note) }), el("small", { text: cfg.enums.pad_types[pad.type] })));
      }
    }
    const pad = pads[state.pad];
    const fields = {
      type: selectField("Type", pad.type, cfg.enums.pad_types),
      channel: numberField("Channel", pad.channel, limit("pad", "channel").min, limit("pad", "channel").max),
      note: numberField("Nota", pad.note, 0, 127),
      min_vel: numberField("MinVel", pad.min_vel, 0, 127),
      max_vel: numberField("MaxVel", pad.max_vel, 0, 127),
      color: colorField("Cor", pad.color),
      led: numberField("LED (0–255)", pad.led, 0, 255),
    };
    const editor = el("div", { class: "smcp-editor" },
      el("h3", { text: `Pad ${pad.number} · grupo ${pad.group} · preset ${state.preset}` }),
      formGrid(fields),
      applyBar(() => collectEdits(fields, (name, value, expected) => ({ scope: "pad", preset: state.preset, index: pad.index, field: name, value, expected })), lockReason()));
    root.append(chips, el("div", { class: "smcp-split" }, grid, editor));
  }

  function renderButtons(root) {
    const cfg = state.cfg;
    for (const button of cfg.preset.buttons) {
      const fields = {
        mode: selectField("Mode", button.mode, cfg.enums.button_modes),
        channel: numberField("Channel", button.channel, 1, 16),
        cc: numberField("CC / Nota / Prog.", button.cc, 0, 127),
        value1: numberField("Value 1", button.value1, 0, 127),
        value2: numberField("Value 2", button.value2, 0, 127),
        led: numberField("LED", button.led, 0, 255),
        sysex: textField("SysEx (modo Custom)", button.sysex, { placeholder: "F0 7F 7F 06 02 F7" }),
      };
      root.append(el("section", { class: "smcp-card" },
        el("h3", { text: `Botão ${button.name} (nº ${button.index + 1})` }),
        formGrid(fields),
        applyBar(() => collectEdits(fields, (name, value, expected) => ({ scope: "button", preset: state.preset, index: button.index, field: name, value, expected })), lockReason())));
    }
  }

  function renderKnobs(root) {
    const cfg = state.cfg;
    for (const bank of [1, 2]) {
      const section = el("section", { class: "smcp-card" }, el("h3", { text: `Banco de knobs ${bank} (CC ${bank === 1 ? "30–37" : "38–45"} de fábrica)` }));
      for (const knob of cfg.preset.knobs.filter((k) => k.bank === bank)) {
        const fields = {
          type: selectField("Type", knob.type, cfg.enums.knob_types),
          speed: selectField("Speed", knob.speed, cfg.enums.knob_speeds),
          channel: numberField("Channel", knob.channel, 1, 16),
          cc: numberField("CC", knob.cc, 0, 127),
          min: numberField("Min", knob.min, 0, 127),
          max: numberField("Max", knob.max, 0, 127),
        };
        section.append(el("div", { class: "smcp-row" },
          el("strong", { text: `Knob ${knob.number}` }), formGrid(fields),
          applyBar(() => collectEdits(fields, (name, value, expected) => ({ scope: "knob", preset: state.preset, index: knob.index, field: name, value, expected })), lockReason())));
      }
      root.append(section);
    }
  }

  function renderGlobals(root) {
    const cfg = state.cfg, g = cfg.globals;
    const fields = {
      tempo: numberField("Tempo", g.tempo, limit("globals", "tempo").min, limit("globals", "tempo").max),
      swing: numberField("Swing", g.swing, 0, 100),
      time: selectField("Time", g.time, cfg.enums.time),
      octave: numberField("Octave", g.octave, limit("globals", "octave").min, limit("globals", "octave").max),
      transpose: numberField("Transpose", g.transpose, 0, 12),
      pad_curve: numberField("PadCurve", g.pad_curve, limit("globals", "pad_curve").min, limit("globals", "pad_curve").max),
      sync: checkField("Sync", g.sync),
      latch: checkField("Latch", g.latch),
      aftertouch: checkField("PadAftertouch", g.aftertouch),
    };
    const presetField = selectField("Preset ativo no aparelho", g.preset - 1, Array.from({ length: 8 }, (_, i) => `Preset ${i + 1}`));
    const disabledReason = state.cfg.write_enabled ? null : "Gravação desligada";
    root.append(el("section", { class: "smcp-card" },
      el("h3", { text: "Globais" }),
      formGrid(fields),
      el("p", { class: "smcp-note", text: `Banco de pads ativo no aparelho: ${g.pad_bank ? "B" : "A"} (só leitura; muda no botão PAD BANK). Transpose negativo ainda não é suportado.` }),
      applyBar(() => collectEdits(fields, (name, value) => ({ scope: "globals", field: name, value })), disabledReason)),
      el("section", { class: "smcp-card" },
        el("h3", { text: "Preset ativo" }),
        formGrid({ preset: presetField }),
        el("p", { class: "smcp-note", text: "Pads, botões e knobs só podem ser editados no preset ativo, como no Midi Suite." }),
        applyBar(() => collectEdits({ preset: presetField }, (name, value) => ({ scope: "globals", field: "preset", value: value + 1 })), disabledReason)));
  }

  function renderCalibration(root) {
    const cfg = state.cfg;
    const levels = Array.from({ length: 8 }, (_, i) => `Level ${i + 1}`);
    const fields = {};
    const grid = el("div", { class: "smcp-cal" });
    for (const row of [3, 2, 1, 0]) {
      for (let col = 0; col < 4; col++) {
        const pad = cfg.calibration[row * 4 + col];
        fields[pad.pad] = selectField(`PAD ${pad.pad}`, pad.level - 1, levels);
        grid.append(el("label", { class: "smcp-cal-cell" }, el("span", { text: `PAD ${pad.pad}` }), fields[pad.pad].node, el("small", { text: `valor ${pad.raw}` })));
      }
    }
    root.append(el("section", { class: "smcp-card" },
      el("h3", { text: "Calibração dos pads" }),
      el("p", { class: "smcp-note", text: "Nível de sensibilidade de cada pad (1 = mais sensível). Cada nível grava 50 + 100 × (nível − 1). O valor de fábrica 220 aparece como Level 2; só é regravado se você mudar o nível." }),
      grid,
      applyBar(() => collectEdits(fields, (pad, value) => ({ scope: "calibration", pad: Number(pad), field: "level", value: value + 1 })), state.cfg.write_enabled ? null : "Gravação desligada")));
  }

  // Cores por cena: cada cena do DiakoPad guarda as 128 cores dos pads do preset ativo.
  // Não exige "Gravação": aplicar pinta o aparelho ao vivo, sem gravar de vez.
  function renderScenes(root) {
    const card = el("section", { class: "smcp-card" },
      el("h3", { text: "Cores por cena" }),
      el("p", { class: "smcp-note", text: "Ao salvar uma cena, as cores atuais dos pads do SMC-PAD entram nela (se o aparelho estiver conectado). Ao carregar a cena, as cores são aplicadas ao vivo no aparelho, sem gravar de vez: ao desligá-lo, ele volta às cores gravadas. Falhas nunca impedem salvar ou trocar de cena." }));
    root.append(card);
    loadSceneColors(card);
  }

  async function loadSceneColors(card) {
    const res = await request("/scene-colors");
    if (!res.ok) { card.append(el("p", { class: "smcp-msg err", text: `Não foi possível carregar as cenas: ${detail(res)}` })); return; }
    const toggle = el("input", { type: "checkbox", checked: res.data.enabled });
    toggle.addEventListener("change", async () => {
      const r = await post("/scene-colors/enabled", { enabled: toggle.checked });
      setMessage(r.ok ? (toggle.checked ? "Cores por cena ligadas." : "Cores por cena desligadas (as cenas mantêm as cores já salvas).") : `Erro: ${detail(r)}`, r.ok ? "ok" : "err");
    });
    card.append(el("label", { class: "smcp-field smcp-toggle" }, toggle, el("span", { text: "Salvar e aplicar as cores ao salvar e trocar de cena" })));
    card.append(
      el("div", { class: "smcp-legend" }, (res.data.legend || []).map((item) =>
        el("span", { class: "smcp-legend-item" }, el("i", { style: `background:${item.color}` }), item.label))),
      el("div", { class: "smcp-apply" },
        el("button", { type: "button", class: "accent-btn", text: "Gerar cores de todas as cenas pelos sons", onclick: async () => {
          if (!window.confirm("Gerar o kit de cores de TODAS as cenas pelo tipo de som de cada pad? Isso substitui as cores dos pads dos sons (as demais cores da cena são mantidas).")) return;
          const r = await post("/scene-colors/generate-all", {});
          setMessage(r.ok ? `Cores geradas para ${r.data.scenes.length} cena(s).` : `Erro: ${detail(r)}`, r.ok ? "ok" : "err");
          if (r.ok) render();
        } }),
        el("small", { text: "Bumbos em azul, caixas em vermelho, hi-hats em amarelo, pratos em ciano… (legenda acima)" })));
    if (!res.data.scenes.length) { card.append(el("p", { class: "smcp-note", text: "Nenhuma cena salva ainda. Salve uma na aba Performance." })); return; }
    const list = el("div", { class: "smcp-scenes" });
    for (const scene of res.data.scenes) list.append(sceneRow(scene));
    card.append(list);
  }

  function sceneRow(scene) {
    const swatches = el("div", { class: "smcp-swatches" });
    if (scene.has_colors) {
      request(`/scenes/${scene.id}/colors`).then((r) => {
        if (r.ok) swatches.replaceChildren(...r.data.colors.map((c, i) => el("i", { style: `background:${c}`, title: `Pad ${i % 16 + 1} · grupo ${Math.floor(i / 16) + 1}` })));
      });
    }
    const act = (label, handler, disabled) => el("button", { type: "button", class: "secondary-btn", text: label, disabled, onclick: handler });
    const run = async (promise, okText) => {
      const r = await promise;
      setMessage(r.ok ? okText : `Erro: ${detail(r)}`, r.ok ? "ok" : "err");
      if (r.ok) render();
    };
    return el("div", { class: "smcp-scene-row" },
      el("strong", { text: scene.name }),
      el("span", { class: "smcp-badge" + (scene.has_colors ? " on" : ""), text: scene.has_colors ? "com cores" : "sem cores" }),
      swatches,
      el("div", { class: "smcp-apply" },
        act("Gerar pelos sons", () => run(post(`/scenes/${scene.id}/colors/generate`, {}), `Cores da cena “${scene.name}” geradas pelos tipos de som.`)),
        act("Salvar cores atuais", () => run(request(`/scenes/${scene.id}/colors/capture`, { method: "POST" }), `Cores atuais do aparelho salvas na cena “${scene.name}”.`)),
        act("Aplicar agora", () => run(request(`/scenes/${scene.id}/colors/apply`, { method: "POST" }), `Cores da cena “${scene.name}” aplicadas ao vivo.`), !scene.has_colors),
        act("Remover", () => { if (window.confirm(`Remover as cores salvas da cena “${scene.name}”?`)) run(request(`/scenes/${scene.id}/colors`, { method: "DELETE" }), "Cores removidas da cena."); }, !scene.has_colors)));
  }

  // ---------- estrutura da tela ----------
  function header() {
    const cfg = state.cfg;
    const presetSelect = el("select", { "aria-label": "Preset em exibição" }, Array.from({ length: 8 }, (_, i) =>
      el("option", { value: i + 1, text: `Preset ${i + 1}${i + 1 === cfg.current_preset ? " (ativo)" : ""}`, selected: i + 1 === state.preset })));
    presetSelect.addEventListener("change", () => load(Number(presetSelect.value)));
    const fileInput = el("input", { type: "file", accept: ".spc,.bin", class: "hidden", onchange: (e) => importFile(e.target.files[0]) });
    return el("div", { class: "smcp-head" },
      el("span", { class: "smcp-dot ok", title: "Conectado" }), el("strong", { text: cfg.identity }),
      presetSelect,
      el("button", { type: "button", class: "secondary-btn", text: "Recarregar", onclick: () => load(state.preset, true) }),
      el("a", { class: "secondary-btn smcp-link", href: `/api/smcpad/preset/${state.preset}/export`, text: "Exportar preset (.spc)" }),
      el("button", { type: "button", class: "secondary-btn", text: "Importar preset…", disabled: !cfg.write_enabled || state.preset !== cfg.current_preset, onclick: () => fileInput.click() }),
      fileInput,
      el("button", { type: "button", class: "smcp-write " + (cfg.write_enabled ? "on" : ""), text: `Gravação: ${cfg.write_enabled ? "LIGADA" : "desligada"}`, onclick: toggleWrite }));
  }

  async function toggleWrite() {
    const turnOn = !state.cfg.write_enabled;
    if (turnOn && !window.confirm("Ligar a gravação no aparelho?\n\nCada alteração faz backup, grava, confere e só então grava de vez. Mesmo assim, use com o show parado.")) return;
    const res = await post("/write-enabled", { enabled: turnOn });
    if (!res.ok) { setMessage(`Não foi possível mudar: ${detail(res)}`, "err"); return; }
    await load(state.preset, true);
  }

  async function importFile(file) {
    if (!file) return;
    if (!window.confirm(`Importar “${file.name}” para o Preset ${state.preset}? Isso sobrescreve o preset no aparelho (um backup é feito antes).`)) return;
    setMessage("Importando…", "info");
    const res = await request(`/preset/${state.preset}/import`, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: await file.arrayBuffer() });
    if (!res.ok) { setMessage(`Não importou: ${detail(res)}`, "err"); return; }
    setMessage("Preset importado e conferido.", "ok");
    await load(state.preset, true);
  }

  function renderMessage() {
    const box = view.querySelector(".smcp-msg");
    if (!box) return;
    box.textContent = state.msg ? state.msg.text : "";
    box.className = "smcp-msg" + (state.msg ? ` ${state.msg.kind}` : "");
  }

  function render() {
    view.replaceChildren();
    if (state.loading && !state.cfg) { view.append(el("p", { class: "smcp-note", text: "Lendo o aparelho…" })); return; }
    if (state.error) {
      view.append(el("section", { class: "smcp-card" },
        el("h3", { text: "SMC-PAD não disponível" }),
        el("p", { text: state.error }),
        el("p", { class: "smcp-note", text: "Ligue o aparelho por USB neste laptop e feche o Midi Suite, que usa a mesma porta." }),
        el("button", { type: "button", class: "secondary-btn", text: "Tentar de novo", onclick: () => load(null, true) })));
      return;
    }
    const nav = el("nav", { class: "smcp-tabs" }, SECTIONS.map(([key, name]) =>
      el("button", { type: "button", class: "smcp-tab" + (key === state.section ? " on" : ""), text: name, onclick: () => { state.section = key; render(); } })));
    const body = el("div", { class: "smcp-body" });
    view.append(header(), el("div", { class: "smcp-msg" }), nav, body);
    if (["pads", "buttons", "knobs"].includes(state.section) && state.preset !== state.cfg.current_preset) {
      body.append(el("p", { class: "smcp-warn", text: `O preset ${state.preset} não está ativo no aparelho (ativo: ${state.cfg.current_preset}). Você pode ver, mas só edita o preset ativo — ative em Globais.` }));
    }
    ({ pads: renderPads, buttons: renderButtons, knobs: renderKnobs, globals: renderGlobals, calibration: renderCalibration, scenes: renderScenes })[state.section](body);
    renderMessage();
  }

  async function load(preset, keepMessage) {
    state.loading = true;
    if (!state.cfg) render();
    const res = await request(preset ? `/config?preset=${preset}` : "/config");
    state.loading = false;
    if (!res.ok) { state.cfg = null; state.error = detail(res); render(); return; }
    state.error = null;
    state.cfg = res.data;
    state.preset = res.data.preset_number;
    if (!keepMessage) state.msg = null;
    render();
  }

  tab.addEventListener("click", () => load(state.preset, false));
})();
