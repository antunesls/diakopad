# SMC-PAD: protocolo de configuração (engenharia reversa do Midi Suite)

Fonte: análise estática do `MidiSuite.exe` antigo (Qt5, PE x86, SHA-256
`8f2b8addaf3ad1171e2f1baece38dc213dfc46950e9fba8c56664718ea06a9c3`) com o REA/Ghidra,
mais os assets do Midi Suite novo (Flutter). Nada foi executado nem capturado do
aparelho: tudo abaixo é leitura de código e de arquivos. **Observado** = visto no
código/arquivo. **Inferido** = interpretação.

## Quadro (frame) lógico — observado

Requisição de leitura de memória (`FUN_004fdd50`, 15 bytes):

| Offset | Tam. | Conteúdo |
|---|---|---|
| 0 | 2 | cabeçalho: constante `DAT_0111a420` = `00 59` (valor inicial no .data; pode mudar em runtime — não verificado) |
| 2 | 1 | `0x23` (`#`), tipo do quadro |
| 3 | 3 | tamanho do payload = 8 (little-endian) |
| 6 | 1 | id da região de memória |
| 7 | 4 | endereço (little-endian) |
| 11 | 3 | quantidade de bytes (little-endian) |
| 14 | 1 | checksum = `~(soma dos bytes 6..13) & 0xFF` |

Resposta (`FUN_004fdfe0`): mesmo cabeçalho/`#`/tamanho; o payload repete região,
endereço e quantidade, depois vêm os dados; o último byte é o checksum
(`FUN_004fde90`). O app confere que região, endereço e tamanho batem com o pedido.
Tamanho do quadro = dados + 15. Máximo de dados por bloco = `DAT_0111a418 - 15`
(`DAT_0111a418` = 0x400, logo 1009 bytes).

## Transporte — observado

`FUN_00468870` (recepção): espera `F0`, lê até `F7`. Com o modo (`+0xc814`) ligado,
cada byte MIDI carrega **7 bits** que são reagrupados em bytes de 8 bits (fluxo de
bits, primeiro bit menos significativo). O envio (`FUN_00468b70` → `FUN_00468a00`)
é o inverso; **não foi lido ainda**, então a ordem exata dos bits no envio é
inferida como simétrica.

Comando simples `0x11` (`FUN_004fd800`): devolve 0x18 bytes de texto e uma segunda
faixa de dígitos — aparenta ser identificação do aparelho (inferido).

## Formato dos arquivos `.bin` (SPB.bin = padrão de fábrica do SMC-PAD) — observado

Registros terminados em `0xFF`:
- Botões: `02 00 CC 00 7F 06 F0 7F 7F 06 NN F7 …` com CC25–29 (MMC 01–05).
- Knobs: `02 00 CC 00 7F` com CC30–45 (8 knobs × 2 bancos).
- Pads: `09 NN 00 7F <3 bytes> F0 FF …`, 64 entradas (4 bancos × 16); os 3 bytes
  (`96 C8 F0`, `78 78 F0`…) parecem cor (inferido).

## Confirmado no aparelho (SMC-PAD por USB no Windows, porta `SMC-PAD`, winmm)

- Comando simples (7 bytes): `00 59 <cmd> 00 00 00 FF`, empacotado em 7 bits entre
  `F0` e `F7`. Com `cmd = 0x11` o aparelho respondeu um quadro de 34 bytes, checksum
  válido, payload de 27 bytes começando com o texto `SMC-PAD_004`.
- Resposta de erro observada: quadro de 8 bytes `00 59 00 01 00 00 01 FE`
  (cmd `0x00`, payload `01`). Recebida para pedidos de leitura com regiões 0x01, 0x07,
  0x09, 0x0A, 0x0D, 0x12, 0x13, 0x19, 0x1B (endereço 0, 32 bytes); as demais regiões
  de 0x00 a 0x1F não responderam em 400 ms. Nenhuma leitura de dados teve sucesso ainda.
