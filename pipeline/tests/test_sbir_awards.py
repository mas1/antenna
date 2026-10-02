"""Offline tests for the sbir_awards collector. No network: parsing runs against
the saved bulk-CSV fixture and trimmed copies of real www.sbir.gov and
USAspending responses, and collect() runs against a stubbed http module."""

from __future__ import annotations

import json
import re
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from antenna import http as real_http
from antenna.collectors import sbir_awards as sb
from antenna.collectors.base import Context

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "capital"
CSV = FIXTURES / "sbir_award_data_no_abstract_head150.csv"
USA_FIXTURE = FIXTURES / "usaspending_spending_by_award_dod_uas.json"
TODAY = date(2026, 10, 1)
SINCE = date(2025, 8, 27)        # a wide window for the parsing tests
RUN_SINCE = date(2026, 6, 3)     # what collect() uses: ctx.since at the default 120 days
NSF = "National Science Foundation"
DOD = "Department of Defense"
NASA = "National Aeronautics and Space Administration"
DOE = "Department of Energy"
# The fixture's on-thesis Phase I rows, newest first. Seven clear the classifier on
# the title alone with one unmistakable keyword or two terms; six hold one keyword
# the classifier will not take alone and are kept on NSF's topic area (marked *).
FIXTURE_FIRMS = [
    "HEXAspec Inc", "VERDE TECHNOLOGIES INCORPORATED",      # semiconductor; solar *
    "RareTerra, Inc.", "HOMEOSTASIS SYSTEMS CORP",          # rare earth; power infrastructure *
    "Sofab Inks", "FlowCellutions, Inc.",                   # solar *; batteries *
    "MXENE INC", "Neural Solid, Inc.",                      # industrial + reactor; 3D printing *
    "Channel Robotics", "PARALLEL ROBOTICS LLC",            # robotic; robot
    "HaloGen Power, Inc", "CNC CONCRETE HOMES LLC",         # battery + energy; energy storage + energy
    "Heat2Power Inc.",                                      # batteries *
]
N_CSV = len(FIXTURE_FIRMS)       # all thirteen are dated inside the default 120 day window

# ---- trimmed copies of real pages (structure verbatim, 2026-10-01) ----------

SEARCH_HIT = """
<span class="margin-top-2">Showing 1-1 of 1 results</span>
<table class="margin-bottom-4 usa-table--borderless" id="search-results-hits"><tbody class="">
<tr><td><div class="margin-bottom-4">
  <h4 class="margin-top-2 line-height-sans-5"><a href="/awards/{id}">{title}</a></h4>
  <p><i><span><b>SBC:</b> {company} </span>
     <span class="padding-left-05 border-left-05 border-primary-lighter"><b>Topic:</b> CH</span></i></p>
  <p>The broader impact of this project...</p>
  <div class="display-inline-flex flex-row flex-wrap">
    <p class="usa-sr-only">Tagged as:</p>
    <p class="margin-right-1 padding-x-105 bg-base-dark radius-sm text-white text-no-uppercase margin-top-1">{program}</p>
    <p class="margin-right-1 padding-x-105 bg-base-dark radius-sm text-white text-no-uppercase margin-top-1">{phase}</p>
    <p class="margin-right-1 padding-x-105 bg-base-dark radius-sm text-white text-no-uppercase margin-top-1">2026</p>
    <p class="margin-right-1 padding-x-105 bg-base-dark radius-sm text-white text-no-uppercase margin-top-1">NSF</p>
  </div></div></td></tr>
</tbody></table>
"""

AWARD_PAGE = """
<p><a href="/awards">Back to Award Search</a></p>
    <h2>{title}</h2>
    <div class="grid-row">
      <h3 class="margin-bottom-2">Awardee</h3>
      <a href="/portfolio/{portfolio}"><h4 class="margin-bottom-0">{company}</h4></a>
      <address>4376B N 372<br />ATWOOD, OK, 74827-9738<br />USA</address>
      <p><strong>Award Year:</strong> 2026</p>
      <p class="margin-top-0"><strong>UEI:</strong> {uei}</p>
      <p class="margin-top-0"><strong>HUBZone Owned:</strong> Yes</p>
      <div class="display-inline-flex flex-row flex-wrap">
        <p class="usa-sr-only">Tagged as:</p>
        <p class="margin-right-1 padding-x-105 bg-base-dark radius-sm text-white text-no-uppercase margin-top-1">SBIR</p>
        <p class="margin-right-1 padding-x-105 bg-base-dark radius-sm text-white text-no-uppercase margin-top-1">Phase I</p>
      </div>
      <h3>Awarding Agency</h3><p>NSF</p>
      <p class="font-serif-lg text-semibold margin-bottom-2">Total Award Amount: <span class="text-secondary">${amount}</span></p>
      <p class="margin-top-0"><strong>Contract Number:</strong> {contract}</p>
      <p class="margin-top-0"><strong>Agency Tracking Number:</strong> {contract}</p>
    </div>
    <h2 class="desktop:text-center"><span class="border-bottom-05 padding-x-2">Abstract</span></h2>
    <div class="grid-col-12"><p class="measure-none">{abstract}</p></div>
  <h2>Award Schedule</h2>
  <li><p class="usa-step-indicator__segment-label"><strong class="text-secondary">2026</strong><br>Award Year</p></li>
  <li><p class="usa-step-indicator__segment-label"><strong class="text-secondary">{start}</strong><br>Award Start Date</p></li>
  <li><p class="usa-step-indicator__segment-label"><strong class="text-secondary">September 30, 2027</strong><br>Award End Date</p></li>
  <h4>Principal Investigator</h4>
  <p><strong>Name:</strong> Edmon L Perkins<br /><strong>Phone:</strong> 571-357-0844<br /></p>
"""

PORTFOLIO_PAGE = """
<p><a href="/portfolio">Back to Company Search</a></p><h2>Tangible Robotics Inc</h2>
<p><strong>UEI:</strong> JL9QTGMN4AZ9</p>
<h4>SBIR/STTR Involvement</h4>
<p>Year of first award: <span class="text-secondary">{first_year}</span></p>
<div class="grid-col-12 text-center">
  <p class="font-serif-md margin-top-2 text-secondary">1</p>
  <p class="margin-top-05 font-sans-2xs text-bold">Phase I Awards</p>
</div>
<div class="grid-col-12 text-center">
  <p class="font-serif-md margin-top-2 text-secondary">1</p>
  <p class="margin-top-05 font-sans-2xs text-bold">Phase II Awards</p>
</div>
<div class="grid-col-12 text-center">
  <p class="font-serif-md margin-top-2 text-secondary">$399,859</p>
  <p class="margin-top-05 font-sans-2xs text-bold">Phase I Dollars</p>
</div>
"""

# One real row from spending_by_award (keywords=["FA8649"]) and its award record.
USA_ROW = {
    "internal_id": 362606164, "Award ID": "FA864926P0183", "Recipient Name": "DRONENX LLC",
    "Recipient UEI": "HDWKYZ7J8HR6", "Start Date": "2026-06-30", "End Date": "2026-09-30",
    "Award Amount": 72178.0, "Awarding Sub Agency": "Department of the Air Force",
    "Contract Award Type": "PURCHASE ORDER",
    "Description": "SCALING LIGHTWEIGHT, HIGH-CAPACITY, LOW-COST BATTERY MANUFACTURING FOR SUAS LOITERING MUNITIONS",
    "generated_internal_id": "CONT_AWD_FA864926P0183_9700_-NONE-_-NONE-",
    "Base Obligation Date": "2026-06-30",
    "Recipient Location": {"city_name": "EL SEGUNDO", "state_code": "CA", "state_name": "California"},
}
USA_DETAIL = {
    "piid": "FA864926P0183", "type_description": "PURCHASE ORDER", "total_obligation": 72178.0,
    "date_signed": "2026-06-30",
    "recipient": {"recipient_name": "DRONENX LLC", "recipient_uei": "HDWKYZ7J8HR6"},
    "awarding_agency": {"office_agency_name": "FA8649 USAF SBIR STTR CNTRCTNG AFRL"},
}

# Real rows from spending_by_award (recipient_search_text=[UEI], oldest first, 2026-10-01).
# Origami Space: SBIR.gov lists nothing, USAspending lists a $1.6M order four months earlier.
ORIGAMI_HISTORY = {
    "results": [
        {"Award ID": "FA864926P0012", "Recipient Name": "ORIGAMI SPACE DEVELOPMENT, INC.",
         "Recipient UEI": "T42SK4ES67V5", "Award Amount": 1606193.0,
         "Description": "ADAPTIVE MANUFACTURING OF ORIGAMI DEPLOYABLE ANTENNAS",
         "Base Obligation Date": "2026-02-23"},
        {"Award ID": "FA864926P0315", "Recipient Name": "ORIGAMI SPACE DEVELOPMENT, INC.",
         "Recipient UEI": "T42SK4ES67V5", "Award Amount": 109964.0,
         "Description": "EXPANDING TRUSS-LIKE SOLAR ARRAY ARCHITECTURE FOR COMPACT, HIGH-POWER SPACECRAFT",
         "Base Obligation Date": "2026-06-25"},
    ],
    "page_metadata": {"page": 1, "hasNext": False},
}
# Verde Technologies: an Army SBIR Phase I five months before the NSF award SBIR.gov shows as its first.
VERDE_CONTRACTS = {
    "results": [
        {"Award ID": "W5170126CA145", "Recipient Name": "VERDE TECHNOLOGIES INCORPORATED",
         "Recipient UEI": "YNNEUBPBKLM7", "Award Amount": 250000.0,
         "Description": "SMALL BUSINESS INNOVATION RESEARCH (SBIR) PHASE I TOPIC A254-P039, XTECHSEARCH 9.",
         "Base Obligation Date": "2026-03-20"},
    ],
    "page_metadata": {"page": 1, "hasNext": False},
}
EMPTY_HISTORY = {"results": [], "page_metadata": {"page": 1, "hasNext": False}}


# One real NASA Phase I row from the bulk file (2026-10-01), contact columns left out.
NASA_ROW = {
    "Company": "QuesTek Innovations LLC",
    "Award Title": "Multi-scale location-specific fatigue life prediction for additive propulsion components",
    "Agency": NASA, "Branch": "", "Phase": "Phase I", "Program": "SBIR",
    "Agency Tracking Number": "25A1031019", "Contract": "80NSSC25C0319",
    "Proposal Award Date": "2025-09-11", "Contract End Date": "2026-03-27", "Topic Code": "A1",
    "Award Year": "2025", "Award Amount": "149958.0000", "UEI": "JMFVWEY5KLN6", "Duns": "088176961",
    "Number Employees": "42", "Company Website": "https://www.questek.com", "City": "Evanston",
    "State": "Illinois", "PI Name": "Gary  Whelan",
}


def origami_candidate() -> dict:
    return sb.parse_afwerx_row({
        **USA_ROW, "Award ID": "FA864926P0315", "Recipient Name": "ORIGAMI SPACE DEVELOPMENT, INC.",
        "Recipient UEI": "T42SK4ES67V5", "Award Amount": 109964.0, "Base Obligation Date": "2026-06-25",
        "Description": "EXPANDING TRUSS-LIKE SOLAR ARRAY ARCHITECTURE FOR COMPACT, HIGH-POWER SPACECRAFT",
        "generated_internal_id": "CONT_AWD_FA864926P0315_9700_-NONE-_-NONE-"}, SINCE, TODAY)


FIRST = {"status": "first", "prior": 0, "prior_phase2": 0, "name_only_prior": 0,
         "name_only_other_uei": 0, "lifetime": 1}


def first_candidate(company: str) -> dict:
    for c in sb.select_candidates(sb.iter_rows(CSV), SINCE, TODAY):
        if c["row"]["Company"].strip() == company:
            return c
    raise AssertionError(company)


def history_for(cands: list[dict], rows=None) -> dict:
    return sb.collect_history(
        rows if rows is not None else sb.iter_rows(CSV),
        {c["uei"] for c in cands if c["uei"]},
        {c["duns"] for c in cands if c["duns"]},
        {c["name_key"] for c in cands if c["name_key"]},
    )


