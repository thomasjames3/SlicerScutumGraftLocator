"""
mesh_isolate.py
================
Shared logic for turning a surgeon-drawn outline on a mesh surface into an
isolated sub-mesh. This is used by the pinna stage (isolate the pinna from
the surrounding head skin) and will be reused for the scutum defect
drawing stage discussed earlier -- both are the same underlying operation:
"here's a closed loop on a surface, here's a point inside it, give me back
just that enclosed patch."

This module is intentionally VTK-free and Slicer-free. In the actual
Slicer wizard, the surgeon draws a curve by clicking points in the 3D
view; Slicer's Markups Curve tool resolves each click to an exact 3D point
already sitting on the mesh surface via ray-casting. By the time points
reach this module, they're just a list of (x, y, z) coordinates -- this
keeps the actual "which mesh vertices does this correspond to, and what's
enclosed" logic testable on its own, outside of Slicer.

Approach: build a vertex-adjacency graph of the mesh (using the same
networkx dependency Curvature Project v4 already uses for its own
patch-extraction logic in geodesics.py), treat the drawn loop's vertices as
a barrier, and flood-fill outward from a seed point to find the enclosed
region. This is deliberately similar to the geodesic patch extraction
already used in Curvature Project v4, so the same mental model applies in
both projects.
"""

from __future__ import annotations
import time
import numpy as np
import trimesh
import networkx as nx
from scipy.spatial import cKDTree

from config import PINNA_CANAL_CROP_MARGIN_MM


class LoopDoesNotSeparateError(ValueError):
    """Raised specifically when a drawn loop's vertices don't actually
    separate the mesh into two pieces (the flood fill reaches almost
    everything), as distinct from a malformed/too-small/disconnected loop.
    A caller may reasonably retry with a different (e.g. more heavily
    processed) mesh built from the same underlying segmentation and the
    same curve/seed points, without asking the surgeon to redraw anything
    -- see page_pinna_draw.py's fallback-mesh retry, added 2026-07-27 after
    confirming (via [isolate diag] console output on a real scan) that a
    perfectly well-drawn, densely-sampled, non-self-intersecting loop can
    still fail to separate a lightly-smoothed mesh because of an unrelated
    "handle" elsewhere on the surface (two nearby folds close enough to
    register as touching, more likely with less aggressive smoothing).
    Other ValueErrors from this module (too few points, a gap that leaves
    zero enclosed vertices, a disconnected seed) are NOT retry-worthy --
    those need the surgeon to actually redraw or re-click, so they stay
    plain ValueError.
    """


def snap_points_to_vertices(mesh: trimesh.Trimesh, points) -> list:
    """
    Convert a list of (x, y, z) points -- e.g. the surgeon's drawn curve --
    into the indices of the nearest mesh vertices.

    Since Slicer's curve tool ray-casts clicks directly onto the mesh
    surface, these points should already sit extremely close to (or
    exactly on) actual vertices, so nearest-neighbor snapping is reliable
    here rather than an approximation.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        The surface mesh the points were drawn on.
    points : sequence of (x, y, z)
        The drawn curve, in the same physical coordinate space as the mesh.

    Returns
    -------
    list of int
        Vertex indices, in the same order as the input points, with
        consecutive duplicates removed (which can happen if the surgeon
        clicks two points that snap to the same vertex).
    """
    tree = cKDTree(mesh.vertices)
    _, indices = tree.query(np.asarray(points))

    deduped = []
    for idx in indices:
        if not deduped or deduped[-1] != idx:
            deduped.append(int(idx))
    return deduped


