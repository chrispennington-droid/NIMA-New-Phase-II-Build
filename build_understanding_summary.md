# build_understanding_summary.md

NIMA Phase II Tyler Prove-Out Facility — Interior Walkthrough Blockout
Pre-build inspection of the locked Excel source-of-truth.

Source workbook inspected:
`NIMA_Phase2_Master_Schedule_LOCKED-3.xlsx`

Scope of this document: report only. No Python is generated yet.
Awaiting approval before producing `extract_schedule_to_json.py`,
`build_nima_blender.py`, and `README_run_instructions.md`.

---

## 1. Workbook structure

- Sheets found: 1
  - `Master Build List` (the only sheet, used as the source)
- Sheet dimensions: A1:AV105 (48 columns × 105 rows)
- Title / scale rows: 1, 2, 3 (row 4 is blank)
- Detected header row: **row 5**
- Data rows: **rows 6 - 105** (100 row slots; 4 are spacer/section-break rows
  — see §2). Effective non-empty data rows: **98**
- No additional sheets to fall back to.

---

## 2. Detected columns (row 5, all 48)

| # | Col | Header |
|---|-----|--------|
| 1 | A | Section |
| 2 | B | Element ID |
| 3 | C | Element Name |
| 4 | D | Category |
| 5 | E | Subcategory |
| 6 | F | Floor |
| 7 | G | Build Phase |
| 8 | H | Generate Geometry? |
| 9 | I | Required? |
| 10 | J | Geometry Type |
| 11 | K | Coordinate / Placement Type |
| 12 | L | Center X Grid |
| 13 | M | Center Y Grid |
| 14 | N | Min X Grid |
| 15 | O | Max X Grid |
| 16 | P | Min Y Grid |
| 17 | Q | Max Y Grid |
| 18 | R | Length Grid X |
| 19 | S | Depth Grid Y |
| 20 | T | Length M |
| 21 | U | Depth M |
| 22 | V | Base Z M |
| 23 | W | Top Z M |
| 24 | X | Height Ft |
| 25 | Y | Height M |
| 26 | Z | Thickness M |
| 27 | AA | Rotation Deg |
| 28 | AB | Material Type |
| 29 | AC | Material Placeholder |
| 30 | AD | Finish / Design Reference |
| 31 | AE | Glass/Solid/Open/Closed |
| 32 | AF | Door Function |
| 33 | AG | Walkable? |
| 34 | AH | Collision Needed? |
| 35 | AI | Visibility Needed? |
| 36 | AJ | F2 Slab Relationship |
| 37 | AK | Alignment / Stacking Group |
| 38 | AL | Confidence 1-10 |
| 39 | AM | Claude Conf. |
| 40 | AN | Gemini Conf. |
| 41 | AO | Grok Conf. |
| 42 | AP | ChatGPT Conf. |
| 43 | AQ | Codex Conf. |
| 44 | AR | Verification Status |
| 45 | AS | Spot Check ID |
| 46 | AT | Source Consensus |
| 47 | AU | Notes |
| 48 | AV | Question for AI Review |

Notes:
- `Length Ft` and `Width Ft` do **not** appear; only `Height Ft` exists as the
  feet-only informational column (per the master prompt this is informational
  unless no metric value exists).
- All grid-unit columns (Center X/Y Grid, Min/Max X/Y Grid, Length Grid X,
  Depth Grid Y) are present and will be converted via × 2.4384 → meters.
- All meter columns (Length M, Depth M, Base Z M, Top Z M, Height M,
  Thickness M) are present and will be used as-is.

---

## 3. Row counts by Section

| Section | Rows |
|---------|------|
| 01 Coordinate Rules | 7 |
| 02 Height Rules | 12 |
| 03 Major Zones | 12 |
| 04 Doors / Openings | 19 |
| 05 New Standard Blockout Elements | 13 |
| 06 Material / Interior Look Consensus | 10 |
| 06 Materials / Design Consensus | 3 |
| 07 Spot Checks / Verification | 7 |
| 08 Roof Elements | 6 |
| 08 Roof Elements (NEW) | 1 |
| 09 Cross-Review Additions | 8 |
| (Section blank — spacer rows 89, 90, 97, 98) | 4 (excluded as spacers) |
| **Total non-empty data rows** | **98** |

