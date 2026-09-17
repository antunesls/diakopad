# DiakoPad

App de gerenciamento de pads para o Zynthian OS + M-Vave SMC-PAD: mostra os
16 pads numa grade touch-friendly, permite atribuir um som a cada pad e
subir novos sons por qualquer navegador na rede. Os sons tocam através da
engine **Sfizz** nativa do Zynthian (mixer/efeitos preservados).

Veja o plano completo de arquitetura em
`C:\Users\antunesls\.claude\plans\eu-estou-usando-o-joyful-mountain.md`.

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

## Estrutura

```
backend/    App FastAPI (API + WebSocket), geração de .sfz, envio de MIDI
frontend/   UI web estática (grade de pads + biblioteca de sons)
deploy/     systemd units, script de instalação, daemon de alternância de tela
```
