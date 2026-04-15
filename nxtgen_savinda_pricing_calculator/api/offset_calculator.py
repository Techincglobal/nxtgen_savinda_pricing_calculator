"""
nxtgen_savinda_pricing_calculator/api/offset_calculator.py

Uses only these DocTypes:
  - Offset Spec            (group: Specification | Finishing | Machine)
  - Offset Spec Cost Fact  (child table: cost_fact Link, is_primary Check)
  - Cost Fact              (calculation, min_qty, items[], table_acwl[])
  - Cost Fact Item         (item→Item, is_fix_rate, rate)
  - Cost Fact  Attribute   (lable, attribute_name, type)
  - Calculation Breakdown  (existing main doc)
  - Cost Fact Details      (existing child table)
"""
import json
import math
import frappe
from frappe.utils import flt, cint


@frappe.whitelist()
def get_specs():
    """Checkbox list — Specification + Finishing groups only, NOT Machine."""
    specs = frappe.get_all(
        "Offset Spec",
        filters={"is_active": 1, "group": ["!=", "Machine"]},
        fields=["name", "spec_name", "group"],
        order_by="spec_name asc",
    )
    return [_enrich_spec(s) for s in specs]


@frappe.whitelist()
def get_machines():
    """Dropdown — Machine group only."""
    specs = frappe.get_all(
        "Offset Spec",
        filters={"is_active": 1, "group": "Machine"},
        fields=["name", "spec_name", "group"],
        order_by="spec_name asc",
    )
    return [_enrich_spec(s) for s in specs]


def _enrich_spec(spec):
    doc = frappe.get_doc("Offset Spec", spec["name"])
    return {
        "name":       spec["name"],
        "spec_name":  spec["spec_name"],
        "group":      spec["group"],
        "cost_facts": [
            {
                "cost_fact":  row.cost_fact,
                "is_primary": cint(row.is_primary),
                "master":     _get_cf_data(row.cost_fact),
            }
            for row in (doc.cost_facts or [])
        ],
    }


@frappe.whitelist()
def calculate(payload):
    if isinstance(payload, str):
        payload = json.loads(payload)

    form           = payload.get("form", {})
    machine_spec   = payload.get("machine_spec")
    selected_specs = payload.get("selected_specs", [])

    price_list     = _resolve_pl(form.get("price_list"), form.get("customer_name"))
    sheet          = _calc_sheet(form)
    item_qty       = flt(form.get("item_qty") or 0)
    no_of_colors   = cint(form.get("no_of_colors") or 0)
    material_rate  = flt(form.get("material_rate") or 0)
    full_sheet_qty = sheet.get("full_sheet_qty", 0)
    cut_sheet_qty  = sheet.get("cut_sheet_qty",  0)
    cut_sheet_area = flt(form.get("cut_sheet_l", 0)) * flt(form.get("cut_sheet_w", 0))

    cost_rows = []
    mat_total = prep_total = prod_total = 0.0

    # 1. Material (auto)
    if form.get("base_material") and material_rate and full_sheet_qty:
        iname = frappe.db.get_value("Item", form["base_material"], "item_name") or form["base_material"]
        amt   = round(full_sheet_qty * material_rate, 2)
        mat_total += amt
        cost_rows.append({
            "section": "Material", "spec_name": "Base Material",
            "cost_fact": "Paper / Board", "cost_group": "Material",
            "selected_item": form["base_material"], "selected_item_name": iname,
            "attribute_values": {}, "req_qty": full_sheet_qty,
            "rate": material_rate, "amount": amt, "is_auto": True,
        })

    # 2. Machine (auto)
    if machine_spec:
        rows, mt, pp, pr = _process_spec(
            machine_spec, form, sheet, item_qty, no_of_colors, material_rate,
            full_sheet_qty, cut_sheet_qty, cut_sheet_area, price_list,
            section="Machine", is_auto=True
        )
        cost_rows.extend(rows)
        mat_total += mt; prep_total += pp; prod_total += pr

    # 3. Selected specs
    for spec in selected_specs:
        rows, mt, pp, pr = _process_spec(
            spec, form, sheet, item_qty, no_of_colors, material_rate,
            full_sheet_qty, cut_sheet_qty, cut_sheet_area, price_list,
            section="Spec", is_auto=False
        )
        cost_rows.extend(rows)
        mat_total += mt; prep_total += pp; prod_total += pr

    grand = mat_total + prep_total + prod_total
    pm    = flt(form.get("profit_margin", 0)) / 100
    uc    = grand / item_qty if item_qty else 0
    sscl  = uc * 0.025 if form.get("tax_sscl") else 0
    qu    = (uc + sscl) * (1 + pm)
    vat   = qu * 0.18 if form.get("tax_vat") else 0
    su    = qu + vat
    mc    = (((qu * item_qty) - (prep_total + mat_total)) / (qu * item_qty) * 100) if qu * item_qty else 0

    return {
        "sheet":       sheet,
        "cost_rows":   cost_rows,
        "group_totals": {
            "material":    round(mat_total,  2),
            "preparation": round(prep_total, 2),
            "production":  round(prod_total, 2),
            "grand":       round(grand, 2),
        },
        "pricing": {
            "item_qty":    item_qty,
            "unit_cost":   round(uc, 4),
            "sscl":        round(sscl * item_qty, 2),
            "quoted_cost": round(qu  * item_qty, 2),
            "vat":         round(vat * item_qty, 2),
            "sell_unit":   round(su, 4),
            "sell_total":  round(su * item_qty, 2),
            "mat_contrib": round(mc, 2),
        },
        "price_list_used": price_list,
    }


