# Anleitung für den Kassenwart

Diese Anleitung beschreibt die Einrichtung, den Zahlungsplan und den monatlichen Lastschrifteinzug. Bankimport und Abgleich ergänzen die Phasen 3 bis 6.

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
4. Das Migrate legt auch die Listen „SEPA-Einzugsläufe“ und „SEPA-Einzugspositionen“ an.

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
5. Prüfe die Absenderdomain des Vereins:
   - SPF: Der DNS-Eintrag der Domain enthält `include:spf.protection.outlook.com`.
   - DKIM: Die DKIM-Signatur für die Domain ist im Microsoft Defender-Portal eingeschaltet.

**HINWEIS:** Ohne SPF und DKIM landen Rechnungen oft im Spam. Dann fehlt die Vorabankündigung beim Zahler.

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
   - Name des Gläubigers: der Kontoinhaber des Vereinskontos, höchstens 70 Zeichen. ERPverein schlägt den Namen des Unternehmens vor.
   - Go-live-Stichtag: 01.01.2027
   - Frist der Vorabankündigung: 14 Kalendertage
   - Vorabankündigung für Mieten per E-Mail: aus (Entscheidung O6: Mieter bekommen keine Vorabankündigung)
   - Vorlauf: 1 Geschäftstag
   - Einreichung frühestens: 15 Geschäftstage vor dem Einzug
   - Sammelbuchung als Standard: an
   - Höchstzahl Positionen je Datei: 1000
   - Vorlage für den Verwendungszweck: `Rechnung {invoice}` oder zum Beispiel `Beitrag {period_from} bis {period_to} Rechnung {invoice}`
   - Gebührenkonto: 68550
2. Prüfe die Warnungen oben im Formular. Sie zeigen abweichende `Accounts Settings`.
3. Ändere die Einstellung für Mieten nur vor dem ersten Mieteinzug. Vorhandene Mietrechnungen behalten sonst ihren alten Status.

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

**VORSICHT:** Lege die `Notification` erst an, wenn Briefkopf (1.4), Firmen-Bankkonto (1.5) und SEPA-Exportprofil (1.6) fertig sind. Sonst fehlen Gläubiger-ID und Vereinskonto im PDF. ERPverein zählt die E-Mail trotzdem als Vorabankündigung.

1. Lege eine `Notification` an:
   - Dokumenttyp: `Sales Invoice`; Senden bei: `Submit`; Kanal: E-Mail
   - Bedingung: `doc.subscription and doc.erpverein_billing_kind == "Mitgliedsbeitrag"`
   - Empfänger: Dokumentfeld `contact_email`
   - Druck anhängen: an; Druckformat: „ERPverein Rechnung DIN 5008“
2. Die Bedingung schließt Rechnungen ohne ERPverein-Subscription aus. Dazu gehört die importierte Historie.
3. Die Bedingung schließt auch Mietrechnungen aus. Mieter bekommen keine Rechnung per E-Mail (Entscheidung O6).

**HINWEIS:** Eine E-Mail vor dem Anlegen des Exportprofils zählt nicht als Vorabankündigung. Das PDF enthält dann keine Gläubiger-ID. Sende die Rechnung in diesem Fall erneut.

### 1.10 Rollen für den Lastschrifteinzug

1. Einzugsläufe und Einzugspositionen brauchen zwei Rollen zusammen:
   - `System Manager` für die ERPverein-DocTypes
   - `Accounts User` oder `Accounts Manager` für Rechnungen und Bankdaten
2. Ein Benutzer ohne Accounts-Rolle sieht keine Einzugsläufe, keine Einzugspositionen und keine Lastschriftdatei.
3. Gib dem Kassenwart alle Rollen aus Abschnitt 20.6 der Spezifikation: `System Manager`, `Accounts Manager`, `Accounts User`, `Sales Master Manager`, `Sales User`.

### 1.11 Mieten zum Monatsersten

Die Miete ist zum 1. des Monats fällig. Mieter bekommen keine Vorabankündigung (Entscheidung O6).

1. Lege das Mandat des Mieters an:
   - Einzugsintervall: Monatlich
   - Monatstag: 1
   - Einzugsbetrag: die Monatsmiete
2. Lege die Mietabrechnung so an:
   - Rechnung erstellen: Tage vor Periodenbeginn
   - Tage vor Periodenbeginn: 14
   - Rechnungen automatisch buchen: an
