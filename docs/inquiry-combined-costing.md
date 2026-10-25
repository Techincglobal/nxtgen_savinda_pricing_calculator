# Combined costing from Inquiry components

This is an optional action. Existing **Create Breakdown**, manual cost items, and
book individual/combined breakdown creation continue to work as before.

1. Create a Cost Sheet from the Inquiry and save it. Use an **empty draft** sheet.
2. Choose **Actions → Create Combined Costing**.
3. Select **Gift Box** or **Book** and preview the component quantities and finishings.
4. Remove unwanted component rows if necessary, then click **Create Components**.
5. Each included row gets its own Cost Item and pricing-list row **only**. Its
   finishings are stored on the Cost Item and displayed on the pricing row.
   No Calculation Breakdowns are created and bundle settings remain unchanged.
6. Use the existing **Add Calculation** dialog for each component. Enter base material,
   sheet sizes, cuts and ups as normal. The calculation created at this point receives
   that component's finishings. Complete machines, rates and costing specifications;
   calculate and save.
   Finishing labels do not themselves create charges. Matching operation specifications
   can preselect through the calculator's existing operation/spec matching; review them.
7. If a combined quotation is required, configure **Quote as One Bundle** after adding
   the calculations, use **Load Bundle Components**, and review quantities/ratios.
   Continue with the existing quotation / FG / BOM process.

## Sample: CRM-OPP-2026-00070

The gift-box table contains Outer box 1,200, Inner carton 7,200 and Bottom tray 1,200.
Their selections are kept separate: Outer box BLACK FOIL / BOX FORMING / COLD FOIL;
Inner carton GLOSS VARNISH; Bottom tray COLD MATT LAMINATION.

The inquiry header says **12,000**. The import does not change or configure bundle
quantities. If the intended complete product quantity is 1,200, use that value when
configuring the optional quotation bundle to obtain component ratios **1 / 6 / 1**.

## Mapping and safety

- Gift-box rows: `custom_gift_box_types`; finishings: `custom_gift_box_finishings`.
- Book rows: `custom_pages`; finishings: `custom_page_finishings`.
- Match checked finishing rows by `page_row_name` to the component's `row_key`
  (or saved child row name); identical display names do not merge components.
- Book component quantity follows the existing convention: `no_of_pages × order quantity`.
  Order quantity is the inquiry header quantity, falling back to the sum of inquiry
  quantity breakdowns. Those breakdowns are not recreated as separate quantity tiers
  by this new action; use existing quantity-break tools where required.
- Gift-box base material comes from the component's `custom_item`. Book material
  comes from its Boards and Papers record's linked Item. Missing material stays
  available for manual selection, not borrowed from another component.
- Finishings are initially stored in each Cost Item's `inquiry_finishings` child table.
  A read-only import snapshot is shown on the pricing row. When the normal Add Calculation
  dialog is used, the component's current finishing child rows are copied to that new
  calculation, including an explicitly empty list. Existing calculations are not changed.
- Existing costing cannot be replaced or imported twice by this action. Document
  creation is atomic, permission checked, and the sheet is locked during creation.
- No inquiry records or existing print formats are modified. No new DocTypes are required.
- Deploy and migrate to install the Cost Item finishing fields and pricing-row display
  field, then clear site/browser cache. No new DocTypes or print formats are introduced.
