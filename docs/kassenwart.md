# Anleitung für den Kassenwart

Diese Anleitung beschreibt die Einrichtung und den Zahlungsplan. Den Monatsablauf für Lastschrift und Abgleich ergänzen die Phasen 2 bis 6.

Kennzeichen:

- **VORSICHT:** Ein Fehler kann Buchungen, Bankdaten oder echte Lastschriften beschädigen.
- **HINWEIS:** Zusätzliche Information.

---

## 1. Einrichtung

Führe diese Schritte einmal aus. Beginne mit der Test-Site.

### 1.1 Update einspielen

1. Spiele das neue Image ein.
2. Führe `bench --site <site> migrate` aus.
3. Das Migrate legt die ERPverein-Felder, die Zahlungsweisen „SEPA-Lastschrift“ und „Überweisung“ und die Zahlungsbedingungen „ERPverein Rate 01“ bis „ERPverein Rate 60“ an.

### 1.2 E-Mail-Sperre auf der Test-Site

**VORSICHT:** Auf der Test-Site stehen echte E-Mail-Adressen. Sperre den Versand, bevor du Rechnungen buchst.

1. Sperre den Versand: `bench --site <site> set-config mute_emails 1`
2. Gib den Versand nur für einen Test an deine eigene Adresse frei: `bench --site <site> set-config mute_emails 0`
3. Sperre den Versand danach wieder.

Bei gesperrtem Versand bleiben E-Mails in der `Email Queue` im Status `Not Sent`. Die Vorabankündigung bleibt dann `Ausstehend`.

### 1.3 E-Mail-Versand über Microsoft 365 (OAuth)

ERPNext sendet über Microsoft 365 mit OAuth. Die Sicherheitsstandards von Microsoft Entra bleiben eingeschaltet.

**HINWEIS:** Microsoft schaltet das Senden mit Passwort (SMTP AUTH, Basic Authentication) Ende Dezember 2026 standardmäßig ab. OAuth ist deshalb der richtige Weg.

In ERPNext:

1. Öffne `Connected App` und lege einen Eintrag an:
   - `Provider Name`: Microsoft
   - `OpenID Configuration`: `https://login.microsoftonline.com/<Tenant-ID>/v2.0/.well-known/openid-configuration`
   - `Scopes`: `https://outlook.office.com/SMTP.Send` und `offline_access`
2. Speichere. Kopiere die angezeigte `Redirect URI`.

Im Microsoft Entra Admin Center:

1. Öffne „App-Registrierungen“ → „Neue Registrierung“.
2. Wähle „Nur Konten in diesem Organisationsverzeichnis“.
3. Trage als Umleitungs-URI (Web) die kopierte `Redirect URI` ein.
4. Öffne „Zertifikate & Geheimnisse“. Lege einen geheimen Clientschlüssel an. Kopiere den Wert.
5. Öffne „API-Berechtigungen“. Füge bei „Microsoft Graph“ die delegierten Berechtigungen `SMTP.Send` und `offline_access` hinzu. Erteile die Administratorzustimmung.

Im Exchange Admin Center:

1. Öffne das Postfach, das die Rechnungen sendet.
2. Öffne „E-Mail-Apps verwalten“. Schalte „Authentifiziertes SMTP“ ein.

Wieder in ERPNext:

1. Trage in der `Connected App` die `Client ID` (Anwendungs-ID) und das `Client Secret` ein. Speichere.
2. Klicke „Connect to Microsoft“. Melde dich mit dem Postfach an, das die Rechnungen sendet.
3. Lege einen `Email Account` an:
   - E-Mail-Adresse: das Postfach
   - `Enable Outgoing` und `Default Outgoing`: an
   - `SMTP Server`: `smtp.office365.com`, `Port`: 587, `Use TLS`: an
   - `Authentication Method`: OAuth
   - `Connected App`: der Eintrag von oben; `Connected User`: dein ERPNext-Benutzer
   - Eingehende E-Mails: aus
4. Sende einen Test an deine eigene Adresse.

### 1.4 Briefkopf und Druckformat

1. Lege einen `Letter Head` an. Der Briefkopf ist bei DIN 5008 Form A höchstens 17 mm hoch (27 mm ab Blattrand). Die Fußzeile enthält Anschrift, Bankverbindung und Registereintrag.
2. Setze in `Customize Form` für `Sales Invoice` das Standard-Druckformat auf „ERPverein Rechnung DIN 5008“.
3. Wähle beim Versand immer dieses Druckformat. Nur dann erkennt ERPverein die Vorabankündigung.

### 1.5 Firmen-Bankkonto

1. Lege die `Bank` HVB an, wenn sie fehlt.
2. Lege ein `Bank Account` an:
   - Kontoname: HVB Vereinskonto
   - `Is Company Account`: an; Unternehmen: der Verein
   - Hauptbuchkonto: 18000 „HVB Vereinskonto“
   - IBAN und BIC des Vereinskontos
   - `Is Default`: an