class TextHelpers(unittest.TestCase):
    def test_program_prefix(self):
        self.assertEqual(sb.strip_program_prefix("SBIR Phase I:  Quantum Oscillator"), "Quantum Oscillator")
        self.assertEqual(sb.strip_program_prefix("STTR Fast-Track: Photonic Fibers"), "Photonic Fibers")
        self.assertEqual(sb.strip_program_prefix("SBIR FastTrack:  Generative AI"), "Generative AI")
        self.assertEqual(sb.strip_program_prefix("Lunar Sentinel"), "Lunar Sentinel")

    def test_title_fit_ignores_program_words_and_false_friends(self):
        # The shared classifier blanks these itself; the collector must not undo that.
        self.assertEqual(sb.title_fit("SBIR Phase I: A Hydrogen Sulfide Delivery Technology")["fit"], 0.0)
        self.assertEqual(sb.title_fit("Compact Hydrogen Peroxide Generator of Enhanced Stability")["fit"], 0.0)
        self.assertEqual(sb.title_fit("Immersive Multi-modal Fusion for Capability Evaluation")["fit"], 0.0)
        self.assertEqual(sb.title_fit("Forecasts of Solar Energetic Particles with MagPy")["fit"], 0.0)
        self.assertGreaterEqual(sb.title_fit("Compact fusion reactor first wall")["fit"], 0.3)
        self.assertEqual(sb.title_fit("Tethered Robot for Coating Application")["sector"], "robotics")
        # The ones only the collector knows: each trips a keyword in the plain classifier.
        for title, word in (("Sensor for hydrogen sulphide leaks", "hydrogen"),
                            ("Forecasting solar flares for grid operators", "solar"),
                            ("Benchtop nuclear magnetic resonance for process control", "nuclear"),
                            ("Solar Blind UV Channel based Wireless Communication System", "solar"),
                            ("Changes for Knowledge Enrichment in Networked Semantics", "enrichment"),
                            # Real NASA rows: the sun as a subject of study, and a gene sequencing kit.
                            ("Interactive Tool for Modeling Multiple Solar Eruptions", "solar"),
                            ("Improved Forecasting of Operational Solar and Geomagnetic Indices", "solar"),
                            ("Time-Dependent Connectivity Mapping of the Solar Magnetic Field", "solar"),
                            ("Improved Forecasting of Solar Particle Events and their Effects on Space Electronics", "solar"),
                            ("Microorganisms Genome Enrichment and Amplification Sequencing (MGEAS)", "enrichment"),
                            ("Track Fusion for Wide Area Surveillance", "fusion")):
            self.assertIn(word, sb.classify(title)["terms"], title)
            self.assertNotIn(word, sb.title_fit(title)["terms"], title)
        # No local rule repeats a phrase the shared list already holds.
        for phrase in sb.config.THESIS_FALSE_FRIENDS:
            self.assertIsNone(sb._FALSE_FRIENDS.search(phrase), phrase)
        # "Strike" is no longer a thesis term, so the bird strike rule that lived here is gone.
        self.assertEqual(sb.classify("Propeller Blade with Bird Strike Resilience")["terms"], [])
        self.assertIsNone(sb._FALSE_FRIENDS.search("Bird Strike"))
        # Solar hardware NASA pays for is untouched by the rules about the sun.
        for title in ("50kW Pneumatic Modular Solar Array STAC Mast", "Solar Sail Tubular Mast",
                      "Extremely power-dense solar for the extremes of space"):
            self.assertEqual(sb.title_fit(title)["terms"], ["solar"], title)

    def test_keyword_the_title_shows_to_mean_something_else(self):
        # Real rows (EPA, EPA, NSF, DOE). The classifier takes "pcb" and "microgrid" alone
        # as unmistakable; here they are a pollutant and a wire mesh inside a display.
        for title, agency, word in (
            ("Machine-learning-assisted development of PCB-free alternatives to commodity pigments",
             "Environmental Protection Agency", "pcb"),
            ("Easy to Apply, Tunable Structural Color: Color Without Pigments, Dyes, Metals, or PCBs",
             "Environmental Protection Agency", "pcb"),
            ("SBIR Phase I: A Transformational Method to Extract Polychlorinated Biphenyls (PCBs) from Building Masonry",
             NSF, "pcb"),
            ("High Performance Substrate Embedded Microgrids for High Efficiency, Flexible Organic Light Emitting Diodes",
             "Department of Energy", "microgrid"),
        ):
            plain = sb.classify(sb.strip_program_prefix(title))
            self.assertEqual((plain["terms"], plain["fit"]), ([word], sb.ONE_KEYWORD_FIT), title)
            self.assertEqual(sb.title_fit(title)["terms"], [], title)
            self.assertIsNone(sb.on_thesis(title, agency, context="SBIR Phase I award."), title)
        # The circuit board and the power grid stay (real rows: NSF, DOE, Navy, DOE).
        for title, word in (
            ("SBIR Phase I:Development of an AI PCB Prototyping Service called FlashPCB", "pcb"),
            ("High Thermal Conductivity, Multi-Layer, AlN-Based PCB Enabling Rapid Prototyping with Embedded Printed Components", "pcb"),
            ("PCB Stator Axial Flux Propulsion Motor for a Deep Operating Unmanned Underwater Vehicle", "pcb"),
            ("Hydrokinetic Baseload Microgrids", "microgrid"),
        ):
            got = sb.on_thesis(title)
            self.assertIn(word, got["terms"], title)
            self.assertNotIn("context", got)

    def test_one_keyword_fit_is_the_classifiers(self):
        # strength_for and the lone-keyword rule both lean on this number.
        self.assertEqual(sb.classify("drone")["fit"], sb.ONE_KEYWORD_FIT)

    def test_lone_keyword_needs_the_sources_own_context(self):
        solar = "SBIR Phase I: Perovskite Solar Cells on Metal Foil Substrates: Enabling Low-Cost and Flexible Solar Technology"
        energy = "NSF SBIR Phase I award. NSF topic area: Energy Technologies."
        # The classifier alone will not take one ambiguous keyword.
        self.assertLess(sb.title_fit(solar)["fit"], sb.MIN_FIT)
        self.assertIsNone(sb.on_thesis(solar, NSF))
        # With what NSF filed it under, it has two terms; the fit stays a one-keyword fit.
        got = sb.on_thesis(solar, NSF, context=energy)
        self.assertEqual((got["fit"], got["sector"], got["terms"], got["context"]),
                         (sb.ONE_KEYWORD_FIT, "energy", ["energy", "solar"], energy))
        # A topic area that says nothing about the thesis does not help ...
        self.assertIsNone(sb.on_thesis(solar, NSF, context="NSF SBIR Phase I award. NSF topic area: Biomedical Technologies."))
        # ... one that repeats the title's own keyword is still one keyword (real row, Galvanix) ...
        self.assertIsNone(sb.on_thesis(
            "SBIR Phase I: Novel Process for Neodymium Manufacturing Using Continuous Chloride Electrolysis", NSF,
            context="NSF SBIR Phase I award. NSF topic area: Advanced Manufacturing."))
        # ... and context never carries a title that has no keyword of its own, or only a weak one.
        # Real rows: a hospital-bed product filed under Semiconductors, a road survey under Energy.
        self.assertIsNone(sb.on_thesis(
            "SBIR Phase I: Reducing Hospital-Acquired Pressure Injuries and Workforce Strain Through "
            "Automated Repositioning Technology", NSF,
            context="NSF SBIR Phase I award. NSF topic area: Semiconductors."))
        self.assertIsNone(sb.on_thesis(
            "SBIR Phase I: AI-enabled Affordable and Autonomous Road Condition Assessment System", NSF, context=energy))
        # A title the classifier takes as it stands is returned untouched.
        plain = sb.on_thesis("SBIR Phase I: High-Energy Primary Battery Chemistry", NSF, context=energy)
        self.assertEqual(plain, sb.title_fit("High-Energy Primary Battery Chemistry"))
        self.assertNotIn("context", plain)

    def test_award_context_is_the_rows_own_columns(self):
        row = {"Agency": NSF, "Branch": "", "Program": "SBIR", "Topic Code": "EN",
               "Award Title": "SBIR Phase I: Perovskite Solar Cells"}
        self.assertEqual(sb.award_context(row), "NSF SBIR Phase I award. NSF topic area: Energy Technologies.")
        self.assertEqual(sb.award_context({**row, "Topic Code": " m ", "Award Title": "STTR Fast-Track: X", "Program": "STTR"}),
                         "NSF STTR Fast-Track award. NSF topic area: Advanced Manufacturing.")
        # A code NSF's list does not hold is not given a name, and other agencies' codes are theirs.
        self.assertEqual(sb.award_context({**row, "Topic Code": "IT"}), "NSF SBIR Phase I award.")
        self.assertIsNone(sb.nsf_topic_area({"Agency": DOD, "Topic Code": "R"}))
        self.assertEqual(sb.award_context({"Agency": DOD, "Branch": "Navy", "Program": "STTR", "Topic Code": "N252-D01"}),
                         "Navy STTR Phase I award.")
        self.assertEqual(sb.award_context({"Agency": NASA, "Program": "SBIR"}), "NASA SBIR Phase I award.")
        self.assertEqual(sb.nsf_topic_area({"Agency": NSF, "Topic Code": "R"}), "Robotics")

    def test_defense_rows_in_the_bulk_file(self):
        navy = "Navy SBIR Phase I award."
        # Real 2025 rows. One hardware keyword and a defence component paying for it: kept.
        for title in ("Radiation Hardened Gallium Nitride Electronics",
                      "Compact CO2 Heat Pump for Shipboard Refrigeration",
                      "Coupled Corrosion and Fatigue Lifing Tool for Aero-Propulsion Components"):
            got = sb.on_thesis(title, DOD, context=navy)
            self.assertEqual(got["fit"], sb.ONE_KEYWORD_FIT, title)
            self.assertIn("navy", got["terms"])
        # Naming the customer in the title is not a subject, with or without a second weak word.
        for title, ctx in (
            ("Improving Military Working Dog Readiness and Performance with a Novel Cooling Technology", "Army SBIR Phase I award."),
            ("DOD Artificial Intelligence Pricing and Supply Chain Risk Management Tool", "DoD SBIR Phase I award."),
            ("Smart Contracts for Navy Supply Chain Risk Management (SCRM)", navy),
            ("Computational Game Theory AI for Army Tactical COA Generation Scenarios", "Army SBIR Phase I award."),
        ):
            self.assertIsNone(sb.on_thesis(title, DOD, context=ctx), title)
        # A funder whose name adds no thesis term leaves a lone keyword where the classifier
        # put it (real rows): out if the keyword is ambiguous ...
        for title, agency, ctx in (
            ("High-Temperature Composite Nozzle Extensions for Space Access and Propulsion", DOD, "DLA SBIR Phase I award."),
            ("Non-Helium Cold Spray System for Nuclear Component Repair and Retrofit", DOE, "DOE SBIR Phase I award."),
        ):
            self.assertEqual(len(sb.title_fit(title)["terms"]), 1, title)
            self.assertIsNone(sb.on_thesis(title, agency, context=ctx), title)
        # ... and in on the title alone if the classifier takes the keyword as unmistakable.
        for title, agency, ctx, word in (
            ("Low-Cost, Portable, Domestic Gallium Production via Molten Salt Electrodeposition", DOD,
             "DLA SBIR Phase I award.", "molten salt"),
            ("Production of Zr Metal by Molten Salt Electrolysis of ZrCl4", DOE, "DOE SBIR Phase I award.", "molten salt"),
            ("AI-Powered Microgrid Database for Quality Decision-Making and Accelerated Adoption", DOE,
             "DOE SBIR Phase I award.", "microgrid"),
        ):
            got = sb.on_thesis(title, agency, context=ctx)
            self.assertEqual((got["fit"], got["terms"]), (sb.ONE_KEYWORD_FIT, [word]), title)
            self.assertNotIn("context", got)

    def test_nasa_rows_in_the_bulk_file(self):
        nasa = "NASA SBIR Phase I award."
        # Real 2025 rows. "Lunar" and "in-space" are unmistakable: the title is enough.
        for title, word in (("Lunar Sentinel", "lunar"),
                            ("Video Compression for Lunar Imaging", "lunar"),
                            ("Compressor for In-Space Gas Transfer and Pressurization", "in-space"),
                            ("The Annular-Wing VTOL Aircraft", "vtol")):
            got = sb.on_thesis(title, NASA, context=nasa)
            self.assertEqual((got["fit"], got["sector"], got["terms"]),
                             (sb.ONE_KEYWORD_FIT, "autonomy" if word == "vtol" else "space", [word]), title)
            self.assertNotIn("context", got)
        # One ambiguous hardware keyword and NASA paying for it: kept on NASA's name, as a
        # Navy row is on the Navy's, and still a one-keyword fit.
        for title, word in (
            ("Multi-scale location-specific fatigue life prediction for additive propulsion components", "propulsion"),
            ("Flexible CMC Structures for Propulsion Efficiency", "propulsion"),
            ("50kW Pneumatic Modular Solar Array STAC Mast", "solar"),
            ("3D Reinforced Composites for Improved Impact Resistance in Spacesuits", "composites"),
            ("Universal Battery Integration Module for Hybrid and Electric Aircraft Powertrains", "battery"),
        ):
            self.assertLess(sb.title_fit(title)["fit"], sb.MIN_FIT, title)
            self.assertIsNone(sb.on_thesis(title, NASA), title)
            got = sb.on_thesis(title, NASA, context=nasa)
            self.assertEqual((got["fit"], got["terms"], got["context"]),
                             (sb.ONE_KEYWORD_FIT, sorted(["nasa", word]), nasa), title)
        # NASA's name carries no title that has no keyword of its own, or only a weak one
        # ("lidar", "star tracker", "autonomous"), nor one about the sun or about biology.
        # All real rows.
        for title in ("Multi-Purpose Flame Retardant Materials for Habitation Solutions",
                      "Long Range LiDAR (LR-LiDAR)",
                      "Event-Driven High Angular Rate Star Tracker",
                      "Plume Impingement Module for Autonomous Proximity Operations",
                      "Interactive Tool for Modeling Multiple Solar Eruptions",
                      "Microorganisms Genome Enrichment and Amplification Sequencing (MGEAS)",
                      "Automated DNA Extraction in Space"):
            self.assertIsNone(sb.on_thesis(title, NASA, context=nasa), title)

    def test_on_thesis_skips_hhs(self):
        title = "A robotic enteroscope for safe and effective access to the small bowel"
        self.assertIsNone(sb.on_thesis(title, "Department of Health and Human Services"))
        self.assertIsNotNone(sb.on_thesis(title, "National Science Foundation"))
        self.assertIsNone(sb.on_thesis("Indigo Eyewear for Treating Myopia", "National Science Foundation"))

    def test_medicine_and_biology_are_off_thesis_whatever_else_the_title_says(self):
        # All real titles that trip a thesis keyword: "defense", "military", "battlefield", "energy",
        # "in-space", "microprocessor", "pcb".
        for title in (
            "SBIR Phase I: A Microprocessor for Complex, Multidimensional Cell Reprogramming: Acoustic-Electric "
            "Micro-Vortices Technology for Precise, Sequential Delivery of Genetic Molecules",
            "PCBS: Portable Cooled Biomedical Storage",
            "BROAD-SPECTRUM PILL FOR VIRAL, BACTERIAL, FUNGAL, AND PROTIST BIOLOGICAL DEFENSE",
            "FOREVR(TM): A SCALABLE DIGITAL THERAPEUTIC TO PREVENT THE NEXT MILITARY OPIOID CRISIS",
            "WARFIGHTER WOUND RECOVERY REIMAGINED: DEPLOYABLE EXTRACELLULAR VESICLE (EV)-BASED THERAPEUTICS",
            "MATRINOVA: A MISSION-READY NON-BIOLOGIC GRAFT FOR BATTLEFIELD DURAL REPAIR",
            "SBIR Phase I: An Energy-Efficient Fermentation Platform to Lower the Cost of Industrial Biomanufacturing",
            "Automated DNA Extraction in Space",
            "NatuWrap: Military-Ready Technology for Reducing Spoilage of Fresh Produce in Storage and Transport",
        ):
            self.assertTrue(sb.classify(sb.strip_program_prefix(title))["terms"], title)
            self.assertIsNone(sb.on_thesis(title), title)
            # Not as an Air Force order either, where one keyword plus the buyer would do.
            self.assertIsNone(sb.on_thesis(title, order=True), title)
        # Six of them clear the classifier outright, so it is this filter that stops them.
        for title in ("A Microprocessor for Complex, Multidimensional Cell Reprogramming: Acoustic-Electric "
                      "Micro-Vortices Technology for Precise, Sequential Delivery of Genetic Molecules",
                      "PCBS: Portable Cooled Biomedical Storage",
                      "Automated DNA Extraction in Space",
                      "BROAD-SPECTRUM PILL FOR VIRAL, BACTERIAL, FUNGAL, AND PROTIST BIOLOGICAL DEFENSE",
                      "WARFIGHTER WOUND RECOVERY REIMAGINED: DEPLOYABLE EXTRACELLULAR VESICLE (EV)-BASED THERAPEUTICS",
                      "An Energy-Efficient Fermentation Platform to Lower the Cost of Industrial Biomanufacturing"):
            self.assertGreaterEqual(sb.classify(title)["fit"], sb.MIN_FIT, title)
        # And one would be kept as an order on "battlefield" plus the buyer.
        self.assertGreaterEqual(sb.classify("MATRINOVA: A MISSION-READY NON-BIOLOGIC GRAFT FOR BATTLEFIELD DURAL REPAIR. "
                                            + sb.ORDER_CONTEXT)["fit"], sb.MIN_FIT)
        # The filter is narrow: these say "health" and "fatigue" and are not medicine.
        self.assertIsNotNone(sb.on_thesis(
            "EXPLAINABLE ARTIFICIAL INTELLIGENCE AGENTS FOR REAL-TIME MODEL HEALTH MONITORING IN UNMANNED AIRCRAFT SYSTEM ECOSYSTEMS",
            order=True))
        self.assertIsNotNone(sb.on_thesis(
            "Coupled Corrosion and Fatigue Lifing Tool for Aero-Propulsion Components", DOD,
            context="Navy SBIR Phase I award."))
        self.assertIsNotNone(sb.on_thesis(
            "SBIR Phase I: A Novel Architecture for blending Human Power and Computer Control in Surgical Robots"))

    def test_cbrn_is_not_nuclear_energy(self):
        t = "CHEMICAL, BIOLOGICAL, RADIOLOGICAL, NUCLEAR, AND EXPLOSIVE DETECTION"
        self.assertNotIn("nuclear", sb.title_fit(t)["terms"])
        self.assertIn("nuclear", sb.title_fit("Heat Exchanger for Nuclear Energy Applications")["terms"])

    def test_order_descriptions_earn_nothing_for_naming_the_customer(self):
        # Every FA8649 order is bought by the Air Force; that is not what the firm builds.
        for desc in (
            "OMNI MISSION PLANNER: ENHANCING WARFIGHTER READINESS AND DECISION-MAKING",
            "DUAL-USE INTELLIGENT ROUTING AND DATA TRANSPORT SOFTWARE FOR THE HYPER-ENABLED WARFIGHTER IN ALL ENVIRONMENTS",
            # Two weak words, one of them the Pentagon's name for its own suppliers.
            "FINCH AI FOR SUPPLY CHAIN RISK MANAGEMENT AND FOREIGN INFLUENCE DETECTION WITHIN THE U.S. INDUSTRIAL BASE",
        ):
            self.assertIsNotNone(sb.on_thesis(desc), desc)            # the plain reading passes
            self.assertIsNone(sb.on_thesis(desc, order=True), desc)   # the order reading does not
        # "Military" alone no longer clears the classifier, but left in place it would be
        # the one keyword the buyer's name then backs up. The customer is not counted twice.
        evrim = ("SCALED OPEN SOURCE DATA COLLECTION FOR STRATEGIC MEETING TRACKING ACROSS MILITARY, "
                 "POLITICAL, AND ECONOMIC SPHERES OF INFLUENCE")
        self.assertGreaterEqual(sb.classify(f"{evrim}. {sb.ORDER_CONTEXT}")["fit"], sb.MIN_FIT)
        self.assertIsNone(sb.on_thesis(evrim, order=True))
        # A solicitation's own name is not a description of anything.
        self.assertIsNone(sb.on_thesis(
            "OPEN TOPIC PHASE 1 CALL FOR INNOVATIVE DEFENSE-RELATED DUAL-PURPOSE TECHNOLOGIES/SOLUTIONS "
            "WITH A CLEAR AIR FORCE STAKEHOLDER NEED", order=True))
        # Real subjects survive, including defence hardware.
        for desc in ("ENHANCING USAF EFFICIENCY AND SPEED WITH A BACK SUPPORT EXOSKELETON",
                     "RADIATION-TOLERANT ADVANCED DEFENSE INTERCEPTOR UPPER STAGE (RADIUS)",
                     "DUAL-USE FIBER OPTIC SENSOR SUITE FOR IN SITU STRUCTURAL AND THERMAL MONITORING IN HYPERSONIC AND SPACE SYSTEMS"):
            self.assertIsNotNone(sb.on_thesis(desc, order=True), desc)

    def test_order_with_one_hardware_keyword_is_kept_on_the_buyers_word(self):
        # Real orders. The classifier wants a second term for "turbine", "solar", "unmanned"
        # or "manufacturing"; that the Air Force placed the order is that term.
        for desc, sector in (("HYBRID ELECTRIC TURBINE ENGINE FOR SUSTAINABLE AVIATION", "energy"),
                             ("SPARROW - SOLAR POWER ARRAYS FOR RESILIENT RECONNAISSANCE AND OPERATIONAL WINGS", "energy"),
                             ("RESILIENT BLOS AND LOS CONNECTIVITY FOR UNMANNED SYSTEMS", "autonomy"),
                             ("NOVEL MANUFACTURING PLATFORM FOR SUB-WAVELENGTH PATTERNING OF METAMATERIALS", "manufacturing")):
            self.assertLess(sb.title_fit(desc, order=True)["fit"], sb.MIN_FIT, desc)
            got = sb.on_thesis(desc, order=True)
            self.assertEqual((got["fit"], got["sector"], got["context"]),
                             (sb.ONE_KEYWORD_FIT, sector, "Air Force SBIR/STTR order"), desc)
            self.assertIn("air force", got["terms"])
        # "Microgrid" and "radar" the classifier takes alone as unmistakable hardware, so
        # these real orders stand on their own description and the buyer is not added.
        for desc, sector, word in (
            ("AMPHORA EXPEDITIONARY MICROGRID", "energy", "microgrid"),
            ("ENHANCED RADAR", "defense", "radar"),
            ("TRANSPOSE-DOMAIN SPACE-TIME ADAPTIVE PROCESSING AND ADAPTIVE CLUTTER SUPPRESSION FOR "
             "WINDFARM-RESILIENT INFILL RADAR", "defense", "radar"),
        ):
            got = sb.on_thesis(desc, order=True)
            self.assertEqual((got["fit"], got["sector"], got["terms"]), (sb.ONE_KEYWORD_FIT, sector, [word]), desc)
            self.assertNotIn("context", got)
        # One weak word and the buyer is two weak words: still not a subject.
        for desc in ("RAPID INTERNAL THREAT DETECTION AT THE TACTICAL EDGE",
                     "FRACTAL AGENT SWARM (FAS)", "LIVE MOVEMENT TRACKING IN GPS DENIED ENVIRONMENT"):
            self.assertTrue(sb.title_fit(desc, order=True)["terms"], desc)
            self.assertGreaterEqual(sb.classify(f"{desc}. {sb.ORDER_CONTEXT}")["fit"], sb.MIN_FIT, desc)
            self.assertIsNone(sb.on_thesis(desc, order=True), desc)
        # An order that clears the classifier by itself keeps its own fit, buyer not added.
        full = sb.on_thesis("CUAS USING EXISTING BROADCAST INFRASTRUCTURE TOWERS (CUEBIT)", order=True)
        self.assertNotIn("air force", full["terms"])
        self.assertNotIn("context", full)

    def test_fmt_usd(self):
        self.assertEqual(sb.fmt_usd(304526), "$305K")
        self.assertEqual(sb.fmt_usd(72178), "$72K")
        self.assertEqual(sb.fmt_usd(1554959), "$1.55M")
        self.assertEqual(sb.fmt_usd(1250000), "$1.25M")
        self.assertEqual(sb.fmt_usd(999600), "$1M")
        self.assertEqual(sb.fmt_usd(850), "$850")

    def test_readable_only_touches_all_caps(self):
        self.assertEqual(sb.readable("Snake Paint-R: Robotic Coating"), "Snake Paint-R: Robotic Coating")
        self.assertEqual(sb.readable("LOW COST AI-ENABLED DRONE AGGRESSORS (LCAIDA)"),
                         "Low cost AI-enabled drone aggressors (LCAIDA)")
        self.assertEqual(sb.readable("MTC SCOUT: ULTRA-AGILE, LOW-COST SWARM UGV FOR ISR"),
                         "MTC SCOUT: ultra-agile, low-cost swarm UGV for ISR")
        self.assertEqual(sb.readable("SIMULATOR FOR ADVANCING COUNTER UAS CAPABILITIES"),
                         "Simulator for advancing counter UAS capabilities")

    def test_readable_prefixes_designators_and_numbers(self):
        self.assertEqual(sb.readable("PRODUCTION OF NEXT-GEN X-BAND SOLID-STATE POWER AMPLIFIERS (SSPA) FOR USAF RADAR"),
                         "Production of next-gen X-band solid-state power amplifiers (SSPA) for USAF radar")
        self.assertEqual(sb.readable("ATAK INTEGRATION FOR REAL TIME RE-TASKING OF HEAVY CARGO UAS"),
                         "ATAK integration for real time re-tasking of heavy cargo UAS")
        self.assertEqual(sb.readable("WIDE OPERATING TEMPERATURE LI-S BATTERIES VS LI-ION"),
                         "Wide operating temperature Li-S batteries vs Li-ion")
        self.assertEqual(sb.readable("HYBRID EVTOL AUTONOMOUS CARGO AIRCRAFT"), "Hybrid eVTOL autonomous cargo aircraft")
        self.assertEqual(sb.readable("100% SILICON ANODES MATCHED WITH N-METHYL PYRROLIDONE"),
                         "100% silicon anodes matched with N-methyl pyrrolidone")

    def test_split_dba(self):
        self.assertEqual(sb.split_dba("Padco Industries, LLC DBA DEM Manufacturing"),
                         ("Padco Industries, LLC", ["DEM Manufacturing"]))
        self.assertEqual(sb.split_dba("RESDEF INC. (DBA Frontier Audio Labs)"),
                         ("RESDEF INC.", ["Frontier Audio Labs"]))
        self.assertEqual(sb.split_dba("Acme Corp d/b/a Rocket Works"), ("Acme Corp", ["Rocket Works"]))
        # Not a trade-name marker: part of the name itself.
        self.assertEqual(sb.split_dba("DBA Systems Inc"), ("DBA Systems Inc", []))
        self.assertEqual(sb.split_dba("Adba Robotics LLC"), ("Adba Robotics LLC", []))
        self.assertEqual(sb.split_dba("  HEXAspec   Inc "), ("HEXAspec Inc", []))

    def test_person_name_refuses_placeholders(self):
        self.assertEqual(sb.person_name(' Jeff "Jack"  Hanson '), 'Jeff "Jack" Hanson')
        self.assertEqual(sb.person_name("Na Li"), "Na Li")
        self.assertIsNone(sb.person_name("FNU Vedant"))     # "first name unknown", real row
        self.assertIsNone(sb.person_name("TBD"))
        self.assertIsNone(sb.person_name("Smith"))
        self.assertIsNone(sb.person_name(""))

    def test_clip(self):
        self.assertEqual(sb.clip("Lunar Sentinel.", 40), "Lunar Sentinel")
        out = sb.clip("Advanced Hexagonal Boron Nitride Composite Semiconductor Packaging for Enhanced Performance", 50)
        self.assertLessEqual(len(out), 50)
        self.assertTrue(out.endswith("…"))
        self.assertFalse(out[:-1].endswith(" "))

    def test_firm_key_is_strict(self):
        self.assertEqual(sb.firm_key("REACH POWER, INC."), sb.firm_key("Reach Power, Inc"))
        self.assertEqual(sb.firm_key("Creare L.L.C."), "creare")
        # Unlike base.normalize_name, descriptive words stay: these are different firms.
        self.assertNotEqual(sb.firm_key("Cosmic Robotics, Inc."), sb.firm_key("Cosmic Systems LLC"))

    def test_website_domain(self):
        self.assertEqual(sb.website_domain("Https://www.soartech.com"), "soartech.com")
        self.assertEqual(sb.website_domain("htpps://www.creativemicro.com"), "creativemicro.com")
        self.assertEqual(sb.website_domain("www://brimrosetechnology.com"), "brimrosetechnology.com")
        self.assertEqual(sb.website_domain("https://www.gosage.com."), "gosage.com")
        self.assertIsNone(sb.website_domain("maxpower@maxpowerinc.com"))
        self.assertIsNone(sb.website_domain("Gateway Bio, Inc."))
        self.assertIsNone(sb.website_domain("www. maher-associates.com"))
        self.assertIsNone(sb.website_domain("1976"))
        self.assertIsNone(sb.website_domain(""))

    def test_firm_domain_never_trusts_a_stranger(self):
        # Real case: the CSV lists a fellowship programme's site for this firm.
        self.assertEqual(sb.firm_domain("activate.org", "x@topolightinc.com", "TopoLight, Inc."),
                         "topolightinc.com")
        # Brand differs from legal name, but the PI's mail is on the same domain.
        self.assertEqual(sb.firm_domain("https://ixana.ai", "pi@ixana.ai", "QUASISTATICS INC."), "ixana.ai")
        self.assertEqual(sb.firm_domain("www.crgdefense.com", "a@crgrp.com", "CORNERSTONE RESEARCH GROUP INC"),
                         "crgdefense.com")
        self.assertEqual(sb.firm_domain("", "israel@yadonai.com", "Yadonai LLC"), "yadonai.com")
        # Real case: this row's PI email belongs to a different company.
        self.assertIsNone(sb.firm_domain("", "zpleolu@gppert.com", "SPECTREE INC."))
        self.assertIsNone(sb.firm_domain("", "mershin@gmail.com", "RealNose Inc"))
        self.assertIsNone(sb.firm_domain("www.berkeley.edu", "x@berkeley.edu", "Berkeley Sensors"))
        self.assertIsNone(sb.firm_domain("https://www.afrl.af.mil", "x@us.af.mil", "AFRL Sensors"))
        # Mailbox providers: the shared cleaner knows the big ones, the collector the rest.
        for host in ("gmail.com", "outlook.com", "hotmail.com", "comcast.net", "pm.me"):
            self.assertIsNone(sb.email_domain(f"pi@{host}"), host)
        # A shared site builder is nobody's own domain.
        self.assertIsNone(sb.firm_domain("https://acme-robotics.wixsite.com/home", "", "Acme Robotics"))
        self.assertIsNone(sb.firm_domain("advancedmaterials.com", "", "Advanced Space LLC"))


