# Installation and update policy

After deploying the committed app code, run the site's normal `bench migrate`.
The migration hooks now install/update app custom fields and refresh the named
app-managed print layouts. Standard DocType schema sync installs new fields too.

Calculation masters are **not imported on migration**. On first installation or
explicit install-only repair, the seed importer creates missing records only:
existing Cost Facts (including child rows), Machines, Inks, Foils, Specs, Items,
rates and Costing Configuration are preserved. Saved Cost Sheets and Calculation
Breakdowns are not recalculated, backfilled or replaced by these setup hooks.
Existing workflows are preserved; legacy transaction conversion helpers are not
called automatically.

## Print formats which DO update

- Offset Job Ticket
- Flexo Job Ticket
- Offset NPD Job Ticket
- Flexo NPD Job Ticket
- Savinda Quotation
- Product Costing Summary
- Savinda Combined Item Quotation
- AOD (40 mm top margin reserved for preprinted letterhead)

These layouts come from the app's corresponding JSON/template files. Any local
changes to these **specific formats** will be replaced on migration; put approved
layout changes in app source, or duplicate the format under a different name to
keep a site-only layout. Formats with other names, such as `Quotation-Savinda`,
are not synchronized by this helper. The site's default print-format selection
is not changed.

Exception for the quotation currency-display upgrade: existing Savinda Quotation
formats with the exact legacy price heading and two-decimal price expression get
only those two fragments replaced. This includes `Quotation-Savinda`. The rest of
their layout remains unchanged; unrecognised custom pricing layouts are skipped.

Foreign-currency quotations show price per **1,000 units**, with **four decimal
places**, and identify that basis in the price-column header. Local-currency
quotations still print per piece with two decimals. Stored selling prices and
Sales Order rates remain per single unit; do not multiply those stored rates by
1,000. The quotation's read-only `Price per 1,000 Units` field provides the foreign
currency display value. Conversion/scaling happens before display rounding.

Before production deployment, take a backup and review the updated layouts.
No production deployment or migration is performed merely by editing these files.

The custom `Offset Job Ticket - 1` format also receives a targeted binding repair
when it exists: row Size shows only `variant_value` (never actual dimensions),
and Exp Date uses `expiry_date`, not `exp_date` (Expected Date). Header dimensions
and all other layout HTML are preserved. Expiry is free text on Job Ticket Item;
the print preserves that text and may fall back to the Sales Order's actual expiry
date, but never to an expected/delivery date. These setup changes do not edit PP rows.