### 1.6 SEPA-Exportprofil

1. Öffne „Vereinsverwaltung“ → „SEPA-Exportprofile“. Lege ein Profil an:
   - Unternehmen und Vereinskonto
   - Gläubiger-ID wie in Vereinsmeister
   - Go-live-Stichtag: 01.01.2027
   - Frist der Vorabankündigung: 14 Kalendertage
   - Vorlauf: 1 Geschäftstag
   - Gebührenkonto: 68550
2. Prüfe die Warnungen oben im Formular. Sie zeigen abweichende `Accounts Settings`.

**VORSICHT:** Die Gläubiger-ID steht nur auf der Site. Schreibe sie nie in Git, Tests oder Dokumentation.

### 1.7 Accounts Settings

| Einstellung | Wert |
|---|---|
| `Enable Automatic Party Matching` | an |
| `Enable Fuzzy Matching` | aus |
| `Automatically run rules on unreconciled transactions` | an |
| `Auto reconcile Payments` | aus |

### 1.8 Geschäftsjahr 2027

1. Öffne die Liste `Fiscal Year`.
2. Prüfe, dass 2027 existiert. ERPNext legt es drei Tage vor Jahresende selbst an.
3. Lege es sonst an: 01.01.2027 bis 31.12.2027.

### 1.9 Automatischer Versand beim Buchen

1. Lege eine `Notification` an:
   - Dokumenttyp: `Sales Invoice`; Senden bei: `Submit`; Kanal: E-Mail
   - Bedingung: `doc.subscription and doc.erpverein_billing_kind`
   - Empfänger: Dokumentfeld `contact_email`
   - Druck anhängen: an; Druckformat: „ERPverein Rechnung DIN 5008“
2. Die Bedingung schließt Rechnungen ohne ERPverein-Subscription aus. Dazu gehört die importierte Historie.

---

## 2. Zahlungsplan

### 2.1 Was ERPverein selbst erledigt

1. Jede Rechnung aus einer ERPverein-Subscription bekommt beim Anlegen einen Zahlungsplan.
2. Hat der Zahler die Abrechnungsart „Lastschrift“ und ein aktives Mandat, kommen die Zeilen aus den Einzugsterminen des Mandats.
3. Ist die Mandatssumme kleiner als der Rechnungsbetrag, entsteht eine Restzeile „Überweisung“.
4. Sonst gilt eine Zeile „Überweisung“ mit der normalen Fälligkeit.
5. Konflikte stehen im Feld „Hinweis zum Zahlungsplan“ der Rechnung, zum Beispiel eine zu hohe Mandatssumme.

### 2.2 Zahlungsplan einer gebuchten Rechnung bearbeiten

1. Öffne die Rechnung. Klicke „ERPverein“ → „Zahlungsplan bearbeiten“.
2. Ändere Fälligkeit, Betrag oder Zahlungsweise.
3. Die Summe muss dem Rechnungsbetrag entsprechen.
4. Bezahlte oder gemahnte Zeilen sind gesperrt.
5. Speichere. ERPverein protokolliert die Änderung an der Rechnung.
6. Hast du eine Lastschriftzeile geändert, steht die Vorabankündigung auf „Erneut senden“. Sende die Rechnung erneut per E-Mail.

Den Plan eines Entwurfs änderst du direkt in der Tabelle „Zahlungsplan“ der Rechnung.

### 2.3 Zahlungsplan aus dem Mandat neu erzeugen

1. Klicke „ERPverein“ → „Zahlungsplan aus Mandat neu erzeugen“.
2. Prüfe die Vorschau: bisheriger und neuer Plan.
3. Bestätige. Das geht nur, solange keine Zeile bezahlt oder gemahnt ist.

### 2.4 Nachtrag für ältere Rechnungen

1. Öffne die Liste `Sales Invoice`. Klicke im Menü „ERPverein: Zahlungsplan nachtragen“.
2. Prüfe die Vorschau. Sie zeigt gebuchte ERPverein-Rechnungen ohne Zahlungsplan.
3. Bestätige. ERPverein meldet: nachgetragen, übersprungen, fehlgeschlagen.

### 2.5 Vorabankündigung

1. Die Rechnung mit Zahlungsplan per E-Mail ist die Vorabankündigung.
2. ERPverein prüft stündlich die gesendeten E-Mails. Danach steht die Vorabankündigung auf „Versendet“.
3. Ein Zahler ohne E-Mail-Adresse bekommt die Rechnung per Brief. Klicke danach „ERPverein“ → „Vorabankündigung manuell bestätigen“.
4. Die E-Mail muss mindestens 14 Tage vor dem Einzug versendet sein.

Zeitplan für den ersten Einzug:

| Schritt | Spätestens |
|---|---|
| Rechnungen 2027 per E-Mail versenden | 18.01.2027 |
| Lastschriftdatei in PayGate hochladen | 28.01.2027 (Annahmeschluss 29.01.2027, 07:00 Uhr) |
| Einzugsdatum | 01.02.2027 |
