"""Offline tests for the fcc_els collector: parsing against saved fixtures, no network."""

from __future__ import annotations

import contextlib
import io
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from antenna import http
from antenna.collectors import fcc_els as els
from antenna.collectors.base import Context
from antenna.models import Signal
from antenna.thesis import classify

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "regulatory"
RSS = (FIXTURES / "fcc_els_new_grants_rss.xml").read_text(encoding="latin-1")
INFO_PAGE = (FIXTURES / "fcc_els_application_info_0950-EX-CN-2026.html").read_text(encoding="latin-1")
FORM_442 = (FIXTURES / "fcc_els_form442_pending_1059-EX-CN-2026.html").read_text(encoding="latin-1")

# What the site returns for a file number that does not exist yet: the same
# page with an empty results table.
MISSING_PAGE = re.sub(r'(?s)(<tbody id="offTblBdy">).*?(</tbody>)', r"\1\2", INFO_PAGE)

# The live STA print view for 1649-EX-ST-2026 (Neros Technologies), fetched
# 2026-10-01, with the boilerplate certification and antenna questions cut
# and the phone number zeroed. There is no STA page among the shared fixtures.
# The broken "</spa>" closing tag is the site's own.
STA_PRINT = """<BODY>
<TABLE BORDER=0>
<TR>
<TD align="center" class="bold-content">
FEDERAL COMMUNICATIONS COMMISSION<BR>
APPLICATION FOR SPECIAL TEMPORARY AUTHORITY
</TD>
</TR>
</TABLE>
<br>
<fieldset title="Applicant Name">
<legend class="small-blue-content">Applicant Name</legend>
<table border=0>
<tr>
<td class="small-bold-content">Name of Applicant:&nbsp;</td>
<td class="small-content">Neros Technologies</td>
</tr>
</table>
</fieldset>
<br>
<fieldset title="Applicant address">
<legend class="small-blue-content">Address</legend>
<table border=0>
<tr>
<td class="small-bold-content">Attention:</td>
<td class="small-content">William LaRose</td>
</tr>
<tr>
<td class="small-bold-content"> Street Address:</td>
<td class="small-content">19681 Pacific Gateway Dr</td>
</tr>
<tr>
<td class="small-bold-content">P.O. Box:</td>
<td class="small-content">90502</td>
</tr>
<tr>
<td class="small-bold-content">City:</td>
<td class="small-content">Torrance</td>
</tr>
<tr>
<td class="small-bold-content">State:</td>
<td class="small-content">CA</td>
</tr>
<tr>
<td class="small-bold-content">Zip Code:</td>
<td class="small-content">90502</td>
</tr>
<tr>
<td class="small-bold-content">Country:</td>
<td class="small-content">United States </td>
</tr>
<tr>
<td class="small-bold-content">E-Mail Address:</td>
<td class="small-content">william@neros.tech</td>
</tr>
</table>
</fieldset>
<br>
<fieldset title="Contact Information">
<legend class="small-blue-content">Best Contact</legend>
<table border=0>
<tr>
<td>
<span class="small-bold-content">
Give the following information of person
who can best handle inquiries pertaining to this application:
</span>&nbsp;
<br>
<span class="small-bold-content">
Last Name:
</span>
<span class="small-content">
LaRose
</span>
<br>
<span class="small-bold-content">
First Name:
</span>
<span class="small-content">
William
</span>
<br>
<span class="small-bold-content">
Title:
</span>
<span class="small-content">
Regulatory Affairs Lead
</span>
<br>
<span class="small-bold-content">
Phone Number:
</span>
<span class="small-content">
0000000000
</span>
</td>
</tr>
</table>
</fieldset>
<br>
<fieldset title="Why Necessary">
<legend class="small-blue-content">Explanation</legend>
<table border=0>
<tr>
<td>
<span class="small-bold-content">
Please explain in the area below why an STA is necessary:
</span>
<br>
<span class="small-content">
A STA is needed to conduct temporary testing.
</span>
</td>
</tr>
</table>
</fieldset>
<br>
<fieldset title="Purpose of Operation">
<legend class="small-blue-content">Purpose of Operation</legend>
<table border=0>
<tr>
<td class="small-bold-content">Please explain the purpose of operation:&nbsp;</td>
<td class="small-content">The purpose of this operation is to test the EcoShield radar.</td>
</tr>
</table>
</fieldset>
<br>
<fieldset title="Information">
<legend class="small-blue-content">Information</legend>
<table border=0>
<tr>
<td class="small-bold-content">Callsign:</td>
<td class="small-content">WA8XIU    </td>
</tr>
<tr>
<td class="small-bold-content">Class of Station:</td>
<td class="small-content"> FX  </td>
</tr>
<tr>
<td class="small-bold-content">Nature of Service:</td>
<td class="small-content">Experimental</td>
</tr>
</table>
</fieldset>
<br>
<fieldset title="Location">
<legend class="small-blue-content">Requested Period of Operation</legend>
<table border=0>
<tr>
<td class="small-bold-content">Operation Start Date:</td>
<td class="small-content">09/21/2026</td>
</tr>
<tr>
<td class="small-bold-content">Operation End Date:</td>
<td class="small-content">03/21/2027</td>
</tr>
</table>
</fieldset>
<br>
<fieldset title="Manufacturer">
<legend class="small-blue-content">Manufacturer</legend>
<table border=0>
<tr>
<td>
<span class="small-bold-content">
List below transmitting equipment to be installed (if experimental, so state) if additional rows are required, please submit equipment list as an exhibit:
</span>&nbsp;
</td>
</tr>
<tr>
<span class="small-bold-content">
<th class="small-bold-content" align="left">Manufacturer</th>
<th class="small-bold-content" align="left">Model Number</th>
<th class="small-bold-content" align="left">No. Of Units</th>
<th class="small-bold-content" align="left">Experimental</th>
</span>
</tr>
<tr>
<td align="left">
<span class="small-content">
Echodyne
</span>
</td>
<td align="left">
<span class="small-content">
EchoShield
</spa>
</td>
<td align="left">
<span class="small-content">
1
</span>
</td>
<td align="left">
<span class="small-content">
Yes
</span>
</td>
</tr>
</table>
</fieldset>
<br>
<br>
<fieldset title="Station Location">
<legend class="small-blue-content">Station Location</legend>
<table border=0 width=100%>
<span class="small-bold-content">
<tr>
<th class="small-bold-content" align="left"></th>
<th class="small-bold-content" align="left">City</th>
<th class="small-bold-content" align="left">State</th>
<th class="small-bold-content" align="left">Latitude</th>
<th class="small-bold-content" align="left">Longitude</th>
<th class="small-bold-content" align="left">Mobile</th>
<th class="small-bold-content" align="left">Radius of Operation</th>
</tr>
</span>
<tr>
<td align="left">
<span class="small-content">
</span>
</td>
<td align="left">
<span class="small-content">
Helendale
</span>
</td>
<td align="left">
<span class="small-content">
California
</span>
</td>
<td align="left">
<span class="small-content">
North&nbsp;
34&nbsp;
42&nbsp;
13
</span>
</td>
<td align="left">
<span class="small-content">
West&nbsp;
117&nbsp;
22&nbsp;
32
</span>
</td>
<td align="left">
<span class="small-content"></span>
</td>
<td align="left">
<span class="small-content">10.00</span>
</td>
</tr>
</table>
<table border=0>
<tr>
<th class="small-bold-content" align="left">Action</th>
<th class="small-bold-content" align="left">Frequency</th>
<th class="small-bold-content" align="left">Station Class</th>
<th class="small-bold-content" align="left">Output Power/ERP</th>
<th class="small-bold-content" align="left">Mean Peak</th>
<th class="small-bold-content" align="left">Frequency Tolerance (+/-)</th>
<th class="small-bold-content" align="left">Emission Designator</th>
<th class="small-bold-content" align="left">Modulating Signal</th>
</tr>
<tr>
<td align="left">
<span class="small-content">
New
</span>
</td>
<td align="left">
<span class="small-content">
15.40000000-16.66000000
GHz
</span>
</td>
<td align="left">
<span class="small-content">
FX
</span>
</td>
<td align="left">
<span class="small-content">
180.000000 W 48.000000 kW
</span>
</td>
<td align="left">
<span class="small-content">
P
</span>
</td>
<td align="left">
<span class="small-content">
</span>
</td>
<td align="left">
<span class="small-content">
57M6Q3N
</span>
</td>
<td align="left">
<span class="small-content">
</span>
</td>
</tr>
</table>
</fieldset>
<br>
</BODY>
</HTML>"""

