"""
app.py

Bird-Strike Mitigation Autonomy Service (v0.2)

This service is NOT directly commanding actuators.
It provides a deterministic, auditable decision layer that other systems
(e.g., FMS, UAV flight controller, tower tools) can query.

Core pieces:
- Sensor ingestion (bird detections, aircraft state, weather/context)
- Risk engine (predict near-term collision risk)
- Planner (recommend safe, bounded actions)
- Telemetry + human-gated overrides
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, validator

# ---------------------------------------------------------------------------
# Service metadata / constants
# ---------------------------------------------------------------------------

ENGINE_VERSION = "0.2.0"

# Basic logging so decisions / errors can be traced in real deployments.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("BirdStrikeMitigationService")

class RiskConfig(BaseModel):
"""
Tunable, versioned knobs for the risk engine.

In a real deployment this would likely come from config files / env and
be managed with change control.
"""
version: str = "2025-11-30-a"

# Geometric / temporal heuristics
distance_threshold_m: float = 500.0 # max 3D distance considered "conflict-relevant"
min_ttc_s: float = 5.0 # floor on time-to-conflict estimates
max_ttc_s: float = 120.0 # ceiling on time-to-conflict estimates

# TTC bands for risk mapping (seconds)
ttc_low_threshold_s: float = 60.0
ttc_medium_threshold_s: float = 30.0
ttc_high_threshold_s: float = 10.0

# Nominal collision probabilities for each band
prob_presence_only: float = 0.05 # birds present, no near-term conflict
prob_low: float = 0.10
prob_medium: float = 0.30
prob_high: float = 0.60
prob_critical: float = 0.80

RISK_CONFIG = RiskConfig()

app = FastAPI(
title="BirdStrikeMitigationService",
description="Deterministic autonomy layer for bird-strike risk assessment.",
version=ENGINE_VERSION,
)

# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

class Coord3D(BaseModel):
"""Simple 3D coordinate in local ENU or NED frame (meters)."""
x: float
y: float
z: float # altitude (m)

class Velocity3D(BaseModel):
"""3D velocity in same frame as Coord3D (m/s)."""
vx: float
vy: float
vz: float

class BirdDetection(BaseModel):
"""
Single bird / flock detection from radar or vision.

For live streaming data, timestamp is expected to be "near now".
In a real system you’d also include covariance / confidence, source_id,
tracking_id, etc.
"""
id: str
position: Coord3D
velocity: Velocity3D

timestamp: datetime
confidence: float = Field(ge=0.0, le=1.0)

@validator("timestamp")
def timestamp_not_in_future(cls, v: datetime) -> datetime:
# Light sanity check to catch clock drift / garbage data for LIVE data paths.
# For offline replay / sim, this check may need to be relaxed or disabled.
if v > datetime.utcnow() + timedelta(seconds=5):
raise ValueError("detection timestamp is in the future")
return v

class AircraftState(BaseModel):
"""Minimal aircraft state for local, short-horizon reasoning."""
callsign: str
position: Coord3D
velocity: Velocity3D
heading_deg: float
altitude_agl_m: float
timestamp: datetime

@validator("altitude_agl_m")
def positive_altitude(cls, v: float) -> float:
if v < -10:
raise ValueError("altitude AGL looks invalid")
return v

class RiskLevel(str, Enum):
NONE = "NONE"
LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"
CRITICAL = "CRITICAL"

class ActionType(str, Enum):
"""What we *recommend*, not what we directly execute."""
MAINTAIN = "MAINTAIN"
SLOW_DOWN = "SLOW_DOWN"
CLIMB = "CLIMB"
DESCEND = "DESCEND"
TURN_LEFT = "TURN_LEFT"
TURN_RIGHT = "TURN_RIGHT"
HOLD = "HOLD"
ABORT_MISSION = "ABORT_MISSION"

class RiskAssessment(BaseModel):
risk_level: RiskLevel
collision_probability: float = Field(ge=0.0, le=1.0)
time_to_conflict_s: Optional[float] = None

most_threatening_bird_id: Optional[str] = None
rationale: str

# Auditability / traceability
decision_id: str
input_hash: str
engine_version: str
config_version: str

class ActionRecommendation(BaseModel):
action: ActionType
urgency: RiskLevel
rationale: str
# This lets a human or higher-level controller decide whether to apply.
requires_human_approval: bool = True

class RiskQuery(BaseModel):
"""Payload for /evaluate and /plan endpoints."""
aircraft: AircraftState
birds: List[BirdDetection]

class PlanResponse(BaseModel):
"""

