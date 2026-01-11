# OpenGrowBox Dev Environment Templates

## 📋 Überblick

Diese Templates zeigen, wie Devices und Entities in Home Assistant konfiguriert werden müssen, damit sie vom OpenGrowBox DeviceManager und RegistryListener korrekt erkannt werden.

## 🚀 Schnellstart

1. **Area erstellen:**
   - Name: `dev_growbox`
   - ID: `dev_growbox`

2. **Template-Datei in HA laden:**
   - Kopieren Sie `01_main_setup.yaml` nach `/config/`
   - Home Assistant neu starten oder Configuration neu laden

3. **OGB konfigurieren:**
   ```yaml
   opengrowbox:
     rooms:
       - name: "dev_growbox"
   ```

4. **Validieren:**
   - HA Developer Tools → States: Prüfen Sie `switch.dev_light_switch_01`
   - HA Developer Tools → Dev Info: Device Labels prüfen
   - OGB Logs: `tail -f home-assistant.log | grep -i "registry\|device"`

## 📊 Datei-Beschreibungen

### `01_main_setup.yaml`
Vollständige Konfiguration für alle Device-Typen:
- Area-Definition
- Template-Sensoren (Temperature, Humidity, CO2, etc.)
- Template-Switches (für alle Device-Types)
- Template-Lights (Main Light + Climate Control)
- Input Numbers (Test-Werte)
- Input Booleans (Device-States)

### `02_device_types.yaml`
Referenz aller verfügbaren Device-Typen:
- Alle Label-Keywords pro Device-Typ
- Entity-Namings-Konventionen
- Device-Kombinations-Beispiele

## 🔍 Kritische Konventionen

### 1. Area-Assignment (WICHTIG!)
```yaml
# Area MUSS mit OGB-Raum-Namen übereinstimmen
area:
  - name: "Dev GrowBox"
    id: dev_growbox  # ✅ RICHTIG
```

### 2. Device-Konfiguration
```yaml
device:
  unique_id: dev_light_main_01  # ✅ Eindeutig
  device_id: dev_light_main_01
  area_id: dev_growbox  # ✅ WICHTIG
  labels:
    - "light"  # ✅ Definiert Device-Typ
```

### 3. Entity-Konfiguration
```yaml
entity:
  unique_id: dev_temperature_01  # ✅ Eindeutig
  device_id: dev_light_main_01  # ✅ MUSS Device-ID entsprechen
  area_id: dev_growbox  # Optional, aber empfohlen
```

## 🎯 Device-Erkennungs-Logik

### RegistryListener Discovery-Prozess:

1. **Area-Filtrierung:**
   ```python
   devices_in_room = {
       device.id: device
       for device in device_registry.devices.values()
       if device.area_id == room_name  # dev_growbox
   }
   ```

2. **Entity-Filtrierung:**
   ```python
   # Nur Entities von Devices im Raum
   if entity.device_id not in devices_in_room:
       return None

   # Relevante Prefixes oder Keywords
   if not (entity.entity_id.startswith(RELEVANT_PREFIXES) or
           any(keyword in entity.entity_id for keyword in RELEVANT_KEYWORDS)):
       return None
   ```

3. **Label-basierte Typisierung:**
   ```python
   # Device-Typ wird aus Labels bestimmt
   if any(kw in label_names for kw in fridgegrow_keywords):
       detected_type = "FridgeGrow"

   # Prioritierung: Exact Match vor Contains
   if label_name in keywords:
       detected_type = device_type
   ```

## 📋 Validierungs-Checkliste

### ✅ Prüfpunkte:

**Area:**
- [ ] Area-ID existiert: `dev_growbox`
- [ ] Area-ID entspricht OGB-Raum-Namen
- [ ] Area ist Devices zugewiesen

**Device:**
- [ ] `unique_id` ist eindeutig
- [ ] `device_id` ist eindeutig
- [ ] `area_id` ist korrekt zugewiesen
- [ ] Labels sind definiert
- [ ] Label-Keywords sind korrekt (siehe `02_device_types.yaml`)

**Entity:**
- [ ] `entity_id` hat gültiges Format (`domain.name`)
- [ ] `device_id` entspricht Parent-Device
- [ ] Entity-Namen haben relevante Keywords
- [ ] Relevantes Prefix ist vorhanden (`number.`, `switch.`, etc.)

## 🔧 Troubleshooting

### Problem: "Keine Devices gefunden"

