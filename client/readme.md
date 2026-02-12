# NVDA Remote Management Client

Dieses modifizierte NVDA-Addon dient als Gegenstück zum Managed Relay Server. Es bietet eine Benutzeroberfläche zur Verwaltung des Servers direkt aus NVDA heraus.

## Wichtigste Änderungen

- **Terminologie:** "Schlüssel" wurde durch **"Sitzungsname"** ersetzt.
- **Standard-Server:** `danijel0.danijels-computer.de` ist als Standard hinterlegt.
- **Server Administration:** Neuer Menüpunkt unter *Extras -> Remote*.
- **Status-Anzeige:** Übersicht über Online-, Offline- und Quarantäne-Sitzungen.
- **Token-Speicherung:** Das Admin-Token wird sicher in der NVDA-Konfiguration gespeichert.

## Verwendung

1. Installiere die `.nvda-addon` Datei.
2. Gehe zu *Extras -> Remote -> Server Administration*.
3. Gib das Admin-Token deines Servers ein und klicke auf **Login**.
4. Schalte neue Sitzungen mit **Approve** frei oder lösche alte Einträge mit **Block/Remove**.

## Build (für Entwickler)

Zum Bauen des Addons wird `scons` und `markdown` (Python-Pakete) benötigt.
```bash
scons
```
Die fertige Datei wird als `remote-3.0.nvda-addon` im Hauptverzeichnis des Clients erstellt.
