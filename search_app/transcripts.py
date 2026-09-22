"""Hand-written test transcripts for seeding Atlas.

Each snippet is a bounded chunk (subset pattern): episode text is unbounded,
so we store one document per passage instead of embedding a growing array.
"""

from datetime import datetime, timezone

from .schema import passage_from_legacy

UTC = timezone.utc


def _dt(year, month, day):
    return datetime(year, month, day, tzinfo=UTC)


_RAW = [
    # Security Now — TLS / certificates
    {
        "podcast_id": "security-now",
        "podcast_title": "Security Now",
        "podcast_author": "Steve Gibson",
        "episode_id": "sn-1042",
        "episode_title": "Certificate Transparency After the Next CA Failure",
        "episode_url": "https://twit.tv/shows/security-now",
        "published_at": _dt(2025, 3, 11),
        "chunk_index": 0,
        "text": (
            "Steve: Let's start with certificate transparency logs. Every publicly trusted "
            "TLS certificate is supposed to be recorded in at least two independent logs "
            "before a browser will treat it as valid. That is the only reason we found "
            "misissued certificates from smaller certificate authorities as quickly as we did. "
            "If a CA goes rogue or gets compromised, CT is the smoke detector."
        ),
    },
    {
        "podcast_id": "security-now",
        "podcast_title": "Security Now",
        "podcast_author": "Steve Gibson",
        "episode_id": "sn-1042",
        "episode_title": "Certificate Transparency After the Next CA Failure",
        "episode_url": "https://twit.tv/shows/security-now",
        "published_at": _dt(2025, 3, 11),
        "chunk_index": 1,
        "text": (
            "The practical advice for operators is boring and correct: enable OCSP stapling, "
            "turn on CAA records so only your chosen CA can mint certificates for the domain, "
            "and monitor CT logs for unexpected hostnames. HTTPS is not a checkbox. It is a "
            "supply chain that includes the CA, the log operators, and the browsers that "
            "enforce the policy."
        ),
    },
    {
        "podcast_id": "security-now",
        "podcast_title": "Security Now",
        "podcast_author": "Steve Gibson",
        "episode_id": "sn-1042",
        "episode_title": "Certificate Transparency After the Next CA Failure",
        "episode_url": "https://twit.tv/shows/security-now",
        "published_at": _dt(2025, 3, 11),
        "chunk_index": 2,
        "text": (
            "Leo: So if my hosting panel auto-renews Let's Encrypt, am I covered? Steve: You "
            "are covered for encryption in transit. You are not covered if someone issues a "
            "look-alike certificate for a sibling domain you forgot to put in CAA. Watch the "
            "logs. That is the whole episode in one sentence."
        ),
    },
    # Security Now — ransomware
    {
        "podcast_id": "security-now",
        "podcast_title": "Security Now",
        "podcast_author": "Steve Gibson",
        "episode_id": "sn-1043",
        "episode_title": "Double Extortion Hits Regional Hospitals",
        "episode_url": "https://twit.tv/shows/security-now",
        "published_at": _dt(2025, 3, 18),
        "chunk_index": 0,
        "text": (
            "Ransomware crews no longer just encrypt file servers. Double extortion means they "
            "steal electronic health records first, then demand payment to decrypt and a second "
            "payment not to leak patient data. Regional hospitals are targets because they have "
            "weak segmentation between imaging workstations and the domain controller."
        ),
    },
    {
        "podcast_id": "security-now",
        "podcast_title": "Security Now",
        "podcast_author": "Steve Gibson",
        "episode_id": "sn-1043",
        "episode_title": "Double Extortion Hits Regional Hospitals",
        "episode_url": "https://twit.tv/shows/security-now",
        "published_at": _dt(2025, 3, 18),
        "chunk_index": 1,
        "text": (
            "Offline backups still matter, but they do not stop the leak. Immutable object "
            "storage, 3-2-1 copies, and a restoration drill you actually ran last quarter are "
            "the difference between a bad week and a closed emergency room. Privileged access "
            "workstations and phishing-resistant MFA on VPN are the cheapest controls that "
            "break the initial access chain."
        ),
    },
    {
        "podcast_id": "security-now",
        "podcast_title": "Security Now",
        "podcast_author": "Steve Gibson",
        "episode_id": "sn-1043",
        "episode_title": "Double Extortion Hits Regional Hospitals",
        "episode_url": "https://twit.tv/shows/security-now",
        "published_at": _dt(2025, 3, 18),
        "chunk_index": 2,
        "text": (
            "Paying the ransom funds the next campaign and does not guarantee deletion. Several "
            "hospital systems paid and still saw records on leak sites two weeks later. The FBI "
            "guidance is consistent: isolate, restore from known-good backups, and assume the "
            "stolen data is already gone. Cyber insurance will ask whether you had MFA on email."
        ),
    },
    # Security Now — passkeys
    {
        "podcast_id": "security-now",
        "podcast_title": "Security Now",
        "podcast_author": "Steve Gibson",
        "episode_id": "sn-1044",
        "episode_title": "Passkeys, Password Managers, and the Sync Problem",
        "episode_url": "https://twit.tv/shows/security-now",
        "published_at": _dt(2025, 3, 25),
        "chunk_index": 0,
        "text": (
            "Passkeys are WebAuthn credentials bound to a site origin. They defeat phishing "
            "because the browser will not assert a key for a look-alike domain. Password "
            "managers still matter for the long tail of sites that have not shipped passkeys, "
            "and for shared family logins that do not fit a single hardware key."
        ),
    },
    {
        "podcast_id": "security-now",
        "podcast_title": "Security Now",
        "podcast_author": "Steve Gibson",
        "episode_id": "sn-1044",
        "episode_title": "Passkeys, Password Managers, and the Sync Problem",
        "episode_url": "https://twit.tv/shows/security-now",
        "published_at": _dt(2025, 3, 25),
        "chunk_index": 1,
        "text": (
            "The hard part is recovery. If your passkeys live only on one phone and that phone "
            "dies, you are locked out unless the provider synced an encrypted copy. iCloud Keychain "
            "and Google Password Manager do that for consumers. Enterprises should prefer "
            "attestation-aware hardware keys for admins and keep a break-glass process that is "
            "not another SMS code."
        ),
    },
    {
        "podcast_id": "security-now",
        "podcast_title": "Security Now",
        "podcast_author": "Steve Gibson",
        "episode_id": "sn-1044",
        "episode_title": "Passkeys, Password Managers, and the Sync Problem",
        "episode_url": "https://twit.tv/shows/security-now",
        "published_at": _dt(2025, 3, 25),
        "chunk_index": 2,
        "text": (
            "Steve: A password manager with a strong master passphrase and phishing-resistant "
            "unlock is still a huge upgrade from reuse. Do not disable it the week you turn on "
            "passkeys. Run both until the sites you actually use every day have migrated."
        ),
    },
    # Science Friday — CRISPR
    {
        "podcast_id": "science-friday",
        "podcast_title": "Science Friday",
        "podcast_author": "Ira Flatow",
        "episode_id": "scifri-crispr-base",
        "episode_title": "Base Editors Rewrite a Letter at a Time",
        "episode_url": "https://www.sciencefriday.com/",
        "published_at": _dt(2025, 2, 7),
        "chunk_index": 0,
        "text": (
            "CRISPR-Cas9 cuts both strands of DNA. Base editors fuse a disabled Cas enzyme to "
            "a deaminase so they can convert a single letter, C to T or A to G, without a double "
            "strand break. That matters for blood disorders caused by one-nucleotide mistakes, "
            "where a cut-and-repair strategy would be messier than a chemical rewrite."
        ),
    },
    {
        "podcast_id": "science-friday",
        "podcast_title": "Science Friday",
        "podcast_author": "Ira Flatow",
        "episode_id": "scifri-crispr-base",
        "episode_title": "Base Editors Rewrite a Letter at a Time",
        "episode_url": "https://www.sciencefriday.com/",
        "published_at": _dt(2025, 2, 7),
        "chunk_index": 1,
        "text": (
            "Off-target edits are the safety question. Prime editing and base editing both need "
            "careful guide RNA design and lots of sequencing to prove they did not nick a similar "
            "site elsewhere in the genome. First clinical readouts in sickle cell disease look "
            "promising, but manufacturing the editor and delivering it to the right stem cells "
            "is still expensive."
        ),
    },
    {
        "podcast_id": "science-friday",
        "podcast_title": "Science Friday",
        "podcast_author": "Ira Flatow",
        "episode_id": "scifri-crispr-base",
        "episode_title": "Base Editors Rewrite a Letter at a Time",
        "episode_url": "https://www.sciencefriday.com/",
        "published_at": _dt(2025, 2, 7),
        "chunk_index": 2,
        "text": (
            "Ira: People hear gene editing and imagine designer babies. Our guest: somatic "
            "editing of bone marrow is a medical intervention in a consenting patient. Germline "
            "changes that would be inherited are a different ethical category and are not what "
            "these hospital trials are doing."
        ),
    },
    # Science Friday — climate / AMOC
    {
        "podcast_id": "science-friday",
        "podcast_title": "Science Friday",
        "podcast_author": "Ira Flatow",
        "episode_id": "scifri-amoc",
        "episode_title": "Is the Atlantic Overturning Slowing Down?",
        "episode_url": "https://www.sciencefriday.com/",
        "published_at": _dt(2025, 4, 4),
        "chunk_index": 0,
        "text": (
            "The Atlantic Meridional Overturning Circulation, the AMOC, moves warm surface water "
            "north and returns cold dense water at depth. A slowdown would rearrange rainfall in "
            "the Sahel, cool parts of northern Europe, and raise sea level along the US East Coast. "
            "Freshwater from Greenland melt can cap the convection that keeps the engine running."
        ),
    },
    {
        "podcast_id": "science-friday",
        "podcast_title": "Science Friday",
        "podcast_author": "Ira Flatow",
        "episode_id": "scifri-amoc",
        "episode_title": "Is the Atlantic Overturning Slowing Down?",
        "episode_url": "https://www.sciencefriday.com/",
        "published_at": _dt(2025, 4, 4),
        "chunk_index": 1,
        "text": (
            "Direct RAPID array measurements since 2004 show a noisy, possibly weakening trend, "
            "but paleoclimate proxies argue the current state is already unusual. Climate models "
            "disagree on timing of a collapse this century. The honest summary is elevated risk, "
            "not a scheduled catastrophe next Tuesday."
        ),
    },
    {
        "podcast_id": "science-friday",
        "podcast_title": "Science Friday",
        "podcast_author": "Ira Flatow",
        "episode_id": "scifri-amoc",
        "episode_title": "Is the Atlantic Overturning Slowing Down?",
        "episode_url": "https://www.sciencefriday.com/",
        "published_at": _dt(2025, 4, 4),
        "chunk_index": 2,
        "text": (
            "Cutting greenhouse gas emissions still reduces the odds of crossing a tipping point. "
            "Ocean observing systems are the other half of the story: you cannot manage a current "
            "you refuse to measure. That is why expanding the RAPID and SAMBA arrays keeps coming "
            "up in these interviews."
        ),
    },
    # Science Friday — JWST
    {
        "podcast_id": "science-friday",
        "podcast_title": "Science Friday",
        "podcast_author": "Ira Flatow",
        "episode_id": "scifri-jwst",
        "episode_title": "Steam and Carbon Dioxide in a Distant Atmosphere",
        "episode_url": "https://www.sciencefriday.com/",
        "published_at": _dt(2025, 5, 16),
        "chunk_index": 0,
        "text": (
            "The James Webb Space Telescope reads exoplanet atmospheres by watching a star dim "
            "during transit and splitting that starlight into spectra. Absorption features of "
            "water vapor, carbon dioxide, and sulfur dioxide showed up in a hot gas giant about "
            "the size of Saturn. That is chemistry, not a postcard of the surface."
        ),
    },
    {
        "podcast_id": "science-friday",
        "podcast_title": "Science Friday",
        "podcast_author": "Ira Flatow",
        "episode_id": "scifri-jwst",
        "episode_title": "Steam and Carbon Dioxide in a Distant Atmosphere",
        "episode_url": "https://www.sciencefriday.com/",
        "published_at": _dt(2025, 5, 16),
        "chunk_index": 1,
        "text": (
            "For smaller rocky worlds the signal is fainter and clouds can mute the spectrum. "
            "JWST is already ruling out hydrogen-rich envelopes on some TRAPPIST-1 planets. "
            "Biosignatures would need a mix of gases out of equilibrium, observed more than once, "
            "before anyone should pop champagne."
        ),
    },
    {
        "podcast_id": "science-friday",
        "podcast_title": "Science Friday",
        "podcast_author": "Ira Flatow",
        "episode_id": "scifri-jwst",
        "episode_title": "Steam and Carbon Dioxide in a Distant Atmosphere",
        "episode_url": "https://www.sciencefriday.com/",
        "published_at": _dt(2025, 5, 16),
        "chunk_index": 2,
        "text": (
            "The instrument that makes this possible is the NIRSpec spectrograph, sitting behind "
            "a sunshield the size of a tennis court at L2. Infrared is where those molecular "
            "fingerprints live. Hubble could hint; Webb can inventory."
        ),
    },
    # Planet Money — inflation
    {
        "podcast_id": "planet-money",
        "podcast_title": "Planet Money",
        "podcast_author": "NPR",
        "episode_id": "pm-sticky-prices",
        "episode_title": "Why Inflation Got Sticky",
        "episode_url": "https://www.npr.org/sections/money/",
        "published_at": _dt(2025, 1, 22),
        "chunk_index": 0,
        "text": (
            "Headline inflation cooled after the Federal Reserve raised interest rates, but "
            "shelter and insurance kept the CPI from falling as fast as grocery prices. Economists "
            "call that stickiness: firms reset prices infrequently, rents lag new leases, and "
            "wages ratchet more easily up than down."
        ),
    },
    {
        "podcast_id": "planet-money",
        "podcast_title": "Planet Money",
        "podcast_author": "NPR",
        "episode_id": "pm-sticky-prices",
        "episode_title": "Why Inflation Got Sticky",
        "episode_url": "https://www.npr.org/sections/money/",
        "published_at": _dt(2025, 1, 22),
        "chunk_index": 1,
        "text": (
            "The Phillips curve is not a law of physics. Tight labor markets can coexist with "
            "falling goods inflation if supply chains heal. The Fed watches core services excluding "
            "housing because that is the part most sensitive to demand they actually control with "
            "the federal funds rate."
        ),
    },
    {
        "podcast_id": "planet-money",
        "podcast_title": "Planet Money",
        "podcast_author": "NPR",
        "episode_id": "pm-sticky-prices",
        "episode_title": "Why Inflation Got Sticky",
        "episode_url": "https://www.npr.org/sections/money/",
        "published_at": _dt(2025, 1, 22),
        "chunk_index": 2,
        "text": (
            "If you only remember one mechanism: higher rates make mortgages and car loans "
            "expensive, people postpone buying houses, and slower demand pulls price growth down "
            "with a lag of many months. That lag is why central bankers talk about looking through "
            "temporary spikes."
        ),
    },
    # Planet Money — AI jobs
    {
        "podcast_id": "planet-money",
        "podcast_title": "Planet Money",
        "podcast_author": "NPR",
        "episode_id": "pm-ai-call-centers",
        "episode_title": "The Chatbot Taking the 1-800 Queue",
        "episode_url": "https://www.npr.org/sections/money/",
        "published_at": _dt(2025, 6, 3),
        "chunk_index": 0,
        "text": (
            "Call centers were already a high-turnover industry before large language models. "
            "The new pitch is that a chatbot handles password resets and shipping status so "
            "human agents only take the angry, ambiguous cases. Early deployments cut average "
            "handle time, then quietly hired the agents back when the bot hallucinated refunds."
        ),
    },
    {
        "podcast_id": "planet-money",
        "podcast_title": "Planet Money",
        "podcast_author": "NPR",
        "episode_id": "pm-ai-call-centers",
        "episode_title": "The Chatbot Taking the 1-800 Queue",
        "episode_url": "https://www.npr.org/sections/money/",
        "published_at": _dt(2025, 6, 3),
        "chunk_index": 1,
        "text": (
            "Labor economists talk about task displacement versus job displacement. AI is good "
            "at scripted tasks. Empathy, policy exceptions, and regulated disclosures still "
            "need a person on the hook. Wages may polarize: fewer entry-level seats, more "
            "escalation specialists."
        ),
    },
    {
        "podcast_id": "planet-money",
        "podcast_title": "Planet Money",
        "podcast_author": "NPR",
        "episode_id": "pm-ai-call-centers",
        "episode_title": "The Chatbot Taking the 1-800 Queue",
        "episode_url": "https://www.npr.org/sections/money/",
        "published_at": _dt(2025, 6, 3),
        "chunk_index": 2,
        "text": (
            "Offshoring did this once already. The difference is the bot sits in the same "
            "cloud region as the customer relationship database, so latency is not the constraint. "
            "Quality and liability are. Companies that measure containment rate without measuring "
            "repeat contacts are fooling themselves."
        ),
    },
    # Planet Money — housing
    {
        "podcast_id": "planet-money",
        "podcast_title": "Planet Money",
        "podcast_author": "NPR",
        "episode_id": "pm-housing-supply",
        "episode_title": "Zoning, Not Just Mortgage Rates",
        "episode_url": "https://www.npr.org/sections/money/",
        "published_at": _dt(2025, 7, 11),
        "chunk_index": 0,
        "text": (
            "Housing shortages in coastal cities are mostly a supply story. Single-family zoning, "
            "parking minimums, and slow permitting keep builders from adding duplexes and mid-rise "
            "apartments where jobs are. Cheap mortgages cannot invent lots that do not exist."
        ),
    },
    {
        "podcast_id": "planet-money",
        "podcast_title": "Planet Money",
        "podcast_author": "NPR",
        "episode_id": "pm-housing-supply",
        "episode_title": "Zoning, Not Just Mortgage Rates",
        "episode_url": "https://www.npr.org/sections/money/",
        "published_at": _dt(2025, 7, 11),
        "chunk_index": 1,
        "text": (
            "When the Fed lifted rates, existing owners with three-percent mortgages stopped "
            "selling. That lock-in effect froze listings. New construction is the release valve, "
            "but lumber, labor, and local hearings still set the pace. YIMBY reforms that allow "
            "accessory dwelling units are small, measurable, and politically easier than towers."
        ),
    },
    {
        "podcast_id": "planet-money",
        "podcast_title": "Planet Money",
        "podcast_author": "NPR",
        "episode_id": "pm-housing-supply",
        "episode_title": "Zoning, Not Just Mortgage Rates",
        "episode_url": "https://www.npr.org/sections/money/",
        "published_at": _dt(2025, 7, 11),
        "chunk_index": 2,
        "text": (
            "Rent control can protect current tenants and discourage new supply at the same time. "
            "Economists on this show keep returning to the same graph: cities that built more "
            "homes per worker saw slower rent growth. The rest is commentary."
        ),
    },
]

SNIPPETS = [passage_from_legacy(doc) for doc in _RAW]
