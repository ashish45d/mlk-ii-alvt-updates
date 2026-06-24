# -*- coding: utf-8 -*-
import os
import sys
import ezdxf
import re
import textwrap
import math
from ezdxf.enums import TextEntityAlignment
# Matplotlib based PDF rendering removed (Slow legacy method)
# Use fast_dxf_to_pdf.py for high-speed vectorized PDF generation

# Add parent directory to path to import core
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.logic_parser_engine import LogicParserEngine, LogicNode
from core.layout_config import load_layout_config


def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller."""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    # scripts is in Toolhub, so parent is Toolhub
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base_dir, relative_path)
    return p

class DXFLadderRenderer:
    """Renderer to convert LogicNode trees into DXF ladder circuits"""
    
    def __init__(self, doc, font_name="ARIAL", font_size=1.5, is_bold=False, timer_map=None, layout_config=None):
        self.doc = doc
        self.msp = doc.modelspace()
        
        self.font_name = font_name
        self.font_size = font_size
        self.is_bold = is_bold
        
        # Coil tracking for Index
        self.coils_in_sheet = []
        self.timer_map = timer_map or {}
        self.layout_config = layout_config or {}
        
        # Ensure text style exists
        if font_name not in doc.styles:
            doc.styles.new(font_name, dxfattribs={'font': font_name})
        
        # Style/Config (Ultra-micro)
        self.contact_w = 12.0 
        self.contact_h = 4.0
        self.h_spacing = 2.0
        self.v_spacing = 25.0
        self.rail_color = 1 # Red in AutoCAD
        self.text_color = 7 # White/Black in AutoCAD
        
        # Layer setup
        if 'LOGIC' not in doc.layers:
            doc.layers.add('LOGIC', color=7)  # Black/White
        if 'TEXT' not in doc.layers:
            doc.layers.add('TEXT', color=7)
            
        # Buffering for Post-Process Wrap
        self.draw_buffer = []
        self.h_counter = 1
        self.x_start = 20
        self.x_end = 320
        self.x_limit = 280 # Reduced to allow H-circles at wrap

    def _add_line(self, p1, p2, layer='LOGIC', bi=0):
        self.draw_buffer.append({'type': 'line', 'x1': p1[0], 'y1': p1[1], 'x2': p2[0], 'y2': p2[1], 'layer': layer, 'bi': bi})

    def _add_circle(self, center, radius, layer='LOGIC'):
        self.draw_buffer.append({'type': 'circle', 'x': center[0], 'y': center[1], 'radius': radius, 'layer': layer})

    def _add_text(self, text, x, y, height=None, align=TextEntityAlignment.LEFT, layer='TEXT', prevent_wrap=False):
        h = height if height is not None else self.font_size
        self.draw_buffer.append({
            'type': 'text', 
            'text': text, 
            'x': x, 
            'y': y, 
            'height': h, 
            'align': align, 
            'layer': layer,
            'style': self.font_name
        })

    def _add_dot(self, x, y, layer='LOGIC'):
        """Draw a small filled dot for terminals"""
        self.draw_buffer.append({'type': 'dot', 'x': x, 'y': y, 'layer': layer})

    def _add_contact(self, x, y, label, is_nc=False):
        """Buffer a contact symbol as an atomic type"""
        self.draw_buffer.append({
            'type': 'contact',
            'x': x,
            'y': y,
            'label': label,
            'is_nc': is_nc,
            'layer': 'LOGIC'
        })

    def _draw_continuation_circle(self, x, y, label, is_end=True):
        """Buffer a continuation circle"""
        radius = 2.5
        cx = x # Center passed exactly
        self._add_circle((cx, y), radius=radius)
        self._add_text(str(label), cx, y, align=TextEntityAlignment.MIDDLE_CENTER)
        dot_x = cx - radius if is_end else cx + radius
        self._add_dot(dot_x, y)

    def _add_coil(self, x, y, name, comment=""):
        """Buffer a coil symbol as an atomic type"""
        self.draw_buffer.append({
            'type': 'coil',
            'x': x,
            'y': y,
            'name': name,
            'comment': comment,
            'layer': 'LOGIC'
        })
        self.coils_in_sheet.append(name)

    def _measure_node(self, node):
        """Flat measurement (no wrap awareness)"""
        if not node: return
        
        # Scale vertical margins based on font size for label clearance
        # Contact body is roughly 4.0 tall, labels need at least 1.5*fs of breathing room
        v_margin = max(2.5, 1.8 * self.font_size)
        
        if node.type in ['VAR', 'NOT']:
            label = node.children[0].value if node.type == 'NOT' else node.value
            text_len = len(str(label))
            
            effective_fs = self.font_size
            if text_len > 20: effective_fs = self.font_size * 0.7
            elif text_len > 14: effective_fs = self.font_size * 0.8
            
            text_w = text_len * effective_fs * 0.85
            node.w = max(self.contact_w, text_w + 5.0)
            
            # Use dynamic margins to prevent label overlap in tall parallel structures
            node.h_above = v_margin
            node.h_below = v_margin
            node.h = node.h_above + node.h_below
        elif node.type == 'AND':
            node.w = 0
            node.h_above, node.h_below = 0, 0
            for i, child in enumerate(node.children):
                self._measure_node(child)
                node.w += child.w
                node.h_above = max(node.h_above, child.h_above)
                node.h_below = max(node.h_below, child.h_below)
            node.h = node.h_above + node.h_below
        elif node.type == 'OR':
            node.w = 0
            # Space between parallel branches
            branch_gap = 2.5 * self.font_size
            for i, child in enumerate(node.children):
                self._measure_node(child)
                node.w = max(node.w, child.w)
                if i == 0: 
                    node.h_above, node.h_below = child.h_above, child.h_below
                else: 
                    # Sum height of branches including the dynamic gap
                    node.h_below += branch_gap + child.h
            node.h = node.h_above + node.h_below
            node.w += 10.0

    def render_rung(self, output_name, root, x_start, y_wire, x_end, comment="", raw_stmt="", use_wrap=True, h_start=1, reset_h=True, **kwargs):
        """Layout flat then slice and shift"""
        self.draw_buffer = []
        if reset_h:
            self.h_counter = h_start
        else:
            # If not resetting, we assume self.h_counter is already set correctly
            pass
        self.x_start = x_start
        self.x_end = x_end
        
        # 1. Pre-calculate header footprint for the FIRST sheet
        h_header_needed = 0
        if raw_stmt:
            clean_stmt = raw_stmt.replace('\n', ' ').strip()
            # Calculate dynamic Wrap Width based on Units and Font Size
            # Use almost full width (0.98) and a realistic Arial character factor (0.82)
            avail_w = (x_end - x_start) * 0.98
            char_w = self.font_size * 0.82
            wrap_char_len = max(20, int(avail_w / char_w))
            
            lines = textwrap.wrap(clean_stmt, width=wrap_char_len, break_long_words=True, break_on_hyphens=True)
            num_draw_lines = len(lines)
            line_spacing = self.font_size * 1.8  # Tighter line spacing for headers
            
            # Dynamic header offset (7.0 for 1.5 font, scales to 9.5 for 2.0 font)
            header_offset = max(7.0, (5.0 * self.font_size - 0.5)) 
            h_header_needed = header_offset + (num_draw_lines - 1) * line_spacing
            
            base_y = y_wire + header_offset + (num_draw_lines - 1) * line_spacing

            for idx, line in enumerate(lines[:num_draw_lines]):
                self._add_text(line, x_start, base_y - (idx * line_spacing), height=self.font_size * 1.0, prevent_wrap=True)
        
        if comment and not comment.strip().startswith('//'):
            self._add_text(f"({comment})", x_end - 40, y_wire + 15.0, height=self.font_size, align=TextEntityAlignment.RIGHT, prevent_wrap=True)

        # 1. Flat Layout
        self._measure_node(root)
        self.x_curr = x_start + 20
        self._draw_node_flat(root, x_start + 10, y_wire, x_start + 10 + root.w)
        
        # Logic finish point
        merger_x = self.x_curr
        
        # 2. Add fixed-distance coil to flat buffer
        coil_x = merger_x + 15
        self._add_line((merger_x, y_wire), (coil_x, y_wire))
        self._add_coil(coil_x, y_wire, output_name, comment)
        # self._add_line((coil_x + 10, y_wire), (coil_x + 30, y_wire)) # REMOVED extra stub
        
        # 3. Post-Process Wrap
        return self._finalize_and_render(x_start, y_wire, x_end, use_wrap=use_wrap, **kwargs)

    def measure_header(self, raw_stmt, x_start, x_term, is_small=True, prefix=''):
        if not raw_stmt: return 0
        clean_stmt = raw_stmt.replace('\n', ' ').strip()
        if prefix: clean_stmt = f"{prefix} {clean_stmt}"
        WRAP_LEN = 150 if (x_term - x_start) > 150 else 75
        if is_small: WRAP_LEN = 75
        lines = textwrap.wrap(clean_stmt, width=WRAP_LEN, break_long_words=True, break_on_hyphens=True)
        return len(lines)

    def measure_wrapped_height(self, root, x_start, x_end):
        old_buf = self.draw_buffer
        old_xc  = getattr(self, 'x_curr', None)
        self.draw_buffer = []
        try:
            self._measure_node(root)
            self.x_curr = x_start + 20
            self._draw_node_flat(root, x_start+10, 0, x_start+10+root.w)
            wrap_w = max(50.0, (x_end-35) - (x_start+10))
            drops = {}
            for p in self.draw_buffer:
                px = p.get('x', min(p.get('x1',0), p.get('x2',0)))
                py = p.get('y', min(p.get('y1',0), p.get('y2',0)))
                rx = px - (x_start+10)
                r  = int(max(0, rx-0.1)/wrap_w) if rx > 0 else 0
                drops[r] = max(drops.get(r, 0), -py)
            mr = max(drops.keys()) if drops else 0
            # Reduced from 8.0 to 4.0 to provide a much tighter estimation (Fixes blank space)
            return sum(max(18.0, drops.get(r, 0)+4.0) for r in range(mr+1))
        finally:
            self.draw_buffer = old_buf
            if old_xc is not None: self.x_curr = old_xc
            elif hasattr(self, 'x_curr'): del self.x_curr

    def _draw_node_flat(self, node, x1, y_wire, x2):
        """Simple horizontal drawing to buffer"""
        node_end_x = x1 + node.w
        
        if node.type == 'AND':
            px = x1
            for child in node.children:
                self._draw_node_flat(child, px, y_wire, px + child.w)
                px += child.w
            self.x_curr = max(self.x_curr, px)
            
        elif node.type == 'OR':
            cx1 = x1 + 5.0
            right_bus = x2 - 5.0  # Use outer x2 so ALL branches share a common right vertical bus
            self._add_line((x1, y_wire), (cx1, y_wire))
            
            # Draw main (first) branch to the right bus
            self._draw_node_flat(node.children[0], cx1, y_wire, right_bus)
            
            # Draw parallel branches
            curr_y = y_wire # Current branch Y starts at main horizontal wire
            lowest_y = y_wire
            
            # Start parallel branches
            branch_gap = 2.5 * self.font_size
            for i, child in enumerate(node.children[1:], start=1):
                prev_y = curr_y 
                prev_child = node.children[i-1]
                # Dynamic vertical step based on measured node heights
                curr_y -= (branch_gap + prev_child.h_below + child.h_above)
                lowest_y = curr_y
                
                # Perfect Electrical Continuity: Segment spans exactly from PREVIOUS Y to CURRENT Y
                # Tagging distinct IDs (bi) for Left/Right (REQUIRED for unique V-labeling)
                self._add_line((cx1, prev_y), (cx1, curr_y), bi=2*i)
                self._add_line((right_bus, prev_y), (right_bus, curr_y), bi=2*i + 1)
                
                # Draw branch contents at the current Y
                self._draw_node_flat(child, cx1, curr_y, right_bus)
                
            self._add_line((right_bus, y_wire), (x2, y_wire))
            self.x_curr = max(self.x_curr, x2)
            return  # Skip fill-wire block at bottom - already filled to x2
            
        elif node.type in ['VAR', 'NOT']:
            mid_x = (x1 + node_end_x) / 2
            self._add_line((x1, y_wire), (mid_x, y_wire))
            self._add_line((mid_x, y_wire), (node_end_x, y_wire))
            self._add_contact(mid_x, y_wire, node.children[0].value if node.type == 'NOT' else node.value, node.type == 'NOT')
            self.x_curr = max(self.x_curr, node_end_x)
            
        # If the requested bounding box x2 is larger than the required node width node_end_x,
        # fill the remaining space with a straight blank continuation wire.
        if x2 > node_end_x:
            self._add_line((node_end_x, y_wire), (x2, y_wire))
            self.x_curr = max(self.x_curr, x2)

    def _finalize_and_render(self, x_start, y_wire, x_end, use_wrap=True, **kwargs):
        """Slice buffer and commit to DXF with rail continuity and vertical pagination tag awareness"""
        Y_TOP = self.layout_config.get('y_max', 215)
        Y_MIN = self.layout_config.get('y_min', 48)
        abs_p = kwargs.get('abs_page', 1)
        SLOT_H = Y_TOP - Y_MIN
        # Account for coil label extension which scales with font size
        # Reduced safety margin to avoid "too early" wrapping (User reported 45+35 was too aggressive)
        label_ext = max(30.0, 20.0 * self.font_size)
        wrap_x_limit = x_end - label_ext
        wrap_width = max(50.0, wrap_x_limit - (x_start + 10))
        
        def get_wrapped_x(ox, pw=False):
            if pw: return ox, 0
            rel_x = ox - (x_start + 10)
            if rel_x < 0: return ox, 0
            row = int(max(0, rel_x - 0.1) / wrap_width)
            return (rel_x - row * wrap_width) + (x_start + 20), row

        max_drops = {}
        for p in self.draw_buffer:
            if p.get('prevent_wrap', False): continue
            rx = (p['x'] if 'x' in p else min(p['x1'], p['x2'])) - (x_start + 10)
            ry = (p['y'] if 'y' in p else min(p['y1'], p['y2']))
            r = int(max(0, rx-0.1)/wrap_width) if rx > 0 else 0
            max_drops[r] = max(max_drops.get(r, 0), y_wire - ry)

        max_r = max(max_drops.keys()) if max_drops else 0
        row_offsets = [0.0] * (max_r + 2)
        for r in range(max_r + 1):
            row_offsets[r+1] = row_offsets[r] + max(18.0, max_drops.get(r, 0) + 8.0)



        paginated_buffer = {} 
        visited_cont = set() # (s_page, x, is_end, label_id)
        
        def add_to_s(s, p):
            if p.get('type') == 'continuation':
                # Strict deduplication for ALL continuations (H and V) to avoid "thick" text/shadowing
                # Round coordinates to avoid tiny float diffs causing duplicate drawing
                rx, ry = round(p.get('x',0), 2), round(p.get('y',0), 2)
                key = (s, p['type'], rx, ry, p.get('is_end', False))
                if key in visited_cont: 
                    return
                visited_cont.add(key)
            paginated_buffer.setdefault(s, []).append(p)
        # Use dynamic bounds for pagination (important for Y_MIN effectiveness)
        ly_max = self.layout_config.get('y_max', 215)
        ly_min = self.layout_config.get('y_min', 48)
        l_top = 210 # Visual top for overflow pages
        l_slot = l_top - ly_min # Height of logic area
        abs_p = kwargs.get('abs_page', 1)

        def map_y(y):
            # If within primary logic area, return page 0
            if y >= ly_min: return 0, y
            
            # Overflow to subsequent pages
            overflow = ly_min - y
            s = 1 + int(overflow / l_slot)
            return s, l_top - (overflow % l_slot)
            
        ay = y_wire; ax = x_start + 2
        s_arr, my_arr = map_y(ay)
        # Arrow Fix
        add_to_s(s_arr, {'type': 'line', 'x1': ax, 'y1': my_arr+1.5, 'x2': ax+3, 'y2': my_arr, 'layer': 'LOGIC'})
        add_to_s(s_arr, {'type': 'line', 'x1': ax, 'y1': my_arr-1.5, 'x2': ax+3, 'y2': my_arr, 'layer': 'LOGIC'})
        add_to_s(s_arr, {'type': 'line', 'x1': ax+3, 'y1': my_arr, 'x2': x_start+20, 'y2': my_arr, 'layer': 'LOGIC'})

        v_id_cache = {} # Map (nx, bi_val) -> sequential_int
        def get_v_id(nx, bi):
            key = (round(nx, 2), bi)
            if key not in v_id_cache:
                v_id_cache[key] = len(v_id_cache) + 1
            return v_id_cache[key]

        for p in self.draw_buffer:
            pw = p.get('prevent_wrap', False)
            if p['type'] == 'line':
                x1, y1, x2, y2 = p['x1'], p['y1'], p['x2'], p['y2']
                if abs(y1 - y2) < 0.1: # Horizontal
                    if x1 > x2: x1, x2 = x2, x1
                    if pw:
                        s, my = map_y(y1); add_to_s(s, {'type': 'line', 'x1': x1, 'y1': my, 'x2': x2, 'y2': my, 'layer': p['layer']})
                    else:
                        rel_x1, rel_x2 = max(0, x1-(x_start+10)), max(0, x2-(x_start+10))
                        r1, r2 = int(max(0, rel_x1-0.1)/wrap_width), int(max(0, rel_x2-0.1)/wrap_width)
                        for r in range(r1, r2 + 1):
                            cs, ce = max(rel_x1, r*wrap_width), min(rel_x2, (r+1)*wrap_width)
                            if cs <= ce:
                                nx1, nx2 = (cs-r*wrap_width)+(x_start+20), (ce-r*wrap_width)+(x_start+20)
                                ny = y1 - row_offsets[r]; s, my = map_y(ny)
                                add_to_s(s, {'type': 'line', 'x1': nx1, 'y1': my, 'x2': nx2, 'y2': my, 'layer': p['layer']})
                                if nx2 >= (x_start + 20 + wrap_width) - 0.5 and r < r2:
                                    lab = f"H{self.h_counter}"
                                    add_to_s(s, {'type': 'continuation', 'x': nx2+2.5, 'y': my, 'label': f" @@ {lab}", 'is_end': True, 'is_v': False, 'layer': p['layer']})
                                    
                                    # Next Row Incoming Circle (Left Side)
                                    ny_next = y1 - row_offsets[r+1]; sn, myn = map_y(ny_next)
                                    # Place at edge (12.5) instead of center (10.0) for the line connection
                                    add_to_s(sn, {'type': 'continuation', 'x': x_start + 10.0, 'y': myn, 'label': f" @@ {lab}", 'is_end': False, 'is_v': False, 'layer': p['layer']})
                                    add_to_s(sn, {'type': 'line', 'x1': x_start + 12.5, 'y1': myn, 'x2': x_start + 20.0, 'y2': myn, 'layer': p['layer']})
                                    self.h_counter += 1
                else: # Vertical
                    if y1 < y2: y1, y2 = y2, y1 
                    nx, r = get_wrapped_x(x1, pw)
                    ny1, ny2 = y1 - (0 if pw else row_offsets[r]), y2 - (0 if pw else row_offsets[r])
                    s1, my1 = map_y(ny1); s2, my2 = map_y(ny2)
                    for s in range(s1, s2 + 1):
                        sy1, sy2 = (my1 if s==s1 else Y_TOP), (my2 if s==s2 else Y_MIN)
                        # Connect at TIP (radius 2.5) instead of center for V_ continuations
                        if s > s1: sy1 -= 2.5 # Stop at top circle edge
                        if s < s2: sy2 += 2.5 # Stop at bottom circle edge
                        
                        bi_val = p.get('bi', 0)
                        if sy1 > sy2: add_to_s(s, {'type': 'line', 'x1': nx, 'y1': sy1, 'x2': nx, 'y2': sy2, 'layer': p['layer'], 'bi': bi_val})
                        if s < s2: 
                            v_id = get_v_id(nx, bi_val)
                            add_to_s(s, {'type': 'continuation', 'x': nx, 'y': Y_MIN, 'label': f"{abs_p + s + 1} @@ V{v_id}", 'is_end': True, 'is_v': True, 'layer': p['layer']})
                        if s > s1: 
                            v_id = get_v_id(nx, bi_val)
                            add_to_s(s, {'type': 'continuation', 'x': nx, 'y': Y_TOP, 'label': f"{abs_p + s - 1} @@ V{v_id}", 'is_end': False, 'is_v': True, 'layer': p['layer']})
            else: # Atomic
                nx, r = get_wrapped_x(p['x'], pw); ny = p['y'] - (0 if pw else row_offsets[r])
                s, my = map_y(ny); new_p = dict(p); new_p['x'], new_p['y'] = nx, my; add_to_s(s, new_p)
                if p['type'] == 'continuation': new_p['is_v'] = p.get('is_v', False) # Preserve vertical flag if already set
        # Calculate ABSOLUTE footprint for the FINAL SHEET only
        m_s = max(paginated_buffer.keys()) if paginated_buffer else 0
        final_min_y = None
        final_max_y = None
        
        # Initial scan to find range on final sheet
        page_cmds = paginated_buffer.get(m_s, [])
        v_ext_needed = False
        if page_cmds:
            for cmd in page_cmds:
                if 'y' in cmd:
                    margin = 0
                    if cmd['type'] in ['continuation','dot','circle']: 
                        margin = 2.5
                        if cmd.get('is_v', False): v_ext_needed = True
                    elif cmd['type'] in ['text','coil','contact']: margin = 4.0
                    
                    y_val = cmd['y']
                    if final_min_y is None or (y_val - margin) < final_min_y: final_min_y = y_val - margin
                    if final_max_y is None or (y_val + margin) > final_max_y: final_max_y = y_val + margin
                elif 'y1' in cmd:
                    y_v1, y_v2 = cmd['y1'], cmd['y2']
                    if final_min_y is None or min(y_v1, y_v2) < final_min_y: final_min_y = min(y_v1, y_v2)
                    if final_max_y is None or max(y_v1, y_v2) > final_max_y: final_max_y = max(y_v1, y_v2)

        if final_min_y is None: final_min_y = 210.0
        if final_max_y is None: final_max_y = 210.0

        
        # Apply mandatory buffer for bracketed vertical labels (prevent title block hit)
        if v_ext_needed:
            final_min_y -= 10.0
            
        # Return standardized signature: (paginated_buffer, logic_height, next_h_index)
        h_logic = final_max_y - final_min_y
        return paginated_buffer, h_logic, self.h_counter

    def _execute_draw_commands(self, msp, commands):
        # 1. Group Vertical Continuations (is_v=True) for common page bracket
        v_groups = {} # (outer_label, is_end, y_base) -> [list of cmds]
        other_v = []
        
        for p in commands:
            if p['type'] == 'continuation' and p.get('is_v', False):
                raw = p['label']
                outer = raw.split("@@")[0].strip() if "@@" in raw else raw
                # Clean (P.108) to just 108 for the group key (if needed)
                # But here we want the exact text for the Page label
                v_groups.setdefault((outer, p['is_end'], p['y']), []).append(p)
            else:
                other_v.append(p)

        # 2. Process Vertical Groups (Bracket + Big Label)
        for (page_label, is_end, y_base), group in v_groups.items():
            if not group: continue
            # Super-Compact Shift (Reduced from 4.0 to 2.5 for maximum clearance)
            v_shift = 2.5 if not is_end else -2.5
            y_shifted = y_base + v_shift
            
            # Find horizontal span
            xs = [p['x'] for p in group]
            min_x, max_x = min(xs) - 3.0, max(xs) + 3.0

            # Draw Bracket Logic
            # Point perfectly tangent to r=2.5 circle (Zero distance)
            oy = 2.5 if not is_end else -2.5
            tick_dir = -1.5 if not is_end else 1.5
            by = y_shifted + oy
            
            msp.add_line((min_x, by), (max_x, by), dxfattribs={'layer': 'LOGIC'})
            msp.add_line((min_x, by), (min_x, by + tick_dir), dxfattribs={'layer': 'LOGIC'})
            msp.add_line((max_x, by), (max_x, by + tick_dir), dxfattribs={'layer': 'LOGIC'})
            
            # Page Number (e.g. "TO P.52")
            p_prefix = "TO P." if is_end else "FROM P."
            ly = by + (2.5 if not is_end else -3.0)
            msp.add_text(f"{p_prefix}{page_label}", dxfattribs={'height': 2.0, 'layer': 'TEXT', 'style': self.font_name}).set_placement(((min_x+max_x)/2, ly), align=TextEntityAlignment.MIDDLE_CENTER)
            
            # Draw individual circles at shifted position with connecting tails
            for p in group:
                raw = p['label']
                inner = raw.split("@@")[1].strip() if "@@" in raw else ""
                # Draw the "Extension Tail" - connects logic tip to circle edge
                tail_start = y_base + (2.5 if is_end else -2.5)
                tail_end = y_shifted + (2.5 if is_end else -2.5)
                msp.add_line((p['x'], tail_start), (p['x'], tail_end), dxfattribs={'layer': 'LOGIC'})
                # Draw continuation circle
                self._draw_continuation_circle_immediate(
                    msp, p['x'], y_shifted, "", p['is_end'], True, inner_label=inner,
                    layer=p.get('layer', 'LOGIC'),
                    text_layer=p.get('text_layer', 'TEXT'),
                    color=p.get('color', 7)
                )

        # 3. Process All Other Commands
        for p in other_v:
            if p['type'] == 'line': msp.add_line((p['x1'], p['y1']), (p['x2'], p['y2']), dxfattribs={'layer': p['layer']})
            elif p['type'] == 'circle': msp.add_circle((p['x'], p['y']), radius=p['radius'], dxfattribs={'layer': p['layer']})
            elif p['type'] == 'text': msp.add_text(p['text'], dxfattribs={'layer': p['layer'], 'height': p['height'], 'style': p.get('style', 'SIMPLEX')}).set_placement((p['x'], p['y']), align=p['align'])
            elif p['type'] == 'dot':
                h = msp.add_hatch(color=p.get('color', 7), dxfattribs={'layer': p['layer']}); h.paths.add_edge_path().add_arc((p['x'], p['y']), radius=0.45, start_angle=0, end_angle=360)
            elif p['type'] == 'contact':
                self._draw_contact_immediate(
                    msp, p['x'], p['y'], p['label'], p['is_nc'],
                    layer=p.get('layer', 'LOGIC'),
                    text_layer=p.get('text_layer', 'TEXT'),
                    color=p.get('color', 7)
                )
            elif p['type'] == 'coil':
                self._draw_coil_immediate(
                    msp, p['x'], p['y'], p['name'], p['comment'],
                    layer=p.get('layer', 'LOGIC'),
                    text_layer=p.get('text_layer', 'TEXT'),
                    color=p.get('color', 7)
                )
            elif p['type'] == 'continuation':
                # horizontal continuations
                raw = p['label']; inner = ""; outer = raw
                if "@@" in raw:
                    pts = raw.split("@@"); outer = pts[0].strip(); inner = pts[1].strip()
                self._draw_continuation_circle_immediate(
                    msp, p['x'], p['y'], outer, p['is_end'], False, inner_label=inner,
                    layer=p.get('layer', 'LOGIC'),
                    text_layer=p.get('text_layer', 'TEXT'),
                    color=p.get('color', 7)
                )
    def _draw_dot_immediate(self, msp, x, y, layer='LOGIC', color=7):
        # Filled circle using true Hatch for identical PDF rendering
        hatch = msp.add_hatch(color=color, dxfattribs={'layer': layer})
        hatch.paths.add_edge_path().add_arc((x, y), radius=0.45, start_angle=0, end_angle=360)

    def _draw_contact_immediate(self, msp, x, y, label, is_nc, layer='LOGIC', text_layer='TEXT', color=7):
        # Leading terminal (centered on x-4)
        self._draw_dot_immediate(msp, x - 4, y, layer=layer, color=color)
        w, h = 1.2, 1.2  # Smaller, square proportions
        if not is_nc:
            # NO: Small sharp V - point ON wire
            msp.add_line((x - w, y + h), (x, y), dxfattribs={'layer': layer, 'color': color})
            msp.add_line((x + w, y + h), (x, y), dxfattribs={'layer': layer, 'color': color})
        else:
            # NC: Small sharp ^ - point ON wire
            msp.add_line((x - w, y - h), (x, y), dxfattribs={'layer': layer, 'color': color})
            msp.add_line((x + w, y - h), (x, y), dxfattribs={'layer': layer, 'color': color})
            
        text_len = len(str(label))
        # Logic: Prefer width-scaling (squishing) over font size reduction
        calc_wf = 1.0
        if text_len > 12:
            # Width-scaling for up to 18 chars to keep vertical height consistent
            calc_wf = max(0.40, 12.0 / text_len)
            
        fs = self.font_size
        if text_len > 18:
            # Only reduce font size for extremely long names (over 18 chars)
            fs = self.font_size * 0.82
        
        msp.add_text(str(label), dxfattribs={'layer': text_layer, 'height': fs, 'style': self.font_name, 'width': calc_wf, 'color': color}).set_placement((x, y + 3.5), align=TextEntityAlignment.CENTER)

    def _draw_coil_immediate(self, msp, x, y, name, comment, layer='LOGIC', text_layer='TEXT', color=7):
        """Draw the rectangle-below-wire coil symbol"""
        # Main wire through top
        msp.add_line((x, y), (x + 10, y), dxfattribs={'layer': layer, 'color': color})
        # Rectangle
        msp.add_line((x, y), (x, y - 3), dxfattribs={'layer': layer, 'color': color})
        msp.add_line((x, y - 3), (x + 10, y - 3), dxfattribs={'layer': layer, 'color': color})
        msp.add_line((x + 10, y - 3), (x + 10, y), dxfattribs={'layer': layer, 'color': color})
        # Terminals
        self._draw_dot_immediate(msp, x, y, layer=layer, color=color)
        self._draw_dot_immediate(msp, x + 10, y, layer=layer, color=color)
        # Trailing stub and final terminal
        msp.add_line((x + 10, y), (x + 15, y), dxfattribs={'layer': layer, 'color': color})
        self._draw_dot_immediate(msp, x + 15, y, layer=layer, color=color)
        
        text_len = len(str(name))
        # Optimized Coil Text scaling: Match Contact logic for consistency
        calc_wf = 1.0
        if text_len > 12:
            calc_wf = max(0.45, 13.0 / text_len)
        
        fs = self.font_size
        if text_len > 18:
            fs = self.font_size * 0.82
        elif text_len > 15:
            # Subtle reduction for coils to fit the box better
            fs = self.font_size * 0.92
        
        msp.add_text(name, dxfattribs={'layer': text_layer, 'height': fs, 'style': self.font_name, 'width': calc_wf, 'color': color}).set_placement((x + 5, y + 3.5), align=TextEntityAlignment.CENTER)

        # Place Timer text below the line if it exists
        if name in self.timer_map:
            timer_text = self.timer_map[name]
            msp.add_text(timer_text, dxfattribs={
                'height': self.font_size * 0.9, 
                'layer': text_layer,
                'style': self.font_name,
                'color': color
            }).set_placement((x + 5, y - 6.5), align=TextEntityAlignment.CENTER) # Adjusted Y for below coil

    def _draw_continuation_circle_immediate(self, msp, x, y, label, is_end, is_v=False, inner_label="", layer='LOGIC', text_layer='TEXT', color=7):
        radius = 2.5
        msp.add_circle((x, y), radius=radius, dxfattribs={'layer': layer, 'color': color})
        
        # Internal label (V1, V2) - DRAWN INSIDE THE CIRCLE
        if inner_label:
            # Reduced size to fit inside the circle cleanly
            msp.add_text(str(inner_label), dxfattribs={'layer': text_layer, 'height': 1.5, 'style': self.font_name, 'color': color}).set_placement((x, y), align=TextEntityAlignment.MIDDLE_CENTER)
            
        # External label (CNT.ON...) - DRAWN OUTSIDE
        fs = 1.8 # Clear architectural size
        if is_v:
            # Vertical: Above for START (is_end=False), Below for END (is_end=True)
            ty = y + 4.5 if not is_end else y - 4.5
            msp.add_text(str(label), dxfattribs={'layer': text_layer, 'height': fs, 'style': self.font_name, 'color': color}).set_placement((x, ty), align=TextEntityAlignment.CENTER)
        else:
            # Horizontal: Left for START (is_end=False), Right for END (is_end=True)
            tx = x - 6.0 if not is_end else x + 6.0
            align = TextEntityAlignment.RIGHT if not is_end else TextEntityAlignment.LEFT
            msp.add_text(str(label), dxfattribs={'layer': text_layer, 'height': fs, 'style': self.font_name, 'color': color}).set_placement((tx, y), align=align)
        # User requested NO dots for continuation circles

    def update_title_block(self, msp, sheet_num, total_sheets, crc, checksum, anchor_text):
        """Write current and next page numbers at the configured coordinate positions only."""
        cfg = self.layout_config

        # Current Sheet number
        curr_x = cfg.get('sheet_curr_x', 0) + cfg.get('sheet_curr_x_off', 0)
        curr_y = cfg.get('sheet_curr_y', 0) + cfg.get('sheet_curr_y_off', 0)

        # Next Sheet number
        next_x = cfg.get('sheet_next_x', 0) + cfg.get('sheet_next_x_off', 0)
        next_y = cfg.get('sheet_next_y', 0) + cfg.get('sheet_next_y_off', 0)

        if curr_x > 0:
            msp.add_text(f"{sheet_num:03d}", dxfattribs={'height': self.font_size, 'layer': 'TEXT'}).set_placement(
                (curr_x, curr_y), align=TextEntityAlignment.MIDDLE_CENTER)

        if next_x > 0:
            msp.add_text(f"{sheet_num + 1:03d}", dxfattribs={'height': self.font_size, 'layer': 'TEXT'}).set_placement(
                (next_x, next_y), align=TextEntityAlignment.MIDDLE_CENTER)

def generate_index_sheets(output_base, template_path, sheet_map, start_page, reserved_count, crc, cksum, anchor_text, 
                          layout_config=None, font_name="ARIAL", font_size=1.5, is_bold=False):
    """Generate professional index sheets listing Coils and their Page Numbers"""
    # Table constants
    x_base, y_base = 25, 215  
    header_row_h = 8.0
    col_w_idx = 15
    col_w_page = 25
    col_w_name = 220
    total_w = col_w_idx + col_w_page + col_w_name
    Y_MIN_INDEX = 45

    # Pre-calculate required pages to prevent overflow
    p_needed = 1
    y_calc = y_base
    
    # We need to temporarily hold the index items without the prefix to calculate p_needed
    temp_items = []
    # Add dummy lengths for the 3 static rows we will add
    y_calc -= max(7.0, (1 - 1) * 3.5 + 5.5) * 3 
    
    for s_num in sorted(sheet_map.keys()):
        coils = sheet_map[s_num]
        if coils:
            temp_items.append((f"{s_num:03d}", ", ".join(coils)))
            
    for i, (p_num, names) in enumerate(temp_items):
        coil_lines = textwrap.wrap(names, width=120)
        num_lines = len(coil_lines)
        line_spacing = 3.5
        current_row_h = max(7.0, (num_lines - 1) * line_spacing + 5.5)
        
        if y_calc - current_row_h < Y_MIN_INDEX:
            p_needed += 1
            y_calc = y_base
        y_calc -= current_row_h
        
    if p_needed > reserved_count:
        raise ValueError(f"Index Overflow Error: The logic index requires {p_needed} sheets, but only {reserved_count} sheets are reserved.")

    # Now construct all_items with exact page ranges
    all_items = [("", "COVER SHEET")]
    
    # Index pages
    idx_start = start_page
    idx_end = start_page + p_needed - 1
    if idx_start == idx_end:
        all_items.append((f"{idx_start:03d}", "INDEX"))
    else:
        all_items.append((f"{idx_start:03d} TO {idx_end:03d}", "INDEX"))
        
    # Spare pages
    spare_start = idx_end + 1
    spare_end = start_page + reserved_count - 1
    if spare_start <= spare_end:
        if spare_start == spare_end:
            all_items.append((f"{spare_start:03d}", "SPARE"))
        else:
            all_items.append((f"{spare_start:03d} TO {spare_end:03d}", "SPARE"))
            
    all_items.extend(temp_items)

    def start_new_page(p_num):
        doc = ezdxf.readfile(template_path) if template_path and os.path.exists(template_path) else ezdxf.new('R2010')
        msp = doc.modelspace()
        renderer = DXFLadderRenderer(doc, font_name=font_name, font_size=font_size, is_bold=is_bold, layout_config=layout_config)
        renderer.update_title_block(msp, p_num, total_sheets=0, crc=crc, checksum=cksum, anchor_text=anchor_text)
        
        # Header
        header_y_top = y_base + header_row_h
        msp.add_text("S.NO", dxfattribs={'height': 2.5, 'layer': 'TEXT'}).set_placement((x_base + 2, y_base + 2.5))
        msp.add_text("PAGE NO", dxfattribs={'height': 2.5, 'layer': 'TEXT'}).set_placement((x_base + col_w_idx + 2, y_base + 2.5))
        msp.add_text("COIL NAME", dxfattribs={'height': 2.5, 'layer': 'TEXT'}).set_placement((x_base + col_w_idx + col_w_page + 2, y_base + 2.5))
        
        msp.add_line((x_base, y_base), (x_base + total_w, y_base))
        msp.add_line((x_base, header_y_top), (x_base + total_w, header_y_top))
        
        return doc, msp, y_base

    generated_index_files = []
    p_idx = 0
    current_page_num = start_page
    doc, msp, y_curr = start_new_page(current_page_num)
    
    for i, (p_num, names) in enumerate(all_items):
        # Calculate height first to see if it fits
        # Use 120 width to better fill the 220 column
        coil_lines = textwrap.wrap(names, width=120) 
        num_lines = len(coil_lines)
        
        # Professional spacing and height (Tightened)
        line_spacing = 3.5
        # row_h calculation: Padding + text block height
        current_row_h = max(7.0, (num_lines - 1) * line_spacing + 5.5)

        
        if y_curr - current_row_h < Y_MIN_INDEX:
            # Finish current page vertical lines
            for vx in [x_base, x_base + col_w_idx, x_base + col_w_idx + col_w_page, x_base + total_w]:
                msp.add_line((vx, y_base + header_row_h), (vx, y_curr))
            
            # Save current page
            base_prefix = os.path.splitext(output_base)[0]
            out_name = f"{base_prefix}{current_page_num:05d}.dxf"
            doc.saveas(out_name)
            generated_index_files.append(out_name)
            
            # Start new page
            p_idx += 1
            current_page_num += 1
            doc, msp, y_curr = start_new_page(current_page_num)
            
        # Draw Data
        s_no = i + 1
        y_top = y_curr
        y_bottom = y_curr - current_row_h
        text_y_base = y_top - 3.0  # Tightened top gap

        
        msp.add_text(str(s_no), dxfattribs={'height': 2, 'layer': 'TEXT'}).set_placement((x_base + 2, text_y_base))
        msp.add_text(p_num, dxfattribs={'height': 2, 'layer': 'TEXT'}).set_placement((x_base + col_w_idx + 2, text_y_base))
        
        for line_idx, line in enumerate(coil_lines):
             msp.add_text(line, dxfattribs={'height': 1.8, 'layer': 'TEXT'}).set_placement((x_base + col_w_idx + col_w_page + 2, text_y_base - line_idx * line_spacing))
        
        msp.add_line((x_base, y_bottom), (x_base + total_w, y_bottom))
        y_curr = y_bottom

    # Finalize last page
    for vx in [x_base, x_base + col_w_idx, x_base + col_w_idx + col_w_page, x_base + total_w]:
        msp.add_line((vx, y_base + header_row_h), (vx, y_curr))
    
    base_prefix = os.path.splitext(output_base)[0]
    out_name = f"{base_prefix}{current_page_num:05d}.dxf"
    doc.saveas(out_name)
    print(f"DEBUG: Saved Index Sheet: {out_name}", flush=True)
    generated_index_files.append(out_name)
    
    return generated_index_files

def extract_crc_checksum(text):
    """Simple extraction of CRC and Checksum from comments"""
    crc = re.search(r'CRC\s*[:=]\s*([0-9A-Fa-f]+)', text, re.IGNORECASE)
    cksum = re.search(r'Checksum\s*[:=]\s*([0-9A-Fa-f]+)', text, re.IGNORECASE)
    return (crc.group(1) if crc else "????", cksum.group(1) if cksum else "????")

# batch_export_to_pdf removed (Slow legacy method)
# GUI now uses fast_dxf_to_pdf.py for high-speed conversion

def process_logic_file(logic_path, dxf_output_base, template_path="none", limit=None, 
                       font_name="ARIAL", font_size=1.5, is_bold=False, 
                       gen_dxf=True, gen_pdf=True, start_page=1, reserved_index=1, 
                       drawing_anchor="LSC35", cancel_check=None, pause_check=None,
                       layout_config=None):
    """Main processing function for the separate module with multi-page support"""
    import glob
    base_prefix = os.path.splitext(dxf_output_base)[0]
    # Clean old output DXF files from this prefix to avoid stale pages in PDF
    for old_f in (glob.glob(f"{base_prefix}_Page*.dxf") + 
                  glob.glob(f"{base_prefix}_Index*.dxf") + 
                  glob.glob(f"{base_prefix}[0-9][0-9][0-9][0-9][0-9].dxf")):
        try:
            os.remove(old_f)
        except Exception:
            pass

    print(f"Reading logic from {logic_path}...", flush=True)
    
    with open(logic_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
        
    # 1. Parse timers dynamically over the file using the official ALVT linter core module
    timer_map = {}
    try:
        from core.timer_bits_validator import _parse_timer_bits_section
        parsed_timers = _parse_timer_bits_section(content)
        for c_name, t_data in parsed_timers.items():
            set_raw = t_data.get("set_raw", "0:SEC")
            clear_raw = t_data.get("clear_raw", "0:SEC")
            timer_map[c_name.upper()] = f"SET={set_raw} CLEAR={clear_raw}"
    except ImportError:
        pass
    
    engine = LogicParserEngine()
    logic_block = engine.extract_logic_block(content)
    print(f"Extracted logic block ({len(logic_block)} chars)", flush=True)
    
    results = engine.parse(logic_block)
    num_rungs = len(results)
    print(f"Parsed {num_rungs} rungs.", flush=True)
    
    crc, cksum = extract_crc_checksum(content)
    print(f"File info: CRC={crc}, Checksum={cksum}", flush=True)
    
    # Use dynamic layout configuration or load from storage (Fixes script-based missing sheet numbers)
    cfg = layout_config or load_layout_config()
    # Tighter Logic Boundaries to provide absolute clearance for continuation circles and headers
    # Tighter Logic Boundaries to provide absolute clearance for continuation circles and headers
    Y_MAX = cfg.get('y_max', 215) # Default synchronized with layout_config
    Y_MIN = cfg.get('y_min', 48)  # Default synchronized with layout_config
    X_START = cfg.get('x_off', 35)
    X_END = cfg.get('full_w', 300)
    SMALL_THR = cfg.get('thr_w', 85)
    
    def create_page(page_num):
        print(f"  Generating Page {page_num}...", flush=True)
        if template_path and os.path.exists(template_path):
            doc = ezdxf.readfile(template_path)
        else:
            doc = ezdxf.new('R2010')
            msp = doc.modelspace()
            msp.add_lwpolyline([(0,0), (420,0), (420,297), (0,297), (0,0)], dxfattribs={'closed': True})
        
        renderer = DXFLadderRenderer(doc, font_name=font_name, font_size=font_size, is_bold=is_bold, 
                                    timer_map=timer_map, layout_config=cfg)
        renderer.update_title_block(doc.modelspace(), page_num, total_sheets=0, crc=crc, checksum=cksum, anchor_text=drawing_anchor)
        return doc, renderer

    page_registry = {} # Tracks Page -> (doc, renderer)
    def get_page(p_num):
        if p_num not in page_registry:
            page_registry[p_num] = create_page(p_num)
        return page_registry[p_num]

    sheet_map = {} # Tracks Page -> [Coils]
    logic_page_start = start_page + reserved_index
    page_num = logic_page_start
    doc, renderer = get_page(page_num)
    curr_y = Y_MAX
    h_counter = 1
    
    # Row-Based Buffering for 1, 2, and 3 columns
    row_rungs = []   # List of (name, root, t_room, raw_stmt, comment)
    row_h_max = 0.0
    current_col_cap = 3
    
    # Pre-calculate Column Layouts
    gap = cfg.get('gap', 15)
    col_w_3 = cfg.get('col_w', 85)
    col_w_2 = (X_END - X_START - gap) / 2.0
    full_w  = X_END - X_START

    X_COLS_3 = [X_START, X_START + col_w_3 + gap, X_START + 2*(col_w_3 + gap)]
    X_COLS_2 = [X_START, X_START + col_w_2 + gap]

    def flush_row():
        nonlocal curr_y, row_rungs, row_h_max, doc, renderer, page_num, current_col_cap, h_counter
        if not row_rungs: return

        # 1. Page Break Check
        if curr_y - row_h_max < Y_MIN:
            page_num += 1; doc, renderer = get_page(page_num); curr_y = Y_MAX; h_counter = 1

        # 2. Assign Columns based on Capacity
        if current_col_cap == 3:
            cols = X_COLS_3; use_w = col_w_3
        elif current_col_cap == 2:
            cols = X_COLS_2; use_w = col_w_2
        else:
            cols = [X_START]; use_w = full_w

        # 3. Render all rungs in the row
        for idx, (rn, rr, rt, rrw, rc) in enumerate(row_rungs):
            cx = cols[idx]
            cxe = cx + use_w
            # Standard renderer handles V-labels internally via render_rung or _execute_draw_commands
            pb, hl, hn = renderer.render_rung(rn, rr, cx, curr_y - rt, cxe, rc, rrw, h_start=h_counter, reset_h=True, abs_page=page_num)
            h_counter = hn
            
            # Draw results to current page (Standard mode usually doesn't wrap side-packed rungs across pages)
            for s in sorted(pb.keys()):
                 # If it wrapped, the subsequent segments will overlap if side-packed. 
                 # We assume side-packed rungs are short enough not to wrap.
                 renderer._execute_draw_commands(doc.modelspace(), pb[s])
        
        # 4. Advance Y
        curr_y -= (row_h_max + 4.0) # Ultra-High Density 4.0 gap
        row_rungs.clear()
        row_h_max = 0.0

    for i, (name, root, comment, raw_stmt) in enumerate(results):
        # Interactive Signal Handling (Pause/Cancel)
        if cancel_check and cancel_check(): 
            print("Conversion Interrupted by User. Finalizing current progress...", flush=True)
            break
        if pause_check:
            pause_check() # External blocking call to handle GUI Pause state
            
        if limit is not None and limit != "none" and i >= int(limit): break
        
        # Real-time Progress Update for GUI
        if i % 10 == 0 or i == len(results) - 1:
            print(f"  Rendering Rung {i+1}/{len(results)}: {name}", flush=True)

        # Size Classification
        ops = (raw_stmt or "").count('*') + (raw_stmt or "").count('+')
        contacts_count = ops + 1
        
        if contacts_count <= 1:
            rung_col_cap = 3
        elif contacts_count <= 3:
            rung_col_cap = 2
        else:
            rung_col_cap = 1

        # Estimate Height
        use_w = full_w if rung_col_cap == 1 else (col_w_3 if rung_col_cap == 3 else col_w_2)
        clean_stmt = (raw_stmt or "").replace('\n', ' ').strip()
        h_wrap = 150 if rung_col_cap == 1 else (75 if rung_col_cap == 2 else 50)
        # Dynamic WRAP_LEN logic for height estimation
        avail_w_est = (X_END - X_START) * 0.92
        char_w_est = font_size * 1.0 * 1.1
        wrap_char_est = max(20, int(avail_w_est / char_w_est))
        
        num_h = min(10, max(1, len(clean_stmt) // wrap_char_est + 1))
        # Match vertical scaling logic in render_rung
        line_sp_est = font_size * 2.3
        header_off_est = max(7.0, (1.5 + font_size + 1.5))
        # Use a conservative 1.25 height for text for safer room estimate
        t_room = header_off_est + (num_h - 1) * line_sp_est + (font_size * 1.5)
        
        lg_h = renderer.measure_wrapped_height(root, X_START, X_START + use_w)
        rung_h = t_room + max(12.0, lg_h) + 1.0 # Shrunk from 3.0 to 1.0 margin
 # Ultra-tight rung

        if rung_col_cap > 1:
            # Side-Packing Mode
            if current_col_cap != rung_col_cap or len(row_rungs) >= rung_col_cap:
                flush_row()
                current_col_cap = rung_col_cap
            
            row_rungs.append((name, root, t_room, raw_stmt, comment))
            row_h_max = max(row_h_max, rung_h)
            continue
        
        # Full Width Mode
        flush_row()
        
        # Check Page Break for Full Width (Original precise check restored to prevent overlap)
        if curr_y - rung_h < Y_MIN:
            page_num += 1; doc, renderer = get_page(page_num); curr_y = Y_MAX; h_counter = 1

        # Render Full Width
        initial_page_before_render = page_num
        pb, h_logic, hn = renderer.render_rung(name, root, X_START, curr_y - t_room, X_END, comment, raw_stmt, h_start=h_counter, reset_h=True, abs_page=page_num)
        h_counter = hn
        
        last_s = 0
        for s in sorted(pb.keys()):
            while s > last_s:
                page_num += 1; doc, renderer = get_page(page_num); curr_y = Y_MAX; h_counter = 1; last_s += 1
            renderer._execute_draw_commands(doc.modelspace(), pb[s])
            
        # 4. Advance Y based on final state
        # If the rung spilled across pages, the curr_y logic needs to be based on Page-Last
        if page_num > initial_page_before_render:
            # Over-spill case: the rung finished on a fresh page at Y_MAX, without a header (t_room)
            curr_y = Y_MAX - h_logic - 4.0
        else:
            # Single-page case: calculate offset from start (including t_room)
            curr_y = (curr_y - t_room) - h_logic - 4.0
 # Tightened full-width inter-row gap
    # Final Flush
    flush_row()

    # Flush all final coil lists
    for p_num, (p_doc, p_rend) in page_registry.items():
        sheet_map[p_num] = list(p_rend.coils_in_sheet)
    
    # Save all pages in the registry
    base_prefix = os.path.splitext(dxf_output_base)[0]
    if gen_dxf or gen_pdf:
        for p_num, (p_doc, p_rend) in page_registry.items():
            out_p = f"{base_prefix}{p_num:05d}.dxf"
            p_doc.saveas(out_p)
        
    logic_pages = []
    for s_num in range(logic_page_start, page_num + 1):
        logic_p = f"{base_prefix}{s_num:05d}.dxf"
        logic_pages.append(logic_p)
        
    print(f"Logic Complete: {len(logic_pages)} sheets prepared.", flush=True)
    
    # Generate Index
    print("Generating Index Sheets...", flush=True)
    index_pages = generate_index_sheets(dxf_output_base, template_path, sheet_map, start_page, reserved_index, crc, cksum, drawing_anchor,
                                        layout_config=cfg, font_name=font_name, font_size=font_size, is_bold=is_bold)
    
    # Combine lists
    all_pages = index_pages + logic_pages
    # Safety debug
    for p in all_pages:
        if not os.path.exists(p): print(f"WARNING: File missing for PDF bundle: {p}", flush=True)
    
    # Export to PDF block removed from engine core
    # Handled by GUI caller using high-speed Fast engine
    pass
    
    # Automated Background DWG Conversion removed (Legacy)

    
    # Clean up DXFs if NOT wanted? Usually better to keep them if they were used for PDF
    if not gen_dxf:
         # Optional: remove generated_pages
         pass

    return all_pages

if __name__ == "__main__":
    # Default paths from user's latest update
    default_txt = r"C:\Users\hitachia\Documents\python testing\Equivalent\DER_C2_S01.txt"
    default_output = r"C:\Users\hitachia\Documents\python testing\Equivalent\Logic_Output.dxf"
    default_template = get_resource_path(os.path.join("resources", "templates", "Template.dxf"))
    
    if len(sys.argv) < 3:
        if os.path.exists(default_txt):
            print(f"Using default user paths (Sample 5 rungs):")
            process_logic_file(default_txt, default_output, default_template, limit=5)
        else:
            print("Usage: python logic_to_autocad.py <input_txt> <output_dxf> [template_dxf]")
    else:
        # If output_dxf is provided, use it as the base name
        # Usage: script logic output [template] [limit] [font] [size] [bold] [gen_dxf] [gen_pdf] [start_p] [res_idx] [anchor]
        tpl = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3].lower() != "none" else None
        limit = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4].lower() != "none" else None
        font = sys.argv[5] if len(sys.argv) > 5 else "SIMPLEX"
        size = float(sys.argv[6]) if len(sys.argv) > 6 else 1.5
        bold = sys.argv[7].lower() == "true" if len(sys.argv) > 7 else False
        gen_dxf = sys.argv[8].lower() != "false" if len(sys.argv) > 8 else True
        gen_pdf = sys.argv[9].lower() != "false" if len(sys.argv) > 9 else True
        start_p = int(sys.argv[10]) if len(sys.argv) > 10 else 1
        res_idx = int(sys.argv[11]) if len(sys.argv) > 11 else 1
        anchor = sys.argv[12] if len(sys.argv) > 12 else "LSC35"
        
        process_logic_file(sys.argv[1], sys.argv[2], tpl, limit=limit, 
                           font_name=font, font_size=size, is_bold=bold,
                           gen_dxf=gen_dxf, gen_pdf=gen_pdf,
                           start_page=start_p, reserved_index=res_idx, 
                           drawing_anchor=anchor)
