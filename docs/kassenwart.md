# Anleitung für den Kassenwart

Diese Anleitung beschreibt die Einrichtung, den Zahlungsplan, den monatlichen Lastschrifteinzug, den Bankimport, den automatischen Abgleich der Sammelgutschrift, die Überweisungen der Selbstzahler, den Arbeitsvorrat und die Rücklastschriften.

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
5. Das Migrate legt die Liste „Bankimporte“ und die ERPverein-Felder an Bankumsätzen (`Bank Transaction`) an. In „Vereinsverwaltung“ stehen jetzt auch „Bankimporte“ und „Bankumsätze“.
6. Das Migrate legt die Felder für den Abgleich an: an Zahlungen (`Payment Entry`) „SEPA-Einzugsposition“ und „ERPverein Bankumsatz“, am Bankumsatz „ERPverein Hinweis zum Abgleich“, am Einzugslauf und am Bankimport je einen Abschnitt „Abgleich“.
7. Das Migrate legt den Bericht „ERPverein Arbeitsvorrat“ an. In „Vereinsverwaltung“ steht er als „Arbeitsvorrat“. Dazu kommen der Status „Erledigt“ an Einzugspositionen und ein verstecktes Feld an Zahlungen, das doppelte Buchungen verhindert.

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
   - ERPverein Bankimport-Alias: `verein-main` (Abschnitt 5.2)

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
   - Toleranz der Sammelgutschrift: 5 Kalendertage (Abschnitt 6.2)
   - Höchstzahl Positionen je Datei: 1000
   - Vorlage für den Verwendungszweck: `Rechnung {invoice}` oder zum Beispiel `Beitrag {period_from} bis {period_to} Rechnung {invoice}`
   - Gebührenkonto: 68550. ERPverein bucht darauf die Gebühren von Rücklastschriften (Abschnitt 9). Ohne Gebührenkonto geht jede Rücklastschrift mit Gebühr in die manuelle Prüfung.
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
4. Bankimport und Abgleich laufen mit dem Benutzer, dem der API-Schlüssel in n8n gehört. Er braucht die Rollen des Kassenwarts (Abschnitt 5.3).

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
→ Sammelgutschrift im Bankimport → automatischer Abgleich (Abgestimmt)
```

1. Ein Einzugslauf hat genau ein Einzugsdatum. Bei mehreren Einzugsdaten entstehen mehrere Einzugsläufe.
2. Eine Einzugsposition ist eine Lastschrift für genau eine Zahlungsplanzeile einer Rechnung.
3. Das Einzugsdatum ist die Fälligkeit der Zeile oder der nächste TARGET2-Geschäftstag.
4. Das Einzugsdatum liegt mindestens 2 Geschäftstage nach heute. So bleibt der Upload bis 07:00 Uhr am Vortag möglich.
5. ERPverein zieht keine Zeile mit einer Fälligkeit vor dem Go-live-Stichtag ein.

**VORSICHT:** Die Datei bucht keine Zahlung. Die Rechnung bleibt offen, bis die Sammelgutschrift im Bankimport ist. Dann bucht ERPverein die Zahlungen (Abschnitt 6).

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
4. Nach der Bankgutschrift lässt sich ein Lauf nicht mehr stornieren. Ab dann gilt Abschnitt 6.

**VORSICHT:** Storniere einen exportierten Lauf nur, wenn die Datei nicht hochgeladen, abgelehnt oder nicht freigegeben ist. Sonst droht ein doppelter Einzug.

Eine Rechnung mit einer aktiven Einzugsposition lässt sich nicht stornieren. Ihr Zahlungsplan ist für diese Zeile gesperrt.

### 3.9 Einzellastschrift

1. Klicke „Fällige Lastschriften vorbereiten“.
2. Setze den Haken „Einzelbuchung (BtchBookg = false)“.
3. Wähle in der Vorschau nur die gewünschten Zeilen aus.
4. Die Bank bucht dann jede Lastschrift einzeln auf dem Vereinskonto. Es gibt keine Sammelgutschrift.
5. ERPverein ordnet jede Einzelgutschrift über die End-to-End-Referenz genau einer Einzugsposition zu. Das braucht den erweiterten Export der Banking-Webtop (Abschnitt 5.10).

### 3.10 Was noch fehlt

1. Bankumsätze kommen seit Phase 3 über n8n nach ERPNext (Abschnitt 5).
2. Seit Phase 4 bucht ERPverein die Zahlungen zur Sammelgutschrift und markiert die Rechnungen als bezahlt (Abschnitt 6).
3. Seit Phase 5 bucht ERPverein Überweisungen von Selbstzahlern mit sicherem Signal (Abschnitt 7). Alle offenen Fälle zeigt der Arbeitsvorrat (Abschnitt 8).
4. Seit Phase 6 bucht ERPverein Rücklastschriften und Rejects mit Gegenbuchung und Gebühr (Abschnitt 9). Nach einer Rückgabe entscheidest du je Position, wie es weitergeht.

---

## 4. Banktest bei der HVB

Vor dem ersten echten Einzug prüfst du, ob PayGate die Datei annimmt.

1. Nutze nie eine Datei der Test-Site.
2. Erzeuge die Testdatei mit dem Werkzeug außerhalb der Site (siehe Phasenbericht).
3. Lade die Testdatei in PayGate hoch.
4. Prüfe die Meldungen von PayGate: Format, Anzahl, Summe, Einzugsdatum, Gläubiger-ID.
5. Gib die Testdatei nicht frei. Lösche den Auftrag in PayGate.
6. Notiere das Ergebnis für den Phasenbericht: angenommen oder abgelehnt, mit Fehlermeldung.

---

## 5. Bankimport

### 5.1 Überblick

Die Banking-Webtop ruft die Umsätze bei der HVB ab. n8n reicht die Datei unverändert an ERPverein weiter. ERPverein legt daraus Bankumsätze an.

```text
Banking-Webtop (Kontoumsätze abrufen) → n8n-Webhook → ERPverein-Schnittstelle
→ Bankimport (Originaldatei, Prüfsumme, Zähler) → Bankumsätze (gebucht, Status Unreconciled)
→ Abgleich im Hintergrund (Abschnitt 6)
```

1. Ein Abruf ergibt genau einen Bankimport. Der Bankimport hält die Originaldatei als private Datei.
2. ERPverein legt jeden neuen Umsatz als gebuchten Bankumsatz (`Bank Transaction`) an.
3. Ein Abruf überschneidet sich mit dem letzten Abruf um 14 Tage. ERPverein erkennt die doppelten Umsätze und legt sie nicht erneut an.
4. ERPverein übernimmt nur Umsätze ab dem Go-live-Stichtag aus dem SEPA-Exportprofil. Ältere Umsätze kommen aus der Historie.
5. ERPverein ordnet jedem Umsatz eine Kategorie zu, zum Beispiel „Lastschrift-Gutschrift“ oder „Bankgebuehr“. Die Kategorie ist ein Hinweis. Sie bucht nichts.
6. Der Bankimport selbst bucht keine Zahlung. Nach jedem Bankimport startet ERPverein den Abgleich im Hintergrund (Abschnitt 6).

**VORSICHT:** Die Datei enthält echte Namen, IBANs und Verwendungszwecke. Speichere sie nirgends außer in ERPverein.

### 5.2 Vereinskonto vorbereiten

1. Öffne das Firmen-Bankkonto „HVB Vereinskonto“ (Abschnitt 1.5).
2. Trage im Feld „ERPverein Bankimport-Alias“ `verein-main` ein. Das ist der Kontoalias der Banking-Webtop.
3. Prüfe: Das Konto hat eine gültige IBAN und das Hauptbuchkonto 18000.
4. Prüfe im SEPA-Exportprofil den Go-live-Stichtag (Abschnitt 1.6). ERPverein übernimmt nur Umsätze ab diesem Tag.
5. Schalte in den `Accounts Settings` „Enable Automatic Party Matching“ ein (Abschnitt 1.7). Dann erkennt ERPNext den Zahler über die IBAN seines Mandats.

**HINWEIS:** Der Alias darf nur Kleinbuchstaben, Ziffern, Punkt, Bindestrich und Unterstrich enthalten. Nur ein Firmen-Bankkonto darf einen Alias haben.

### 5.3 API-Schlüssel für n8n

n8n meldet sich mit einem API-Schlüssel bei ERPverein an. Bankimport und Abgleich laufen mit den Rechten des Benutzers, dem dieser Schlüssel gehört. ERPverein hat dafür kein eigenes Rechtesystem.

1. Nimm den Benutzer des Kassenwarts. Er hat alle Rollen aus Abschnitt 1.10.
2. Öffne den Benutzer. Öffne den Bereich `API Access`. Klicke `Generate Keys`.
3. Kopiere den API-Schlüssel und das API-Geheimnis. ERPNext zeigt das Geheimnis nur einmal an.
4. Trage beide in die n8n-Zugangsdaten „ERPverein Bankimport API“ ein (Abschnitt 5.4).

**HINWEIS:** Ein eigener Benutzer für n8n ist möglich. Gib ihm dann dieselben Rollen wie dem Kassenwart. Mit nur `Accounts User` gelingt der Bankimport, der Abgleich aber nicht. Der Bankimport zeigt dann „Status des Abgleichs: Fehler“ und nennt die fehlenden Rechte. Das gilt auch für einen API-Benutzer, den du nach der Anleitung von Phase 3 angelegt hast.

**VORSICHT:** Schlüssel und Geheimnis gehören nur in die Zugangsdaten von n8n. Schreibe sie nicht in Dateien, E-Mails oder Git. „Generate Keys“ erzeugt ein neues Geheimnis. Ändere danach die Zugangsdaten in n8n.

### 5.4 n8n-Workflow einrichten

Die Workflow-Datei liegt außerhalb des Repositorys: `.private/n8n/erpverein-bankimport.json`.

1. Lege in n8n zwei Zugangsdaten vom Typ „Header Auth“ an:
   - „ERPverein Banking Webhook Token“: Name `Authorization`, Wert `Bearer <Token>`. Der Token ist das Secret `webhook_token` des VPSmith-Pakets „banking“.
   - „ERPverein Bankimport API“: Name `Authorization`, Wert `token <API-Schlüssel>:<API-Geheimnis>` (Abschnitt 5.3).
2. Importiere die Workflow-Datei in n8n.
3. Wähle in jedem Knoten die passenden Zugangsdaten.
4. Prüfe im Knoten „ERPverein Bankimport“ die Adresse. Sie beginnt mit `http://verein-api:8080`. `verein-api` ist der Standard des Eingabefelds `api_alias` im VPSmith-Paket „erpverein“. Hast du dort einen anderen Alias eingetragen, ersetze den Namen.
5. Prüfe die Workflow-Einstellungen: „Save successful production executions“, „Save failed production executions“ und „Save manual executions“ sind aus. Die Daten enthalten IBANs.
6. Aktiviere den Workflow. Der Webhook hat den Pfad `/webhook/erpverein-bank-import`. Das ist der Standard des Eingabefelds `webhook_path` im Paket „banking“.

