## DLI Light Control Mode (Deutsch)

Diese Anleitung erklärt den DLI-basierten Lichtsteuerungsmodus in OpenGrowBox (OGB). Sie beschreibt Voraussetzungen, wie OGB DLI (Daily Light Integral) verwendet, um dimmbare Leuchten automatisch anzupassen, welche Datenfelder benötigt werden und wie man typische Probleme löst.

## Kurzbeschreibung

- Zweck: OGB kann die Helligkeit dimmbarer Leuchten automatisch anpassen, um ein Ziel‑DLI (mol·m²·d⁻¹) für die aktuelle Pflanzenphase zu erreichen.
- Kernmechanismus: OGB vergleicht die aktuell gemessene DLI mit dem Zielwert aus dem gewählten Lichtplan und passt die Geräte-"Voltage" (d.h. Helligkeits-Prozent) schrittweise an.

## Voraussetzungen

1. Die Leuchte muss dimmbar sein.
2. OGB-Steuerung für Licht muss aktiviert sein.
3. Der Steuerungsmodus muss auf DLI gesetzt sein.
4. Ein Light-Plan muss ausgewählt sein und passende Kurven für `*Veg` oder `*Flower` vorhanden sein.
5. Pflanzphase muss gesetzt sein und die relevanten Growstartdaten bzw. Blütenwechseldatum muss gesetz sein.
6. Min/Max-Werte für Licht sollten konfiguriert sein unter `DeviceMinMax.Light` (mind. `minVoltage` und `maxVoltage`).
   *HINWEIS: Wenn diese nicht gesetzt sind wird ein Bereich von 10-100% als mögliche Lichtstärke angenommen.*
7. Licht Sensor muss für den Raum vorhanden sein. Das Updateintervall des Senssors darf nicht kleiner als 10 Sekunden. (Empfohen 1 Minute).

## UI / Einstellungs-Checkliste (Was prüfen, wenn DLI nicht wirkt)

