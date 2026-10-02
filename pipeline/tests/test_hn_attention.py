"""Offline tests for the hn_attention collector.

No network. Three kinds of input:

  * the saved Algolia responses in fixtures/social/ (a 3-hit search and the
    55 Launch HN stories), for hit parsing and key matching;
  * PINNED: Algolia responses for ten real queries as read live on
    2026-10-01, with comment bodies cut to the few words around the match,
    for the numbers the collector actually emitted that day (Skild AI,
    robocurve, Joby, Atomarine) and the traps it has to avoid ("Salem,
    Robotics student", "9 mothers can't make a baby in a month", and
    xgorobot.com, first linked in 2026 by a company whose Kickstarter was a
    story in 2021);
  * hits built by `synthetic()` on a reserved .test domain, only for the
    over-1,000-hits fallback, which no early-stage key reaches.

The tests that drive collect() swap http.get_json for a fake that serves
those responses and fails on any other URL.
"""

from __future__ import annotations

import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from antenna import score
from antenna.collectors import hn_attention as hn
from antenna.collectors.base import Context, clean_domain
from antenna.db import fingerprint

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SEARCH_3 = json.loads((FIXTURES / "social" / "hn_algolia_search_by_date.json").read_text())["hits"]
LAUNCHES = json.loads((FIXTURES / "social" / "hn_algolia_launch_hn_180d.json").read_text())["hits"]
KNOWN_SAMPLE = json.loads((FIXTURES / "known_sample.json").read_text())

TODAY = date(2026, 10, 1)
END_TS = hn.day_ts(TODAY + timedelta(days=1))

