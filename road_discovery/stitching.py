"""Route stitching algorithms: Assembles disconnected road segments into continuous driving corridors."""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Set, Tuple
import networkx as nx
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import linemerge

from .config import DiscoveryConfig
from .geometry import (
    calculate_line_length_km,
    calculate_bearing_deg,
    haversine_distance_m,
)


@dataclass
class StitchedRoute:
    """Represents a continuous driving corridor stitched from multiple road segments."""

    road_name: str
    ref: Optional[str]
    highway_type: str
    geometry: LineString
    length_km: float
    avg_speed_limit: float
    intersection_count: int
    signal_count: int
    intersection_density: float
    signal_density: float
    business_count: int = 0
    driveway_count: int = 0
    business_density: float = 0.0
    driveway_density: float = 0.0
    smoothness_penalty: float = 0.0
    road_quality_label: str = "Smooth Pavement"


class RouteStitcher:
    """Stitches adjacent road segments with matching names/refs and heading continuity."""

    def __init__(self, config: Optional[DiscoveryConfig] = None):
        self.config = config or DiscoveryConfig()

    def stitch_network(self, G: nx.MultiDiGraph) -> List[StitchedRoute]:
        """Process the filtered road network graph and stitch into continuous driving routes.

        Args:
            G: Filtered NetworkX MultiDiGraph

        Returns:
            List of StitchedRoute instances meeting the minimum length criteria.
        """
        if len(G.edges) == 0:
            return []

        # Find traffic signals nodes
        signal_nodes = set()
        for node_id, data in G.nodes(data=True):
            if data.get("highway") == "traffic_signals" or data.get("traffic_signals") == "signal":
                signal_nodes.add(node_id)

        # Group edges by road identity (ref or name)
        groups = self._group_edges_by_identity(G)

        stitched_routes: List[StitchedRoute] = []

        for group_key, edges_in_group in groups.items():
            routes = self._stitch_edge_group(G, edges_in_group, signal_nodes)
            stitched_routes.extend(routes)

        # Filter by minimum route length
        valid_routes = [
            r for r in stitched_routes if r.length_km >= self.config.min_route_length_km
        ]

        # Sort by length descending
        valid_routes.sort(key=lambda r: r.length_km, reverse=True)
        return valid_routes

    def _group_edges_by_identity(
        self, G: nx.MultiDiGraph
    ) -> Dict[str, List[Tuple[Any, Any, Any, Dict[str, Any]]]]:
        """Cluster edges by canonical ref or name."""
        groups: Dict[str, List[Tuple[Any, Any, Any, Dict[str, Any]]]] = {}
        seen_undirected_edges: Set[Tuple[Any, Any]] = set()

        for u, v, k, data in G.edges(keys=True, data=True):
            # De-duplicate reciprocal bidirectional edges
            edge_key = tuple(sorted([str(u), str(v)]))
            if edge_key in seen_undirected_edges:
                continue
            seen_undirected_edges.add(edge_key)

            ref = data.get("canonical_ref")
            name = data.get("canonical_name")

            # Prioritize route ref (e.g., 'US 129', 'CA 2') over local street name
            if ref and ref != "None":
                group_id = f"ref::{ref}"
            elif name and name != "Unnamed Road" and name != "None":
                group_id = f"name::{name}"
            else:
                # Group unnamed roads by connected components
                group_id = f"unnamed::{u}_{v}"

            if group_id not in groups:
                groups[group_id] = []
            groups[group_id].append((u, v, k, data))

        return groups

    def _stitch_edge_group(
        self,
        G: nx.MultiDiGraph,
        edges: List[Tuple[Any, Any, Any, Dict[str, Any]]],
        signal_nodes: Set[Any],
    ) -> List[StitchedRoute]:
        """Merge a group of related edges into one or more continuous LineStrings."""
        if not edges:
            return []

        geometries = [data["geometry"] for _, _, _, data in edges if "geometry" in data]
        if not geometries:
            return []

        # Use Shapely linemerge to sew contiguous endpoints into continuous LineStrings
        merged = linemerge(geometries)

        lines: List[LineString] = []
        if isinstance(merged, LineString):
            lines = [merged]
        elif isinstance(merged, MultiLineString):
            lines = list(merged.geoms)
        else:
            return []

        routes: List[StitchedRoute] = []

        # Compute aggregate properties across member edges
        total_edge_length_m = sum(data.get("length", 1.0) for _, _, _, data in edges)
        if total_edge_length_m <= 0:
            total_edge_length_m = 1.0

        # Length-weighted speed limit & smoothness penalty
        weighted_speed = sum(
            data.get("speed_mph", self.config.target_speed_mph)
            * data.get("length", 1.0)
            for _, _, _, data in edges
        ) / total_edge_length_m

        weighted_smoothness_penalty = sum(
            data.get("smoothness_penalty", 0.0) * data.get("length", 1.0)
            for _, _, _, data in edges
        ) / total_edge_length_m

        if weighted_smoothness_penalty >= 20.0:
            quality_label = "Rough / Damaged Surface"
        elif weighted_smoothness_penalty >= 5.0:
            quality_label = "Moderate Bumps"
        else:
            quality_label = "Smooth Pavement"

        dominant_highway = edges[0][3].get("highway_type", "secondary")
        canonical_name = edges[0][3].get("canonical_name", "Unnamed Road")
        canonical_ref = edges[0][3].get("canonical_ref")

        # Format descriptive road label
        if canonical_ref and canonical_name and canonical_ref not in canonical_name:
            road_label = f"{canonical_ref} ({canonical_name})"
        elif canonical_ref:
            road_label = canonical_ref
        else:
            road_label = canonical_name

        # Count commercial businesses and driveways across member edges
        biz_count = sum(1 for _, _, _, d in edges if d.get("is_commercial"))
        drv_count = sum(1 for _, _, _, d in edges if d.get("is_driveway"))

        for line in lines:
            length_km = calculate_line_length_km(line)
            if length_km <= 0.05:
                continue

            # Identify intersections and signals along this line
            int_count, sig_count = self._count_junctions_along_line(G, line, signal_nodes)
            int_density = round(int_count / max(0.1, length_km), 2)
            sig_density = round(sig_count / max(0.1, length_km), 2)
            biz_density = round(biz_count / max(0.1, length_km), 2)
            drv_density = round(drv_count / max(0.1, length_km), 2)

            routes.append(
                StitchedRoute(
                    road_name=road_label,
                    ref=canonical_ref,
                    highway_type=dominant_highway,
                    geometry=line,
                    length_km=round(length_km, 2),
                    avg_speed_limit=round(weighted_speed, 1),
                    intersection_count=int_count,
                    signal_count=sig_count,
                    intersection_density=int_density,
                    signal_density=sig_density,
                    business_count=biz_count,
                    driveway_count=drv_count,
                    business_density=biz_density,
                    driveway_density=drv_density,
                    smoothness_penalty=round(weighted_smoothness_penalty, 1),
                    road_quality_label=quality_label,
                )
            )


        return routes

    def _count_junctions_along_line(
        self, G: nx.MultiDiGraph, line: LineString, signal_nodes: Set[Any]
    ) -> Tuple[int, int]:
        """Count graph intersections (degree > 2) and traffic signals near the LineString."""
        coords = list(line.coords)
        if len(coords) < 2:
            return 0, 0

        # Bounding box of line for fast candidate lookup
        min_x, min_y, max_x, max_y = line.bounds

        intersections = 0
        signals = 0

        # Sample nodes within envelope
        for node_id, data in G.nodes(data=True):
            nx_val = data.get("x")
            ny_val = data.get("y")
            if nx_val is None or ny_val is None:
                continue

            if min_x <= nx_val <= max_x and min_y <= ny_val <= max_y:
                # Check proximity to line (within ~15 meters)
                pt = Point(nx_val, ny_val)
                # Projected distance approximation in degrees (~0.00015 deg ~ 15m)
                if line.distance(pt) < 0.00015:
                    if G.degree(node_id) > 2:
                        intersections += 1
                    if node_id in signal_nodes:
                        signals += 1

        return intersections, signals