**HINWEIS:** n8n darf ERPverein nur aufrufen, wenn der Alias im n8n-Eingabefeld `ssrf_allowed_hostnames` steht. Das hast du bei der Installation von ERPverein eingetragen. Trage den Webhook-Pfad nie unter `public_webhook_paths` ein.

Der Workflow hat drei Knoten:

| Knoten | Aufgabe |
|---|---|
| Banking-Webtop Webhook | Nimmt Manifest und Umsatzdatei an. Prüft den Token. |
| ERPverein Bankimport | Sendet beide Dateien unverändert an `erpverein.api.bank_import.import_statement` über `http://verein-api:8080`. |
| Antwort mit Zaehlern | Antwortet der Webtop nur mit Zählern. Bei „Teilfehler“ oder „Fehler“ antwortet er mit HTTP 422. |

**HINWEIS:** Änderst du das Secret `webhook_token` in VPSmith, wende das Paket „banking“ neu an. Ändere danach auch die Zugangsdaten „ERPverein Banking Webhook Token“ in n8n.

### 5.5 Umsätze abrufen

1. Öffne die Banking-Webtop.
2. Klicke auf dem Desktop „Kontoumsaetze abrufen“.
3. Gib die PIN ein. Bestätige die TAN.
4. Die Webtop meldet „Upload successful.“. Dann ist der Abruf in ERPverein.
5. Meldet die Webtop „ERROR: n8n answered HTTP 422 for request …“, ist der Bankimport mit „Teilfehler“ oder „Fehler“ beendet. Gehe vor wie in 5.7.
6. Meldet die Webtop „ERROR: the CSV export is empty“, gab es im Zeitraum keine Umsätze. Es gibt nichts zu tun.

Rufe die Umsätze mindestens einmal pro Woche ab. Ein Abruf umfasst die letzten 14 Tage. Nach längerer Pause fehlen sonst Umsätze.

**HINWEIS:** Ein zweiter Abruf am selben Tag schadet nicht. ERPverein legt keinen Umsatz doppelt an.

### 5.6 Ergebnis prüfen

1. Öffne „Vereinsverwaltung“ → „Bankimporte“.
2. Öffne den neuesten Bankimport. Prüfe den Status:

| Status | Bedeutung |
|---|---|
| Importiert | Alle Zeilen sind verarbeitet. |
| Teilfehler | Einige Zeilen haben Fehler. Die übrigen Umsätze sind angelegt. |
| Fehler | ERPverein hat nichts angelegt. Der Grund steht unter „Fehler der Datei“. |

3. Prüfe die Zähler:

| Zähler | Bedeutung |
|---|---|
| Zeilen | Datenzeilen der Datei |
| Neue Bankumsaetze | Umsätze, die dieser Bankimport angelegt hat |
| Duplikate | Umsätze, die es schon aus einem früheren Abruf gibt |
| Vor dem Stichtag | Umsätze vor dem Go-live-Stichtag. ERPverein übernimmt sie nicht. |
| Ohne Betrag | Zeilen mit Betrag 0,00. ERPverein übernimmt sie nicht. |
| Fehlerhafte Zeilen | Zeilen, die ERPverein nicht lesen oder nicht anlegen konnte |
| Abgeglichen, Offen | Status der neuen Bankumsätze in ERPNext nach dem letzten Abgleich |

4. Prüfe den Abschnitt „Abgleich“: „Status des Abgleichs“ und „Ergebnis des Abgleichs“ (Abschnitt 6.1).
5. Klicke „Bankumsätze anzeigen“. Die Liste zeigt die Umsätze dieses Bankimports.
6. Klicke „Banking-Bereich öffnen“. Dort gleichst du die Umsätze ab, die ERPverein nicht automatisch abgleicht.

