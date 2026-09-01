"""
geodesics.py
------------
Approximates geodesic (surface) distances using shortest paths along the
mesh's own edge graph, and uses that to:
  1. Extract a "patch" (submesh) around a given vertex, sized by geodesic
     radius rather than straight-line radius -- this matters because the
     pinna is curved, so straight-line distance would cut patches short.
  2. Estimate the geodesic diameter and approximate geodesic center of a
     mesh (used to size the defect and generate an intrinsic,
     bending-invariant "distance from center" shape descriptor).
  3. Measure a patch's boundary (perimeter, area, compactness, aspect
     ratio) as simple pose-invariant footprint descriptors.

The standalone Curvature Project v4 used potpourri3d's heat-method geodesic
solver here. potpourri3d is a compiled extension, the same category of
dependency (alongside pymeshlab and open3d) that originally forced this
whole comparison to run as a subprocess in a separate venv rather than
directly in Slicer's own Python. Ported here to use
scipy.sparse.csgraph.dijkstra over the mesh's own vertex-adjacency graph
instead -- a standard, well-established approximation to true geodesic
distance (shortest path constrained to existing mesh edges, so it reads
systematically a little *longer* than the true continuous geodesic
distance, but consistently so for both the defect and every candidate,
which is what the scoring in scoring.py actually depends on). scipy is
already a required dependency of this extension's segmentation pipeline
(see ../../dependencies.py), so this adds no new install.
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra


@dataclass
class BoundaryShape:
    area: float
    perimeter: float
    compactness: float   # perimeter^2 / (4*pi*area); 1.0 for a perfect circle
    aspect_ratio: float   # >= 1.0; how elongated the footprint is
    hole_count: int = 0   # number of interior boundary loops (e.g. damage holes),
                           # excluded from perimeter/compactness/aspect_ratio below


def _build_edge_graph(mesh: trimesh.Trimesh) -> coo_matrix:
    """
    Sparse (n_vertices, n_vertices) graph with edge weights = Euclidean
    edge length, one entry per unique undirected edge. Passed to
    scipy's dijkstra with directed=False, which treats each stored entry
    as usable in either direction -- correct here since edge length is
    symmetric (see scipy.sparse.csgraph.dijkstra's own docs/warning about
    directed=False, which only applies when a direction's distance would
    differ from its reverse).
    """
    edges = mesh.edges_unique
    lengths = mesh.edges_unique_length
    n = len(mesh.vertices)
    return coo_matrix(
        (lengths, (edges[:, 0], edges[:, 1])), shape=(n, n)
    ).tocsr()


class GeodesicPatchExtractor:
    """
    Builds the mesh's edge graph once per mesh (this is the expensive
    part), then lets you cheaply query approximate geodesic distances /
    patches from many different source vertices.
    """

    def __init__(self, mesh: trimesh.Trimesh):
        self.mesh = mesh
        self._graph = _build_edge_graph(mesh)

    def distances_from(self, vertex_idx: int, limit: float = np.inf) -> np.ndarray:
        """
        Approximate geodesic distance from `vertex_idx` to every vertex in
        the mesh (graph shortest-path along mesh edges). `limit` lets
        callers that only care about nearby vertices (e.g. extract_patch)
        stop the search early for speed -- vertices beyond `limit` come
        back as inf, same as an unreachable vertex would.
        """
        return dijkstra(self._graph, directed=False, indices=vertex_idx, limit=limit)

    def geodesic_diameter(self) -> float:
        """
        Approximate geodesic diameter of the whole mesh, using the
        standard two-pass trick:
          1. pick any vertex, find the farthest vertex from it (call it A)
          2. find the farthest vertex from A (call it B)
          3. distance(A, B) approximates the true diameter
        """
        d0 = self.distances_from(0)
        a = int(np.argmax(d0))
        d_a = self.distances_from(a)
        b = int(np.argmax(d_a))
        return float(d_a[b])

    def approximate_geodesic_center(self):
        """
        Approximate the geodesic center of the whole mesh (a point that
        minimizes the maximum geodesic distance to any other point --
        i.e. an approximate 1-center), and return the geodesic distance
        from that center to every vertex.

        This "distance from center" array is bending-invariant: unlike
        curvature, it doesn't change if the surface is bent (only if it's
        stretched). It's used as an intrinsic, isometry-invariant shape
        descriptor -- see signature.py's radial_distance_hist -- which
        matters here because cartilage bends readily but resists
        stretching, so a good coarse score shouldn't penalize a candidate
        purely for having different curvature if its *intrinsic* spread
        (how far points are from the center, along the surface) already
        matches the defect well.

        Reuses the same two extreme points found by geodesic_diameter()
        (A and B), so this costs one extra distance solve beyond that,
        not several.

        Returns
        -------
        center_idx : int
        distances_from_center : np.ndarray
        diameter : float
            The same approximate diameter geodesic_diameter() would give,
            returned here too so callers don't need a second, separate
            (and separately expensive) call.
        """
        d0 = self.distances_from(0)
        a = int(np.argmax(d0))
        d_a = self.distances_from(a)
        b = int(np.argmax(d_a))
        diameter = float(d_a[b])
        d_b = self.distances_from(b)

        # Approximate 1-center: the point minimizing its worst-case
        # distance to either extreme point of the diameter.
        eccentricity_bound = np.maximum(d_a, d_b)
        center_idx = int(np.argmin(eccentricity_bound))
        distances_from_center = self.distances_from(center_idx)

        return center_idx, distances_from_center, diameter

    def extract_patch(self, center_idx: int, radius: float):
        """
        Extract the submesh consisting of all faces whose vertices are
        entirely within `radius` geodesic distance of `center_idx`.

        Returns
        -------
        patch_mesh : trimesh.Trimesh or None
            None if the resulting patch has no faces (radius too small).
        vertex_ids : np.ndarray
            Indices of the kept vertices in the *original* mesh, useful
            for mapping patch results back onto the full pinna mesh.
        radial_distances : np.ndarray
            Geodesic distance from `center_idx` to each kept vertex, in
            the same order as `vertex_ids` / `patch_mesh.vertices`. This
            is essentially free (it's exactly the distance array already
            used to decide which vertices to keep), and doubles as an
            intrinsic, bending-invariant shape descriptor for the patch.
        """
        dist = self.distances_from(center_idx, limit=radius)
        keep_vertex_mask = dist <= radius
        if keep_vertex_mask.sum() < 3:
            return None, np.array([], dtype=int), np.array([], dtype=float)

        faces = self.mesh.faces
        face_mask = keep_vertex_mask[faces].all(axis=1)
        if face_mask.sum() == 0:
            return None, np.array([], dtype=int), np.array([], dtype=float)

        kept_faces = faces[face_mask]
        used_vertex_ids = np.unique(kept_faces)

        # Remap to a compact 0..N-1 index space for the submesh.
        remap = -np.ones(len(self.mesh.vertices), dtype=int)
        remap[used_vertex_ids] = np.arange(len(used_vertex_ids))
        new_faces = remap[kept_faces]
        new_vertices = self.mesh.vertices[used_vertex_ids]

        patch_mesh = trimesh.Trimesh(vertices=new_vertices, faces=new_faces,
                                      process=False)
        radial_distances = dist[used_vertex_ids]
        return patch_mesh, used_vertex_ids, radial_distances


def boundary_shape_descriptors(patch_mesh: trimesh.Trimesh) -> BoundaryShape:
    """
    Compute simple pose-invariant footprint descriptors for an open-surface
    patch: surface area, boundary perimeter, compactness (isoperimetric
    ratio), and aspect ratio (elongation of the boundary loop).
    """
    area = float(patch_mesh.area)

    try:
        outline = patch_mesh.outline()
    except Exception:
        outline = None

    if outline is None or len(outline.entities) == 0:
        # No clear boundary found (shouldn't normally happen for an open
        # patch) -- fall back to neutral-ish values so scoring doesn't crash.
        return BoundaryShape(area=area, perimeter=0.0, compactness=np.inf,
                              aspect_ratio=1.0, hole_count=0)

    # A mesh with damage/holes has multiple boundary loops: one around the
    # true outer footprint, plus one around each hole. We only want the
    # outer footprint's shape for matching purposes (holes are treated as
    # damage/imaging artifacts here, not part of the target shape) -- so
    # we identify the *longest* loop as the outer boundary and ignore the
    # rest for perimeter/compactness/aspect_ratio.
    entity_lengths = [_entity_length(e, outline.vertices) for e in outline.entities]
    outer_idx = int(np.argmax(entity_lengths))
    perimeter = float(entity_lengths[outer_idx])
    hole_count = len(outline.entities) - 1

    if area <= 0 or perimeter <= 0:
        compactness = np.inf
    else:
        compactness = (perimeter ** 2) / (4.0 * np.pi * area)

    # Aspect ratio from PCA of the *outer* boundary loop's own vertices
    # only (using the boundary points directly keeps this valid even for
    # curved patches, where "project onto a single flat plane" would
    # distort a lot).
    outer_entity = outline.entities[outer_idx]
    boundary_points = outline.vertices[np.unique(outer_entity.points)]

    if boundary_points.shape[0] >= 3:
        centered = boundary_points - boundary_points.mean(axis=0)
        cov = np.cov(centered.T)
        eigvals = np.sort(np.linalg.eigvalsh(cov))[::-1]  # descending
        eigvals = np.clip(eigvals, 1e-12, None)
        aspect_ratio = float(np.sqrt(eigvals[0] / eigvals[-1]))
    else:
        aspect_ratio = 1.0

    return BoundaryShape(area=area, perimeter=perimeter,
                          compactness=compactness, aspect_ratio=aspect_ratio,
                          hole_count=hole_count)


def _entity_length(entity, vertices: np.ndarray) -> float:
    """Arc length of a single boundary loop entity."""
    pts = vertices[entity.points]
    return float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())