(Note: the workbook contains both `06 Material / Interior Look Consensus` and
`06 Materials / Design Consensus` as section labels — both are treated as the
materials section. The row counts above reflect the literal text in the cell.)

---

## 4. Row counts by Category (top values)

| Category | Rows |
|----------|------|
| Door Marker | 19 |
| Material | 16 |
| Zone | 11 |
| Spot Check | 7 |
| Thickness Rule | 6 |
| Rule | 6 |
| Height Rule | 5 |
| Reference | 3 |
| Height Reference | 2 |
| Structure | 2 |
| Glass / Curtain Wall | 2 |
| Safety / railing | 2 |
| Slab, Void, Door, Window, Roof, Enclosure, Floor/ceiling assembly, Wayfinding, Entry / glass, Safety / glass wall, Safety / gate, Safety / debug edge, Readability / route logic, Door / route logic, Learning activity / spatial review | 1 each |
| (blank Category) | 2 (the section-break rows) |

---

## 5. Generate Geometry? distribution

| Generate Geometry? | Rows |
|---|---|
| No | 35 |
| Reference Only now | 28 |
| Debug Only | 13 |
| Reference Only | 10 |
| Yes | 9 |
| VERIFY | 1 |
| (blank) | 2 (section-break rows) |
| **Total** | **98** |

### Build-eligible (will reach the Blender builder)

After excluding rules / materials / spot checks per the master prompt
(Sections 01, 02, 06, 07 + Categories Rule / Material / Material definition /
Spot Check / Thickness Rule / Height Rule), the geometry-eligible set is:

| Bucket | Source rows | Notes |
|---|---|---|
| `Yes` (solid) | 9 | All in Sections 04 (none — see below), 05 (8), 09 (0). Specifically rows 56, 57, 59, 60, 61, 63, 65, 66, 68. |
| `Reference Only now` (translucent) | 28 | All Section-04 doors (19), all Section-03 zones except those tagged Debug Only (8), Section-09 segment lists (3). |
| `Reference Only` (translucent) | 7 | SLAB_F2_CENTRAL, ZONE_LOBBY_CTX, ZONE_CONNECTOR_PATH, ZONE_STAIRS, ZONE_OFFICELAB_TRANS, ELEM_SHORT_DOOR_F2_SW, ELEM_OVERHEAD_VERIFY, ROOF_POLYGON_VERIFY, ROOF_STRUCT_ABOVE_HB, ROOF_PARAPET_EDGE — counting only those not excluded by section/category. |
| `VERIFY` (treat as translucent if dims safe) | 1 | ELEM_RAIL_STAIR_VOID (row 58). |
| `Debug Only` (only when DEBUG_MODE=True) | 13 | RULE_OUTER, RULE_INNER, RULE_ROTATION (debug overlays); HT_F1_LOWER, HT_F2_TOP, HT_F2_OCCUPIED, HT_HIGHBAY, HT_ROOF_REF (height posts); ZONE_SPAWN, ZONE_HIGHBAY_VOID, ELEM_SPAWN_ARROW, ROOF_HIGHBAY_TOP_REF, ROOF_LOBBY_CONN_TOP_REF. |

**Geometry-eligible row count (DEBUG_MODE = False):** ~48
**Geometry-eligible row count (DEBUG_MODE = True, the default first build):** ~61

(Exact assignment will be re-counted by the extractor and printed in its
validation report so it is auditable.)

### Excluded from geometry generation

| Bucket | Rows | Action |
|---|---|---|
| Material rows (Section 06 + Section 09 material definitions) | 16 | Ingested as material catalog only. |
| Rule rows (RULE_*, THK_*, RULE_F2_STAIR_TO_OVERLOOK_OPEN) | 11 | Ingested into rules table only. |
| Spot-check rows (SC01–SC08) | 7 | Logged; no geometry unless DEBUG_MODE. |
| Section-break / spacer rows | 4 | Skipped. |

---

## 6. Floor distribution

| Floor | Rows |
|---|---|
| F1 | 30 |
| F2 | 24 |
| All | 17 |
| F1/F2 | 11 |
| Roof | 7 |
| High-Bay | 3 |
| F2/high-bay | 1 |
| Between F1/F2 | 1 |
| All non-high-bay | 1 |
| F1 lobby/connector | 1 |
| (blank) | 2 (section-break rows) |

