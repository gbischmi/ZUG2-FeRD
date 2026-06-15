# ZUG2-FeRD // Community Edition

<p align="center">
  <img src="assets/logo.png" alt="ZUG2-FeRD Logo" width="220">
</p>

<p align="center">
  <strong>Ein kostenfreies, schlankes B2B-Werkzeug zur § 14 UStG-konformen Veredelung, Prüfung und Zusammenführung von E-Rechnungen mit rechnungsbegründenden Unterlagen (RBU).</strong>
</p>

<p align="center">
  <a href="https://codeberg.org/gbischmi/ZUG2-FeRD"><img src="https://img.shields.io/badge/Codeberg-Main_Repo-blue?style=flat-square&logo=codeberg&logoColor=white" alt="Codeberg Main Repo"></a>
  <a href="https://github.com/gbischmi/ZUG2-FeRD"><img src="https://img.shields.io/badge/GitHub-Mirror-gray?style=flat-square&logo=github&logoColor=white" alt="GitHub Mirror"></a>
</p>

---

## 💡 Das Problem & Die Mission

In der heutigen B2B-Praxis stehen kleine und mittelständische Unternehmen (KMU) wie Handwerksbetriebe, Autohäuser oder Dienstleister vor einer doppelten Hürde bei der gesetzlichen E-Rechnungspflicht:

1. **Das unvollständige Layout:** Branchensoftware erzeugt zwar oft valide ZUGFeRD-XML-Datensätze, das visuelle PDF-Dokument ist jedoch häufig ungestaltet, fehlerhaft und enthält kein Firmen-Corporate-Identity (fehlendes digitales Briefpapier). Dadurch fehlen im sichtbaren Teil oft zwingende Pflichtangaben nach § 14 Abs. 4 UStG.
2. **Die Dokumenten-Zersplitterung:** Wichtige **rechnungsbegründende Unterlagen (RBU)** wie Aufmaßblätter, Arbeits-/Einsatznachweise, Baustellenberichte, Bilder oder Abnahmeprotokolle werden separat verschickt. Weder Sender noch Empfänger verfügen im Standard über eine Software, die diese Anlagen sauber mit dem maschinenlesbaren XML-Datensatz in einem einzigen Container verheiratet.

**ZUG2-FeRD** setzt genau hier an – als praktisches, lizenzkostenfreies Hilfsmittel von privat für Gewerbetreibende. Wir *"Ziehen zu FeRD"*, indem wir die originale E-Rechnung, optionale visuelle Briefbögen und alle Anlagen zu einem einzigen, rechtssicheren **PDF/A-3b-Belegpaket** verschmelzen. Sowohl auf der Seite der Rechnungssteller als auch auf der Seite der Rechnungsempfänger ist eine stabile Belegpaket-Verarbeitung in den genutzten Anwendungen leider immer noch kein einheitlich sauber abgebildeter Standard.

---

## ✨ Kernfunktionen

* **§ 14 UStG Compliance-Check:** Automatischer Abgleich der XML-Stammdaten des Ausstellers (Name, Steuernummer, USt-IdNr.) mit dem extrahierten Text der PDF-Vorschau. Proaktive Warnung bei unvollständigem Layout.
* **Mustergültiges Briefpapier-Stamping:** Nachträgliches Einbetten eines vorhandenen Firmenbriefbogens (PDF) als visuelle Hintergrund-Ebene (wahlweise auf Seite 1 oder alle Seiten) via `pypdf`, ohne die Maschinenlesbarkeit der Daten zu berühren.
* **RBU-Anlagen-Konverter:** Direktes Einbetten und Verschmelzen von zusätzlichen PDF-Anlagen und Bildformaten (PNG, JPG via `Pillow` auf DIN A4 skaliert) direkt in das Rechnungs-Dokument.
* **ZUGFeRD PDF/A-3b Container-Compilation:** Erzeugt normkonforme Hybrid-Dateien inklusive korrekter XMP-Metadaten und Validierung des Endergebnisses.
* **Mandanten-Sperre (User-Branding):** Das Tool prägt sich beim Erst-Import auf Ihr Unternehmen und verhindert durch einen Hard-Block die versehentliche Vermischung von Fremddaten.
* **Hierophantische Resilienz (Selbstheilung):** Autonomer Integritäts-Heartbeat, automatische JRE/Mustang-CLI-Wartung und kryptografisch abgesicherte Log-Restaurierung bei Manipulationen.

