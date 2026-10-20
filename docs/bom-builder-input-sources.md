# BOM Builder technical input sources

The FG's linked Product Library is now preferred for matching technical fields.
If Item.custom_product_library is empty, the library is looked up by fg_item.
A library belonging to another FG is never used.

Precedence when loading/recomputing: manual builder overrides > filled, usable
Product Library inputs > the original selected Calculation Breakdown's saved form.
The original calculation still supplies production type, base material, selected
specifications/machines, costing rules and any missing technical inputs.

| Product Library | BOM Builder input |
| --- | --- |
| No of Ups | Full-sheet ups; cut-sheet ups are recalculated with Cuts |
| No of Colors | Colour count |
| Full Sheet Size | Full sheet length and width (Offset) |
| Cut Sheet Size | Cut sheet length and width (Offset) |
| Product Size | Product/carton-size description |
| Width (mm) | Reel width (Flexo), matching the existing FG creation mapping |
| Length (mm) | Product/label length (Flexo), matching the existing FG creation mapping |

Cuts is not a standard field on this Product Library. It retains the calculation
value. If a site already has a positive `no_of_cuts` field on the library, it is used.
No new DocType fields are added. Cut Sheet Ups is calculated, not copied as a fixed
order quantity; manufacturing quantity, sheet counts, wastage and consumption are
recomputed for the current builder quantity.

Offset size pairs support `31 x 43`, `31*43`, `31 × 43` and explicit inch notation.
Sizes retain the calculator's inch units; mm strings, partial sizes, zero dimensions
and descriptive multi-set strings do not override the original pair. Numeric zero,
negative values and blanks are treated as unset for these library inputs.

The BOM's stamped Calculation Details use the same library-first fallback rather
than always reading the original calculation. Manual operation/material edits still
work as before. Neither Product Library nor Cost Item/Calculation Breakdown is
changed by loading or recomputing the BOM Builder. Existing BOM records and live
print formats are not updated automatically.

To check: set the FG library's No of Ups to 8, keep Cuts at 2 in its calculation,
then reopen/reload the BOM Builder. Expect Cut Sheet Ups 4. Clear the library's Ups
to return to the saved calculation value. No master records are changed by tests.
