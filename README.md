# 🏁 Road Discovery Engine: Driving Enthusiast Route Discovery & Ranking

An algorithmic geospatial engine designed for sports cars and driving enthusiasts to discover, rank, and export the best driving roads. Ingests OpenStreetMap road networks, computes geodetic sinuosity and angular twistiness, filters speed windows and traffic density, stitches contiguous road corridors, and outputs analysis in GeoJSON format ready for web maps (Mapbox, Leaflet, React).

---

## 📐 1. Mathematical Scoring Algorithm

The engine evaluates road geometries and attributes using four mathematical components combined into a composite **"Fun Index"** score ($F \in [0, 100]$):

### A. Sinuosity Index ($S$)
Measures the road's macro deviation from a straight line:
$$S = \frac{L_{\text{arc}}}{L_{\text{chord}}}$$
- $L_{\text{arc}}$: Cumulative geodetic arc length along the road (in km).
- $L_{\text{chord}}$: Straight-line geodesic distance between the starting and ending coordinate.
- $S = 1.0$ for a straight road.
- $S \ge 1.8$ represents extreme switchbacks and winding corridors.

**Sinuosity Score ($S_{\text{score}} \in [0, 100]$):**
$$S_{\text{score}} = \text{clip}\left(\frac{S - 1.0}{S_{\max} - 1.0} \times 100, \; 0, \; 100\right) \quad (\text{with } S_{\max} = 1.8)$$

---

### B. Angular Twistiness & Directional Change Rate ($C$)
Sinuosity alone can miss rhythmic "S-curves" that progress linearly. Angular twistiness measures the **cumulative absolute heading deflection** per kilometer:
1. For resampled vertices $P_i = (\text{lon}_i, \text{lat}_i)$, compute initial forward bearings $\theta_i \in [0, 360)^\circ$.
2. Heading change at vertex $i$:
   $$\Delta \theta_i = \min\left(|\theta_{i+1} - \theta_i|, \; 360^\circ - |\theta_{i+1} - \theta_i|\right)$$
3. Total deflection: $\Theta_{\text{total}} = \sum \Delta \theta_i$ (degrees).
4. Angular Twistiness Density:
   $$T = \frac{\Theta_{\text{total}}}{L_{\text{arc\_km}}} \quad (\text{degrees per kilometer})$$

**Curvature Score ($C_{\text{score}} \in [0, 100]$):**
Uses an asymptotic exponential saturation curve:
$$C_{\text{score}} = 100 \times \left(1 - \exp\left(-\frac{T}{T_{\text{scale}}}\right)\right) \quad (\text{with } T_{\text{scale}} = 600^\circ/\text{km})$$
- Straight road ($0^\circ/\text{km}$): $0.0$
- Sweeping country road ($300^\circ/\text{km}$): $39.3$
- Spirited canyon pass ($600^\circ/\text{km}$): $63.2$
- Technical mountain pass ($1200^\circ/\text{km}$): $86.5$
- Tail of the Dragon tier ($1800+^\circ/\text{km}$): $95.0+$

---

### C. Speed Limit Suitability Window ($V_{\text{score}}$)
Spirited passenger car driving thrives when road design permits 2nd-to-4th gear rhythm (~45–55 mph). We model this using a Gaussian bell curve:
$$V_{\text{score}}(v) = 100 \times \exp\left( -\frac{(v - \mu_v)^2}{2 \sigma_v^2} \right)$$
- Target speed: $\mu_v = 50.0\text{ mph}$
- Tolerance spread: $\sigma_v = 12.0\text{ mph}$
- $50\text{ mph} \to 100.0$
- $45 / 55\text{ mph} \to 91.6$
- $40 / 60\text{ mph} \to 70.7$
- $25\text{ mph}$ (tedious residential) $\to 11.4$
- $75\text{ mph}$ (straight interstate) $\to 11.4$

---

### D. Traffic & Intersections Penalty ($P_{\text{traffic}}$)
Penalizes urban congestion and frequent interruptions:
$$P_{\text{traffic}} = \min\left(50, \; 12 \times \max(0, D_{\text{int}} - 0.5) + 20 \times D_{\text{signals}}\right)$$
- $D_{\text{int}}$: Graph junctions / intersections per km.
- $D_{\text{signals}}$: Traffic lights and stop signs per km.

---

### E. Length Factor ($L_{\text{multiplier}}$) & Composite "Fun Index" ($F$)
Enthusiasts prioritize sustained drives over isolated 500m corners:
$$L_{\text{multiplier}} = \begin{cases}
0.50 + 0.50 \times \left(\frac{L_{\text{km}}}{L_{\text{target}}}\right) & \text{if } L_{\text{km}} < L_{\text{target}} \quad (L_{\text{target}} = 8.0\text{ km}) \\
1.0 & \text{if } L_{\text{km}} \ge L_{\text{target}}
\end{cases}$$

**Composite Fun Index:**
$$F_{\text{base}} = \left(0.50 \cdot C_{\text{score}} + 0.25 \cdot S_{\text{score}} + 0.25 \cdot V_{\text{score}}\right) - P_{\text{traffic}}$$
$$F = \text{round}\left( \max\left(0, \min(100, F_{\text{base}} \times L_{\text{multiplier}})\right), \; 1 \right)$$