PINNED = json.loads(r'''{
 "skild.ai": {"nbHits": 9, "hits": [
  {"objectID": "49822292", "created_at": "2026-09-23T20:43:50Z", "created_at_i": 1790196230, "author": "mostlyk", "title": "Physical Self-Play", "url": "https://www.skild.ai/blogs/physical-self-play", "points": 4, "num_comments": 1, "_tags": ["story", "author_mostlyk", "story_49822292"]},
  {"objectID": "49661434", "created_at": "2026-09-11T16:46:40Z", "created_at_i": 1789145200, "author": "gietema", "title": "Skild crosses $100M ARR within ten months", "url": "https://www.skild.ai/blogs/skild-crosses-100m-arr", "points": 1, "num_comments": 0, "_tags": ["story", "author_gietema", "story_49661434"]},
  {"objectID": "49458245", "created_at": "2026-08-27T01:23:38Z", "created_at_i": 1787793818, "author": "epsteingpt", "title": "S1: In-Context Learning for Robotics", "url": "https://skild.ai/blogs/s1", "points": 2, "num_comments": 1, "_tags": ["story", "author_epsteingpt", "story_49458245"]},
  {"objectID": "46607791", "created_at": "2026-01-13T20:52:48Z", "created_at_i": 1768337568, "author": "famouswaffles", "story_title": "TimeCapsuleLLM: LLM trained only on data from 1800-1875", "_tags": ["comment", "author_famouswaffles", "story_46590280"], "comment_text": "=\"https://www.skild.ai/blogs/omni-bo"},
  {"objectID": "45455059", "created_at": "2025-10-02T20:22:05Z", "created_at_i": 1759436525, "author": "famouswaffles", "story_title": "Why today's humanoids won't learn dexterity", "_tags": ["comment", "author_famouswaffles", "story_45392922"], "comment_text": "=\"https://www.skild.ai/blogs/omni-bo"},
  {"objectID": "45372115", "created_at": "2025-09-25T12:49:15Z", "created_at_i": 1758804555, "author": "d3w1tt", "title": "A body-agnostic robot brain that enables any robot to adapt to damage", "url": "https://www.skild.ai/blogs/omni-bodied", "points": 2, "num_comments": 0, "_tags": ["story", "author_d3w1tt", "story_45372115"]},
  {"objectID": "45367894", "created_at": "2025-09-25T00:46:33Z", "created_at_i": 1758761193, "author": "ricardobeat", "title": "Omni-Bodied Robot Brain", "url": "https://www.skild.ai/blogs/omni-bodied", "points": 2, "num_comments": 0, "_tags": ["story", "author_ricardobeat", "story_45367894"]},
  {"objectID": "44819055", "created_at": "2025-08-06T23:30:33Z", "created_at_i": 1754523033, "author": "BrooklynRage", "title": "One Model, Any Scenario: End-to-End Locomotion from Vision", "url": "https://www.skild.ai/blogs/one-policy-all-scenarios", "points": 1, "num_comments": 0, "_tags": ["story", "author_BrooklynRage", "story_44819055"]},
  {"objectID": "40924705", "created_at": "2024-07-10T08:06:47Z", "created_at_i": 1720598807, "author": "niborgen", "title": "Skild – Announcing our $300M Series A Funding", "url": "https://www.skild.ai/blogs/announcing-our-300m-series-a", "points": 1, "num_comments": 0, "_tags": ["story", "author_niborgen", "story_40924705"]}
 ]},
 "robocurve.org": {"nbHits": 5, "hits": [
  {"objectID": "49818038", "created_at": "2026-09-23T15:56:33Z", "created_at_i": 1790178993, "author": "famouswaffles", "story_title": "GPT-6 Astra has gained the ability to drive a car", "_tags": ["comment", "author_famouswaffles", "story_49817404"], "comment_text": "ttps://openai.robocurve.org/gpt-6-astra/\""},
  {"objectID": "49791720", "created_at": "2026-09-21T18:58:26Z", "created_at_i": 1790017106, "author": "msadowski", "title": "Roboharm: Do frontier robot policies refuse unsafe instructions?", "url": "https://robocurve.org/roboharm/", "points": 60, "num_comments": 23, "_tags": ["story", "author_msadowski", "story_49791720"]},
  {"objectID": "49775120", "created_at": "2026-09-20T12:21:08Z", "created_at_i": 1789906868, "author": "fittingopposite", "title": "RoboHarm: Do Frontier Robot Policies Refuse Unsafe Instructions?", "url": "https://robocurve.org/roboharm/", "points": 2, "num_comments": 0, "_tags": ["story", "author_fittingopposite", "story_49775120"]},
  {"objectID": "49765438", "created_at": "2026-09-19T11:00:32Z", "created_at_i": 1789815632, "author": "jonbaer", "title": "RoboHarm: Do Frontier Robot Policies Refuse Unsafe Instructions?", "url": "https://robocurve.org/roboharm/", "points": 3, "num_comments": 0, "_tags": ["story", "author_jonbaer", "story_49765438"]},
  {"objectID": "49582582", "created_at": "2026-09-06T01:52:45Z", "created_at_i": 1788659565, "author": "Anon84", "title": "GPT-6 Astra on robot arms", "url": "https://openai.robocurve.org/gpt-6-astra/", "points": 242, "num_comments": 190, "_tags": ["story", "author_Anon84", "story_49582582"]}
 ]},
 "atomarine.co": {"nbHits": 1, "hits": [
  {"objectID": "49108039", "created_at": "2026-07-30T10:31:46Z", "created_at_i": 1785407506, "author": "ndr", "title": "Atomarine: Nuclear Data Centers at Sea", "url": "https://atomarine.co/", "points": 42, "num_comments": 77, "_tags": ["story", "author_ndr", "story_49108039"]}
 ]},
 "jobyaviation.com": {"nbHits": 24, "hits": [
  {"objectID": "49868495", "created_at": "2026-09-27T17:00:25Z", "created_at_i": 1790528425, "author": "sssilver", "title": "Joby Completes First-Ever Autonomous Flight Across the United States", "url": "https://www.jobyaviation.com/news/joby-completes-first-ever-fully-autonomous-flight-across-the-united-states", "points": 2, "num_comments": 0, "_tags": ["story", "author_sssilver", "story_49868495"]},
  {"objectID": "49789230", "created_at": "2026-09-21T16:14:23Z", "created_at_i": 1790007263, "author": "geox", "title": "First Autonomous Flight Across the United States", "url": "https://www.jobyaviation.com/news/joby-completes-first-ever-fully-autonomous-flight-across-the-united-states", "points": 7, "num_comments": 2, "_tags": ["story", "author_geox", "story_49789230"]},
  {"objectID": "46117807", "created_at": "2025-12-02T05:10:45Z", "created_at_i": 1764652245, "author": "jobyairtaxi", "story_title": "Ask HN: Who is hiring? (December 2025)", "_tags": ["comment", "author_jobyairtaxi", "story_46108941"], "comment_text": "=\"https://www.jobyaviation.com/careers/\" rel"},
  {"objectID": "45803288", "created_at": "2025-11-03T19:26:49Z", "created_at_i": 1762198009, "author": "jobyairtaxi", "story_title": "Ask HN: Who is hiring? (November 2025)", "_tags": ["comment", "author_jobyairtaxi", "story_45800465"], "comment_text": "=\"https://www.jobyaviation.com/careers/\" rel"},
  {"objectID": "44799076", "created_at": "2025-08-05T15:13:47Z", "created_at_i": 1754406827, "author": "taubek", "title": "Joby to Acquire Blade's Passenger Business, Accelerating Air Taxi", "url": "https://www.jobyaviation.com/news/joby-to-acquire-blade-passenger-business/", "points": 2, "num_comments": 0, "_tags": ["story", "author_taubek", "story_44799076"]},
  {"objectID": "41111523", "created_at": "2024-07-30T17:06:49Z", "created_at_i": 1722359209, "author": "belter", "title": "Joby's hydrogen-electric demonstrator aircraft completed 523-mile flight", "url": "https://www.jobyaviation.com/news/joby-demonstrates-potential-regional-journeys-landmark-hydrogen-electric-flight/", "points": 2, "num_comments": 0, "_tags": ["story", "author_belter", "story_41111523"]},
  {"objectID": "41055937", "created_at": "2024-07-24T11:45:01Z", "created_at_i": 1721821501, "author": "bookofjoe", "title": "Joby completes 523-mile emissions-free hydrogen-electric flight (press release)", "url": "https://www.jobyaviation.com/news/joby-demonstrates-potential-regional-journeys-landmark-hydrogen-electric-flight/", "points": 3, "num_comments": 0, "_tags": ["story", "author_bookofjoe", "story_41055937"]},
  {"objectID": "40962970", "created_at": "2024-07-14T20:17:02Z", "created_at_i": 1720988222, "author": "guerby", "story_title": "[dead]", "_tags": ["comment", "author_guerby", "story_40962966"], "comment_text": "=\"https://www.jobyaviation.com/news/joby-dem"},
  {"objectID": "40958240", "created_at": "2024-07-14T01:39:22Z", "created_at_i": 1720921162, "author": "condiment", "story_title": "A hydrogen-powered air taxi flew 523 miles emitting only water vapor", "_tags": ["comment", "author_condiment", "story_40957658"], "comment_text": "=\"https://www.jobyaviation.com/news/joby-pro"},
  {"objectID": "40953795", "created_at": "2024-07-13T12:48:33Z", "created_at_i": 1720874913, "author": "guerby", "title": "Joby demonstrates emissions-free journeys with 523-mile hydrogen-electric flight", "url": "https://www.jobyaviation.com/news/joby-demonstrates-potential-regional-journeys-landmark-hydrogen-electric-flight/", "points": 2, "num_comments": 1, "_tags": ["story", "author_guerby", "story_40953795"]},
  {"objectID": "39099849", "created_at": "2024-01-23T05:08:57Z", "created_at_i": 1705986537, "author": "JumpCrisscross", "story_title": "NYC-bound flight canceled when passenger notices missing bolts on plane wing", "_tags": ["comment", "author_JumpCrisscross", "story_39093773"], "comment_text": "=\"https://www.jobyaviation.com/news/joby-fli"},
  {"objectID": "38254851", "created_at": "2023-11-13T20:21:11Z", "created_at_i": 1699906871, "author": "aschobel", "title": "Joby Flies Quiet Electric Air Taxi in New York City", "url": "https://www.jobyaviation.com/news/joby-flies-quiet-electric-air-taxi-new-york-city/", "points": 3, "num_comments": 2, "_tags": ["story", "author_aschobel", "story_38254851"]},
  {"objectID": "37670923", "created_at": "2023-09-27T06:27:44Z", "created_at_i": 1695796064, "author": "joahua", "title": "Joby Delivers First EVTOL Aircraft", "url": "https://www.jobyaviation.com/news/joby-delivers-first-evtol-edwards/", "points": 1, "num_comments": 0, "_tags": ["story", "author_joahua", "story_37670923"]},
  {"objectID": "36382569", "created_at": "2023-06-18T17:54:32Z", "created_at_i": 1687110872, "author": "Animats", "story_title": "Hexa Lift: Single person drone", "_tags": ["comment", "author_Animats", "story_36381177"], "comment_text": "=\"https://www.jobyaviation.com/\" rel=\"nofoll"},
  {"objectID": "31521241", "created_at": "2022-05-26T17:41:22Z", "created_at_i": 1653586882, "author": "Czarcasm", "story_title": "Ask HN: Recommend employers with positive social impact", "_tags": ["comment", "author_Czarcasm", "story_31518945"], "comment_text": "=\"https://www.jobyaviation.com/\" rel=\"nofoll"},
  {"objectID": "31493117", "created_at": "2022-05-24T15:09:22Z", "created_at_i": 1653404962, "author": "kkfx", "story_title": "Millions of electric cars are coming. What happens to all the dead batteries?", "_tags": ["comment", "author_kkfx", "story_31467104"], "comment_text": "=\"https://www.jobyaviation.com/\" rel=\"nofoll"},
  {"objectID": "29706437", "created_at": "2021-12-27T20:28:42Z", "created_at_i": 1640636922, "author": "lisper", "title": "Joby Aviation – electric aerial ridesharing", "url": "https://www.jobyaviation.com/", "points": 1, "num_comments": 0, "_tags": ["story", "author_lisper", "story_29706437"]},
  {"objectID": "25107042", "created_at": "2020-11-16T01:45:38Z", "created_at_i": 1605491138, "author": "strbean", "story_title": "Electrified wingsuit from BMW reaches 186MPH on first flight", "_tags": ["comment", "author_strbean", "story_25100580"], "comment_text": "=\"https://www.jobyaviation.com/\" rel=\"nofoll"},
  {"objectID": "23284278", "created_at": "2020-05-23T16:31:56Z", "created_at_i": 1590251516, "author": "ur-whale", "story_title": "Ask HN: What startup/technology is on your 'to watch' list?", "_tags": ["comment", "author_ur-whale", "story_23276456"], "comment_text": "=\"https://www.jobyaviation.com/\" rel=\"nofoll"},
  {"objectID": "16746863", "created_at": "2018-04-03T17:15:52Z", "created_at_i": 1522775752, "author": "jquast", "story_title": "Ask HN: Who is hiring? (April 2018)", "_tags": ["comment", "author_jquast", "story_16735011"], "comment_text": "f=\"http://www.jobyaviation.com/#join-us\" rel"},
  {"objectID": "15388707", "created_at": "2017-10-02T22:59:15Z", "created_at_i": 1506985155, "author": "jquast", "story_title": "Ask HN: Who is hiring? (October 2017)", "_tags": ["comment", "author_jquast", "story_15384262"], "comment_text": "f=\"http://www.jobyaviation.com/careers/?gh_j"},
  {"objectID": "14168691", "created_at": "2017-04-21T19:36:39Z", "created_at_i": 1492803399, "author": "poultron", "story_title": "Lilium – Electric vertical take-⁠off and landing jet", "_tags": ["comment", "author_poultron", "story_14167870"], "comment_text": "f=\"http://www.jobyaviation.com/\" rel=\"nofoll"},
  {"objectID": "9391513", "created_at": "2015-04-16T23:59:39Z", "created_at_i": 1429228779, "author": "nether", "story_title": "World-record electric motor for aircraft", "_tags": ["comment", "author_nether", "story_9391151"], "comment_text": "f=\"http://www.jobyaviation.com/S2/\" rel=\"nof"},
  {"objectID": "8411559", "created_at": "2014-10-05T05:45:14Z", "created_at_i": 1412487914, "author": "kashkhan", "story_title": "Why haven't quadcopters been scaled up yet?", "_tags": ["comment", "author_kashkhan", "story_8411026"], "comment_text": "f=\"http://www.jobyaviation.com/S2/\" rel=\"nof"}
 ]},
 "Salem Robotics": {"nbHits": 5, "hits": [
  {"objectID": "49475672", "created_at": "2026-08-28T07:54:49Z", "created_at_i": 1787903689, "author": "beckford", "story_title": "Launch HN: Salem Robotics (YC S26) – Software for industrial inspection robots", "_tags": ["comment", "author_beckford", "story_49466715"], "comment_text": "e search for \"Salem Robotics\" (and 4 of th"},
  {"objectID": "49467131", "created_at": "2026-08-27T16:09:34Z", "created_at_i": 1787846974, "author": "droidjj", "story_title": "Launch HN: Salem Robotics (YC S26) – Software for industrial inspection robots", "_tags": ["comment", "author_droidjj", "story_49466715"], "comment_text": "Boston, MA :: Salem Robotics : Boston Dyna"},
  {"objectID": "49466715", "created_at": "2026-08-27T15:46:04Z", "created_at_i": 1787845564, "author": "Salem_robotics", "title": "Launch HN: Salem Robotics (YC S26) – Software for industrial inspection robots", "points": 49, "num_comments": 31, "_tags": ["story", "author_Salem_robotics", "story_49466715", "launch_hn"], "story_text": "e founders of Salem Robotics (<a href=\"htt"},
  {"objectID": "21090158", "created_at": "2019-09-27T10:02:55Z", "created_at_i": 1569578575, "author": "passwordreset", "story_title": "Stallman Still Heading the GNU Project", "_tags": ["comment", "author_passwordreset", "story_21088690"], "comment_text": "as \"Salem, Robotics student who st"},
  {"objectID": "20996546", "created_at": "2019-09-17T15:42:49Z", "created_at_i": 1568734969, "author": "atomashpolskiy", "story_title": "Richard M. Stallman resigns", "_tags": ["comment", "author_atomashpolskiy", "story_20990583"], "comment_text": ".<p>Salem, Robotics student who st"}
 ]},
 "Skild AI": {"nbHits": 7, "hits": [
  {"objectID": "49559073", "created_at": "2026-09-04T00:41:29Z", "created_at_i": 1788482489, "author": "ilaksh", "story_title": "GPT-6 Astra", "_tags": ["comment", "author_ilaksh", "story_49554643"], "comment_text": "uch as the recent Skild AI demos."},
  {"objectID": "49513683", "created_at": "2026-08-31T19:10:24Z", "created_at_i": 1788203424, "author": "ilaksh", "story_title": "The safest job from AI may be writing", "_tags": ["comment", "author_ilaksh", "story_49512856"], "comment_text": "xample the recent Skild AI fairly general im"},
  {"objectID": "47484772", "created_at": "2026-03-23T02:17:47Z", "created_at_i": 1774232267, "author": "ilaksh", "story_title": "What young workers are doing to AI-proof themselves", "_tags": ["comment", "author_ilaksh", "story_47480447"], "comment_text": "F;Neo, Figure 03, Skild AI. Also see open pu"},
  {"objectID": "47392686", "created_at": "2026-03-15T22:30:24Z", "created_at_i": 1773613824, "author": "ilaksh", "story_title": "Learning athletic humanoid tennis skills from imperfect human motion data", "_tags": ["comment", "author_ilaksh", "story_47388273"], "comment_text": "F;Neo, Figure 03, Skild AI. Also see open pu"},
  {"objectID": "47216355", "created_at": "2026-03-02T10:58:10Z", "created_at_i": 1772449090, "author": "ajax33", "title": "Ask HN: Billions of dollars in funding, but what's changed for robotics?", "points": 9, "num_comments": 3, "story_text": " and $1B+ rounds, Skild AI raising ~$1.4B, P", "_tags": ["story", "author_ajax33", "story_47216355", "ask_hn"]},
  {"objectID": "46623032", "created_at": "2026-01-14T20:45:08Z", "created_at_i": 1768423508, "author": "visioninmyblood", "title": "Skild AI Raises $1.4B, Now Valued over $14B", "url": "https://www.businesswire.com/news/home/20260114335623/en/Skild-AI-Raises-%241.4B-Now-Valued-Over-%2414B", "points": 1, "num_comments": 0, "_tags": ["story", "author_visioninmyblood", "story_46623032"]},
  {"objectID": "41700418", "created_at": "2024-09-30T18:32:09Z", "created_at_i": 1727721129, "author": "academiclolz", "story_title": "Liquid Foundation Models: Our First Series of Generative AI Models", "_tags": ["comment", "author_academiclolz", "story_41698361"], "comment_text": "g something.<p>5. Skild Ai -&gt; Started by "}
 ]},
 "xgorobot.com": {"nbHits": 1, "hits": [
  {"objectID": "49466605", "created_at": "2026-08-27T15:38:55Z", "created_at_i": 1787845135, "author": "NalNezumi", "story_title": "Microduck", "_tags": ["comment", "author_NalNezumi", "story_49462763"], "comment_text": " href=\"https:&#x2F;&#x2F;shop.xgorobot.com&#x2F;products&#x2F;xgo-rider-"}
 ]},
 "weaverobotics.com": {"nbHits": 7, "hits": [
  {"objectID": "49900781", "created_at": "2026-09-29T21:21:14Z", "created_at_i": 1790716874, "author": "darkwizard42", "story_title": "AI needs $6T in annual revenue to justify data centre boom", "_tags": ["comment", "author_darkwizard42", "story_49898952"], "comment_text": "https:&#x2F;&#x2F;www.weaverobotics.com&#x2F;\">https:&#x2F;&#"},
  {"objectID": "49838556", "created_at": "2026-09-25T00:16:15Z", "created_at_i": 1790295375, "author": "daotoad", "story_title": "The AI Build-Out Is Becoming the Biggest Economic Bet in U.S. History", "_tags": ["comment", "author_daotoad", "story_49833264"], "comment_text": "https:&#x2F;&#x2F;www.weaverobotics.com&#x2F;isaac-0\">https:&"},
  {"objectID": "49539183", "created_at": "2026-09-02T17:00:38Z", "created_at_i": 1788368438, "author": "vessenes", "story_title": "Why humanoid robots won't catch up to human workers any time soon", "_tags": ["comment", "author_vessenes", "story_49535506"], "comment_text": "https:&#x2F;&#x2F;www.weaverobotics.com\">https:&#x2F;&#x2F;ww"},
  {"objectID": "49169071", "created_at": "2026-08-04T13:56:50Z", "created_at_i": 1785851810, "author": "throwawayffffas", "story_title": "Ray Bradbury's \"There Will Come Soft Rains\" is set today (2026-08-04)", "_tags": ["comment", "author_throwawayffffas", "story_49166491"], "comment_text": "https:&#x2F;&#x2F;www.weaverobotics.com&#x2F;\" rel=\"nofollow\""},
  {"objectID": "48752427", "created_at": "2026-07-01T20:07:34Z", "created_at_i": 1782936454, "author": "xpct", "story_title": "Weave Robotics launches Isaac 1, a $7,999 home robot with Fall 2026 deliveries", "_tags": ["comment", "author_xpct", "story_48750989"], "comment_text": "https:&#x2F;&#x2F;www.weaverobotics.com&#x2F;isaac-1\" rel=\"no"},
  {"objectID": "48751813", "created_at": "2026-07-01T19:17:16Z", "created_at_i": 1782933436, "author": "droidjj", "story_title": "Weave Robotics launches Isaac 1, a $7,999 home robot with Fall 2026 deliveries", "_tags": ["comment", "author_droidjj", "story_48750989"], "comment_text": "https:&#x2F;&#x2F;www.weaverobotics.com&#x2F;isaac-1\" rel=\"no"},
  {"objectID": "48750989", "created_at": "2026-07-01T18:12:18Z", "created_at_i": 1782929538, "author": "ryanmerket", "title": "Weave Robotics launches Isaac 1, a $7,999 home robot with Fall 2026 deliveries", "url": "https://www.weaverobotics.com/isaac-1", "points": 234, "num_comments": 383, "_tags": ["story", "author_ryanmerket", "story_48750989"]}
 ]},
 "Weave Robotics": {"nbHits": 4, "hits": [
  {"objectID": "49539183", "created_at": "2026-09-02T17:00:38Z", "created_at_i": 1788368438, "author": "vessenes", "story_title": "Why humanoid robots won't catch up to human workers any time soon", "_tags": ["comment", "author_vessenes", "story_49535506"], "comment_text": "Weave robotics (no affiliation) clai"},
  {"objectID": "48762743", "created_at": "2026-07-02T15:04:04Z", "created_at_i": 1783004644, "author": "joelthelion", "story_title": "Weave Robotics launches Isaac 1, a $7,999 home robot with Fall 2026 deliveries", "_tags": ["comment", "author_joelthelion", "story_48750989"], "comment_text": "ruptly shut down when Weave Robotics decides to pivot?"},
  {"objectID": "48750989", "created_at": "2026-07-01T18:12:18Z", "created_at_i": 1782929538, "author": "ryanmerket", "title": "Weave Robotics launches Isaac 1, a $7,999 home robot with Fall 2026 deliveries", "url": "https://www.weaverobotics.com/isaac-1", "points": 234, "num_comments": 383, "_tags": ["story", "author_ryanmerket", "story_48750989"]},
  {"objectID": "48574715", "created_at": "2026-06-17T18:36:18Z", "created_at_i": 1781721378, "author": "SpicyLemonZest", "story_title": "Only 16 Percent of Americans Think AI Will Have a Positive Impact on Society", "_tags": ["comment", "author_SpicyLemonZest", "story_48573332"], "comment_text": "my way to work today. Weave Robotics launched a laundry he"}
 ]},
 "9 Mothers": {"nbHits": 8, "hits": [
  {"objectID": "49173030", "created_at": "2026-08-04T18:42:00Z", "created_at_i": 1785868920, "author": "giancarlostoro", "story_title": "All of Winona Police Department's Flock cameras cut down and stolen", "_tags": ["comment", "author_giancarlostoro", "story_49171656"], "comment_text": "l for them.<p>9 Mothers.<p><a href=\"h"},
  {"objectID": "48755024", "created_at": "2026-07-02T00:52:42Z", "created_at_i": 1782953562, "author": "dowakin", "story_title": "Ask HN: Who is hiring? (July 2026)", "_tags": ["comment", "author_dowakin", "story_48747976"], "comment_text": "9 Mothers YC P26 | Robo"},
  {"objectID": "48088105", "created_at": "2026-05-10T21:12:56Z", "created_at_i": 1778447576, "author": "delbronski", "story_title": "YC's Biggest Scandals", "_tags": ["comment", "author_delbronski", "story_48085314"], "comment_text": "company named 9 mothers, which sells "},
  {"objectID": "47914102", "created_at": "2026-04-26T20:36:04Z", "created_at_i": 1777235764, "author": "staticshock", "story_title": "AI should elevate your thinking, not replace it", "_tags": ["comment", "author_staticshock", "story_47913650"], "comment_text": " org chart\", \"9 mothers can't make a "},
  {"objectID": "47486037", "created_at": "2026-03-23T06:27:09Z", "created_at_i": 1774247229, "author": "slopinthebag", "story_title": "You are not your job", "_tags": ["comment", "author_slopinthebag", "story_47478548"], "comment_text": "kin to saying 9 mothers can birth a c"},
  {"objectID": "45608076", "created_at": "2025-10-16T17:21:22Z", "created_at_i": 1760635282, "author": "NitpickLawyer", "story_title": "Claude Skills", "_tags": ["comment", "author_NitpickLawyer", "story_45607117"], "comment_text": " month out of 9 mothers\" paradigm. Wh"},
  {"objectID": "44162021", "created_at": "2025-06-02T19:13:28Z", "created_at_i": 1748891608, "author": "ukd1", "story_title": "Ask HN: Who is hiring? (June 2025)", "_tags": ["comment", "author_ukd1", "story_44159528"], "comment_text": "9 Mothers Defense | Emb"},
  {"objectID": "43733435", "created_at": "2025-04-19T01:28:27Z", "created_at_i": 1745026107, "author": "YeGoblynQueenne", "story_title": "I passionately hate hype, especially the AI hype", "_tags": ["comment", "author_YeGoblynQueenne", "story_43732047"], "comment_text": "th, invest in 9 mothers?"}
 ]}
}''')

