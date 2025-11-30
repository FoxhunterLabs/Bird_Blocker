Bird-Strike Mitigation Autonomy Service
Version: 0.2.0
Role: Deterministic risk-scoring + bounded action-planning layer
Guarantee: No direct actuator control. Ever.
This service provides a transparent, auditable decision layer for evaluating near-term bird-strike risk around an aircraft and recommending bounded, human-gated actions. It is designed to sit between raw sensors and higher-level control systems (FMS, UAV flight controllers, tower decision tools, etc.).
It is intentionally conservative, inspectable, and deterministic.
________________________________________
🧠 What This Service Is
A small autonomy microservice that produces:
1.	Risk Assessments
o	predicts near-term time-to-conflict (TTC) using simple relative-motion math
o	maps TTC into discrete risk levels (NONE → CRITICAL)
o	provides a collision probability aligned with configurable heuristics
o	outputs a full audit trail (decision ID, input hash, engine/config versions)
2.	Bounded Action Recommendations
o	never directly drives actuators
o	outputs structured, explainable guidance (MAINTAIN, SLOW_DOWN, TURN_RIGHT, etc.)
o	all non-low-risk outputs require human approval
This sits cleanly inside a safety-critical autonomy pipeline:
sensor → tracker → this service → HMI / operator → primary flight system.
________________________________________
🚫 What This Service Is Not
•	It does not control surfaces, propulsion, or navigation directly.
•	It does not perform full trajectory prediction or probabilistic filtering.
•	It does not include radar/vision sensor fusion, clutter rejection, or flock modelling.
•	It does not assume access to classified airspace rules or mission ROE.
This is deliberately a simple, auditable, first-layer decision engine.
________________________________________
📦 Features
Deterministic Input Hashing
All queries are hashed via sorted JSON → SHA-256.
This ensures decisions are:
•	replayable
•	peer-reviewable
•	traceable across logs and flight recorders
Configurable Risk Engine
Everything important is tunable (distance threshold, TTC bounds, band thresholds, collision probabilities). The config is versioned for proper change control.
Clear Risk Bands
TTC (s)	Risk Level	Nominal Probability
> 60	LOW	0.10
30–60	MEDIUM	0.30
10–30	HIGH	0.60
< 10	CRITICAL	0.80
Presence-only (birds but no conflicts) defaults to 0.05.
Safe Action Mapping
Simple, human-gated output layer:
•	NONE/LOW → MAINTAIN
•	MEDIUM → SLOW_DOWN
•	HIGH → TURN_RIGHT
•	CRITICAL → ABORT_MISSION
All non-MAINTAIN recommendations require operator approval.
________________________________________
📚 API Overview
The service is a FastAPI app exposing two primary endpoints:
GET /health
Basic liveliness + version metadata.
POST /evaluate
Input:
{
  "aircraft": { ... },
  "birds": [ ... ]
}
Output: RiskAssessment
Includes:
•	risk level
•	collision probability
•	time-to-conflict
•	most threatening bird ID
•	rationale
•	deterministic audit metadata
POST /plan
Returns:
{
  "assessment": { ... },
  "recommendation": { ... }
}
This is what downstream FMS / HMI layers will normally consume.
________________________________________
🧩 Core Data Models
AircraftState
Minimal state vector required for short-horizon geometry:
•	3D position
•	3D velocity
•	heading
•	AGL altitude
•	timestamp
BirdDetection
Per-track bird/ flock measurement:
•	3D position
•	3D velocity
•	timestamp sanity-checked for “not in the future”
•	detection confidence
RiskAssessment
Final risk determination with full audit trail.
ActionRecommendation
Bounded, human-gated real-world action.
________________________________________
🔬 Risk Engine Logic (v0.2)
At a high level:
1.	Compute input hash
2.	Loop over birds
3.	Estimate time-to-conflict using linear relative motion
4.	Filter by distance threshold
5.	Take the minimum TTC
6.	Map TTC → discrete risk band
7.	Produce an auditable RiskAssessment
The TTC estimator is intentionally simple—just enough to wire the system while keeping behavior predictable.
________________________________________
🛠 Running Locally
Prereqs
•	Python 3.10+
•	FastAPI
•	Uvicorn
•	Pydantic v1
Run
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
Test
GET  http://localhost:8000/health
POST http://localhost:8000/evaluate
POST http://localhost:8000/plan
________________________________________
🧪 Example Query
{
  "aircraft": {
    "callsign": "TEST01",
    "position": {"x": 0, "y": 0, "z": 100},
    "velocity": {"vx": 60, "vy": 0, "vz": 0},
    "heading_deg": 90,
    "altitude_agl_m": 100,
    "timestamp": "2025-11-30T12:00:00Z"
  },
  "birds": [
    {
      "id": "B1",
      "position": {"x": 200, "y": 0, "z": 90},
      "velocity": {"vx": -5, "vy": 0, "vz": 0},
      "timestamp": "2025-11-30T12:00:00Z",
      "confidence": 0.9
    }
  ]
}
________________________________________
🧭 Intended Deployment Pattern
The service is designed for:
•	small onboard avionics modules
•	mission computers (UAV, rotorcraft, GA aircraft)
•	ground-based tower tools
•	sim/replay frameworks
•	audit pipelines + digital flight recorders
Common integration pattern:
Sensors → Tracker → This Service → Human-Gated Command Layer → Vehicle
________________________________________
🔒 Safety Philosophy
1.	Deterministic first
Auditors must be able to reproduce every decision byte-for-byte.
2.	Human-gated actions
Nothing actionable happens without explicit approval.
3.	Graceful degradation
Missing or garbage bird data defaults to LOW-risk presence-only, not unsafe confidence.
4.	Explainability
Every output includes a rationale string that a human reviewer can understand.
________________________________________
🗺 Future Work (0.3+)
•	Proper relative-motion geometry (closest-approach vector math)
•	Covariance-aware uncertainty modelling
•	Flock-size & species modelling
•	Nonlinear prediction with wind & turbulence fields
•	Weighted multi-sensor fusion
•	Integration with ADS-B & radar-altimeter feeds
•	Safety case documentation (ARP-4754A / DO-178C alignment)
________________________________________
📄 License
MIT
________________________________________
