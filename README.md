# DiakoPad

App standalone de drum pad para Raspberry Pi + M-Vave SMC-PAD: mostra os 16
pads numa grade touch-friendly, permite atribuir um som a cada pad e subir
novos sons por qualquer navegador na rede. Roda sobre a imagem do Zynthian
OS (kernel RT e jackd já calibrados), mas com **zynthian-ui e
zynthian-webconf desabilitados** — o próprio DiakoPad orquestra o áudio
diretamente: uma instância do **Sfizz** por pad, mais **mod-host** (o mesmo
host de plugins LV2 que o Zynthian usa) para os efeitos configuráveis por
pad, um step sequencer, um metrônomo e um looper ao vivo. Sequencer e looper
disparam os pads via um canal MIDI próprio; o metrônomo usa uma instância
Sfizz dedicada. Veja `backend/engine/` e o plano de migração para os detalhes.


## Rodando localmente (sem hardware, para desenvolver a UI)

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # no Windows: .venv\Scripts\pip
.venv/bin/uvicorn app:app --reload --port 8080
```

Abra `http://localhost:8080/`. Sem JACK/sfizz_jack/mod-host reais disponíveis
(o caso normal num PC de desenvolvimento), as atribuições de pad funcionam
normalmente na interface (grava no banco SQLite e gera os `.sfz` em
`backend/sfz_bank/`), só a parte de motor de áudio vira no-op registrado no
log — mesmo comportamento best-effort que o MIDI já tinha.

## Deploy num Raspberry Pi (imagem do Zynthian OS, zynthian-ui desligado)

1. Copie este repositório para o Pi, ex.: `scp -r . zynthian@10.100.99.158:~/diakopad`
2. No Pi: `bash ~/diakopad/deploy/install.sh`
3. Siga os passos manuais impressos ao final do script: confirmar que
   jackd/a2jmidid continuam de pé antes de desligar zynthian-ui/webconf,
   descobrir os binários/URIs LV2 reais instalados na imagem (`lv2ls`,
   `lv2info`) e só então iniciar `diakopad.service` e o kiosk.

## Passo manual único: preparar o SMC-PAD

No app **CubeSuite** (M-Vave), configure um preset onde os 16 pads enviam
**Note** (não CC/PC) num canal MIDI fixo (ex.: canal 10) — é esse preset que
deve ficar ativo no controlador durante o uso normal.

Depois, em `Library > Captures` no webconf do Zynthian, grave uma captura
MIDI tocando cada pad físico para descobrir qual nota cada um envia (o
manual do fabricante não documenta uma tabela fixa). Ajuste as notas de cada
pad no DiakoPad via `POST /api/pads/{n}/note` (ou uma futura tela de
configuração) para bater com o que o SMC-PAD realmente envia.

## Volumes e Efeitos

Aba **Volumes** (volume + pan por pad): aplicam ~300ms depois de soltar o
slider (regrava o `.sfz` do pad e reinicia sua instância sfizz).

Aba **Efeitos**: tom (filtro grave/agudo, nativo do sfizz) mais **3 slots de
efeito configuráveis por pad** — em cada slot você escolhe um plugin de um
catálogo curado (Reverb, Delay, Compressor, Overdrive, EQ 3 bandas, ver
`backend/engine/effects_catalog.py`) e ajusta os parâmetros daquele plugin.
Trocar o plugin de um slot recria a cadeia no mod-host (`sfizz → slot 1 →
slot 2 → slot 3 → saída`); mudar só um parâmetro é um `param_set` barato,
sem reiniciar nada.

O Sfizz em si nunca suportou reverb/delay internos (opcode `effect1`/
`<effect>` do SFZ v2 não implementado — testado com `sfizz_render` na versão
1.2.3, `Unsupported effect type: reverb`); é por isso que todo efeito é
hospedado via **mod-host** (LV2), o mesmo host de efeitos que o Zynthian usa
nativamente, e não via SFZ.

## Sequencer, Metrônomo e Looper

Aba **Sequencer**: step sequencer clássico (16 passos × 16 pads, um padrão
compartilhado, BPM global) — liga/desliga passos na grade, dá play/stop, e
o passo atual é destacado em tempo real via WebSocket.

Aba **Metrônomo**: clique independente com Tap Tempo, destaque visual do
tempo atual, escolha de compasso e quatro estilos de som sintetizados
(Digital, Beep, Madeira e Click seco). O BPM é global e compartilhado em
tempo real com o Sequencer e com o alvo Tempo da aba Knobs.

Aba **Looper**: um loop único e compartilhado, estilo pedal de loop. Grava o
que você toca em qualquer pad enquanto está gravando; ao fechar a gravação,
a duração do loop fica fixa (sem quantização por tempo) e ele passa a
repetir sozinho. Sem overdub por enquanto.

Os dois disparam os pads programaticamente pela porta MIDI virtual
`DiakoPad-trigger-out` (`backend/engine/trigger.py`), conectada pelo
orchestrator a cada instância sfizz — nunca à porta de entrada `DiakoPad-in`,
para o looper não gravar os próprios disparos automáticos.

## Knobs

Aba central pra atribuir função a cada knob físico do SMC-PAD: clique em
"Atribuir novo knob", escolha o alvo (um pad específico ou "Global", hoje só
com o Tempo compartilhado), escolha o parâmetro daquele alvo (volume, pan,
tom, ou um parâmetro de um efeito já atribuído a algum slot do pad) e gire o
knob físico — o app captura o CC automaticamente (MIDI learn). A lista mostra
todos os mapeamentos atuais, com opção de remover um por um ou limpar todos.
Trocar/esvaziar um slot de efeito remove automaticamente qualquer knob que
apontava pra um parâmetro dele.

## Estrutura

```
backend/         App FastAPI (API + WebSocket), storage SQLite, geração de .sfz
backend/engine/  Orquestrador do motor de áudio: sfizz por pad, mod-host,
                 catálogo de efeitos, registro de parâmetros de knob,
                 step sequencer, metrônomo, looper, grafo JACK
frontend/        UI web estática (pads, sons, volumes, efeitos, sequencer,
                 metrônomo, looper, knobs, config)
deploy/          systemd units e script de instalação
```
