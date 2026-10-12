"""Scoring algorithms for road curvature, sinuosity, speed suitability, and composite Fun Index."""

import math
from typing import Dict, Any, Optional
from .config import DiscoveryConfig


def calculate_sinuosity_score(sinuosity: float, config: Optional[DiscoveryConfig] = None) -> float:
    """Calculate the normalized sinuosity score [0, 100].

    S = 1.0 (straight line) -> 0.0 score.
    S >= sinuosity_max (default 1.8) -> 100.0 score.
    """
    if config is None:
        config = DiscoveryConfig()

    s = max(1.0, float(sinuosity))
    s_max = max(1.01, config.sinuosity_max)

    score = ((s - 1.0) / (s_max - 1.0)) * 100.0
    return round(max(0.0, min(100.0, score)), 1)


def calculate_curvature_score(
    twistiness_deg_per_km: float, config: Optional[DiscoveryConfig] = None
) -> float:
    """Calculate the normalized curvature score [0, 100] based on directional change rate.

    Uses an asymptotic exponential saturation curve:
        Score = 100 * (1 - exp(-T / T_scale))

    For T_scale = 600 deg/km:
        - 150 deg/km (mild country road) -> ~22.1
        - 300 deg/km (spirited sweeping curves) -> ~39.3
        - 600 deg/km (active canyon drive) -> ~63.2
        - 1200 deg/km (very technical pass) -> ~86.5
        - 1800+ deg/km (Tail of the Dragon tier) -> ~95.0+
    """
    if config is None:
        config = DiscoveryConfig()

    t = max(0.0, float(twistiness_deg_per_km))
    scale = max(10.0, config.twistiness_scale)

    score = 100.0 * (1.0 - math.exp(-t / scale))
    return round(max(0.0, min(100.0, score)), 1)


def calculate_speed_score(speed_mph: float, config: Optional[DiscoveryConfig] = None) -> float:
    """Calculate the speed suitability score [0, 100] using an asymmetric Gaussian distribution.

    Centered around target_speed_mph (default 50 mph).
    - Lower speeds (< 50 mph): uses tighter spread speed_sigma_low_mph (default 8 mph)
      so 35-40 mph roads score significantly lower.
    - Higher speeds (>= 50 mph): uses spread speed_sigma_high_mph (default 12 mph).

    - 50 mph -> 100.0
    - 45 mph -> 82.2
    - 40 mph -> 45.7
    - 35 mph -> 17.2
    - 25 mph -> 0.7
    - 55 mph -> 91.6
    - 60 mph -> 70.7
    """
    if config is None:
        config = DiscoveryConfig()

    v = float(speed_mph)
    mu = config.target_speed_mph
    sigma_low = getattr(config, "speed_sigma_low_mph", 8.0)
    sigma_high = getattr(config, "speed_sigma_high_mph", 12.0)

    sigma = sigma_low if v < mu else sigma_high

    score = 100.0 * math.exp(-((v - mu) ** 2) / (2.0 * (sigma**2)))
    return round(max(0.0, min(100.0, score)), 1)



def calculate_traffic_penalty(
    intersection_density_per_km: float,
    signal_density_per_km: float = 0.0,
    business_density_per_km: Any = 0.0,
    driveway_density_per_km: float = 0.0,
    config: Optional[DiscoveryConfig] = None,
) -> float:
    """Calculate the traffic, signal, commercial business, and driveway penalty [0, max_penalty].

    Penalizes road segments with high junction densities, traffic lights, commercial strip malls,
    or frequent commercial/service driveways.
    Mountain passes and rural backroads with minimal commercial activity receive ~0 penalty.
    """
    if isinstance(business_density_per_km, DiscoveryConfig):
        config = business_density_per_km
        business_density_per_km = 0.0

    if config is None:
        config = DiscoveryConfig()

    # Mountain roads typically have occasional turnoffs/trails (e.g., < 0.5 per km)
    excess_intersections = max(0.0, float(intersection_density_per_km) - 0.5)

    int_penalty = excess_intersections * config.intersection_penalty_weight
    sig_penalty = float(signal_density_per_km) * config.signals_penalty_weight
    biz_penalty = float(business_density_per_km) * config.business_penalty_weight
    drv_penalty = float(driveway_density_per_km) * config.driveway_penalty_weight

    total_penalty = int_penalty + sig_penalty + biz_penalty + drv_penalty
    return round(min(config.max_traffic_penalty, max(0.0, total_penalty)), 1)


