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

Na tela **Pads**, toque para disparar o som e segure o pad para abrir a troca
de sample. Os hits físicos e os disparos pela tela recebem feedback visual;
o topbar mantém BPM/Tap, transporte do Sequencer, master, status e PANIC
acessíveis durante a execução.


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

### Deploy rápido (dev → Pi, sem reinstalar)

Para enviar só o código durante o desenvolvimento, use
`bash deploy/sync-to-pi.sh` (rsync, assume o alias `diakopad-pi` do
`~/.ssh/config`). **Nunca** copie `backend/diakopad.db` nem
`backend/samples/` por cima do Pi: o banco guarda as atribuições dos pads, o
catálogo de samples, os efeitos e os knobs. No Windows, o equivalente é:

```powershell
tar -czf "$env:TEMP\diakopad.tgz" --exclude=.git --exclude=.venv `
  --exclude=__pycache__ --exclude=diakopad.db --exclude=samples `
  --exclude=sfz_bank --exclude=.pytest_cache .
scp "$env:TEMP\diakopad.tgz" diakopad-pi:/tmp/ ; ssh diakopad-pi `
  "tar -xzf /tmp/diakopad.tgz -C ~/diakopad && sudo systemctl restart diakopad.service"
```

Se o banco for sobrescrito por engano, `deploy/recover_pads_from_sfz.py`
reconstrói o catálogo de samples e as atribuições (sample, volume, pan,
nota) a partir dos arquivos de sample e dos `padNN.sfz` gerados pelo motor —
efeitos, knobs e sequencer não são recuperáveis. Em outra máquina (ex. o
deploy de laptop abaixo), aponte-o para o bank certo com
`DIAKOPAD_SFZ_BANK_DIR=... python3 deploy/recover_pads_from_sfz.py`.

## Deploy num laptop (Ubuntu Studio, sem Zynthian)

Mesmo motor de áudio (um `sfizz_jack` por pad + `mod-host` para efeitos),
rodando nativo num desktop Linux em vez da imagem do Zynthian — útil pra
não depender do Pi (que satura ~1 núcleo de CPU por pad carregado, ver
"Performance e Kits" abaixo) ou pra desenvolver com áudio real sem estar
perto do hardware.

1. Copie o repositório para o laptop (ou rode em-place num checkout já
   local) e execute `bash deploy/install-ubuntu-studio.sh`. Ele instala as
   dependências via apt, compila `sfizz_jack` e `mod-host` a partir do
   código-fonte (não costumam vir empacotados) e instala um serviço
   `systemd --user` (`deploy/diakopad-desktop.service`).
2. Diferenças-chave em relação ao Pi:
   - **Sem root**: o app roda como o seu próprio usuário — no Pi o
     `jackd` é um serviço de sistema rodando como root e a memória
     compartilhada do JACK é isolada por UID (por isso
     `deploy/diakopad-runtime.conf` força `User=root` lá); num laptop o
     JACK/PipeWire já roda na sua própria sessão, então rodar como root
     quebraria a conexão em vez de consertar. **Não reaproveite**
     `diakopad-runtime.conf` aqui.
   - **Sem kiosk**: pule `diakopad-kiosk.service`/`kiosk-xinitrc`, é
     tela de toque específica do Pi — no laptop é só abrir
     `http://localhost:8080/` no navegador.
    - **Catálogo de efeitos já validado pra este alvo**: os defaults de
      `backend/engine/effects_catalog.py` (Dragonfly Hall/Plate, ZamVerb,
      LSP chorus/flanger/phaser, mda delay/compressor/drive, x42 fil4) e o
      master gain (x42 balance) do `diakopad-desktop.service` foram
      validados no-device (set/2026) — nenhum `DIAKOPAD_FX_*` extra é
      preciso; sobrescrevas só se a sua instalação divergir
      (`lv2ls`/`lv2info`).
3. Garanta que um servidor compatível com JACK já esteja rodando na sua
   sessão (jackd2 via QjackCtl/Ubuntu Studio Controls, ou pipewire-jack)
   antes de iniciar o serviço — o unit não gerencia isso.
4. `systemctl --user enable --now diakopad.service`

O botão **Config > Reiniciar DiakoPad e áudio** reinicia o serviço do
DiakoPad, PipeWire e WirePlumber para recuperar o grafo de áudio após uma
troca de dispositivo. O `install-ubuntu-studio.sh` instala a unidade auxiliar
necessária; em um deploy já existente, copie `deploy/diakopad-restart.service`
para `~/.config/systemd/user/` e execute `systemctl --user daemon-reload`.

### Achados da validação em hardware (set/2026, Ubuntu Studio + PipeWire)