@frappe.whitelist()
def save_costing(payload):
    if isinstance(payload, str):
        payload = json.loads(payload)

    form           = payload.get("form", {})
    machine_spec   = payload.get("machine_spec")
    selected_specs = payload.get("selected_specs", [])
    calc_result    = payload.get("calc_result", {})
    doc_name       = payload.get("doc_name", "")

    pricing = calc_result.get("pricing", {})
    sheet   = calc_result.get("sheet",   {})

    if doc_name and frappe.db.exists("Calculation Breakdown", doc_name):
        doc = frappe.get_doc("Calculation Breakdown", doc_name)
        doc.cost_facts = []
    else:
        doc = frappe.new_doc("Calculation Breakdown")

    doc.customer_name = form.get("customer_name", "")
    doc.ref           = form.get("ref", "")
    doc.price_list    = form.get("price_list", "")
    doc.pricing_type  = "Offset"
    doc.base_material = form.get("base_material", "")
    doc.material_rate = flt(form.get("material_rate", 0))
    doc.carton_size   = form.get("carton_size", "")
    doc.full_sheet_l  = flt(form.get("full_sheet_l", 0))
    doc.full_sheet_w  = flt(form.get("full_sheet_w", 0))
    doc.cut_sheet_l   = flt(form.get("cut_sheet_l", 0))
    doc.cut_sheetw    = flt(form.get("cut_sheet_w", 0))
    doc.no_of_cuts    = cint(form.get("no_of_cuts", 1))
    doc.no_of_ups     = cint(form.get("no_of_ups",  1))
    doc.no_of_colors  = cint(form.get("no_of_colors", 0))
    doc.item_qty      = flt(form.get("item_qty", 0))
    doc.cut_sheet_ups = sheet.get("cut_sheet_ups",  0)
    doc.cut_sheet_qty = sheet.get("cut_sheet_qty",  0)
    doc.wastage       = sheet.get("wastage",        0)
    doc.req_cut_sheets= sheet.get("req_cut_sheets", 0)
    doc.full_sheet_qty= sheet.get("full_sheet_qty", 0)

    for f, v in [
        ("profit_margin", flt(form.get("profit_margin", 0))),
        ("tax_sscl",      1 if form.get("tax_sscl") else 0),
        ("tax_vat",       1 if form.get("tax_vat")  else 0),
        ("unit_cost",     flt(pricing.get("unit_cost", 0))),
        ("selling_price", flt(pricing.get("sell_total", 0))),
    ]:
        if hasattr(doc, f):
            setattr(doc, f, v)

    if hasattr(doc, "ui_state"):
        doc.ui_state = json.dumps({
            "form":           form,
            "machine_spec":   machine_spec,
            "selected_specs": selected_specs,
            "calc_result":    calc_result,
        })

    for row in calc_result.get("cost_rows", []):
        # Store ALL rows including auto (material, machine) so Frappe form
        # shows the complete breakdown AND validate() can recalculate correctly.
        # The base material row is stored as a cost_fact row with cost_fact=""
        # so validate() picks it up via material_rate × full_sheet_qty separately.
        # Non-auto rows go into cost_facts child table.
        cf = doc.append("cost_facts", {})
        cf.cost_fact      = row.get("cost_fact", "") if not row.get("is_auto") else ""
        cf.cost_group     = row.get("cost_group", "")
        cf.selected_item  = row.get("selected_item", "")
        cf.req_qty        = flt(row.get("req_qty", 0))
        cf.rate           = flt(row.get("rate", 0))
        cf.amount         = flt(row.get("amount", 0))
        cf.attribute_json = json.dumps(row.get("attribute_values", {}))

    # Set unit_cost from calculator result BEFORE save
    # so even if validate() recalculates, it should match
    if pricing.get("unit_cost"):
        doc.unit_cost     = flt(pricing.get("unit_cost", 0))
    if pricing.get("sell_total"):
        doc.selling_price = flt(pricing.get("sell_total", 0))

    if doc_name and frappe.db.exists("Calculation Breakdown", doc_name):
        doc.save(ignore_permissions=True)
    else:
        doc.insert(ignore_permissions=True)

    # After save, force correct unit_cost (in case validate() overwrote it)
    final_uc = flt(pricing.get("unit_cost", 0))
    if final_uc and abs(flt(doc.unit_cost) - final_uc) > 0.001:
        frappe.db.set_value("Calculation Breakdown", doc.name, "unit_cost", final_uc)
        frappe.db.set_value("Calculation Breakdown", doc.name, "selling_price",
                            flt(pricing.get("sell_total", 0)))

    frappe.db.commit()
    return {"doc_name": doc.name, "status": "saved"}