# The response for "Sudo AI" as fetched on 2026-10-02 (text cut to the words around the match).
SUDO_AI = json.loads(r'''{"nbHits": 4, "hits": [
  {"objectID": "45105574", "created_at": "2025-09-02T16:43:41Z", "created_at_i": 1756831421, "author": "ventali08", "title": "Show HN: Sudo – AI monetization infrastructure for developers", "url": "https://sudoapp.dev/", "points": 4, "num_comments": 0, "story_text": "Sudo — AI monetization infrastructure for developers<p>4 mon", "_tags": ["story", "author_ventali08", "story_45105574", "show_hn"]},
  {"objectID": "38044211", "created_at": "2023-10-27T21:27:28Z", "created_at_i": 1698442048, "author": "ea016", "title": "Zero123: A Single Image to Consistent Multi-View Diffusion Base Model", "url": "https://github.com/SUDO-AI-3D/zero123plus", "points": 4, "num_comments": 1, "_tags": ["story", "author_ea016", "story_38044211"]},
  {"objectID": "38040902", "created_at": "2023-10-27T16:53:33Z", "created_at_i": 1698425613, "author": "smusamashah", "story_title": "DreamCraft3D: Hierarchical 3D Generation with Bootstrapped Diffusion Prior", "_tags": ["comment", "author_smusamashah", "story_38037449"], "comment_text": "href=\"https:&#x2F;&#x2F;github.com&#x2F;SUDO-AI-3D&#x2F;zero123plus\">https:&#x2F;&#x2F;github.com&#x"},
  {"objectID": "16873827", "created_at": "2018-04-19T06:42:26Z", "created_at_i": 1524120146, "author": "grimskin", "story_title": "Lea Kissner's job is making sure Google products protect the privacy of users", "_tags": ["comment", "author_grimskin", "story_16873249"], "comment_text": "d from the recovery of their account by sudo-AI recovery form and all the support they get is repeat"}
]}''')

SKILD = {"name": "Skild AI", "kind": "company", "domain": "skild.ai", "github": None}
ROBOCURVE = {"name": "robocurve", "kind": "project", "domain": "robocurve.org", "github": "robocurve"}
SALEM = {"name": "Salem Robotics", "kind": "company", "domain": None, "github": None}
ATOMARINE = {"name": "Atomarine", "kind": "company", "domain": "atomarine.co", "github": None}
JOBY = {"name": "Joby Aero, Inc.", "kind": "company", "domain": "jobyaviation.com", "github": None}
NINE_MOTHERS = {"name": "9 Mothers", "kind": "company", "domain": None, "github": None}
CASTELION = {"name": "Castelion", "kind": "company", "domain": None, "github": None}
WEAVE = {"name": "Weave Robotics", "kind": "company", "domain": "weaverobotics.com", "github": None}
LUWU = {"name": "LuwuDynamics", "kind": "company", "domain": "xgorobot.com", "github": "LuwuDynamics",
        "links": {"repo": "https://github.com/LuwuDynamics/xgoduck_hardware"}}

RESPONSES = {
    '"skild.ai"': PINNED["skild.ai"],
    '"robocurve.org"': PINNED["robocurve.org"],
    '"Salem Robotics"': PINNED["Salem Robotics"],
    '"atomarine.co"': PINNED["atomarine.co"],
    '"jobyaviation.com"': PINNED["jobyaviation.com"],
    '"9 Mothers"': PINNED["9 Mothers"],
    '"Skild AI"': PINNED["Skild AI"],
    '"xgorobot.com"': PINNED["xgorobot.com"],
    '"weaverobotics.com"': PINNED["weaverobotics.com"],
    '"Weave Robotics"': PINNED["Weave Robotics"],
}


class FakeHN:
    """Stands in for http.get_json: Algolia search, Algolia counts, Firebase items."""

    def __init__(self, responses=None, counts=None, dead=(), fail=()):
        self.responses = dict(RESPONSES if responses is None else responses)
        self.counts = dict(counts or {})
        self.dead = set(dead)
        self.fail = set(fail)
        self.searches: list[str] = []
        self.count_calls: list[tuple[str, str | None]] = []
        self.items: list[str] = []

    def get_json(self, url, params=None, **kw):
        if url.startswith("https://hacker-news.firebaseio.com/v0/item/"):
            item_id = url.rsplit("/", 1)[1].removesuffix(".json")
            self.items.append(item_id)
            if item_id in self.dead:
                return {"id": int(item_id), "dead": True, "type": "story"}
            return {"id": int(item_id), "type": "story"}
        if url != hn.SEARCH_URL:
            raise AssertionError(f"unexpected request to {url}")
        query = params["query"]
        # The request itself is part of the contract (source card gotchas 1-3).
        assert params["typoTolerance"] == "false" and params["advancedSyntax"] == "true"
        assert params["restrictSearchableAttributes"] == "url,title,story_text,comment_text"
        assert params["tags"] == "(story,comment)"
        if query in self.fail:
            raise OSError("connection reset")
        if params["hitsPerPage"] == 0:
            self.count_calls.append((query, params.get("numericFilters")))
            n = self.counts.get((query, params.get("numericFilters")), self.counts.get(query, 0))
            return {"nbHits": n, "hits": []}
        self.searches.append(query)
        return self.responses.get(query, {"nbHits": 0, "hits": []})


def run(known, fake=None, limit=None, lookback=120):
    fake = fake or FakeHN()
    ctx = Context(today=TODAY, lookback_days=lookback, limit=limit, known=known)
    with mock.patch.object(hn.http, "get_json", fake.get_json):
        signals = list(hn.collect(ctx))
    return signals, ctx, fake


def search_page(query: str) -> str:
    """The public Hacker News search page a baseline links to (query is URL-quoted)."""
    return f"https://hn.algolia.com/?dateRange=all&page=0&prefix=false&query={query}&sort=byDate&type=all"


def mentions_of(key: hn.Key, hits: list[dict]) -> list[hn.Mention]:
    found, _, _ = hn.parse_hits(hits, key)
    return sorted(found, key=lambda m: m.ts)


def synthetic(n: int, newest_ts: int, step: int, domain: str = "example-robotics.test") -> list[dict]:
    """n story hits linking `domain`, newest first, `step` seconds apart."""
    return [{
        "objectID": str(900000 + i), "created_at_i": newest_ts - i * step,
        "created_at": "", "author": f"user{i % 300}", "title": f"Post {i}",
        "url": f"https://{domain}/p/{i}", "points": 3, "num_comments": 0,
        "_tags": ["story", f"author_user{i % 300}"],
    } for i in range(n)]


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

class Keys(unittest.TestCase):
    def test_domain_is_preferred_and_no_name_is_added(self):
        self.assertEqual(hn.keys_for(SKILD), [hn.Key("domain", "skild.ai")])
        self.assertEqual(hn.Key("domain", "skild.ai").query, '"skild.ai"')

    def test_every_sample_entity_with_a_domain_is_keyed_by_it(self):
        for k in KNOWN_SAMPLE:
            keys = hn.keys_for(k)
            if k["domain"]:
                self.assertEqual(keys[0], hn.Key("domain", k["domain"]), k["name"])
                self.assertNotIn("name", [x.kind for x in keys], k["name"])

    def test_sample_name_only_entities(self):
        got = {k["name"]: hn.keys_for(k) for k in KNOWN_SAMPLE if not k["domain"]}
        self.assertEqual(got["Salem Robotics"], [hn.Key("name", "Salem Robotics")])
        self.assertEqual(got["Blue Laser Fusion"], [hn.Key("name", "Blue Laser Fusion")])
        self.assertEqual(got["Valstad Shipworks"], [hn.Key("name", "Valstad Shipworks")])
        self.assertEqual(got["9 Mothers"], [hn.Key("name", "9 Mothers")])  # dropped later, on the hits

    def test_company_github_is_the_account(self):
        known = {"name": "Hebbian Robotics", "kind": "company", "domain": None, "github": "Hebbian-Robotics",
                 "links": {"repo": "https://github.com/Hebbian-Robotics/hflow"}}
        self.assertEqual(hn.keys_for(known), [hn.Key("github", "github.com/hebbian-robotics")])

    def test_project_github_is_one_repo(self):
        known = {"name": "titania", "kind": "project", "domain": None, "github": "penberg",
                 "links": {"repo": "https://github.com/penberg/titania/"}}
        self.assertEqual(hn.keys_for(known), [hn.Key("github", "github.com/penberg/titania")])

    def test_project_without_its_repo_link_gets_no_github_key(self):
        # The account alone would count every repo the author ever pushed.
        known = {"name": "titania", "kind": "project", "domain": None, "github": "penberg", "links": {}}
        self.assertEqual(hn.keys_for(known), [])
        other = dict(known, links={"repo": "https://github.com/someone-else/titania"})
        self.assertEqual(hn.keys_for(other), [])

    def test_domain_and_company_github_are_both_used(self):
        known = {"name": "Menlo Research", "kind": "company", "domain": "menlo.ai", "github": "menloresearch"}
        self.assertEqual(hn.keys_for(known),
                         [hn.Key("domain", "menlo.ai"), hn.Key("github", "github.com/menloresearch")])

    def test_single_word_names_are_never_queried(self):
        for name in ("Castelion", "Mantle", "FIBRX INC", "SKYTRAX LLC", "Ergodic LLC", "X-STAR INC.", "Foundation"):
            self.assertEqual(hn.keys_for({"name": name, "domain": None, "github": None}), [], name)

    def test_generic_and_personal_names_are_skipped(self):
        for name in ("GENERAL ROBOTICS TECHNOLOGY, INC.", "Advanced Materials Group", "Applied Energy Systems LLC"):
            self.assertIsNone(hn.name_key(name), name)
        self.assertIsNone(hn.name_key("Jane Doe", kind="person"))
        self.assertIsNone(hn.name_key("Yan Fusion (Shanghai) Technology Co. Ltd"))  # not what anyone writes
        self.assertIsNone(hn.name_key("A B"))

    def test_clean_name(self):
        cases = {
            "Hebbian Robotics (YC S26)": "Hebbian Robotics",
            "Blue Laser Fusion Inc": "Blue Laser Fusion",
            "Deep Fission, Inc": "Deep Fission",
            "Beijing VeloAlpha Technology Co. Ltd": "Beijing VeloAlpha Technology",
            "Proxima Fusion GmbH": "Proxima Fusion",
            "CONFLUENT RF TECHNOLOGIES LLC": "CONFLUENT RF TECHNOLOGIES",
            "The Boring Company": "The Boring Company",
            "Marduk Technologies OÜ": "Marduk Technologies",
            "Skild AI": "Skild AI",
        }
        for raw, want in cases.items():
            self.assertEqual(hn.clean_name(raw), want)

    def test_local_host_list_does_not_repeat_the_core(self):
        for d in sorted(hn._SHARED_HOSTS):
            self.assertEqual(clean_domain(d), d, f"{d} is already rejected by clean_domain")

    def test_shared_and_institutional_hosts_are_not_keys(self):
        for d in ("mit.edu", "cs.stanford.edu", "nasa.gov", "ox.ac.uk", "pages.dev", "github.com", "army.mil"):
            self.assertIsNone(hn.usable_domain(d), d)
        self.assertEqual(hn.usable_domain("https://www.Skild.ai/blogs"), "skild.ai")
        # The shared core now rejects an app platform and everything under it,
        # so no entity arrives with such a domain and none is searched.
        for d in ("acme.pages.dev", "acme.itch.io", "jobs.ashbyhq.com", "kickstarter.com"):
            self.assertIsNone(hn.usable_domain(d), d)
        # So does it reject the national institution hosts it now lists.
        for d in ("iitb.ac.in", "x.gov.uk", "nus.edu.sg", "tech.gov.sg", "auckland.ac.nz", "nrc-cnrc.gc.ca",
                  "hku.edu.hk", "ict.ac.cn"):
            self.assertIsNone(clean_domain(d), d)
            self.assertIsNone(hn.usable_domain(d), d)
        # What the core lets through and this collector still refuses as a key:
        # treaty bodies, the institutions of countries the core does not list,
        # a bare national root, and publishers and site builders whose bare
        # host would count every site on them.
        for d in ("unam.edu.mx", "tau.ac.il", "defesa.gov.br", "nato.int", "gov.uk",
                  "blogspot.com", "techcrunch.com", "ieee.org", "myshopify.com"):
            self.assertIsNotNone(clean_domain(d), d)
            self.assertIsNone(hn.usable_domain(d), d)
        self.assertEqual(hn.usable_domain("acme.blogspot.com"), "acme.blogspot.com")
        self.assertEqual(hn.usable_domain("interlune.space"), "interlune.space")  # "int" only as the TLD
        # With its only domain unusable, an entity is treated as name-only.
        self.assertEqual(hn.keys_for({"name": "Uni Lab", "domain": "mit.edu", "github": None}),
                         [hn.Key("name", "Uni Lab")])
        self.assertEqual(hn.keys_for({"name": "Unilab", "domain": "mit.edu", "github": None}), [])