def calculate_length_multiplier(
    length_km: float, config: Optional[DiscoveryConfig] = None
) -> float:
    """Compute the route length multiplier.

    Sports car drivers prefer substantial driving runs (e.g. 5+ miles / ~8 km).
    Short micro-segments (< 8 km) are scaled down smoothly to prevent isolated
    mini-corners from ranking above great continuous routes.
    """
    if config is None:
        config = DiscoveryConfig()

    if length_km >= config.target_route_length_km:
        return 1.0

    ratio = max(0.0, length_km / config.target_route_length_km)
    multiplier = config.min_length_multiplier + (1.0 - config.min_length_multiplier) * ratio
    return max(config.min_length_multiplier, min(1.0, multiplier))


def calculate_composite_score(
    curvature_score: float,
    sinuosity_score: float,
    speed_score: float,
    traffic_penalty: float,
    length_km: float,
    cop_risk_pct: float = 0.0,
    smoothness_penalty: float = 0.0,
    config: Optional[DiscoveryConfig] = None,
) -> float:
    """Calculate the final composite 'Fun Index' driving score [0, 100].

    Formula:
        Weighted_Base = (w_c * Curvature + w_s * Sinuosity + w_v * Speed)
        Cop_Adj = -(cop_risk - threshold) * factor if cop_risk > threshold else +(threshold - cop_risk) * bonus
        Net_Score = max(0, Weighted_Base - Traffic_Penalty - Smoothness_Penalty + Cop_Adj)

        Straight Road Dampening:
        If Curvature < min_curvature_threshold (default 25.0), scale down non-linearly:
            straight_factor = min(1.0, max(0.1, (Curvature / threshold) ** 1.5))
            Net_Score *= straight_factor

        Composite_Score = max(0, min(100, Net_Score * Length_Multiplier))
    """
    if config is None:
        config = DiscoveryConfig()

    weighted_base = (
        (config.weight_curvature * curvature_score)
        + (config.weight_sinuosity * sinuosity_score)
        + (config.weight_speed * speed_score)
    )

    # Police Patrol / Cop Enforcement Adjustment
    cop_thresh = getattr(config, "cop_risk_threshold_pct", 35.0)
    cop_pen_factor = getattr(config, "cop_risk_penalty_factor", 0.30)
    cop_bonus_factor = getattr(config, "cop_risk_low_bonus_factor", 0.20)

    if cop_risk_pct > cop_thresh:
        cop_adj = -(cop_risk_pct - cop_thresh) * cop_pen_factor
    else:
        cop_adj = (cop_thresh - cop_risk_pct) * cop_bonus_factor

    net_score = max(0.0, weighted_base - traffic_penalty - float(smoothness_penalty) + cop_adj)

    # High-Speed Sweeping Twistiness Synergy Bonus (45-55 mph + active curvature)
    # Rewards thrilling high-momentum corridors (e.g. Hawk's Nest NY 97, NY 17A, Lime Kiln Rd)
    if speed_score >= 80.0 and curvature_score >= 18.0:
        net_score += 8.0

    # Pristine Remote Flow Bonus (zero signals, zero commercial strip malls, long continuous run)
    if traffic_penalty == 0.0 and length_km >= 5.0:
        net_score += 5.0

    # Straight road dampener: heavily scale down straight arterials (e.g. NY 17M)
    min_curv = getattr(config, "min_curvature_threshold", 20.0)
    if min_curv > 0 and curvature_score < min_curv:
        straight_factor = min(1.0, max(0.1, (max(0.0, curvature_score) / min_curv) ** 1.5))
        net_score *= straight_factor

    length_mult = calculate_length_multiplier(length_km, config=config)

    composite = net_score * length_mult
    return round(max(0.0, min(100.0, composite)), 1)


