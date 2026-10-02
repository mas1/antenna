"""Offline tests for the USPTO trademark collector: owner and goods parsing,
the thesis and consumer filters, the thesis gate, request bodies,
filing-history parsing, titles, strength, and the collect() wiring. No
network: collect() runs with antenna.http.post_json swapped for a function
that serves the saved fixture.
"""

from __future__ import annotations

import copy
import json
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from antenna import http
from antenna.collectors import uspto_trademarks as tm
from antenna.collectors.base import Context
from antenna.thesis import classify

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "regulatory"
RESPONSE = json.loads((FIX / "uspto_tmsearch_uav_us_owners.json").read_text())
HITS = [h["source"] for h in RESPONSE["hits"]["hits"]]
BY_ID = {h["id"]: h for h in HITS}
TODAY = date(2026, 10, 1)

VERIDIS = "50139305"       # Veridis Defense LLC, 1b, IC 012: Unmanned aerial vehicles (UAVs).
INDIVIDUAL = "50139089"    # Tyrone Cornell Franklin (INDIVIDUAL; USA)
PEAKEEP = "50131896"       # US myehome LLC, photography and camera drones
ASYLON_OLD = "50120068"    # Asylon, Inc., 1a, first used 2016-01-31
ASYLON_NEW = "50120096"    # Asylon, Inc., 1b
XCELL = ["50121132", "50121113", "50121097"]  # Firestorm Labs, Inc., IC 040, all filed 2026-09-21
SWRM = "50122141"          # SWRM, Inc: drones in IC 012 beside a long IC 045 list


def mark(serial: str) -> tm.Mark:
    m, why = tm.parse_mark(copy.deepcopy(BY_ID[serial]))
    assert m is not None, why
    return m


def kept(serial: str) -> tm.Mark:
    m = mark(serial)
    why = tm.assess(m)
    assert why == "", why
    return m


def hist(total=1, first="2026-09-30", before=0, name_before=0, name_first=None) -> dict:
    return {"total": total, "first_filed": date.fromisoformat(first),
            "before": before, "name_before": name_before,
            "name_first_filed": date.fromisoformat(name_first) if name_first else None}


def with_goods(goods: list[tuple[str, str]], basis=("1b",), wordmark="TESTMARK") -> tm.Mark:
    """The Veridis application with other goods, to exercise assess()."""
    m = mark(VERIDIS)
    m.goods, m.basis, m.wordmark = list(goods), list(basis), wordmark
    m.classes = sorted({c for c, _ in goods})
    return m


# Filing histories served to collect(): (total, first filed, before, name before).
HISTORY = {
    "AEROVIRONMENT, INC.": (129, "1974-10-04", 124, 124),
    "Flock Group Inc.": (34, "2019-02-02", 21, 21),
    "Firestorm Labs, Inc.": (30, "2022-03-15", 27, 27),
    "Joby Aero, Inc.": (24, "2020-05-01", 20, 20),
    "Asylon, Inc.": (8, "2017-03-01", 4, 4),
    "Autum, Inc.": (2, "2023-04-01", 1, 6),
}


def fake_post_json(url, body, **kw):
    """Stands in for http.post_json: the fixture for a search, and a filters
    aggregation shaped like the live one for a history request."""
    assert url == tm.SEARCH_URL
    if "aggs" not in body:
        return copy.deepcopy(RESPONSE) if body["from"] == 0 else {"hits": {"totalValue": 39, "hits": []}}
    buckets = {}
    for key, flt in body["aggs"]["owners"]["filters"]["filters"].items():
        if not key.startswith("t"):
            continue
        i = key[1:]
        name = flt["match_phrase"]["ownerName"]
        total, first, before, name_before = HISTORY.get(name, (1, "2026-09-20", 0, 0))
        buckets[f"t{i}"] = {"doc_count": total, "first": {"value": 0, "value_as_string": first}}
        buckets[f"l{i}"] = {"doc_count": before, "first": {"value": None}}
        buckets[f"c{i}"] = {"doc_count": name_before, "first": {"value": None}}
    return {"hits": {"totalValue": 0, "hits": []}, "aggregations": {"owners": {"buckets": buckets}}}


def run_collect(limit=None, post=fake_post_json) -> tuple[list, Context]:
    ctx = Context(today=TODAY, limit=limit)
    with mock.patch.object(tm.http, "post_json", side_effect=post), \
         mock.patch.object(tm.time, "sleep"):
        return list(tm.collect(ctx)), ctx


class OwnerParsing(unittest.TestCase):
    def test_company_owner(self):
        self.assertEqual(
            tm.parse_owner("Proception Inc. (CORPORATION; Delaware, USA)"),
            ("Proception Inc.", "CORPORATION", "Delaware", "USA"))

    def test_individual_owner_has_no_state(self):
        self.assertEqual(
            tm.parse_owner("Tyrone Cornell Franklin (INDIVIDUAL; USA)"),
            ("Tyrone Cornell Franklin", "INDIVIDUAL", None, "USA"))

    def test_name_with_parentheses_keeps_them(self):
        name, entity, state, country = tm.parse_owner(
            "Kaishan Compressor (USA), LLC (LIMITED LIABILITY COMPANY; Alabama, USA)")
        self.assertEqual(name, "Kaishan Compressor (USA), LLC")
        self.assertEqual((entity, state, country), ("LIMITED LIABILITY COMPANY", "Alabama", "USA"))

    def test_unparseable(self):
        self.assertIsNone(tm.parse_owner("no entity here"))

    def test_personal_names(self):
        for name in ("William Evert Traver IV", "Nathan A. Ernst", "zhengyi he"):
            self.assertTrue(tm.looks_personal(name), name)
        for name in ("Veridis Defense LLC", "Bobsweep USA", "Opticore", "NeuroSymbolic Systems",
                     "1st American Nuclear Co.", "Atomic Semi, Inc."):
            self.assertFalse(tm.looks_personal(name), name)

    def test_owner_core_and_key(self):
        self.assertEqual(tm.owner_core("Draupnir Systems, Inc."), "Draupnir Systems")
        self.assertEqual(tm.owner_core("PAVE Aerospace, LLC"), "PAVE Aerospace")
        self.assertEqual(tm.owner_core("Opticore"), "Opticore")
        # Legal forms only: the words that say what the company does stay.
        # "Company" and "Co." are legal forms here, though base.strip_legal
        # keeps them: the history match must reach the same owner under
        # another form (Sierra Nevada Corporation, now Sierra Nevada Company, LLC).
        self.assertEqual(tm.owner_core("The American Drone Company, LLC"), "The American Drone")
        self.assertEqual(tm.owner_core("Sierra Nevada Company, LLC"), "Sierra Nevada")
        self.assertEqual(tm.owner_core("1st American Nuclear Co."), "1st American Nuclear")
        self.assertEqual(tm.owner_core("AVX Aircraft Company"), "AVX Aircraft")
        self.assertEqual(tm.owner_core("Company"), "Company")
        self.assertEqual(tm.owner_core("Salem Robotics, Inc."), "Salem Robotics")
        self.assertEqual(tm.owner_core("Zeeno Robotics, LLC."), "Zeeno Robotics")
        self.assertEqual(tm.owner_key("AEROVIRONMENT, INC."), tm.owner_key("AeroVironment, Inc."))
        self.assertNotEqual(tm.owner_key("Harbinger LLC"), tm.owner_key("Harbinger Motors Inc."))


