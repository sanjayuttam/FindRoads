"""Proof of Concept 2: Angeles Crest Highway (CA-2, San Gabriel Mountains, Southern California).

Demonstrates the Road Discovery Engine ranking the iconic Angeles Crest Highway (CA-2)
and surrounding mountain passes (Mount Wilson, Upper Big Tujunga).
"""

from road_discovery.config import DiscoveryConfig
from road_discovery.pipeline import RoadDiscoveryEngine


def run_angeles_crest_poc():
    print("=" * 80)
    print(" PROOF OF CONCEPT: ANGELES CREST HIGHWAY (CA-2, CALIFORNIA)")
    print("=" * 80)

    # San Gabriel Mountains BBox covering Angeles Crest Hwy (CA-2) mountain pass
    bbox = (-118.20, 34.22, -118.08, 34.30)

    config = DiscoveryConfig(
        target_speed_mph=50.0,
        speed_sigma_mph=12.0,
        min_route_length_km=3.0,  # Minimum 3 km
    )

    engine = RoadDiscoveryEngine(config)

    print(f"\n[1/3] Querying OpenStreetMap network within BBox: {bbox}...")
    geojson = engine.discover_from_bbox(bbox)

    features = geojson.get("features", [])
    print(f"[2/3] Found and ranked {len(features)} eligible continuous routes.")

    print("\n" + "-" * 85)
    print(f"{'Rank':<5} {'Road Name':<34} {'Length':<10} {'Speed':<8} {'Sinuosity':<10} {'Curvature':<10} {'Score':<8}")
    print("-" * 85)

    for i, f in enumerate(features[:10], start=1):
        p = f["properties"]
        name = p["road_name"][:32]
        length = f"{p['length_km']} km"
        speed = f"{p['avg_speed_limit']} mph"
        sin = f"{p['sinuosity_score']}/100"
        curv = f"{p['curvature_score']}/100"
        score = f"{p['composite_driving_score']}"

        print(f"{i:<5} {name:<34} {length:<10} {speed:<8} {sin:<10} {curv:<10} {score:<8}")

    output_file = "angeles_crest_routes.geojson"
    print("\n[3/3] Exporting GeoJSON FeatureCollection...")
    engine.save_geojson(geojson, output_file)
    print(f"Successfully saved to: {output_file}")

    if features:
        top_route = features[0]["properties"]
        print("\n=== TOP ENTHUSIAST ROUTE DIAGNOSTICS ===")
        print(f"Road: {top_route['road_name']}")
        print(f"Length: {top_route['length_km']} km ({round(top_route['length_km']*0.621371, 1)} miles)")
        print(f"Speed Limit: {top_route['avg_speed_limit']} mph (Speed Score: {top_route['speed_score']}/100)")
        print(f"Raw Sinuosity: {top_route['sinuosity_raw']} (Sinuosity Score: {top_route['sinuosity_score']}/100)")
        print(f"Twistiness Density: {top_route['twistiness_deg_per_km']} deg/km (Curvature Score: {top_route['curvature_score']}/100)")
        print(f"Total Angular Deflection: {top_route['total_turn_degrees']:,} degrees")
        print(f"Curves Detected: {top_route['curve_count']} curves ({top_route['curves_per_km']} curves/km)")
        print(f"Traffic / Intersection Penalty: {top_route['traffic_penalty']}")
        print(f"Composite Fun Index: {top_route['composite_driving_score']}/100")
        print("=" * 80 + "\n")


if __name__ == "__main__":
    run_angeles_crest_poc()
