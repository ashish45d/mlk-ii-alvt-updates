# -*- coding: utf-8 -*-
"""
CommLogicValidator module for MLK-II ALVT
Validates Enable, Disable, and COMOK logic for communication stations.
"""

import re
import os

class CommLogicValidator:
    """Validates Enable, Disable, and COMOK logic for communication stations."""

    def __init__(self, parent_app=None):
        self.app = parent_app

    def validate(self, file_path):
        """Main entry point for validation."""
        if not os.path.exists(file_path):
            return [f"File not found: {file_path}"]

        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        errors = []
        
        # 1. Extract Stations and identify those with functional I/O
        stations = self._extract_stations(content)
        
        # Determine functional stations to validate:
        # 1. Exclude Datalogger (DL_) and VDU (OPC_) stations.
        # 2. Specifically exclude links with ACK.TIMEOUT 1000 and NO I/O (per user request).
        functional_stations = [
            s for s in stations 
            if not ('DL_' in s['name'].upper() or 'TCAS_' in s['name'].upper() or any(prefix in s['name'].upper() for prefix in ['OPC_', 'OP_', 'OPVDU_']))
            and not (s['ack_timeout'] == 1000 and not s['has_io'])
        ]
        
        if not functional_stations:
            return errors # Nothing to check

        # 2. Extract Logic and Timers
        assignments = self._extract_assignments(content)
        timers = self._extract_timers(content)
        
        # 3. Perform Checks
        errors.extend(self._check_enable_cascade(stations, functional_stations, assignments, timers))
        errors.extend(self._check_disable_logic(functional_stations, assignments))
        errors.extend(self._check_comok_logic(stations, assignments))
        
        return errors

    def _extract_stations(self, content):
        """Extracts station information including name, link, timeout, and I/O status."""
        stations = []
        
        # Match MII.ADDRESS or MII.NV.ADDRESS blocks
        block_pattern = re.compile(
            r'ADJUSTABLE\s+MII\.(?:ADDRESS|NV\.ADDRESS)\s*[:：]\s*(?P<addr>\d+)(?P<body>.*?)(?=ADJUSTABLE\s+MII\.(?:ADDRESS|NV\.ADDRESS)\s*[:：]|BOOLEAN\s+BITS|\Z)',
            re.DOTALL | re.IGNORECASE
        )
        
        # Detect LINK blocks to understand which stations belong to which LINK
        link_blocks = []
        link_pattern = re.compile(r'LINK\s*[:：]\s*(?P<link_name>\w+)(?P<body>.*?)(?=LINK\s*[:：]|\Z)', re.DOTALL | re.IGNORECASE)
        for m in link_pattern.finditer(content):
            link_blocks.append((m.group('link_name'), m.start(), m.end()))

        for m_block in block_pattern.finditer(content):
            addr = m_block.group('addr')
            body = m_block.group('body')
            start_pos = m_block.start()
            
            # Find which LINK this block belongs to
            current_link = "UNKNOWN"
            for link_name, l_start, l_end in link_blocks:
                if l_start <= start_pos < l_end:
                    current_link = link_name
                    break
            
            name_m = re.search(r'STATION\.NAME\s*[:：]\s*"?([\w\-_.]+)"?', body, re.IGNORECASE)
            name = name_m.group(1).strip() if name_m else "UNKNOWN"
            
            timeout_m = re.search(r'ACK\.TIMEOUT\s*[:：]\s*(\d+)', body, re.IGNORECASE)
            ack_timeout = int(timeout_m.group(1)) if timeout_m else 0
            
            # Simple I/O count check
            has_io = False
            out_m = re.search(r'OUTPUT\s*:(.*?);', body, re.DOTALL | re.IGNORECASE)
            in_m = re.search(r'INPUT\s*:(.*?);', body, re.DOTALL | re.IGNORECASE)
            
            def is_functional(io_text):
                if not io_text: return False
                bits = [b.strip() for b in io_text.split(',') if b.strip() and not b.strip().startswith('//')]
                return any(b.upper() != 'SPARE' for b in bits)

            if out_m and is_functional(out_m.group(1)):
                has_io = True
            if in_m and is_functional(in_m.group(1)):
                has_io = True
                
            stations.append({
                'name': name,
                'link': current_link,
                'ack_timeout': ack_timeout,
                'has_io': has_io,
                'enable_bit': f"{current_link}.{name}.ENABLE",
                'disable_bit': f"{current_link}.{name}.DISABLE",
                'status_bit': f"{current_link}.{name}.STATUS"
            })
            
        return stations

    def _extract_assignments(self, content):
        """Extracts all ASSIGN and NV.ASSIGN statements."""
        assignments = []
        # Use re.DOTALL to handle multi-line assignments split by newlines
        # Updated regex to capture multiple destinations separated by commas
        assign_pattern = re.compile(r'(?:NV\.)?ASSIGN\s+(?P<src>.*?)\s+TO\s+(?P<dst>[^;]+);', re.IGNORECASE | re.DOTALL)
        for m in assign_pattern.finditer(content):
            # Clean up source: replace newlines/multiple spaces with single space
            src = re.sub(r'\s+', ' ', m.group('src').strip())
            dst_raw = m.group('dst')
            # Handle multiple destinations separated by commas
            for d in dst_raw.split(','):
                bit = d.strip().upper()
                if bit:
                    assignments.append({'src': src, 'dst': bit})
        return assignments

    def _extract_timers(self, content):
        """Extracts timer definitions from the TIMER BITS section."""
        timers = {}
        # Find start of BOOLEAN BITS section to narrow down the search window
        bool_start = re.search(r'(?mi)^\s*(?:\d+\s+)?BOOLEAN\s+BITS\s*$', content)
        search_start = bool_start.end() if bool_start else 0

        start_match = re.search(r'(?mi)^\s*(?:\d+\s+)?TIMER\s+BITS\s*$', content[search_start:])
        if not start_match: return timers
        start = search_start + start_match.end()
        
        end = content.find("LOG BITS", start)
        if end == -1: end = len(content)
        
        timer_section = content[start:end]
        # Pattern: BitName: SET=val:UNIT CLEAR=val:UNIT;
        timer_pattern = re.compile(r'(?P<bits>[\w\-_.]+(?:\s*,\s*[\w\-_.]+)*)\s*:\s*SET\s*=\s*(?P<set_val>\d+)\s*:\s*(?P<set_unit>\w+)\s+CLEAR\s*=\s*(?P<clr_val>\d+)\s*:\s*(?P<clr_unit>\w+)', re.IGNORECASE)
        
        for m in timer_pattern.finditer(timer_section):
            bits = [b.strip().upper() for b in m.group('bits').split(',')]
            set_val = int(m.group('set_val'))
            set_unit = m.group('set_unit').upper()
            
            # Convert to seconds for comparison
            set_sec = set_val if set_unit == 'SEC' else set_val / 1000.0
            
            for bit in bits:
                timers[bit] = set_sec
                
        return timers

    def _check_enable_cascade(self, all_stations, functional_stations, assignments, timers):
        """Verifies the 2-second staggered cascade of ENABLE bits by following the assignment chain."""
        errors = []
        
        # 1. Build a map of target_enable -> source
        # We need this to identify which stations are part of the chain and if their source is correct.
        target_to_src = {}
        for a in assignments:
            dst = a['dst'].upper()
            if dst.endswith('.ENABLE'):
                target_to_src[dst] = a['src'].upper()
        
        # 2. Build a map of source -> target_enable to follow the chain forward
        src_to_target = {}
        for dst, src in target_to_src.items():
            if src not in src_to_target:
                src_to_target[src] = []
            src_to_target[src].append(dst)

        # 3. Follow the chain starting from CPS.STATUS
        # The cascade is expected to be a single chain.
        visited_bits = set()
        current_source = "CPS.STATUS"
        
        # We look for ANY CPS.STATUS (sometimes it has a suffix like CPS.STATUS.C2C3)
        # However, the user's logic usually just says CPS.STATUS if it's internal.
        # Let's find any source that contains "CPS.STATUS"
        potential_starts = [s for s in src_to_target.keys() if "CPS.STATUS" in s]
        if potential_starts:
            current_source = potential_starts[0]
        else:
            # Fallback to literal check if no partial match
            pass

        # Follow the chain
        chain = []
        while current_source in src_to_target:
            targets = src_to_target[current_source]
            if len(targets) > 1:
                # Ambiguous cascade? Multiple bits assigned from same source.
                # In MLK-II, it should usually be a single chain. 
                # We'll just take the first one that is a station's ENABLE bit.
                target = targets[0]
            else:
                target = targets[0]
            
            if target in visited_bits:
                # Circular dependency?
                break
            
            visited_bits.add(target)
            chain.append((current_source, target))
            
            # Check timer for this target
            if target in timers:
                if timers[target] != 2.0:
                    errors.append(f"Timer mismatch for '{target}': Expected 2.0s delay, found {timers[target]}s")
            else:
                # Report missing timer if it's part of the cascade
                errors.append(f"Timer definition missing for '{target}' in TIMER BITS section.")
            
            current_source = target

        # 4. Verify all functional stations are in the visited_bits
        functional_enable_bits = {s['enable_bit'].upper() for s in functional_stations}
        missing_from_cascade = functional_enable_bits - visited_bits
        for bit in sorted(missing_from_cascade):
            # Find station name for the error message
            st_name = next((s['name'] for s in functional_stations if s['enable_bit'].upper() == bit), "Unknown")
            errors.append(f"Enable logic missing or broken for station '{st_name}': '{bit}' is not part of the CPS.STATUS cascade chain.")

        # 5. Check if there are any .ENABLE bits assigned from a WRONG source (not in the chain we followed)
        # This catches "Incorrect logic" errors where a station is assigned from something else.
        # But wait, if we followed the chain, we only saw the "correct" ones.
        # Let's check all station assignments that are NOT in our visited path.
        all_bits_with_assign = set(target_to_src.keys())
        for bit in all_bits_with_assign:
            if bit not in visited_bits:
                # This bit has an assignment, but it's not connected to the CPS.STATUS chain.
                src = target_to_src[bit]
                # If it's a functional station, it's definitely an error.
                # If it's not functional, it might be a dangling logic bit.
                st_match = next((s for s in all_stations if s['enable_bit'].upper() == bit), None)
                if st_match:
                    errors.append(f"Incorrect logic for '{bit}': Assigned from '{src}', but this is not connected to the main CPS.STATUS cascade.")

        return errors

    def _check_disable_logic(self, stations, assignments):
        """Verifies ~ENABLE TO DISABLE logic."""
        errors = []
        for station in stations:
            enable = station['enable_bit'].upper()
            disable = station['disable_bit'].upper()
            
            found = False
            for assign in assignments:
                if assign['dst'] == disable:
                    expected = f"~{enable}"
                    if assign['src'].replace(" ", "") == expected:
                        found = True
                        break
                    else:
                        errors.append(f"Incorrect disable logic for '{disable}': Expected '{expected}', found '{assign['src']}'")
                        found = True
                        break
            if not found:
                errors.append(f"Disable logic missing for station '{station['name']}': '{disable}' not assigned.")
        return errors

    def _check_comok_logic(self, all_stations, assignments):
        """Verifies COMOK/Link OK logic by ensuring all functional status bits are included in appropriate assignments."""
        errors = []
        
        # 1. Filter out non-functional stations (DL_, OPC_, or 1000ms & No I/O)
        functional_stations = [
            s for s in all_stations 
            if not ('DL_' in s['name'].upper() or 'TCAS_' in s['name'].upper() or any(prefix in s['name'].upper() for prefix in ['OPC_', 'OP_', 'OPVDU_']))
            and not (s['ack_timeout'] == 1000 and not s['has_io'])
        ]
        
        if not functional_stations:
            return errors

        # 2. Find all COMOK/Link OK assignments and status bits used in them
        # Keywords: COMOK, VSL (Vital Serial Link), _OK
        keywords = ["COMOK", "VSL", "_OK"]
        comok_assigns = [
            a for a in assignments 
            if any(k in a['dst'].upper() for k in keywords) 
            and "COMOK" not in a['src'].upper() # Skip redirections
        ]
        
        # Map of COMOK bit name to set of status bits found in its source
        comok_contents = {}
        for a in comok_assigns:
            dst = a['dst'].upper()
            src = a['src'].upper()
            # Extract status bits using regex
            # Use a more robust pattern that handles underscores and dots correctly
            status_bits_in_src = set(re.findall(r'([A-Z0-9][A-Z0-9_.\-]*\.STATUS)', src, re.IGNORECASE))
            if dst not in comok_contents:
                comok_contents[dst] = set()
            comok_contents[dst].update(status_bits_in_src)

        # 3. Verify every functional status bit is in at least one COMOK/Link OK bit
        all_referenced_statuses = set().union(*comok_contents.values()) if comok_contents else set()
        for s in functional_stations:
            s_bit = s['status_bit'].upper()
            if s_bit not in all_referenced_statuses:
                # Add station name and link for better debugging
                errors.append(f"Station status '{s_bit}' is not included in any COMOK/Link OK logic.")

        # 4. For each COMOK/Link OK assignment, verify it contains all functional stations 
        # that belong to the same group based on the name.
        for comok_name, bits_in_comok in comok_contents.items():
            link_name = comok_name.split('.')[0]
            expected_bits = set()
            
            # Heuristic: If ANY station bit is already in this COMOK, 
            # find all other functional stations that share the same prefix.
            represented_prefixes = set()
            for bit in bits_in_comok:
                parts = bit.split('.')
                if len(parts) >= 2:
                    st_name = parts[1]
                    prefix = st_name.split('_')[0]
                    represented_prefixes.add(prefix)
            
            for s in functional_stations:
                if s['link'].upper() == link_name:
                    st_parts = s['name'].split('_')
                    st_prefix = st_parts[0] if st_parts else ""
                    
                    # Match if prefix is already represented
                    if st_prefix in represented_prefixes:
                        expected_bits.add(s['status_bit'].upper())
                        continue
                    
                    # Match if station prefix is a substring of the COMOK bit name (e.g. C1OPC in NVSLC1OPC_COMOK)
                    # We strip common prefixes like NV, SL, VSL from the comok name for better matching
                    clean_comok = re.sub(r'^(?:NV|SL|VSL|V)+', '', comok_name.split('.')[-1])
                    if st_prefix and re.search(rf'\b{re.escape(st_prefix)}\b', clean_comok):
                        expected_bits.add(s['status_bit'].upper())

            missing = expected_bits - bits_in_comok
            if missing:
                errors.append(f"Link status logic for '{comok_name}' is incomplete. Missing: {', '.join(sorted(missing))}")

        return errors

def generate_comm_logic_report(txt_files, output_dir, validator, version="?.?", designer_name="", authorized_user="Unregistered User"):
    """
    Generate a PDF report for Communication Logic validation across multiple TXT files.
    """
    import os
    from core.reporting import generate_error_pdf
    
    pdf_path = os.path.join(output_dir, "Comm_Logic_Validation_Report.pdf")
    
    errors_by_file = {}
    for file_path in txt_files:
        file_name = os.path.basename(file_path)
        errors = validator.validate(file_path)
        errors_by_file[file_name] = errors if errors else []

    return generate_error_pdf(errors_by_file, pdf_path, report_type="Communication Logic", version=version, designer_name=designer_name, authorized_user=authorized_user)

