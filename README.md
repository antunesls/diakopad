# DiakoPad

App de gerenciamento de pads para o Zynthian OS + M-Vave SMC-PAD: mostra os
16 pads numa grade touch-friendly, permite atribuir um som a cada pad e
subir novos sons por qualquer navegador na rede. Os sons tocam através da
engine **Sfizz** nativa do Zynthian (mixer/efeitos preservados).


## Rodando localmente (sem hardware, para desenvolver a UI)

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # no Windows: .venv\Scripts\pip
.venv/bin/uvicorn app:app --reload --port 8080
```

Abra `http://localhost:8080/`. Sem uma porta MIDI/engine Sfizz real
conectada, as atribuições de pad funcionam normalmente na interface (grava
no banco SQLite e gera os `.sfz` em `backend/sfz_bank/`), só o envio do
Program Change vira um no-op registrado no log.

## Deploy no Zynthian

1. Copie este repositório para o Pi, ex.: `scp -r . zynthian@10.100.99.158:~/diakopad`
2. No Pi: `bash ~/diakopad/deploy/install.sh`
3. Siga os passos manuais impressos ao final do script (criar a chain Sfizz
   na UI nativa, apontar o banco de presets, conectar a porta MIDI).

## Passo manual único: preparar o SMC-PAD

No app **CubeSuite** (M-Vave), configure um preset onde os 16 pads enviam
**Note** (não CC/PC) num canal MIDI fixo (ex.: canal 10) — é esse preset que
deve ficar ativo no controlador durante o uso normal.

Depois, em `Library > Captures` no webconf do Zynthian, grave uma captura
MIDI tocando cada pad físico para descobrir qual nota cada um envia (o
manual do fabricante não documenta uma tabela fixa). Ajuste as notas de cada
pad no DiakoPad via `POST /api/pads/{n}/note` (ou uma futura tela de
configuração) para bater com o que o SMC-PAD realmente envia.

## Volumes, Efeitos e knobs

Abas **Volumes** (volume + pan por pad) e **Efeitos** (tom/filtro grave-agudo
por pad, via SFZ) — cada slider aplica ~300ms depois de soltar (regrava o
`.sfz` e troca A/B, igual à atribuição de som). Reverb/delay **não** está
disponível: a versão do Sfizz deste Zynthian (1.2.3) não suporta o efeito
interno (`Unsupported effect type: reverb`, testado com `sfizz_render`).

Cada slider tem um botão **atribuir knob**: clique, gire um knob físico do
SMC-PAD, o app captura o CC automaticamente (MIDI learn) e a partir daí esse
knob controla aquele slider (com o mesmo delay de ~300ms). Isso exige a porta
de entrada MIDI do DiakoPad conectada aos knobs — já feito pelo
`diakopad-midi-connect.service` (ver `deploy/`).

## Estrutura

```
backend/    App FastAPI (API + WebSocket), geração de .sfz, envio de MIDI
frontend/   UI web estática (grade de pads + biblioteca de sons)
deploy/     systemd units, script de instalação, daemon de alternância de tela
```