def score_route_metrics(
    sinuosity: float,
    twistiness_deg_per_km: float,
    speed_mph: float,
    length_km: float,
    intersection_density: float = 0.0,
    signal_density: float = 0.0,
    business_density: float = 0.0,
    driveway_density: float = 0.0,
    cop_risk_pct: float = 0.0,
    smoothness_penalty: float = 0.0,
    config: Optional[DiscoveryConfig] = None,
) -> Dict[str, float]:
    """Convenience helper to compute all score components at once."""
    if config is None:
        config = DiscoveryConfig()

    s_score = calculate_sinuosity_score(sinuosity, config=config)
    c_score = calculate_curvature_score(twistiness_deg_per_km, config=config)
    v_score = calculate_speed_score(speed_mph, config=config)
    penalty = calculate_traffic_penalty(
        intersection_density_per_km=intersection_density,
        signal_density_per_km=signal_density,
        business_density_per_km=business_density,
        driveway_density_per_km=driveway_density,
        config=config,
    )
    composite = calculate_composite_score(
        curvature_score=c_score,
        sinuosity_score=s_score,
        speed_score=v_score,
        traffic_penalty=penalty,
        length_km=length_km,
        cop_risk_pct=cop_risk_pct,
        smoothness_penalty=smoothness_penalty,
        config=config,
    )

    return {
        "sinuosity_score": s_score,
        "curvature_score": c_score,
        "speed_score": v_score,
        "traffic_penalty": penalty,
        "smoothness_penalty": float(smoothness_penalty),
        "composite_driving_score": composite,
    }


def calculate_cop_enforcement_risk(
    highway_type: str = "secondary",
    speed_mph: float = 45.0,
    sinuosity: float = 1.2,
    intersection_density: float = 0.5,
    business_density: float = 0.0,
) -> Dict[str, Any]:
    """Calculate the Patrol & Radar Enforcement Risk Level [0-100%] based on OSM highway attributes.

    Higher risk corridors are straight multi-lane arterials, commercial corridors, or state routes heavily monitored by troopers.
    Lower risk corridors are twisty remote canyon passes where stationary radar traps are impractical.
    """
    hw = str(highway_type).lower().strip()

    # 1. Base Highway Classification Risk
    if hw in ["motorway", "motorway_link", "trunk", "trunk_link"]:
        base_risk = 80.0
    elif hw in ["primary", "primary_link"]:
        base_risk = 65.0
    elif hw in ["secondary", "secondary_link"]:
        base_risk = 45.0
    elif hw in ["tertiary", "tertiary_link"]:
        base_risk = 30.0
    else:
        base_risk = 15.0

    # 2. Speed Limit Adjustment
    speed_adj = 0.0
    if speed_mph >= 60.0:
        speed_adj = 12.0
    elif speed_mph >= 45.0:
        speed_adj = 5.0
    elif speed_mph < 35.0:
        speed_adj = -8.0

    # 3. Sinuosity / Curve Shielding (harder for stationary radar)
    sin_adj = 0.0
    if sinuosity >= 1.4:
        sin_adj = -20.0
    elif sinuosity >= 1.2:
        sin_adj = -10.0
    elif sinuosity < 1.1:
        sin_adj = 10.0

    # 4. Junction & Commercial Density Adjustment
    int_adj = min(10.0, float(intersection_density) * 3.0)
    biz_adj = min(15.0, float(business_density) * 5.0)

    # 5. Remote Scenic Highway Discount (Primary/Secondary state routes with 0 commercial activity)
    rural_scenic_adj = 0.0
    if hw in ["primary", "primary_link", "secondary", "secondary_link"] and float(business_density) == 0.0 and float(intersection_density) < 0.5:
        rural_scenic_adj = -15.0

    final_risk = max(5.0, min(95.0, base_risk + speed_adj + sin_adj + int_adj + biz_adj + rural_scenic_adj))
    final_risk = round(final_risk, 1)

    if final_risk >= 58.0:
        level = "High"
        badge_color = "#f38ba8"
        desc = "High Police Patrol / Trooper Radar Zone"
    elif final_risk >= 32.0:
        level = "Moderate"
        badge_color = "#fab387"
        desc = "Moderate Local Patrol Likelihood"
    else:
        level = "Low"
        badge_color = "#a6e3a1"
        desc = "Low Patrol / Remote Backroad Shield"

    return {
        "risk_pct": final_risk,
        "level": level,
        "badge_color": badge_color,
        "description": desc,
    }