class Patterns(unittest.TestCase):
    def test_domain_boundaries(self):
        pat = hn.Key("domain", "skild.ai").pattern
        for text in ("https://www.skild.ai/blogs/s1", "see skild.ai.", "blog.skild.ai", "mail jobs@skild.ai now",
                     "HTTPS://SKILD.AI"):
            self.assertTrue(pat.search(text), text)
        for text in ("notskild.ai", "my-skild.ai", "skild.ai.evil.com", "skild.aircraft", "skild ai"):
            self.assertFalse(pat.search(text), text)

    def test_short_tld_does_not_match_a_longer_one(self):
        pat = hn.Key("domain", "reliable.co").pattern
        self.assertTrue(pat.search("https://reliable.co/pricing"))
        self.assertFalse(pat.search("https://reliable.com/pricing"))
        self.assertFalse(pat.search("https://reliable.co.uk/"))

    def test_github_boundaries(self):
        account = hn.Key("github", "github.com/hebbian-robotics").pattern
        self.assertTrue(account.search("https://github.com/Hebbian-Robotics/hflow"))
        self.assertTrue(account.search("at github.com/hebbian-robotics."))
        self.assertFalse(account.search("https://github.com/hebbian-robotics2/x"))
        self.assertFalse(account.search("https://gist.github.com/hebbian-robotics/abc"))
        repo = hn.Key("github", "github.com/penberg/titania").pattern
        self.assertTrue(repo.search("https://github.com/penberg/titania/issues/3"))
        self.assertFalse(repo.search("https://github.com/penberg/titania-old"))
        self.assertFalse(repo.search("https://github.com/penberg/limbo"))

    def test_name_needs_its_capitals(self):
        pat = hn.Key("name", "9 Mothers").pattern
        self.assertTrue(pat.search("Flock? 9 Mothers? This is"))
        self.assertTrue(pat.search("9 MOTHERS | Robotics | Austin"))
        self.assertFalse(pat.search("9 mothers can't make a baby in a month"))
        self.assertFalse(pat.search("19 Mothers"))

    def test_name_does_not_match_across_punctuation(self):
        pat = hn.Key("name", "Salem Robotics").pattern
        self.assertTrue(pat.search("founders of Salem Robotics (https://...)"))
        self.assertTrue(pat.search("Salem  Robotics's demo"))
        self.assertFalse(pat.search("Salem, Robotics student who started"))
        self.assertFalse(pat.search("salem robotics"))
        self.assertFalse(pat.search("Salem Roboticsville"))

    def test_all_caps_source_name_matches_normal_writing(self):
        pat = hn.Key("name", "ARCH SYSTEMS").pattern
        self.assertTrue(pat.search("Arch Systems raised"))
        self.assertTrue(pat.search("ARCH SYSTEMS LLC"))
        self.assertFalse(pat.search("on Arch systems you can"))  # measured live: 18 of 26 hits read "Arch systems"
        self.assertFalse(pat.search("arch systems"))

    def test_lower_case_initial_must_match_exactly(self):
        pat = hn.Key("name", "xLean Robotics").pattern
        self.assertTrue(pat.search("xLean Robotics"))
        self.assertFalse(pat.search("Xlean Robotics"))


# ---------------------------------------------------------------------------
# Parsing the saved fixtures
# ---------------------------------------------------------------------------

class ParseSearchFixture(unittest.TestCase):
    def test_story_cited_by_its_own_url(self):
        key = hn.Key("domain", "war.gov")
        found, generic, other = hn.parse_hits(SEARCH_3, key)
        self.assertEqual((len(found), generic, other), (1, 0, 2))
        m = found[0]
        self.assertEqual(m.id, "49916995")
        self.assertEqual((m.kind, m.author, m.points, m.comments), ("story", "lelandfe", 1, 0))
        self.assertEqual((m.ts, m.created_at), (1790821840, "2026-10-01T02:30:40Z"))
        self.assertTrue(m.in_headline)
        self.assertFalse(m.launch or m.hiring)
        self.assertEqual(m.title, "XTechHumanoid winners advance military exploration of humanoid capabilities")

    def test_a_comment_does_not_inherit_its_thread(self):
        # Comment 49916825 sits under a cnbc.com story about Palmer Luckey; the
        # comment itself cites neither, so neither key matches it.
        self.assertEqual(hn.parse_hits(SEARCH_3, hn.Key("domain", "cnbc.com"))[0], [])
        self.assertEqual(hn.parse_hits(SEARCH_3, hn.Key("name", "Palmer Luckey"))[0], [])

    def test_comment_fields(self):
        comment = next(h for h in SEARCH_3 if h["objectID"] == "49916825")
        m = hn.to_mention(comment, hn.Key("name", "Tesla humanoid"))
        self.assertEqual((m.kind, m.author, m.points, m.comments), ("comment", "fragmede", 0, 0))
        self.assertEqual(m.title, "Elon Musk, Palmer Luckey, Newt Gingrich to Help Pentagon with Warfare Initiative")
        self.assertFalse(m.in_headline)

    def test_name_in_comment_text(self):
        # "Tesla humanoid robots" is in the comment body: capitalised first word, exact second.
        found, generic, _ = hn.parse_hits(SEARCH_3, hn.Key("name", "Tesla humanoid"))
        self.assertEqual([m.id for m in found], ["49916825"])
        self.assertEqual(generic, 0)

    def test_bad_hits_are_dropped_not_fatal(self):
        found, _, other = hn.parse_hits([{"title": "no id", "url": "https://war.gov/x"}, {}], hn.Key("domain", "war.gov"))
        self.assertEqual((found, other), ([], 2))


class ParseLaunchFixture(unittest.TestCase):
    def test_nori_by_domain(self):
        found = mentions_of(hn.Key("domain", "norirobotics.com"), LAUNCHES)
        self.assertEqual(len(found), 1)
        m = found[0]
        self.assertEqual((m.id, m.points, m.comments, m.author), ("49525153", 201, 66, "AntonioLi"))
        self.assertTrue(m.launch and m.in_headline)
        self.assertEqual(m.created_at[:10], "2026-09-01")

    def test_domain_in_post_text_only(self):
        # Salem's Launch HN has no URL; the site is linked inside the post text.
        found = mentions_of(hn.Key("domain", "salemroboticsinc.com"), LAUNCHES)
        self.assertEqual([m.id for m in found], ["49466715"])
        self.assertFalse(found[0].in_headline)

    def test_github_keys(self):
        account = mentions_of(hn.Key("github", "github.com/hebbian-robotics"), LAUNCHES)
        repo = mentions_of(hn.Key("github", "github.com/hebbian-robotics/hflow"), LAUNCHES)
        self.assertEqual([m.id for m in account], ["49510632"])
        self.assertEqual([m.id for m in repo], ["49510632"])
        self.assertEqual(mentions_of(hn.Key("github", "github.com/hebbian-robotics/other"), LAUNCHES), [])

    def test_name_in_title(self):
        found = mentions_of(hn.Key("name", "Rise Reforming"), LAUNCHES)
        self.assertEqual([(m.id, m.points) for m in found], [("49074817", 85)])

    def test_every_launch_story_is_the_makers_own_post(self):
        for hit in LAUNCHES:
            m = hn.to_mention(hit, hn.Key("name", "Launch HN"))
            self.assertTrue(m.launch, hit["title"])
            self.assertEqual(hn.split_attention([m])[0], [])

    def test_weekly_buckets_add_up(self):
        # youtube.com is linked from many of these posts: a mechanical check that
        # every verified hit lands in exactly one week of the right index.
        key = hn.Key("domain", "youtube.com")
        found = mentions_of(key, LAUNCHES)
        self.assertGreater(len(found), 10)
        series = hn.weekly_series(found, END_TS, TODAY, with_strength=False)
        self.assertEqual(len(series), 26)
        self.assertEqual([p["t"] for p in series][-2:], ["2026-09-24", "2026-10-01"])
        self.assertTrue(all("s" not in p for p in series))
        in_window = [m for m in found if m.ts >= END_TS - 26 * hn.WEEK]
        self.assertEqual(sum(p["v"] for p in series), len(in_window))
        for m in in_window:
            w = (END_TS - 1 - m.ts) // hn.WEEK  # 0 = the week ending today
            self.assertGreaterEqual(series[25 - w]["v"], 1)


# ---------------------------------------------------------------------------
# Responses read live on 2026-10-01
# ---------------------------------------------------------------------------

class Skild(unittest.TestCase):
    key = hn.Key("domain", "skild.ai")

    def setUp(self):
        self.mentions = mentions_of(self.key, PINNED["skild.ai"]["hits"])

    def test_all_nine_hits_verify(self):
        found, generic, other = hn.parse_hits(PINNED["skild.ai"]["hits"], self.key)
        self.assertEqual((len(found), generic, other), (9, 0, 0))
        self.assertEqual(self.mentions[0].created_at[:10], "2024-07-10")
        self.assertEqual(sum(1 for m in self.mentions if m.kind == "comment"), 2)

    def test_window(self):
        st = hn.window_stats(self.mentions, END_TS)
        self.assertEqual(st, {"n4": 2, "n8": 1, "authors": 2, "stories": 2, "top_points": 4})
        self.assertEqual(hn.lift(2, 1), 4.0)
        self.assertTrue(hn.is_rise(st))
        self.assertEqual(hn.rise_strength(st), 0.296)
        self.assertEqual(hn.rise_title("domain", st),
                         "Cited 2 times on Hacker News in 4 weeks against 1 in the prior 8, by 2 users")

    def test_series(self):
        series = hn.weekly_series(self.mentions, END_TS, TODAY)
        self.assertEqual(len(series), 26)
        self.assertEqual([p["v"] for p in series][-6:], [1, 0, 0, 1, 1, 0])  # 08-27, 09-11, 09-23
        self.assertEqual(sum(p["v"] for p in series), 3)  # the January comment is older than 26 weeks
        self.assertEqual(series[-1], {"t": "2026-10-01", "v": 0, "s": 0.296})
        # Before the second story of September there was nothing to call a rise.
        self.assertTrue(all(p["s"] == 0.0 for p in series[:-3]))
        self.assertEqual([p["t"] for p in series], sorted(p["t"] for p in series))


class Salem(unittest.TestCase):
    key = hn.Key("name", "Salem Robotics")

    def test_punctuation_split_hits_are_not_mentions(self):
        found, generic, other = hn.parse_hits(PINNED["Salem Robotics"]["hits"], self.key)
        self.assertEqual(sorted(m.id for m in found), ["49466715", "49467131", "49475672"])
        self.assertEqual((generic, other), (0, 2))  # two 2019 "Salem, Robotics student" comments

    def test_launch_post_is_not_attention(self):
        mentions = mentions_of(self.key, PINNED["Salem Robotics"]["hits"])
        others, hiring, own = hn.split_attention(mentions, hn.own_handles(SALEM))
        self.assertEqual(([m.author for m in others], hiring, own), (["droidjj", "beckford"], 0, 1))
        self.assertTrue(mentions[0].launch)

    def test_no_first_mention_and_no_rise(self):
        # The debut was the founders' own Launch HN (hn_launch scores it) and
        # two replies a month ago are not a rise today. What is left is the
        # lifetime total, at strength 0.
        signals, ctx, _ = run([SALEM])
        self.assertEqual([(s.kind, s.strength, s.title) for s in signals],
                         [("hn_baseline", 0.0, "Mentioned 3 times on Hacker News to date")])
        self.assertEqual(signals[0].metrics["hn_own_posts_total"], 1)
        self.assertEqual(signals[0].url, search_page("%22Salem%20Robotics%22"))
        self.assertEqual(ctx.warnings, [])