def isolate_surface_patch(
    mesh: trimesh.Trimesh,
    loop_vertex_indices,
    seed_point,
) -> trimesh.Trimesh:
    """
    Extract the sub-mesh enclosed by a closed loop of vertices, on the side
    containing `seed_point`.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        The full surface mesh (e.g. the cropped skin-surface blob from
        segment_pinna_threshold, converted to a mesh).
    loop_vertex_indices : sequence of int
        Vertex indices forming a closed loop on the mesh surface -- the
        output of snap_points_to_vertices() on the surgeon's drawn curve.
        The loop should form a single closed boundary (last point
        connecting back to the first); this function does not check for
        or fix self-intersecting loops, so a malformed drawn curve can
        produce an unexpected result.
    seed_point : (x, y, z)
        A single point inside the region the surgeon wants to keep -- in
        the wizard, this could be an extra click ("click once inside the
        outline") or simply the average of the loop's own points if the
        loop is reliably convex-ish (the pinna's rough oval shape makes
        this a reasonable default, but an explicit seed click is more
        robust for irregular shapes and is what's used here).

    Returns
    -------
    trimesh.Trimesh
        An open-surface sub-mesh containing only the region enclosed by
        the loop, on the seed_point's side. Not watertight -- this is
        expected and fine for the mesh-comparison use case here.
    """
    raw_loop_set = set(loop_vertex_indices)
    if len(raw_loop_set) < 3:
        raise ValueError(
            f"The drawn outline needs at least 3 distinct points to enclose "
            f"a region, but only {len(raw_loop_set)} distinct point(s) were "
            f"found on the mesh after snapping your {len(loop_vertex_indices)} "
            "drawn point(s) to its surface. This usually means the drawn "
            "curve isn't actually landing on the mesh surface (check that "
            "it visually hugs the mesh's contours as you draw, not floating "
            "above/through it), or that the points were clicked very close "
            "together. Please re-draw with points spread further apart."
        )

    # TEMPORARY TIMING INSTRUMENTATION (2026-07-30, see CLAUDE.md "Pinna
    # segmentation performance") -- mesh_export.decimate_to_target_resolution()
    # was added to shrink the pinna mesh before it ever reaches this
    # function, but Thomas reports no noticeable speedup on a real re-test.
    # These prints (Slicer Python console) time every real step of this
    # function individually, so the next report can point at the actual
    # slow step instead of guessing again. Remove once the bottleneck here
    # is found and addressed.
    _t_isolate_total = time.time()

    # TEMPORARY DIAGNOSTICS (2026-07-27) -- prints to the Slicer Python
    # console (View > Python console). Added to investigate a report that
    # "isolated region covers almost the entire mesh" started firing on
    # drawings that used to work, coinciding with a fix elsewhere that
    # changed how much the mesh gets smoothed before this function ever
    # sees it. Rather than guess at a second fix blind, this prints enough
    # to tell whether the mesh is unexpectedly fragmented (many small
    # components) vs. genuinely leaking within one component. Remove once
    # this is root-caused.
    _t0 = time.time()
    num_components = len(mesh.split(only_watertight=False))
    print(f"[isolate timing] mesh.split (component count): {time.time() - _t0:.2f}s")
    print(
        f"[isolate diag] mesh: {len(mesh.vertices)} vertices, "
        f"{len(mesh.faces)} faces, {num_components} connected component(s)"
    )
    # Euler characteristic (V - E + F) directly reveals genuine topological
    # handles, no inference from flood-fill behavior needed: for a closed,
    # connected surface, chi = 2 - 2*genus. chi=2 means genus 0 (sphere/
    # disk-like, no handles -- what isolate_surface_patch's "draw a loop,
    # it separates the surface" assumption needs). Each handle (e.g. the
    # ear canal's tube touching another part of the mesh, per Thomas's
    # hypothesis 2026-07-27) lowers this by 2. is_watertight/boundary edge
    # count checks the other possibility: an OPEN mesh (e.g. the ear canal
    # tube getting cut off by the ROI crop without a proper closing cap),
    # which is a structurally different problem from a genuine handle.
    # trimesh has no `edges_boundary` attribute (confirmed the hard way --
    # this crashed in real use); boundary edges are ones used by exactly
    # one face, found via grouping.group_rows with require_count=1.
    _t0 = time.time()
    _boundary_edge_count = len(
        trimesh.grouping.group_rows(mesh.edges_sorted, require_count=1)
    )
    print(f"[isolate timing] euler_number/boundary-edge topology check: {time.time() - _t0:.2f}s")
    print(
        f"[isolate diag] mesh topology: euler_number={mesh.euler_number} "
        f"(2=genus-0/no handles, lower=handles present), "
        f"is_watertight={mesh.is_watertight}, "
        f"boundary edges={_boundary_edge_count}"
    )
    print(
        f"[isolate diag] drawn loop: {len(loop_vertex_indices)} points, "
        f"{len(raw_loop_set)} distinct vertices before bridging"
    )

    _t0 = time.time()
    graph = mesh.vertex_adjacency_graph  # networkx.Graph, one node per vertex
    print(f"[isolate timing] vertex_adjacency_graph build: {time.time() - _t0:.2f}s")

    # loop_vertex_indices are the drawn curve's control points snapped to
    # their nearest vertices, in click order -- but consecutive clicks are
    # almost always several mesh edges apart (mesh vertex spacing is much
    # finer than click spacing), so treating just these snapped points as
    # the barrier leaves real gaps in the mesh graph for the flood fill to
    # leak through, even though the curve looks fully closed on screen
    # (Slicer's ShortestDistanceOnSurface curve type interpolates the
    # actual path between clicks, which this code wasn't accounting for).
    # Bridge every consecutive pair -- and the last point back to the
    # first, closing the loop -- with the shortest path along the mesh's
    # own surface, so the barrier has no gaps regardless of click spacing.
    _t0 = time.time()
    loop_set = set(loop_vertex_indices)
    n = len(loop_vertex_indices)
    for i in range(n):
        a = loop_vertex_indices[i]
        b = loop_vertex_indices[(i + 1) % n]  # wraps last -> first
        if a == b:
            continue
        try:
            bridge = nx.shortest_path(graph, a, b)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            raise ValueError(
                "Part of the drawn outline isn't connected along the mesh "
                "surface, so the loop can't be closed. Please redraw the "
                "outline."
            )
        loop_set.update(bridge)

    print(f"[isolate timing] loop bridging ({n} shortest-path calls): {time.time() - _t0:.2f}s")
    print(f"[isolate diag] loop after bridging: {len(loop_set)} vertices")

    # Find the vertex closest to the seed point to start the flood fill
    # from -- searching only among non-loop vertices, so a seed click that's
    # genuinely inside the loop never gets rejected just because its
    # single nearest neighbor (among ALL vertices) happens to be a loop
    # vertex. That's a real, common case on a coarse mesh: if the loop
    # encloses only a few vertices, the boundary vertices are often closer
    # to any interior click than the true interior ones are to each other.
    _t0 = time.time()
    non_loop_indices = np.fromiter(
        (i for i in range(len(mesh.vertices)) if i not in loop_set),
        dtype=int,
    )
    print(f"[isolate timing] non_loop_indices (Python loop over all vertices): {time.time() - _t0:.2f}s")
    if len(non_loop_indices) == 0:
        raise ValueError(
            "The drawn outline doesn't enclose any mesh vertices besides "
            "its own boundary -- it may be too small for this mesh's "
            "resolution. Please try drawing a larger outline."
        )

    _t0 = time.time()
    tree = cKDTree(mesh.vertices[non_loop_indices])
    snap_dist, nearest_pos = tree.query(np.asarray(seed_point))
    seed_idx = int(non_loop_indices[nearest_pos])
    print(f"[isolate timing] seed snapping (cKDTree build + query): {time.time() - _t0:.2f}s")
    print(
        f"[isolate diag] seed snapped {snap_dist:.2f}mm from the clicked "
        f"point (large = may have snapped to the wrong side of a thin fold)"
    )

    # Restrict the flood fill to a local neighborhood around the seed point
    # before removing the loop's vertices, sized from the loop's own
    # spatial extent (with a safety margin). Confirmed on a real scan
    # (2026-07-27) that a mesh with only one connected component can still
    # defeat a perfectly-closed drawn loop: removing the loop's vertices
    # left the *entire rest of the mesh* as a single connected piece
    # (99.8% reached, exactly mesh_vertex_count - loop_vertex_count) --
    # meaning the loop wasn't failing to close, it just wasn't a
    # topologically separating cut. This happens when the mesh has a
    # "handle"/tunnel elsewhere entirely (e.g. two nearby folds of the
    # pinna just barely touching, a real possibility at CT resolution or
    # with lighter smoothing) that gives the surface a back door from
    # inside the loop to the rest of the mesh, bypassing the loop
    # entirely. A globally-applied fix (more morphological smoothing to
    # try to seal such handles shut) was rejected: it's the same knob that
    # was found to erode real thin anatomy like the helix (see Known
    # Issues in CLAUDE.md), so tuning it further just trades one failure
    # mode for the other. This spatial restriction sidesteps the mesh's
    # global topology entirely -- a small drawn loop should only ever need
    # to enclose vertices reasonably close to where it was actually drawn,
    # so a distant handle can't be reached regardless of whether it
    # exists.
    _t0 = time.time()
    loop_positions = mesh.vertices[list(loop_set)]
    seed_position = mesh.vertices[seed_idx]
    max_loop_dist = float(np.max(np.linalg.norm(loop_positions - seed_position, axis=1)))
    search_radius = max_loop_dist * 2.0
    nearby_indices = cKDTree(mesh.vertices).query_ball_point(seed_position, r=search_radius)
    nearby_set = set(nearby_indices)
    print(f"[isolate timing] local-radius search (cKDTree over ALL vertices): {time.time() - _t0:.2f}s")
    print(
        f"[isolate diag] local search radius: {search_radius:.1f}mm, "
        f"{len(nearby_set)} vertices within range"
    )

    # Flood-fill outward from the seed, treating loop vertices as walls the
    # traversal cannot pass through -- this is what confines the fill to
    # "inside the loop" rather than spreading across the whole mesh. Built
    # from the local subgraph (nearby_set) rather than the full mesh graph,
    # per the restriction above.
    _t0 = time.time()
    barrier_graph = graph.subgraph(nearby_set).copy()
    barrier_graph.remove_nodes_from(loop_set)
    print(f"[isolate timing] barrier_graph subgraph + remove_nodes_from: {time.time() - _t0:.2f}s")

    if seed_idx not in barrier_graph:
        raise ValueError(
            "Could not start the region fill from the seed point -- it may "
            "not be connected to the rest of the mesh. Please try a "
            "different seed point."
        )

    _t0 = time.time()
    reached = nx.node_connected_component(barrier_graph, seed_idx)
    print(f"[isolate timing] flood fill (node_connected_component): {time.time() - _t0:.2f}s")
    local_total = len(nearby_set) - len(loop_set & nearby_set)
    print(
        f"[isolate diag] flood fill reached {len(reached)} of "
        f"{local_total} locally-reachable vertices "
        f"({len(reached) / local_total:.1%} local, "
        f"{len(reached) / len(mesh.vertices):.1%} of whole mesh)"
    )

    # Sanity check: if the flood fill reached a large fraction of the
    # *local* search region, the loop likely isn't actually closed (there's
    # a gap letting the fill leak out within its own neighborhood), and the
    # result would be the wrong region rather than a genuine mistake worth
    # silently returning. Checked against the local neighborhood, not the
    # whole mesh, now that the search itself is spatially restricted.
    if len(reached) > 0.9 * local_total:
        raise LoopDoesNotSeparateError(
            "The isolated region covers almost the entire area near the "
            "seed point. This can mean the drawn outline has a gap and "
            "isn't fully closed, but on a well-drawn outline it usually "
            "means the mesh has an unrelated thin connection elsewhere "
            "(e.g. two nearby folds just barely touching) that lets the "
            "fill bypass the loop entirely -- please double check the "
            "drawn loop connects back to its starting point, but if it "
            "looks correct, this may resolve itself automatically."
        )

    # Include the loop's own vertices in the output so the resulting patch
    # has a clean, closed boundary edge rather than a ragged one stopping
    # just short of the drawn line.
    keep_vertices = reached | loop_set

    _t0 = time.time()
    result = _submesh_from_vertices(mesh, keep_vertices)
    print(f"[isolate timing] _submesh_from_vertices (incl. np.vectorize remap): {time.time() - _t0:.2f}s")
    print(f"[isolate timing] isolate_surface_patch TOTAL: {time.time() - _t_isolate_total:.2f}s")

    return result