class BulkCsv(unittest.TestCase):
    def test_select_candidates_from_fixture(self):
        cands = sb.select_candidates(sb.iter_rows(CSV), SINCE, TODAY)
        names = [c["row"]["Company"].strip() for c in cands]
        self.assertEqual(names, FIXTURE_FIRMS)
        # A fermentation platform cleared the keyword bar on "energy" and "industrial".
        self.assertNotIn("IZOTE BIOSCIENCES INC.", names)
        # Kept on one title keyword plus NSF's topic area, and every candidate carries the sentence.
        backed = {c["row"]["Company"].strip(): c["fit"]["context"] for c in cands if "context" in c["fit"]}
        energy = "NSF SBIR Phase I award. NSF topic area: Energy Technologies."
        self.assertEqual(backed, {
            "VERDE TECHNOLOGIES INCORPORATED": energy, "Sofab Inks": energy, "Heat2Power Inc.": energy,
            "FlowCellutions, Inc.": "NSF SBIR Fast-Track award. NSF topic area: Energy Technologies.",
            "HOMEOSTASIS SYSTEMS CORP": "NSF SBIR Phase I award. NSF topic area: Advanced Manufacturing.",
            "Neural Solid, Inc.": "NSF SBIR Fast-Track award. NSF topic area: Advanced Manufacturing.",
        })
        self.assertTrue(all(c["fit"]["fit"] == sb.ONE_KEYWORD_FIT for c in cands if "context" in c["fit"]))
        self.assertTrue(all(c["context"].startswith("NSF SBIR ") for c in cands))
        # One weak word under a topic area is not enough (real rows: an RF detector, a road
        # survey, a bearingless motor with no keyword at all under Energy Technologies).
        for firm in ("AK INNOVATIONS LLC", "PaveX LLC", "MOTIBERA, INC.", "TAKACHAR LIMITED"):
            self.assertNotIn(firm, names)
        for c in cands:
            self.assertEqual(c["row"]["Phase"], "Phase I")
            self.assertGreaterEqual(c["fit"]["fit"], sb.MIN_FIT)
            self.assertTrue(SINCE.isoformat() <= c["date"] <= TODAY.isoformat())
        hexa = cands[0]
        self.assertEqual((hexa["date"], hexa["amount"], hexa["uei"]), ("2026-08-24", 1554959.0, "LUYZNGL2VUE3"))
        # Phase II rows on thesis are not candidates (L5 Automation, Juno Propulsion).
        self.assertNotIn("L5 AUTOMATION INC.", names)
        self.assertNotIn("JUNO PROPULSION INC.", names)

    def test_window_is_respected(self):
        late = sb.select_candidates(sb.iter_rows(CSV), date(2026, 7, 10), TODAY)
        self.assertEqual(len(late), 8)
        self.assertEqual(min(c["date"] for c in late), "2026-07-21")
        # Both ends are inclusive, and nothing dated after today gets in.
        self.assertEqual([c["date"] for c in sb.select_candidates(sb.iter_rows(CSV), date(2026, 8, 24), date(2026, 8, 24))],
                         ["2026-08-24"])
        self.assertEqual(sb.select_candidates(sb.iter_rows(CSV), date(2026, 8, 25), TODAY), [])
        self.assertEqual(sb.select_candidates(sb.iter_rows(CSV), SINCE, date(2026, 6, 20))[0]["row"]["Company"],
                         "Heat2Power Inc.")

    def test_date_code(self):
        self.assertEqual(sb.date_code({"Proposal Award Date": "2026-08-24", "Award Year": "2026"}), 20260824)
        # Undated legacy rows sort to the start of their fiscal year.
        self.assertEqual(sb.date_code({"Proposal Award Date": "", "Award Year": "2003"}), 20021001)
        self.assertIsNone(sb.date_code({"Proposal Award Date": "", "Award Year": ""}))

    def test_fixture_firms_are_first_awards(self):
        cands = sb.select_candidates(sb.iter_rows(CSV), SINCE, TODAY)
        hist = history_for(cands)
        for c in cands:
            st = sb.award_standing(hist, c["uei"], c["duns"], c["name_key"], c["code"], c["row_id"])
            self.assertEqual((st["status"], st["prior"], st["lifetime"]), ("first", 0, 1))

    def test_standing_repeat_unclear_and_same_day(self):
        def row(company, uei, duns, when, year, phase="Phase I", contract="X"):
            return {"Company": company, "UEI": uei, "Duns": duns, "Proposal Award Date": when,
                    "Award Year": year, "Phase": phase, "Contract": contract, "Company Website": "",
                    "PI Email": "", "Number Employees": "7"}

        rows = [
            row("Acme Robotics, Inc.", "AAA111", "", "2026-08-01", "2026"),          # 0 the candidate
            row("Acme Robotics, Inc.", "AAA111", "", "2026-08-01", "2026"),          # 1 same day
            row("ACME ROBOTICS INC", "AAA111", "123", "2024-03-05", "2024", "Phase II"),  # 2 same UEI
            row("Acme Robotics Inc", "", "123", "", "2009"),                         # 3 legacy via DUNS chain? no: DUNS 123
            row("Acme Robotics", "", "", "", "1998"),                                # 4 legacy, name only
            row("Acme Robotics LLC", "ZZZ999", "", "2019-01-01", "2019"),            # 5 same name, other UEI
            row("Bolt Drones Inc", "BBB222", "", "2026-07-01", "2026"),              # 6 candidate
            row("Bolt Drones, Inc.", "CCC333", "", "2021-01-01", "2021"),            # 7 name only, other UEI
            row("Solo Fusion Inc", "DDD444", "", "2026-07-02", "2026"),              # 8 candidate, alone
            row("Solo Fusion Inc", "DDD444", "", "2026-09-02", "2026", "Phase II"),  # 9 later award
        ]
        hist = sb.collect_history(rows, {"AAA111", "BBB222", "DDD444"}, {"123"},
                                  {"acme robotics", "bolt drones", "solo fusion"})
        acme = sb.award_standing(hist, "AAA111", "", "acme robotics", 20260801, 0)
        # Earlier: row 2 (UEI) and row 4 (legacy, no UEI). Row 3 hangs off a DUNS this
        # candidate row does not carry, so it arrives as a name match without a UEI too.
        self.assertEqual(acme["status"], "repeat")
        self.assertEqual(acme["prior"], 3)
        self.assertEqual(acme["prior_phase2"], 1)
        self.assertEqual(acme["name_only_other_uei"], 1)   # row 5 is reported, never counted

        bolt = sb.award_standing(hist, "BBB222", "", "bolt drones", 20260701, 6)
        self.assertEqual((bolt["status"], bolt["prior"], bolt["name_only_other_uei"]), ("unclear", 0, 1))

        solo = sb.award_standing(hist, "DDD444", "", "solo fusion", 20260702, 8)
        self.assertEqual((solo["status"], solo["prior"], solo["lifetime"]), ("first", 0, 2))

        # A firm that is not in the file at all (the AFWERX case).
        self.assertEqual(sb.award_standing(hist, "NEW000", "", "brand new", 20260630)["status"], "first")

    def test_history_profile_and_afwerx_contracts(self):
        rows = [
            {"Company": "Acme", "UEI": "aaa111", "Duns": "", "Proposal Award Date": "2025-09-01",
             "Award Year": "2025", "Phase": "Phase II", "Contract": "FA8649-25-P-0123",
             "Company Website": "acme.com", "PI Email": "pi@acme.com", "Number Employees": "12"},
            {"Company": "Other", "UEI": "OOO", "Duns": "", "Proposal Award Date": "2025-09-01",
             "Award Year": "2025", "Phase": "Phase I", "Contract": "N68335-25-C-0001",
             "Company Website": "", "PI Email": "", "Number Employees": "3"},
        ]
        hist = sb.collect_history(rows, {"AAA111"}, set(), set())
        self.assertEqual(hist["afwerx_contracts"], {"FA864925P0123"})
        self.assertEqual(hist["profile"]["AAA111"]["employees"], 12)
        self.assertNotIn("OOO", hist["uei"])