@frappe.whitelist()
def load_costing(name):
    doc = frappe.get_doc("Calculation Breakdown", name)
    if hasattr(doc, "ui_state") and doc.ui_state:
        try:
            state = json.loads(doc.ui_state)
            state["doc_name"] = doc.name
            state["status"]   = doc.docstatus
            return state
        except Exception:
            pass
    form = {
        "customer_name": doc.customer_name or "",
        "ref":           doc.ref or "",
        "price_list":    doc.price_list or "",
        "carton_size":   doc.carton_size or "",
        "base_material": doc.base_material or "",
        "material_rate": flt(doc.material_rate),
        "full_sheet_l":  flt(doc.full_sheet_l),
        "full_sheet_w":  flt(doc.full_sheet_w),
        "cut_sheet_l":   flt(doc.cut_sheet_l),
        "cut_sheet_w":   flt(doc.cut_sheetw),
        "no_of_cuts":    cint(doc.no_of_cuts),
        "no_of_ups":     cint(doc.no_of_ups),
        "no_of_colors":  cint(doc.no_of_colors),
        "item_qty":      flt(doc.item_qty),
        "profit_margin": flt(getattr(doc, "profit_margin", 0)),
        "tax_sscl":      cint(getattr(doc, "tax_sscl", 0)),
        "tax_vat":       cint(getattr(doc, "tax_vat",  0)),
    }
    return {"doc_name": doc.name, "status": doc.docstatus,
            "form": form, "machine_spec": None, "selected_specs": [], "calc_result": None}


# ── Helpers ───────────────────────────────────────────────────

def _get_cf_data(name):
    if not name:
        return {}
    try:
        doc = frappe.get_doc("Cost Fact", name)
        return {
            "name":         doc.name,
            "cost_group":   doc.cost_group or "",
            # Legacy keyword field (fallback if formulas are empty)
            "calculation":  doc.calculation or "",
            # New formula fields — evaluated by _safe_eval()
            "qty_formula":  (getattr(doc, "qty_formula",  None) or "").strip(),
            "rate_formula": (getattr(doc, "rate_formula", None) or "").strip(),
            "min_qty":      flt(doc.min_qty),
            "items": [
                {
                    "item":        r.item,
                    "item_name":   frappe.db.get_value("Item", r.item, "item_name") or r.item if r.item else "",
                    "is_fix_rate": cint(r.is_fix_rate),
                    "rate":        flt(r.rate),
                    "min_rate":    flt(getattr(r, "min_rate", 0)),
                }
                for r in (doc.items or [])
            ],
            "attributes": [
                {"attribute_name": r.attribute_name, "lable": r.lable, "type": r.type or "Number"}
                for r in (doc.table_acwl or [])
            ],
        }
    except Exception:
        return {}


