"""FastAPI REST API server for the Road Discovery Engine.

Exposes GeoJSON endpoints consumable by web frontends (Leaflet, Mapbox, React),
and serves the interactive driving map and herdr.dev styled navigation guide.
"""

import os
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from fastapi import FastAPI, Query, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import DiscoveryConfig
from .pipeline import RoadDiscoveryEngine
from .ingestion import setup_robust_overpass

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Initialize robust Overpass settings and proxy fallbacks
setup_robust_overpass()

app = FastAPI(
    title="Road Discovery Engine API",
    description="Identifies and ranks top enthusiast driving routes for sports cars using OpenStreetMap.",
    version="0.2.0",
)

# Enable CORS for local and web frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PRESETS = {
    "tail-of-the-dragon": {
        "name": "Tail of the Dragon (US 129 / Deals Gap, NC/TN)",
        "bbox": (-83.98, 35.44, -83.88, 35.54),
        "target_speed": 45.0,
    },
    "angeles-crest": {
        "name": "Angeles Crest Highway (CA-2, Southern California)",
        "bbox": (-118.20, 34.22, -118.08, 34.30),
        "target_speed": 50.0,
    },
}


class DiscoverRequest(BaseModel):
    address: str = Field(..., description="Address, city, town, landmark, or ZIP code (e.g. 'Warwick, NY', 'Franklin Lakes, NJ')")
    radius: float = Field(12.0, description="Search radius in miles around the location")
    min_length: float = Field(2.5, description="Minimum route length in km")
    target_speed: float = Field(50.0, description="Target driving speed in mph")


class LoopRouteRequest(BaseModel):
    address: str = Field(..., description="Start/center location for driving loop (e.g. 'Franklin Lakes, NJ')")
    loop_miles: float = Field(20.0, description="Target driving loop length in miles")
    target_speed: float = Field(50.0, description="Target speed in mph")


class PointToPointRequest(BaseModel):
    origin: str = Field(..., description="Start / origin address or landmark (e.g. 'Franklin Lakes, NJ')")
    destination: str = Field(..., description="End / destination address or landmark (e.g. 'Warwick, NY')")
    target_speed: float = Field(50.0, description="Target speed in mph")


class QARatingRequest(BaseModel):
    road_name: str = Field(..., description="Name of the road being rated")
    user_rating: int = Field(..., ge=1, le=5, description="Human driver rating from 1 to 5 stars")
    algo_score: float = Field(..., description="Current algorithm calculated fun score")
    suggested_score: Optional[float] = Field(None, description="Driver suggested true score (0-100)")
    tags: Optional[List[str]] = Field(default_factory=list, description="Tags like 'too_residential', 'heavy_stoplights', 'great_sweepers'")
    notes: Optional[str] = Field("", description="Driver feedback notes")
    location: Optional[str] = Field("", description="Geographic area or town name")


@app.get("/health")
def health_check():
    """Health check probe."""
    return {"status": "healthy", "service": "road-discovery-engine"}


@app.get("/api/v1/presets")
def list_presets():
    """List pre-configured enthusiast driving road presets."""
    return PRESETS


@app.post("/api/discover")
def discover_roads_post(req: DiscoverRequest):
    """Discover curvy enthusiast driving routes around an address or place (POST)."""
    return run_discovery(req.address, req.radius, req.min_length, req.target_speed)


@app.get("/api/discover")
def discover_roads_get(
    address: str = Query(..., description="Address, town, or landmark (e.g. 'Warwick, NY')"),
    radius: float = Query(12.0, description="Search radius in miles"),
    min_length: float = Query(2.5, description="Minimum route length in km"),
    target_speed: float = Query(50.0, description="Target speed in mph"),
):
    """Discover curvy enthusiast driving routes around an address or place (GET)."""
    return run_discovery(address, radius, min_length, target_speed)


@app.post("/api/route/loop")
def route_loop_post(req: LoopRouteRequest):
    """Generate a closed loop driving route around a starting location."""
    return run_loop_routing(req.address, req.loop_miles, req.target_speed)


@app.post("/api/route/point-to-point")
def route_p2p_post(req: PointToPointRequest):
    """Generate a twisty point-to-point driving route between Origin and Destination."""
    return run_p2p_routing(req.origin, req.destination, req.target_speed)