This drives the collection assignment (NIMA_F1, NIMA_F2, NIMA_HighBay,
NIMA_Roof, NIMA_Spawn, NIMA_Debug_Reference, NIMA_Rules_Materials).

---

## 7. Geometry Type distribution (top values)

| Geometry Type | Rows |
|---|---|
| Door/panel marker | 19 |
| Rule | 11 |
| Material / Material definition | 16 |
| Height Post | 7 |
| Screenshot/crop (Spot Check) | 7 |
| Polygon / rotated rectangle (4 user-confirmed corners) | 2 |
| Polygon - 4 verified corners (bbox debug only) | 1 |
| Polygon - 16 unique vertices | 1 |
| Debug Lines | 2 |
| Slab Reference | 1 |
| Marker + arrow (spawn) | 1 |
| Transparent bbox / later floor plate | 1 |
| Path marker / floor overlay | 1 |
| Opening/import zone (stairs, import-only) | 1 |
| Path/zone marker | 1 |
| Transparent bbox + rotation overlays | 1 |
| Overlook bbox / edge marker | 1 |
| Transparent void marker | 1 |
| Room bbox | 1 |
| Glass wall / guard | 1 |
| L-shaped railing | 1 |
| Guardrail | 1 |
| Gate / barrier | 1 |
| Wire cage enclosure | 1 |
| Solid plate | 1 |
| Door marker / opening | 1 |
| Glass door/sidelight proxy | 1 |
| Floor decal/arrow | 1 |
| Edge strip | 1 |
| Text plane/sign | 1 |
| Wide panel placeholder | 1 |
| Floor proxy / transparent boxes | 1 |
| Volume | 1 |
| Edge comparison | 1 |
| Perimeter element | 1 |
| Segment list (5 cyan rects + 4 cyan polys) | 1 |
| 8 window markers | 1 |
| Segment list (9 cyan rects) | 1 |
| Rotation Overlay | 1 |
| (blank — section-break rows) | 2 |

All Geometry Types listed above are mappable to the code paths defined in
the master prompt (bbox, polygon extrusion, door/panel marker, glass-wall
solid, segment list, reference plane, transparent volume, height post,
rule/material/spot-check skip). No unmapped Geometry Type values were found.

---

## 8. Coordinate / Placement Type distribution (highlights)

- Point marker: 20
- Global / Default / Source hierarchy / Rule (rule-row placements): ~24
- bbox / Bounds / Broad context bbox / Room bbox / Corrected stacked bbox /
  F2 storage bbox / F2 occupied/support zones: ~7
- Polygon / verified corners / see notes for corners / rotated rectangle: 3
  (rows 31, 32, 36; row 91 uses placement label "Roof With Grid SVG" but its
  Geometry Type is `Polygon - 16 unique vertices` — treated as polygon.)
- Stacked / Corrected/stacked / Aligned with stack / Between GRND/RECYC and
  Pallet Storage: 4 (the GRND/RECYC + Pallet stacked relationship)
- Along exposed edges / Around / Perimeter / At edge: 4
- Heights SVG / Roof With Grid SVG / Roof SVG vs F1/F2 SVGs / F1 SVG cyan /
  F1 SVG blue / F2 SVG cyan: 7 (will use the row's metric / Notes data; SVGs
  not re-parsed)
- Specific X/Y bbox strings (e.g. "X -12 to -10, Y -2 to 0"): 6 (spot-check
  rows in Section 07 — no geometry)

No "Unknown" placement type values were detected.

---

## 9. Verification Status distribution

| Status | Rows |
|---|---|
| Accepted | 47 |
| VERIFY | 40 |
| VERIFY locations | 1 |
| Required | 7 |
| Optional | 1 |
| (blank) | 2 (section-break rows) |

**Total VERIFY-style rows: 41** (40 `VERIFY` + 1 `VERIFY locations`).
These will be routed to `NIMA_Debug_Reference` and rendered translucent
with `MAT_Debug_VERIFY`.

---

## 10. Material Placeholder distribution

17 rows have a blank `Material Placeholder` (mostly Section-01/02 rule rows
and section-break rows — they do not need a material).

Distinct, in-scope material placeholders found in geometry-bearing rows:

- MAT_Door_ClosedNonWorking (17)
- MAT_OverheadDoor_Industrial (5)
- MAT_Roof_StructurePlaceholder (4)
- MAT_Cage_WireMetal (3 + appears in 2 combined)
- MAT_Glass_ClearArchitectural (3 + appears in 2 combined)
- MAT_Debug_VERIFY (3)
- MAT_Debug_Height_Roof (3)
- MAT_Debug_PurpleBoundary (2)
- MAT_Debug_Height_F2 (2)
- MAT_Debug_SpawnRed (2)
- MAT_Floor_TanCeramicTile + MAT_Wall_WarmCreamPaint (2)
- MAT_Floor_TanCeramicTile + MAT_Glass_ClearArchitectural (2)
- MAT_Handrail_BlackPaintedMetal (2)
- MAT_Gate_MetalSafety (2)
- MAT_Plate_Steel (2)
- MAT_Cage_WireMetal + MAT_Plate_Steel (1)
- MAT_Cage_WireMetal + MAT_Gate_MetalSafety (1)
- MAT_Door_Working (1)
- MAT_Glass_ClearArchitectural + MAT_Storefront_ClearAnodizedAluminum (1)
- MAT_HighBay_ConcreteSlab + MAT_HighBay_SolidIndustrialWall (1)
- MAT_HighBay_ConcreteSlab (1)
- MAT_HighBay_SolidIndustrialWall (1)
- MAT_Wall_WarmCreamPaint (1)
- MAT_Wood_LightMapleTrim (1)
- MAT_Ceiling_WhitePaintedMetalDeck + MAT_Structure_WhitePaintedSteel (1)
- MAT_Floor_DarkPolishedStoneInlay (1)
- MAT_Slab_ConcretePlaceholder (1)
- MAT_Window_Trumatch34B (1)
- MAT_GlassWall_Trumatch31D (1)
- MAT_Debug_Height_F1 (1)
- MAT_Debug_Height_HighBay (1)
- MAT_Debug_PathSubtle (1)
- MAT_Debug_OpenToBelow (1)
- MAT_Debug_EquipmentProxy (1)
- N/A (7 rows, all rule rows)

**Material rows (Section 06 + Section 09 material definitions): 16.**

All placeholders above either match the minimum material map in the master
prompt directly (warm cream paint, light maple trim, glass, cage, plate,
door working/non-working, overhead door, high-bay floor/wall, roof
structure, debug, spawn) or follow the same naming convention. Anything not
in the curated map will be assigned `MAT_Debug_VERIFY` and logged by the
builder, per the master prompt rule.

---

## 11. Polygon rows — Notes-driven vertex extraction

Four rows in the workbook store their authoritative geometry inside the
Notes column. For each, the proposed extractor logic and chosen vertex
set are:

### Row 31 — `ZONE_GRND_RECYC` (Polygon / rotated rectangle)
- Notes contain a single `[USER DECISION - FINAL]` block.
- Selected vertices (Priority 1 — final user decision):
  - NW (-0.40005912, -4.66619264)
  - NE  (1.49118706, -4.97698287)
  - SE  (1.02241887, -7.82956950)
  - SW (-0.86882732, -7.51877927)
- Also-present axis-aligned bbox `X[-0.87, 1.49] Y[-7.83, -4.67]` is
  explicitly tagged as debug bbox; **will NOT be used** for the polygon.
- Order preserved: NW → NE → SE → SW.

### Row 32 — `ZONE_F2_OVERLOOK_POLYGON_VERIFIED` (4 verified corners)
- Notes contain one set of "Verified corner points":
  - NE (-3.5425804,    2.32234629)
  - NW (-5.9055563,    2.32248083)
  - SW (-6.4485403,   -1.0574465)
  - SE (-4.13278622,  -2.06928562)
- Logical order on extrusion: NW → NE → SE → SW.
- Also referenced: 10-foot glass perimeter wall (row 56) is to follow this
  same polygon/perimeter; row 99 prevents wall generation between the stair
  cutout and the SW corner.

### Row 36 — `ZONE_PALLET` (Polygon / rotated rectangle)
- Notes re-publish the same `[USER DECISION - FINAL]` block as row 31 plus
  "Verified to sit directly above GRND/RECYC."
- Selected vertices: same four corners as row 31 (stacked footprint).
- Confirms F2 Pallet Storage sits directly above F1 GRND/RECYC; metal plate
  (row 61) bridges them with Base Z = 3.0138 m, Top Z = 3.09 m
  (3-inch / 0.0762 m thickness, plate top flush with F2 slab Z = 3.09 m).

