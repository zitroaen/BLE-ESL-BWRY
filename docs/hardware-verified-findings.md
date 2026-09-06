# Am Gerät verifizierte Protokoll-Erkenntnisse (BLE-35BWRY)

Dieses Dokument hält fest, was am **echten Label** gemessen wurde, nicht was
das Herstellerdokument vermuten lässt. Mehrere Annahmen im aktuellen Code sind
damit bestätigt, eine zentrale ist widerlegt.

Alles hier Beschriebene wurde am **2026-09-06** an einem physischen
BLE-35BWRY reproduziert.

## Testaufbau

| | |
|---|---|
| Label | `66:66:17:40:27:77`, Advertising-Name `WL17402777` |
| Host | Windows 11, Python 3.10.11, bleak 1.1.1 (WinRT-Backend) |
| Verbindung | **direkter** BLE-Adapter, kein ESPHome-Proxy, kein Home Assistant |
| Abstand | Label direkt am Rechner, RSSI −53 dBm |

Die Home-Assistant-Integration war während der Messungen deaktiviert, damit
nichts um den einzigen Verbindungsslot konkurriert.

Das verwendete Referenzskript liegt unter
[`hardware-verification/ble35bwry_reference.py`](hardware-verification/ble35bwry_reference.py).
Es ist bewusst minimal und ohne Home-Assistant-Abhängigkeiten, damit die
Messung nachvollziehbar bleibt.

---

## 1. GATT-Aufbau — bestätigt

Vollständiger Discovery-Dump:

```
Service 00001800-0000-1000-8000-00805f9b34fb   (Generic Access)
  Char 00002a00-…  props=['read', 'notify']
  Char 00002a01-…  props=['read']
  Char 00002a04-…  props=['read']
Service 00001801-0000-1000-8000-00805f9b34fb   (Generic Attribute)
  Char 00002a05-…  props=['indicate']
Service 30323032-4c53-4545-4c42-4b4e494c4f57   ← alle Nutzdaten hier
  Char 35323032-4c53-4545-4c42-4b4e494c4f57  props=['read', 'notify']   Batterie
  Char 34323032-4c53-4545-4c42-4b4e494c4f57  props=['read', 'notify']   Status
  Char 33323032-4c53-4545-4c42-4b4e494c4f57  props=['read', 'write']    Security
  Char 31323032-4c53-4545-4c42-4b4e494c4f57  props=['read', 'write']    Command
  Char 32323032-4c53-4545-4c42-4b4e494c4f57  props=['read', 'notify']   Version
```

**Die fünf UUIDs in `const.py` sind korrekt.** Wichtig ist die Struktur: es sind
**Characteristics eines einzigen Service** `30323032-…`, nicht fünf eigene
Services. Wer sie als Service-UUIDs auflöst (`get_service()`), bekommt `None` —
das ist der ursprüngliche Fehler des Vorgängerskripts und eine plausible
Erklärung für frühere "Characteristic not found"-Meldungen.

### Nebenbefund: Write-ohne-Response gibt es nicht

Die Command-Characteristic meldet ausschließlich `['read', 'write']` — **kein**
`write-without-response`. Die Option `CONF_WRITE_MODE` mit dem Wert
`without_response` (`WRITE_MODE_NO_RESPONSE` in `const.py`) kann an diesem Label
also nicht funktionieren. `auto` und `with_response` sind gleichwertig richtig.

---

## 2. AES-Unlock — bestätigt, Variante `encrypt`

Ablauf laut Doku, exakt so umgesetzt und akzeptiert:

1. 16 Byte Challenge von `33323032-…` **lesen**
2. Mit AES-128-**ECB** und dem Herstellerschlüssel **verschlüsseln**
3. Ergebnis auf dieselbe Characteristic **zurückschreiben**

Messung direkt nach dem Unlock:

```
Entsperrt.
Status: BUSY=0 ERR=0 (kein Fehler), Batterie: 2947 mV
```

