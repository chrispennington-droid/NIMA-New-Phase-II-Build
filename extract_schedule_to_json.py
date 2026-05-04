#!/usr/bin/env python3
"""
extract_schedule_to_json.py

Outside-Blender extractor for the NIMA Phase II Tyler Prove-Out Facility
interior walkthrough blockout.

Reads the locked Master Schedule workbook
    NIMA_Phase2_Master_Schedule_LOCKED-3.xlsx
and writes
    nima_schedule.json
    nima_schedule_extractor_report.txt

The Excel workbook is the absolute source of truth. SVGs are not parsed.
This extractor never invents geometry. Rows that cannot be safely converted
are logged and skipped.

Run:
    python3 extract_schedule_to_json.py
or
    python3 extract_schedule_to_json.py --xlsx <path> --out <dir>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter

try:
    import openpyxl
except ImportError:
    sys.stderr.write(
        "ERROR: openpyxl is required. Install with: pip install openpyxl\n"
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRID_TO_M = 2.4384  # 1 grid square = 8 ft = 2.4384 m
HEADER_ROW = 5

DEFAULT_XLSX_NAME = "NIMA_Phase2_Master_Schedule_LOCKED-3.xlsx"
OUT_JSON_NAME = "nima_schedule.json"
OUT_REPORT_NAME = "nima_schedule_extractor_report.txt"

# Forbidden terms (must never appear in any text we forward to the builder).
# Internal validation constant only — never written to JSON output.
FORBIDDEN_TERMS = [
    "elevator",
    "elev shaft",
    "lift shaft",
]

# Section labels whose rows are not geometry-eligible (rules / materials /
# spot checks).
NON_GEOMETRY_SECTION_PREFIXES = ("01", "02", "06", "07")

# Categories that never produce geometry.
NON_GEOMETRY_CATEGORIES = {
    "Rule",
    "Material",
    "Material definition",
    "Spot Check",
    "Thickness Rule",
    "Height Rule",
}

# Header columns we expect in row 5.
NUMERIC_GRID_COLS = [
    "Center X Grid", "Center Y Grid",
    "Min X Grid", "Max X Grid", "Min Y Grid", "Max Y Grid",
    "Length Grid X", "Depth Grid Y",
]
NUMERIC_M_COLS = [
    "Length M", "Depth M", "Base Z M", "Top Z M",
    "Height M", "Thickness M",
]
NUMERIC_OTHER_COLS = ["Height Ft", "Rotation Deg"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_float(value):
    """Return float(value) or None if not safely convertible."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def grid_to_m(v):
    f = safe_float(v)
    return None if f is None else f * GRID_TO_M


def scrub_forbidden(text):
    """Replace any forbidden term in free text with a neutral marker so the
    builder never reproduces them in object names, labels, or logs."""
    if text is None:
        return None
    s = str(text)
    out = s
    for term in FORBIDDEN_TERMS:
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        out = pattern.sub("[redacted]", out)
    return out


def collection_for_row(row):
    """Apply the master-prompt collection assignment priority."""
    eid = (row.get("element_id") or "").upper()
    cat = (row.get("category") or "")
    floor = (row.get("floor") or "")
    section = (row.get("section") or "")
    generate = (row.get("generate") or "")
    verification = (row.get("verification") or "")

    if "HIGHBAY" in eid or "_HB_" in eid or "high-bay" in cat.lower():
        return "NIMA_HighBay"
    if floor.strip() == "Roof" or eid.startswith("ROOF"):
        return "NIMA_Roof"
    if "SPAWN" in eid:
        return "NIMA_Spawn"

    sec_prefix = section.strip()[:2]
    if sec_prefix in NON_GEOMETRY_SECTION_PREFIXES or cat in NON_GEOMETRY_CATEGORIES:
        return "NIMA_Rules_Materials"

    if generate == "Debug Only" or "VERIFY" in str(verification).upper():
        return "NIMA_Debug_Reference"

    if "F2" in floor:
        return "NIMA_F2"
    if "F1" in floor:
        return "NIMA_F1"

    return "NIMA_Debug_Reference"


def is_non_geometry_row(row):
    """Rows that should not produce normal build geometry."""
    sec_prefix = (row.get("section") or "").strip()[:2]
    cat = row.get("category") or ""
    if sec_prefix in NON_GEOMETRY_SECTION_PREFIXES:
        return True
    if cat in NON_GEOMETRY_CATEGORIES:
        return True
    return False