Jeder Bankumsatz zeigt im Abschnitt „ERPverein“: Kategorie, Abgleich, Wertstellung, SEPA-Referenzen, Kundenreferenz, Primanota, Textschlüssel, Bankimport und Zeile in der Originaldatei.

### 5.7 Fehler beheben

| Meldung | Was du tust |
|---|---|
| Kein Firmen-Bankkonto hat den Bankimport-Alias … | Trage den Alias am Vereinskonto ein (5.2). Klicke im Bankimport „Erneut verarbeiten“. |
| Fuer das Unternehmen … gibt es kein aktives SEPA-Exportprofil mit Go-live-Stichtag | Lege das Exportprofil an oder trage den Stichtag ein (1.6). Klicke „Erneut verarbeiten“. |
| Das Bankkonto … braucht eine gueltige IBAN | Trage die IBAN am Vereinskonto ein. Klicke „Erneut verarbeiten“. |
| Die Datei enthaelt Umsaetze eines anderen Kontos | Prüfe den Kontoalias in der Banking-Webtop. ERPverein hat nichts angelegt. |
| Fehlercode `betrag`, `datum` oder `spaltenzahl` | Die Zeile ist beschädigt. Buche den Umsatz im Banking-Bereich von Hand, wenn er fehlt. Melde den Fall für den Phasenbericht. |
| Fehlercode `bankumsatz` | ERPNext hat den Umsatz abgelehnt. Die Meldung nennt den Grund. Behebe ihn. Klicke „Erneut verarbeiten“. |

1. „Erneut verarbeiten“ liest die gespeicherte Originaldatei noch einmal.
2. Schon angelegte Umsätze bleiben unverändert. ERPverein legt sie nicht doppelt an.
3. Ein Bankimport mit Status „Importiert“ lässt sich nicht erneut verarbeiten.

**HINWEIS:** Meldungen zeigen IBANs nur maskiert (`**** 1234`).

### 5.8 Doppelte Umsätze vermeiden

1. ERPverein erkennt einen Umsatz an einem Fingerprint: Konto, Buchungsdatum, Betrag, IBAN und Name der Gegenseite, Verwendungszweck.
2. Zwei gleiche Umsätze am selben Tag zählt ERPverein getrennt. Beide werden angelegt.
3. Dieselbe Datei erkennt ERPverein an der Prüfsumme. Sie erzeugt keinen neuen Bankimport.
4. Lege einen importierten Umsatz nie zusätzlich von Hand an. Importiere Kontoauszüge nicht zusätzlich über „Bank Statement Import“.

**VORSICHT:** Storniere keinen importierten Bankumsatz. ERPverein importiert einen stornierten Umsatz nicht erneut. Brauchst du eine Korrektur, buche sie im Banking-Bereich.

### 5.9 Test auf der Test-Site

1. Prüfe den Go-live-Stichtag im Exportprofil der Test-Site. Liegt er in der Zukunft, landen alle Umsätze unter „Vor dem Stichtag“.
2. Setze für einen Test den Stichtag der Test-Site auf ein Datum der letzten 14 Tage. Ändere ihn nach dem Test zurück.
3. Rufe die Umsätze ab (5.5). Prüfe das Ergebnis (5.6).
4. Rufe die Umsätze ein zweites Mal ab. Der zweite Bankimport hat keine neuen Bankumsätze, nur Duplikate.

**VORSICHT:** Auf der Test-Site stehen danach echte Umsätze. Lösche die Test-Site vor dem Go-live oder setze sie neu auf (E21).

### 5.10 Erweiterter Export der Banking-Webtop

Der Standardexport von AqBanking enthält nur Datum, Betrag, Konto, Name, IBAN und Verwendungszweck. Für den Abgleich der Lastschriften fehlen Wertstellung, End-to-End-Referenz, Mandatsreferenz, Gläubiger-ID, Bankreferenz und Buchungstext. Der erweiterte Export liefert sie (Entscheidung O7).

1. Den Export erzeugt `image/banking-fetch` im VPSmith-Paket „banking“. Die Fassung mit dem erweiterten Export schreibt eine Kopfzeile mit 22 Spalten und meldet das Schema `erpverein.aqbanking.listtrans-template.v3`.
2. Übernimm das geänderte Paket in dein VPSmith-Projekt. Speichere es und gib eine neue Version frei. VPSmith erhöht die Version selbst.
3. Wende „banking“ an. VPSmith baut das Desktop-Image neu, weil sich `image/` geändert hat. Das dauert eine Weile, denn AqBanking wird neu übersetzt. Die HVB-Einrichtung bleibt erhalten.
4. ERPverein liest beide Formate. Umsätze aus dem alten und dem neuen Format erkennt ERPverein als dieselben Umsätze. Der erste Abruf nach der Umstellung legt keine Duplikate an.
5. Rufe einmal ab (5.5). Öffne den Bankimport. Das Feld „Fuellgrad je Spalte“ zeigt je Spalte nur die Anzahl gefüllter Werte, keine Inhalte. Teile diese Zahlen für den Phasenbericht.

### 5.11 Was noch fehlt

1. Erledigt mit Phase 5: Überweisungen von Selbstzahlern (Abschnitt 7) und der Arbeitsvorrat (Abschnitt 8).
2. Erledigt mit Phase 6: ERPverein bucht Rücklastschriften mit Gegenbuchung und Gebühr (Abschnitt 9).

---

## 6. Automatischer Abgleich der Sammelgutschrift

### 6.1 Überblick

Nach jedem Bankimport startet ERPverein den Abgleich im Hintergrund. ERPverein sucht zu jeder Lastschrift-Gutschrift den passenden Einzugslauf. Passt genau ein Lauf, bucht ERPverein je Einzugsposition eine Zahlung und gleicht alle Zahlungen mit dem Bankumsatz ab.

```text
Bankimport → Abgleich im Hintergrund → Einzugslauf „Bankgutschrift erkannt“
→ je Einzugsposition eine gebuchte Zahlung (Payment Entry) → Abgleich mit dem Bankumsatz
→ Einzugslauf „Abgestimmt“, Positionen „Bezahlt“, Bankumsatz „Abgeglichen“ (ERPNext: Reconciled)
```

1. Der Abgleich dauert Sekunden bis wenige Minuten. Lade das Formular danach neu.
2. Der Bankimport zeigt das Ergebnis im Abschnitt „Abgleich“, zum Beispiel „12 Zahlungen gebucht, 1 Einzugslaeufe abgestimmt, 0 Bankumsaetze zur manuellen Pruefung“.
3. Die Zahlung hat das Buchungsdatum des Bankumsatzes, die End-to-End-Referenz der Position als Referenznummer und die Zahlungsweise „SEPA-Lastschrift“.
4. Die Zahlung bezahlt genau die Zahlungsplanzeile der Einzugsposition. Die Rechnung steht danach auf „Paid“ oder „Partly Paid“.
5. Der Abgleich ist wiederholbar. Er bucht nie eine zweite Zahlung für dieselbe Einzugsposition.

**VORSICHT:** Die pain.008-Datei bucht nichts. Erst der echte Bankumsatz löst die Zahlungen aus.

### 6.2 So erkennt ERPverein die Sammelgutschrift

ERPverein prüft jeden offenen Bankumsatz mit der Kategorie „Lastschrift-Gutschrift“ gegen alle exportierten Einzugsläufe des Vereinskontos:

| Signal | Regel |
|---|---|
| Betrag | gleich der Summe des Einzugslaufs |
| Buchungsdatum | am oder nach dem Einzugsdatum, höchstens „Toleranz der Sammelgutschrift“ Kalendertage später (Exportprofil, Standard 5) |
| „POSTEN nn“ im Verwendungszweck | wenn vorhanden: gleich der Zahl der Einzugspositionen |
| Name des Laufs (`SEPA-LAUF-…`), MsgId oder Bankreferenz (3.7) | wenn die Bank sie liefert: Sie bestimmen den Lauf |

1. ERPverein ordnet nur zu, wenn genau ein Lauf passt und dieser Lauf zu keinem anderen Bankumsatz passt.
2. Einen Bankumsatz mit anderer Kategorie lässt ERPverein offen. Ausnahme: Er nennt den Namen eines Einzugslaufs.
3. Einzellastschrift (3.9): ERPverein ordnet jede Gutschrift über die End-to-End-Referenz genau einer Position zu. Der Betrag muss gleich sein. Der Lauf steht auf „Bankgutschrift erkannt“, bis jede Position bezahlt ist.

**HINWEIS:** Welche Felder die HVB im Abruf füllt, zeigt erst ein echter Abruf (Abschnitt 5.10). Die Regeln lassen sich danach anpassen.

### 6.3 Status

| Einzugslauf | Bedeutung |
|---|---|
| Bankgutschrift erkannt | Die Gutschrift ist zugeordnet. Es fehlen noch Zahlungen oder, bei Einzelbuchung, noch Gutschriften. |
| Abgestimmt | Jede Position ist bezahlt oder als erledigt markiert (6.8). Jeder zugehörige Bankumsatz ist abgeglichen. |
| Manuelle Pruefung | ERPverein hat etwas nicht automatisch erledigt. Den Grund nennt das Feld „Hinweis zum Abgleich“. |

| Einzugsposition | Bedeutung |
|---|---|
| Bezahlt | Die Zahlung ist gebucht. Das Feld „Zahlung“ zeigt sie. |
| Manuelle Pruefung | ERPverein hat keine Zahlung gebucht. Den Grund nennt das Feld „Pruefergebnis“. |
| Erledigt | Du hast den Fall im Banking-Bereich geklärt und die Position als erledigt markiert (6.8). |

| Bankumsatz, Feld „ERPverein Abgleich“ | Bedeutung |
|---|---|
| Offen | ERPverein hat den Umsatz noch nicht zugeordnet. Den Grund nennt das Feld „ERPverein Hinweis zum Abgleich“. ERPverein prüft ihn bei jedem Abgleich erneut. |
| Vorschlag | ERPverein schlägt eine Zuordnung vor (Abschnitt 7.5). Du bestätigst oder verwirfst sie. |
| Abgeglichen | ERPverein hat den Umsatz vollständig abgeglichen. |
| Manuelle Pruefung | Den Grund nennt das Feld „ERPverein Hinweis zum Abgleich“. ERPverein ändert den Umsatz danach nicht mehr von selbst. |

### 6.4 Manuelle Prüfung

| Grund | Was du tust |
|---|---|
| Der Betrag … weicht von der Summe … des Einzugslaufs … ab | Prüfe in PayGate, ob die Bank Lastschriften vor der Gutschrift abgelehnt hat. ERPverein bucht nichts. Buche die Zahlungen im Banking-Bereich von Hand. |
| Mehrere Einzugslaeufe passen | Prüfe Datum und Summe. Nennt die Bank eine Referenz im Verwendungszweck, erfasse sie im richtigen Lauf als Bankreferenz (3.7). Klicke dann „Abgleich starten“ (6.5). |
| Mehrere Bankumsaetze passen zum Einzugslauf | Prüfe, ob die Bank doppelt gebucht hat. Gleiche den richtigen Umsatz im Banking-Bereich ab. |
| Kein exportierter Einzugslauf passt | Prüfe, ob die Gutschrift aus einem Einzug von Vereinsmeister stammt. Kam sie später als die Toleranz, erhöhe die Toleranz im Exportprofil und klicke „Abgleich starten“. |
| POSTEN … passt zu keinem Einzugslauf | Prüfe die Anzahl in PayGate. Buche die Zahlungen im Banking-Bereich von Hand. |
| Die Rechnung … ist inzwischen anders bezahlt | Der Zahler hat zusätzlich überwiesen. Entscheide: erstatten oder als Vorauszahlung behalten. Buche die Zahlung im Banking-Bereich und gleiche sie mit dem Bankumsatz ab. |
| ERPverein konnte die Zahlung nicht buchen | Behebe den Grund, zum Beispiel ein gesperrtes Geschäftsjahr. Klicke „Abgleich starten“. ERPverein bucht nur die fehlenden Zahlungen. |
| Die Zahlung … wurde storniert | Kläre den Fall im Banking-Bereich. ERPverein bucht für diese Position keine neue Zahlung. Markiere die Position danach als erledigt (6.8). |
| Der Betrag … weicht vom Betrag der Position … ab | Einzelbuchung: Prüfe die Gutschrift. Buche die Zahlung im Banking-Bereich von Hand. |
| Die End-to-End-Referenz gehoert zur Position … | Die Bank hat eine Position gutgeschrieben, die schon bezahlt oder storniert ist. Kläre den Fall mit der Bank. |
| Der Benutzer … hat nicht alle Rechte fuer den Abgleich | Gib dem Benutzer des API-Schlüssels die Rollen des Kassenwarts oder nutze den Schlüssel des Kassenwarts (5.3). Klicke dann „Abgleich starten“. |

### 6.5 Abgleich erneut starten

1. Öffne den Bankimport oder den Einzugslauf.
2. Klicke „Abgleich starten“.
3. ERPverein prüft alle offenen Bankumsätze des Vereinskontos und alle angefangenen Einzugsläufe erneut. ERPverein bucht nur fehlende Zahlungen.
4. Lade das Formular nach einer Minute neu.

### 6.6 Was du nicht tust

**VORSICHT:** Storniere keine Zahlung, die ERPverein gebucht hat. Die Position geht dann auf „Manuelle Pruefung“. ERPverein bucht für sie keine neue Zahlung.

**VORSICHT:** Lege keine `Bank Transaction Rule` für Lastschrift-Gutschriften an. Eine Regel bucht den Umsatz sonst an ERPverein vorbei.

**HINWEIS:** Löst du im Banking-Bereich den Abgleich eines Bankumsatzes, bleiben die Zahlungen gebucht. Verknüpfe sie dort wieder.

**HINWEIS:** Eine Position in „Manuelle Pruefung“ hält den Einzugslauf in „Manuelle Pruefung“. Kläre den Fall und markiere die Position als erledigt (6.8).

### 6.7 Test auf der Test-Site

Auf der Test-Site kommt keine echte Sammelgutschrift, denn du lädst dort keine Datei bei der Bank hoch. Teste mit einer synthetischen Gutschrift.

1. Setze den Go-live-Stichtag der Test-Site in die Vergangenheit (5.9).
2. Bereite einen Einzugslauf mit wenigen Positionen vor. Prüfe ihn und erzeuge die Datei (3.2 bis 3.5). Lade die Datei nie bei der Bank hoch.
3. Erzeuge mit dem Testwerkzeug aus dem Phasenbericht eine synthetische Gutschrift: Betrag gleich Summe des Laufs, Datum gleich Einzugsdatum, „POSTEN“ gleich Anzahl der Positionen.
4. Sende sie mit dem Befehl aus dem Phasenbericht an ERPverein. Der Bankimport startet den Abgleich.
5. Prüfe: Einzugslauf „Abgestimmt“, Positionen „Bezahlt“, Rechnungen „Paid“, Bankumsatz „Reconciled“.
6. Wiederhole den Test mit einem anderen Lauf und einem um 1,00 EUR kleineren Betrag. Prüfe: keine Zahlung, Lauf und Bankumsatz auf „Manuelle Pruefung“ mit Grund.

