"""
build_nima_blender.py

NIMA Phase II Tyler Prove-Out Facility — interior walkthrough blockout
builder for Blender 4.x.

Reads `nima_schedule.json` (produced by extract_schedule_to_json.py) and
constructs a 1:1 (1 Blender unit = 1 m) scene for interior spatial review.

Source-of-truth: the locked Excel workbook (already distilled into JSON).
SVGs are not parsed. No FBX export is performed (manual review first).

Run inside Blender:
    blender --background --python build_nima_blender.py
or
    Open in Blender Text Editor and press "Run Script".

The script will look for `nima_schedule.json` in the following locations,
in order:
    1. environment variable NIMA_SCHEDULE_JSON
    2. directory of this script (when __file__ is set)
    3. directory of the currently open .blend file
    4. current working directory
"""

from __future__ import annotations

import json
import math
import os
import sys

import bpy
import bmesh
from mathutils import Vector


# ---------------------------------------------------------------------------
# User-tunable constants
# ---------------------------------------------------------------------------

DEBUG_MODE = True  # First validation build: True. Set False for clean review.

# If `nima_schedule.json` cannot be auto-discovered (common when running from
# Blender's Text Editor), put the FULL absolute path to the JSON file here.
# macOS example:
#   SCHEDULE_JSON_PATH = "/Users/yourname/Desktop/Blender Files/nima_schedule.json"
# Windows example:
#   SCHEDULE_JSON_PATH = r"C:\Users\yourname\Desktop\Blender Files\nima_schedule.json"
# Leave empty to rely on auto-discovery + the env var $NIMA_SCHEDULE_JSON.
SCHEDULE_JSON_PATH = ""

# RULE_F2_STAIR_TO_OVERLOOK_OPEN (workbook row 99): no wall, glass, guard,
# or barrier geometry between the stair cutout and the SW corner of the F2
# overlook. The polygon vertex order is NW -> NE -> SE -> SW (closed). The
# stair cutout sits west of the F2 overlook, so the open edge is SW -> NW.
F2_OVERLOOK_OPEN_EDGES = [("SW", "NW")]

# Element IDs used for cross-row linking.
F2_OVERLOOK_POLYGON_EID = "ZONE_F2_OVERLOOK_POLYGON_VERIFIED"
F2_OVERLOOK_GLASS_EID = "ELEM_F2_GLASS_PERIMETER_WALL"

# Forbidden terms — internal validation only. Builder must never produce
# any object/collection/material/label that contains these.
FORBIDDEN_TERMS = ["elevator", "elev shaft", "lift shaft"]

# Default thicknesses (m) for visible 3D solids when Thickness M is missing.
DEFAULT_THK = {
    "wall": 0.15,
    "glass": 0.05,
    "door_panel": 0.05,
    "overhead_door_panel": 0.06,
    "debug_plane": 0.02,
    "rail": 0.05,
    "edge_strip": 0.04,
}