# ---------------------------------------------------------------------------
# Notes-driven polygon parsing
# ---------------------------------------------------------------------------

# Priority-1 markers (final user decision)
PRIORITY_1_MARKERS = [
    r"\[USER DECISION\s*-\s*FINAL",
    r"USER DECISION\s*-\s*FINAL",
    r"Final locked corners",
    r"Use corrected corners",
    r"User confirmed",
    r"Accepted final corners",
    r"Locked coordinates",
]

# Priority-2 markers (lesser user decisions)
PRIORITY_2_MARKERS = [
    r"\[USER DECISION\b",
    r"Accepted corners",
    r"User decision",
    r"Final user ruling",
    r"Verified corner points",
    r"\[Footprint matches\b",
]

# Phrases marking superseded coordinate sets (line-level).
SUPERSEDED_MARKERS = [
    "prior", "superseded", "deprecated", "older bbox", "old parallelogram",
    "debug only", "fallback", "previous extraction", "not final",
    "axis-aligned debug bbox", "axis aligned debug bbox",
]

# Hard stop tokens for bare-pair collection: stop scanning at the first
# occurrence of any of these (case-insensitive), so we don't pick up
# closing-loop duplicates, debug bbox values, or cross-review references.
HARD_STOP_TOKENS = [
    "→ closes", "->closes", "-> closes", "closes to",
    "Bbox:", "[CROSS-REVIEW", "[CROSS REVIEW", "[Overall",
    "Axis-aligned debug bbox", "axis aligned debug bbox",
]


def _hard_stop_position(text, start=0):
    """Return the lowest index >= start at which any HARD_STOP_TOKENS
    appears, or len(text) if none."""
    lower = text.lower()
    end = len(text)
    for tok in HARD_STOP_TOKENS:
        i = lower.find(tok.lower(), start)
        if i != -1 and i < end:
            end = i
    return end


def _dedupe_polygon(points):
    """Drop a trailing point that equals the first point (closing-loop dup)
    and consecutive duplicates."""
    if not points:
        return points
    out = [points[0]]
    for p in points[1:]:
        if p != out[-1]:
            out.append(p)
    if len(out) > 2 and out[-1] == out[0]:
        out = out[:-1]
    return out


# Coordinate patterns
RE_LABELED_CORNER = re.compile(
    r"\b(NW|NE|SE|SW)\s*\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)",
    re.IGNORECASE,
)
RE_BARE_PAIR = re.compile(
    r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)"
)


def _filter_blocks(notes):
    """Split notes into blocks (split on '].' or sentence boundaries) and
    flag whether each block is superseded or labeled with a priority marker."""
    return notes  # we operate directly on the full string with regex windows


def _extract_labeled_polygon(text):
    """Return a dict {NW, NE, SE, SW: (x,y)} from a contiguous span, or None.

    Looks for at least 3 distinct corner labels in `text` and returns a
    dict keyed by corner label."""
    found = {}
    for m in RE_LABELED_CORNER.finditer(text):
        label = m.group(1).upper()
        x = float(m.group(2))
        y = float(m.group(3))
        if label not in found:
            found[label] = (x, y)
    if len(found) >= 3:
        return found
    return None


def _ordered_from_labels(label_dict):
    order = ["NW", "NE", "SE", "SW"]
    pts = []
    for lbl in order:
        if lbl in label_dict:
            pts.append([label_dict[lbl][0], label_dict[lbl][1]])
    return pts


