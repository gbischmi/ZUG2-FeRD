# ⚖️ RECHTLICHE HINWEISE, LIZENZ & HAFTUNGSAUSSCHLUSS
**Projekt:** ZUG2-FeRD (Community Edition)  
**Urheber:** Bereitgestellt von Privatperson an Gewerbetreibende (B2B)  
**Gewinnabsicht:** Keine (Kostenfreies Open-Source-Gemeinschaftsprojekt)

---

## 1. Haftungsausschluss nach deutschem Schenkungsrecht (§ 521 BGB)

Die Bereitstellung dieser Software ("ZUG2-FeRD") erfolgt vollständig unentgeltlich und ohne jede Absicht der Einnahmen- oder Gewinnerzielung. Rechtlich handelt es sich bei der Überlassung um eine Schenkung bzw. eine reine Gefälligkeit im geschäftlichen Verkehr.

* **Gesetzliche Haftungsbeschränkung:** Gemäß **§ 521 BGB** haftet der Schenker dem Beschenkten ausschließlich für Vorsatz und grobe Fahrlässigkeit. Eine Haftung für einfache oder normale Fahrlässigkeit ist vollumfänglich ausgeschlossen.
* **Ausschluss der Sach- und Rechtsmängelhaftung:** Die Software wird im gegenwärtigen Zustand ("as is" / wie gesehen) zur Verfügung gestellt. Es wird keinerlei Gewährleistung, Garantie oder Zusicherung für die Funktion, Fehlerfreiheit, Marktgängigkeit oder Eignung für einen bestimmten Zweck übernommen.
* **Kein Anspruch auf Support:** Es besteht kein Anspruch auf Wartung, Reparatur, Besserung, Updates oder technischen Support durch den Urheber.
* **Alleiniges Betriebs- und Steuerrisiko:** Die Software verarbeitet sensible steuerrelevante Daten (E-Rechnungen nach dem ZUGFeRD-Standard). Die finale Prüfung der erzeugten PDF/A-3b-Dokumente und XML-Datensätze auf Konformität mit der europäischen Norm EN 16931 sowie den Vorgaben der Finanzbehörden (GoBD / Umsatzsteuergesetz) obliegt einzig und allein dem Anwender. 
* **Schadensersatzausschluss:** Der Urheber haftet unter keinen Umständen für direkte, indirekte, zufällige oder Folgeschäden (einschließlich, aber nicht beschränkt auf: entgangenen Gewinn, Betriebsunterbrechung, steuerliche Fehlberechnungen, aberkannte Vorsteuerabzüge oder Datenverlust), die durch die Nutzung oder Unfähigkeit zur Nutzung dieser Software entstehen.

---

## 2. Open-Source-Lizenzbedingungen (Community Edition)

Jedem Anwender wird hiermit das kostenfreie, nicht-exklusive Recht eingeräumt, diese Software im Rahmen seiner gewerblichen oder privaten Tätigkeit zu nutzen, zu kopieren und auszuführen, sofern folgende Bedingungen erfüllt sind:

1. **Beibehaltung der Hinweise:** Der rechtliche B2B-Disclaimer im Footer der grafischen Benutzeroberfläche (GUI) sowie alle Copyright-Hinweise dürfen weder entfernt, modifiziert noch unkenntlich gemacht werden.
2. **Keine kommerzielle Weiterveräußerung:** Es ist untersagt, diese Software oder Teile davon als eigenständiges Produkt gegen Entgelt zu verkaufen oder zu vermieten.
3. **Audit-Trail-Integrität:** Die integrierten Mechanismen zur forensischen Protokollierung (Schatten-Log `.sys_cache.dat`) dienen der Beweissicherung im Schadensfall. Die gezielte Manipulation oder Löschung dieser Sicherheitskomponente zum Zweck der Täuschung führt zum sofortigen Erlöschen der Nutzungserlaubnis.

---

## 3. Markenrechtliche Abgrenzung (§ 23 MarkenG)

Der gewählte Projektname **„ZUG2-FeRD“** ist eine unabhängige Bezeichnung für dieses freie Software-Werkzeug.

* **Markenrechte:** Die Begriffe **„ZUGFeRD“** und **„FeRD“** sind geschützte Marken der *AWV e.V. (Arbeitsgemeinschaft für wirtschaftliche Verwaltung e.V.)*.
* **Beschreibende Nutzung:** Die Verwendung dieser Begriffe oder ähnlich klingender Abwandlungen innerhalb dieser Software und des Repositories dient **ausschließlich als notwendiger, beschreibender Hinweis auf den technischen Zweck und die Bestimmung** des Programms (das „Verheiraten“ und Validieren von Dokumenten des entsprechenden Standard-Typs gemäß **§ 23 MarkenG**).
* **Keine Partnerschaft:** Dieses Projekt steht in keinerlei geschäftlicher Verbindung, Verbindung, Autorisierung, Lizenzbeziehung oder offizieller Partnerschaft mit der AWV e.V. oder dem Forum für elektronische Rechnung Deutschland.

---

## 4. Eingebettete Fremdkomponenten (Third-Party Licenses)

Diese Software greift zur Erfüllung ihrer Aufgaben im Wege der relativen Pfad-Isolation auf externe Open-Source-Komponenten im Hintergrund zurück. Für diese Komponenten gelten die Lizenzen der jeweiligen Urheber, für die vom Entwickler dieses Tools ebenfalls **keinerlei Haftung oder Support** übernommen wird:

* **Mustangproject CLI (Validierungs-Engine):** Unterliegt der *Apache License 2.0* (Copyright by Jochen Stärk // mustangproject.org).
* **Eclipse Temurin JRE (Portable Java-Laufzeitumgebung):** Unterliegt der *GPLv2 mit Classpath Exception (CPE)*.
* **Python-Bibliotheken (PyQt6, pypdf, pdfplumber, cryptography, Pillow):** Unterliegen ihren jeweiligen, liberalen Open-Source-Lizenzen (MIT, BSD, LGPL).

Mit der Nutzung dieser Software erklärt sich der Anwender mit allen oben genannten Punkten ausdrücklich und unwiderruflich einverstanden.