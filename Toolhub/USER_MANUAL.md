# MLK-II ALVT Logic to AutoCAD Converter
## Tool User Manual (TUM) & Operational Guide

---

## 1. Document Control & Safety Warning

### Document Scope
This manual defines the operational instructions, configuration parameters, and safety rules for the MLK-II ALVT Logic to CAD Converter Tool suite:
*   **Standard Boolean to AutoCAD Converter** (Black & White layout schematics)
*   **Logic Differential CAD System** (Red & Green layout schematics)

### Safety Warning (Class T2 Compliance)
> [!IMPORTANT]
> **EN 50128 CLASS T2 COMPLIANCE REQUIREMENT**
> This tool is qualified as a Class T2 engineering support tool. It simplifies drawing production and visual verification of interlocking logic but does **not** replace vital signaling verification.
> *   **All outputs (DXF and PDF) must be manually reviewed and checked** by a qualified Signaling Designer and Checker.
> *   The final safety-critical audit must check the generated schematic diagrams against the approved source logic file (`.txt`/`.ML2`).

---

## 2. System Overview & Pre-requisites

The ALVT Logic to CAD Tool automatically translates compiled Microlok logic files into structured, scaled AutoCAD ladder schematics (`.dxf`) and merges them into vector PDF sheets.

### Software Prerequisites
For end-users running the compiled installer (`MLK-II_ALVT_Setup.exe`), the workstation has **zero dependency installation requirements**:
*   **Operating System**: Windows 10 / 11 (64-bit)
*   **AutoCAD / DXF Viewer**: AutoCAD, DWG TrueView, or any CAD viewer supporting standard DXF formats (AutoCAD 2010 DXF / AC1024 or newer).

> [!NOTE]
> The compiled executable runs in a standalone sandbox with Python and all drawing libraries pre-packaged. No local Python environment or library installations are required.

### Developer & Audit Dependencies (Source Build Only)
For developers compiling the tool from source, or for safety auditors conducting the T2 independent assessment on the code base:
*   **Language**: Python 3.10+
*   **Core Libraries**: `PySide6` (GUI), `ezdxf` (CAD), `pymupdf` (PDF generation & merge), `pandas` (VDU utilities).


---

## 3. Standard Logic-to-CAD Converter

This tool translates a single Microlok logic file into standard black & white ladder schematics.

### 3.1 Interface Fields

| Field Name | Description | Example / Default Value |
| :--- | :--- | :--- |
| **Source Logic (.txt)** | The path to the compiled Microlok logic source file. | `DER_C2_S01.txt` |
| **CAD Template (.dxf)** | Optional template containing the standard title block frame, borders, and layers. | `Template.dxf` |
| **Base File Name** | The prefix path where drawings are saved. Sheet numbers are appended automatically. | `C:\Drawings\INASRR52SNT.dxf` |
| **Font Family** | The AutoCAD text style to apply. | `SIMPLEX` / `ARIAL` / `ROMANS` |
| **Font Size** | The height of text annotations on the ladder. Title block sheet numbers scale to this size. | `1.5` |
| **Start Sheet No** | The sheet number where the logic schematics begin. | `100` |
| **Reserved Index** | The number of sheets reserved for Cover and Index sheets before the logic begins. | `50` |

### 3.2 Conversion Workflow

1.  **Launch standard converter**: Click on **BLACK/WHITE CIRCUIT** in the selection hub.
2.  **Select Source Logic**: Click **Browse** next to *Source Logic* and choose your `.txt` or `.ML2` logic file. The *Base File Name* will autofill using the logic file name prefix.
3.  **Specify CAD Template**: Browse and select your project's `.dxf` template frame.
4.  **Confirm Base File Name**: Adjust the *Base File Name* to match your station's naming standard (e.g. `INASRR52SNT.dxf`).
5.  **Set Page Parameters**:
    - Select a font family (e.g. `SIMPLEX`).
    - Select font size (defaults to `1.5`).
    - Input the starting sheet number (e.g. `1001` or `100`) directly via keyboard.
    - Set the number of index/cover sheets to reserve (e.g. `50` or `5`).