class MarkParsing(unittest.TestCase):
    def test_fields_from_fixture(self):
        m = mark(VERIDIS)
        self.assertEqual(m.serial, "50139305")
        self.assertEqual(m.wordmark, "VERIDIS DEFENSE")
        self.assertEqual(m.owner, "Veridis Defense LLC")
        self.assertEqual(m.entity, "LIMITED LIABILITY COMPANY")
        self.assertEqual((m.city, m.state, m.state_of_org), ("Beverly Hills", "California", "California"))
        self.assertEqual(m.filed, date(2026, 9, 30))
        self.assertEqual(m.basis, ["1b"])
        self.assertEqual(m.basis_kind, "itu")
        self.assertIsNone(m.first_use)
        self.assertEqual(m.classes, ["012"])
        self.assertEqual(m.goods, [("012", "Unmanned aerial vehicles (UAVs)")])
        self.assertIsNone(m.attorney)
        self.assertEqual(
            m.url,
            "https://tsdr.uspto.gov/#caseNumber=50139305&caseSearchType=US_APPLICATION"
            "&caseType=DEFAULT&searchType=statusSearch")

    def test_use_based_mark_carries_first_use(self):
        m = mark(ASYLON_OLD)
        self.assertEqual(m.basis_kind, "use")
        self.assertEqual(m.first_use, date(2016, 1, 31))
        self.assertEqual(m.attorney, "Diana S. Bae")

    def test_individual_is_dropped(self):
        self.assertEqual(tm.parse_mark(BY_ID[INDIVIDUAL]), (None, "not a company"))

    def test_dead_joint_and_foreign_are_dropped(self):
        src = copy.deepcopy(BY_ID[VERIDIS])
        self.assertEqual(tm.parse_mark({**src, "alive": False})[1], "dead")
        self.assertEqual(tm.parse_mark({**src, "ownerName": src["ownerName"] * 2})[1], "joint owners")
        self.assertEqual(
            tm.parse_mark({**src, "ownerName": ["Veridis GmbH (CORPORATION; Germany)"]})[1],
            "foreign entity")
        self.assertEqual(
            tm.parse_mark({**src, "ownerStateCountryAddress": "Shenzhen 518000, CHINA"})[1],
            "foreign address")
        self.assertEqual(
            tm.parse_mark({**src, "ownerName": ["Acme Intermediate, LLC (LIMITED LIABILITY COMPANY; Delaware, USA)"]})[1],
            "holding vehicle or foreign-style shell")
        self.assertEqual(
            tm.parse_mark({**src, "ownerName": ["Jane Q Public (CORPORATION; Delaware, USA)"]})[1],
            "owner is a personal name")
        self.assertEqual(
            tm.parse_mark({**src, "ownerName": ["Mecha Picnic Holdings I LLC (LIMITED LIABILITY COMPANY; Delaware, USA)"]})[1],
            "holding vehicle or foreign-style shell")
        self.assertEqual(
            tm.parse_mark({**src, "ownerName": ["ChipMango Holdings (CORPORATION; Delaware, USA)"]})[1], "")

    def test_us_arm_of_another_company_is_dropped(self):
        src = copy.deepcopy(BY_ID[VERIDIS])
        for name in ("HEO (USA), INC.", "Kaishan Compressor (USA), LLC", "Acme Robotics USA, Inc.",
                     "Acme Drones North America LLC", "Bobsweep USA"):
            got = tm.parse_mark({**src, "ownerName": [f"{name} (CORPORATION; Delaware, USA)"]})
            self.assertEqual(got, (None, "US arm of another company"), name)
        for name in ("1st American Nuclear Co.", "The American Drone Company, LLC", "Usable Robotics, Inc."):
            m, why = tm.parse_mark({**src, "ownerName": [f"{name} (CORPORATION; Delaware, USA)"]})
            self.assertEqual((m.owner if m else None, why), (name, ""))

    def test_scalar_fields_where_lists_are_usual(self):
        src = copy.deepcopy(BY_ID[VERIDIS])
        src.update(ownerName=src["ownerName"][0], goodsAndServices=src["goodsAndServices"][0],
                   currentBasis="1b", internationalClass="IC 012", ownerCity=["BEVERLY  HILLS"],
                   attorney=["Jane  Doe"])
        m, why = tm.parse_mark(src)
        self.assertEqual(why, "")
        self.assertEqual((m.owner, m.basis, m.classes), ("Veridis Defense LLC", ["1b"], ["012"]))
        self.assertEqual(m.goods, [("012", "Unmanned aerial vehicles (UAVs)")])
        self.assertEqual((m.city, m.attorney), ("Beverly Hills", "Jane Doe"))

    def test_missing_fields_give_a_reason_not_an_exception(self):
        self.assertEqual(tm.parse_mark({}), (None, "dead"))
        self.assertEqual(tm.parse_mark({"alive": True}), (None, "unparsed owner"))
        src = copy.deepcopy(BY_ID[VERIDIS])
        for key in ("filedDate", "id"):
            self.assertEqual(tm.parse_mark({k: v for k, v in src.items() if k != key})[1], "no serial or date")
        bare = {k: src[k] for k in ("alive", "ownerName", "ownerStateCountryAddress", "filedDate", "id")}
        m, why = tm.parse_mark(bare)
        self.assertEqual(why, "")
        self.assertEqual((m.wordmark, m.city, m.basis, m.goods, m.attorney), (None, None, [], [], None))
        self.assertEqual(tm.assess(m), "no goods text")

    def test_place_names_typed_in_one_case_are_tidied(self):
        self.assertEqual(tm._tidy_place("OKLAHOMA CITY"), "Oklahoma City")
        self.assertEqual(tm._tidy_place("bronx"), "Bronx")
        self.assertEqual(tm._tidy_place("McLean"), "McLean")
        self.assertIsNone(tm._tidy_place(None))

    def test_split_goods(self):
        self.assertEqual(
            tm.split_goods(["IC 012: Drones; Military drones.", "IC 009:  Radar   apparatus."]),
            [("012", "Drones"), ("012", "Military drones"), ("009", "Radar apparatus")])
        self.assertEqual(tm.split_goods(None), [])

    def test_every_fixture_hit_parses_or_gives_a_reason(self):
        reasons = [tm.parse_mark(h)[1] for h in HITS]
        self.assertEqual(len(reasons), 30)
        self.assertEqual([r for r in reasons if r], ["not a company"])