1. Ist OGB_LightControl auf DLI? (OGB → Einstellungen)
2. Steht OGB_Light_ControlType auf `DLI`? Wenn nicht, DLI-Modus aktivieren.
3. Ist das Gerät dimmbar? Sonst ist DLI-Regelung nicht möglich.
4. Ist ein OGB_LightPlan ausgewählt und enthält er eine passende Kurve (*veg/*flower)?
5. Sind OGB_GrowStartDate bzw. OGB_BloomSwitchDate gesetzt? Diese werden zur Ermittlung der Woche benötigt.
6. Sind OGB_Light_Volt_Min und OGB_Light_Volt_May sinnvoll gesetzt (z. B. 20–100)? Wenn OGB_Light_MinMax deaktiviert ist, nimmt OGB Standard 20–100%.
7. Liefert der Sensor tatsächlich DLI (oder PPFD/Lux mit Umrechnung)? Prüfe `OGB_DLI` bzw. das Sensor-Log.
8. Prüfe Logs: Das Light-Device loggt informative Zeilen (z. B. „DLI target for week ...“, Anpassungen, Gründe für Nicht-Anpassung).

## Wie OGB DLI anwendet (Algorithmus, kurz)
1. OGB liest die aktuelle DLI
2. Es prüft Lichtkontrolltye und agiert nur, wenn dieser auf `DLI` steht.
3. OGB bestimmt die aktuelle Pflanzenphase (`*Veg` oder `*Flower`) und berechnet die aktuelle Grow- bzw Blütenwoche.
4. Aus dem gewählten Light-Plan wird für die Woche das DLI Target gelesen.
5. OGB vergleicht Ist-DLI mit Ziel-DLI:
   - Toleranz: ±5%
   - Anpassungsschritt: 1% pro Update
   - Wenn Ist < Ziel*(1 - Toleranz) → Voltage += 1% (bis Maximum)
   - Wenn Ist > Ziel*(1 + Toleranz) → Voltage -= 1% (bis Minimum)
   - Andernfalls → keine Änderung
6. Die neue Dimmereinstellung wird gesetzt und an die Leuchte gesendet.

Die Lichtwerte sind wie folgt vorkonfiguriert:

### Photoperiodische Pflanzen
#### Vegitationssphase 
| Woche | PPFDTarget (μmol·m⁻²·s⁻¹) | DLITarget (mol·m⁻²·d⁻¹) |
| ----: | ------------------------: | ----------------------: |
|     1 |                       200 |                      12 |
|     2 |                       300 |                      20 |
|     3 |                       350 |                      25 |
|     4 |                       400 |                      30 |

#### Blütephase 
| Woche | PPFDTarget (μmol·m⁻²·s⁻¹) | DLITarget (mol·m⁻²·d⁻¹) |
| ----: | ------------------------: | ----------------------: |
|     1 |                       450 |                      25 |
|     2 |                       600 |                      35 |
|     3 |                       700 |                      40 |
|     4 |                       800 |                      45 |
|     5 |                       850 |                      48 |
|     6 |                       900 |                      50 |
|     7 |                       900 |                      50 |
|     8 |                       900 |                      50 |

## Autoflower
### Vegitationssphase
| Woche | PPFDTarget (μmol·m⁻²·s⁻¹) | DLITarget (mol·m⁻²·d⁻¹) |
| ----: | ------------------------: | ----------------------: |
|     1 |                       200 |                      15 |
|     2 |                       300 |                      22 |
|     3 |                       400 |                      28 |

### Blütephase
| Woche | PPFDTarget (μmol·m⁻²·s⁻¹) | DLITarget (mol·m⁻²·d⁻¹) |
| ----: | ------------------------: | ----------------------: |
|     4 |                       500 |                      32 |
|     5 |                       600 |                      35 |
|     6 |                       700 |                      38 |
|     7 |                       750 |                      40 |
|     8 |                       800 |                      42 |


## Formeln: DLI ↔ PPFD
- Begriffe:
  - PPFD (Photonenflussdichte): μmol·m⁻²·s⁻¹
  - DLI (Daily Light Integral): mol·m⁻²·d⁻¹

- Umrechnung:
  - DLI = PPFD × Sekunden_bei_Licht / 1.000.000
    - Beispiel: 16 Stunden Licht → Sekunden = 16 × 3600 = 57.600
    - DLI = PPFD × 57.600 / 1.000.000 = PPFD × 0,0576
  - PPFD = DLI × 1.000.000 / Sekunden_bei_Licht
    - Beispiel: DLI = 30, Lichtdauer = 12 h → Sekunden = 43.200
    - PPFD = 30 × 1.000.000 / 43.200 ≈ 694 μmol·m⁻²·s⁻¹
*Hinweis zu Lux: OGB speichert einen `luxToPPFDFactor` (Standard z. B. 15.0) zur groben Umrechnung von Lux → PPFD, falls nur Lux-Sensoren vorliegen. Lux → PPFD ist aber sehr abhängig vom Lichtspektrum und bleibt eine Näherung.*



## Häufige Ursachen & Troubleshooting

- Keine Anpassungen: `controlOptions.lightControlType` nicht auf "DLI" oder `lightbyOGBControl` = false.
- Kein DLITarget verfügbar: Plan fehlt oder für die aktuelle Woche kein Eintrag → es wird das letzte Wochen-Ziel genutzt oder es tritt ein Fehler auf (siehe Log).
- Voltage bleibt am Minimum/Maximum: `DeviceMinMax.Light` begrenzt die Werte.
- Falsche Woche: Prüfe `plantDates.*` (Datumformat: YYYY-MM-DD). OGB berechnet Wochen relativ zu diesen Daten.
- Messwerte unplausibel: Lux→PPFD Umrechnung ungenau; besser direkte PPFD/DLI-Sensoren verwenden.
- Sonnenphasen (SunRise/SunSet) können während ihrer Sequenzen manuelle Änderungen blockieren oder verhindern (SunPhaseActive). OGB vermeidet Änderungen während SunPhases.

## Tipps & Hinweise

- Anpassungsrate ist konservativ (±1% pro Update, ±5% Toleranz). Das ist bewusst so gewählt, um Überschwinger zu vermeiden.
- Wenn Sie schneller reagieren möchten, passen Sie in der Codebasis `calibration_step_size` und `dli_tollerance` an (nur für Fortgeschrittene).
- Achten Sie darauf, dass mehrere Lichtsteuerungsmechanismen (z. B. VPD-basiert und DLI) nicht gleichzeitig aktiv sind, wenn sie widersprüchliche Aktionen auslösen.

## Testen (Kurz)

1. Stelle `controlOptions.lightbyOGBControl` = true und `controlOptions.lightControlType` = "DLI".
2. Wähle einen Light-Plan und setze `plantDates.growstartdate` so, dass die aktuelle Woche einen DLITarget-Eintrag hat.
3. Setze `Light.dli` testweise per Event oder Datastore auf einen Wert unter/über dem DLITarget und beobachte Logs und `voltage`-Änderungen.

