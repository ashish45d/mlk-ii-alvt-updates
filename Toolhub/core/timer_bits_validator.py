
# core/timer_bits_validator.py
# -*- coding: utf-8 -*-

"""
Timer Bits Validator (core module)
----------------------------------

Parses:
  1) TIMER BITS section     -> bit -> (SET_ms, CLEAR_ms)
  2) LOGIC BEGIN ... END LOGIC window -> explicit // STP / // STR comments tied to TO <bit>;

Validates:
  A) Bits used with TO <bit>; but missing inline/nearby // STP/STR comments
  B) Bits having STP/STR comments in logic but missing from TIMER BITS
  C) Value mismatches between TIMER BITS (SET/CLEAR) and comments (STP/STR), in ms with tolerance

Design/Rules:
  - **Fix**: TIMER BITS window starts just after the exact line "TIMER BITS".
    If multiple candidates exist, use the *last one before LOGIC BEGIN*.
    If none, fallback to the last tolerant "TIMER ... BIT" before LOGIC BEGIN.
  - End of TIMER BITS: LOGIC BEGIN (preferred), fallback to LOG BITS, else EOF.
  - Accept inline comments on the same 'TO ...;' line and comments on the following lines.
  - Aliases: // SP -> // STP, // SR -> // STR
  - Do NOT infer timers from suffixes like '_TSR'.
  - All names compared in UPPERCASE; units normalized (SEC/MSEC) and compared in ms.
  - Tolerance configurable (default 1 ms).

Public API:
  - TimerBitsValidator(tolerance_ms=1, default_comment_unit="SEC")
    .validate(file_path) -> list[str]
    .validate_many(file_paths) -> dict[str, list[str]]
    .validate_content(text) -> list[str]
"""

from __future__ import annotations
import os
import re
from typing import Dict, List, Tuple, Optional


# ---------- Unit normalization & conversion ----------

def normalize_unit(unit: str) -> str:
    u = (unit or "").strip().upper()
    if u in {"MS", "MSEC", "MSECS", "MILLISECONDS", "MILLISECOND"}:
        return "MSEC"
    if u in {"S", "SEC", "SECS", "SECOND", "SECONDS"}:
        return "SEC"
    return u


def to_milliseconds(val_str: str, unit_str: str) -> int:
    try:
        v = float(val_str)
    except Exception:
        return 0
    unit = normalize_unit(unit_str)
    if unit == "SEC":
        return int(round(v * 1000.0))
    # Treat blank/unknown as milliseconds
    return int(round(v))


def _equal_ms(a: int, b: int, tolerance_ms: int = 1) -> bool:
    return abs(int(a) - int(b)) <= int(tolerance_ms)


def _fmt_sec(ms: int) -> str:
    """Format milliseconds as seconds:
       - whole seconds: '10s'
       - fractional (up to 3 decimals): '1.25s' (trim trailing zeros)
    """
    if ms is None:
        return "—"
    sec = ms / 1000.0
    # If it's an integer number of seconds, print as int
    if ms % 1000 == 0:
        return f"{int(sec)}s"
    # Else up to 3 decimals, but strip trailing zeros and dot
    s = f"{sec:.3f}".rstrip('0').rstrip('.')
    return f"{s}s"

# ---------- Section boundary / sanitization helpers ----------

def _index_of_logic_begin(lines: List[str]) -> int:
    for i, L in enumerate(lines):
        if "LOGIC BEGIN" in L.upper():
            return i
    return len(lines)


