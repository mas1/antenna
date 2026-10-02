"""Paths, the thesis, and the knobs. One file, read by everything."""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = ROOT / "pipeline"
DATA_DIR = PIPELINE_DIR / "data"
FIXTURES_DIR = PIPELINE_DIR / "fixtures"
DB_PATH = DATA_DIR / "antenna.db"
CACHE_PATH = DATA_DIR / "http_cache.db"
DOWNLOADS_DIR = DATA_DIR / "downloads"
EXPORT_DIR = ROOT / "web" / "src" / "data"
BRIEFS_DIR = PIPELINE_DIR / "briefs"
REVIEW_PATH = PIPELINE_DIR / "review.json"

CONTACT_EMAIL = os.environ.get("ANTENNA_CONTACT", "mason@alterity.systems")

# How far back a signal still counts.
LOOKBACK_DAYS = 120

# How fast each family fades, in days. Filings and licences stay meaningful
# for months; a spike of attention is stale in two weeks.
HALF_LIFE_DAYS = {
    "capital": 45.0,
    "regulatory": 60.0,
    "research": 45.0,
    "hiring": 30.0,
    "traffic": 30.0,
    "github": 21.0,
    "launch": 21.0,
    "social": 14.0,
}

# The thesis, from Anti Fund's September 2026 manifesto: "The next wave
# scarcity chokepoints will be automating the physical world with robotics,
# defense tech, energy and manufacturing." Each sector lists the terms that
# count as evidence a piece of text is about it. Terms are matched on word
# boundaries, case-insensitively. Weights: strong terms 1.0, context terms 0.5.
THESIS: dict[str, dict[str, list[str]]] = {
    "robotics": {
        "strong": [
            "robot", "robotic", "humanoid", "robotic manipulation", "manipulator",
            "teleoperation", "teleop", "quadruped", "legged", "gripper", "end effector",
            "embodied ai", "embodied intelligence", "robot learning", "vision-language-action",
            "vla", "dexterous", "exoskeleton", "cobot", "mobile manipulator", "ros2", "ros 2",
            "physical ai", "physical intelligence", "sim-to-real", "sim2real", "lerobot",
            "urdf", "rover",
        ],
        "context": [
            "actuator", "servo", "motor controller", "slam", "kinematics", "locomotion",
            "motion planning", "isaac sim", "mujoco", "warehouse automation", "manipulation",
            "imitation learning",
        ],
    },
    "autonomy": {
        "strong": [
            "drone", "uav", "uas", "suas", "unmanned", "uncrewed", "autopilot", "px4",
            "ardupilot", "bvlos", "evtol", "autonomous vehicle", "autonomous vessel",
            "autonomous underwater", "usv", "uuv", "auv", "ugv", "asv", "quadcopter",
            "quadrotor", "multirotor", "fixed-wing", "loitering", "counter-uas", "c-uas",
            "cuas", "counter unmanned", "self-driving", "autonomous aircraft",
            "autonomous truck", "flight controller", "unmanned aerial", "vtol", "vertical take-off",
        ],
        "context": [
            "mavlink", "gnss-denied", "gps-denied", "visual odometry", "path planning",
            "lidar", "perception stack", "autonomy stack", "swarm", "swarming",
            "autonomy", "autonomous", "pnt",
        ],
    },
    "defense": {
        "strong": [
            "defense", "defence", "military", "warfighter", "munition", "missile",
            "hypersonic", "electronic warfare", "battlefield",
            "national security", "counter-drone", "directed energy", "weapon", "warhead",
            "interceptor drone", "air defense", "missile defense", "radar", "phased array",
        ],
        "context": [
            "dual-use", "itar", "cmmc", "signals intelligence", "sigint", "maritime domain",
            "contested", "attritable", "kill chain", "tactical", "interceptor",
            "command and control", "isr", "dod", "diu", "department of defense",
            "department of war", "air force", "afwerx", "navy", "army", "darpa",
            "space force", "spacewerx", "socom", "marine corps", "cdao", "washington headquarters services",
        ],
    },
    "energy": {
        "strong": [
            "nuclear", "fission", "fusion", "microreactor", "smr", "tokamak", "stellarator",
            "haleu", "enrichment", "geothermal", "power grid", "electric grid", "grid-scale",
            "microgrid", "battery", "energy storage", "electrolyzer", "hydrogen",
            "solar", "photovoltaic", "power electronics", "inverter", "turbine",
            "data center power", "thermal storage", "heat pump", "power plant",
            "transmission line", "power transformer", "htgr", "triso", "molten salt",
            "heat pipe reactor", "power infrastructure",
        ],
        "context": [
            "megawatt", "gigawatt", "kwh", "mwh", "interconnection", "electrification",
            "solid-state", "anode", "cathode", "electrolyte", "ferc", "nrc", "grid",
            "reactor", "plasma", "energy", "transformer monitoring", "substation",
        ],
    },
    "manufacturing": {
        "strong": [
            "manufacturing", "factory", "microfactory", "cnc", "machining",
            "machine shop", "additive manufacturing", "3d printing", "casting", "forging",
            "injection molding", "industrial automation", "reshoring", "onshoring", "welding",
            "metrology", "shipbuilding", "rare earth", "critical mineral", "critical metal", "smelting",
            "composites", "machine tool", "sheet metal", "pcb", "circuit board",
            "printed circuit", "cad kernel", "parametric cad", "g-code",
        ],
        "context": [
            "cad", "cam", "gd&t", "quality inspection", "assembly line", "bill of materials",
            "industrial", "process control", "fabrication", "supply chain", "foundry",
            "manufacturer", "kicad", "freecad", "opencascade",
        ],
    },
    "semiconductors": {
        "strong": [
            "semiconductor", "asic", "fpga", "risc-v", "wafer", "lithography", "photonic", "tapeout", "tape-out", "verilog", "systemverilog", "vhdl",
            "chip packaging", "advanced packaging", "silicon carbide", "gallium nitride",
            "inference chip", "analog ic", "mems", "chip design", "microprocessor",
            "software-defined radio", "gnuradio",
        ],
        "context": [
            "tsmc", "pdk", "edge inference", "neuromorphic", "rf front end", "chip",
            "silicon", "transceiver",
        ],
    },
    "space": {
        "strong": [
            "satellite", "spacecraft", "launch vehicle", "space vehicle", "orbital",
            "in-space", "cubesat", "smallsat", "propulsion", "thruster", "low earth orbit",
            "lunar", "cislunar", "earth observation", "space domain awareness", "reentry",
            "ground station",
        ],
        "context": [
            "star tracker", "hall thruster", "deorbit", "rideshare", "rocket",
            "constellation", "aerospace", "nasa",
        ],
    },
}