class OwnPosts(unittest.TestCase):
    def test_handles(self):
        self.assertEqual(hn.own_handles(SALEM), frozenset({"salemrobotics"}))
        self.assertEqual(hn.own_handles({"name": "quackd", "domain": "quackd.org", "github": "rokbenko"}),
                         frozenset({"rokbenko", "quackd"}))
        self.assertEqual(hn.own_handles({"name": "Hub", "domain": "hub.xyz"}), frozenset())  # too short to trust

    def test_account_named_after_the_entity_is_the_entity(self):
        key = hn.Key("github", "github.com/rokbenko/quackd")
        hit = {"objectID": "49507586", "created_at_i": 1788168042, "created_at": "2026-08-31T09:20:42Z",
               "author": "rokbenko", "story_title": "Show HN: Microduck", "_tags": ["comment", "author_rokbenko"],
               "comment_text": '<a href="https://github.com/rokbenko/quackd">https://github.com/rokbenko/quackd</a>'}
        mentions = mentions_of(key, [hit])
        self.assertEqual(len(mentions), 1)
        others, _, own = hn.split_attention(mentions, frozenset({"rokbenko"}))
        self.assertEqual((others, own), ([], 1))

    def test_hiring_threads(self):
        mentions = mentions_of(hn.Key("domain", "jobyaviation.com"), PINNED["jobyaviation.com"]["hits"])
        others, hiring, own = hn.split_attention(mentions, hn.own_handles(JOBY))
        self.assertEqual((len(mentions), hiring, own, len(others)), (24, 4, 0, 20))
        self.assertTrue(all("hiring" in m.title.lower() for m in mentions if m.hiring))


class NineMothers(unittest.TestCase):
    def test_idiom_is_generic_and_company_is_verified(self):
        found, generic, other = hn.parse_hits(PINNED["9 Mothers"]["hits"], hn.Key("name", "9 Mothers"))
        self.assertEqual(sorted(m.id for m in found), ["44162021", "48755024", "49173030"])
        self.assertEqual((generic, other), (5, 0))

    def test_entity_is_skipped(self):
        signals, ctx, fake = run([NINE_MOTHERS])
        self.assertEqual(signals, [])
        self.assertEqual(fake.searches, ['"9 Mothers"'])
        self.assertEqual(ctx.warnings, [])  # a skip is logged, not a failure

    def test_observe_raises_skip(self):
        fake = FakeHN()
        with mock.patch.object(hn.http, "get_json", fake.get_json):
            with self.assertRaises(hn.Skip):
                hn.observe(NINE_MOTHERS, hn.keys_for(NINE_MOTHERS), END_TS)


# ---------------------------------------------------------------------------
# collect()
# ---------------------------------------------------------------------------

class Collect(unittest.TestCase):
    KNOWN = [SKILD, ROBOCURVE, SALEM, ATOMARINE, JOBY, NINE_MOTHERS, CASTELION]

    def setUp(self):
        self.signals, self.ctx, self.fake = run(self.KNOWN)
        self.by_name = {s.entity.name: s for s in self.signals}

    def test_what_is_emitted(self):
        self.assertEqual([(s.entity.name, s.kind) for s in self.signals], [
            ("Skild AI", "hn_attention"),
            ("robocurve", "hn_first_mention"),
            ("Salem Robotics", "hn_baseline"),  # looked up, nothing to say: the total alone
            ("Atomarine", "hn_first_mention"),
            ("Joby Aero, Inc.", "hn_attention"),
        ])  # nothing for 9 Mothers (ambiguous) or Castelion (no usable key)
        self.assertEqual(self.ctx.warnings, [])

    def test_all_valid_and_inside_the_lookback(self):
        for s in self.signals:
            s.validate()
            self.assertEqual((s.source, s.family), ("hn_attention", "social"))
            self.assertGreaterEqual(s.occurred_at[:10], self.ctx.since.isoformat())
            self.assertLessEqual(s.occurred_at[:10], TODAY.isoformat())
            if s.kind == "hn_baseline":
                self.assertTrue(s.url.startswith("https://hn.algolia.com/?"), s.url)
                self.assertEqual(s.strength, 0.0)
            else:
                self.assertTrue(s.url.startswith("https://news.ycombinator.com/item?id="))
                self.assertGreater(s.strength, 0.2)
                self.assertNotIn("observed_only", s.metrics)
            self.assertGreater(s.metrics["hn_mentions_total"], 0)
            self.assertEqual(s.people, [])

    def test_title_rules(self):
        for s in self.signals:
            self.assertLess(len(s.title), 110, s.title)
            self.assertFalse(s.title.endswith("."), s.title)
            self.assertTrue(s.title[0].isupper() and not s.title.startswith(("HN", "Hacker News:")), s.title)
            self.assertRegex(s.title, r"\d")

    def test_skild_rise(self):
        s = self.by_name["Skild AI"]
        self.assertEqual(s.title, "Cited 2 times on Hacker News in 4 weeks against 1 in the prior 8, by 2 users")
        self.assertEqual((s.value, s.unit, s.strength), (2, "mentions/4w", 0.296))
        # Linked and dated by the best story of the window.
        self.assertEqual(s.url, "https://news.ycombinator.com/item?id=49822292")
        self.assertEqual(s.occurred_at, "2026-09-23T20:43:50Z")
        # 9 posts link skild.ai and 7 others write "Skild AI": the lifetime
        # total is the 16 distinct posts, the title counts links only.
        self.assertEqual(s.metrics["hn_mentions_total"], 16)
        self.assertEqual((s.metrics["hn_citations_total"], s.metrics["hn_name_mentions_total"]), (9, 7))
        self.assertNotIn("hn_familiarity_damp", s.metrics)  # 16 lifetime mentions is not a household name
        self.assertEqual(s.metrics["hn_total_basis"], "verified")
        self.assertEqual(s.metrics["hn_first_seen"], "2024-07-10")
        self.assertEqual(s.metrics["hn_item_ids_4w"], ["49661434", "49822292"])
        self.assertEqual((s.metrics["hn_mentions_4w"], s.metrics["hn_mentions_prior_8w"], s.metrics["hn_lift"]),
                         (2, 1, 4.0))
        self.assertEqual(s.series[-1]["s"], s.strength)
        self.assertEqual(s.text, "Physical Self-Play\nSkild crosses $100M ARR within ten months")
        self.assertEqual((s.entity.domain, s.entity.github, s.entity.kind), ("skild.ai", None, "company"))

    def test_robocurve_first_mention(self):
        s = self.by_name["robocurve"]
        self.assertEqual(s.title, "First cited on Hacker News, now 5 citations by 5 users, one a 242-point story")
        self.assertEqual(s.url, "https://news.ycombinator.com/item?id=49582582")
        self.assertEqual(s.occurred_at, "2026-09-06T01:52:45Z")
        self.assertEqual((s.value, s.unit, s.strength), (5, "mentions", 0.759))
        self.assertEqual(s.metrics["hn_mentions_total"], 5)
        self.assertEqual(s.metrics["hn_item_ids"], ["49582582", "49765438", "49775120", "49791720", "49818038"])
        # A point event: the series is for the chart only and carries no strength.
        self.assertTrue(s.series and all("s" not in p for p in s.series))
        self.assertEqual(sum(p["v"] for p in s.series), 5)
        self.assertEqual((s.entity.kind, s.entity.github), ("project", "robocurve"))
        # The debut is the rise: one signal, not two.
        self.assertEqual(sum(1 for x in self.signals if x.entity.name == "robocurve"), 1)

    def test_first_mention_checks_the_name_before_claiming_first(self):
        self.assertIn(('"robocurve"', "created_at_i<1788659565"), self.fake.count_calls)
        self.assertIn(('"Atomarine"', "created_at_i<1785407506"), self.fake.count_calls)

    def test_atomarine_lone_story(self):
        s = self.by_name["Atomarine"]
        self.assertEqual(s.title, "First cited on Hacker News, in a story with 42 points and 77 comments")
        self.assertEqual((s.occurred_at, s.strength, s.value), ("2026-07-30T10:31:46Z", 0.368, 1))

    def test_joby_rise_from_silence(self):
        s = self.by_name["Joby Aero, Inc."]
        self.assertEqual(s.title, "Cited 2 times on Hacker News in 4 weeks against none in the prior 8, by 2 users")
        self.assertEqual(s.url, "https://news.ycombinator.com/item?id=49789230")  # 7 points beats 2
        self.assertEqual(s.occurred_at, "2026-09-21T16:14:23Z")
        self.assertEqual(s.strength, 0.306)
        self.assertEqual((s.metrics["hn_mentions_total"], s.metrics["hn_hiring_posts_total"]), (24, 4))
        self.assertNotIn("hn_lift", s.metrics)
        self.assertEqual(s.metrics["hn_first_seen"], "2014-10-05")

    def test_one_search_per_key_and_none_for_unkeyed(self):
        # One search per key. The two entities keyed by domain that have a
        # searchable name pay one more, for the lifetime total.
        self.assertEqual(self.fake.searches, ['"skild.ai"', '"Skild AI"', '"robocurve.org"', '"Salem Robotics"',
                                              '"atomarine.co"', '"jobyaviation.com"', '"Joby Aero"',
                                              '"9 Mothers"'])
        self.assertNotIn('"Castelion"', self.fake.searches)
        self.assertNotIn('"Atomarine"', self.fake.searches)  # single word: counted before the date, never read

    def test_only_evidence_pages_are_checked(self):
        self.assertEqual(sorted(self.fake.items), ["49108039", "49582582", "49789230", "49822292"])


class CollectEdges(unittest.TestCase):
    def test_earlier_use_of_the_name_cancels_first_and_leaves_the_rise(self):
        fake = FakeHN(counts={'"robocurve"': 3})
        signals, _, _ = run([ROBOCURVE], fake)
        self.assertEqual([s.kind for s in signals], ["hn_attention"])
        s = signals[0]
        self.assertEqual(s.title, "Cited 5 times on Hacker News in 4 weeks against none in the prior 8, "
                                  "by 5 users, top story 242 points")
        self.assertEqual(s.url, "https://news.ycombinator.com/item?id=49582582")
        self.assertEqual(s.series[-1]["s"], s.strength)

    def test_first_mention_outside_lookback_is_not_emitted(self):
        signals, _, _ = run([ATOMARINE], lookback=30)
        self.assertEqual([(s.kind, s.strength, s.title) for s in signals],
                         [("hn_baseline", 0.0, "Mentioned 1 time on Hacker News to date")])

    def test_dead_evidence_means_no_first_mention(self):
        # The one story is dead, so there is no post to link and no event to
        # claim. The count is unchanged and links to the search page instead.
        signals, ctx, _ = run([ATOMARINE], FakeHN(dead={"49108039"}))
        self.assertEqual([(s.kind, s.strength, s.url) for s in signals],
                         [("hn_baseline", 0.0, search_page("%22atomarine.co%22"))])
        self.assertEqual(ctx.warnings, [])

    def test_dead_top_story_falls_to_the_next_post(self):
        signals, _, _ = run([JOBY], FakeHN(dead={"49789230"}))
        self.assertEqual(signals[0].url, "https://news.ycombinator.com/item?id=49868495")
        self.assertEqual(signals[0].occurred_at, "2026-09-27T17:00:25Z")

    def test_limit(self):
        signals, _, fake = run(Collect.KNOWN, limit=2)
        self.assertEqual(fake.searches, ['"skild.ai"', '"Skild AI"', '"robocurve.org"'])
        self.assertEqual(len(signals), 2)

    def test_one_failing_entity_does_not_lose_the_run(self):
        signals, ctx, _ = run([SKILD, ROBOCURVE, JOBY], FakeHN(fail={'"robocurve.org"'}))
        self.assertEqual([s.entity.name for s in signals], ["Skild AI", "Joby Aero, Inc."])
        self.assertEqual(len(ctx.warnings), 1)
        self.assertIn("robocurve", ctx.warnings[0])

    def test_garbage_known_rows(self):
        signals, ctx, fake = run([None, {}, {"name": ""}, {"name": "X", "domain": "not a domain"}, SKILD, SKILD])
        self.assertEqual([s.entity.name for s in signals], ["Skild AI"])
        self.assertEqual(fake.searches, ['"skild.ai"', '"Skild AI"'])  # the duplicate is queried once
        self.assertEqual(ctx.warnings, [])

    def test_empty_known(self):
        self.assertEqual(run([])[0], [])

    def test_asof_ignores_later_posts(self):
        # Run "as of" 2026-09-12: the 09-23 story has not happened yet.
        fake = FakeHN()
        ctx = Context(today=date(2026, 9, 12), known=[SKILD])
        end = hn.day_ts(date(2026, 9, 13))
        late = [h for h in PINNED["skild.ai"]["hits"] if h["created_at_i"] >= end]
        self.assertEqual(len(late), 1)
        fake.responses['"skild.ai"'] = {"nbHits": 8, "hits": [h for h in PINNED["skild.ai"]["hits"]
                                                              if h["created_at_i"] < end]}
        with mock.patch.object(hn.http, "get_json", fake.get_json):
            signals = list(hn.collect(ctx))
        self.assertEqual(signals[0].title,
                         "Cited 2 times on Hacker News in 4 weeks against none in the prior 8, by 2 users")
        self.assertEqual(signals[0].metrics["hn_citations_total"], 8)
        self.assertEqual(signals[0].metrics["hn_mentions_total"], 15)  # and 7 posts that write the name