@app.post("/api/qa/rate")
def rate_road_qa(req: QARatingRequest):
    """Submit a driver QA rating and feedback record for a road."""
    from .qa import RoadQAEngine
    qa_engine = RoadQAEngine()
    record = qa_engine.add_feedback(
        road_name=req.road_name,
        user_rating=req.user_rating,
        algo_score=req.algo_score,
        suggested_score=req.suggested_score,
        tags=req.tags,
        notes=req.notes or "",
        location=req.location or ""
    )
    return {"status": "success", "message": "QA rating saved successfully", "record": record}


@app.get("/api/qa/summary")
def get_qa_summary():
    """Retrieve aggregate QA feedback metrics and algorithm accuracy stats."""
    from .qa import RoadQAEngine
    qa_engine = RoadQAEngine()
    return qa_engine.get_qa_summary()


@app.post("/api/qa/calibrate")
def calibrate_algorithm_weights():
    """Re-calibrate algorithm scoring weights based on human QA benchmark ratings."""
    from .qa import RoadQAEngine
    qa_engine = RoadQAEngine()
    return qa_engine.calibrate_weights()


def save_geojson_outputs(engine: RoadDiscoveryEngine, geojson: Dict[str, Any]):
    """Save GeoJSON, GPX, and KML files to disk."""
    try:
        out_geojson = os.path.join(ROOT_DIR, "discovered_routes.geojson")
        out_gpx = os.path.join(ROOT_DIR, "discovered_routes.gpx")
        out_kml = os.path.join(ROOT_DIR, "discovered_routes.kml")

        engine.save_geojson(geojson, out_geojson)
        engine.save_gpx(geojson, out_gpx)
        engine.save_kml(geojson, out_kml)

        ex_dir = os.path.join(ROOT_DIR, "examples")
        if os.path.exists(ex_dir):
            engine.save_geojson(geojson, os.path.join(ex_dir, "discovered_routes.geojson"))
            engine.save_gpx(geojson, os.path.join(ex_dir, "discovered_routes.gpx"))
    except Exception as e:
        print(f"[API Save Warning] {e}")


def run_discovery(address: str, radius_miles: float, min_length_km: float, target_speed_mph: float) -> Dict[str, Any]:
    """Execute road discovery pipeline for a given address/place."""
    query = address.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Address or place cannot be empty.")

    print(f"\n[API Discover] Ingesting road network for: '{query}' (radius: {radius_miles} miles)...")

    config = DiscoveryConfig(
        target_speed_mph=target_speed_mph,
        min_route_length_km=min_length_km,
    )
    engine = RoadDiscoveryEngine(config)

    try:
        geojson = engine.discover_from_radius(query, radius_miles=radius_miles)
        features = geojson.get("features", [])

        if len(features) == 0:
            print(f"[API Discover] Radius search returned 0 routes, trying municipal place boundary...")
            geojson = engine.discover_from_place(query)
            features = geojson.get("features", [])

    except Exception as e:
        print(f"[API Discover Error] {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Could not discover roads for '{query}': {str(e)}",
        )

    geojson["metadata"]["route_mode"] = "discover"
    save_geojson_outputs(engine, geojson)
    print(f"[API Discover Done] Discovered {len(features)} routes for '{query}'.")
    return geojson


def run_loop_routing(address: str, loop_miles: float, target_speed_mph: float) -> Dict[str, Any]:
    query = address.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Address cannot be empty.")

    config = DiscoveryConfig(target_speed_mph=target_speed_mph, min_route_length_km=2.0)
    engine = RoadDiscoveryEngine(config)

    print(f"\n[API Loop Route] Building {loop_miles} mile continuous loop circuit around: '{query}'...")
    try:
        geojson = engine.build_loop_route(query, loop_miles=loop_miles)
    except Exception as e:
        print(f"[API Loop Route Fallback] {e}, attempting fallback radius discovery...")
        try:
            radius_miles = max(4.0, loop_miles / 2.0)
            geojson = engine.discover_from_radius(query, radius_miles=radius_miles)
            geojson["metadata"]["route_mode"] = "loop"
            geojson["metadata"]["loop_miles"] = loop_miles
        except Exception as fallback_err:
            print(f"[API Loop Route Failed] {fallback_err}")
            raise HTTPException(
                status_code=400,
                detail=f"Could not locate geographic coordinates or build loop route for '{query}'. Check location spelling or try a nearby town/county name.",
            ) from fallback_err

    geojson["metadata"]["origin_address"] = query
    save_geojson_outputs(engine, geojson)
    print(f"[API Loop Route Done] Generated {len(geojson.get('features', []))} connected loop legs for '{query}'.")
    return geojson


