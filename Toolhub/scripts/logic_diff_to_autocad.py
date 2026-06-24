# -*- coding: utf-8 -*-
"""
Logic Differential Renderer — Standalone Module
Renders OLD vs NEW logic diffs as colored DXF/PDF output.

Color Rules:
  ADDED   rung  → entire rung RED (color=1)
  DELETED rung  → entire rung GREEN (color=3)
  MODIFIED rung → Twin-Rung:
      OLD circuit: only DELETED nodes GREEN, unchanged WHITE
      NEW circuit: only ADDED nodes RED,     unchanged WHITE
  Timer-only change → Single rung, changed tokens colored surgically

NO changes are made to logic_to_autocad.py.
"""
import os
import sys
import glob
import ezdxf
import re
import textwrap
import difflib
from ezdxf.enums import TextEntityAlignment

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.logic_parser_engine import LogicParserEngine, LogicNode
from core.logic_diff_engine import LogicDiffEngine
from core.layout_config import load_layout_config
from scripts.logic_to_autocad import DXFLadderRenderer, extract_crc_checksum

try:
    from scripts.fast_dxf_to_pdf import batch_process_parallel
except ImportError:
    batch_process_parallel = None


class DiffStatus:
    UNCHANGED = 0
    ADDED     = 1
    DELETED   = 2
    MODIFIED  = 3
    COL_TIMER = 4


