"""GitHub star velocity and new organizations publishing thesis-aligned repos.

The source is public GitHub: repository search for the sectors where GitHub
actually has signal (robotics, embodied AI, simulation, drones, CAD/CAM,
EDA/FPGA, SDR/RF, thin slices of energy and space), the daily star-history
endpoint for how fast developers are starring each repo, and owner and
contributor profiles for who is behind it. It is early because engineers
star, fork and commit to a technical project weeks or months before the
company around it raises, hires or gets press, and a new organization with
its own domain and several committers is a company forming in public.

Two kinds are emitted, at most one of each per repo owner:

  star_velocity   stars in the last 7 and 30 days against the periods before,
                  with a weekly series whose per-point strength lets the
                  scorer rebuild rank history
  new_org_repo    an organization created in the last year, with its own
                  domain, published a repo that two or more people commit to

How it works, in order:

1. Discovery: batched GraphQL repository search through two lenses: repos
   created inside the lookback window with traction, and older repos pushed
   in the last two weeks that might be accelerating now.
2. Filtering: forks, archives, awesome-lists, course material, paper lists,
   big-tech and university owners are dropped. The rest must fit the thesis
   (thesis_gate): anchored by unambiguous physical-world vocabulary. The
   shared keyword model caps a lone ambiguous term, but "satellite", "uav",
   "drone", "cnc", "fpga", "autopilot", "radar" and "pcb" count there as
   unambiguous, and on GitHub they are not: a Home Assistant "voice
   satellite", a "UAV" video downloader, Command & Conquer ("cnc"), MiSTer
   FPGA game cores, "autopilot" job-hunt agents, a "meme radar" for crypto
   tokens, a game cheat's "radar overlay" and a hobby laptop tagged "pcb"
   all pass it on that one word. So the anchors and the negative list below
   still decide what is emitted.
3. Velocity: one call per repo to GET /repos/{o}/{r}/stargazers/history.
4. Fake-star check, on the high-velocity repos only: sample recent WatchEvent
   actors from /repos/{o}/{r}/events and look at account age. An owner whose
   recent stargazers are mostly accounts under 90 days old is dropped (see
   stars_look_bought); borderline samples are down-weighted and
   stargazer_quality is recorded. If the events cannot be fetched the repo
   is held back, not emitted unchecked.
5. Owner and people: org or user profile, and the top human contributors
   who did a real share of the work (attached_contributors), with the
   company and bio their profiles state.

Core REST calls on a full run are capped at 580 (see _BUDGET; measured 483).
GraphQL is paced because GitHub meters it by server time, and a batch that
times out (HTTP 502) is split in half and retried.

Thesis fit. Signal.text is everything GitHub says about the repo: its full
name, description, all of its topics (up to 20), the language GitHub detects
most of the code to be written in, and the owning organization's own
description. metrics["thesis_fit"] is the shared keyword model's score of
exactly that text plus the entity name. metrics["thesis_anchor_only"] = 1
marks a signal this module's anchors place on thesis while the shared model
scores it below 0.3. That is one of two things: a single term under its
lone-term cap ("cad" on a DXF editor, "mujoco" on a simulation library,
"kicad" on a footprint exporter), or no term at all, thesis_fit 0.0, where
the repo speaks in vocabulary the shared model does not list (an RTL-SDR
scanner that never writes out "software-defined radio", an OPC UA or PLC
gateway, an Isaac Lab training repo). Nothing is added to the text to lift
such a row; it waits for a second source. Hardware description languages,
"risc-v", "pcb" and "software-defined radio" each pass the shared model
alone, so a Verilog linter or a RISC-V core is not in this group.

Storage. A star_velocity signal is a continuous measurement, stored as one
row per repo that each run refreshes: metrics["repo"] ("owner/name") is the
key that tells two repos of one owner apart.

Dates. The star-history endpoint labels each week with Sunday 00:00 UTC but
buckets stars into US Pacific calendar days (checked against WatchEvent
timestamps on nine repos: an offset of -7 hours left 2 stars unexplained,
UTC left 86). occurred_at on a star_velocity signal is therefore a Pacific
calendar day; it is clamped so it never precedes the repo's creation date
(UTC) and never follows ctx.today.

Not claimed. A GitHub organization's creation date is not a company's
founding date (a three-year-old company can open a new org last month), so
EntityHint.founded is never set from it; the date appears only in the
new_org_repo title, where it is called what it is.
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from .. import http
from ..config import github_token
from ..models import EntityHint, Person, Signal
from ..thesis import classify
from .base import Context, clean_domain, parse_date, squash

SLUG = "github_velocity"
FAMILY = "github"
STAGE = "discover"
DESCRIPTION = "Star velocity on thesis repos, and new orgs with a domain and several committers"

API = "https://api.github.com"
GRAPHQL = API + "/graphql"

# Core REST calls on a full run: 380 + 70 + 130 = 580 (limit given: 600).
_BUDGET = {"history": 380, "events": 70, "contributors": 130}
_OLDER_SHARE = 0.34          # share of the history budget spent on the older-repo lens
_NODES_PER_REQUEST = 45      # GraphQL search nodes per request; more than ~80 returns HTTP 502
_OWNERS_PER_REQUEST = 30
_PENDING_MAX = 150           # org repos re-gated with the organization's description
_USERS_PER_REQUEST = 70
_STARGAZERS_PER_REQUEST = 90
_WORKERS = 3

_CONTRIBUTORS_PAGE = 100     # GitHub's maximum page size for contributors
_SG_SAMPLE = 30              # recent stargazers sampled per repo for the fake-star check
_SG_MIN_SAMPLE = 15          # fewer than this and no judgement is made
_SG_YOUNG_DAYS = 90
_SG_DROP_ABOVE = 0.5         # share of sampled stargazers with accounts under 90 days old

_NEW_ORG_DAYS = 365          # "created recently" for the new_org_repo kind
_OLDER_MAX_AGE_DAYS = 730
_ESTABLISHED_DAYS = 5 * 365  # org older than this with many public repos is an incumbent
_ESTABLISHED_REPOS = 50
_SERIES_WEEKS = 13
_SUSTAINED_MIN = 10          # stars in a week for it to count as a sustained week

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# --------------------------------------------------------------------------
# Search slices. (group, query core, min stars for new repos, nodes new, nodes older)
# Keyword cores get "in:name,description,topics" appended. Groups are used to
# interleave sectors so thin ones are not crowded out by robotics.
# --------------------------------------------------------------------------

_KW = " in:name,description,topics"
SLICES: list[tuple[str, str, int, int, int]] = [
    ("robotics", "topic:robotics", 25, 40, 25),
    ("autonomy", "px4 OR ardupilot OR uav OR drone" + _KW, 15, 30, 15),
    ("semis", "fpga OR verilog OR systemverilog OR risc-v OR asic OR tapeout" + _KW, 20, 30, 15),
    ("manufacturing", "topic:cad", 15, 25, 12),
    ("rf", "topic:sdr", 10, 20, 10),
    ("space", 'cubesat OR spacecraft OR "ground station" OR satellite' + _KW, 15, 20, 10),
    ("energy", 'tokamak OR stellarator OR "nuclear reactor" OR "power grid" OR '
               '"battery management" OR "fusion energy"' + _KW, 5, 10, 7),
    ("robotics", '"vision-language-action" OR vla OR "robot learning"' + _KW, 30, 25, 15),
    ("robotics", "humanoid" + _KW, 15, 25, 12),
    ("robotics", "topic:embodied-ai", 15, 25, 12),
    ("autonomy", "topic:drone", 10, 15, 10),
    ("semis", "topic:fpga", 10, 20, 10),
    ("manufacturing", 'cnc OR cad OR gcode OR "computer-aided"' + _KW, 30, 25, 12),
    ("rf", 'sdr OR "software-defined radio" OR gnuradio' + _KW, 15, 15, 8),
    ("space", "rocket OR propulsion OR thruster OR orbital" + _KW, 15, 15, 6),
    ("energy", '"energy storage" OR inverter OR microgrid OR "power electronics"' + _KW, 10, 10, 6),
    ("robotics", "robot OR robotics OR robotic" + _KW, 40, 40, 20),
    ("robotics", "topic:ros2", 15, 25, 15),
    ("robotics", "manipulation OR teleoperation OR dexterous" + _KW, 25, 25, 10),
    ("robotics", 'mujoco OR "isaac lab" OR isaaclab OR "isaac sim"' + _KW, 20, 20, 12),
    ("autonomy", '"flight controller" OR autopilot OR mavlink OR "counter-uas"' + _KW, 10, 15, 8),
    ("semis", "topic:eda", 10, 15, 8),
    ("semis", 'pcb OR kicad OR "chip design" OR eda' + _KW, 30, 20, 10),
    ("manufacturing", "topic:3d-printing", 25, 15, 8),
    ("rf", "topic:radar", 10, 10, 6),
    ("energy", "topic:energy-storage", 5, 8, 4),
    ("space", "topic:aerospace", 5, 5, 5),
    ("robotics", "topic:robot-learning", 10, 15, 5),
    ("robotics", '"embodied ai" OR "embodied intelligence" OR "physical ai"' + _KW, 25, 20, 10),
    ("robotics", 'quadruped OR exoskeleton OR gripper OR "robot arm"' + _KW, 10, 10, 8),
    ("robotics", '"sim-to-real" OR sim2real OR "imitation learning" OR "motion planning"' + _KW, 10, 10, 8),
    ("robotics", "topic:lerobot", 10, 10, 3),
    ("robotics", "topic:slam", 15, 15, 8),
    ("autonomy", "topic:autonomous-driving", 15, 10, 8),
    ("autonomy", "topic:ardupilot", 4, 5, 5),
    ("autonomy", "topic:px4", 4, 6, 4),
    ("autonomy", "topic:uav", 10, 10, 6),
    ("semis", "topic:kicad", 15, 15, 8),
    ("semis", "topic:riscv", 10, 10, 3),
    ("semis", "topic:risc-v", 10, 10, 5),
    ("semis", "topic:asic", 8, 8, 5),
    ("manufacturing", "topic:plc", 10, 10, 6),
    ("manufacturing", "topic:industrial-automation", 10, 10, 5),
    ("manufacturing", "topic:manufacturing", 5, 10, 4),
    ("manufacturing", "topic:cnc", 5, 6, 4),
    ("energy", "topic:power-systems", 3, 6, 0),
    ("energy", "topic:battery-management-system", 3, 4, 2),
    ("energy", "topic:power-electronics", 3, 5, 1),
    ("space", "topic:space", 8, 8, 4),
    ("defense", 'military OR "counter-drone" OR "electronic warfare" OR "defense tech"' + _KW, 10, 10, 5),
]

# Dense slices also get a "fresh" pass: created in the last three weeks, low bar.
_FRESH_CORES: list[tuple[str, str]] = [
    ("robotics", "robot OR robotics OR robotic OR humanoid" + _KW),
    ("autonomy", "px4 OR ardupilot OR uav OR drone" + _KW),
    ("semis", "fpga OR verilog OR risc-v OR asic OR pcb OR kicad" + _KW),
    ("manufacturing", 'cnc OR cad OR gcode OR "computer-aided"' + _KW),
    ("rf", 'sdr OR "software-defined radio" OR gnuradio OR radar' + _KW),
]

_REPO_FRAGMENT = (
    "fragment R on Repository{ nameWithOwner url description stargazerCount forkCount "
    "createdAt pushedAt homepageUrl isFork isArchived primaryLanguage{name} "
    "repositoryTopics(first:20){nodes{topic{name}}} owner{__typename login} }"
)
_OWNER_SELECTION = (
    "{ __typename login "
    "... on Organization{ name websiteUrl createdAt description email location "
    "twitterUsername isVerified repositories(privacy:PUBLIC){totalCount} } "
    "... on User{ name company websiteUrl location bio createdAt twitterUsername "
    "followers{totalCount} repositories(privacy:PUBLIC){totalCount} } }"
)
_STARGAZER_SELECTION = "{ login createdAt followers{totalCount} }"
_USER_SELECTION = (
    "{ login name company bio location websiteUrl twitterUsername createdAt "
    "followers{totalCount} repositories(privacy:PUBLIC){totalCount} }"
)


# --------------------------------------------------------------------------
# Stoplists and text filters
# --------------------------------------------------------------------------

# Big tech, their research arms, incumbents, foundations and well-known
# university labs. Lower case. Not exhaustive: organizations are also dropped
# by profile text, academic domains and age (see parse_owner, is_established).
STOP_OWNERS = {
    "google", "google-deepmind", "google-research", "googlecloudplatform", "googleapis",
    "deepmind", "tensorflow", "facebook", "facebookresearch", "meta-pytorch", "pytorch",
    "microsoft", "azure", "nvidia", "nvlabs", "nvidia-omniverse", "nvidia-isaac-ros",
    "nvidia-isaac", "isaac-sim", "nvidia-ai-iot", "nvidiagameworks", "newton-physics",
    "openai", "anthropics", "apple", "amazon", "amzn", "aws", "awslabs", "amazon-science",
    "aws-samples", "alibaba", "alibaba-damo-academy", "aliyun", "damo-nlp-sg", "tencent",
    "tencentarc", "tencent-hunyuan", "tencentcloud", "bytedance", "bytedance-seed", "baidu",
    "paddlepaddle", "huawei", "huawei-noah", "mindspore-ai", "intel", "intellabs", "ibm",
    "samsung", "sony", "sonyresearch", "bosch", "boschresearch", "bosch-ai", "siemens",
    "toyota", "toyotaresearchinstitute", "tri-ml", "honda-research-institute", "huggingface",
    "pollen-robotics", "xiaomi", "xiaomirobotics", "oppo", "meituan", "didi", "jd-opensource",
    "sensetime", "megvii", "openbmb", "open-mmlab", "opengvlab", "openrobotlab",
    "internrobotics", "opendrivelab", "modelscope", "qualcomm", "arm-software", "armmbed",
    "amd", "xilinx", "stmicroelectronics", "espressif", "nxp", "texasinstruments",
    "analogdevicesinc", "infineon", "nordicsemiconductor", "raspberrypi", "arduino",
    "adafruit", "sparkfun", "seeed-studio", "robotis-git", "tesla", "teslamotors", "commaai",
    "waymo-research", "uber", "uber-research", "lyft", "salesforce", "adobe",
    "adobe-research", "autodesk", "ansys", "mathworks", "dassault", "ros", "ros2",
    "ros-planning", "ros-navigation", "ros-controls", "moveit", "px4", "ardupilot", "mavlink",
    "autowarefoundation", "apolloauto", "carla-simulator", "gazebosim", "osrf", "open-rmf",
    "unitreerobotics", "boston-dynamics", "bostondynamics", "dji-sdk", "agibot-world",
    "agibottech", "ubtech", "fourier-intelligence", "physical-intelligence", "figure-ai",
    "anduril", "spacex", "nasa", "nasa-jpl", "nasa-ammos", "esa", "cern", "iterorganization",
    "lowrisc", "chipsalliance", "the-openroad-project", "yosyshq", "kicad", "freecad",
    "opencascade", "prusa3d", "ultimaker", "bambulab", "klipper3d", "marlinfirmware",
    "gnuradio", "apache", "eclipse", "eclipse-zenoh", "linuxfoundation", "lf-energy",
    "zephyrproject-rtos", "home-assistant", "stanfordnlp", "stanford-crfm",
    "berkeleyautomation", "mit-pdos", "leggedrobotics", "ethz-asl", "eth-sri", "rai-inst",
    "theaiinstitute", "allenai", "mistralai", "deepseek-ai", "qwenlm", "thudm", "zai-org",
    "stepfun-ai", "moonshotai", "x-plug", "microsoft-research", "janestreet", "jane-street",
    "hudson-trading", "intrinsic-ai", "robbyant", "antgroup", "ant-research", "showlab",
    "jsk-ros-pkg", "rlinf", "zju3dv", "prbonn", "cvg", "dfki-ric", "sii-research", "opendcai",
    "openmoss", "h-embodvis", "roboverseorg", "robotwin-platform", "davian-robotics",
    "open-x-humanoid", "datawhalechina", "cair-vinuni", "nvidia-cosmos", "stripe",
    "cloudflare", "vercel", "shopify", "netflix", "airbnb", "spotify", "databricks", "snowflakedb", "oracle", "sap", "cisco",
    "vmware", "redhat", "canonical", "mozilla", "jetbrains", "github", "gitlab", "docker",
    "kubernetes", "hashicorp", "elastic",
}

_BIG = (r"google|microsoft|msft|nvidia|amazon|aws|alibaba|tencent|bytedance|baidu|huawei|intel|"
        r"ibm|samsung|sony|bosch|apple|meta|facebook|openai|xiaomi|toyota|honda|siemens|nasa")
_UNI = (r"mit|stanford|berkeley|cmu|eth|ethz|epfl|hku|hkust|cuhk|ntu|nus|thu|tsinghua|pku|sjtu|"
        r"zju|ustc|fudan|kaist|snu|utokyo|ucla|ucsd|ucb|uiuc|gatech|umich|upenn|princeton|"
        r"harvard|caltech|cornell|columbia|nyu|oxford|cambridge|imperial|tum|kit|tudelft|kth|"
        r"inria|cnrs|mpi|dlr|jsk|iit|iisc|nycu|uw|ut|usc|jhu|purdue|vinuni|dfki")
# Owner logins that start with a big-tech or university name ("amazon-far",
# "baidu-baige", "eth-siplab", "hku-sail"), and organization logins that end
# with a university ("real-stanford"). A student's own account that ends in
# "-cuhk" is a person, not a university org, and stays.
_STOP_PREFIX = re.compile(rf"^({_BIG}|{_UNI})[\-_]", re.I)
_STOP_SUFFIX = re.compile(rf"[\-_]({_UNI})$", re.I)
# Organization logins that say what they are: a lab, a research group, a
# student team, or an account opened for one paper or benchmark.
_LAB_NAME = re.compile(r"\b(lab|laboratory|laboratories|group|team|benchmark|official)\b", re.I)

_ACADEMIC_TLD = re.compile(r"(\.edu|\.edu\.[a-z]{2}|\.ac\.[a-z]{2})$")
# For an organization's own name and description.
_ACADEMIC_TEXT = re.compile(
    r"\b(universit(y|ies|at|ät|é|e|à|a)|universidad|college|polytechnic|institute of|"
    r"academy of sciences|school of|faculty of|department of|research group|laborator(y|ies)|"
    r"lab|student (team|club|group|organization)|capstone|robocup|first robotics|frc team|"
    r"ftc team)\b|大学|学院|实验室|研究所|研究院|课题组",
    re.I,
)
# For repo descriptions: the same cues without the bare word "lab" (a company
# can call its product a lab; an organization calling itself one is academic).
_UNIVERSITY_TEXT = re.compile(
    r"\b(universit(y|ies|at|ät|é|e|à|a)|universidad|polytechnic|capstone|robocup|"
    r"student (team|club|group|organization)|first robotics|frc team|ftc team|"
    r"(fall|spring|summer|winter)[\s\-_]?20\d\d|20\d\d[\s\-_]?(fall|spring|summer|winter))\b"
    # student electronics contests (蓝桥杯, 电赛) publish practice boards by the hundred
    r"|大学|学院|实验室|课题组|毕业设计|课程设计|竞赛|蓝桥杯|电赛",
    re.I,
)

# Repos that are reading or teaching material rather than a project.
_LIST_TEXT = re.compile(
    r"\b(awesome|curated|paper[\s\-]?lists?|reading[\s\-]lists?|papers? (list|collection)|"
    r"(list|summary|survey|review|roadmap) of|(curated|comprehensive) (list|collection|hub)|"
    r"collection of (papers|resources|links|tutorials|examples)|must[\s\-]read|"
    r"tutorials?|course(work|s)?|lectures?|homework|assignments?|syllabus|bootcamp|"
    r"cheat[\s\-]?sheets?|interview|(internship|exam)[\s\-]prep\w*|study (notes|guide)|"
    r"learning (notes|path|resources|handbook)|"
    r"guides? (of|to|for)|beginner'?s guide|workshop materials?|textbook|handbook|book|"
    r"cookbook|exercises|leetcode|knowledge base|engineer roadmap|learning roadmap|"
    r"for learning purposes|design practice|zero to|basic logic gates)\b"
    r"|教程|课程|笔记|论文(列表|合集|整理|汇总)|综述|入门|学习(资料|手册|笔记|路线|指南)|手册|指南"
    r"|面试|知识库|求职|资料汇总|合集|从零",
    re.I,
)
_LIST_TOPICS = {
    "awesome", "awesome-list", "awesome-lists", "list", "papers", "paper-list", "survey",
    "tutorial", "tutorials", "course", "courses", "education", "learning-resources",
    "interview", "roadmap", "cheatsheet", "book", "homework", "handbook",
}

# Keyword collisions seen in live results: Home Assistant "voice satellites",
# Command & Conquer ("cnc"), MiSTer FPGA retro gaming, SDR as in sales reps and
# display dynamic range, a "UAV" video downloader, cracked CAD installers,
# exploit proofs of concept, agent prompt packs. Matched on name and
# description only: real hardware projects carry topics like "home-assistant".
# Still needed with the shared model's lone-term cap: of 124 repos this
# pattern dropped among the 825 search results in the HTTP cache on
# 2 Oct 2026, 95 scored 0.3 or more there, 61 of them on a single word it
# treats as unambiguous (satellite, pcb, radar, drone, fpga, risc-v,
# autopilot, cnc).
_NOT_A_PROJECT = re.compile(
    r"home[\s\-]?assistant|esphome|voice (satellite|assistant)|echo (dot|show)|\balexa\b|\bhacs\b"
    r"|\bgames?\b(?![\s\-]theor)|\b(gaming|gameplay|shmup|arcade|emulat(or|ion)|recompil\w*|"
    r"decompil\w*|retro|mister|doom|minecraft|roblox|modding|unlocker|dlss|steam deck|godot|gta|"
    r"shaders?|playstation|nintendo|famicom|red alert|tiberi\w+|command (&|and) conquer|wwii?|"
    r"world war|industrialcraft|ic2|addon for create|factorio|kerbal)\b"
    r"|\b((brand|portfolio|landing|cinematic)[\s\-]?(web)?(sites?|pages?)|wallpaper|dotfiles|"
    r"desktop environment|window manager|omarchy|waybar|hyprland|koreader|widget|"
    r"e[\s\-]?reader|e[\s\-]?paper|guitar|synthesizers?|keyboard firmware)\b|koplugin"
    r"|\b(agent|codex|claude|cursor|ai)[\s\-]skills?\b|\bskills? (for|pack)\b"
    r"|\bskill\b.{0,20}\b(codex|claude|gpt)\b|\bbunch of skills\b|\bmodel[\s\-]rout(er|ing)\b"
    r"|\bcve-\d{4}-\d+\b|\b(exploit|proof of concept|jailbreak|unlock|leech|osint)\b|pcileech"
    r"|\b(hdr|display calibration|color grading|footage|video[\s\-]editing|travel)\b"
    r"|\b(video[\s\-])?downloader\b|jable|missav|hanime|\bnsfw\b|\bporn\w*"
    r"|\b(crack(ed)?|keygen|activator|license key|premium|pro edition|full version)\b"
    r"|\b(b2b|cold[\s\-]email|sales (pipeline|agent|development|rep\w*)|crm)\b"
    r"|\badmin[\s\-](template|dashboard|panel)\b|中后台|后台模板"
    r"|\b(bot[\s\-]detect\w+|anti[\s\-]bot|headless|webdriver|captcha|issue tracker)\b"
    r"|\b(trading|forex|crypto|telegram|discord|whatsapp|wechat|qq)[\s\-]?(ro)?bots?\b|\bchat ?bots?\b"
    r"|robots\.txt|robot framework|\brpa\b|robotic process|solar system|observable universe"
    r"|house planner|floor plan|\b(exam|grade) (score|predict\w+)|student success"
    r"|comfyui|node graph|business card|macropad"
    # hobby gadgets seen live: Flipper Zero apps, Gridfinity storage bins, car and
    # scooter OBD dongles
    r"|flipper[\s\-]?zero|gridfinity|\bobd(2|[\s\-]?ii)?\b"
    # phone apps that warn of police cameras; their topics say "drone-detection"
    r"|\balpr\b|license[\s\-]plate (camera|reader)s?|\bbody[\s\-]?cams?\b|\bbody[\s\-]worn\b",
    re.I,
)

_NOT_TOPICS = {
    "gta-modding", "gta-sa", "game-development", "godot", "godot4", "nintendo", "famicom",
    "nes", "retro-gaming", "mister", "mister-fpga", "video-downloader", "minecraft",
    "minecraft-mod", "roblox", "hacs-integration", "agent-skills", "claude-skills",
    "codex-skill", "rocket-league",
}

# Topics that mark LLM-agent, crypto, game or web tooling. A repo carrying one
# must be anchored (below) by its own name and description, not by topics.
_NOISE_TOPICS = {
    "llm", "llm-agent", "ai-agents", "ai-agent", "agents", "agentic-ai", "agentic", "mcp",
    "mcp-server", "claude", "claude-code", "claude-skills", "chatgpt", "openai", "langchain",
    "rag", "prompt-engineering", "chatbot", "telegram-bot", "discord-bot", "trading",
    "trading-bot", "crypto", "cryptocurrency", "blockchain", "web3", "solana", "ethereum",
    "defi", "gamedev", "unity3d", "nextjs", "react", "tailwindcss", "wordpress", "seo",
    "scraper", "selenium", "playwright", "stable-diffusion", "comfyui", "text-to-image",
    "video-generation",
}

# The thesis gate. The shared keyword model passes text on one stray word
# when it holds that word to be unambiguous ("satellite" maps, an "autopilot"
# for job applications, a "robot" admin template), and on two loose ones
# ("autonomous agent swarm"), so a repo must be anchored: see anchored().
# Specific families score 2, ambiguous ones 1, and each family is counted
# once. Families in _LOOSE are specific terms that the LLM-agent crowd also
# uses loosely ("physical AI assistant", an "embodied" memory layer, a
# "humanoid" avatar): one of those alone is not enough.
_SPECIFIC: list[tuple[str, re.Pattern[str]]] = [(k, re.compile(rf"\b({v})\b", re.I)) for k, v in {
    "robotics": r"robotics",
    "humanoid": r"humanoids?",
    "legged": r"quadrupeds?|quadrupedal|legged|bipeds?|bipedal|exoskeletons?",
    "manipulation": r"manipulators?|teleop|teleoperation|grippers?|dexterous|robot(ic)? arms?|"
                    r"end[\s\-]effectors?|bimanual|mobile manipulat\w+",
    "embodied": r"embodied|physical ai|vla|vision[\s\-]language[\s\-]action|"
                r"world[\s\-]action models?|robot learning|imitation learning|"
                r"sim[\s\-]?(to|2)[\s\-]?real",
    "ros": r"ros ?2|ros|urdf|nav2|moveit2?|gazebo",
    "robot_sim": r"mujoco|mjlab|isaac[\s\-]?(sim|lab|gym)|isaaclab|lerobot",
    "motion": r"locomotion|whole[\s\-]body|motion planning|bldc|field[\s\-]oriented control|"
              r"motor[\s\-]control(ler)?s?|servo (drive|motor)s?|actuators?",
    "autopilot": r"px4|ardupilot|mavlink|betaflight|inav|flight[\s\-]controllers?|"
                 r"ground control station|expresslrs|fpv",
    "uav": r"quadcopters?|quadrotors?|multirotors?|evtol|vtol|counter[\s\-]?(uas|drone)|"
           r"drone[\s\-]detect\w+|remote ?id|(gps|gnss)[\s\-]denied",
    "driving": r"autonomous (driving|vehicles?|navigation|flight)|self[\s\-]driving",
    "perception": r"lidar|slam|odometry|gnss|rtk",
    "hdl": r"fpga|verilog|systemverilog|vhdl|asic|tape[\s\-]?out|pdk|chip[\s\-]?design|"
           r"ic design|semiconductors?|photonics?|lithography|rv32i|rv64\w*|"
           r"risc[\s\-]?v (core|cpu|processor|soc|chip|mcu|microcontroller)s?",
    "pcb": r"kicad|pcbs?|circuit boards?|electronic design automation|eda (tool|flow)s?|"
           r"altium|easyeda|open[\s\-]hardware|gerbers?",
    "radio": r"software[\s\-]defined radios?|gnu ?radio|rtl[\s\-]?sdr|hackrf|usrp|plutosdr|"
             r"limesdr|ham[\s\-]?radio|amateur[\s\-]radio|transceivers?|rf (front[\s\-]end|design)",
    "space": r"cubesats?|spacecraft|ground stations?|astrodynamics|orbital mechanics|"
             r"launch vehicles?|thrusters?|rocket (engine|propulsion|motor)s?|rocketry|"
             r"attitude (control|determination)",
    "cad": r"b[\s\-]?rep|nurbs|opencascade|cad kernel|freecad|build123d|cadquery|openscad|"
           r"step files?|dxf|dwg|parametric (3d )?(cad|model\w+)|3d cad|g[\s\-]?code|"
           r"(text to|ai|agentic) cad|toolpaths?|cnc|machining|sheet metal|"
           r"additive manufacturing",
    "industrial": r"plc|industrial automation|factory automation|opc[\s\-]?ua|modbus|"
                  r"iec[\s\-]?61131(-3)?|codesys|twincat|scada",
    "energy": r"tokamaks?|stellarators?|fusion (energy|reactor|plasma)s?|"
              r"plasma (physics|equilibrium|simulation)s?|fission|nuclear reactors?|"
              r"microgrids?|inverter[\s\-]based|grid[\s\-]forming|load[\s\-]flow|"
              r"photovoltaics?|electroly[sz]ers?",
    "power": r"battery management|bms|energy storage|bess|power electronics|"
             r"power (systems?|grid)",
}.items()] + [("zh", re.compile(r"人形机器人|机械臂|飞控|四足")),
              ("embodied", re.compile(r"具身"))]
_LOOSE = {"embodied", "humanoid", "manipulation", "motion", "driving", "power"}
_AMBIGUOUS: list[tuple[str, re.Pattern[str]]] = [(k, re.compile(rf"\b({v})\b", re.I)) for k, v in {
    "robot": r"robots?|robotic",
    "drone": r"drones?|uavs?|uas",
    "satellite": r"satellites?|orbit|orbits|orbital",
    "sdr": r"sdr",
    "rf": r"rf|radar|antennas?",
    "cad_word": r"cad",
    "printing": r"3d[\s\-]?print(er|ers|ing|able|ed)",
    "eda_word": r"eda",
    "riscv": r"risc[\s\-]?v",
    "nuclear": r"nuclear|reactors?",
    "battery": r"batter(y|ies)|inverters?",
    "chip": r"chips?|silicon",
    "embedded": r"firmware|esp32\w*|stm32\w*|embedded",
    "rtl_word": r"rtl|hdl",
    "manip_word": r"manipulation|grasping",
}.items()] + [("zh_word", re.compile(r"机器人|无人机"))]

# "radar" for a screen, seen live: an ADS-B plane tracker "shows their position
# on a radar GUI", an ESP32 "flight radar" draws a "radar sweep", game cheats
# add a "radar overlay" and a "radar-style minimap". The shared model passes
# text on the word "radar" alone, so a repo anchored here by something else
# (the plane tracker's RTL-SDR dongle) would go to the board as defense on
# that phrase. See only_a_screen_radar.
_SCREEN_RADAR = re.compile(
    r"\bradar[\s\-]?(gui|ui|overlay|sweep|style)s?\b|\bflight[\s\-]?radars?\b", re.I)

_PAPER_DROP = re.compile(
    r"[\[\(][^\]\)]{0,12}\b(neurips|nips|iclr|icml|corl|icra|iros|rss|cvpr|iccv|eccv|aaai|"
    r"siggraph|t-?ro|ra-?l|ijrr|acl|emnlp|tpami|arxiv)\b[\s'’]*(20)?\d\d\b[^\]\)]{0,16}[\]\)]"
    r"|\bofficial\b.{0,30}\b(implementation|code|codebase|repo|repository|release)\b"
    r".{0,12}(\bpaper\b|[\"“'‘\[])"
    r"|\b(code|implementation|repository) (for|of) (the |our )?(\w+ )?paper\b"
    r"|\bcode for [\"“]",
    re.I,
)

# Hosts that are not a project's or company's own site and that
# base.clean_domain does not already reject: paper and docs hosts, Chinese
# portals, mail providers, maker galleries, upstream projects' own sites.
_JUNK_HOSTS = {
    "gitbook.io", "mintlify.app", "readme.io", "bilibili.com", "zhihu.com", "csdn.net",
    "qq.com", "openreview.net", "doi.org", "ieee.org", "acm.org", "researchgate.net",
    "modelscope.cn", "gitee.com", "docs.rs", "hf.co", "surge.sh", "sourceforge.net",
    "patreon.com", "ko-fi.com", "buymeacoffee.com", "opencollective.com", "slack.com",
    "telegram.org", "printables.com", "thingiverse.com", "makerworld.com", "kaggle.com",
    "zenodo.org", "nature.com", "springer.com", "sciencedirect.com", "mdpi.com", "ros.org",
    "px4.io", "ardupilot.org", "python.org", "rust-lang.org", "microsoft.com", "nvidia.com",
    "aliyun.com", "baidu.com", "163.com", "126.com", "hotmail.com", "protonmail.com",
    "icloud.com", "foxmail.com", "live.com", "pm.me", "mail.com", "gmx.de", "gmx.com",
    "yandex.ru", "sina.com", "naver.com", "canva.com", "framer.app", "steampowered.com",
    "twitch.tv", "threads.net", "mastodon.social", "wikimedia.org", "openai.com",
    "anthropic.com", "claude.ai", "chatgpt.com",
}

_BOT_LOGINS = {
    "claude", "copilot", "dependabot", "github-actions", "actions-user", "cursoragent",
    "cursor", "devin-ai-integration", "codex", "openhands", "openhands-agent", "renovate",
    "renovate-bot", "semantic-release-bot", "web-flow", "gemini-cli", "google-labs-jules",
    "sweep-ai", "aider", "imgbot", "allcontributors", "snyk-bot", "pre-commit-ci",
    "codecov", "mergify", "greenkeeper", "pyup-bot", "restyled-io", "sourcery-ai",
    "codegen-sh", "factory-droid", "amp", "blacksmith-sh", "release-please",
}

# Automation accounts registered as ordinary users ("blb3d-automation",
# "acme-release-bot"). Not a bare "bot" suffix: "talbot" is a person.
_BOT_PATTERN = re.compile(r"\[bot\]$|[\-_]bots?$|^bots?[\-_]|automation|[\-_](ci|actions|autofix|deploy)$")

_GENERIC_REPO_NAMES = {
    "core", "sdk", "firmware", "hardware", "software", "docs", "documentation", "website",
    "web", "app", "main", "demo", "demos", "examples", "example", "code", "project",
    "platform", "tools", "toolkit", "framework", "library", "lib", "api", "server", "client",
    "engine", "simulator", "sim", "robot", "robotics", "drone", "controller", "driver",
    "drivers", "models", "model", "data", "dataset", "datasets", "benchmark", "open-source",
    "opensource", "public", "monorepo", "stack", "ros2", "pcb", "cad",
}


# --------------------------------------------------------------------------
# Parsing (pure functions; these are what the offline tests exercise)
# --------------------------------------------------------------------------

@dataclass
class Repo:
    full_name: str
    owner: str
    name: str
    url: str
    description: str
    stars: int
    forks: int
    created: date
    pushed: date | None
    homepage: str | None
    topics: list[str]
    owner_type: str  # "Organization" | "User"
    is_fork: bool = False
    is_archived: bool = False
    language: str | None = None  # the language GitHub detects most of the code in
    lens: str = "new"      # "new" | "older"
    group: str = "robotics"


def parse_repo_node(node: dict | None) -> Repo | None:
    """One GraphQL Repository node (search result) to a Repo. None if unusable."""
    if not isinstance(node, dict):
        return None
    full = node.get("nameWithOwner") or ""
    created = parse_date(node.get("createdAt"))
    owner = node.get("owner") or {}
    if "/" not in full or created is None or not owner.get("login"):
        return None
    topics = [
        (t.get("topic") or {}).get("name", "")
        for t in ((node.get("repositoryTopics") or {}).get("nodes") or [])
        if isinstance(t, dict)
    ]
    lang = node.get("primaryLanguage")
    lang = lang if isinstance(lang, dict) else {}
    return Repo(
        full_name=full,
        owner=owner["login"],
        name=full.split("/", 1)[1],
        url=node.get("url") or f"https://github.com/{full}",
        description=" ".join((node.get("description") or "").split()),
        stars=int(node.get("stargazerCount") or 0),
        forks=int(node.get("forkCount") or 0),
        created=created,
        pushed=parse_date(node.get("pushedAt")),
        homepage=(node.get("homepageUrl") or "").strip() or None,
        topics=[t for t in topics if t],
        owner_type=owner.get("__typename") or "User",
        is_fork=bool(node.get("isFork")),
        is_archived=bool(node.get("isArchived")),
        language=" ".join(str(lang.get("name") or "").split()) or None,
    )


def _count(x: Any) -> int | None:
    """A GraphQL connection count ({"totalCount": n}); a bare number is accepted too."""
    if isinstance(x, dict):
        x = x.get("totalCount")
    return x if isinstance(x, int) and not isinstance(x, bool) else None


def _words(name: str) -> str:
    return name.replace("-", " ").replace("_", " ")


def _sentences(parts: Iterable[str | None]) -> str:
    """Join parts as sentences without doubling a full stop a part already ends on."""
    return ". ".join(p.rstrip(" .") for p in parts if p and p.strip(" ."))


def repo_text(repo: Repo) -> str:
    """The text a repo gives us for thesis classification: its name, its
    description and its topics, hyphens in topic slugs written as spaces."""
    topics = "Topics: " + ", ".join(t.replace("-", " ") for t in repo.topics) if repo.topics else ""
    return _sentences([_words(repo.name), repo.description, topics])


def exclusion_reason(repo: Repo) -> str | None:
    """Why a repo is not a candidate at all, or None if it is."""
    if repo.is_fork:
        return "fork"
    if repo.is_archived:
        return "archived"
    if (repo.owner.lower() in STOP_OWNERS or _STOP_PREFIX.search(repo.owner)
            or (repo.owner_type == "Organization" and _STOP_SUFFIX.search(repo.owner))):
        return "stoplist owner"
    head = f"{_words(repo.name)} {repo.description}"
    topics = {t.lower() for t in repo.topics}
    if _LIST_TEXT.search(head) or _LIST_TOPICS & topics or "roadmap" in repo.name.lower():
        return "list or course material"
    if repo.owner_type == "Organization" and _LAB_NAME.search(_words(repo.owner)):
        return "university"
    if _UNIVERSITY_TEXT.search(f"{_words(repo.owner)} {repo.description}"):
        return "university"
    if _NOT_A_PROJECT.search(head) or _NOT_TOPICS & topics:
        return "not a hardware project"
    return None


def anchored(text: str) -> bool:
    """Is this text about the physical world, by this module's anchors?

    True when it carries one strict specific family (px4, fpga, mujoco,
    cubesat, b-rep, tokamak...), or a loose specific one plus any other
    family, or three ambiguous ones.
    """
    specific = _specific_families(text)
    if specific - _LOOSE:
        return True
    text = text.replace("-", " ").replace("_", " ")
    return 2 * len(specific) + sum(1 for _, pat in _AMBIGUOUS if pat.search(text)) >= 3


def _specific_families(text: str) -> set[str]:
    text = text.replace("-", " ").replace("_", " ")
    return {k for k, pat in _SPECIFIC if pat.search(text)}


def thesis_gate(repo: Repo, owner_text: str = "") -> tuple[float, str] | None:
    """(fit, gate) if the repo belongs to the thesis, else None.

    `owner_text` is the owning organization's own description, when known: a
    young company often gives its repo a tagline ("Hardware as fast as
    software") and says what it builds on the org profile.

    gate is "classify" when the shared keyword model scores the repo's own
    text at 0.3 or more, and "anchor" when only this module's anchors place
    it: a single term under the shared model's lone-term cap, or vocabulary
    the shared model does not list at all. The fit an emitted signal
    reports is taken again from the full Signal text (see thesis_metrics),
    which also carries the owner and the language.
    """
    text = repo_text(repo) + (f". {owner_text}" if owner_text else "")
    who = _words(repo.owner)  # "X-Square-Robot", "Hebbian-Robotics", "OpenDrone-hw"
    if not anchored(f"{who}. {text}"):
        return None
    head = f"{who}. {_words(repo.name)}. {repo.description}. {owner_text}"
    head_anchored = anchored(head)
    # LLM-agent, crypto and web tooling that only mentions a robot or a drone
    # in its topics: the name and description themselves must be anchored.
    if _NOISE_TOPICS & {t.lower() for t in repo.topics} and not head_anchored:
        return None
    # "kicad" or "pcb" as a topic says a gadget was made with KiCad (a UPS
    # network adapter, a hobby laptop), not that it is EDA tooling. PCB
    # vocabulary anchors a repo only from its own name and description. The
    # shared model holds "pcb" to be unambiguous, so that topic alone passes it.
    if not head_anchored and _specific_families(f"{who}. {text}") - _LOOSE == {"pcb"}:
        return None
    # Anchored, but the shared model's whole case is a screen called a radar.
    if only_a_screen_radar(_signal_text(repo, owner_text)):
        return None
    fit = classify(text)["fit"]
    return fit, ("classify" if fit >= 0.3 else "anchor")


def only_a_screen_radar(text: str) -> bool:
    """Does the shared model pass this text only because a screen is called a
    radar? True when it scores 0.3 or more as written and under 0.3 once the
    _SCREEN_RADAR phrases are blanked. A radar project that also has a
    "radar GUI" keeps its other mentions of radar and is not affected, and
    neither is a repo with two other thesis terms."""
    if not _SCREEN_RADAR.search(text) or classify(text)["fit"] < 0.3:
        return False
    return classify(_SCREEN_RADAR.sub(" ", text))["fit"] < 0.3


def is_paper_drop(repo: Repo) -> bool:
    return bool(_PAPER_DROP.search(repo.description))


def daily_counts(history: Any) -> dict[date, int]:
    """Flatten the star-history response into {day: stars gained}.

    Each row is {"week": unix seconds of Sunday 00:00 UTC, "days": [Sun..Sat]}.
    The week label is UTC midnight; the seven buckets are US Pacific calendar
    days (see the module docstring), so the keys here are Pacific dates.
    """
    out: dict[date, int] = {}
    if not isinstance(history, list):
        return out
    for row in history:
        try:
            start = datetime.fromtimestamp(int(row["week"]), timezone.utc).date()
            days = list(row.get("days") or [])
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
        for i, n in enumerate(days[:7]):
            try:
                out[start + timedelta(days=i)] = out.get(start + timedelta(days=i), 0) + int(n)
            except (TypeError, ValueError):
                continue
    return out


def _window(daily: dict[date, int], end: date, days: int) -> int:
    """Stars gained in the `days` days ending on `end`, inclusive."""
    return sum(daily.get(end - timedelta(days=i), 0) for i in range(days))


def velocity(daily: dict[date, int], asof: date, stars_now: int) -> dict[str, Any]:
    """Velocity features as of a date. Days after `asof` are ignored."""
    s7 = _window(daily, asof, 7)
    p7 = _window(daily, asof - timedelta(days=7), 7)
    s30 = _window(daily, asof, 30)
    p30 = _window(daily, asof - timedelta(days=30), 30)
    later = sum(n for d, n in daily.items() if d > asof)
    total = max(stars_now - later, 0)
    weeks = [_window(daily, asof - timedelta(days=7 * w), 7) for w in range(8)]
    last30 = [(daily.get(asof - timedelta(days=i), 0), asof - timedelta(days=i)) for i in range(30)]
    # earliest of equal peaks: the date stays put from one daily run to the next
    peak, peak_date = max(last30, key=lambda x: (x[0], -x[1].toordinal()))
    peak7, peak7_date = max(last30[:7], key=lambda x: (x[0], -x[1].toordinal()))
    nonzero = [d for d, n in daily.items() if n > 0 and d <= asof]
    return {
        "stars_7d": s7,
        "stars_prev_7d": p7,
        "stars_30d": s30,
        "stars_prev_30d": p30,
        "accel_7d": round((s7 + 1) / (p7 + 1), 2),
        "accel_30d": round((s30 + 1) / (p30 + 1), 2),
        "stars_total": total,
        "velocity_share_30d": round(min(s30 / max(total, s30, 1), 1.0), 3),
        "sustained_weeks": sum(1 for w in weeks if w >= _SUSTAINED_MIN),
        "peak_day": peak,
        "peak_date": peak_date if peak > 0 else None,
        # the biggest day inside the last 7, for a signal whose title is about the week
        "peak_date_7d": peak7_date if peak7 > 0 else None,
        "burstiness": round(peak / s30, 3) if s30 > 0 else 0.0,
        "first_star": min(nonzero) if nonzero else None,
        "last_star": max(nonzero) if nonzero else None,
    }


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def velocity_strength(v: dict[str, Any]) -> float:
    """0..1 read of one repo's star velocity, before any stargazer-quality discount.

    Level (how many stars this week and this month) carries most of the
    weight; acceleration adds or subtracts in proportion to how much is
    actually moving, so 3 stars against 0 is not an event; sustained weeks
    add a little so a one-day spike ranks below steady pull of the same size.
    """
    s7, p7, s30, p30 = v["stars_7d"], v["stars_prev_7d"], v["stars_30d"], v["stars_prev_30d"]
    level = 0.55 * squash(s7, 120) + 0.45 * squash(s30, 500)
    a7 = _clamp(math.log2((s7 + 1) / (p7 + 1)), -3.0, 4.0)
    a30 = _clamp(math.log2((s30 + 1) / (p30 + 1)), -2.0, 4.0)
    s = (
        0.12
        + 0.85 * level
        + squash(s7, 80) * 0.045 * a7
        + squash(s30, 300) * 0.03 * a30
        + 0.015 * v["sustained_weeks"]
    )
    return round(_clamp(s, 0.05, 0.98), 3)


def quality_factor(quality: dict[str, Any] | None) -> float:
    """Multiplier on strength from the stargazer sample.

    None (not sampled, or sample too small) leaves strength alone. A share of
    accounts under 90 days old between 20% and 50% scales strength down to
    0.4; above 50% the caller drops the repo. Separately, a sample that is
    mostly zero-follower accounts with a median age under a year is
    down-weighted by 0.6. Both are heuristics about the sample, not proof
    about the repo.
    """
    if not quality:
        return 1.0
    young = quality["sg_pct_age_lt_90d"]
    f = 1.0 if young <= 0.2 else _clamp(1.0 - 2.0 * (young - 0.2), 0.4, 1.0)
    if quality["sg_pct_zero_followers"] >= 0.7 and quality["sg_median_account_age_days"] < 365:
        f *= 0.6
    return round(f, 3)


def stars_look_bought(quality: dict[str, Any] | None) -> bool:
    """Should a repo be dropped on its stargazer sample?

    Yes when more than half the sampled accounts are under 90 days old, or
    when three signs agree: 30% or more that young, 70% or more with no
    followers, and a median account age under a year. A title that states a
    star count the collector itself does not believe is worse than no row.
    """
    if not quality:
        return False
    young = quality["sg_pct_age_lt_90d"]
    if young > _SG_DROP_ABOVE:
        return True
    return (young >= 0.3 and quality["sg_pct_zero_followers"] >= 0.7
            and quality["sg_median_account_age_days"] < 365)


def weekly_series(daily: dict[date, int], asof: date, stars_now: int, created: date,
                  factor: float = 1.0, weeks: int = _SERIES_WEEKS) -> list[dict[str, Any]]:
    """Weekly points, oldest first: v = stars gained in the 7 days ending t,
    s = the strength this signal would have had on t."""
    out = []
    for w in range(weeks - 1, -1, -1):
        t = asof - timedelta(days=7 * w)
        if t < created:
            continue
        v = velocity(daily, t, stars_now)
        out.append({"t": t.isoformat(), "v": v["stars_7d"],
                    "s": round(velocity_strength(v) * factor, 3)})
    return out


def stargazer_logins(events: Any, limit: int = _SG_SAMPLE) -> list[str]:
    """Distinct actors of WatchEvents, newest first, bots removed."""
    if not isinstance(events, list):
        return []
    rows = [e for e in events if isinstance(e, dict) and e.get("type") == "WatchEvent"]
    rows.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    seen: list[str] = []
    for e in rows:
        login = ((e.get("actor") or {}).get("login") or "").strip()
        if not login or login.endswith("[bot]") or login in seen:
            continue
        seen.append(login)
        if len(seen) >= limit:
            break
    return seen


def stargazer_quality(users: list[dict], asof: date) -> dict[str, Any] | None:
    """Account-age profile of sampled stargazers. None if the sample is too small."""
    ages, zero_followers = [], 0
    for u in users:
        created = parse_date((u or {}).get("createdAt"))
        if created is None:
            continue
        ages.append(max((asof - created).days, 0))
        if (_count(u.get("followers")) or 0) == 0:
            zero_followers += 1
    if len(ages) < _SG_MIN_SAMPLE:
        return None
    ages.sort()
    young = sum(1 for a in ages if a < _SG_YOUNG_DAYS) / len(ages)
    return {
        "stargazer_quality": round(1.0 - young, 3),
        "sg_sample": len(ages),
        "sg_pct_age_lt_90d": round(young, 3),
        "sg_median_account_age_days": ages[len(ages) // 2],
        "sg_pct_zero_followers": round(zero_followers / len(ages), 3),
    }


def human_contributors(contribs: Any) -> list[dict[str, Any]]:
    """[{login, contributions}] for people, most commits first; automation removed."""
    out = []
    if not isinstance(contribs, list):
        return out
    for c in contribs:
        if not isinstance(c, dict):
            continue
        login = (c.get("login") or "").strip()
        if not login or c.get("type") != "User":
            continue
        low = login.lower()
        if low in _BOT_LOGINS or _BOT_PATTERN.search(low):
            continue
        out.append({"login": login, "contributions": int(c.get("contributions") or 0)})
    out.sort(key=lambda c: -c["contributions"])
    return out


# Leading labels that name a section of a company's site, not a different site.
_SERVICE_LABELS = {
    "developer", "developers", "dev", "docs", "doc", "open", "opensource", "oss", "tech",
    "technology", "blog", "app", "about", "home", "en", "cn", "global", "research", "github",
    "labs", "ai", "cad", "robotics", "wiki", "community", "forum", "learn", "get", "try",
    "web", "site", "main", "portal", "hub", "code", "git", "source", "engineering",
}


def _site_domain(url: str | None) -> str | None:
    """clean_domain plus a longer list of hosts that are nobody's own site.
    "developer.intrinsic.ai" becomes "intrinsic.ai"; other subdomains are
    kept exactly as the source gave them."""
    d = clean_domain(url)
    if not d:
        return None
    for junk in _JUNK_HOSTS:
        if d == junk or d.endswith("." + junk):
            return None
    labels = d.split(".")
    if len(labels) >= 3 and labels[0] in _SERVICE_LABELS and len(labels[-1]) >= 2:
        rest = labels[1:]
        if not (len(rest) == 2 and rest[0] in {"co", "com", "org", "net", "ac", "edu", "gov"}
                and len(rest[1]) == 2):
            d = ".".join(rest)
    return d


def _host(url_or_host: str | None) -> str | None:
    """The bare host of a URL or host string, whatever kind of site it is.
    clean_domain answers None for schools and governments, so it cannot be
    used to ask whether a site is academic."""
    s = (url_or_host or "").strip().lower()
    if not s or " " in s:
        return None
    try:
        host = urlparse(s if "://" in s else "http://" + s).netloc
    except ValueError:
        return None
    host = host.split("@")[-1].split(":")[0].removeprefix("www.").rstrip(".")
    return host if "." in host else None


def _is_academic_host(url_or_host: str | None) -> bool:
    host = _host(url_or_host)
    return bool(host and _ACADEMIC_TLD.search(host))


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def domain_related(domain: str, *names: str | None) -> bool:
    """Does a domain visibly belong to one of these names (owner, repo)?"""
    labels = domain.lower().split(".")[:-1]
    if len(labels) > 1 and labels[-1] in {"co", "com", "org", "net", "ac", "edu", "gov"}:
        labels = labels[:-1]
    # only the registrable label counts: "skyfall-gs.jayinnn.dev" is jayinnn's
    # personal site with a project subdomain, not the project's own domain
    labels = [_alnum(x) for x in labels[-1:] if len(_alnum(x)) >= 4]
    for name in names:
        n = _alnum(name or "")
        if len(n) < 4:
            continue
        for lab in labels:
            if lab in n or n in lab:
                return True
    return False


@dataclass
class Owner:
    login: str
    is_org: bool
    name: str | None = None
    website: str | None = None
    domain: str | None = None      # the owner's own site (org website or org email domain)
    description: str | None = None  # org description or user bio
    company: str | None = None
    location: str | None = None
    twitter: str | None = None
    created: date | None = None
    followers: int | None = None
    public_repos: int | None = None
    is_verified: bool = False
    email_on_domain: bool = False
    academic: bool = False


def parse_owner(node: dict | None) -> Owner | None:
    """A GraphQL repositoryOwner node (Organization or User) to an Owner."""
    if not isinstance(node, dict) or not node.get("login"):
        return None
    is_org = node.get("__typename") == "Organization"
    website = (node.get("websiteUrl") or "").strip() or None
    if website and not re.match(r"^[a-z][a-z0-9+.\-]*://", website, re.I):
        website = "https://" + website if "." in website and " " not in website else None
    o = Owner(
        login=node["login"],
        is_org=is_org,
        name=" ".join((node.get("name") or "").split()) or None,
        website=website,
        description=" ".join((node.get("description") or node.get("bio") or "").split()) or None,
        company=" ".join((node.get("company") or "").split()) or None,
        location=" ".join((node.get("location") or "").split()) or None,
        twitter=(node.get("twitterUsername") or "").strip() or None,
        created=parse_date(node.get("createdAt")),
        followers=_count(node.get("followers")),
        public_repos=_count(node.get("repositories")),
        is_verified=bool(node.get("isVerified")),
    )
    if _is_academic_host(website):
        o.academic = True
    if is_org:
        if _ACADEMIC_TEXT.search(f"{o.name or ''} {o.description or ''}"):
            o.academic = True
        o.domain = _site_domain(website)
        email = (node.get("email") or "").strip().lower()
        if "@" in email:
            mail_host = email.rsplit("@", 1)[1]
            if _is_academic_host(mail_host):
                o.academic = True
            mail_domain = _site_domain(mail_host)
            if mail_domain and o.domain and (mail_domain == o.domain or o.domain.endswith("." + mail_domain)
                                             or mail_domain.endswith("." + o.domain)):
                o.email_on_domain = True
            if mail_domain and not o.domain:
                o.domain = mail_domain
                o.email_on_domain = True
    return o


def is_established(owner: Owner, today: date) -> bool:
    """An organization that has been on GitHub for years with a large public
    footprint is an incumbent, not something to surface early."""
    if not (owner.is_org and owner.created):
        return False
    return ((today - owner.created).days > _ESTABLISHED_DAYS
            and (owner.public_repos or 0) >= _ESTABLISHED_REPOS)


def entity_domain(repo: Repo, owner: Owner | None) -> str | None:
    """Domain for the entity hint. The owner's own site first; the repo
    homepage only when it visibly belongs to this organization or repo.

    For a user-owned repo only the repo's own name counts: a homepage such as
    yunhaifeng.com/REGRIND/ matches the owner's login, and that makes it the
    author's personal site with a project page on it, not the project's
    domain (it stays on the Person as their website)."""
    if owner and owner.is_org and owner.domain:
        return owner.domain
    home = _site_domain(repo.homepage)
    if not home or _ACADEMIC_TLD.search(home):
        return None
    if repo.owner_type == "Organization":
        names = (repo.owner, repo.name, owner.name if owner else None)
    else:
        names = (repo.name,)
    return home if domain_related(home, *names) else None


def _shorten(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    return text[:n].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"


def _fmt_day(d: date) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def velocity_title(v: dict[str, Any], repo_created: date, asof: date) -> tuple[str, float, str]:
    """(title, headline value, unit) for a star_velocity signal."""
    s7, p7, s30, p30, total = (v["stars_7d"], v["stars_prev_7d"], v["stars_30d"],
                               v["stars_prev_30d"], v["stars_total"])
    age = (asof - repo_created).days
    if age <= 21 and p30 == 0:
        return (f"New repo gained {s30:,} stars since it was created on {_fmt_day(repo_created)}",
                s30, "stars")
    if s7 >= 10 and s7 >= 2 * p7 and s7 >= p7 + 10:
        return f"Stars up {s7:,} in 7 days against {p7:,} the week before", s7, "stars/7d"
    if s30 >= 1.5 * p30 and p30 > 0:
        return (f"Stars up {s30:,} in 30 days against {p30:,} in the 30 days before",
                s30, "stars/30d")
    share = round(100 * s30 / max(total, s30, 1))
    if p30 == 0:
        return f"Added {s30:,} stars in 30 days, {share}% of its {total:,} total", s30, "stars/30d"
    if s7 * 3 < p7 and p7 >= 30:
        return (f"Added {s30:,} stars in 30 days; {s7:,} in the last 7 against {p7:,} the week before",
                s30, "stars/30d")
    return f"Added {s30:,} stars in 30 days, {share}% of its {total:,} total", s30, "stars/30d"


def velocity_date(v: dict[str, Any], unit: str, repo_created: date, today: date) -> date | None:
    """occurred_at for a star_velocity signal, or None if no star is on record.

    The biggest single day inside the window the title talks about: the last
    7 days for a "stars/7d" title, the last 30 otherwise. That is when it
    happened, and unlike "today" it does not drift forward a day with every
    daily run. History days are Pacific calendar days, so the date is
    clamped to the repo's creation date (UTC) and today (the UTC run date).
    """
    when = v.get("peak_date_7d") if unit == "stars/7d" else None
    when = when or v["peak_date"] or v["last_star"]
    if when is None:
        return None
    return min(max(when, repo_created), today)


def new_org_title(org_created: date, repo_name: str, repo_created: date, contributors: str,
                  stars: int) -> str:
    """Title for a new_org_repo signal.

    A repo older than its organization was moved into it (a founder's
    personal repo, or an existing project's new home), so the organization
    "now hosts" it; only a repo created afterwards was "published" by it.
    """
    verb = "published" if repo_created >= org_created else "now hosts"
    tail = f"with {contributors} contributors and {stars:,} stars"
    title = f"Organization created {_fmt_day(org_created)} {verb} {repo_name} {tail}"
    if len(title) > 108:
        title = f"Organization created {_fmt_day(org_created)} {verb} a repo {tail}"
    return title


def new_org_strength(contributors: int, stars: int, org_age_days: int, has_twitter: bool,
                     org_public_repos: int | None, email_on_domain: bool, first_repo: bool) -> float:
    """0..1 read of "a company is forming in public here".

    Every repo that reaches this function already cleared the gate (young
    org, own domain, two or more committers), so the floor is a routine 0.25
    and it takes many committers and real traction to reach the top band.
    """
    s = 0.25
    s += 0.20 * squash(max(contributors - 1, 0), 4)
    s += 0.20 * squash(stars, 400)
    if org_age_days <= 180:
        s += 0.06
    if has_twitter:
        s += 0.03
    if email_on_domain:
        s += 0.03
    if org_public_repos and org_public_repos >= 3:
        s += 0.04
    if first_repo:
        s += 0.03
    return round(_clamp(s, 0.2, 0.92), 3)


# --------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------

def _headers(accept: str = "application/vnd.github+json") -> dict[str, str]:
    h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    tok = github_token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


class _RateLimited(Exception):
    """GitHub has refused this run for rate limits; the call was not made."""


# Circuit breaker. A rate-limited token answers every call with 403.
# antenna.http gives up at once on a long Retry-After, but without this a run
# would still make that doomed call once per search batch and once per repo
# (several hundred). Two refusals in a row and the rest of that API is
# skipped for the run. Reset at the start of collect().
_STRIKES_MAX = 2
_strikes: dict[str, int] = {"graphql": 0, "rest": 0}
_gql_errors: list[str] = []


def _is_rate_limit(e: Exception) -> bool:
    return isinstance(e, http.HttpError) and (
        e.status == 429 or (e.status == 403 and "rate limit" in (e.body or "").lower()))


def _rest(path: str, params: dict | None = None, ttl: float = 6 * 3600) -> Any:
    if _strikes["rest"] >= _STRIKES_MAX:
        raise _RateLimited("REST")
    try:
        out = http.get_json(API + path, params=params, headers=_headers(), ttl=ttl, retries=2)
    except http.HttpError as e:
        if _is_rate_limit(e):
            _strikes["rest"] += 1
        raise
    _strikes["rest"] = 0
    return out


def _gql_once(selections: list[tuple[str, str]], fragment: str, ttl: float,
              salt: str = "") -> dict[str, Any]:
    body = {"query": "query{ " + " ".join(f"{a}: {sel}" for a, sel in selections) + " }" +
                     (" " + fragment if fragment else "") + (f" #{salt}" if salt else "")}
    res = http.post_json(GRAPHQL, body, headers=_headers("application/json"),
                         ttl=ttl, retries=0, timeout=25)
    data = res.get("data") if isinstance(res, dict) else None
    if not isinstance(data, dict):
        raise ValueError(f"GraphQL returned no data: {json.dumps(res)[:200]}")
    # A partial answer (one alias timed out) comes back as HTTP 200 with the
    # alias null and an entry here. A login that no longer exists is routine.
    for err in (res.get("errors") or []) if isinstance(res, dict) else []:
        if isinstance(err, dict) and err.get("type") != "NOT_FOUND" and len(_gql_errors) < 50:
            _gql_errors.append(str(err.get("type") or err.get("message") or "error")[:80])
    return data


def _gql(ctx: Context, selections: list[tuple[str, str]], fragment: str = "",
         ttl: float = 6 * 3600) -> dict[str, Any]:
    """Run aliased selections in one request. The server times out at about
    10 s and answers HTTP 502, so on 502/503/504 or a timeout the batch is
    split in half and each half retried."""
    if not selections:
        return {}
    if _strikes["graphql"] >= _STRIKES_MAX:
        raise _RateLimited("GraphQL")
    err: Exception | None = None
    for attempt in range(3):
        t0 = time.monotonic()
        try:
            try:
                data = _gql_once(selections, fragment, ttl)
            except ValueError:
                # HTTP 200 with no data (a rate-limit or server error in the
                # body). antenna.http has cached that answer; ask once more
                # under a different cache key so a later run is not served
                # the same failure for six hours.
                try:
                    data = _gql_once(selections, fragment, ttl,
                                     salt=f"retry {int(time.time() // 60)}")
                except ValueError:
                    _strikes["graphql"] += 1
                    raise
            _strikes["graphql"] = 0
            # GitHub's secondary limits meter GraphQL by server time, shared
            # by everything using this token; back-to-back slow searches drew
            # an HTTP 403 during development. Rest in proportion to how long
            # the query ran; a cache hit returns instantly and skips it.
            took = time.monotonic() - t0
            if took > 0.2:
                time.sleep(min(4.0, 0.6 * took))
            return data
        except http.HttpError as e:
            err = e
            if e.status in (403, 429):  # secondary rate limit: wait it out, twice at most
                _strikes["graphql"] += 1
                if _strikes["graphql"] >= _STRIKES_MAX:
                    raise
                time.sleep(20.0 * (attempt + 1))
                continue
            if e.status not in (502, 503, 504):
                raise
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            err = e
            break
    if isinstance(err, http.HttpError) and err.status in (403, 429):
        raise err
    if len(selections) == 1:
        try:
            return _gql_once(selections, fragment, ttl)
        except Exception as e:  # noqa: BLE001 - reported, the run continues
            ctx.warn(f"GraphQL selection failed twice ({type(err).__name__}/{type(e).__name__}): "
                     f"{selections[0][1][:70]}")
            return {}
    mid = len(selections) // 2
    out = _gql(ctx, selections[:mid], fragment, ttl)
    out.update(_gql(ctx, selections[mid:], fragment, ttl))
    return out


def attached_contributors(contribs: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    """The contributors worth naming as people on the entity: at most `limit`,
    each with 3 or more commits and either 10 commits or 5% of the lead
    contributor's count. Someone with six commits against a maintainer's two
    hundred sent a few fixes; their employer says nothing about who is
    behind the project."""
    if not contribs:
        return []
    lead = max(c["contributions"] for c in contribs)
    return [c for c in contribs[:limit]
            if c["contributions"] >= 3 and (c["contributions"] >= 10 or c["contributions"] >= 0.05 * lead)]


def _pmap(fn: Callable[[Any], Any], items: list[Any]) -> list[Any]:
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(_WORKERS, len(items))) as pool:
        return list(pool.map(fn, items))


def _search_queries(ctx: Context) -> list[tuple[str, str, str, int]]:
    """[(group, lens, query, first)] in priority order."""
    since = ctx.since.isoformat()
    pushed = ctx.days_ago(14).isoformat()
    old_lo = ctx.days_ago(_OLDER_MAX_AGE_DAYS).isoformat()
    old_hi = (ctx.since - timedelta(days=1)).isoformat()
    fresh = ctx.days_ago(21).isoformat()
    out: list[tuple[str, str, str, int]] = []
    for i, (group, core, min_stars, n_new, n_old) in enumerate(SLICES):
        out.append((group, "new", f"{core} created:>={since} stars:>={min_stars} sort:stars-desc", n_new))
        if n_old:
            out.append((group, "older",
                        f"{core} pushed:>={pushed} created:{old_lo}..{old_hi} stars:60..3000 sort:stars-desc",
                        n_old))
        if i == 6:  # after one slice per sector, the fresh pass
            for g, c in _FRESH_CORES:
                out.append((g, "new", f"{c} created:>={fresh} stars:>=8 sort:stars-desc", 15))
    return out


def discover(ctx: Context, want: int | None) -> tuple[list[Repo], list[Repo]]:
    """Run search slices until `want` gated candidates are in hand (all slices if None).

    Returns (candidates, pending): pending are organization-owned repos that
    did not pass the thesis gate on their own text and get a second look once
    the organization's description is known.
    """
    queries = _search_queries(ctx)
    batches: list[list[tuple[str, str, str, int]]] = []
    cur: list[tuple[str, str, str, int]] = []
    weight = 0
    for q in queries:
        if cur and weight + q[3] > _NODES_PER_REQUEST:
            batches.append(cur)
            cur, weight = [], 0
        cur.append(q)
        weight += q[3]
    if cur:
        batches.append(cur)

    seen: set[str] = set()
    kept: list[Repo] = []
    pending: list[Repo] = []
    dropped: dict[str, int] = {}
    for batch in batches:
        sels = [
            (f"s{i}", f"search(query:{json.dumps(q)}, type:REPOSITORY, first:{first})"
                      "{ repositoryCount nodes{ ...R } }")
            for i, (_, _, q, first) in enumerate(batch)
        ]
        try:
            data = _gql(ctx, sels, _REPO_FRAGMENT)
        except _RateLimited:
            ctx.warn("GitHub GraphQL is rate limited: remaining search batches skipped")
            break
        except Exception as e:  # noqa: BLE001
            ctx.warn(f"search batch failed: {type(e).__name__}: {str(e)[:120]}")
            continue
        for i, (group, lens, _, _) in enumerate(batch):
            result = data.get(f"s{i}")
            nodes = (result.get("nodes") if isinstance(result, dict) else None) or []
            for node in nodes if isinstance(nodes, list) else []:
                try:
                    repo = parse_repo_node(node)
                    if repo is None or repo.full_name.lower() in seen:
                        continue
                    seen.add(repo.full_name.lower())
                    if lens == "new" and repo.created < ctx.since:
                        continue
                    repo.lens, repo.group = lens, group
                    reason = exclusion_reason(repo)
                    gate = None if reason else thesis_gate(repo)
                except Exception as e:  # noqa: BLE001 - one odd node must not lose the batch
                    dropped["unparseable"] = dropped.get("unparseable", 0) + 1
                    if dropped["unparseable"] <= 3:
                        ctx.warn(f"search node skipped: {type(e).__name__}: {str(e)[:80]}")
                    continue
                if reason:
                    dropped[reason] = dropped.get(reason, 0) + 1
                    continue
                if gate is None:
                    if repo.owner_type == "Organization":
                        pending.append(repo)
                    else:
                        dropped["off thesis"] = dropped.get("off thesis", 0) + 1
                    continue
                kept.append(repo)
        if want is not None and len(kept) >= want:
            break
    ctx.log(f"discovery: {len(seen)} repos seen, {len(kept)} candidates, {len(pending)} org repos "
            f"pending a second look; dropped {dropped}")
    return kept, pending


def resolve_owners(ctx: Context, logins: list[str]) -> dict[str, Owner]:
    out: dict[str, Owner] = {}
    uniq = sorted({l for l in logins}, key=str.lower)
    for i in range(0, len(uniq), _OWNERS_PER_REQUEST):
        chunk = uniq[i:i + _OWNERS_PER_REQUEST]
        sels = [(f"o{j}", f"repositoryOwner(login:{json.dumps(l)}){_OWNER_SELECTION}")
                for j, l in enumerate(chunk)]
        try:
            data = _gql(ctx, sels, ttl=24 * 3600)
        except _RateLimited:
            ctx.warn("GitHub GraphQL is rate limited: remaining owner profiles skipped")
            break
        except Exception as e:  # noqa: BLE001
            ctx.warn(f"owner batch failed: {type(e).__name__}: {str(e)[:120]}")
            continue
        for j, l in enumerate(chunk):
            owner = parse_owner(data.get(f"o{j}"))
            if owner:
                out[l.lower()] = owner
    return out


def resolve_users(ctx: Context, logins: list[str], light: bool = False) -> dict[str, dict]:
    """User profiles by login. `light` asks only for what the stargazer check reads."""
    out: dict[str, dict] = {}
    uniq = sorted({l for l in logins if re.fullmatch(r"[A-Za-z0-9\-]+", l or "")}, key=str.lower)
    size = _STARGAZERS_PER_REQUEST if light else _USERS_PER_REQUEST
    selection = _STARGAZER_SELECTION if light else _USER_SELECTION
    for i in range(0, len(uniq), size):
        chunk = uniq[i:i + size]
        sels = [(f"u{j}", f"user(login:{json.dumps(l)}){selection}") for j, l in enumerate(chunk)]
        try:
            data = _gql(ctx, sels, ttl=24 * 3600)
        except _RateLimited:
            ctx.warn("GitHub GraphQL is rate limited: remaining user profiles skipped")
            break
        except Exception as e:  # noqa: BLE001
            ctx.warn(f"user batch failed: {type(e).__name__}: {str(e)[:120]}")
            continue
        for j, l in enumerate(chunk):
            node = data.get(f"u{j}")
            if isinstance(node, dict) and node.get("login"):
                out[l.lower()] = node
    return out


def _fetch_history(ctx: Context, repo: Repo) -> dict[date, int] | None:
    try:
        rows = _rest(f"/repos/{repo.full_name}/stargazers/history", {"per_page": 30})
    except _RateLimited:
        return None  # reported once by collect()
    except Exception as e:  # noqa: BLE001
        ctx.warn(f"star history failed for {repo.full_name}: {type(e).__name__}: {str(e)[:80]}")
        return None
    daily = daily_counts(rows)
    if rows and not daily:  # an empty list is a repo nobody starred lately; this is not
        ctx.warn(f"star history for {repo.full_name} came back in an unexpected shape")
    return daily or None


def _fetch_events(ctx: Context, repo: Repo) -> list[str] | None:
    """Recent stargazer logins, or None when the events could not be fetched
    (the caller then holds the repo back rather than emit it unchecked)."""
    try:
        rows = _rest(f"/repos/{repo.full_name}/events", {"per_page": 100})
    except _RateLimited:
        return None
    except Exception as e:  # noqa: BLE001
        ctx.warn(f"events failed for {repo.full_name}: {type(e).__name__}: {str(e)[:80]}")
        return None
    return stargazer_logins(rows)


def _fetch_contributors(ctx: Context, repo: Repo) -> tuple[list[dict[str, Any]], bool] | None:
    """(human contributors, capped). capped means the page was full, so the
    true count may be higher and titles say "N+"."""
    try:
        rows = _rest(f"/repos/{repo.full_name}/contributors", {"per_page": _CONTRIBUTORS_PAGE},
                     ttl=24 * 3600)
    except _RateLimited:
        return None
    except Exception as e:  # noqa: BLE001
        ctx.warn(f"contributors failed for {repo.full_name}: {type(e).__name__}: {str(e)[:80]}")
        return None
    return human_contributors(rows), isinstance(rows, list) and len(rows) >= _CONTRIBUTORS_PAGE


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def _prior(repo: Repo, owner: Owner | None, today: date) -> float:
    """Cheap ordering before the star history is known."""
    if repo.lens == "new":
        p = repo.stars / max((today - repo.created).days, 3)
    else:
        p = repo.stars / 400.0
    if owner and owner.is_org:
        p *= 1.5
        if owner.domain:
            p *= 1.3
    return p


def _interleave(repos: list[Repo], key: Callable[[Repo], float]) -> list[Repo]:
    """Round-robin across sector groups, best first within each."""
    by: dict[str, list[Repo]] = {}
    for r in sorted(repos, key=key, reverse=True):
        by.setdefault(r.group, []).append(r)
    order = ["robotics", "autonomy", "semis", "manufacturing", "rf", "space", "energy", "defense"]
    groups = [by[g] for g in order if g in by] + [v for g, v in by.items() if g not in order]
    out: list[Repo] = []
    while any(groups):
        for g in groups:
            # robotics is the dense sector: two picks per round
            take = 2 if g and g[0].group == "robotics" else 1
            out.extend(g[:take])
            del g[:take]
    return out


def person_from_user(u: dict, role: str, extra_facts: dict[str, Any] | None = None) -> Person:
    """A GraphQL User node to a Person, with only what the profile states."""
    login = u.get("login") or ""
    name = " ".join((u.get("name") or "").split()) or login
    company = " ".join((u.get("company") or "").split())
    links = {"github": f"https://github.com/{login}"}
    if u.get("twitterUsername"):
        links["twitter"] = f"https://x.com/{u['twitterUsername']}"
    site = (u.get("websiteUrl") or "").strip()
    if site and "." in site and " " not in site:
        links["website"] = site if re.match(r"^https?://", site, re.I) else "https://" + site
    facts: dict[str, Any] = {}
    followers = _count(u.get("followers"))
    if followers is not None:
        facts["followers"] = followers
    bio = " ".join((u.get("bio") or "").split())
    if bio:
        facts["bio"] = bio
    if u.get("location"):
        facts["location"] = " ".join(str(u["location"]).split())
    facts.update(extra_facts or {})
    return Person(name=name, role=role, github=login,
                  affiliations=[company] if company else [], links=links, facts=facts)


# "Hebbian Robotics (YC S26)", "Acme [hiring]": a trailing bracketed tag is a
# batch or a status, not part of the name.
_NAME_TAG = re.compile(r"\s*[\(\[（【][^\)\]）】]{1,24}[\)\]）】]\s*$")


def clean_org_name(name: str) -> str:
    base = _NAME_TAG.sub("", name).strip()
    return base if len(base) >= 3 else name


def project_name(repo: Repo) -> str:
    """Name for a user-owned repo: the repo's own name, or owner/repo when the
    bare name would not identify anything ("-PCB", "firmware", "sim")."""
    bare = repo.name.strip("-_.")
    if bare != repo.name or len(_alnum(bare)) < 4 or bare.lower() in _GENERIC_REPO_NAMES:
        return repo.full_name
    return repo.name


def build_entity(repo: Repo, owner: Owner | None, today: date) -> EntityHint:
    domain = entity_domain(repo, owner)
    is_org = bool(owner and owner.is_org)
    # A paper's code drop on a project site is a project, whoever hosts it.
    is_company = bool(is_org and domain and not is_paper_drop(repo))
    links = {"github": f"https://github.com/{repo.owner}", "repo": repo.url}
    aliases: list[str] = []
    one_liner = _shorten(repo.description, 200) or None
    description = None
    if is_org and owner:
        raw = owner.name or owner.login
        name = clean_org_name(raw)
        if name != raw:
            aliases.append(raw)
        if owner.login.lower() != name.lower():
            aliases.append(owner.login)
        # The resolver merges entities on aliases as well as names, so a repo
        # name ("cyclotron", "hflow") is offered as another name for the
        # organization only when the organization's own domain carries it
        # (FluidCAD at fluidcad.io). Otherwise it could fold an unrelated
        # company of that name into this one.
        if (domain and repo.name.lower() not in _GENERIC_REPO_NAMES and len(repo.name) >= 5
                and repo.name.lower() not in (name.lower(), owner.login.lower())
                and domain_related(domain, repo.name)):
            aliases.append(repo.name)
        if owner.twitter:
            links["twitter"] = f"https://x.com/{owner.twitter}"
        if owner.website and clean_domain(owner.website):
            links["website"] = owner.website
        description = owner.description
    else:
        name = project_name(repo)
    if "website" not in links and domain and repo.homepage and _site_domain(repo.homepage) == domain:
        home = repo.homepage
        links["website"] = home if re.match(r"^https?://", home, re.I) else "https://" + home
    return EntityHint(
        name=name,
        kind="company" if is_company else "project",
        domain=domain,
        github=repo.owner,
        aliases=aliases,
        one_liner=one_liner,
        description=description,
        location=owner.location if owner else None,
        # Never the GitHub organization's creation date: that is when an
        # account was opened, not when a company was founded.
        founded=None,
        links=links,
    )


def signal_text(repo: Repo, owner: Owner | None) -> str:
    """Everything GitHub says about what this repo is, for the shared thesis
    model: full name, description, topics, the language most of the code is
    written in (GitHub's own detection: "SystemVerilog" on a repo is a
    statement that it is hardware description, in a way a README is not),
    and the owning organization's description."""
    return _signal_text(repo, (owner.description or "") if owner and owner.is_org else "")


def _signal_text(repo: Repo, owner_text: str) -> str:
    return f"{repo.full_name}: " + _sentences([
        repo_text(repo),
        f"Mostly written in {repo.language}" if repo.language else None,
        owner_text or None,
    ])


def thesis_metrics(entity_name: str, text: str) -> dict[str, Any]:
    """What the shared keyword model makes of the text this signal carries.

    thesis_anchor_only is 1 when it scores below the 0.3 the rest of the
    pipeline gates on, so the row is on thesis by this module's anchors
    alone and needs a second source before it can reach the board.
    """
    fit = classify(f"{entity_name}. {text}")["fit"]
    return {"thesis_fit": fit, "thesis_anchor_only": 0 if fit >= 0.3 else 1}


@dataclass
class _Work:
    repo: Repo
    owner: Owner | None = None
    daily: dict[date, int] | None = None
    v: dict[str, Any] | None = None
    raw_strength: float = 0.0
    quality: dict[str, Any] | None = None
    contributors: list[dict[str, Any]] | None = None
    contributors_capped: bool = False
    emit_velocity: bool = False


def _wants_velocity(repo: Repo, v: dict[str, Any]) -> bool:
    if repo.lens == "new":
        # A week of 10 stars is enough for an organization's repo. A personal
        # repo needs 20 in the month: below that it is the author's followers
        # ("New repo gained 13 stars" was emitted before this floor).
        if repo.owner_type == "Organization":
            return v["stars_30d"] >= 20 or v["stars_7d"] >= 10
        return v["stars_30d"] >= 20
    # Older repos only count when they are accelerating now.
    return ((v["accel_30d"] >= 1.5 and v["stars_30d"] >= 30)
            or (v["accel_7d"] >= 2.0 and v["stars_7d"] >= 20))


def _high_velocity(w: _Work) -> bool:
    v = w.v or {}
    ratio = w.repo.forks / max(w.repo.stars, 1)
    return (v.get("stars_7d", 0) >= 40 or v.get("stars_30d", 0) >= 150
            or (ratio < 0.01 and v.get("stars_30d", 0) >= 60))


def collect(ctx: Context) -> Iterable[Signal]:
    if not github_token():
        ctx.warn("no GitHub token (GITHUB_TOKEN or gh auth): GraphQL search needs one; nothing collected")
        return
    today = ctx.today
    limit = ctx.limit
    _strikes.update(graphql=0, rest=0)
    del _gql_errors[:]
    budget = dict(_BUDGET)
    if limit:
        budget = {"history": min(budget["history"], 2 * limit + 10),
                  "events": min(budget["events"], limit),
                  "contributors": min(budget["contributors"], limit)}

    # 1-2. Discover and filter.
    repos, pending = discover(ctx, want=(4 * limit + 20) if limit else None)
    if not repos and not pending:
        return

    # 3. Owners for organization-owned candidates (GraphQL, no core calls). Org
    # repos that failed the gate on their own text get a second look with the
    # organization's description.
    pending = sorted(pending, key=lambda r: -r.stars)[:_PENDING_MAX]
    owners = resolve_owners(ctx, [r.owner for r in repos + pending if r.owner_type == "Organization"])
    rescued = 0
    for r in pending:
        o = owners.get(r.owner.lower())
        if o and o.description and thesis_gate(r, o.description):
            repos.append(r)
            rescued += 1
    if rescued:
        ctx.log(f"{rescued} org repos passed the thesis gate on the organization's description")
    kept: list[Repo] = []
    academic = established = 0
    for r in repos:
        o = owners.get(r.owner.lower())
        if o and o.academic:
            academic += 1
            continue
        if o and is_established(o, today):
            established += 1
            continue
        kept.append(r)
    if academic or established:
        ctx.log(f"dropped {academic} repos owned by university or lab organizations and "
                f"{established} owned by long-established organizations")

    # 4. Choose which repos get a star-history call.
    key = lambda r: _prior(r, owners.get(r.owner.lower()), today)  # noqa: E731
    n_old = int(budget["history"] * _OLDER_SHARE)
    new_q = _interleave([r for r in kept if r.lens == "new"], key)
    old_q = _interleave([r for r in kept if r.lens == "older"], key)
    old_pick = old_q[:n_old]
    new_pick = new_q[:budget["history"] - len(old_pick)]
    if len(new_pick) + len(old_pick) < budget["history"]:
        old_pick = old_q[:budget["history"] - len(new_pick)]
    # interleave the two lenses 2:1 so a limited probe sees both
    picked: list[Repo] = []
    a, b = list(new_pick), list(old_pick)
    while a or b:
        picked.extend(a[:2]); del a[:2]
        picked.extend(b[:1]); del b[:1]

    # 5. Star history and velocity.
    work = [_Work(repo=r, owner=owners.get(r.owner.lower())) for r in picked]
    for w, daily in zip(work, _pmap(lambda w: _fetch_history(ctx, w.repo), work)):
        if not daily:
            continue
        w.daily = daily
        w.v = velocity(daily, today, w.repo.stars)
        w.raw_strength = velocity_strength(w.v)
        w.emit_velocity = _wants_velocity(w.repo, w.v)
    ctx.log(f"star history: {sum(1 for w in work if w.daily)} of {len(work)} repos "
            f"({len(new_pick)} new, {len(old_pick)} older); "
            f"{sum(1 for w in work if w.emit_velocity)} with movement")

    # 6. Fake-star check on the high-velocity repos.
    hot = sorted((w for w in work if w.emit_velocity and _high_velocity(w)),
                 key=lambda w: -w.raw_strength)[:budget["events"]]
    actors = _pmap(lambda w: _fetch_events(ctx, w.repo), hot)
    profiles = resolve_users(ctx, [l for ls in actors if ls for l in ls], light=True) if hot else {}
    held_owners: set[str] = set()  # owners with bought-looking stars, or that could not be checked
    unchecked = 0
    for w, logins in zip(hot, actors):
        if logins is None:
            # The check could not run. A high-velocity repo is exactly where
            # bought stars show up, so its owner is held back (both kinds)
            # rather than emitted on trust.
            held_owners.add(w.repo.owner.lower())
            unchecked += 1
            continue
        w.quality = stargazer_quality([profiles[l.lower()] for l in logins if l.lower() in profiles], today)
        if stars_look_bought(w.quality):
            held_owners.add(w.repo.owner.lower())
            ctx.log(f"dropped {w.repo.full_name}: of {w.quality['sg_sample']} recent stargazers "
                    f"{w.quality['sg_pct_age_lt_90d']:.0%} have accounts under 90 days old and "
                    f"{w.quality['sg_pct_zero_followers']:.0%} have no followers")
    if unchecked:
        ctx.warn(f"{unchecked} high-velocity repos held back: their recent stargazers could not be fetched")

    # 7. One flagship repo per owner for each kind.
    def final_strength(w: _Work) -> float:
        return round(w.raw_strength * quality_factor(w.quality), 3)

    best_velocity: dict[str, _Work] = {}
    for w in work:
        # an owner caught with bought stars on one repo is dropped altogether
        if not w.emit_velocity or w.repo.owner.lower() in held_owners:
            continue
        cur = best_velocity.get(w.repo.owner.lower())
        if cur is None or final_strength(w) > final_strength(cur):
            best_velocity[w.repo.owner.lower()] = w
    velocity_work = sorted(best_velocity.values(), key=lambda w: -final_strength(w))

    by_full = {w.repo.full_name.lower(): w for w in work}
    new_org: dict[str, _Work] = {}
    for r in kept:
        o = owners.get(r.owner.lower())
        if not (o and o.is_org and o.created and o.domain):
            continue
        if (today - o.created).days > _NEW_ORG_DAYS or is_paper_drop(r):
            continue  # a paper's code drop is not a company forming
        event = max(r.created, o.created)
        if event < ctx.since or event > today:
            continue
        if r.owner.lower() in held_owners:
            continue
        w = by_full.get(r.full_name.lower()) or _Work(repo=r, owner=o)
        cur = new_org.get(r.owner.lower())
        if cur is None or r.stars > cur.repo.stars:
            new_org[r.owner.lower()] = w
    new_org_work = sorted(new_org.values(), key=lambda w: -w.repo.stars)

    if limit:
        velocity_work = velocity_work[:limit]
        have = {w.repo.owner.lower() for w in velocity_work}
        room = max(limit - len(have), 0)
        new_org_work = ([w for w in new_org_work if w.repo.owner.lower() in have]
                        + [w for w in new_org_work if w.repo.owner.lower() not in have][:room])

    # 8. Contributors: new-org candidates first, then org-owned velocity repos, then the rest.
    need: list[_Work] = []
    for w in new_org_work + [w for w in velocity_work if w.repo.owner_type == "Organization"] \
            + [w for w in velocity_work if w.repo.owner_type != "Organization"]:
        if all(w.repo.full_name != n.repo.full_name for n in need):
            need.append(w)
    need = need[:budget["contributors"]]
    for w, got in zip(need, _pmap(lambda w: _fetch_contributors(ctx, w.repo), need)):
        if got is not None:
            w.contributors, w.contributors_capped = got
    # the velocity signal for the same repo should see the same contributor list
    contrib_by_repo = {w.repo.full_name: w for w in need}
    for w in velocity_work + new_org_work:
        src = contrib_by_repo.get(w.repo.full_name)
        if w.contributors is None and src is not None:
            w.contributors, w.contributors_capped = src.contributors, src.contributors_capped

    # 9. Profiles for repo owners that are users, and for top contributors.
    emitting = velocity_work + new_org_work
    logins = [w.repo.owner for w in emitting if w.repo.owner_type != "Organization"]
    for w in emitting:
        logins.extend(c["login"] for c in attached_contributors(w.contributors or []))
    users = resolve_users(ctx, logins)

    def people_for(w: _Work) -> list[Person]:
        out: list[Person] = []
        seen: set[str] = set()
        contribs = w.contributors or []
        commits = {c["login"].lower(): c["contributions"] for c in contribs}
        if w.repo.owner_type != "Organization":
            u = users.get(w.repo.owner.lower())
            if u:
                facts = {}
                if w.repo.owner.lower() in commits:
                    facts["commits"] = commits[w.repo.owner.lower()]
                out.append(person_from_user(u, "Repository owner", facts))
                seen.add(w.repo.owner.lower())
        for c in attached_contributors(contribs):
            low = c["login"].lower()
            if low in seen or low not in users:
                continue
            seen.add(low)
            out.append(person_from_user(
                users[low], f"Top contributor ({c['contributions']:,} commits)",
                {"commits": c["contributions"]}))
        return out

    # stars_total is the owner's most-starred repo among those discovery saw
    # (the scorer reads it as "how well known is this already"); repo_stars is
    # the repo the signal is about.
    owner_max: dict[str, int] = {}
    for r in kept:
        owner_max[r.owner.lower()] = max(owner_max.get(r.owner.lower(), 0), r.stars)

    def base_metrics(w: _Work) -> dict[str, Any]:
        r, o = w.repo, w.owner
        m: dict[str, Any] = {
            "stars_total": max(r.stars, owner_max.get(r.owner.lower(), 0)),
            "repo_stars": r.stars,
            "forks": r.forks,
            "fork_star_ratio": round(r.forks / max(r.stars, 1), 3),
            "repo_age_days": (today - r.created).days,
            "owner_is_org": 1 if r.owner_type == "Organization" else 0,
        }
        if is_paper_drop(r):
            m["paper_drop"] = 1
        if o and o.is_org:
            if o.created:
                m["org_age_days"] = (today - o.created).days
            if o.public_repos is not None:
                m["org_public_repos"] = o.public_repos
        if w.contributors is not None:
            m["contributors"] = len(w.contributors)  # people, automation removed
            m["contributors_ge5"] = sum(1 for c in w.contributors if c["contributions"] >= 5)
            if w.contributors_capped:
                m["contributors_capped"] = 1
        return m

    # 10. Emit.
    for w in velocity_work:
        try:
            r, v = w.repo, w.v
            assert v is not None and w.daily is not None
            factor = quality_factor(w.quality)
            strength = round(w.raw_strength * factor, 3)
            title, value, unit = velocity_title(v, r.created, today)
            when = velocity_date(v, unit, r.created, today)
            if when is None or when < ctx.since:
                continue
            entity = build_entity(r, w.owner or _user_owner(users, r), today)
            text = signal_text(r, w.owner)
            metrics = base_metrics(w)
            metrics.update(thesis_metrics(entity.name, text))
            # The storage key for this continuous signal: one row per repo.
            metrics["repo"] = r.full_name
            metrics.update({k: v[k] for k in (
                "stars_7d", "stars_prev_7d", "stars_30d", "stars_prev_30d", "accel_7d",
                "accel_30d", "velocity_share_30d", "sustained_weeks", "peak_day", "burstiness")})
            if v["peak_date"]:
                metrics["days_since_peak"] = (today - v["peak_date"]).days
            if v["first_star"] and len(w.daily) < 7 * 30:  # history covers the repo's whole life
                metrics["days_since_first_star"] = (today - v["first_star"]).days
            metrics["lens_older"] = 1 if r.lens == "older" else 0
            if w.quality:
                metrics.update(w.quality)
            yield Signal(
                source=SLUG, family=FAMILY, kind="star_velocity",
                entity=entity,
                title=title,
                occurred_at=when.isoformat(),
                url=r.url,
                value=value, unit=unit, strength=strength,
                metrics=metrics,
                people=people_for(w),
                series=weekly_series(w.daily, today, r.stars, r.created, factor),
                text=text,
            )
        except Exception as e:  # noqa: BLE001 - one bad row must not lose the run
            ctx.warn(f"star_velocity failed for {w.repo.full_name}: {type(e).__name__}: {e}")

    for w in new_org_work:
        try:
            r, o = w.repo, w.owner
            assert o is not None and o.created is not None
            humans = w.contributors
            if humans is None:
                continue  # no contributor data (budget or error): do not claim committers
            committed = [c for c in humans if c["contributions"] >= 3]
            if len(committed) < 2:
                continue
            n = len(humans)
            n_txt = f"{n}+" if w.contributors_capped else str(n)
            org_age = (today - o.created).days
            first_repo = o.public_repos == 1
            strength = new_org_strength(n, r.stars, org_age, bool(o.twitter), o.public_repos,
                                        o.email_on_domain, first_repo)
            strength = round(strength * quality_factor(w.quality), 3)
            entity = build_entity(r, o, today)
            text = signal_text(r, o)
            metrics = base_metrics(w)
            metrics.update(thesis_metrics(entity.name, text))
            metrics.update({"committers_ge3": len(committed), "has_twitter": 1 if o.twitter else 0,
                            "email_on_domain": 1 if o.email_on_domain else 0,
                            "is_verified": 1 if o.is_verified else 0})
            if w.quality:
                metrics.update(w.quality)
            title = new_org_title(o.created, r.name, r.created, n_txt, r.stars)
            yield Signal(
                source=SLUG, family=FAMILY, kind="new_org_repo",
                entity=entity,
                title=title,
                occurred_at=max(r.created, o.created).isoformat(),
                url=r.url,
                value=n, unit="contributors", strength=strength,
                metrics=metrics,
                people=people_for(w),
                text=text,
            )
        except Exception as e:  # noqa: BLE001
            ctx.warn(f"new_org_repo failed for {w.repo.full_name}: {type(e).__name__}: {e}")

    if _strikes["rest"] >= _STRIKES_MAX:
        ctx.warn("GitHub REST is rate limited: remaining star-history, events and contributor calls skipped")
    if _gql_errors:
        kinds = sorted(set(_gql_errors))
        ctx.warn(f"GraphQL answered with {len(_gql_errors)} partial errors ({', '.join(kinds)[:120]}): "
                 "some search slices or profiles may be missing")


def _user_owner(users: dict[str, dict], repo: Repo) -> Owner | None:
    """Owner record for a user-owned repo from the user batch (location only matters)."""
    u = users.get(repo.owner.lower())
    if not u:
        return None
    return parse_owner({**u, "__typename": "User"})