---

## 🛠 2. Installation & Quickstart

```bash
# 1. Activate Python virtual environment
source .venv/bin/activate

# 2. Install dependencies & package
pip install -e .
```

### Run Unit Tests
```bash
python -m unittest tests/test_scoring.py
```

---

## 🚀 3. CLI Usage

Discover and rank roads by bounding box:
```bash
# Discover roads in Tail of the Dragon / Deals Gap area
python -m road_discovery.cli --bbox -83.98 35.44 -83.88 35.54 --output dragon.geojson

# Discover roads by named county or region
python -m road_discovery.cli --place "Graham County, North Carolina" --target-speed 45.0
```

CLI arguments:
- `--bbox WEST SOUTH EAST NORTH`: Bounding box in EPSG:4326.
- `--place "NAME"`: Named geographic query (e.g. county, state, park).
- `--target-speed`: Target optimal speed limit in mph (default: 50.0).
- `--min-length`: Minimum route length in km (default: 3.0 km).
- `--output`: Output GeoJSON path (default: `discovered_routes.geojson`).
- `--top`: Number of top routes to display in the CLI summary table.

---

## 🧪 4. Proof of Concept (PoC) Results

### PoC 1: Tail of the Dragon (US 129 / Deals Gap, NC/TN)
Command:
```bash
python examples/run_tail_of_the_dragon.py
```
Output:
```
--------------------------------------------------------------------------------
Rank  Road Name                        Length     Speed    Sinuosity  Curvature  Fun Score 
--------------------------------------------------------------------------------
1     US 129 (Tapoco Road)             23.35 km   33.0 mph 100.0/100  64.8/100   75.5/100  
2     NC 28                            4.61 km    50.0 mph 31.1/100   54.8/100   46.4/100  

Diagnostics for US 129:
- Length: 23.35 km (14.5 miles)
- Raw Sinuosity: 2.27 (Sinuosity Score: 100/100)
- Twistiness Density: 625.8 deg/km
- Total Turn Deflection: 14,616 degrees
- Curves Detected: 82 curve clusters
- Traffic Penalty: 0.0
- Fun Index: 75.5 / 100
```

### PoC 2: Angeles Crest Highway (CA-2, San Gabriel Mountains, CA)
Command:
```bash
python examples/run_angeles_crest.py
```
Output:
```
-------------------------------------------------------------------------------------
Rank  Road Name                          Length     Speed    Sinuosity  Curvature  Score   
-------------------------------------------------------------------------------------
1     CA 2 (Angeles Crest Highway)       12.18 km   45.3 mph 100.0/100  45.2/100   70.8    
2     CR N3;FH 59 (Angeles Forest High   8.68 km    55.0 mph 100.0/100  40.9/100   67.4    
3     Mount Wilson Red Box Road          5.81 km    45.0 mph 57.4/100   69.6/100   62.2    
4     CA 2 (Angeles Crest Highway)       10.98 km   45.3 mph 65.7/100   42.9/100   61.0    
5     Big Tujunga Canyon Road            5.86 km    45.0 mph 40.7/100   37.9/100   45.1    
```

---

## 🗺 5. Visualizing Routes in the Browser

An interactive Leaflet visualizer is included in [examples/view_routes_map.html](file:///Users/sanjayuttam/Documents/gemini-cli/FindRoads/examples/view_routes_map.html).

Start a simple local HTTP server:
```bash
python -m http.server 8000
```
Open `http://localhost:8000/examples/view_routes_map.html` in your browser.
- Routes are rendered and color-coded by **Fun Score** (Red = Legendary, Orange = Spirited, Green = Technical).
- Clicking any road opens an enthusiast diagnostics popup with length, sinuosity, curvature, turns, and speed.

---

## 🌐 6. Architecture for Future Web UI (React + Mapbox / Leaflet)

```
┌─────────────────────────────────────────────────────────────┐
│                 React / Next.js Web Client                  │
│       (Mapbox GL JS / Deck.gl / Leaflet Interactive Map)     │
└───────────────▲─────────────────────────────┬───────────────┘
                │                             │
    GeoJSON FeatureCollection     GET /api/v1/routes/bbox
    with Fun Index Properties     (viewport bounds, target speed)
                │                             │
┌───────────────┴─────────────────────────────▼───────────────┐
│                    FastAPI Backend Server                   │
│                    (road_discovery/api.py)                  │
└───────────────▲─────────────────────────────┬───────────────┘
                │                             │
       Stitched & Scored Routes        Raw Overpass Graph
                │                             │
┌───────────────┴──────────────┐ ┌────────────▼───────────────┐
│    Route Stitching & Scoring │ │   OSMnx Ingestion & Cache  │
│   (stitching.py, scoring.py) │ │   (ingestion.py, Overpass) │
└──────────────────────────────┘ └────────────────────────────┘
```

### Running the REST API Server
```bash
uvicorn road_discovery.api:app --reload --port 8000
```

### Endpoints
- `GET /health`
- `GET /api/v1/presets`
- `GET /api/v1/routes/bbox?west=-83.98&south=35.44&east=-83.88&north=35.54&target_speed=45`
- `GET /api/v1/routes/preset/tail-of-the-dragon`
- `GET /api/v1/routes/preset/angeles-crest`