def parse_polygon_from_notes(notes):
    """Parse polygon vertices from a Notes cell using master-prompt priority.

    Returns dict:
        {
          "polygon_grid": [[x,y], ...] or None,
          "selected_priority": "P1"|"P2"|"P3"|None,
          "source_phrase": str|None,
          "all_detected_sets": [ {kind, points, span_start, span_end}, ... ],
          "warnings": [str, ...]
        }
    """
    result = {
        "polygon_grid": None,
        "selected_priority": None,
        "source_phrase": None,
        "all_detected_sets": [],
        "warnings": [],
    }
    if not notes:
        return result

    text = str(notes)

    # Helper: find spans matching any pattern in `markers`.
    def find_marker_spans(markers):
        spans = []
        for pat in markers:
            for m in re.finditer(pat, text, flags=re.IGNORECASE):
                spans.append((m.start(), m.end(), m.group(0)))
        spans.sort(key=lambda x: x[0])
        return spans

    # Try Priority 1.
    p1_spans = find_marker_spans(PRIORITY_1_MARKERS)
    if p1_spans:
        # Use the LAST priority-1 span; scan a window after it for labeled
        # corners or bare pairs, BEFORE any superseded marker.
        last = p1_spans[-1]
        window_start = last[1]
        # window ends at next ']' or 800 chars or next superseded marker.
        window_end = min(len(text), window_start + 800)
        sup_idx = window_end
        for sup in SUPERSEDED_MARKERS:
            i = text.lower().find(sup, window_start, window_end)
            if i != -1 and i < sup_idx:
                sup_idx = i
        bracket_close = text.find("]", window_start, window_end)
        if bracket_close != -1 and bracket_close < sup_idx:
            sup_idx = bracket_close + 1
        window = text[window_start:sup_idx]
        labeled = _extract_labeled_polygon(window)
        if labeled:
            pts = _dedupe_polygon(_ordered_from_labels(labeled))
            if len(pts) >= 3:
                result["polygon_grid"] = pts
                result["selected_priority"] = "P1"
                result["source_phrase"] = last[2]
                result["all_detected_sets"].append(
                    {"kind": "P1-labeled", "points": pts}
                )

    # Try Priority 2 if Priority 1 didn't yield anything.
    if result["polygon_grid"] is None:
        p2_spans = find_marker_spans(PRIORITY_2_MARKERS)
        if p2_spans:
            last = p2_spans[-1]
            window_start = last[1]
            window_end = min(len(text), window_start + 800)
            sup_idx = window_end
            for sup in SUPERSEDED_MARKERS:
                i = text.lower().find(sup, window_start, window_end)
                if i != -1 and i < sup_idx:
                    sup_idx = i
            bracket_close = text.find("]", window_start, window_end)
            if bracket_close != -1 and bracket_close < sup_idx:
                sup_idx = bracket_close + 1
            window = text[window_start:sup_idx]
            labeled = _extract_labeled_polygon(window)
            if labeled:
                pts = _ordered_from_labels(labeled)
                if len(pts) >= 3:
                    result["polygon_grid"] = pts
                    result["selected_priority"] = "P2"
                    result["source_phrase"] = last[2]
                    result["all_detected_sets"].append(
                        {"kind": "P2-labeled", "points": pts}
                    )
            else:
                # bare pair sequence
                pairs = _dedupe_polygon([
                    [float(a), float(b)]
                    for a, b in RE_BARE_PAIR.findall(window)
                ])
                # avoid grabbing single-pair bbox-style debug values
                if len(pairs) >= 3:
                    result["polygon_grid"] = pairs
                    result["selected_priority"] = "P2"
                    result["source_phrase"] = last[2]
                    result["all_detected_sets"].append(
                        {"kind": "P2-bare-pairs", "points": pairs}
                    )

    # Priority 3: first clean coordinate sequence in the entire notes.
    if result["polygon_grid"] is None:
        # Walk the notes; gather bare pairs but exclude regions inside
        # superseded markers' lines.
        # Build a mask of "bad" regions.
        bad_regions = []
        lower = text.lower()
        for sup in SUPERSEDED_MARKERS:
            start = 0
            while True:
                i = lower.find(sup, start)
                if i == -1:
                    break
                # mark that line/sentence
                line_start = text.rfind("\n", 0, i)
                line_start = 0 if line_start == -1 else line_start
                line_end = text.find("\n", i)
                line_end = len(text) if line_end == -1 else line_end
                bad_regions.append((line_start, line_end))
                start = i + len(sup)

        def in_bad(pos):
            for s, e in bad_regions:
                if s <= pos <= e:
                    return True
            return False

        # Truncate at the first hard-stop token (e.g. "Bbox:", "[CROSS-REVIEW",
        # "→ closes to") so we don't ingest bbox values, cross-review notes,
        # or closing-loop duplicates as polygon vertices.
        hard_stop = _hard_stop_position(text, 0)

        # Try labeled first.
        labeled_filtered = {}
        for m in RE_LABELED_CORNER.finditer(text):
            if m.start() >= hard_stop:
                break
            if in_bad(m.start()):
                continue
            label = m.group(1).upper()
            x = float(m.group(2))
            y = float(m.group(3))
            if label not in labeled_filtered:
                labeled_filtered[label] = (x, y)
        if len(labeled_filtered) >= 3:
            pts = _ordered_from_labels(labeled_filtered)
            result["polygon_grid"] = _dedupe_polygon(pts)
            result["selected_priority"] = "P3"
            result["source_phrase"] = "first labeled set outside superseded regions"
            result["all_detected_sets"].append(
                {"kind": "P3-labeled", "points": result["polygon_grid"]}
            )
        else:
            pairs = []
            for m in RE_BARE_PAIR.finditer(text):
                if m.start() >= hard_stop:
                    break
                if in_bad(m.start()):
                    continue
                pairs.append([float(m.group(1)), float(m.group(2))])
            pairs = _dedupe_polygon(pairs)
            if len(pairs) >= 3:
                result["polygon_grid"] = pairs
                result["selected_priority"] = "P3"
                result["source_phrase"] = "first bare-pair sequence outside superseded regions"
                result["all_detected_sets"].append(
                    {"kind": "P3-bare-pairs", "points": pairs}
                )

    # Always also report all detected coordinate sets for transparency.
    all_labeled = _extract_labeled_polygon(text)
    if all_labeled:
        pts = _ordered_from_labels(all_labeled)
        result["all_detected_sets"].append(
            {"kind": "all-labeled-in-notes", "points": pts}
        )
    all_pairs = [
        [float(a), float(b)] for a, b in RE_BARE_PAIR.findall(text)
    ]
    if all_pairs:
        result["all_detected_sets"].append(
            {"kind": "all-bare-pairs-in-notes", "count": len(all_pairs)}
        )

    if result["polygon_grid"] is None:
        result["warnings"].append(
            "no polygon vertices extracted from notes"
        )

    return result