TODAY = date(2026, 10, 1)

# Status rows as the site showed them on 2026-10-01.
HAWTHORN_INFO = {
    "file_num": "1059-EX-CN-2026", "seq": 1059, "type": "CN", "year": 2026, "callsign": None,
    "applicant": "Hawthorn Aero Inc", "received": date(2026, 9, 27), "status": "Pending",
    "status_date": date(2026, 9, 27),
}
NEROS_INFO = {
    "file_num": "1649-EX-ST-2026", "seq": 1649, "type": "ST", "year": 2026, "callsign": "WA8XIU",
    "applicant": "Neros Technologies", "received": date(2026, 8, 18), "status": "Granted",
    "status_date": date(2026, 9, 9),
}


def bare_form(**over):
    """A parsed form with nothing on it, for strength and title edge cases."""
    form = {
        "is_sta": False, "applicant": None, "attention": None, "attention_owns_email": False,
        "mail_city": None, "mail_state": None, "email_domain": None, "contact_name": None,
        "contact_title": None, "application_for": None, "applicant_type": None, "gov_contract": None,
        "duration_months": None, "purpose": None, "sta_reason": None, "op_start": None, "op_end": None,
        "equipment": [], "stations": [], "frequencies": [],
    }
    form.update(over)
    return form


class RssTest(unittest.TestCase):
    def setUp(self):
        self.items = els.parse_rss(RSS)

    def test_every_item_parses(self):
        self.assertEqual(len(self.items), RSS.count("<item>"))
        self.assertEqual(len(self.items), 80)

    def test_first_item(self):
        g = self.items[0]
        self.assertEqual(g["file_num"], "1816-EX-ST-2026")
        self.assertEqual((g["seq"], g["type"], g["year"]), (1816, "ST", 2026))
        self.assertEqual(g["callsign"], "WA8XPQ")
        self.assertEqual(g["granted"], date(2026, 10, 1))
        self.assertEqual(g["applicant"], "Carnegie Mellon University")
        self.assertEqual(g["experiment_type"], "Unmanned Aerial Vehicle")
        self.assertEqual(g["url"], "https://apps.fcc.gov/oetcf/els/reports/GetApplicationInfo.cfm?id_file_num=1816-EX-ST-2026")

    def test_entities_are_decoded(self):
        self.assertIn("S&C Electric Company", [g["applicant"] for g in self.items])

    def test_name_with_comma_keeps_whole_name(self):
        g = next(g for g in self.items if g["file_num"] == "1616-EX-ST-2026")
        self.assertEqual(g["applicant"], "Firefly Aerospace, Inc.")
        self.assertEqual(g["experiment_type"], "Rocket Launch")

    def test_feed_filter(self):
        keep = [g["applicant"] for g in self.items
                if g["type"] in els.WALK_TYPES and not els.is_stoplisted(g["applicant"])
                and els._feed_on_thesis(g["applicant"], g["experiment_type"])]
        self.assertIn("Gravimon Ltd", keep)  # thesis experiment type
        self.assertIn("Watney Robotics Inc.", keep)  # WiFi type, but the name is on thesis
        self.assertNotIn("Carnegie Mellon University", keep)
        self.assertNotIn("Broadcast Sports International", keep)
        self.assertNotIn("FCCTest1", keep)
        self.assertNotIn("Gulfstream Aerospace Corporation", keep)

    def test_one_strong_word_in_a_name_is_enough_to_read_the_form(self):
        # The classifier holds a lone ambiguous word just under its gate. A
        # feed row has only the name, so that word still earns a fetch; a
        # lone context word ("Aerospace") does not, as before.
        self.assertLess(classify("Orbital Harbor Inc. equipment testing, general")["fit"], 0.3)
        self.assertTrue(els._feed_on_thesis("Orbital Harbor Inc", "equipment testing, general"))
        # "Lunar" and "Radar" can hardly mean anything else: they pass the
        # classifier's own gate, so they are read with or without this allowance.
        for name in ("Lunar Harbor Inc", "Harbor Radar Inc"):
            self.assertGreaterEqual(classify(f"{name}. equipment testing, general")["fit"], 0.3)
            self.assertTrue(els._feed_on_thesis(name, "equipment testing, general"))
        self.assertFalse(els._feed_on_thesis("Harbor Aerospace Inc", "Communications, general"))
        self.assertFalse(els._feed_on_thesis("Harbor Networks Inc", "Communications, general"))

    def test_granted_sta_is_read_whatever_the_name_says(self):
        # An STA form states the experiment in the applicant's words, so the
        # name is not what decides. A new-licence form states nothing.
        rows = {g["file_num"]: g for g in self.items}
        abside = rows["1770-EX-ST-2026"]  # "Abside Networks, Inc.", type "5G technology"
        self.assertFalse(els._feed_on_thesis(abside["applicant"], abside["experiment_type"]))
        self.assertTrue(els._worth_reading(abside))
        self.assertFalse(els._worth_reading(rows["1535-EX-ST-2026"]))  # an event crew, "Remote audio and video"
        self.assertFalse(els._worth_reading(rows["0937-EX-CN-2026"]))  # new licence, name and type say nothing
        self.assertFalse(els._worth_reading(rows["0356-EX-CN-2026"]))  # GPS re-radiator
        self.assertTrue(els._worth_reading(rows["0721-EX-CN-2026"]))  # new licence in a thesis category
        self.assertTrue(els._worth_reading(dict(rows["1535-EX-ST-2026"], applicant="Harbor Drone Racing")))


class ApplicationInfoTest(unittest.TestCase):
    def test_pending_row(self):
        info = els.parse_application_info(INFO_PAGE)
        self.assertEqual(info["file_num"], "0950-EX-CN-2026")
        self.assertEqual(info["applicant"], "Astro Digital US, Inc.")
        self.assertEqual(info["status"], "Pending")
        self.assertEqual(info["received"], date(2026, 9, 2))
        self.assertEqual(info["status_date"], date(2026, 9, 2))
        self.assertIsNone(info["callsign"])  # "N/A" until grant
        self.assertEqual((info["seq"], info["type"], info["year"]), (950, "CN", 2026))

    def test_links_point_at_the_current_form(self):
        info = els.parse_application_info(INFO_PAGE)
        self.assertEqual(
            info["form_url"],
            "https://apps.fcc.gov/oetcf/els/reports/442_Print.cfm?mode=current&application_seq=154127&license_seq=156093",
        )
        self.assertIn("ViewExhibitReport.cfm?id_file_num=0950-EX-CN-2026", info["exhibits_url"])

    def test_missing_number_is_none(self):
        self.assertIsNone(els.parse_application_info(MISSING_PAGE))
        self.assertIsNone(els.parse_application_info("<html><body>Access Denied</body></html>"))

    def test_cache_lifetime_follows_status(self):
        self.assertEqual(els._info_ttl(INFO_PAGE), els.PENDING_TTL)
        self.assertEqual(els._info_ttl(MISSING_PAGE), els.MISSING_TTL)
        granted = INFO_PAGE.replace(">Pending<", ">Granted<")
        self.assertEqual(els._info_ttl(granted), els.FINAL_TTL)
        self.assertGreater(els.FINAL_TTL, els.PENDING_TTL)