### Row 91 — `ROOF_POLYGON_VERIFY` (16 unique vertices)
- Notes contain a single sequence of 16 vertices (Priority 3 — no user
  decision tag present, but only one vertex set in the cell):
  ```
  (2.65,11.47), (2.39,10.77), (3.08,10.62), (3.20,10.97),
  (4.44,10.74), (1.13,-8.05), (-6.56,-6.72), (-6.37,-5.63),
  (-9.89,-5.01), (-9.78,-4.36), (-14.39,-3.59), (-14.38,-0.26),
  (-5.32,0.93), (-5.07,2.34), (-5.43,2.30), (-3.62,12.62)
  ```
  Closes back to (2.65, 11.47).
- Bbox stated in notes: X [-14.39, 4.44], Y [-8.05, 12.62]; bbox is reference
  only — extruded geometry uses the polygon.
- Cross-review note: "Claude initially extracted 15 vertices; re-extraction
  confirmed 16 unique vertices. ChatGPT correctly identified vertex 16 at
  (-3.62, 12.62)." 16 will be used.
- Important: this row's `Base Z M` and `Height M` are the literal string
  `"varies"`. Per master-prompt rules, the row will be rendered as a
  reference outline / transparent marker only at `Top Z M = 11.91 m`. Do not
  apply the 1.61 m high-bay-only structure-zone height to the entire roof
  polygon — that height belongs to row 94 (`ROOF_STRUCT_ABOVE_HB`).

**Polygon rows parsed successfully (planned):** 4 / 4
**Polygon rows skipped or failed:** 0 (all four contain unambiguous
coordinate sets; none required a Priority-2 fallback).

---

## 12. Non-numeric values inside numeric columns

Only one row contains non-numeric tokens in numeric columns:

- Row 91 — `ROOF_POLYGON_VERIFY`
  - `Base Z M`: `"varies"`
  - `Height M`: `"varies"`
  - `Height Ft`: `"varies"`

Action plan: `safe_float()` will return `None` for these; the builder will
emit a transparent reference outline at the polygon footprint with
`Top Z M = 11.91 m` and log the row as a partial-geometry case. Solid
geometry will not be attempted for this row.

No other rows contain `VERIFY`, `varies`, `TBD`, `N/A`, or text in numeric
fields.

---

## 13. Rows that will be skipped, and why

| Reason | Count | Examples |
|---|---|---|
| Section 01/02/06/07 rule, height-rule, thickness-rule, material, or spot-check rows | ~36 | RULE_SCALE, THK_GLASS, MAT_Floor_TanCeramicTile, SC01 |
| Generate Geometry? = `No` (and not categorized as material/rule above) | covered above | — |
| Section-break / spacer rows | 4 | rows 89, 90, 97, 98 |
| Partial-geometry rows where dimensions are `"varies"` (still emit reference outline + label) | 1 | ROOF_POLYGON_VERIFY (row 91) |
| Generate Geometry? = `Debug Only` when `DEBUG_MODE = False` | 13 | HT_*, RULE_OUTER/INNER/ROTATION, ZONE_SPAWN, ZONE_HIGHBAY_VOID, ELEM_SPAWN_ARROW, ROOF_HIGHBAY_TOP_REF, ROOF_LOBBY_CONN_TOP_REF |

For the first validation build the master prompt mandates `DEBUG_MODE = True`,
so the 13 Debug-Only rows will in fact be created.

No row had to be skipped because its dimensions were unparseable (other
than the `"varies"` cells noted above).

---

## 14. Possible contradictions / required-field notes

- **`ROOF_POLYGON_VERIFY` (row 91)** — `Base Z M`, `Height M`, `Height Ft`
  are all `"varies"`, but `Top Z M` is fixed at 11.91. Treated per master
  prompt: outline / reference only. No contradiction with the high-bay-only
  1.61 m volume in `ROOF_STRUCT_ABOVE_HB` (row 94).
- **`ZONE_HIGHBAY` (row 30)** — `Verification Status = VERIFY`, with a note
  about a 0° vs 10° rotation overlay. Handled per master prompt: rotation
  remains VERIFY; both overlays will be reference-only / debug.
