# Sales Order pricing-rule currency fallback

Costing-created FGs (including assembled bundle FGs) use generated quantity rules
when the Sales Order is validated/saved. Ordinary ERPNext Items are unaffected.

1. Find a matching rule in the Sales Order currency. An explicitly currency-scoped
   rule wins over a legacy rule without currency; priorities apply within each group.
2. If no transaction-currency rule matches, find a matching company-base rule.
   For Savinda's LKR company, this is an LKR rule.
3. Divide that base rule rate by the Sales Order's conversion rate, expressed as
   company base per one unit of transaction currency. For example, LKR 24.554 at
   300 LKR/USD becomes USD 0.081846666... per item before normal ERPNext precision.
4. Never reuse an unrelated foreign-currency rule. No match means zero.

Both lookups retain customer, active/disabled, validity-date and quantity conditions.
An expired USD rule can fall back to a valid LKR rule; an expired LKR rule cannot
supply a price. A matching USD rule does not get divided by the exchange rate again.
A missing/non-positive/non-finite foreign-currency exchange rate blocks validation.

This uses the Sales Order's exchange rate; it does not fetch an external rate or
change/create Pricing Rules. Rates remain **per single item**, not per 1,000.
Quotation per-1,000 printing is a separate presentation feature.

After deployment, check the order's company, currency, customer, date, quantity and
exchange rate, then save/re-save the draft Sales Order. ERPNext's initial item-picker
preview is not overridden by this change; authoritative custom pricing is on save.

## Create currency-specific rules from FG / Product Library

Open **Manage Pricing Rules** in the Product Library (or the available FG pricing
action). The pricing table itself now includes editable **Currency** and
**Exchange Rate** columns. **Fetch Missing Exchange Rates** fills pending rows.

1. Select USD, EUR or another Currency master record in each pricing row.
2. Enter company-base currency per one selected currency, for example **300 LKR
   per USD**, in that row's Exchange Rate column, or fetch missing rates for Valid
   From (today when that date is blank).
3. The popup converts the displayed per-item rates and unit costs. You may edit
   the selling rates and add quantity tiers. Conversion preserves nine decimal
   places rather than rounding small FX rates to zero at two decimals.
4. Review customer, quantities, validity and priority, then create the rules.

Different rows may use different currencies, including LKR and USD for the same
FG. They are saved as separate FG/currency rule groups, not under one popup-wide
currency. Changing a row's currency requires a new positive exchange rate before
creation is enabled. Changing that exchange rate converts only that row's entered
prices; it does not
silently reload or discard manual tier edits. Outdated exchange lookup responses
cannot overwrite a newer currency selection or manual exchange-rate entry in the row.

The saved Pricing Rule rate is **per single item in the selected currency**, not
per 1,000. For example, enter USD 0.081846667, not the quotation's printed USD
81.8467 per 1,000. The exchange rate is used for preparing these fixed-currency
rule prices, not for repricing the saved rule every day.

Creating a new USD/EUR rule retains LKR rules. Only an existing generated rule set
with the same FG, customer, currency and date window is replaced, as before.
Costing calculations and masters are not modified by the popup.
