# DiakoPad

Versão: 0.1.0<br>
Desenvolvedor: Lucas Souza (AntunesLS)

DiakoPad é uma estação de drum pads para apresentações ao vivo em um laptop
com Ubuntu Studio. O aplicativo conecta o controlador M-Vave SMC-PAD ao motor
de áudio PipeWire/JACK, dispara até 16 samples por MIDI e oferece sequencer,
metrônomo, looper MIDI, efeitos LV2, cenas e controles de palco em uma única
interface web local.

O laptop executa o motor de áudio e a interface. Use um navegador no próprio
equipamento ou, com o acesso pela rede habilitado (veja
[Controle por tablet ou celular](#controle-por-tablet-ou-celular)), outro
dispositivo conectado à mesma rede para controlar o set.

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

- Laptop com Ubuntu Studio ou outra distribuição Debian/Ubuntu compatível,
  **ou** Raspberry Pi 4/5 com Raspberry Pi OS Lite 64-bit, sem monitor (veja
  [Raspberry Pi sem monitor](#raspberry-pi-sem-monitor)).
- PipeWire com compatibilidade JACK ou jackd2 em execução na sessão do usuário.
- Controlador M-Vave SMC-PAD por USB ou Bluetooth.
- Acesso a `sudo` durante a instalação das dependências e compilação do motor.

## Instalação no Ubuntu Studio

No checkout do projeto, execute:

```bash
bash deploy/install-ubuntu-studio.sh
```

O instalador cria o ambiente Python, instala as dependências, compila
`sfizz_jack` e `mod-host` quando necessário, registra `diakopad.service` como
serviço `systemd --user`, configura `http://localhost:8080` para abrir
automaticamente no Firefox ao entrar na sessão gráfica (autostart KDE) e fixa
o clock do PipeWire em 44100 Hz / 128 frames (baixa latência, sem
renegociação durante o show).

Antes de iniciar o aplicativo, confirme que o servidor de áudio da sessão está
ativo. No Ubuntu Studio, use Ubuntu Studio Controls, QjackCtl ou PipeWire. Se o
PipeWire já estava rodando antes da instalação, reinicie-o para aplicar o
clock fixado em 44100 Hz / 128 frames:

```bash
systemctl --user restart pipewire.service pipewire-pulse.service wireplumber.service
pw-metadata -n settings | grep clock   # confirma rate=44100 quantum=128
jack_lsp
systemctl --user enable --now diakopad.service
systemctl --user status diakopad.service
```

Abra `http://localhost:8080/` no navegador. Para iniciar o serviço mesmo sem
login gráfico, opcionalmente execute:

```bash
loginctl enable-linger "$USER"
```

### Ligar e cair direto no DiakoPad

Para o laptop funcionar como um aparelho (liga e já está no DiakoPad em tela
cheia), execute depois do instalador:

```bash
bash deploy/setup-autoboot.sh          # login automático + serviço + sessão limpa
bash deploy/setup-autoboot.sh --grub   # também esconde o menu do GRUB (0 s)
```

O script configura o login automático no SDDM, faz o KDE iniciar sem
restaurar janelas da sessão anterior e habilita `diakopad.service`. No login,
`diakopad-open-browser.sh` espera o backend responder e abre o Firefox em modo
quiosque, com perfil próprio, sem telas de primeira execução. Enquanto a
janela estiver aberta, apagar a tela, bloquear e suspender ficam inibidos. Se
o Firefox travar, ele é reaberto.

- **Alt+F4** sai do modo quiosque.
- Para uma janela normal, edite `~/.config/autostart/diakopad-browser.desktop`
  e use `Exec=env DIAKOPAD_KIOSK=0 /home/<usuário>/.local/bin/diakopad-open-browser.sh`.
- Para voltar à tela de senha: `bash deploy/setup-autoboot.sh --disable`.
- Se aparecer o pedido de senha do KWallet após o login automático, marque a
  rede Wi-Fi como "Todos os usuários podem se conectar a esta rede".

## Raspberry Pi sem monitor

O mesmo instalador também prepara um Raspberry Pi 4/5 como "caixa" de som:
sem monitor, com o SMC-PAD por **USB** e a interface aberta pelo navegador de
outro aparelho (celular, tablet ou notebook) na mesma rede.

Requisitos:

- Raspberry Pi OS **Lite 64-bit**. No Raspberry Pi Imager, já defina hostname,
  usuário e SSH.
- Placa DAC I2S **PCM5122** (HAT), padrão do instalador, ou uma interface de
  áudio USB. A saída P2/HDMI do Pi não serve para palco.
- Fonte oficial e dissipador ou cooler. De preferência, rede cabeada.

Pelo SSH, no checkout do projeto:

```bash
bash deploy/install-ubuntu-studio.sh   # detecta o Pi sem monitor sozinho
systemctl --user enable diakopad.service
sudo reboot
```

No Pi, o instalador:

- instala PipeWire + WirePlumber e direciona `libjack` para o PipeWire;
- ativa o `linger`, para PipeWire e DiakoPad subirem no boot sem ninguém logar;
- carrega `snd-seq`/`snd-usb-audio` no boot e desliga o autosuspend USB, para
  o SMC-PAD não "dormir" e perder notas;
- ativa a DAC PCM5122 no `config.txt` (`dtoverlay=hifiberry-dacplus`, em um
  bloco "DiakoPad audio", com backup em `config.txt.diakopad-bak`) e desliga
  o áudio da placa e do HDMI, deixando a DAC como única saída;
- fixa a CPU em `performance` e o PipeWire em 44100 Hz / **256 frames**
  (~5,8 ms), porque o Pi 4 não segura 128 frames com 16 pads e efeitos.

Como a interface só é acessível pela rede, o instalador também faz o serviço
escutar em `0.0.0.0` e gera um `DIAKOPAD_TOKEN`, guardado em
`~/.config/diakopad/token` e mantido nas reinstalações. Ele não instala
navegador nem autostart. Para forçar um alvo, use
`DIAKOPAD_TARGET=laptop` ou `DIAKOPAD_TARGET=pi-headless`. Para outro buffer,
use `DIAKOPAD_QUANTUM=128`.

Se `aplay -l` não mostrar a DAC depois do reboot, a sua placa pode precisar de
outro overlay. Reinstale com `DIAKOPAD_PI_DAC_OVERLAY=<overlay>` e tente, nesta
ordem, `hifiberry-dacplus-std` (placa sem cristal próprio), `iqaudio-dacplus`
ou `allo-boss-dac-pcm512x-audio`, conforme o fabricante. Com interface USB, use
`DIAKOPAD_PI_DAC_OVERLAY=none`.

A PCM5122 tem volume digital próprio. Deixe-o em 0 dB (sem corte e sem
distorção) e controle o nível pelo Master do DiakoPad:

```bash
amixer -c sndrpihifiberry sset Digital 0dB
sudo alsactl store
```

Depois do reboot, sem logar no Pi, confira pelo SSH:

```bash
systemctl --user status pipewire wireplumber diakopad
pw-metadata -n settings | grep clock   # rate=44100 quantum=256
aplay -l                               # a DAC (sndrpihifiberry) aparece?
wpctl status                           # a DAC é o sink padrão?
wpctl set-default <id>                 # se não for
jack_lsp | grep -i -E "sinco|smc-pad"  # porta Midi-Bridge do SMC-PAD
pw-top                                 # xruns durante o teste de carga
```

No outro aparelho, abra uma vez
`http://<hostname>.local:8080/?token=<token>`, com o token mostrado no fim da
instalação (`cat ~/.config/diakopad/token`). Ele vira cookie, e depois basta
`http://<hostname>.local:8080/`. O SMC-PAD pode ser
plugado e desplugado com o sistema rodando: o DiakoPad reconecta a porta MIDI
em até 3 s. No Pi 4, evite usar reverbs Dragonfly em muitos pads ao mesmo
tempo. Se usar Wi-Fi, desligue a economia de energia
(`sudo iw dev wlan0 set power_save off`).

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

### Controle por tablet ou celular

Um aplicativo **Android nativo experimental** está em desenvolvimento em
`android/`. Ele usa Kotlin/Jetpack Compose (sem WebView) e atualmente oferece
conexão por IP privado ou nome `.local`, disparo de pads, atribuição de sons
existentes, montagem/carregamento de kits com sons já instalados, mix básico,
criação/carregamento/ativação/exclusão de cenas, envios e parâmetros de efeitos,
BPM, grade do sequencer/padrões, metrônomo, quatro trilhas do looper, ajustes
do master e PANIC.
**Ainda não substitui a interface web:** mapa MIDI, aprendizagem de knobs,
algumas opções de efeitos/configuração e validação num aparelho real estão
pendentes. Upload/importação
de arquivos e gerenciamento de pastas ficam intencionalmente na interface web.
O plano de paridade
está em `docs/plans/2026-11-02-native-android-ci.md`.

Para compilar um APK de testes, use `cd android && bash gradlew
:app:assembleDebug` (ou `gradlew.bat :app:assembleDebug` no Windows); o arquivo
gerado é `android/app/build/outputs/apk/debug/app-debug.apk`. Ele é assinado
com uma chave de desenvolvimento, não serve como release.

**Parear o celular.** Com o servidor escutando na rede (`DIAKOPAD_HOST=0.0.0.0`)
e o pacote `avahi-utils` instalado (o instalador já instala), o DiakoPad se
anuncia no Wi-Fi como `_diakopad._tcp`. No app, toque no DiakoPad da lista
“Encontrados nesta rede”: a tela do laptop (ou qualquer tela já autorizada)
mostra um código de 6 dígitos, válido por 2 minutos, que você digita no
celular. O celular recebe um acesso próprio, guardado cifrado no Android
Keystore, e entra direto nas próximas vezes. Os aparelhos pareados aparecem em
**Config ▸ Celulares pareados**, onde podem ser removidos sem trocar o
`DIAKOPAD_TOKEN`. Para nomear o servidor na lista, defina `DIAKOPAD_NAME` no
serviço.

Se o DiakoPad não aparecer (rede que bloqueia mDNS, por exemplo), use “Digitar
endereço e token” com `IP:8080` e o `DIAKOPAD_TOKEN`. A rede HTTP local não é
criptografada: não use Wi-Fi desconhecido nem exponha a porta 8080 à internet.

#### Publicar o APK no GitHub Releases

O workflow `.github/workflows/release-android.yml` publica um **APK assinado**
ao receber uma tag `vMAJOR.MINOR.PATCH` apontando para um commit já presente na
`main` (ex.: `v0.1.0`). Antes da primeira publicação, crie uma keystore privada
com `keytool -genkeypair -keystore diakopad-release.jks -alias diakopad \
-keyalg RSA -keysize 3072 -validity 10000` **fora do repositório** e configure
em *Settings → Secrets and variables → Actions* do GitHub os quatro secrets:

| Secret | Valor |
| --- | --- |
| `DIAKOPAD_KEYSTORE_BASE64` | Conteúdo binário da keystore codificado em Base64, sem quebras de linha. |
| `DIAKOPAD_KEYSTORE_PASSWORD` | Senha da keystore. |
| `DIAKOPAD_KEY_ALIAS` | Alias informado ao `keytool` (`diakopad` no exemplo). |
| `DIAKOPAD_KEY_PASSWORD` | Senha da chave privada. |

No PowerShell, obtenha o Base64 com
`[Convert]::ToBase64String([IO.File]::ReadAllBytes('C:\caminho-privado\diakopad-release.jks'))`
e cole diretamente no secret, sem salvar a saída no projeto. **Faça backup da
keystore e das senhas**: versões futuras precisam da mesma chave para serem
instaladas como atualização. O APK debug tem outra assinatura e precisa ser
desinstalado antes de instalar a primeira release.

Depois que o código estiver na `main` e os secrets configurados, crie e envie
uma tag nova (`git tag v0.1.0` e `git push origin v0.1.0`). O workflow executa
testes, calcula `versionCode` crescente a partir da tag, monta o APK, confere
assinatura e versão e só então cria o GitHub Release com
`DiakoPad-Android-0.1.0.apk`. Se faltar algum secret ou a build falhar, não há
publicação. Tags devem ter componentes minor/patch inferiores a 100; não
reutilize uma tag existente.

Por padrão o serviço escuta apenas em `127.0.0.1`: só o próprio laptop acessa
a interface. Para controlar o set de outro aparelho na mesma rede, edite
`~/.config/systemd/user/diakopad.service`:

```ini
Environment=DIAKOPAD_HOST=0.0.0.0
Environment=DIAKOPAD_TOKEN=um-segredo-longo
```

Execute `systemctl --user daemon-reload && systemctl --user restart
diakopad.service` e, no outro aparelho, abra uma vez
`http://<ip-do-laptop>:8080/?token=um-segredo-longo`. O token fica guardado em
cookie, e a URL sem o token passa a funcionar. Requisições sem o cookie recebem
401; o navegador do próprio laptop nunca é bloqueado. Não exponha a porta sem
token: qualquer pessoa no Wi-Fi do local poderia apagar samples ou disparar
PANIC.

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
aba **Volumes** controla volume e panorama.

Na aba **Efeitos**, os **efeitos compartilhados** (Reverb Hall, Reverb Plate e
um Delay que segue o BPM, inclusive o Tap Tempo) servem a todos os pads: cada
pad regula quanto envia para cada um (0-100%). Eles ficam sempre carregados,
então são a forma recomendada de dar ambiência: leves, e trocar de cena só muda
números. Abaixo deles ficam o filtro de tom e até três **efeitos só daquele
pad** (slots LV2: pitch, EQ, drive, chorus...). Use poucos slots por cena:
criar e remover plugins na troca de cena mexe no grafo de áudio e pode estalar.
O catálogo foi validado no Ubuntu Studio com Dragonfly, ZamVerb, LSP, mda e
x42.

O controle **Master** inclui limiter estéreo LSP e ganho de saída. Segure
**PANIC** por aproximadamente 0,65 s para parar os transportes, enviar
all-notes-off e mutar a saída.

### Cenas

Uma cena armazena a atribuição dos pads, mixagem, efeitos (envios e ajustes
dos efeitos compartilhados inclusive), mapeamentos de knob, BPM, metrônomo e
grid do sequencer. As notas MIDI dos pads e o master não fazem parte da cena.

Cada pad mantém na memória todos os sons que ele usa em todas as cenas salvas,
então trocar de cena é instantâneo (cerca de 0,1 s), sem recarregar nada, e um
prato que ainda está soando continua até o fim. Um som que ainda não está na
memória (por exemplo, trocado pelo navegador de kits) é preparado em segundo
plano enquanto o som atual continua tocando, sem silêncio na troca.

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

## Controle por agente de IA (MCP)

`mcp_server/diakopad_mcp.py` é um servidor [MCP](https://modelcontextprotocol.io)
que permite a um agente (Claude Code, Claude Desktop ou outro cliente MCP)
operar o DiakoPad: criar e carregar kits, enviar sons, ajustar volume, pan,
filtro e efeitos dos pads, salvar e carregar cenas, programar o sequencer,
controlar looper, metrônomo e master, gerenciar mapeamentos MIDI e reiniciar o
motor. Ele roda como processo `stdio` separado e só usa a API REST, então pode
ficar no próprio laptop/Pi ou em outra máquina da rede.

```bash
python3 -m venv mcp_server/.venv
mcp_server/.venv/bin/pip install -r mcp_server/requirements.txt
```

Registro no Claude Code (troque a URL e o token se o DiakoPad estiver em
outra máquina; no próprio laptop, o token não é necessário):

```bash
claude mcp add diakopad \
  --env DIAKOPAD_URL=http://diakopad.local:8080 \
  --env DIAKOPAD_TOKEN="$(cat ~/.config/diakopad/token)" \
  -- "$PWD/mcp_server/.venv/bin/python" "$PWD/mcp_server/diakopad_mcp.py"
```

No Claude Desktop, use o mesmo comando, os mesmos argumentos e as mesmas
variáveis em `mcpServers` do `claude_desktop_config.json`.

Ações que apagam dados ou cortam o som (`delete_*`, `clear_*`, `panic`,
`restart_engine`, `restart_system`) são marcadas como destrutivas, para o
cliente pedir confirmação. As mudanças valem na hora no áudio. Durante o show,
prefira não deixar o agente operar o set sem supervisão. Sons e packs podem
vir de um caminho local da máquina do MCP ou de uma URL. Os learns MIDI
(`pad_note_learn`, `knob_learn`, `controller_action_learn`) dependem de
alguém tocar o controlador.

Testes: `cd mcp_server && python -m unittest discover tests`, num ambiente com
`mcp_server/requirements.txt` e `backend/requirements.txt` instalados.

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

## Backups e recuperação

A cada início do serviço, o banco é copiado para
`backend/backups/diakopad-AAAAMMDD-HHMMSS.db` (as 10 cópias mais recentes são
mantidas). Para voltar a uma cópia:

```bash
systemctl --user stop diakopad.service
cp backend/backups/diakopad-AAAAMMDD-HHMMSS.db backend/diakopad.db
rm -f backend/diakopad.db-wal backend/diakopad.db-shm
systemctl --user start diakopad.service
```

Os samples (`backend/samples/`) não entram no backup automático; copie essa
pasta para um pendrive antes de cada turnê.

### Recuperação de pads

Se o banco SQLite e os backups forem perdidos, recupere as atribuições de pads a partir dos
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
mcp_server/      Servidor MCP para controle do DiakoPad por agentes de IA
```