def _find_timer_bits_window(content: str) -> Tuple[Optional[int], Optional[int]]:
    r"""
    Robustly locate the TIMER BITS block:
      - Prefer the exact line: ^\s*TIMER\s+BITS\s*$
        -> choose the *last* occurrence that appears before LOGIC BEGIN.
      - Fallback: the *last* tolerant 'TIMER ... BIT' header before LOGIC BEGIN.
      - End at LOGIC BEGIN (preferred), then LOG BITS, else EOF.

    Returns (start_line_index, end_line_index) where start_line_index points to
    the FIRST line *after* the heading ("TIMER BITS") so that the block is only the
    list of timer definitions.
    """
    lines = content.splitlines()
    logic_begin_idx = _index_of_logic_begin(lines)

    # Find the start index of BOOLEAN BITS section to limit our search window
    bool_idx = 0
    for i, L in enumerate(lines[:logic_begin_idx]):
        if re.match(r'^\s*(?:\d+\s+)?BOOLEAN\s+BITS\s*$', L, flags=re.IGNORECASE):
            bool_idx = i
            break

    # 1) Prefer exact "TIMER BITS"
    exact_idxs = [
        i for i, L in enumerate(lines[bool_idx:logic_begin_idx])
        if re.match(r'^\s*(?:\d+\s+)?TIMER\s+BITS\s*$', L, flags=re.IGNORECASE)
    ]
    start = None
    if exact_idxs:
        start = bool_idx + exact_idxs[-1] + 1  # just after "TIMER BITS"
    else:
        # 2) Fallback to tolerant 'TIMER ... BIT' (but still before LOGIC BEGIN)
        tol_idxs = [
            i for i, L in enumerate(lines[bool_idx:logic_begin_idx])
            if re.match(r'.*TIMER.*BIT.*', L, flags=re.IGNORECASE)
        ]
        if tol_idxs:
            start = bool_idx + tol_idxs[-1] + 1  # last tolerant occurrence
        else:
            return None, None

    # Determine end boundary
    end = None
    # Prefer LOGIC BEGIN
    for j in range(start, len(lines)):
        if "LOGIC BEGIN" in lines[j].upper():
            end = j
            break
    # Fallback to LOG BITS if LOGIC BEGIN not found
    if end is None:
        for j in range(start, len(lines)):
            if "LOG BITS" in lines[j].upper():
                end = j
                break
    if end is None:
        end = len(lines)

    return start, end


def _sanitize_timer_bits_block(block_lines: List[str]) -> str:
    """
    Remove decorative separators, trailing backslashes, and full comment lines; then join into one string.
    """
    clean_lines = []
    for ln in block_lines:
        s = ln.rstrip()
        # Skip decorative/separator lines
        if re.match(r"^\s*%\+", s):                 # e.g. "%+-----"
            continue
        if re.match(r"^\s*[-=\\/]{3,}\s*$", s):     # ====, \\\\, ////, ---
            continue
        # Remove trailing backslash that may cause concatenation
        s = re.sub(r"\\\s*$", "", s)
        # Drop full comment lines
        if s.strip().startswith("//"):
            continue
        clean_lines.append(s)

    joined = " ".join(clean_lines)
    joined = re.sub(r"\s+", " ", joined).strip()
    return joined


def preprocess_comments(content: str) -> str:
    """
    Normalize inline comments and alias SP/SR -> STP/STR. Ensure a newline before ASSIGN for block parsing.
    """
    # Standardize placement of comment marker after semicolon
    content = re.sub(r';\s*//', '; //', content)
    # Ensure a space after // for robust regex
    content = re.sub(r'//\s*', '// ', content)

    # Aliases: // SP -> // STP, // SR -> // STR
    content = re.sub(r'//\s*SP(\b|$)', '// STP ', content)
    content = re.sub(r'//\s*SR(\b|$)', '// STR ', content)

    # Ensure newline before ASSIGN to preserve logical blocks
    content = re.sub(r'([^\n])\s*(ASSIGN)', r'\1\n\2', content)
    return content


# ---------- Parse TIMER BITS & LOGIC ----------