Composite response so we always surface "why" (RiskAssessment)
alongside "what" (ActionRecommendation).
"""
assessment: RiskAssessment
recommendation: ActionRecommendation

# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def compute_input_hash(query: RiskQuery) -> str:
"""
Compute a deterministic hash of the input payload so we can replay / audit.
"""
# Use a stable, sorted JSON representation. Datetimes are stringified.
encoded = json.dumps(
query.dict(),
sort_keys=True,
default=str,
).encode("utf-8")
return hashlib.sha256(encoded).hexdigest()

def estimate_time_to_conflict(

aircraft: AircraftState,
bird: BirdDetection,
config: RiskConfig,
) -> Optional[float]:
"""
Rough placeholder:
- Assume linear relative motion.
- Approximate time-to-closest approach based on current distance and
relative speed.
- Only consider conflicts within a 3D distance threshold.

This is intentionally simple and deterministic so you can wire the pipeline.
Replace with proper vector math + units handling later.
"""
relative_vx = aircraft.velocity.vx - bird.velocity.vx
relative_vy = aircraft.velocity.vy - bird.velocity.vy
relative_vz = aircraft.velocity.vz - bird.velocity.vz

relative_speed = (relative_vx**2 + relative_vy**2 + relative_vz**2) ** 0.5
if relative_speed < 1e-3:
return None

dx = aircraft.position.x - bird.position.x
dy = aircraft.position.y - bird.position.y
dz = aircraft.position.z - bird.position.z
distance = (dx**2 + dy**2 + dz**2) ** 0.5

# Only consider birds within the configured 3D radius.
if distance > config.distance_threshold_m:
return None

# Very crude TTC estimate: current distance / relative speed.
raw_ttc = distance / relative_speed

# Clamp into a band so we don't get absurdly small or large values driving logic.
ttc = max(config.min_ttc_s, min(config.max_ttc_s, raw_ttc))
return ttc

def evaluate_risk(query: RiskQuery, config: RiskConfig = RISK_CONFIG) ->
RiskAssessment:
"""
Deterministic risk scoring from one aircraft and many birds.

This is the main box you’ll iterate on:
- Replace heuristics with trajectory prediction
- Add uncertainty, clutter rejection, flock size, etc.
"""
input_hash = compute_input_hash(query)
decision_id = str(uuid.uuid4())

if not query.birds:

rationale = "No birds in current surveillance volume."
logger.info(
"risk_decision",
extra={
"decision_id": decision_id,
"risk_level": RiskLevel.NONE.value,
"collision_probability": 0.0,
"reason": rationale,
},
)
return RiskAssessment(
risk_level=RiskLevel.NONE,
collision_probability=0.0,
time_to_conflict_s=None,
most_threatening_bird_id=None,
rationale=rationale,
decision_id=decision_id,
input_hash=input_hash,
engine_version=ENGINE_VERSION,
config_version=config.version,
)

best_ttc: Optional[float] = None
best_bird: Optional[BirdDetection] = None

for b in query.birds:

ttc = estimate_time_to_conflict(query.aircraft, b, config=config)
if ttc is None:
continue
if best_ttc is None or ttc < best_ttc:
best_ttc = ttc
best_bird = b

if best_ttc is None or best_bird is None:
rationale = "Birds present but no projected near-term conflicts within configured
thresholds."
logger.info(
"risk_decision",
extra={
"decision_id": decision_id,
"risk_level": RiskLevel.LOW.value,
"collision_probability": config.prob_presence_only,
"reason": rationale,
},
)
return RiskAssessment(
risk_level=RiskLevel.LOW,
collision_probability=config.prob_presence_only,
time_to_conflict_s=None,
most_threatening_bird_id=None,
rationale=rationale,
decision_id=decision_id,

input_hash=input_hash,
engine_version=ENGINE_VERSION,
config_version=config.version,
)

# Risk mapping from TTC → discrete risk band.
if best_ttc >= config.ttc_low_threshold_s:
level = RiskLevel.LOW
prob = config.prob_low
elif best_ttc >= config.ttc_medium_threshold_s:
level = RiskLevel.MEDIUM
prob = config.prob_medium
elif best_ttc >= config.ttc_high_threshold_s:
level = RiskLevel.HIGH
prob = config.prob_high
else:
level = RiskLevel.CRITICAL
prob = config.prob_critical

rationale = (
f"Closest projected conflict with bird {best_bird.id} in ~{best_ttc:.1f}s "
f"using linear relative motion and heuristic TTC risk bands."
)

logger.info(
"risk_decision",

extra={
"decision_id": decision_id,
"risk_level": level.value,
"collision_probability": prob,
"time_to_conflict_s": best_ttc,
"most_threatening_bird_id": best_bird.id,
},
)

return RiskAssessment(
risk_level=level,
collision_probability=prob,
time_to_conflict_s=best_ttc,
most_threatening_bird_id=best_bird.id,
rationale=rationale,
decision_id=decision_id,
input_hash=input_hash,
engine_version=ENGINE_VERSION,
config_version=config.version,
)

def plan_action(assessment: RiskAssessment) -> ActionRecommendation:
"""
Map risk → bounded, human-gated action recommendations.