class Form442Test(unittest.TestCase):
    def setUp(self):
        self.form = els.parse_form(FORM_442)

    def test_applicant_block(self):
        f = self.form
        self.assertFalse(f["is_sta"])
        self.assertEqual(f["applicant"], "Hawthorn Aero Inc")
        self.assertEqual(f["attention"], "Eli Lockwood")
        self.assertEqual((f["mail_city"], f["mail_state"]), ("Miamisburg", "OH"))
        self.assertEqual(f["application_for"], "NEW LICENSE")
        self.assertEqual(f["applicant_type"], "Corporation")
        self.assertIs(f["gov_contract"], False)
        self.assertEqual(f["duration_months"], 24)

    def test_domain_is_the_mailing_one_not_the_personal_one(self):
        # The form carries two addresses: the company one on the mailing
        # block and a gmail one on the contact block.
        self.assertEqual(self.form["email_domain"], "hawthornaero.com")
        self.assertTrue(self.form["attention_owns_email"])

    def test_contact(self):
        self.assertEqual(self.form["contact_name"], "Elijah Lockwood")
        self.assertEqual(self.form["contact_title"], "VP of Systems")

    def test_empty_value_does_not_swallow_next_label(self):
        # "P.O. Box:" is blank on this form; "City:" must still be the city.
        pairs = dict(els._labelled(FORM_442)[:12])
        self.assertEqual(pairs["P.O. Box:"], "")
        self.assertEqual(pairs["City:"], "Miamisburg")

    def test_no_phone_or_mailbox_kept(self):
        blob = repr(self.form)
        self.assertNotIn("0000000000", blob)
        self.assertNotIn("@", blob)
        self.assertNotIn("lockwood.elijah", blob)

    def test_equipment(self):
        eq = self.form["equipment"]
        self.assertEqual([e["model"] for e in eq], ["L125GPSA-T", "L125GRRKPA-T", "HNRRKAMP-N/5/MC"])
        self.assertTrue(all(e["manufacturer"] == "GPS Networking" and e["units"] == 1 for e in eq))
        self.assertFalse(any(e["experimental"] for e in eq))

    def test_station(self):
        (st,) = self.form["stations"]
        self.assertEqual((st["city"], st["state"]), ("Miamisburg", "Ohio"))
        self.assertAlmostEqual(st["lat"], 39.6058, places=4)  # N 39 36 21
        self.assertAlmostEqual(st["lon"], -84.2428, places=4)  # W 84 14 34
        self.assertIsNone(st["mobile"])  # the redacted digits are not a place

    def test_frequencies(self):
        self.assertEqual([f["label"] for f in self.form["frequencies"]], ["1227.6 MHz", "1575.42 MHz", "1575.42 MHz"])
        self.assertEqual(self.form["frequencies"][0]["station_class"], "FX")

    def test_gps_reradiator_is_not_emitted(self):
        # GPS L1 and L2 at picowatts from GPS Networking repeaters: a hangar
        # re-radiator, and nothing in the form places it on the thesis.
        self.assertTrue(els._gps_only(self.form))
        self.assertIsNone(els.build_signal(HAWTHORN_INFO, self.form, None, 1))

    def test_title_and_text_for_this_form(self):
        self.assertEqual(
            els.build_title(HAWTHORN_INFO, self.form, None),
            "Applied for a new FCC experimental licence to test at Miamisburg, Ohio on 1227.6 and 1575.42 MHz",
        )
        text = els.build_text(HAWTHORN_INFO, self.form, None)
        self.assertIn("1 x GPS Networking L125GPSA-T", text)
        self.assertNotIn("Byers Rd", text)  # street addresses stay out

    def test_person_is_the_mailing_contact_with_the_matching_title(self):
        (p,) = els.filing_people(self.form, "hawthornaero.com")
        self.assertEqual((p.name, p.role), ("Eli Lockwood", "VP of Systems"))
        self.assertEqual(p.affiliations, [])


class StaTest(unittest.TestCase):
    def setUp(self):
        self.form = els.parse_form(STA_PRINT)

    def test_fields(self):
        f = self.form
        self.assertTrue(f["is_sta"])
        self.assertEqual(f["applicant"], "Neros Technologies")
        self.assertEqual(f["email_domain"], "neros.tech")
        self.assertEqual(f["contact_name"], "William LaRose")  # the form lists last name first
        self.assertEqual(f["contact_title"], "Regulatory Affairs Lead")
        self.assertEqual(f["purpose"], "The purpose of this operation is to test the EcoShield radar.")
        self.assertEqual(f["sta_reason"], "A STA is needed to conduct temporary testing.")
        self.assertEqual((f["op_start"], f["op_end"]), (date(2026, 9, 21), date(2027, 3, 21)))

    def test_tables_survive_the_broken_closing_tag(self):
        self.assertEqual(self.form["equipment"],
                         [{"manufacturer": "Echodyne", "model": "EchoShield", "units": 1, "experimental": True}])
        (st,) = self.form["stations"]
        self.assertEqual((st["city"], st["state"], st["radius"]), ("Helendale", "California", "10.00"))
        self.assertAlmostEqual(st["lat"], 34.7036, places=4)
        self.assertAlmostEqual(st["lon"], -117.3756, places=4)
        (fr,) = self.form["frequencies"]
        self.assertEqual(fr["label"], "15.4-16.66 GHz")
        self.assertEqual((fr["low_mhz"], fr["high_mhz"]), (15400.0, 16660.0))

    def test_granted_signal(self):
        sig = els.build_signal(NEROS_INFO, self.form, "Fixed Radar", 1)
        self.assertIsInstance(sig, Signal)
        sig.validate()
        self.assertEqual(sig.kind, "fcc_sta")
        self.assertEqual(sig.entity.name, "Neros Technologies")
        self.assertEqual(sig.entity.domain, "neros.tech")
        self.assertEqual(sig.entity.location, "Torrance, CA")
        self.assertEqual(sig.occurred_at, "2026-09-09")  # the grant, not the filing
        self.assertEqual(sig.url, "https://apps.fcc.gov/oetcf/els/reports/GetApplicationInfo.cfm?id_file_num=1649-EX-ST-2026")
        self.assertEqual((sig.value, sig.unit), (22.0, "days to grant"))
        self.assertEqual(sig.title,
                         "Received FCC special temporary authority for fixed radar testing on 15.4-16.66 GHz, 22 days after filing")
        self.assertEqual(sig.metrics["els_days_to_grant"], 22)
        self.assertEqual(sig.metrics["els_received"], "2026-08-18")
        self.assertEqual(sig.metrics["els_operation_end"], "2027-03-21")
        self.assertEqual([(p.name, p.role) for p in sig.people], [("William LaRose", "Regulatory Affairs Lead")])
        self.assertIn("EcoShield radar", sig.text)
        self.assertNotIn("0000000000", repr(sig.to_row()))
        self.assertNotIn("@", repr(sig.to_row()))

    def test_text_carries_both_answers_the_applicant_wrote(self):
        text = els.build_text(NEROS_INFO, self.form, "Fixed Radar")
        self.assertIn("Purpose of operation: The purpose of this operation is to test the EcoShield radar.", text)
        self.assertIn("Why the authority is needed: A STA is needed to conduct temporary testing.", text)
        # Applicants often give the same sentence twice; it is written once.
        twice = dict(self.form, sta_reason="The purpose of this operation is to test the EcoShield radar")
        self.assertNotIn("Why the authority is needed", els.build_text(NEROS_INFO, twice, None))
        self.assertNotIn("Why the authority is needed", els.build_text(HAWTHORN_INFO, els.parse_form(FORM_442), None))

    def test_the_second_answer_can_place_the_filing(self):
        # A purpose that names nothing on the thesis (1872-EX-ST-2026's) is
        # not enough. The words of 1650-EX-ST-2026's "why an STA is
        # necessary" answer, quoted without its radar clause, are what place
        # such a filing.
        pending = dict(NEROS_INFO, status="Pending", status_date=date(2026, 8, 18), callsign=None)
        quiet = dict(self.form, purpose="To conduct over-the-air communications using radio frequency (RF) radiation")
        self.assertEqual(classify(els.build_text(pending, quiet, None))["terms"], [])
        self.assertIsNone(els.build_signal(pending, quiet, None, 1))
        told = dict(quiet, sta_reason="Special Temporary Authority is requested to conduct time-limited radiolocation "
                                      "testing in support of counter-unmanned aircraft system (C-UAS) research and development.")
        sig = els.build_signal(pending, told, None, 1)
        self.assertIsNotNone(sig)
        self.assertIn("counter-unmanned aircraft system", sig.text)

    def test_contact_details_typed_into_free_text_are_not_kept(self):
        # Applicants end these answers with a "stop buzzer" name and number.
        page = STA_PRINT.replace("to test the EcoShield radar.",
                                 "to test the EcoShield radar. 24/7 Stop Buzzer is Pat Example, 650-555-0100, pat@example.com")
        page = page.replace("conduct temporary testing.", "conduct temporary testing. Stop Buzzers: A +1 914.555.0188 B +44 7700 900123")
        form = els.parse_form(page)
        self.assertEqual(form["purpose"], "The purpose of this operation is to test the EcoShield radar. 24/7 Stop Buzzer is Pat Example, ,")
        self.assertEqual(form["sta_reason"], "A STA is needed to conduct temporary testing. Stop Buzzers: A B")
        row = repr(els.build_signal(NEROS_INFO, form, "Fixed Radar", 1).to_row())
        for leak in ("555", "7700", "@", "example.com"):
            self.assertNotIn(leak, row)
        for number in ("(415) 555-0134", "914.555.0188", "703-555-0127", "+44 7700 900123"):
            self.assertIsNone(els._scrub(number), number)
        # Frequencies, file numbers, dates and counts are none of those shapes.
        for kept in ("2412-2472 MHz", "902.3-914.9 MHz", "1164.00000000-1610.00000000 MHz", "2.2 and 7.125-7.250 GHz",
                     "File Nos. 0697-EX-ST-2024 and 0321-EX-ST-2025", "File No. SES-LIC-20260121-00612",
                     "FCC ID 2ANLB-MESASSR00053", "October 19-23, 2026", "phases of 150, 250 and 250 units",
                     "Fort Hood, TX 76544", "+/- 5 MHz", "up to 29000 feet"):
            self.assertEqual(els._scrub(kept), kept)

    def test_pending_sta_must_show_thesis_in_its_own_words(self):
        # There is no FCC category until the grant, so the applicant's words
        # decide. One applicant filed twice in a row on 2026-09-30:
        # 1968-EX-ST-2026 does not say what the product is, 1967-EX-ST-2026 does.
        pending = dict(NEROS_INFO, status="Pending", status_date=date(2026, 8, 18), callsign=None)
        vague = dict(self.form, purpose="Demonstration for end-customers on operation of product.",
                     sta_reason="Temporary testing and demonstration of product.")
        self.assertIsNone(els.build_signal(pending, vague, None, 1))
        said = dict(self.form, purpose="Demonstration for end-customers on operation of product for tracking drones.",
                    sta_reason="Temporary Evaluation and Testing")
        sig = els.build_signal(pending, said, None, 1)
        self.assertEqual(sig.kind, "fcc_sta")
        self.assertEqual(sig.occurred_at, "2026-08-18")  # still open: dated by receipt
        self.assertTrue(sig.title.startswith("Applied for FCC special temporary authority"))
        self.assertEqual((sig.value, sig.unit), (1.0, "transmitters"))

    def test_radar_alone_passes_on_the_applicants_words(self):
        # "radar" is a thesis word in its own right now. The form Neros filed
        # and the purposes of 1838-EX-ST-2026 (Robin Radar Systems),
        # 1944-EX-ST-2026 (Echodyne) and 1694-EX-ST-2026 (Aloft Sensing) say
        # nothing else on the thesis. Each is tried as a pending STA, which
        # has no FCC category to lean on.
        pending = dict(NEROS_INFO, status="Pending", status_date=date(2026, 8, 18), callsign=None)
        for purpose in [self.form["purpose"],
                        "The purpose of the operation is to showcase and demonstrate our radar capabilities.",
                        "The purpose of the operations is to test the EchoGuard radar.",
                        "To test Aloft Sensing, Inc. X-band radar sensors from the stratosphere for wildfire research "
                        "and demonstration purposes."]:
            sig = els.build_signal(pending, dict(self.form, purpose=purpose), None, 1)
            self.assertIsNotNone(sig, purpose)
            read = classify(sig.text)
            self.assertEqual((read["sector"], read["terms"]), ("defense", ["radar"]), purpose)
            self.assertGreaterEqual(read["fit"], 0.3)
            self.assertTrue(sig.title.startswith("Applied for FCC special temporary authority"))