`ERR=0` — nicht `5` (`unlock_failed`). Anschließend wurden **98 aufeinander­
folgende Kommando-Writes** akzeptiert, ohne dass das Label die Verbindung
gekappt hat.

**Folgerung:** `DEFAULT_UNLOCK_VARIANT = "encrypt"` ist richtig. Die übrigen
Einträge in `UNLOCK_VARIANTS` (`decrypt`, `*_reversed`, `echo`) und der
`debug_unlock_sweep` werden nicht gebraucht. Das Weglassen der Challenge (also
das Verschlüsseln von 16 Nullbytes, wie im pre-HACS-Prototyp) ist dagegen
definitiv falsch.

---

## 3. ⚠️ Byte-Reihenfolge der Kommandos — Little-Endian, nicht Big-Endian

**Das ist der zentrale Fund und er widerspricht dem aktuellen Code.**

`const.py` definiert die Opcodes Big-Endian:

```python
CMD_IMAGE_STORE: Final = b"\xa5\x00"
CMD_IMAGE_REFRESH_RAW: Final = b"\xa5\x01"
CMD_CLEAR: Final = b"\xa5\x04"
```

Am Gerät funktioniert hat dagegen **Little-Endian**:

| Kommando | Gesendete Bytes | Ergebnis |
|---|---|---|
| `0xA500` Bilddaten speichern | `00 a5` + Pointer 4B LE + Daten | akzeptiert, 98×|
| `0xA501` unkomprimiert auffrischen | `01 a5` + Größe 4B LE | Panel zeichnet neu |

Belegt durch **drei** unabhängige, vollständige Übertragungen, nach denen sich
das Display jeweils sichtbar geändert hat:

1. 8832 Byte (erster Versuch, noch mit falscher 1-bpp-Annahme) → Schachbrett
   erschien auf etwa der halben Fläche
2. 17664 Byte Kalibrierbild → vier Farbbalken über die gesamte Fläche
3. 17664 Byte Testkarte → vollflächig, korrekt

Log der dritten Übertragung:

```
Verbunden.
Entsperrt.
Status: BUSY=0 ERR=0 (kein Fehler), Batterie: 2947 mV
Sende Bild ...
  ... 3600/17664 Bytes
  ... 7200/17664 Bytes
  ... 10800/17664 Bytes
  ... 14400/17664 Bytes
  ... 17664/17664 Bytes
Gesendet, Display frischt jetzt auf.
```

### Warum die bisherige Gegenthese vermutlich ein Messartefakt ist

`protocol.py` stuft Little-Endian aktiv als widerlegt ein:

```python
bytes((0x04, 0xA5)),  # 3.7, little endian opcode - known rejected
```

Diese Einstufung entstand im `clear_screen_candidates()`-Sweep. Dort steht
`a5 04` an **erster** und `04 a5` an **vorletzter** Stelle. Laut der eigenen
Dokumentation in `async_command_sweep` entzieht ein abgelehntes Kommando die
Autorisierung (ATT-Fehler 0x08) und nimmt die Verbindung mit. Ein früher
Kandidat vergiftet also alle folgenden — `04 a5` wurde damit nie auf einer
sauberen Verbindung getestet.

### Was ausdrücklich **nicht** gemessen wurde

`a5 04` bzw. `04 a5` für **Clear Screen** wurden nicht getestet. Verifiziert ist
Little-Endian ausschließlich für `0xA500` und `0xA501`. Der saubere
Gegenbeweis wäre ein A/B-Test beider Schreibweisen, **jeweils auf einer eigenen,
frisch aufgebauten Verbindung**.

---

## 4. Bildformat und Palette — bestätigt und jetzt belegt

`imaging.py` `_pack_bwry()` mit `bit_order="msb"` ist **exakt richtig**:

- **2 Bit pro Pixel**, 4 Pixel pro Byte, **MSB zuerst**, zeilenweise (row-major)
- 184 × 384 → 46 Byte/Zeile → **17664 Byte** für ein Vollbild

