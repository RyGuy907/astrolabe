"""The objects a beginner actually points a telescope at.

The catalogue holds 12,371 objects. Perhaps a hundred and thirty of them are
what anyone means by "things worth looking at" -- the Ring Nebula, M13, the
Double Cluster -- and the rest is a reference work. Scrolling past four
thousand anonymous NGC galaxies to reach the Dumbbell is not a filter problem
the observability scoring can solve, because most of those galaxies *are*
above the horizon and *are* technically within reach of a large scope. They
are simply not what you drove out there for.

So this is a curated list, and there is no way to pretend otherwise. What can
be done is to say exactly how it was curated and let the reasoning be argued
with.

The rule
--------
An object is included when it is both **well known** and **worth the time**
visually. Two halves, and both matter: M87 is famous and visually a grey
smudge; NGC 3115 is a fine sight and almost nobody has heard of it. The list
is the intersection, not the union.

Where the entries come from
---------------------------
* **The Messier catalogue**, minus the exclusions below. It is the canonical
  beginner list -- Messier was cataloguing things that were *not* comets, so
  by construction every entry is a conspicuous object in a small refractor --
  and it is what "Turn Left at Orion", the Astronomical League's Messier
  Program, and every introductory book are organised around.
* **Famous non-Messier objects**, which are mostly the southern sky Messier
  never saw (Omega Centauri, 47 Tucanae, the Magellanic Clouds, Eta Carinae)
  plus the northern objects that acquired names anyway: the Double Cluster,
  the Veil, the Helix, the Coathanger.
* **Famous double stars** -- Albireo, Mizar, Almach and the rest. These come
  from `engine/catalog/data/double_stars.csv` rather than OpenNGC, which is a
  deep-sky catalogue and does not have them. They earn their place twice
  over: they are among the first things anyone is shown through a telescope,
  and they are nearly immune to moonlight, so they are the right answer on
  the nights when nothing else is.

Why some Messier objects are left out
-------------------------------------
Being in the catalogue is not the same as being worth finding:

* **Not deep-sky objects.** M40 is a double star Messier logged by mistake;
  M73 is an asterism of four unrelated stars.
* **Disputed identity.** M102 was probably a duplicate observation of M101;
  what it refers to has been argued about since 1781.
* **Virgo and Coma ellipticals.** M49, M58, M59, M60, M61, M84, M85, M86,
  M88, M89, M90, M91, M98, M99, M100 and M105 are a dozen featureless ovals
  in one crowded patch of sky. They are a rewarding evening for someone
  working a list and a thoroughly confusing one for anybody else.
* **Genuinely hard.** M74 is the textbook low-surface-brightness spiral and a
  disappointment from anywhere but a dark site; M76 is the faintest planetary
  in the catalogue.

M87 stays despite being a smudge: it is the galaxy with the photographed
black hole, and somebody who has heard of it and cannot find it in the list
will conclude the filter is broken rather than that the object is dull.

What is deliberately absent
---------------------------
The famous *photographic* targets. The Horsehead needs an H-beta filter and a
genuinely dark sky; the Flaming Star and the Elephant's Trunk are camera
objects. They are household names because of pictures, and putting them in a
list headed "popular targets" would send a beginner hunting for something
they will not see. They remain in the full catalogue, where the scoring can
say what it thinks of them.

Identifiers, not names
----------------------
Entries are OpenNGC primary identifiers, which is what `CatalogObject.name`
holds. Matching on common names would be fragile -- M17 alone carries four of
them -- and matching on Messier number alone would miss everything here that
Messier never catalogued. `tests/test_showpieces.py` checks that every id
below still resolves, so a catalogue update cannot silently empty the list.
"""

from __future__ import annotations

#: Messier numbers omitted from the list, with the reason in the docstring
#: above. Kept as data rather than prose so the tests can assert the
#: arithmetic instead of trusting a comment.
MESSIER_EXCLUDED: frozenset[int] = frozenset({
    40, 73,                                    # not deep-sky objects
    102,                                       # disputed identity
    49, 58, 59, 60, 61, 84, 85, 86, 88, 89,    # Virgo / Coma ellipticals
    90, 91, 98, 99, 100, 105,
    74, 76,                                    # genuinely hard
})