**VORSICHT:** Die Test-Site enthält danach gebuchte Testzahlungen. Setze sie vor dem Go-live neu auf (E21).

### 6.8 Position als erledigt markieren

Nutze diese Aktion nur für eine Position in „Manuelle Pruefung“, deren Fall du im Banking-Bereich geklärt hast. Beispiele: Du hast die Zahlung von Hand gebucht, die Rechnung war schon anders bezahlt, oder du hast das Geld zurücküberwiesen.

1. Kläre den Fall im Banking-Bereich `/banking`. Gleiche den Bankumsatz dort vollständig ab.
2. Öffne die Einzugsposition.
3. Klicke „Als erledigt markieren“.
4. Schreibe in einem Satz, was du getan hast.
5. ERPverein setzt die Position auf „Erledigt“. Das Feld „Erledigt-Vermerk“ bekommt Benutzer, Datum, deinen Text und das bisherige Prüfergebnis. Das Feld „Pruefergebnis“ ist danach leer. ERPverein kommentiert Position und Lauf.
6. Sind alle Positionen bezahlt oder erledigt und ist jeder Bankumsatz abgeglichen, geht der Lauf auf „Abgestimmt“.

**VORSICHT:** ERPverein bucht für eine erledigte Position nichts mehr. Die Zahlungsplanzeile bleibt für einen neuen Einzug gesperrt. Fehlt das Geld noch, kläre den Fall vorher. Rücklastschriften bucht ERPverein selbst (Abschnitt 9).

---

## 7. Überweisungen von Selbstzahlern

### 7.1 Überblick

Selbstzahler überweisen. Die Rechnung nennt dafür die Rechnungsnummer als Verwendungszweck. Nach jedem Bankimport prüft ERPverein jede eingehende Überweisung, die nicht zu einer Lastschrift gehört.

```text
Bankimport → Abgleich im Hintergrund → Sammelgutschriften (Abschnitt 6) → Überweisungen
→ sicheres Signal: Zahlung buchen und mit dem Bankumsatz abgleichen
→ nur schwache Signale: Vorschlag; Konflikt: Manuelle Pruefung; kein Signal: Offen
```

1. Der Bankimport zeigt das Ergebnis im Abschnitt „Abgleich“, zum Beispiel „1 Ueberweisungen zugeordnet, 2 Vorschlaege warten auf Bestaetigung“.
2. Die Zahlung hat das Buchungsdatum des Bankumsatzes, die Zahlungsweise „Überweisung“ und als Referenznummer die Referenz der Bank oder den Namen des Bankumsatzes.
3. Partei der Zahlung ist immer der Kunde der Rechnung. Das gilt auch, wenn eine dritte Person zahlt, zum Beispiel die Eltern für ein Kind.
4. ERPverein bucht für einen Bankumsatz und einen Kunden nie eine zweite Zahlung. Ein verstecktes Feld an der Zahlung sichert das ab.

### 7.2 Sichere Signale

ERPverein bucht nur mit einem sicheren Signal. Die Signale gelten in dieser Reihenfolge:

| Nr. | Signal | Bedingung |
|---|---|---|
| 1 | Rechnungsnummer im Verwendungszweck (`ACC-SINV-JJJJ-NNNNN`) | Genau eine offene Rechnung ist genannt. Der Betrag darf kleiner sein (Teilzahlung) oder größer (Überzahlung). |
| 1a | Mehrere Rechnungsnummern | Alle Rechnungen gehören zu einem Kunden. Der Betrag ist mindestens ihre Summe. ERPverein bucht eine Zahlung auf alle Rechnungen. |
| 2 | Mitglieds- oder Mieter-ID im Verwendungszweck (`MIT-JJJJ-NNNNN`, `MIE-JJJJ-NNNNN`) | Der Betrag ist gleich dem offenen Betrag genau einer Rechnung dieses Zahlers. Mitgliedsbeitrag und Miete zählen getrennt. |
| 3 | Bekannte IBAN der Gegenseite | Ein Bankkonto mit dieser IBAN gehört genau einem Kunden. Der Betrag ist gleich dem offenen Betrag genau einer Rechnung dieses Kunden. Passt der Betrag zu einem Mitgliedsbeitrag und zu einer Miete, ist das mehrdeutig. |

1. Eine Rechnungsnummer geht vor ID und IBAN.
2. Rechnungsnummer und ID zählen nur vollständig. Eine gekürzte Nummer wie „SINV-2027-12“ ist kein Signal.
3. Eine ID ohne Mitglied oder Mieter zählt nicht. ERPverein prüft dann die IBAN.
4. Name und Betrag allein lösen nie eine Buchung aus. ERPverein macht dann einen Vorschlag (7.5).
5. Rechnungen vor dem Go-live-Stichtag (Historie) bucht ERPverein nur mit Rechnungsnummer. Mit ID oder IBAN entsteht ein Vorschlag.
6. Ist die Gutschrift älter als die Rechnung, bucht ERPverein über ID oder IBAN nicht automatisch. Es entsteht ein Vorschlag. Beispiel: Eine doppelte Zahlung aus dem Vorjahr bezahlt nicht still die neue Rechnung.
7. Nennt der Zahler die Nummer einer stornierten Rechnung, die du berichtigt hast (Name mit „-1“), bucht ERPverein auf die Berichtigung.

### 7.3 Teilzahlung, Überzahlung, mehrere Rechnungen

1. **Teilzahlung:** ERPverein bucht den Betrag als Teilbetrag. Zuerst bezahlt er die Zeilen mit Zahlungsweise „Überweisung“, dann die Lastschriftzeilen, jeweils die älteste Fälligkeit zuerst. Die Rechnung steht danach auf „Partly Paid“.
2. Würde eine Teilzahlung eine Lastschriftzeile nur zum Teil bezahlen, bucht ERPverein nichts. Grund: Der angekündigte Einzugsbetrag stimmte dann nicht mehr. Der Bankumsatz geht auf „Manuelle Pruefung“.
3. **Überzahlung:** ERPverein bezahlt die Rechnung voll. Der Rest bleibt am Bankumsatz offen. Der Bankumsatz geht auf „Manuelle Pruefung“ und nennt den Rest. Buche den Rest im Banking-Bereich: als Vorauszahlung, als Spende oder als Rückzahlung.
4. **Mehrere Rechnungen eines Kunden:** Nennt der Verwendungszweck alle Nummern, bucht ERPverein eine Zahlung mit einer Referenz je Zahlungsplanzeile.
5. **Mehrere Kunden:** Nennt der Verwendungszweck Rechnungen verschiedener Kunden und passt die Summe, macht ERPverein einen Vorschlag. Beim Bestätigen entsteht je Kunde eine Zahlung.

### 7.4 Eingezogene Rechnungen

**VORSICHT:** Steht die Rechnung in einer aktiven Einzugsposition (Entwurf, Geprüft, Exportiert oder Manuelle Prüfung), bucht ERPverein die Überweisung nie. Sonst droht ein doppelter Einzug. Der Bankumsatz geht auf „Manuelle Pruefung“ und nennt die Position.