Die Palettenreihenfolge in `BWRY_PALETTE` stimmt ebenfalls. Gemessen mit einem
Kalibrierbild aus vier gleich hohen Bändern, jedes mit einem konstanten
2-Bit-Code gefüllt:

| Band (oben → unten) | Code | Bytewert | Angezeigte Farbe |
|---|---|---|---|
| 1 | `00` | `0x00` | **Schwarz** |
| 2 | `01` | `0x55` | **Weiß** |
| 3 | `10` | `0xAA` | **Gelb** |
| 4 | `11` | `0xFF` | **Rot** |

Das entspricht `BWRY_PALETTE = [schwarz, weiß, gelb, rot]` Index für Index.
Der Kommentar *"the index is the two bit code written to the panel; the order is
unverified"* kann entfallen — die Reihenfolge ist verifiziert.

Gegenprobe: Der erste Versuch mit 1 bpp erzeugte Läufe aus je 20 gleichen Bits.
Paarweise als 2-Bit-Codes gelesen ergibt das abwechselnd `00` und `11` — auf dem
Display erschien exakt Schwarz/Rot, und nur die halbe Fläche wurde beschrieben,
weil 8832 Byte genau die Hälfte von 17664 sind.

`bwry_packed` sollte damit fester Default für BWRY-Panels sein; `bwry_planes`
und `lsb` sind für dieses Modell widerlegt.

---

## 5. Advertisement — bestätigt

Live aufgezeichnet:

```
66:66:17:40:27:77  name=WL17402777  rssi=-53
mfg={48042: b'0\x00\x00\x0e\x030\x02\x01\x0b\x8b'}
```

- Company-ID **48042 = 0xBBAA** — der Matcher in `manifest.json` greift korrekt
- Payload **10 Byte**: `30 00 00 0e 03 30 02 01 0b 8b`, passt zu `ADV_PAYLOAD_LEN`
- Batterie big-endian ab Offset 8: `0x0b8b` = **2955 mV**

Gegenprobe über GATT in derselben Sitzung: **2947 mV**. Die gemischte
Byte-Reihenfolge (Versionsfelder little-endian, Batterie big-endian), die
`parse_advertisement()` implementiert, ist damit bestätigt.

---

## 6. Advertising-Verhalten — die eigentliche Verbindungshürde

Das Label advertised **sehr selten und unregelmäßig**. Gemessen mit
kontinuierlichem aktivem Scan:

| Lauf | Scan-Fenster | Ergebnis |
|---|---|---|
| A | 10 s | nicht gefunden |
| B | 12 s | nicht gefunden |
| C | 20 s gezielt | nicht gefunden |
| D | 45 s (Callback) | **gefunden** |
| E | 3 × 30 s | erst im 3. Versuch gefunden |
| F | 4 × 30 s (nach einem Transfer) | **gar nicht gefunden** (>120 s) |
| G | 3 × 45 s | erst im 3. Versuch gefunden |

Ein einzelnes Scan-Fenster von 10–30 s findet das Label also **regelmäßig
nicht**, obwohl es 20 cm entfernt liegt und mit −53 dBm sendet.

Verbinden ist nur im kurzen Fenster rund um ein Advertisement möglich. Ein
**verbundenes** BLE-Gerät sendet zudem gar keine Advertisements mehr — solange
irgendetwas die Verbindung hält, ist das Label für alles andere unsichtbar.

---

## 7. Warum Home Assistant derzeit nicht verbindet

Nach Wichtigkeit geordnet:

### 7.1 Konkurrenz um den einzigen Verbindungsslot

`DEFAULT_LINGER_S = 60` hält die Verbindung nach jedem Kommando 60 s offen und
verwendet sie wieder. Das ist für Kommando-Bursts sinnvoll, macht das Label
aber für diese Zeit unsichtbar. Beobachtet wurde beides: Das Label war
unauffindbar, solange etwas anderes verbunden war, und war direkt nach unseren
Transfers minutenlang wieder weg (Lauf F oben).