# ---------------------------------------------------------------------------
# Notes-driven segment-list parsing (rows 103, 104, 105)
# ---------------------------------------------------------------------------

RE_CYAN_RECT = re.compile(
    r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)"
    r"\s*len\s*(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def parse_segment_list_from_notes(notes):
    """Parse '(x,y) len L' segment entries from notes."""
    if not notes:
        return []
    out = []
    for m in RE_CYAN_RECT.finditer(str(notes)):
        out.append({
            "center_grid": [float(m.group(1)), float(m.group(2))],
            "len_grid": float(m.group(3)),
        })
    return out


def parse_window_marker_list_from_notes(notes):
    """Parse window-marker centers from row 104's notes (no 'len' tokens)."""
    if not notes:
        return []
    text = str(notes)
    # Window markers are bare pairs that are NOT immediately followed by 'len'.
    out = []
    for m in RE_BARE_PAIR.finditer(text):
        tail = text[m.end(): m.end() + 8].lower()
        if tail.lstrip().startswith("len"):
            continue
        out.append([float(m.group(1)), float(m.group(2))])
    return out


# ---------------------------------------------------------------------------
# Excel reading
# ---------------------------------------------------------------------------

def read_workbook(xlsx_path):
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    sheet_names = wb.sheetnames
    if "Master Build List" not in sheet_names:
        raise SystemExit(
            f"ERROR: 'Master Build List' sheet not found. Sheets: {sheet_names}"
        )
    ws = wb["Master Build List"]

    headers = {}
    for c in range(1, ws.max_column + 1):
        v = ws.cell(row=HEADER_ROW, column=c).value
        if v is not None:
            headers[str(v).strip()] = c

    rows = []
    for r in range(HEADER_ROW + 1, ws.max_row + 1):
        any_val = False
        rec = {"_row": r}
        for name, c in headers.items():
            v = ws.cell(row=r, column=c).value
            rec[name] = v
            if v is not None and str(v).strip() != "":
                any_val = True
        if any_val:
            rows.append(rec)

    return sheet_names, headers, rows


