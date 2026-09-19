# Indicador de Nota MIDI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** Exibir no cabeçalho a última nota MIDI física recebida, inclusive se ela não estiver associada a um pad.

**Architecture:** O backend publica um evento WebSocket separado para cada `note_on` da entrada física antes de verificar o mapeamento dos pads. O frontend converte o número MIDI para a convenção `C3 = 60` e atualiza um indicador persistente, com pulso visual e anúncio acessível.

**Tech Stack:** FastAPI, WebSocket, Python unittest, JavaScript e CSS.

---

### Task 1: Broadcast da nota física

**Files:**
- Modify: `backend/app.py:175-193,314-337`
- Test: `backend/tests/test_engine_live.py:88-113`

**Step 1:** Criar teste que envia uma nota sem pad mapeado e espera o evento `midi_note`.

**Step 2:** Rodar `backend/.venv/Scripts/python.exe -m unittest tests.test_engine_live.PadHitFeedbackTests.test_physical_midi_note_is_broadcast_without_matching_pad` no diretório `backend` e confirmar a falha.

**Step 3:** Publicar `{type: "midi_note", note, velocity}` antes do mapeamento de pads.

**Step 4:** Rodar o teste novamente e confirmar sucesso.

### Task 2: Indicador no cabeçalho

**Files:**
- Modify: `frontend/index.html:24-37`
- Modify: `frontend/app.js:37-45,281-290,1420-1422`
- Modify: `frontend/style.css:272-326`

**Step 1:** Adicionar um elemento `aria-live` no cabeçalho, inicialmente sem nota recebida.

**Step 2:** Tratar `midi_note`, converter o valor para nome de nota com `C3 = 60` e pulsar o indicador.

**Step 3:** Adicionar estilos responsivos que não reduzam a área dos controles existentes.

### Task 3: Verificação

**Files:**
- Test: `backend/tests/test_engine_live.py`

**Step 1:** Rodar a suíte completa com `backend/.venv/Scripts/python.exe -m unittest discover -s tests -v` no diretório `backend`.

**Step 2:** Revisar `git diff --check` e o diff dos arquivos alterados.