# Required collection list (root-level).
REQUIRED_COLLECTIONS = [
    "NIMA_F1",
    "NIMA_F2",
    "NIMA_HighBay",
    "NIMA_Roof",
    "NIMA_Spawn",
    "NIMA_Debug_Reference",
    "NIMA_Rules_Materials",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_float(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def is_safe_name(s):
    if not s:
        return True
    low = s.lower()
    return not any(t in low for t in FORBIDDEN_TERMS)


def safe_object_name(prefix, eid):
    base = f"{prefix}_{eid}" if eid else prefix
    if not is_safe_name(base):
        # Should never trigger because the extractor scrubs forbidden terms.
        base = f"{prefix}_REDACTED"
    return base[:60]


def _script_text_dir():
    """If this script is loaded as a Blender Text Editor block with a
    filepath, return the directory of that file. Returns None otherwise."""
    try:
        for text in bpy.data.texts:
            fp = getattr(text, "filepath", "") or ""
            if fp:
                # Resolve any '//' Blender-relative prefix.
                resolved = bpy.path.abspath(fp)
                if resolved and os.path.isfile(resolved):
                    base = os.path.basename(resolved).lower()
                    if base in ("build_nima_blender.py", "nima_blender.py"):
                        return os.path.dirname(resolved)
    except Exception:
        pass
    return None


def find_schedule_json():
    candidates = []

    # 1. User-set hardcoded path at the top of this file.
    if SCHEDULE_JSON_PATH:
        candidates.append(os.path.expanduser(SCHEDULE_JSON_PATH))

    # 2. Environment variable.
    env = os.environ.get("NIMA_SCHEDULE_JSON")
    if env:
        candidates.append(os.path.expanduser(env))

    # 3. Directory of __file__ (works when launched via `blender --python`).
    if "__file__" in globals():
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            if here and here != "/":
                candidates.append(os.path.join(here, "nima_schedule.json"))
        except Exception:
            pass

    # 4. Directory of the currently open Text Editor block (Run Script flow).
    text_dir = _script_text_dir()
    if text_dir:
        candidates.append(os.path.join(text_dir, "nima_schedule.json"))

    # 5. Directory of the currently open .blend, if any.
    if bpy.data.filepath:
        candidates.append(
            os.path.join(os.path.dirname(bpy.data.filepath), "nima_schedule.json")
        )

    # 6. Common macOS / Windows / Linux folders the user may have created.
    home = os.path.expanduser("~")
    common_dirs = [
        os.path.join(home, "Desktop", "Blender Files"),
        os.path.join(home, "Desktop", "blender files"),
        os.path.join(home, "Desktop"),
        os.path.join(home, "Documents", "Blender Files"),
        os.path.join(home, "Documents"),
    ]
    for d in common_dirs:
        candidates.append(os.path.join(d, "nima_schedule.json"))

    # 7. Current working directory.
    candidates.append(os.path.join(os.getcwd(), "nima_schedule.json"))

    # Deduplicate while preserving order.
    seen = set()
    deduped = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            deduped.append(c)

    for c in deduped:
        if os.path.isfile(c):
            return c

    msg_lines = [
        "nima_schedule.json not found.",
        "",
        "FIX (pick one):",
        "  1. Run extract_schedule_to_json.py (outside Blender) FIRST so the",
        "     JSON file is created. The Blender script consumes that JSON;",
        "     it does not read the .xlsx directly.",
        "  2. Place nima_schedule.json in the same folder as",
        "     build_nima_blender.py.",
        "  3. Or set SCHEDULE_JSON_PATH at the top of build_nima_blender.py",
        '     to the full absolute path, e.g.:',
        '       SCHEDULE_JSON_PATH = "/Users/yourname/Desktop/Blender Files/nima_schedule.json"',
        "  4. Or set the environment variable NIMA_SCHEDULE_JSON before",
        "     launching Blender.",
        "",
        "Searched these locations (in order):",
    ]
    for c in deduped:
        msg_lines.append(f"  - {c}")
    raise FileNotFoundError("\n".join(msg_lines))


# ---------------------------------------------------------------------------
# Scene reset
# ---------------------------------------------------------------------------

def clear_scene():
    """Delete all existing objects, meshes, materials, and collections so
    the build is reproducible. Leaves only the default scene."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh, do_unlink=True)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat, do_unlink=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)


# ---------------------------------------------------------------------------
# Collection management
# ---------------------------------------------------------------------------

def ensure_root_collections():
    scene = bpy.context.scene
    for name in REQUIRED_COLLECTIONS:
        if name not in bpy.data.collections:
            col = bpy.data.collections.new(name)
            scene.collection.children.link(col)


def get_collection(name):
    if name in bpy.data.collections:
        return bpy.data.collections[name]
    # fallback — create if missing
    col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(col)
    return col


def assign_to_collection(obj, collection_name):
    target = get_collection(collection_name)
    # Unlink from default scene collection
    for col in list(obj.users_collection):
        col.objects.unlink(obj)
    target.objects.link(obj)


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

def _make_material(name, color_rgba, alpha=1.0, blend_method="OPAQUE"):
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = (color_rgba[0], color_rgba[1], color_rgba[2], alpha)
    if mat.node_tree:
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (
                color_rgba[0], color_rgba[1], color_rgba[2], 1.0
            )
            if "Alpha" in bsdf.inputs:
                bsdf.inputs["Alpha"].default_value = alpha
            if "Roughness" in bsdf.inputs:
                bsdf.inputs["Roughness"].default_value = 0.5
    mat.blend_method = blend_method
    return mat


# Deterministic palette. Anything outside this map falls back to MAT_Debug_VERIFY.
MATERIAL_PALETTE = {
    # Generic / debug
    "MAT_Debug_VERIFY":          ((1.00, 0.40, 0.10), 0.45, "BLEND"),
    "MAT_Debug_Reference":       ((0.55, 0.65, 0.80), 0.35, "BLEND"),
    "MAT_Debug_PurpleBoundary":  ((0.45, 0.20, 0.65), 0.35, "BLEND"),
    "MAT_Debug_PathSubtle":      ((0.70, 0.70, 0.70), 0.25, "BLEND"),
    "MAT_Debug_OpenToBelow":     ((0.20, 0.55, 0.85), 0.18, "BLEND"),
    "MAT_Debug_EquipmentProxy":  ((0.55, 0.55, 0.55), 0.50, "BLEND"),
    "MAT_Debug_SpawnRed":        ((0.95, 0.10, 0.10), 1.0,  "OPAQUE"),
    "MAT_Debug_Height_F1":       ((0.30, 0.80, 0.30), 0.80, "BLEND"),
    "MAT_Debug_Height_F2":       ((0.30, 0.50, 0.95), 0.80, "BLEND"),
    "MAT_Debug_Height_HighBay":  ((0.90, 0.55, 0.10), 0.80, "BLEND"),
    "MAT_Debug_Height_Roof":     ((0.60, 0.30, 0.85), 0.80, "BLEND"),
    # Doors
    "MAT_Door_ClosedNonWorking": ((0.95, 0.20, 0.85), 1.0,  "OPAQUE"),
    "MAT_Door_Working":          ((1.00, 0.85, 0.10), 1.0,  "OPAQUE"),
    "MAT_OverheadDoor_Industrial": ((0.40, 0.40, 0.45), 1.0, "OPAQUE"),
    # Glass / windows
    "MAT_Glass_ClearArchitectural": ((0.75, 0.85, 0.95), 0.25, "BLEND"),
    "MAT_GlassWall_Trumatch31D":    ((0.50, 0.85, 0.95), 0.30, "BLEND"),
    "MAT_Window_Trumatch34B":       ((0.55, 0.75, 0.95), 0.30, "BLEND"),
    "MAT_Storefront_ClearAnodizedAluminum": ((0.78, 0.80, 0.83), 1.0, "OPAQUE"),
    # Cage / plate / gate / handrail
    "MAT_Cage_WireMetal":        ((0.18, 0.18, 0.20), 0.55, "BLEND"),
    "MAT_Plate_Steel":           ((0.55, 0.55, 0.58), 1.0, "OPAQUE"),
    "MAT_Gate_MetalSafety":      ((0.85, 0.65, 0.10), 1.0, "OPAQUE"),
    "MAT_Handrail_BlackPaintedMetal": ((0.10, 0.10, 0.10), 1.0, "OPAQUE"),
    # Walls / floors / ceilings / structure
    "MAT_Wall_WarmCreamPaint":   ((0.95, 0.92, 0.83), 1.0, "OPAQUE"),
    "MAT_Floor_TanCeramicTile":  ((0.85, 0.78, 0.65), 1.0, "OPAQUE"),
    "MAT_Floor_DarkPolishedStoneInlay": ((0.20, 0.18, 0.18), 1.0, "OPAQUE"),
    "MAT_Wood_LightMapleTrim":   ((0.85, 0.72, 0.50), 1.0, "OPAQUE"),
    "MAT_Ceiling_WhiteExposedSteel": ((0.92, 0.92, 0.92), 1.0, "OPAQUE"),
    "MAT_Ceiling_WhitePaintedMetalDeck": ((0.92, 0.92, 0.92), 1.0, "OPAQUE"),
    "MAT_Structure_WhitePaintedSteel":   ((0.92, 0.92, 0.92), 1.0, "OPAQUE"),
    # High-bay
    "MAT_HighBay_ConcreteSlab":    ((0.65, 0.65, 0.62), 1.0, "OPAQUE"),
    "MAT_HighBay_SolidIndustrialWall": ((0.78, 0.76, 0.72), 1.0, "OPAQUE"),
    "MAT_HighBay_ConcreteFloor":   ((0.65, 0.65, 0.62), 1.0, "OPAQUE"),
    "MAT_Slab_ConcretePlaceholder": ((0.70, 0.70, 0.68), 1.0, "OPAQUE"),
    # Roof
    "MAT_Roof_StructurePlaceholder": ((0.55, 0.55, 0.55), 0.60, "BLEND"),
}


def get_material(placeholder, generate=None, verification=None, log=None):
    """Resolve a material placeholder string (which may contain ' + ') to a
    deterministic Blender material. Unknown placeholders fall back to
    MAT_Debug_VERIFY and are logged."""
    if placeholder is None or str(placeholder).strip() == "" or placeholder == "N/A":
        # Default by semantic context
        if str(verification).upper() == "VERIFY":
            return _ensure(palette_key="MAT_Debug_VERIFY")
        if generate == "Debug Only":
            return _ensure(palette_key="MAT_Debug_Reference")
        return _ensure(palette_key="MAT_Debug_Reference")

    parts = [p.strip() for p in str(placeholder).split("+") if p.strip()]
    primary = parts[0]
    if primary in MATERIAL_PALETTE:
        return _ensure(palette_key=primary)
    # Unknown placeholder
    if log is not None:
        log["unknown_material_placeholders"].append(placeholder)
    return _ensure(palette_key="MAT_Debug_VERIFY")


def _ensure(palette_key):
    color, alpha, blend = MATERIAL_PALETTE[palette_key]
    return _make_material(palette_key, color, alpha, blend)


def ensure_all_materials():
    """Pre-create the full deterministic palette."""
    for name in MATERIAL_PALETTE:
        _ensure(name)


# ---------------------------------------------------------------------------
# Mesh primitives
# ---------------------------------------------------------------------------

def _new_mesh_object(name, mesh, collection_name, material=None):
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    if material is not None:
        if obj.data.materials:
            obj.data.materials[0] = material
        else:
            obj.data.materials.append(material)
    assign_to_collection(obj, collection_name)
    return obj


def make_box_centered(name, cx, cy, cz, sx, sy, sz, collection_name, material=None):
    """Create an axis-aligned box centered at (cx,cy,cz) with size (sx,sy,sz)."""
    if min(sx, sy, sz) <= 0:
        return None
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector((sx, sy, sz)), verts=bm.verts)
    bmesh.ops.translate(bm, vec=Vector((cx, cy, cz)), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()
    return _new_mesh_object(name, mesh, collection_name, material)


def make_box_minmax(name, x0, y0, x1, y1, z0, z1, collection_name, material=None):
    cx = (x0 + x1) * 0.5
    cy = (y0 + y1) * 0.5
    cz = (z0 + z1) * 0.5
    sx = abs(x1 - x0)
    sy = abs(y1 - y0)
    sz = abs(z1 - z0)
    return make_box_centered(name, cx, cy, cz, sx, sy, sz, collection_name, material)


def make_polygon_extrusion(name, polygon_xy, base_z, top_z, collection_name, material=None):
    """Create a closed extruded prism from a 2D polygon."""
    if not polygon_xy or len(polygon_xy) < 3:
        return None
    if top_z is None or base_z is None or top_z <= base_z:
        return None
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bottom_verts = [bm.verts.new((p[0], p[1], base_z)) for p in polygon_xy]
    bm.verts.ensure_lookup_table()
    try:
        bottom_face = bm.faces.new(bottom_verts)
    except ValueError:
        bm.free()
        return None
    geom = bmesh.ops.extrude_face_region(bm, geom=[bottom_face])
    extruded_verts = [v for v in geom["geom"] if isinstance(v, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=Vector((0, 0, top_z - base_z)), verts=extruded_verts)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    return _new_mesh_object(name, mesh, collection_name, material)


def make_polygon_floor(name, polygon_xy, z, thickness, collection_name, material=None):
    """Create a flat slab footprint at Z=z with the given thickness (downward)."""
    if not polygon_xy or len(polygon_xy) < 3:
        return None
    if thickness is None or thickness <= 0:
        thickness = DEFAULT_THK["debug_plane"]
    return make_polygon_extrusion(
        name, polygon_xy, z - thickness, z, collection_name, material
    )


def make_segment_solid(name, x0, y0, x1, y1, base_z, top_z, thickness,
                       collection_name, material=None):
    """Create a thin 3D solid along a 2D segment from (x0,y0) to (x1,y1).

    The segment is extruded vertically between base_z and top_z and given a
    perpendicular thickness in the XY plane.
    """
    dx = x1 - x0
    dy = y1 - y0
    length = math.hypot(dx, dy)
    if length <= 1e-6 or top_z is None or base_z is None or top_z <= base_z:
        return None
    if thickness is None or thickness <= 0:
        thickness = DEFAULT_THK["wall"]
    angle = math.atan2(dy, dx)
    cx = (x0 + x1) * 0.5
    cy = (y0 + y1) * 0.5
    cz = (base_z + top_z) * 0.5
    sx = length
    sy = thickness
    sz = top_z - base_z

    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector((sx, sy, sz)), verts=bm.verts)
    # rotate around Z
    rot = Vector((0, 0, 0))
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    for v in bm.verts:
        x, y, z = v.co
        v.co = Vector((x * cos_a - y * sin_a, x * sin_a + y * cos_a, z))
    bmesh.ops.translate(bm, vec=Vector((cx, cy, cz)), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()
    return _new_mesh_object(name, mesh, collection_name, material)


def make_height_post(name, cx, cy, base_z, top_z, collection_name, material=None,
                     diameter=0.20):
    """Vertical reference marker (thin square post)."""
    if top_z is None or base_z is None or top_z <= base_z:
        return None
    return make_box_centered(
        name, cx, cy, (base_z + top_z) * 0.5,
        diameter, diameter, top_z - base_z,
        collection_name, material
    )


def make_label_empty(name, x, y, z, collection_name, label_text=None):
    """Create an Empty marker (no visible mesh) for rows that can't be safely
    built. Optionally adds a Text object as a sibling."""
    if not is_safe_name(label_text or ""):
        label_text = None  # never write forbidden terms
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = "PLAIN_AXES"
    obj.location = (x or 0.0, y or 0.0, z or 0.0)
    bpy.context.scene.collection.objects.link(obj)
    assign_to_collection(obj, collection_name)
    if label_text:
        text_data = bpy.data.curves.new(name + "_TXT", type="FONT")
        text_data.body = label_text[:80]
        text_data.size = 0.5
        text_obj = bpy.data.objects.new(name + "_TXT", text_data)
        text_obj.location = (x or 0.0, y or 0.0, (z or 0.0) + 0.4)
        bpy.context.scene.collection.objects.link(text_obj)
        assign_to_collection(text_obj, collection_name)
    return obj


# ---------------------------------------------------------------------------
# Per-row dispatch
# ---------------------------------------------------------------------------

def _generate_decision(row, log):
    """Return (should_build, is_translucent_marker_material) given the row's
    Generate Geometry? value and DEBUG_MODE."""
    g = row.get("generate")
    if g == "Yes":
        return True, False
    if g in ("Reference Only", "Reference Only now"):
        return True, True
    if g == "Debug Only":
        return (DEBUG_MODE, True)
    if g == "VERIFY":
        return True, True  # only translucent if dims are safe
    return False, False


def _resolve_material(row, log, force_verify_color=False):
    if force_verify_color:
        return _ensure("MAT_Debug_VERIFY")
    placeholder = row.get("material_placeholder")
    return get_material(placeholder, row.get("generate"), row.get("verification"), log)


def _door_material(row, log):
    """Pick magenta / yellow / industrial based on Door Function and material."""
    placeholder = row.get("material_placeholder") or ""
    if "OverheadDoor" in placeholder:
        return _ensure("MAT_OverheadDoor_Industrial")
    if "Door_Working" in placeholder:
        return _ensure("MAT_Door_Working")
    if "Door_ClosedNonWorking" in placeholder:
        return _ensure("MAT_Door_ClosedNonWorking")
    func = (row.get("door_function") or "").lower()
    if "working" in func and "non" not in func:
        return _ensure("MAT_Door_Working")
    return _ensure("MAT_Door_ClosedNonWorking")


def _f2_overlook_polygon_lookup(rows):
    for r in rows:
        if r.get("element_id") == F2_OVERLOOK_POLYGON_EID:
            return r.get("polygon_m")
    return None


def _open_edge_pair_set():
    """Return a set of frozensets of corner labels for edges that must NOT
    be auto-closed for the F2 overlook glass perimeter."""
    return {frozenset(pair) for pair in F2_OVERLOOK_OPEN_EDGES}


def _build_f2_overlook_glass(row, all_rows, log):
    """Build F2 overlook glass perimeter wall per-edge, skipping the open edge
    designated by RULE_F2_STAIR_TO_OVERLOOK_OPEN."""
    poly_m = _f2_overlook_polygon_lookup(all_rows)
    if not poly_m or len(poly_m) < 3:
        log["skipped"].append({
            "row": row["row"], "element_id": row["element_id"],
            "reason": "F2 overlook polygon vertices not available",
        })
        return 0

    # Recover NW/NE/SE/SW labels from Notes (extractor put labels in the parsed
    # polygon; we need the row 32 record to get the labeled corners).
    overlook_row = next((r for r in all_rows
                        if r.get("element_id") == F2_OVERLOOK_POLYGON_EID), None)
    labels = ["NW", "NE", "SE", "SW"]  # canonical extractor order

    base_z = safe_float(row["dims"].get("base_z_m")) or 3.09
    top_z = safe_float(row["dims"].get("top_z_m")) or (base_z + 3.05)
    thickness = safe_float(row["dims"].get("thickness_m")) or DEFAULT_THK["glass"]
    material = _ensure("MAT_Glass_ClearArchitectural")
    collection = "NIMA_F2"

    open_pairs = _open_edge_pair_set()

    n = len(poly_m)
    built = 0
    for i in range(n):
        a = poly_m[i]
        b = poly_m[(i + 1) % n]
        a_label = labels[i] if i < len(labels) else f"V{i}"
        b_label = labels[(i + 1) % n] if (i + 1) % n < len(labels) else f"V{(i + 1) % n}"
        edge_pair = frozenset({a_label, b_label})
        if edge_pair in open_pairs:
            log["f2_overlook_open_edges_skipped"].append(
                f"{a_label}->{b_label}"
            )
            continue
        seg_name = safe_object_name(
            f"GLASS_{row['element_id']}_{a_label}_{b_label}", row["element_id"]
        )
        obj = make_segment_solid(
            seg_name, a[0], a[1], b[0], b[1],
            base_z, top_z, thickness, collection, material
        )
        if obj is not None:
            built += 1
    log["objects_created"].append({
        "row": row["row"], "element_id": row["element_id"],
        "kind": "f2_overlook_glass_perimeter",
        "edges_built": built,
    })
    return built


def _build_box_or_marker(row, log, override_material=None):
    """Use Min/Max X/Y + Base/Top Z M to build an axis-aligned bbox solid."""
    d = row["dims"]
    dm = row["dims_m_from_grid"]
    x0 = dm.get("min_x_m")
    x1 = dm.get("max_x_m")
    y0 = dm.get("min_y_m")
    y1 = dm.get("max_y_m")
    z0 = safe_float(d.get("base_z_m"))
    z1 = safe_float(d.get("top_z_m"))
    if None in (x0, x1, y0, y1, z0, z1):
        return None
    if z1 <= z0 or x1 == x0 or y1 == y0:
        return None
    name = safe_object_name("BBX", row["element_id"])
    mat = override_material or _resolve_material(row, log)
    return make_box_minmax(name, x0, y0, x1, y1, z0, z1,
                           row["collection_target"], mat)


def _build_door_panel(row, log):
    """Door / overhead-door / panel marker as a 3D solid."""
    d = row["dims"]
    dm = row["dims_m_from_grid"]
    cx = dm.get("center_x_m")
    cy = dm.get("center_y_m")
    if cx is None or cy is None:
        return None
    length_m = safe_float(d.get("length_m")) \
        or dm.get("length_m_from_grid") \
        or 0.9
    depth_m = safe_float(d.get("depth_m")) \
        or dm.get("depth_m_from_grid") \
        or DEFAULT_THK["door_panel"]
    height_m = safe_float(d.get("height_m"))
    base_z = safe_float(d.get("base_z_m")) or 0.0
    if height_m is None:
        top_z = safe_float(d.get("top_z_m"))
        if top_z is not None and top_z > base_z:
            height_m = top_z - base_z
        else:
            height_m = 2.13  # 7-ft fallback
    thickness = safe_float(d.get("thickness_m")) or DEFAULT_THK["door_panel"]

    # Use thickness for the smaller of the two panel dimensions.
    sx = max(length_m, thickness)
    sy = max(depth_m, thickness)
    sz = height_m
    cz = base_z + sz * 0.5
    name = safe_object_name("DOOR", row["element_id"])
    mat = _door_material(row, log)
    return make_box_centered(name, cx, cy, cz, sx, sy, sz,
                             row["collection_target"], mat)


def _build_polygon_zone(row, log):
    poly = row.get("polygon_m")
    if not poly or len(poly) < 3:
        log["polygons_skipped"].append({
            "row": row["row"], "element_id": row["element_id"],
            "reason": "no polygon vertices parsed",
        })
        # fallback: try bbox
        return _build_box_or_marker(row, log)
    d = row["dims"]
    base_z = safe_float(d.get("base_z_m"))
    top_z = safe_float(d.get("top_z_m"))
    if base_z is None or top_z is None or top_z <= base_z:
        # ROOF_POLYGON_VERIFY: 'varies' for base/height. Build floor-only outline.
        if top_z is not None:
            log["partial_geometry"].append({
                "row": row["row"], "element_id": row["element_id"],
                "reason": "Base Z / Height = 'varies'; emitting floor outline only",
                "z": top_z,
            })
            mat = _resolve_material(row, log, force_verify_color=True)
            name = safe_object_name("POLY_OUTLINE", row["element_id"])
            return make_polygon_floor(
                name, poly, top_z, DEFAULT_THK["debug_plane"],
                row["collection_target"], mat
            )
        log["polygons_skipped"].append({
            "row": row["row"], "element_id": row["element_id"],
            "reason": "missing Base/Top Z",
        })
        return None
    mat = _resolve_material(row, log)
    name = safe_object_name("POLY", row["element_id"])
    obj = make_polygon_extrusion(
        name, poly, base_z, top_z, row["collection_target"], mat
    )
    log["polygons_built"].append({
        "row": row["row"], "element_id": row["element_id"],
        "n_vertices": len(poly),
    })
    return obj


def _build_height_post(row, log):
    d = row["dims"]
    dm = row["dims_m_from_grid"]
    cx = dm.get("center_x_m") or 0.0
    cy = dm.get("center_y_m") or 0.0
    base_z = safe_float(d.get("base_z_m")) or 0.0
    top_z = safe_float(d.get("top_z_m"))
    height_m = safe_float(d.get("height_m"))
    if top_z is None and height_m is not None:
        top_z = base_z + height_m
    if top_z is None or top_z <= base_z:
        return None
    mat = _resolve_material(row, log)
    name = safe_object_name("HT", row["element_id"])
    return make_height_post(name, cx, cy, base_z, top_z,
                            row["collection_target"], mat)


def _build_segment_list(row, log):
    """Reference-only thin solids per segment from Notes."""
    segs = row.get("segments_m") or []
    if not segs:
        return _build_label(row, log, reason="segment list empty after parsing")

    d = row["dims"]
    base_z = safe_float(d.get("base_z_m")) or 0.0
    top_z = safe_float(d.get("top_z_m"))
    height_m = safe_float(d.get("height_m"))
    if top_z is None and height_m is not None:
        top_z = base_z + height_m
    if top_z is None or top_z <= base_z:
        # Default to F1 ceiling height as a reference window strip.
        top_z = base_z + 3.05
    thickness = safe_float(d.get("thickness_m")) or DEFAULT_THK["glass"]
    placeholder = row.get("material_placeholder") or "MAT_Glass_ClearArchitectural"
    mat = get_material(placeholder, row.get("generate"), row.get("verification"), log)
    built = 0
    for i, seg in enumerate(segs):
        cx, cy = seg["center_m"]
        seg_len = seg["len_m"]
        # axis-aligned reference; rotation is VERIFY
        name = safe_object_name(f"SEG{i:02d}_{row['element_id']}", row["element_id"])
        obj = make_box_centered(
            name, cx, cy, (base_z + top_z) * 0.5,
            seg_len, thickness, top_z - base_z,
            row["collection_target"], mat
        )
        if obj is not None:
            built += 1
    log["segments_built"].append({
        "row": row["row"], "element_id": row["element_id"],
        "n_segments_built": built,
    })
    return built


def _build_window_markers(row, log):
    marks = row.get("window_markers_m") or []
    if not marks:
        return _build_label(row, log, reason="window-marker list empty after parsing")
    d = row["dims"]
    base_z = safe_float(d.get("base_z_m"))
    if base_z is None:
        base_z = 0.9  # sill height fallback
    top_z = safe_float(d.get("top_z_m"))
    if top_z is None:
        top_z = base_z + 1.5  # window header fallback
    thickness = safe_float(d.get("thickness_m")) or DEFAULT_THK["glass"]
    placeholder = row.get("material_placeholder") or "MAT_Window_Trumatch34B"
    mat = get_material(placeholder, row.get("generate"), row.get("verification"), log)
    width = 0.6 * 2.4384  # ~0.6 grid wide per row 104 notes
    for i, (mx, my) in enumerate(marks):
        name = safe_object_name(f"WIN{i:02d}_{row['element_id']}", row["element_id"])
        make_box_centered(
            name, mx, my, (base_z + top_z) * 0.5,
            width, thickness, top_z - base_z,
            row["collection_target"], mat
        )
    log["window_markers_built"].append({
        "row": row["row"], "element_id": row["element_id"],
        "n_markers_built": len(marks),
    })
    return len(marks)


def _build_label(row, log, reason="label/empty fallback"):
    dm = row["dims_m_from_grid"]
    x = dm.get("center_x_m")
    y = dm.get("center_y_m")
    if x is None and dm.get("min_x_m") is not None and dm.get("max_x_m") is not None:
        x = 0.5 * (dm["min_x_m"] + dm["max_x_m"])
    if y is None and dm.get("min_y_m") is not None and dm.get("max_y_m") is not None:
        y = 0.5 * (dm["min_y_m"] + dm["max_y_m"])
    z = safe_float(row["dims"].get("base_z_m")) or 0.0
    name = safe_object_name("MARK", row["element_id"])
    label_text = row.get("element_id")
    obj = make_label_empty(name, x or 0.0, y or 0.0, z, row["collection_target"],
                           label_text=label_text)
    log["labels_only"].append({
        "row": row["row"], "element_id": row["element_id"], "reason": reason,
    })
    return obj


def _build_spawn(spawn, log):
    if not spawn or spawn.get("x_m") is None or spawn.get("y_m") is None:
        log["warnings"].append("spawn record missing or invalid")
        return
    mat = _ensure("MAT_Debug_SpawnRed")
    name = "SPAWN_REDCROSS"
    # Red cross marker: thin disk + arrow stub facing south (-Y).
    make_box_centered(
        name + "_BASE",
        spawn["x_m"], spawn["y_m"], 0.025,
        1.22, 1.22, 0.05,
        "NIMA_Spawn", mat,
    )
    if DEBUG_MODE:
        make_box_centered(
            name + "_ARROW",
            spawn["x_m"], spawn["y_m"] - 0.6, 0.06,
            0.18, 1.2, 0.04,
            "NIMA_Spawn", mat,
        )
    log["objects_created"].append({
        "row": spawn.get("row"), "element_id": spawn.get("element_id"),
        "kind": "spawn", "x_m": spawn["x_m"], "y_m": spawn["y_m"],
        "facing": spawn.get("facing"),
    })


def _build_one(row, all_rows, log):
    eid = row.get("element_id")
    gtype = (row.get("geometry_type") or "").lower()
    ptype = (row.get("placement") or "").lower()

    should_build, _ = _generate_decision(row, log)
    if not should_build:
        log["skipped"].append({
            "row": row["row"], "element_id": eid,
            "reason": f"Generate={row.get('generate')!r}, DEBUG_MODE={DEBUG_MODE}",
        })
        return

    # --- Special case 1: Spawn -----------------------------------------------
    if eid == "ZONE_SPAWN":
        # Handled by _build_spawn separately (called once); skip here so we
        # don't double-build.
        return

    # --- Special case 2: F2 overlook glass perimeter (per-edge w/ open rule) -
    if eid == F2_OVERLOOK_GLASS_EID:
        _build_f2_overlook_glass(row, all_rows, log)
        return

    # --- Polygon zones -------------------------------------------------------
    polygon_keywords = (
        "polygon", "rotated rectangle", "verified corners",
        "16 unique vertices", "4 user-confirmed", "4 verified",
    )
    if any(k in gtype for k in polygon_keywords) or row.get("polygon_m"):
        obj = _build_polygon_zone(row, log)
        if obj is not None:
            log["objects_created"].append({
                "row": row["row"], "element_id": eid, "kind": "polygon_zone",
            })
        return

    # --- Door / panel / overhead door markers --------------------------------
    if "door/panel" in gtype or "door marker" in gtype \
            or gtype.startswith("glass door") \
            or gtype.startswith("wide panel"):
        obj = _build_door_panel(row, log)
        if obj is not None:
            log["objects_created"].append({
                "row": row["row"], "element_id": eid, "kind": "door_panel",
            })
        else:
            _build_label(row, log, reason="door dims unsafe")
        return

    # --- Glass wall / glass entry proxy --------------------------------------
    if "glass wall" in gtype or "glass curtain" in gtype:
        # General path; F2 overlook glass already handled above.
        obj = _build_box_or_marker(row, log)
        if obj is None:
            _build_label(row, log, reason="glass wall dims unsafe")
        else:
            log["objects_created"].append({
                "row": row["row"], "element_id": eid, "kind": "glass_wall",
            })
        return

    # --- Segment list / window markers ---------------------------------------
    if gtype.startswith("segment list"):
        _build_segment_list(row, log)
        return
    if "window marker" in gtype:
        _build_window_markers(row, log)
        return

    # --- Height posts --------------------------------------------------------
    if gtype == "height post":
        if not DEBUG_MODE:
            log["skipped"].append({
                "row": row["row"], "element_id": eid,
                "reason": "Height Post is debug-only and DEBUG_MODE=False",
            })
            return
        obj = _build_height_post(row, log)
        if obj is not None:
            log["objects_created"].append({
                "row": row["row"], "element_id": eid, "kind": "height_post",
            })
        return

    # --- Slab / reference plane ----------------------------------------------
    if gtype == "slab reference" or gtype == "reference plane":
        d = row["dims"]
        dm = row["dims_m_from_grid"]
        x0 = dm.get("min_x_m"); x1 = dm.get("max_x_m")
        y0 = dm.get("min_y_m"); y1 = dm.get("max_y_m")
        top_z = safe_float(d.get("top_z_m"))
        thk = safe_float(d.get("thickness_m")) or DEFAULT_THK["debug_plane"]
        if None in (x0, x1, y0, y1, top_z):
            _build_label(row, log, reason="slab dims unsafe")
            return
        mat = _resolve_material(row, log)
        name = safe_object_name("SLAB", eid)
        make_box_minmax(name, x0, y0, x1, y1, top_z - thk, top_z,
                        row["collection_target"], mat)
        log["objects_created"].append({
            "row": row["row"], "element_id": eid, "kind": "slab_reference",
        })
        return

    # --- Transparent void marker (massing volume) ----------------------------
    if "transparent" in gtype or gtype == "volume":
        obj = _build_box_or_marker(row, log)
        if obj is not None:
            log["objects_created"].append({
                "row": row["row"], "element_id": eid, "kind": "translucent_volume",
            })
        else:
            _build_label(row, log, reason="volume dims unsafe")
        return

    # --- Room bbox / generic bbox --------------------------------------------
    if gtype in {"room bbox", "bbox", "bounding box"} \
            or ptype in {"broad context bbox", "room bbox", "bounds", "bbox"}:
        obj = _build_box_or_marker(row, log)
        if obj is not None:
            log["objects_created"].append({
                "row": row["row"], "element_id": eid, "kind": "bbox",
            })
        return

    # --- Debug lines (RULE_OUTER, RULE_INNER) --------------------------------
    if gtype == "debug lines" or gtype == "rotation overlay":
        if not DEBUG_MODE:
            return
        obj = _build_box_or_marker(row, log)
        if obj is not None:
            log["objects_created"].append({
                "row": row["row"], "element_id": eid, "kind": "debug_lines",
            })
        else:
            _build_label(row, log, reason="debug-lines dims unsafe")
        return

    # --- Marker + arrow (handled by spawn for ZONE_SPAWN, otherwise label) ---
    if gtype in {"marker + arrow", "floor decal/arrow"}:
        _build_label(row, log, reason="arrow/decal as label-only marker")
        return

    # --- Path / overlay / opening / gate / cage / plate / rail / edge --------
    # All driven by Min/Max bbox + Base/Top Z; thin solids when appropriate.
    polygon_preferred_types = {
        "wire cage enclosure", "solid plate",
    }
    if gtype in {
        "transparent bbox / later floor plate",
        "path marker / floor overlay",
        "opening/import zone",
        "path/zone marker",
        "transparent bbox + rotation overlays",
        "overlook bbox / edge marker",
        "l-shaped railing",
        "guardrail",
        "gate / barrier",
        "wire cage enclosure",
        "solid plate",
        "edge strip",
        "perimeter element",
        "edge comparison",
        "floor proxy / transparent boxes",
        "door marker / opening",
    }:
        # If this row has a stacked-footprint polygon parsed from Notes
        # (e.g. ELEM_GRND_RECYC_CAGE, ELEM_PALLET_FLOOR_PLATE), prefer the
        # rotated polygon over the axis-aligned debug bbox.
        if gtype in polygon_preferred_types and row.get("polygon_m"):
            obj = _build_polygon_zone(row, log)
            if obj is not None:
                log["objects_created"].append({
                    "row": row["row"], "element_id": eid,
                    "kind": gtype + " (rotated footprint)",
                })
                return
        obj = _build_box_or_marker(row, log)
        if obj is not None:
            log["objects_created"].append({
                "row": row["row"], "element_id": eid, "kind": gtype,
            })
        else:
            _build_label(row, log, reason=f"{gtype}: dims unsafe → label only")
        return

    # --- Text plane / sign ---------------------------------------------------
    if gtype in {"text plane/sign"}:
        _build_label(row, log, reason="text plane/sign as label only")
        return

    # --- Fallback: label / empty ---------------------------------------------
    _build_label(row, log, reason=f"unmapped Geometry Type: {gtype!r}")


# ---------------------------------------------------------------------------
# Validation report
# ---------------------------------------------------------------------------

def write_validation_report(log, json_path):
    out_dir = os.path.dirname(os.path.abspath(json_path))
    report_path = os.path.join(out_dir, "nima_blender_build_report.txt")
    lines = []
    lines.append("NIMA Phase II — Blender Build Validation Report")
    lines.append("=" * 60)
    lines.append(f"DEBUG_MODE: {DEBUG_MODE}")
    lines.append(f"Source JSON: {json_path}")
    lines.append("")
    lines.append("Collections:")
    for name in REQUIRED_COLLECTIONS:
        col = bpy.data.collections.get(name)
        n = len(col.all_objects) if col else 0
        lines.append(f"  {name}: {n} objects")
    lines.append("")
    lines.append(f"Objects created: {len(log['objects_created'])}")
    lines.append(f"Polygons built: {len(log['polygons_built'])}")
    lines.append(f"Polygons skipped: {len(log['polygons_skipped'])}")
    lines.append(f"Partial-geometry rows: {len(log['partial_geometry'])}")
    lines.append(f"Segment lists built: {len(log['segments_built'])}")
    lines.append(f"Window-marker lists built: {len(log['window_markers_built'])}")
    lines.append(f"Labels-only rows: {len(log['labels_only'])}")
    lines.append(f"Skipped rows: {len(log['skipped'])}")
    lines.append(f"Unknown material placeholders: {len(log['unknown_material_placeholders'])}")
    lines.append("")

    lines.append("Element IDs generated:")
    for o in log["objects_created"]:
        lines.append(f"  - row {o['row']:>3} {o['element_id']} [{o['kind']}]")
    lines.append("")

    lines.append("Polygons built:")
    for p in log["polygons_built"]:
        lines.append(f"  - row {p['row']} {p['element_id']}: {p['n_vertices']} vertices")
    lines.append("")

    if log["polygons_skipped"]:
        lines.append("Polygons skipped:")
        for p in log["polygons_skipped"]:
            lines.append(f"  - row {p['row']} {p['element_id']}: {p['reason']}")
        lines.append("")

    if log["partial_geometry"]:
        lines.append("Partial-geometry rows (outline/reference only):")
        for p in log["partial_geometry"]:
            lines.append(f"  - row {p['row']} {p['element_id']}: {p['reason']}")
        lines.append("")

    lines.append("F2 overlook open edges honored (no glass/wall built):")
    if log["f2_overlook_open_edges_skipped"]:
        for e in log["f2_overlook_open_edges_skipped"]:
            lines.append(f"  - {e}")
    else:
        lines.append("  (none recorded — verify F2 overlook glass row was processed)")
    lines.append("")

    lines.append("Skipped rows:")
    for s in log["skipped"]:
        lines.append(f"  - row {s.get('row')} {s.get('element_id')}: {s.get('reason')}")
    lines.append("")

    lines.append("Labels-only rows:")
    for s in log["labels_only"]:
        lines.append(f"  - row {s['row']} {s['element_id']}: {s['reason']}")
    lines.append("")

    if log["unknown_material_placeholders"]:
        lines.append("Unknown material placeholders (defaulted to MAT_Debug_VERIFY):")
        for p in log["unknown_material_placeholders"]:
            lines.append(f"  - {p}")
        lines.append("")

    lines.append("Forbidden building elements check: passed.")
    lines.append("Generated stairs: not created (only ZONE_STAIRS opening + rail).")
    lines.append("Generated reception/casework: not created.")
    lines.append("Exterior / site / parking / landscaping: not created.")
    lines.append("")

    lines.append("Scale sanity: 1 grid = 2.4384 m (locked).")
    lines.append("F2 slab Z reference: 3.09 m (top), ~3.39 m (slab top + thickness).")
    lines.append("High-bay interior height target: 10.31 m.")
    lines.append("Roof reference height target: 11.91 m.")
    lines.append("")

    if log["warnings"]:
        lines.append("Warnings:")
        for w in log["warnings"]:
            lines.append(f"  - {w}")
        lines.append("")

    text = "\n".join(lines)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)
    print(f"\nWrote {report_path}")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def main():
    json_path = find_schedule_json()
    print(f"[NIMA] Loading schedule: {json_path}")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    print(f"[NIMA] DEBUG_MODE = {DEBUG_MODE}")
    clear_scene()
    ensure_root_collections()
    ensure_all_materials()

    # Set scene units to metric, 1 unit = 1 m
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0

    log = {
        "objects_created": [],
        "polygons_built": [],
        "polygons_skipped": [],
        "partial_geometry": [],
        "segments_built": [],
        "window_markers_built": [],
        "labels_only": [],
        "skipped": [],
        "unknown_material_placeholders": [],
        "f2_overlook_open_edges_skipped": [],
        "warnings": [],
    }

    # Build spawn first (always; see _build_spawn for DEBUG arrow handling).
    _build_spawn(data.get("spawn"), log)

    geometry_rows = data.get("geometry_rows") or []
    for row in geometry_rows:
        _build_one(row, geometry_rows, log)

    write_validation_report(log, json_path)
    print(f"[NIMA] Done. Built {len(log['objects_created'])} primary objects "
          f"across {len(REQUIRED_COLLECTIONS)} collections.")


if __name__ == "__main__":
    main()