---

## 🏗️ Architektur & Workspace-Governance

Das Projekt ist streng nach dem Prinzip der **portablen Kapselung** aufgebaut. Die Anwendung läuft vollständig autark innerhalb ihres Stammverzeichnisses, benötigt keine globalen System-Installationen und lässt sich rückstandslos verschieben (z.B. auf USB-Sticks).

---
## 🚀 Installation & Start

Da die Software alle Laufzeitumgebungen und Prüf-Engines beim ersten Start asynchron im Hintergrund über offizielle Schnittstellen bezieht, ist das Setup denkbar simpel.
Voraussetzungen

* **Python 3.11 oder höher

* **Ein Betriebssystem mit gängigen CLI-Zugriffen (Windows, Linux, macOS)

Starten

1. **Klonen Sie das Repository (oder den Mirror):

* **git clone [https://codeberg.org/gbischmi/ZUG2-FeRD.git](https://codeberg.org/gbischmi/ZUG2-FeRD.git)
   cd ZUG2-FeRD

2. **Installieren Sie die minimalen Python-Abhängigkeiten:

* **pip install -r requirements.txt

3. **Starten Sie das Tool:

* **python run.py
 
---
## 🔒 Forensische Auditierung

Um Missbrauch und nachträgliche Manipulationen des Verarbeitungsprotokolls auszuschließen, verfügt die Anwendung über ein integriertes, hardwaregebundenes Schatten-Log (.sys_cache.dat), welches parallel zum normalen Log im AES-256-Verfahren verschlüsselt und komprimiert mitgeschrieben wird.

Zugriff für Administratoren: Ein 5-facher Klick auf das Copyright-Label im Footer der GUI öffnet das verdeckte Authentifizierungsfeld. Nach Eingabe des Codes 0000 lässt sich der unzensierte, forensische Audit-Trail einsehen und exportieren.
 
---
## ⚖️ VOLLSTÄNDIGER RECHTLICHER HINWEIS & LIZENZTEXT (Direkteinbettung aus LICENSE.md)

##🛑 WICHTIGER HINWEIS FÜR NUTZER:
Mit der Nutzung dieses Programms und/oder des Quellcodes erklärt sich der Anwender mit den nachfolgenden Bedingungen, Haftungsausschlüssen und Lizenzvorgaben ausdrücklich, unwiderruflich und rechtsverbindlich einverstanden.

================================================================================
          LIZENZBEDINGUNGEN, MARKENRECHT & RECHTLICHER HAFTUNGSAUSSCHLUSS
================================================================================
Projekt: ZUG2-FeRD (Community Edition)
Urheber: Bereitgestellt von Privatperson an Gewerbetreibende (B2B)
Gewinnabsicht: KEINE (Kostenfreies Open-Source-Gemeinschaftsprojekt)

1. HAFTUNGSBUSSCHLUSS NACH DEUTSCHEM SCHENKUNGSRECHT (§ 521 BGB)
--------------------------------------------------------------------------------
Die Bereitstellung dieser Software ("ZUG2-FeRD") erfolgt vollständig unentgelt-
lich und ohne jede Absicht der Einnahmen- oder Gewinnerzielung. Rechtlich handelt
es sich bei der Überlassung um eine Schenkung bzw. eine reine Gefälligkeit im
geschäftlichen Verkehr.

* GESETZLICHE HAFTUNGSBESCHRÄNKUNG: Gemäß § 521 BGB haftet der Schenker dem
  Beschenkten ausschließlich für Vorsatz und grobe Fahrlässigkeit. Eine Haftung
  für einfache oder normale Fahrlässigkeit ist vollumfänglich ausgeschlossen.
* AUSSCHLUSS DER SACH- UND RECHTSMÄNGELHAFTUNG: Die Software wird im gegenwär-
  tigen Zustand ("as is" / wie gesehen) zur Verfügung gestellt. Es wird keiner-
  lei Gewährleistung, Garantie oder Zusicherung für die Funktion, Fehlerfreiheit,
  Marktgängigkeit oder Eignung für einen bestimmten Zweck übernommen.
* KEIN ANSPRUCH AUF SUPPORT: Es besteht kein Anspruch auf Wartung, Reparatur,
  Besserung, Updates oder technischen Support durch den Urheber.
* ALLEINIGES BETRIEBS- UND STEUERRISIKO: Die Software verarbeitet sensible steuer-
  relevante Daten (E-Rechnungen nach dem ZUGFeRD-Standard). Die finale Prüfung
  der erzeugten PDF/A-3b-Dokumente und XML-Datensätze auf Konformität mit der
  europäischen Norm EN 16931 sowie den Vorgaben der Finanzbehörden (GoBD / Um-
  satzsteuergesetz) obliegt einzig und allein dem Anwender.
* SCHADENSERSATZAUSSCHLUSS: Der Urheber haftet unter keinen Umständen für direkte,
  indirekte, zufällige oder Folgeschäden (einschließlich, aber nicht beschränkt
  auf: entgangenen Gewinn, Betriebsunterbrechung, steuerliche Fehlberechnungen,
  aberkannte Vorsteuerabzüge oder Datenverlust), die durch die Nutzung oder Un-
  fähigkeit zur Nutzung dieser Software entstehen.

2. OPEN-SOURCE-LIZENZBEDINGUNGEN (COMMUNITY EDITION)
--------------------------------------------------------------------------------
Jedem Anwender wird hiermit das kostenfreie, nicht-exklusive Recht eingeräumt,
diese Software im Rahmen seiner gewerblichen oder privaten Tätigkeit zu nutzen,
zu kopieren und auszuführen, sofern folgende Bedingungen erfüllt sind:

1. Beibehaltung der Hinweise: Der rechtliche B2B-Disclaimer im Footer der gra-
   fischen Benutzeroberfläche (GUI) sowie alle Copyright-Hinweise dürfen weder
   entfernt, modifiziert noch unkenntlich gemacht werden.
2. Keine kommerzielle Weiterveräußerung: Es ist untersagt, diese Software oder
   Teile davon als eigenständiges Produkt gegen Entgelt zu verkaufen oder zu vermieten.
3. Audit-Trail-Integrität: Die integrierten Mechanismen zur forensischen Proto-
   kollierung (Schatten-Log `.sys_cache.dat`) dienen der Beweissicherung im
   Schadensfall. Die gezielte Manipulation oder Löschung dieser Sicherheits-
   komponente zum Zweck der Täuschung führt zum sofortigen Erlöschen der Nutzungserlaubnis.

3. MARKENRECHTLICHE ABGRENZUNG (§ 23 MARKENG)
--------------------------------------------------------------------------------
Der gewählte Projektname „ZUG2-FeRD“ ist eine unabhängige Bezeichnung für dieses
freie Software-Werkzeug.

* MARKENRECHTE: Die Begriffe „ZUGFeRD“ und „FeRD“ sind geschützte Marken der
  AWV e.V. (Arbeitsgemeinschaft für wirtschaftliche Verwaltung e.V.).
* BESCHREIBENDE NUTZUNG: Die Verwendung dieser Begriffe oder ähnlich klingender
  Abwandlungen innerhalb dieser Software und des Repositories dient ausschließ-
  lich als notwendiger, beschreibender Hinweis auf den technischen Zweck und die
  Bestimmung des Programms (das „Verheiraten“ und Validieren von Dokumenten des
  entsprechenden Standard-Typs gemäß § 23 MarkenG).
* KEINE PARTNERSCHAFT: Dieses Projekt steht in keinerlei geschäftlicher Verbin-
  dung, Autorisierung, Lizenzbeziehung oder offizieller Partnerschaft mit der
  AWV e.V. oder dem Forum für elektronische Rechnung Deutschland.

4. EINGEBETTETE FREMDKOMPONENTEN (THIRD-PARTY LICENSES)
--------------------------------------------------------------------------------
Diese Software greift zur Erfüllung ihrer Aufgaben im Wege der relativen Pfad-
Isolation auf externe Open-Source-Komponenten im Hintergrund zurück. Für diese
Komponenten gelten die Lizenzen der jeweiligen Urheber, für die vom Entwickler
dieses Tools ebenfalls keinerlei Haftung oder Support übernommen wird:

* Mustangproject CLI: Apache License 2.0 (Copyright by Jochen Stärk)
* Eclipse Temurin JRE: GPLv2 mit Classpath Exception (CPE).
* Python-Bibliotheken: PyQt6, pypdf, pdfplumber, cryptography, Pillow unterliegen
  ihren jeweiligen Open-Source-Lizenzen (MIT / BSD / LGPL).
================================================================================