class ThesisFilter(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(tm.thesis_label("Unmanned aerial vehicles (UAVs)"), "unmanned aerial vehicles")
        self.assertEqual(tm.thesis_label("Military drones"), "military drones")
        self.assertEqual(tm.thesis_label("Nuclear reactors"), "nuclear reactors")
        self.assertEqual(tm.thesis_label("Humanoid robots with artificial intelligence"), "humanoid robots")
        self.assertEqual(tm.thesis_label("Rocket engines"), "rocket engines")
        # The ID Manual's stock phrase for hot-rod and boat parts is not a space company.
        self.assertIsNone(tm.thesis_label("Rocket engines for land vehicle propulsion"))
        self.assertEqual(tm.thesis_label("Software for directed energy deposition"), None)
        self.assertIsNone(tm.thesis_label("Camera drones, other than toys"))
        self.assertIsNone(tm.thesis_label("Radar detectors"))
        self.assertIsNone(tm.thesis_label("Satellite navigation apparatus"))
        self.assertIsNone(tm.thesis_label("Toy robots"))
        self.assertIsNone(tm.thesis_label("Clothing, namely, shirts"))

    def test_labels_say_only_what_the_wording_supports(self):
        for item, label in [
            ("Semiconductor wafers", "semiconductors"),
            ("Semiconductor chips", "semiconductors"),
            ("Semiconductor foundry services", "semiconductor manufacturing"),
            ("Wafer foundry services for others", "semiconductor manufacturing"),
            ("Foundry machines", "metal casting"),
            ("silicon photonics circuits", "photonics"),
            ("Nuclear reactors and small modular reactors", "nuclear reactors"),
            ("Generating of electricity from nuclear power", "nuclear power"),
            ("Nuclear generators", "nuclear power"),
            ("Lithium ion batteries", "lithium batteries"),
            ("Solid-state batteries", "solid-state batteries"),
            ("Radar apparatus", "radar"),
            ("Weather radar", "radar"),
            ("Vertical take-off and landing (VTOL) aircraft", "VTOL aircraft"),
            ("vertical takeoff and landing aircraft", "VTOL aircraft"),
            ("Exhibitions in the field of vertical take off and landing (VTOL) aircraft", "VTOL aircraft"),
            ("VTOL air vehicles", "VTOL aircraft"),
            ("eVTOL (electric Vertical Takeoff and Landing)", "VTOL aircraft"),
            ("Space vehicles, namely, satellite buses", "spacecraft"),
            ("Robotic arms for palletizing", "robotic arms"),
            ("Machine tools in the nature of dexterous robotic manipulator hands", "robotic hands"),
            ("Industrial robot parts, namely, robotic end effectors", "robotic manipulators"),
            ("Engineering services in the field of hypersonic systems", "hypersonics"),
            ("Software for power grid monitoring", "power grids"),
        ]:
            self.assertEqual(tm.thesis_label(item), label, item)
        self.assertNotIn("semiconductor chips", tm._LABEL_ORDER)
        # Live wording: aerospace vehicles are as often aircraft as spacecraft.
        self.assertIsNone(tm.thesis_label("maintenance and repair of aerospace vehicles and equipment"))

    def test_look_alike_wording_gets_no_label(self):
        # Live case: "infusion devices" read as fusion.
        self.assertIsNone(tm.thesis_label("Wearable infusion pumps being infusion devices for administering drugs"))
        self.assertIsNone(tm.thesis_label("Butt fusion machines for joining plastic pipe"))
        self.assertIsNone(tm.thesis_label("Fusion devices for optical fibre splicing"))
        for item in ("Fusion reactors", "Fusion power plants", "Research in the field of controlled nuclear fusion",
                     "Plasma fusion devices", "Tokamaks"):
            self.assertEqual(tm.thesis_label(item), "fusion energy", item)
        # A laser is not a directed energy system unless the applicant says so.
        self.assertIsNone(tm.thesis_label("High-energy lasers for cutting metal"))
        self.assertEqual(tm.thesis_label("Directed energy lasers for military applications"), "directed energy systems")
        self.assertIsNone(tm.thesis_label("Battery storage cases"))
        self.assertIsNone(tm.thesis_label("Battery storage boxes"))
        self.assertEqual(tm.thesis_label("Off-grid power and battery storage systems"), "energy storage systems")
        self.assertEqual(tm.thesis_label("generation of electricity utilizing battery storage"), "energy storage systems")
        for item in ("Lithographic printing presses", "Offset lithography for others", "Lithographic printing plates"):
            self.assertIsNone(tm.thesis_label(item), item)
        self.assertEqual(tm.thesis_label("test wafers for etch, deposition, and lithography evaluation"),
                         "semiconductor manufacturing")

    def test_household_appliances_called_robots_are_consumer_goods(self):
        # Live wording: the ID Manual's names for robot vacuums, mops and kin.
        for item in ("Household cleaning robots", "Household cleaning robots with artificial intelligence (AI)",
                     "Household cleaning and laundry robots with artificial intelligence", "Household beverage robots",
                     "Robotic air purifiers for household use", "Disinfectant spray robots for household purposes",
                     "Custom manufacture of robot cleaners for household purposes",
                     "Self-driving housekeeping robots for household purpose not for use in delivery",
                     "Robotic vacuum cleaners", "Robotic lawnmowers", "Robotic pool cleaners", "Pool cleaning robots",
                     "Robotic mops", "Toy drones", "Entertainment drones", "Camera drones", "Smart robot toys"):
            self.assertTrue(tm.is_consumer(item), item)
            self.assertIsNone(tm.thesis_label(item), item)
        # A general-purpose humanoid that also does housework is still a humanoid.
        for item, label in [
            ("Humanoid robots with artificial intelligence for assisting humans with household chores and tasks",
             "humanoid robots"),
            ("Humanoid robots with artificial intelligence for use in household cleaning and laundry", "humanoid robots"),
            ("Robots for use with household, industrial and commercial purposes", "robots"),
            ("Robots for use in commercial cleaning", "robots"),
            ("Robotic cleaners for solar panels", "robots"),
            ("Robotic sweepers for streets and parking lots", "robots"),
            ("Industrial robots", "industrial robots"),
            ("First-person view (FPV) drones", "drones"),
        ]:
            self.assertFalse(tm.is_consumer(item), item)
            self.assertEqual(tm.thesis_label(item), label, item)

    def test_consumer_sellers_are_dropped(self):
        vacuums = with_goods([("007", "Household cleaning robots"), ("007", "Robotic vacuum cleaners"),
                              ("007", "Robotic lawnmowers"), ("007", "Window cleaning robots")])
        self.assertEqual(tm.assess(vacuums), "no thesis goods in a qualifying class")
        # One industrial entry in a list of home appliances does not carry it.
        vacuums = with_goods([("007", "Industrial robots"), ("007", "Household cleaning robots"),
                              ("007", "Robotic mops")])
        self.assertEqual(tm.assess(vacuums), "mostly consumer goods")
        toys = with_goods([("012", "Drones"), ("012", "Camera drones"), ("012", "Photography drones"),
                           ("028", "Toy drones"), ("028", "Remote control toys, namely, drones")])
        self.assertEqual(tm.assess(toys), "mostly consumer goods")
        toys = with_goods([("028", "Toy drones"), ("028", "Toy robots")])
        self.assertEqual(tm.assess(toys), "no thesis goods in a qualifying class")

    def test_exclusion_clauses_are_not_matched(self):
        # Live case: a life-coaching app whose goods end by disclaiming robotics.
        clause = ("none of the foregoing relating to robotics, robotic hardware, autonomous machines, "
                  "industrial automation, or embodied AI")
        self.assertEqual(tm.covered(clause), "")
        self.assertIsNone(tm.thesis_label(clause))
        self.assertIsNone(tm.thesis_label("Vehicles, excluding unmanned aerial vehicles"))
        self.assertIsNone(tm.thesis_label("Computer hardware, not for satellites"))
        # What comes before the exclusion still counts, and an excluded
        # consumer word no longer hides the goods.
        self.assertEqual(tm.thesis_label("Remote controls for drones, excluding gaming apparatus"), "drones")
        self.assertEqual(tm.thesis_label("Drones, other than toys"), "drones")
        self.assertEqual(tm.thesis_label("Robotic exoskeleton suits, other than for medical purposes"), "exoskeletons")
        self.assertEqual(tm.thesis_label("Aircraft, including but not limited to drones"), "drones")
        m = with_goods([
            ("009", "Downloadable software using artificial intelligence (AI) for personal guidance"),
            ("009", "downloadable software using AI for natural language processing"),
            ("009", clause),
        ])
        self.assertEqual(tm.assess(m), "no thesis goods in a qualifying class")

    def test_satellite_as_a_modifier_is_not_a_satellite(self):
        self.assertIsNone(tm.thesis_label(
            "resource allocation modules, namely hybrid generators, solar power output, and satellite connectivity"))
        self.assertIsNone(tm.thesis_label("Software for Global Navigation Satellite System (GNSS) data"))
        self.assertEqual(tm.thesis_label("earth observation satellites"), "satellites")
        self.assertEqual(tm.thesis_label("Satellites and satellite data links"), "satellites")
        # A ground terminal or an imagery product is named as what it is.
        self.assertEqual(tm.thesis_label("Satellite communications terminals"), "satellite communications")
        self.assertEqual(tm.thesis_label("downloadable satellite imagery"), "satellite imagery")

    def test_item_scope_software_is_what_the_item_is(self):
        self.assertEqual(tm.item_scope(
            "009", "Distributed energy storage systems for off-grid power comprising batteries, "
                   "downloadable software, charge controllers"), "goods")
        self.assertEqual(tm.item_scope(
            "009", "Electro-optical imaging payloads for spacecraft, comprised of sensors and software"), "related")
        self.assertEqual(tm.item_scope("009", "Recorded software for controlling drones"), "software")
        self.assertEqual(tm.item_scope("009", "Computer programs for controlling industrial robots"), "software")
        # Live case: a software-defined radar is a radar.
        self.assertEqual(tm.item_scope("009", "software-defined radar apparatus for air surveillance"), "goods")
        self.assertEqual(tm.item_scope("009", "signal processors for radar"), "related")
        self.assertEqual(tm.item_scope(
            "042", "Software as a service (SAAS) services featuring software for planning satellite missions"),
            "software")
        self.assertEqual(tm.item_scope(
            "042", "Providing temporary use of on-line non-downloadable software for operating drones"), "software")
        self.assertEqual(tm.item_scope(
            "042", "Research, design and development of software for designing nuclear reactors"), "services")
        self.assertEqual(tm.item_scope("042", "Design of semiconductor chips"), "services")

    def test_goods_are_named_from_the_head_of_the_item(self):
        # Live cases: the applicant makes the missile or the radar, not the UAV it is aimed at.
        self.assertEqual(
            tm.label_and_scope("013", "Interceptor missiles for defense against unmanned aerial vehicles"),
            ("missiles", "goods"))
        self.assertEqual(
            tm.label_and_scope("009", "radar apparatus for detection and tracking of unmanned aerial vehicles"),
            ("radar", "goods"))
        self.assertEqual(tm.label_and_scope("012", "Rocket engines for launch vehicles"), ("rocket engines", "goods"))
        self.assertEqual(
            tm.label_and_scope("009", "infrared cameras for aircraft and unmanned aerial vehicles"),
            ("unmanned aerial vehicles", "related"))
        self.assertIsNone(tm.label_and_scope("009", "Computer hardware"))

    def test_pieces_of_a_split_software_item_stay_software(self):
        # Live case: one IC 009 software item cut at its semicolons.
        m = with_goods([
            ("009", "Downloadable enterprise software for planning satellite missions"),
            ("009", "managing spacecraft operations"),
            ("009", "managing satellite constellations"),
            ("009", "and utilizing artificial intelligence to analyze satellite and sensor data"),
        ])
        self.assertEqual(tm.assess(m), "")
        self.assertEqual((m.label, m.scope), ("satellites", "software"))
        self.assertFalse(m.hardware)
        # A new noun phrase after a software item is its own goods.
        m = with_goods([("009", "Downloadable software for controlling drones"), ("009", "Satellites")])
        self.assertEqual(tm.assess(m), "")
        self.assertEqual((m.label, m.scope), ("satellites", "goods"))

    def test_installers_and_side_services_are_dropped(self):
        # IC 037 is construction, installation and repair: a contractor.
        m = with_goods([("037", "Installation, maintenance and repair of battery energy storage systems")])
        self.assertEqual(tm.assess(m), "no thesis goods in a qualifying class")
        for item in ("Surveying services by means of aerial drone and lidar scanning",
                     "Providing temporary use of non-downloadable software for space launch insurance risk scores",
                     "Software as a service (SAAS) featuring software for workforce training in nuclear energy",
                     "Custom additive manufacturing of furniture for others"):
            self.assertEqual(tm.assess(with_goods([("042", item)])), "no thesis goods in a qualifying class", item)
        m = with_goods([("009", "Industrial automation controls")] +
                       [("037", f"Electrical contractor services number {i}") for i in range(3)])
        self.assertEqual(tm.assess(m), "mostly installation and repair services")
        # The same wording on goods is left alone: sonar and survey robots are hardware.
        m = with_goods([("009", "Lidar sensors for hydrographic surveying robots")])
        self.assertEqual(tm.assess(m), "")

    def test_thesis_as_one_service_among_many_is_dropped(self):
        goods = [("042", "Design of semiconductor chips"), ("042", "Computer programming")]
        goods += [("035", f"Business consulting number {i}") for i in range(6)]
        self.assertEqual(tm.assess(with_goods(goods)), "thesis services are a small part of the list")
        # The same share is enough when the thesis item is a product.
        goods = [("009", "Semiconductor chips"), ("009", "Computer hardware")]
        goods += [("035", f"Business consulting number {i}") for i in range(6)]
        self.assertEqual(tm.assess(with_goods(goods)), "")

    def test_filing_that_rests_only_on_a_foreign_application_is_dropped(self):
        goods = [("012", "Unmanned aerial vehicles (UAVs)")]
        self.assertEqual(tm.assess(with_goods(goods, basis=["44d"])), "foreign-basis filing")
        self.assertEqual(tm.assess(with_goods(goods, basis=["44d", "44e"])), "foreign-basis filing")
        self.assertEqual(tm.assess(with_goods(goods, basis=["1b", "44d"])), "")
        self.assertEqual(tm.assess(with_goods(goods, basis=[])), "")

    def test_item_scope(self):
        self.assertEqual(tm.item_scope("012", "Unmanned aerial vehicles (UAVs) and structural parts therefor"), "goods")
        self.assertEqual(tm.item_scope("009", "infrared cameras for aircraft and unmanned aerial vehicles"), "related")
        self.assertEqual(tm.item_scope("009", "Downloadable software for controlling drones"), "software")
        self.assertEqual(tm.item_scope("040", "Manufacturing services for others in the field of drones"), "services")
        self.assertEqual(tm.item_scope("012", "Water vehicles, namely, unmanned surface vessels"), "goods")

    def test_pick_label_prefers_goods_then_frequency(self):
        hits = [("missiles", "services"), ("missiles", "software"), ("autonomous vessels", "goods")]
        self.assertEqual(tm.pick_label(hits), ("autonomous vessels", "goods"))
        hits = [("drones", "goods"), ("drones", "goods"), ("unmanned ground vehicles", "goods"),
                ("unmanned ground vehicles", "goods"), ("unmanned aerial vehicles", "goods")]
        self.assertEqual(tm.pick_label(hits), ("unmanned aerial vehicles", "goods"))
        hits = [("nuclear power", "goods"), ("nuclear power", "goods"), ("nuclear reactors", "goods")]
        self.assertEqual(tm.pick_label(hits), ("nuclear reactors", "goods"))
        self.assertEqual(tm.pick_label(hits[:2]), ("nuclear power", "goods"))
        self.assertEqual(tm.pick_label([("drones", "services")]), ("drones", "services"))
        # A passing specific mention does not outvote what the list is mostly about.
        hits = [("robots", "software")] * 3 + [("power grids", "software")]
        self.assertEqual(tm.pick_label(hits), ("robots", "software"))
        hits = [("drones", "goods"), ("military drones", "goods")]
        self.assertEqual(tm.pick_label(hits), ("military drones", "goods"))

    def test_plain_uav_mark_is_kept(self):
        m = kept(VERIDIS)
        self.assertEqual((m.label, m.scope, m.share), ("unmanned aerial vehicles", "goods", 1.0))
        self.assertTrue(m.hardware)
        self.assertGreaterEqual(m.fit, 0.3)
        self.assertEqual(m.excerpt, "Goods and services: IC 012: Unmanned aerial vehicles (UAVs)")

    def test_service_mark_is_kept_as_services(self):
        m = kept(XCELL[0])
        self.assertEqual((m.label, m.scope), ("unmanned aerial vehicles", "services"))
        self.assertFalse(m.hardware)

    def test_one_plural_or_multi_word_term_is_enough_when_unmistakable(self):
        for cls, item, label in [("012", "Launch vehicles", "launch vehicles"),
                                 ("040", "Treatment of materials by means of rare earth separation", "rare earths"),
                                 ("013", "Guided missiles", "missiles"),
                                 ("012", "Drones", "drones")]:
            m = with_goods([(cls, item)])
            self.assertEqual(tm.assess(m), "", item)
            self.assertEqual(m.label, label)

    def test_unmistakable_goods_pass_on_the_applicants_own_wording(self):
        # Live cases, each the whole of what made the application on thesis:
        # the classifier counts radar, VTOL and space vehicles alone, so the
        # collector's reading is not needed and is not used.
        for goods, label in [
            ([("009", "Radar apparatus")], "radar"),
            ([("009", "radar apparatus"), ("009", "radar receivers"), ("009", "radar transmitters"),
              ("009", "signal processors for radar"), ("009", "radar antennas"),
              ("009", "software-defined radar apparatus for air surveillance")], "radar"),
            ([("012", "Vertical take-off and landing (VTOL) aircraft")], "VTOL aircraft"),
            ([("012", "Aircraft"), ("012", "Vertical take-off and landing (VTOL) aircraft"),
              ("012", "Structural parts for aircraft")], "VTOL aircraft"),
            ([("012", "Aircraft"), ("012", "Ultralight aircraft"),
              ("012", "Vertical take-off and landing (VTOL) aircraft")], "VTOL aircraft"),
            ([("012", "Space vehicles")], "spacecraft"),
        ]:
            m = with_goods(goods)
            self.assertEqual(tm.assess(m), "", goods)
            self.assertEqual((m.label, m.scope), (label, "goods"), goods)
            wording = m.excerpt.removeprefix("Goods and services: ")
            self.assertEqual(m.fit, classify(wording)["fit"], goods)
            self.assertGreaterEqual(m.fit, 0.3, goods)
        m = with_goods([("009", "Radar apparatus")])
        self.assertEqual(tm.assess(m), "")
        sig = tm.build_signal([m], hist(), TODAY)
        self.assertEqual(sig.title, "Filed intent-to-use trademark TESTMARK for radar, its first trademark filing")
        self.assertEqual(sig.entity.one_liner, "Trademark goods: Radar apparatus")
        self.assertTrue(sig.metrics["tm_hardware_goods"])

    def test_radar_look_alikes_and_toys_stay_out(self):
        # Radar is named as the goods only when the item is a radar. Live
        # wording first: a tank level gauge, a weather app, a putting trainer.
        for cls, item in [
            ("009", "Radar detectors"),
            ("009", "Laser and radar detectors for automobiles"),
            ("009", "Wired radar level sensors that continuously measure the level height of liquid or solids "
                    "in tanks, sumps, silos or bins"),
            ("009", "Downloadable software in the nature of a mobile application for wirelessly configuring, "
                    "monitoring, and diagnosing industrial radar level sensors"),
            ("009", "Downloadable mobile applications for displaying weather information, weather forecasts, "
                    "weather radar imagery, and severe weather alerts"),
            ("009", "Golf training equipment, namely, electronic putting training devices utilizing optical "
                    "sensors and light detection and ranging (LiDAR) technology"),
            ("009", "Golf launch monitors, namely, radar apparatus for measuring ball speed"),
            ("009", "Radar guns for sporting events"),
            ("009", "Electronic sports training simulators for simulating robot fighting"),
            ("012", "Toy vertical take-off and landing (VTOL) aircraft"),
            ("009", "Scale model space vehicles"),
        ]:
            self.assertTrue(tm.is_consumer(item), item)
            self.assertIsNone(tm.thesis_label(item), item)
            self.assertEqual(tm.assess(with_goods([(cls, item)])), "no thesis goods in a qualifying class", item)
        # Class 028 is toys, however the item is worded.
        for item in ("Radar apparatus", "Space vehicles", "Vertical take-off and landing (VTOL) aircraft"):
            self.assertEqual(tm.assess(with_goods([("028", item)])), "no thesis goods in a qualifying class", item)
        # A list that is mostly detectors and toys is not carried by one radar item.
        m = with_goods([("009", "Radar apparatus"), ("009", "Radar detectors"), ("028", "Toy radar guns")])
        self.assertEqual(tm.assess(m), "mostly consumer goods")
        # Radar that is the goods, whatever it watches.
        for item in ("Weather radar", "Air traffic control radar systems", "Radar object detectors for use on vehicles",
                     "Radar jamming apparatus"):
            self.assertFalse(tm.is_consumer(item), item)
            self.assertEqual(tm.thesis_label(item), "radar", item)
        self.assertFalse(tm.is_consumer("Providing large-scale models for controlling robots"))

    def test_goods_with_one_ambiguous_term_pass_on_the_plain_reading(self):
        # "Self-driving" alone is one term the classifier will not take by
        # itself. The applicant lists the cars as its goods; the reading the
        # title states ("for autonomous vehicles") is the second term, and
        # the text says so.
        m = with_goods([("012", "Self-driving cars")])
        self.assertEqual(classify("IC 012: Self-driving cars")["terms"], ["self-driving"])
        self.assertLess(classify("IC 012: Self-driving cars")["fit"], 0.3)
        self.assertEqual(tm.assess(m), "")
        self.assertEqual((m.label, m.scope, tm.covers(m)),
                         ("autonomous vehicles", "goods", "for autonomous vehicles"))
        self.assertGreaterEqual(m.fit, 0.3)
        sig = tm.build_signal([m], hist(), TODAY)
        self.assertEqual(sig.title,
                         "Filed intent-to-use trademark TESTMARK for autonomous vehicles, its first trademark filing")
        self.assertIn("an application for autonomous vehicles. Goods and services: IC 012: Self-driving cars", sig.text)
        self.assertGreaterEqual(classify(sig.text)["fit"], 0.3)
        self.assertEqual(tm.assess(with_goods([("013", "Warheads")])), "")
        self.assertEqual(tm.assess(with_goods([("012", "Unmanned surface vessels")])), "")
        # Live wording: spelled out with no "(VTOL)", the classifier has one
        # term ("vertical take-off"), and the reading is the abbreviation.
        m = with_goods([("012", "vertical takeoff and landing aircraft")])
        self.assertLess(classify("IC 012: vertical takeoff and landing aircraft")["fit"], 0.3)
        self.assertEqual(tm.assess(m), "")
        self.assertEqual((m.label, m.scope), ("VTOL aircraft", "goods"))

    def test_a_reading_that_repeats_the_one_term_does_not_pass(self):
        # Live cases, all dropped: the ID Manual's "machine tools, namely"
        # catch-all, concrete forms, an oil major's power plants.
        for goods in ([("007", "Machine tools, namely, rotary dies for cutting boxes for packaging industry")],
                      [("007", "Machines and machine tools, namely, soil sampling equipment for agricultural purposes")],
                      [("006", "Metal casting forms for concrete")],
                      [("011", "Power plants"), ("040", "Generation of power")],
                      [("009", "Lithium ion batteries")],
                      [("009", "Battery cells")],
                      # "Batteries" is the plural of the same term, not a second one.
                      [("009", "Battery cells"), ("009", "Lithium ion batteries")]):
            self.assertEqual(tm.assess(with_goods(goods)), "below thesis fit", goods)

    def test_the_reading_never_passes_alone_or_for_what_the_applicant_does_not_make(self):
        # Nothing for the classifier in the wording: the label alone is not evidence.
        m = with_goods([("012", "Driverless vehicles")])
        self.assertEqual(tm.assess(m), "below thesis fit")
        self.assertEqual((m.label, m.scope, m.fit), ("autonomous vehicles", "goods", 0.0))
        # One term, and the thesis goods are only what the item is used with,
        # written software for, or serviced.
        for cls, item in [("009", "Cameras for self-driving cars"),
                          ("009", "Cameras for monitoring and inspecting equipment in a nuclear power station"),
                          ("009", "Downloadable software for routing self-driving trucks"),
                          ("042", "Engineering services in the field of power plants")]:
            m = with_goods([(cls, item)])
            self.assertEqual(tm.assess(m), "below thesis fit", item)
            self.assertNotEqual(m.scope, "goods", item)
        # A term the classifier counts alone carries those too: a camera for
        # space vehicles is on thesis as goods related to spacecraft.
        m = with_goods([("009", "Cameras for space vehicles")])
        self.assertEqual(tm.assess(m), "")
        self.assertEqual((m.label, m.scope, tm.covers(m)), ("spacecraft", "related", "for goods related to spacecraft"))

    def test_camera_drone_seller_is_dropped(self):
        self.assertEqual(tm.assess(mark(PEAKEEP)), "mostly consumer goods")

    def test_old_use_based_mark_is_dropped_and_new_itu_kept(self):
        self.assertEqual(tm.assess(mark(ASYLON_OLD)), "mark in use for years")
        self.assertEqual(kept(ASYLON_NEW).wordmark, "ASLYON ROBOTICS")

    def test_drones_in_their_own_class_survive_a_long_services_list(self):
        m = kept(SWRM)
        self.assertEqual((m.label, m.scope), ("unmanned aerial vehicles", "goods"))

    def test_shopping_list_is_dropped(self):
        m = mark(VERIDIS)
        m.goods = [("009", f"Gadget number {i}") for i in range(40)] + [("009", "Humanoid robots")]
        self.assertEqual(tm.assess(m), "thesis goods are a small part of the list")

    def test_thesis_words_only_under_retail_do_not_count(self):
        m = mark(VERIDIS)
        m.goods = [("035", "Retail store services featuring drones"), ("041", "Drone racing events")]
        self.assertEqual(tm.assess(m), "no thesis goods in a qualifying class")

    def test_excerpt_cuts_at_item_boundary(self):
        items = [("012", "Drones"), ("012", "Military drones"), ("009", "Radar apparatus")]
        self.assertEqual(tm.excerpt(items), "IC 012: Drones; Military drones. IC 009: Radar apparatus")
        self.assertEqual(tm.excerpt(items, limit=40), "IC 012: Drones; Military drones ...")


class Requests(unittest.TestCase):
    def test_search_body(self):
        body = tm.search_body(date(2026, 6, 3), TODAY, 250, 500)
        self.assertEqual((body["size"], body["from"]), (250, 500))
        self.assertEqual(body["query"]["bool"]["filter"],
                         [{"range": {"filedDate": {"gte": "2026-06-03", "lte": "2026-10-01"}}}])
        goods, country, entity = body["query"]["bool"]["must"]
        self.assertEqual(goods["query_string"]["fields"], ["goodsAndServices"])
        self.assertIn('"unmanned aerial vehicles"', goods["query_string"]["query"])
        self.assertIn("(nuclear AND (reactor OR", goods["query_string"]["query"])
        self.assertEqual(country["query_string"]["query"], '"UNITED STATES"')
        self.assertEqual(entity["query_string"]["fields"], ["ownerEntity"])
        self.assertEqual(body["sort"][0], {"filedDate": {"order": "desc"}})
        self.assertIn("goodsAndServices", body["_source"])
        json.dumps(body)

    def test_search_body_for_named_owners(self):
        body = tm.search_body(date(2026, 6, 3), TODAY, 250, 0, ["Veridis Defense LLC", "SYLUX INC."])
        owners = body["query"]["bool"]["must"][-1]["bool"]
        self.assertEqual(owners["minimum_should_match"], 1)
        self.assertEqual(owners["should"][1], {"match_phrase": {"ownerName": "SYLUX INC."}})

    def test_history_body_and_parse(self):
        owners = [("Veridis Defense LLC", date(2026, 9, 30)), ("AEROVIRONMENT, INC.", date(2026, 9, 25))]
        body = tm.history_body(owners)
        self.assertEqual(body["size"], 0)
        f = body["aggs"]["owners"]["filters"]["filters"]
        self.assertEqual(f["t0"], {"match_phrase": {"ownerName": "Veridis Defense LLC"}})
        self.assertEqual(f["l1"]["bool"]["must"][1], {"range": {"filedDate": {"lt": "2026-09-25"}}})
        self.assertEqual(f["c0"]["bool"]["must"][0], {"match_phrase": {"ownerName": "Veridis Defense"}})
        # The shape the live API returned for this request.
        resp = {"hits": {"totalValue": 130, "hits": []}, "aggregations": {"owners": {"buckets": {
            "t0": {"doc_count": 1, "first": {"value": 1790726400000, "value_as_string": "2026-09-30"}},
            "l0": {"doc_count": 0, "first": {"value": None}},
            "c0": {"doc_count": 0, "first": {"value": None}},
            "t1": {"doc_count": 129, "first": {"value": 150076800000, "value_as_string": "1974-10-04"}},
            "l1": {"doc_count": 124, "first": {"value": 150076800000, "value_as_string": "1974-10-04"}},
            "c1": {"doc_count": 126, "first": {"value": 150076800000, "value_as_string": "1974-10-04"}},
        }}}}
        got = tm.parse_history(resp, owners)
        self.assertEqual(got["Veridis Defense LLC"], hist(1, "2026-09-30", 0, 0))
        self.assertEqual(got["AEROVIRONMENT, INC."], hist(129, "1974-10-04", 124, 126, "1974-10-04"))

    def test_post_leaves_retries_to_the_http_client(self):
        calls = []

        def answer(result):
            def post(url, body, **kw):
                calls.append(kw)
                if isinstance(result, Exception):
                    raise result
                return result
            return post

        with mock.patch.object(tm.time, "sleep"):
            with mock.patch.object(tm.http, "post_json", side_effect=answer({"hits": {}})):
                self.assertEqual(tm._post({"q": 1}, 60), {"hits": {}})
            # The client retries and honours Retry-After; nothing here turns that off.
            self.assertEqual(calls[-1]["ttl"], 60)
            self.assertGreaterEqual(calls[-1]["retries"], 1)
            with mock.patch.object(tm.http, "post_json", side_effect=answer(http.HttpError(429, tm.SEARCH_URL))):
                with self.assertRaises(tm.RateLimited):
                    tm._post({"q": 1}, 60)
            # Any other failure is the client's last word: one call, no second try here.
            for err in (http.HttpError(503, tm.SEARCH_URL), http.HttpError(403, tm.SEARCH_URL), TimeoutError()):
                del calls[:]
                with mock.patch.object(tm.http, "post_json", side_effect=answer(err)):
                    with self.assertRaises(type(err)):
                        tm._post({"q": 1}, 60)
                self.assertEqual(len(calls), 1)

    def test_post_pauses_after_a_real_request_only(self):
        counts = iter([{"requests": 4}, {"requests": 5}, {"requests": 5}, {"requests": 5}])
        with mock.patch.object(tm.http, "post_json", return_value={}), \
             mock.patch.object(tm.http, "stats", side_effect=lambda: next(counts)), \
             mock.patch.object(tm.time, "sleep") as sleep:
            tm._post({}, 60)   # 4 -> 5 requests: went to the network
            sleep.assert_called_once_with(tm.PAUSE_SECONDS)
            tm._post({}, 60)   # 5 -> 5: served from the cache
            sleep.assert_called_once()

    def test_hits_reads_both_envelopes(self):
        self.assertEqual(tm._hits(RESPONSE)[1], 39)
        self.assertEqual(len(tm._hits(RESPONSE)[0]), 30)
        stock = {"hits": {"total": {"value": 7, "relation": "eq"}, "hits": [{"_source": {"id": "1"}}]}}
        self.assertEqual(tm._hits(stock), ([{"id": "1"}], 7))
        self.assertEqual(tm._hits({"hits": {"total": 3, "hits": []}}), ([], 3))
        self.assertEqual(tm._hits({"hits": {"hits": [{"source": {"id": "2"}}, "junk", {"source": "junk"}, {}]}}),
                         ([{"id": "2"}], None))
        for odd in ({}, {"hits": None}, {"hits": []}, None, {"message": "Forbidden"}):
            self.assertEqual(tm._hits(odd), ([], None))

    def test_last_page(self):
        page = [{}] * tm.PAGE_SIZE
        self.assertTrue(tm._last_page([], 0, 100))
        self.assertFalse(tm._last_page(page, 250, 1233))
        self.assertTrue(tm._last_page(page[:233], 1233, 1233))
        # A server that caps the page below what was asked is not the end.
        self.assertFalse(tm._last_page(page[:100], 100, 1233))
        # No total stated: a full page means more, a short page means done.
        self.assertFalse(tm._last_page(page, 250, None))
        self.assertTrue(tm._last_page(page[:10], 260, None))

    def test_parse_history_skips_missing_buckets(self):
        owners = [("Veridis Defense LLC", date(2026, 9, 30))]
        self.assertEqual(tm.parse_history({}, owners), {})
        self.assertEqual(tm.parse_history({"aggregations": {"owners": {"buckets": {
            "t0": {"doc_count": 0, "first": {"value": None}}, "l0": {"doc_count": 0}, "c0": {"doc_count": 0}}}}},
            owners), {})


class Signals(unittest.TestCase):
    def test_first_filing_signal(self):
        sig = tm.build_signal([kept(VERIDIS)], hist(), TODAY)
        sig.validate()
        self.assertEqual(sig.source, "uspto_trademarks")
        self.assertEqual(sig.family, "regulatory")
        self.assertEqual(sig.kind, "trademark_intent_to_use")
        self.assertEqual(sig.entity.name, "Veridis Defense LLC")
        self.assertIsNone(sig.entity.domain)
        self.assertEqual(sig.entity.location, "Beverly Hills, California")
        self.assertEqual(sig.entity.one_liner, "Trademark goods: Unmanned aerial vehicles (UAVs)")
        self.assertEqual(
            sig.title,
            "Filed intent-to-use trademark VERIDIS DEFENSE for unmanned aerial vehicles, its first trademark filing")
        self.assertEqual(sig.occurred_at, "2026-09-30")
        self.assertEqual(sig.url, kept(VERIDIS).url)
        self.assertEqual((sig.value, sig.unit), (1, "trademark applications"))
        self.assertEqual(
            sig.text,
            "VERIDIS DEFENSE (serial 50139305, filed 2026-09-30, 1b intent to use), "
            "an application for unmanned aerial vehicles. "
            "Goods and services: IC 012: Unmanned aerial vehicles (UAVs)")
        m = sig.metrics
        self.assertEqual(m["tm_serial"], "50139305")
        self.assertTrue(m["tm_first_filing_by_owner"])
        self.assertEqual((m["tm_intent_to_use"], m["tm_in_use"]), (1, 0))
        self.assertEqual(m["tm_owner_first_filed"], "2026-09-30")
        self.assertNotIn("tm_attorney", m)
        self.assertEqual(sig.people, [])

    def test_first_use_is_reported_as_the_applicant_claims_it(self):
        m = with_goods([("012", "Unmanned aerial vehicles (UAVs)")], basis=["1a"])
        m.first_use = date(2026, 3, 1)
        self.assertEqual(tm.assess(m), "")
        sig = tm.build_signal([m], hist(), TODAY)
        self.assertEqual(sig.kind, "trademark_in_use")
        self.assertIn("1a use in commerce, first use claimed 2026-03-01", sig.text)
        self.assertEqual(sig.metrics["tm_first_use_date"], "2026-03-01")
        self.assertTrue(sig.title.startswith("Filed in-use trademark TESTMARK for unmanned aerial vehicles"))

    def test_not_first_when_a_similar_name_filed_before(self):
        sig = tm.build_signal([kept(VERIDIS)], hist(name_before=2), TODAY)
        self.assertFalse(sig.metrics["tm_first_filing_by_owner"])
        self.assertEqual(sig.title, "Filed intent-to-use trademark VERIDIS DEFENSE for unmanned aerial vehicles")

    def test_first_is_not_claimed_for_part_of_a_larger_batch(self):
        # Live case: 2 on-thesis applications among 18 the owner filed that
        # month. They are 2 of its first 18, not "its first 2".
        one = [kept(VERIDIS)]
        self.assertEqual(
            tm.build_signal(one, hist(total=2), TODAY).title,
            "Filed intent-to-use trademark VERIDIS DEFENSE for unmanned aerial vehicles, "
            "one of its first 2 filings")
        marks = sorted((kept(s) for s in XCELL), key=tm.lead_order)
        self.assertEqual(
            tm.title_for(marks, True, 3),
            "Filed its first 3 trademark applications for services related to unmanned aerial vehicles, "
            "all intent to use")
        self.assertEqual(
            tm.title_for(marks, True, 18),
            "Filed 3 of its first 18 trademark applications for services related to unmanned aerial vehicles")
        self.assertNotIn("first", tm.title_for(marks, False, 18))
        sig = tm.build_signal(marks, hist(18, "2026-09-21"), TODAY)
        self.assertTrue(sig.metrics["tm_first_filing_by_owner"])
        self.assertEqual((sig.value, sig.metrics["tm_owner_marks_total"]), (3, 18))
        self.assertIn("3 of its first 18", sig.title)
        # A total below the batch (index lag) never produces "3 of its first 2".
        self.assertIn("its first 3", tm.build_signal(marks, hist(2, "2026-09-21"), TODAY).title)

    def test_goods_are_not_said_of_applications_that_do_not_cover_them(self):
        # Live case: 13 applications in one day, 5 for missiles and 8 for software.
        missiles = [with_goods([("013", "Guided missiles")], wordmark=f"REVERE {i}") for i in range(2)]
        software = with_goods([("009", "Downloadable software for tracking unmanned aerial vehicles")],
                              wordmark="GUARDIAN")
        for i, m in enumerate(missiles + [software]):
            self.assertEqual(tm.assess(m), "")
            m.serial = str(50139305 - i)
        marks = sorted(missiles + [software], key=tm.lead_order)
        self.assertEqual(marks[0].label, "missiles")
        self.assertEqual(
            tm.title_for(marks, True, 3),
            "Filed its first 3 trademark applications in one day, all intent to use, including REVERE 0 for missiles")
        marks[0].wordmark = None
        self.assertEqual(
            tm.title_for(marks, True, 3),
            "Filed its first 3 trademark applications in one day, all intent to use, 2 of them for missiles")
        marks[0].wordmark = "A MARK WITH A VERY LONG NAME THAT CANNOT POSSIBLY FIT IN ONE TITLE"
        self.assertEqual(
            tm.title_for(marks, False, 9),
            "Filed 3 trademark applications in one day, all intent to use, 2 of them for missiles")
        # When every application covers the same goods the batch is described as one.
        missiles = [with_goods([("013", "Guided missiles")], wordmark=f"REVERE {i}") for i in range(2)]
        for m in missiles:
            self.assertEqual(tm.assess(m), "")
        self.assertEqual(
            tm.title_for(missiles, True, 2),
            "Filed its first 2 trademark applications in one day for missiles, all intent to use, including REVERE 0")

    def test_title_never_ends_with_a_slogan_full_stop(self):
        a, b = kept(VERIDIS), kept(VERIDIS)
        a.wordmark, b.wordmark, b.serial = "COMPLEX AUTOMATION. SIMPLE OPERATION.", "ACME", "50139000"
        t = tm.title_for([a, b], False, 2)
        self.assertFalse(t.endswith("."), t)
        self.assertNotIn("including", t)
        a.wordmark = "ACME ONE"
        self.assertTrue(tm.title_for([a, b], False, 2).endswith("including ACME ONE"))

    def test_brands_are_counted_not_applications(self):
        same = [kept(VERIDIS) for _ in range(3)]
        self.assertEqual(tm.distinct_marks(same), 1)
        designs = [kept(VERIDIS) for _ in range(3)]
        for m in designs[1:]:
            m.wordmark = None
        self.assertEqual(tm.distinct_marks(designs), 2)
        sig = tm.build_signal(same, hist(3), TODAY)
        self.assertEqual((sig.metrics["tm_applications"], sig.metrics["tm_distinct_marks"]), (3, 1))

    def test_owner_name_prefers_mixed_case_spelling(self):
        a, b = kept(VERIDIS), kept(VERIDIS)
        a.owner = "VERIDIS DEFENSE LLC"
        self.assertEqual(tm.display_owner([a, b]), "Veridis Defense LLC")
        self.assertEqual(tm.display_owner([a]), "VERIDIS DEFENSE LLC")

    def test_text_says_what_each_application_covers_in_the_titles_words(self):
        missile = with_goods([("013", "Guided missiles")], wordmark="REVERE")
        software = with_goods([("009", "Downloadable software for tracking unmanned aerial vehicles")],
                              wordmark="GUARDIAN")
        service = kept(XCELL[0])
        for i, m in enumerate((missile, software)):
            self.assertEqual(tm.assess(m), "")
            m.serial = str(50139305 - i)
        sig = tm.build_signal([missile, software, service], hist(3), TODAY)
        a, b, c = sig.text.split("\n")
        self.assertIn("1b intent to use), an application for missiles. Goods and services: IC 013", a)
        self.assertIn("an application for software related to unmanned aerial vehicles. Goods and services: IC 009", b)
        self.assertIn("an application for services related to unmanned aerial vehicles. ", c)
        for m in (missile, software, service):
            self.assertIn(tm.covers(m), tm.title_for([m], False))

    def test_family_of_service_marks(self):
        marks = [kept(s) for s in XCELL]
        sig = tm.build_signal(marks, hist(3, "2026-09-21"), TODAY)
        sig.validate()
        self.assertEqual(sig.value, 3)
        self.assertEqual(sig.occurred_at, "2026-09-21")
        self.assertEqual(sig.metrics["tm_serials"], sorted(XCELL, reverse=True))
        self.assertTrue(sig.metrics["tm_family_burst"])
        self.assertEqual(sig.metrics["tm_goods_scope"], "services")
        self.assertEqual(
            sig.title,
            "Filed its first 3 trademark applications for services related to unmanned aerial vehicles, "
            "all intent to use")
        self.assertEqual(sig.url, tm.TSDR_URL.format(serial="50121132"))
        self.assertEqual(sig.metrics["tm_attorney"], "Kari Moyer-Henry")

    def test_titles_obey_the_rules(self):
        for src in HITS:
            m, why = tm.parse_mark(copy.deepcopy(src))
            if not m or tm.assess(m):
                continue
            for first in (True, False):
                t = tm.title_for([m], first)
                self.assertLess(len(t), 110, t)
                self.assertFalse(t.endswith("."), t)
                self.assertTrue(t.startswith("Filed "), t)
                self.assertNotIn("USPTO", t.split(" ")[0])

    def test_design_mark_and_mixed_basis_titles(self):
        m = kept(VERIDIS)
        m.wordmark = None
        self.assertEqual(tm.title_for([m], False), "Filed intent-to-use design mark for unmanned aerial vehicles")
        m = kept(VERIDIS)
        m.basis = ["1a", "1b"]
        self.assertEqual(tm.title_for([m], False), "Filed trademark VERIDIS DEFENSE for unmanned aerial vehicles")

    def test_incumbents_are_dropped(self):
        m = kept(VERIDIS)
        self.assertIsNone(tm.build_signal([m], hist(total=tm.INCUMBENT_MARKS, first="2023-01-01", before=24), TODAY))
        self.assertIsNone(tm.build_signal([m], hist(total=3, first="2012-05-01", before=2), TODAY))
        aero = kept("50130520")  # AEROVIRONMENT, INC.
        self.assertIsNone(tm.build_signal([aero], hist(), TODAY))
        self.assertIsNotNone(tm.build_signal([m], hist(total=6, first="2024-01-01", before=5), TODAY))

    def test_subsidiary_mark_is_dropped(self):
        m = kept(VERIDIS)
        m.wordmark = "SEMIDICE A MICROSS COMPANY"
        self.assertIsNone(tm.build_signal([m], hist(), TODAY))
        m.wordmark = "THE AMERICAN DRONE COMPANY"
        self.assertIsNotNone(tm.build_signal([m], hist(), TODAY))

    def test_strength_ordering(self):
        m = kept(VERIDIS)
        first = tm.strength_for([m], hist(), True, TODAY)
        later = tm.strength_for([m], hist(total=6, first="2024-01-01", before=5), False, TODAY)
        mature = tm.strength_for([m], hist(total=20, first="2020-01-01", before=19), False, TODAY)
        self.assertGreater(first, later)
        self.assertGreater(later, mature)
        services = tm.strength_for([kept(XCELL[0])], hist(), True, TODAY)
        self.assertGreater(first, services)
        brands = [kept(VERIDIS) for _ in range(4)]
        for i, b in enumerate(brands):
            b.wordmark = f"VERIDIS {i}"
        family = tm.strength_for(brands, hist(4), True, TODAY)
        self.assertGreater(family, first)
        self.assertGreaterEqual(family, 0.8)
        # One name filed in four classes is one brand, not a family.
        classes = tm.strength_for([kept(VERIDIS) for _ in range(4)], hist(4), True, TODAY)
        self.assertEqual(classes, first)
        # The top band needs the family to be mostly intent to use.
        for b in brands[1:]:
            b.basis = ["1a"]
        self.assertLess(tm.strength_for(brands, hist(4), True, TODAY), family - 0.1)
        # A foreign priority claim (a parent abroad, or a stealth filing) is
        # marked down and never earns the top band.
        for b in brands:
            b.basis = ["1b", "44d"]
        foreign = tm.strength_for(brands, hist(4), True, TODAY)
        self.assertLess(foreign, family - 0.15)
        self.assertLess(foreign, 0.7)
        for s in (first, later, mature, services, family, classes, foreign):
            self.assertTrue(0.15 <= s <= 1.0, s)
        # One ordinary first filing is solid, not rare.
        self.assertTrue(0.35 <= first <= 0.6, first)

    def test_lead_is_newest_then_closest_to_a_product(self):
        a, b = kept(ASYLON_NEW), kept(VERIDIS)
        self.assertEqual(sorted([a, b], key=tm.lead_order)[0].serial, VERIDIS)
        x = [kept(s) for s in XCELL]
        self.assertEqual(sorted(x, key=tm.lead_order)[0].serial, max(XCELL))


class Collect(unittest.TestCase):
    def test_collect_on_fixture(self):
        sigs, ctx = run_collect()
        self.assertEqual(ctx.warnings, [])
        names = [s.entity.name for s in sigs]
        self.assertEqual(len(names), len(set(names)), "one signal per owner")
        for s in sigs:
            s.validate()
            self.assertLess(len(s.title), 110)
            self.assertTrue(s.url.startswith("https://tsdr.uspto.gov/#caseNumber=" + s.metrics["tm_serial"]))
            self.assertGreaterEqual(s.occurred_at, ctx.since.isoformat())
            self.assertIn(s.metrics["tm_serial"], BY_ID)
            self.assertEqual(s.occurred_at, BY_ID[s.metrics["tm_serial"]]["filedDate"][:10])
            self.assertEqual(BY_ID[s.metrics["tm_serial"]]["ownerName"][0].split(" (")[0], s.entity.name)
        self.assertIn("Veridis Defense LLC", names)
        self.assertIn("PAVE Aerospace, LLC", names)
        # Individuals, camera-drone sellers and established companies are gone.
        for gone in ("Tyrone Cornell Franklin", "US myehome LLC", "MASSAGE CHAIR MAX, INC.",
                     "AEROVIRONMENT, INC.", "Flock Group Inc.", "Firestorm Labs, Inc."):
            self.assertNotIn(gone, names)
        quantum = next(s for s in sigs if s.entity.name == "Quantum Drones Corporation")
        self.assertEqual(quantum.value, 3)
        self.assertEqual(sorted(quantum.metrics["tm_serials"]), ["50123353", "50123358", "50123361"])
        autum = next(s for s in sigs if s.entity.name == "Autum, Inc.")
        self.assertFalse(autum.metrics["tm_first_filing_by_owner"])
        self.assertNotIn("first", autum.title)

    def test_limit_is_respected(self):
        sigs, _ = run_collect(limit=3)
        self.assertEqual(len(sigs), 3)

    def test_outside_window_is_skipped(self):
        ctx = Context(today=TODAY, lookback_days=5)  # since 2026-09-26
        with mock.patch.object(tm.http, "post_json", side_effect=fake_post_json), \
             mock.patch.object(tm.time, "sleep"):
            sigs = list(tm.collect(ctx))
        self.assertTrue(sigs)
        for s in sigs:
            self.assertGreaterEqual(s.occurred_at, "2026-09-26")

    def test_rate_limit_on_search_emits_nothing_and_warns(self):
        def limited(url, body, **kw):
            raise http.HttpError(429, url)
        sigs, ctx = run_collect(post=limited)
        self.assertEqual(sigs, [])
        self.assertTrue(any("rate limited" in w for w in ctx.warnings))

    def test_rate_limit_on_history_emits_nothing_unverified(self):
        def limited(url, body, **kw):
            if "aggs" in body:
                raise http.HttpError(429, url)
            return fake_post_json(url, body, **kw)
        sigs, ctx = run_collect(post=limited)
        self.assertEqual(sigs, [])
        self.assertTrue(any("rate limited" in w for w in ctx.warnings))

    def test_paging_survives_a_response_without_a_total(self):
        def no_total(url, body, **kw):
            resp = fake_post_json(url, body, **kw)
            if "aggs" not in body:
                resp["hits"].pop("totalValue", None)
            return resp
        want, _ = run_collect()
        got, ctx = run_collect(post=no_total)
        self.assertEqual([s.title for s in got], [s.title for s in want])
        self.assertEqual(ctx.warnings, [])

    def test_limit_refetches_each_owner_and_pages_to_the_end(self):
        calls = []
        def counting(url, body, **kw):
            calls.append(body)
            return fake_post_json(url, body, **kw)
        full, _ = run_collect()
        got, ctx = run_collect(limit=3, post=counting)
        self.assertEqual(ctx.warnings, [])
        by_name = {s.entity.name: s for s in full}
        for s in got:
            self.assertEqual(s.title, by_name[s.entity.name].title)
            self.assertEqual(s.metrics["tm_serials"], by_name[s.entity.name].metrics["tm_serials"])
        self.assertLess(len(calls), 12)

    def test_every_emitted_signal_obeys_the_contract(self):
        sigs, ctx = run_collect()
        self.assertGreaterEqual(len(sigs), 5)
        for s in sigs:
            self.assertRegex(s.occurred_at, r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(ctx.since.isoformat() <= s.occurred_at <= TODAY.isoformat())
            self.assertIsNone(s.entity.domain)
            self.assertIsNone(s.entity.github)
            self.assertEqual(s.people, [])
            self.assertTrue(s.title[0].isupper() and not s.title.endswith("."), s.title)
            self.assertNotIn("  ", s.title)
            self.assertTrue(0.15 <= s.strength <= 0.97)
            self.assertEqual(s.value, len(s.metrics["tm_serials"]))
            m = s.metrics
            self.assertGreaterEqual(m["tm_owner_marks_total"], m["tm_applications"])
            if "its first" in s.title and " of its first" not in s.title:
                self.assertTrue(m["tm_first_filing_by_owner"])
                self.assertEqual((m["tm_owner_marks_before"], m["tm_similar_name_marks_before"]), (0, 0))
                self.assertEqual(m["tm_owner_marks_total"], m["tm_applications"])

    def test_one_bad_record_does_not_lose_the_page(self):
        def broken(url, body, **kw):
            resp = fake_post_json(url, body, **kw)
            if "aggs" not in body and resp["hits"]["hits"]:
                resp["hits"]["hits"][0]["source"]["goodsAndServices"] = [42]
            return resp
        sigs, ctx = run_collect(post=broken)
        self.assertTrue(len(sigs) >= 5)
        self.assertEqual(len(ctx.warnings), 1)
        self.assertIn("50139305", ctx.warnings[0])


if __name__ == "__main__":
    unittest.main()