# ---------------------------------------------------------------------------
# Color-aware subclass of DXFLadderRenderer
# ---------------------------------------------------------------------------
class ColorDiffRenderer(DXFLadderRenderer):
    """
    Inherits DXFLadderRenderer but overrides ALL primitive buffer methods so
    that color is baked into every draw command at the moment it is buffered,
    while the node_status_stack is still populated with the correct node status.

    This avoids the need for any post-hoc blanket color assignment.
    """

    def __init__(self, doc, status_override=None, **kwargs):
        if 'font_size' not in kwargs:
            kwargs['font_size'] = 1.5
        super().__init__(doc, **kwargs)

        self.status_override  = status_override  # ADDED / DELETED / None for MODIFIED
        self.rung_context     = None             # 'OLD', 'NEW', or None
        self.node_status_stack = []

        # Create surgical color layers once
        for lname, cidx in [('SURGICAL_GREEN', 3), ('SURGICAL_RED', 1),
                             ('SURGICAL_TEXT_GREEN', 3), ('SURGICAL_TEXT_RED', 1)]:
            if lname not in doc.layers:
                doc.layers.new(lname, dxfattribs={'color': cidx})

    # ------------------------------------------------------------------
    # Color resolver
    # ------------------------------------------------------------------
    def _resolve_color(self, node=None):
        """
        Returns (logic_layer, text_layer, color_index, true_color_or_None).

        Priority:
          1. status_override (only for ADDED/DELETED whole-rung color)
          2. rung_context    → forces GREEN (OLD pass) or RED (NEW pass)
             but ONLY for nodes that are DELETED/ADDED respectively.
             Unchanged nodes in twin-rung mode stay WHITE.
          3. node diff_status from node_status_stack
        """
        # Whole-rung uniform color (ADDED or DELETED rungs)
        if self.status_override in (DiffStatus.ADDED, DiffStatus.DELETED):
            s = self.status_override
        else:
            # Per-node surgical color
            s = getattr(node, 'diff_status', DiffStatus.UNCHANGED)
            if s in (None, DiffStatus.UNCHANGED, DiffStatus.MODIFIED):
                # Walk stack for an inherited status
                for ss in reversed(self.node_status_stack):
                    if ss not in (DiffStatus.UNCHANGED, DiffStatus.MODIFIED):
                        s = ss
                        break
                else:
                    s = DiffStatus.UNCHANGED

        if s == DiffStatus.DELETED or (s == DiffStatus.ADDED and self.rung_context == 'OLD'):
            return 'SURGICAL_GREEN', 'SURGICAL_TEXT_GREEN', 3, 65280
        if s == DiffStatus.ADDED or (s == DiffStatus.DELETED and self.rung_context == 'NEW'):
            return 'SURGICAL_RED', 'SURGICAL_TEXT_RED', 1, 16711680

        return 'LOGIC', 'TEXT', 7, None

    def _get_color(self, node=None):
        """Legacy shim used by base class internals."""
        return self._resolve_color(node)[2]

    # ------------------------------------------------------------------
    # Primitive buffer overrides — bake color at buffer time
    # ------------------------------------------------------------------
    def _add_line(self, p1, p2, layer='LOGIC', bi=0):
        _, _, c, _ = self._resolve_color()
        lyr = 'SURGICAL_GREEN' if c == 3 else ('SURGICAL_RED' if c == 1 else layer)
        self.draw_buffer.append({'type': 'line',
                                  'x1': p1[0], 'y1': p1[1],
                                  'x2': p2[0], 'y2': p2[1],
                                  'layer': lyr, 'bi': bi, 'color': c})

    def _add_circle(self, center, radius, layer='LOGIC'):
        _, _, c, _ = self._resolve_color()
        lyr = 'SURGICAL_GREEN' if c == 3 else ('SURGICAL_RED' if c == 1 else layer)
        self.draw_buffer.append({'type': 'circle',
                                  'x': center[0], 'y': center[1],
                                  'radius': radius, 'layer': lyr, 'color': c})

    def _add_text(self, text, x, y, height=None, align=TextEntityAlignment.LEFT,
                  layer='TEXT', prevent_wrap=False):
        h = height if height is not None else self.font_size
        _, tlyr, c, _ = self._resolve_color()
        self.draw_buffer.append({'type': 'text', 'text': text, 'x': x, 'y': y,
                                  'height': h, 'align': align,
                                  'layer': tlyr, 'style': self.font_name, 'color': c})

    def _add_dot(self, x, y, layer='LOGIC'):
        # Dots must always match the wire color of the circuit they sit on.
        # Priority:
        #   1. status_override  → whole-rung ADDED (red) / DELETED (green)
        #   2. rung_context     → twin-rung OLD pass = green, NEW pass = red
        #   3. node_status_stack → surgical per-node coloring
        _, _, c, _ = self._resolve_color()

        # In twin-rung mode every dot lives on a wire that belongs to that
        # pass (OLD=green, NEW=red), so colour the dot to match the wire.
        if c == 7 and self.rung_context == 'OLD':
            c = 3
        elif c == 7 and self.rung_context == 'NEW':
            c = 1
        elif c == 7 and self.node_status_stack:
            # Single-rung surgical: scan ancestors for a changed branch
            for s in reversed(self.node_status_stack):
                if s == DiffStatus.DELETED or (s == DiffStatus.ADDED and self.rung_context == 'OLD'):
                    c = 3; break
                if s == DiffStatus.ADDED or (s == DiffStatus.DELETED and self.rung_context == 'NEW'):
                    c = 1; break

        lyr = 'SURGICAL_GREEN' if c == 3 else ('SURGICAL_RED' if c == 1 else layer)
        self.draw_buffer.append({'type': 'dot', 'x': x, 'y': y, 'layer': lyr, 'color': c})

    def _add_contact(self, x, y, label, is_nc=False):
        lyr, tlyr, c, _ = self._resolve_color()
        self.draw_buffer.append({'type': 'contact', 'x': x, 'y': y,
                                  'label': label, 'is_nc': is_nc,
                                  'layer': lyr, 'text_layer': tlyr, 'color': c})

    def _add_coil(self, x, y, name, comment=""):
        lyr, tlyr, c, _ = self._resolve_color()
        self.draw_buffer.append({'type': 'coil', 'x': x, 'y': y,
                                  'name': name, 'comment': comment,
                                  'layer': lyr, 'text_layer': tlyr, 'color': c})

    # ------------------------------------------------------------------
    # Node flat-draw override — twin-rung filtering + stack management
    # ------------------------------------------------------------------
    def _draw_node_flat(self, node, x1, y_wire, x2):
        s = getattr(node, 'diff_status', DiffStatus.UNCHANGED)
        # MODIFIED at a parent node means children carry the specific statuses
        eff = s if s != DiffStatus.MODIFIED else DiffStatus.UNCHANGED

        # Twin-Rung suppression: replace absent nodes with a plain wire segment
        if self.rung_context == 'OLD' and s == DiffStatus.ADDED:
            self._add_line((x1, y_wire), (x2, y_wire))
            return
        if self.rung_context == 'NEW' and s == DiffStatus.DELETED:
            self._add_line((x1, y_wire), (x2, y_wire))
            return

        # Special case for NOT nodes: the base class uses child.value directly
        # (line "_add_contact(node.children[0].value if type=='NOT'...)")
        # without routing through _draw_node_flat for the child.  That means
        # the child VAR's diff_status (DELETED/ADDED) never reaches the stack
        # and the contact renders white.  Push the child's status explicitly.
        if node.type == 'NOT' and node.children:
            child_s   = getattr(node.children[0], 'diff_status', DiffStatus.UNCHANGED)
            child_eff = child_s if child_s != DiffStatus.MODIFIED else DiffStatus.UNCHANGED
            # Use the more-specific (child) status if it carries colour information
            push_s = child_eff if child_eff not in (DiffStatus.UNCHANGED,) else eff
            self.node_status_stack.append(push_s)
            try:
                super()._draw_node_flat(node, x1, y_wire, x2)
            finally:
                self.node_status_stack.pop()
            return

        self.node_status_stack.append(eff)
        try:
            super()._draw_node_flat(node, x1, y_wire, x2)
        finally:
            self.node_status_stack.pop()


    def _add_coil(self, x, y, name, comment=""):
        # Always add to the logic draw buffer so both iterations of a modified
        # circuit (Twin-Rung passes) show their coils visually.
        self.draw_buffer.append({
            'type': 'coil',
            'x': x,
            'y': y,
            'name': name,
            'comment': comment,
            'layer': 'LOGIC',
            'rung_context': self.rung_context  # Persist for deferred timer rendering
        })
        # Only add to the sheet's coil list once if the coil name repeats on a page
        if name not in self.coils_in_sheet:
            self.coils_in_sheet.append(name)

    # ------------------------------------------------------------------
    # _finalize_and_render
    # The base class processes draw_buffer into paginated_buffer.
    # For LINE entries it creates new dicts with only x,y,layer — the
    # 'color' key is deliberately stripped.  For ATOMIC entries (contact,
    # coil, dot, text) it uses dict(p) which preserves 'color'.
    # We recover color for line/circle entries by reading the layer name.
    # ------------------------------------------------------------------
    def _finalize_and_render(self, x_start, y_wire, x_term, use_wrap=True,
                              h_logic=None, y_min=None):
        # ── Whole-rung color ──────────────────────────────────────────────────
        # Only non-7 for ADDED/DELETED rungs where EVERY element is one color.
        # Twin-rung passes keep rung_color=7 so unchanged wires/coils stay black.
        if self.status_override == DiffStatus.ADDED:
            rung_color = 1   # entire rung is red
        elif self.status_override == DiffStatus.DELETED:
            rung_color = 3   # entire rung is green
        else:
            rung_color = 7   # unchanged / MODIFIED / COL_TIMER → elements stay black



        pb, ht_min, ht_max = super()._finalize_and_render(x_start, y_wire, x_term,
                                                   use_wrap=use_wrap)

        # Map surgical layer names back to explicit color ints
        _lyr2c = {'SURGICAL_RED': 1, 'SURGICAL_GREEN': 3}

        for seg in pb.values():
            for p in seg:
                # Atomic items (contacts, text, dots) already have 'color' set by
                # their _add_* overrides — leave them as-is so only DELETED/ADDED
                # nodes are colored in twin-rung mode.
                if 'color' in p:
                    continue
                t   = p.get('type', '')
                lyr = p.get('layer', 'LOGIC')
                if t in ('line', 'circle'):
                    # Recover color from surgical layer name (preserved by base class).
                    inferred = _lyr2c.get(lyr)
                    if inferred:
                        p['color'] = inferred
                    elif rung_color in (1, 3):
                        # ADDED/DELETED whole-rung: entry arrow + wrap connection
                        # lines created fresh by the base class with layer='LOGIC'
                        # inherit the whole-rung color.
                        p['color'] = rung_color
                        p['layer'] = ('SURGICAL_RED' if rung_color == 1
                                      else 'SURGICAL_GREEN')
                    # else: unchanged lines stay without color → defaults to 7 (black)
                elif t in ('continuation', 'coil', 'text', 'dot'):
                    # Continuation items (V1/V2 brackets, H-wrap circles), coils,
                    # text, and dots that reach here have no color key yet.
                    # Inherit from surgical layer or use whole-rung color (which
                    # is 7 for twin-rungs, keeping them black).
                    p['color'] = _lyr2c.get(lyr, rung_color)
                    if p['color'] in (1, 3) and lyr in ('LOGIC', 'TEXT'):
                        p['layer'] = ('SURGICAL_RED' if p['color'] == 1
                                      else 'SURGICAL_GREEN')

        return pb, ht_min, ht_max



    # ------------------------------------------------------------------
    # Execute draw commands — color-aware version with v-group brackets
    # ------------------------------------------------------------------
    def _execute_draw_commands(self, msp, commands):
        # 1. Group vertical continuation circles (for bracket drawing)
        v_groups = {}
        other_v  = []
        for p in commands:
            if p['type'] == 'continuation' and p.get('is_v', False):
                raw   = p['label']
                outer = raw.split('@@')[0].strip() if '@@' in raw else raw
                c_outer = outer.replace('(','').replace(')','').replace('P.','').replace('P','').strip()
                v_groups.setdefault((c_outer, p['is_end'], p['y']), []).append(p)
            else:
                other_v.append(p)

        # 2. Draw vertical brackets (colored by first item's color)
        for (page_num, is_end, y_base), group in v_groups.items():
            if not group: continue
            # Prefer a non-white color from any member of the group so that
            # the bracket inherits the rung color (red/green) even if the
            # first item happens to have defaulted to 7.
            gc = next((p.get('color', 7) for p in group if p.get('color', 7) != 7), 7)
            gl, tl = self._layer_names(gc)
            a = {'layer': gl, 'color': gc}

            v_shift = 4.0 if not is_end else -4.0
            y_shifted = y_base + v_shift
            xs = [p['x'] for p in group]
            min_x, max_x = min(xs) - 3.0, max(xs) + 3.0
            oy = 2.5 if not is_end else -2.5
            tick = -2.0 if not is_end else 2.0
            by = y_shifted + oy

            msp.add_line((min_x, by), (max_x, by), dxfattribs=a)
            msp.add_line((min_x, by), (min_x, by + tick), dxfattribs=a)
            msp.add_line((max_x, by), (max_x, by + tick), dxfattribs=a)
            ly = by + (2.5 if not is_end else -3.0)
            msp.add_text(f"Page {page_num}",
                         dxfattribs={'layer': tl, 'color': gc, 'height': 2.0,
                                     'style': self.font_name}
                         ).set_placement(((min_x+max_x)/2, ly),
                                         align=TextEntityAlignment.MIDDLE_CENTER)
            for p in group:
                raw   = p['label']
                inner = raw.split('@@')[1].strip() if '@@' in raw else ''
                tail_s = y_base + (2.5 if is_end else -2.5)
                tail_e = y_shifted + (2.5 if is_end else -2.5)
                # Use the group color (gc) for the tail line so it matches
                # the bracket and circle — not the item's potentially stale color.
                msp.add_line((p['x'], tail_s), (p['x'], tail_e), dxfattribs=a)
                self._draw_continuation_diff(msp, p['x'], y_shifted, '',
                                              is_end, True, inner, gc, None)

        # 3. Draw all other commands with explicit color
        for p in other_v:
            c  = p.get('color', 7)
            tc = p.get('true_color')
            gl, tl = self._layer_names(c)
            a  = {'layer': gl, 'color': c}
            if tc: a['true_color'] = tc

            t = p['type']
            if t == 'line':
                msp.add_line((p['x1'], p['y1']), (p['x2'], p['y2']), dxfattribs=a)
            elif t == 'circle':
                msp.add_circle((p['x'], p['y']), radius=p['radius'], dxfattribs=a)
            elif t == 'text':
                msp.add_text(p['text'],
                             dxfattribs={'height': p['height'], 'color': c,
                                         'layer': tl,
                                         'style': p.get('style', self.font_name)}
                             ).set_placement((p['x'], p['y']), align=p['align'])
            elif t == 'mtext':
                msp.add_mtext(p['text'],
                              dxfattribs={'char_height': p['height'], 'color': c,
                                          'layer': tl, 'style': p.get('style', self.font_name),
                                          'width': p.get('width', 100), 'insert': (p['x'], p['y']),
                                          'attachment_point': p.get('align', 1)})
            elif t == 'dot':
                # Pass color directly to add_hatch() — this is the fill color.
                # Do NOT call set_solid_fill() without a color arg; it resets
                # the fill to black.  Mirror the base-class pattern exactly.
                gl, _ = self._layer_names(c)
                h = msp.add_hatch(color=c, dxfattribs={'layer': gl})
                h.paths.add_edge_path().add_arc(
                    (p['x'], p['y']), radius=0.45, start_angle=0, end_angle=360)
            elif t == 'contact':
                wf = 1.0
                if len(p['label']) > 12: wf = max(0.4, 12.0 / len(p['label']))
                self._draw_contact_diff(msp, p['x'], p['y'],
                                         p['label'], p['is_nc'], c, tc, wf)
            elif t == 'coil':
                self._draw_coil_diff(msp, p['x'], p['y'],
                                      p['name'], p.get('comment', ''), c, tc,
                                      rung_context=p.get('rung_context'))
            elif t == 'continuation':
                raw = p['label']; outer = raw; inner = ''
                if '@@' in raw:
                    pts = raw.split('@@', 1)
                    outer, inner = pts[0].strip(), pts[1].strip()
                self._draw_continuation_diff(msp, p['x'], p['y'], outer,
                                              p['is_end'], p.get('is_v', False),
                                              inner, c, tc)

    # ------------------------------------------------------------------
    # Immediate draw helpers (called at execution time)
    # ------------------------------------------------------------------
    def _layer_names(self, color):
        if color == 3:  return 'SURGICAL_GREEN',      'SURGICAL_TEXT_GREEN'
        if color == 1:  return 'SURGICAL_RED',        'SURGICAL_TEXT_RED'
        return 'LOGIC', 'TEXT'

    def _draw_coil_diff(self, msp, x, y, name, comment, color=7, tc=None, rung_context=None):
        gl, tl = self._layer_names(color)
        a = {'layer': gl, 'color': color}
        if tc: a['true_color'] = tc
        ta = {'layer': tl, 'color': color}
        if tc: ta['true_color'] = tc

        msp.add_line((x, y),       (x+10, y),    dxfattribs=a)
        msp.add_line((x, y),       (x, y-3),     dxfattribs=a)
        msp.add_line((x, y-3),     (x+10, y-3),  dxfattribs=a)
        msp.add_line((x+10, y-3),  (x+10, y),    dxfattribs=a)
        for px in (x, x+10, x+15):
            self._draw_dot_diff(msp, px, y, color, tc)
        msp.add_line((x+10, y), (x+15, y), dxfattribs=a)

        name_str = str(name)
        fs = self.font_size
        if len(name_str) > 20: fs *= 0.7
        elif len(name_str) > 14: fs *= 0.8
        msp.add_text(name_str,
                     dxfattribs={**ta, 'height': fs, 'style': self.font_name}
                     ).set_placement((x+5, y+3.5), align=TextEntityAlignment.CENTER)

        # Timer value below coil — color rules:
        #   COL_TIMER / MODIFIED : OLD value GREEN (above) + NEW value RED (below), stacked
        #   ADDED rung            : timer value RED  (new addition)
        #   DELETED rung          : timer value GREEN (was removed)
        #   UNCHANGED / same val  : timer value WHITE (no diff marker)
        tu = name_str.upper()
        if tu in self.timer_map:
            td = self.timer_map[tu]
            if isinstance(td, dict):
                t_new = td.get('new', '')
                t_old = td.get('old', '')
                rung_status = td.get('status', DiffStatus.UNCHANGED)

                _, tl_g = self._layer_names(3)
                _, tl_r = self._layer_names(1)

                if t_old and t_new and t_old != t_new:
                    # Timer value changed
                    if rung_context == 'OLD':
                        # Twin-rung OLD pass — show only old timer (green)
                        msp.add_text(t_old, dxfattribs={'layer': tl_g, 'color': 3,
                                                         'height': self.font_size * 0.9,
                                                         'style': self.font_name}
                                     ).set_placement((x+5, y-6.5),
                                                     align=TextEntityAlignment.CENTER)
                    elif rung_context == 'NEW':
                        # Twin-rung NEW pass — show only new timer (red)
                        msp.add_text(t_new, dxfattribs={'layer': tl_r, 'color': 1,
                                                         'height': self.font_size * 0.9,
                                                         'style': self.font_name}
                                     ).set_placement((x+5, y-6.5),
                                                     align=TextEntityAlignment.CENTER)
                    else:
                        # Single-rung surgical — stack both old (green) + new (red)
                        msp.add_text(t_old, dxfattribs={'layer': tl_g, 'color': 3,
                                                         'height': self.font_size * 0.85,
                                                         'style': self.font_name}
                                     ).set_placement((x+5, y-6.5),
                                                     align=TextEntityAlignment.CENTER)
                        msp.add_text(t_new, dxfattribs={'layer': tl_r, 'color': 1,
                                                         'height': self.font_size * 0.85,
                                                         'style': self.font_name}
                                     ).set_placement((x+5, y-10.5),
                                                     align=TextEntityAlignment.CENTER)
                elif t_new and not t_old:
                    # ADDED rung — timer value is new, show RED
                    msp.add_text(t_new, dxfattribs={'layer': tl_r, 'color': 1,
                                                     'height': self.font_size * 0.9,
                                                     'style': self.font_name}
                                 ).set_placement((x+5, y-6.5),
                                                 align=TextEntityAlignment.CENTER)
                elif t_old and not t_new:
                    # DELETED rung — timer value was removed, show GREEN
                    msp.add_text(t_old, dxfattribs={'layer': tl_g, 'color': 3,
                                                     'height': self.font_size * 0.9,
                                                     'style': self.font_name}
                                 ).set_placement((x+5, y-6.5),
                                                 align=TextEntityAlignment.CENTER)
                elif t_new:
                    # Same value on both sides — inherit rung color (white for unchanged)
                    msp.add_text(t_new, dxfattribs={**ta, 'height': self.font_size * 0.9,
                                                     'style': self.font_name}
                                 ).set_placement((x+5, y-6.5),
                                                 align=TextEntityAlignment.CENTER)
            elif td:
                msp.add_text(str(td), dxfattribs={**ta, 'height': self.font_size * 0.9,
                                                    'style': self.font_name}
                             ).set_placement((x+5, y-6.5), align=TextEntityAlignment.CENTER)

    def _draw_contact_diff(self, msp, x, y, label, is_nc, color=7, tc=None, wf=1.0):
        gl, tl = self._layer_names(color)
        a = {'layer': gl, 'color': color}
        if tc: a['true_color'] = tc

        self._draw_dot_diff(msp, x-4, y, color, tc)

        w, hb = 1.2, 1.2
        if not is_nc:
            msp.add_line((x-w, y+hb), (x, y), dxfattribs=a)
            msp.add_line((x+w, y+hb), (x, y), dxfattribs=a)
        else:
            msp.add_line((x-w, y-hb), (x, y), dxfattribs=a)
            msp.add_line((x+w, y-hb), (x, y), dxfattribs=a)

        lbl = str(label)
        fs = self.font_size
        
        calc_wf = wf
        if len(lbl) > 12:
            calc_wf = max(0.4, 12.0 / len(lbl))
            
        msp.add_text(lbl, dxfattribs={'height': fs, 'style': self.font_name,
                                      'color': color, 'layer': tl, 'width': calc_wf}
                     ).set_placement((x, y+3.5), align=TextEntityAlignment.CENTER)

    def _draw_continuation_diff(self, msp, x, y, label, is_end, is_v,
                                  inner='', color=7, tc=None):
        gl, tl = self._layer_names(color)
        a = {'layer': gl, 'color': color}
        if tc: a['true_color'] = tc
        msp.add_circle((x, y), radius=2.5, dxfattribs=a)
        if inner:
            msp.add_text(str(inner), dxfattribs={'layer': tl, 'color': color,
                                                   'height': 1.5, 'style': self.font_name}
                         ).set_placement((x, y), align=TextEntityAlignment.MIDDLE_CENTER)
        fs = 1.8
        if is_v:
            ty = y+4.5 if not is_end else y-4.5
            msp.add_text(str(label), dxfattribs={'layer': tl, 'color': color,
                                                   'height': fs, 'style': self.font_name}
                         ).set_placement((x, ty), align=TextEntityAlignment.CENTER)
        else:
            tx = x-6.0 if not is_end else x+6.0
            al = TextEntityAlignment.RIGHT if not is_end else TextEntityAlignment.LEFT
            msp.add_text(str(label), dxfattribs={'layer': tl, 'color': color,
                                                   'height': fs, 'style': self.font_name}
                         ).set_placement((tx, y), align=al)

    def _draw_dot_diff(self, msp, x, y, color=7, tc=None):
        gl, _ = self._layer_names(color)
        # Pass color directly to add_hatch() as the fill color parameter.
        # Do NOT call set_solid_fill() — it resets fill to black when called
        # without a color arg.  Mirror what the base class does for black dots.
        h = msp.add_hatch(color=color, dxfattribs={'layer': gl})
        h.paths.add_edge_path().add_arc((x, y), radius=0.6, start_angle=0, end_angle=360)

    # ------------------------------------------------------------------
    # Helper: add text to draw_buffer with an explicit color (for headers)
    # ------------------------------------------------------------------
    def _add_text_to_buffer(self, text, x, y, h, color):
        _, tl = self._layer_names(color)
        self.draw_buffer.append({'type': 'text', 'text': text, 'x': x, 'y': y,
                                  'height': h, 'align': TextEntityAlignment.LEFT,
                                  'layer': tl, 'style': self.font_name, 'color': color,
                                  'prevent_wrap': True})

    # ------------------------------------------------------------------
    # Surgical header renderer
    # ------------------------------------------------------------------
    def _draw_surgical_statement(self, x_base, y_start, logic_new,
                                  logic_old='', diff_color=7, limit_w=160,
                                  twin_mode=None, prefix=''):
        """Render the statement text using standard word wrap without gap-inducing token diffing."""
        if logic_old is None:
            logic_old = ''
        h = self.font_size
        tx, ty = x_base, y_start
        line_count = 1

        def strip_meta(s):
            m = re.match(r'^(\(Checksum=[^,]+,\s*CRC=[^)]+\))\s*(.*)$', s)
            return (m.group(1), m.group(2)) if m else ('', s)

        # Decide which text to draw
        text_to_draw = logic_old if twin_mode == 'OLD' else logic_new
        meta_text, clean_text = strip_meta(text_to_draw)

        # Arial/Standard font: actual rendered average char width ≈ 0.82x height
        char_w = h * 0.82
        chars_per_line = max(10, int(limit_w / char_w))

        def add_wrapped(plaintext, mtext_str=None, color=None):
            nonlocal line_count, ty
            if not plaintext: return
            text = plaintext.replace('\n', ' ').strip()
            
            # Predict line height requirements using textwrap (standard spacing)
            wraps = textwrap.wrap(text, width=chars_per_line, break_long_words=False, break_on_hyphens=False)
            n_lines = len(wraps) if wraps else 1
            
            # Scale line spacing based on font size
            line_step = 2.4 * self.font_size
            
            if mtext_str:
                self.draw_buffer.append({'type': 'mtext', 'x': x_base, 'y': ty + h*0.8,
                                         'height': h, 'width': limit_w, 'text': mtext_str,
                                         'color': color or 7, 'align': 1}) # 1 = TopLeft
            else:
                for ln in wraps:
                    self._add_text_to_buffer(ln, x_base, ty, h, color)
                    ty -= line_step

            if mtext_str:
                ty -= n_lines * line_step
            line_count += n_lines

        # Render Prefix
        if prefix:
            pc = 3 if twin_mode == 'OLD' else (1 if twin_mode == 'NEW' else 7)
            add_wrapped(prefix, color=pc)

        # Render Meta
        if meta_text:
            add_wrapped(meta_text, color=7)

        # Token diffing for the logic string
        meta_old, clean_old = strip_meta(logic_old) if logic_old else ('', '')
        # Render Statement using solid fallback
        sc = 3 if twin_mode == 'OLD' else (1 if twin_mode == 'NEW' else diff_color)
        add_wrapped(clean_text, color=sc)

        return line_count

    # ------------------------------------------------------------------
    # Measurement helpers (dry-run, no side effects)
    # ------------------------------------------------------------------
    def measure_header(self, raw_stmt, x_start, x_term, is_small=True, prefix=''):
        if not raw_stmt: return 0
        old_buf = self.draw_buffer
        self.draw_buffer = []
        limit_w = max(50.0, x_term - x_start - 15.0)
        n = self._draw_surgical_statement(x_start, 0, raw_stmt,
                                           getattr(self, 'old_stmt', ''),
                                           7, limit_w=limit_w, prefix=prefix)
        self.draw_buffer = old_buf
        return n

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
            return sum(max(25.0, drops.get(r, 0)+12.0) for r in range(mr+1))
        finally:
            self.draw_buffer = old_buf
            if old_xc is not None: self.x_curr = old_xc
            elif hasattr(self, 'x_curr'): del self.x_curr

    # ------------------------------------------------------------------
    # Twin-Rung render_rung
    # ------------------------------------------------------------------
    def render_rung(self, output_name, root, x_start, y_wire, x_term,
                    comment='', raw_stmt='', use_wrap=True, h_start=1,
                    double_render_data=None):
        """
        Renders a logic rung with diff coloring.
        If double_render_data is provided, renders OLD (green surgical) +
        NEW (red surgical) stacked twin rungs.
        """
        if not double_render_data:
            # --- Single-rung path (ADDED / DELETED / surgical MODIFIED) ---
            self.max_label_lines = 0
            self.h_counter = h_start

            # Surgical merge for MODIFIED (non-major) rungs
            if getattr(self, 'old_stmt', '') and self.status_override == DiffStatus.MODIFIED:
                de = LogicDiffEngine()
                try:
                    old_r = LogicParserEngine().parse(self.old_stmt)[0][1]
                    de.diff_nodes(old_r, root, merge=True)
                except Exception:
                    pass

            pag, h_logic, hn = super().render_rung(
                output_name, root, x_start, y_wire, x_term,
                comment=comment, raw_stmt='', use_wrap=use_wrap, h_start=h_start, reset_h=True)
            
            local_consumed = self.h_counter - h_start
            for p_elements in pag.values():
                for p in p_elements:
                    if p.get('type') == 'continuation':
                        import re
                        m = re.match(r'(.*?@@\s*H)(\d+)(.*)', p.get('label', ''))
                        if m:
                            orig_num = int(m.group(2))
                            new_num = int(orig_num - 1 + h_start)
                            p['label'] = f"{m.group(1)}{new_num}{m.group(3)}"

            h_next = int(h_start + local_consumed)

            # Header
            limit_w = max(50.0, x_term - x_start - 15.0)
            extra_lh = max(0, (self.max_label_lines - 1) * 3.5)
            c = self._resolve_color()[2]
            old_stmt = getattr(self, 'old_stmt', '')

            # Dynamic line step for consistent spacing
            line_step = 2.4 * self.font_size
            
            # Text diff detection for header coloring
            def _norm(s):
                import re
                return re.sub(r'\s+', '', re.sub(r'\(Checksum=[^,]+,\s*CRC=[^)]+\)', '', s))
            is_text_diff = bool(old_stmt and _norm(old_stmt) != _norm(raw_stmt))

            if is_text_diff:
                old_buf = self.draw_buffer
                self.draw_buffer = []
                n_new = self._draw_surgical_statement(x_start, 0, raw_stmt, old_stmt, diff_color=1, limit_w=limit_w, twin_mode='NEW', prefix='(NEW)')
                n_old = self._draw_surgical_statement(x_start, 0, raw_stmt, old_stmt, diff_color=3, limit_w=limit_w, twin_mode='OLD', prefix='(OLD)')
                n = n_new + n_old
                self.draw_buffer = old_buf

                y_head = y_wire + 9.0 + extra_lh + (n-1)*line_step
                self.draw_buffer = []
                self._draw_surgical_statement(x_start, y_head, raw_stmt, old_stmt, diff_color=3, limit_w=limit_w, twin_mode='OLD', prefix='(OLD)')
                y_head_new = y_head - (n_old * line_step)
                self._draw_surgical_statement(x_start, y_head_new, raw_stmt, old_stmt, diff_color=1, limit_w=limit_w, twin_mode='NEW', prefix='(NEW)')
            else:
                # Dry-run header to get line count
                old_buf = self.draw_buffer
                self.draw_buffer = []
                n = self._draw_surgical_statement(x_start, 0, raw_stmt, old_stmt, diff_color=7, limit_w=limit_w)
                self.draw_buffer = old_buf

                y_head = y_wire + 9.0 + extra_lh + (n-1)*line_step
                self.draw_buffer = []
                self._draw_surgical_statement(x_start, y_head, raw_stmt, old_stmt, diff_color=c, limit_w=limit_w)

            if 0 not in pag: pag[0] = []
            pag[0].extend(self.draw_buffer)

            self.draw_buffer = []
            return pag, h_logic, h_next

        # --- Twin-Rung path (structural MODIFIED) ---
        old_root = double_render_data['old_root']
        new_root = double_render_data['new_root']
        old_raw  = double_render_data['old_raw']
        new_raw  = double_render_data['new_raw']
        limit_w  = x_term - x_start - 15

        # Mark diff status on nodes
        de = LogicDiffEngine()
        de.diff_nodes(old_root, new_root, merge=False)

        # ── OLD rung ──────────────────────────────────────────────────
        self.rung_context   = 'OLD'
        self.status_override = None
        self.max_label_lines = 0
        self.h_counter = h_start

        # Dry-run OLD header
        old_buf = self.draw_buffer
        self.draw_buffer = []
        n_old = self._draw_surgical_statement(x_start, 0, new_raw, old_raw,
                                               diff_color=3, limit_w=limit_w,
                                               twin_mode='OLD', prefix='(OLD)')
        self.draw_buffer = old_buf

        pag_old, h_old, hn_old = super().render_rung(
            output_name, old_root, x_start, y_wire, x_term,
            comment='', raw_stmt='', use_wrap=use_wrap, h_start=h_start, reset_h=True)
            
        consumed_old = self.h_counter - h_start
        for p_elements in pag_old.values():
            for p in p_elements:
                if p.get('type') == 'continuation':
                    import re
                    m = re.match(r'(.*?@@\s*H)(\d+)(.*)', p.get('label', ''))
                    if m:
                        orig_num = int(m.group(2))
                        new_num = orig_num - 1 + h_start
                        p['label'] = f"{m.group(1)}{new_num}{m.group(3)}"

        next_h = int(h_start + consumed_old)

        line_step = 2.4 * self.font_size
        extra_old_h = max(0, (self.max_label_lines-1)*3.5)
        # Scale the header clearance
        head_clearance = 6.0 * self.font_size - 1.0
        y_head_old  = y_wire + head_clearance + extra_old_h + (n_old-1)*line_step





        self.draw_buffer = []
        self._draw_surgical_statement(x_start, y_head_old, new_raw, old_raw,
                                       diff_color=3, limit_w=limit_w,
                                       twin_mode='OLD', prefix='(OLD)')
        head_old = list(self.draw_buffer)

        # ── NEW rung ──────────────────────────────────────────────────
        self.rung_context    = 'NEW'
        self.status_override = None
        self.max_label_lines = 0

        # Dry-run NEW header
        old_buf = self.draw_buffer
        self.draw_buffer = []
        n_new = self._draw_surgical_statement(x_start, 0, new_raw, old_raw,
                                               diff_color=1, limit_w=limit_w,
                                               twin_mode='NEW', prefix='(NEW)')
        self.draw_buffer = old_buf

        # Gap between OLD circuit bottom and NEW wire = inter_gap + NEW header clearance
        line_step      = 2.4 * self.font_size
        head_clearance = 6.0 * self.font_size - 1.0
        h_head_new_req = head_clearance + n_new * line_step
        inter_gap      = 4.0 * self.font_size

        # Detect if OLD circuit overflowed to additional sub-pages
        max_old_page = max(pag_old.keys()) if pag_old else 0

        if max_old_page == 0:
            # OLD fit on this page — position NEW below OLD on same page
            y_wire_new = y_wire - h_old - inter_gap - h_head_new_req
        else:
            # OLD overflowed to sub-page(s).  Start NEW at the top of the next
            # sub-page after OLD's last page so there is zero overlap.
            # Sub-page coordinate system (from _finalize_and_render / map_y):
            #   Page 0: absolute y in [ly_min, y_max]
            #   Page s (s>=1): absolute y in [ly_min - s*l_slot, ly_min - (s-1)*l_slot)
            #   Top of page s (s>=1) → absolute y just below ly_min - (s-1)*l_slot
            ly_min  = self.layout_config.get('y_min', 45)
            l_top   = 210
            l_slot  = l_top - ly_min
            # Absolute y of the TOP of page (max_old_page + 1)
            y_top_of_next = ly_min - max_old_page * l_slot
            # NEW wire sits below its header on that fresh sub-page
            y_wire_new = y_top_of_next - 2.0 - h_head_new_req







        self.h_counter = next_h
        pag_new, h_new, hn_new = super().render_rung(
            output_name, new_root, x_start, y_wire_new, x_term,
            comment='', raw_stmt='', use_wrap=use_wrap, h_start=next_h, reset_h=True)
            
        consumed_new = self.h_counter - next_h
        for p_elements in pag_new.values():
            for p in p_elements:
                if p.get('type') == 'continuation':
                    import re
                    m = re.match(r'(.*?@@\s*H)(\d+)(.*)', p.get('label', ''))
                    if m:
                        orig_num = int(m.group(2))
                        new_num = int(orig_num - 1 + next_h)
                        p['label'] = f"{m.group(1)}{new_num}{m.group(3)}"

        # h_new is already provided by the standardized return signature
        final_h = int(next_h + consumed_new)

        line_step = 2.4 * self.font_size
        extra_new_h = max(0, (self.max_label_lines-1)*3.5)
        head_clearance = 6.0 * self.font_size - 1.0
        y_head_new  = y_wire_new + head_clearance + extra_new_h + (n_new-1)*line_step





        self.draw_buffer = []
        self._draw_surgical_statement(x_start, y_head_new, new_raw, old_raw,
                                       diff_color=1, limit_w=limit_w,
                                       twin_mode='NEW', prefix='(NEW)')
        head_new = list(self.draw_buffer)

        # Merge page buffers
        pag = {}
        for s in set(pag_old) | set(pag_new):
            pag[s] = pag_old.get(s, []) + pag_new.get(s, [])

        if 0 not in pag: pag[0] = []
        # OLD header always belongs on page 0 (it sits above y_wire which is on page 0)
        pag[0] = head_old + pag[0]

        if max_old_page == 0:
            # OLD fit on a single page — NEW header also goes on page 0
            pag[0].extend(head_new)
        else:
            # OLD overflowed — NEW header goes on sub-page (max_old_page+1)
            # Its absolute Y coordinates must be remapped to that sub-page's
            # coordinate space (same mapping as _finalize_and_render's map_y).
            ly_min_v = self.layout_config.get('y_min', 45)
            l_top_v  = 210
            l_slot_v = l_top_v - ly_min_v

            def _remap_y(y_abs):
                if y_abs >= ly_min_v:
                    return y_abs
                overflow = ly_min_v - y_abs
                return l_top_v - (overflow % l_slot_v)

            target_page = max_old_page + 1
            remapped_head = []
            for item in head_new:
                it = dict(item)
                if 'y'  in it: it['y']  = _remap_y(it['y'])
                if 'y1' in it: it['y1'] = _remap_y(it['y1'])
                if 'y2' in it: it['y2'] = _remap_y(it['y2'])
                remapped_head.append(it)
            # Prepend header so it appears ABOVE the circuit on that sub-page
            pag[target_page] = remapped_head + pag.get(target_page, [])

        self.rung_context = None

        # Total height on the FINAL PAGE of the NEW circuit
        # If NEW circuit is on Page 0, height includes headers. 
        # If NEW circuit overflows to Page 1+, headers are on Page 0 so we only count circuit height.
        if max(pag_new.keys()) == 0:
             # Height from y_wire (logic start) down to bottom of new circuit
             total_h = y_wire - (y_wire_new - h_new) 
        else:
             total_h = h_new + 10.0 # Just the final page logic height

        
        return pag, total_h, final_h