# ---------------------------------------------------------------------------
# Rise rule and strength scale
# ---------------------------------------------------------------------------

class RiseRule(unittest.TestCase):
    def st(self, n4, n8, authors, top=0):
        return {"n4": n4, "n8": n8, "authors": authors, "stories": 0, "top_points": top}

    def test_needs_two_mentions_from_two_users(self):
        self.assertFalse(hn.is_rise(self.st(1, 0, 1)))
        self.assertFalse(hn.is_rise(self.st(5, 0, 1)))  # one person posting five times
        self.assertTrue(hn.is_rise(self.st(2, 0, 2)))

    def test_needs_the_weekly_rate_to_be_up_by_half(self):
        self.assertTrue(hn.is_rise(self.st(3, 4, 3)))  # 0.75 a week against 0.5: lift 1.5
        self.assertFalse(hn.is_rise(self.st(3, 5, 3)))  # lift 1.2
        self.assertFalse(hn.is_rise(self.st(4, 8, 4)))  # flat
        self.assertEqual(hn.lift(3, 4), 1.5)
        self.assertIsNone(hn.lift(3, 0))

    def test_scale(self):
        weakest = hn.rise_strength(self.st(2, 3, 2))
        self.assertEqual(weakest, 0.23)
        self.assertEqual(hn.first_strength(1, 0), 0.25)
        self.assertGreater(hn.first_strength(1, 1), weakest)
        # More users, more points, more lift: never weaker.
        self.assertLess(hn.rise_strength(self.st(4, 0, 2)), hn.rise_strength(self.st(4, 0, 4)))
        self.assertLess(hn.rise_strength(self.st(4, 0, 4)), hn.rise_strength(self.st(4, 0, 4, top=150)))
        self.assertLess(hn.rise_strength(self.st(4, 4, 4)), hn.rise_strength(self.st(8, 4, 4)))
        self.assertLess(hn.first_strength(2, 0), hn.first_strength(2, 300))
        # Bands: a handful of users is solid, a front-page story is notable, nothing passes the cap.
        self.assertTrue(0.35 <= hn.rise_strength(self.st(4, 0, 4)) <= 0.55)
        self.assertTrue(0.6 <= hn.rise_strength(self.st(9, 0, 9)) <= 0.8)
        self.assertTrue(0.6 <= hn.first_strength(5, 242) <= 0.8)
        self.assertTrue(0.85 <= hn.first_strength(10, 500) <= hn.STRENGTH_CAP)  # rare: a front-page debut
        self.assertEqual(hn.rise_strength(self.st(900, 0, 600, top=5000)), hn.STRENGTH_CAP)
        self.assertTrue(0.9 <= hn.first_strength(600, 5000) <= hn.STRENGTH_CAP)

    def test_titles(self):
        self.assertEqual(hn.rise_title("name", self.st(3, 0, 3)),
                         "Named 3 times on Hacker News in 4 weeks against none in the prior 8, by 3 users")
        self.assertEqual(hn.rise_title("github", self.st(12, 5, 9, top=618)),
                         "Cited 12 times on Hacker News in 4 weeks against 5 in the prior 8, by 9 users, "
                         "top story 618 points")
        self.assertNotIn("users", hn.rise_title("domain", self.st(593, 40, 298), users_known=False))
        comment = hn.Mention("1", 0, "", "comment", "a", 0, 0, "", False, False, False)
        story = hn.Mention("2", 0, "", "story", "a", 1, 1, "", False, False, True)
        self.assertEqual(hn.first_title("name", comment, 1, 1, 0), "First named on Hacker News, in 1 comment so far")
        self.assertEqual(hn.first_title("domain", story, 1, 1, 1),
                         "First cited on Hacker News, in a story with 1 point and 1 comment")
        self.assertEqual(hn.first_title("name", story, 3, 2, 9), "First named on Hacker News, now 3 mentions by 2 users")
        self.assertEqual(hn.first_title("github", story, 4, 4, 480),
                         "First cited on Hacker News, now 4 citations by 4 users, one a 480-point story")
        self.assertLess(len(hn.first_title("domain", story, 99999, 99999, 99999)), 110)
        for n4 in (2, 20, 2000):
            self.assertLess(len(hn.rise_title("domain", self.st(n4, 99999, n4, top=99999))), 110)


# ---------------------------------------------------------------------------
# Keys with more hits than one response can carry (synthetic)
# ---------------------------------------------------------------------------

class BigKeys(unittest.TestCase):
    KNOWN = {"name": "Example Robotics", "kind": "company", "domain": "example-robotics.test", "github": None}
    Q = '"example-robotics.test"'

    def test_sample_that_covers_the_window_is_bucketed_exactly(self):
        # 1,000 newest hits spread over 30 weeks; the index says 5,000 in all.
        hits = synthetic(1000, END_TS - 60, 30 * hn.WEEK // 1000)
        fake = FakeHN(responses={self.Q: {"nbHits": 5000, "hits": hits}})
        with mock.patch.object(hn.http, "get_json", fake.get_json):
            obs = hn.observe(self.KNOWN, hn.keys_for(self.KNOWN), END_TS)
        self.assertEqual((obs.total, obs.total_basis), (5000, "index_count"))
        self.assertIsNone(obs.counts)
        self.assertEqual(obs.covered_since, hits[-1]["created_at_i"] + 1)
        self.assertEqual(fake.count_calls, [])
        series = hn.weekly_series(obs.mentions, END_TS, TODAY, covered_since=obs.covered_since)
        self.assertEqual(len(series), 26)
        # Flat attention: the same count every week and never a rise.
        self.assertTrue(all(33 <= p["v"] <= 34 for p in series))
        self.assertTrue(all(p.get("s", 0.0) == 0.0 for p in series))
        self.assertTrue(all("s" not in p for p in series[:8]))  # 12-week look-back not covered there
        signals, _, fake = run([self.KNOWN], FakeHN(responses={self.Q: {"nbHits": 5000, "hits": hits}}))
        # No first mention can be claimed and nothing rose. The total is the
        # index's own count, and the title says it is not exact.
        self.assertEqual([(s.kind, s.strength, s.title) for s in signals],
                         [("hn_baseline", 0.0, "Mentioned about 5000 times on Hacker News to date")])
        s = signals[0]
        s.validate()
        self.assertEqual((s.metrics["hn_mentions_total"], s.metrics["hn_total_basis"]), (5000, "index_count"))
        self.assertNotIn("hn_item_ids_latest", s.metrics)
        self.assertEqual(fake.searches, [self.Q])  # the name cannot widen a count that was not read

    def test_count_only_fallback(self):
        # 1,000 hits inside the last 3 weeks: weekly counts must come from the index.
        hits = synthetic(1000, END_TS - 60, 3 * hn.WEEK // 1000)
        bounds = lambda w0, w1: f"created_at_i>={END_TS - w1 * hn.WEEK},created_at_i<{END_TS - w0 * hn.WEEK}"  # noqa: E731
        weekly = [340, 330, 330, 120, 40, 30, 30, 20]
        counts = {(self.Q, bounds(w, w + 1)): n for w, n in enumerate(weekly)}
        counts[(self.Q, bounds(8, 12))] = 100
        counts[(self.Q, bounds(12, 26))] = 400
        fake = FakeHN(responses={self.Q: {"nbHits": 9000, "hits": hits}}, counts=counts)
        signals, ctx, fake = run([self.KNOWN], fake)
        self.assertEqual(len(fake.count_calls), 10)  # 8 weekly windows and two older buckets
        self.assertEqual(ctx.warnings, [])
        self.assertEqual(len(signals), 1)
        s = signals[0]
        s.validate()
        self.assertEqual(s.kind, "hn_attention")
        # 1,120 in 4 weeks against 120 + 100 in the prior 8; users are not stated
        # because the readable sample stops short of the 4 weeks.
        self.assertEqual(s.title, "Cited 1120 times on Hacker News in 4 weeks against 220 in the prior 8")
        self.assertEqual((s.metrics["hn_mentions_total"], s.metrics["hn_total_basis"]), (9000, "index_count"))
        self.assertEqual(s.metrics["hn_counts_basis"], "index_count")
        self.assertEqual(s.metrics["hn_mentions_weeks_13_26"], 400)
        self.assertNotIn("hn_first_seen", s.metrics)
        self.assertEqual([p["v"] for p in s.series], weekly[::-1])
        self.assertEqual(s.series[-1], {"t": "2026-10-01", "v": 340, "s": s.strength})
        # 9,000 lifetime hits is a household name: the rise is scaled down.
        self.assertEqual(s.metrics["hn_familiarity_damp"], round(hn.familiarity_damp(9000), 3))
        self.assertLess(s.strength, 0.5)
        self.assertTrue(all("s" not in p for p in s.series[:-1]))

    def test_unverifiable_big_key_is_skipped(self):
        hits = synthetic(1000, END_TS - 60, 600)
        for h in hits[::2]:
            h["url"] = "https://example-robotics.testing.example/x"  # the index matched something else
        fake = FakeHN(responses={self.Q: {"nbHits": 9000, "hits": hits}})
        signals, ctx, fake = run([self.KNOWN], fake)
        self.assertEqual(signals, [])
        self.assertEqual(fake.count_calls, [])
        self.assertEqual(ctx.warnings, [])


# ---------------------------------------------------------------------------
# "First" means first; totals cover the name; dates come from the post
# ---------------------------------------------------------------------------

class FirstMeansFirst(unittest.TestCase):
    def test_terms_searched_before_the_date(self):
        # Name, domain label, company GitHub login; duplicates collapse.
        self.assertEqual(hn.earlier_terms(LUWU), ["LuwuDynamics", "xgorobot"])
        self.assertEqual(hn.earlier_terms(ATOMARINE), ["Atomarine"])
        self.assertEqual(hn.earlier_terms({"name": "Menlo Research", "kind": "company", "domain": "menlo.ai",
                                           "github": "menloresearch"}),
                         ["Menlo Research", "menlo", "menloresearch"])
        # A project's owner is a person with a history of their own.
        self.assertEqual(hn.earlier_terms({"name": "titania", "kind": "project", "domain": None, "github": "penberg"}),
                         ["titania"])
        # An institutional host is nobody's own label; a 3-letter name cannot establish anything.
        self.assertEqual(hn.earlier_terms({"name": "Uni Lab", "domain": "mit.edu"}), ["Uni Lab"])
        self.assertIsNone(hn.earlier_terms({"name": "Hub", "domain": "hub.xyz"}))

    def test_first_link_is_emitted_when_nothing_came_before(self):
        signals, ctx, fake = run([LUWU])
        self.assertEqual(fake.searches, ['"xgorobot.com"', '"github.com/luwudynamics"'])
        self.assertEqual([q for q, _ in fake.count_calls], ['"LuwuDynamics"', '"xgorobot"'])
        self.assertTrue(all(f == "created_at_i<1787845135" for _, f in fake.count_calls))
        self.assertEqual([(s.kind, s.title, s.occurred_at) for s in signals],
                         [("hn_first_mention", "First cited on Hacker News, in 1 comment so far",
                           "2026-08-27T15:38:55Z")])
        self.assertEqual(ctx.warnings, [])

    def test_domain_label_seen_years_earlier_cancels_first(self):
        # Live 2026-10-01: item 28092763 (2021-08-06) is a story linking
        # kickstarter.com/projects/xgorobot/..., the same company's product.
        signals, ctx, fake = run([LUWU], FakeHN(counts={'"xgorobot"': 1}))
        self.assertEqual([(s.kind, s.strength, s.title) for s in signals],
                         [("hn_baseline", 0.0, "Mentioned 1 time on Hacker News to date")])
        self.assertIn(('"xgorobot"', "created_at_i<1787845135"), fake.count_calls)
        self.assertEqual(fake.items, [])  # nothing to link, so no evidence page was fetched
        self.assertEqual(ctx.warnings, [])

    def test_company_named_before_it_was_linked_is_not_a_first(self):
        # "Weave Robotics launched a laundry helper" was written on 2026-06-17,
        # two weeks before anyone linked weaverobotics.com.
        signals, ctx, fake = run([WEAVE])
        self.assertEqual([s.kind for s in signals], ["hn_attention"])
        self.assertEqual(fake.count_calls, [])  # settled by the name hits already read
        s = signals[0]
        self.assertEqual(s.title, "Cited 2 times on Hacker News in 4 weeks against 2 in the prior 8, by 2 users")
        self.assertEqual(s.occurred_at, "2026-09-29T21:21:14Z")
        self.assertEqual(s.url, "https://news.ycombinator.com/item?id=49900781")
        # 7 posts link the site; 3 write the name with its capitals, one of them
        # the launch story already counted; "Weave robotics" is not the name.
        self.assertEqual((s.metrics["hn_citations_total"], s.metrics["hn_name_mentions_total"],
                          s.metrics["hn_mentions_total"]), (7, 3, 9))
        self.assertEqual(s.metrics["hn_first_seen"], "2026-06-17")
        self.assertEqual(s.metrics["hn_item_ids_4w"], ["49838556", "49900781"])

    def test_same_site_under_a_placeholder_name_still_claims_only_the_link(self):
        # With no real name to search, the title says what was checked: cited.
        signals, _, fake = run([{"name": "weaverobotics.com", "kind": "company", "domain": "weaverobotics.com"}])
        self.assertEqual([s.kind for s in signals], ["hn_first_mention", "hn_attention"])
        self.assertTrue(signals[0].title.startswith("First cited on Hacker News, now 7 citations by 7 users"))
        self.assertEqual([q for q, _ in fake.count_calls], ['"weaverobotics.com"', '"weaverobotics"'])
        self.assertNotIn('"Weave Robotics"', fake.searches)


class LifetimeTotal(unittest.TestCase):
    def test_name_is_read_once_for_every_entity_keyed_by_domain(self):
        # Every signal states the same total, a baseline included, so the name
        # is read for each entity that has one worth searching: not for a
        # single word (Atomarine), not twice for a name key (Salem Robotics).
        signals, _, fake = run([ATOMARINE, SALEM, {"name": "Charge Robotics", "domain": "chargerobotics.com"}])
        self.assertEqual(fake.searches, ['"atomarine.co"', '"Salem Robotics"', '"chargerobotics.com"',
                                         '"Charge Robotics"'])
        # Neither search found Charge Robotics (in this fake): its total is a stated 0.
        self.assertEqual([(s.entity.name, s.metrics["hn_mentions_total"]) for s in signals],
                         [("Atomarine", 1), ("Salem Robotics", 3), ("Charge Robotics", 0)])

    def test_name_is_not_searched_twice_when_a_first_mention_is_cancelled(self):
        # Weave was written about by name before it was linked: the name is
        # read to settle "first", and the signal that follows reuses it.
        _, _, fake = run([WEAVE])
        self.assertEqual(fake.searches, ['"weaverobotics.com"', '"Weave Robotics"'])

    def test_ordinary_english_name_adds_nothing(self):
        fake = FakeHN()
        with mock.patch.object(hn.http, "get_json", fake.get_json):
            self.assertEqual(hn.name_mentions({"name": "9 Mothers"}, END_TS), [])  # 5 of 8 are the idiom
            self.assertEqual(len(hn.name_mentions({"name": "Skild AI"}, END_TS)), 7)
            self.assertEqual(hn.name_mentions({"name": "Castelion"}, END_TS), [])  # single word: not searched
            self.assertEqual(hn.name_mentions({"name": "Jane Doe", "kind": "person"}, END_TS), [])
        self.assertEqual(fake.searches, ['"9 Mothers"', '"Skild AI"'])

    def test_name_with_more_hits_than_can_be_read_adds_nothing(self):
        fake = FakeHN(responses={'"Skild AI"': {"nbHits": 4000, "hits": PINNED["Skild AI"]["hits"]}})
        with mock.patch.object(hn.http, "get_json", fake.get_json):
            self.assertEqual(hn.name_mentions({"name": "Skild AI"}, END_TS), [])

    def test_failed_name_search_keeps_the_signal_and_says_so(self):
        signals, ctx, _ = run([SKILD], FakeHN(fail={'"Skild AI"'}))
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].metrics["hn_mentions_total"], 9)
        self.assertNotIn("hn_name_mentions_total", signals[0].metrics)
        self.assertEqual(len(ctx.warnings), 1)
        self.assertIn("citations only", ctx.warnings[0])