- **`ZONE_GRND_RECYC` (row 31) and `ZONE_PALLET` (row 36)** — each row's own
  `Min/Max X Grid` and `Min/Max Y Grid` describe the debug bbox, NOT the
  polygon; the actual polygon corners live in Notes. The builder will use
  the Notes polygon and ignore Min/Max for these two rows.
- **`ZONE_F2_OVERLOOK_POLYGON_VERIFIED` (row 32) vs
  `ELEM_F2_GLASS_PERIMETER_WALL` (row 56)** — both reference the same four
  corners; row 32's `Top Z M = 5.99 m` (≈ 9.5 ft floor-to-ceiling slot) and
  row 56's `Top Z M = 6.14 m` (= 3.09 + 3.05 m, the user-locked 10-foot
  glass wall). This is intentional — the glass wall is taller than the
  overlook room volume — but worth flagging in the validation report.
- **`RULE_F2_STAIR_TO_OVERLOOK_OPEN` (row 99)** — explicitly forbids any
  wall between the stair cutout and the SW corner of the F2 overlook. The
  builder must not auto-close that edge when extruding row 32's polygon.
- No rows are missing both Element ID and Section simultaneously (other
  than spacer rows 89, 90, 97, 98).

---

## 15. Forbidden-element / scope checks

### Forbidden building elements (elevator and related terms)

The forbidden-term scan ran across every cell of every row. Findings:

- **Zero geometry-bearing or named element rows reference the forbidden
  terms.** No Element ID, Element Name, Category, Floor, Generate
  Geometry?, Geometry Type, Coordinate / Placement Type, or Material
  Placeholder cell contains the forbidden terms anywhere.
- Three incidental string occurrences were found, all in informational /
  descriptive columns only:
  - row 72 (`MAT_Wood_LightMapleTrim`), `Finish / Design Reference` —
    descriptive sentence about photographed casework.
  - row 83 (`SC04`, a spot-check screenshot row, Generate Geometry = No),
    `Finish / Design Reference` and `Question for AI Review` — describe
    "verify corrected stair/<forbidden> footprints".
- These cells are descriptive metadata on rows that do **not** generate
  geometry, do **not** create labels in the scene, and do **not** drive
  collections, materials, or named objects. The extractor will scrub the
  forbidden terms from any text it would otherwise pass through to the
  builder (Notes, Finish / Design Reference, Question for AI Review).

**Forbidden building elements check: passed.**

### Generated stair geometry

- One stair-related row drives geometry: `ZONE_STAIRS` (row 28), Geometry
  Type = `Opening/import zone`, Generate Geometry? = `Reference Only`. This
  is the import-only stair opening / cutout reference and is permitted by
  the master prompt.
- `ELEM_RAIL_STAIR_VOID` (row 58) is a stair-void guardrail (VERIFY),
  permitted as a safety / railing element, not a generated stair tread,
  riser, stringer, or landing.
- `RULE_F2_STAIR_TO_OVERLOOK_OPEN` (row 99) is a No-geometry rule.
- `SLAB_F2_CENTRAL` (row 18) is the F2 slab reference (with stair-cutout
  acknowledgement); reference only.
- Two incidental occurrences of "stair rail" appear in descriptive notes
  (rows 24, 58) and do not produce stair-tread / -riser / -stringer /
  -landing geometry.

**Generated stair geometry check: passed (no treads / risers / stringers /
landings / generated stair rails will be created).**

### Generated reception furniture

- One incidental occurrence of "reception counter" in row 72's
  `Finish / Design Reference` (a material-only row, Generate Geometry = No).
- No row has Category, Geometry Type, or Element ID indicating a reception
  desk, reception counter, reception casework, reception cabinetry, or
  reception millwork to be built.

**Generated reception/casework check: passed.**

### Exterior / site / parking / landscaping