# ---------------------------------------------------------------------------
# PDF export — operates ONLY on the supplied file list
# ---------------------------------------------------------------------------
def batch_export_to_pdf_colored(dxf_paths, pdf_path):
    """Export exactly the supplied DXF pages (no directory scan)."""
    if not dxf_paths:
        return
    print(f"Exporting Colored PDF: {os.path.basename(pdf_path)}...", flush=True)
    try:
        from scripts.fast_dxf_to_pdf import batch_process_parallel, consolidate_pdfs
        import tempfile, shutil

        tmp_dir = tempfile.mkdtemp(prefix='diff_pdf_')
        try:
            # Copy only the current-run pages to a clean temp dir
            names = []
            for f in dxf_paths:
                if os.path.exists(f):
                    dst = os.path.join(tmp_dir, os.path.basename(f))
                    shutil.copy2(f, dst)
                    names.append(dst)

            if not names:
                print("  [PDF] No valid DXF files to export.", flush=True)
                return

            batch_process_parallel(tmp_dir, tmp_dir, use_color=True, merge=True)
            gen = os.path.join(tmp_dir, 'CONSOLIDATED_DRAWINGS.pdf')
            if os.path.exists(gen):
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)
                shutil.move(gen, pdf_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    except Exception as e:
        print(f"  [PDF ERROR] {e}", flush=True)
        import traceback; traceback.print_exc()


# ---------------------------------------------------------------------------
# Main diff entry point
# ---------------------------------------------------------------------------
def process_diff(old_path, new_path, out_base, template_path='none',
                 font_name='SIMPLEX', font_size=1.5, is_bold=False,
                 gen_dxf=True, gen_pdf=True, start_page=1, reserved_index=50,
                 drawing_anchor='LSC35', modified_only=False,
                 cancel_check=None, pause_check=None,
                 layout_config=None, only_modified_sheets=True):
    """
    Compare old_path vs new_path and render the differences as colored DXF/PDF.
    """
    print("Diffing Logic Files...", flush=True)
    with open(old_path, 'r', encoding='utf-8', errors='ignore') as f:
        old_content = f.read()
    with open(new_path, 'r', encoding='utf-8', errors='ignore') as f:
        new_content = f.read()

    engine     = LogicParserEngine()
    old_parsed = engine.parse(engine.extract_logic_block(old_content))
    new_parsed = engine.parse(engine.extract_logic_block(new_content))

    de = LogicDiffEngine()

    from core.timer_bits_validator import _parse_timer_bits_section
    old_timers = _parse_timer_bits_section(old_content)
    new_timers = _parse_timer_bits_section(new_content)

    merged_rungs = []
    
    from collections import defaultdict
    
    # Track order of each occurrence in the new file
    new_occurrences_indexed = []
    new_seen_counts = defaultdict(int)
    for idx, r in enumerate(new_parsed):
        name = r[0]
        uname = name.upper()
        count = new_seen_counts[uname]
        new_seen_counts[uname] += 1
        new_occurrences_indexed.append((r, count, idx))

    old_seen_counts = defaultdict(int)
    old_occurrences_indexed = []
    for idx, r in enumerate(old_parsed):
        name = r[0]
        uname = name.upper()
        count = old_seen_counts[uname]
        old_seen_counts[uname] += 1
        old_occurrences_indexed.append((r, count))

    old_lookup = {}
    for r, count in old_occurrences_indexed:
        name, root_old, comment_old, raw_old = r
        old_lookup[(name.upper(), count)] = r

    new_lookup = {}
    for r_new, count, idx in new_occurrences_indexed:
        new_lookup[(r_new[0].upper(), count)] = idx

    # --- Changes and additions (iterate new file order) ---
    for r_new, count, idx in new_occurrences_indexed:
        name, root_new, comment, raw_new = r_new
        uname = name.upper()
        t_new = new_timers.get(uname, {})
        sort_k = float(idx)

        # Do we have a matching occurrence in the old file?
        key = (uname, count)
        if key in old_lookup:
            _, root_old, _, raw_old = old_lookup[key]
            t_old = old_timers.get(uname, {})

            # Structural difference
            is_logic_diff = de.diff_nodes(root_old, root_new)

            # Normalize text to ignore ALVT hidden formatting spaces/newlines and Checksum prefixes
            def normalize_text(s):
                s = re.sub(r'\(Checksum=[^,]+,\s*CRC=[^)]+\)', '', s)
                return re.sub(r'\s+', '', s)

            is_text_diff = (normalize_text(raw_old) != normalize_text(raw_new))
            is_timer_diff = (t_old.get('set_raw')   != t_new.get('set_raw') or
                             t_old.get('clear_raw') != t_new.get('clear_raw'))

            if is_logic_diff or is_text_diff:
                merged_rungs.append((name, root_new, comment, raw_new,
                                      DiffStatus.MODIFIED, t_new, root_old, raw_old, sort_k))
            elif is_timer_diff:
                merged_rungs.append((name, root_new, comment, raw_new,
                                      DiffStatus.COL_TIMER, t_new, root_old, raw_old, sort_k))
            elif not modified_only:
                merged_rungs.append((name, root_new, comment, raw_new,
                                      DiffStatus.UNCHANGED, t_new, None, '', sort_k))
        else:
            # It's an addition
            merged_rungs.append((name, root_new, comment, raw_new,
                                  DiffStatus.ADDED, t_new, None, '', sort_k))

    # --- Deletions (in old, not in new) ---
    last_known_new_idx = -1.0
    for r_old, count in old_occurrences_indexed:
        name, root_old, comment, raw_old = r_old
        uname = name.upper()
        key = (uname, count)
        if key in new_lookup:
            last_known_new_idx = float(new_lookup[key])
        else:
            # It's a deletion. Assign it just after last_known_new_idx
            last_known_new_idx += 0.001
            t_old = old_timers.get(uname, {})
            # Keep name clean — header color communicates DELETED status
            merged_rungs.append((name, root_old, comment, raw_old,
                                  DiffStatus.DELETED, t_old, None, '', last_known_new_idx))
                                  
    # Finalize merged order and strip sort key 
    merged_rungs.sort(key=lambda x: x[-1])
    merged_rungs = [x[:-1] for x in merged_rungs]

    if not merged_rungs:
        print("No changes found to render.", flush=True)
        return

    print(f"Rendering {len(merged_rungs)} rungs matching selection...", flush=True)

    # Use more of the page — Y=40.0 provides a 5.0 unit safety buffer above title boundary
    cfg = load_layout_config()
    if layout_config:
        cfg.update(layout_config)
    cfg['y_min'] = cfg.get('y_min', 40)
    Y_MAX = cfg.get('y_max', 215)
    Y_MIN = cfg.get('y_min', 40.0) # Safety buffer (Title block at 35.0)
    X_COLS_3 = [cfg.get('x_off', 20),
                cfg.get('x_off', 20) + cfg.get('col_w', 60) + cfg.get('gap', 5),
                cfg.get('x_off', 20) + 2*(cfg.get('col_w', 60) + cfg.get('gap', 5))]
    
    col_w_2 = (cfg.get('full_w', 190) - cfg.get('gap', 5)) / 2.0

    crc, cksum = extract_crc_checksum(new_content)

    # Build timer_map for renderer
    timer_map = {}
    for n, _, _, _, s_flag, t, _, _ in merged_rungs:
        un = n.upper()
        t_new_val = (f"STP {t.get('set_raw','?')} CLEAR={t.get('clear_raw','?')}" if t else '')
        t_old_val = ''
        if un in old_timers:
            ot = old_timers[un]
            t_old_val = f"STP {ot.get('set_raw','?')} CLEAR={ot.get('clear_raw','?')}"
        # For timer-change rungs, store both so coil can show old (green) / new (red)
        timer_map[un] = {'old': t_old_val, 'new': t_new_val,
                          'status': s_flag}  # status helps coil pick color

    base_prefix = os.path.splitext(out_base)[0]

    # ── Save diff report ───────────────────────────────────────────────────
    STATUS_LABELS = {DiffStatus.ADDED: 'ADDED', DiffStatus.DELETED: 'DELETED',
                     DiffStatus.MODIFIED: 'MODIFIED', DiffStatus.COL_TIMER: 'TIMER_CHANGE',
                     DiffStatus.UNCHANGED: 'UNCHANGED'}
    report_path = base_prefix + '_diff_report.txt'
    with open(report_path, 'w', encoding='utf-8') as rpt:
        rpt.write(f'DIFF REPORT\nOld: {os.path.basename(old_path)}\nNew: {os.path.basename(new_path)}\n')
        rpt.write('=' * 70 + '\n')
        for _n, _r, _c, _raw, _s, _t, _, _ in merged_rungs:
            lbl = STATUS_LABELS.get(_s, '?')
            rpt.write(f'[{lbl:15s}] {_n}\n')
            rpt.write(f'  STMT: {_raw[:120].strip()}\n\n')
    print(f"  Diff report saved: {report_path}", flush=True)

    # ── Print summary ─────────────────────────────────────────────────────
    from collections import Counter
    cnt = Counter(STATUS_LABELS.get(s,'?') for _,_,_,_,s,_,_,_ in merged_rungs)
    for k,v in cnt.items(): print(f"    {k}: {v}", flush=True)

    # Clean old output DXF files from this prefix to avoid stale pages in PDF
    for old_f in (glob.glob(f"{base_prefix}_Page*.dxf") + 
                  glob.glob(f"{base_prefix}_Index*.dxf") + 
                  glob.glob(f"{base_prefix}[0-9][0-9][0-9][0-9][0-9].dxf")):
        try: os.remove(old_f)
        except Exception: pass

    def create_page(p_num):
        print(f"  Generating Page {p_num}...", flush=True)
        d = (ezdxf.readfile(template_path)
             if template_path and template_path != 'none' and os.path.exists(template_path)
             else ezdxf.new('R2010'))
        r = ColorDiffRenderer(d, font_name=font_name, font_size=font_size,
                               is_bold=is_bold, timer_map=timer_map, layout_config=cfg)
        r.update_title_block(d.modelspace(), p_num, 0, crc, cksum, drawing_anchor)
        return d, r

    page_registry = {}
    def get_page(p_num):
        if p_num not in page_registry:
            page_registry[p_num] = create_page(p_num)
        return page_registry[p_num]

    modified_pages = set()

    sheet_map = {}
    page_num  = start_page + reserved_index
    doc, renderer = get_page(page_num)
    curr_y    = Y_MAX
    next_h    = 1

    row_rungs = []   # (name, root, t_room, raw, status, old_raw, old_root)
    row_h_max = 0.0
    current_col_cap = 3

    def flush_row():
        nonlocal curr_y, row_rungs, row_h_max, next_h, doc, renderer, page_num, current_col_cap
        if not row_rungs: return

        # Relax page break to prevent huge empty margins
        if curr_y - row_h_max - 2.0 < cfg['y_min']:
            sheet_map[page_num] = list(renderer.coils_in_sheet)
            page_num += 1
            doc, renderer = get_page(page_num)
            curr_y = Y_MAX

        col_max_h = 0.0
        
        if current_col_cap == 3:
            cols = X_COLS_3
            use_w = cfg.get('col_w', 60)
        elif current_col_cap == 2:
            cols = [cfg.get('x_off', 20), cfg.get('x_off', 20) + col_w_2 + cfg.get('gap', 5)]
            use_w = col_w_2
        else:
            cols = [cfg.get('x_off', 20)]
            use_w = cfg.get('full_w', 190)

        for i, (rn, rr, rt, rrw, rs, ro, r_old) in enumerate(row_rungs):
            cx = cols[i]; cxe = cx + use_w
            renderer.status_override = rs
            renderer.old_stmt = ro
            
            pb, hl, hn = renderer.render_rung(rn, rr, cx, curr_y - rt, cxe,
                                               '', rrw, use_wrap=True,
                                               h_start=next_h)
            
            next_h = int(hn)
            col_max_h = max(col_max_h, rt + hl)

            # Paginate correctly to avoid overlaps
            last_s = 0
            for s in sorted(pb.keys()):
                # Multi-page wrap for tall rungs
                while s > last_s:
                    sheet_map[page_num] = list(renderer.coils_in_sheet)
                    page_num += 1
                    doc, renderer = get_page(page_num)
                    curr_y = Y_MAX
                    last_s += 1
                
                renderer._execute_draw_commands(doc.modelspace(), pb[s])
                if rs != DiffStatus.UNCHANGED:
                    modified_pages.add(page_num)

        # If it was a multi-page diff, the new curr_y must be based on the final page's min y
        if last_s > 0:
            final_sheet_cmds = pb.get(last_s, [])
            min_y_on_page = 210.0
            for p in final_sheet_cmds:
                y_val = p.get('y', min(p.get('y1', 210.0), p.get('y2', 210.0)))
                if y_val < min_y_on_page:
                    min_y_on_page = y_val
            curr_y = min_y_on_page - 4.0
        else:
            curr_y -= col_max_h + 4.0 # Tightened inter-row gap

        row_rungs.clear()
        row_h_max = 0.0

    # ── Main render loop ────────────────────────────────────────────────────
    status_labels = {DiffStatus.ADDED: 'ADDED', DiffStatus.DELETED: 'DELETED',
                     DiffStatus.MODIFIED: 'MODIFIED', DiffStatus.COL_TIMER: 'TIMER'}
    for name, root, comment, raw, status, t_data, old_root, old_raw in merged_rungs:
        if cancel_check and cancel_check(): break
        print(f"  [{status_labels.get(status,'?'):8s}] {name} "
              f"| w={root.w:.0f} h={root.h:.0f} | page={page_num} y={curr_y:.0f}", flush=True)

        is_major = False

        if status == DiffStatus.MODIFIED:
            if old_root:
                if not de.nodes_equal(old_root, root):
                    is_major = True

        # ── Surgical merge for non-major MODIFIED / COL_TIMER ─────────────
        if status in (DiffStatus.MODIFIED, DiffStatus.COL_TIMER) and not is_major and old_root:
            de.diff_nodes(old_root, root, merge=True)

        # ── Measure node layout ───────────────────────────────────────────
        renderer._measure_node(root)
        renderer.old_stmt = old_raw

        # ── Size classification ───────────────────────────────────────────
        ops = raw.count('*') + raw.count('+')
        contacts_count = ops + 1
        
        if is_major:
            rung_col_cap = 1
        elif contacts_count <= 1:
            rung_col_cap = 3
        elif contacts_count <= 3:
            rung_col_cap = 2
        else:
            rung_col_cap = 1

        is_sm = (rung_col_cap > 1)

        # ── Estimate rung height ──────────────────────────────────────────
        use_w = cfg.get('full_w', 190) if rung_col_cap == 1 else (cfg.get('col_w', 60) if rung_col_cap == 3 else col_w_2)
        rung_h = renderer.measure_wrapped_height(root, X_COLS_3[0], X_COLS_3[0] + use_w)

        # ── Header height ─────────────────────────────────────────────────
        x_lim_hdr = (cfg.get('x_off', 20) + use_w - 10) if is_sm else (cfg.get('x_off', 20) + cfg.get('full_w', 190))
        n_hdr  = renderer.measure_header(raw, cfg.get('x_off', 20), x_lim_hdr, is_small=is_sm, prefix='(NEW)')
        
        # Room for vertical stacked OLD header if text-only diff is present
        if status == DiffStatus.MODIFIED and old_raw:
            def _norm(s): return re.sub(r'\s+', '', re.sub(r'\(Checksum=[^,]+,\s*CRC=[^)]+\)', '', s))
            if _norm(old_raw) != _norm(raw):
                n_hdr_old = renderer.measure_header(old_raw, cfg.get('x_off', 20), x_lim_hdr, is_small=is_sm, prefix='(OLD)')
                n_hdr += n_hdr_old

        t_room = 8.0 + max(0, n_hdr-1)*4.0 + getattr(root, 'h_above', 0.0)

        # ── Estimate rung height ──────────────────────────────────────────
        if is_major and old_root:
            old_h = renderer.measure_wrapped_height(old_root, cfg.get('x_off', 20),
                                                     cfg.get('x_off', 20) + cfg.get('full_w', 190))
            new_h = renderer.measure_wrapped_height(root,     cfg.get('x_off', 20),
                                                     cfg.get('x_off', 20) + cfg.get('full_w', 190))
            n_ho  = renderer.measure_header(old_raw, cfg.get('x_off', 20),
                                             cfg.get('x_off', 20) + cfg.get('full_w', 190), is_small=False)
            n_hn  = renderer.measure_header(raw,     cfg.get('x_off', 20),
                                             cfg.get('x_off', 20) + cfg.get('full_w', 190), is_small=False)
            # Use the same constants as the inner render_rung twin path:
            # head_clearance = 6.0*fs - 1.0, line_step = 2.4*fs, inter_gap = 4.0*fs
            fs = renderer.font_size
            _lnstep = 2.4 * fs
            _hclr   = 6.0 * fs - 1.0
            _inter  = 4.0 * fs
            h_old_hdr = _hclr + (n_ho - 1) * _lnstep   # OLD header above OLD wire
            h_new_hdr = _hclr + n_hn * _lnstep           # NEW header above NEW wire
            rung_h = h_old_hdr + old_h + _inter + h_new_hdr + new_h + 4.0

        else:
            lw    = cfg.get('full_w', 190) if rung_col_cap == 1 else (cfg.get('col_w', 60) if rung_col_cap == 3 else col_w_2)
            lg_h  = renderer.measure_wrapped_height(root, cfg.get('x_off', 20),
                                                      cfg.get('x_off', 20) + lw)
            rung_h = t_room + max(12.0, lg_h) + 3.0 # Shrunk from 18.0/8.0


        double_data = None
        if is_major and old_root:
            double_data = {'old_root': old_root, 'new_root': root,
                           'old_raw': old_raw,   'new_raw': raw}

        # ── Pack small rungs side-by-side (Enforced for all small rungs) ──
        if is_sm:
            if current_col_cap != rung_col_cap or len(row_rungs) >= rung_col_cap:
                flush_row()
                current_col_cap = rung_col_cap
            row_rungs.append((name, root, t_room, raw, status, old_raw, old_root))
            row_h_max = max(row_h_max, rung_h)
            continue

        # ── Full-width rung (major, complex, added/deleted whole rung) ─────
        flush_row()

        # ── Page Break Logic ────────────────────────────────────────────────
        # Force placement if on a fresh page (curr_y >= Y_MAX-5) to avoid blank page loops
        if curr_y - rung_h < Y_MIN and curr_y < (Y_MAX - 5):
            sheet_map[page_num] = list(renderer.coils_in_sheet)
            page_num += 1
            doc, renderer = get_page(page_num)
            curr_y = Y_MAX

        renderer.y_min = Y_MIN
        renderer.old_stmt = old_raw

        if is_major:
            renderer.status_override = None    # twin-rung manages context internally
        else:
            renderer.status_override = status  # blanket color for ADDED/DELETED

        pb, h_logic, hn = renderer.render_rung(
            name, root, cfg.get('x_off', 20), curr_y - t_room,
            cfg.get('x_off', 20) + cfg.get('full_w', 190),
            comment, raw, use_wrap=True, h_start=next_h,
            double_render_data=double_data)
        next_h = int(hn)

        for s in sorted(pb):
            if s > 0:
                sheet_map[page_num] = list(renderer.coils_in_sheet)
                page_num += 1
                doc, renderer = get_page(page_num)
                curr_y = Y_MAX
                if is_major:
                    renderer.status_override = None
                else:
                    renderer.status_override = status
            renderer._execute_draw_commands(doc.modelspace(), pb[s])
            if status != DiffStatus.UNCHANGED:
                modified_pages.add(page_num)

        # Advance cursor by actual consumed height + tightened parity buffer
        curr_y = (curr_y - t_room) - h_logic - 4.0

    flush_row()

    # Save last page
    sheet_map[page_num] = list(renderer.coils_in_sheet)

    # Save DXF sheets
    logic_pages = []
    for p_num, (p_doc, p_rend) in page_registry.items():
        if (not only_modified_sheets) or (p_num in modified_pages):
            out_p = f"{base_prefix}{p_num:05d}.dxf"
            p_doc.saveas(out_p)
            logic_pages.append(out_p)
            
    print(f"  Saved {len(logic_pages)} logic pages.", flush=True)

    # Index sheets
    index_pages = []
    try:
        from scripts.logic_to_autocad import generate_index_sheets
        index_pages = generate_index_sheets(out_base, template_path, sheet_map,
                                             start_page, reserved_index,
                                             crc, cksum, drawing_anchor,
                                             layout_config=cfg,
                                             font_name=font_name, font_size=font_size, is_bold=is_bold)
    except Exception as e:
        print(f"  [INDEX ERROR] Failed to generate index sheets: {e}", flush=True)
        import traceback; traceback.print_exc()
                                             
    all_pages = index_pages + logic_pages

    if gen_pdf and all_pages:
        pdf_out = out_base.replace('.dxf', '.pdf')
        batch_export_to_pdf_colored(all_pages, pdf_out)
        print(f"Differential PDF: {pdf_out}", flush=True)