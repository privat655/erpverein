# ERPverein

ERPNext/Frappe custom app for Vereinsverwaltung

## Zielumgebung

- Frappe: `v16.27.0`
- ERPNext: `v16.28.0`
- App: `erpverein` `0.1.7`
- Deployment-Ziel: eigenes ERPNext-Image ueber GitHub Actions/GHCR, danach rootless Podman Quadlet auf `host01`

## Rename-Reset

Die Umbenennung auf die Paket- und App-Kennung `erpverein` ist ein Identitaets-Reset. Sie wird nur als Neuinstallation auf einer sauberen Site unterstuetzt; alte Development-Sites aus der Zeit vor der Umbenennung werden nicht migriert und sind ausdruecklich nicht unterstuetzt.

## Erste Feature-Scheibe

`Mitglied` ist ein App-eigener DocType fuer Mitglied-Stammdaten. Er ist nicht buchbar, nicht submittable und erzeugt keine Buchungen.

Die App ergaenzt `Customer.erpverein_mitglied` als codeverwaltetes Custom Field. `Mitglied.customer` und `Customer.erpverein_mitglied` bilden eine serverseitig validierte 1:1-Beziehung und werden durch App-Code synchronisiert.

Kontodaten werden bewusst nicht in `Mitglied` dupliziert. Fuer Zahlungskonto-Daten werden die ERPNext-Mechanismen rund um Customer/Bank Account genutzt; `Mitglied` speichert nur die Lastschriftmandats-Metadaten.

## Lokale Installation

```bash
bench --site <site> install-app erpverein
bench --site <site> migrate
bench --site <site> set-config allow_tests true
bench --site <site> run-tests --module erpnext.tests.bootstrap_test_data --lightmode
bench --site <site> run-tests --app erpverein
```

## Vorproduktions-Reset

Der aktuelle Stand ist eine patchfreie Vorproduktionsbasis. Bestehende Entwicklungs-Sites werden nicht aktualisiert, sondern vollstaendig neu erstellt. Erst nach Festlegung der Produktionsbasis werden normale Update-Pfade mit unveraenderlicher Migrationshistorie unterstuetzt.

## Releases und Images

Release-Tags folgen `erpverein-v<erpnext-version>-<app-version>`, fuer diesen Stand `erpverein-v16.28.0-0.1.7`. Das Image wird als `ghcr.io/<owner>/erpverein:<tag>` veroeffentlicht.

Der Image-Workflow baut und laedt exakt ein `linux/amd64`-Image. Vor dem Push erstellt er mit den gepinnten Frappe-, ERPNext-, `frappe_docker`-, MariaDB- und Redis-Komponenten eine saubere Site, installiert zuerst ERPNext und dann `erpverein` und fuehrt diese Befehle im gebauten Image aus:

```bash
bench --site ci.localhost install-app erpverein
bench --site ci.localhost migrate
bench --site ci.localhost run-tests --module erpnext.tests.bootstrap_test_data --lightmode
bench --site ci.localhost run-tests --app erpverein
```

Nur ein erfolgreich verifiziertes Image wird nach GHCR gepusht. Manuelle Workflow-Laeufe akzeptieren einen App-Git-Ref und einen optionalen Image-Tag; der Image-Tag wird auf Docker-kompatible Zeichen normalisiert.