class SbirGovPages(unittest.TestCase):
    def page(self, **kw):
        base = dict(title="SBIR Phase I: Quantum Oscillator Processing Unit", portfolio="2285305",
                    company="LAB2701 LLC", uei="RS4ATN5KZXS4", amount="304,526", contract="2605035",
                    abstract="The broader impact is quantum Oscillator Processing Units &amp; Moore&#039;s Law.",
                    start="August 24, 2026")
        base.update(kw)
        return AWARD_PAGE.format(**base)

    def cand(self, **kw):
        row = {"Company": "LAB2701 LLC", "Contract": "2605035", "Agency Tracking Number": "2605035",
               "Award Title": "SBIR Phase I: Quantum Oscillator Processing Unit"}
        c = {"row": row, "uei": "RS4ATN5KZXS4", "name_key": "lab2701", "amount": 304526.0, "date": "2026-08-24"}
        c.update(kw)
        return c

    def test_parse_search_results_and_pick(self):
        html_page = SEARCH_HIT.format(id="220819", title="SBIR Phase I: Quantum Oscillator Processing Unit",
                                      company="LAB2701 LLC", program="SBIR", phase="Phase I")
        hits = sb.parse_search_results(html_page)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["id"], "220819")
        self.assertEqual(hits[0]["company"], "LAB2701 LLC")
        self.assertEqual(hits[0]["tags"], ["SBIR", "Phase I", "2026", "NSF"])
        self.assertEqual(sb.pick_hit(hits, "LAB2701 LLC", "whatever")["id"], "220819")
        self.assertIsNone(sb.pick_hit(hits, "Some Other Firm Inc", "whatever"))
        self.assertEqual(sb.parse_search_results("<p>No results found.</p>"), [])

    def test_pick_refuses_phase_two_and_ambiguity(self):
        one = SEARCH_HIT.format(id="1", title="Reactor A", company="DOE Firm LLC", program="SBIR", phase="Phase II")
        two = SEARCH_HIT.format(id="2", title="Reactor A", company="DOE Firm LLC", program="SBIR", phase="Phase I")
        three = SEARCH_HIT.format(id="3", title="Reactor B", company="DOE Firm LLC", program="SBIR", phase="Phase I")
        hits = sb.parse_search_results(one + two)
        self.assertEqual(sb.pick_hit(hits, "DOE Firm LLC", "Reactor A")["id"], "2")
        hits = sb.parse_search_results(one + two + three)
        self.assertEqual(sb.pick_hit(hits, "DOE Firm LLC", "Reactor B")["id"], "3")
        self.assertIsNone(sb.pick_hit(hits, "DOE Firm LLC", "Reactor C"))

    def test_parse_award_page(self):
        info = sb.parse_award_page(self.page())
        self.assertEqual(info["title"], "SBIR Phase I: Quantum Oscillator Processing Unit")
        self.assertEqual(info["company"], "LAB2701 LLC")
        self.assertEqual(info["portfolio_id"], "2285305")
        self.assertEqual(info["uei"], "RS4ATN5KZXS4")
        self.assertEqual(info["amount"], 304526.0)
        self.assertEqual(info["contract"], "2605035")
        self.assertEqual(info["start_date"], "2026-08-24")
        self.assertEqual(info["abstract"], "The broader impact is quantum Oscillator Processing Units & Moore's Law.")
        self.assertEqual(info["tags"], ["SBIR", "Phase I"])

    def test_page_agrees(self):
        info = sb.parse_award_page(self.page())
        self.assertIsNone(sb.page_agrees(info, self.cand()))
        self.assertIn("amount", sb.page_agrees(info, self.cand(amount=304000.0)))
        self.assertIn("date", sb.page_agrees(info, self.cand(date="2026-08-25")))
        self.assertIn("UEI", sb.page_agrees(info, self.cand(uei="OTHERUEI0000")))
        self.assertIn("firm", sb.page_agrees(info, self.cand(name_key="someone else")))
        other = self.cand()
        other["row"] = {**other["row"], "Contract": "999", "Agency Tracking Number": "998"}
        self.assertIn("contract", sb.page_agrees(info, other))
        # DoD contract numbers are written with and without dashes.
        dod = sb.parse_award_page(self.page(contract="FA8222-26-P-B005"))
        c = self.cand()
        c["row"] = {**c["row"], "Contract": "FA822226PB005", "Agency Tracking Number": "F254-0808-0575"}
        self.assertIsNone(sb.page_agrees(dod, c))

    def test_parse_portfolio_page(self):
        prof = sb.parse_portfolio_page(PORTFOLIO_PAGE.format(first_year="2025"))
        self.assertEqual(prof, {"first_year": 2025, "phase1": 1, "phase2": 1})
        self.assertEqual(sb.parse_portfolio_page("<p>nothing</p>"), {"first_year": None, "phase1": None, "phase2": None})


