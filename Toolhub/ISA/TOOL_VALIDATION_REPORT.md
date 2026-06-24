# Tool Validation Plan & Report (TVP/TVR)
## MLK-II ALVT Logic to CAD Converter Tool

---

## 1. Document Control

| Metadata Field | Value |
| :--- | :--- |
| **Document Reference** | ALVT-TVR-01 |
| **Associated Software Version** | 16.1.35 |
| **Safety Integrity Classification** | Class T2 (CENELEC EN 50128) |
| **Author** | Ashish Dixit |
| **Validation Result** | **PASSED** |
| **Release Date** | 2026-06-24 |

---

## 2. Introduction & Validation Scope

This Tool Validation Plan & Report (TVP/TVR) documents the testing framework and validation execution results for the MLK-II ALVT Logic to CAD Converter. The purpose of validation is to demonstrate that the tool operates correctly, reliably, and consistently in translating Boolean interlocking logic into CAD schematics, satisfying EN 50128 Class T2 qualification criteria.

The validation scope covers:
1.  **Logical Equivalence**: Verifying contacts are placed and connected in accordance with boolean equations.
2.  **Boundary Constraints**: Verifying layout scaling, dynamic sizing, and multi-sheet index parsing.
3.  **Differential Correctness**: Verifying added/deleted logic is highlighted without errors.

---

## 3. Validation & Test Environment

All validation testing was executed under the following configuration:
*   **Operating System**: Windows 11 (64-bit)
*   **Python Runtime**: Python 3.11.3 (embedded in virtual env)
*   **CAD Rendering Engine**: `ezdxf` 1.1.0
*   **PDF Compiler**: `PyMuPDF` (fitz) 1.22.0
*   **GUI Libraries**: `PySide6` 6.5.0

---

## 4. Test Case Definitions & Results

### 4.1 Logical & Parsing Correctness

#### VAL-TC-001: Boolean Operator Interpretation
*   **Objective**: Verify that boolean logical operations are drawn in the schematic correctly.
*   **Input**: `ASSIGN A * B * ~C TO D;`
*   **Expected Output**: Contacts `A` and `B` drawn in series (normally open), contact `C` drawn in series (normally closed) leading to coil `D`.
*   **Result**: **PASS** (Logical equivalence verified by DXF output check).

#### VAL-TC-002: Scoped Timer Bit Parsing
*   **Objective**: Verify that `TIMER BITS` header search bounds do not capture metadata comments or revision logs.
*   **Input**: `KMRC-27066-23-2.mll` logic file containing the substring "TIMER BITS" inside top-level revision logs.
*   **Expected Output**: Scoped search identifies exactly **335** timer bits (instead of 26,058), outputting a correct packet sync size of **9,700 bytes** in `packet_validator.py`.
*   **Result**: **PASS** (Timer count and packet sizes verified correct).

---

### 4.2 Drawing Layout, Sizing & Suffix Formatting

#### VAL-TC-003: Dynamic Title Block Sheet Number Sizing
*   **Objective**: Verify that drawing sheet numbers in the title block scale dynamically with the GUI-configured font size.
*   **Input**: Font Size set to `1.5` in standard converter.
*   **Expected Output**: Title block text height for sheet numbers is drawn at exactly `1.5` units in DXF.
*   **Result**: **PASS** (Variable height scaling verified in layout output).

#### VAL-TC-004: Standard 5-Digit Sheet Numbering
*   **Objective**: Verify drawing filenames match the `[ProjectCode][StationCode][5-digit sheet number]` standard without intermediate strings (no `_Page` or `_Index`).
*   **Input**: Base Name prefix `INASRR52SNT`.
*   **Expected Output**: DXF filenames are named:
    - `INASRR52SNT00001.dxf` (Index Page)
    - `INASRR52SNT00100.dxf` (Logic Page start from 100)
*   **Result**: **PASS** (Zero-padded 5-digit filename formats verified).

#### VAL-TC-005: File Cleanup & Temp Directory Isolation
*   **Objective**: Verify that older drawing sheets matching the active prefix are cleaned up, and PDF merge does not include unrelated DXFs from the destination folder.
*   **Input**: Folder containing `INASRR52SNT00100.dxf` (old) and `OTHER_PROJECT.dxf`.
*   **Expected Output**: Stale `INASRR52SNT*` files are deleted. Merged PDF contains only the newly generated sheets in the current run (isolated via temp directory).
*   **Result**: **PASS** (Isolated temp folder and glob cleanup verified).

---

### 4.3 Boundary & Differential Limits

#### VAL-TC-006: High Start Sheet Limits (Direct Keyboard Entry)
*   **Objective**: Verify the UI accepts typed starting numbers like `1001` or `3001` directly.
*   **Input**: Type `3001` into `Start Sheet No` spinbox.
*   **Expected Output**: Starting sheet is set to `3001` without capping at `999` or reverting.
*   **Result**: **PASS** (Numeric range verified up to `99999`).

#### VAL-TC-007: Differential Color Highlights
*   **Objective**: Verify modification statuses are drawn in red/green correctly.
*   **Input**: Diffs of revised rungs.
*   **Expected Output**: `ADDED` coils/contacts in red, `DELETED` contacts in green (dashed), `UNCHANGED` in black/white.
*   **Result**: **PASS** (Color highlighting verified).

---

## 5. Test Suite Execution Logs

The unit and regression test suite was executed in the virtual environment. All 7 core test cases passed successfully.

```
& "c:\Users\ashish\My Drive\Python\.venv\Scripts\python.exe" -m unittest discover -s tests -p "test_*.py"
Ran 7 tests in 0.039s

OK
```

---

## 6. Validation Conclusion

The MLK-II ALVT Logic to CAD Converter (v16.1.35) has successfully satisfied all logical validation criteria and boundary tests defined in this plan. No safety-critical errors or representation mismatches were detected. The tool is **approved for Class T2 vital design support operations**.
