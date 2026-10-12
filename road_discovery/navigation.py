"""Navigation and Route Export Module.

Converts discovered road geometries into actionable navigation routes:
1. One-click Google Maps Navigation URLs (with waypoints ensuring the twisty route is followed).
2. GPX 1.1 tracks for Garmin, Apple Maps, InRoute, Scenic, and Calimoto.
3. KML files for 1-click import into Google My Maps.
"""

import urllib.parse
from typing import List, Dict, Any, Tuple, Optional
from xml.etree import ElementTree as ET
from shapely.geometry import shape, LineString


def generate_google_maps_url(
    line: LineString, num_mid_waypoints: int = 2
) -> str:
    """Generate a 1-click Google Maps turn-by-turn navigation URL for a road corridor.

    Strategically places intermediate waypoints along the curves to prevent
    Google Maps from rerouting onto a straight interstate or highway bypass.

    Args:
        line: Shapely LineString geometry of the route
        num_mid_waypoints: Number of intermediate curve waypoints (default: 2)

    Returns:
        Google Maps universal direction URL
    """
    coords = list(line.coords)
    if len(coords) < 2:
        return ""

    # Origin (lat, lon)
    origin_lon, origin_lat = coords[0][:2]
    # Destination (lat, lon)
    dest_lon, dest_lat = coords[-1][:2]

    # Sample midpoints evenly along coordinate vertices
    waypoints = []
    if num_mid_waypoints > 0 and len(coords) > 2:
        step = max(1, len(coords) // (num_mid_waypoints + 1))
        for i in range(1, num_mid_waypoints + 1):
            idx = min(len(coords) - 2, i * step)
            wp_lon, wp_lat = coords[idx][:2]
            waypoints.append(f"{round(wp_lat, 6)},{round(wp_lon, 6)}")

    base_url = "https://www.google.com/maps/dir/?api=1"
    params = {
        "origin": f"{round(origin_lat, 6)},{round(origin_lon, 6)}",
        "destination": f"{round(dest_lat, 6)},{round(dest_lon, 6)}",
        "travelmode": "driving",
    }
    if waypoints:
        params["waypoints"] = "|".join(waypoints)

    return f"{base_url}&{urllib.parse.urlencode(params)}"


def generate_street_view_url(line: LineString) -> str:
    """Generate a 1-click Google Street View panorama URL for a road segment."""
    coords = list(line.coords)
    if not coords:
        return ""
    mid_idx = len(coords) // 2
    lon, lat = coords[mid_idx][:2]
    return f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={round(lat, 6)},{round(lon, 6)}"


def generate_multi_road_tour_url(
    features: List[Dict[str, Any]], max_stops: int = 8
) -> str:
    """Generate a combined Google Maps Tour URL connecting multiple top driving roads.

    Google Maps URL API supports up to 9 total points (origin + 7 waypoints + destination).
    This picks the start and endpoints of the highest-scoring roads in order.
    """
    if not features:
        return ""

    stops: List[Tuple[float, float]] = []

    for feat in features[:max_stops]:
        geom = feat.get("geometry")
        if not geom:
            continue
        line = shape(geom)
        coords = list(line.coords)
        if len(coords) >= 2:
            # Add start and end point of each enthusiast road
            stops.append((coords[0][1], coords[0][0]))
            stops.append((coords[-1][1], coords[-1][0]))

    if len(stops) < 2:
        return ""

    # Subsample to fit Google Maps limit (max 9 points total)
    if len(stops) > 9:
        step = (len(stops) - 1) / 8.0
        stops = [stops[int(round(i * step))] for i in range(9)]

    origin = stops[0]
    destination = stops[-1]
    waypoints = [f"{round(lat, 6)},{round(lon, 6)}" for lat, lon in stops[1:-1]]

    params = {
        "api": "1",
        "origin": f"{round(origin[0], 6)},{round(origin[1], 6)}",
        "destination": f"{round(destination[0], 6)},{round(destination[1], 6)}",
        "travelmode": "driving",
    }
    if waypoints:
        params["waypoints"] = "|".join(waypoints)

    return f"https://www.google.com/maps/dir/?{urllib.parse.urlencode(params)}"


def export_routes_to_gpx(geojson_data: Dict[str, Any], output_path: str) -> None:
    """Export GeoJSON routes to standard GPX 1.1 format.

    Compatible with:
    - Garmin GPS & BMW Motorrad units
    - InRoute, Scenic, Calimoto, Rever (iOS & Android)
    - Google My Maps (google.com/mymaps)
    - Apple Maps (iOS 18+ custom route import)
    """
    gpx = ET.Element(
        "gpx",
        version="1.1",
        creator="RoadDiscoveryEngine",
        xmlns="http://www.topografix.com/GPX/1/1",
    )

    features = geojson_data.get("features", [])

    for feat in features:
        p = feat.get("properties", {})
        geom = feat.get("geometry", {})
        if geom.get("type") != "LineString":
            continue

        road_name = p.get("road_name", "Enthusiast Route")
        score = p.get("composite_driving_score", 0)
        length_km = p.get("length_km", 0)

        trk = ET.SubElement(gpx, "trk")
        ET.SubElement(trk, "name").text = f"{road_name} (Score: {score}/100, {length_km}km)"
        ET.SubElement(trk, "desc").text = (
            f"Curvature: {p.get('curvature_score')}/100 | "
            f"Sinuosity: {p.get('sinuosity_score')}/100 | "
            f"Speed Limit: {p.get('avg_speed_limit')} mph"
        )

        trkseg = ET.SubElement(trk, "trkseg")
        for lon, lat in geom.get("coordinates", []):
            ET.SubElement(trkseg, "trkpt", lat=str(round(lat, 6)), lon=str(round(lon, 6)))

    tree = ET.ElementTree(gpx)
    ET.indent(tree, space="  ", level=0)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def export_routes_to_kml(geojson_data: Dict[str, Any], output_path: str) -> None:
    """Export GeoJSON routes to KML for 1-click import into Google My Maps & Google Earth."""
    kml = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    doc = ET.SubElement(kml, "Document")
    ET.SubElement(doc, "name").text = "Enthusiast Driving Routes"

    # Color styles: Red (Legendary), Orange (Spirited), Green (Technical)
    colors = {
        "legendary": "ff4757ff", # AABBGGRR
        "spirited": "ff02a5ff",
        "technical": "ff73d52e",
        "mild": "ffffa170",
    }
    for style_id, color_hex in colors.items():
        style = ET.SubElement(doc, "Style", id=style_id)
        line_style = ET.SubElement(style, "LineStyle")
        ET.SubElement(line_style, "color").text = color_hex
        ET.SubElement(line_style, "width").text = "5"

    features = geojson_data.get("features", [])
    for feat in features:
        p = feat.get("properties", {})
        geom = feat.get("geometry", {})
        if geom.get("type") != "LineString":
            continue

        score = p.get("composite_driving_score", 0)
        if score >= 75:
            style_url = "#legendary"
        elif score >= 60:
            style_url = "#spirited"
        elif score >= 45:
            style_url = "#technical"
        else:
            style_url = "#mild"

        placemark = ET.SubElement(doc, "Placemark")
        ET.SubElement(placemark, "name").text = p.get("road_name", "Route")
        ET.SubElement(placemark, "styleUrl").text = style_url
        ET.SubElement(placemark, "description").text = (
            f"Fun Score: {score}/100\n"
            f"Length: {p.get('length_km')} km ({round(p.get('length_km', 0)*0.621371, 1)} mi)\n"
            f"Speed Limit: {p.get('avg_speed_limit')} mph\n"
            f"Curves: {p.get('curve_count', 0)}"
        )

        ls = ET.SubElement(placemark, "LineString")
        coord_strings = [f"{lon},{lat},0" for lon, lat in geom.get("coordinates", [])]
        ET.SubElement(ls, "coordinates").text = " ".join(coord_strings)

    tree = ET.ElementTree(kml)
    ET.indent(tree, space="  ", level=0)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
