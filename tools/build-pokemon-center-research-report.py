from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


ROOT = Path(r"C:\Users\A\Desktop\aresUpdatedares")
OUTPUT = ROOT / "reports" / "ARES_Pokemon_Center_Deep_Research.docx"


def set_cell_fill(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_border(cell, color="D9D9D9", size="6"):
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.first_child_found_in("w:tcBorders")
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = f"w:{edge}"
        element = borders.find(qn(tag))
        if element is None:
            element = OxmlElement(tag)
            borders.append(element)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), size)
        element.set(qn("w:color"), color)


def add_table(doc, headers, rows, widths=None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    for i, header in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = str(header)
        set_cell_fill(cell, "30363D")
        set_cell_border(cell)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        for run in cell.paragraphs[0].runs:
            run.font.bold = True
            run.font.color.rgb = RGBColor(255, 255, 255)
            run.font.size = Pt(8.5)
        if widths:
            cell.width = Cm(widths[i])
    for row_index, row in enumerate(rows):
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = str(value)
            cells[i].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            set_cell_border(cells[i])
            if row_index % 2:
                set_cell_fill(cells[i], "F4F6F8")
            for paragraph in cells[i].paragraphs:
                paragraph.paragraph_format.space_after = Pt(0)
                for run in paragraph.runs:
                    run.font.size = Pt(8.5)
            if widths:
                cells[i].width = Cm(widths[i])
    doc.add_paragraph().paragraph_format.space_after = Pt(0)
    return table


def add_bullet(doc, text, level=0):
    p = doc.add_paragraph(style="List Bullet" if level == 0 else "List Bullet 2")
    p.add_run(text)
    return p


def add_number(doc, text):
    p = doc.add_paragraph(style="List Number")
    p.add_run(text)
    return p


def add_source_marker(paragraph, number):
    run = paragraph.add_run(str(number))
    run.font.superscript = True


doc = Document()
section = doc.sections[0]
section.top_margin = Cm(2.0)
section.bottom_margin = Cm(1.8)
section.left_margin = Cm(2.1)
section.right_margin = Cm(2.1)

styles = doc.styles
styles["Normal"].font.name = "Aptos"
styles["Normal"].font.size = Pt(9.5)
styles["Normal"].paragraph_format.space_after = Pt(6)
styles["Normal"].paragraph_format.line_spacing = 1.08
styles["Title"].font.name = "Aptos Display"
styles["Title"].font.size = Pt(25)
styles["Title"].font.bold = True
styles["Title"].font.color.rgb = RGBColor(0, 0, 0)
for name, size in (("Heading 1", 16), ("Heading 2", 12), ("Heading 3", 10.5)):
    styles[name].font.name = "Aptos Display"
    styles[name].font.size = Pt(size)
    styles[name].font.bold = True
    styles[name].font.color.rgb = RGBColor(0, 0, 0)
    styles[name].paragraph_format.space_before = Pt(10)
    styles[name].paragraph_format.space_after = Pt(4)

title = doc.add_paragraph(style="Title")
title.add_run("ARES Pokemon Center Drop Runtime Analyse")

p = doc.add_paragraph()
p.add_run("Haupturteil. ").bold = True
p.add_run(
    "Die Runtime erreicht den Checkout grundsätzlich mit derselben Browser-Session, doch die derzeitige "
    "Drop-Zuverlässigkeit wird stärker durch unsichere Übergänge zwischen Produkt, Warenkorb und Global-E "
    "gefährdet als durch Semantic Field Detection. Der einzige vollständige sichtbare Lauf benötigte 126,35 s "
    "bis zum ausgefüllten Profil; 36,56 s davon lagen im Semantic Checkout. Vier weitere Läufe endeten mit "
    "Navigationstimeouts, einer mit einem nicht bereiten Klick und einer mit abgelehnter lokaler Verbindung."
)
add_source_marker(p, 1)

p = doc.add_paragraph()
p.add_run("Entscheidung. ").bold = True
p.add_run(
    "Vor Optimierungen am Tippverhalten müssen zwei Drop-kritische Zustandsübergänge abgesichert werden: "
    "Cart-Bestätigung nach Add-to-Cart und Erhalt der sessiongebundenen Checkout-Weiterleitung. Danach bietet "
    "die Country-Auswahl den besten kleinen Performance-Fix: zwei aussichtslose Select-Timeouts kosten allein "
    "5,16 s und lassen zugleich ein Pflichtattribut unbestätigt."
)
add_source_marker(p, 2)

doc.add_heading("Prioritäten", level=1)
add_table(
    doc,
    ["Rang", "Befund", "Drop-Wirkung", "Eingriff", "Risiko"],
    [
        ["P0", "Erzwungene Navigation 350 ms nach Add-to-Cart", "Cart-Request kann vor Abschluss verlassen werden", "Auf bestätigten Cart-Zustand warten; direkte URL nur als Fallback", "Mittel"],
        ["P0", "Erzwungene /intl-checkout-Navigation 250 ms nach Gast-Klick", "Session- oder Redirect-Parameter können überschrieben werden", "Echte Zielnavigation übernehmen; canonical Fallback nur bei Stillstand", "Mittel"],
        ["P0", "Queue-Freigabe kann nur aus URL-Verschwinden entstehen", "Challenge-Seite kann fälschlich als Release gelten", "Storefront- oder autoritativen Release-Nachweis verlangen", "Mittel"],
        ["P1", "Country Select scheitert zweimal", "5,16 s Verlust und unvollständiger Checkout", "Optionen einmal lesen und DE gegen Value/Text normalisieren", "Niedrig"],
        ["P1", "Form-Actions dominieren Semantic Checkout", "31,11 s für Textfelder", "Zuerst RPC-Teilzeiten messen; Tippmodell erst danach ändern", "Niedrig bis mittel"],
        ["P2", "Challenge Poll ist fire-and-forget", "Überlappende Solver-Aktionen und Cancel-Rennen möglich", "Ein in-flight Flag plus Abort-Prüfung", "Niedrig"],
    ],
    [1.0, 4.1, 5.1, 5.0, 2.0],
)

doc.add_page_break()
doc.add_heading("1 Systemgrenze und tatsächlicher Ablauf", level=1)
p = doc.add_paragraph(
    "Der produktive Pfad besteht aus Orchestrator, externem Browser-Worker, BrowserGateMonitorExecutor, "
    "BrowserQueueWaiter, EarlyGateBrowserTaskExecutor und PokemonCenterReleaseJourney. Der Worker übergibt "
    "den bereits geöffneten Browser-Handle aus dem Monitor direkt an den Completion-Pfad. Das ist die richtige "
    "Grundarchitektur: Queue-Cookies, Browserprofil und Seitensitzung können erhalten bleiben."
)
add_source_marker(p, 3)

add_table(
    doc,
    ["Phase", "Verantwortung", "Beobachteter Übergang"],
    [
        ["Task und Worker", "Orchestrator und BrowserWorkerPoolClient", "CREATED → QUEUED → STARTING → RUNNING"],
        ["Gate Monitor", "BrowserGateMonitorExecutor", "Browser öffnen, passive Queue-Signale lesen"],
        ["Queue", "BrowserQueueWaiter", "WAITING_QUEUE; zwei freie Signale bestätigen Release"],
        ["Completion", "EarlyGateBrowserTaskExecutor", "vorhandenen Handle übernehmen, Storefront öffnen"],
        ["Commerce", "PokemonCenterReleaseJourney", "Neuheiten → Produkt → Cart → Global-E Checkout"],
        ["Checkout", "SemanticCheckoutPreparer", "Felder abwarten, Profil und Zahlung vorbereiten"],
        ["Finaler Kauf", "Guarded submit", "nur bei expliziter allowFinalPurchase-Freigabe"],
    ],
    [3.0, 6.2, 7.9],
)

doc.add_heading("Vollständiger Lauf", level=2)
add_table(
    doc,
    ["Zeit ab Start", "Ereignis", "Dauer oder Zustand"],
    [
        ["0,00 s", "Task Start", "QUEUED → STARTING → RUNNING"],
        ["0,12 s", "Browser Context Start", "4,30 s"],
        ["46,72 s", "Queue-Zustand", "RUNNING → WAITING_QUEUE"],
        ["48,25 s", "Queue freigegeben", "WAITING_QUEUE → RUNNING"],
        ["56,90 s", "Produktsuche", "16,22 s"],
        ["73,11 s", "Add to Cart", "6,36 s"],
        ["79,47 s", "Checkout öffnen", "7,91 s"],
        ["87,39 s", "Checkout-Feldgate", "0,56 s"],
        ["87,97 s", "Semantic Checkout", "36,56 s"],
        ["126,35 s", "Profil bereit", "kontrollierter Abbruch vor Kauf"],
    ],
    [3.2, 8.1, 5.8],
)

doc.add_heading("Interpretation", level=2)
p = doc.add_paragraph(
    "Der Beginn des Ausfüllens ist nach einem verfügbaren Checkout-DOM nicht langsam: Das Feldgate und die "
    "ersten Scans benötigen zusammen rund 0,68 s. Die wahrgenommene Pause entsteht überwiegend davor in "
    "openCheckout. Von dessen 7,91 s entfallen 2,95 s auf den maschinellen Klick und ungefähr 4,96 s auf "
    "Wartezeit, Navigation, DOM-Verarbeitung und Zustandsaktualisierung."
)
add_source_marker(p, 1)

doc.add_page_break()
doc.add_heading("2 Queue und Challenge Analyse", level=1)

doc.add_heading("Freigabenachweis", level=2)
p = doc.add_paragraph(
    "BrowserQueueWaiter wertet Netzwerk, DOM und URL aus. Sobald die Queue-URL verschwunden ist, liefert der "
    "URL-Kanal active=false. Zwei aufeinanderfolgende freie Signale reichen zur Freigabe. Das ist problematisch, "
    "wenn eine Queue auf eine separate CAPTCHA- oder Challenge-URL weiterleitet: Das Verlassen der Queue ist dann "
    "noch kein Storefront-Release."
)
add_source_marker(p, 4)

p = doc.add_paragraph()
p.add_run("Beobachtung im Test. ").bold = True
p.add_run(
    "Der Monitor meldete queue=detected source=url released=true. Damit wurde nicht durch einen Storefront-Marker "
    "oder einen autoritativen Serverstatus bewiesen, dass die Challenge abgeschlossen war. Der Test erreichte zwar "
    "den Checkout, doch die Freigaberegel ist für reale Drops zu schwach."
)
add_source_marker(p, 1)

doc.add_heading("Erkennungsverzögerung", level=2)
p = doc.add_paragraph(
    "Der Browser startete nach 0,12 s, aber WAITING_QUEUE erschien erst nach 46,72 s. Der Monitor erzwingt einen "
    "Mindestwert von 30 s für refreshIntervalMs, obwohl der Konstruktor standardmäßig 5 s vorgibt. Wenn der erste "
    "passive Snapshot kein Signal liefert, wartet der Monitor diesen gesamten Zeitraum und navigiert dann erneut. "
    "Bei kurzen Queues kann ARES dadurch den aktiven Zustand verpassen und erst den späteren URL-Wechsel sehen."
)
add_source_marker(p, 5)

doc.add_heading("Challenge Polling", level=2)
p = doc.add_paragraph(
    "pollChallenge startet challengeAction ohne await und ohne in-flight Sperre. Ein langsamer Solver kann daher "
    "noch laufen, wenn der nächste Poll ausgelöst oder der Task abgebrochen wird. Die Fehler werden absichtlich "
    "verschluckt. Das schützt den Queue-Loop vor Abstürzen, erschwert aber Fehlerdiagnose und erlaubt überlappende "
    "Challenge-Aktionen."
)
add_source_marker(p, 4)

doc.add_heading("Minimaler P0 Fix", level=2)
add_number(doc, "URL-clear allein nur als Übergangssignal behandeln, nicht als endgültige Freigabe.")
add_number(doc, "Freigabe bestätigen, wenn ein Storefront-Marker sichtbar ist, ein autoritativer Release-Status vorliegt oder die Challenge explizit als gelöst gemeldet wurde.")
add_number(doc, "Challenge-Poll mit einem in-flight Flag serialisieren und beim Abort keine neue Aktion beginnen.")

doc.add_page_break()
doc.add_heading("3 Produkt und Warenkorb", level=1)

doc.add_heading("Produktsuche", level=2)
p = doc.add_paragraph(
    "discover navigiert auf die Neuheiten-Seite, liest JSON-LD Produkte, bewertet Titel und Keywords und klickt "
    "anschließend bevorzugt die gerenderte Produktkarte. Falls sie nicht gefunden wird, erfolgt eine direkte "
    "Navigation. Nach dem Klick wird bis zu 8 s auf den Add-to-Cart-Control gepollt. Dieser Ansatz ist robust "
    "gegen verspätete Navigation, kostete im vollständigen Lauf aber 16,22 s."
)
add_source_marker(p, 6)

doc.add_heading("Add to Cart Race", level=2)
p = doc.add_paragraph(
    "addToCart klickt den Button, wartet fest 350 ms und navigiert danach bedingungslos zu /de-de/cart. Es gibt "
    "keine Bestätigung, dass die Add-to-Cart-Anfrage erfolgreich war, dass die Warenkorbanzahl gestiegen ist oder "
    "dass ein Cart-Token gesetzt wurde. Unter Last kann die direkte Navigation die noch laufende Anfrage abbrechen. "
    "Das ist ein Drop-Verlustpfad: ARES ist schnell im Cart, aber der Cart kann leer sein."
)
add_source_marker(p, 2)

doc.add_heading("Checkout Session Race", level=2)
p = doc.add_paragraph(
    "openCheckout klickt den Gast-Checkout, wartet fest 250 ms und navigiert dann bedingungslos auf eine selbst "
    "konstruierte /de-de/intl-checkout-URL. Wenn die echte Seite in dieser Zeit eine sessiongebundene Global-E-URL, "
    "Query-Parameter oder ein Zwischentoken erzeugt, überschreibt ARES den natürlichen Übergang. Der anschließende "
    "Titeltest bestätigt nur, dass irgendeine Checkout-URL geöffnet ist, nicht dass die korrekte Session übernommen wurde."
)
add_source_marker(p, 2)

doc.add_heading("Empfohlene Zustandsregel", level=2)
add_table(
    doc,
    ["Aktion", "Erfolgssignal", "Fallback"],
    [
        ["Add to Cart", "Cart-Badge, Cart-Response oder sichtbarer Cart mit Zielprodukt", "Canonical Cart erst nach begrenztem Stillstand öffnen"],
        ["Gast-Checkout", "URL-/Frame-Wechsel oder checkout-spezifischer Sessionmarker", "Canonical Checkout nur wenn kein Übergang begonnen hat"],
        ["Checkout bereit", "kein Blocker und Pflichtfeld sichtbar, enabled, stabil", "weiter pollen; keine feste Wartezeit"],
    ],
    [3.3, 8.2, 5.6],
)

doc.add_page_break()
doc.add_heading("4 Semantic Checkout", level=1)

doc.add_heading("Zeitverteilung", level=2)
add_table(
    doc,
    ["Komponente", "Dauer", "Anteil an Semantic Fill", "Bewertung"],
    [
        ["Semantic fill gesamt", "36,56 s", "100 %", "kritischer Checkout-Pfad"],
        ["Interaction formAction", "33,45 s", "91,5 %", "dominanter Kostenblock"],
        ["Textfeld-Aktionen", "31,11 s", "85,1 %", "Zeichenweise Eingabe und RPC"],
        ["Country Select Fehler", "5,16 s", "14,1 %", "zweimal outcome-timeout"],
        ["Alle Field Scans", "0,75 s", "2,1 %", "nicht zuerst optimieren"],
        ["Alle Klassifikationen", "0,008 s", "<0,1 %", "vernachlässigbar"],
    ],
    [5.0, 3.0, 4.0, 6.0],
)

doc.add_heading("Feldmessung", level=2)
add_table(
    doc,
    ["Feld", "Dauer", "Resultat", "Anmerkung"],
    [
        ["firstName", "3,68 s", "erfolgreich", "ein Versuch"],
        ["lastName", "4,27 s", "erfolgreich", "ein Versuch"],
        ["email", "5,74 s", "erfolgreich", "langer Wert"],
        ["countryCode", "2,55 s", "Timeout", "erster Select-Pfad"],
        ["address1", "4,77 s", "erfolgreich", "ein Versuch"],
        ["address2", "0,14 s", "übersprungen", "kein Profilwert"],
        ["city", "3,82 s", "erfolgreich", "ein Versuch"],
        ["postalCode", "3,72 s", "erfolgreich", "ein Versuch"],
        ["countryCode", "2,61 s", "Timeout", "Fallback wiederholt denselben Fehler"],
        ["phone", "0,08 s", "zunächst nicht interaktiv", "später erneut gefunden"],
        ["phone", "4,89 s", "erfolgreich", "zweiter Pfad"],
    ],
    [4.0, 2.5, 3.3, 8.2],
)

doc.add_heading("Warum Eingaben langsam sind", level=2)
p = doc.add_paragraph(
    "SemanticFieldAutofill prüft Sichtbarkeit und Enabled-State, liest vorhandene Werte, gibt dann Zeichen für "
    "Zeichen mit 45 bis 120 ms Verzögerung ein und simuliert mit 22 Prozent Wahrscheinlichkeit einen korrigierten "
    "Tippfehler. InteractionEngine führt zusätzlich Scroll, Stabilitätsprüfung und Outcome-Polling aus. Nach dessen "
    "erfolgreicher locator-value-equals-Prüfung liest SemanticFieldAutofill den Wert nochmals. Der größte Anteil "
    "ist jedoch die native Typing-Aktion selbst, nicht Field Classification."
)
add_source_marker(p, 7)

doc.add_heading("Pokeball und Loader", level=2)
p = doc.add_paragraph(
    "Das bestehende Checkout-Gate berücksichtigt indirekt den Global-E-Loader: Es wartet, bis mindestens drei "
    "Felder existieren und observeReady ein aktiviertes Nicht-Suchfeld findet. Das ist zeitlich offen und daher "
    "grundsätzlich richtig. Es prüft jedoch nicht ausdrücklich, ob ein sichtbares blockierendes Overlay weiterhin "
    "über dem Formular liegt. Ein im Hintergrund enabled Feld kann das Gate zu früh öffnen."
)
add_source_marker(p, 8)

doc.add_page_break()
doc.add_heading("5 Stabilität und Lifecycle", level=1)

doc.add_heading("Testbilanz", level=2)
add_table(
    doc,
    ["Probe", "Endzustand", "Letzter Fehler", "Aussage"],
    [
        ["04-19-21", "FAILED", "WinError 1225 Verbindung abgelehnt", "lokale Abhängigkeit nicht bereit"],
        ["04-23-17", "FAILED", "Interaction click not-ready", "UI-Zustand nicht klickbar"],
        ["04-29-09", "FAILED", "navigate timeout 35 s", "Navigation/RPC instabil"],
        ["04-32-32", "unvollständig", "navigate timeout 35 s", "kein sauberer Suite-Abschluss"],
        ["04-34-51", "unvollständig", "navigate timeout 35 s", "kein sauberer Suite-Abschluss"],
        ["04-40-55", "unvollständig", "kein Endfehler", "manuell/interaktiv beendet"],
        ["04-43-30", "unvollständig", "navigate timeout 35 s", "Context-Close begonnen"],
        ["04-50-09", "profile-ready", "kein Laufzeitfehler", "vollständiger sichtbarer Lauf"],
    ],
    [3.0, 3.1, 6.4, 5.5],
)

p = doc.add_paragraph(
    "Nur einer von acht protokollierten Versuchen erreichte den gewünschten Checkout-Messpunkt. Diese Quote darf "
    "nicht als Produktionsquote interpretiert werden, weil mehrere Läufe durch falsche Testverkettung, lokalen Server "
    "oder manuelle Unterbrechung beeinflusst waren. Trotzdem ist die Wiederholung des 35-s-Navigationstimeouts ein "
    "echter Stabilitätshinweis und muss in kontrollierten Wiederholungen isoliert werden."
)
add_source_marker(p, 9)

doc.add_heading("Lifecycle Stärken", level=2)
add_bullet(doc, "Worker-Heartbeat ist serialisiert und der Interval-Timer wird beim Stop gelöscht und unref gesetzt.")
add_bullet(doc, "Task-Eigentümer werden im finally aus der Pool-Zuordnung entfernt.")
add_bullet(doc, "Monitor und Early-Gate-Executor aborten aktive Controller bei Cancellation.")
add_bullet(doc, "Der Worker schließt Context und Profilbindung sowohl nach Erfolg als auch nach Fehler.")
add_bullet(doc, "Der finale Kauf ist durch allowFinalPurchase unmittelbar vor dem irreversiblen Klick geschützt.")

doc.add_heading("Lifecycle Risiken", level=2)
add_bullet(doc, "Fire-and-forget Challenge-Aktionen sind nicht an den AbortController gekoppelt.")
add_bullet(doc, "Fehler in passiver Queue-Telemetrie und Solver-Polls werden verschluckt; Root Cause kann verloren gehen.")
add_bullet(doc, "Ein recycelter Worker löscht alle taskIds und taskRefs; laufende Tasks sind danach auf Orchestrator-Recovery angewiesen.")
add_bullet(doc, "Für den Pokemon-Center-Pfad wurden keine gezielten Unit- oder Integrationstests gefunden.")

doc.add_page_break()
doc.add_heading("6 Root Causes", level=1)

add_table(
    doc,
    ["ID", "Root Cause", "Datei und Funktion", "Reproduzierbarkeit", "Folge"],
    [
        ["RC1", "Zeitbasierter Cart-Übergang statt bestätigtem Warenkorbzustand", "release-journey.ts addToCart", "deterministisch im Code", "leerer Cart oder verlorene Add-Anfrage"],
        ["RC2", "Zeitbasierter Checkout-Übergang überschreibt natürliche Weiterleitung", "release-journey.ts openCheckout", "deterministisch im Code", "verlorene Global-E Session"],
        ["RC3", "URL-clear zählt als Queue-Release", "queue-waiter.ts waitIfQueued", "im sichtbaren Lauf beobachtet", "Challenge kann übersprungen werden"],
        ["RC4", "Monitor wartet mindestens 30 s vor aktivem Fallback", "browser-gate-monitor-executor.ts constructor", "deterministisch; Lauf zeigt 46,7 s", "kurze Queue wird spät erkannt"],
        ["RC5", "Country Value wird ohne Optionsnormalisierung geschrieben", "semantic-field-autofill.ts selectLocator", "zweimal im Lauf", "5,16 s und fehlendes Land"],
        ["RC6", "Checkout-Typing ist absichtlich langsam und RPC-lastig", "semantic-field-autofill.ts fillLocator", "bei allen langen Feldern", "31,11 s Textfeldeingabe"],
        ["RC7", "Challenge Poll nicht serialisiert", "queue-waiter.ts pollChallenge", "codebasiert; Lasttest fehlt", "Überlappung und Cancel-Race"],
    ],
    [1.2, 5.4, 5.3, 3.3, 4.8],
)

doc.add_heading("Call Chains", level=2)
add_bullet(doc, "Cart: EarlyGateBrowserTaskExecutor.execute → journey.addToCart → GhostCursor click → 350 ms → page.goto cart.")
add_bullet(doc, "Checkout: execute → journey.openCheckout → GhostCursor click → 250 ms → page.goto intl-checkout → load-state → field gate.")
add_bullet(doc, "Field: prepareCheckoutUntilReady → SemanticCheckoutPreparer.prepare → fillSemantic → fillLocator → InteractionEngine.type → SeleniumBase RPC type.")
add_bullet(doc, "Queue: BrowserGateMonitorExecutor.execute → BrowserQueueWaiter.waitIfQueued → readSignal → DOM/network/URL → two release confirmations.")

doc.add_page_break()
doc.add_heading("7 Minimalfix Roadmap", level=1)

add_table(
    doc,
    ["Schritt", "Änderung", "Dateien", "Erwarteter Nutzen", "Risiko"],
    [
        ["1", "Country-Optionen einmal gegen ISO-Code und sichtbaren Text normalisieren; denselben fehlgeschlagenen Locator im selben Pass nicht erneut probieren", "semantic-field-autofill.ts", "ca. 5,16 s und vollständiges Land", "Niedrig"],
        ["2", "Add-to-Cart anhand Cart-Response oder sichtbarem Zielprodukt bestätigen; canonical URL nur als Fallback", "release-journey.ts", "höhere Cart-Zuverlässigkeit; keine garantierte Zeitersparnis", "Mittel"],
        ["3", "Natürliche Gast-Checkout-Navigation übernehmen; sessiongebundene Ziel-URL nicht überschreiben", "release-journey.ts", "höhere Global-E Session-Zuverlässigkeit", "Mittel"],
        ["4", "Queue-Release nicht allein aus URL-clear bestätigen", "queue-waiter.ts", "keine Challenge-Falschfreigabe", "Mittel"],
        ["5", "Challenge-Poll serialisieren und Abort respektieren", "queue-waiter.ts", "weniger Solver-Races", "Niedrig"],
        ["6", "RPC-Teilzeiten innerhalb type messen; erst danach Tippdelay oder Doppelevaluation ändern", "interaction-engine.ts und Python RPC", "belegbare statt geschätzte Eingabeoptimierung", "Niedrig"],
    ],
    [1.2, 7.3, 4.5, 5.0, 2.0],
)

doc.add_heading("Warum Schritt 1 zuerst", level=2)
p = doc.add_paragraph(
    "Die Country-Korrektur hat die beste Kombination aus geringer Eingriffsfläche, messbarer Zeitersparnis und "
    "funktionalem Gewinn. Sie verändert weder Queue noch Navigation, Solver, Fingerprint, Tippgeschwindigkeit oder "
    "State-Machine. Anders als das Entfernen einer Sicherheitsprüfung verhindert sie einen bereits beobachteten "
    "Fehler und spart zwei vollständige Outcome-Timeouts."
)

doc.add_heading("Abnahmekriterien je Änderung", level=2)
add_number(doc, "Mindestens fünf vollständige sichtbare Läufe mit derselben nummerierten Offline-Sequenz.")
add_number(doc, "Kein Lauf darf den lokalen Host verlassen; jede URL wird protokolliert.")
add_number(doc, "Queue-Release benötigt einen belegten Freigabegrund; URL-clear allein ist nicht ausreichend.")
add_number(doc, "Der Cart enthält Waldszene Puzzle, bevor Checkout geöffnet wird.")
add_number(doc, "Country wird in einem Versuch gesetzt und danach als vollständig bewertet.")
add_number(doc, "Kein zweiter Write auf bereits bestätigte Textfelder.")
add_number(doc, "Cancellation beendet Browser, Worker-Task, Poll-Timer und Solver-Aktion ohne Restprozess.")
add_number(doc, "Final Purchase bleibt in allen Tests deaktiviert.")

doc.add_page_break()
doc.add_heading("8 Messplan für Drop Readiness", level=1)

add_table(
    doc,
    ["Run", "Startzustand", "Pflichtmessung", "Erfolg"],
    [
        ["1 Cold", "neuer Worker und neues Profil", "Worker, Browser, Queue, Checkout", "profile-ready ohne Retry"],
        ["2 Warm", "gleicher Worker und Profil", "Session-Reuse, Startzeit", "keine doppelte Context-Erzeugung"],
        ["3 New Task", "neue Task-ID", "Owner-Zuordnung, Cookie-Erhalt", "korrekter Handle und Produkt"],
        ["4 Repeat", "gleiche Konfiguration erneut", "Listener und Timer", "keine Doppelereignisse"],
        ["5 Restart", "Worker und Browser neu", "Recovery und Child-Prozesse", "sauberer Neustart"],
    ],
    [2.2, 5.0, 7.1, 5.0],
)

doc.add_heading("Zusätzliche Instrumentierung", level=2)
add_bullet(doc, "Jede Navigation: requestedUrl, committedUrl, redirect chain, DOMContentLoaded, first storefront marker.")
add_bullet(doc, "Add-to-Cart: clickEnd, requestStart, responseEnd, cart token, item visible.")
add_bullet(doc, "Checkout: guestClick, redirectStart, committed session URL, blocker visible/hidden, first interactive field.")
add_bullet(doc, "Typing: readiness, scroll, focus, key emission, formatter settle, outcome verify und post-read getrennt.")
add_bullet(doc, "Queue: jede Signalquelle mit active, source, status, URL und release evidence.")
add_bullet(doc, "Lifecycle: aktive Timer, Listener, Pending RPCs und Child-PIDs vor und nach Task-Ende.")

doc.add_heading("Go No Go Kriterien", level=2)
add_table(
    doc,
    ["Bereich", "Go", "No Go"],
    [
        ["Queue", "5 von 5 Releases mit belastbarem Nachweis", "URL-clear ohne Challenge-/Storefront-Beleg"],
        ["Cart", "5 von 5 mit Zielprodukt vor Checkout", "direkte Navigation vor Cart-Bestätigung"],
        ["Checkout", "Country und Pflichtfelder vollständig", "wiederholte outcome-timeouts"],
        ["Stabilität", "keine Navigate-Timeouts oder Restprozesse", "ein reproduzierbarer 35-s Timeout"],
        ["Sicherheit", "Final Purchase Guard nachweislich blockiert", "irreversibler Klick ohne Freigabe"],
    ],
    [4.0, 7.2, 8.1],
)

doc.add_page_break()
doc.add_heading("9 Schlussfolgerung", level=1)
p = doc.add_paragraph(
    "ARES besitzt bereits die wesentlichen Bausteine für eine Drop-fähige Pokemon-Center-Runtime: externen Worker, "
    "profilgebundene Browser-Session, Queue-Telemetrie, Challenge-Anbindung, semantischen Checkout und einen harten "
    "Final-Purchase-Guard. Der sichtbare End-to-End-Lauf beweist, dass diese Kette bis profile-ready funktionieren kann."
)

p = doc.add_paragraph(
    "Die aktuelle Implementierung ist jedoch noch nicht Drop-ready. Die größten Risiken liegen an drei Übergängen, "
    "an denen Zeit statt Zustand verwendet wird: Queue verlassen, Add-to-Cart abschließen und Gast-Checkout öffnen. "
    "Diese Stellen können unter realer Last gerade dann versagen, wenn das Produkt verfügbar ist. Eine reine "
    "Beschleunigung der Felder würde die Gesamtlaufzeit verbessern, aber diese Verlustpfade nicht beheben."
)

p = doc.add_paragraph(
    "Die empfohlene Reihenfolge ist deshalb: Country-Fehler als kleinen sicheren Fix beseitigen, Cart und Checkout "
    "zustandsbasiert absichern, Queue-Freigabe härten und erst anschließend die native Typing-Pipeline mit feineren "
    "Timings optimieren. So steigt zuerst die Wahrscheinlichkeit, den Drop überhaupt bis zu einem gültigen Checkout "
    "zu tragen; Geschwindigkeit wird danach dort reduziert, wo Messdaten tatsächliche Wartezeit belegen."
)

doc.add_heading("Quellen", level=1)
sources = [
    "1. ARES Runtime Probe Log semantic-checkout-2026-09-13T04-50-09-605Z.jsonl, vollständiger sichtbarer Lauf, lokaler Zugriff.",
    "2. src/commerce/pokemon-center/release-journey.ts, Funktionen addToCart und openCheckout, Zeilen 172 bis 208, Commit d709c125.",
    "3. src/browser-worker/worker.ts, Monitor-zu-Completion Handle-Übergabe und Context-Cleanup, Zeilen 70 bis 103, Commit d709c125.",
    "4. src/browser-worker/queue-waiter.ts, waitIfQueued, readSignal und pollChallenge, Zeilen 166 bis 358, Commit d709c125.",
    "5. src/monitor/browser-gate-monitor-executor.ts, Refresh-Intervall und Queue-Schleife, Zeilen 22, 75 und 296 bis 340, Commit d709c125.",
    "6. src/commerce/pokemon-center/release-journey.ts, discover und waitForAddToCart, Zeilen 76 bis 143, Commit d709c125.",
    "7. src/browser-worker/semantic-field-autofill.ts sowie interaction-engine.ts, Typing- und Outcome-Pipeline, Commit d709c125.",
    "8. src/browser-worker/early-gate-task-executor.ts und semantic-field-autofill.ts, Checkout-Feldgate und observeReady, Commit d709c125.",
    "9. Acht JSONL-Dateien unter runtime-probe-logs, Laufzeitproben vom 13. September 2026, lokaler Zugriff.",
    "10. src/browser-worker/client.ts, Heartbeat, Worker-Recycle und Task-Owner-Cleanup, Commit d709c125.",
]
for source in sources:
    doc.add_paragraph(source)

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
doc.core_properties.title = "ARES Pokemon Center Drop Runtime Analyse"
doc.core_properties.subject = "Runtime, Stabilität und Performance des Pokemon-Center-Moduls"
doc.core_properties.author = "ARES Runtime QA"
doc.save(OUTPUT)
print(OUTPUT)
