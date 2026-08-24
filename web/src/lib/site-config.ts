/**
 * Single source of truth for real AngelCare Transit business facts used
 * across the site. Values here must trace back to
 * `docs/business-decisions-log.md` at the repo root — never invent a
 * value here without adding it there first.
 */
export const siteConfig = {
  name: "AngelCare Transit",
  shortName: "AngelCare",
  domain: "angelcaretransit.com",
  url: "https://www.angelcaretransit.com",
  description:
    "Non-emergency medical transportation serving the state of Texas. Ambulatory, wheelchair, and stretcher transport, plus medical equipment delivery.",
  phone: "817-766-9228",
  phoneHref: "tel:+18177669228",
  email: "angelcaretx@gmail.com",
  emailHref: "mailto:angelcaretx@gmail.com",
  serviceArea: "State of Texas",
  /** Lowercase, mid-sentence form, e.g. "...transportation across {serviceAreaLower}." */
  serviceAreaLower: "the state of Texas",
} as const;

export const services = [
  {
    slug: "ambulatory",
    name: "Ambulatory Transportation",
    summary:
      "For passengers who can walk and sit independently but need reliable, safe transportation to and from medical appointments.",
  },
  {
    slug: "wheelchair",
    name: "Wheelchair Transportation",
    summary:
      "Wheelchair-accessible vehicles and trained staff for passengers who use a wheelchair and cannot transfer to a standard seat.",
  },
  {
    slug: "stretcher",
    name: "Stretcher Transportation",
    summary:
      "For passengers who must remain lying down during transport and cannot be safely transported seated.",
  },
  {
    slug: "equipment-delivery",
    name: "Medical Supply & Equipment Delivery",
    summary:
      "Delivery of wheelchairs, walkers, oxygen equipment, and other home-care equipment to homes, clinics, and care facilities.",
  },
] as const;
