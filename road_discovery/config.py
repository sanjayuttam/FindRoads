"""Configuration settings and defaults for the Road Discovery Engine."""

from dataclasses import dataclass, field
from typing import Set, List


@dataclass
class DiscoveryConfig:
    """Configuration parameters for road network filtering, stitching, and scoring."""

    # --- Highway & Surface Filtering ---
    # Eligible highway types for enthusiast spirited driving
    # Includes rural primary roads (like US 129 Deals Gap / CA-2 Angeles Crest)
    # Excludes motorways (interstates), residential, living streets, service roads, tracks
    eligible_highways: List[str] = field(
        default_factory=lambda: [
            "secondary",
            "tertiary",
            "unclassified",
            "primary",
            "secondary_link",
            "tertiary_link",
            "primary_link",
        ]
    )

    # Surfaces explicitly rejected as unpaved or loose
    unpaved_surfaces: Set[str] = field(
        default_factory=lambda: {
            "unpaved",
            "gravel",
            "dirt",
            "ground",
            "earth",
            "compacted",
            "fine_gravel",
            "sand",
            "mud",
            "grass",
            "wood",
            "rock",
            "pebblestone",
        }
    )

    # Allowed access tags (exclude private, no-entry, and agricultural roads)
    disallowed_access: Set[str] = field(
        default_factory=lambda: {"private", "no", "delivery", "forestry", "agricultural"}
    )

    # --- Speed Profile (Asymmetric Gaussian suitability window) ---
    # Target speed for spirited passenger car driving (typically 2nd-4th gear rhythm)
    target_speed_mph: float = 50.0
    speed_sigma_mph: float = 12.0
    speed_sigma_low_mph: float = 8.0   # Steeper falloff for lower speed limits (< 50 mph)
    speed_sigma_high_mph: float = 12.0 # Smooth decay for higher speeds (>= 50 mph)

    # Default fallback speed limits (in mph) when OSM tag maxspeed is omitted
    default_speeds_by_highway: dict = field(
        default_factory=lambda: {
            "motorway": 65.0,
            "motorway_link": 45.0,
            "trunk": 55.0,
            "trunk_link": 45.0,
            "primary": 35.0,
            "primary_link": 35.0,
            "secondary": 35.0,
            "secondary_link": 30.0,
            "tertiary": 30.0,
            "tertiary_link": 25.0,
            "unclassified": 30.0,
            "residential": 25.0,
        }
    )

    # --- Geometry & Curvature Scoring ---
    # Sinuosity index: S = arc_length / chord_length
    # S = 1.0 (straight). S >= sinuosity_max scores 100 on sinuosity component.
    sinuosity_max: float = 1.8

    # Angular twistiness density (cumulative absolute heading change in degrees/km)
    # T_scale controls the exponential saturation: score = 100 * (1 - exp(-T / T_scale))
    twistiness_scale: float = 350.0

    # Minimum segment step length in meters for calculating bearings (filters out digitizer jitter)
    min_step_meters: float = 10.0

    # Minimum angular deflection (degrees) to qualify as a curve event
    curve_threshold_deg: float = 15.0

    # --- Traffic & Congestion Penalties ---
    # Penalties applied per unit density
    intersection_penalty_weight: float = 12.0
    signals_penalty_weight: float = 20.0
    business_penalty_weight: float = 15.0
    driveway_penalty_weight: float = 10.0
    max_traffic_penalty: float = 50.0

    # --- Route Stitching & Length Thresholds ---
    # Minimum length for a stitched route to be retained in discovery output
    min_route_length_km: float = 3.0  # ~1.86 miles
    # Target length for full score multiplier (5 miles = ~8.0 km)
    target_route_length_km: float = 8.0
    min_length_multiplier: float = 0.50

    # Maximum angular deflection between adjacent segments to consider continuous stitching
    stitch_max_deflection_deg: float = 55.0

    # --- Composite Score Weights & Penalties ---
    # Base component weights (must sum to 1.0)
    weight_curvature: float = 0.50  # 50% technical corner density
    weight_sinuosity: float = 0.25  # 25% macro sinuosity
    weight_speed: float = 0.25      # 25% speed suitability window

    # Minimum curvature score threshold to avoid heavy straight-road dampening
    min_curvature_threshold: float = 30.0

    # Cop Risk Penalty Weight
    # High police risk corridors (> 35%) incur score penalties to reward secluded remote roads
    cop_risk_threshold_pct: float = 35.0
    cop_risk_penalty_factor: float = 0.30
    cop_risk_low_bonus_factor: float = 0.20

    # OSM Smoothness & Surface Quality Penalties (pts deducted)
    smoothness_penalties: dict = field(
        default_factory=lambda: {
            "excellent": 0.0,
            "good": 0.0,
            "intermediate": 5.0,
            "bad": 15.0,
            "very_bad": 30.0,
            "horrible": 40.0,
            "very_horrible": 50.0,
            "impassable": 60.0,
        }
    )