def crop_toward_canal(
    mesh: trimesh.Trimesh,
    plane_point,
    axis_direction,
    margin_mm: float = PINNA_CANAL_CROP_MARGIN_MM,
) -> trimesh.Trimesh:
    """
    Removes any part of `mesh` that lies toward the interior of the head
    from `plane_point`.

    Originally run AFTER isolate_surface_patch(), as cleanup for a sliver
    of head-side attachment left on the isolated pinna patch. Moved to
    run BEFORE isolate_surface_patch instead (2026-07-27, Thomas's
    suggestion) -- see page_pinna_draw.py's `_crop_toward_canal_if_possible()`
    for why: discarding the head-interior material first removes whatever
    genuine mesh-topology handles/tunnels were hiding in it (that material
    was always going to be thrown away eventually, so there's nothing
    lost by removing it earlier) before isolate_surface_patch's "a closed
    loop separates the surface" assumption ever has to hold up against
    them. Works identically either way -- this function only ever cared
    about vertex positions relative to a cutting plane, not whether the
    mesh it's given is the full pre-isolation blob or an already-isolated
    patch.

    `plane_point` and `axis_direction` are deliberately separate:
    `plane_point` should be a marker placed directly on *this* pinna
    mesh, right at the ear canal opening (see page_pinna_draw.py's
    "Mark Canal Opening" step) -- reusing the ear canal's own
    `canal_opening` landmark here isn't reliable, since it was placed
    earlier on a different mesh/context and doesn't necessarily sit far
    enough outward relative to this specific pinna geometry.
    `axis_direction` only supplies "which way is into the head" and can
    still come from the ear canal's own landmarks
    (`near_eardrum - canal_opening`), since that direction is a stable
    anatomical fact independent of exactly where the plane needs to sit.

    The cut plane passes through `plane_point`, perpendicular to
    `axis_direction`, extended outward by `margin_mm` so an imprecisely
    placed marker doesn't clip the pinna's own tissue right at its base.
    Everything on the interior side (the direction `axis_direction`
    points) is discarded; everything on the outward (visible pinna) side
    is kept. This crop can leave the interior material as one or more
    disconnected islands rather than fully removing it in one clean cut
    -- see keep_connected_component_containing() to clean those up
    afterward.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        The pinna mesh to crop -- either the full pre-isolation
        skin-surface blob (current usage) or an already-isolated patch
        (original usage); this function doesn't care which.
    plane_point : (x, y, z)
        Where the cut plane sits -- the surgeon's canal-opening marker on
        this mesh.
    axis_direction : (x, y, z)
        A vector (need not be unit length) pointing from `plane_point`
        toward the interior of the head -- e.g.
        `near_eardrum - canal_opening` from the ear canal's own
        landmarks.
    margin_mm : float
        How far outward (past `plane_point`, away from the interior) the
        cut plane is shifted before clipping. See
        PINNA_CANAL_CROP_MARGIN_MM in config.py for tuning guidance.

    Returns
    -------
    trimesh.Trimesh
        The mesh with the head-interior side removed.
    """
    plane_point = np.array(plane_point, dtype=float)
    axis_direction = np.array(axis_direction, dtype=float)
    axis_length = np.linalg.norm(axis_direction)
    if axis_length < 1e-8:
        raise ValueError(
            "Could not determine which direction is into the head -- the "
            "axis direction is zero-length."
        )
    axis_unit = axis_direction / axis_length  # points inward

    along_axis = (mesh.vertices - plane_point) @ axis_unit
    keep_mask = along_axis <= margin_mm  # keep the outward (pinna) side

    keep_vertices = set(np.nonzero(keep_mask)[0].tolist())
    if not keep_vertices:
        raise ValueError(
            "Cropping toward the ear canal removed the entire pinna mesh -- "
            "double-check the canal opening marker looks correctly placed "
            "on this ear."
        )

    return _submesh_from_vertices(mesh, keep_vertices)