def before(query: str, cutoff: str) -> dict:
    """A pinned response as it stood before `cutoff` (an ISO date)."""
    hits = [h for h in PINNED[query]["hits"] if h["created_at"] < cutoff]
    return {"nbHits": len(hits), "hits": hits}


class Baseline(unittest.TestCase):
    """An entity that was looked up and has no rise and no first mention still
    reports its lifetime total, at strength 0, so the scorer is not blind to it."""

    # Joby without its two September 2026 stories: 22 citations since 2014, none recent.
    FLAT_JOBY = {'"jobyaviation.com"': before("jobyaviation.com", "2026-01-01")}

    def joby(self):
        signals, ctx, fake = run([JOBY], FakeHN(responses=self.FLAT_JOBY))
        self.assertEqual(ctx.warnings, [])
        self.assertEqual(len(signals), 1)
        return signals[0], fake

    def test_flat_entity_reports_its_total_and_nothing_else(self):
        s, fake = self.joby()
        s.validate()
        self.assertEqual((s.source, s.family, s.kind), ("hn_attention", "social", "hn_baseline"))
        self.assertEqual(s.title, "Mentioned 22 times on Hacker News to date")
        self.assertEqual((s.value, s.unit, s.strength), (22, "mentions", 0.0))
        self.assertEqual(s.occurred_at, "2026-10-01")  # no date of its own: the day it was read
        self.assertEqual(s.metrics["hn_mentions_total"], 22)
        self.assertIs(s.metrics["observed_only"], True)
        self.assertEqual((s.metrics["hn_total_basis"], s.metrics["hn_citations_total"],
                          s.metrics["hn_hiring_posts_total"], s.metrics["hn_first_seen"]),
                         ("verified", 22, 4, "2014-10-05"))
        self.assertEqual((s.series, s.text, s.people), ([], None, []))
        self.assertEqual((s.entity.name, s.entity.domain), ("Joby Aero, Inc.", "jobyaviation.com"))
        # The ids behind the count, newest first, so it can be checked post by post.
        self.assertEqual(len(s.metrics["hn_item_ids_latest"]), 22)
        self.assertEqual(s.metrics["hn_item_ids_latest"][0], "46117807")
        # Nothing was claimed about any one post, so none was looked up.
        self.assertEqual(fake.items, [])
        self.assertEqual(fake.searches, ['"jobyaviation.com"', '"Joby Aero"'])

    def test_evidence_is_the_public_search_page_for_the_query(self):
        s, _ = self.joby()
        self.assertEqual(s.url, search_page("%22jobyaviation.com%22"))
        self.assertEqual(s.url, "https://hn.algolia.com/?dateRange=all&page=0&prefix=false"
                                "&query=%22jobyaviation.com%22&sort=byDate&type=all")

    def test_total_is_the_union_with_the_name_like_every_other_kind(self):
        # skild.ai without its two September stories: 7 links, and 7 other posts write "Skild AI".
        fake = FakeHN(responses={'"skild.ai"': before("skild.ai", "2026-09-01"), '"Skild AI"': PINNED["Skild AI"]})
        signals, _, _ = run([SKILD], fake)
        self.assertEqual([s.kind for s in signals], ["hn_baseline"])
        s = signals[0]
        self.assertEqual(s.title, "Mentioned 14 times on Hacker News to date")
        self.assertEqual((s.metrics["hn_mentions_total"], s.metrics["hn_citations_total"],
                          s.metrics["hn_name_mentions_total"]), (14, 7, 7))
        self.assertEqual(s.url, search_page("%22skild.ai%22"))  # a tie goes to the exact key

    def test_entity_never_linked_but_written_about_by_name(self):
        # No post links the (reserved) domain; three write "Weave Robotics".
        known = {"name": "Weave Robotics", "kind": "company", "domain": "weave-robotics.test", "github": None}
        signals, ctx, fake = run([known])
        self.assertEqual(fake.searches, ['"weave-robotics.test"', '"Weave Robotics"'])
        self.assertEqual([(s.kind, s.title) for s in signals],
                         [("hn_baseline", "Mentioned 3 times on Hacker News to date")])
        s = signals[0]
        self.assertEqual((s.metrics["hn_citations_total"], s.metrics["hn_name_mentions_total"],
                          s.metrics["hn_first_seen"]), (0, 3, "2026-06-17"))
        # The page that shows the three posts is the name search, not the empty domain search.
        self.assertEqual(s.url, search_page("%22Weave%20Robotics%22"))
        self.assertEqual(ctx.warnings, [])

    def test_no_mentions_is_a_reading_of_zero(self):
        # Searched and found nothing is not the same as never measured: the
        # zero is emitted, linked to the search that came back empty.
        signals, ctx, fake = run([{"name": "Charge Robotics", "domain": "chargerobotics.com"},
                                  {"name": "Valstad Shipworks", "domain": None}])
        self.assertEqual(fake.searches, ['"chargerobotics.com"', '"Charge Robotics"', '"Valstad Shipworks"'])
        self.assertEqual([s.entity.name for s in signals], ["Charge Robotics", "Valstad Shipworks"])
        for s in signals:
            s.validate()
            self.assertEqual((s.source, s.family, s.kind), ("hn_attention", "social", "hn_baseline"))
            self.assertEqual(s.title, "No mentions on Hacker News to date")
            self.assertEqual((s.value, s.unit, s.strength), (0, "mentions", 0.0))
            self.assertEqual(s.metrics, {"hn_mentions_total": 0, "observed_only": True})
            self.assertEqual(s.occurred_at, "2026-10-01")
            self.assertEqual((s.series, s.text, s.people), ([], None, []))
        # The query that was made: the exact key, not the name read after it.
        self.assertEqual(signals[0].url, search_page("%22chargerobotics.com%22"))
        self.assertEqual(signals[1].url, search_page("%22Valstad%20Shipworks%22"))
        self.assertEqual((signals[0].entity.domain, signals[1].entity.domain), ("chargerobotics.com", None))
        self.assertEqual(fake.items, [])  # no post to look up
        self.assertEqual(ctx.warnings, [])

    def test_hits_that_do_not_verify_are_a_zero_not_a_count(self):
        # The index answers "Salem Robotics" with two 2019 comments that read
        # "Salem, Robotics student". Neither is the company: the total is 0.
        split = [h for h in PINNED["Salem Robotics"]["hits"] if h["created_at"] < "2020-01-01"]
        signals, ctx, _ = run([SALEM], FakeHN(responses={'"Salem Robotics"': {"nbHits": 2, "hits": split}}))
        self.assertEqual([(s.kind, s.title, s.value) for s in signals],
                         [("hn_baseline", "No mentions on Hacker News to date", 0)])
        self.assertEqual(ctx.warnings, [])

    def test_name_only_in_a_link_is_neither_a_count_nor_a_zero(self):
        # Fetched 2026-10-02: the index answers "Sudo AI" with four hits and
        # none writes the name. Two link github.com/SUDO-AI-3D, the company's
        # own repo, so "no mentions" would be false; one is another product
        # ("Sudo – AI monetization") and one is "sudo-AI recovery form".
        key = hn.Key("name", "Sudo AI")
        self.assertEqual(hn.parse_hits(SUDO_AI["hits"], key), ([], 0, 4))
        self.assertEqual(hn.other_forms(SUDO_AI["hits"], key), 3)
        self.assertEqual(hn.other_forms(PINNED["Salem Robotics"]["hits"][3:], hn.Key("name", "Salem Robotics")), 0)
        fake = FakeHN(responses={'"Sudo AI"': SUDO_AI})
        signals, ctx, _ = run([{"name": "Sudo AI", "kind": "company", "domain": None, "github": None}], fake)
        self.assertEqual((signals, ctx.warnings), ([], []))  # skipped and logged, like an ambiguous name
        with mock.patch.object(hn.http, "get_json", fake.get_json):
            with self.assertRaises(hn.Skip):
                hn.observe({"name": "Sudo AI"}, [key], END_TS)
        # The same for an entity keyed by a domain nobody has linked.
        signals, ctx, fake = run([{"name": "Sudo AI", "kind": "company", "domain": "sudo-ai.test"}],
                                 FakeHN(responses={'"Sudo AI"': SUDO_AI}))
        self.assertEqual(fake.searches, ['"sudo-ai.test"', '"Sudo AI"'])
        self.assertEqual((signals, ctx.warnings), ([], []))

    def test_other_forms_of_a_name(self):
        pat = hn._other_form("Salem Robotics")
        for text in ("https://github.com/salem-robotics/x", "u/Salem_robotics", "salemrobotics.com", "SALEM ROBOTICS",
                     "at salem.robotics today", "q=salem%20robotics", "SalemRobotics raises"):
            self.assertTrue(pat.search(text), text)
        for text in ("Salem, Robotics student", "in Salem. Robotics is", "Salem – Robotics", "Salem Roboticsville",
                     "Jerusalem Robotics"):
            self.assertFalse(pat.search(text), text)

    def test_uncountable_name_hits_keep_a_zero_from_being_stated(self):
        # Nobody links the (reserved) domain. The name is the idiom in 5 of 8
        # hits, or has more hits than can be read: there is something on
        # Hacker News under that name, so neither a total nor a 0 is stated.
        known = {"name": "9 Mothers", "kind": "company", "domain": "nine-mothers.test"}
        signals, ctx, fake = run([known])
        self.assertEqual(fake.searches, ['"nine-mothers.test"', '"9 Mothers"'])
        self.assertEqual((signals, ctx.warnings), ([], []))
        big = {"name": "Skild AI", "kind": "company", "domain": "skild-ai.test"}
        signals, ctx, _ = run([big], FakeHN(responses={'"Skild AI"': {"nbHits": 4000,
                                                                      "hits": PINNED["Skild AI"]["hits"]}}))
        self.assertEqual((signals, ctx.warnings), ([], []))
        # read_name says why; no reason when the name was not searched or was read and empty.
        with mock.patch.object(hn.http, "get_json", FakeHN().get_json):
            self.assertEqual(hn.read_name(known, END_TS),
                             ([], '"9 Mothers" is ordinary lower-case English in 5 of 8 hits'))
            self.assertEqual(hn.read_name({"name": "Castelion"}, END_TS), ([], None))  # not searched: no reason
            self.assertEqual(hn.read_name({"name": "Valstad Shipworks"}, END_TS), ([], None))  # searched: nothing

    def test_zero_is_not_stated_when_the_name_search_failed(self):
        # No post links the domain and the name search did not answer, so
        # "no mentions" was not established. Nothing is emitted; the failure
        # is reported. (With citations in hand the total is still stated, as
        # test_failed_name_search_still_reports_the_citations shows.)
        signals, ctx, fake = run([{"name": "Charge Robotics", "domain": "chargerobotics.com"}],
                                 FakeHN(fail={'"Charge Robotics"'}))
        self.assertEqual(signals, [])
        self.assertEqual(len(ctx.warnings), 1)
        self.assertIn("Charge Robotics", ctx.warnings[0])

    def test_zero_and_a_later_count_are_the_same_row(self):
        known = {"name": "Weave Robotics", "kind": "company", "domain": "weave-robotics.test", "github": None}
        zero = run([known], FakeHN(responses={}))[0][0]
        counted = run([known])[0][0]  # three posts write the name
        self.assertEqual((zero.value, counted.value), (0, 3))
        self.assertEqual(fingerprint(zero), fingerprint(counted))
        other = run([{"name": "Charge Robotics", "domain": "chargerobotics.com"}])[0][0]
        self.assertNotEqual(fingerprint(zero), fingerprint(other))

    def test_unsearched_entities_get_no_zero(self):
        # No usable key (a single-word name and nothing else): never searched,
        # so nothing is said. Ambiguous: searched, but no number can be stated.
        signals, _, fake = run([CASTELION, NINE_MOTHERS])
        self.assertEqual(signals, [])
        self.assertEqual(fake.searches, ['"9 Mothers"'])

    def test_ambiguous_entity_gets_no_number(self):
        # 5 of 8 hits for "9 Mothers" are the idiom: no total can be stated.
        self.assertEqual(run([NINE_MOTHERS])[0], [])

    def test_real_signal_replaces_the_baseline(self):
        signals, _, _ = run([SKILD, ROBOCURVE, ATOMARINE, JOBY, WEAVE])
        self.assertNotIn("hn_baseline", [s.kind for s in signals])
        self.assertEqual(len({s.entity.name for s in signals}), 5)

    def test_failed_name_search_still_reports_the_citations(self):
        signals, ctx, _ = run([JOBY], FakeHN(responses=self.FLAT_JOBY, fail={'"Joby Aero"'}))
        self.assertEqual([(s.kind, s.metrics["hn_mentions_total"]) for s in signals], [("hn_baseline", 22)])
        self.assertEqual(len(ctx.warnings), 1)
        self.assertIn("citations only", ctx.warnings[0])

    def test_one_row_per_entity_refreshed_by_each_run(self):
        s, _ = self.joby()
        later = Context(today=TODAY + timedelta(days=1), known=[JOBY])
        with mock.patch.object(hn.http, "get_json", FakeHN(responses=self.FLAT_JOBY).get_json):
            tomorrow = list(hn.collect(later))[0]
        self.assertEqual(tomorrow.occurred_at, "2026-10-02")
        self.assertEqual(fingerprint(tomorrow), fingerprint(s))
        # It never collides with the entity's rise, which is a row of its own.
        rise = run([JOBY])[0][0]
        self.assertEqual(rise.kind, "hn_attention")
        self.assertNotEqual(fingerprint(rise), fingerprint(s))

    def test_scorer_reads_the_total_and_no_momentum(self):
        s, _ = self.joby()
        row = s.to_row()
        self.assertEqual(score.strength_asof(row, TODAY), 0.0)
        scored = score.score_entity({"name": "Joby Aero, Inc."}, [row], TODAY)
        self.assertEqual((scored["momentum"], scored["families"]["social"], scored["convergence"]), (0.0, 0.0, 0))
        # The total reaches the consensus term (how it is weighed is the scorer's business).
        self.assertEqual(scored["metrics"]["hn_mentions_total"], 22)
        self.assertNotIn("observed_only", scored["metrics"])  # a flag, not a number to rank on
        self.assertIn("press", scored["consensus_parts"])
        self.assertNotIn("unmeasured", scored["consensus_parts"])

    def test_scorer_tells_a_zero_from_never_measured(self):
        zero = run([{"name": "Charge Robotics", "domain": "chargerobotics.com"}])[0][0].to_row()
        entity = {"name": "Charge Robotics", "domain": "chargerobotics.com"}
        scored = score.score_entity(entity, [zero], TODAY)
        self.assertEqual(scored["metrics"]["hn_mentions_total"], 0)
        # A search that found nothing is not a footprint measurement: the
        # scorer says "unmeasured" and never "undiscovered".
        self.assertEqual(scored["momentum"], 0.0)
        self.assertIn("unmeasured", scored["consensus_parts"])
        self.assertLess(scored["earliness"], 1.0)
        # A later rise on the same entity carries the real total; the larger number wins.
        rise = run([JOBY])[0][0].to_row()
        both = score.score_entity({"name": "Joby Aero, Inc."}, [dict(zero), rise], TODAY)
        self.assertEqual(both["metrics"]["hn_mentions_total"], 24)