3. ERPNext erzeugt jede Mietrechnung 14 Tage vor dem Monatsbeginn. Der Zahlungsplan hat eine Lastschriftzeile zum 1. des Monats.
4. Die Mietrechnung bekommt die Vorabankündigung „Nicht erforderlich“.

**HINWEIS:** Das Rechnungsdatum liegt im Vormonat. Die Januarmiete hat ein Rechnungsdatum im Dezember. Die Auswertung nach Zufluss (EÜR) betrifft das nicht.

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
4. Bezahlte, gemahnte und Zeilen mit aktiver Einzugsposition sind gesperrt.
5. Speichere. ERPverein protokolliert die Änderung an der Rechnung.
6. Hast du eine Lastschriftzeile geändert, steht die Vorabankündigung auf „Erneut senden“. Sende die Rechnung erneut per E-Mail.

Den Plan eines Entwurfs änderst du direkt in der Tabelle „Zahlungsplan“ der Rechnung.

### 2.3 Zahlungsplan aus dem Mandat neu erzeugen

1. Klicke „ERPverein“ → „Zahlungsplan aus Mandat neu erzeugen“.
2. Prüfe die Vorschau: bisheriger und neuer Plan.
3. Bestätige. Das geht nur, solange keine Zeile bezahlt, gemahnt oder in einer aktiven Einzugsposition ist.

### 2.4 Nachtrag für ältere Rechnungen

1. Öffne die Liste `Sales Invoice`. Klicke im Menü „ERPverein: Zahlungsplan nachtragen“.
2. Prüfe die Vorschau. Sie zeigt gebuchte ERPverein-Rechnungen ohne Zahlungsplan.
3. Bestätige. ERPverein meldet: nachgetragen, übersprungen, fehlgeschlagen.

### 2.5 Vorabankündigung

1. Die Rechnung mit Zahlungsplan per E-Mail ist die Vorabankündigung.
2. ERPverein prüft stündlich die gesendeten E-Mails. Danach steht die Vorabankündigung auf „Versendet“.
3. Ein Zahler ohne E-Mail-Adresse bekommt die Rechnung per Brief. Klicke danach „ERPverein“ → „Vorabankündigung manuell bestätigen“.
4. Die E-Mail muss mindestens 14 Tage vor dem Einzug versendet sein.
5. Wechselt der Zahler das Mandat, erzeuge den Zahlungsplan neu. Die Vorabankündigung steht dann auf „Erneut senden“. Sende die Rechnung erneut.
6. Für Mieten ist keine Vorabankündigung nötig (Abschnitt 1.11).

Zeitplan für den ersten Einzug:

| Schritt | Zeitpunkt |
|---|---|
| Rechnungen 2027 per E-Mail versenden | spätestens 18.01.2027 |
| Fällige Lastschriften vorbereiten (Stichtag 01.02.2027), prüfen, Datei erzeugen | 20.01. bis 25.01.2027 |
| Lastschriftdatei in PayGate hochladen | frühestens 11.01.2027, spätestens 28.01.2027 (Annahmeschluss 29.01.2027, 07:00 Uhr) |
| Einzugsdatum | 01.02.2027 |

---

## 3. Monatlicher Lastschrifteinzug

### 3.1 Überblick

Einmal im Monat bereitest du alle fälligen Lastschriften vor. ERPverein erzeugt daraus Einzugsläufe und je Einzugslauf eine Datei `pain.008.001.08`. Du lädst die Datei bei der HVB hoch.

```text
Fällige Lastschriften vorbereiten → Vorschau → Einzugslauf (Entwurf) → Lauf prüfen (Geprüft)
→ pain.008-Datei erzeugen (Exportiert) → Upload in PayGate → Bankreferenz erfassen
```

1. Ein Einzugslauf hat genau ein Einzugsdatum. Bei mehreren Einzugsdaten entstehen mehrere Einzugsläufe.
2. Eine Einzugsposition ist eine Lastschrift für genau eine Zahlungsplanzeile einer Rechnung.
3. Das Einzugsdatum ist die Fälligkeit der Zeile oder der nächste TARGET2-Geschäftstag.
4. Das Einzugsdatum liegt mindestens 2 Geschäftstage nach heute. So bleibt der Upload bis 07:00 Uhr am Vortag möglich.
5. ERPverein zieht keine Zeile mit einer Fälligkeit vor dem Go-live-Stichtag ein.

