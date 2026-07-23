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
import numpy as np
import trimesh
import networkx as nx
from scipy.spatial import cKDTree


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
    loop_set = set(loop_vertex_indices)
    if len(loop_set) < 3:
        raise ValueError(
            "The drawn outline needs at least 3 distinct points to enclose "
            "a region. Please draw a closed loop with more points."
        )

    graph = mesh.vertex_adjacency_graph  # networkx.Graph, one node per vertex

    # Find the vertex closest to the seed point to start the flood fill from.
    tree = cKDTree(mesh.vertices)
    _, seed_idx = tree.query(np.asarray(seed_point))
    seed_idx = int(seed_idx)

    if seed_idx in loop_set:
        raise ValueError(
            "The seed point landed on the drawn outline itself rather than "
            "inside it. Please click a point clearly inside the loop, away "
            "from the drawn line."
        )

    # Flood-fill outward from the seed, treating loop vertices as walls the
    # traversal cannot pass through -- this is what confines the fill to
    # "inside the loop" rather than spreading across the whole mesh.
    barrier_graph = graph.copy()
    barrier_graph.remove_nodes_from(loop_set)

    if seed_idx not in barrier_graph:
        raise ValueError(
            "Could not start the region fill from the seed point -- it may "
            "not be connected to the rest of the mesh. Please try a "
            "different seed point."
        )

    reached = nx.node_connected_component(barrier_graph, seed_idx)

    # Sanity check: if the flood fill reached a large fraction of the whole
    # mesh, the loop likely isn't actually closed (there's a gap letting
    # the fill leak out), and the result would be the wrong region rather
    # than a genuine mistake worth silently returning.
    if len(reached) > 0.9 * len(mesh.vertices):
        raise ValueError(
            "The isolated region covers almost the entire mesh, which "
            "usually means the drawn outline has a gap and isn't fully "
            "closed. Please double check the drawn loop connects back to "
            "its starting point."
        )

    # Include the loop's own vertices in the output so the resulting patch
    # has a clean, closed boundary edge rather than a ragged one stopping
    # just short of the drawn line.
    keep_vertices = reached | loop_set

    return _submesh_from_vertices(mesh, keep_vertices)


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
