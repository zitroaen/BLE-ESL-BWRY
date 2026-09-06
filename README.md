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
| Bildupload `0xA500` / Refresh `0xA501` | Abschn. 3.1–3.2 | ✅ |
| Refresh komprimiert `0xA502` | Abschn. 3.3 | ⚠️ implementiert, Kompression ungetestet |
| Testbilder ohne Bilddatei | – | ✅ |
| Panel-Vorschau als `image`-Entität | – | ✅ |
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

## Beliebige Bilder senden

`set_image` nimmt einen Dateipfad. Home Assistant lässt nur freigegebene
Verzeichnisse zu, also einmalig in die `configuration.yaml`:

```yaml
homeassistant:
  allowlist_external_dirs:
    - /config/www/esl
```

```yaml
action: esl_zhsunyco.set_image
data:
  device_id: <dein Label>
  path: /config/www/esl/kalender.png
  dither: true
```

Um Größe und Farben musst du dich nicht kümmern: das Bild wird auf die
Panelgröße gebracht, per Floyd-Steinberg auf die vier darstellbaren Farben
gerastert und in 2 bpp gepackt. Ein Vollbild sind 17664 Byte, also 99
Chunks — rechne mit etwa einer halben Minute, sobald die Verbindung steht.

Bei Grafiken mit großen einfarbigen Flächen (Text, Tabellen, Kalender)
liefert `dither: false` meist ein ruhigeres Bild als das Rastern.

### Hat das Senden geklappt?

Es gibt **zwei** verschiedene Fehlschläge, und sie fühlen sich unterschiedlich
an:

| Fall | Woran man ihn erkennt |
|---|---|
| Übertragung gescheitert (Label schläft, Verbindung bricht ab) | Der Dienst wirft einen Fehler, die Automation bricht ab |
| Übertragung angenommen, Panel tut nichts | **Kein** Fehler — nur `label_reacted: false` |

Der zweite ist der heimtückische: HA meldet Erfolg, das Label zeigt weiter
das alte Bild. Deshalb geben `set_image`, `send_test_pattern`, `clear_screen`
und `set_rgb` eine Antwort zurück:

```yaml
action: esl_zhsunyco.set_image
data:
  device_id: <dein Label>
  path: /config/www/esl/kalender.png
response_variable: ergebnis
```

```yaml
results:
  - address: "66:66:17:40:27:77"
    ok: true              # der Schreibvorgang selbst lief durch
    label_reacted: true   # das Panel meldete busy, hat also gezeichnet
    connection_dropped: false
    error_code: 0
    bytes: 17664
    encoding: bwry_packed
    at: "2026-09-06T19:12:04.881+00:00"
```

**`label_reacted` ist die Prüfung, die zählt.** `ok: true` sagt nur, dass die
Bytes rausgingen.

Die Antwort ist optional — bestehende Automationen ohne `response_variable`
laufen unverändert weiter.

#### Automation mit Wiederholung

Ein schlafendes Label ist der Normalfall, nicht die Ausnahme. Drei Versuche
mit Pause dazwischen sind realistisch:

```yaml
- repeat:
    count: 3
    sequence:
      # Zurücksetzen, sonst steht nach einem Fehler noch das Ergebnis
      # des vorherigen Durchlaufs in der Variable.
      - variables:
          ergebnis: null
      - action: esl_zhsunyco.set_image
        data:
          device_id: <dein Label>
          path: /config/www/esl/kalender.png
        response_variable: ergebnis
        continue_on_error: true
      - if:
          - condition: template
            value_template: >-
              {{ ergebnis and ergebnis.results[0].label_reacted }}
        then:
          - stop: "Bild steht auf dem Panel"
      - delay: "00:05:00"
- action: persistent_notification.create
  data:
    title: ESL
    message: Kalenderbild konnte nach drei Versuchen nicht übertragen werden.
```

`continue_on_error: true` ist nötig, damit ein Verbindungsfehler die Schleife
nicht sofort beendet. Das `variables:`-Zurücksetzen ist nicht kosmetisch: ohne
es behält `ergebnis` nach einem geworfenen Fehler den Wert des letzten
erfolgreichen Durchlaufs, und die Schleife bricht fälschlich ab.

Zur Pause: Warte großzügig. Das Label advertised unregelmäßig, die Integration
wartet ohnehin bis zu 300 s auf ein Fenster, und ein zu schneller zweiter
Versuch konkurriert nur mit dem ersten um den einzigen Verbindungsslot.

#### Ohne Antwortvariable