**Lösung:**
1. Area-ID prüfen: `area_id: dev_growbox`
2. Area in HA UI erstellen falls nötig
3. HA Registry reload: `homeassistant.reload`

### Problem: "Device nicht erkannt"

**Lösung:**
1. Labels prüfen: `labels: ["light"]`
2. Label-Keywords validieren: siehe `02_device_types.yaml`
3. Device-Neustart: HA neu starten

### Problem: "Entities nicht gefunden"

**Lösung:**
1. `device_id` prüfen: muss Parent-Device `unique_id` entsprechen
2. Entity-Name prüfen: relevante Keywords enthalten
3. Prefix prüfen: `number.`, `switch.`, `sensor.`

### Problem: "Falscher Device-Typ erkannt"

**Lösung:**
1. Label-Priorität prüfen: Exact Match vor Contains
2. Keywords validieren: siehe DEVICE_TYPE_MAPPING
3. Label-Schreibweise prüfen: Case-sensitive

## 🚀 Test-Szenarien

### Minimal Setup (1 Device)
```bash
# Nur ein Light mit Intensity-Sensor
input_number:
  dev_light_intensity:
    name: "Dev Light Intensity"
    min: 0
    max: 100
    step: 1

light:
  - platform: template
    name: "Dev Light"
    unique_id: dev_light_main_01
    device_id: dev_light_main_01
    area_id: dev_growbox
    labels:
      - "light"

sensor:
  - platform: template
    name: "Dev Light Intensity"
    unique_id: dev_light_intensity_01
    device_id: dev_light_main_01  # GLEICHES DEVICE!
    area_id: dev_growbox
    unit_of_measurement: "%"
```

### Vollständiges Setup (Alle Device-Types)
- Kopieren Sie `01_main_setup.yaml`
- Alle Devices werden erstellt
- Testen Sie jeden Device-Typ separat

### Multi-Sensor Setup (Mehrere Entities pro Device)
```yaml
# Ein Device mit mehreren Sensors
device_id: dev_sensor_hub_01

sensor:
  - unique_id: dev_temperature_01
    device_id: dev_sensor_hub_01
    # ... temperature sensor config

  - unique_id: dev_humidity_01
    device_id: dev_sensor_hub_01  # GLEICHES DEVICE!
    # ... humidity sensor config

  - unique_id: dev_dewpoint_01
    device_id: dev_sensor_hub_01  # GLEICHES DEVICE!
    # ... dewpoint sensor config
```

## 📞 Debug-Logging

### RegistryListener Debug-Logs aktivieren:
```yaml
logger:
  default: info
  logs:
    custom_components.opengrowbox.OGBController.RegistryListener: debug
    custom_components.opengrowbox.OGBController.OGBDevices: debug
    custom_components.opengrowbox.OGBController.managers.OGBDeviceManager: debug
```

### Wichtige Log-Messages:
```bash
# Erfolgreiche Discovery
RegistryListener: Found X devices in room 'dev_growbox'
RegistryListener: Device identified as Light via label match

# Fehlende Devices
RegistryListener: No devices found in room 'dev_growbox'

# Fehlende Labels
RegistryListener: Device has no labels, using entity name detection
```

## 🎯 Best Practices

1. **Konsistente Namens-Konventionen:**
   - Prefix: `dev_` für alle Dev-Entities
   - Device-Type: `light_main`, `sensor_hub`, etc.
   - Entity-Typ: `temperature_01`, `humidity_01`, etc.

2. **Eindeutige IDs:**
   - `unique_id`: `{prefix}_{device_type}_{number}`
   - `device_id`: `{prefix}_{device_type}_{number}`

3. **Label-Verwendung:**
   - Mindestens ein Label pro Device
   - Label-Keywords aus `02_device_types.yaml` kopieren
   - Case-sensitive Schreibweise beachten

4. **Testing:**
   - Beginnen Sie mit einem Device
   - Validieren Sie Device-Erkennung in Logs
   - Fügen Sie schrittweise weitere Devices hinzu

## 📚 Zusätzliche Ressourcen

- OpenGrowBox Wiki: [Link]
- Home Assistant Documentation: https://www.home-assistant.io/docs/
- HA Device Registry: Developer Tools → Dev Info → Devices
- HA Entity Registry: Developer Tools → Dev Info → Entities

---

**Viel Erfolg beim Dev Environment Setup!** 🌱