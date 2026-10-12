"""Proof of Concept 1: Tail of the Dragon (US 129 Deals Gap, NC/TN).

Demonstrates the Road Discovery Engine identifying and ranking the world-famous
Tail of the Dragon on US 129 and connecting enthusiast routes (NC 28 / Moonshiner 28).
"""

import os
import json
from road_discovery.config import DiscoveryConfig
from road_discovery.pipeline import RoadDiscoveryEngine


def run_tail_of_the_dragon_poc():
    print("=" * 80)
    print(" PROOF OF CONCEPT: TAIL OF THE DRAGON (US 129 / DEALS GAP, NC/TN)")
    print("=" * 80)

    # Deals Gap / US 129 Corridor BBox (West, South, East, North)
    # Covering Robbinsville, Deals Gap, Cheoah Dam, and Chilhowee Lake
    bbox = (-83.98, 35.44, -83.88, 35.54)

    # Configure engine with sports car parameters
    # Note: Tail of the Dragon has a posted 30-35 mph technical speed limit due to 318 curves in 11 miles
    config = DiscoveryConfig(
        target_speed_mph=45.0,     # Enthusiast technical road target
        speed_sigma_mph=15.0,
        min_route_length_km=3.0,   # Minimum 3 km
    )

    engine = RoadDiscoveryEngine(config)

    print(f"\n[1/3] Querying OpenStreetMap network within BBox: {bbox}...")
    geojson = engine.discover_from_bbox(bbox)

    features = geojson.get("features", [])
    print(f"[2/3] Found and ranked {len(features)} eligible continuous routes.")

    print("\n" + "-" * 80)
    print(f"{'Rank':<5} {'Road Name':<32} {'Length':<10} {'Speed':<8} {'Sinuosity':<10} {'Curvature':<10} {'Fun Score':<10}")
    print("-" * 80)

    for i, f in enumerate(features, start=1):
        p = f["properties"]
        name = p["road_name"][:30]
        length = f"{p['length_km']} km"
        speed = f"{p['avg_speed_limit']} mph"
        sin = f"{p['sinuosity_score']}/100"
        curv = f"{p['curvature_score']}/100"
        score = f"{p['composite_driving_score']}/100"

        print(f"{i:<5} {name:<32} {length:<10} {speed:<8} {sin:<10} {curv:<10} {score:<10}")

    output_file = "tail_of_the_dragon_routes.geojson"
    print("\n[3/3] Exporting GeoJSON FeatureCollection...")
    engine.save_geojson(geojson, output_file)
    print(f"Successfully saved to: {output_file}")

    if features:
        top_route = features[0]["properties"]
        print("\n=== TOP ENTHUSIAST ROUTE DIAGNOSTICS ===")
        print(f"Road: {top_route['road_name']}")
        print(f"Length: {top_route['length_km']} km ({round(top_route['length_km']*0.621371, 1)} miles)")
        print(f"Raw Sinuosity: {top_route['sinuosity_raw']} (Sinuosity Score: {top_route['sinuosity_score']})")
        print(f"Twistiness Density: {top_route['twistiness_deg_per_km']} deg/km")
        print(f"Total Angular Deflection: {top_route['total_turn_degrees']:,} degrees")
        print(f"Curves Detected: {top_route['curve_count']} curves ({top_route['curves_per_km']} curves/km)")
        print(f"Traffic / Intersection Penalty: {top_route['traffic_penalty']}")
        print(f"Composite Fun Index: {top_route['composite_driving_score']}/100")
        print("=" * 80 + "\n")


if __name__ == "__main__":
    run_tail_of_the_dragon_poc()