Der Zustand der `image`-Entität **ist** der Zeitstempel der letzten
erfolgreichen Übertragung. Das reicht als Auslöser:

```yaml
triggers:
  - trigger: state
    entity_id: image.esl_66_66_17_40_27_77_panel
```

Und als Prüfung, ob heute schon etwas ankam:

```yaml
{{ states('image.esl_66_66_17_40_27_77_panel') | as_datetime | as_local
   > today_at('00:00') }}
```

Ausführlicher steht der letzte Befehl mit Statusbytes und Deutung in der
Diagnose-Datei unter `last_command`.

### Was das Panel gerade zeigt

Jedes Label hat eine `image`-Entität, die das zuletzt übertragene Bild
zeigt. Sie wird **aus den gepackten Pixeln** erzeugt, nicht aus der
Quelldatei — Dithering und Farbreduktion sind darin also genauso zu sehen
wie auf dem Panel.

```yaml
type: picture-entity
entity: image.esl_66_66_17_40_27_77_panel
```

Zwei bewusste Eigenheiten:

- Sie bleibt **verfügbar**, auch wenn das Label schläft. Was auf dem Panel
  steht, hört nicht auf zu stimmen, nur weil gerade niemand es sieht.
- Nach einem Neustart von Home Assistant ist sie leer. Ein E-Ink-Panel
  lässt sich nicht auslesen; nach einem Neustart wissen wir schlicht nicht,
  was darauf steht, und ein Bild von vorhin zu zeigen wäre geraten.

Schlägt eine Übertragung fehl, bleibt die vorherige Vorschau stehen.

## Bekannte Unsicherheiten

Diese Punkte sind im Herstellerdokument **nicht** spezifiziert und daher im
Code als begründete Annahme umgesetzt. Sie sind bewusst an einer Stelle
gebündelt, damit sie leicht korrigierbar sind:

- **Pixelformat für andere Panels als BWRY.** Für BWRY ist es keine Annahme
  mehr: 2 Bit pro Pixel, MSB zuerst, zeilenweise, Palettenreihenfolge
  Schwarz/Weiß/Gelb/Rot — am Gerät belegt (siehe unten). Für 1-Bit-Panels
  ist die Packung weiterhin ungetestet; anpassbar über `MONO_BLACK_BIT`.
- **Blockkomprimierung** (Abschn. 3.3/3.9) ist nicht beschrieben. In
  `rle.py` liegt der RLE-Codec der easyTag-Firmware desselben Herstellers als
  begründeter Kandidat — mit Encoder, Decoder und Round-Trip-Tests, aber
  ungetestet gegen diese Hardware. Übertragen wird bisher ausschließlich
  unkomprimiert über `0xA501`.
- ~~**Byte-Reihenfolge** von `on_ms`/`off_ms`/`work_ms` (Abschn. 3.8) und der
  Bildgröße (Abschn. 3.2).~~ Erledigt: Little Endian, am Gerät belegt — die
  LED reagiert auf das 13-Byte-Layout und `01 a5` + Größe frischt das Panel
  auf.
- **Company Identifier** im Advertisement: Die Doku nennt `0xbbaa` für Byte
  0–1. Bestätigt: Home Assistant meldet `0xBBAA`.
- **Slot-Header** `PIC0x\0` (Abschn. 3.9) lässt bei Index 10 nur eine Ziffer
  zu; implementiert ist zweistellig, also `PIC00`…`PIC10`.
- **Panel-Auflösungen** in `const.py` stammen nicht aus der Doku und können
  je nach Gerät abweichen.

## Der AES-Handshake ist bestätigt

Ein Unlock-Sweep über alle sechs Varianten hat es entschieden — **`encrypt`
ist korrekt**, also genau das, was die Doku beschreibt:

| Variante | Statusbyte 0 nach Unlock | Verbindung überlebt Kommando |
|---|---|---|
| **`encrypt`** | **`0x00`** | **ja** |
| decrypt | `0x06` | nein |
| encrypt_reversed | `0x06` | nein |
| decrypt_reversed | `0x06` | nein |
| encrypt_then_reverse | `0x06` | nein |
| echo | `0x06` | nein |

Die fünf falschen Varianten lösen exakt das aus, was die Doku für ein
gesperrtes Label beschreibt: Das Gerät legt beim nächsten Write auf.

### Das Statusbyte trägt einen undokumentierten Sperr-Indikator

