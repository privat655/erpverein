# Verein ERP

ERPNext/Frappe custom app for Vereinsverwaltung

## Zielumgebung

- Frappe: `v16.23.1`
- ERPNext: `v16.23.1`
- App: `verein_erp` `0.1.2`
- Deployment-Ziel: eigenes ERPNext-Image ueber GitHub Actions/GHCR, danach rootless Podman Quadlet auf `host01`

## Erste Feature-Scheibe

`Mitglied` ist ein App-eigener DocType fuer Mitglied-Stammdaten. Er ist nicht buchbar, nicht submittable und erzeugt keine Buchungen.

Die App ergaenzt `Customer.mitglied` als codeverwaltetes Custom Field. `Mitglied.customer` und `Customer.mitglied` bilden eine serverseitig validierte 1:1-Beziehung und werden durch App-Code synchronisiert.

Kontodaten werden bewusst nicht in `Mitglied` dupliziert. Fuer Zahlungskonto-Daten werden die ERPNext-Mechanismen rund um Customer/Bank Account genutzt; `Mitglied` speichert nur die Lastschriftmandats-Metadaten.

## Lokale Installation

```bash
bench --site <site> install-app verein_erp
bench --site <site> migrate
bench --site <site> run-tests --app verein_erp
```

## Normales Update

Fuer normale Updates nicht deinstallieren und neu installieren.

```bash
bench --site <site> backup --with-files
bench --site <site> migrate
bench --site <site> run-tests --app verein_erp
```

Bei produktiven Image-Deployments vor `bench migrate` ein Backup mit Dateien erstellen und `site_config.json` inklusive `encryption_key` sichern.
