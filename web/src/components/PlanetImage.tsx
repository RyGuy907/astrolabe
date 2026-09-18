/**
 * A photograph of one planet.
 *
 * Vendored rather than hotlinked: seven files, 208 KB the lot, and they never
 * change. The alternative is a runtime dependency on somebody else's CDN for
 * something entirely static, which is the kind of thing that breaks quietly
 * two years later.
 *
 * All seven are NASA spacecraft imagery and therefore public domain, but
 * attribution is still owed and is given in the caption, in
 * THIRD_PARTY_NOTICES.md and in `CREDITS` below.
 *
 * **Where a photograph and the eyepiece disagree.** These are close-up
 * spacecraft images and the view through a telescope is not: Jupiter's bands
 * are there but the Great Red Spot is a faint notch, Mars shows a disc and a
 * polar cap on a good night, and Uranus and Neptune are featureless dots. The
 * two images deliberately *not* used are the ones that would mislead most --
 * Magellan's radar map of Venus's surface, which nobody has ever seen through
 * a telescope, and the JWST portrait of Uranus blazing with rings. The picked
 * ones are Mariner 10's cloud-top Venus and Voyager 2's plain blue-green
 * Uranus, which are at least the same object you will be looking at.
 */

/** Credit per image, shown under it. Public domain, credit owed regardless. */
const CREDITS: Record<string, string> = {
  mercury: "NASA / Johns Hopkins APL / Carnegie Institution — MESSENGER",
  venus: "NASA / JPL-Caltech — Mariner 10",
  mars: "NASA / JPL-Caltech / MSSS — Mars Global Surveyor",
  jupiter: "NASA / JPL-Caltech / SwRI / MSSS — Juno",
  saturn: "NASA / JPL-Caltech — Voyager",
  uranus: "NASA / JPL-Caltech — Voyager 2",
  neptune: "NASA / JPL-Caltech — Voyager 2",
};

interface Props {
  /** Lowercase planet name, which is also the file name. */
  name: string;
}

export function PlanetImage({ name }: Props) {
  const credit = CREDITS[name];
  if (!credit) return null;

  return (
    <figure className="planet-figure">
      <img
        className="planet-photo"
        src={`/planets/${name}.jpg`}
        width={480}
        height={480}
        // Not lazy, for the same reason the survey cutouts are not: the row
        // renders this only once it has been expanded, so nothing is fetched
        // while browsing the list.
        decoding="async"
        alt={`Spacecraft photograph of ${name}.`}
      />
      <figcaption className="muted small">{credit}</figcaption>
    </figure>
  );
}