def keep_connected_component_containing(
    mesh: trimesh.Trimesh, reference_point
) -> trimesh.Trimesh:
    """
    Splits `mesh` into its connected components and returns only the one
    containing (or nearest to) `reference_point`, discarding the rest.

    Cleanup pass meant to run after crop_toward_canal(): a single plane
    cut doesn't always cleanly sever the head-interior material in one
    piece -- it commonly leaves it as one or more "islands" disconnected
    from the main pinna body, since that material was only ever attached
    to the pinna at/near the canal opening in the first place. Discarding
    every component except the one containing a known-good reference
    point (e.g. the surgeon's own seed point from "Mark Inside Point",
    which is guaranteed to be on the pinna) cleans these up regardless of
    how many disconnected islands there are or how large they are.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        The mesh to clean up (e.g. the output of crop_toward_canal()).
    reference_point : (x, y, z)
        A point known to be on the piece that should be kept.

    Returns
    -------
    trimesh.Trimesh
        Just the connected component containing/nearest to
        `reference_point`. If the mesh was already a single connected
        piece, it's returned unchanged.
    """
    components = mesh.split(only_watertight=False)
    if len(components) <= 1:
        return mesh

    reference_point = np.asarray(reference_point, dtype=float)
    best_component = None
    best_dist = np.inf
    for component in components:
        tree = cKDTree(component.vertices)
        dist, _ = tree.query(reference_point)
        if dist < best_dist:
            best_dist = dist
            best_component = component

    return best_component


def _submesh_from_vertices(mesh: trimesh.Trimesh, vertex_indices: set) -> trimesh.Trimesh:
    """
    Extract the sub-mesh containing only faces whose vertices are all in
    `vertex_indices`, and re-index everything so the result is a clean,
    standalone mesh (not just a masked view of the original).
    """
    face_mask = np.all(np.isin(mesh.faces, list(vertex_indices)), axis=1)
    kept_faces = mesh.faces[face_mask]

    used_vertex_ids = np.unique(kept_faces)
    remap = {old: new for new, old in enumerate(used_vertex_ids)}

    new_vertices = mesh.vertices[used_vertex_ids]
    new_faces = np.vectorize(remap.get)(kept_faces)

    return trimesh.Trimesh(vertices=new_vertices, faces=new_faces, process=True)