class UsaSpending(unittest.TestCase):
    def test_fixture_rows_from_other_offices_are_rejected(self):
        rows = json.loads(USA_FIXTURE.read_text())["response"]["results"]
        self.assertGreater(len(rows), 5)
        self.assertEqual([sb.parse_afwerx_row(r, SINCE, TODAY) for r in rows], [None] * len(rows))

    def test_parse_afwerx_row(self):
        c = sb.parse_afwerx_row(USA_ROW, SINCE, TODAY)
        self.assertEqual(c["award_id"], "FA864926P0183")
        self.assertEqual(c["name"], "DRONENX LLC")
        self.assertEqual(c["date"], "2026-06-30")
        self.assertEqual(c["amount"], 72178.0)
        self.assertEqual(c["location"], "El Segundo, California")
        self.assertGreaterEqual(c["fit"]["fit"], sb.MIN_FIT)
        # Phase II sized, outside the window, off thesis, or missing the award id: all dropped.
        self.assertIsNone(sb.parse_afwerx_row({**USA_ROW, "Award Amount": 1249999.0}, SINCE, TODAY))
        self.assertIsNone(sb.parse_afwerx_row({**USA_ROW, "Base Obligation Date": "2025-01-01"}, SINCE, TODAY))
        self.assertIsNone(sb.parse_afwerx_row({**USA_ROW, "Base Obligation Date": None}, SINCE, TODAY))
        self.assertIsNone(sb.parse_afwerx_row({**USA_ROW, "Description": "VIRTUAL TRAINING FOR NURSES"}, SINCE, TODAY))
        self.assertIsNone(sb.parse_afwerx_row({**USA_ROW, "generated_internal_id": None}, SINCE, TODAY))
        # Outside the run's own window (collect passes ctx.since), and after today.
        self.assertIsNone(sb.parse_afwerx_row({**USA_ROW, "Base Obligation Date": "2026-06-02"}, RUN_SINCE, TODAY))
        self.assertIsNotNone(sb.parse_afwerx_row({**USA_ROW, "Base Obligation Date": "2026-06-03"}, RUN_SINCE, TODAY))
        self.assertIsNone(sb.parse_afwerx_row({**USA_ROW, "Base Obligation Date": "2026-10-02"}, RUN_SINCE, TODAY))
        # A malformed location must not take the row down with it.
        self.assertIsNone(sb.parse_afwerx_row({**USA_ROW, "Recipient Location": None}, SINCE, TODAY)["location"])
        # Medicine, customer words alone, and the solicitation's own name are not subjects.
        for desc in ("BROAD-SPECTRUM PILL FOR VIRAL, BACTERIAL, FUNGAL, AND PROTIST BIOLOGICAL DEFENSE",
                     "THE FUTURE OF MILITARY AND COMMERCIAL COMMUNICATIONS",
                     "OPEN TOPIC PHASE 1 CALL FOR INNOVATIVE DEFENSE-RELATED DUAL-PURPOSE TECHNOLOGIES"):
            self.assertIsNone(sb.parse_afwerx_row({**USA_ROW, "Description": desc}, SINCE, TODAY), desc)

    def test_future_start_date_does_not_matter(self):
        # Start Date is period of performance; the obligation date is the event.
        c = sb.parse_afwerx_row({**USA_ROW, "Start Date": "2027-01-01"}, SINCE, TODAY)
        self.assertEqual(c["date"], "2026-06-30")

    def test_detail_agrees(self):
        c = sb.parse_afwerx_row(USA_ROW, SINCE, TODAY)
        self.assertIsNone(sb.detail_agrees(USA_DETAIL, c))
        self.assertIn("office", sb.detail_agrees(
            {**USA_DETAIL, "awarding_agency": {"office_agency_name": "W58RGZ ARMY CONTRACTING"}}, c))
        self.assertIn("amount", sb.detail_agrees({**USA_DETAIL, "total_obligation": 75000.0}, c))
        self.assertIn("date", sb.detail_agrees({**USA_DETAIL, "date_signed": "2026-07-02"}, c))
        self.assertIn("recipient", sb.detail_agrees(
            {**USA_DETAIL, "recipient": {"recipient_name": "SOMEONE ELSE LLC"}}, c))

    def test_request_body(self):
        body = sb.usaspending_body(SINCE, TODAY, 2)
        f = body["filters"]
        self.assertEqual(f["keywords"], ["FA8649"])
        self.assertEqual(f["time_period"][0], {"start_date": "2025-08-27", "end_date": "2026-10-01",
                                               "date_type": "new_awards_only"})
        self.assertEqual(f["award_amounts"], [{"lower_bound": 1, "upper_bound": 200000}])
        self.assertEqual((body["page"], body["limit"], body["sort"]), (2, 100, "Base Obligation Date"))

    def test_history_body_is_stable_from_day_to_day(self):
        body = sb.history_body("T42SK4ES67V5", TODAY, sb.CONTRACT_CODES)
        self.assertEqual(body, sb.history_body("T42SK4ES67V5", date(2026, 11, 15), sb.CONTRACT_CODES))
        f = body["filters"]
        self.assertEqual(f["recipient_search_text"], ["T42SK4ES67V5"])
        self.assertEqual(f["award_type_codes"], ["A", "B", "C", "D"])
        self.assertEqual(f["time_period"], [{"start_date": "2007-10-01", "end_date": "2026-12-31"}])
        self.assertEqual((body["sort"], body["order"], body["limit"]), ("Base Obligation Date", "asc", 100))
        self.assertEqual(sb.history_body("X", TODAY, sb.GRANT_CODES)["filters"]["award_type_codes"],
                         ["02", "03", "04", "05"])

    def test_earlier_awards(self):
        own = {"FA864926P0315"}
        got = sb.earlier_awards(ORIGAMI_HISTORY, "T42SK4ES67V5", own, "2026-06-25")
        self.assertEqual((got["n"], got["complete"]), (1, True))
        self.assertEqual(got["rows"][0]["Award ID"], "FA864926P0012")
        # The award itself is not its own predecessor, however its number was written.
        both = own | {sb.contract_key("FA8649-26-P-0012")}
        self.assertEqual(sb.earlier_awards(ORIGAMI_HISTORY, "T42SK4ES67V5", both, "2026-06-25")["n"], 0)
        # Same-day awards are not "earlier"; rows under another UEI (name or parent matches) are not this firm's.
        self.assertEqual(sb.earlier_awards(ORIGAMI_HISTORY, "T42SK4ES67V5", set(), "2026-02-23")["n"], 0)
        self.assertEqual(sb.earlier_awards(ORIGAMI_HISTORY, "SOMEONEELSE1", own, "2026-06-25")["n"], 0)
        # A row USAspending never back-filled with a UEI is the firm's only if the name is.
        legacy = {"results": [{"Award ID": "W911QX09C0001", "Recipient UEI": None, "Base Obligation Date": "2009-05-01",
                               "Recipient Name": "Origami Space Development Inc"},
                              {"Award ID": "W911QX09C0002", "Recipient UEI": "", "Base Obligation Date": "2009-05-01",
                               "Recipient Name": "ORIGAMI SPACE HOLDINGS"}],
                  "page_metadata": {"hasNext": False}}
        self.assertEqual(sb.earlier_awards(legacy, "T42SK4ES67V5", own, "2026-06-25",
                                           sb.firm_key("ORIGAMI SPACE DEVELOPMENT, INC."))["n"], 1)
        self.assertEqual(sb.earlier_awards(legacy, "T42SK4ES67V5", own, "2026-06-25")["n"], 0)
        # An undated row counts as earlier: the safe side for a "first" claim.
        undated = {"results": [{"Award ID": "X1", "Recipient UEI": "T42SK4ES67V5", "Base Obligation Date": None}],
                   "page_metadata": {"hasNext": False}}
        self.assertEqual(sb.earlier_awards(undated, "T42SK4ES67V5", own, "2026-06-25")["n"], 1)
        # A full page that never reaches the award's date is a floor, not a count.
        cut = {"results": [{"Award ID": f"C{i}", "Recipient UEI": "BIGFIRM00000", "Base Obligation Date": "2015-01-01"}
                           for i in range(100)], "page_metadata": {"hasNext": True}}
        got = sb.earlier_awards(cut, "BIGFIRM00000", set(), "2026-06-25")
        self.assertEqual((got["n"], got["complete"]), (100, False))
        for bad in (None, [], {"results": None}, {"detail": "error"}):
            with self.assertRaises(ValueError):
                sb.earlier_awards(bad, "T42SK4ES67V5", own, "2026-06-25")
        self.assertEqual(sb.earlier_awards({"results": ["junk", None]}, "T42SK4ES67V5", own, "2026-06-25")["n"], 0)

    def federal(self, cand, standing, answers, on_file=frozenset()):
        """federal_standing with post_json answering from `answers` keyed by award type group."""
        calls = []

        def post(url, payload, **kw):
            group = "contracts" if payload["filters"]["award_type_codes"][0] == "A" else "grants"
            calls.append(group)
            got = answers[group]
            if isinstance(got, Exception):
                raise got
            return got

        with mock.patch.object(sb.http, "post_json", post):
            return sb.federal_standing(cand, standing, set(on_file), TODAY, sb.Budget(30)), calls

    def test_federal_standing_withdraws_a_first_claim_sbir_gov_cannot_see(self):
        # Real case: no SBIR.gov row at all, and a $1.6M order from the same office four months before.
        st, calls = self.federal(origami_candidate(), FIRST, {"contracts": ORIGAMI_HISTORY})
        self.assertEqual((st["status"], st["federal_contracts"], st["federal_checked"]), ("federal", 1, True))
        self.assertEqual(calls, ["contracts"])            # grants are not asked once a contract shows
        self.assertEqual(sb.prior_count(st), 1)

    def test_federal_standing_confirms_a_first_only_with_no_contract_and_no_grant(self):
        c = sb.parse_afwerx_row(USA_ROW, SINCE, TODAY)
        own = {"results": [{"Award ID": "FA864926P0183", "Recipient UEI": "HDWKYZ7J8HR6",
                            "Base Obligation Date": "2026-06-30"}], "page_metadata": {"hasNext": False}}
        st, calls = self.federal(c, FIRST, {"contracts": own, "grants": EMPTY_HISTORY})
        self.assertEqual((st["status"], st["federal_contracts"], st["federal_grants"]), ("first", 0, 0))
        self.assertEqual(calls, ["contracts", "grants"])
        grant = {"results": [{"Award ID": "2401234", "Recipient UEI": "HDWKYZ7J8HR6",
                              "Base Obligation Date": "2024-09-01"}], "page_metadata": {"hasNext": False}}
        st, _ = self.federal(c, FIRST, {"contracts": own, "grants": grant})
        self.assertEqual((st["status"], st["federal_grants"]), ("federal", 1))
        self.assertEqual(sb.history_clause(st), "1 earlier federal grant on record")

    def test_federal_standing_for_a_csv_award(self):
        verde = first_candidate("VERDE TECHNOLOGIES INCORPORATED")
        st, calls = self.federal(verde, FIRST, {"contracts": VERDE_CONTRACTS})
        self.assertEqual((st["status"], st["federal_contracts"]), ("federal", 1))
        # Its own NSF grant, listed under the same number, is not an earlier award.
        own_grant = {"results": [{"Award ID": "2528317", "Recipient UEI": "YNNEUBPBKLM7",
                                  "Base Obligation Date": "2026-08-13"}], "page_metadata": {"hasNext": False}}
        st, calls = self.federal(verde, FIRST, {"contracts": EMPTY_HISTORY, "grants": own_grant})
        self.assertEqual((st["status"], calls), ("first", ["contracts", "grants"]))

    def test_federal_standing_never_claims_what_it_could_not_check(self):
        c = sb.parse_afwerx_row(USA_ROW, SINCE, TODAY)
        st, _ = self.federal(c, FIRST, {"contracts": sb.http.HttpError(504, "u", "Gateway Timeout")})
        self.assertEqual((st["status"], st["federal_checked"]), ("unclear", False))
        self.assertIn("HttpError", st["federal_error"])
        st, _ = self.federal(c, FIRST, {"contracts": EMPTY_HISTORY, "grants": TimeoutError("slow")})
        self.assertEqual(st["status"], "unclear")
        st, _ = self.federal(c, FIRST, {"contracts": {"detail": "bad request"}})
        self.assertEqual(st["status"], "unclear")
        st, calls = self.federal({**c, "uei": ""}, FIRST, {})
        self.assertEqual((st["status"], calls), ("unclear", []))
        # A repeat firm keeps its SBIR.gov count when USAspending is down.
        repeat = {**FIRST, "status": "repeat", "prior": 7, "lifetime": 8}
        st, _ = self.federal(c, repeat, {"contracts": TimeoutError("slow")})
        self.assertEqual((st["status"], st["prior"]), ("repeat", 7))

    def test_federal_standing_adds_office_awards_missing_from_sbir_gov(self):
        repeat = {**FIRST, "status": "repeat", "prior": 3, "lifetime": 4}
        st, calls = self.federal(origami_candidate(), repeat, {"contracts": ORIGAMI_HISTORY})
        self.assertEqual((st["status"], st["prior"], st["prior_not_on_sbir_gov"]), ("repeat", 4, 1))
        self.assertEqual(calls, ["contracts"])
        # Already in the SBIR.gov file: counted there, not twice.
        st, _ = self.federal(origami_candidate(), repeat, {"contracts": ORIGAMI_HISTORY}, on_file={"FA864926P0012"})
        self.assertEqual((st["prior"], st["prior_not_on_sbir_gov"]), (3, 0))

    def test_established_firms(self):
        self.assertFalse(sb.is_established({**FIRST}))
        self.assertFalse(sb.is_established({**FIRST, "status": "repeat", "prior": 49}))
        self.assertTrue(sb.is_established({**FIRST, "status": "repeat", "prior": 50}))
        self.assertTrue(sb.is_established({**FIRST, "status": "federal", "federal_contracts": 59}))
        self.assertFalse(sb.is_established({**FIRST, "status": "federal", "federal_contracts": 2,
                                            "federal_complete": True}))
        # More than a page of contracts before this one: an incumbent, whatever the exact count.
        self.assertTrue(sb.is_established({**FIRST, "status": "federal", "federal_contracts": 12,
                                           "federal_complete": False}))

    def test_budget_is_hard(self):
        budget = sb.Budget(1.0)
        t0 = time.monotonic()
        with self.assertRaises(TimeoutError):
            budget.call(lambda: time.sleep(5))
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertLessEqual(budget.left, 0.05)
        with self.assertRaises(TimeoutError):
            budget.call(lambda: 1)  # nothing left: not even attempted
        fresh = sb.Budget(5)
        self.assertEqual(fresh.call(lambda: 42), 42)
        with self.assertRaises(KeyError):
            fresh.call(lambda: {}["missing"])
        self.assertGreater(fresh.left, 4)

    def test_fetch_degrades_when_usaspending_hangs(self):
        ctx = Context(today=TODAY)

        calls = []

        def hang(*a, **k):
            calls.append(1)
            time.sleep(10)

        t0 = time.monotonic()
        with mock.patch.object(sb.http, "post_json", hang):
            out = sb.fetch_afwerx(ctx, SINCE, TODAY, sb.Budget(3.5))
        self.assertLess(time.monotonic() - t0, 5.0)
        self.assertEqual(calls, [1])                 # a timeout is not retried
        self.assertEqual(out, [])
        self.assertEqual(len(ctx.warnings), 1)
        self.assertIn("TimeoutError", ctx.warnings[0])

    def test_fetch_survives_a_non_json_answer(self):
        ctx = Context(today=TODAY)
        with mock.patch.object(sb.http, "post_json", lambda *a, **k: None):
            self.assertEqual(sb.fetch_afwerx(ctx, SINCE, TODAY, sb.Budget(5)), [])
        self.assertEqual(len(ctx.warnings), 1)

    def test_fetch_retries_a_gateway_error_once(self):
        ctx = Context(today=TODAY)
        calls = []

        def flaky(url, payload, **kw):
            calls.append(payload["page"])
            if len(calls) == 1:
                raise sb.http.HttpError(504, url, "Gateway Timeout")
            return {"results": [USA_ROW], "page_metadata": {"hasNext": False}}

        with mock.patch.object(sb.http, "post_json", flaky):
            out = sb.fetch_afwerx(ctx, SINCE, TODAY, sb.Budget(30))
        self.assertEqual(calls, [1, 1])
        self.assertEqual([c["award_id"] for c in out], ["FA864926P0183"])
        self.assertEqual(ctx.warnings, [])

    def test_fetch_pages_and_stops_at_the_cap(self):
        ctx = Context(today=TODAY)
        pages = []

        def endless(url, payload, **kw):
            pages.append(payload["page"])
            return {"results": [{**USA_ROW, "Award ID": f"FA864926P{payload['page']:04d}"}],
                    "page_metadata": {"hasNext": True}}

        with mock.patch.object(sb.http, "post_json", endless):
            out = sb.fetch_afwerx(ctx, SINCE, TODAY, sb.Budget(30))
        self.assertEqual(pages, list(range(1, sb.AFWERX_MAX_PAGES + 1)))   # bounded, never a loop
        self.assertEqual(len(out), sb.AFWERX_MAX_PAGES)
        self.assertEqual(len(ctx.warnings), 1)                              # and it says it stopped
        self.assertIn("not read", ctx.warnings[0])