class JudgementTest(unittest.TestCase):
    def test_stoplist(self):
        for name in ["Carnegie Mellon University", "MIT Lincoln Laboratory", "The BOEING Company",
                     "Lockheed Martin Corporation", "Northrop Grumman Systems Corporation",
                     "T-Mobile License LLC", "County of Oneida", "Kuiper Systems LLC",
                     "Space Exploration Technologies Corp. (SpaceX)", "FCCTest1", "Testz 9/30/26 1pm",
                     "Testing STA Form (CBTS)", "George Washington UniversitY", "The Mitre Corp"]:
            self.assertTrue(els.is_stoplisted(name), name)
        for name in ["Firestorm Labs, Inc", "Starcloud, Inc.", "Neros Technologies", "Hawthorn Aero Inc",
                     "Reliable Robotics Corporation", "Intelligent Robotics Co", "Testbed Robotics"]:
            self.assertFalse(els.is_stoplisted(name), name)

    def test_listed_and_long_established_companies_are_stoplisted(self):
        # On thesis, but not a discovery: all of these were in the live feed on 2026-10-01.
        for name in ["Anduril Industries, Inc.", "Joby Aero, Inc.", "Firefly Aerospace, Inc.", "DroneShield LLC",
                     "TCOM, L.P.", "Toyon Research Corporation", "Technology Service Corporation",
                     "Weibel Equipment, Inc."]:
            self.assertTrue(els.is_stoplisted(name), name)
        # 1966-EX-ST-2026: "radar level measuring instruments" for process
        # plants. Its one thesis word passes the gate now, so the maker is named.
        self.assertTrue(els.is_stoplisted("VEGA Grieshaber KG"))
        for name in ["Archer Robotics", "Voyager Robotics", "Planetary Systems", "Red Canyon Software",
                     "Aloft Sensing, Inc.", "Northwood Space Corp."]:
            self.assertFalse(els.is_stoplisted(name), name)
        # build_signal refuses them too, whichever path the row came by.
        form = els.parse_form(STA_PRINT)
        self.assertIsNone(els.build_signal(dict(NEROS_INFO, applicant="Anduril Industries, Inc."), form, "Fixed Radar", 1))
        form["email_domain"] = "eng.anduril.com"  # a subdomain of a stoplisted mailbox
        self.assertIsNone(els.build_signal(NEROS_INFO, form, "Fixed Radar", 1))

    def test_telecom_lookalikes_need_thesis_evidence(self):
        # A new-licence form has no description, so a carrier or an ISP reads
        # like a hardware startup. With nothing on thesis it is dropped; a
        # name that is not telecom is still emitted for the join to judge.
        form = bare_form(email_domain="example.net",
                         frequencies=[els._frequency("3700.00000000-3800.00000000 MHz")])
        for name in ["PARALLEL WIRELESS INC", "MidwayNet, LLC", "Valley Broadband Co"]:
            self.assertIsNone(els.build_signal(dict(HAWTHORN_INFO, applicant=name), form, None, 1), name)
        for name in ["Tiami Networks Inc", "Beamlink, Inc.", "Planet Forge Inc"]:
            self.assertIsNotNone(els.build_signal(dict(HAWTHORN_INFO, applicant=name), form, None, 1), name)
        drone = dict(form, stations=[{"city": None, "state": None, "lat": None, "lon": None, "radius": None,
                                      "mobile": "Unmanned aircraft datalink flight test", "where": None}])
        sig = els.build_signal(dict(HAWTHORN_INFO, applicant="Skyline Wireless Drones Inc"), drone, "Unmanned Aerial Vehicle", 1)
        self.assertIsNotNone(sig)  # an FCC category on the thesis outranks the name

    def test_category_is_put_in_words_the_classifier_reads(self):
        def read(kind):
            return classify(els.build_text(NEROS_INFO, bare_form(), kind))

        rocket = read("Rocket Launch")  # "rocket" alone used to sit at 0.245
        self.assertIn("launch vehicle", rocket["terms"])
        self.assertGreaterEqual(rocket["fit"], 0.3)
        # "other than cubesats" is not a cubesat. The classifier blanks the
        # phrase itself now, so the FCC's words are kept and one is added:
        # "Space" alone is not a thesis term.
        self.assertIn("other than cubesats", els.TYPE_IN_WORDS["Space (other than cubesats)"])
        self.assertEqual(classify("Experiment type: Space (other than cubesats).")["terms"], [])
        space = read("Space (other than cubesats)")
        self.assertNotIn("cubesat", space["terms"])
        self.assertEqual((space["sector"], space["terms"]), ("space", ["spacecraft"]))
        self.assertIn("cubesat", read("Cubesats")["terms"])  # the plural matches now
        self.assertEqual(read("big LEO (Low Earth Orbit)")["terms"], ["low earth orbit", "satellite"])
        self.assertGreaterEqual(read("Unmanned Aerial Vehicle")["fit"], 0.3)
        self.assertGreaterEqual(read("Autonomous Ground Vehicle")["fit"], 0.3)
        # A radar category says "radar" and no more, and that word is enough
        # now: it needs no rendering of its own.
        for kind in ("Fixed Radar", "Vehicle Radar"):
            self.assertNotIn(kind, els.TYPE_IN_WORDS)
            self.assertEqual((read(kind)["sector"], read(kind)["terms"]), ("defense", ["radar"]))
            self.assertGreaterEqual(read(kind)["fit"], 0.3)
        # A category that names no thesis word and has no plain meaning to
        # add is left as the FCC wrote it: the applicant's words decide.
        for kind in ("RF Sensor", "High-Altitude Platform System"):
            self.assertEqual(read(kind)["terms"], [])
        for label, words in els.TYPE_IN_WORDS.items():
            self.assertIn(label, els.THESIS_TYPES)
            self.assertTrue(words.startswith(label.split(" (")[0]), words)  # the FCC's label is still what leads
        # The label itself is kept verbatim where it is data.
        sig = els.build_signal(NEROS_INFO, els.parse_form(STA_PRINT), "Space (other than cubesats)", 1)
        self.assertEqual(sig.metrics["els_experiment_type"], "Space (other than cubesats)")
        self.assertIn("Experiment type: Space (spacecraft other than cubesats).", sig.text)

    def test_a_vendors_name_does_not_describe_the_applicant(self):
        # 1547-EX-ST-2026: a power-tool maker range-testing two Nordic
        # Semiconductor dev kits. "semiconductor" is the vendor's trade.
        def gear(maker, model, units=2, experimental=False):
            return [{"manufacturer": maker, "model": model, "units": units, "experimental": experimental}]

        info = dict(NEROS_INFO, applicant="Milwaukee Electric Tool Corp", status="Pending",
                    status_date=date(2026, 8, 18), callsign=None)
        form = bare_form(purpose="Range testing using a pair of Rx/Tx chips with antennas.",
                         equipment=gear("Nordic Semiconductor", "NRF9151-SMA-DK"))
        text = els.build_text(info, form, None)
        self.assertIn("Transmitters: 2 x NRF9151-SMA-DK.", text)
        self.assertNotIn("Semiconductor", text)
        self.assertIsNone(els.build_signal(info, form, None, 1))
        # The applicant's own make is what the applicant builds: it stays whole.
        own = dict(NEROS_INFO, applicant="Robin Radar Systems")
        self.assertIn("1 x Robin Radar Systems B.V. IRIS.",
                      els.build_text(own, bare_form(equipment=gear("Robin Radar Systems B.V.", "IRIS", 1)), None))
        self.assertTrue(els._own_make("BlueForce Technologies Ltd.", "BlueForce"))
        self.assertTrue(els._own_make("ACME AtronOmatic", "ACME AtronOmatic LLC d/b/a MyRadar"))
        self.assertFalse(els._own_make("Nordic Semiconductor", "Milwaukee Electric Tool Corp"))
        self.assertFalse(els._own_make("Systems", "Robin Radar Systems"))
        # A vendor name with no thesis word in it is left alone, and nothing
        # is trimmed from a model that ends in "x" or has no unit count.
        self.assertIn("1 x Echodyne EchoShield (experimental).",
                      els.build_text(info, bare_form(equipment=gear("Echodyne", "EchoShield", 1, True)), None))
        self.assertIn("Transmitters: 3 x Vertex Linux box; Vertex T1.",
                      els.build_text(info, bare_form(equipment=gear("Vertex", "Linux box", 3) + gear("Vertex", "T1", None)), None))

    def test_event_production_is_not_a_space_or_a_drone_company(self):
        # 1864-EX-ST-2026, in the applicant's words. "satellite broadcast"
        # used to be enough for the keyword model. The classifier blanks the
        # phrase now, and the thesis gate drops the filing without help.
        info = dict(NEROS_INFO, applicant="Frequency Coordination Group", status="Pending",
                    status_date=date(2026, 8, 18), callsign=None)
        form = bare_form(
            purpose="Remote Video and Audio Support for Latin Billboard Award Show at the James L Knight Center",
            sta_reason="Applicant is a television and event production company which provides video, audio and communications "
                       "equipment and services for broadcast, satellite broadcast and cablecast of sporting and other events.")
        self.assertEqual(classify(els.build_text(info, form, None))["terms"], [])
        self.assertIsNone(els.build_signal(info, form, None, 1))
        # The production filter is for the crew whose words do carry a thesis
        # term. This purpose is not from a filing: no crew in the feed on
        # 2026-10-01 wrote one.
        crew = dict(form, purpose="Wireless video from drone cameras for coverage of a drone racing event")
        self.assertGreaterEqual(classify(els.build_text(info, crew, None))["fit"], 0.3)
        self.assertIsNone(els.build_signal(info, crew, None, 1))
        # A thesis category from the FCC outranks the wording, as it does a telecom name.
        self.assertIsNotNone(els.build_signal(dict(info, status="Granted"), form, "Unmanned Aerial Vehicle", 1))
        # "broadcast" as radio engineers use it (1941-EX-ST-2026) is not production.
        self.assertIsNone(els._PRODUCTION_TEXT.search("The transmitters will broadcast unmodulated, LFM and phase modulation waveforms"))
        said = dict(form, purpose="Flight test of a counter-UAS interceptor drone against small unmanned aircraft.",
                    sta_reason="The transmitters will broadcast unmodulated waveforms.")
        self.assertIsNotNone(els.build_signal(info, said, None, 1))

    def test_wideband_gnss_repeater_is_a_reradiator_too(self):
        # 1031-EX-CN-2026: one FalTech GPS repeater filed as a single 1164-1610 MHz entry.
        wide = bare_form(frequencies=[els._frequency("1164.00000000-1610.00000000 MHz")])
        self.assertTrue(els._gps_only(wide))
        self.assertIsNone(els.build_signal(dict(HAWTHORN_INFO, applicant="Stauder Technologies"), wide, None, 1))
        radio = bare_form(frequencies=[els._frequency("1164.00000000-1610.00000000 MHz"),
                                       els._frequency("2200.00000000-2290.00000000 MHz")])
        self.assertFalse(els._gps_only(radio))
        lband = bare_form(frequencies=[els._frequency("1435.00000000-1525.00000000 MHz")])
        self.assertFalse(els._gps_only(lband))

    def test_dba_name_is_split(self):
        self.assertEqual(els.split_dba("ACME AtronOmatic LLC d/b/a MyRadar"), ("MyRadar", ["ACME AtronOmatic LLC"]))
        self.assertEqual(els.split_dba("Foo Labs, Inc. DBA Bar Robotics"), ("Bar Robotics", ["Foo Labs, Inc."]))
        self.assertEqual(els.split_dba("Starcloud, Inc."), ("Starcloud, Inc.", []))
        self.assertEqual(els.split_dba("Adba Systems"), ("Adba Systems", []))
        sig = els.build_signal(dict(HAWTHORN_INFO, applicant="ACME AtronOmatic LLC d/b/a MyRadar"),
                               bare_form(email_domain="myradar.com", mail_city="orlando", mail_state="FL"), None, 1)
        self.assertEqual((sig.entity.name, sig.entity.aliases), ("MyRadar", ["ACME AtronOmatic LLC"]))
        self.assertEqual(sig.entity.domain, "myradar.com")
        self.assertEqual(sig.entity.location, "Orlando, FL")  # typed in lower case on the form

    def test_company_domain_is_kept_only_when_it_is_the_applicants(self):
        ok = [("launchfirestorm.com", "Firestorm Labs, Inc"), ("neros.tech", "Neros Technologies"),
              ("reliable.co", "Reliable Robotics Corporation"), ("flyzipline.com", "Zipline International"),
              ("str.us", "Systems & Technology Research"), ("maszengrange.co.nz", "MAS Zengrange (NZ) Ltd"),
              ("k2space.com", "K2 Space Corporation"), ("myradar.com", "ACME AtronOmatic LLC d/b/a MyRadar"),
              # the whole name, even when every word of it is a common one
              ("advancedspace.com", "Advanced Space, LLC"), ("surtrdefense.com", "Surtr Defense Systems, Inc.")]
        for dom, name in ok:
            self.assertEqual(els.company_domain(dom, name), dom, name)
        wrong = [("boeing.com", "Wisk Aero, LLC"), ("axon.com", "Dedrone Holdings, LLC"),
                 ("hwglaw.com", "VEGA Grieshaber KG"), ("jenner.com", "National Operations Research Alliance"),
                 ("gmail.com", "Targeted Meteorological Systems"), ("yahoo.com", "Cartier Aviation"),
                 ("alaska.edu", "ACUASI"), ("spacelaw.com", "Apex Space Inc"), (None, "Anyone Inc"),
                 # a short label that merely occurs inside the name
                 ("arc.com", "Starcloud, Inc."), ("ast.com", "Blast Dynamics"),
                 # a generic word is nobody's domain
                 ("space.com", "Northwood Space Corp."), ("robotics.com", "Watney Robotics Inc."),
                 # a common four-letter word of the name inside someone else's label
                 ("skybluehalo.com", "Blue Sky Robotics"), ("dzyne.com", "High Point Aerotechnologies"),
                 ("makerain.com", "Rainmaker Technology Corporation"),
                 # only the descriptive words of the name, which the name key now keeps
                 ("defensesystems.com", "Surtr Defense Systems, Inc."), ("spacetech.com", "Varda Space Technologies")]
        for dom, name in wrong:
            self.assertIsNone(els.company_domain(dom, name), name)
        # Every domain the live probe attached on 2026-10-01 still passes.
        live = [("kmb.ac", "KMB Telematics Inc."), ("noshrobotics.co", "Nosh Robotics Inc"),
                ("blue-force.com", "BlueForce"), ("shifters-ai.com", "Shifters Robotic Systems Ltd."),
                ("virtussolis.space", "Virtus Solis Technologies"), ("stucan-solutions.com", "Stucan Solutions Corp"),
                ("watneyrobotics.com", "Watney Robotics Inc."), ("poseidonaero.com", "Poseidon Aerospace"),
                ("fortemtech.com", "Fortem Technologies, Inc."), ("luxaeterna.com", "Lux Aeterna Space, Inc."),
                ("robinradar.com", "Robin Radar Systems"), ("northwoodspace.io", "Northwood Space Corp.")]
        for dom, name in live:
            self.assertEqual(els.company_domain(dom, name), dom, name)

    def test_parent_company_mailbox_drops_the_filing(self):
        form = els.parse_form(STA_PRINT)
        form["email_domain"] = "boeing.com"
        self.assertIsNone(els.build_signal(NEROS_INFO, form, "Fixed Radar", 1))
        form["email_domain"] = "ll.mit.edu"
        self.assertIsNone(els.build_signal(NEROS_INFO, form, "Fixed Radar", 1))
        # National institution hosts, including the ones clean_domain now
        # refuses: what is not a company's domain is not a company's filing.
        for host in ("drdc-rddc.gc.ca", "dstl.gov.uk", "ntu.edu.sg", "auckland.ac.nz", "iitb.ac.in"):
            self.assertTrue(els._stop_domain(host), host)
            self.assertIsNone(els.company_domain(host, "Neros Technologies"), host)
        self.assertFalse(els._stop_domain("kmb.ac"))  # a company on the .ac registry, not a university

    def test_outside_counsel_is_not_the_team(self):
        # Mailing block is the company's own person; the "best contact" is a
        # law-firm partner. Only the first is attached, without the title.
        form = bare_form(attention="Kit Cutler", attention_owns_email=True, email_domain="watneyrobotics.com",
                         contact_name="Jodi Goldberg", contact_title="Partner")
        (p,) = els.filing_people(form, "watneyrobotics.com")
        self.assertEqual((p.name, p.role), ("Kit Cutler", None))
        # A law firm's mailbox on the mailing block: nobody is attached.
        form = bare_form(attention="Pat Lee", attention_owns_email=True, email_domain="hwglaw.com",
                         contact_name="Pat Lee", contact_title="Legal Counsel")
        self.assertEqual(els.filing_people(form, None), [])
        # A founder writing from a personal mailbox is kept.
        form = bare_form(attention="Edward Cartier Jr", email_domain="yahoo.com",
                         contact_name="Edward Cartier Jr", contact_title="Owner")
        (p,) = els.filing_people(form, None)
        self.assertEqual((p.name, p.role), ("Edward Cartier Jr", "Owner"))

    def test_a_desk_is_not_a_person(self):
        # "Attention: Spectrum Management" with spectrum@company.com would
        # otherwise pass as a person who owns that mailbox.
        for desk in ["Spectrum Management", "Regulatory Affairs", "Legal Department", "Licensing Team"]:
            form = bare_form(attention=desk, attention_owns_email=True, email_domain="example-aero.com",
                             contact_name=desk, contact_title="Manager")
            self.assertEqual(els.filing_people(form, "example-aero.com"), [], desk)
        self.assertTrue(els._owns_mailbox("Dr. Shammi Doly", "sdoly"))
        self.assertTrue(els._owns_mailbox("Severin Staehly", "severin.staehly"))
        self.assertTrue(els._owns_mailbox("Ann Lee", "ann.lee"))
        self.assertTrue(els._owns_mailbox("Ann Lee", "alee"))
        self.assertFalse(els._owns_mailbox("Ann Lee", "planning"))
        self.assertFalse(els._owns_mailbox("Kyle Wesson", "regulatory"))
        self.assertFalse(els._owns_mailbox(None, "info"))

    def test_swapped_and_shortened_names_are_one_person(self):
        self.assertTrue(els._same_person("Michael Downing", "Downing Michael"))
        self.assertTrue(els._same_person("Eli Lockwood", "Elijah Lockwood"))
        self.assertTrue(els._same_person("MATTHEW W. SMITH", "Matthew Smith"))
        self.assertFalse(els._same_person("Kit Cutler", "Jodi Goldberg"))
        self.assertFalse(els._same_person("John Smith", "Jane Smith"))

    def test_private_individuals_are_left_out(self):
        form = bare_form(attention="Burke Donovan", contact_name="Burke Donovan", contact_title="Applicant",
                         email_domain="gmail.com", purpose="Radar sensor to detect small unmanned aircraft.")
        info = dict(NEROS_INFO, applicant="Burke Donovan")
        self.assertTrue(els.is_individual("Burke Donovan", form))
        self.assertIsNone(els.build_signal(info, form, "Vehicle Radar", 1))
        self.assertFalse(els.is_individual("Tiami Networks Inc", bare_form(applicant_type="Individual")))

    def test_strength_ordering_and_range(self):
        cn_pending = dict(HAWTHORN_INFO)
        cn_granted = dict(HAWTHORN_INFO, status="Granted", status_date=date(2026, 10, 1))
        st_granted = dict(NEROS_INFO)
        st_pending = dict(NEROS_INFO, status="Pending", callsign=None)
        st_extension = dict(NEROS_INFO, status="Pending")  # still carries the call sign of an earlier grant
        plain = bare_form()
        uav = "Unmanned Aerial Vehicle"
        s = {
            "cn_pending": els.strength_of(cn_pending, plain, uav, 1, 0.8),
            "cn_granted": els.strength_of(cn_granted, plain, uav, 1, 0.8),
            "st_pending": els.strength_of(st_pending, plain, uav, 1, 0.8),
            "st_granted": els.strength_of(st_granted, plain, uav, 1, 0.8),
            "st_granted_repeat": els.strength_of(st_granted, plain, uav, 8, 0.8),
            "st_extension": els.strength_of(st_extension, plain, uav, 1, 0.8),
        }
        self.assertGreater(s["cn_pending"], s["cn_granted"])
        self.assertGreater(s["cn_granted"], s["st_granted"])
        self.assertGreater(s["st_pending"], s["st_granted"])
        self.assertLess(s["st_granted_repeat"], 0.3)  # routine
        # Asking to extend an STA already held is no earlier than the grant was.
        self.assertLess(s["st_extension"], s["st_pending"])
        self.assertLessEqual(s["st_extension"], s["st_granted"])
        # The only-filing-seen premium is small: most applicants qualify for it.
        self.assertLessEqual(s["cn_pending"] - els.strength_of(cn_pending, plain, uav, 2, 0.8), 0.1)
        self.assertTrue(0.6 <= s["cn_pending"] <= 0.8)  # notable
        fleet = bare_form(equipment=[{"manufacturer": "X", "model": "Y", "units": 200, "experimental": True}],
                          gov_contract=True)
        top = els.strength_of(cn_pending, fleet, uav, 1, 0.8, "Co-Founder and CTO")
        self.assertGreaterEqual(top, 0.85)  # rare: founder, fleet scale, own radios, government contract
        self.assertLessEqual(top, 0.95)
        vp = els.strength_of(cn_pending, plain, uav, 1, 0.8, "Vice President of Regulatory")
        self.assertEqual(vp, s["cn_pending"])  # a vice president is not a founder
        no_evidence = els.strength_of(cn_pending, plain, None, 1, 0.0)
        self.assertLess(no_evidence, s["cn_pending"])
        gps = els.parse_form(FORM_442)
        self.assertLessEqual(els.strength_of(cn_pending, gps, uav, 1, 0.8), 0.22)

    def test_titles_follow_the_rules(self):
        orbit = bare_form(
            stations=[{"city": None, "state": None, "lat": None, "lon": None, "mobile": "NONGEOSTATIONARY",
                       "where": None, "radius": None}],
            frequencies=[els._frequency("1618.72500000-1626.50000000 MHz"), els._frequency("400.48000000-400.52000000 MHz")],
            equipment=[{"manufacturer": "A", "model": "B", "units": 3, "experimental": False}],
        )
        many = bare_form(
            stations=[{"city": c, "state": "California", "lat": 1.0, "lon": 1.0, "mobile": None, "where": None, "radius": None}
                      for c in ("Mojave", "Edwards", "Bissell")],
            frequencies=[els._frequency("5046.08400000- MHz")],
        )
        junk = bare_form(stations=[{"city": "CONUS", "state": "Contiguous United States", "lat": None, "lon": None,
                                    "mobile": "Various locations throughout CONUS", "where": None, "radius": None}])
        cases = [
            (dict(HAWTHORN_INFO), orbit, None,
             "Applied for a new FCC experimental licence for a non-geostationary satellite with 3 transmitters"),
            (dict(HAWTHORN_INFO), many, None,
             "Applied for a new FCC experimental licence to test at Mojave, California and 2 more sites on 5046.084 MHz"),
            (dict(HAWTHORN_INFO), junk, None,
             "Applied for a new FCC experimental licence, file number 1059-EX-CN-2026"),
            (dict(HAWTHORN_INFO, status="Granted", status_date=date(2026, 9, 28)), many, "Unmanned Aerial Vehicle",
             # "... and 2 more sites" would run past the limit, so the shorter form is used
             "Received a new FCC experimental licence for UAV testing at Mojave, California, 1 day after filing"),
        ]
        for info, form, kind, want in cases:
            got = els.build_title(info, form, kind)
            self.assertEqual(got, want)
        for info, form, kind, _ in cases:
            t = els.build_title(info, form, kind)
            self.assertLess(len(t), 110)
            self.assertFalse(t.endswith("."))
            self.assertTrue(t[0].isupper())
            self.assertTrue(any(ch.isdigit() for ch in t))

    def test_category_phrase_does_not_claim_an_event(self):
        # 1833-EX-ST-2026: the FCC files a radio test at a rocket factory in
        # Bertram, Texas under "Rocket Launch". Nothing launches from there.
        factory = bare_form(stations=[{"city": "Bertram", "state": "Texas", "lat": 30.88, "lon": -97.92,
                                       "mobile": None, "where": None, "radius": None}])
        info = dict(NEROS_INFO, received=date(2026, 9, 15), status_date=date(2026, 9, 25))
        title = els.build_title(info, factory, "Rocket Launch")
        self.assertEqual(title, "Received FCC special temporary authority for launch vehicle testing at Bertram, Texas, "
                                "10 days after filing")
        for phrase in els.THESIS_TYPES.values():
            self.assertNotRegex(phrase, r"^an? ")  # a category, never "a launch" or "a mission"

    def test_place_must_be_a_place(self):
        def st(city=None, state=None, mobile=None):
            return {"city": city, "state": state, "mobile": mobile, "lat": None, "lon": None, "where": None, "radius": None}
        self.assertEqual(els._place(st("AUBURNDALE", "Florida")), "at Auburndale, Florida")
        self.assertEqual(els._place(st("Washington", "Dist of Columbia")), "at Washington, District of Columbia")
        self.assertEqual(els._place(st("UT", "Utah")), "in Utah")
        self.assertEqual(els._place(st("Various", "Texas")), "in Texas")
        self.assertEqual(els._place(st(mobile="Rockaway Township, New Jersey")), "at Rockaway Township, New Jersey")
        self.assertIsNone(els._place(st(mobile="Airborne, CONUS")))
        self.assertIsNone(els._place(st(mobile="Handheld, Nationwide")))
        self.assertIsNone(els._place(st(mobile="Fort Hood, TX 76544, United States")))
        self.assertIsNone(els._place(st("Orbit", "Space Station")))
        self.assertIsNone(els._place(st("Anywhere", "United States (All 50)")))
        self.assertIsNone(els._place(st("CONUS", "Contiguous United States")))
        self.assertEqual(els._place(st(mobile="LEO Iridium 9603")), "for a non-geostationary satellite")

    def test_operating_dates_are_used_only_when_sane(self):
        # 1967-EX-ST-2026 gives an end date five months before its start.
        info = dict(NEROS_INFO, received=date(2026, 9, 1), status_date=date(2026, 9, 9))
        site = [{"city": "Odon", "state": "Indiana", "lat": 1.0, "lon": 1.0, "mobile": None, "where": None, "radius": None}]
        good = bare_form(stations=site, op_start=date(2026, 9, 21), op_end=date(2027, 3, 21))
        typo = bare_form(stations=site, op_start=date(2026, 10, 25), op_end=date(2026, 5, 25))
        self.assertEqual(els.build_title(info, good, None),
                         "Received FCC special temporary authority to test at Odon, Indiana, 8 days after filing, "
                         "valid to 21 Mar 2027")
        self.assertEqual(els.build_title(info, typo, None),
                         "Received FCC special temporary authority to test at Odon, Indiana, 8 days after filing")
        pending = dict(info, status="Pending", status_date=date(2026, 9, 1), callsign=None)
        self.assertEqual(els.build_title(pending, good, None),
                         "Applied for FCC special temporary authority to test at Odon, Indiana, starting 21 Sep 2026")
        early = dict(good, op_start=date(2025, 9, 21))  # before the filing itself
        self.assertEqual(els.build_title(pending, early, None),
                         "Applied for FCC special temporary authority to test at Odon, Indiana")

    def test_frequency_and_coordinates(self):
        self.assertEqual(els._frequency("1227.60000000- MHz")["label"], "1227.6 MHz")
        self.assertEqual(els._frequency("15.40000000-16.66000000 GHz")["high_mhz"], 16660.0)
        self.assertIsNone(els._frequency(""))
        self.assertIsNone(els._dms("North"))  # satellites have no coordinates
        self.assertAlmostEqual(els._dms("South 33 52 4"), -33.8678, places=4)