def normalize_row(raw):
    """Convert a raw spreadsheet row dict to our canonical structure."""
    def get(name):
        v = raw.get(name)
        if v is None:
            return None
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    notes_raw = get("Notes")
    finish_raw = get("Finish / Design Reference")
    question_raw = get("Question for AI Review")

    canonical = {
        "row": raw.get("_row"),
        "section": get("Section"),
        "element_id": get("Element ID"),
        "element_name": get("Element Name"),
        "category": get("Category"),
        "subcategory": get("Subcategory"),
        "floor": get("Floor"),
        "build_phase": get("Build Phase"),
        "generate": get("Generate Geometry?"),
        "required": get("Required?"),
        "geometry_type": get("Geometry Type"),
        "placement": get("Coordinate / Placement Type"),

        "dims": {
            "center_x_grid": safe_float(get("Center X Grid")),
            "center_y_grid": safe_float(get("Center Y Grid")),
            "min_x_grid":    safe_float(get("Min X Grid")),
            "max_x_grid":    safe_float(get("Max X Grid")),
            "min_y_grid":    safe_float(get("Min Y Grid")),
            "max_y_grid":    safe_float(get("Max Y Grid")),
            "length_grid_x": safe_float(get("Length Grid X")),
            "depth_grid_y":  safe_float(get("Depth Grid Y")),
            "length_m":      safe_float(get("Length M")),
            "depth_m":       safe_float(get("Depth M")),
            "base_z_m":      safe_float(get("Base Z M")),
            "top_z_m":       safe_float(get("Top Z M")),
            "height_ft":     safe_float(get("Height Ft")),
            "height_m":      safe_float(get("Height M")),
            "thickness_m":   safe_float(get("Thickness M")),
            "rotation_deg":  safe_float(get("Rotation Deg")),
        },

        "raw_text_dims": {
            "base_z_m_raw":   None if get("Base Z M") is None else str(get("Base Z M")),
            "top_z_m_raw":    None if get("Top Z M") is None else str(get("Top Z M")),
            "height_m_raw":   None if get("Height M") is None else str(get("Height M")),
            "height_ft_raw":  None if get("Height Ft") is None else str(get("Height Ft")),
        },

        "material_type": get("Material Type"),
        "material_placeholder": get("Material Placeholder"),
        "finish_design_reference": scrub_forbidden(finish_raw),
        "glass_solid_open_closed": get("Glass/Solid/Open/Closed"),
        "door_function": get("Door Function"),
        "walkable": get("Walkable?"),
        "collision_needed": get("Collision Needed?"),
        "visibility_needed": get("Visibility Needed?"),
        "f2_slab_relationship": get("F2 Slab Relationship"),
        "alignment_stacking_group": get("Alignment / Stacking Group"),

        "verification": get("Verification Status"),
        "spot_check_id": get("Spot Check ID"),
        "source_consensus": scrub_forbidden(get("Source Consensus")),
        "notes": scrub_forbidden(notes_raw),
        "question_for_ai": scrub_forbidden(question_raw),
    }

    # Compute meter-equivalent of grid coords for convenience.
    g = canonical["dims"]
    canonical["dims_m_from_grid"] = {
        "center_x_m": None if g["center_x_grid"] is None else g["center_x_grid"] * GRID_TO_M,
        "center_y_m": None if g["center_y_grid"] is None else g["center_y_grid"] * GRID_TO_M,
        "min_x_m":    None if g["min_x_grid"] is None    else g["min_x_grid"]    * GRID_TO_M,
        "max_x_m":    None if g["max_x_grid"] is None    else g["max_x_grid"]    * GRID_TO_M,
        "min_y_m":    None if g["min_y_grid"] is None    else g["min_y_grid"]    * GRID_TO_M,
        "max_y_m":    None if g["max_y_grid"] is None    else g["max_y_grid"]    * GRID_TO_M,
        "length_m_from_grid": None if g["length_grid_x"] is None else g["length_grid_x"] * GRID_TO_M,
        "depth_m_from_grid":  None if g["depth_grid_y"] is None else g["depth_grid_y"]  * GRID_TO_M,
    }

    canonical["collection_target"] = collection_for_row(canonical)
    return canonical


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def classify_and_enrich(canonical_rows):
    """Split rows into geometry / materials / rules / spot_checks / skipped,
    enrich polygon rows, segment-list rows, etc."""
    geometry_rows = []
    material_rows = []
    rule_rows = []
    spot_check_rows = []
    skipped_rows = []
    polygon_log = []
    segment_log = []
    non_numeric_log = []

    for r in canonical_rows:
        eid = r.get("element_id")
        if not eid:
            skipped_rows.append({
                "row": r["row"],
                "reason": "no Element ID (section spacer)",
                "section": r.get("section"),
            })
            continue

        # Track non-numeric values that came in as text (Base Z M = 'varies' etc).
        nn = []
        for k, raw in r["raw_text_dims"].items():
            if raw is None:
                continue
            try:
                float(raw)
            except (ValueError, TypeError):
                nn.append({"field": k, "value": raw})
        if nn:
            non_numeric_log.append({
                "row": r["row"], "element_id": eid, "fields": nn,
            })

        sec_prefix = (r.get("section") or "").strip()[:2]
        cat = (r.get("category") or "")

        # Materials (Section 06 + Section 09 material definitions)
        if cat == "Material" or cat == "Material definition":
            material_rows.append(r)
            continue

        # Rules / thickness / height rules
        if cat in {"Rule", "Thickness Rule", "Height Rule"}:
            rule_rows.append(r)
            continue

        # Spot checks
        if cat == "Spot Check":
            spot_check_rows.append(r)
            continue

        # Section-driven non-geometry buckets (catches Section 01 reference-style
        # debug rows so they end up in NIMA_Rules_Materials by collection rule).
        if sec_prefix in NON_GEOMETRY_SECTION_PREFIXES and r.get("generate") in (None, "No"):
            skipped_rows.append({
                "row": r["row"],
                "element_id": eid,
                "reason": f"Section {sec_prefix} non-geometry / Generate=No",
            })
            continue

        generate = r.get("generate")
        if generate in (None, "No"):
            skipped_rows.append({
                "row": r["row"],
                "element_id": eid,
                "reason": "Generate Geometry? = No",
            })
            continue

        # ---- Polygon parsing (Notes-driven) -------------------------------
        gtype = (r.get("geometry_type") or "").lower()
        ptype = (r.get("placement") or "").lower()
        polygon_keywords = (
            "polygon", "rotated rectangle", "verified corners", "see notes",
            "16 unique vertices", "4 user-confirmed", "4 verified",
        )
        # Also try polygon extraction for stacked-footprint rows (cage/plate)
        # whose Notes contain a "[Footprint matches ...]" labeled corner set.
        notes_has_footprint = "[footprint matches" in (
            (r.get("notes") or "").lower()
        )
        if any(k in gtype or k in ptype for k in polygon_keywords) or notes_has_footprint:
            poly = parse_polygon_from_notes(r.get("notes") or "")
            r["polygon_grid"] = poly["polygon_grid"]
            r["polygon_m"] = (
                None if poly["polygon_grid"] is None
                else [[p[0] * GRID_TO_M, p[1] * GRID_TO_M] for p in poly["polygon_grid"]]
            )
            r["polygon_source"] = {
                "priority": poly["selected_priority"],
                "source_phrase": poly["source_phrase"],
                "warnings": poly["warnings"],
            }
            polygon_log.append({
                "row": r["row"],
                "element_id": eid,
                "selected_priority": poly["selected_priority"],
                "source_phrase": poly["source_phrase"],
                "n_vertices": 0 if poly["polygon_grid"] is None else len(poly["polygon_grid"]),
                "all_detected_sets": poly["all_detected_sets"],
                "warnings": poly["warnings"],
            })

        # ---- Segment-list / window-marker parsing (rows 103-105) ----------
        gtype_full = (r.get("geometry_type") or "")
        if gtype_full.lower().startswith("segment list"):
            segs = parse_segment_list_from_notes(r.get("notes") or "")
            r["segments_grid"] = segs
            r["segments_m"] = [
                {
                    "center_m": [s["center_grid"][0] * GRID_TO_M, s["center_grid"][1] * GRID_TO_M],
                    "len_m": s["len_grid"] * GRID_TO_M,
                }
                for s in segs
            ]
            segment_log.append({
                "row": r["row"], "element_id": eid,
                "segments_parsed": len(segs),
            })
        elif "window marker" in gtype_full.lower():
            marks = parse_window_marker_list_from_notes(r.get("notes") or "")
            r["window_markers_grid"] = marks
            r["window_markers_m"] = [
                [p[0] * GRID_TO_M, p[1] * GRID_TO_M] for p in marks
            ]
            segment_log.append({
                "row": r["row"], "element_id": eid,
                "window_markers_parsed": len(marks),
            })

        geometry_rows.append(r)

    return {
        "geometry_rows": geometry_rows,
        "material_rows": material_rows,
        "rule_rows": rule_rows,
        "spot_check_rows": spot_check_rows,
        "skipped_rows": skipped_rows,
        "polygon_log": polygon_log,
        "segment_log": segment_log,
        "non_numeric_log": non_numeric_log,
    }


