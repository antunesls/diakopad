# DiakoPad

Versão: 0.1.0<br>
Desenvolvedor: Lucas Souza (AntunesLS)

DiakoPad é uma estação de drum pads para apresentações ao vivo em um laptop
com Ubuntu Studio. O aplicativo conecta o controlador M-Vave SMC-PAD ao motor
de áudio PipeWire/JACK, dispara até 16 samples por MIDI e oferece sequencer,
metrônomo, looper MIDI, efeitos LV2, cenas e controles de palco em uma única
interface web local.

O laptop executa o motor de áudio e a interface. Use um navegador no próprio
equipamento ou outro dispositivo conectado à mesma rede para controlar o set.

## Recursos para palco

- Grade de 16 pads com disparo por toque e pelo SMC-PAD.
- Biblioteca de samples, kits e atribuição de som por pad.
- Volume, pan, filtro e até três efeitos LV2 por pad.
- Sequencer de 16 passos e 16 pads.
- Metrônomo com BPM global, Tap Tempo, compassos e estilos de clique.
- Looper MIDI de quatro trilhas, com overdub, volume, mute e quantização por
  compasso opcional.
- Cenas para guardar e alternar configurações do set ao vivo.
- Mapa MIDI, MIDI learn para knobs e ações dedicadas do controlador.
- Master, limiter e botão PANIC para interromper sequencer, metrônomo, looper
  e notas MIDI ativas.

## Requisitos

- Laptop com Ubuntu Studio ou outra distribuição Debian/Ubuntu compatível.
- PipeWire com compatibilidade JACK ou jackd2 em execução na sessão do usuário.
- Controlador M-Vave SMC-PAD por USB ou Bluetooth.
- Acesso a `sudo` durante a instalação das dependências e compilação do motor.

## Instalação no Ubuntu Studio

No checkout do projeto, execute:

```bash
bash deploy/install-ubuntu-studio.sh
```

O instalador cria o ambiente Python, instala as dependências, compila
`sfizz_jack` e `mod-host` quando necessário e registra `diakopad.service` como
serviço `systemd --user`.

Antes de iniciar o aplicativo, confirme que o servidor de áudio da sessão está
ativo. No Ubuntu Studio, use Ubuntu Studio Controls, QjackCtl ou PipeWire:

```bash
jack_lsp
systemctl --user enable --now diakopad.service
systemctl --user status diakopad.service
```

Abra `http://localhost:8080/` no navegador. Para iniciar o serviço mesmo sem
login gráfico, opcionalmente execute:

```bash
loginctl enable-linger "$USER"
```

## Preparação para apresentação

1. Conecte o SMC-PAD por USB ou Bluetooth e confirme a porta MIDI:

```bash
jack_lsp -p | grep -i -E "sinco|smc-pad"
```

2. No CubeSuite, configure os 16 pads para enviar mensagens **Note** em um
canal MIDI fixo, como o canal 10.
3. Abra o DiakoPad, atribua samples e ajuste as notas dos pads no Mapa MIDI
para corresponder ao preset do controlador.
4. Teste o áudio, os efeitos, os níveis de master e o botão PANIC antes da
passagem de som.
5. Salve uma cena para cada música ou trecho do repertório. Marque como ativas
apenas as cenas que devem entrar na navegação ao vivo.

O serviço usa por padrão portas PipeWire `Midi-Bridge` do SMC-PAD. Se o nome
exibido em seu laptop for diferente, defina
`DIAKOPAD_HARDWARE_MIDI_PATTERN` no arquivo
`~/.config/systemd/user/diakopad.service`, execute
`systemctl --user daemon-reload` e reinicie o serviço.

### Bluetooth

O SMC-PAD pode desconectar após um período inativo mesmo estando pareado. Para
apresentações por Bluetooth, instale o watchdog opcional depois de descobrir o
endereço do dispositivo:

```bash
bluetoothctl devices
sed -e "s#__INSTALL_DIR__#$HOME/diakopad#g" -e "s#__BT_MAC__#AA:BB:CC:DD:EE:FF#g" \
  deploy/diakopad-bt-watchdog.service > ~/.config/systemd/user/diakopad-bt-watchdog.service
systemctl --user daemon-reload
systemctl --user enable --now diakopad-bt-watchdog.service
```

## Operação

### Pads, efeitos e master

Na aba **Pads**, toque para disparar e segure um pad para trocar o sample. A
aba **Volumes** controla volume e panorama; **Efeitos** oferece filtro e slots
LV2 por pad. O catálogo padrão foi validado no Ubuntu Studio com Dragonfly,
ZamVerb, LSP, mda e x42.

O controle **Master** inclui limiter estéreo LSP e ganho de saída. Segure
**PANIC** por aproximadamente 0,65 s para parar os transportes, enviar
all-notes-off e mutar a saída.

### Cenas

Uma cena armazena a atribuição dos pads, mixagem, efeitos, mapeamentos de
knob, BPM, metrônomo e grid do sequencer. As notas MIDI dos pads, o master,
recarregam os pads e podem levar alguns segundos; faça a mudança antes do
momento crítico da música.

### Sequencer, metrônomo e looper

O sequencer usa uma grade de 16 passos por pad. Metrônomo, sequencer e looper
compartilham BPM e um transport musical baseado em ticks.

O looper grava eventos MIDI, não áudio. Ele possui quatro trilhas, overdub,
demais gravam relativas a esse ciclo. A quantização por compasso pode ser
ativada em **Config**. Na mesma tela, o filtro de toque duplicado descarta
Note On repetidos do controlador dentro da janela configurada.

## Reinício e diagnóstico

O botão **Config > Reiniciar DiakoPad e áudio** reinicia DiakoPad, PipeWire e
WirePlumber para recuperar o grafo de áudio depois de uma troca de dispositivo.
Para diagnóstico pelo terminal:

```bash
systemctl --user status diakopad.service
journalctl --user -u diakopad.service -f
jack_lsp -c
```

Se não houver áudio, confirme primeiro que PipeWire/JACK está ativo, que o
SMC-PAD aparece no grafo e que `sfizz_jack` e `mod-host` estão disponíveis no
`PATH` do serviço.

## Desenvolvimento local

Para trabalhar na interface sem o motor de áudio:

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app:app --reload --port 8080
```

Abra `http://localhost:8080/`. Sem JACK, `sfizz_jack` ou `mod-host`, a
interface, o banco SQLite e a geração de `.sfz` continuam funcionando; o motor
de áudio registra a indisponibilidade no log.

## Atualização por Git

Em uma máquina de desenvolvimento, envie a versão para o laptop configurado
como `diakopad-laptop` no SSH:

```bash
bash deploy/git-deploy.sh
```

O comando executa `git fetch` e `git reset --hard` no destino, descartando
alterações não commitadas do checkout remoto. Os dados de execução, como
`backend/diakopad.db`, `backend/samples/` e `backend/sfz_bank/`, não são
versionados e não são removidos pelo reset.

## Recuperação de pads

Se o banco SQLite for perdido, recupere as atribuições de pads a partir dos
samples e arquivos `.sfz`:

```bash
systemctl --user stop diakopad.service
backend/.venv/bin/python deploy/recover_pads_from_sfz.py
systemctl --user start diakopad.service
```

O processo restaura samples, atribuições, notas, volume, pan e filtro. Efeitos,
knobs, sequencer e configurações gerais não podem ser reconstruídos.

## Estrutura

```text
backend/         FastAPI, WebSocket, SQLite e geração de arquivos .sfz
backend/engine/  MIDI, JACK/PipeWire, sfizz, mod-host, efeitos e transportes
frontend/        Interface web para operação e configuração ao vivo
deploy/          Instalação Ubuntu Studio e serviços systemd --user
```