- Five incidental occurrences of the substring `road` are inside the words
  `broad` (e.g. "Broad context bbox", "Broad walkable path", "Broad
  bounding box only"). These are not exterior road geometry.
- No row's Element ID, Category, Generate Geometry?, or Geometry Type
  indicates parking, landscaping, sidewalks, roads, terrain, exterior
  facade, or detailed roof construction.
- The roof rows that do exist (Section 08) are all interior-height /
  reference / closure rows (`ROOF_POLYGON_VERIFY`,
  `ROOF_HIGHBAY_TOP_REF`, `ROOF_LOBBY_CONN_TOP_REF`,
  `ROOF_STRUCT_ABOVE_HB`, `ROOF_OVERHANG_VERIFY`,
  `ROOF_PARAPET_EDGE`) and are either Reference Only / Debug Only / No.
  None will be built as exterior roof construction.

**Exterior / site / parking / landscaping check: passed.**

---

## 16. Locked design decisions detected and acknowledged

These will be enforced verbatim by the builder once approved:

- **Spawn** — row 25 `ZONE_SPAWN`:
  Center X Grid = -10.6043320328266, Center Y Grid = -1.5170187,
  Facing South, MAT_Debug_SpawnRed. Convert grid → meters by × 2.4384.
- **F2 overlook** — row 32: NE/NW/SW/SE polygon as listed in §11. Row 56
  glass wall locked at Base Z 3.09 m, Top Z 6.14 m (3.05 m / 10 ft).
  Row 99 forbids wall between stair cutout and SW corner.
- **OH_A overhead door** — row 42 `D_F1_OH_A`: Length Grid X = 0.97
  (Length M = 2.37 m), narrower than OH_B/OH_C as user-locked. Will not be
  defaulted to 1.5 grid.
- **GRND/RECYC + Pallet stacked relationship** — rows 31, 36, 60, 61.
  GRND/RECYC and Pallet share the same four-corner footprint. Plate row 61:
  Base Z 3.0138 m, Top Z 3.09 m (0.0762 m / 3 in), top flush with F2 slab.
- **High-bay** — row 30: Base Z 0, Top Z 10.31 m (interior height 10.31 m),
  rotation VERIFY. Materials: concrete slab + simple industrial wall.
- **Roof / top** — Top Z 11.91 m where applicable
  (`ROOF_POLYGON_VERIFY`, `ROOF_STRUCT_ABOVE_HB`); 1.61 m structure zone
  belongs only to `ROOF_STRUCT_ABOVE_HB`, not the entire roof polygon.

---

## 17. Pre-build sanity checks

- Scale: 1 grid = 2.4384 m — confirmed by row 6 (`RULE_SCALE`).
- Axes: +X East, -X West, +Y North, -Y South, +Z Up — confirmed by
  row 7 (`RULE_AXES`).
- F2 slab Z range: 3.09 m to ~3.39 m — consistent with row 18
  (`SLAB_F2_CENTRAL`) and the 3.09 m used in rows 32, 36, 56, 61.
- High-bay interior height: 10.31 m — consistent with row 30.
- Roof / top reference height: 11.91 m — consistent with rows 91 and 94.
- Plate flush with F2 slab: 3.09 m top, 3.0138 m bottom — consistent with
  row 61.
- All grid-unit coordinate columns present and parseable except where
  noted in §12.

---

## 18. Summary of intended next steps (awaiting approval)

If approved, the next message will produce, in order, three files only:

1. `extract_schedule_to_json.py`
   - Outside-Blender. Reads `NIMA_Phase2_Master_Schedule_LOCKED-3.xlsx`
     using `openpyxl` (`pandas` optional). Treats row 5 as the header.
   - Categorizes rows into: geometry-eligible, materials, rules, spot
     checks, verification references.
   - Parses Notes-driven polygons for rows 31, 32, 36, 91 using the
     priority order from the master prompt.
   - Scrubs forbidden terms from any text fields it forwards.
   - Emits `nima_schedule.json` plus an extractor validation report.
2. `build_nima_blender.py`
   - Blender 4.x. `DEBUG_MODE = True` at the top.
   - Reads `nima_schedule.json` only. Does not read the XLSX.
   - Creates the seven required collections, the deterministic material
     palette, and 3D solids (no zero-thickness planes for visible
     barriers). Polygons extruded with `bmesh`. Default thicknesses applied
     where Thickness M is missing. No FBX export. No simulations.
   - Emits the in-Blender validation report.
3. `README_run_instructions.md`
   - How to run the extractor (Python 3 + openpyxl), how to run the
     Blender script, expected outputs, troubleshooting, known limitations,
     and the full list of remaining VERIFY items (rows 30, 33, 34, 58, 67,
     91, 94, 95, 96, 103, 104, 105, etc.).

**Stopping here per the master prompt. Awaiting approval to generate the
three Python / Markdown files above.**