# A single matched term only counts on its own when it can hardly mean
# anything but hardware. Everything else needs a second term.
THESIS_UNAMBIGUOUS = {
    "robot", "robotic", "humanoid", "quadruped", "exoskeleton", "teleoperation",
    "manipulator", "drone", "uav", "uas", "suas", "evtol", "autopilot", "px4", "ardupilot",
    "bvlos", "quadcopter", "quadrotor", "multirotor", "counter-uas", "c-uas", "cuas",
    "unmanned aerial", "munition", "missile", "hypersonic", "warfighter",
    "counter-drone", "fission", "microreactor", "tokamak", "stellarator", "haleu",
    "geothermal", "electrolyzer", "photovoltaic", "semiconductor", "fpga", "asic",
    "lithography", "photonic", "tapeout", "satellite", "rare earth", "critical mineral", "spacecraft", "launch vehicle",
    "cubesat", "smallsat", "thruster", "shipbuilding", "machining", "cnc",
    "additive manufacturing", "physical ai", "embodied ai", "robot learning",
    "energy storage", "power electronics", "defense", "defence", "radar", "phased array",
    "verilog", "systemverilog", "vhdl", "risc-v", "ros2", "ros 2", "gnuradio",
    "software-defined radio", "space vehicle", "microprocessor", "molten salt", "microgrid",
    "vtol", "in-space", "lunar", "microfactory", "pcb", "circuit board",
}

# Phrases that contain a thesis word and mean something else. They are
# blanked out before matching.
THESIS_FALSE_FRIENDS = [
    "sensor fusion", "data fusion", "multi-modal fusion", "multimodal fusion",
    "feature fusion", "fusion cuisine", "fusion 360", "hydrogen peroxide",
    "hydrogen sulfide", "hydrogen bond", "solar wind", "solar energetic particle",
    "nuclear receptor", "nuclear delivery", "nuclear family", "nuclear localization",
    "cell nuclear", "home assistant", "battery included", "batteries included",
    "space complexity", "satellite office", "satellite campus", "drone music",
    "robot.txt", "robots.txt", "defense mechanism", "self-defense", "legal defense",
    "public defender", "grid layout", "css grid", "data grid", "grid search",
    "casting call", "casting director", "type casting", "plasma donation",
    "blood plasma", "plasma tv", "factory method", "factory pattern", "factory reset",
    "energy drink", "army of", "powder bed fusion", "bev fusion", "capability fusion",
    "model fusion", "information fusion", "nuclear clock", "battery-inspired",
    "satellite broadcast", "satellite tv", "satellite radio", "other than cubesat",
    "tech radar", "technology radar", "radar chart", "radar plot", "on my radar",
    "on our radar", "on the radar", "under the radar", "off the radar", "cyber defense",
    "cyberdefense", "defense in depth", "threat defense", "ddos defense",
    "injection defense", "defense attorney", "robotic process automation",
    "lunar new year", "solar plexus",
]

# Family weights in the composite. They sum to 1. Capital and regulatory
# exhaust are weighted up on purpose: for hardware companies they fire
# earlier than anything a software-era sourcing tool watches.
FAMILY_WEIGHTS = {
    "capital": 0.20,
    "regulatory": 0.16,
    "github": 0.15,
    "research": 0.14,
    "hiring": 0.14,
    "launch": 0.09,
    "social": 0.06,
    "traffic": 0.06,
}

# A family at full strength, alone, lifts momentum to weight * FAMILY_GAIN,
# and never past FAMILY_CAP: no single source, however loud, can take a
# company to the top. Families combine by noisy-OR, so independent
# corroboration compounds and is the only way past the cap.
FAMILY_GAIN = 4.2
FAMILY_CAP = 0.70

# Companies Anti Fund already holds. They are flagged, not ranked: the point
# is to find the next one.
PORTFOLIO = [
    "OpenAI", "Cognition", "Physical Intelligence", "Etched", "Modal", "Efference",
    "Kela Systems", "Merge", "Trajectory", "Enigma", "Orbital", "General Galactic",
    "Anduril", "Saronic", "Aeon", "The Boring Company", "Helion", "General Matter",
    "Ramp", "Erebor", "Polymarket", "Archive", "WithCoverage", "Lighter", "Cluely",
    "Creed", "Melius", "Natural", "Pensive", "ElevenLabs", "Monaco", "Liquid", "Entropy",
    "Eight Sleep", "Chronosphere", "Rail", "Aerodome", "Metis", "SpaceX",
]


@lru_cache(maxsize=1)
def github_token() -> str | None:
    """Token from the environment, falling back to the gh CLI's keyring."""
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok
    try:
        out = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None
