# NVDA Remote Management Client

Dieses modifizierte NVDA-Addon dient als Gegenstück zum Managed Relay Server. Es bietet eine Benutzeroberfläche zur Verwaltung des Servers direkt aus NVDA heraus.

## Wichtigste Änderungen

- **Terminologie:** "Schlüssel" wurde durch **"Sitzungsname"** ersetzt.
- **Standard-Server:** `danijel0.danijels-computer.de` ist als Standard hinterlegt.
- **Tab-basiertes Admin-GUI:** Trennung von Token-Verwaltung und Sitzungsverwaltung für mehr Übersicht.
- **Multi-Token Support:** Speicherung von Admin-Token für mehrere verschiedene Server möglich.
- **Konfigurations-Migration:** Automatische Übernahme alter Token in das neue Format beim Start.
- **Heartbeat-System:** Aktiver Austausch von Ping/Pong Nachrichten zur Stabilisierung der Verbindung.
- **Status-Anzeige:** Übersicht über Online-, Offline- und Quarantäne-Sitzungen.

## Verwendung

1. Installiere die `.nvda-addon` Datei.
2. Gehe zu *Extras -> Remote -> Server Administration*.
3. Im Tab **Token-Verwaltung** kannst du Token für verschiedene Server hinterlegen oder den Login für den aktuellen Server durchführen.
4. Nach dem Login wechselt das GUI zum Tab **Sitzungsverwaltung**. Hier kannst du neue Sitzungen mit **Approve** freischalten oder alte Einträge mit **Block/Remove** löschen.

## Build (für Entwickler)

Zum Bauen des Addons wird `scons` und `markdown` (Python-Pakete) benötigt.
```bash
scons
```
Die fertige Datei wird als `remote-3.0.nvda-addon` im Hauptverzeichnis des Clients erstellt.
