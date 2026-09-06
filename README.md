# Zhsunyco ESL — BLE E-Ink-Label für Home Assistant

[![Validate](https://github.com/zitroaen/BLE-ESL-BWRY/actions/workflows/validate.yml/badge.svg)](https://github.com/zitroaen/BLE-ESL-BWRY/actions/workflows/validate.yml)
[![hacs](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)

Home-Assistant-Integration für BLE-E-Ink-Preisschilder (Electronic Shelf Labels)
mit Wolink/Zhsunyco-Firmware, z. B. `BLE-35BWRY`.

Die Implementierung folgt dem Herstellerdokument **„BLE Display API" Rev. 1.5**.
Alle Kommandos und Charakteristiken sind im Code mit der jeweiligen
Abschnittsnummer kommentiert.

## Funktionsumfang

| Funktion | Doku | Status |
|---|---|---|
| Security-Unlock (Challenge/Response, AES-128-ECB) | Abschn. 2 | ✅ |
| Batteriestand passiv aus dem Advertisement | Abschn. 1.2 | ✅ |
| Version, Batterie, Status über eigene Charakteristiken | Abschn. IV–VI | ✅ |
| RGB-LED `0xA508` | Abschn. 3.8 | ✅ |
| Bildschirm löschen `0xA504` | Abschn. 3.7 | ✅ |
| Bildupload `0xA500` / Refresh `0xA501` / `0xA502` | Abschn. 3.1–3.3 | ⚠️ Transport implementiert, Pixelformat experimentell |
| Testbilder ohne Bilddatei | – | ✅ |
| Multi-Screen `0xA503` / `0xA509` | Abschn. 3.9–3.10 | ⚠️ implementiert, ungetestet |
| OTA `0xA505`–`0xA507` | Abschn. 3.4–3.6 | ❌ bewusst nicht implementiert |

Unterstützt Bluetooth-Adapter **und ESPHome-Bluetooth-Proxies** — Verbindungen
laufen über `bluetooth` + `bleak-retry-connector`, nicht über direktes `bleak`.

## Installation über HACS

1. In Home Assistant: **HACS → ⋮ → Custom repositories**
2. Repository `https://github.com/zitroaen/BLE-ESL-BWRY`, Kategorie **Integration**
3. Hinzufügen, dann **Zhsunyco ESL** herunterladen
4. Home Assistant neu starten
5. **Einstellungen → Geräte & Dienste → Integration hinzufügen → Zhsunyco ESL**

Updates laufen danach normal über HACS. Zum Veröffentlichen einer neuen
Version einen Git-Tag setzen, der zur `version` in `manifest.json` passt:

```bash
git tag v0.2.0 && git push origin v0.2.0
```

### Upgrade vom Prototyp

Ein Config-Entry, der noch mit der Vorgängerversion angelegt wurde (Titel im
Format `ESL 66:66:17:40:27:77 (BLE-35BWRY)`), wird beim ersten Start
**automatisch migriert** — Adresse, Modell und das alte
`battery_scan_interval` im Format `HH:MM:SS` werden übernommen (`12:00:00`
wird zu 720 Minuten). Löschen und neu einrichten ist nicht nötig.

### Manuelle Installation

`custom_components/esl_zhsunyco/` nach `<config>/custom_components/` kopieren
und Home Assistant neu starten.

## Einrichtung

Labels, die ein passendes Manufacturer-Advertisement senden, werden von Home
Assistant **automatisch erkannt**. Alternativ manuell hinzufügen — die Adresse
beginnt laut Doku (Abschn. 1.1) mit den festen Bytes `66:66`, z. B.
`66:66:54:20:00:55`.

Das **Abfrageintervall** steuert nur den verbindungsbasierten Poll für Status
und Version. Batteriestand und Versionen kommen passiv aus dem Advertisement,
deshalb ist ein langes Intervall sinnvoll. `0` deaktiviert den Poll ganz.

## Entitäten

| Entität | Typ | Bemerkung |
|---|---|---|
| Batteriespannung | Sensor | aus dem Advertisement, Verbindung nicht nötig |
| Status | Sensor | BUSY/ERR laut Abschn. VI, inkl. Klartext-Fehlercodes |
| Signalstärke, Display-Version, Produkt-ID | Sensor | Diagnose, standardmäßig deaktiviert |
| RGB-LED | Light | Farbe + Blinkmuster |
| RGB-Ein-/Ausschaltzeit, RGB-Dauer | Number | Blinkparameter für die LED |
| Bildschirm löschen | Button | Kommando `0xA504` |

## Dienste

```yaml
# LED rot blinken lassen
action: esl_zhsunyco.set_rgb
target:
  device_id: <dein Label>
data:
  rgb_color: [255, 0, 0]
  on_ms: 500
  off_ms: 500
  work_ms: 30000
```

```yaml
action: esl_zhsunyco.clear_screen
target:
  device_id: <dein Label>
```

```yaml
# Experimentell
action: esl_zhsunyco.set_image
target:
  device_id: <dein Label>
data:
  path: /config/www/label.png
  rotate: 0
  dither: true
```

`set_image` verlangt, dass der Pfad in `allowlist_external_dirs` liegt.

## Verwandte, aber inkompatible Firmware

Unter dem Namen „Zhsunyco" werden **zwei völlig unterschiedliche
BLE-Protokolle** verkauft. Diese Integration spricht ausschließlich das
erste:

| | WOLINK (diese Integration) | easyTag |
|---|---|---|
| Service/Charakteristiken | `…-4C53-4545-4C42-4B4E494C4F57` | `00001523/1525/1526-1212-efde-…` |
| Authentifizierung | AES-128-ECB Challenge/Response | XOR-Schlüssel aus der MAC |
| Kommandos | `0xA500`–`0xA509` | 20-Byte-Header + 204-Byte-Pakete, CRC-16/ARC |
| Kennung | keine | ASCII `easyTag` / `eTag-CO` |
| Rückmeldung | keine | Notify-Charakteristik |

Für die easyTag-Variante gibt es eine eigene Implementierung:
[roxburghm/zhsunyco-esl](https://github.com/roxburghm/zhsunyco-esl).

Die **Diagnose-Probe erkennt beide** und meldet unter `protocol_family`,
welche das Label tatsächlich spricht. Steht dort `easytag_xor`, ist diese
Integration die falsche Software für das Gerät.

## Bekannte Unsicherheiten

Diese Punkte sind im Herstellerdokument **nicht** spezifiziert und daher im
Code als begründete Annahme umgesetzt. Sie sind bewusst an einer Stelle
gebündelt, damit sie leicht korrigierbar sind:

- **Pixelformat der Bilddaten.** Die Doku beschreibt nur den Transport. Die
  Packung in `imaging.py` nimmt für BWRY-Panels 2 Bit pro Pixel mit der
  Palettenreihenfolge Schwarz/Weiß/Gelb/Rot an, für andere Panels 1 Bit pro
  Pixel. Anpassbar über `BWRY_PALETTE` und `MONO_BLACK_BIT`.
- **Blockkomprimierung** (Abschn. 3.3/3.9) ist nicht beschrieben. In
  `rle.py` liegt der RLE-Codec der easyTag-Firmware desselben Herstellers als
  begründeter Kandidat — mit Encoder, Decoder und Round-Trip-Tests, aber
  ungetestet gegen diese Hardware. Übertragen wird bisher ausschließlich
  unkomprimiert über `0xA501`.
- **Byte-Reihenfolge** von `on_ms`/`off_ms`/`work_ms` (Abschn. 3.8) und der
  Bildgröße (Abschn. 3.2) — angenommen wird Little Endian, passend zum
  4-Byte-Datenzeiger. Achtung: Das Gerät mischt die Byte-Reihenfolgen (siehe
  unten), diese Annahme ist also nicht sicher.
- **Company Identifier** im Advertisement: Die Doku nennt `0xbbaa` für Byte
  0–1. Bestätigt: Home Assistant meldet `0xBBAA`.
- **Slot-Header** `PIC0x\0` (Abschn. 3.9) lässt bei Index 10 nur eine Ziffer
  zu; implementiert ist zweistellig, also `PIC00`…`PIC10`.
- **Panel-Auflösungen** in `const.py` stammen nicht aus der Doku und können
  je nach Gerät abweichen.

## Bestätigt gegen echte Hardware

Ein vollständiger Probe-Lauf an einem BLE-35BWRY hat folgendes belegt.

### GATT-Tabelle

Alle fünf Charakteristiken liegen unter dem Service
`30323032-4C53-4545-4C42-4B4E494C4F57`:

| Charakteristik | UUID-Präfix | Handle | Properties |
|---|---|---|---|
| Batterie | `35323032` | 14 | notify, read |
| Status | `34323032` | 17 | notify, read |
| Security | `33323032` | 20 | read, **write** |
| Command | `31323032` | 23 | read, **write** |
| Version | `32323032` | 26 | notify, read |

**Die Command-Charakteristik unterstützt nur „Write with Response"** — kein
Write-without-Response. Der Schreibmodus `auto` wählt damit automatisch das
Richtige; die Option bleibt nur für abweichende Firmware-Stände erhalten.

Alle drei Lese-Charakteristiken können außerdem **notify** — die Doku
erwähnt das nicht. Bisher ungenutzt, aber der naheliegende Kanal für eine
Rückmeldung nach dem Bildupload.

MTU: **247 Bytes**, also 238 Byte Nutzdaten pro Chunk.

### Unlock

Challenge/Response ist bitgenau verifiziert:

```
Challenge : d8 2f 70 03 5f a0 99 07 36 fd e2 1c 4d a5 9b bf
Antwort   : e0 87 76 67 af a5 3a 8b 00 6e 24 71 0f 58 fc 57
```

Danach meldet die Status-Charakteristik Fehlercode 0 (`no_error`).
Die Status-Charakteristik liefert übrigens 32 Bytes statt der
dokumentierten 2 — der Rest ist Null und wird ignoriert.

### Gemischte Byte-Reihenfolge

Ein Probe hat alle drei Quellen gleichzeitig erfasst:

```
Advertisement           : 30 00 00 0e 03 30 02 01 0b 99
Version-Charakteristik  : 30 00 00 0e 03 30 02 01
Batterie-Charakteristik : 99 0b   -> Little Endian = 2969 mV
```

Daraus folgt eindeutig:

- Die ersten acht Advertisement-Bytes sind **byte-identisch** mit der
  Version-Charakteristik. Die Versionsfelder müssen also in beiden Quellen
  **gleich** dekodiert werden (Little Endian).
- Die letzten beiden sind die **Byte-Umkehr** der Batterie-Charakteristik.
  Nur dieses eine Feld ist im Advertisement Big Endian.

Das komplette Advertisement einheitlich zu lesen ist in beiden Richtungen
falsch. Die Doku erwähnt zu Byte-Reihenfolgen nichts.

Die Bedeutung der Versionsfelder selbst bleibt offen: `03 30` lässt sich als
`3.48`, `48.3` oder `3.30` lesen. Die Rohbytes stehen deshalb als
`version_bytes` in der Diagnose.

## Testbild senden

Weil das Pixelformat nicht dokumentiert ist, kommt beim ersten Upload
wahrscheinlich nicht das Richtige heraus. Deshalb gibt es eingebaute
Testmuster — keine Bilddatei, kein `allowlist_external_dirs` nötig.

**Ein Klick:** der Button **Testbild** am Gerät sendet das Diagnose-Muster.

**Mit Parametern:**

```yaml
action: esl_zhsunyco.send_test_pattern
target:
  device_id: <dein Label>
data:
  pattern: diagnostic     # oder solid_black, quadrants, stripes_v, ...
  encoding: auto          # auto | mono | bwry_packed | bwry_planes
  bit_order: msb          # msb | lsb
  rotate: 0
  mirror: false
```

### Das Diagnose-Muster lesen

Es ist so aufgebaut, dass ein Foto des Ergebnisses verrät, **welcher**
Parameter falsch ist:

| Beobachtung | Bedeutung |
|---|---|
| Nichts ändert sich | Upload kommt nicht an — erst `clear_screen` prüfen |
| Rahmen fehlt oder ist doppelt | Breite und Höhe vertauscht → `rotate: 90` |
| Ecken-Dreieck an der falschen Stelle | Drehung oder Spiegelung → `rotate` / `mirror` |
| Farbblöcke in falscher Farbe | Palettenreihenfolge → `BWRY_PALETTE` in `imaging.py` |
| Feine Streifen verschmieren/versetzt | Bit-Reihenfolge → `bit_order: lsb` |
| Bild diagonal verzogen | Zeilenlänge stimmt nicht → anderes `encoding` |
| Nur oberes Drittel gefüllt | Falsche Bits pro Pixel → `encoding: mono` statt `bwry_packed` |

Sinnvolle Reihenfolge zum Durchprobieren: erst `solid_black` (reagiert das
Panel überhaupt?), dann `quadrants` (Orientierung und Farben), dann
`diagnostic` für die Feinheiten.

## Fehlersuche

### Diagnose-Probe

Der schnellste Weg, ein Problem einzugrenzen: am Gerät den Button
**Diagnose-Probe** drücken (alternativ der Dienst
`esl_zhsunyco.debug_probe`). Er verbindet sich, listet
die komplette GATT-Tabelle mit allen Charakteristiken und deren Properties
auf, versucht das Unlock und liest anschließend Version, Batterie und Status
im Rohformat. Das Ergebnis erscheint als Benachrichtigung und im Log.

Der Button funktioniert auch dann, wenn alle anderen Entitäten
„nicht verfügbar" sind — er scheitert nicht, sondern meldet den Fehlgrund
unter `connection_error`.

Besonders aussagekräftig sind zwei Felder:

- `connection` — `ok` heißt, Verbindung und GATT-Zugriff funktionieren.
- `reads.status.error_meaning` — `unlock_failed` bedeutet, dass die
  Challenge/Response abgelehnt wurde; alles andere heißt, dass das Unlock
  funktioniert hat.

Zusätzlich liefert **Einstellungen → Geräte & Dienste → Zhsunyco ESL →
Diagnose herunterladen** eine JSON-Datei mit dem rohen Advertisement, der
Feld-für-Feld-Zerlegung und den Batteriewerten unter allen plausiblen
Dekodierregeln.

### Das Label reagiert nicht auf Kommandos

Ein Schreibvorgang auf die Command-Charakteristik meldet keinen Fehler,
selbst wenn das Label ihn ignoriert. Die **Status-Charakteristik ist der
Rückkanal**: Ändert sich `busy` oder `error_code` nach einem Kommando nicht,
wurde es nicht verstanden.

Genau das prüft der Sweep — und zwar alle Kandidaten in **einer** Verbindung,
weil das Aufwecken des Labels der langsame Teil ist:

```yaml
action: esl_zhsunyco.debug_command_sweep
target:
  device_id: <dein Label>
data:
  opcode: "A504"     # Bildschirm löschen
```

Getestet werden daraus abgeleitet `a5 04`, `04 a5`, `a5 04 00`,
`a5 04 00 00` und `04 a5 00`. Das Ergebnis kommt als Benachrichtigung:

- `status_changed: true` bei einer Variante → **diese Kodierung ist richtig**
- `any_status_changed: false` → keine Variante wurde verstanden
- `connected_after: false` → das Label hat nach dieser Variante aufgelegt,
  was ebenfalls eine Reaktion ist

Eigene Kandidaten gehen auch: `payloads: ["a5 04", "04 a5 00 00"]`.

Das Ergebnis des letzten Kommandos steht außerdem als `last_command` in der
Diagnose-Datei.

### Kommandos scheitern mit „out of connection slots"

Ein ESL schläft zwischen seinen Advertisements — Pausen von mehreren Minuten
sind normal. Ein Bluetooth-Proxy kann eine Verbindung nur zu einem Gerät
aufbauen, das er **gerade** sieht. Außerhalb dieses Fensters scheitert jeder
Versuch, egal wie oft wiederholt wird:

```
BleakOutOfConnectionSlotsError: ... no scanner currently has it in its
discovered devices ... last advertisement 262s ago
```

Die Integration wartet deshalb seit 0.6.0 auf das nächste Advertisement
(bis zu 180 s) und verbindet sich innerhalb dieses Fensters. Ein Tastendruck
kann dadurch spürbar dauern — das ist normal und kein Fehler.

Hilft das nicht, steht der Proxy zu weit weg oder hat keine freien
Verbindungs-Slots. Ein zusätzlicher
[ESPHome-Bluetooth-Proxy](https://esphome.github.io/bluetooth-proxies/) in
der Nähe des Labels ist dann die Lösung.

### Zwei ESL-Integrationen gleichzeitig

Ein BLE-Label kann immer nur **eine** Verbindung gleichzeitig annehmen. Wenn
eine zweite Integration (z. B. `esl_tag`) dasselbe Label anspricht, greifen
beide abwechselnd zu und Kommandos gehen verloren. Falls beide installiert
sind: die andere Integration für das Label testweise deaktivieren.

### LED und Bildschirm löschen bleiben wirkungslos

Wenn die Sensoren aktualisieren, Kommandos aber nichts bewirken, akzeptiert
das Label vermutlich nur einen der beiden ATT-Schreibtypen. In den Optionen
der Integration lässt sich der **Schreibmodus für Kommandos** von
`Automatisch` auf `Ohne Bestätigung` bzw. `Mit Bestätigung` umstellen.

### Debug-Logging aktivieren

```yaml
logger:
  default: warning
  logs:
    custom_components.esl_zhsunyco: debug
```

**Status meldet `unlock_failed` (Fehlercode 5 laut Abschn. VI):** Das Label hat
die Challenge/Response abgelehnt. Prüfen, ob die Firmware denselben AES-Key
verwendet.

**„not in range of any Bluetooth adapter or proxy":** Home Assistant sieht das
Label gerade nicht. Bei ESPHome-Proxies muss `bluetooth_proxy: active: true`
gesetzt sein, sonst sind nur passive Advertisements möglich.

## Entwicklung

```bash
pip install -r requirements-test.txt ruff

ruff check custom_components tests
ruff format --check custom_components tests

python tests/test_protocol.py      # Protokoll, ohne Home Assistant
pytest tests/integration -q        # Integration, gegen echtes Home Assistant
```

Es gibt zwei Testebenen:

- **`tests/test_protocol.py`** prüft die erzeugten Wire-Bytes gegen die Doku —
  Challenge/Response beim Unlock, das 13-Byte-RGB-Layout, Chunk-Offsets beim
  Bildupload, die vorzeichenbehafteten Multi-Screen-Indizes und das
  Advertisement-Parsing. Läuft ohne Home-Assistant-Installation, weil
  `protocol.py` bewusst keine HA-Importe enthält — dieselbe Datei lässt sich
  daher aus einem einfachen Skript gegen echte Hardware testen.
- **`tests/integration/`** startet eine echte Home-Assistant-Instanz und prüft
  Config-Flow, Options-Flow, Entity-Registrierung, Dienste und die
  Advertisement-Verarbeitung.

## Lizenz

MIT — siehe [LICENSE](LICENSE).