def _parse_timer_bits_section(content: str) -> Dict[str, dict]:
    """
    Parse TIMER BITS into:
        { NAME_UPPER: {"set_ms": int, "clear_ms": int, "set_raw": "v:UNIT", "clear_raw": "v:UNIT"} }
    Supports 'ADJUSTABLE' keyword and comma-separated name lists.
    """
    start, end = _find_timer_bits_window(content)
    if start is None:
        return {}

    lines = content.splitlines()
    joined = _sanitize_timer_bits_block(lines[start:end])
    if not joined:
        return {}

    # Split by semicolon; tolerate trailing spaces
    entries = [e.strip() for e in re.split(r";", joined) if e.strip()]
    timers: Dict[str, dict] = {}

    # SET/CLEAR capture
    re_set = re.compile(r"SET\s*=?\s*([0-9]+(?:\.[0-9]+)?)\s*:\s*([A-Za-z]+)", re.IGNORECASE)
    re_clear = re.compile(r"CLEAR\s*=?\s*([0-9]+(?:\.[0-9]+)?)\s*:\s*([A-Za-z]+)", re.IGNORECASE)

    for entry in entries:
        # Strip ADJUSTABLE if present
        entry_clean = re.sub(r"\bADJUSTABLE\b", "", entry, flags=re.IGNORECASE).strip()
        if ":" not in entry_clean:
            continue

        names_part, rest = entry_clean.split(":", 1)
        names = [n.strip() for n in re.split(r",", names_part) if n.strip()]
        if not names:
            continue

        set_ms = clear_ms = 0
        set_raw = clear_raw = "0:SEC"

        m_set = re_set.search(rest)
        if m_set:
            v, u = m_set.group(1), m_set.group(2)
            set_ms = to_milliseconds(v, u)
            set_raw = f"{v}:{normalize_unit(u)}"

        m_clear = re_clear.search(rest)
        if m_clear:
            v, u = m_clear.group(1), m_clear.group(2)
            clear_ms = to_milliseconds(v, u)
            clear_raw = f"{v}:{normalize_unit(u)}"

        for nm in names:
            timers[nm.upper()] = {
                "set_ms": int(set_ms),
                "clear_ms": int(clear_ms),
                "set_raw": set_raw,
                "clear_raw": clear_raw,
            }
    return timers


def _find_logic_window(content: str) -> Tuple[int, int]:
    lines = content.splitlines()
    s = e = None
    for i, L in enumerate(lines):
        if s is None and "LOGIC BEGIN" in L.upper():
            s = i + 1
        elif s is not None and "END LOGIC" in L.upper():
            e = i
            break
    if s is None:
        s = 0
    if e is None:
        e = len(lines)
    return s, e


def _extract_logic_timers(content: str, default_comment_unit="SEC"):
    """
    Scan LOGIC window for timing comments around TO <bit>;
      - Capture inline on same line as TO ...; and
      - Capture on subsequent lines until ASSIGN or next TO.

    Returns:
      to_bits_upper: set[str]
      stp_str_ms: { NAME_UPPER: {"STP_ms": int|None, "STR_ms": int|None, "STP_raw": str|None, "STR_raw": str|None} }
    """
    pre = preprocess_comments(content)
    s, e = _find_logic_window(pre)
    logic_text = "\n".join(pre.splitlines()[s:e])

    to_re = re.compile(r"\bTO\s+([A-Za-z0-9_.]+)\s*;", re.IGNORECASE)
    # Robust action regex: allow STR or STP, unit optional (defaults to default_comment_unit)
    act_re = re.compile(
        r"//\s*(STP|STR)\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)?",
        re.IGNORECASE
    )

    lines = logic_text.splitlines()
    to_bits_upper = set()
    stp_str_ms: Dict[str, dict] = {}

    i = 0
    while i < len(lines):
        line = re.sub(r';\s*//', '; //', lines[i])
        m_to = to_re.search(line)
        if not m_to:
            i += 1
            continue

        name = m_to.group(1).strip().upper()
        to_bits_upper.add(name)

        STP_ms = STR_ms = None
        STP_raw = STR_raw = None

        # Capture inline comments on the same TO line
        for typ, val, unit in act_re.findall(line):
            u = normalize_unit(unit or default_comment_unit)
            ms = to_milliseconds(val, u)
            raw = f"{val}:{u}"
            if typ.upper() == "STP":
                STP_ms, STP_raw = ms, raw
            else:  # STR
                STR_ms, STR_raw = ms, raw

        # Forward scan within the same action block
        j = i + 1
        while j < len(lines):
            nxt = re.sub(r';\s*//', '; //', lines[j])
            if nxt.strip().upper().startswith("ASSIGN"):
                break
            if to_re.search(nxt):  # next TO begins a new block
                break

            m_act = act_re.search(nxt)
            if m_act:
                typ, val, unit = m_act.groups()
                u = normalize_unit(unit or default_comment_unit)
                ms = to_milliseconds(val, u)
                raw = f"{val}:{u}"
                if typ.upper() == "STP":
                    STP_ms, STP_raw = ms, raw
                else:  # STR
                    STR_ms, STR_raw = ms, raw
            j += 1

        if STP_ms is not None or STR_ms is not None:
            stp_str_ms[name] = {
                "STP_ms": STP_ms, "STR_ms": STR_ms,
                "STP_raw": STP_raw, "STR_raw": STR_raw,
            }

        i = j  # continue after scanned block

    return to_bits_upper, stp_str_ms


