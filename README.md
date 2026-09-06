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

## Bekannte Unsicherheiten

Diese Punkte sind im Herstellerdokument **nicht** spezifiziert und daher im
Code als begründete Annahme umgesetzt. Sie sind bewusst an einer Stelle
gebündelt, damit sie leicht korrigierbar sind:

- **Pixelformat der Bilddaten.** Die Doku beschreibt nur den Transport. Die
  Packung in `imaging.py` nimmt für BWRY-Panels 2 Bit pro Pixel mit der
  Palettenreihenfolge Schwarz/Weiß/Gelb/Rot an, für andere Panels 1 Bit pro
  Pixel. Anpassbar über `BWRY_PALETTE` und `MONO_BLACK_BIT`.
- **Blockkomprimierung** (Abschn. 3.3/3.9) ist nicht beschrieben; es wird
  ausschließlich unkomprimiert über `0xA501` übertragen.
- **Byte-Reihenfolge** von `on_ms`/`off_ms`/`work_ms` (Abschn. 3.8) und der
  Bildgröße (Abschn. 3.2) — angenommen wird Little Endian, passend zum
  4-Byte-Datenzeiger.
- **Company Identifier** im Advertisement: Die Doku nennt `0xbbaa` für Byte
  0–1. Die Integration akzeptiert sowohl `0xBBAA` als auch `0xAABB`.
- **Slot-Header** `PIC0x\0` (Abschn. 3.9) lässt bei Index 10 nur eine Ziffer
  zu; implementiert ist zweistellig, also `PIC00`…`PIC10`.
- **Panel-Auflösungen** in `const.py` stammen nicht aus der Doku und können
  je nach Gerät abweichen.

## Fehlersuche

Debug-Logging aktivieren:

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