class FrontierTest(unittest.TestCase):
    def frontier(self, present, hint):
        calls = []

        def exists(kind, year, n):
            calls.append(n)
            return n in present

        return els.find_frontier("CN", 2026, hint, exists), calls

    def test_from_feed_hint(self):
        top, calls = self.frontier(set(range(1, 1083)), 1064)
        self.assertEqual(top, 1082)
        self.assertLess(len(calls), 30)  # gentle: not a linear scan

    def test_gaps_do_not_end_the_walk_early(self):
        present = set(range(1, 1083)) - {1006, 989, 1081}  # real gaps seen in the sequence
        self.assertEqual(self.frontier(present, 1064)[0], 1082)
        self.assertEqual(self.frontier(present, 1080)[0], 1082)

    def test_no_hint_and_empty_year(self):
        self.assertEqual(self.frontier(set(range(1, 41)), 0)[0], 40)
        self.assertEqual(self.frontier(set(), 0), (0, [1]))

    def test_hint_itself_missing(self):
        self.assertEqual(self.frontier(set(range(1, 20)), 500)[0], 19)


class CollectTest(unittest.TestCase):
    def test_runs_offline_and_survives_missing_pages(self):
        # The feed is real; every status lookup answers "no such file number".
        # Nothing can be emitted, nothing may raise, and each feed row that
        # could not be read is reported.
        fetched = []

        def fake_fetch(url, **kw):
            fetched.append(url)
            return RSS if url == els.RSS_URL else MISSING_PAGE

        ctx = Context(today=TODAY, limit=5)
        with mock.patch.object(els, "_fetch", fake_fetch), \
                mock.patch.object(els.shutil, "which", lambda _: "/usr/bin/curl"), \
                mock.patch.object(els.subprocess, "run", side_effect=AssertionError("network")), \
                contextlib.redirect_stderr(io.StringIO()):
            signals = list(els.collect(ctx))
        self.assertEqual(signals, [])
        self.assertEqual(fetched[0], els.RSS_URL)
        self.assertTrue(all("apps.fcc.gov/oetcf/els/" in u for u in fetched))
        self.assertTrue(any("has no status page" in w for w in ctx.warnings))

    def _one_grant_site(self):
        """A fake site holding one granted STA (Neros, 1649-EX-ST-2026) and nothing else."""
        feed = ('<rss version="2.0"><channel><item><title>File Number: 1649-EX-ST-2026, Callsign: WA8XIU    </title>'
                "<description>A grant was issued on 09/09/2026 to Neros Technologies, experiment type: Fixed Radar"
                "</description></item></channel></rss>")
        info = (INFO_PAGE.replace("0950-EX-CN-2026", "1649-EX-ST-2026").replace("Astro Digital US, Inc.", "Neros Technologies")
                .replace(">Pending<", ">Granted<").replace("09/02/2026", "08/18/2026", 1).replace("09/02/2026", "09/09/2026"))
        parsed = els.parse_application_info(info)
        assert parsed and parsed["status"] == "Granted" and parsed["received"] == date(2026, 8, 18), parsed

        def fake_fetch(url, **kw):
            if url == els.RSS_URL:
                return feed
            if "id_file_num=1649-EX-ST-2026" in url:
                return info
            return STA_PRINT if "_Print.cfm" in url else MISSING_PAGE

        return fake_fetch

    def _collect(self, ctx, fetch):
        with mock.patch.object(els, "_fetch", fetch), \
                mock.patch.object(els.shutil, "which", lambda _: "/usr/bin/curl"), \
                mock.patch.object(els.subprocess, "run", side_effect=AssertionError("network")), \
                contextlib.redirect_stderr(io.StringIO()):
            return list(els.collect(ctx))

    def test_end_to_end_over_fixtures(self):
        ctx = Context(today=TODAY, limit=5)
        (sig,) = self._collect(ctx, self._one_grant_site())
        sig.validate()
        self.assertEqual((sig.entity.name, sig.entity.domain), ("Neros Technologies", "neros.tech"))
        self.assertEqual((sig.kind, sig.occurred_at), ("fcc_sta", "2026-09-09"))
        self.assertEqual(sig.metrics["els_experiment_type"], "Fixed Radar")
        self.assertEqual(ctx.warnings, [])

    def test_form_and_status_page_must_name_the_same_applicant(self):
        # The same filer written two ways is one filer; a different name on
        # the form means the two pages are not about the same application.
        site = self._one_grant_site()

        def renamed(name):
            return lambda url, **kw: site(url, **kw).replace(">Neros Technologies<", f">{name}<") if "_Print.cfm" in url else site(url, **kw)

        for name in ("Neros Technologies, Inc.", "NEROS TECHNOLOGIES INC", "Neros"):
            ctx = Context(today=TODAY, limit=5)
            self.assertEqual(len(self._collect(ctx, renamed(name))), 1, name)
            self.assertEqual(ctx.warnings, [])
        ctx = Context(today=TODAY, limit=5)
        self.assertEqual(self._collect(ctx, renamed("Echodyne Corp.")), [])
        self.assertTrue(any("form names 'Echodyne Corp.'" in w for w in ctx.warnings), ctx.warnings)

    def test_window_and_future_dates(self):
        # Granted before the window opens: nothing, and no warning.
        ctx = Context(today=date(2027, 6, 1), limit=5)
        self.assertEqual(self._collect(ctx, self._one_grant_site()), [])
        # Dated more than a day after "today": a typing error on the site, skipped loudly.
        ctx = Context(today=date(2026, 9, 1), limit=5)
        self.assertEqual(self._collect(ctx, self._one_grant_site()), [])
        self.assertTrue(any("in the future" in w for w in ctx.warnings), ctx.warnings)
        # One day ahead is the site's Eastern clock, not an error.
        ctx = Context(today=date(2026, 9, 8), limit=5)
        self.assertEqual(len(self._collect(ctx, self._one_grant_site())), 1)

    def test_empty_feed_is_reported(self):
        ctx = Context(today=TODAY, limit=5)
        self.assertEqual(self._collect(ctx, lambda url, **kw: "<rss></rss>" if url == els.RSS_URL else MISSING_PAGE), [])
        self.assertTrue(any("no grants parsed" in w for w in ctx.warnings), ctx.warnings)

    def test_without_curl_it_warns_and_stops(self):
        ctx = Context(today=TODAY, limit=5)
        with mock.patch.object(els.shutil, "which", lambda _: None), \
                mock.patch.object(els, "_fetch", side_effect=AssertionError("network")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(list(els.collect(ctx)), [])
        self.assertEqual(len(ctx.warnings), 1)


class FetchTest(unittest.TestCase):
    """The curl transport against a throwaway cache file: no network, no shared cache."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.pages: list[tuple[int, str]] = []
        self.calls = 0

        def fake_curl(url, timeout):
            self.calls += 1
            return self.pages.pop(0)

        for target, attr, value in [(http, "CACHE_PATH", Path(self.dir.name) / "cache.db"), (http, "_pace", lambda host: None),
                                    (els, "_curl", fake_curl), (els.time, "sleep", lambda s: None)]:
            patcher = mock.patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.url = els.INFO_URL.format("1649-EX-ST-2026")

    def test_block_page_with_status_200_is_not_parsed_or_cached(self):
        # The CDN's refusal and the server's outage page can both arrive as
        # 200. Read as "no such file number" they would silently empty the
        # run and sit in the cache; they must raise instead.
        self.pages = [(200, "<HTML><TITLE>Access Denied</TITLE></HTML>")] * 3
        with self.assertRaises(http.HttpError):
            els._fetch(self.url, ttl=els._info_ttl, expect=els._INFO_MARKS)
        self.assertEqual(self.calls, 3)  # tried again, then gave up
        self.pages = [(200, INFO_PAGE)]
        self.assertEqual(els._fetch(self.url, ttl=els._info_ttl, expect=els._INFO_MARKS), INFO_PAGE)
        self.assertEqual(self.calls, 4)  # the block page was not served from the cache

    def test_good_page_is_cached_and_the_real_missing_page_counts_as_good(self):
        self.pages = [(200, INFO_PAGE)]
        self.assertEqual(els._fetch(self.url, ttl=3600, expect=els._INFO_MARKS), INFO_PAGE)
        self.assertEqual(els._fetch(self.url, ttl=3600, expect=els._INFO_MARKS), INFO_PAGE)
        self.assertEqual(self.calls, 1)
        # What the site answers for a number that does not exist yet.
        missing = "<html><title>FCC OET Error Page</title><b>OET Validation Error Page</b> is not a valid confirmation number.</html>"
        self.pages = [(200, missing)]
        other = els.INFO_URL.format("9990-EX-CN-2026")
        self.assertIsNone(els.parse_application_info(els._fetch(other, ttl=els._info_ttl, expect=els._INFO_MARKS)))
        self.assertIsNone(els.parse_application_info(els._fetch(other, ttl=els._info_ttl, expect=els._INFO_MARKS)))
        self.assertEqual(self.calls, 2)  # kept for MISSING_TTL

    def test_server_error_raises(self):
        self.pages = [(503, "busy")] * 3
        before = http.stats()
        with self.assertRaises(http.HttpError):
            els._fetch(self.url, ttl=3600)
        self.assertEqual(self.calls, 3)
        self.assertEqual(http.stats()["errors"], before["errors"] + 1)

    def test_not_found_is_counted_as_antenna_http_counts_it(self):
        self.pages = [(404, "gone")] * 3
        before = http.stats()
        with self.assertRaises(http.HttpError):
            els._fetch(self.url, ttl=3600)
        self.assertEqual(self.calls, 1)  # no retry
        after = http.stats()
        self.assertEqual(after["not_found"], before["not_found"] + 1)
        self.assertEqual(after["errors"], before["errors"])

    def test_pacing_is_the_shared_one_and_happens_once_per_request(self):
        # antenna.http carries the interval for this host; the collector's
        # own figure must not be a second, different one.
        self.assertEqual(http.HOST_INTERVAL[els.HOST], els.MIN_INTERVAL)
        paced = []
        with mock.patch.object(http, "_pace", paced.append):
            self.pages = [(200, INFO_PAGE)]
            els._fetch(self.url, ttl=3600, expect=els._INFO_MARKS)
            els._fetch(self.url, ttl=3600, expect=els._INFO_MARKS)  # from the cache: no wait at all
        self.assertEqual(paced, [els.HOST])


if __name__ == "__main__":
    unittest.main()
