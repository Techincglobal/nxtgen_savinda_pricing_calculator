# Quote one assembled product from multiple Cost Items

This is optional. Normal Cost Sheets, quotations, Items and BOMs keep their existing flow.
The first version supports one bundle per Cost Sheet and one production type (Offset or
Flexo) across its components. It does not merge material calculations or printing layouts.
Saved/live print formats are not overwritten or synchronised by this feature.

### Adding another component from the calculator

Use **Add / Duplicate → New Component Item**, enter a name such as `Inner Carton`
and its Order Qty, then **Create & Open**. The copy is a separate Cost Item with its
own calculation; edit its material, cuts, ups and finishings independently. On a draft
bundle Cost Sheet it is added to both Pricing List and Bundle Components, initially
with Qty per Bundle = 1. Review/change that ratio on the Cost Sheet. Creating its FG
uses the existing Create FG Items action; quantity-only tiers do not create another FG.

The bundle quotation and the new parent Product Library take the distinct union of all
component finishing selections. For example, lid = Die Cutting + Gloss and tray = Die
Cutting + Lamination gives **Die Cutting + Gloss + Lamination**, not Die Cutting twice.
Individual component finishings are not replaced by that union.

The source Savinda Quotation template now supports this bundle finishing field. Saved
live print formats are still not synchronised. If your existing format has this line:

```jinja
{% set ns_r.fin = '' %}
```

inside its first-row collection, replace that occurrence manually with:

```jinja
{% set ns_r.fin = (row.finishing or '') if row.custom_costing_bundle else '' %}
```

This changes bundle rows only; normal rows still calculate their finishing as before.
If the format is Standard and editing is locked, duplicate it as a custom Print Format,
apply the change to that copy, and select the copy when printing. Keep the original intact.

## 1. Cost the components separately

Create and save a Cost Sheet with separate Cost Items/calculations for the lid, tray and
inner carton. Each calculation keeps its own material, dimensions, cuts, ups, machine,
finishing and wastage. Do not share a combined calculation between components.

For 1,000 gift boxes requiring one lid, one tray and two inner cartons, the component
calculation quantities are 1,000 / 1,000 / 2,000. The bundle calculation recalculates these
quantities for every bundle tier; it does not add their order quantities together.

## 2. Configure the bundle before submitting the Cost Sheet

In **Quotation Bundle / Assembly**:

1. Tick **Quote as One Bundle**.
2. Set **Bundle Product Name**, e.g. `Gift Box Complete`.
3. Set **Bundle Order Qty**, e.g. `1000`.
4. Select **Bundle Stock UOM**, e.g. `Nos` or an existing `Set` UOM.
5. Use **Bundle → Load Bundle Components**. This loads the saved Cost Items, each at
   Qty per Bundle = 1. Review the selected Calculation, especially if a Cost Item has
   more than one calculation.
6. Set the assembly ratios: lid = 1, tray = 1, inner carton = 2. Every Cost Item in this
   Cost Sheet must appear exactly once.
7. Optionally enter **Assembly / Packing Unit Cost** and **Unit Selling Price** in the
   company base currency, per finished bundle. Avoid including a charge twice if it is
   already in the component calculations. These fields affect quoted prices only; add
   the actual assembly operation/packing materials to the assembly BOM separately.
8. If using quantity breaks, enter **bundle quantities**, e.g. 1000 / 5000 / 10000.
   Whole bundle quantities are required.
9. Save and submit the Cost Sheet.

Submitted bundle quantities, components and calculation links are locked. Component FG
links can still be completed afterwards. Use cancellation/amendment for structural changes.

## 3. Create the quotation

1. Click **Create Quotation** on the submitted Cost Sheet.
2. The quotation contains one product, `Gift Box Complete`, with one price row per
   bundle quantity tier. It does not show separate lid/tray/carton product rows.
3. Each unit price is the sum of recalculated component selling prices multiplied by
   their assembly ratios, plus the assembly/packing unit selling price.
4. Check customer, currency, exchange rate, validity, charges and terms.
5. Save, print and follow the existing quotation approval workflow. The margin uses
   the lowest component margin, assembly-charge margin if applicable, and overall
   bundle margin. A manual quotation-price reduction cannot bypass the low-margin gate.