**VORSICHT:** Die Datei bucht keine Zahlung. Die Rechnung bleibt offen, bis der Bankumsatz da ist (Phasen 3 und 4).

Empfohlener Rhythmus:

1. Arbeite zwischen dem 20. und dem 25. des Monats. Dann gibt es die Mietrechnungen des Folgemonats schon (Abschnitt 1.11).
2. Wähle als Stichtag den 1. des Folgemonats. Dann sind die Mieten zum 1. und die Beiträge zum Monatsende im selben Durchgang.

### 3.2 Fällige Lastschriften vorbereiten

1. Öffne „Vereinsverwaltung“ → „SEPA-Einzugsläufe“.
2. Klicke „Fällige Lastschriften vorbereiten“.
3. Prüfe das Unternehmen.
4. Wähle den Stichtag. ERPverein sucht Lastschriftzeilen mit Fälligkeit bis zu diesem Tag.
5. Lass „Einzelbuchung“ aus. Nutze sie nur im Einzelfall (Abschnitt 3.9).
6. Klicke „Vorschau anzeigen“. Die Vorschau ändert nichts.
7. Prüfe die Vorschau:
   - Oben stehen die geplanten Einzugsläufe mit Einzugsdatum, Anzahl, Summe und Upload-Fenster.
   - Die Tabelle zeigt die einzugsbereiten Zeilen. Entferne den Haken „Einziehen“ bei einer Zeile, die du nicht einziehen willst.
   - Darunter stehen die Zeilen, die nicht einzugsbereit sind, jeweils mit Grund.
8. Klicke „Einzugsläufe erzeugen“. ERPverein meldet die neuen Einzugsläufe.

**HINWEIS:** Ist die Vorschau veraltet, meldet ERPverein das. Erzeuge dann eine neue Vorschau.

### 3.3 Gründe in der Vorschau

| Grund | Was du tust |
|---|---|
| Die Vorabankündigung fehlt | Sende die Rechnung per E-Mail. Für einen Zahler ohne E-Mail: „Vorabankündigung manuell bestätigen“. |
| Die Vorabankündigung kommt zu spät | Warte, bis die Frist von 14 Tagen erreicht ist, oder wähle einen späteren Stichtag. |
| Die Zeile wurde nach der Vorabankündigung geändert | Sende die Rechnung erneut per E-Mail. |
| Die Vorabankündigung liegt vor dem Anlegen des Exportprofils | Sende die Rechnung erneut per E-Mail. |
| Der Zahler hat inzwischen ein anderes SEPA-Mandat | Öffne die Rechnung. Klicke „Zahlungsplan aus Mandat neu erzeugen“. Sende die Rechnung erneut. |
| Das SEPA-Mandat der Rechnung ist nicht mehr aktiv | Prüfe das Mandat. Ändere die Zeile sonst auf „Überweisung“. |
| Die Abrechnungsart des Zahlers ist nicht mehr Lastschrift | Ändere die Zeile auf „Überweisung“ (Zahlungsplan bearbeiten). |
| Das Einzugsdatum liegt nach dem Austritt oder dem Mietende | Kläre den Fall. Ändere die Zeile auf „Überweisung“ oder ändere das Datum. |
| Der Betrag ist größer als der offene Betrag der Rechnung | Prüfe die Zahlungen der Rechnung. Passe den Zahlungsplan an. |
| Die IBAN liegt außerhalb des EWR | Ziehe diese Zeile nicht per Lastschrift ein. Ändere sie auf „Überweisung“. |
| Die Einzugsposition existiert schon | Nichts. Die Zeile steht schon in einem Einzugslauf. |

### 3.4 Einzugslauf prüfen

1. Öffne den Einzugslauf.
2. Prüfe oben das Upload-Fenster: „Upload bei der HVB frühestens am … , spätestens am … bis 07:00 Uhr“.
3. Prüfe die Tabelle „Positionen“: Zahler, Rechnung, Fälligkeit, Betrag, Mandatsreferenz, maskierte IBAN.
4. Klicke „Lauf prüfen“. ERPverein prüft jede Einzugsposition erneut.
5. Ohne Fehler steht der Lauf auf „Geprueft“.
6. Mit Fehlern bleibt der Lauf auf „Entwurf“. Der Grund steht in der Spalte „Prüfergebnis“.
   - Klicke „Fehlerhafte Positionen stornieren“ und gib einen Grund ein. Die Zeilen sind danach frei für einen späteren Einzugslauf.
   - Klicke danach erneut „Lauf prüfen“.