- As portas `MIDIIN2/MIDIOUT2` e `MIDIIN3/MIDIOUT3` não responderam ao comando `0x11`.
- O cabeçalho `00 59` é constante: as 4 referências a ele no executável só leem.

## Leitura completa — capturada do Midi Suite antigo (Frida em `midiOutLongMsg`/callback de entrada)

O app, ao conectar, faz exatamente (todos com checksum válido, 111 respostas, 0 erros):

1. `cmd 0x11` → identificação (`SMC-PAD_004`).
2. Leitura região `0x04`, endereço 0, 12 bytes → `78 00 32 04 00 00 02 00 01 01 00 00`
   (significado não decifrado). **Repete a cada ~1 s** enquanto o app está aberto.
3. Leitura região `0x05`, endereço 0, em blocos de 1009 bytes (`0x3f1`) até 28.312 bytes
   (último bloco de 60 bytes em `0x6e5c`). Cada requisição tem o formato de leitura
   acima; a resposta tem `cmd 0x23` e payload = região, endereço, tamanho, dados.
4. Resultado: o bloco da região 5 do aparelho de fábrica é **idêntico** ao `SPB.bin`
   do Midi Suite (28.312 bytes). Ou seja, `SPB.bin` = memória de configuração do SMC-PAD.

Obs.: minha varredura anterior pedia 32 bytes às regiões 0–31 e não obteve dados;
o app pede 12 bytes à região 4 e 1009 à região 5. Replicar exatamente esses pedidos.

## Gravação — capturada ao salvar a nota do pad 1 no Midi Suite

Comando `0x22` (mesmo quadro: `00 59 22 <len3> <payload> <chk>`), payload =
região(1) + endereço(4, LE) + tamanho(3, LE) + dados. Resposta: `cmd 0x00`, payload
de 1 byte, `00` = OK (`00 59 00 01 00 00 00 FF`) e `01` = erro.

Sequência observada:
1. Escrita região `0x04`, endereço `0x0a`, 1 byte `01`, depois `00` (flag de edição;
   função não confirmada).
2. Escrita região `0x05`, endereço `0x415`, 1 byte `0x25` → nota do pad 1 de 36 (`0x24`)
   para 37 (`0x25`). O registro de pad é `09 <nota> 00 7f <3 bytes> ff …` (26 bytes, a partir
   de `0xd4`), logo `<nota>` fica no byte 1 do registro.
3. Escrita região `0x05`, endereço 0, **tamanho 0** (`… 22 08 00 00 05 00 00 00 00 00 00 00 fa`)
   → confirma/grava; a resposta OK demora ~0,25 s (gravação em flash, inferido).

## Mapa de campos (tela do Midi Suite × bytes) — 1ª coluna observada na tela, 2ª nos bytes

Pad 1 na tela: Type=Note, Channel=10, Note=36, MinVel=0, MaxVel=127, Color=magenta, Led=255.
Registro de pad no bloco 5 (`09 24 00 7f f0 00 f0 ff`):

| Byte | Valor | Campo | Confiança |
|---|---|---|---|
| 0 | `09` | canal (0-based: 9 = Channel 10) | alta (bate com a tela) |
| 1 | `24` | nota (36) | confirmado (escrita capturada em `0x415`) |
| 2 | `00` | MinVel | alta |
| 3 | `7f` | MaxVel | alta |
| 4–6 | `f0 00 f0` | cor **R, G, B** (magenta) | confirmado: escrita capturada em `0x418` (3 bytes); valor `ff` aceito |
| 7 | `ff` | Led (255) | alta |

Botão `<` na tela: Mode=CC Push, Channel 1, CC 25, Value1 0, Value2 127, Led 255 ↔ registro
`02 00 19 00 7f 06 f0 7f 7f 06 01 f7 … ff` (modo `02`, canal 0, CC `0x19`, 0, 127, SysEx
opcional de 6 bytes, terminador/Led `ff`). Knob 1: Type=CC, Channel 1, Speed=Normal, CC 30,
Min 0, Max 127 ↔ `02 00 1e 00 7f …`.