* O `sfizz_jack` do repositório upstream (`sfztools/sfizz`) sobe uma thread
  de CLI interativa que bloqueia em `std::getline(std::cin, ...)` esperando
  comandos. Sem terminal (systemd/SSH), o stdin já nasce em EOF, a thread
  fecha o cliente na hora, e o loop principal (que só checa isso a cada 1s)
  mata o processo ~1s após cada spawn — o *watchdog* reinicia em loop,
  parecendo áudio "fora de sincronia" quando na verdade é o instrumento
  reiniciando sem parar. `install-ubuntu-studio.sh` já aplica o patch
  (remove essa thread antes de compilar); o build do Zynthian no Pi não tem
  esse problema, então só aparece nesse build a partir do fonte.
* Pra recompilar um `sfizz_jack` já em uso (ex.: reinstalar por cima), pare
  o `diakopad.service` antes — sobrescrever o binário rodando dá
  `Text file busy`.
* `HARDWARE_MIDI_PATTERN` (`backend/engine/orchestrator.py`) tem como padrão
  `system:midi_capture_.*`, que só existe num setup jackd2/a2jmidid puro. Sob
  PipeWire, o SMC-PAD aparece como `Midi-Bridge:SINCO SMC-PAD-Master
  (capture)` quando conectado por USB (confirme com `jack_lsp -p | grep -i
  sinco`) — sem sobrescrever `DIAKOPAD_HARDWARE_MIDI_PATTERN` (ver
  `deploy/diakopad-desktop.service`), o hardware físico nunca chega em
  `DiakoPad-in`: knob-learn, gravação do looper e o aprendizado de ações de
  controlador (aba Config) ficam surdos ao controlador, mesmo com os pads
  soando normalmente pela tela.
* **Pareado por Bluetooth**, o mesmo SMC-PAD aparece com um nome de porta
  diferente: `Midi-Bridge:SMC-PAD Bluetooth (capture)` (confirme com
  `jack_lsp -p | grep -i -E "sinco|smc-pad"` ou `pw-link -l | grep -i smc`).
  O `DIAKOPAD_HARDWARE_MIDI_PATTERN` do `deploy/diakopad-desktop.service` já
  cobre os dois modos (`Midi-Bridge:(SINCO|SMC-PAD Bluetooth).*`), então
  funciona com o controlador cabeado ou pareado, inclusive trocando de um
  pro outro sem reiniciar o serviço (a reconexão dinâmica do orchestrator
  já cobre isso a cada poucos segundos). PipeWire também expõe o mesmo
  fluxo Bluetooth sob outros nomes (`bluez_midi.server`,
  `bluez_midi.<endereço>`, `SMC-PAD:out`, `BLE MIDI 1:out`) — **não**
  adicione esses ao padrão: são representações duplicadas do mesmo stream,
  e conectar mais de uma faz cada pad disparar mais de uma vez por toque.
