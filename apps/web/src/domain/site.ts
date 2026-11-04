/**
 * The site register. One real site exists: this is a fact about the data set, not a metric.
 * Nothing here may imply Cairo data. The product is named CairoFlow; the footage is Los Angeles.
 */
export interface Site {
  id: string;
  street: string;
  crossStreet: string;
  city: string;
  dataset: string;
  year: number;
}

export const SITES: readonly Site[] = [
  {
    id: "ngsim-lankershim-universal-hollywood",
    street: "Lankershim Blvd",
    crossStreet: "Universal Hollywood Dr",
    city: "Los Angeles",
    dataset: "NGSIM",
    year: 2005,
  },
];

export const ACTIVE_SITE = SITES[0] as Site;

export function siteName(site: Site): string {
  return `${site.street} at ${site.crossStreet}`;
}

/**
 * The name comes from a present-day map, not from the dataset, so it is a supported hypothesis and
 * never a measured fact: docs/DATA_REGISTER.md §14.3.
 */
export const SITE_NAME_NOTE = "Name from a present-day map, not the dataset";

/** What the product is, why a page called CairoFlow shows this site, and what it doesn't do. */
export function productLine(site: Site): string {
  return (
    `Decision support for traffic engineers: CairoFlow turns camera footage into checked evidence, tests signal ` +
    `changes in simulation, and says so when the evidence can't support one. This site is ${site.city}, ${site.year}. ` +
    `It holds no Cairo data, and nothing here controls real traffic.`
  );
}

/** A record's ground frame as a reader sees it. The record's own id stays one hover away. */
export function groundFrameName(frame: string): string {
  return frame === "local_enu_junction_m" ? "Local junction grid, in metres" : frame;
}

/** The site record's own status for its name, as a reader sees it: supported_hypothesis becomes Supported hypothesis. */
export function siteNameStatusLabel(status: string): string {
  if (status === "supported_hypothesis") return "Inferred from a present-day map";
  const words = status.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
