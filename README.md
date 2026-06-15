# ZUG2-FeRD // Community Edition

<p align="center">
  <img src="assets/logo.png" alt="ZUG2-FeRD Logo" width="220">
</p>

<p align="center">
  <strong>Ein kostenfreies, schlankes B2B-Werkzeug zur § 14 UStG-konformen Veredelung, Prüfung und Zusammenführung von E-Rechnungen mit rechnungsbegründenden Unterlagen (RBU).</strong>
</p>

<p align="center">
  <a href="https://codeberg.org/gbischmi/ZUG2-FeRD"><strong>Codeberg Haupt-Repository</strong></a> | 
  <a href="https://github.com/gbischmi/ZUG2-FeRD"><strong>GitHub Mirror</strong></a>
</p>

---

## 💡 Das Problem & Die Mission

In der heutigen B2B-Praxis stehen kleine und mittelständische Unternehmen (KMU) wie Handwerksbetriebe, Autohäuser oder Dienstleister vor einer doppelten Hürde bei der gesetzlichen E-Rechnungspflicht:

1. **Das unvollständige Layout:** Branchensoftware erzeugt zwar oft valide ZUGFeRD-XML-Datensätze, das visuelle PDF-Dokument ist jedoch häufig ungestaltet, fehlerhaft und enthält kein Firmen-Corporate-Identity (fehlendes digitales Briefpapier). Dadurch fehlen im sichtbaren Teil oft zwingende Pflichtangaben nach § 14 Abs. 4 UStG.
2. **Die Dokumenten-Zersplitterung:** Wichtige **rechnungsbegründende Unterlagen (RBU)** wie Aufmaßblätter, Arbeits-/Einsatznachweise, Baustellenberichte, Bilder oder Abnahmeprotokolle werden separat verschickt. Weder Sender noch Empfänger verfügen im Standard über eine Software, die diese Anlagen sauber mit dem maschinenlesbaren XML-Datensatz in einem einzigen Container verheiratet.

**ZUG2-FeRD** setzt genau hier an – als praktisches, lizenzkostenfreies Hilfsmittel von privat für Gewerbetreibende. Wir *"Ziehen zu FeRD"*, indem wir die originale E-Rechnung, optionale visuelle Briefbögen und alle Anlagen zu einem einzigen, rechtssicheren **PDF/A-3b-Belegpaket** verschmelzen.

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

```text
/ProjectRoot
 ├── bin/             <- Lokaler Speicherort für Mustang-CLI (.jar)
 ├── packages/        <- Isoliertes, portables OpenJDK (JRE Runtime)
 ├── log/             <- Transparente, menschlesbare Logfiles (365 Tage Rotation)
 ├── stationery/      <- Relativ abgelegte Briefbogen-Vorlagen (PDF)
 ├── assets/          <- Anwendungs-Assets (logo.png)
 └── .sys_cache.dat   <- Getarntes, AES-256 verschlüsseltes forensisches Audit-Log