Região `0x04` (12 bytes, `78 00 32 04 00 00 02 00 01 01 00 00`) = globais da tela
(Presets 1: Time 1/16, Tempo 120, Transpose 0, PadCurve 2, Sync off, Swing 50,
PadAftertouch on, Octave 3, Latch off). Bate com: byte 0 = tempo (`0x78` = 120),
byte 2 = swing (`0x32` = 50), byte 6 = PadCurve (2). Os demais bytes são palpite.

## Cor do pad — capturada ao mudar a cor do pad 1 para vermelho

Escritas de 3 bytes na região `0x05`, endereço `0x418` (registro `0x414` + 4), uma por
movimento do seletor (~7 ms entre elas, todas respondidas com `00`). Sequência vista:
`62 f2 ff` (azul claro) → `dc 2e ff` (violeta) → `ff 1e 78` (rosa) → `ff 00 04` (vermelho),
o que só faz sentido na ordem R,G,B. As escritas mudam o aparelho ao vivo; a gravação
definitiva é a escrita de tamanho 0 em `0x05`/0, enviada **uma vez**, ao clicar em Salvar.

## LED — capturado

- Pad: escritas de 1 byte na região `0x05`, endereço `0x41b` (registro `0x414` + 7), valores
  `01`, `0a`, `64` (o usuário digitou 1, 10, 100) → intensidade do LED, 0–255.
- Botão `<` (CC25, 1º registro de botão): escritas de 1 byte em `0x16` (offset 22 do
  registro de 23 bytes), mesmos valores. O `ff` que fecha cada registro de botão no
  `SPB.bin` **é o campo Led** (255), não um separador.
- Em ambos, a gravação definitiva é a escrita de tamanho 0 em `0x05`/0, enviada ao Salvar.

## Registro de pad completo — capturado (pad 1 começa em `0x413`)

| Offset | Campo | Evidência |
|---|---|---|
| 0 | Type (0 = Note; 7 opções, valores 0–6) | `0x413`: `01…06` e volta a `00` |
| 1 | Channel (0-based) | `0x414` = `02` p/ Channel 3 |
| 2 | Note | `0x415` |
| 3 | MinVel | `0x416` = `0a` p/ 10 |
| 4 | MaxVel | `0x417` = `64` p/ 100 |
| 5–7 | Cor R,G,B | `0x418`–`0x41a` |
| 8 | LED | `0x41b` |

Tipos do pad (lista do app, na ordem dos valores 0–6): Note, CC Toggle, Momentary,
Program, MCP, Custom, Comb MCP. (O significado de cada um, e o que muda nos outros
bytes do registro conforme o tipo, ainda não foi investigado.)
Registros de pad têm passo de 26 bytes no bloco (cada um com 9 bytes úteis).

## "Redefinir" (restaurar fábrica) — capturado

Não existe comando de reset: o app **reescreve a imagem de fábrica**. Foram 27 escritas
de 1024 bytes (`0x0000`…`0x6800`) + 1 de 664 bytes (`0x6c00`) na região `0x05` — o total
(28.312 bytes) é idêntico ao `SPB.bin` — e 1 escrita de 12 bytes na região `0x04`
(`78 00 32 04 00 00 02 00 01 01 00 00`). Sem erros. **Sem** a escrita de tamanho 0
(commit) ao final; o usuário precisa clicar em Salvar para gravar de vez (o app novo tem
a mensagem "reset ok, salve"). Isso também mostra o formato de gravação em lote.

## Registro de botão — capturado (botão `<`, começa em `0x00`)

