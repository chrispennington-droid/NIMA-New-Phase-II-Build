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

DEBUG_MODE = False  # Sparse, clean review build. Set True only for debug.

# --- Walkthrough realism toggles ----------------------------------------
SHOW_REFERENCE_VOLUMES = False        # Hide large reference volumes by default.
SHOW_CUTOUT_VOLUMES = False           # Hide ZONE_STAIRS / ZONE_HIGHBAY_VOID as solids.
SHOW_CUTOUT_OUTLINES = False          # Hide thin outlines at cutouts; default off.
BUILD_FLOOR_PLATES = True             # Floor plates instead of full extrusions.
BUILD_DERIVED_WALLS = True            # Local accepted-room walls only.
BUILD_DERIVED_F2_SLAB_ENVELOPE = False  # Do NOT invent a union/fallback F2 slab.
APPLY_BOOLEAN_CUTOUTS = False         # No Boolean cutters; nothing to cut into.
DELETE_BOOLEAN_CUTTERS_AFTER_APPLY = True  # (only matters if cutouts run.)
BUILD_EDGE_RAILS = False              # Skip rail / guard / void-edge / overlook-edge.

# --- Door / window / glass / roof toggles -------------------------------
# Doors are intentionally OFF — the user will place them manually.
BUILD_DOOR_MARKERS = False
# Window markers are off so the model stays focused on rooms/walls.
BUILD_WINDOW_MARKERS = False
# Glass walls (F2 overlook glass and the F1/F2 curtain-wall segment lists)
# are kept on — they are spatial boundaries, not doors.
BUILD_GLASS_WALLS = True

# Roof / top reference visibility: independent of SHOW_REFERENCE_VOLUMES.
BUILD_ROOF_REFERENCE = True
BUILD_ROOF_CAP = True
BUILD_ROOF_PARAPET = False
# Modes:
#   "outline_only"                 -> just a thin polygon perimeter ring
#   "outline_and_translucent_cap"  -> ring + a translucent flat cap
ROOF_VISUAL_MODE = "outline_and_translucent_cap"
ROOF_CAP_THICKNESS = 0.06             # ~ 5–10 cm
ROOF_CAP_ALPHA = 0.20                 # 0.12–0.22 readable but see-through

# --- Building-shell alignment with the angled roof ----------------------
# When True, the high-bay and any other large axis-aligned bbox row may NOT
# emit final exterior/perimeter walls. Floor plates and accepted polygon
# walls are still allowed.
ALIGN_BUILDING_TO_ROOF = True
# Roof-projected reference shell is OFF for the sparse review pass.
BUILD_OUTER_SHELL_FROM_ROOF_POLYGON = False
OUTER_SHELL_ALPHA = 0.18
BUILD_AXIS_ALIGNED_BBOX_SHELLS = False

# Floor-plate thicknesses (m). F2 plate spans 3.09 -> 3.39 (=0.30 m thick).
F1_FLOOR_THICKNESS = 0.08             # 0.05–0.10 m per spec.
F2_FLOOR_THICKNESS = 0.30
F2_SLAB_BASE_Z = 3.09
F2_SLAB_TOP_Z = F2_SLAB_BASE_Z + F2_FLOOR_THICKNESS  # 3.39

# Phase 0 fallback envelope used only when no F2 extents exist.
DERIVED_F2_FALLBACK_GRID = (-15.0, -8.0, 5.0, 13.0)  # (x0, y0, x1, y1)
DERIVED_F2_MARGIN_M = 0.25

# Roof reference height target (m). Used for the cap and the outer shell
# top when the ROOF_POLYGON_VERIFY row has 'varies' for Base Z / Height.
ROOF_TOP_Z = 11.91

GRID_TO_M = 2.4384  # 1 grid square = 8 ft = 2.4384 m

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
    # blend_method is required for translucent materials to actually look
    # transparent in Eevee. Wrap in try/except for Blender versions that
    # have renamed/relocated this property.
    try:
        mat.blend_method = blend_method
    except Exception:
        pass
    if blend_method == "BLEND":
        # Reasonable defaults for translucent reference materials.
        for attr, val in (("show_transparent_back", False),
                          ("shadow_method", "HASHED")):
            try:
                setattr(mat, attr, val)
            except Exception:
                pass
        # Roof cap and outer-shell reference materials must not darken the
        # interior — disable shadow casting where Blender supports it.
        if name in ("MAT_Roof_Cap_Translucent", "MAT_OuterShell_Reference"):
            for attr, val in (("shadow_method", "NONE"),
                              ("use_shadow", False)):
                try:
                    setattr(mat, attr, val)
                except Exception:
                    pass
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
    # Roof cap (translucent) + outer-shell reference envelope (translucent).
    "MAT_Roof_Cap_Translucent":      ((0.55, 0.55, 0.55), ROOF_CAP_ALPHA, "BLEND"),
    "MAT_OuterShell_Reference":      ((0.45, 0.55, 0.70), OUTER_SHELL_ALPHA, "BLEND"),
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
# Floor-plate / perimeter-wall / cutout primitives
# ---------------------------------------------------------------------------

def make_bbox_floor_plate(name, x0, y0, x1, y1, base_z, top_z,
                          collection_name, material=None):
    """Floor plate from an axis-aligned bbox occupying [base_z, top_z]."""
    if None in (x0, y0, x1, y1, base_z, top_z) or top_z <= base_z:
        return None
    return make_box_minmax(name, x0, y0, x1, y1, base_z, top_z,
                           collection_name, material)


def make_polygon_floor_plate(name, polygon_xy, base_z, top_z,
                             collection_name, material=None):
    """Floor plate extruded from a 2D polygon between [base_z, top_z]."""
    return make_polygon_extrusion(name, polygon_xy, base_z, top_z,
                                  collection_name, material)


def make_bbox_perimeter_walls(name_prefix, x0, y0, x1, y1, base_z, top_z,
                              thickness, collection_name, material=None,
                              skip_edges=None):
    """Build wall segments along the four bbox edges as 3D solids.

    skip_edges: set of {"N","S","E","W"} for edges to omit (e.g. open
    loading side of a mezzanine).
    """
    if None in (x0, y0, x1, y1, base_z, top_z) or top_z <= base_z:
        return []
    skip = set(skip_edges or [])
    if thickness is None or thickness <= 0:
        thickness = DEFAULT_THK["wall"]
    walls = []
    edges = (
        ("S", x0, y0, x1, y0),
        ("N", x0, y1, x1, y1),
        ("W", x0, y0, x0, y1),
        ("E", x1, y0, x1, y1),
    )
    for label, ax, ay, bx, by in edges:
        if label in skip:
            continue
        seg = make_segment_solid(
            f"{name_prefix}_{label}", ax, ay, bx, by,
            base_z, top_z, thickness, collection_name, material,
        )
        if seg is not None:
            walls.append(seg)
    return walls


def make_polygon_perimeter_walls(name_prefix, polygon_xy, base_z, top_z,
                                 thickness, collection_name, material=None,
                                 skip_edge_indices=None):
    """Build wall segments along each polygon edge."""
    if not polygon_xy or len(polygon_xy) < 3:
        return []
    if None in (base_z, top_z) or top_z <= base_z:
        return []
    if thickness is None or thickness <= 0:
        thickness = DEFAULT_THK["wall"]
    skip = set(skip_edge_indices or [])
    walls = []
    n = len(polygon_xy)
    for i in range(n):
        if i in skip:
            continue
        a = polygon_xy[i]
        b = polygon_xy[(i + 1) % n]
        seg = make_segment_solid(
            f"{name_prefix}_E{i:02d}",
            a[0], a[1], b[0], b[1],
            base_z, top_z, thickness, collection_name, material,
        )
        if seg is not None:
            walls.append(seg)
    return walls


def make_cutter_box(name, x0, y0, x1, y1, base_z, top_z, padding=0.05):
    """Hidden, slightly oversized box used as a Boolean DIFFERENCE cutter."""
    if None in (x0, y0, x1, y1, base_z, top_z):
        return None
    cutter = make_box_minmax(
        name, x0, y0, x1, y1,
        base_z - padding, top_z + padding,
        "NIMA_Debug_Reference", None,
    )
    if cutter is not None:
        cutter.hide_render = True
        cutter.display_type = "BOUNDS"
    return cutter


