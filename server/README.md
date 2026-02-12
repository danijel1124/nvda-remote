# NVDA Remote Managed Relay Server

Ein erweiterter Relay-Server für [NVDA Remote](https://nvdaremote.com) mit integrierter Zugangskontrolle und Sitzungsverwaltung.

## Funktionen

- **Zugangskontrolle (Whitelist):** Nur autorisierte Sitzungsnamen (Keys) dürfen Daten übertragen.
- **Quarantäne-Modus:** Unbekannte Clients werden blockiert und erhalten alle 5 Sekunden eine Sprachansage ("Nicht autorisiert"), bis sie freigeschaltet werden.
- **Sitzungs-Tracking:** Der Server merkt sich alle jemals verbundenen Sitzungsnamen (Offline-Anzeige).
- **Admin-Schnittstelle:** Ermöglicht die Fernverwaltung über das modifizierte NVDA-Addon.
- **Docker-Unterstützung:** Einfache Bereitstellung mit automatischer Datenpersistenz.

## Installation & Start

1. **Zertifikate hinterlegen:**
   Erstelle den Ordner `server/certificate` und lege dort `cert`, `key` und `chain` ab (SSL/TLS).
2. **Starten:**
   ```bash
   docker compose up --build -d
   ```

## Administration

- **Admin-Token:** Beim ersten Start wird ein sicheres Token in `data/admin.token` generiert. Dieses wird für den Login im NVDA-Addon benötigt.
- **Daten:** 
  - `data/authorized_keys.json`: Liste der erlaubten Sitzungsnamen.
  - `data/seen_keys.json`: Liste aller jemals gesehenen Sitzungsnamen.

## Ports
Standardmäßig lauscht der Server auf Port **6837**.