**Für Diagnosen gilt: immer nur *ein* Client gleichzeitig.**

### 7.2 `ADVERTISEMENT_WAIT_S = 180` ist grenzwertig

`device.py:70`. Lauf F oben blieb über 120 s erfolglos, Läufe E und G brauchten
je rund 90–135 s. 180 s liegen damit knapp über dem Beobachteten — ein
schlafendes Label lässt `_async_wait_for_connectable()` regelmäßig in den
`HomeAssistantError` laufen.

### 7.3 Ein falscher Opcode sieht aus wie ein Verbindungsfehler

Da eine Ablehnung die Autorisierung entzieht und die Verbindung mitnimmt,
äußert sich Abschnitt 3 nicht als saubere Protokollmeldung, sondern als
Abbruch bzw. als "Characteristic not found" beim nächsten Zugriff. Die
README-Beobachtung *"Testbild ~75 Chunks, dann Characteristic not found"* passt
zu diesem Muster.

### 7.4 Chunk-Größe aus der Fallback-MTU

`_chunk_size()` in `protocol.py` rechnet ohne bekannte MTU mit
`_FALLBACK_MTU = 23` → `23 − 3 − 6` = **14 Byte Nutzdaten pro Write**. Ein
Vollbild braucht damit ~1262 Writes; über einen Proxy dauert das Minuten und
ist entsprechend anfällig.

Zum Vergleich: Die verifizierten Läufe nutzten **180 Byte Nutzdaten pro Write**
(Frame also 186 Byte) mit `response=True` und liefen ohne einen einzigen
Fehlversuch durch.

---

## 8. Empfohlene Änderungen

Bewusst **nicht** in diesem PR umgesetzt — dieser PR dokumentiert nur.

| # | Datei | Änderung | Belegt durch |
|---|---|---|---|
| 1 | `const.py` | Opcodes auf Little-Endian (`b"\x00\xa5"`, `b"\x01\xa5"`, …) | Abschn. 3 |
| 2 | `protocol.py` | `known rejected`-Kommentar entfernen, Sweep-Reihenfolge umdrehen | Abschn. 3 |
| 3 | `protocol.py` | `_FALLBACK_MTU`-Ableitung ersetzen, größere Chunks | Abschn. 7.4 |
| 4 | `imaging.py` | `bwry_packed` + `msb` als fester Default, Palette als verifiziert markieren | Abschn. 4 |
| 5 | `const.py` | `WRITE_MODE_NO_RESPONSE` entfernen oder als nicht unterstützt kennzeichnen | Abschn. 1 |
| 6 | `device.py` | `ADVERTISEMENT_WAIT_S` erhöhen, `DEFAULT_LINGER_S` senken | Abschn. 6, 7.1, 7.2 |
| 7 | `protocol.py` | `UNLOCK_VARIANTS` auf `encrypt` reduzieren, Sweep entfernen | Abschn. 2 |

**Reihenfolge:** Zuerst #1 (ohne die richtige Byte-Reihenfolge lässt sich nichts
anderes sinnvoll testen), dann #3, dann der Rest.

## 9. Offene Punkte

- **Clear Screen** (`0xA504`) wurde in keiner Byte-Reihenfolge getestet. Vor #1
  wäre ein A/B-Test `a5 04` gegen `04 a5` auf je eigener Verbindung sinnvoll.
- **`0xA502`** (blockkomprimiert) und die Kompression selbst sind unberührt —
  das Herstellerdokument beschreibt das Verfahren nicht.
- **Multi-Screen** (`0xA503` / `0xA509`, Slot-Header `PIC0x\0`) ungetestet.
- **RGB-LED** (`0xA508`) ungetestet. Das Timing-Layout gilt weiter als Annahme.
- Alle Messungen stammen von **einem** Exemplar über einen **direkten** Adapter.
  Verhalten über einen ESPHome-Proxy kann abweichen, besonders bei MTU und
  Timing.
