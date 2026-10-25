"""Loop and Corridor Routing Module: Generates continuous closed driving loops and point-to-point corridors."""

import math
from typing import List, Dict, Any, Tuple, Optional
import networkx as nx
import osmnx as ox
from shapely.geometry import LineString, mapping, Point

from .config import DiscoveryConfig
from .geometry import calculate_sinuosity, calculate_angular_twistiness, calculate_line_length_km
from .scoring import score_route_metrics, calculate_cop_enforcement_risk
from .navigation import generate_google_maps_url, generate_street_view_url


def find_nearest_node(G: nx.Graph, lat: float, lon: float, candidate_nodes=None) -> Any:
    """Find the nearest graph node to (lat, lon) using pure Python spatial distance."""
    nodes = candidate_nodes if candidate_nodes is not None else G.nodes()
    best_node = None
    min_dist_sq = float("inf")
    for node in nodes:
        data = G.nodes[node]
        y = data.get("y")
        x = data.get("x")
        if y is not None and x is not None:
            dist_sq = (y - lat) ** 2 + (x - lon) ** 2
            if dist_sq < min_dist_sq:
                min_dist_sq = dist_sq
                best_node = node
    return best_node


class LoopRouteBuilder:
    """Generates continuous closed driving loop circuits and point-to-point corridors."""

    def __init__(self, config: Optional[DiscoveryConfig] = None):
        self.config = config or DiscoveryConfig()

    @staticmethod
    def _assign_backroad_weights(G_undir: nx.Graph) -> None:
        """Assign 'backroad_cost' to every edge to heavily penalize highways (e.g. Rt 17, I-87) and reward curvy backroads."""
        import re
        hw_ref_re = re.compile(
            r"\b(I-\d+|Interstate|NY\s*17\b|NJ\s*17\b|Rt\.?\s*17\b|Route\s*17\b|US\s*9W\b|Thruway|Turnpike|Parkway|Expressway)\b",
            re.IGNORECASE,
        )
        for u, v, k, data in G_undir.edges(keys=True, data=True):
            length = float(data.get("length", 100.0) or 100.0)
            hw = data.get("highway", "secondary")
            if isinstance(hw, (list, tuple)):
                hw = hw[0]
            hw_str = str(hw).lower()

            ref = data.get("canonical_ref") or data.get("ref") or ""
            name = data.get("canonical_name") or data.get("name") or ""
            if isinstance(ref, (list, tuple)):
                ref = " ".join(str(x) for x in ref)
            if isinstance(name, (list, tuple)):
                name = " ".join(str(x) for x in name)
            combined_label = f"{ref} {name}"

            multiplier = 1.0
            if hw_str in ("motorway", "motorway_link", "trunk", "trunk_link") or hw_ref_re.search(combined_label):
                # 12x penalty on major highways like Rt 17 / I-87 so they are avoided unless an unavoidable bridge/crossing
                multiplier = 12.0
            elif hw_str in ("primary", "primary_link"):
                multiplier = 3.2
            elif hw_str in ("secondary", "tertiary", "unclassified"):
                multiplier = 0.65

            geom = data.get("geometry")
            if geom is not None and length > 80.0:
                try:
                    sin = calculate_sinuosity(geom)
                    if sin >= 1.15 and multiplier <= 1.0:
                        multiplier *= 0.65
                except Exception:
                    pass

            data["backroad_cost"] = max(1.0, length * multiplier)

    def build_loop_circuit(
        self, G: nx.MultiDiGraph, start_lat: float, start_lon: float, target_miles: float = 25.0
    ) -> Dict[str, Any]:
        """Build a continuous closed-loop driving circuit starting and ending near (start_lat, start_lon)."""
        from .stitching import RouteStitcher

        # Convert graph to undirected for robust circuit pathfinding
        G_undir = ox.convert.to_undirected(G)

        # Get nodes in the largest connected component to ensure complete path connectivity
        components = list(nx.connected_components(G_undir))
        if not components:
            raise ValueError("No connected components found in road network graph.")
        main_component = max(components, key=len)

        # 1. Find start node in main component
        start_node = find_nearest_node(G_undir, start_lat, start_lon, candidate_nodes=main_component)

        # 2. Identify top enthusiast road corridors in graph to anchor the loop
        stitcher = RouteStitcher(self.config)
        stitched = stitcher.stitch_network(G)

        # Rank stitched routes by composite Fun Index score
        scored_stitched = []
        for r in stitched:
            sin = calculate_sinuosity(r.geometry)
            tw = calculate_angular_twistiness(
                r.geometry,
                step_meters=self.config.min_step_meters,
                curve_deflection_threshold_deg=self.config.curve_threshold_deg,
            )
            cop_risk = calculate_cop_enforcement_risk(
                highway_type=r.highway_type,
                speed_mph=r.avg_speed_limit,
                sinuosity=sin,
                intersection_density=r.intersection_density,
                business_density=r.business_density,
            )
            sc = score_route_metrics(
                sinuosity=sin,
                twistiness_deg_per_km=tw["twistiness_deg_per_km"],
                speed_mph=r.avg_speed_limit,
                length_km=r.length_km,
                intersection_density=r.intersection_density,
                signal_density=r.signal_density,
                business_density=r.business_density,
                driveway_density=r.driveway_density,
                cop_risk_pct=cop_risk["risk_pct"],
                smoothness_penalty=r.smoothness_penalty,
                config=self.config,
            )
            scored_stitched.append((sc["composite_driving_score"], r))

        scored_stitched.sort(key=lambda x: x[0], reverse=True)

        anchor_nodes = []

        # Target top enthusiast routes (e.g., NY 97, NY 211, US 129, etc.)
        for score, r in scored_stitched:
            if r.road_name and r.geometry:
                mid_pt = r.geometry.interpolate(0.5, normalized=True)
                node = find_nearest_node(G_undir, mid_pt.y, mid_pt.x, candidate_nodes=main_component)
                if node and node != start_node and node not in anchor_nodes:
                    anchor_nodes.append(node)
                    if len(anchor_nodes) >= 4:
                        break

        # Fallback to 8 directional rays if stitched routes yielded fewer than 2 anchors
        if len(anchor_nodes) < 2:
            target_km = target_miles * 1.60934
            radius_deg = max(0.04, (target_km / 4.0) / 111.0) # Approximate radius in degrees lat/lon
            angles = [i * (2 * math.pi / 8) for i in range(8)]
            for angle in angles:
                t_lat = start_lat + radius_deg * math.cos(angle)
                t_lon = start_lon + (radius_deg * math.sin(angle)) / max(0.2, math.cos(math.radians(start_lat)))
                nearest = find_nearest_node(G_undir, t_lat, t_lon, candidate_nodes=main_component)
                if nearest and nearest != start_node and nearest not in anchor_nodes:
                    anchor_nodes.append(nearest)

        if len(anchor_nodes) < 2:
            comp_nodes = list(main_component)
            step = max(1, len(comp_nodes) // 4)
            anchor_nodes = [comp_nodes[i] for i in range(0, len(comp_nodes), step)[:3] if comp_nodes[i] != start_node]

        # 3. Form closed circuit path: start -> wp1 -> wp2 -> wp3 -> wp4 -> start (using highway-avoiding backroad_cost)
        self._assign_backroad_weights(G_undir)
        circuit_nodes = [start_node] + anchor_nodes[:4] + [start_node]
        full_node_path = []

        for i in range(len(circuit_nodes) - 1):
            u_node = circuit_nodes[i]
            v_node = circuit_nodes[i + 1]
            try:
                sub_path = nx.shortest_path(G_undir, source=u_node, target=v_node, weight="backroad_cost")
                if full_node_path and sub_path:
                    full_node_path.extend(sub_path[1:])
                else:
                    full_node_path.extend(sub_path)
            except Exception:
                continue

        if len(full_node_path) < 2:
            raise ValueError("Could not compute continuous loop circuit across network graph.")

        # 4. Extract edge geometries along continuous node path and group into ordered legs
        return self._convert_node_path_to_geojson_loop(G_undir, full_node_path, start_lat, start_lon, target_miles)

    def _convert_node_path_to_geojson_loop(
        self, G_undir: nx.Graph, node_path: List[Any], start_lat: float, start_lon: float, target_miles: float
    ) -> Dict[str, Any]:
        edges_data = []

        for i in range(len(node_path) - 1):
            u = node_path[i]
            v = node_path[i + 1]

            edge_dict = G_undir.get_edge_data(u, v)
            if not edge_dict:
                continue

            # Pick primary edge key (usually 0)
            data = next(iter(edge_dict.values()))

            ref = data.get("canonical_ref") or data.get("ref")
            name = data.get("canonical_name") or data.get("name", "Un-named Road")

            if isinstance(ref, (list, tuple)):
                ref = ref[0]
            if isinstance(name, (list, tuple)):
                name = name[0]

            ref_str = str(ref).strip() if ref and str(ref) != "None" else ""
            name_str = str(name).strip() if name and str(name) != "None" else ""

            if ref_str and name_str and ref_str not in name_str:
                road_label = f"{ref_str} ({name_str})"
            elif ref_str:
                road_label = ref_str
            else:
                road_label = name_str or "Un-named Road"

            geom = data.get("geometry")
            if not geom:
                u_x, u_y = G_undir.nodes[u]["x"], G_undir.nodes[u]["y"]
                v_x, v_y = G_undir.nodes[v]["x"], G_undir.nodes[v]["y"]
                geom = LineString([(u_x, u_y), (v_x, v_y)])

            speed = data.get("maxspeed", 45)
            if isinstance(speed, list):
                speed = speed[0]
            try:
                speed_mph = float(str(speed).replace("mph", "").strip())
            except Exception:
                speed_mph = 45.0

            hw = data.get("highway", "secondary")
            if isinstance(hw, list):
                hw = hw[0]

            edges_data.append({
                "name": road_label,
                "geom": geom,
                "speed": speed_mph,
                "highway": str(hw)
            })

        # Group consecutive edges with same road name into continuous legs
        grouped_legs = []
        if edges_data:
            current_leg = {
                "name": edges_data[0]["name"],
                "coords": list(edges_data[0]["geom"].coords),
                "speed": edges_data[0]["speed"],
                "highway": edges_data[0]["highway"]
            }

            for item in edges_data[1:]:
                if item["name"] == current_leg["name"]:
                    # Extend coordinates
                    item_coords = list(item["geom"].coords)
                    if current_leg["coords"][-1] == item_coords[0]:
                        current_leg["coords"].extend(item_coords[1:])
                    elif current_leg["coords"][-1] == item_coords[-1]:
                        current_leg["coords"].extend(reversed(item_coords[:-1]))
                    else:
                        current_leg["coords"].extend(item_coords)
                else:
                    grouped_legs.append(current_leg)
                    current_leg = {
                        "name": item["name"],
                        "coords": list(item["geom"].coords),
                        "speed": item["speed"],
                        "highway": item["highway"]
                    }
            grouped_legs.append(current_leg)

        # Build GeoJSON Features
        features = []
        waypoint_coords = [[start_lat, start_lon]]
        total_loop_km = 0.0

        for idx, leg in enumerate(grouped_legs, start=1):
            coords = leg["coords"]
            if len(coords) < 2:
                continue

            ls = LineString(coords)
            length_km = calculate_line_length_km(ls)
            total_loop_km += length_km

            sinuosity = calculate_sinuosity(ls)
            twistiness = calculate_angular_twistiness(ls)

            cop_risk = calculate_cop_enforcement_risk(
                highway_type=leg["highway"],
                speed_mph=leg["speed"],
                sinuosity=sinuosity
            )

            scores = score_route_metrics(
                sinuosity=sinuosity,
                twistiness_deg_per_km=twistiness["twistiness_deg_per_km"],
                speed_mph=leg["speed"],
                length_km=length_km,
                cop_risk_pct=cop_risk["risk_pct"],
                config=self.config
            )

            # Store waypoint for Google Maps Tour
            mid_pt = ls.interpolate(0.5, normalized=True)
            waypoint_coords.append([mid_pt.y, mid_pt.x])

            feat = {
                "type": "Feature",
                "geometry": mapping(ls),
                "properties": {
                    "rank": idx,
                    "seq": idx,
                    "road_name": f"Leg {idx}: {leg['name']}",
                    "name": f"Leg {idx}: {leg['name']}",
                    "length_km": float(length_km),
                    "avg_speed_limit": float(leg["speed"]),
                    "composite_driving_score": float(scores["composite_driving_score"]),
                    "score": float(scores["composite_driving_score"]),
                    "cop_risk_pct": float(cop_risk["risk_pct"]),
                    "cop_risk_level": str(cop_risk["level"]),
                    "cop_risk_color": str(cop_risk["badge_color"]),
                    "cop_risk_desc": str(cop_risk["description"]),
                    "highway_type": str(leg["highway"]),
                    "google_maps_url": generate_google_maps_url(ls),
                    "street_view_url": generate_street_view_url(ls)
                }
            }
            features.append(feat)

        # Construct 1-click Google Maps driving loop tour URL
        orig_str = f"{start_lat:.5f},{start_lon:.5f}"
        dest_str = f"{start_lat + 0.0002:.5f},{start_lon + 0.0002:.5f}"
        wp_str_list = [f"{pt[0]:.5f},{pt[1]:.5f}" for pt in waypoint_coords[1:] if f"{pt[0]:.5f},{pt[1]:.5f}" != orig_str]
        wp_param = "%7C".join(wp_str_list[:8]) # Max 8 waypoints for Google Maps
        gmaps_loop_tour = f"https://www.google.com/maps/dir/?api=1&origin={orig_str}&destination={dest_str}&travelmode=driving&waypoints={wp_param}"

        return {
            "type": "FeatureCollection",
            "metadata": {
                "generator": "RoadDiscoveryEngineLoopBuilder",
                "route_mode": "loop",
                "loop_miles": target_miles,
                "total_loop_miles": float(total_loop_km * 0.621371),
                "total_routes_found": len(features),
                "origin_coords": [start_lat, start_lon],
                "google_maps_tour_url": gmaps_loop_tour
            },
            "features": features
        }

    def build_p2p_corridor(
        self, G: nx.MultiDiGraph, orig_lat: float, orig_lon: float, dest_lat: float, dest_lon: float
    ) -> Dict[str, Any]:
        """Build a continuous driving corridor from (orig_lat, orig_lon) to (dest_lat, dest_lon)."""
        G_undir = ox.convert.to_undirected(G)
        components = list(nx.connected_components(G_undir))
        if not components:
            raise ValueError("No connected components found in road network graph.")
        main_component = max(components, key=len)

        start_node = find_nearest_node(G_undir, orig_lat, orig_lon, candidate_nodes=main_component)
        end_node = find_nearest_node(G_undir, dest_lat, dest_lon, candidate_nodes=main_component)

        if not start_node or not end_node:
            raise ValueError("Could not resolve origin/destination nodes on network graph.")

        self._assign_backroad_weights(G_undir)
        try:
            node_path = nx.shortest_path(G_undir, source=start_node, target=end_node, weight="backroad_cost")
        except Exception as e:
            raise ValueError(f"No connected path found between origin and destination: {e}")

        return self._convert_node_path_to_geojson_p2p(G_undir, node_path, orig_lat, orig_lon, dest_lat, dest_lon)

    def _convert_node_path_to_geojson_p2p(
        self, G_undir: nx.Graph, node_path: List[Any], orig_lat: float, orig_lon: float, dest_lat: float, dest_lon: float
    ) -> Dict[str, Any]:
        edges_data = []

        for i in range(len(node_path) - 1):
            u = node_path[i]
            v = node_path[i + 1]

            edge_dict = G_undir.get_edge_data(u, v)
            if not edge_dict:
                continue

            data = next(iter(edge_dict.values()))

            ref = data.get("canonical_ref") or data.get("ref")
            name = data.get("canonical_name") or data.get("name", "Un-named Road")

            if isinstance(ref, (list, tuple)):
                ref = ref[0]
            if isinstance(name, (list, tuple)):
                name = name[0]

            ref_str = str(ref).strip() if ref and str(ref) != "None" else ""
            name_str = str(name).strip() if name and str(name) != "None" else ""

            if ref_str and name_str and ref_str not in name_str:
                road_label = f"{ref_str} ({name_str})"
            elif ref_str:
                road_label = ref_str
            else:
                road_label = name_str or "Un-named Road"

            geom = data.get("geometry")
            if not geom:
                u_x, u_y = G_undir.nodes[u]["x"], G_undir.nodes[u]["y"]
                v_x, v_y = G_undir.nodes[v]["x"], G_undir.nodes[v]["y"]
                geom = LineString([(u_x, u_y), (v_x, v_y)])

            speed = data.get("maxspeed", 45)
            if isinstance(speed, list):
                speed = speed[0]
            try:
                speed_mph = float(str(speed).replace("mph", "").strip())
            except Exception:
                speed_mph = 45.0

            hw = data.get("highway", "secondary")
            if isinstance(hw, list):
                hw = hw[0]

            edges_data.append({
                "name": road_label,
                "geom": geom,
                "speed": speed_mph,
                "highway": str(hw)
            })

        grouped_legs = []
        if edges_data:
            current_leg = {
                "name": edges_data[0]["name"],
                "coords": list(edges_data[0]["geom"].coords),
                "speed": edges_data[0]["speed"],
                "highway": edges_data[0]["highway"]
            }

            for item in edges_data[1:]:
                if item["name"] == current_leg["name"]:
                    item_coords = list(item["geom"].coords)
                    if current_leg["coords"][-1] == item_coords[0]:
                        current_leg["coords"].extend(item_coords[1:])
                    elif current_leg["coords"][-1] == item_coords[-1]:
                        current_leg["coords"].extend(reversed(item_coords[:-1]))
                    else:
                        current_leg["coords"].extend(item_coords)
                else:
                    grouped_legs.append(current_leg)
                    current_leg = {
                        "name": item["name"],
                        "coords": list(item["geom"].coords),
                        "speed": item["speed"],
                        "highway": item["highway"]
                    }
            grouped_legs.append(current_leg)

        features = []
        total_p2p_km = 0.0

        for idx, leg in enumerate(grouped_legs, start=1):
            coords = leg["coords"]
            if len(coords) < 2:
                continue

            ls = LineString(coords)
            length_km = calculate_line_length_km(ls)
            total_p2p_km += length_km

            sinuosity = calculate_sinuosity(ls)
            twistiness = calculate_angular_twistiness(ls)

            cop_risk = calculate_cop_enforcement_risk(
                highway_type=leg["highway"],
                speed_mph=leg["speed"],
                sinuosity=sinuosity
            )

            scores = score_route_metrics(
                sinuosity=sinuosity,
                twistiness_deg_per_km=twistiness["twistiness_deg_per_km"],
                speed_mph=leg["speed"],
                length_km=length_km,
                cop_risk_pct=cop_risk["risk_pct"],
                config=self.config
            )

            feat = {
                "type": "Feature",
                "geometry": mapping(ls),
                "properties": {
                    "rank": idx,
                    "seq": idx,
                    "road_name": f"Leg {idx}: {leg['name']}",
                    "name": f"Leg {idx}: {leg['name']}",
                    "length_km": float(length_km),
                    "avg_speed_limit": float(leg["speed"]),
                    "composite_driving_score": float(scores["composite_driving_score"]),
                    "score": float(scores["composite_driving_score"]),
                    "cop_risk_pct": float(cop_risk["risk_pct"]),
                    "cop_risk_level": str(cop_risk["level"]),
                    "cop_risk_color": str(cop_risk["badge_color"]),
                    "cop_risk_desc": str(cop_risk["description"]),
                    "highway_type": str(leg["highway"]),
                    "google_maps_url": generate_google_maps_url(ls),
                    "street_view_url": generate_street_view_url(ls)
                }
            }
            features.append(feat)

        p2p_waypoints = []
        for feat in features:
            coords = feat.get("geometry", {}).get("coordinates", [])
            if len(coords) >= 2:
                mid_idx = len(coords) // 2
                p2p_waypoints.append(f"{coords[mid_idx][1]:.5f},{coords[mid_idx][0]:.5f}")

        if len(p2p_waypoints) > 8:
            step = (len(p2p_waypoints) - 1) / 7.0
            p2p_waypoints = [p2p_waypoints[int(round(i * step))] for i in range(8)]

        wp_param = "%7C".join(p2p_waypoints)
        if wp_param:
            gmaps_p2p = f"https://www.google.com/maps/dir/?api=1&origin={orig_lat:.5f},{orig_lon:.5f}&destination={dest_lat:.5f},{dest_lon:.5f}&travelmode=driving&waypoints={wp_param}"
        else:
            gmaps_p2p = f"https://www.google.com/maps/dir/?api=1&origin={orig_lat:.5f},{orig_lon:.5f}&destination={dest_lat:.5f},{dest_lon:.5f}&travelmode=driving"

        return {
            "type": "FeatureCollection",
            "metadata": {
                "generator": "RoadDiscoveryEngineCorridorBuilder",
                "route_mode": "point_to_point",
                "total_corridor_miles": float(total_p2p_km * 0.621371),
                "total_routes_found": len(features),
                "origin_coords": [orig_lat, orig_lon],
                "destination_coords": [dest_lat, dest_lon],
                "google_maps_tour_url": gmaps_p2p
            },
            "features": features
        }

    def build_valhalla_backroad_p2p(
        self, orig_lat: float, orig_lon: float, dest_lat: float, dest_lon: float
    ) -> Dict[str, Any]:
        """Build a highway-avoiding backroad corridor via Valhalla (use_highways=0.0, top_speed=48) + lateral ridge offset."""
        import json
        import re
        import urllib.request

        def decode_poly6(encoded: str) -> List[Tuple[float, float]]:
            inv = 1.0 / 1e6
            decoded = []
            prev = [0, 0]
            i = 0
            while i < len(encoded):
                ll = [0, 0]
                for j in (0, 1):
                    shift = 0
                    byte = 0x20
                    while byte >= 0x20:
                        byte = ord(encoded[i]) - 63
                        i += 1
                        ll[j] |= (byte & 0x1F) << shift
                        shift += 5
                    ll[j] = prev[j] + (~(ll[j] >> 1) if (ll[j] & 1) else (ll[j] >> 1))
                    prev[j] = ll[j]
                decoded.append((round(ll[1] * inv, 6), round(ll[0] * inv, 6)))  # (lon, lat)
            return decoded

        dlat = dest_lat - orig_lat
        dlon = dest_lon - orig_lon
        mid_lat = (orig_lat + dest_lat) * 0.5
        mid_lon = (orig_lon + dest_lon) * 0.5
        chord_deg = math.hypot(dlat, dlon)

        candidates = []
        if chord_deg > 0.09:
            candidates.append([
                {"lat": orig_lat, "lon": orig_lon, "type": "break"},
                {"lat": orig_lat + dlat * 0.28 - dlon * 0.15, "lon": orig_lon + dlon * 0.28 - abs(dlat) * 0.15, "type": "through", "radius": 3500},
                {"lat": orig_lat + dlat * 0.68 - dlon * 0.12, "lon": orig_lon + dlon * 0.68 - abs(dlat) * 0.12, "type": "through", "radius": 3500},
                {"lat": dest_lat, "lon": dest_lon, "type": "break"},
            ])
            candidates.append([
                {"lat": orig_lat, "lon": orig_lon, "type": "break"},
                {"lat": orig_lat + dlat * 0.28 + dlon * 0.15, "lon": orig_lon + dlon * 0.28 + abs(dlat) * 0.15, "type": "through", "radius": 3500},
                {"lat": orig_lat + dlat * 0.68 + dlon * 0.12, "lon": orig_lon + dlon * 0.68 + abs(dlat) * 0.12, "type": "through", "radius": 3500},
                {"lat": dest_lat, "lon": dest_lon, "type": "break"},
            ])
        candidates.append([
            {"lat": orig_lat, "lon": orig_lon, "type": "break"},
            {"lat": dest_lat, "lon": dest_lon, "type": "break"},
        ])

        hw_re = re.compile(
            r"\b(I-\d+|Interstate|NY\s*17\b|NJ\s*17\b|Rt\.?\s*17\b|Route\s*17\b|State\s*Highway\s*17\b|US\s*9W\b|Thruway|Turnpike\s*Mainline|Parkway|Expressway)\b",
            re.IGNORECASE,
        )

        best_leg = None
        best_hw_mi = float("inf")

        for locs in candidates:
            payload = {
                "locations": locs,
                "costing": "auto",
                "costing_options": {
                    "auto": {
                        "use_highways": 0.0,
                        "use_tolls": 0.0,
                        "top_speed": 48,
                        "shortest": False,
                    }
                },
                "directions_options": {"units": "miles", "language": "en-US"},
            }
            req = urllib.request.Request(
                "https://valhalla1.openstreetmap.de/route",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "FindRoadsNav/1.0"},
            )
            try:
                with urllib.request.urlopen(req, timeout=14) as resp:
                    vdata = json.loads(resp.read().decode("utf-8"))
                    leg = (vdata.get("trip") or {}).get("legs", [None])[0]
                    if not leg or not leg.get("shape"):
                        continue
                    max_hw = 0.0
                    for m in leg.get("maneuvers", []):
                        names = " ".join((m.get("street_names") or []) + (m.get("begin_street_names") or []))
                        m_len = float(m.get("length", 0.0) or 0.0)
                        if hw_re.search(names) and m_len > max_hw:
                            max_hw = m_len
                    if max_hw < best_hw_mi:
                        best_hw_mi = max_hw
                        best_leg = leg
                    if max_hw <= 0.5:
                        break
            except Exception:
                continue

        if not best_leg:
            raise ValueError("Valhalla backroad routing unavailable")

        pts = decode_poly6(best_leg["shape"])
        grouped_legs = []
        cur_name = ""
        cur_coords: List[Tuple[float, float]] = []
        cur_speed = 45.0

        for m in best_leg.get("maneuvers", []):
            b_idx = int(m.get("begin_shape_index", 0))
            e_idx = int(m.get("end_shape_index", 0))
            if e_idx <= b_idx:
                continue
            streets = m.get("street_names") or []
            beg_streets = m.get("begin_street_names") or []
            raw_name = streets[0] if streets else (beg_streets[0] if beg_streets else "")
            alt_ref = streets[1] if len(streets) > 1 else ""
            label = raw_name or "Scenic Backroad"
            if alt_ref and raw_name and alt_ref not in raw_name:
                label = f"{alt_ref} ({raw_name})"

            slice_pts = pts[b_idx : e_idx + 1]
            if len(slice_pts) < 2:
                continue

            if cur_name and label != cur_name and len(cur_coords) >= 2:
                grouped_legs.append({"name": cur_name, "coords": cur_coords, "speed": cur_speed, "highway": "secondary"})
                cur_coords = [cur_coords[-1]]
            cur_name = label
            for pt in slice_pts:
                if not cur_coords or cur_coords[-1] != pt:
                    cur_coords.append(pt)

        if len(cur_coords) >= 2:
            grouped_legs.append({"name": cur_name or "Scenic Backroad", "coords": cur_coords, "speed": cur_speed, "highway": "secondary"})

        features = []
        total_p2p_km = 0.0
        for idx, leg in enumerate(grouped_legs, start=1):
            ls = LineString(leg["coords"])
            length_km = calculate_line_length_km(ls)
            if length_km < 0.05:
                continue
            total_p2p_km += length_km
            sinuosity = calculate_sinuosity(ls)
            twistiness = calculate_angular_twistiness(ls)
            cop_risk = calculate_cop_enforcement_risk(
                highway_type=leg["highway"],
                speed_mph=leg["speed"],
                sinuosity=sinuosity,
            )
            scores = score_route_metrics(
                sinuosity=sinuosity,
                twistiness_deg_per_km=twistiness["twistiness_deg_per_km"],
                speed_mph=leg["speed"],
                length_km=length_km,
                cop_risk_pct=cop_risk["risk_pct"],
                config=self.config,
            )
            features.append({
                "type": "Feature",
                "geometry": mapping(ls),
                "properties": {
                    "rank": len(features) + 1,
                    "seq": len(features) + 1,
                    "road_name": leg["name"],
                    "name": leg["name"],
                    "length_km": float(length_km),
                    "avg_speed_limit": float(leg["speed"]),
                    "composite_driving_score": float(scores["composite_driving_score"]),
                    "score": float(scores["composite_driving_score"]),
                    "cop_risk_pct": float(cop_risk["risk_pct"]),
                    "cop_risk_level": str(cop_risk["level"]),
                    "cop_risk_color": str(cop_risk["badge_color"]),
                    "cop_risk_desc": str(cop_risk["description"]),
                    "highway_type": str(leg["highway"]),
                    "google_maps_url": generate_google_maps_url(ls),
                    "street_view_url": generate_street_view_url(ls),
                },
            })

        return {
            "type": "FeatureCollection",
            "metadata": {
                "generator": "RoadDiscoveryEngineCorridorBuilder",
                "route_mode": "point_to_point",
                "total_corridor_miles": float(total_p2p_km * 0.621371),
                "total_routes_found": len(features),
                "origin_coords": [orig_lat, orig_lon],
                "destination_coords": [dest_lat, dest_lon],
            },
            "features": features,
        }