1. Prüfe, ob die Bank die Lastschrift schon ausgeführt hat.
2. Ist der Lauf noch nicht bei der Bank: Storniere die Position (3.8). Buche die Überweisung danach im Banking-Bereich.
3. Ist die Lastschrift schon ausgeführt: Zahle die Überweisung zurück.

**HINWEIS:** Kam die Lastschrift zurück und du hast noch nicht entschieden (9.5), wartet die Überweisung. Der Bankumsatz bleibt „Offen“ und nennt die Position. Entscheidest du „Auf Überweisung umstellen“, bucht ERPverein die Überweisung beim nächsten Abgleich.

### 7.5 Vorschläge bestätigen oder verwerfen

Ein Vorschlag nennt Kunde, Rechnungen, Teilbeträge, Signale und Konflikte.

1. Öffne den Bankumsatz, zum Beispiel aus dem Arbeitsvorrat (Abschnitt 8).
2. Lies den Hinweis oben im Formular.
3. Klicke „ERPverein“ → „Vorschlag bestätigen“. ERPverein zeigt Kunde, Rechnung, Zahlungsbedingung und Betrag.
4. Klicke „Zahlung buchen“. ERPverein prüft den Vorschlag erneut, bucht die Zahlung und gleicht sie ab. Der Hinweis nennt dich als Bestätigenden.
5. Passt der Vorschlag nicht: Klicke „ERPverein“ → „Vorschlag verwerfen“ und nenne den Grund. Der Bankumsatz geht auf „Manuelle Pruefung“. ERPverein schlägt ihn nicht erneut vor.

**HINWEIS:** ERPverein prüft Vorschläge bei jedem Abgleich neu. Ist die Rechnung inzwischen anders bezahlt, geht der Bankumsatz auf „Offen“ mit Grund. Hat sich der Vorschlag geändert, meldet „Zahlung buchen“ das. Lade dann das Formular neu.

### 7.6 Gründe und was du tust

| Grund (Anfang) | Status | Was du tust |
|---|---|---|
| Der Verwendungszweck nennt keine offene Rechnung | Manuelle Pruefung | Die Rechnung ist bezahlt, storniert oder unbekannt. Prüfe auf doppelte Zahlung. Zahle zurück oder buche als Vorauszahlung. |
| Die Rechnung … hat die aktive Einzugsposition … | Manuelle Pruefung | Siehe 7.4. |
| Die Zahlung wuerde eine Lastschriftzeile nur teilweise bezahlen | Manuelle Pruefung | Buche die Teilzahlung im Banking-Bereich. Prüfe danach den Zahlungsplan der Rechnung. |
| Ueberzahlung: … bleibt am Bankumsatz offen | Manuelle Pruefung | Siehe 7.3, Punkt 3. |
| Der Betrag passt zu mehreren offenen Rechnungen | Manuelle Pruefung | Frage den Zahler oder ordne im Banking-Bereich zu. |
| Der Verwendungszweck nennt mehrere IDs | Manuelle Pruefung | Ordne im Banking-Bereich zu. |
| Vorschlag: … | Vorschlag | Siehe 7.5. |
| … hat keine offene Rechnung / Der Betrag passt zu keiner offenen Rechnung | Offen | Fehlt die Rechnung noch, lege sie an. ERPverein prüft beim nächsten Abgleich erneut. Sonst ordne im Banking-Bereich zu. |
| Kein Signal | Offen | Ordne im Banking-Bereich zu. Für feste Vorgänge lege eine `Bank Transaction Rule` an. |
| Automatische Zuordnung fehlgeschlagen | Offen oder Vorschlag | Behebe den Grund, zum Beispiel ein fehlendes Geschäftsjahr. Klicke im Bankimport „Abgleich starten“. |
| Die Zahlung … wurde storniert | Manuelle Pruefung | ERPverein bucht diesen Bankumsatz nicht erneut. Kläre ihn im Banking-Bereich. |

### 7.7 Was du nicht tust

**VORSICHT:** Storniere keine Zahlung, die ERPverein zu einer Überweisung gebucht hat. Der Bankumsatz geht dann auf „Manuelle Pruefung“. ERPverein bucht ihn nicht erneut.

1. Lege `Bank Transaction Rules` nur für Gebühren und feste Vorgänge an, nicht für Beiträge oder Mieten. Eine Regel, die zu einem Bankumsatz passt, macht aus einer Buchung einen Vorschlag.
2. Schalte `Auto reconcile Payments` nicht ein (1.7). Es verteilt Zahlungen bei einem gemeinsamen Kunden über Mitgliedsbeitrag und Miete.

### 7.8 Test auf der Test-Site

1. Setze den Go-live-Stichtag der Test-Site in die Vergangenheit (5.9).
2. Buche eine Rechnung für einen Selbstzahler. Notiere die Rechnungsnummer und den Betrag.
3. Erzeuge mit dem Testwerkzeug aus dem Phasenbericht eine synthetische Überweisung mit der Rechnungsnummer im Verwendungszweck. Sende sie an ERPverein.
4. Prüfe: Zahlung gebucht, Rechnung „Paid“, Bankumsatz „Reconciled“ und „Abgeglichen“.
5. Wiederhole mit einer zweiten Rechnung. Nimm als Verwendungszweck nur „Mitgliedsbeitrag“ und als Name den Kundennamen. Prüfe: Bankumsatz „Vorschlag“, keine Zahlung. Bestätige den Vorschlag (7.5).

**VORSICHT:** Die Test-Site enthält danach gebuchte Testzahlungen. Setze sie vor dem Go-live neu auf (E21).

---

## 8. Arbeitsvorrat

### 8.1 Überblick

Der Arbeitsvorrat zeigt jeden offenen Fall an einer Stelle: mit Bereich, Datum, Link, Betrag, Kunde, Status, Grund und nächster Aktion. Öffne ihn über „Vereinsverwaltung“ → „Arbeitsvorrat“.

1. Der Bericht braucht die Rolle `Accounts User` oder `Accounts Manager`.
2. Einzugsläufe und Einzugspositionen erscheinen nur mit der Rolle `System Manager`. Fehlt sie, nennt der Bericht die ausgeblendeten Bereiche.
3. IBANs erscheinen nur maskiert (`**** 1234`).
4. Ein Fall verschwindet, sobald er erledigt ist. Du musst nichts abhaken.

### 8.2 Bereiche

| Bereich | Wann ein Fall entsteht | Nächste Aktion |
|---|---|---|
| Bankumsatz | Der Bankumsatz ist nicht vollständig abgeglichen: „Offen“, „Vorschlag“, „Manuelle Pruefung“ oder ein aufgehobener Abgleich | Vorschlag bestätigen (7.5) oder im Banking-Bereich zuordnen. Gebühren: `Bank Transaction Rule` anlegen. Rücklastschrift in „Manuelle Pruefung“: „Rücklastschrift zuordnen“ oder Gegenbuchung von Hand (9.6). |
| Einzugslauf | Der Lauf steht auf „Manuelle Pruefung“, oder die Sammelgutschrift fehlt nach Einzugsdatum plus Toleranz | Lauf öffnen. Bei fehlender Gutschrift Umsätze abrufen oder bei der Bank nachfragen. |
| Einzugsposition | Die Position steht auf „Manuelle Pruefung“, oder sie ist zurückbelastet und wartet auf deine Entscheidung | Im Banking-Bereich klären, dann „Als erledigt markieren“ (6.8). Bei einer Rücklastschrift: „Erneut einziehen“ oder „Auf Überweisung umstellen“ (9.5). |
| Bankimport | Status „Teilfehler“ oder „Fehler“, oder der Abgleich ist fehlgeschlagen | Grund beheben, dann „Erneut verarbeiten“ oder „Abgleich starten“. |
| Zahlungsplan | Die Rechnung hat einen Hinweis zum Zahlungsplan, zum Beispiel „Mandatssumme größer als Rechnungsbetrag“ | Mandat korrigieren, dann „Zahlungsplan aus Mandat neu erzeugen“, oder „Zahlungsplan bearbeiten“ (Abschnitt 2). |
| Vorabankündigung | Eine Lastschriftzeile ohne Einzugsposition ist in den nächsten Tagen fällig (Filter „Vorabankündigung: Tage voraus“, Standard 45). Die Vorabankündigung fehlt oder kommt zu spät. | Rechnung bis zum genannten Tag per E-Mail senden oder die Vorabankündigung manuell bestätigen (2.5). |