def run_p2p_routing(origin: str, destination: str, target_speed_mph: float) -> Dict[str, Any]:
    orig_str = origin.strip()
    dest_str = destination.strip()
    if not orig_str or not dest_str:
        raise HTTPException(status_code=400, detail="Origin and Destination cannot be empty.")

    config = DiscoveryConfig(target_speed_mph=target_speed_mph, min_route_length_km=2.0)
    engine = RoadDiscoveryEngine(config)

    print(f"\n[API P2P Route] Building continuous corridor from '{orig_str}' to '{dest_str}'...")
    try:
        geojson = engine.build_p2p_route(orig_str, dest_str)
    except Exception as e:
        print(f"[API P2P Route Fallback] {e}, attempting fallback bbox discovery...")
        try:
            import osmnx as ox
            lat1, lon1 = ox.geocode(orig_str)
            lat2, lon2 = ox.geocode(dest_str)
            bbox = (min(lon1, lon2)-0.05, min(lat1, lat2)-0.05, max(lon1, lon2)+0.05, max(lat1, lat2)+0.05)
            geojson = engine.discover_from_bbox(bbox)
        except Exception as fallback_err:
            print(f"[API P2P Route Failed] {fallback_err}")
            raise HTTPException(
                status_code=400,
                detail=f"Could not build point-to-point route from '{orig_str}' to '{dest_str}'. Check location spelling or try a nearby town/county name.",
            ) from fallback_err

    geojson["metadata"]["route_mode"] = "point_to_point"
    geojson["metadata"]["origin_name"] = orig_str
    geojson["metadata"]["destination_name"] = dest_str

    save_geojson_outputs(engine, geojson)
    print(f"[API P2P Route Done] Generated {len(geojson.get('features', []))} connected corridor legs from '{orig_str}' to '{dest_str}'.")
    return geojson



@app.get("/api/v1/routes/bbox")
def get_routes_by_bbox(
    west: float = Query(..., description="Min longitude"),
    south: float = Query(..., description="Min latitude"),
    east: float = Query(..., description="Max longitude"),
    north: float = Query(..., description="Max latitude"),
    target_speed: float = Query(50.0, description="Target speed limit in mph"),
    min_length: float = Query(3.0, description="Minimum route length in km"),
):
    """Query a bounding box and return a ranked GeoJSON FeatureCollection."""
    try:
        config = DiscoveryConfig(
            target_speed_mph=target_speed,
            min_route_length_km=min_length,
        )
        engine = RoadDiscoveryEngine(config)
        bbox = (west, south, east, north)
        geojson = engine.discover_from_bbox(bbox)
        return geojson
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/routes/preset/{slug}")
def get_routes_by_preset(slug: str):
    """Retrieve pre-configured famous driving road routes."""
    if slug not in PRESETS:
        raise HTTPException(
            status_code=404,
            detail=f"Preset '{slug}' not found. Available: {list(PRESETS.keys())}",
        )

    preset = PRESETS[slug]
    config = DiscoveryConfig(
        target_speed_mph=preset.get("target_speed", 50.0),
        min_route_length_km=3.0,
    )
    engine = RoadDiscoveryEngine(config)
    return engine.discover_from_bbox(preset["bbox"])


# ── Webpage & Static File Handlers ──
@app.get("/")
def serve_index():
    """Serve the interactive web map."""
    index_path = os.path.join(ROOT_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "index.html not found"}


@app.get("/navigation_guide.html")
def serve_guide():
    """Serve the herdr.dev styled navigation guide."""
    guide_path = os.path.join(ROOT_DIR, "navigation_guide.html")
    if os.path.exists(guide_path):
        return FileResponse(guide_path)
    return {"message": "navigation_guide.html not found"}


# Mount root directory for static downloads (.geojson, .gpx, .kml)
app.mount("/", StaticFiles(directory=ROOT_DIR, html=True), name="static")