class StrengthAndTitles(unittest.TestCase):
    def test_strength_scale(self):
        first_small = sb.strength_for("first", 0, 0.465, 5)
        first_big = sb.strength_for("first", 0, 0.465, 300)
        few = sb.strength_for("repeat", 2, 0.465, 12)
        some = sb.strength_for("repeat", 8, 0.465, 30)
        mill = sb.strength_for("repeat", 400, 0.465, 140)
        self.assertTrue(first_small > first_big > mill)
        self.assertTrue(first_small > few > some > mill)
        self.assertTrue(0.6 <= first_small <= 0.8)
        self.assertTrue(0.35 <= few <= 0.55)
        self.assertTrue(0.15 <= mill <= 0.3)
        # The top band needs a first award, a tiny team and a title deep in the thesis.
        self.assertGreaterEqual(sb.strength_for("first", 0, 0.83, 2, fast_track=True), 0.85)
        self.assertLess(sb.strength_for("first", 0, 0.83, None, afwerx=True), 0.85)
        # A title that only scraped past on context words is marked down.
        self.assertLess(sb.strength_for("first", 0, 0.316, 5), first_small)
        self.assertLess(sb.strength_for("unclear", 0, 0.465, 5), few + 0.01)
        for args in (("first", 0, 1.0, 1), ("repeat", 5000, 0.3, 9000)):
            self.assertTrue(0.15 <= sb.strength_for(*args) <= 1.0)
        # Earlier federal contracts cost the premium exactly as earlier SBIR awards do.
        self.assertEqual(sb.strength_for("federal", 1, 0.465, 5), few := sb.strength_for("repeat", 1, 0.465, 5))
        self.assertLess(few, first_small - 0.2)
        self.assertLess(sb.strength_for("federal", 30, 0.465, None), 0.3)

    def test_csv_titles(self):
        hexa = first_candidate("HEXAspec Inc")
        first = {"status": "first", "prior": 0}
        t = sb.csv_title(hexa, first)
        self.assertTrue(t.startswith("First federal award on record, $1.55M NSF SBIR Fast-Track: Advanced Hexagonal Boron Nitride"))
        par = first_candidate("PARALLEL ROBOTICS LLC")
        self.assertEqual(sb.csv_title(par, {"status": "repeat", "prior": 1}),
                         "$305K NSF SBIR Phase I, 1 prior award on record: A Novel Architecture for blending Human Power and Computer…")
        self.assertTrue(sb.csv_title(par, {"status": "repeat", "prior": 12}).startswith(
            "$305K NSF SBIR Phase I, 12 prior awards on record: "))
        self.assertTrue(sb.csv_title(par, {"status": "unclear", "prior": 0}).startswith("$305K NSF SBIR Phase I: A Novel"))
        # Real case: USAspending lists an Army SBIR contract five months before this NSF award.
        verde = first_candidate("VERDE TECHNOLOGIES INCORPORATED")
        federal = {"status": "federal", "prior": 0, "federal_contracts": 1}
        self.assertEqual(sb.csv_title(verde, federal),
                         "$305K NSF SBIR Phase I, 1 earlier federal contract on record: Perovskite Solar Cells on Metal Foil…")
        self.assertNotIn("First", sb.csv_title(verde, federal))
        self.assertTrue(sb.csv_title(verde, {"status": "federal", "prior": 0, "federal_contracts": 4}).startswith(
            "$305K NSF SBIR Phase I, 4 earlier federal contracts on record: "))
        for c in sb.select_candidates(sb.iter_rows(CSV), SINCE, TODAY):
            for st in (first, {"status": "repeat", "prior": 1795}, {"status": "unclear", "prior": 0},
                       {"status": "federal", "prior": 0, "federal_contracts": 49}):
                title = sb.csv_title(c, st)
                self.assertLess(len(title), 110, title)
                self.assertFalse(title.endswith("."))
                self.assertNotIn("  ", title)

    def test_afwerx_titles(self):
        c = sb.parse_afwerx_row(USA_ROW, SINCE, TODAY)
        self.assertEqual(
            sb.afwerx_title(c, {"status": "first", "prior": 0}),
            "First federal award on record, $72K Air Force SBIR/STTR order: Scaling lightweight, high-capacity, low-cost…")
        t = sb.afwerx_title(c, {"status": "repeat", "prior": 14})
        self.assertTrue(t.startswith("$72K Air Force SBIR/STTR order, 14 prior awards on record: Scaling lightweight"))
        self.assertEqual(sb.afwerx_title(origami_candidate(), {"status": "federal", "prior": 0, "federal_contracts": 1}),
                         "$110K Air Force SBIR/STTR order, 1 earlier federal contract on record: Expanding truss-like solar array…")
        plain = sb.afwerx_title(c, {"status": "unclear", "prior": 0})
        self.assertTrue(plain.startswith("$72K Air Force SBIR/STTR order: Scaling lightweight"))
        for title in (t, plain, sb.afwerx_title(c, {"status": "first", "prior": 0})):
            self.assertLess(len(title), 110)
            # The award record names the office and no phase, so no title asserts one.
            self.assertNotIn("Phase", title)
            self.assertNotIn("AFWERX", title)