* **Pareado não é conectado**: um periférico BLE (caso do SMC-PAD por
  Bluetooth) derruba a própria conexão sozinho depois de um tempo parado,
  mesmo já pareado/confiável/*bonded* (`bluetoothctl info <endereço>`
  mostra `Paired: yes` mas `Connected: no`). Quando isso acontece, a porta
  `Midi-Bridge:SMC-PAD Bluetooth` continua aparecendo no grafo do JACK
  (parece "conectada" ali), mas nenhum byte de MIDI passa mais — os pads
  simplesmente param de reagir ao toque físico, sem nenhum erro no log do
  DiakoPad (ele só vê o grafo JACK/PipeWire, nunca a pilha Bluetooth em
  si, então não detecta nem conserta isso sozinho). `deploy/
  diakopad-bt-watchdog.sh` + `deploy/diakopad-bt-watchdog.service` existem
  pra isso: um serviço `systemd --user` opcional que fica checando a
  conexão a cada poucos segundos e reconecta (`bluetoothctl connect`)
  quando cair. Só instale se for usar o SMC-PAD por Bluetooth (o cabeado
  não precisa) — `install-ubuntu-studio.sh` não instala isso sozinho
  porque precisa do endereço MAC do seu dispositivo:
  ```bash
  bluetoothctl devices   # ache o endereço do SMC-PAD (deve já estar pareado/trusted)
  sed -e "s#__INSTALL_DIR__#$HOME/diakopad#g" -e "s#__BT_MAC__#AA:BB:CC:DD:EE:FF#g" \
    deploy/diakopad-bt-watchdog.service > ~/.config/systemd/user/diakopad-bt-watchdog.service
  systemctl --user daemon-reload
  systemctl --user enable --now diakopad-bt-watchdog.service
  ```

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
catálogo curado (Reverb Dragonfly Hall, Reverb Plate, Reverb IR ZamVerb,
Chorus/Flanger/Phaser LSP, Delay, Compressor, Overdrive, EQ 3 bandas, ver
`backend/engine/effects_catalog.py`) e ajusta os parâmetros daquele plugin.
Trocar o plugin de um slot recria a cadeia no mod-host (`sfizz → slot 1 →
slot 2 → slot 3 → master`); mudar só um parâmetro é um `param_set` barato,
sem reiniciar nada. Params salvos de uma versão anterior do catálogo (ex.:
kits gravados com o mda/Ambience) são filtrados no carregamento — símbolos
que não pertencem ao plugin atual caem fora e os defaults completam.

Os defaults do catálogo foram validados no-device no deploy de laptop
(Ubuntu Studio, set/2026): Reverb = Dragonfly Hall, Reverb Plate = Dragonfly
Plate (pacote apt `dragonfly-reverb`), Reverb IR = ZamVerb — convolução com
IRs embutidos, selecionáveis pelo param "Sala" (pacote `zam-plugins`) —
Chorus/Flanger/Phaser = LSP stereo (`lsp-plugins`; portas e faixas reais,
controles em unidades físicas) e Delay/Compressor/Overdrive = mda (portas
normalizadas 0..1, `mda-lv2`) e EQ 3 bandas = x42 fil4 stereo (±18 dB).
No Pi (imagem bookworm) esses plugins não existem: `deploy/diakopad.service`
pinna o reverb de volta ao mda/Ambience via `DIAKOPAD_FX_REVERB_LV2_URI`, e
os slots novos simplesmente ficam vazios lá — e o drop-in
`deploy/diakopad-master.conf` continua exportando o `LV2_PATH` com os três
diretórios de plugins da imagem (sem ele o mod-host só enxerga 10 plugins
de exemplo). Cada entrada continua
sobreponível por `DIAKOPAD_FX_<NAME>_LV2_URI`.

### Master e Segurança De Palco

O topbar oferece volume **Master**, mute, status do motor e **PANIC** (segure
o botão por cerca de 0,65 s). PANIC para sequencer, metrônomo e looper, envia
all-notes-off e muta a saída master. Para cortar imediatamente samples
one-shot, configure um plugin LV2 de ganho estéreo em
`DIAKOPAD_FX_MASTER_GAIN_LV2_URI`, junto com seus símbolos/limites em dB
(`DIAKOPAD_FX_MASTER_GAIN_SYMBOL`, `_MIN`, `_MAX`) no serviço systemd.

Sem esse plugin, o app mantém o roteamento direto para a saída JACK e exibe o
motor como degradado. Nesse modo, PANIC encerra os players sfizz como fallback
para cortar one-shots e o watchdog os reconstrói em seguida.

No deploy de laptop, a aba **Master** também usa o `LSP Limiter Stereo`
(`http://lsp-plug.in/plugins/lv2/limiter_stereo`) antes do ganho master. O
limite inicial é -1 dB e protege a saída quando vários one-shots se sobrepõem;
o limiter pode ser desligado ou ajustado entre -12 dB e 0 dB. Se o plugin não
estiver disponível, o ganho/mute master continua funcionando sem limiter.

### Achados da validação em hardware (set/2026, imagem bookworm/kernel 6.12)

* **Master LV2**: use o plugin lvtk "Volume" (`http://lvtk.org/plugins/volume`,
  símbolo `volume`, estéreo, sem latência) — já configurado no drop-in
  `deploy/diakopad-master.conf`.
* **Usuário do serviço**: jackd roda como root e a memória compartilhada do
  JACK é escopada por UID, então o app roda como root via drop-in
  `deploy/diakopad-runtime.conf`.
* **mod-host**: o protocolo de controle exige comandos terminados em NUL
  (`\x00`), não `\n` — com `\n` o parser do servidor lê memória heap como
  comandos (respostas corrompidas e crashes). O binário também daemoniza
  (o pai sai com código 0): o cliente detecta vida via TCP e desliga via
  `quit`. Se um binário antigo corromper a memória, rebuild:
  `cd /zynthian/zynthian-sw/mod-host && sudo make && sudo make install`.
* **JACK-Client no venv**: se o log mostrar `No module named 'jack'`,
  rode o `pip install -r requirements.txt` do install.sh de novo.

O Sfizz em si nunca suportou reverb/delay internos (opcode `effect1`/
`<effect>` do SFZ v2 não implementado — testado com `sfizz_render` na versão
1.2.3, `Unsupported effect type: reverb`); é por isso que todo efeito é
hospedado via **mod-host** (LV2), o mesmo host de efeitos que o Zynthian usa
nativamente, e não via SFZ.

## Performance e Kits

Aba **Performance**: modo de palco com os 16 pads grandes e uma faixa de
kits no topo. Um **kit** é um snapshot nomeado de tudo que define o som do
set: a atribuição de cada pad, volume/pan/tom, as cadeias de efeito e os
mapeamentos de knob (trocar de kit também troca o layout dos knobs
físicos; kits gravados antes disso entram no snapshot deixam os mapeamentos
atuais como estão). Use ◀ ▶ para trocar de kit ao vivo (recarrega todos os
pads no motor), **Salvar** para gravar o estado atual sobre um nome e
**Excluir** para remover. O padrão do sequencer e as notas MIDI dos pads
**não** fazem parte do kit (as notas pertencem ao controlador físico).

Trocar de kit reaplica os 16 pads no motor (cada um regrava o `.sfz` e
reinicia sua instância sfizz), então leva alguns segundos — a grade fica em
estado "aplicando" durante a troca.

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