def _resolve_pl(price_list=None, customer=None):
    if price_list: return price_list
    if customer:
        pl = frappe.db.get_value("Customer", customer, "default_price_list")
        if pl: return pl
    return frappe.db.get_single_value("Selling Settings", "selling_price_list") or ""


def _get_item_rate(item_code, price_list):
    if not item_code: return 0.0
    if price_list:
        rate = frappe.db.get_value(
            "Item Price",
            {"item_code": item_code, "price_list": price_list, "selling": 1},
            "price_list_rate", order_by="valid_from desc",
        )
        if rate: return flt(rate)
    return flt(frappe.db.get_value("Item", item_code, "valuation_rate") or 0)


def _calc_sheet(form):
    no_cuts  = max(cint(form.get("no_of_cuts"))  or 1, 1)
    no_ups   = max(cint(form.get("no_of_ups"))   or 1, 1)
    item_qty = flt(form.get("item_qty") or 0)
    if not item_qty:
        return {k: 0 for k in ["cut_sheet_ups","cut_sheet_qty","wastage","req_cut_sheets","full_sheet_qty"]}
    cup    = max(no_ups // no_cuts, 1)
    cqty   = math.ceil(item_qty / cup)
    waste  = max(math.ceil(cqty * 0.05), 500)
    rqty   = cqty + waste
    fqty   = math.ceil(rqty / no_cuts)
    return {"cut_sheet_ups": cup, "cut_sheet_qty": cqty, "wastage": waste,
            "req_cut_sheets": rqty, "full_sheet_qty": fqty}


def _process_spec(spec, form, sheet, item_qty, no_of_colors, material_rate,
                  full_sheet_qty, cut_sheet_qty, cut_sheet_area, price_list,
                  section="Spec", is_auto=False):
    all_rows = []; mat = prep = prod = 0.0
    for cf_row in spec.get("cost_facts", []):
        row, mt, pp, pr = _build_row(
            cf_row, spec.get("spec_name",""), form, sheet,
            item_qty, no_of_colors, material_rate,
            full_sheet_qty, cut_sheet_qty, cut_sheet_area, price_list
        )
        row["section"] = section
        row["is_auto"] = is_auto
        all_rows.append(row)
        mat += mt; prep += pp; prod += pr
    return all_rows, mat, prep, prod




class _AttrDict:
    """
    Wraps the attribute_values dict so formulas can use dot notation.
    attr.length  →  attribute_values["length"]  (returns 0.0 if missing)
    """
    def __init__(self, d):
        self._d = d or {}
    def __getattr__(self, key):
        val = self._d.get(key, 0)
        try:    return float(val)
        except: return val
    def __getitem__(self, key):
        val = self._d.get(key, 0)
        try:    return float(val)
        except: return val
    def get(self, key, default=0):
        val = self._d.get(key, default)
        try:    return float(val)
        except: return val


def _safe_eval(formula, ctx):
    """
    Safely evaluate a user-defined formula string.

    Security:
      - Runs with __builtins__ = {} (no access to Python built-ins)
      - Blocks dangerous keywords: __, import, exec, eval, open, os, sys etc.
      - Only exposes whitelisted math functions + the evaluation context

    Returns float result, or 0.0 on error.
    """
    if not formula or not str(formula).strip():
        return 0.0

    cleaned = str(formula).strip()

    # Security: reject dangerous patterns
    _blocked = ["__", "import", "exec", "eval", "open", "os.",
                "sys.", "getattr", "setattr", "globals", "locals", "builtins"]
    for bad in _blocked:
        if bad in cleaned:
            frappe.log_error(
                title="Cost Fact Formula Security Block",
                message=f"Formula blocked: '{cleaned}' contains '{bad}'"
            )
            return 0.0

    # Build safe namespace
    safe_ns = {
        "__builtins__": {},
        "ceil":  math.ceil,
        "floor": math.floor,
        "round": round,
        "max":   max,
        "min":   min,
        "abs":   abs,
        "sqrt":  math.sqrt,
    }
    safe_ns.update(ctx)

    try:
        result = eval(cleaned, safe_ns)
        return float(result) if result is not None else 0.0
    except ZeroDivisionError:
        return 0.0
    except Exception as e:
        frappe.log_error(
            title="Cost Fact Formula Error",
            message=f"Formula: '{cleaned}' | Error: {e}"
        )
        return 0.0


def _build_row(cf_row, spec_name, form, sheet, item_qty, no_of_colors,
               material_rate, full_sheet_qty, cut_sheet_qty,
               cut_sheet_area, price_list='Standard Selling'):
    cf_name    = cf_row.get("cost_fact", "")
    is_primary = cint(cf_row.get("is_primary", 0))
    sel_item   = cf_row.get("selected_item", "")
    attr       = cf_row.get("attribute_values", {}) or {}
    user_rate  = flt(cf_row.get("rate", 0))

    cf          = _get_cf_data(cf_name)
    grp_str     = cf.get("cost_group", "")
    qty_formula = cf.get("qty_formula",  "")
    rate_formula= cf.get("rate_formula", "")
    calc_key    = (cf.get("calculation") or "").strip().lower()
    min_amt     = flt(cf.get("min_qty", 0))

    # ── Resolve item_rate, fix_rate, min_rate for use in formulas ──
    items    = cf.get("items", [])
    item_row = next((i for i in items if i["item"] == sel_item), None)
    if item_row:
        fix_rate  = flt(item_row["rate"])     if item_row.get("is_fix_rate") else 0.0
        min_rate  = flt(item_row.get("min_rate", 0))
        item_rate = fix_rate if item_row.get("is_fix_rate") else _get_item_rate(sel_item, price_list)
    else:
        fix_rate  = 0.0
        min_rate  = 0.0
        item_rate = _get_item_rate(sel_item, price_list) if sel_item else material_rate

    # ── Build formula evaluation context ──────────────────────────────
    # All variables available inside qty_formula and rate_formula:
    #
    #   Sheet variables:  full_sheet_qty, cut_sheet_qty, cut_sheet_area,
    #                     no_of_colors, item_qty, no_of_cuts, no_of_ups, cut_sheet_ups
    #
    #   Attribute variables: attr.xxx  (any attribute defined in the Cost Fact Attribute table
    #                                   e.g. attr.length, attr.width, attr.qty, attr.per_box)
    #                        Also accessible as plain variables: length, width, qty
    #                        (for convenience — attr.xxx and xxx are both valid)
    #
    #   Rate variables:   item_rate  — price list rate of the selected item (or valuation_rate)
    #                     fix_rate   — fixed rate stored on the Cost Fact Item row
    #                     material_rate — base material rate from the order form
    #
    #   Math functions:   ceil, floor, max, min, abs, round, sqrt
    #
    # Examples:
    #   qty_formula  = "attr.length * attr.width * attr.qty"
    #   qty_formula  = "cut_sheet_qty * no_of_colors"
    #   qty_formula  = "ceil(item_qty / attr.per_box)"
    #   rate_formula = "item_rate"
    #   rate_formula = "fix_rate"
    #   rate_formula = "item_rate * attr.color_factor"
    # ─────────────────────────────────────────────────────────────────

    eval_ctx = {
        # Sheet
        "full_sheet_qty":  full_sheet_qty,
        "cut_sheet_qty":   cut_sheet_qty,
        "cut_sheet_area":  cut_sheet_area,
        "no_of_colors":    no_of_colors,
        "item_qty":        item_qty,
        "no_of_cuts":      cint(form.get("no_of_cuts", 1)),
        "no_of_ups":       cint(form.get("no_of_ups",  1)),
        "cut_sheet_ups":   sheet.get("cut_sheet_ups",  1),
        # Rates
        "item_rate":       item_rate,
        "fix_rate":        fix_rate,
        "min_rate":        min_rate,   # per-unit minimum from item row (min_rate field)
        "min_qty":         min_amt,    # Cost Fact min_qty — usable in rate_formula (Case 3: max(item_rate, min_qty/cut_sheet_qty))
        "material_rate":   material_rate,
        # Attributes via dot notation: attr.length, attr.width etc.
        "attr":            _AttrDict(attr),
    }
    # Also expose each attribute as a plain variable for convenience
    # e.g. qty_formula = "length * width" instead of "attr.length * attr.width"
    for k, v in attr.items():
        try:
            eval_ctx[str(k)] = float(v)
        except (TypeError, ValueError):
            eval_ctx[str(k)] = v

    # ── Calculate req_qty ──────────────────────────────────────────────
    rq = flt(cf_row.get("req_qty", 0))
    if qty_formula and (not rq or is_primary):
        # Use the formula field (new dynamic system)
        rq = _safe_eval(qty_formula, eval_ctx)

    elif not rq or is_primary:
        # Fallback: legacy keyword system (backward compatible)
        if   calc_key == "full_sheet_qty":       rq = full_sheet_qty
        elif calc_key == "cut_sheet_qty":        rq = cut_sheet_qty
        elif calc_key == "cut_sheet_area":       rq = cut_sheet_qty * cut_sheet_area
        elif calc_key == "no_of_colors":         rq = no_of_colors
        elif calc_key == "cut_sheet_qty_colors": rq = cut_sheet_qty * no_of_colors
        elif calc_key == "order_qty":            rq = item_qty
        elif calc_key == "fixed":                rq = flt(attr.get("qty", 1)) or 1
        elif calc_key == "block_area" or (attr.get("length") and attr.get("width")):
            rq = flt(attr.get("length",0)) * flt(attr.get("width",0)) * (flt(attr.get("qty",1)) or 1)
        elif attr.get("qty"):
            rq = flt(attr["qty"])

    # ── Calculate rate ─────────────────────────────────────────────────
    rate = 0.0
    if rate_formula:
        # Use the formula field (new dynamic system)
        rate = _safe_eval(rate_formula, eval_ctx)
    elif user_rate:
        rate = user_rate
    elif sel_item:
        rate = item_rate
    elif not cf.get("items"):
        rate = material_rate

    # ── Amount with minimum charge ────────────────────────────────────
    amount = round(rq * rate, 2)
    if min_amt and amount < min_amt:
        amount = flt(min_amt)

    # item_name for display
    sel_name = item_row["item_name"] if item_row else (
        frappe.db.get_value("Item", sel_item, "item_name") or sel_item if sel_item else ""
    )

    grp = grp_str.lower()
    return {
        "spec_name": spec_name, "cost_fact": cf_name, "cost_group": grp_str,
        "selected_item": sel_item, "selected_item_name": sel_name,
        "attribute_values": attr, "req_qty": round(rq, 4),
        "rate": round(rate, 4), "amount": amount,
        "is_auto": False, "section": "Spec",
    }, (amount if grp=="material" else 0), (amount if grp=="preparation" else 0), (amount if grp not in ("material","preparation") else 0)


@frappe.whitelist()
def sync_cost_item_unit_cost(calculation_breakdown):
    """
    Called after calculator saves a Calculation Breakdown.
    Finds all Cost Item Calculation rows linking this CB,
    then updates the parent Cost Item's unit_cost (sum of all linked CBs).
    Uses ignore_permissions since this is triggered by the whitelisted save flow.
    """
    if not calculation_breakdown:
        return {"updated": 0}

    # Find all Cost Item Calculation rows linking this CB
    rows = frappe.get_all(
        "Cost Item Calculation",
        filters={"calculation_breakdown": calculation_breakdown},
        fields=["parent", "name"],
    )

    if not rows:
        return {"updated": 0}

    parents = list(set(r["parent"] for r in rows))
    updated = 0

    for ci_name in parents:
        try:
            ci = frappe.get_doc("cost Item", ci_name)

            # Sync unit_cost from each linked CB
            total_uc = 0.0
            for row in (ci.calculations or []):
                if not row.calculation_breakdown:
                    continue
                uc = frappe.db.get_value(
                    "Calculation Breakdown", row.calculation_breakdown, "unit_cost"
                )
                row.unit_cost = frappe.utils.flt(uc)
                row.amount    = round(frappe.utils.flt(uc) * frappe.utils.flt(ci.item_qty), 2)
                total_uc += frappe.utils.flt(uc)

            ci.unit_cost  = round(total_uc, 4)
            ci.total_cost = round(total_uc * frappe.utils.flt(ci.item_qty), 2)
            ci.save(ignore_permissions=True)
            updated += 1
        except Exception as e:
            frappe.log_error(title="sync_cost_item_unit_cost error", message=str(e))

    frappe.db.commit()
    return {"updated": updated}