# Delivery Note from Production Plan Packing

1. Approve/submit Packing records for the Production Plan. Enter package quantities
   and any Extra quantity on Packing; both quantities consume available stock.
2. Create a draft Delivery Note and select the company.
3. Choose **Get Items From → Get from Packing**.
4. Select **Production Plan (with approved packing)**. No Sales Order picker is shown.
   The list contains readable plans with submitted, undelivered Packing records.
5. Tick the Packing records and choose **Set Delivery Note Items**. This replaces the
   current item table. Selecting a different PP clears the previous selection.
6. Review customer, currency, warehouse, taxes, batch/serial details and delivery quantities.
7. Save and submit using ERPNext's normal process.

For each Packing, Packed Qty creates a normal priced line. Positive Extra Qty creates
a separate line for the same Item with rate/amount zero, marked as a free item.
Both lines keep the Packing link, warehouse/UOM and, for an SO-based PP, the native
`against_sales_order` and `so_detail` links. ERPNext's original Sales Order mapper is used.
Extra rows' copied price, discount/margin amounts and pricing-rule values are cleared;
the free row has 100% discount to prevent a fetched price-list rate restoring its value.
The pricing app enforces zero rates before validation and after standard validation.

Example: Packed Qty 100, rate 50, Extra 5 → paid line 100 at 50 (amount 5,000),
extra line 5 at zero (amount zero); physical quantity 105. Stock availability now reserves
Packed Qty + Extra on submitted, undelivered Packings. The AOD helper total counts both
DN lines, but saved print templates are not synchronised or overwritten.

Both lines count toward normal ERPNext delivery quantities and stock movement. If the
order is already fully delivered, Extra may require an appropriate over-delivery allowance
or order change under your normal policy. This feature does not bypass standard ERPNext
stock, discount-limit, order-status or over-delivery validations.

For an NPD PP without a Sales Order, its Customer (or the linked NPD Request's Customer)
must be selected. ERPNext Item defaults supply the DN lines; no artificial SO link is made.
The paid line's price must be reviewed if no applicable Item price exists.

Submitting/cancelling the DN sets/clears the delivered flag once per Packing even though
there may be two lines. Partial deliveries and multiple concurrent DNs for the same Packing
still use the existing boolean delivered flag; this is not a partial-dispatch/reservation system.

Deploy the pricing app changes, restart the relevant Frappe processes and clear site cache,
then hard-refresh the browser. No new fields or ERPNext-source changes are required.