Den Verwendungszweck einer Einzugsposition änderst du im Status „Entwurf“ direkt in der Position. ERPverein wandelt Umlaute um (ä → ae). Der Text hat höchstens 140 Zeichen.

### 3.5 Datei erzeugen und herunterladen

1. Klicke im geprüften Einzugslauf „pain.008-Datei erzeugen“. Bestätige.
2. ERPverein prüft jede Position ein letztes Mal. Findet ERPverein einen Fehler, steht der Lauf wieder auf „Entwurf“. Gehe dann vor wie in 3.4.
3. Ohne Fehler steht der Lauf auf „Exportiert“. Die Datei hängt privat am Einzugslauf.
4. Der Lauf zeigt MsgId, PmtInfId, SHA-256, Zeitpunkt und Benutzer des Exports.
5. Klicke „Datei herunterladen“.

**VORSICHT:** Nach dem Export ist der Lauf unveränderlich. Eine Änderung braucht einen neuen Einzugslauf.

### 3.6 Upload bei der HVB

1. Melde dich bei PayGate (HVB Corporate Portal) an.
2. Lade die Datei als Sammeldatei hoch.
3. Prüfe in PayGate Anzahl, Summe und Einzugsdatum. Sie müssen zum Einzugslauf passen.
4. Gib die Datei mit TAN frei.
5. Halte die Frist ein: spätestens am angezeigten Tag bis 07:00 Uhr.

**VORSICHT:** Lade nie eine Datei der Test-Site bei der Bank hoch. Sie enthält echte IBANs.

**VORSICHT:** Lade dieselbe Datei nicht zweimal hoch. Prüfe vor jedem Upload die Auftragsliste in PayGate. Verlass dich nicht darauf, dass die Bank eine doppelte Datei ablehnt.

### 3.7 Bankreferenz erfassen

1. Kopiere nach dem Upload die Auftragsreferenz aus PayGate.
2. Klicke im Einzugslauf „Bankreferenz erfassen“. Füge die Referenz ein.
3. Der Lauf steht danach auf „Bankreferenz erfasst“.

### 3.8 Einzugslauf stornieren

1. Vor dem Export: Klicke „Lauf stornieren“ und gib einen Grund ein. Die Zeilen sind danach frei für einen neuen Einzugslauf.
2. Eine einzelne Position stornierst du in der Position: „Position stornieren“.
3. Nach dem Export stornierst du nur den ganzen Lauf. Bestätige dabei, dass die Bank die Datei nicht ausführt.

**VORSICHT:** Storniere einen exportierten Lauf nur, wenn die Datei nicht hochgeladen, abgelehnt oder nicht freigegeben ist. Sonst droht ein doppelter Einzug.

Eine Rechnung mit einer aktiven Einzugsposition lässt sich nicht stornieren. Ihr Zahlungsplan ist für diese Zeile gesperrt.

### 3.9 Einzellastschrift

1. Klicke „Fällige Lastschriften vorbereiten“.
2. Setze den Haken „Einzelbuchung (BtchBookg = false)“.
3. Wähle in der Vorschau nur die gewünschten Zeilen aus.
4. Die Bank bucht dann jede Lastschrift einzeln auf dem Vereinskonto. Es gibt keine Sammelgutschrift.

### 3.10 Was noch fehlt

1. Bankumsätze kommen ab Phase 3 automatisch über n8n.
2. Ab Phase 4 bucht ERPverein die Zahlungen zur Sammelgutschrift und markiert die Rechnungen als bezahlt.
3. Bis dahin bleiben die Rechnungen offen. Rücklastschriften buchst du bis Phase 6 im Banking-Bereich `/banking`.

---

## 4. Banktest bei der HVB

Vor dem ersten echten Einzug prüfst du, ob PayGate die Datei annimmt.

1. Nutze nie eine Datei der Test-Site.
2. Erzeuge die Testdatei mit dem Werkzeug außerhalb der Site (siehe Phasenbericht).
3. Lade die Testdatei in PayGate hoch.
4. Prüfe die Meldungen von PayGate: Format, Anzahl, Summe, Einzugsdatum, Gläubiger-ID.
5. Gib die Testdatei nicht frei. Lösche den Auftrag in PayGate.
6. Notiere das Ergebnis für den Phasenbericht: angenommen oder abgelehnt, mit Fehlermeldung.