| Offset | Campo | Evidência |
|---|---|---|
| 0 | Mode (fábrica `02` = CC Push; valores vistos 0–5) | escritas `00…05` em `0x00` |
| 1 | Channel (0-based) | `0x01` = `02` p/ Channel 3 |
| 2 | CC | `0x02` = `1a…28` (26→40) |
| 3 | Value1 | `0x03` = `01…06`, `05` |
| 4 | Value2 | `0x04` = `7e…5a` (127→90) |
| 5 | tamanho do SysEx (fábrica `06`) | do `SPB.bin` (sem escrita capturada) |
| 6–21 | bytes do SysEx (fábrica `f0 7f 7f 06 0n f7`) | do `SPB.bin` (sem escrita capturada) |
| 22 | LED | `0x16` |

Modos do botão (lista do app, valores 0–5): CC Single, CC Toggle, CC Push (fábrica),
Program, Custom, Note. O campo SysEx do app não foi editado nesta captura.

## Registro de knob — capturado (6 bytes por knob; o knob 1 começa em `0x73`)

| Offset | Campo | Evidência |
|---|---|---|
| 0 | Type: 0 CC (fábrica), 1 Aftertouch, 2 Note, 3 CW | `0x73`: `01,02,03,00` |
| 1 | Speed: 0 Lowest, 1 Low, 2 Normal (fábrica), 3 Fast, 4 Fastest | `0x74`: `00…04`, `02` |
| 2 | Channel (0-based) | `0x75` = `02` p/ Channel 3 |
| 3 | CC | `0x76` = `1f…32` (31→50) |
| 4 | Min | `0x77` = `0a` |
| 5 | Max | `0x78` = `64` |

Knob N começa em `0x73 + 6*(N-1)` (16 knobs: banco 1 = CC30–37, banco 2 = CC38–45).

## Globais (região `0x04`, 12 bytes) — capturado

Fábrica: `78 00 32 04 00 00 02 00 01 01 00 00`. Escritas vistas (1 byte, exceto o tempo):

| Byte | Campo | Evidência |
|---|---|---|
| 0–1 | Tempo (u16 LE; fábrica 120) | `5a 00` = 90 |
| 2 | Swing (fábrica 50) | `33…3c` = 51…60 |
| 3 | Time (índice 0–7; fábrica `04` = 1/16) | `00…07` |
| 4 | Sync 0/1 (fábrica 0) | `01`, `00` |
| 5 | Latch 0/1 (fábrica 0) | `01`, `00` |
| 6 | Octave = valor mostrado − 1 (fábrica `02` = 3) | `04` p/ Octave 5 |
| 7 | Transpose (fábrica 0; negativos não testados) | `01`, `02` |
| 8 | PadCurve = valor mostrado − 1 (fábrica `01` = 2) | `02` p/ PadCurve 3 |
| 9 | PadAftertouch 0/1 (fábrica 1) | `00`, `01` |
| 10–11 | não identificados (o app escreveu o byte 10 = `01`/`00` num momento da 1ª captura) | — |

Gravação definitiva: escrita de tamanho 0 em `0x05`/0 (mesma dos registros).

## Presets e bancos — capturado

- Preset atual = byte 10 da região `0x04` (0 = Preset 1 … 7 = Preset 8). Trocar 1→2→1
  no app escreveu `01` e `00` nesse byte; o app **não** releu a região 5 ao trocar.
- O bloco da região 5 (28.312 bytes) = 8 presets × 3.539 bytes, em sequência. Cada preset:
  botões (5 × 23 bytes, `0x000`), knobs (16 × 6, `0x073`), depois os registros de pad
  (a partir de `0x0d4`, passo 26). Conferido no `SPB.bin`: presets 1 e 3–8 têm a mesma
  estrutura e o pad 1 (nota `0x24`) fica em `0x414` em cada um; o Preset 2 tem outro
  conteúdo de botões (`05 00 2e 00 00 …`).
