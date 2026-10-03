"""The text the exporter cleans before it reaches the site."""

from __future__ import annotations

import unittest
from datetime import date

from antenna.export import _house_style, _one_liner, _tidy_note


class OneLiner(unittest.TestCase):
    def test_the_company_name_in_front_is_dropped(self):
        self.assertEqual(_one_liner("VoltX builds low-cost autonomous defense systems.", "VoltX"),
                         "Builds low-cost autonomous defense systems")
        self.assertEqual(_one_liner("K2 Space is building the largest satellites in orbit.", "K2 Space"),
                         "Building the largest satellites in orbit")
        self.assertEqual(_one_liner("AREO: Physical AI for retail. Robots that learn. We're hiring.", "AREO"),
                         "Physical AI for retail")

    def test_a_shorter_or_longer_form_of_the_name_counts(self):
        self.assertEqual(_one_liner("Neros is vertically integrating production of unmanned systems.",
                                    "Neros Technologies"),
                         "Vertically integrating production of unmanned systems")
        self.assertEqual(_one_liner("Terminal Missile Defense (TMD), LLC is an early stage defense aeronautics "
                                    "company based in Las Vegas.", "Terminal Missile Defense"),
                         "An early stage defense aeronautics company based in Las Vegas")
        self.assertEqual(_one_liner("Advanced Float Co. Ltd. — owner and operator of offshore floating nuclear "
                                    "power plants. A certified reactor.", "Advanced Float"),
                         "Owner and operator of offshore floating nuclear power plants")
        self.assertEqual(_one_liner("Recon-RF designs custom MMICs and RF modules", "Recon RF"),
                         "Designs custom MMICs and RF modules")

    def test_another_word_that_starts_the_same_is_left_alone(self):
        self.assertEqual(_one_liner("Lightmatter is the photonic computing company", "Light Materials"),
                         "Lightmatter is the photonic computing company")

    def test_only_the_first_sentence_is_kept(self):
        self.assertEqual(_one_liner("Uses drones to seed clouds. Learn how our scalable platform works.", "Rainmaker"),
                         "Uses drones to seed clouds")
        # An abbreviation or an initialism does not end a sentence.
        self.assertEqual(_one_liner("Secure hardware for the U.S. defense industrial base. More here.", "X"),
                         "Secure hardware for the U.S. defense industrial base")
        self.assertEqual(_one_liner("Plants from 100 MW to 1 GW. Turnkey.", "X"), "Plants from 100 MW to 1 GW")

    def test_a_description_cut_off_at_the_source_does_not_end_on_half_a_clause(self):
        cut = ("Bringing high-performance radar sensing and advanced navigation to autonomous platforms in the "
               "stratosphere and other domains of flight across many regions, leveraging innovative processing "
               "techniques to…")
        self.assertEqual(_one_liner(cut, "X"),
                         "Bringing high-performance radar sensing and advanced navigation to autonomous platforms "
                         "in the stratosphere and other domains of flight across many regions")
        # With no short fragment to drop, the ellipsis stays.
        going = ("Supports interference situational awareness by continuously correlating signal quality, jamming "
                 "and spoofing indicators that are gathered every hour across the whole of a satellite operator's "
                 "fleet into a single…")
        self.assertTrue(_one_liner(going, "X").endswith("fleet into a single…"))
        self.assertTrue(_one_liner("Tenna " + going[0].lower() + going[1:], "Tenna").endswith("fleet into a single…"))

    def test_the_company_speaking_is_put_in_the_third_person(self):
        self.assertEqual(_one_liner("We're building task-specific robots for small manufacturers.", "Whistle"),
                         "Building task-specific robots for small manufacturers")
        self.assertEqual(_one_liner("We’re a YC-backed startup building robots that build solar farms", "Charge"),
                         "A YC-backed startup building robots that build solar farms")
        self.assertEqual(_one_liner("We make robots that autonomously construct buildings", "Monumental"),
                         "Makes robots that autonomously construct buildings")
        self.assertEqual(_one_liner("We're building teleoperated robots, because we believe humanoids cannot wait",
                                    "Telekin"), "Building teleoperated robots")
        # "our" in the middle of a description is still a description.
        self.assertEqual(_one_liner("Builds reusable rockets for science beyond our planet", "X"),
                         "Builds reusable rockets for science beyond our planet")

    def test_a_slogan_is_not_shown(self):
        for text in ("Your physical AI partner", "Meet Roger, the humanoid robot built for demanding work",
                     "Discover our counter-drone detection platform", "We are dedicated to serving our customers",
                     "We can read a 2D map we’ve never seen", "Build your own robot duck",
                     "Our team is an elite group of engineers"):
            self.assertIsNone(_one_liner(text, "X"), text)

    def test_what_is_not_a_description_is_not_shown(self):
        self.assertIsNone(_one_liner(None, "X"))
        self.assertIsNone(_one_liner("Trademark goods: unmanned aerial vehicles", "X"))
        self.assertIsNone(_one_liner("Defense, Security, Autonomy — Learn why Echodyne is a trusted leader.", "Echodyne"))
        self.assertIsNone(_one_liner("JetZero announces it has raised approximately $175 million.", "JetZero"))
        self.assertIsNone(_one_liner("Radiant", "Radiant"))


