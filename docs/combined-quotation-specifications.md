# Combined item quotation: customer specification and print

Uses the existing one-bundle-per-Cost-Sheet calculations and manufacturing links.
It does not add a second quotation DocType or merge component BOMs.

## Sample COST-26-10.00643-1

Treat the supplied components as separate physical parts, even though both currently
have the same name. Gift Box test at bundle quantity 1,000, ratios 1 and 2, recalculates
the selected component calculations at 1,000 and 2,000. It does not add the old
320,000-unit row prices. Cost Sheet/SO/FG/BOM handling is unchanged.

1. After deployment/migration, open the submitted Cost Sheet on Cloud and use its
   existing Create Quotation action. If it already has an active quotation, open that
   record instead of creating a duplicate.
2. A NEW quotation receives a **Combined Product — Customer-facing Specifications**
   section. Defaults come from the selected component calculations/cost items:
   bundle product name, distinct customer/common material names, physical dimensions,
   distinct colour counts, distinct finishings and BOM remarks. Raw material Item names
   and component costs are not automatically printed. Unresolved material names stay
   blank; review and enter a suitable customer description.
3. For an EXISTING DRAFT quotation, use **Fill → Load Combined Specifications**.
   This explicitly replaces specification text, not prices/quantities. Existing submitted
   quotations are not automatically rewritten; amend using the existing process if needed.
4. Review/edit product name, materials, colours, finishings, sizes and combined remarks.
   For the sample, default title is Gift Box test, size is 150 x 40 x 60 if supplied by
   both components, colours are 5, and the customer quantity is 1,000 complete products.
   The Cost Sheet export alone does not contain all CB/common material data, so the
   exact material text and price require the linked Cloud calculations.
5. Check VAT note, duty basis, currency, validity/date, terms and any separate tooling
   charges. No historical reference-PDF prices or dates are copied into the document.
6. Save, then **Actions → Print Combined Quotation**. Alternatively choose
   **Savinda Combined Item Quotation** in Print View. Select a letterhead if required.
7. Follow the existing margin approval/Won/FG/Sales Order process.

The format has one combined specification block, with one product price row per
quantity tier. It uses stored selling_price in quotation currency (no second conversion).
Specification wording is a quotation snapshot: ordinary subsequent saves do not refill
it from live costing. Editing the wording changes customer presentation only, not the
manufacturing calculations, ratios, component finishing tables or Product Library.

## Deployment / format protection

Update the pricing app, run `bench --site <site> migrate`, and hard-refresh. The existing
setup_bundle_fields helper adds the custom fields; the install/migration print sync
updates the app-managed combined format and the other named app-managed layouts.
Calculation masters and saved costing data are not overwritten. See
`update-data-policy.md` for the exact list of formats that update. Site-only formats
under different names and the default print-format selection remain unchanged.
Keep approved combined layout changes in `templates/prints/combined_item_quotation.html`;
local changes to this managed format will be replaced on the next migration.

The Cloud sample is not present on the local site; no Cloud records were changed.
