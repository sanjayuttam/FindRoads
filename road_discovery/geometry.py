"""Geometrical calculations for road sinuosity, heading deltas, and curvature."""

import math
from typing import List, Tuple, Dict, Any
from shapely.geometry import LineString


EARTH_RADIUS_METERS = 6371008.8  # Mean Earth radius in meters


def haversine_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Calculate the great-circle distance between two points in meters using the Haversine formula."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_METERS * c


def calculate_bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Calculate the initial geodetic bearing from point 1 to point 2 in degrees [0, 360)."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)

    y = math.sin(delta_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)

    bearing = math.degrees(math.atan2(y, x))
    return (bearing + 360.0) % 360.0


def calculate_line_length_km(line: LineString) -> float:
    """Calculate the cumulative geodetic arc length of a Shapely LineString in kilometers."""
    coords = list(line.coords)
    if len(coords) < 2:
        return 0.0

    total_meters = 0.0
    for i in range(len(coords) - 1):
        lon1, lat1 = coords[i][:2]
        lon2, lat2 = coords[i + 1][:2]
        total_meters += haversine_distance_m(lon1, lat1, lon2, lat2)

    return total_meters / 1000.0


def calculate_chord_length_km(line: LineString) -> float:
    """Calculate the straight-line geodetic chord distance between the endpoints in kilometers."""
    coords = list(line.coords)
    if len(coords) < 2:
        return 0.0

    lon1, lat1 = coords[0][:2]
    lon2, lat2 = coords[-1][:2]
    return haversine_distance_m(lon1, lat1, lon2, lat2) / 1000.0


def calculate_sinuosity(line: LineString, min_chord_km: float = 0.05) -> float:
    """Calculate the sinuosity index: S = arc_length / chord_length.

    A straight line has S = 1.0. Winding roads have S > 1.3.
    If the start and end points form a loop (chord ~ 0), chord length is clamped to min_chord_km
    or evaluated using a rolling sub-window sinuosity to prevent division by zero.
    """
    arc_km = calculate_line_length_km(line)
    if arc_km <= 0.0:
        return 1.0

    chord_km = calculate_chord_length_km(line)

    # For loop routes where chord ~ 0, calculate windowed sinuosity across intermediate chords
    if chord_km < min_chord_km:
        coords = list(line.coords)
        if len(coords) > 2:
            mid_idx = len(coords) // 2
            line1 = LineString(coords[: mid_idx + 1])
            line2 = LineString(coords[mid_idx:])
            s1 = calculate_sinuosity(line1, min_chord_km=min_chord_km)
            s2 = calculate_sinuosity(line2, min_chord_km=min_chord_km)
            return (s1 + s2) / 2.0
        chord_km = min_chord_km

    return max(1.0, arc_km / chord_km)


def resample_coords_by_distance(
    coords: List[Tuple[float, float]], step_meters: float = 15.0
) -> List[Tuple[float, float]]:
    """Resample coordinate sequence along geodesic arc at regular intervals.

    This dampens high-frequency GPS node digitizer jitter while capturing genuine curve geometry.
    """
    if len(coords) < 2:
        return coords

    resampled = [coords[0]]
    accumulated_dist = 0.0

    for i in range(len(coords) - 1):
        p1 = coords[i]
        p2 = coords[i + 1]
        seg_dist = haversine_distance_m(p1[0], p1[1], p2[0], p2[1])

        if seg_dist == 0.0:
            continue

        accumulated_dist += seg_dist
        if accumulated_dist >= step_meters:
            resampled.append(p2)
            accumulated_dist = 0.0

    # Ensure last coordinate is included
    if resampled[-1] != coords[-1]:
        resampled.append(coords[-1])

    return resampled


def calculate_angular_twistiness(
    line: LineString,
    step_meters: float = 15.0,
    curve_deflection_threshold_deg: float = 15.0,
) -> Dict[str, Any]:
    """Analyze the angular deflection rate and curve events along the road.

    Returns:
        total_deflection_deg: Sum of absolute heading changes along the route.
        twistiness_deg_per_km: Total deflection divided by arc length in km.
        curve_count: Number of distinct curve events exceeding the threshold.
        curves_per_km: Curve events per kilometer.
        length_km: Geodesic arc length in km.
    """
    raw_coords = [(c[0], c[1]) for c in line.coords]
    length_km = calculate_line_length_km(line)

    if len(raw_coords) < 2 or length_km < 0.01:
        return {
            "total_deflection_deg": 0.0,
            "twistiness_deg_per_km": 0.0,
            "curve_count": 0,
            "curves_per_km": 0.0,
            "length_km": length_km,
        }

    # Resample to filter out sub-meter vertex noise
    coords = resample_coords_by_distance(raw_coords, step_meters=step_meters)
    if len(coords) < 3:
        return {
            "total_deflection_deg": 0.0,
            "twistiness_deg_per_km": 0.0,
            "curve_count": 0,
            "curves_per_km": 0.0,
            "length_km": length_km,
        }

    # Calculate bearings between consecutive resampled nodes
    bearings = []
    for i in range(len(coords) - 1):
        b = calculate_bearing_deg(
            coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1]
        )
        bearings.append(b)

    # Compute heading deltas between consecutive vectors
    total_deflection = 0.0
    curve_count = 0
    in_curve = False
    current_curve_deflection = 0.0

    for i in range(len(bearings) - 1):
        diff = abs(bearings[i + 1] - bearings[i])
        if diff > 180.0:
            diff = 360.0 - diff

        total_deflection += diff

        # Curve event detection: cluster consecutive angular changes
        if diff >= 3.0:  # slight turn initiated
            current_curve_deflection += diff
            if current_curve_deflection >= curve_deflection_threshold_deg and not in_curve:
                curve_count += 1
                in_curve = True
        else:
            in_curve = False
            current_curve_deflection = 0.0

    twistiness_deg_per_km = total_deflection / max(0.001, length_km)
    curves_per_km = curve_count / max(0.001, length_km)

    return {
        "total_deflection_deg": round(total_deflection, 1),
        "twistiness_deg_per_km": round(twistiness_deg_per_km, 1),
        "curve_count": curve_count,
        "curves_per_km": round(curves_per_km, 2),
        "length_km": round(length_km, 3),
    }
