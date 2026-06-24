# Changelog - MLK-II ALVT Tool

All notable changes to this project will be documented in this file.
This log is maintained in accordance with safety-critical audit standards for T2 Independent Safety Assessment (ISA).

## [16.1.35] - 2026-06-24

### Fixed
- **Timer Bits Parsing Safety**: Corrected `TIMER BITS` parsing logic in `core/mll_parser.py`, `core/comm_logic_validator.py`, and `core/timer_bits_validator.py` to search strictly after the `BOOLEAN BITS` section. This prevents comment blocks, revision log entries, and headers containing the substring "TIMER BITS" from being parsed as valid timer bit indices.
  - *Impact:* Fixed an issue in `KMRC-27066-23-2.mll` where the parsed timer count was incorrectly calculated as 26,058 instead of 335. This corrects the sync packet size calculation in `packet_validator.py` from 318,376 bytes to 9,700 bytes.
- **AutoCAD Sizing Scale**: Modified title block rendering in `scripts/logic_to_autocad.py` to scale the sheet numbers using the GUI-configured `font_size` instead of the hardcoded height value of `2.5`.

### Added
- **Clean Base Filename Prompts**: Configured destination path auto-updates in `gui/standalone_logic_cad.py` and `gui/standalone_logic_diff_gui.py` to default directly to `{logic_base}.dxf` (without generating `_CAD` or `_DIFF` suffixes in the middle of filenames).
- **AutoCAD 5-Digit Numbering Standard**: Generated drawing files now directly append a 5-digit zero-padded sheet number to the base prefix (e.g. `INASRR52SNT00100.dxf`).
- **Dynamic File Cleanup**: Added automatic deletion of old drawing sheets matching the active prefix pattern (e.g. `{base_prefix}_Page*.dxf`, `{base_prefix}[0-9][0-9][0-9][0-9][0-9].dxf`) before generation, preventing stale sheets from preceding runs from bleeding into consolidated PDFs.
- **Isolated PDF Generation**: Introduced `export_dxf_list_to_pdf` in `scripts/fast_dxf_to_pdf.py` to convert and merge *only* the current run's generated drawing sheets via a temporary folder. This leaves the destination folder clean of individual page PDFs and prevents merging unrelated DXFs in the folder.
- **Start Sheet and Reserved Index Keyboard Entry**: Expanded starting sheet spinbox range limit to `99999` (supporting up to 5-digit numbers) and reserved sheets range to `999` in the GUIs. This enables direct keyboard entry of higher start sheet numbers (e.g., `1001`, `2001`, `3001`) instead of forcing sequential scroll-only interaction.
