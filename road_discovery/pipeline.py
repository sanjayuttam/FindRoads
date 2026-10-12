"""Pipeline module: Orchestrates ingestion, stitching, scoring, and GeoJSON export."""

import json
from typing import Tuple, Dict, Any, List, Optional
from shapely.geometry import mapping

from .config import DiscoveryConfig
from .ingestion import RoadNetworkIngester
from .stitching import RouteStitcher, StitchedRoute
from .loop_builder import LoopRouteBuilder
from .geometry import calculate_sinuosity, calculate_angular_twistiness

from .scoring import score_route_metrics, calculate_cop_enforcement_risk
from .navigation import (
    generate_google_maps_url,
    generate_street_view_url,
    generate_multi_road_tour_url,
    export_routes_to_gpx,
    export_routes_to_kml,
)



class RoadDiscoveryEngine:
    """End-to-end engine for discovering, ranking, and exporting enthusiast driving routes."""

    def __init__(self, config: Optional[DiscoveryConfig] = None):
        self.config = config or DiscoveryConfig()
        self.ingester = RoadNetworkIngester(self.config)
        self.stitcher = RouteStitcher(self.config)

    def discover_from_bbox(
        self, bbox: Tuple[float, float, float, float]
    ) -> Dict[str, Any]:
        """Ingest, stitch, rank, and format driving routes within a bounding box.

        Args:
            bbox: (left/west, bottom/south, right/east, top/north)

        Returns:
            GeoJSON FeatureCollection dict
        """
        G = self.ingester.fetch_from_bbox(bbox)
        return self._process_network_to_geojson(G)

    def discover_from_place(self, place_query: str) -> Dict[str, Any]:
        """Ingest, stitch, rank, and format driving routes within a geocodable place name.

        Args:
            place_query: E.g., 'Graham County, North Carolina'

        Returns:
            GeoJSON FeatureCollection dict
        """
        G = self.ingester.fetch_from_place(place_query)
        result = self._process_network_to_geojson(G)

        # Smart fallback: if 0 routes found in a tiny village boundary, check if a broader 'Town of' exists
        if len(result.get("features", [])) == 0 and not place_query.lower().startswith("town of "):
            parts = [p.strip() for p in place_query.split(",")]
            if len(parts) >= 2:
                town_query = f"Town of {parts[0]}, " + ", ".join(parts[1:])
                try:
                    G_town = self.ingester.fetch_from_place(town_query)
                    result_town = self._process_network_to_geojson(G_town)
                    if len(result_town.get("features", [])) > 0:
                        print(f"[Smart Expansion] '{place_query}' matched a small municipal village with 0 routes. Automatically expanded to '{town_query}' ({len(result_town['features'])} routes found).")
                        return result_town
                except Exception:
                    pass

        return result

    def discover_from_radius(
        self, center_query: str, radius_miles: float = 10.0
    ) -> Dict[str, Any]:
        """Ingest, stitch, rank, and format driving routes within a radius around a location.

        Args:
            center_query: Town, landmark, or coordinates (e.g. 'Warwick, NY')
            radius_miles: Radius in miles (default: 10.0 miles)

        Returns:
            GeoJSON FeatureCollection dict
        """
        G = self.ingester.fetch_from_radius(center_query, radius_miles=radius_miles)
        return self._process_network_to_geojson(G)

    def build_loop_route(self, place_or_address: str, loop_miles: float = 25.0) -> Dict[str, Any]:
        """Generate a continuous closed-loop driving circuit starting and ending at place_or_address."""
        import osmnx as ox
        lat, lon = ox.geocode(place_or_address)
        radius_miles = max(10.0, loop_miles * 0.65)
        G = self.ingester.fetch_from_radius(place_or_address, radius_miles=radius_miles)
        builder = LoopRouteBuilder(self.config)
        return builder.build_loop_circuit(G, start_lat=lat, start_lon=lon, target_miles=loop_miles)

    def build_p2p_route(self, origin: str, destination: str) -> Dict[str, Any]:
        """Generate a continuous point-to-point driving corridor between origin and destination."""
        import osmnx as ox
        lat1, lon1 = ox.geocode(origin)
        lat2, lon2 = ox.geocode(destination)

        west = min(lon1, lon2) - 0.05
        east = max(lon1, lon2) + 0.05
        south = min(lat1, lat2) - 0.05
        north = max(lat1, lat2) + 0.05
        bbox = (west, south, east, north)

        G = self.ingester.fetch_from_bbox(bbox)
        builder = LoopRouteBuilder(self.config)
        return builder.build_p2p_corridor(G, orig_lat=lat1, orig_lon=lon1, dest_lat=lat2, dest_lon=lon2)

    def _process_network_to_geojson(self, G) -> Dict[str, Any]:
        """Process the network graph through stitching, scoring, and GeoJSON conversion."""
        stitched_routes: List[StitchedRoute] = self.stitcher.stitch_network(G)

        features: List[Dict[str, Any]] = []

        for route in stitched_routes:
            # 1. Geometric measurements
            sinuosity = calculate_sinuosity(route.geometry)
            twist_metrics = calculate_angular_twistiness(
                route.geometry,
                step_meters=self.config.min_step_meters,
                curve_deflection_threshold_deg=self.config.curve_threshold_deg,
            )

            # 1. Police Patrol & Radar Enforcement Risk Model
            cop_risk = calculate_cop_enforcement_risk(
                highway_type=route.highway_type,
                speed_mph=route.avg_speed_limit,
                sinuosity=sinuosity,
                intersection_density=route.intersection_density,
                business_density=route.business_density,
            )

            # 2. Driving scores
            scores = score_route_metrics(
                sinuosity=sinuosity,
                twistiness_deg_per_km=twist_metrics["twistiness_deg_per_km"],
                speed_mph=route.avg_speed_limit,
                length_km=route.length_km,
                intersection_density=route.intersection_density,
                signal_density=route.signal_density,
                business_density=route.business_density,
                driveway_density=route.driveway_density,
                cop_risk_pct=cop_risk["risk_pct"],
                smoothness_penalty=route.smoothness_penalty,
                config=self.config,
            )

            # 3. Construct GeoJSON Feature conforming strictly to requirements
            feature = {
                "type": "Feature",
                "geometry": mapping(route.geometry),
                "properties": {
                    # Core required fields
                    "road_name": route.road_name,
                    "name": route.road_name,
                    "length_km": float(route.length_km),
                    "avg_speed_limit": float(route.avg_speed_limit),
                    "sinuosity_score": float(scores["sinuosity_score"]),
                    "curvature_score": float(scores["curvature_score"]),
                    "traffic_penalty": float(scores["traffic_penalty"]),
                    "smoothness_penalty": float(route.smoothness_penalty),
                    "road_quality_label": str(route.road_quality_label),
                    "composite_driving_score": float(scores["composite_driving_score"]),
                    "composite_score": float(scores["composite_driving_score"]),
                    # Patrol & Radar Risk Model
                    "cop_risk_pct": float(cop_risk["risk_pct"]),
                    "cop_risk_level": str(cop_risk["level"]),
                    "cop_risk_desc": str(cop_risk["description"]),
                    "cop_risk_color": str(cop_risk["badge_color"]),
                    # Additional rich enthusiast diagnostics
                    "speed_score": float(scores["speed_score"]),
                    "sinuosity_raw": float(round(sinuosity, 2)),
                    "twistiness_deg_per_km": float(twist_metrics["twistiness_deg_per_km"]),
                    "total_turn_degrees": float(twist_metrics["total_deflection_deg"]),
                    "curve_count": int(twist_metrics["curve_count"]),
                    "curves_per_km": float(twist_metrics["curves_per_km"]),
                    "highway_type": str(route.highway_type),
                    "intersections_count": int(route.intersection_count),
                    "traffic_signals_count": int(route.signal_count),
                    "business_count": int(route.business_count),
                    "driveway_count": int(route.driveway_count),
                    # 1-click turn-by-turn navigation & Street View URLs
                    "google_maps_url": generate_google_maps_url(route.geometry),
                    "street_view_url": generate_street_view_url(route.geometry),
                },
            }
            features.append(feature)

        # Rank routes by composite_driving_score descending
        features.sort(
            key=lambda f: f["properties"]["composite_driving_score"], reverse=True
        )

        for idx, feat in enumerate(features, start=1):
            feat["properties"]["rank"] = idx

        geojson = {
            "type": "FeatureCollection",
            "metadata": {
                "generator": "RoadDiscoveryEngine",
                "total_routes_found": len(features),
                "target_speed_mph": self.config.target_speed_mph,
                "weights": {
                    "curvature": self.config.weight_curvature,
                    "sinuosity": self.config.weight_sinuosity,
                    "speed": self.config.weight_speed,
                },
                "google_maps_tour_url": generate_multi_road_tour_url(features),
            },
            "features": features,
        }

        return geojson

    @staticmethod
    def save_geojson(geojson_data: Dict[str, Any], output_path: str) -> None:
        """Save GeoJSON dictionary to a formatted file."""
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(geojson_data, f, indent=2)

    @staticmethod
    def save_gpx(geojson_data: Dict[str, Any], output_path: str) -> None:
        """Export routes to standard GPX 1.1 format for Garmin, InRoute, Scenic, Apple Maps."""
        export_routes_to_gpx(geojson_data, output_path)

    @staticmethod
    def save_kml(geojson_data: Dict[str, Any], output_path: str) -> None:
        """Export routes to KML format for 1-click import into Google My Maps."""
        export_routes_to_kml(geojson_data, output_path)
