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

These layouts come from the app's corresponding JSON/template files. Any local
changes to these **specific formats** will be replaced on migration; put approved
layout changes in app source, or duplicate the format under a different name to
keep a site-only layout. Formats with other names, such as `Quotation-Savinda`,
are not synchronized by this helper. The site's default print-format selection
is not changed.

Before production deployment, take a backup and review the updated layouts.
No production deployment or migration is performed merely by editing these files.