#: Non-Messier showpieces, by OpenNGC primary identifier.
NON_MESSIER_SHOWPIECES: frozenset[str] = frozenset({
    # --- clusters -------------------------------------------------------
    "C014",        # Double Cluster (h & chi Persei)
    "NGC0869",     # h Persei, the western half, also listed on its own
    "NGC0884",     # chi Persei, the eastern half
    "Cl399",       # Coathanger / Brocchi's Cluster
    "Mel111",      # Coma Star Cluster
    "NGC0457",     # Owl (or ET) Cluster
    "NGC0752",     # large, loose, bright -- a binocular object in Andromeda
    "NGC0663",     # Cassiopeia open cluster, a standard sweep target
    "NGC7789",     # Caroline's Rose
    "NGC6231",     # the northern jewel box, in Scorpius
    "NGC2264",     # Christmas Tree Cluster
    "NGC0104",     # 47 Tucanae -- second only to Omega Centauri
    "NGC5139",     # Omega Centauri, the finest globular in the sky
    # --- nebulae --------------------------------------------------------
    "NGC7000",     # North America Nebula
    "NGC6960",     # Veil, western arc
    "NGC6992",     # Veil, eastern arc
    "NGC7293",     # Helix -- the nearest bright planetary
    "NGC2392",     # Eskimo / Clown Face
    "NGC2238",     # Rosette Nebula
    "NGC1499",     # California Nebula
    "NGC7635",     # Bubble Nebula
    "NGC6543",     # Cat's Eye
    "NGC6826",     # Blinking Planetary
    "NGC6888",     # Crescent Nebula
    "NGC7009",     # Saturn Nebula
    "NGC7662",     # Blue Snowball
    "NGC3242",     # Ghost of Jupiter
    "NGC6302",     # Bug / Butterfly Nebula
    "NGC7023",     # Iris Nebula
    "NGC1977",     # Running Man, beside the Orion Nebula
    "NGC2261",     # Hubble's Variable Nebula
    "NGC2359",     # Thor's Helmet
    "NGC2070",     # Tarantula Nebula, in the Large Magellanic Cloud
    "NGC3372",     # Eta Carinae Nebula
    "NGC2438",     # the planetary superimposed on M46
    # --- galaxies -------------------------------------------------------
    "NGC0253",     # Sculptor Galaxy
    "NGC5128",     # Centaurus A
    "NGC4565",     # Needle Galaxy
    "NGC4631",     # Whale Galaxy
    "NGC4656",     # Hockey Stick, beside the Whale
    "NGC0891",     # Silver Sliver -- a textbook edge-on
    "NGC2903",     # bright barred spiral in Leo, often called the best
                   # galaxy Messier missed
    "NGC3628",     # third member of the Leo Triplet, with M65 and M66
    "NGC7331",     # the Deer Lick group's bright member
    "NGC6946",     # Fireworks Galaxy
    "NGC4038",     # Antennae -- two galaxies mid-collision
    "NGC0292",     # Small Magellanic Cloud
    "ESO056-115",  # Large Magellanic Cloud
    # --- double stars ---------------------------------------------------
    # Famous enough that leaving them out would look like an omission, and
    # they are the one class of target a bright moon barely touches -- which
    # makes them the right answer on exactly the nights when nothing else is.
    "DBLAlbireo",           # Albireo
    "DBLMizar",             # Mizar
    "DBLAlmach",            # Almach
    "DBLCorCaroli",        # Cor Caroli
    "DBLTheDoubleDouble", # The Double Double
    "DBLCastor",            # Castor
    "DBLIzar",              # Izar
    "DBLGraffias",          # Graffias
    "DBLMesarthim",         # Mesarthim
    "DBLGammaDelphini",    # Gamma Delphini
    "DBLRasalgethi",        # Rasalgethi
    "DBL61Cygni",          # 61 Cygni
    "DBLTegmine",           # Tegmine
    "DBLIotaCancri",       # Iota Cancri
    "DBLAchird",            # Achird
    "DBLAcrux",             # Acrux
    "DBLAlphaCentauri",    # Alpha Centauri
    "DBLTrapezium",         # Trapezium
    "DBLBetaMonocerotis",  # Beta Monocerotis
    "DBLAlgieba",           # Algieba
})


def showpiece_ids(catalog) -> frozenset[str]:
    """Primary identifiers of every showpiece present in `catalog`.

    Resolved against the catalogue rather than hardcoded so that a Messier
    number maps to whatever identifier that release uses -- M45 is `Mel022`,
    not an NGC number -- and so that an object the catalogue does not carry
    drops out quietly instead of leaving an id that matches nothing.
    """
    present = {obj.name for obj in catalog}
    ids = {obj.name for obj in catalog
           if obj.messier is not None and obj.messier not in MESSIER_EXCLUDED}
    return frozenset(ids | (NON_MESSIER_SHOWPIECES & present))