- Todos os endereços capturados acima são do Preset 1. Para o preset P, somar
  `(P-1) × 3539` — **dedução**, ainda sem escrita capturada nos presets 2–8.
- PAD BANK / KNOB BANK do desenho do app não escrevem nada (só mudam a tela).
- Time (byte 3 dos globais): 0 = 1/4, 1 = 1/4T, 2 = 1/8, 3 = 1/8T, 4 = 1/16 (fábrica),
  5 = 1/16T, 6 = 1/32, 7 = 1/32T.

## SysEx do botão, Importar e escrita em outro preset — capturado

- Modo `04` (Custom) do botão habilita o campo SysEx. Gravação: 17 bytes a partir do byte 5
  do registro: `06 f0 7f 7f 06 02 f7` + zeros → byte 5 = tamanho, bytes 6–21 = dados (16 máx.).
- **Importar** (`.smc`): escreveu 3.539 bytes (1024+1024+1024+467) a partir de `0x0000` →
  um arquivo `.smc` = **um preset** (3.539 bytes), gravado no preset atual. Sem escrita de
  commit logo em seguida (precisa Salvar).
- Escrita no Preset 2: byte 10 dos globais = `01` e 1 byte em `0x155c` = `0x47`; `0x155c` é maior
  que 3.539, confirmando endereço = `(P-1) × 3539 + offset do preset`.
- Redefinir de novo: imagem de fábrica completa (28.312 bytes) + globais + byte 10 = 0 + commit.

## PAD BANK / KNOB BANK (botões físicos) — capturado

- **PAD BANK**: nenhuma escrita; a mudança aparece na leitura periódica da região `0x04`,
  **byte 11**: `00` = banco A, `01` = banco B (o estado ficou `…00 01` e voltou a `…00 00`).
  Pad 1 no banco B enviou Note On canal 10 nota **52**; de volta ao banco A, nota **36**
  (confirma notas 36–51 = banco A, 52–67 = banco B).
- **KNOB BANK**: nenhuma escrita e **nenhuma mudança** nos 12 bytes da região `0x04`. Com o
  banco alternado, o knob 1 enviou **CC 38** (banco 1 = CC30–37, banco 2 = CC38–45). Só se
  detecta pelo CC recebido.
- Mensagens curtas chegam ao app como MIDI normal (Note On/Off, CC) pela mesma porta
  `SMC-PAD`.

## Calibração dos pads ("Pad Calibration Mode") — capturado

- Região `0x03`, 160 bytes = 16 pads × 10 bytes (5 valores u16 LE por pad). O app lê
  `região 3, endereço 0, 160` ao abrir a tela. Fábrica por pad: `220, 550, 720, 850, 950`
  (PAD 13: `150, 550, …`). Só o **primeiro** u16 muda com o nível.
- Tela: um seletor "Level 1…8" por pad. Escolher o nível L grava o u16 no endereço
  `(pad-1)*10` com valor `50 + 100*(L-1)` (50, 150, 250, 350, 450, 550, 650, 750) e em seguida
  envia uma escrita de tamanho 0 na **região 3**, endereço 0 (commit da região 3). A tela mostra
  `floor((valor-50)/100)+1`, por isso o valor de fábrica 220 aparece como "Level 2".
- Ao sair da tela, o app enviou ainda um commit da região 5.
- O **Redefinir não restaura a calibração** (reescreve só as regiões 4 e 5).
- Estado do aparelho de teste após a captura: PAD 1 = 150 (era 220); PAD 13 = 150 (inalterado).

## Pendente

- Ids de região e endereços do SMC-PAD (o código do `SmcPad` chama `FUN_004fd630`
  com valores próprios; há 23 chamadores, um por tipo de aparelho).
- Comando de gravação, "salvar tudo" e "restaurar padrão de fábrica".
- Valor real do cabeçalho de 2 bytes em runtime.
- Escrever no aparelho só depois de ler e comparar com um backup feito pelo Midi Suite.
