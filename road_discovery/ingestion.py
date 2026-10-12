"""Data ingestion module for OpenStreetMap road networks via OSMnx and Overpass API."""

import re
import socket
from typing import Tuple, Optional, List, Dict, Any, Union
from collections import OrderedDict
import requests
import networkx as nx
import osmnx as ox
from osmnx import _http, _overpass
from shapely.geometry import LineString, Point

from .config import DiscoveryConfig

# Official high-capacity Overpass API mirrors
OVERPASS_MIRRORS = [
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

_overpass_configured = False


def setup_robust_overpass() -> None:
    """Configure OSMnx to use resilient Overpass connection with multi-mirror POST failover and caching."""
    global _overpass_configured
    if _overpass_configured:
        return

    # Standard OSMnx settings
    ox.settings.overpass_url = "https://lz4.overpass-api.de/api"
    ox.settings.use_cache = True
    ox.settings.cache_folder = "./cache"
    ox.settings.overpass_rate_limit = False
    ox.settings.requests_timeout = 30
    ox.settings.http_user_agent = "RoadDiscovery/1.0 (contact@findroads.dev)"

    # Prevent socket DNS monkeypatch from breaking connection pooling or failover
    _http._config_dns = lambda url: None
    if hasattr(_http, "_original_getaddrinfo"):
        socket.getaddrinfo = _http._original_getaddrinfo

    def robust_overpass_request(data: OrderedDict) -> dict:
        # Generate canonical GET-style URL solely for cache lookup and storage
        base_url = OVERPASS_MIRRORS[0]
        prepared_url = str(requests.Request("GET", base_url, params=data).prepare().url)
        cached = _http._retrieve_from_cache(prepared_url)
        if isinstance(cached, dict):
            return cached

        headers = {
            "User-Agent": "RoadDiscovery/1.0 (contact@findroads.dev)",
            "Accept": "application/json",
        }

        last_error = None
        for mirror in OVERPASS_MIRRORS:
            try:
                # Transmit HTTP POST with full query payload in body (prevents HTTP 414 Request-URI Too Long)
                r = requests.post(
                    mirror,
                    data=data,
                    headers=headers,
                    timeout=30,
                )
                if r.status_code == 200:
                    parsed = r.json()
                    if isinstance(parsed, dict):
                        _http._save_to_cache(prepared_url, parsed, True)
                        return parsed
                last_error = f"{mirror} returned HTTP {r.status_code}"
            except Exception as e:
                last_error = f"{mirror} error: {e}"
                continue

        raise RuntimeError(f"Could not connect to Overpass API directly or via fallback mirrors: {last_error}")

    _overpass._overpass_request = robust_overpass_request
    _overpass_configured = True


def parse_osm_speed(
    speed_val: Any, default_speed_mph: float = 50.0
) -> float:
    """Parse various OSM maxspeed representations into numeric speed in mph."""
    if speed_val is None:
        return default_speed_mph

    # If OSMnx grouped multiple values into a list or tuple
    if isinstance(speed_val, (list, tuple)):
        # Calculate the average of parsed values or take the first valid
        parsed_speeds = [parse_osm_speed(v, default_speed_mph) for v in speed_val if v is not None]
        return float(sum(parsed_speeds) / len(parsed_speeds)) if parsed_speeds else default_speed_mph

    s = str(speed_val).strip().lower()

    # Check for km/h or kph
    if "km/h" in s or "kph" in s or "kmh" in s:
        match = re.search(r"(\d+(?:\.\d+)?)", s)
        if match:
            return round(float(match.group(1)) * 0.621371, 1)

    # Check for mph or plain numbers
    match = re.search(r"(\d+(?:\.\d+)?)", s)
    if match:
        return round(float(match.group(1)), 1)

    return default_speed_mph


def extract_primary_string(val: Any) -> Optional[str]:
    """Helper to extract a clean string from an OSM tag that might be a list or None."""
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        return str(val[0])
    return str(val)


class RoadNetworkIngester:
    """Fetches, cleans, and filters road networks for driving enthusiast analysis."""

    def __init__(self, config: Optional[DiscoveryConfig] = None):
        self.config = config or DiscoveryConfig()
        setup_robust_overpass()

    def fetch_from_bbox(
        self,
        bbox: Tuple[float, float, float, float],
        simplify: bool = True,
        retain_all: bool = True,
    ) -> nx.MultiDiGraph:
        """Download road network within a bounding box (west, south, east, north).

        Args:
            bbox: (left/west, bottom/south, right/east, top/north) in EPSG:4326
            simplify: Whether to collapse non-intersection degree-2 nodes
            retain_all: Whether to retain disconnected components

        Returns:
            NetworkX MultiDiGraph of driving roads
        """
    def _configure_useful_tags(self) -> None:
        """Configure OSMnx to retain essential highway, surface, speed, commercial, and driveway tags."""
        ox.settings.useful_tags_way = list(
            set(
                ox.settings.useful_tags_way
                + [
                    "surface",
                    "smoothness",
                    "tracktype",
                    "lanes",
                    "maxspeed",
                    "ref",
                    "name",
                    "access",
                    "motor_vehicle",
                    "landuse",
                    "building",
                    "shop",
                    "amenity",
                    "service",
                ]
            )
        )
        ox.settings.useful_tags_node = list(
            set(ox.settings.useful_tags_node + ["highway", "traffic_signals", "amenity", "shop"])
        )

    def fetch_from_bbox(
        self,
        bbox: Tuple[float, float, float, float],
        simplify: bool = True,
        retain_all: bool = True,
    ) -> nx.MultiDiGraph:
        """Download road network within a bounding box (west, south, east, north).

        Args:
            bbox: (left/west, bottom/south, right/east, top/north) in EPSG:4326
            simplify: Whether to collapse non-intersection degree-2 nodes
            retain_all: Whether to retain disconnected components

        Returns:
            NetworkX MultiDiGraph of driving roads
        """
        self._configure_useful_tags()

        G = ox.graph_from_bbox(
            bbox=bbox,
            network_type="drive",
            simplify=simplify,
            retain_all=retain_all,
            truncate_by_edge=True,
        )
        return self.filter_graph(G)

    def fetch_from_place(
        self,
        place_query: str,
        simplify: bool = True,
        retain_all: bool = True,
    ) -> nx.MultiDiGraph:
        """Download road network for a named geographic area (e.g. county or state).

        Args:
            place_query: Geocodable place name (e.g., 'Graham County, North Carolina')
            simplify: Whether to collapse degree-2 nodes
            retain_all: Retain all road components

        Returns:
            NetworkX MultiDiGraph of driving roads
        """
        self._configure_useful_tags()

        try:
            try:
                G = ox.graph_from_place(
                    place_query,
                    network_type="drive",
                    custom_filter='["highway"~"primary|secondary|tertiary|unclassified"]',
                    simplify=simplify,
                    retain_all=retain_all,
                    truncate_by_edge=True,
                )
            except Exception:
                G = ox.graph_from_place(
                    place_query,
                    network_type="drive",
                    simplify=simplify,
                    retain_all=retain_all,
                    truncate_by_edge=True,
                )
        except ValueError as e:
            # Check what Nominatim geocoded the query to, to give a friendly error
            try:
                gdf = ox.geocode_to_gdf(place_query)
                display_name = gdf["display_name"].iloc[0] if not gdf.empty else "Unknown"
            except Exception:
                display_name = "None"

            raise ValueError(
                f"No road network found within '{place_query}'. "
                f"OpenStreetMap matched this query to: '{display_name}'.\n"
                f"Tip: If this was a typo, check the spelling (e.g. 'Franklin Lakes, NJ' vs 'Frankin Lakes, NJ'), "
                f"or try querying the county/region (e.g. 'Bergen County, New Jersey' or 'Passaic County, New Jersey')."
            ) from e

        return self.filter_graph(G)

    def fetch_from_radius(
        self,
        center_query: str,
        radius_miles: float = 10.0,
        simplify: bool = True,
        retain_all: bool = True,
    ) -> nx.MultiDiGraph:
        """Download road network within a radius (in miles) around a central place or address.

        Args:
            center_query: Town, landmark, or coordinates (e.g. 'Warwick, NY')
            radius_miles: Search radius in miles (default: 10.0 miles)
            simplify: Whether to collapse non-intersection nodes
            retain_all: Retain all road components

        Returns:
            NetworkX MultiDiGraph of driving roads
        """
        self._configure_useful_tags()

        try:
            lat, lon = ox.geocode(center_query)
        except Exception as e:
            raise ValueError(f"Could not locate geographic coordinates for '{center_query}': {e}") from e

        radius_meters = float(radius_miles) * 1609.344

        try:
            G = ox.graph_from_point(
                (lat, lon),
                dist=radius_meters,
                network_type="drive",
                custom_filter='["highway"~"primary|secondary|tertiary|unclassified"]',
                simplify=simplify,
                retain_all=retain_all,
                truncate_by_edge=True,
            )
        except Exception:
            G = ox.graph_from_point(
                (lat, lon),
                dist=radius_meters,
                network_type="drive",
                simplify=simplify,
                retain_all=retain_all,
                truncate_by_edge=True,
            )
        return self.filter_graph(G)

    def filter_graph(self, G: nx.MultiDiGraph) -> nx.MultiDiGraph:
        """Filter road segments to sports car enthusiast criteria.

        Retains:
            - Eligible highway classifications (secondary, tertiary, unclassified, rural primary)
            - Paved surfaces (asphalt, concrete, paved, or default paved for primary/secondary)
        Removes:
            - Interstates/motorways, residential, living streets, service roads, tracks
            - Explicit unpaved surfaces (gravel, dirt, ground)
            - Private or restricted roads
        """
        edges_to_remove = []

        for u, v, k, data in G.edges(keys=True, data=True):
            if not self.is_edge_eligible(data):
                edges_to_remove.append((u, v, k))
            else:
                # Normalize edge attributes
                self.normalize_edge_data(G, u, v, data)

        G_filtered = G.copy()
        G_filtered.remove_edges_from(edges_to_remove)

        # Remove isolated orphan nodes
        isolated_nodes = [node for node, degree in G_filtered.degree() if degree == 0]
        G_filtered.remove_nodes_from(isolated_nodes)

        return G_filtered

    def is_edge_eligible(self, data: Dict[str, Any]) -> bool:
        """Check if an edge meets the enthusiast vehicle and road quality criteria."""
        highway = data.get("highway")
        if isinstance(highway, list):
            highway = highway[0]
        if not highway or str(highway).lower() not in self.config.eligible_highways:
            return False

        # Check access restrictions
        access = data.get("access")
        if isinstance(access, list):
            access = access[0]
        if access and str(access).lower() in self.config.disallowed_access:
            return False

        motor_vehicle = data.get("motor_vehicle")
        if isinstance(motor_vehicle, list):
            motor_vehicle = motor_vehicle[0]
        if motor_vehicle and str(motor_vehicle).lower() in {"no", "private", "agricultural"}:
            return False

        # Check surface quality
        surface = data.get("surface")
        if isinstance(surface, list):
            surface = surface[0]
        if surface and str(surface).lower() in self.config.unpaved_surfaces:
            return False

        # Exclude tracks / unpaved tracktypes
        tracktype = data.get("tracktype")
        if tracktype and str(tracktype).lower() in {"grade2", "grade3", "grade4", "grade5"}:
            return False

        # Exclude service roads
        if "service" in data:
            return False

        return True

    def normalize_edge_data(
        self, G: nx.MultiDiGraph, u: Any, v: Any, data: Dict[str, Any]
    ) -> None:
        """Enrich edge data with uniform fields (speed, name, ref, geometry, commercial & driveway flags)."""
        highway = extract_primary_string(data.get("highway")) or "unclassified"
        data["highway_type"] = highway

        # Name & Ref
        name = extract_primary_string(data.get("name"))
        ref = extract_primary_string(data.get("ref"))
        data["canonical_name"] = name or ref or "Unnamed Road"
        data["canonical_ref"] = ref

        # Residential Suburban Detection
        landuse = str(data.get("landuse", "")).lower()
        building = str(data.get("building", "")).lower()
        name_lower = (data["canonical_name"] or "").lower()
        name_words = set(re.findall(r"\b\w+\b", name_lower))
        res_suffixes = {"avenue", "ave", "street", "st", "lane", "ln", "drive", "dr", "court", "ct", "place", "pl", "circle", "cir", "terrace", "ter", "subdivision"}
        is_res_name = bool(name_words & res_suffixes)

        is_residential = (
            highway in {"residential", "living_street"}
            or landuse in {"residential", "suburb"}
            or building in {"residential", "house", "detached"}
            or (is_res_name and highway in {"tertiary", "unclassified"})
        )
        data["is_residential"] = is_residential

        # Speed limit
        raw_speed = data.get("maxspeed")
        fallback_speed = self.config.default_speeds_by_highway.get(
            highway, self.config.target_speed_mph
        )
        if raw_speed is None and is_residential:
            fallback_speed = 25.0

        data["speed_mph"] = parse_osm_speed(raw_speed, default_speed_mph=fallback_speed)

        # Commercial & Business Activity Indicators
        landuse = str(data.get("landuse", "")).lower()
        building = str(data.get("building", "")).lower()
        shop = data.get("shop")
        amenity = str(data.get("amenity", "")).lower()

        comm_keywords = {"avenue", "ave", "broadway", "street", "st", "plaza", "center", "mall", "boulevard", "blvd", "shopping"}
        has_comm_name = bool(name_words & comm_keywords)

        data["is_commercial"] = (
            landuse in {"commercial", "retail"}
            or building in {"commercial", "retail", "supermarket"}
            or shop is not None
            or amenity in {"fast_food", "restaurant", "fuel", "bank", "cafe", "pub", "marketplace"}
            or (has_comm_name and highway in {"primary", "secondary"})
        )

        # Smoothness & Road Surface Quality
        smoothness_val = extract_primary_string(data.get("smoothness"))
        if smoothness_val:
            smoothness_val = smoothness_val.lower().strip()
        data["smoothness"] = smoothness_val or "good"
        data["smoothness_penalty"] = self.config.smoothness_penalties.get(
            smoothness_val or "", 0.0
        )

        service = str(data.get("service", "")).lower()
        data["is_driveway"] = (
            highway == "service"
            or service in {"driveway", "drive-through", "parking_aisle"}
        )

        # Ensure geometry exists (if not generated by OSMnx simplify)
        if "geometry" not in data or data["geometry"] is None:
            u_node = G.nodes[u]
            v_node = G.nodes[v]
            data["geometry"] = LineString(
                [(u_node["x"], u_node["y"]), (v_node["x"], v_node["y"])]
            )