class Signals(unittest.TestCase):
    def test_csv_signal(self):
        c = first_candidate("HEXAspec Inc")
        st = sb.award_standing(history_for([c]), c["uei"], c["duns"], c["name_key"], c["code"], c["row_id"])
        page = {"url": "https://www.sbir.gov/awards/220814", "portfolio_id": "2669971",
                "abstract": "Hexagonal boron nitride packaging for power semiconductors."}
        s = sb.csv_signal(c, st, page, {"first_year": 2026, "phase1": 1, "phase2": 0})
        s.validate()
        self.assertEqual((s.source, s.family, s.kind), ("sbir_awards", "capital", "sbir_phase1"))
        self.assertEqual(s.entity.name, "HEXAspec Inc")
        self.assertEqual(s.entity.domain, "hexaspec.com")
        self.assertEqual(s.entity.location, "Houston, Texas")
        self.assertEqual(s.entity.links, {"sbir_profile": "https://www.sbir.gov/portfolio/2669971"})
        self.assertEqual(s.occurred_at, "2026-08-24")
        self.assertEqual(s.url, "https://www.sbir.gov/awards/220814")
        self.assertEqual((s.value, s.unit), (1554959.0, "USD"))
        self.assertEqual(s.metrics["amount_usd"], 1554959.0)
        self.assertEqual(s.metrics["team_size"], 2)
        self.assertEqual(s.metrics["first_award_on_record"], 1)
        self.assertEqual(s.metrics["sbir_profile_first_year"], 2026)
        self.assertEqual(s.metrics["phase"], "Fast-Track")
        self.assertEqual([(p.name, p.affiliations) for p in s.people], [("Tianshu Zhai", ["HEXAspec Inc"])])
        self.assertIn("Principal investigator", s.people[0].role)
        self.assertEqual(s.people[0].links, {})          # no emails or phones stored
        self.assertNotIn("@", json.dumps(s.to_row()))
        self.assertIn("Hexagonal boron nitride packaging", s.text)
        self.assertTrue(0.6 <= s.strength <= 1.0)
        # Identifiers the resolver and the scorer join on.
        self.assertEqual(s.metrics["uei"], "LUYZNGL2VUE3")
        self.assertEqual(s.metrics["award_key"], "nsf:2554320")
        self.assertEqual(s.metrics["sbir_prior_awards"], 0)
        self.assertEqual((s.metrics["topic_code"], s.metrics["nsf_topic_area"]), ("AM", "Advanced Materials"))
        self.assertNotIn("public_at", s.metrics)       # SBIR.gov gives no release date

    def test_uei_is_upper_case_and_award_key_is_nsf_only(self):
        c = first_candidate("HEXAspec Inc")
        page = {"url": "https://www.sbir.gov/awards/1", "portfolio_id": None, "abstract": ""}
        lower = list(sb.select_candidates([{**c["row"], "UEI": " luyzngl2vue3 "}], SINCE, TODAY))[0]
        self.assertEqual(sb.csv_signal(lower, FIRST, page).metrics["uei"], "LUYZNGL2VUE3")
        blank = list(sb.select_candidates([{**c["row"], "UEI": ""}], SINCE, TODAY))[0]
        self.assertNotIn("uei", sb.csv_signal(blank, FIRST, page).metrics)
        # The key is NSF's seven-digit award id, wherever the row carries it, and nothing else.
        self.assertEqual(sb.nsf_award_key({"Agency": NSF, "Agency Tracking Number": "2554320", "Contract": "x"}), "nsf:2554320")
        self.assertEqual(sb.nsf_award_key({"Agency": NSF, "Agency Tracking Number": "", "Contract": " 2554320 "}), "nsf:2554320")
        self.assertIsNone(sb.nsf_award_key({"Agency": NSF, "Agency Tracking Number": "25543", "Contract": "NSF-25"}))
        self.assertIsNone(sb.nsf_award_key({"Agency": DOD, "Agency Tracking Number": "2554320", "Contract": "2554320"}))
        navy = {**c["row"], "Agency": DOD, "Branch": "Navy", "Contract": "N6833526C0101",
                "Agency Tracking Number": "N252-001-0001"}
        s = sb.csv_signal({**c, "row": navy, "context": sb.award_context(navy)}, FIRST, page)
        self.assertNotIn("award_key", s.metrics)
        self.assertNotIn("nsf_topic_area", s.metrics)
        self.assertTrue(s.text.startswith("Navy SBIR Fast-Track award.\n"))

    def test_text_states_what_backed_a_lone_keyword(self):
        # The gate showed the classifier the title and this sentence; the stored text
        # has to show the downstream classifier the same two terms.
        page = {"url": "https://www.sbir.gov/awards/220837", "portfolio_id": None, "abstract": ""}
        verde = first_candidate("VERDE TECHNOLOGIES INCORPORATED")
        s = sb.csv_signal(verde, FIRST, page)
        self.assertEqual(s.text.split("\n")[0], "NSF SBIR Phase I award. NSF topic area: Energy Technologies.")
        seen = sb.classify(s.text)
        self.assertGreaterEqual(seen["fit"], sb.MIN_FIT)
        self.assertEqual((seen["sector"], seen["terms"]), ("energy", ["energy", "solar"]))
        self.assertEqual(s.metrics["title_fit"], sb.ONE_KEYWORD_FIT)
        # Same strength as a first award whose title holds one unmistakable keyword.
        robot = sb.csv_signal(first_candidate("Channel Robotics"), FIRST, page)
        self.assertEqual(s.strength, robot.strength)
        o = sb.parse_afwerx_row({**USA_ROW, "Description": "HYBRID ELECTRIC TURBINE ENGINE FOR SUSTAINABLE AVIATION"},
                                SINCE, TODAY)
        self.assertEqual(o["fit"]["context"], sb.ORDER_CONTEXT)
        order = sb.afwerx_signal(o, FIRST, None, TODAY, USA_DETAIL)
        seen = sb.classify(order.text)
        self.assertEqual((seen["sector"], seen["terms"]), ("energy", ["air force", "turbine"]))
        self.assertGreaterEqual(seen["fit"], sb.MIN_FIT)
        # A NASA row kept on NASA's name (real row): the sentence leads the stored text, and
        # only NSF awards carry an award_key.
        (questek,) = sb.select_candidates([NASA_ROW], SINCE, TODAY)
        self.assertEqual((questek["fit"]["context"], questek["context"]), ("NASA SBIR Phase I award.",) * 2)
        s = sb.csv_signal(questek, {**FIRST, "status": "repeat", "prior": 12}, page)
        s.validate()
        self.assertEqual(s.text.split("\n")[:2], ["NASA SBIR Phase I award.", NASA_ROW["Award Title"]])
        seen = sb.classify(s.text)
        self.assertEqual((seen["sector"], seen["terms"]), ("space", ["nasa", "propulsion"]))
        self.assertGreaterEqual(seen["fit"], sb.MIN_FIT)
        self.assertEqual(s.metrics["title_fit"], sb.ONE_KEYWORD_FIT)
        self.assertNotIn("award_key", s.metrics)
        self.assertTrue(s.title.startswith("$150K NASA SBIR Phase I, 12 prior awards on record: "))

    def test_kind_does_not_depend_on_history(self):
        # db.fingerprint includes kind: if it changed when a "first" is later corrected, the stale
        # claim would stay in the database as a second row for the same award.
        c = first_candidate("VERDE TECHNOLOGIES INCORPORATED")
        st = sb.award_standing(history_for([c]), c["uei"], c["duns"], c["name_key"], c["code"], c["row_id"])
        page = {"url": "https://www.sbir.gov/awards/220837", "portfolio_id": "2092861", "abstract": ""}
        as_first = sb.csv_signal(c, st, page)
        corrected = sb.csv_signal(c, {**st, "status": "federal", "federal_checked": True,
                                      "federal_contracts": 1, "federal_grants": None}, page)
        self.assertEqual(as_first.kind, corrected.kind)
        self.assertEqual((as_first.url, as_first.occurred_at, as_first.entity.domain),
                         (corrected.url, corrected.occurred_at, corrected.entity.domain))
        self.assertEqual((as_first.metrics["first_award_on_record"], corrected.metrics["first_award_on_record"]), (1, 0))
        self.assertEqual(corrected.metrics["federal_prior_contracts"], 1)
        self.assertEqual(corrected.metrics["history_status"], "federal")
        self.assertLess(corrected.strength, as_first.strength - 0.2)
        self.assertNotIn("First", corrected.title)
        o = sb.parse_afwerx_row(USA_ROW, SINCE, TODAY)
        self.assertEqual(sb.afwerx_signal(o, FIRST, None, TODAY, USA_DETAIL).kind,
                         sb.afwerx_signal(o, {**FIRST, "status": "repeat", "prior": 3}, None, TODAY, USA_DETAIL).kind)

    def test_unchecked_history_is_recorded_not_claimed(self):
        o = sb.parse_afwerx_row(USA_ROW, SINCE, TODAY)
        st = {**FIRST, "status": "unclear", "federal_checked": False, "federal_error": "TimeoutError: slow"}
        s = sb.afwerx_signal(o, st, None, TODAY, USA_DETAIL)
        self.assertEqual(s.metrics["first_award_on_record"], 0)
        self.assertIn("not checked", s.metrics["federal_history"])
        self.assertNotIn("First", s.title)
        self.assertLess(s.strength, 0.6)

    def test_trade_names_become_aliases_and_placeholder_pis_are_dropped(self):
        c = first_candidate("HEXAspec Inc")
        c = {**c, "row": {**c["row"], "Company": "Padco Industries, LLC DBA DEM Manufacturing",
                          "Company Website": "https://dem-mfg.com", "PI Email": "s@dem-mfg.com",
                          "PI Name": "FNU Vedant"}}
        s = sb.csv_signal(c, FIRST, {"url": "https://www.sbir.gov/awards/1", "portfolio_id": None, "abstract": ""})
        self.assertEqual((s.entity.name, s.entity.aliases), ("Padco Industries, LLC", ["DEM Manufacturing"]))
        self.assertEqual(s.entity.domain, "dem-mfg.com")   # judged against the full name as written
        self.assertEqual(s.people, [])

    def test_afwerx_signal(self):
        c = sb.parse_afwerx_row(USA_ROW, SINCE, TODAY)
        st = sb.award_standing(history_for([c], rows=[]), c["uei"], "", c["name_key"], c["code"])
        s = sb.afwerx_signal(c, st, None, TODAY, USA_DETAIL)
        s.validate()
        self.assertEqual(s.kind, "afwerx_order")
        self.assertEqual(s.metrics["first_award_on_record"], 1)
        self.assertEqual(s.entity.name, "DRONENX LLC")
        self.assertIsNone(s.entity.domain)
        self.assertEqual(s.url, "https://www.usaspending.gov/award/CONT_AWD_FA864926P0183_9700_-NONE-_-NONE-")
        self.assertEqual((s.occurred_at, s.value), ("2026-06-30", 72178.0))
        self.assertEqual(s.metrics["office"], "FA8649 USAF SBIR STTR CNTRCTNG AFRL")
        self.assertNotIn("team_size", s.metrics)
        self.assertEqual(s.people, [])
        self.assertEqual(s.metrics["uei"], "HDWKYZ7J8HR6")
        self.assertNotIn("award_key", s.metrics)
        # The buyer is named once and the office as the record writes it, so the
        # classifier's sector is the product's (autonomy), not the customer's.
        self.assertEqual(s.text, "SCALING LIGHTWEIGHT, HIGH-CAPACITY, LOW-COST BATTERY MANUFACTURING FOR SUAS "
                                 "LOITERING MUNITIONS\nAir Force SBIR/STTR order FA864926P0183, purchase order. "
                                 "Awarding office: FA8649 USAF SBIR STTR CNTRCTNG AFRL.")
        seen = sb.classify(" ".join([s.entity.name, s.entity.description, s.title, s.text]))
        self.assertEqual(seen["sector"], "autonomy")
        self.assertNotIn("afwerx", seen["terms"])
        # Signed 2026-06-30, withheld 90 days: public from 2026-09-28.
        self.assertEqual(s.metrics["public_at"], "2026-09-28")
        # A firm already in the SBIR.gov file lends its website and recent headcount.
        prof = {"website": "https://dronenx.com", "pi_email": "", "employees": 6, "year": 2025}
        s2 = sb.afwerx_signal(c, st, prof, TODAY, USA_DETAIL)
        self.assertEqual((s2.entity.domain, s2.metrics["team_size"]), ("dronenx.com", 6))
        stale = sb.afwerx_signal(c, st, {**prof, "year": 2019}, TODAY, USA_DETAIL)
        self.assertNotIn("team_size", stale.metrics)

    def test_public_date_is_the_embargo_and_never_the_future(self):
        self.assertEqual(sb.public_date("2026-06-30", TODAY), "2026-09-28")
        self.assertEqual(sb.public_date("2026-06-03", TODAY), "2026-09-01")
        # A record seen today was public by today.
        self.assertEqual(sb.public_date("2026-07-15", TODAY), "2026-10-01")
        # Stable from run to run once the embargo has passed.
        self.assertEqual(sb.public_date("2026-06-30", date(2026, 11, 20)), "2026-09-28")