6.  **Run**: Click **EXECUTE CONVERSION**.
    - Stale `.dxf` sheets with the active prefix from previous runs are deleted automatically.
    - Standard drawing index sheets are calculated and drawn.
    - Logic equations are parsed and drawn.
    - If **Generate Merged PDF** is checked, sheets are converted and consolidated into a clean PDF named `[BaseFileName].pdf`.

---

## 4. Logic Differential CAD System

This tool diffs two Microlok logic files and renders the modifications in color:
*   **Added Logic**: Rendered in **RED**.
*   **Deleted Logic**: Rendered in **GREEN** (dashed outline).
*   **Modified Logic**: Old rungs in **GREEN**, replaced rungs in **RED**.

### 4.1 Interface Fields

| Field Name | Description | Example / Default Value |
| :--- | :--- | :--- |
| **Old Logic (Base)** | The original baseline logic file path. | `DER_C2_S01_V1.txt` |
| **New Logic (Target)** | The modified logic file path. | `DER_C2_S01_V2.txt` |
| **Differential Mode** | **Modified Logic Only**: Generates sheets containing only the changes.<br>**Full Logic with Red/Green**: Generates the complete schematic set with modifications highlighted in-place. | *Modified Logic Only* (Default) |

### 4.2 Differential Workflow

1.  **Launch differential tool**: Select **RED/GREEN EQUIVALENT** in the selection hub.
2.  **Load Files**: Select the *Old Logic* and *New Logic* source files.
3.  **Define Output Base**: Select the output Base File Name (e.g. `INASRR52SNT_DIFF.dxf`).
4.  **Select Differential Mode**:
    - Choose *Modified Logic Only* to quickly review altered equations.
    - Choose *Full Logic* to generate a complete approved drawing set showing red/green change status.
5.  **Run**: Click **EXECUTE DIFFERENTIAL RENDER**.
    - The tool generates a text-based diff report (`*_diff_report.txt`) listing all status tags (`ADDED`, `DELETED`, `MODIFIED`).
    - DXF sheets are saved with sheet numbering appended (e.g. `INASRR52SNT_DIFF00100.dxf`).
    - A merged color PDF is generated as `INASRR52SNT_DIFF.pdf`.

---

## 5. Layout & Margin Settings

Click **Layout Settings ⚙️** to configure standard drawing coordinate boundaries:
*   **Sheet Boundaries**: `y_max` (default `215`) and `y_min` (default `48`) define the ladder rendering window, leaving absolute clearance for the title block at the bottom and headers at the top.
*   **Margins**: Define horizontal margins (`x_off` and `full_w`) to ensure the ladder rungs are fully aligned within your custom title blocks.
*   **Grid Column Widths**: Adjust spacing for 1, 2, or 3 column structures to suit high-density signal rungs.

---

## 6. Safety Audit Rules for Signaling Checkers

> [!CAUTION]
> **MANDATORY CHECKLIST FOR T2 ISA AUDITS**
> Every signaling reviewer and independent checker must complete this verification checklist before signing off on ALVT drawings:

*   [ ] **Check 1: Text Diff Report Reconciliation**
    Open the generated text file `[BasePrefix]_diff_report.txt`. Verify that the quantity and naming of `ADDED` and `DELETED` equations listed in the text file matches the red and green highlights in the PDF drawings.
*   [ ] **Check 2: Drawing Page Sequence Integrity**
    Verify that the sheet sequence is continuous (e.g. from Page 1 to the end). Check that no sheets were skipped or lost during PDF consolidation.
*   [ ] **Check 3: Title Block Data Correspondence**
    Inspect the title block fields (specifically CRC, Checksum, Station Name, and Sheet Number) on each page. Confirm they correspond exactly with the metadata comments at the top of the input Microlok logic `.txt` file.
*   [ ] **Check 4: Ladder Continuity Circles**
    For long rungs that span across multiple sheets, check the continuation page numbers inside the continuation circles. Verify they accurately point to the preceding or succeeding sheet number.
