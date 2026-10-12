"""Command-line interface (CLI) for the Road Discovery Engine."""

import argparse
import sys
from typing import Tuple

from .config import DiscoveryConfig
from .pipeline import RoadDiscoveryEngine


def parse_args():
    parser = argparse.ArgumentParser(
        description="Discover, filter, and score the best enthusiast driving roads from OpenStreetMap."
    )

    # Spatial query mode
    query_group = parser.add_mutually_exclusive_group(required=True)
    query_group.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        help="Bounding box coordinates in decimal degrees (e.g. -83.95 35.48 -83.90 35.53)",
    )
    query_group.add_argument(
        "--place",
        type=str,
        help="Named geographical place query (e.g. 'Graham County, North Carolina')",
    )

    # Driving preferences
    parser.add_argument(
        "--target-speed",
        type=float,
        default=50.0,
        help="Optimal target speed limit in mph (default: 50.0 mph)",
    )
    parser.add_argument(
        "--speed-sigma",
        type=float,
        default=12.0,
        help="Gaussian speed window tolerance in mph (default: 12.0 mph)",
    )
    parser.add_argument(
        "--min-length",
        type=float,
        default=3.0,
        help="Minimum continuous route length in km (default: 3.0 km)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="discovered_routes.geojson",
        help="Output GeoJSON filepath (default: discovered_routes.geojson)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Number of top routes to display in summary table (default: 10)",
    )
    parser.add_argument(
        "--radius",
        type=float,
        default=None,
        help="Search radius in miles around the location (e.g. --radius 10 for a 10-mile radius around a town)",
    )
    parser.add_argument(
        "--view",
        action="store_true",
        help="Automatically open the interactive web map visualizer in your default browser",
    )

    return parser.parse_args()


def print_summary_table(features, top_n: int = 10):
    """Print an enthusiast-friendly ranking table to the console."""
    if not features:
        print("\nNo routes met the minimum length and filtering criteria.")
        return

    print("\n" + "=" * 90)
    print(f" TOP {min(top_n, len(features))} DISCOVERED ENTHUSIAST DRIVING ROUTES")
    print("=" * 90)
    print(
        f"{'Rank':<5} {'Road Name':<30} {'Length':<10} {'Speed':<8} {'Sinuosity':<10} {'Curvature':<10} {'Score':<8}"
    )
    print("-" * 90)

    for i, feat in enumerate(features[:top_n], start=1):
        p = feat["properties"]
        name = p["road_name"][:28]
        length_str = f"{p['length_km']} km"
        speed_str = f"{p['avg_speed_limit']} mph"
        sin_str = f"{p['sinuosity_score']}/100"
        curv_str = f"{p['curvature_score']}/100"
        score_str = f"{p['composite_driving_score']}"

        print(
            f"{i:<5} {name:<30} {length_str:<10} {speed_str:<8} {sin_str:<10} {curv_str:<10} {score_str:<8}"
        )

    print("=" * 90 + "\n")


def main():
    args = parse_args()

    config = DiscoveryConfig(
        target_speed_mph=args.target_speed,
        speed_sigma_mph=args.speed_sigma,
        min_route_length_km=args.min_length,
    )

    engine = RoadDiscoveryEngine(config)

    print("\n[Road Discovery Engine] Initializing...")
    try:
        if args.bbox:
            bbox: Tuple[float, float, float, float] = tuple(args.bbox)
            print(f"[Query] Ingesting road network for BBox: {bbox}")
            geojson = engine.discover_from_bbox(bbox)
        elif args.radius:
            print(f"[Query] Ingesting road network for {args.radius} mile radius around: '{args.place}'")
            geojson = engine.discover_from_radius(args.place, radius_miles=args.radius)
        else:
            print(f"[Query] Ingesting road network for Place: '{args.place}'")
            geojson = engine.discover_from_place(args.place)
    except Exception as e:
        print(f"\n[Error] Could not process query: {e}\n", file=sys.stderr)
        sys.exit(1)

    features = geojson.get("features", [])
    print(f"[Done] Processed and ranked {len(features)} eligible continuous routes.")

    print_summary_table(features, top_n=args.top)

    import os
    base_name = os.path.splitext(args.output)[0]
    gpx_output = f"{base_name}.gpx"
    kml_output = f"{base_name}.kml"

    print(f"[Export] Saving GeoJSON results to: {args.output}")
    engine.save_geojson(geojson, args.output)

    print(f"[Export] Saving GPX track to: {gpx_output} (for Garmin, Apple Maps, InRoute, Scenic)")
    engine.save_gpx(geojson, gpx_output)

    print(f"[Export] Saving KML map to: {kml_output} (for Google My Maps / Google Earth)")
    engine.save_kml(geojson, kml_output)

    # Also mirror to examples directory so view_routes_map.html always has the freshest routes
    examples_mirror = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples", "discovered_routes.geojson"))
    examples_gpx = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples", "discovered_routes.gpx"))
    try:
        engine.save_geojson(geojson, examples_mirror)
        engine.save_gpx(geojson, examples_gpx)
    except Exception:
        pass

    tour_url = geojson.get("metadata", {}).get("google_maps_tour_url")
    if tour_url and features:
        print("\n" + "=" * 90)
        print(" 🗺️ 1-CLICK GOOGLE MAPS NAVIGATION (TOP ROUTES CONNECTED):")
        print(f" {tour_url}")
        print("=" * 90)

    print("\n[Success] Ready for Google Maps / Garmin / InRoute / Mapbox consumption.\n")

    if args.view:
        import os
        import webbrowser
        map_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples", "view_routes_map.html"))
        print(f"[View] Opening interactive map: file://{map_path}")
        webbrowser.open(f"file://{map_path}")


if __name__ == "__main__":
    main()
