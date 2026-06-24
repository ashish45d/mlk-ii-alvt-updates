# Safety Assessment Documentation Guide - MLK-II ALVT Tool
## Class T2 Tool Qualification Framework (CENELEC EN 50128)

This document outlines the structured documentation required for the **T2 Independent Safety Assessment (ISA)** of the MLK-II Boolean to CAD Converter Tool. 

For a Class T2 software tool, the safety assessor (ISA) will verify that the tool is sufficiently validated to ensure it does not introduce silent faults or fail to identify logic changes.

---

## Required Documentation Structure

To qualify the tool, you must prepare three key documents:
1. **Tool Classification Report (TCR)**
2. **Tool Validation Plan & Report (TVP/TVR)**
3. **Tool User Manual & Safety Rules (TUM)**

---

### Document 1: Tool Classification Report (TCR)
*Purpose: Justify to the assessor why this tool is classified as **Class T2** and not Class T3 (which would require much more expensive formal methods).*

#### Key Sections to Include:
1. **Tool Description**: Describe the tool’s function (translating Microlok logic files to AutoCAD schematics and generating diff reports).
2. **Tool Scope**: Limit the safety scope strictly to:
   - Standard Logic to CAD (B&W)
   - Logic Differential CAD (Red/Green)
3. **Safety Integration Level / Hazard Analysis**:
   - State: *"The tool does not run on-board or trackside and does not directly generate vital software."*
   - State: *"The output schematics (DXF/PDF) are subject to manual check by a signaling designer, verification by a checker, and final approval. Therefore, any drawing representation errors are caught before commissioning."*
4. **Classification Justification**: 
   - Classify as **Class T2** under CENELEC EN 50128.

---

### Document 2: Tool Validation Plan & Report (TVP / TVR)
*Purpose: Prove to the assessor that you have tested the tool extensively and it does not have faults.*

#### Key Sections to Include:
1. **Validation Strategy**: 
   - Unit tests to verify parsing rules (e.g. timer parsing, unused bits).
   - Equivalence partition testing: Feeding valid logic files and inspecting output drawings.
   - Regression testing: Ensuring new updates do not break existing modules.
2. **Test Cases**:
   - **TC-01: Boolean Parser Integrity**: Verify that boolean operators (`*`, `+`, `~`) are translated into series, parallel, and normally-closed contacts correctly.
   - **TC-02: Timer Parsing Scope**: Verify that timer limits are parsed correctly (e.g. after the `BOOLEAN BITS` section, avoiding comments).
   - **TC-03: Sizing & Suffix Formatting**: Verify that font sizes scale dynamically and filenames follow the standard 5-digit number format.
   - **TC-04: Differential Logic Accuracy**: Verify that additions, deletions, and alterations are highlighted correctly in red/green.
3. **Test Results (TVR)**:
   - Capture console outputs of the test suite execution showing `OK`.
   - Provide visual drawing comparison tests (before/after).

---

### Document 3: Tool User Manual & Safety Rules (TUM)
*Purpose: Provide clear operating guidelines and specify "Safety Rules" that the operator must follow to guarantee safety.*

#### Key Sections to Include:
1. **System Requirements**: OS, Python dependencies, and AutoCAD viewer configuration.
2. **Step-by-step User Guide**: How to browse files, set start sheet numbers, scale font sizes, and run the tool.
3. **Assumptions & Restrictions**:
   - The input logic files must compile successfully on the Microlok compiler before being fed to this tool.
   - The designer must verify that the base file prefix matches their project name format.
4. **Tool Safety Rules (CRITICAL FOR ISA)**:
   - **Rule 1**: *"The verifier must perform a sample check on sheet coordinates and margins using AutoCAD."*
   - **Rule 2**: *"The consolidated PDF must be cross-checked against the generated individual DXF sheets to ensure page count equivalence."*
   - **Rule 3**: *"All Red/Green differential layouts must be reviewed in conjunction with the text-based diff report (`*_diff_report.txt`)."*

---

## Action Plan to Prepare for the Audit

1. **Step 1**: Run the existing unit test suite in your virtual environment and save the stdout as `test_results.log` (proves test execution).
2. **Step 2**: Generate a B&W circuit PDF and a Red/Green diff PDF for a sample project (acts as visual validation artifacts).
3. **Step 3**: Compile the final documents (TCR, TVR, TUM) using the sections outlined above.