### 8.3 Täglicher Ablauf

1. Öffne nach jedem Abruf den Arbeitsvorrat.
2. Arbeite zuerst „Bankimport“ und „Vorabankündigung“ ab. Sie haben Fristen.
3. Bestätige oder verwirf die Vorschläge.
4. Kläre die übrigen Bankumsätze im Banking-Bereich. Der Knopf „Banking-Bereich öffnen“ steht oben im Bericht.
5. Markiere geklärte Einzugspositionen als erledigt.
6. Entscheide über zurückbelastete Positionen (9.5).

**HINWEIS:** Mit dem Filter „Bereich“ zeigst du nur einen Bereich, zum Beispiel nur Vorschläge im Bereich „Bankumsatz“.

---

## 9. Rücklastschriften und Rejects

### 9.1 Überblick

Gibt die Bank eine Lastschrift zurück, belastet sie das Vereinskonto. ERPverein findet die Einzugsposition, bucht eine Gegenbuchung und öffnet die Rechnung wieder. Die ursprüngliche Zahlung bleibt bestehen.

```text
Bankimport → Abgleich im Hintergrund → Sammelgutschriften (Abschnitt 6) → Rücklastschriften → Überweisungen (Abschnitt 7)
→ Einzugsposition gefunden: Gegenbuchung (Journal Entry) buchen und mit dem Bankumsatz abgleichen
→ Position „Rueckbelastet“, Rechnung wieder offen, Lauf „Teilweise zurueckbelastet“
→ du entscheidest je Position: erneut einziehen oder auf Überweisung umstellen
```

1. Ein Reject kommt bei der HVB am Tag der Sammelgutschrift als eigene Belastung. Die Gutschrift enthielt den Betrag. ERPverein bucht den Reject deshalb wie eine Rücklastschrift, meist ohne Gebühr.
2. Die Gegenbuchung hat drei Zeilen:

   | Konto | Soll | Haben |
   |---|---|---|
   | Forderungen (12170), Kunde der Rechnung, Referenz auf die Rechnung | Hauptbetrag | |
   | Gebührenkonto aus dem Exportprofil (68550), Kostenstelle der Rechnung | Gebühr | |
   | Vereinskonto (18000) | | Gesamtbelastung |

3. Ohne Gebühr entfällt die mittlere Zeile.
4. Die Gebühr trägt der Verein. Sie ändert den offenen Betrag der Rechnung nicht. ERPverein berechnet sie dem Zahler nicht weiter.
5. ERPverein öffnet auch die Zahlungsplanzeile wieder. Sie bleibt aber gesperrt, bis du entscheidest (9.5).
6. Der Bankimport zeigt das Ergebnis, zum Beispiel „1 Ruecklastschriften gebucht“.

**VORSICHT:** Nach einer Rückgabe zieht ERPverein nie selbst erneut ein. Ein neuer Einzug braucht deine Entscheidung, eine neue Fälligkeit und bei Mitgliedern eine neue Vorabankündigung.

### 9.2 So findet ERPverein die Einzugsposition

ERPverein prüft jeden offenen Bankumsatz mit einer Belastung in dieser Reihenfolge:

| Nr. | Signal | Regel |
|---|---|---|
| 1 | End-to-End-Referenz | Sie ist der Name der Einzugsposition. Sie gilt in jeder Kategorie. |
| 2 | Mandatsreferenz | Nur bei „Ruecklastschrift“ oder „Lastschrift-Rueckgabe (Reject)“. |
| 3 | IBAN der Gegenseite | Nur bei diesen Kategorien. Mit Mandat müssen beide passen. |
| 4 | Betrag | Ohne End-to-End-Referenz muss die Belastung genau gleich dem Positionsbetrag plus den Gebühren aus dem Text sein. |
| 5 | Zeitliche Nähe | Passen mehrere Positionen, zählt das nächste Einzugsdatum, höchstens 14 Tage vor der Belastung. |

1. ERPverein bucht nur, wenn genau eine Position passt.
2. Name und Betrag allein lösen nie eine Gegenbuchung aus.
3. Rückgaben aus der Zeit vor dem Go-live finden keine Position. Vereinsmeister nutzte andere Referenzen. Buche sie im Banking-Bereich (9.6).

### 9.3 Hauptbetrag und Gebühr

1. Der Hauptbetrag ist der Betrag der Einzugsposition.
2. Die Gebühr ist die Gesamtbelastung minus Hauptbetrag. Es gibt keine Obergrenze (deine Entscheidung vom 03.11.2026).
3. Nennt der Text Gebühren („Eigengebühren“, „Fremdgebühren“, „Entgelt“), muss ihre Summe gleich der Gebühr sein. Sonst bucht ERPverein nichts.
4. Ist die Gesamtbelastung kleiner als der Hauptbetrag, bucht ERPverein nichts.
5. Den Rückgabegrund liest ERPverein aus dem Text, zum Beispiel „Sonstige Gründe“ (MS03), „Konto aufgelöst“ (AC04) oder „Deckung fehlt“ (AM04). Die Position zeigt Code und Text.

### 9.4 Status

| Objekt | Status | Bedeutung |
|---|---|---|
| Einzugsposition | Rueckbelastet | Gegenbuchung gebucht. Abschnitt „Ruecklastschrift“ nennt Bankumsatz, Gegenbuchung, Hauptbetrag, Gebühr und Grund. |
| Einzugslauf | Teilweise zurueckbelastet | Mindestens eine Position kam zurück. Alle anderen sind bezahlt oder erledigt. |
| Einzugslauf | Abgeschlossen | 8 Wochen nach dem Einzugsdatum, wenn jede Rückgabe entschieden ist. Ein täglicher Job setzt den Status. |
| Bankumsatz | Abgeglichen | Gegenbuchung gebucht und abgeglichen. |
| Bankumsatz | Offen | ERPverein wartet, zum Beispiel weil die Zahlung der Position noch nicht gebucht ist. Der Hinweis nennt den Grund. |
| Bankumsatz | Manuelle Pruefung | ERPverein bucht nichts. Der Hinweis nennt den Grund. ERPverein ändert den Bankumsatz danach nicht mehr von selbst. |

**HINWEIS:** Kommt nach dem Abschluss noch eine Rückgabe (zum Beispiel eine nicht autorisierte Lastschrift, bis 13 Monate), bucht ERPverein sie trotzdem. Der Lauf geht dann zurück auf „Teilweise zurueckbelastet“ und schließt nach deiner Entscheidung wieder.

### 9.5 Nach der Rückgabe entscheiden