Die Doku definiert Byte 0 als BUSY („1: busy, 0: no busy"). Gemessen wurde:

- `0x00` → Unlock akzeptiert
- `0x06` → Unlock abgelehnt

Bit 0 ist also der dokumentierte BUSY-Flag, **Bits 1 und 2 zeigen den
Sperrzustand** — das steht nirgends in der Doku. Die Integration prüft das
seit 0.15.0 direkt nach jedem Unlock und warnt im Log, statt den Fehler
erst beim ersten Kommando als „Characteristic not found" auftauchen zu
lassen. In der Diagnose steht er als `unlock_verified`.

Vorher wurde `0x06` fälschlich als „busy" gemeldet, weil das ganze Byte als
Boolean gelesen wurde.

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
Write-without-Response. Seit 0.19.0 ist `with_response` deshalb der Default
und `without_response` gar nicht mehr auswählbar; ein Eintrag, der den Wert
noch gespeichert hat, schreibt mit Response und bekommt eine Warnung ins
Log.

Alle drei Lese-Charakteristiken können außerdem **notify** — die Doku
erwähnt das nicht. Bisher ungenutzt, aber der naheliegende Kanal für eine
Rückmeldung nach dem Bildupload.

MTU: **247 Bytes**. Geschrieben wird trotzdem in **180-Byte-Scheiben** —
das ist die Größe, mit der drei vollständige Bilder ohne einen einzigen
Fehlversuch durchgingen; größere Frames sind auf dieser Hardware nie
probiert worden, und ein Bildupload ist der falsche Ort dafür. Meldet der
Client gar keine MTU, gilt dieselbe Größe: der frühere Rückfallwert von 23
(das ATT-Minimum) ließ 14 Nutzbytes übrig und machte aus einem Bild rund
1262 Writes.

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

### Alle Entitäten „nicht verfügbar", keine Advertisements mehr

Betrifft **0.9.0**. Ein Diagnose-Download hat dort automatisch eine Probe
gestartet; lief deren Timeout ab, blieb eine interne Sperre dauerhaft
gehalten und eine eventuell schon offene BLE-Verbindung bestehen. Ein
verbundenes BLE-Gerät **sendet keine Advertisements mehr** — die Integration
wurde dadurch blind und alle Entitäten fielen aus.

Behoben in 0.9.1. Falls es noch auftritt: **Integration neu laden**
(Einstellungen → Geräte & Dienste → ⋮ → Neu laden).

Seit 0.10.0 gibt es zusätzlich:

- Der Advertisement-Callback verlangt **keinen verbindungsfähigen Scanner**
  mehr. Ohne diese Angabe verlangt Home Assistant standardmäßig einen — und
  Advertisements, die nur ein passiver Scanner sieht, kamen nie an.
- Alle 5 Minuten werden die Daten zusätzlich direkt aus dem
  Bluetooth-Stack von Home Assistant gelesen. Ein ausbleibender Callback
  lässt die Sensoren dadurch nicht mehr dauerhaft leer.
- Die Diagnose-Datei enthält einen Abschnitt **`bluetooth`**. Er
  unterscheidet die beiden grundverschiedenen Fälle:

  | Feld | Bedeutung |
  |---|---|
  | `last_service_info_any: null` | Home Assistant sieht das Label **überhaupt nicht** — es ist still, außer Reichweite oder noch verbunden |
  | `last_service_info_any` gefüllt, aber Entitäten leer | HA sieht es, unser Callback greift nicht |
  | `scanners_seeing_this_label: []` | kein Adapter/Proxy empfängt es |
  | `learned_advertising_interval_s` | wie oft HA das Label senden sieht |

Der Diagnose-Download baut seit 0.9.1 **keine Verbindung mehr auf**. Für
einen Probe-Bericht den Button **Diagnose-Probe** verwenden; das Ergebnis
landet dann auch in der Diagnose-Datei.

### „Characteristic … was not found" beim Bildupload

Das ist **kein** Cache-Problem, sondern eine Folge des gesperrten Labels.
Deine Doku, Abschnitt 2:

> „If it is not unlocked, writing other services will be disconnected
> immediately."

Beobachtung, die dazu passt:

| Aktion | Writes auf Command | Ergebnis |
|---|---|---|
| Diagnose-Probe | 0 (nur Lesen + Security-Write) | funktioniert |
| Bildschirm löschen / RGB | 1 | „ok", aber keine Reaktion |
| Testbild | ~75 | „Characteristic not found" ab dem 2. |

Der erste Command-Write bringt das Label dazu aufzulegen. Danach hat bleak
keine Service-Tabelle mehr, und jeder weitere Write meldet die
Charakteristik als nicht gefunden. Die Integration erkennt das jetzt und
schreibt es in `last_command` unter `connection_dropped` samt Deutung.

### Das Label entzieht die Autorisierung

Gemessen: Nach dem Schreiben von `04 a5` beantwortet das Label schon das
nächste **Lesen** mit einem ATT-Fehler:

```
BluetoothGATTErrorResponse: Insufficient authorization (8)
```

Das ist ATT-Fehlercode 0x08 und kommt vom Label selbst. Ein abgelehntes
Kommando macht also das Unlock zunichte.

> **Korrektur (0.19.0).** Aus „`a5 04` löst das nicht aus" wurde hier
> geschlossen, die dokumentierte Byte-Reihenfolge stimme. Das war falsch.
> Der Messaufbau selbst hat den Fehler erzeugt: `a5 04` stand im Sweep an
> erster Stelle, `04 a5` kam danach auf **derselben** Verbindung — die das
> Label zu diesem Zeitpunkt längst gekappt hatte. Ein direkter Lauf über
> einen lokalen Adapter zeigt das Gegenteil: `00 a5` und `01 a5` haben ein
> Bild übertragen und das Panel dreimal neu gezeichnet. Kommandos gehen
> **little endian** raus. Siehe
> [`docs/hardware-verified-findings.md`](docs/hardware-verified-findings.md).

Daraus folgen zwei Dinge, beide seit 0.17.0 umgesetzt:

- Der Kommando-Sweep verwendet **eine Verbindung pro Kandidat**. Vorher
  waren alle Kandidaten nach dem ersten abgelehnten wertlos.
- Der Report unterscheidet jetzt zwischen `rejected` (Label entzieht den
  Zugriff) und `tolerated_but_ignored` (Kommando angenommen, aber wirkungslos).
  Nur Letzteres ist ein Hinweis auf eine richtige Kodierung.

### Das Label nimmt Kommandos an und tut nichts

Beobachtet: Verbindung steht, Status ist lesbar, Fehlercode bleibt 0, aber
`busy` steigt nie. Das Label **ignoriert die Writes stillschweigend** — genau
das Verhalten eines Geräts, das noch **gesperrt** ist. Die Doku sagt dazu:

> „If it is not unlocked, writing other services will be disconnected"

Der AES-Handshake war damit lange der Hauptverdächtige — unsere Rechnung
war verifiziert, dass das **Label sie akzeptiert**, war es nie.

**Das ist inzwischen beantwortet: der Handshake stimmt.** Zwei unabhängige
Messungen belegen ihn — ein Sweep über einen ESPHome-Proxy, bei dem nur
`encrypt` das Status-Byte entsperrt hinterließ, und ein direkter Lauf, der
nach dem Unlock `ERR=0` las und danach 98 Kommando-Writes am Stück ohne
Abbruch durchbrachte.

Deshalb gibt es seit 0.19.0 **keine Unlock-Varianten mehr**: weder die
Option „Unlock-Berechnung" noch die Aktion `debug_unlock_sweep`. Eine
falsche Variante scheitert nicht bloß, sie lässt das Label die
Autorisierung entziehen — fünf davon vorrätig zu halten war ein Risiko
ohne Gegenwert. Gesendet wird AES-128-ECB über die gelesene Challenge.

### Hat das Kommando wirklich etwas bewirkt?

Ein Schreibvorgang auf die Command-Charakteristik meldet Erfolg, **egal ob
das Label ihn ausführt**. Seit 0.12.0 liest die Integration deshalb um jedes
Kommando herum die Status-Charakteristik: einmal davor, dann bis zu 8
Sekunden lang danach. Ein E-Ink-Refresh dauert Sekunden, in denen das Panel
`busy` meldet.

Das Ergebnis steht in der Diagnose unter `last_command`:

```json
"label_reacted": true,
"interpretation": "the panel went busy, so it acted on the command"
```

- `label_reacted: true` → das Panel hat gearbeitet, das Kommando kam an
- `label_reacted: false` → der Schreibvorgang wurde angenommen, das Panel
  hat aber nichts getan; die Kodierung stimmt vermutlich nicht

Der Status-Sensor wird nach jedem Kommando direkt aktualisiert.

### „Characteristic 31323032-… was not found"

Die Verbindung stand, aber die zwischengespeicherte GATT-Tabelle enthielt
die Command-Charakteristik nicht — obwohl ein Probe sie zuvor gefunden
hatte. Seit 0.11.0 prüft die Integration direkt nach dem Verbinden, ob
Security- und Command-Charakteristik vorhanden sind. Fehlen sie, wird der
Service-Cache verworfen und **einmal neu verbunden**. Hilft auch das nicht,
sagt die Fehlermeldung das ausdrücklich, statt an einem Schreibvorgang zu
scheitern.

Auch eine offen gehaltene Verbindung wird vor jeder Wiederverwendung
geprüft.

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

Standardmäßig (`preset: clear_screen`) werden **beide dokumentierten
Löschwege** getestet, nicht nur Byte-Varianten eines einzigen — in
Little-Endian zuerst, weil das die gemessene Reihenfolge ist:

| Payload | Herkunft |
|---|---|
| `04 a5` | Abschn. 3.7 „Unbind Clear Screen", little endian |
| `09 a5 fe fe` | Abschn. 3.10, beide Ebenen löschen (Index −2) |
| `09 a5 fe ff` | Abschn. 3.10, nur Ebene A löschen |
| `09 a5 ff fe` | Abschn. 3.10, nur Ebene B löschen |
| `04 a5 00`, `04 a5 00 00` | mit Längenbytes |
| `a5 04`, `a5 09 fe fe` | wie die Doku es schreibt, big endian |

„Unbind" in 3.7 klingt nach Kopplung/Reset — möglicherweise ist gar nicht
das der normale Löschweg, sondern 3.10 mit Index −2. **Löschen ist als
einziges Kommando in keiner Byte-Reihenfolge gemessen**, deshalb gibt es
diesen Sweep überhaupt noch. Mit `preset: opcode` lassen sich stattdessen
Varianten aus einem beliebigen Opcode ableiten.

Das Ergebnis kommt als Benachrichtigung:

- `status_changed: true` bei einer Variante → **diese Kodierung ist richtig**
- `any_status_changed: false` → keine Variante wurde verstanden
- `connected_after: false` → das Label hat nach dieser Variante aufgelegt,
  was ebenfalls eine Reaktion ist

Eigene Kandidaten gehen auch: `payloads: ["04 a5", "a5 04 00 00"]`.

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
und verbindet sich innerhalb dieses Fensters. Der **erste** Tastendruck
kann dadurch spürbar dauern — das ist normal und kein Fehler.

Seit 0.19.0 sind es **bis zu 300 s** statt 180. Mit durchgehendem aktivem
Scan gemessen: einzelne Fenster von 10–30 s finden das Label bei 20 cm
Abstand und −53 dBm regelmäßig **nicht**, und direkt nach einer Übertragung
blieb ein Lauf über 120 s am Stück leer. 180 s lagen damit genau auf der
beobachteten Streuung.

Seit 0.9.0 bleibt die Verbindung danach offen — seit 0.19.0 für **15
Sekunden** statt 60 (einstellbar in den Optionen, 0 = sofort trennen).
Folgekommandos innerhalb dieses Fensters wirken **sofort**, weil nicht
erneut gewartet werden muss.

Kürzer, weil ein **verbundenes** BLE-Gerät gar nicht mehr advertised: jede
Sekunde am offenen Link ist eine Sekunde, in der das Label für alles andere
unsichtbar ist — auch für den Scanner von Home Assistant selbst.

Zum Vergleich: Die Referenzimplementierung
[roxburghm/zhsunyco-esl](https://github.com/roxburghm/zhsunyco-esl) wartet
mit `BleakScanner.find_device_by_address(..., timeout=120.0)` genauso auf ein
Advertisement — das Warten ist also keine Eigenheit dieser Integration,
sondern eine Eigenschaft der Geräteklasse. Sie hält die Verbindung
allerdings über den gesamten Vorgang offen, was diese Integration jetzt
ebenfalls tut.

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

Beide sind an einem BLE-35BWRY belegt und funktionieren, seit die Opcodes in
0.19.0 auf Little-Endian umgestellt wurden. Wenn sie bei dir wirkungslos
bleiben:

1. **Version prüfen.** Vor 0.19.0 hat die Integration `a5 04` statt `04 a5`
   geschickt — das Label nimmt den Write an und tut nichts.
2. **`unlock_verified` in der Diagnose prüfen.** Steht dort `false`, ist das
   Label gesperrt und ignoriert grundsätzlich jedes Kommando.
3. **`last_command` in der Diagnose lesen.** `label_reacted: false` heißt,
   das Panel ist nach dem Kommando nie `busy` geworden.

Handelt es sich um ein **anderes Modell**, ist die Byte-Reihenfolge dort
nicht gemessen. Dann hilft `debug_command_sweep` (siehe oben).

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