# ---------- Public API (class) ----------

class TimerBitsValidator:
    def __init__(self, tolerance_ms: int = 1, default_comment_unit: str = "SEC"):
        self.tolerance_ms = int(tolerance_ms)
        self.default_comment_unit = str(default_comment_unit or "SEC")

    def validate(self, file_path: str) -> List[str]:
        """Validate a single TXT file path."""
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as fh:
            content = fh.read()
        return self.validate_content(content)

    def validate_many(self, file_paths: List[str]) -> Dict[str, List[str]]:
        """Validate multiple TXT files; return dict[file_name] -> errors list."""
        results: Dict[str, List[str]] = {}
        for path in (file_paths or []):
            name = os.path.basename(path)
            try:
                results[name] = self.validate(path)
            except Exception as ex:
                results[name] = [f"Open/parse error: {ex}"]
        return results

    def validate_content(self, content: str) -> List[str]:
        """
        Validate TIMER vs LOGIC using ms-normalized, uppercase names and tolerance.
        """
        bits = _parse_timer_bits_section(content)
        errors: List[str] = []

        # If no TIMER BITS section found -> keep previous behavior (no errors)
        if not bits:
            return errors

        to_bits_upper, stp_str_ms = _extract_logic_timers(
            content, default_comment_unit=self.default_comment_unit
        )

        # A) TO present but no STP/STR comment (for bits that exist in TIMER BITS)
        for name in sorted(to_bits_upper):
            if name in bits and name not in stp_str_ms:
                # Try to echo a portion of the timer definition in error for context
                t_start, t_end = _find_timer_bits_window(content)
                timer_block = "\n".join(content.splitlines()[t_start:t_end]) if t_start is not None else ""
                pattern = rf'(?mi)^\s*(?:{re.escape(name)}|[^:]*\b{re.escape(name)}\b[^:]*)\s*:\s*(SET\s*=\s*[^;]+)\s*;'
                m = re.search(pattern, timer_block)
                if m:
                    timer_def = m.group(1).strip()
                    errors.append(f"Timer bit '{name}' {timer_def} but missing STP/STR comment")
                else:
                    errors.append(f"Timer bit '{name}' Defined in Timer Bit Section but missing STP/STR comment")

        # B) STP/STR present in logic but bit missing from TIMER BITS
        for name in sorted(stp_str_ms.keys()):
            if name not in bits:
                errors.append(f"Bit '{name}' has STP/STR operations but missing from TIMER BITS section")

        # C) Value mismatches in ms (STP vs SET, STR vs CLEAR)
        tol = self.tolerance_ms
        for name, info in stp_str_ms.items():
            binfo = bits.get(name)
            if not binfo:
                continue

            if info.get("STP_ms") is not None:
                set_ms = binfo.get("set_ms", 0)
                if not _equal_ms(info["STP_ms"], set_ms, tol):
   
                    errors.append(
                        f"Timer bit '{name}' SET={_fmt_sec(set_ms)} but STP comment says {_fmt_sec(info['STP_ms'])}"
                    )

            if info.get("STR_ms") is not None:
                clear_ms = binfo.get("clear_ms", 0)
                if not _equal_ms(info["STR_ms"], clear_ms, tol):
                    
                    errors.append(
                        f"Timer bit '{name}' CLEAR={_fmt_sec(clear_ms)} but STR comment says {_fmt_sec(info['STR_ms'])}"
                    )


        return errors

    def validate_standard_timers(self, content: str, standard_timers: List[dict], bit_filter: str = None) -> List[dict]:
        """
        Check specific bits against a standard set of STP/STR values.
        Supports multiple matches for a single standard bit (e.g. multiple .ENABLE bits).
        If bit_filter is provided, it will also include bits found in the file that are NOT in standards.
        """
        bits_in_timer_section = _parse_timer_bits_section(content)
        to_bits_upper, stp_str_ms = _extract_logic_timers(
            content, default_comment_unit=self.default_comment_unit
        )

        all_file_bits = to_bits_upper | set(bits_in_timer_section.keys())
        results = []
        processed_file_bits = set()

        for std in standard_timers:
            name = std['bit'].upper()
            expected_stp_ms = to_milliseconds(str(std.get('stp', 0)), "SEC")
            expected_str_ms = to_milliseconds(str(std.get('str', 0)), "SEC")

            matched_keys = []
            for k in all_file_bits:
                if k == name or k.endswith("." + name) or k.endswith(" " + name):
                    matched_keys.append(k)
                elif k.endswith(name):
                    prefix = k[:-len(name)]
                    if not prefix or not prefix[-1].isalpha():
                        matched_keys.append(k)
            
            if not matched_keys:
                # If filtering, only show this 'NA' if the standard name matches the filter
                if bit_filter and not name.endswith(bit_filter.upper()):
                    continue

                results.append({
                    "bit": name,
                    "std_stp": _fmt_sec(expected_stp_ms),
                    "std_str": _fmt_sec(expected_str_ms),
                    "logic_stp": "NA",
                    "logic_str": "NA",
                    "timer_set": "NA",
                    "timer_clear": "NA",
                    "status": "NA",
                    "details": "Bit not used in Logic section"
                })
                continue

            for m_key in sorted(matched_keys):
                # If filtering, skip bits that don't match the keyword suffix
                if bit_filter and not m_key.upper().endswith(bit_filter.upper()):
                    continue

                processed_file_bits.add(m_key)
                logic_info = stp_str_ms.get(m_key)
                timer_info = bits_in_timer_section.get(m_key)

                actual_stp_ms = logic_info.get("STP_ms") if logic_info else None
                actual_str_ms = logic_info.get("STR_ms") if logic_info else None
                actual_set_ms = timer_info.get("set_ms") if timer_info else None
                actual_clear_ms = timer_info.get("clear_ms") if timer_info else None

                status = "OK"
                details = []
                
                if m_key not in to_bits_upper:
                    status = "NA"
                    details.append("Bit not used in Logic section")
                else:
                    if actual_stp_ms is None and actual_str_ms is None:
                        status = "Mismatch"
                        details.append("Missing STP/STR comments in Logic")
                    else:
                        if actual_stp_ms is not None:
                            if not _equal_ms(expected_stp_ms, actual_stp_ms, self.tolerance_ms):
                                status = "Mismatch"
                                details.append(f"Logic STP: {_fmt_sec(actual_stp_ms)} (Exp: {_fmt_sec(expected_stp_ms)})")
                        elif expected_stp_ms > 0:
                            status = "Mismatch"
                            details.append(f"Logic STP: Missing (Exp: {_fmt_sec(expected_stp_ms)})")

                        if actual_str_ms is not None:
                            if not _equal_ms(expected_str_ms, actual_str_ms, self.tolerance_ms):
                                status = "Mismatch"
                                details.append(f"Logic STR: {_fmt_sec(actual_str_ms)} (Exp: {_fmt_sec(expected_str_ms)})")
                        elif expected_str_ms > 0:
                            status = "Mismatch"
                            details.append(f"Logic STR: Missing (Exp: {_fmt_sec(expected_str_ms)})")

                    if timer_info is None:
                        status = "Mismatch"
                        details.append("Found in Logic but missing from TIMER BITS section")
                    else:
                        if not _equal_ms(expected_stp_ms, actual_set_ms, self.tolerance_ms):
                            status = "Mismatch"
                            details.append(f"Timer SET: {_fmt_sec(actual_set_ms)} (Exp: {_fmt_sec(expected_stp_ms)})")
                        if not _equal_ms(expected_str_ms, actual_clear_ms, self.tolerance_ms):
                            status = "Mismatch"
                            details.append(f"Timer CLEAR: {_fmt_sec(actual_clear_ms)} (Exp: {_fmt_sec(expected_str_ms)})")

                results.append({
                    "bit": m_key,
                    "std_stp": _fmt_sec(expected_stp_ms),
                    "std_str": _fmt_sec(expected_str_ms),
                    "logic_stp": _fmt_sec(actual_stp_ms) if actual_stp_ms is not None else (_fmt_sec(expected_stp_ms) if (m_key in to_bits_upper and expected_stp_ms == 0) else "Missing"),
                    "logic_str": _fmt_sec(actual_str_ms) if actual_str_ms is not None else (_fmt_sec(expected_str_ms) if (m_key in to_bits_upper and expected_str_ms == 0) else "Missing"),
                    "timer_set": _fmt_sec(actual_set_ms) if actual_set_ms is not None else (_fmt_sec(expected_stp_ms) if (m_key in to_bits_upper and expected_stp_ms == 0) else "Missing"),
                    "timer_clear": _fmt_sec(actual_clear_ms) if actual_clear_ms is not None else (_fmt_sec(expected_str_ms) if (m_key in to_bits_upper and expected_str_ms == 0) else "Missing"),
                    "status": status,
                    "details": ", ".join(details)
                })

        # --- Handle bits that exist in the file but NOT in the standard list ---
        if bit_filter:
            upper_filter = bit_filter.upper()
            remaining_bits = [b for b in all_file_bits if b not in processed_file_bits and b.upper().endswith(upper_filter)]
            for r_bit in sorted(remaining_bits):
                logic_info = stp_str_ms.get(r_bit)
                timer_info = bits_in_timer_section.get(r_bit)
                
                a_stp = logic_info.get("STP_ms") if logic_info else None
                a_str = logic_info.get("STR_ms") if logic_info else None
                a_set = timer_info.get("set_ms") if timer_info else None
                a_clr = timer_info.get("clear_ms") if timer_info else None

                results.append({
                    "bit": r_bit,
                    "std_stp": "Undefined",
                    "std_str": "Undefined",
                    "logic_stp": _fmt_sec(a_stp) if a_stp is not None else "—",
                    "logic_str": _fmt_sec(a_str) if a_str is not None else "—",
                    "timer_set": _fmt_sec(a_set) if a_set is not None else "—",
                    "timer_clear": _fmt_sec(a_clr) if a_clr is not None else "—",
                    "status": "Undefined",
                    "details": f"Bit '{r_bit}' is in file but not defined in standard zone list"
                })

        return results