class Familiarity(unittest.TestCase):
    def st(self, n4, n8, authors, top=0):
        return {"n4": n4, "n8": n8, "authors": authors, "stories": 0, "top_points": top}

    def test_damp(self):
        for total in (0, 1, 16, 50):
            self.assertEqual(hn.familiarity_damp(total), 1.0)
        self.assertEqual(round(hn.familiarity_damp(245), 3), 0.803)   # Flock Safety
        self.assertEqual(round(hn.familiarity_damp(666), 3), 0.636)   # spacex.com
        self.assertEqual(round(hn.familiarity_damp(2536), 3), 0.483)  # tesla.com
        self.assertEqual(round(hn.familiarity_damp(14153), 3), 0.417)  # openai.com
        values = [hn.familiarity_damp(n) for n in (0, 60, 200, 1000, 10**4, 10**7)]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertGreater(values[-1], 1.0 - hn.FAMILIAR_DAMP - 1e-9)
        self.assertEqual(hn.familiarity_damp(None), 1.0)

    def test_household_name_no_longer_outranks_a_front_page_debut(self):
        tesla = self.st(22, 9, 17, top=5)  # read live 2026-10-01
        self.assertEqual(hn.rise_strength(tesla), 0.764)
        self.assertEqual(hn.rise_strength(tesla, hn.familiarity_damp(2536)), 0.447)
        isar = self.st(2, 1, 2, top=618)  # 10 lifetime mentions, a 618-point story
        self.assertEqual(hn.rise_strength(isar, hn.familiarity_damp(10)), 0.787)
        self.assertLess(hn.rise_strength(tesla, hn.familiarity_damp(2536)), hn.first_strength(5, 242))
        # Never below the floor, never above the undamped value.
        self.assertGreaterEqual(hn.rise_strength(tesla, 0.0), hn.RISE_FLOOR)
        self.assertEqual(hn.rise_strength(tesla, 7.0), hn.rise_strength(tesla))

    def test_series_uses_the_same_damp_as_the_signal(self):
        mentions = mentions_of(hn.Key("domain", "skild.ai"), PINNED["skild.ai"]["hits"])
        st = hn.window_stats(mentions, END_TS)
        series = hn.weekly_series(mentions, END_TS, TODAY, damp=0.5)
        self.assertEqual(series[-1]["s"], hn.rise_strength(st, 0.5))
        self.assertLess(series[-1]["s"], hn.rise_strength(st))


class Robustness(unittest.TestCase):
    def test_date_comes_from_the_timestamp_not_the_string(self):
        hit = dict(PINNED["atomarine.co"]["hits"][0], created_at="not a date")
        signals, _, _ = run([ATOMARINE], FakeHN(responses={'"atomarine.co"': {"nbHits": 1, "hits": [hit]}}))
        self.assertEqual(signals[0].occurred_at, "2026-07-30T10:31:46Z")
        self.assertEqual(signals[0].metrics["hn_first_seen"], "2026-07-30")
        missing = {k: v for k, v in hit.items() if k != "created_at"}
        signals, _, _ = run([ATOMARINE], FakeHN(responses={'"atomarine.co"': {"nbHits": 1, "hits": [missing]}}))
        self.assertEqual(signals[0].occurred_at, "2026-07-30T10:31:46Z")

    def test_epoch_and_future_stamps_are_not_mentions(self):
        base = PINNED["skild.ai"]["hits"]
        epoch = dict(base[0], objectID="1", created_at_i=0, created_at="1970-01-01T00:00:00Z")
        future = dict(base[0], objectID="59999999", created_at_i=END_TS + 86400, created_at="2026-10-03T00:00:00Z")
        text = dict(base[0], objectID="2", created_at_i="yesterday")
        hits = [future, *base, epoch, text, None, "junk"]
        signals, ctx, _ = run([SKILD], FakeHN(responses={'"skild.ai"': {"nbHits": len(hits), "hits": hits}}))
        self.assertEqual(ctx.warnings, [])
        self.assertEqual(len(signals), 1)
        s = signals[0]
        self.assertEqual(s.title, "Cited 2 times on Hacker News in 4 weeks against 1 in the prior 8, by 2 users")
        self.assertEqual((s.metrics["hn_citations_total"], s.metrics["hn_first_seen"]), (9, "2024-07-10"))
        self.assertLessEqual(s.occurred_at[:10], TODAY.isoformat())

    def test_odd_field_types_do_not_raise(self):
        key = hn.Key("domain", "war.gov")
        hit = {"objectID": "77", "created_at_i": "1790821840", "title": 42, "url": "https://war.gov/x",
               "points": "n/a", "num_comments": None, "author": None, "_tags": None}
        found, _, other = hn.parse_hits([hit, None, 3, "x", {"objectID": "abc", "created_at_i": 5,
                                                              "url": "https://war.gov/y"}], key)
        self.assertEqual([(m.id, m.ts, m.points, m.comments, m.author) for m in found],
                         [("77", 1790821840, 0, 0, "")])
        self.assertEqual(other, 4)  # an id that is not a number cannot become a link

    def test_launch_post_without_its_tag_is_still_the_makers_own(self):
        hit = dict(next(h for h in PINNED["Salem Robotics"]["hits"] if h["objectID"] == "49466715"))
        hit["_tags"] = ["story", "author_Salem_robotics"]
        m = hn.to_mention(hit, hn.Key("name", "Salem Robotics"))
        self.assertTrue(m.launch)
        self.assertFalse(hn.to_mention(dict(hit, title="Show HNX"), hn.Key("name", "Salem Robotics")).launch)

    def test_entity_hint_is_a_login_never_a_url(self):
        e = hn._entity({"name": "  Hebbian   Robotics ", "kind": "startup", "domain": "https://www.HebbianRobotics.com/x",
                        "github": "https://github.com/hebbian-robotics"})
        self.assertEqual((e.name, e.kind, e.domain, e.github),
                         ("Hebbian Robotics", "company", "hebbianrobotics.com", None))
        e = hn._entity({"name": "robocurve", "kind": "project", "domain": "github.com", "github": "robocurve"})
        self.assertEqual((e.kind, e.domain, e.github), ("project", None, "robocurve"))


class Module(unittest.TestCase):
    def test_contract(self):
        self.assertEqual((hn.SLUG, hn.FAMILY, hn.STAGE), ("hn_attention", "social", "enrich"))
        self.assertTrue(hn.DESCRIPTION)
        self.assertLessEqual(hn.MAX_ENTITIES, 400)
        self.assertIn("Hacker News", hn.__doc__.split("\n\n")[0])

    def test_only_first_party_hosts(self):
        for url in (hn.SEARCH_URL, hn.ITEM_URL, hn.ITEM_PAGE, hn.SEARCH_PAGE):
            self.assertRegex(url, r"^https://(hn\.algolia\.com|hacker-news\.firebaseio\.com|news\.ycombinator\.com)/")


if __name__ == "__main__":
    unittest.main()