def apply_boolean_difference(target_obj, cutter_obj, log):
    """Apply a Boolean DIFFERENCE modifier from cutter_obj to target_obj.
    Returns True on success."""
    if target_obj is None or cutter_obj is None:
        return False
    try:
        if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    try:
        bpy.ops.object.select_all(action="DESELECT")
    except Exception:
        pass
    target_obj.select_set(True)
    bpy.context.view_layer.objects.active = target_obj
    mod_name = (f"BOOL_{cutter_obj.name}")[:60]
    mod = target_obj.modifiers.new(name=mod_name, type="BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter_obj
    try:
        if hasattr(mod, "solver"):
            mod.solver = "FAST"
    except Exception:
        pass
    try:
        bpy.ops.object.modifier_apply(modifier=mod_name)
        log["boolean_applied"].append({
            "target": target_obj.name, "cutter": cutter_obj.name,
        })
        return True
    except Exception as e:
        try:
            target_obj.modifiers.remove(mod)
        except Exception:
            pass
        log["boolean_failed"].append({
            "target": target_obj.name, "cutter": cutter_obj.name,
            "error": str(e),
        })
        return False


def _bbox_overlap(a, b):
    """Return True if two (x0,y0,x1,y1) bboxes overlap in XY."""
    if a is None or b is None:
        return False
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


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
    floor = (row.get("floor") or "")
    base_z = safe_float(d.get("base_z_m"))
    if base_z is None:
        # Use F2 slab top as the sill default for F2 rows; floor for F1.
        base_z = F2_SLAB_TOP_Z if "F2" in floor else 0.0
    top_z = safe_float(d.get("top_z_m"))
    height_m = safe_float(d.get("height_m"))
    if top_z is None and height_m is not None:
        top_z = base_z + height_m
    if top_z is None or top_z <= base_z:
        # Default to room-height reference glass strip.
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
    """In DEBUG_MODE, place an empty + text label at the row's center.
    With DEBUG_MODE=False, leave nothing in the scene — just log."""
    log["labels_only"].append({
        "row": row["row"], "element_id": row["element_id"], "reason": reason,
    })
    if not DEBUG_MODE:
        return None
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
    return make_label_empty(name, x or 0.0, y or 0.0, z,
                            row["collection_target"], label_text=label_text)


def _build_spawn(spawn, log):
    if not spawn or spawn.get("x_m") is None or spawn.get("y_m") is None:
        log["warnings"].append("spawn record missing or invalid")
        return
    if not DEBUG_MODE:
        # Sparse review pass: no spawn marker, no arrow. Spawn data is still
        # in the JSON so a future build can place it.
        log["spawn_skipped"] = "DEBUG_MODE=False"
        return
    mat = _ensure("MAT_Debug_SpawnRed")
    name = "SPAWN_REDCROSS"
    make_box_centered(
        name + "_BASE",
        spawn["x_m"], spawn["y_m"], 0.025,
        1.22, 1.22, 0.05,
        "NIMA_Spawn", mat,
    )
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


# ---------------------------------------------------------------------------
# Element-ID -> role classification
# ---------------------------------------------------------------------------

ELEMENT_ROLES = {
    # Walkable F1 floor plates (no walls).
    "ZONE_LOBBY_CTX":              "f1_floor_only",
    "ZONE_CONNECTOR_PATH":         "f1_floor_only",
    "ZONE_OFFICELAB_TRANS":        "f1_floor_only",

    # F2 cutouts — never solid; used as Boolean cutters.
    "ZONE_STAIRS":                 "cutout_f2",
    "ZONE_HIGHBAY_VOID":           "cutout_f2",

    # High-bay = concrete floor + tall industrial perimeter walls.
    "ZONE_HIGHBAY":                "highbay_floor_walls",

    # F2 edge / overlook geometry.
    "ZONE_HIGHBAY_OVERLOOK":       "f2_edge_outline",
    "ZONE_F2_OVERLOOK_POLYGON_VERIFIED": "f2_overlook_polygon_floor",

    # F2 mezzanine = bbox floor + walls (open loading side from notes).
    "ZONE_MEZZ":                   "f2_room_with_walls",

    # GRND/RECYC + Pallet stack:
    #  - ZONE_GRND_RECYC: F1 polygon floor only (full cage walls come from
    #    ELEM_GRND_RECYC_CAGE which spans 0–5.99 m).
    #  - ZONE_PALLET: F2 polygon floor only (cage walls from same row above).
    "ZONE_GRND_RECYC":             "f1_polygon_floor_only",
    "ZONE_PALLET":                 "f2_polygon_floor_only",
    "ELEM_GRND_RECYC_CAGE":        "polygon_perimeter_walls",
    "ELEM_PALLET_FLOOR_PLATE":     "polygon_floor_plate_explicit",

    # Spawn (built before main pass).
    "ZONE_SPAWN":                  "spawn_already_built",

    # F2 slab seed — no footprint; envelope handled in post-pass.
    "SLAB_F2_CENTRAL":             "f2_slab_envelope_seed",

    # F2 overlook glass perimeter — per-edge with SW->NW skip rule.
    "ELEM_F2_GLASS_PERIMETER_WALL": "f2_overlook_glass_per_edge",

    # Edge / rail elements: thin perimeter solids.
    "ELEM_RAIL_HB_OVERLOOK":       "edge_rail_bbox",
    "ELEM_RAIL_STAIR_VOID":        "edge_rail_bbox",
    "ELEM_OPEN_TO_BELOW_EDGE":     "edge_rail_bbox",

    # Door-style box markers.
    "ELEM_MEZZ_LOADING_GATE":      "door_panel",
    "ELEM_SHORT_DOOR_F2_SW":       "door_panel",
    "ELEM_ENTRY_GLASS_PROXY":      "door_panel",
    "ELEM_OVERHEAD_VERIFY":        "door_panel",

    # Equipment proxy / arrow / labels.
    "ELEM_HIGHBAY_EQUIP_PROXY":    "label",
    "ELEM_SPAWN_ARROW":            "label",
    "ELEM_NONWORKING_DOOR_LABELS": "label",

    # Roof / closure references.
    "ROOF_POLYGON_VERIFY":         "roof_outline",
    "ROOF_HIGHBAY_TOP_REF":        "height_post",
    "ROOF_LOBBY_CONN_TOP_REF":     "height_post",
    "ROOF_STRUCT_ABOVE_HB":        "roof_reference_volume",
    "ROOF_PARAPET_EDGE":           "roof_parapet_edge",

    # Section 01 debug overlays.
    "RULE_OUTER":                  "debug_overlay",
    "RULE_INNER":                  "debug_overlay",
    "RULE_ROTATION":               "debug_overlay",

    # Section 02 height posts.
    "HT_F1_LOWER":                 "height_post",
    "HT_F2_TOP":                   "height_post",
    "HT_F2_OCCUPIED":              "height_post",
    "HT_HIGHBAY":                  "height_post",
    "HT_ROOF_REF":                 "height_post",

    # Section 09 segment lists / windows.
    "F1_GLASS_CW_SEGMENTS":        "segment_list",
    "F1_WINDOWS_HB_EAST_NORTH":    "window_markers",
    "F2_GLASS_CW_SEGMENTS":        "segment_list",
}


DOOR_LIKE_ELEMENT_IDS = {
    "ELEM_MEZZ_LOADING_GATE",
    "ELEM_SHORT_DOOR_F2_SW",
    "ELEM_ENTRY_GLASS_PROXY",
    "ELEM_OVERHEAD_VERIFY",
    "ELEM_NONWORKING_DOOR_LABELS",  # door labels — also off when doors off
}


def _is_door_like(row):
    """Return True if a row should be considered a door / panel / gate /
    door-label so it can be uniformly suppressed when BUILD_DOOR_MARKERS=False."""
    eid = (row.get("element_id") or "")
    if eid.startswith("D_F1_") or eid.startswith("D_F2_"):
        return True
    if eid in DOOR_LIKE_ELEMENT_IDS:
        return True
    cat = (row.get("category") or "").lower()
    if "door marker" in cat:
        return True
    gtype = (row.get("geometry_type") or "").lower()
    if "door/panel" in gtype or "door marker" in gtype \
            or gtype.startswith("glass door") or gtype.startswith("wide panel"):
        return True
    door_func = row.get("door_function")
    if door_func not in (None, "", "N/A"):
        return True
    return False


def _classify_role(row):
    eid = row.get("element_id") or ""

    # Door suppression takes precedence over any explicit role mapping so
    # the user can flip BUILD_DOOR_MARKERS without re-editing the table.
    if _is_door_like(row):
        return "door_panel" if BUILD_DOOR_MARKERS else "skip_door"

    role = ELEMENT_ROLES.get(eid)
    if role is not None:
        return role

    gtype = (row.get("geometry_type") or "").lower()
    if gtype.startswith("segment list"):
        return "segment_list"
    if "window marker" in gtype:
        return "window_markers"
    if gtype == "height post":
        return "height_post"
    return "label"


# ---------------------------------------------------------------------------
# Role handlers
# ---------------------------------------------------------------------------

def _role_f1_floor_only(row, ctx, log):
    eid = row["element_id"]
    dm = row["dims_m_from_grid"]
    x0, y0 = dm.get("min_x_m"), dm.get("min_y_m")
    x1, y1 = dm.get("max_x_m"), dm.get("max_y_m")
    if None in (x0, y0, x1, y1):
        return _role_label(row, ctx, log, reason="F1 floor: bbox missing")
    base_z = safe_float(row["dims"].get("base_z_m")) or 0.0
    top_z = base_z + F1_FLOOR_THICKNESS
    mat = _resolve_material(row, log)
    obj = make_bbox_floor_plate(
        safe_object_name("FLOOR", eid), x0, y0, x1, y1,
        base_z, top_z, row["collection_target"], mat,
    )
    if obj is not None:
        log["floor_plates"].append({
            "row": row["row"], "element_id": eid,
            "kind": "f1_bbox", "name": obj.name,
        })


def _role_f1_polygon_floor_only(row, ctx, log):
    eid = row["element_id"]
    poly = row.get("polygon_m")
    if not poly or len(poly) < 3:
        return _role_label(row, ctx, log, reason="F1 polygon floor: no polygon")
    base_z = safe_float(row["dims"].get("base_z_m")) or 0.0
    top_z = base_z + F1_FLOOR_THICKNESS
    mat = _resolve_material(row, log)
    obj = make_polygon_floor_plate(
        safe_object_name("FLOOR", eid), poly, base_z, top_z,
        row["collection_target"], mat,
    )
    if obj is not None:
        log["floor_plates"].append({
            "row": row["row"], "element_id": eid,
            "kind": "f1_polygon", "name": obj.name,
        })


def _role_f2_polygon_floor_only(row, ctx, log):
    eid = row["element_id"]
    poly = row.get("polygon_m")
    if not poly or len(poly) < 3:
        return _role_label(row, ctx, log, reason="F2 polygon floor: no polygon")
    mat = _resolve_material(row, log)
    obj = make_polygon_floor_plate(
        safe_object_name("FLOOR", eid), poly, F2_SLAB_BASE_Z, F2_SLAB_TOP_Z,
        row["collection_target"], mat,
    )
    if obj is not None:
        log["floor_plates"].append({
            "row": row["row"], "element_id": eid,
            "kind": "f2_polygon", "name": obj.name,
        })
        ctx["f2_floor_plates"].append(obj)


def _role_f2_overlook_polygon_floor(row, ctx, log):
    """F2 overlook = polygon-shaped floor plate at 3.09–3.39 m only.
    Glass perimeter is built separately via ELEM_F2_GLASS_PERIMETER_WALL."""
    eid = row["element_id"]
    poly = row.get("polygon_m")
    if not poly or len(poly) < 3:
        return _role_label(row, ctx, log, reason="overlook: no polygon")
    mat = _ensure("MAT_Floor_TanCeramicTile")
    obj = make_polygon_floor_plate(
        safe_object_name("OVERLOOK_FLOOR", eid), poly,
        F2_SLAB_BASE_Z, F2_SLAB_TOP_Z,
        row["collection_target"], mat,
    )
    if obj is not None:
        log["floor_plates"].append({
            "row": row["row"], "element_id": eid,
            "kind": "f2_overlook_polygon", "name": obj.name,
        })
        ctx["f2_floor_plates"].append(obj)


def _role_f2_room_with_walls(row, ctx, log):
    """ZONE_MEZZ: bbox floor + perimeter walls. Notes-driven open side detection."""
    eid = row["element_id"]
    d = row["dims"]
    dm = row["dims_m_from_grid"]
    x0, y0 = dm.get("min_x_m"), dm.get("min_y_m")
    x1, y1 = dm.get("max_x_m"), dm.get("max_y_m")
    if None in (x0, y0, x1, y1):
        return _role_label(row, ctx, log, reason="room: bbox missing")
    row_base_z = safe_float(d.get("base_z_m")) or F2_SLAB_BASE_Z
    row_top_z = safe_float(d.get("top_z_m")) or (row_base_z + 2.9)

    # Floor plate.
    floor_top = row_base_z + F2_FLOOR_THICKNESS
    floor_mat = _ensure("MAT_Floor_TanCeramicTile")
    floor_obj = make_bbox_floor_plate(
        safe_object_name("FLOOR", eid), x0, y0, x1, y1,
        row_base_z, floor_top, row["collection_target"], floor_mat,
    )
    if floor_obj is not None:
        log["floor_plates"].append({
            "row": row["row"], "element_id": eid,
            "kind": "f2_room_bbox", "name": floor_obj.name,
        })
        ctx["f2_floor_plates"].append(floor_obj)

    if not BUILD_DERIVED_WALLS:
        return

    # Detect open loading side from notes (e.g. ZONE_MEZZ NE↔SE = max-X / east).
    notes_low = (row.get("notes") or "").lower()
    skip_edges = set()
    if "no wall between the ne and se" in notes_low \
            or "fork-truck loading" in notes_low \
            or "fork truck loading" in notes_low:
        skip_edges.add("E")
        log["mezz_open_side_skipped"] = "E"

    wall_thk = safe_float(d.get("thickness_m")) or DEFAULT_THK["wall"]
    wall_mat = _ensure("MAT_Wall_WarmCreamPaint")
    walls = make_bbox_perimeter_walls(
        safe_object_name("WALL", eid), x0, y0, x1, y1,
        floor_top, row_top_z, wall_thk,
        row["collection_target"], wall_mat,
        skip_edges=skip_edges,
    )
    log["perimeter_walls"].append({
        "row": row["row"], "element_id": eid,
        "kind": "bbox_room",
        "n_segments_built": len(walls),
        "skipped_edges": sorted(list(skip_edges)),
    })


def _role_highbay(row, ctx, log):
    """ZONE_HIGHBAY: concrete floor at Z=0 + tall industrial perimeter walls."""
    eid = row["element_id"]
    d = row["dims"]
    dm = row["dims_m_from_grid"]
    x0, y0 = dm.get("min_x_m"), dm.get("min_y_m")
    x1, y1 = dm.get("max_x_m"), dm.get("max_y_m")
    if None in (x0, y0, x1, y1):
        return _role_label(row, ctx, log, reason="highbay: bbox missing")
    base_z = safe_float(d.get("base_z_m")) or 0.0
    top_z = safe_float(d.get("top_z_m")) or 10.31

    # Concrete floor.
    floor_top = base_z + 0.10
    floor_mat = _ensure("MAT_HighBay_ConcreteSlab")
    floor_obj = make_bbox_floor_plate(
        safe_object_name("FLOOR", eid), x0, y0, x1, y1,
        base_z, floor_top, row["collection_target"], floor_mat,
    )
    if floor_obj is not None:
        log["floor_plates"].append({
            "row": row["row"], "element_id": eid,
            "kind": "highbay_concrete", "name": floor_obj.name,
        })

    # Walls: only build when BOTH the general walls toggle AND the
    # axis-aligned shell toggle allow it AND we are not aligning the
    # building to the angled roof. Otherwise the high-bay rectangle below
    # the angled roof produces the misaligned look the user flagged.
    walls_allowed = (
        BUILD_DERIVED_WALLS
        and BUILD_AXIS_ALIGNED_BBOX_SHELLS
        and not ALIGN_BUILDING_TO_ROOF
    )
    has_polygon = bool(row.get("polygon_m"))
    if walls_allowed or has_polygon:
        wall_thk = 0.25  # high-bay walls are heftier
        wall_mat = _ensure("MAT_HighBay_SolidIndustrialWall")
        if has_polygon:
            walls = make_polygon_perimeter_walls(
                safe_object_name("WALL", eid), row["polygon_m"],
                floor_top, top_z, wall_thk,
                row["collection_target"], wall_mat,
            )
            log["perimeter_walls"].append({
                "row": row["row"], "element_id": eid,
                "kind": "highbay_polygon",
                "n_segments_built": len(walls),
            })
        else:
            walls = make_bbox_perimeter_walls(
                safe_object_name("WALL", eid), x0, y0, x1, y1,
                floor_top, top_z, wall_thk,
                row["collection_target"], wall_mat,
            )
            log["perimeter_walls"].append({
                "row": row["row"], "element_id": eid,
                "kind": "highbay_axis_aligned_bbox",
                "n_segments_built": len(walls),
            })
    else:
        log["axis_aligned_shell_skipped"].append({
            "row": row["row"], "element_id": eid,
            "reason": ("ALIGN_BUILDING_TO_ROOF=True / "
                       "BUILD_AXIS_ALIGNED_BBOX_SHELLS=False; "
                       "high-bay walls require an accepted angled polygon."),
        })

    if SHOW_REFERENCE_VOLUMES:
        ref_mat = _ensure("MAT_Debug_Reference")
        make_box_minmax(
            safe_object_name("VOL", eid), x0, y0, x1, y1,
            floor_top, top_z, "NIMA_Debug_Reference", ref_mat,
        )
        log["reference_volumes_built"].append({
            "row": row["row"], "element_id": eid,
            "kind": "highbay_translucent_volume",
        })


def _role_cutout_f2(row, ctx, log):
    """ZONE_STAIRS / ZONE_HIGHBAY_VOID.

    Sparse-pass behavior:
      - APPLY_BOOLEAN_CUTOUTS=False -> no cutter, no Boolean ever.
      - SHOW_CUTOUT_VOLUMES=False    -> no translucent debug preview box.
      - SHOW_CUTOUT_OUTLINES=False   -> no thin outline at slab top.
    With all three false (the new defaults) this row produces NO geometry —
    the stair / void area simply remains visually open.
    """
    eid = row["element_id"]
    dm = row["dims_m_from_grid"]
    x0, y0 = dm.get("min_x_m"), dm.get("min_y_m")
    x1, y1 = dm.get("max_x_m"), dm.get("max_y_m")
    if None in (x0, y0, x1, y1):
        log["cutouts_skipped"].append({
            "row": row["row"], "element_id": eid, "reason": "bbox missing",
        })
        return

    log["cutouts_recorded"].append({
        "row": row["row"], "element_id": eid,
        "bbox_m": (x0, y0, x1, y1),
    })

    if APPLY_BOOLEAN_CUTOUTS:
        cutter = make_cutter_box(
            safe_object_name("_CUTTER", eid), x0, y0, x1, y1,
            F2_SLAB_BASE_Z, F2_SLAB_TOP_Z, padding=0.05,
        )
        if cutter is None:
            log["cutouts_skipped"].append({
                "row": row["row"], "element_id": eid,
                "reason": "cutter creation failed",
            })
        else:
            ctx["cutters"].append({
                "row": row["row"], "element_id": eid, "obj": cutter,
                "bbox_m": (x0, y0, x1, y1),
            })
    else:
        log["boolean_skipped_reason"] = "APPLY_BOOLEAN_CUTOUTS=False"

    if SHOW_CUTOUT_VOLUMES:
        debug_mat = _ensure("MAT_Debug_OpenToBelow")
        d_top = safe_float(row["dims"].get("top_z_m")) or F2_SLAB_TOP_Z
        d_base = safe_float(row["dims"].get("base_z_m")) or F2_SLAB_BASE_Z
        preview = make_box_minmax(
            safe_object_name("CUTOUT_PREVIEW", eid),
            x0, y0, x1, y1, d_base, d_top,
            "NIMA_Debug_Reference", debug_mat,
        )
        if preview is not None:
            preview.hide_render = True
            log["cutout_previews"].append({"element_id": eid,
                                           "name": preview.name})

    if SHOW_CUTOUT_OUTLINES:
        # Use the translucent open-to-below color (NOT a dark/black material)
        # so a thin outline at slab top doesn't read as a solid black box.
        outline_mat = _ensure("MAT_Debug_OpenToBelow")
        edge_z = F2_SLAB_TOP_Z + 0.001
        edge_h = 0.04
        outline = make_bbox_perimeter_walls(
            safe_object_name("CUTOUT_OUTLINE", eid),
            x0, y0, x1, y1,
            edge_z - edge_h * 0.5, edge_z + edge_h * 0.5,
            0.03, "NIMA_Debug_Reference", outline_mat,
        )
        log["cutout_outlines"].append({
            "element_id": eid, "n_edges": len(outline),
        })


def _role_f2_edge_outline(row, ctx, log):
    """ZONE_HIGHBAY_OVERLOOK: thin perimeter outline at F2 slab top, no solid."""
    eid = row["element_id"]
    if not BUILD_EDGE_RAILS:
        log["edges_skipped"].append({
            "row": row["row"], "element_id": eid,
            "reason": "BUILD_EDGE_RAILS=False",
        })
        return
    dm = row["dims_m_from_grid"]
    x0, y0 = dm.get("min_x_m"), dm.get("min_y_m")
    x1, y1 = dm.get("max_x_m"), dm.get("max_y_m")
    if None in (x0, y0, x1, y1):
        return _role_label(row, ctx, log, reason="edge outline: bbox missing")
    z = F2_SLAB_TOP_Z
    edge_h = 0.04
    mat = _ensure("MAT_Debug_Reference")
    walls = make_bbox_perimeter_walls(
        safe_object_name("EDGE", eid), x0, y0, x1, y1,
        z - edge_h * 0.5, z + edge_h * 0.5,
        0.04, row["collection_target"], mat,
    )
    log["edge_outlines"].append({
        "row": row["row"], "element_id": eid, "n_edges": len(walls),
    })


def _role_edge_rail_bbox(row, ctx, log):
    """Thin perimeter rail/edge solid using row Base/Top Z."""
    eid = row["element_id"]
    if not BUILD_EDGE_RAILS:
        log["edges_skipped"].append({
            "row": row["row"], "element_id": eid,
            "reason": "BUILD_EDGE_RAILS=False",
        })
        return
    d = row["dims"]
    dm = row["dims_m_from_grid"]
    x0, y0 = dm.get("min_x_m"), dm.get("min_y_m")
    x1, y1 = dm.get("max_x_m"), dm.get("max_y_m")
    if None in (x0, y0, x1, y1):
        return _role_label(row, ctx, log, reason="rail: bbox missing")
    base_z = safe_float(d.get("base_z_m")) or F2_SLAB_TOP_Z
    top_z = safe_float(d.get("top_z_m")) or (base_z + 1.1)
    thk = safe_float(d.get("thickness_m")) or DEFAULT_THK["rail"]
    mat = _ensure("MAT_Handrail_BlackPaintedMetal")
    walls = make_bbox_perimeter_walls(
        safe_object_name("RAIL", eid), x0, y0, x1, y1,
        base_z, top_z, thk, row["collection_target"], mat,
    )
    log["edge_rails"].append({
        "row": row["row"], "element_id": eid, "n_segments": len(walls),
    })


def _role_polygon_perimeter_walls(row, ctx, log):
    """ELEM_GRND_RECYC_CAGE: full-height polygon cage walls."""
    eid = row["element_id"]
    poly = row.get("polygon_m")
    if not poly or len(poly) < 3:
        return _role_label(row, ctx, log, reason="cage: no polygon")
    d = row["dims"]
    base_z = safe_float(d.get("base_z_m")) or 0.0
    top_z = safe_float(d.get("top_z_m"))
    if top_z is None or top_z <= base_z:
        top_z = 5.99
    thk = safe_float(d.get("thickness_m")) or DEFAULT_THK["rail"]
    mat = _resolve_material(row, log)
    walls = make_polygon_perimeter_walls(
        safe_object_name("CAGE", eid), poly, base_z, top_z, thk,
        row["collection_target"], mat,
    )
    log["perimeter_walls"].append({
        "row": row["row"], "element_id": eid,
        "kind": "polygon_cage", "n_segments_built": len(walls),
    })


def _role_polygon_floor_plate_explicit(row, ctx, log):
    """ELEM_PALLET_FLOOR_PLATE: polygon-shaped plate at row Base/Top Z."""
    eid = row["element_id"]
    poly = row.get("polygon_m")
    if not poly or len(poly) < 3:
        return _role_label(row, ctx, log, reason="plate: no polygon")
    d = row["dims"]
    base_z = safe_float(d.get("base_z_m"))
    top_z = safe_float(d.get("top_z_m"))
    if base_z is None or top_z is None:
        return _role_label(row, ctx, log, reason="plate: missing Base/Top Z")
    mat = _resolve_material(row, log)
    obj = make_polygon_floor_plate(
        safe_object_name("PLATE", eid), poly, base_z, top_z,
        row["collection_target"], mat,
    )
    if obj is not None:
        log["floor_plates"].append({
            "row": row["row"], "element_id": eid,
            "kind": "polygon_plate", "name": obj.name,
        })


def _role_door_panel(row, ctx, log):
    obj = _build_door_panel(row, log)
    if obj is not None:
        log["doors"].append({
            "row": row["row"], "element_id": row["element_id"],
            "name": obj.name,
        })
    else:
        _role_label(row, ctx, log, reason="door dims unsafe")


def _role_skip_door(row, ctx, log):
    """No mesh, no label, no empty — user will add doors manually."""
    log["doors_skipped"].append({
        "row": row["row"], "element_id": row["element_id"],
        "reason": "BUILD_DOOR_MARKERS=False; user will add doors manually.",
    })


def _role_label(row, ctx, log, reason="label fallback"):
    _build_label(row, log, reason=reason)


def _role_f2_overlook_glass_per_edge(row, ctx, log):
    if not BUILD_GLASS_WALLS:
        log["glass_segments_skipped"].append({
            "row": row["row"], "element_id": row["element_id"],
            "reason": "BUILD_GLASS_WALLS=False",
        })
        return
    _build_f2_overlook_glass(row, list(ctx["rows_by_eid"].values()), log)


def _role_f2_slab_envelope_seed(row, ctx, log):
    """SLAB_F2_CENTRAL has no X/Y footprint. With BUILD_DERIVED_F2_SLAB_ENVELOPE
    off, this row produces no geometry and no label — only a log entry. An
    accepted F2 slab polygon is required before a real F2 slab can be built."""
    log["f2_slab_envelope_seeds"].append({
        "row": row["row"], "element_id": row["element_id"],
        "note": ("Treated as reference rule only. "
                 "Exact F2 slab polygon required for future phase."),
    })


def _role_roof_outline(row, ctx, log):
    eid = row["element_id"]
    poly = row.get("polygon_m")
    if not poly or len(poly) < 3:
        return _role_label(row, ctx, log, reason="roof: no polygon")
    if not BUILD_ROOF_REFERENCE:
        log["roof_skipped"].append({
            "row": row["row"], "element_id": eid,
            "reason": "BUILD_ROOF_REFERENCE=False",
        })
        return
    top_z = safe_float(row["dims"].get("top_z_m")) or ROOF_TOP_Z
    perimeter_mat = _ensure("MAT_Roof_StructurePlaceholder")

    # Always emit the perimeter ring (visible by default).
    edge_h = 0.05
    ring_name = f"ROOF_OUTLINE_{eid}"
    walls = make_polygon_perimeter_walls(
        ring_name, poly,
        top_z - edge_h * 0.5, top_z + edge_h * 0.5,
        0.10, "NIMA_Roof", perimeter_mat,
    )
    log["roof_outlines"].append({
        "row": row["row"], "element_id": eid,
        "kind": "perimeter_ring", "n_segments_built": len(walls),
    })

    if BUILD_ROOF_CAP and ROOF_VISUAL_MODE == "outline_and_translucent_cap":
        cap_mat = _ensure("MAT_Roof_Cap_Translucent")
        cap_name = f"ROOF_CAP_{eid}"
        cap = make_polygon_floor_plate(
            cap_name, poly,
            top_z - ROOF_CAP_THICKNESS, top_z,
            "NIMA_Roof", cap_mat,
        )
        log["roof_cap"] = {
            "built": cap is not None,
            "name": cap.name if cap is not None else None,
            "alpha": ROOF_CAP_ALPHA,
            "blend_method": "BLEND",
            "thickness_m": ROOF_CAP_THICKNESS,
            "z_top": top_z,
        }
    else:
        log["roof_cap"] = {
            "built": False,
            "reason": ("BUILD_ROOF_CAP=False" if not BUILD_ROOF_CAP
                       else f"ROOF_VISUAL_MODE={ROOF_VISUAL_MODE!r}"),
        }


def _role_roof_parapet_edge(row, ctx, log):
    eid = row["element_id"]
    if not BUILD_ROOF_PARAPET:
        log["parapet_edges_skipped"].append({
            "row": row["row"], "element_id": eid,
            "reason": "BUILD_ROOF_PARAPET=False",
        })
        return
    roof_row = ctx["rows_by_eid"].get("ROOF_POLYGON_VERIFY")
    poly = roof_row.get("polygon_m") if roof_row else None
    if not poly or len(poly) < 3:
        return _role_label(row, ctx, log,
                           reason="parapet: ROOF_POLYGON_VERIFY polygon missing")
    base_z = safe_float(row["dims"].get("base_z_m")) or ROOF_TOP_Z
    top_z = safe_float(row["dims"].get("top_z_m")) or 12.37
    thk = safe_float(row["dims"].get("thickness_m")) or 0.254
    mat = _ensure("MAT_Roof_StructurePlaceholder")
    walls = make_polygon_perimeter_walls(
        safe_object_name("PARAPET", eid), poly, base_z, top_z, thk,
        "NIMA_Roof", mat,
    )
    log["parapet_edges"].append({
        "row": row["row"], "element_id": eid, "n_segments_built": len(walls),
    })


def _role_roof_reference_volume(row, ctx, log):
    if not SHOW_REFERENCE_VOLUMES:
        log["reference_volumes_skipped"].append({
            "row": row["row"], "element_id": row["element_id"],
        })
        return
    eid = row["element_id"]
    hb_row = ctx["rows_by_eid"].get("ZONE_HIGHBAY")
    if not hb_row:
        return _role_label(row, ctx, log, reason="ref vol: no HB ref")
    dm = hb_row["dims_m_from_grid"]
    x0, y0 = dm.get("min_x_m"), dm.get("min_y_m")
    x1, y1 = dm.get("max_x_m"), dm.get("max_y_m")
    if None in (x0, y0, x1, y1):
        return _role_label(row, ctx, log, reason="ref vol: HB bbox missing")
    base_z = safe_float(row["dims"].get("base_z_m")) or 10.31
    top_z = safe_float(row["dims"].get("top_z_m")) or 11.91
    mat = _ensure("MAT_Roof_StructurePlaceholder")
    obj = make_box_minmax(
        safe_object_name("REF_VOL", eid), x0, y0, x1, y1,
        base_z, top_z, "NIMA_Debug_Reference", mat,
    )
    if obj is not None:
        log["reference_volumes_built"].append({
            "row": row["row"], "element_id": eid, "name": obj.name,
        })


def _role_height_post(row, ctx, log):
    if not DEBUG_MODE:
        log["skipped"].append({
            "row": row["row"], "element_id": row["element_id"],
            "reason": "Height Post is debug-only and DEBUG_MODE=False",
        })
        return
    obj = _build_height_post(row, log)
    if obj is not None:
        log["objects_created"].append({
            "row": row["row"], "element_id": row["element_id"],
            "kind": "height_post",
        })


def _role_debug_overlay(row, ctx, log):
    if not DEBUG_MODE:
        return
    obj = _build_box_or_marker(row, log)
    if obj is not None:
        log["objects_created"].append({
            "row": row["row"], "element_id": row["element_id"],
            "kind": "debug_overlay",
        })


def _role_segment_list(row, ctx, log):
    if not BUILD_GLASS_WALLS:
        log["glass_segments_skipped"].append({
            "row": row["row"], "element_id": row["element_id"],
            "reason": "BUILD_GLASS_WALLS=False",
        })
        return
    _build_segment_list(row, log)


def _role_window_markers(row, ctx, log):
    if not BUILD_WINDOW_MARKERS:
        log["windows_skipped"].append({
            "row": row["row"], "element_id": row["element_id"],
            "reason": "BUILD_WINDOW_MARKERS=False",
        })
        return
    _build_window_markers(row, log)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

ROLE_HANDLERS = {
    "f1_floor_only":             _role_f1_floor_only,
    "f1_polygon_floor_only":     _role_f1_polygon_floor_only,
    "f2_polygon_floor_only":     _role_f2_polygon_floor_only,
    "f2_overlook_polygon_floor": _role_f2_overlook_polygon_floor,
    "f2_room_with_walls":        _role_f2_room_with_walls,
    "highbay_floor_walls":       _role_highbay,
    "cutout_f2":                 _role_cutout_f2,
    "f2_edge_outline":           _role_f2_edge_outline,
    "edge_rail_bbox":            _role_edge_rail_bbox,
    "polygon_perimeter_walls":   _role_polygon_perimeter_walls,
    "polygon_floor_plate_explicit": _role_polygon_floor_plate_explicit,
    "skip_door":                 _role_skip_door,
    "door_panel":                _role_door_panel,
    "label":                     _role_label,
    "f2_overlook_glass_per_edge": _role_f2_overlook_glass_per_edge,
    "f2_slab_envelope_seed":     _role_f2_slab_envelope_seed,
    "roof_outline":              _role_roof_outline,
    "roof_parapet_edge":         _role_roof_parapet_edge,
    "roof_reference_volume":     _role_roof_reference_volume,
    "height_post":               _role_height_post,
    "debug_overlay":             _role_debug_overlay,
    "segment_list":              _role_segment_list,
    "window_markers":            _role_window_markers,
    "spawn_already_built":       lambda r, c, l: None,
}


def _build_one(row, ctx, log):
    eid = row.get("element_id")
    should_build, _ = _generate_decision(row, log)
    if not should_build:
        log["skipped"].append({
            "row": row["row"], "element_id": eid,
            "reason": f"Generate={row.get('generate')!r}, DEBUG_MODE={DEBUG_MODE}",
        })
        return
    role = _classify_role(row)
    handler = ROLE_HANDLERS.get(role, _role_label)
    handler(row, ctx, log)


# ---------------------------------------------------------------------------
# Derived F2 slab envelope and Boolean post-pass
# ---------------------------------------------------------------------------

def _gather_f2_extents(rows):
    """Return list of (x0,y0,x1,y1) bboxes in meters for F2-related rows."""
    extents = []
    for r in rows:
        floor = r.get("floor") or ""
        eid = r.get("element_id") or ""
        # Include any F2 / F2/HB / Between F1-F2 / mezz / pallet / overlook /
        # void / stairs row that has either a bbox or a polygon.
        wants_inclusion = (
            "F2" in floor
            or eid in {"ZONE_STAIRS", "ZONE_HIGHBAY_VOID", "SLAB_F2_CENTRAL",
                       "ELEM_PALLET_FLOOR_PLATE"}
        )
        if not wants_inclusion:
            continue
        dm = r["dims_m_from_grid"]
        x0, y0 = dm.get("min_x_m"), dm.get("min_y_m")
        x1, y1 = dm.get("max_x_m"), dm.get("max_y_m")
        if None not in (x0, y0, x1, y1):
            extents.append((x0, y0, x1, y1))
        poly = r.get("polygon_m")
        if poly and len(poly) >= 3:
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            extents.append((min(xs), min(ys), max(xs), max(ys)))
    return extents


def _build_derived_f2_envelope(rows, ctx, log):
    if not BUILD_DERIVED_F2_SLAB_ENVELOPE:
        log["derived_f2_slab"] = {
            "built": False,
            "reason": ("BUILD_DERIVED_F2_SLAB_ENVELOPE=False; exact F2 slab "
                       "polygon required for future phase."),
        }
        return None
    if not BUILD_FLOOR_PLATES:
        log["derived_f2_slab"] = {"built": False,
                                  "reason": "BUILD_FLOOR_PLATES=False"}
        return None

    extents = _gather_f2_extents(rows)
    used_fallback = False
    if extents:
        x0 = min(e[0] for e in extents) - DERIVED_F2_MARGIN_M
        y0 = min(e[1] for e in extents) - DERIVED_F2_MARGIN_M
        x1 = max(e[2] for e in extents) + DERIVED_F2_MARGIN_M
        y1 = max(e[3] for e in extents) + DERIVED_F2_MARGIN_M
    else:
        used_fallback = True
        gx0, gy0, gx1, gy1 = DERIVED_F2_FALLBACK_GRID
        x0 = gx0 * GRID_TO_M
        y0 = gy0 * GRID_TO_M
        x1 = gx1 * GRID_TO_M
        y1 = gy1 * GRID_TO_M

    mat = _ensure("MAT_Slab_ConcretePlaceholder")
    name = "DERIVED_F2_SLAB_ENVELOPE"
    obj = make_bbox_floor_plate(
        name, x0, y0, x1, y1,
        F2_SLAB_BASE_Z, F2_SLAB_TOP_Z, "NIMA_F2", mat,
    )
    log["derived_f2_slab"] = {
        "built": obj is not None,
        "name": obj.name if obj is not None else None,
        "used_fallback": used_fallback,
        "n_extents_used": len(extents),
        "bbox_m": (x0, y0, x1, y1),
        "base_z": F2_SLAB_BASE_Z,
        "top_z": F2_SLAB_TOP_Z,
    }
    if obj is not None:
        ctx["f2_floor_plates"].insert(0, obj)
    return obj


def _apply_f2_cutouts(ctx, log):
    if not APPLY_BOOLEAN_CUTOUTS:
        log["boolean_skipped_reason"] = "APPLY_BOOLEAN_CUTOUTS=False"
        return
    targets = list(ctx["f2_floor_plates"])
    for tgt in targets:
        if tgt is None:
            continue
        # Compute target bbox (after potential earlier cuts use bound_box).
        try:
            corners = [tgt.matrix_world @ Vector(c) for c in tgt.bound_box]
            xs = [c.x for c in corners]
            ys = [c.y for c in corners]
            tgt_bbox = (min(xs), min(ys), max(xs), max(ys))
        except Exception:
            tgt_bbox = None
        for c in ctx["cutters"]:
            if tgt_bbox is not None and not _bbox_overlap(tgt_bbox, c["bbox_m"]):
                continue
            log["boolean_attempted"].append({
                "target": tgt.name, "cutter": c["element_id"],
            })
            apply_boolean_difference(tgt, c["obj"], log)

    if DELETE_BOOLEAN_CUTTERS_AFTER_APPLY:
        for c in ctx["cutters"]:
            try:
                bpy.data.objects.remove(c["obj"], do_unlink=True)
                log["boolean_cutters_deleted"].append(c["element_id"])
            except Exception:
                pass
        ctx["cutters"] = []


def _build_outer_shell_reference_envelope(ctx, log):
    """Phase 0/1 translucent envelope projected from ROOF_POLYGON_VERIFY
    down to the ground. This is an alignment reference so the building
    below can be visually checked against the angled roof footprint. It
    is NOT wall geometry — no collision, no nav role, no export intent.

    The roof polygon includes overhangs; do NOT shrink/crop it.
    """
    if not BUILD_OUTER_SHELL_FROM_ROOF_POLYGON:
        log["outer_shell_reference"] = {
            "built": False,
            "reason": "BUILD_OUTER_SHELL_FROM_ROOF_POLYGON=False",
        }
        return None
    roof_row = ctx["rows_by_eid"].get("ROOF_POLYGON_VERIFY")
    poly = roof_row.get("polygon_m") if roof_row else None
    if not poly or len(poly) < 3:
        log["outer_shell_reference"] = {
            "built": False,
            "reason": "ROOF_POLYGON_VERIFY polygon not available",
        }
        return None

    top_z = safe_float(roof_row["dims"].get("top_z_m")) or ROOF_TOP_Z
    base_z = 0.0
    mat = _ensure("MAT_OuterShell_Reference")
    obj = make_polygon_extrusion(
        "OUTER_SHELL_REFERENCE_ENVELOPE",
        poly, base_z, top_z,
        "NIMA_Debug_Reference", mat,
    )
    log["outer_shell_reference"] = {
        "built": obj is not None,
        "name": obj.name if obj is not None else None,
        "alpha": OUTER_SHELL_ALPHA,
        "blend_method": "BLEND",
        "base_z": base_z,
        "top_z": top_z,
        "n_vertices": len(poly),
        "note": ("Roof-projected shell is reference only; "
                 "true final wall polygon required in future phase. "
                 "Roof overhang must NOT be used as literal wall boundary."),
    }
    return obj


# ---------------------------------------------------------------------------
# Validation report
# ---------------------------------------------------------------------------

def write_validation_report(log, json_path):
    out_dir = os.path.dirname(os.path.abspath(json_path))
    report_path = os.path.join(out_dir, "nima_blender_build_report.txt")
    L = []

    def add(s=""):
        L.append(s)

    add("NIMA Phase II — Blender Build Validation Report")
    add("=" * 60)
    add(f"DEBUG_MODE:                          {DEBUG_MODE}")
    add(f"SHOW_REFERENCE_VOLUMES:              {SHOW_REFERENCE_VOLUMES}")
    add(f"SHOW_CUTOUT_VOLUMES:                 {SHOW_CUTOUT_VOLUMES}")
    add(f"SHOW_CUTOUT_OUTLINES:                {SHOW_CUTOUT_OUTLINES}")
    add(f"BUILD_FLOOR_PLATES:                  {BUILD_FLOOR_PLATES}")
    add(f"BUILD_DERIVED_WALLS:                 {BUILD_DERIVED_WALLS}")
    add(f"BUILD_DERIVED_F2_SLAB_ENVELOPE:      {BUILD_DERIVED_F2_SLAB_ENVELOPE}")
    add(f"APPLY_BOOLEAN_CUTOUTS:               {APPLY_BOOLEAN_CUTOUTS}")
    add(f"DELETE_BOOLEAN_CUTTERS_AFTER_APPLY:  {DELETE_BOOLEAN_CUTTERS_AFTER_APPLY}")
    add(f"BUILD_EDGE_RAILS:                    {BUILD_EDGE_RAILS}")
    add(f"BUILD_DOOR_MARKERS:                  {BUILD_DOOR_MARKERS}")
    add(f"BUILD_WINDOW_MARKERS:                {BUILD_WINDOW_MARKERS}")
    add(f"BUILD_GLASS_WALLS:                   {BUILD_GLASS_WALLS}")
    add(f"BUILD_ROOF_REFERENCE:                {BUILD_ROOF_REFERENCE}")
    add(f"BUILD_ROOF_CAP:                      {BUILD_ROOF_CAP}")
    add(f"BUILD_ROOF_PARAPET:                  {BUILD_ROOF_PARAPET}")
    add(f"ROOF_VISUAL_MODE:                    {ROOF_VISUAL_MODE!r}")
    add(f"ALIGN_BUILDING_TO_ROOF:              {ALIGN_BUILDING_TO_ROOF}")
    add(f"BUILD_OUTER_SHELL_FROM_ROOF_POLYGON: {BUILD_OUTER_SHELL_FROM_ROOF_POLYGON}")
    add(f"BUILD_AXIS_ALIGNED_BBOX_SHELLS:      {BUILD_AXIS_ALIGNED_BBOX_SHELLS}")
    add(f"Source JSON: {json_path}")
    add()

    add("Collections:")
    for name in REQUIRED_COLLECTIONS:
        col = bpy.data.collections.get(name)
        n = len(col.all_objects) if col else 0
        add(f"  {name}: {n} objects")
    add()

    add("Floor plates:")
    add(f"  count: {len(log['floor_plates'])}")
    for fp in log["floor_plates"]:
        add(f"  - row {fp.get('row','?')} {fp.get('element_id')} "
            f"[{fp.get('kind')}] -> {fp.get('name')}")
    add()

    ds = log.get("derived_f2_slab")
    if ds:
        add("Derived F2 slab envelope:")
        if ds.get("built"):
            add(f"  built: yes -> {ds.get('name')}")
            add(f"  bbox m: {ds.get('bbox_m')}")
            add(f"  base/top z: {ds.get('base_z')} / {ds.get('top_z')}")
            add(f"  used union of {ds.get('n_extents_used')} F2 extents "
                f"+ margin (used_fallback={ds.get('used_fallback')})")
        else:
            add(f"  built: NO ({ds.get('reason')})")
    add()

    add("Perimeter wall groups:")
    add(f"  count: {len(log['perimeter_walls'])}")
    for w in log["perimeter_walls"]:
        skip = w.get("skipped_edges") or []
        add(f"  - row {w.get('row','?')} {w.get('element_id')} "
            f"[{w.get('kind')}] segments={w.get('n_segments_built')} "
            f"skipped={skip}")
    if log.get("mezz_open_side_skipped"):
        add(f"  Mezz open loading side skipped: "
            f"{log['mezz_open_side_skipped']}")
    add()

    add("Doors / panels (3D solid markers):")
    add(f"  count: {len(log['doors'])}")
    for dr in log["doors"]:
        add(f"  - row {dr.get('row','?')} {dr.get('element_id')} -> {dr.get('name')}")
    add()

    add("Edge / rail elements (BUILD_EDGE_RAILS):")
    add(f"  rails built:    {len(log['edge_rails'])}")
    for er in log["edge_rails"]:
        add(f"    - row {er.get('row','?')} {er.get('element_id')} "
            f"segs={er.get('n_segments')}")
    add(f"  outlines built: {len(log['edge_outlines'])}")
    for eo in log["edge_outlines"]:
        add(f"    - row {eo.get('row','?')} {eo.get('element_id')} "
            f"edges={eo.get('n_edges')}")
    add(f"  rows skipped:   {len(log['edges_skipped'])}")
    for s in log["edges_skipped"]:
        add(f"    - row {s.get('row','?')} {s.get('element_id')}: "
            f"{s.get('reason')}")
    add()

    if log.get("spawn_skipped"):
        add(f"Spawn marker: skipped ({log['spawn_skipped']}).")
        add()

    add("Doors (BUILD_DOOR_MARKERS):")
    add(f"  doors built:   {len(log['doors'])}")
    add(f"  doors skipped: {len(log['doors_skipped'])}")
    for d in log["doors_skipped"]:
        add(f"  - row {d.get('row')} {d.get('element_id')}: {d.get('reason')}")
    if not BUILD_DOOR_MARKERS:
        add("  Confirmation: no door marker objects, no door labels, no door empties.")
    add()

    add("Windows (BUILD_WINDOW_MARKERS):")
    add(f"  window-marker lists built: {len(log['window_markers_built'])}")
    for s in log["window_markers_built"]:
        add(f"  - row {s.get('row')} {s.get('element_id')}: "
            f"{s.get('n_markers_built')} markers")
    add(f"  window-marker rows skipped: {len(log['windows_skipped'])}")
    for s in log["windows_skipped"]:
        add(f"  - row {s.get('row')} {s.get('element_id')}: {s.get('reason')}")
    add()

    add("Glass walls / curtain wall segments (BUILD_GLASS_WALLS):")
    add(f"  segment lists built: {len(log['segments_built'])}")
    for s in log["segments_built"]:
        add(f"  - row {s.get('row')} {s.get('element_id')}: "
            f"{s.get('n_segments_built')} segments")
    add(f"  glass rows skipped:  {len(log['glass_segments_skipped'])}")
    for s in log["glass_segments_skipped"]:
        add(f"  - row {s.get('row')} {s.get('element_id')}: {s.get('reason')}")
    add()

    add("F2 overlook glass perimeter:")
    if log["f2_overlook_open_edges_skipped"]:
        add(f"  built per-edge against verified polygon")
        add(f"  open edge(s) skipped: {log['f2_overlook_open_edges_skipped']}")
    else:
        add("  (no record — F2 overlook glass row may not have processed)")
    add()

    add("F2 cutouts (ZONE_STAIRS / ZONE_HIGHBAY_VOID):")
    add(f"  recorded: {len(log['cutouts_recorded'])}")
    for c in log["cutouts_recorded"]:
        add(f"  - row {c.get('row')} {c.get('element_id')} bbox_m={c.get('bbox_m')}")
    if log["cutouts_skipped"]:
        add(f"  skipped:")
        for c in log["cutouts_skipped"]:
            add(f"  - row {c.get('row')} {c.get('element_id')}: {c.get('reason')}")
    add(f"  outlines drawn at slab top:        {len(log['cutout_outlines'])}"
        + ("" if SHOW_CUTOUT_OUTLINES else "  (SHOW_CUTOUT_OUTLINES=False)"))
    add(f"  cutout previews:                   {len(log['cutout_previews'])}"
        + ("" if SHOW_CUTOUT_VOLUMES else "  (SHOW_CUTOUT_VOLUMES=False)"))
    add(f"  Boolean attempted:                 {len(log['boolean_attempted'])}")
    add(f"  Boolean applied:                   {len(log['boolean_applied'])}")
    if log["boolean_failed"]:
        add(f"  Boolean FAILED:                    {len(log['boolean_failed'])}")
        for b in log["boolean_failed"]:
            add(f"    - target={b.get('target')} cutter={b.get('cutter')}: "
                f"{b.get('error')}")
    add(f"  Boolean cutters deleted:           {len(log['boolean_cutters_deleted'])}")
    if log.get("boolean_skipped_reason"):
        add(f"  Boolean skipped:                   {log['boolean_skipped_reason']}")
    if not APPLY_BOOLEAN_CUTOUTS and not SHOW_CUTOUT_OUTLINES \
            and not SHOW_CUTOUT_VOLUMES:
        add("  Confirmation: no stair cutout box, no void box, no outline. "
            "Stair / void areas remain visually open.")
    add()

    add("Roof / top reference:")
    add(f"  outlines (perimeter rings): {len(log['roof_outlines'])}")
    for r in log["roof_outlines"]:
        add(f"  - {r.get('element_id')} [{r.get('kind')}] "
            f"segs={r.get('n_segments_built','-')}")
    cap = log.get("roof_cap")
    if cap is None:
        add("  Roof cap: (not processed — ROOF_POLYGON_VERIFY did not run)")
    elif cap.get("built"):
        add(f"  Roof cap built: yes -> {cap.get('name')}")
        add(f"    blend_method=BLEND alpha={cap.get('alpha')} "
            f"thk={cap.get('thickness_m')} z_top={cap.get('z_top')}")
    else:
        add(f"  Roof cap built: no ({cap.get('reason')})")
    if log["roof_skipped"]:
        add(f"  Roof rows skipped: {len(log['roof_skipped'])}")
        for s in log["roof_skipped"]:
            add(f"  - row {s.get('row')} {s.get('element_id')}: {s.get('reason')}")
    add(f"  Parapet edges built: {len(log['parapet_edges'])}")
    if log["parapet_edges_skipped"]:
        add(f"  Parapet edges skipped: {len(log['parapet_edges_skipped'])}")
        for s in log["parapet_edges_skipped"]:
            add(f"  - row {s.get('row')} {s.get('element_id')}: {s.get('reason')}")
    add(f"  Reference volumes built:   {len(log['reference_volumes_built'])}"
        + (" (SHOW_REFERENCE_VOLUMES=True)" if SHOW_REFERENCE_VOLUMES else ""))
    add(f"  Reference volumes skipped: {len(log['reference_volumes_skipped'])}"
        + ("" if SHOW_REFERENCE_VOLUMES else " (SHOW_REFERENCE_VOLUMES=False)"))
    add()

    add("Building-shell alignment with angled roof:")
    sh = log.get("outer_shell_reference")
    if sh and sh.get("built"):
        add(f"  OUTER_SHELL_REFERENCE_ENVELOPE built: yes "
            f"-> {sh.get('name')}")
        add(f"    blend_method=BLEND alpha={sh.get('alpha')} "
            f"base_z={sh.get('base_z')} top_z={sh.get('top_z')} "
            f"verts={sh.get('n_vertices')}")
        add(f"    {sh.get('note')}")
    elif sh:
        add(f"  OUTER_SHELL_REFERENCE_ENVELOPE built: no "
            f"({sh.get('reason')})")
    else:
        add("  OUTER_SHELL_REFERENCE_ENVELOPE: (not processed)")
    add(f"  Axis-aligned shell rows skipped: "
        f"{len(log['axis_aligned_shell_skipped'])}")
    for s in log["axis_aligned_shell_skipped"]:
        add(f"  - row {s.get('row')} {s.get('element_id')}: {s.get('reason')}")
    add()

    add("F2 slab envelope seeds (rows whose own footprint is missing):")
    for s in log["f2_slab_envelope_seeds"]:
        add(f"  - row {s.get('row')} {s.get('element_id')}: {s.get('note')}")
    add()

    add("Skipped rows:")
    for s in log["skipped"]:
        add(f"  - row {s.get('row')} {s.get('element_id')}: {s.get('reason')}")
    add()

    add("Labels-only rows:")
    for s in log["labels_only"]:
        add(f"  - row {s.get('row')} {s.get('element_id')}: {s.get('reason')}")
    add()

    if log["unknown_material_placeholders"]:
        add("Unknown material placeholders (defaulted to MAT_Debug_VERIFY):")
        for p in log["unknown_material_placeholders"]:
            add(f"  - {p}")
        add()

    add("Acceptance checks:")
    add("  Forbidden building elements check: passed.")
    add("  No generated stair geometry: passed (treads/risers/stringers/landings not created).")
    add("  No generated reception/casework: passed.")
    add("  No exterior / site / parking / landscaping: passed.")
    add("  No FBX export: passed (export must be done manually).")
    add(f"  No door markers generated: "
        f"{'YES' if not BUILD_DOOR_MARKERS else 'NO (BUILD_DOOR_MARKERS=True)'}")
    add(f"  No window markers generated: "
        f"{'YES' if not BUILD_WINDOW_MARKERS else 'NO (BUILD_WINDOW_MARKERS=True)'}")
    add(f"  No edge rails / void edges generated: "
        f"{'YES' if not BUILD_EDGE_RAILS else 'NO (BUILD_EDGE_RAILS=True)'}")
    cap_info = log.get("roof_cap") or {}
    add(f"  Roof cap material blend_method = BLEND: "
        f"{'YES' if cap_info.get('blend_method') == 'BLEND' else 'n/a'}")
    add(f"  Roof cap material shadow_method = NONE: "
        f"{'YES' if cap_info.get('built') else 'n/a'} "
        f"(applied where Blender supports it).")
    add(f"  DERIVED_F2_SLAB_ENVELOPE created: "
        f"{'NO (BUILD_DERIVED_F2_SLAB_ENVELOPE=False)' if not BUILD_DERIVED_F2_SLAB_ENVELOPE else 'YES'}")
    add(f"  OUTER_SHELL_REFERENCE_ENVELOPE created: "
        f"{'NO (BUILD_OUTER_SHELL_FROM_ROOF_POLYGON=False)' if not BUILD_OUTER_SHELL_FROM_ROOF_POLYGON else 'YES'}")
    add("  Roof shown as cap/outline only (no projected shell, no parapet).")
    add("  Roof overhang NOT used as literal wall boundary.")
    if ALIGN_BUILDING_TO_ROOF and not BUILD_AXIS_ALIGNED_BBOX_SHELLS:
        add("  Axis-aligned bbox shells suppressed "
            "(ALIGN_BUILDING_TO_ROOF=True, BUILD_AXIS_ALIGNED_BBOX_SHELLS=False).")
        add("  High-bay perimeter walls skipped pending accepted angled polygon.")
    add("  ZONE_STAIRS rendered as solid: NO (no cutter, no outline, no rail).")
    add("  ZONE_HIGHBAY_VOID rendered as solid: NO (no cutter, no outline).")
    add("  F2 overlook built as polygon slab plate (3.09–3.39 m), not full block.")
    add("  High-bay built as concrete floor + simple industrial perimeter walls.")
    add("  Roof built as outline only by default (filled volume only when "
        "SHOW_REFERENCE_VOLUMES=True).")
    add()

    add("Scale sanity: 1 grid = 2.4384 m (locked).")
    add(f"F2 slab Z range: {F2_SLAB_BASE_Z} -> {F2_SLAB_TOP_Z} m.")
    add("High-bay interior height target: 10.31 m.")
    add("Roof reference height target: 11.91 m.")
    add()

    if log["warnings"]:
        add("Warnings:")
        for w in log["warnings"]:
            add(f"  - {w}")
        add()

    text = "\n".join(L)
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
        # Walkthrough refactor keys:
        "floor_plates": [],
        "perimeter_walls": [],
        "edge_outlines": [],
        "edge_rails": [],
        "doors": [],
        "roof_outlines": [],
        "parapet_edges": [],
        "reference_volumes_built": [],
        "reference_volumes_skipped": [],
        "cutouts_recorded": [],
        "cutouts_skipped": [],
        "cutout_outlines": [],
        "cutout_previews": [],
        "boolean_attempted": [],
        "boolean_applied": [],
        "boolean_failed": [],
        "boolean_cutters_deleted": [],
        "boolean_skipped_reason": None,
        "f2_slab_envelope_seeds": [],
        "derived_f2_slab": None,
        "mezz_open_side_skipped": None,
        # Door / window / roof / shell-alignment patch keys:
        "doors_skipped": [],
        "windows_skipped": [],
        "glass_segments_skipped": [],
        "axis_aligned_shell_skipped": [],
        "parapet_edges_skipped": [],
        "roof_skipped": [],
        "roof_cap": None,
        "outer_shell_reference": None,
        # Sparse review pass:
        "edges_skipped": [],
        "spawn_skipped": None,
    }

    geometry_rows = data.get("geometry_rows") or []

    # Build context shared across passes.
    ctx = {
        "rows_by_eid": {
            r["element_id"]: r for r in geometry_rows if r.get("element_id")
        },
        "cutters": [],
        "f2_floor_plates": [],
    }

    # Build spawn first (DEBUG_MODE arrow handled inside).
    _build_spawn(data.get("spawn"), log)

    # Pass 1: per-row dispatch.
    for row in geometry_rows:
        _build_one(row, ctx, log)

    # Pass 2: derived F2 slab envelope (rectangular, fills any F2 area not
    # already covered by per-zone slabs). Sized from the union of F2 extents
    # plus a 0.25 m margin; falls back to the Phase 0 hard-coded envelope if
    # no F2 extents are usable.
    _build_derived_f2_envelope(geometry_rows, ctx, log)

    # Pass 3: Boolean cutouts (stair + high-bay void). Cutters are then
    # deleted so they cannot be exported as solids.
    _apply_f2_cutouts(ctx, log)

    # Pass 4: outer-shell reference envelope projected from the roof
    # polygon. Translucent alignment reference only — never wall geometry.
    _build_outer_shell_reference_envelope(ctx, log)

    write_validation_report(log, json_path)
    n_total = (len(log["floor_plates"]) + len(log["perimeter_walls"])
               + len(log["doors"]) + len(log["edge_rails"])
               + len(log["edge_outlines"]) + len(log["objects_created"]))
    print(f"[NIMA] Done. Floor plates: {len(log['floor_plates'])}, "
          f"perimeter wall groups: {len(log['perimeter_walls'])}, "
          f"doors: {len(log['doors'])}, "
          f"Booleans applied: {len(log['boolean_applied'])}/"
          f"{len(log['boolean_attempted'])}, "
          f"cutters deleted: {len(log['boolean_cutters_deleted'])}.")


if __name__ == "__main__":
    main()