Die Zahlungsplanzeile bleibt gesperrt, bis du entscheidest. Der Arbeitsvorrat zeigt jede Position ohne Entscheidung.

1. Öffne die Einzugsposition, zum Beispiel aus dem Arbeitsvorrat.
2. Lies den Rückgabegrund. Bei „Konto aufgelöst“ oder „Kein gültiges Mandat“ ziehe nicht erneut ein.
3. Klicke „Rücklastschrift“ und wähle:
   - **Erneut einziehen:** Gib eine neue Fälligkeit an. Sie liegt mindestens 14 Tage in der Zukunft (bei Mieten genügt der Vorlauf). ERPverein ändert die Zeile über „Zahlungsplan bearbeiten“. Die Vorabankündigung steht danach auf „Erneut senden“. Sende die Rechnung jetzt per E-Mail. Der nächste Einzugslauf nimmt die Zeile auf, wenn die Frist erfüllt ist.
   - **Auf Überweisung umstellen:** Die Zeile bekommt die Zahlungsweise „Überweisung“, auf Wunsch mit neuer Fälligkeit. Sende die Rechnung erneut, damit der Zahler den offenen Betrag kennt. ERPverein bucht eine passende Überweisung automatisch (Abschnitt 7).
4. Schreibe auf Wunsch eine Notiz. ERPverein speichert Entscheidung, Benutzer, Datum und Notiz an der Position und kommentiert Position, Lauf und Rechnung.

**HINWEIS:** „Auf Überweisung umstellen“ geht auch, wenn du das Mandat schon widerrufen hast. Weitere Lastschriftzeilen derselben Rechnung zieht ERPverein dann nicht ein; die Vorschau nennt das inaktive Mandat. Stelle sie mit „Zahlungsplan bearbeiten“ ebenfalls auf „Überweisung“.

**VORSICHT:** Die Entscheidung ist endgültig. Danach lässt sich die Gegenbuchung nicht mehr stornieren.

### 9.6 Manuelle Prüfung

| Grund (Anfang) | Was du tust |
|---|---|
| Mehrere Einzugspositionen passen | Prüfe Mandat, IBAN und Betrag. Ordne die Rücklastschrift der richtigen Position zu (siehe unten). |
| Widerspruch bei der Gebuehr | Prüfe den Text, frage bei Bedarf bei der Bank nach. Ordne die Rücklastschrift dann der Position zu. Die Gebühr ist die Belastung minus Positionsbetrag. |
| Keine Einzugsposition passt | Meist eine Rückgabe aus Vereinsmeister. Buche sie im Banking-Bereich von Hand. |
| Die Belastung … ist kleiner als der Betrag | Frage bei der Bank nach. Buche dann im Banking-Bereich von Hand. |
| Die Position … ist schon zurueckbelastet | Doppelte Rückgabe? Frage bei der Bank nach. |
| Die Position … ist als erledigt markiert | Du hast die Position von Hand geklärt. Buche die Rückgabe von Hand. |
| Die Gegenbuchung … wurde storniert | Siehe 9.7. |

So ordnest du eine Rücklastschrift zu:

1. Öffne den Bankumsatz.
2. Klicke „ERPverein“ → „Rücklastschrift zuordnen“.
3. Wähle die bezahlte Einzugsposition. Schreibe auf Wunsch eine Notiz.
4. Klicke „Gegenbuchung buchen“. ERPverein bucht wie beim automatischen Abgleich (9.1) und nennt dich im Hinweis. Ist die Belastung kleiner als der Positionsbetrag oder die Position nicht bezahlt, lehnt ERPverein ab.
5. Entscheide danach über die Position (9.5).

**HINWEIS:** Fehlt eine Einrichtung, zum Beispiel das Gebührenkonto im Exportprofil oder eine Kostenstelle, bleibt der Bankumsatz „Offen“. Der Hinweis nennt den Grund, der Bankimport zählt einen Fehler. Behebe den Grund; der nächste Abgleich bucht die Rücklastschrift.

So buchst du eine Gegenbuchung von Hand (nur ohne ERPverein-Position):

1. Öffne im Banking-Bereich `/banking` den Bankumsatz.
2. Lege einen Buchungssatz (`Journal Entry`, Typ „Bank Entry“) an: Soll Forderungskonto mit Kunde und Referenz auf die Rechnung (Hauptbetrag), Soll 68550 (Gebühr), Haben Vereinskonto (Gesamtbelastung).
3. Gleiche ihn mit dem Bankumsatz ab.
4. Öffne die Rechnung. Prüfe den Zahlungsplan. Ändere die Zeile bei Bedarf mit „Zahlungsplan bearbeiten“.

**HINWEIS:** Ein Buchungssatz von Hand ändert die Zahlungsplanzeile nicht. ERPNext ändert Zeilen nur bei Zahlungen.

### 9.7 Gegenbuchung stornieren

Storniere eine Gegenbuchung nur, wenn ERPverein sie falsch zugeordnet hat.

1. Öffne die Gegenbuchung aus der Einzugsposition.
2. Klicke „Stornieren“. Das geht nur vor deiner Entscheidung (9.5) und nur mit den Rollen des Kassenwarts.
3. ERPverein schließt die Zahlungsplanzeile wieder. Position und Bankumsatz gehen auf „Manuelle Pruefung“. ERPverein bucht für diese Rückgabe keine neue Gegenbuchung.
4. Buche den Fall von Hand (9.6). Markiere die Position danach als erledigt (6.8).
5. Mit „Berichtigen“ (Amend) legst du aus der stornierten Gegenbuchung einen eigenen Buchungssatz an. ERPverein entfernt dabei seine Felder; der neue Buchungssatz ändert die Zahlungsplanzeile nicht.

### 9.8 Was du nicht tust

**VORSICHT:** Storniere nie die ursprüngliche Zahlung einer zurückbelasteten Position. ERPverein lehnt das ab. Die Rechnung wäre sonst doppelt offen.

**VORSICHT:** Nutze für eine echte Rücklastschrift nie „Unreconcile Payment“. Das ist nur für eine falsche Zuordnung gedacht.

**VORSICHT:** Lege keine `Bank Transaction Rule` für Rücklastschriften an. Eine Regel bucht den Umsatz sonst an ERPverein vorbei.

### 9.9 Test auf der Test-Site

1. Setze den Go-live-Stichtag der Test-Site in die Vergangenheit (5.9).
2. Führe einen Einzug bis „Abgestimmt“ durch (6.7). Notiere den Namen einer Einzugsposition (`SEPA-POS-…`) und ihren Betrag.
3. Erzeuge mit dem Testwerkzeug aus dem Phasenbericht eine synthetische Rücklastschrift: Betrag plus 10,67 EUR, Datum zwei Tage nach dem Einzug, End-to-End-Referenz gleich Positionsname, Gebühren „3,00“ und „7,67“.
4. Sende sie an ERPverein (Befehl im Phasenbericht).
5. Prüfe: Gegenbuchung mit drei Zeilen, Bankumsatz „Reconciled“ und „Abgeglichen“, Position „Rueckbelastet“, Rechnung wieder offen, ursprüngliche Zahlung gebucht, Lauf „Teilweise zurueckbelastet“.
6. Entscheide „Auf Überweisung umstellen“. Sende danach eine synthetische Überweisung mit der Rechnungsnummer (7.8). Prüfe: Rechnung „Paid“.

**VORSICHT:** Die Test-Site enthält danach gebuchte Testbelege. Setze sie vor dem Go-live neu auf (E21).