def fmt_sec(ms: int) -> str:
    """Format milliseconds as seconds:
       - whole seconds: '10s'
       - fractional (up to 3 decimals): '1.25s' (trim trailing zeros)
    """
    if ms is None:
        return "—"
    if ms % 1000 == 0:
        return f"{ms // 1000}s"
    s = f"{ms / 1000:.3f}".rstrip('0').rstrip('.')
    return f"{s}s"


def build_timer_vs_comment_matrix(content: str, default_comment_unit: str = "SEC") -> List[dict]:
    """
    Core-level matrix for report rendering.
    Returns a list of rows with: bit_name, timer_set, timer_clear, comment_stp, comment_str

    - Row keys are the UNION of:
        * names in TIMER BITS,
        * names with STP/STR in LOGIC,
        * TO-target names in LOGIC (even if comments missing).
    - Values are human-formatted seconds (using fmt_sec) or '—' if not present.
    """
    bits = _parse_timer_bits_section(content)
    to_bits, stp_str = _extract_logic_timers(content, default_comment_unit=default_comment_unit)

    # Build union of row keys
    row_keys = set(bits.keys()) | set(stp_str.keys()) | set(to_bits)

    rows: List[dict] = []
    for name in sorted(row_keys):
        b = bits.get(name)
        c = stp_str.get(name)

        timer_set  = fmt_sec(b["set_ms"])   if b else "—"
        timer_clear= fmt_sec(b["clear_ms"]) if b else "—"

        comment_stp = fmt_sec(c["STP_ms"]) if c and c.get("STP_ms") is not None else "—"
        comment_str = fmt_sec(c["STR_ms"]) if c and c.get("STR_ms") is not None else "—"

        rows.append({
            "bit_name": name,
            "timer_set": timer_set,
            "timer_clear": timer_clear,
            "comment_stp": comment_stp,
            "comment_str": comment_str,
        })
    return rows