This is where you encode ROE, airspace rules, mission profile, etc.
"""
if assessment.risk_level in {RiskLevel.NONE, RiskLevel.LOW}:
return ActionRecommendation(
action=ActionType.MAINTAIN,
urgency=assessment.risk_level,
rationale="Risk is NONE/LOW. Maintain current trajectory with monitoring.",
requires_human_approval=False,
)

if assessment.risk_level == RiskLevel.MEDIUM:
return ActionRecommendation(
action=ActionType.SLOW_DOWN,
urgency=RiskLevel.MEDIUM,
rationale="Medium near-term risk. Recommend speed reduction and altitude
review.",
requires_human_approval=True,
)

if assessment.risk_level == RiskLevel.HIGH:
return ActionRecommendation(
action=ActionType.TURN_RIGHT,
urgency=RiskLevel.HIGH,
rationale="High risk of conflict. Recommend lateral deviation away from threat
vector.",
requires_human_approval=True,
)

# CRITICAL
return ActionRecommendation(
action=ActionType.ABORT_MISSION,
urgency=RiskLevel.CRITICAL,
rationale="Critical near-term conflict predicted. Recommend immediate abort /
escape.",
requires_human_approval=True,
)

# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def healthcheck():
return {
"status": "ok",
"time_utc": datetime.utcnow().isoformat(),
"engine_version": ENGINE_VERSION,
"config_version": RISK_CONFIG.version,
}

@app.post("/evaluate", response_model=RiskAssessment)
def evaluate(query: RiskQuery):
"""
Core endpoint: given aircraft + bird tracks, return a risk assessment.

This is safe to call from:
- onboard mission computer
- ground tools
- simulation harnesses
"""
try:
return evaluate_risk(query)
except Exception:
# In prod, never swallow; log with full context + trace id.
logger.exception("Error during risk evaluation")
raise HTTPException(status_code=500, detail="Internal evaluation error")

@app.post("/plan", response_model=PlanResponse)
def plan(query: RiskQuery):
"""
Convenience endpoint: evaluate risk AND return a recommended action.

You keep human-in-the-loop by:
- Surfacing the RiskAssessment alongside the ActionRecommendation
- Requiring explicit approval from a certified operator system

"""
try:
assessment = evaluate_risk(query)
recommendation = plan_action(assessment)
return PlanResponse(assessment=assessment, recommendation=recommendation)
except Exception:
logger.exception("Error during planning")
raise HTTPException(status_code=500, detail="Internal planning error")

# ---------------------------------------------------------------------------
# Local dev entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
import uvicorn

uvicorn.run(
"app:app",
host="0.0.0.0",
port=8000,
reload=True,
)
