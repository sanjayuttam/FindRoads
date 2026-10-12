"""Unit tests for geometry, scoring formulas, and route analysis."""

import math
import unittest
import numpy as np
from shapely.geometry import LineString

from road_discovery.config import DiscoveryConfig
from road_discovery.geometry import (
    haversine_distance_m,
    calculate_bearing_deg,
    calculate_line_length_km,
    calculate_chord_length_km,
    calculate_sinuosity,
    calculate_angular_twistiness,
)
from road_discovery.scoring import (
    calculate_sinuosity_score,
    calculate_curvature_score,
    calculate_speed_score,
    calculate_traffic_penalty,
    calculate_length_multiplier,
    calculate_composite_score,
    score_route_metrics,
)


class TestGeometryAndScoring(unittest.TestCase):
    def setUp(self):
        self.config = DiscoveryConfig()

    def test_haversine_distance(self):
        # Distance between (0, 0) and (0, 1 deg lat) ~ 111.195 km
        dist = haversine_distance_m(0.0, 0.0, 0.0, 1.0)
        self.assertAlmostEqual(dist, 111195.0, delta=500.0)

    def test_bearing(self):
        # Heading due North: bearing should be 0 deg
        b_north = calculate_bearing_deg(0.0, 0.0, 0.0, 1.0)
        self.assertAlmostEqual(b_north, 0.0, delta=0.1)

        # Heading due East: bearing should be 90 deg
        b_east = calculate_bearing_deg(0.0, 0.0, 1.0, 0.0)
        self.assertAlmostEqual(b_east, 90.0, delta=0.1)

    def test_straight_road_scoring(self):
        # 10 km straight road heading north
        coords = [(0.0, float(lat)) for lat in np.linspace(35.0, 35.09, 100)]
        straight_road = LineString(coords)

        sinuosity = calculate_sinuosity(straight_road)
        self.assertAlmostEqual(sinuosity, 1.0, places=2)

        sin_score = calculate_sinuosity_score(sinuosity, self.config)
        self.assertEqual(sin_score, 0.0)

        twist = calculate_angular_twistiness(straight_road)
        self.assertAlmostEqual(twist["twistiness_deg_per_km"], 0.0, delta=1.0)

        curv_score = calculate_curvature_score(twist["twistiness_deg_per_km"], self.config)
        self.assertEqual(curv_score, 0.0)

    def test_sinuosity_scale(self):
        # S = 1.0 -> 0 score
        self.assertEqual(calculate_sinuosity_score(1.0, self.config), 0.0)
        # S = 1.4 -> 50 score (halfway between 1.0 and 1.8)
        self.assertEqual(calculate_sinuosity_score(1.4, self.config), 50.0)
        # S = 1.8 -> 100 score
        self.assertEqual(calculate_sinuosity_score(1.8, self.config), 100.0)
        # S > 1.8 -> clamped at 100 score
        self.assertEqual(calculate_sinuosity_score(2.5, self.config), 100.0)

    def test_curvature_score_exponential_saturation(self):
        # Curvature score saturates smoothly towards 100
        c0 = calculate_curvature_score(0.0, self.config)
        c300 = calculate_curvature_score(300.0, self.config)
        c600 = calculate_curvature_score(600.0, self.config)
        c1200 = calculate_curvature_score(1200.0, self.config)
        c2400 = calculate_curvature_score(2400.0, self.config)

        self.assertEqual(c0, 0.0)
        self.assertTrue(0.0 < c300 < c600 < c1200 < c2400 <= 100.0)
        self.assertAlmostEqual(c600, 82.0, delta=0.5)

    def test_speed_gaussian_window(self):
        # Target 50 mph should be 100.0
        s50 = calculate_speed_score(50.0, self.config)
        self.assertEqual(s50, 100.0)

        # Asymmetric falloff: 45 mph (~82.2) is lower than 55 mph (~91.6)
        s45 = calculate_speed_score(45.0, self.config)
        s55 = calculate_speed_score(55.0, self.config)
        self.assertTrue(s45 < s55)
        self.assertAlmostEqual(s45, 82.2, delta=0.5)
        self.assertAlmostEqual(s55, 91.6, delta=0.5)

        # 35 mph is heavily penalized (~17.2)
        s35 = calculate_speed_score(35.0, self.config)
        self.assertTrue(s35 < 20.0)

        # Residential (25 mph) and Interstate (75 mph) should be heavily penalized
        s25 = calculate_speed_score(25.0, self.config)
        s75 = calculate_speed_score(75.0, self.config)
        self.assertTrue(s25 < 5.0)
        self.assertTrue(s75 < 15.0)


    def test_traffic_penalty(self):
        # Pure rural pass: 0 intersections -> 0 penalty
        p_rural = calculate_traffic_penalty(0.0, 0.0, self.config)
        self.assertEqual(p_rural, 0.0)

        # Urban arterial: 4 intersections/km, 1 signal/km
        p_urban = calculate_traffic_penalty(4.0, 1.0, self.config)
        self.assertTrue(p_urban >= 40.0)
        self.assertTrue(p_urban <= self.config.max_traffic_penalty)

    def test_commercial_and_driveway_penalty(self):
        # Commercial corridor with 2 businesses/km and 1 driveway/km
        p_commercial = calculate_traffic_penalty(
            intersection_density_per_km=0.0,
            signal_density_per_km=0.0,
            business_density_per_km=2.0,
            driveway_density_per_km=1.0,
            config=self.config,
        )
        # Expected: 2.0 * 15.0 + 1.0 * 10.0 = 40.0
        self.assertEqual(p_commercial, 40.0)

    def test_length_multiplier(self):
        # Full length (>= 8 km) gets 1.0 multiplier
        m_long = calculate_length_multiplier(10.0, self.config)
        self.assertEqual(m_long, 1.0)

        # Short route gets scaled down
        m_short = calculate_length_multiplier(4.0, self.config)
        self.assertAlmostEqual(m_short, 0.75, delta=0.01)

    def test_composite_score(self):
        # High curvature (80), high sinuosity (80), optimal speed (100), 0 penalty, full length, cop risk 35% (neutral)
        comp = calculate_composite_score(
            curvature_score=80.0,
            sinuosity_score=80.0,
            speed_score=100.0,
            traffic_penalty=0.0,
            length_km=10.0,
            cop_risk_pct=35.0,
            config=self.config,
        )
        # Expected: 0.50*80 + 0.25*80 + 0.25*100 = 85.0 + 8 (synergy) + 5 (pristine flow) = 98.0
        self.assertEqual(comp, 98.0)

    def test_straight_road_dampener_penalty(self):
        # Straight route (e.g. NY 17M) with low curvature (10.0), sinuosity 0, speed 100
        comp_straight = calculate_composite_score(
            curvature_score=10.0,
            sinuosity_score=0.0,
            speed_score=100.0,
            traffic_penalty=0.0,
            length_km=10.0,
            cop_risk_pct=35.0,
            config=self.config,
        )
        # Curvy route (Hawk's Nest) with high curvature (80.0), sinuosity 80, speed 100
        comp_curvy = calculate_composite_score(
            curvature_score=80.0,
            sinuosity_score=80.0,
            speed_score=100.0,
            traffic_penalty=0.0,
            length_km=10.0,
            cop_risk_pct=35.0,
            config=self.config,
        )
        # Straight route should be significantly lower (e.g. < 15) than curvy route (85)
        self.assertTrue(comp_straight < 15.0)
        self.assertTrue(comp_curvy > 75.0)

    def test_straight_road_cr694_dampening(self):
        # Straight 45 mph connecting corridor (like CR 694 Main St) with low curvature (18.0) and high speed score (82.2)
        score_cr694 = calculate_composite_score(
            curvature_score=18.0,
            sinuosity_score=10.0,
            speed_score=82.2,
            traffic_penalty=0.0,
            length_km=2.0,
            cop_risk_pct=20.0,
            config=self.config,
        )
        # Should be heavily dampened below 20 (Smooth Highway / Green)
        self.assertTrue(score_cr694 < 20.0, f"Expected < 20.0, got {score_cr694}")

    def test_cop_risk_score_impact(self):
        # High cop enforcement risk (85%) should reduce score compared to low cop risk (15%)
        comp_high_cop = calculate_composite_score(
            curvature_score=60.0,
            sinuosity_score=50.0,
            speed_score=90.0,
            traffic_penalty=0.0,
            length_km=10.0,
            cop_risk_pct=85.0,
            config=self.config,
        )
        comp_low_cop = calculate_composite_score(
            curvature_score=60.0,
            sinuosity_score=50.0,
            speed_score=90.0,
            traffic_penalty=0.0,
            length_km=10.0,
            cop_risk_pct=15.0,
            config=self.config,
        )
        self.assertTrue(comp_high_cop < comp_low_cop)

    def test_smoothness_road_quality_penalty(self):
        # Rough surface (30 pts smoothness penalty) vs Smooth surface (0 pts penalty)
        comp_rough = calculate_composite_score(
            curvature_score=60.0,
            sinuosity_score=50.0,
            speed_score=90.0,
            traffic_penalty=0.0,
            length_km=10.0,
            smoothness_penalty=30.0,
            config=self.config,
        )
        comp_smooth = calculate_composite_score(
            curvature_score=60.0,
            sinuosity_score=50.0,
            speed_score=90.0,
            traffic_penalty=0.0,
            length_km=10.0,
            smoothness_penalty=0.0,
            config=self.config,
        )
        self.assertAlmostEqual(comp_smooth - comp_rough, 30.0, delta=0.5)

    def test_closed_loop_sinuosity(self):
        # Closed circular loop route where start point equals end point
        t = np.linspace(0, 2 * np.pi, 100)
        coords = list(zip(-84.0 + 0.01 * np.cos(t), 35.0 + 0.01 * np.sin(t)))
        loop_line = LineString(coords)

        # Must not raise ZeroDivisionError or return NaN/inf
        sin = calculate_sinuosity(loop_line)
        self.assertTrue(math.isfinite(sin))
        self.assertTrue(sin >= 1.0)


if __name__ == "__main__":
    unittest.main()

