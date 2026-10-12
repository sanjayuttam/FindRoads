"""Road Discovery Engine: Algorithmic ranking and route discovery for sports car enthusiasts."""

__version__ = "0.1.0"

from .config import DiscoveryConfig
from .geometry import (
    calculate_sinuosity,
    calculate_angular_twistiness,
    calculate_line_length_km,
    calculate_chord_length_km,
)
from .scoring import (
    calculate_sinuosity_score,
    calculate_curvature_score,
    calculate_speed_score,
    calculate_traffic_penalty,
    calculate_composite_score,
)
from .ingestion import RoadNetworkIngester
from .stitching import RouteStitcher
from .pipeline import RoadDiscoveryEngine

__all__ = [
    "DiscoveryConfig",
    "calculate_sinuosity",
    "calculate_angular_twistiness",
    "calculate_line_length_km",
    "calculate_chord_length_km",
    "calculate_sinuosity_score",
    "calculate_curvature_score",
    "calculate_speed_score",
    "calculate_traffic_penalty",
    "calculate_composite_score",
    "RoadNetworkIngester",
    "RouteStitcher",
    "RoadDiscoveryEngine",
]