class FakeHttp:
    """Stands in for antenna.http: serves the fixture CSV and pages built from it.

    `earlier` maps a UEI to rows USAspending lists for it before its award
    (contracts); by default every firm's record holds its own award and
    nothing else.
    """

    HttpError = real_http.HttpError

    def __init__(self, tamper: str | None = None, usa_rows: list | None = None,
                 earlier: dict | None = None):
        self.rows = list(sb.iter_rows(CSV))
        self.by_contract = {r["Contract"]: (i, r) for i, r in enumerate(self.rows)}
        self.by_uei = {r["UEI"].upper(): r for r in self.rows}
        self.tamper = tamper
        self.usa_rows = usa_rows if usa_rows is not None else [
            USA_ROW,
            {**USA_ROW, "Award ID": "FA864926P0306", "Recipient Name": "B20 LABS INC",
             "Recipient UEI": "QCF4YHPPQMD7", "Base Obligation Date": "2026-07-01",
             "Description": "CUAS USING EXISTING BROADCAST INFRASTRUCTURE TOWERS (CUEBIT)",
             "generated_internal_id": "CONT_AWD_FA864926P0306_9700_-NONE-_-NONE-"},
        ]
        self.earlier = earlier or {}
        self.urls: list[str] = []
        self.history_calls: list[tuple[str, str]] = []
        self.search_bodies: list[dict] = []

    def download(self, url, dest, **kw):
        self.urls.append(url)
        return CSV

    def get(self, url, params=None, **kw):
        self.urls.append(url)
        if url.endswith("/awards"):
            i, r = self.by_contract[params["keywords"]]
            return SEARCH_HIT.format(id=1000 + i, title=r["Award Title"], company=r["Company"],
                                     program=r["Program"], phase=r["Phase"])
        if "/awards/" in url:
            i = int(url.rsplit("/", 1)[1]) - 1000
            r = self.rows[i]
            amount = f"{float(r['Award Amount']):,.0f}"
            if self.tamper == r["Company"]:
                amount = "1"
            start = date.fromisoformat(r["Proposal Award Date"]).strftime("%B %d, %Y")
            return AWARD_PAGE.format(title=r["Award Title"], portfolio=5000 + i, company=r["Company"],
                                     uei=r["UEI"], amount=amount, contract=r["Contract"],
                                     abstract="Abstract text about the project.", start=start)
        if "/portfolio/" in url:
            return PORTFOLIO_PAGE.format(first_year="2026")
        raise AssertionError(f"unexpected GET {url}")

    def post_json(self, url, payload, **kw):
        self.urls.append(url)
        filters = payload["filters"]
        if "recipient_search_text" not in filters:
            self.search_bodies.append(payload)
            return {"results": self.usa_rows, "page_metadata": {"hasNext": False}}
        (uei,) = filters["recipient_search_text"]
        contracts = filters["award_type_codes"][0] == "A"
        self.history_calls.append((uei, "contracts" if contracts else "grants"))
        rows = []
        if contracts:
            rows += self.earlier.get(uei, [])
            rows += [{"Award ID": r["Award ID"], "Recipient UEI": uei,
                      "Base Obligation Date": r["Base Obligation Date"]}
                     for r in self.usa_rows if r["Recipient UEI"] == uei]
        elif uei in self.by_uei:   # the fixture's awards are NSF grants
            r = self.by_uei[uei]
            rows.append({"Award ID": r["Contract"], "Recipient UEI": uei,
                         "Base Obligation Date": r["Proposal Award Date"]})
        return {"results": rows, "page_metadata": {"hasNext": False}}

    def get_json(self, url, **kw):
        self.urls.append(url)
        if "FA864926P0183" in url:
            return USA_DETAIL
        raise self.HttpError(404, url, "No Award found")


class CollectOffline(unittest.TestCase):
    def run_collect(self, fake, **ctx_kw):
        ctx = Context(today=TODAY, **ctx_kw)
        with mock.patch.object(sb, "http", fake):
            return list(sb.collect(ctx)), ctx

    def test_end_to_end(self):
        fake = FakeHttp()
        signals, ctx = self.run_collect(fake)
        for s in signals:
            s.validate()
            self.assertLess(len(s.title), 110)
            self.assertFalse(s.title.endswith("."))
            self.assertTrue(ctx.since.isoformat() <= s.occurred_at <= TODAY.isoformat(), s.occurred_at)
            self.assertTrue(s.url.startswith(("https://www.sbir.gov/awards/", "https://www.usaspending.gov/award/")))
            self.assertTrue(0.15 <= s.strength <= 1.0)
        self.assertEqual(len(signals), N_CSV + 1)               # 13 from the CSV, 1 Air Force order
        self.assertEqual([s.occurred_at for s in signals], sorted((s.occurred_at for s in signals), reverse=True))
        kinds = [s.kind for s in signals]
        self.assertEqual((kinds.count("sbir_phase1"), kinds.count("afwerx_order")), (N_CSV, 1))
        self.assertEqual([s.entity.name for s in signals if s.kind == "sbir_phase1"], FIXTURE_FIRMS)
        # Every signal is one the shared classifier, shown what is stored, calls on thesis.
        for s in signals:
            seen = sb.classify(" ".join(filter(None, [s.entity.name, s.entity.description, s.title, s.text])))
            self.assertGreaterEqual(seen["fit"], sb.MIN_FIT, s.title)
        # Join keys: a UEI on every signal, NSF's award id on every NSF award, a release date on the order.
        self.assertTrue(all(s.metrics["uei"] == s.metrics["uei"].upper() and len(s.metrics["uei"]) == 12 for s in signals))
        self.assertEqual(len({s.metrics["award_key"] for s in signals if s.kind == "sbir_phase1"}), N_CSV)
        self.assertTrue(all(s.metrics["award_key"] == "nsf:" + s.metrics["contract"]
                            for s in signals if s.kind == "sbir_phase1"))
        # The same shape energy_grants writes for the award from NSF's own feed: "nsf:" + seven digits.
        self.assertTrue(all(re.fullmatch(r"nsf:\d{7}", s.metrics["award_key"])
                            for s in signals if s.kind == "sbir_phase1"))
        self.assertEqual([s.metrics.get("public_at") for s in signals if s.kind == "afwerx_order"], ["2026-09-28"])
        self.assertTrue(all("sbir_prior_awards" in s.metrics for s in signals))
        # Every firm here has only its own award on USAspending, so every claim was checked and stands.
        self.assertTrue(all(s.title.startswith("First federal award on record, ") for s in signals))
        self.assertTrue(all(s.metrics["federal_prior_contracts"] == 0 and s.metrics["federal_prior_grants"] == 0
                            for s in signals))
        self.assertEqual(sorted(g for _, g in fake.history_calls),
                         ["contracts"] * (N_CSV + 1) + ["grants"] * (N_CSV + 1))
        # The order whose award record 404s is reported, not emitted, and costs no history lookup.
        self.assertNotIn("B20 LABS INC", [s.entity.name for s in signals])
        self.assertNotIn("QCF4YHPPQMD7", [u for u, _ in fake.history_calls])
        self.assertTrue(any("404" in w for w in ctx.warnings))
        self.assertEqual(len(ctx.warnings), 1)
        self.assertTrue(all(u.startswith(("https://data.www.sbir.gov/", "https://www.sbir.gov/",
                                          "https://api.usaspending.gov/")) for u in fake.urls))

    def test_window_is_the_contexts(self):
        fake = FakeHttp()
        signals, ctx = self.run_collect(fake)
        # Contract rule 3: nothing older than ctx.since, and USAspending is asked for the same window.
        self.assertEqual(ctx.since, RUN_SINCE)
        self.assertEqual(fake.search_bodies[0]["filters"]["time_period"][0]["start_date"], "2026-06-03")
        short, ctx30 = self.run_collect(FakeHttp(), lookback_days=45)   # since 2026-08-17
        self.assertEqual([s.entity.name for s in short], ["HEXAspec Inc"])
        self.assertTrue(all(s.occurred_at >= ctx30.since.isoformat() for s in short))
        self.assertLess(len(short), len(signals))

    def test_first_claim_is_withdrawn_when_usaspending_lists_an_earlier_award(self):
        # Real case: SBIR.gov shows this NSF award as Verde's first; USAspending lists an Army
        # SBIR Phase I five months before it.
        fake = FakeHttp(earlier={"YNNEUBPBKLM7": VERDE_CONTRACTS["results"]}, usa_rows=[])
        signals, ctx = self.run_collect(fake)
        verde = next(s for s in signals if s.entity.name == "VERDE TECHNOLOGIES INCORPORATED")
        self.assertEqual(verde.title, "$305K NSF SBIR Phase I, 1 earlier federal contract on record: "
                                      "Perovskite Solar Cells on Metal Foil…")
        self.assertEqual((verde.metrics["first_award_on_record"], verde.metrics["history_status"]), (0, "federal"))
        self.assertLessEqual(verde.strength, 0.55)
        self.assertEqual(verde.kind, "sbir_phase1")
        others = [s for s in signals if s is not verde]
        self.assertEqual(len(others), N_CSV - 1)
        self.assertTrue(all(s.title.startswith("First federal award on record, ") for s in others))
        self.assertTrue(all(s.strength > verde.strength for s in others))
        self.assertEqual(ctx.warnings, [])

    def test_established_contractor_is_not_emitted(self):
        # Real shape: a federal IT contractor with dozens of contracts takes one Air Force order.
        old = [{"Award ID": f"HHSM{i:05d}", "Recipient UEI": "HDWKYZ7J8HR6", "Base Obligation Date": "2018-03-06"}
               for i in range(59)]
        signals, ctx = self.run_collect(FakeHttp(earlier={"HDWKYZ7J8HR6": old}, usa_rows=[USA_ROW]))
        self.assertNotIn("DRONENX LLC", [s.entity.name for s in signals])
        self.assertEqual(len(signals), N_CSV)
        self.assertEqual(ctx.warnings, [])        # a filter doing its job is logged, not warned

    def test_limit_stops_early(self):
        fake = FakeHttp()
        signals, _ = self.run_collect(fake, limit=3)
        self.assertEqual(len(signals), 3)
        self.assertEqual(signals[0].entity.name, "HEXAspec Inc")
        # Only the awards that were emitted were looked up.
        self.assertEqual(sum(1 for u in fake.urls if u.endswith("/awards")), 3)
        self.assertEqual(len({u for u, _ in fake.history_calls}), 3)

    def test_page_that_disagrees_is_dropped(self):
        signals, ctx = self.run_collect(FakeHttp(tamper="Channel Robotics", usa_rows=[]))
        names = [s.entity.name for s in signals]
        self.assertNotIn("Channel Robotics", names)
        self.assertEqual(len(signals), N_CSV - 1)
        self.assertTrue(any("Channel Robotics" in w and "amount" in w for w in ctx.warnings))

    def test_survives_usaspending_failure(self):
        fake = FakeHttp()

        def boom(url, payload, **kw):
            raise fake.HttpError(504, url, "Gateway Timeout")

        fake.post_json = boom
        signals, ctx = self.run_collect(fake)
        # The awards still go out, each with its evidence page, but nothing is called a first
        # award on SBIR.gov's word alone.
        self.assertEqual(len(signals), N_CSV)
        self.assertTrue(all(s.kind == "sbir_phase1" for s in signals))
        self.assertFalse(any("First" in s.title for s in signals))
        self.assertTrue(all(s.metrics["first_award_on_record"] == 0 for s in signals))
        self.assertTrue(all("not checked" in s.metrics["federal_history"] for s in signals))
        self.assertTrue(all(s.strength < 0.6 for s in signals))
        self.assertTrue(any("USAspending page 1 failed" in w for w in ctx.warnings))
        self.assertTrue(any("without a USAspending history check" in w for w in ctx.warnings))

    def test_sbir_gov_outage_emits_nothing_unverified(self):
        fake = FakeHttp(usa_rows=[USA_ROW])

        def down(url, params=None, **kw):
            raise fake.HttpError(403, url, "Forbidden")

        fake.get = down
        signals, ctx = self.run_collect(fake)
        self.assertEqual([(s.kind, s.entity.name) for s in signals], [("afwerx_order", "DRONENX LLC")])
        self.assertTrue(any("unreachable" in w for w in ctx.warnings))

    def test_stale_bulk_file_is_used_when_the_download_fails(self):
        fake = FakeHttp(usa_rows=[])

        def offline(url, dest, **kw):
            raise OSError("network is unreachable")

        fake.download = offline
        with mock.patch.object(sb.config, "DOWNLOADS_DIR", CSV.parent), \
             mock.patch.object(sb, "CSV_FILE", CSV.name):
            signals, ctx = self.run_collect(fake)
        self.assertEqual(len(signals), N_CSV)
        self.assertTrue(any("could not be refreshed" in w for w in ctx.warnings))
        # With no saved copy either, the failure is the collector's to report.
        with mock.patch.object(sb.config, "DOWNLOADS_DIR", CSV.parent), \
             mock.patch.object(sb, "CSV_FILE", "no_such_file.csv"):
            with self.assertRaises(OSError):
                self.run_collect(fake)

    def test_changed_csv_layout_is_reported(self):
        self.assertEqual(sb.missing_columns(CSV), [])
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            odd = Path(tmp) / "odd.csv"
            odd.write_text("Company,Title,Awarded On\nAcme,Robots,2026-08-01\n")
            self.assertIn("Proposal Award Date", sb.missing_columns(odd))
            fake = FakeHttp(usa_rows=[])
            fake.download = lambda url, dest, **kw: odd
            signals, ctx = self.run_collect(fake)
        self.assertEqual(signals, [])
        self.assertTrue(any("layout changed" in w for w in ctx.warnings))


if __name__ == "__main__":
    unittest.main()