def build_spawn_record(rows):
    """Promote ZONE_SPAWN to a top-level record for the builder."""
    for r in rows:
        if (r.get("element_id") or "").upper() == "ZONE_SPAWN":
            cx = r["dims"].get("center_x_grid")
            cy = r["dims"].get("center_y_grid")
            return {
                "element_id": r["element_id"],
                "x_grid": cx,
                "y_grid": cy,
                "x_m": None if cx is None else cx * GRID_TO_M,
                "y_m": None if cy is None else cy * GRID_TO_M,
                "facing": "South",
                "row": r["row"],
            }
    return None


def forbidden_term_scan(rows):
    """Scan the canonical (already-scrubbed) text for any forbidden term.
    Should always return zero hits because we scrub during normalize_row()."""
    hits = []
    for r in rows:
        for field in ("element_id", "element_name", "category", "subcategory",
                      "geometry_type", "placement", "material_placeholder",
                      "finish_design_reference", "notes", "source_consensus",
                      "question_for_ai"):
            v = r.get(field)
            if not v:
                continue
            for term in FORBIDDEN_TERMS:
                if term in str(v).lower():
                    hits.append({
                        "row": r["row"], "field": field, "term": term,
                        "value_excerpt": str(v)[:120],
                    })
    return hits


def write_outputs(out_dir, payload, report_lines):
    json_path = os.path.join(out_dir, OUT_JSON_NAME)
    report_path = os.path.join(out_dir, OUT_REPORT_NAME)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")
    return json_path, report_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", default=None,
                    help="Path to NIMA_Phase2_Master_Schedule_LOCKED-*.xlsx")
    ap.add_argument("--out", default=None,
                    help="Output directory (default: same dir as the xlsx)")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    if args.xlsx:
        xlsx_path = args.xlsx
    else:
        # Find the most recent file that begins with the locked prefix.
        prefix = "NIMA_Phase2_Master_Schedule_LOCKED"
        candidates = [
            f for f in os.listdir(here)
            if f.startswith(prefix) and f.lower().endswith(".xlsx")
        ]
        if not candidates:
            raise SystemExit(
                f"ERROR: no '{prefix}*.xlsx' found in {here}. "
                f"Use --xlsx <path>."
            )
        candidates.sort()
        xlsx_path = os.path.join(here, candidates[-1])

    out_dir = args.out or os.path.dirname(os.path.abspath(xlsx_path))

    print(f"Reading {xlsx_path}")
    sheet_names, headers, raw_rows = read_workbook(xlsx_path)
    print(f"Sheets: {sheet_names}")
    print(f"Header row {HEADER_ROW}: {len(headers)} columns detected")
    print(f"Non-empty data rows: {len(raw_rows)}")

    canonical = [normalize_row(r) for r in raw_rows]
    classified = classify_and_enrich(canonical)
    spawn = build_spawn_record(canonical)
    forbidden_hits = forbidden_term_scan(classified["geometry_rows"])

    payload = {
        "meta": {
            "source_xlsx": os.path.basename(xlsx_path),
            "sheet_used": "Master Build List",
            "sheets_in_workbook": sheet_names,
            "header_row": HEADER_ROW,
            "scale_grid_to_m": GRID_TO_M,
            "axes": "+X East, -X West, +Y North, -Y South, +Z Up",
            "expected_collections": [
                "NIMA_F1", "NIMA_F2", "NIMA_HighBay", "NIMA_Roof",
                "NIMA_Spawn", "NIMA_Debug_Reference", "NIMA_Rules_Materials",
            ],
            "f2_overlook_open_edge_rule_row": "RULE_F2_STAIR_TO_OVERLOOK_OPEN",
            "default_thicknesses_m": {
                "wall": 0.15, "glass": 0.05, "door_panel": 0.05,
                "overhead_door_panel": 0.06, "debug_plane": 0.02,
            },
        },
        "spawn": spawn,
        "geometry_rows": classified["geometry_rows"],
        "material_rows": classified["material_rows"],
        "rule_rows": classified["rule_rows"],
        "spot_check_rows": classified["spot_check_rows"],
        "skipped_rows": classified["skipped_rows"],
        "validation": {
            "n_data_rows": len(raw_rows),
            "n_geometry_rows": len(classified["geometry_rows"]),
            "n_material_rows": len(classified["material_rows"]),
            "n_rule_rows": len(classified["rule_rows"]),
            "n_spot_check_rows": len(classified["spot_check_rows"]),
            "n_skipped_rows": len(classified["skipped_rows"]),
            "n_polygon_rows_attempted": len(classified["polygon_log"]),
            "n_polygon_rows_succeeded": sum(
                1 for p in classified["polygon_log"] if p["n_vertices"] >= 3
            ),
            "polygon_log": classified["polygon_log"],
            "segment_log": classified["segment_log"],
            "non_numeric_in_numeric_columns": classified["non_numeric_log"],
            "forbidden_term_hits_in_output": forbidden_hits,
            "forbidden_terms_check_passed": (len(forbidden_hits) == 0),
        },
    }

    # ------------------------ Validation report ----------------------------
    rep = []
    rep.append("NIMA Phase II — Extractor Validation Report")
    rep.append("=" * 60)
    rep.append(f"Source XLSX:       {payload['meta']['source_xlsx']}")
    rep.append(f"Sheet used:        {payload['meta']['sheet_used']}")
    rep.append(f"Sheets found:      {sheet_names}")
    rep.append(f"Header row:        {HEADER_ROW}")
    rep.append(f"Columns detected:  {len(headers)}")
    rep.append(f"Non-empty rows:    {len(raw_rows)}")
    rep.append("")
    v = payload["validation"]
    rep.append(f"Geometry rows:     {v['n_geometry_rows']}")
    rep.append(f"Material rows:     {v['n_material_rows']}")
    rep.append(f"Rule rows:         {v['n_rule_rows']}")
    rep.append(f"Spot-check rows:   {v['n_spot_check_rows']}")
    rep.append(f"Skipped rows:      {v['n_skipped_rows']}")
    rep.append("")

    # Generate Geometry breakdown across geometry rows
    gen_counter = Counter(r.get("generate") for r in classified["geometry_rows"])
    rep.append("Generate Geometry? (geometry rows only):")
    for k, n in gen_counter.most_common():
        rep.append(f"  {n:3d}  {k}")
    rep.append("")

    # Verification breakdown across geometry rows
    ver_counter = Counter(r.get("verification") for r in classified["geometry_rows"])
    rep.append("Verification Status (geometry rows only):")
    for k, n in ver_counter.most_common():
        rep.append(f"  {n:3d}  {k}")
    rep.append("")

    rep.append("Polygon rows:")
    rep.append(f"  attempted:  {v['n_polygon_rows_attempted']}")
    rep.append(f"  succeeded:  {v['n_polygon_rows_succeeded']}")
    for p in classified["polygon_log"]:
        rep.append(f"  - row {p['row']:3d} {p['element_id']}: "
                   f"priority={p['selected_priority']} "
                   f"verts={p['n_vertices']} "
                   f"src={p['source_phrase']!r}")
        if p["warnings"]:
            for w in p["warnings"]:
                rep.append(f"      WARNING: {w}")
    rep.append("")

    rep.append("Segment-list / window-marker rows:")
    for s in classified["segment_log"]:
        kvs = {k: v_ for k, v_ in s.items() if k not in ("row", "element_id")}
        rep.append(f"  - row {s['row']} {s['element_id']}: {kvs}")
    rep.append("")

    rep.append("Non-numeric values in numeric columns:")
    if not classified["non_numeric_log"]:
        rep.append("  (none)")
    for nn in classified["non_numeric_log"]:
        rep.append(f"  - row {nn['row']} {nn['element_id']}: {nn['fields']}")
    rep.append("")

    rep.append("Skipped rows:")
    skip_reason_counter = Counter(s.get("reason") for s in classified["skipped_rows"])
    for k, n in skip_reason_counter.most_common():
        rep.append(f"  {n:3d}  {k}")
    rep.append("")

    rep.append("Spawn record:")
    rep.append(f"  {spawn}")
    rep.append("")

    rep.append("Forbidden building elements check:")
    rep.append(f"  hits in output JSON: {len(forbidden_hits)}")
    if forbidden_hits:
        for h in forbidden_hits:
            rep.append(f"    row {h['row']} field {h['field']}: {h['value_excerpt']}")
        rep.append("  Forbidden building elements check: FAILED")
    else:
        rep.append("  Forbidden building elements check: passed.")
    rep.append("")

    rep.append("Generated stair geometry: not created (import-only zones only).")
    rep.append("Generated reception/casework: not created.")
    rep.append("Exterior / site / parking / landscaping: not created.")
    rep.append("")

    rep.append("Scale sanity check:")
    rep.append(f"  1 grid = {GRID_TO_M} m  (locked).")
    rep.append("")

    rep.append("Done.")
    rep_text = "\n".join(rep)

    json_path, report_path = write_outputs(out_dir, payload, rep)
    print(f"\nWrote {json_path}")
    print(f"Wrote {report_path}\n")
    print(rep_text)


if __name__ == "__main__":
    main()