class TidyNote(unittest.TestCase):
    def test_a_whole_note_only_gains_its_full_stop(self):
        self.assertEqual(_tidy_note("Founded 2019 by Bret Kugelmass; $164M raised in total"),
                         "Founded 2019 by Bret Kugelmass; $164M raised in total.")
        self.assertEqual(_tidy_note("LinkedIn lists 2-10 employees."), "LinkedIn lists 2-10 employees.")
        self.assertIsNone(_tidy_note(None))

    def test_the_gaps_a_removed_link_leaves_are_closed(self):
        self.assertEqual(_tidy_note("A $200M Series B at a $2.2B valuation (Jun 2026) ( )."),
                         "A $200M Series B at a $2.2B valuation (Jun 2026).")
        self.assertEqual(_tidy_note("Total funding of $15M, per"), "Total funding of $15M.")
        self.assertEqual(_tidy_note("'brings total capital raised... to $598 million' per; founded January 2021 per"),
                         "'brings total capital raised... to $598 million'; founded January 2021.")
        self.assertEqual(_tidy_note("Led by Brian Matthews (ADAMS ML26266A278, ); money is strategic"),
                         "Led by Brian Matthews (ADAMS ML26266A278); money is strategic.")

    def test_a_cut_off_note_ends_where_it_last_ends_cleanly(self):
        long = ("Founded 2024 in San Leandro by people from Joby and Xwing, with $26M raised through a Series A. "
                "Already covered by Aviation Week, so not an unknown; the record shows no founding year or…")
        self.assertEqual(_tidy_note(long),
                         "Founded 2024 in San Leandro by people from Joby and Xwing, with $26M raised through a "
                         "Series A. Already covered by Aviation Week, so not an unknown.")
        bracket = ("Caplight lists founded 2022, about 22 employees and $4.33M total funding including grants "
                   "(PitchBook's search snippet says $2.4M; investors named include Capital Factory…")
        self.assertEqual(_tidy_note(bracket),
                         "Caplight lists founded 2022, about 22 employees and $4.33M total funding including grants.")

    def test_a_bracket_that_opens_mid_clause_is_not_a_place_to_stop(self):
        text = ("One of the best-funded US defense startups, with over $1B raised and coverage in Reuters and "
                "Aviation Week. The VESPID intent-to-use trademark (serial 99123456, filed for radar…")
        self.assertEqual(_tidy_note(text),
                         "One of the best-funded US defense startups, with over $1B raised and coverage in Reuters "
                         "and Aviation Week.")

    def test_an_abbreviation_is_not_the_end_of_a_sentence(self):
        text = ("The filer is Abraxas Systems, Inc. of San Francisco, a Delaware corporation with a mailbox address "
                "on Market Street. A one-page site names Abraxas Systems Inc. - Infrastructure for Human-Machine…")
        self.assertEqual(_tidy_note(text),
                         "The filer is Abraxas Systems, Inc. of San Francisco, a Delaware corporation with a mailbox "
                         "address on Market Street.")

    def test_a_note_with_nowhere_clean_to_end_keeps_its_ellipsis(self):
        self.assertEqual(_tidy_note("A seed led by Crane with YC and Expa…"), "A seed led by Crane with YC and Expa…")


class HouseStyle(unittest.TestCase):
    TODAY = date(2026, 10, 3)

    def test_iso_dates_read_like_the_rest_of_the_site(self):
        self.assertEqual(_house_style("Domain x.com was registered on 2026-09-19", self.TODAY),
                         "Domain x.com was registered on Sep 19")
        self.assertEqual(_house_style("20 of 21 roles first published from 2026-09-04 to 2026-09-05", self.TODAY),
                         "20 of 21 roles first published from Sep 4 to Sep 5")

    def test_day_first_dates_too(self):
        self.assertEqual(_house_style("NRC scheduled a meeting for 7 Oct 2026 on docket 99902154", self.TODAY),
                         "NRC scheduled a meeting for Oct 7 on docket 99902154")
        self.assertEqual(_house_style("incorporated 21 November 2016; seed in June 2026", self.TODAY),
                         "incorporated Nov 21, 2016; seed in June 2026")

    def test_another_year_keeps_its_year(self):
        self.assertEqual(_house_style("LLC deal dated 2024-10-04", self.TODAY), "LLC deal dated Oct 4, 2024")

    def test_spelling_is_american(self):
        self.assertEqual(_house_style("Applied for a new FCC experimental licence", self.TODAY),
                         "Applied for a new FCC experimental license")
        self.assertEqual(_house_style("Licences granted; a licensed band", self.TODAY),
                         "Licenses granted; a licensed band")

    def test_numbers_that_are_not_dates_are_left_alone(self):
        for text in ("FAA waiver 107W-2026-00907", "serial 2026-13-40", "docket 99902154"):
            self.assertEqual(_house_style(text, self.TODAY), text)


if __name__ == "__main__":
    unittest.main()
