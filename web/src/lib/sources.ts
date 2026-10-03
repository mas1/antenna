import type { Family } from "./types";

export type SourceInfo = {
  slug: string;
  name: string;
  family: Family;
  /** What it watches. */
  watches: string;
  /** Why it counts. */
  early: string;
};

/** One entry per collector in pipeline/antenna/collectors. */
export const SOURCES: SourceInfo[] = [
  {
    slug: "sec_form_d",
    name: "SEC Form D",
    family: "capital",
    watches: "Every private placement notice filed with the SEC, parsed down to young operating companies.",
    early: "A round is on file about two weeks after first sale, usually before any announcement.",
  },
  {
    slug: "fpds_ot",
    name: "DoD Other Transactions",
    family: "capital",
    watches: "New prototype agreements from DIU, DARPA, SOCOM and the services, from the federal procurement feed.",
    early: "This is how the Pentagon buys from startups, and these awards do not appear in USAspending.",
  },
  {
    slug: "sbir_awards",
    name: "SBIR and STTR awards",
    family: "capital",
    watches: "Phase I awards across agencies, with a premium for a firm's first award ever.",
    early: "A first Phase I often precedes a priced round by a year or more.",
  },
  {
    slug: "energy_grants",
    name: "NSF and ARPA-E",
    family: "capital",
    watches: "NSF small-business Phase I awards and ARPA-E projects led by private companies.",
    early: "Non-dilutive money reaches energy and deep-tech teams before venture does.",
  },
  {
    slug: "fcc_els",
    name: "FCC experimental licenses",
    family: "regulatory",
    watches: "Applications and grants for experimental radio licenses, including those still pending.",
    early: "Anything that radiates (drones, radar, satellites) must file before it can be tested.",
  },
  {
    slug: "nrc_adams",
    name: "NRC pre-application dockets",
    family: "regulatory",
    watches: "New documents on reactor pre-application dockets in the NRC's public records.",
    early: "A new docket marks the first formal contact with the regulator, long before a license.",
  },
  {
    slug: "faa_uas",
    name: "FAA drone filings",
    family: "regulatory",
    watches: "Remote ID declarations of compliance and Part 107 waivers for advanced operations.",
    early: "A first declaration means a first aircraft model is heading to market.",
  },
  {
    slug: "uspto_trademarks",
    name: "USPTO trademarks",
    family: "regulatory",
    watches: "New trademark applications whose goods are drones, robots, radar, reactors and the like.",
    early: "Hardware companies file a mark when they name a product, often on intent to use.",
  },
  {
    slug: "github_velocity",
    name: "GitHub star velocity",
    family: "github",
    watches: "Daily star counts for repositories that match the thesis, discounted when the stars look fake.",
    early: "Star growth shows within days. A new organization with its own domain is often a new company.",
  },
  {
    slug: "research_affil",
    name: "Research affiliations",
    family: "research",
    watches: "Recent papers where an author's affiliation is an organization no registry knows.",
    early: "The first time a researcher lists the company as their affiliation.",
  },
  {
    slug: "yc_directory",
    name: "Y Combinator directory",
    family: "launch",
    watches: "The three most recent batches and their launch posts, filtered to the thesis.",
    early: "Often the first public listing of a team of two to five.",
  },
  {
    slug: "hn_launch",
    name: "Show HN and Launch HN",
    family: "launch",
    watches: "Launch posts on Hacker News with their points and comments.",
    early: "Covers launches from teams outside accelerators.",
  },
  {
    slug: "accelerators",
    name: "a16z speedrun, HAX and SOSV",
    family: "launch",
    watches: "Cohort lists and founder records from hard-tech accelerators.",
    early: "Listed before any press, sometimes with a note that the company is raising.",
  },
  {
    slug: "hn_hiring",
    name: "Who is hiring",
    family: "hiring",
    watches: "The monthly Hacker News hiring thread, filtered to the thesis.",
    early: "Teams of two to twenty post here before they have a careers page.",
  },
  {
    slug: "ats_jobs",
    name: "Job boards",
    family: "hiring",
    watches: "Open roles on Greenhouse, Lever and Ashby, with posted dates and function mix.",
    early: "A first Head of Manufacturing or first sales hire says where a company is going.",
  },
  {
    slug: "hn_attention",
    name: "Developer attention",
    family: "social",
    watches: "Weekly Hacker News mentions of each company, and its lifetime total.",
    early: "First mentions count for a company. A large lifetime total counts against it as already known.",
  },
  {
    slug: "web_presence",
    name: "Website verification",
    family: "traffic",
    watches: "Finds the website of a company known only from a filing, and checks it really is that company.",
    early: "It moves nothing by itself. It is what lets hiring, traffic and attention be measured for a name on a form.",
  },
  {
    slug: "tranco_rank",
    name: "Tranco rank",
    family: "traffic",
    watches: "Each domain's position in the 4.5 million domain Tranco list, now and in the past.",
    early: "Movement deep in the list is visible long before a site reaches any top-million ranking.",
  },
  {
    slug: "domain_footprint",
    name: "Domain footprint",
    family: "traffic",
    watches: "Registration dates, DNS records and certificate logs for each company's domain.",
    early: "New subdomains and a move to government cloud show up before the company says anything.",
  },
];