6. Mark as Won once the customer accepts.

The existing quotation print format already groups these rows as one product. No print
format replacement is needed. A quotation price override does not update its Pricing Rules.

## 4. Create component FGs

1. On the Cost Sheet, use the existing **Actions → Create FG Items** action.
2. Review each component's Product Library details and finishings.
3. Use **Bundle → Sync Component FG Links** to fill unambiguous FG matches.
4. If multiple FGs exist for a Cost Item, select the correct **Component FG** manually
   and update the submitted Cost Sheet. The selected FG must link to that Cost Item.
5. Complete each component's existing Product Library/pricing approval, enabling the FG.

## 5. Create the assembled parent FG and its prices

1. Use **Bundle → Create / Open Bundle FG** on the submitted Cost Sheet.
2. Choose Item Group, Department and Customer. Customer defaults from the inquiry/name
   when available; review it in Product Library before saving customer-specific rules.
3. The parent FG is created disabled with a separate Product Library approval record.
   Its costing source is the entire bundle Cost Sheet, not one component's calculation.
4. Review the Product Library details. In **Manage Pricing Rules**, combined bundle
   quantity prices are prefilled; review ranges, currency, customer, validity and priority.
5. Use **Approve & Enable FG**. Active positive-price rules must cover the bundle's
   order quantity and all quantity tiers.

The quotation's bundle FG links are populated when this parent is created. For existing
bundle FGs, **Create / Link FG Items** on the quotation opens their pricing-rule editor.

## 6. Build the manufacturing BOMs

1. Open **BOM Builder** from the bundle quotation. It lists the component FGs with their
   separate calculations, not the parent as if it had one printing calculation.
2. Build and submit a BOM for each component using the existing builder. Select each
   non-base raw-material Item and its UOM manually.
3. Create the parent's BOM using the standard ERPNext BOM form, quantity = 1 bundle.
4. Add lid × 1, tray × 1 and inner carton × 2. Link their submitted component BOMs.
5. Add assembly operation/workstation and actual assembly/packing materials as needed.
6. Submit the parent BOM and mark it active/default.

The assembly ratios must match the bundle configuration. Component BOMs can contain
their own multilevel SFG chains; the custom planning button only expands the parent's
first level.

## 7. Sales Order and Production Plan

1. From the Won quotation, use **Create Sales Order**. Only the assembled Gift Box FG
   is offered, not its component Items. Confirm the customer's actual order quantity.
2. Enter delivery date, PO number/date and department. Create the draft Sales Order.
3. Check the fetched price and submit the Sales Order. The price is from an active,
   matching generated Pricing Rule. No matching quantity/date/customer/currency rule
   means rate = 0; it does not fall back to the quotation price.
4. Create the Production Plan using the existing flow for that order and parent BOM.
5. In the draft PP at the permitted stage, use **Get BOM Items for Manufacture**.
   This replaces the planning table, so run it before making manual planning edits.
6. The table contains an Assembly parent plus separate Manufacture components at their
   BOM quantities. Each component keeps its own material, cuts, ups and sheet figures.
7. For Offset, the parent is grouped and components link to it for the existing
   `line_item_bk` job-ticket layout. Flexo retains its separate component rows.

For separate component Work Orders, also use ERPNext's **Get Sub Assembly Items**
in the PP's standard **Sub Assembly Items** section and set the components to
**In House** manufacture. The custom Manufacturing Planning table supplies printing/
material planning and job-ticket details; it does not replace ERPNext's Work Order
sub-assembly setup.

The parent BOM must be submitted/active, its component ratios must match the bundle,
and all selected components must have their own active BOMs. Different materials are
not consolidated into one print run.

## Deployment and verification

Deploy the updated app and run `bench --site <site> migrate` (or the Frappe Cloud app
update/migration process), then hard-refresh the browser. This adds the child DocType
and custom fields; it does not rewrite your saved print formats. Migration was run on
the local site only; no cloud deployment or Git push was performed.

Local validation includes mocked regression tests and real read-only calculations/print
rendering. Creating/submitting/approving business documents end-to-end was not executed:
the safety review blocked that test because it could send notifications or have other
non-transactional effects. Do that test on a dedicated test site before production use.
