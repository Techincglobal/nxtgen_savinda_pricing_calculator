"""
nxtgen_savinda_pricing_calculator/api/offset_calculator.py

Uses only these DocTypes:
  - Offset Spec            (group: Specification | Finishing | Machine)
  - Offset Spec Cost Fact  (child table: cost_fact Link, is_primary Check)
  - Offset Spec Machine    (child table: machine Link)
  - Offset Machine         (machine master with cost parameters)
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
def get_specs(pricing_type="Offset"):
    """
    Checkbox list — Specification + Finishing groups only, NOT Machine.
    Filters by pricing_type: returns specs where pricing_type matches
    the current calculation type (Offset / Flexo) OR is set to Both.
    """
    specs = frappe.get_all(
        "Offset Spec",
        filters={
            "is_active": 1,
            "group": ["!=", "Machine"],
            "pricing_type": ["in", [pricing_type, "Both"]],
        },
        fields=["name", "spec_name", "group"],
        order_by="`group` asc, spec_name asc",
    )
    return [_enrich_spec(s) for s in specs]


@frappe.whitelist()
def get_machines(pricing_type="Offset"):
    """Dropdown — Machine group only, filtered by pricing_type."""
    specs = frappe.get_all(
        "Offset Spec",
        filters={
            "is_active": 1,
            "group": "Machine",
            "pricing_type": ["in", [pricing_type, "Both"]],
        },
        fields=["name", "spec_name", "group"],
        order_by="spec_name asc",
    )
    return [_enrich_spec(s) for s in specs]


def _enrich_spec(spec):
    doc = frappe.get_doc("Offset Spec", spec["name"])
    machines = []
    for row in (doc.machines or []):
        try:
            m = frappe.get_doc("Offset Machine", row.machine)
            machines.append({
                "machine":                     m.name,
                "is_printing_machine":         cint(m.is_printing_machine),
                "color_capacity":              cint(m.color_capacity or 1),
                "max_output_per_hour":         flt(m.max_output_per_hour),
                "can_run_parallel":            cint(m.can_run_parallel),
                "basic_setup_time":            flt(m.basic_setup_time),
                "customer_sample_setup_time":  flt(getattr(m, "customer_sample_setup_time", 0)),
                "additional_color_setup_time": flt(getattr(m, "additional_color_setup_time", 0)),
                "machine_cost_per_hour":       flt(m.machine_cost_per_hour),
                "qty_formula":                 (getattr(m, "qty_formula",  None) or "").strip(),
                "rate_formula":                (getattr(m, "rate_formula", None) or "").strip(),
            })
        except Exception:
            pass
    return {
        "name":                spec["name"],
        "spec_name":           spec["spec_name"],
        "group":               spec["group"],
        "operation":           doc.operation or "",
        "has_machine":         cint(doc.has_machine),
        "units":               doc.units or "Full sheet",
        "skip_machine_if_spec": doc.skip_machine_if_spec or "",
        "machines":            machines,
        "cost_facts": [
            {
                "cost_fact":  row.cost_fact,
                "is_primary": cint(row.is_primary),
                "master":     _get_cf_data(row.cost_fact),
            }
            for row in (doc.cost_facts or [])
        ],
    }


def _get_unit_qty(units, form, sheet):
    """Convert spec unit type to a calculated quantity."""
    if units == "Full sheet":
        return sheet.get("full_sheet_qty", 0)
    if units == "Cut sheet":
        return sheet.get("cut_sheet_qty", 0)
    if units == "Sqinch":
        cut_l = flt(form.get("cut_sheet_l", 0))
        cut_w = flt(form.get("cut_sheetw", 0)) or flt(form.get("cut_sheet_w", 0))
        return cut_l * cut_w * sheet.get("cut_sheet_qty", 0)
    if units == "Pcs":
        return flt(form.get("item_qty", 0))
    if units == "Impressions":
        cut_ups = sheet.get("cut_sheet_ups", 1) or 1
        return flt(form.get("item_qty", 0)) / cut_ups
    return 0


def _calc_spec_machine_cost(spec_name, m_data, machine_assignment, form, sheet,
                             item_qty, no_of_colors, units, machine_count_map):
    """Calculate machine cost for one spec, given a chosen machine and assignment params."""
    is_printing  = cint(m_data.get("is_printing_machine", 0))
    max_out      = flt(m_data.get("max_output_per_hour", 1)) or 1
    cost_ph      = flt(m_data.get("machine_cost_per_hour", 0))
    can_parallel = cint(m_data.get("can_run_parallel", 0))
    basic_st     = flt(m_data.get("basic_setup_time", 0))
    cs_st        = flt(m_data.get("customer_sample_setup_time", basic_st)) or basic_st
    add_c_st     = flt(m_data.get("additional_color_setup_time", 0))
    color_cap    = cint(m_data.get("color_capacity", 1)) or 1
    cycles       = max(cint(machine_assignment.get("cycles", 1)), 1)
    csc          = machine_assignment.get("customer_sample_colors", False)
    machine_name = m_data["machine"]
    qty_formula  = m_data.get("qty_formula",  "")
    rate_formula = m_data.get("rate_formula", "")

    unit_qty = _get_unit_qty(units, form, sheet)

    if is_printing:
        pass_count = math.ceil(no_of_colors / color_cap) if color_cap else 1
    else:
        ops_sharing = machine_count_map.get(machine_name, 1)
        pass_count  = 1 if can_parallel else ops_sharing

    time_per_pass  = unit_qty / max_out if max_out else 0
    production_hrs = time_per_pass * pass_count

    if is_printing:
        setup_t       = cs_st if csc else basic_st
        extra_c       = max(0, no_of_colors - 4)
        makeready_hrs = setup_t + (extra_c * add_c_st if extra_c > 0 else 0)
    else:
        makeready_hrs = basic_st * pass_count

    total_hrs = (makeready_hrs + production_hrs) * cycles

    # Build formula context — all variables available in qty/rate formulas
    formula_ctx = {
        "total_hrs":           total_hrs,
        "makeready_hrs":       makeready_hrs,
        "production_hrs":      production_hrs,
        "pass_count":          pass_count,
        "unit_qty":            unit_qty,
        "cycles":              cycles,
        "machine_cost_per_hour": cost_ph,
        "no_of_colors":        no_of_colors,
        "item_qty":            item_qty,
        "full_sheet_qty":      sheet.get("full_sheet_qty", 0),
        "cut_sheet_qty":       sheet.get("cut_sheet_qty",  0),
        "cut_sheet_ups":       sheet.get("cut_sheet_ups",  1),
        "cut_sheet_area":      flt(form.get("cut_sheet_l", 0)) * (flt(form.get("cut_sheetw", 0)) or flt(form.get("cut_sheet_w", 0))),
        "reel_area":           sheet.get("reel_area",   0),
        "reel_length":         sheet.get("reel_length", 0),
        "no_of_cuts":          cint(form.get("no_of_cuts", 1)),
        "no_of_ups":           cint(form.get("no_of_ups",  1)),
    }

    # Apply custom formulas if provided — override standard calculation
    req_qty = _safe_eval(qty_formula,  formula_ctx) if qty_formula  else total_hrs
    rate    = _safe_eval(rate_formula, formula_ctx) if rate_formula else cost_ph

    machine_cost = round(req_qty * rate, 2)

    row = {
        "section": "Spec", "spec_name": spec_name,
        "cost_fact": f"{spec_name} - Machine",
        "cost_group": "Production",
        "selected_item": machine_name, "selected_item_name": machine_name,
        "attribute_values": {
            "machine":         machine_name,
            "cycles":          cycles,
            "total_hrs":       round(total_hrs, 4),
            "makeready_hrs":   round(makeready_hrs, 4),
            "production_hrs":  round(production_hrs, 4),
            "pass_count":      pass_count,
            "req_qty":         round(req_qty, 4),
            "rate":            round(rate, 4),
        },
        "req_qty": round(req_qty, 4),
        "rate": round(rate, 4), "amount": machine_cost, "is_auto": False,
    }
    return row, machine_cost


@frappe.whitelist()
def calculate(payload):
    if isinstance(payload, str):
        payload = json.loads(payload)

    form           = payload.get("form", {})
    machine_spec   = payload.get("machine_spec")
    selected_specs = payload.get("selected_specs", [])

    # breakdown_qtys affects ONLY sheet calculation — item_qty remains the canonical total for formulas
    breakdown_qtys = [flt(q) for q in (form.get("breakdown_qtys") or []) if flt(q) > 0]

    pricing_type  = (form.get("pricing_type") or "Offset").strip()
    price_list    = _resolve_pl(form.get("price_list"), form.get("customer_name"))
    item_qty      = flt(form.get("item_qty") or 0)
    no_of_colors  = cint(form.get("no_of_colors") or 0)
    material_rate = flt(form.get("material_rate") or 0)

    # Route sheet calculation by pricing type
    if pricing_type == "Flexo":
        sheet = _calc_flexo(form)
        reel_area      = sheet.get("reel_area", 0)
        reel_length    = sheet.get("reel_length", 0)
        ups            = sheet.get("ups", 0)
        full_sheet_qty = 0
        cut_sheet_qty  = 0
        cut_sheet_area = 0
    else:
        sheet = _calc_sheet(form, breakdown_qtys or None)
        full_sheet_qty = sheet.get("full_sheet_qty", 0)
        cut_sheet_qty  = sheet.get("cut_sheet_qty",  0)
        cut_sheet_area = flt(form.get("cut_sheet_l", 0)) * flt(form.get("cut_sheet_w", 0))
        reel_area   = 0
        reel_length = 0
        ups         = 0

    cost_rows = []
    mat_total = prep_total = prod_total = 0.0

    # 1. Material (auto)
    auto_qty = reel_area if pricing_type == "Flexo" else full_sheet_qty
    if form.get("base_material") and material_rate and auto_qty:
        iname = frappe.db.get_value("Item", form["base_material"], "item_name") or form["base_material"]
        amt   = round(auto_qty * material_rate, 2)
        mat_total += amt
        label = "Paper / Board" if pricing_type == "Offset" else "Reel Material"
        cost_rows.append({
            "section": "Material", "spec_name": "Base Material",
            "cost_fact": label, "cost_group": "Material",
            "selected_item": form["base_material"], "selected_item_name": iname,
            "attribute_values": {}, "req_qty": round(auto_qty, 4),
            "rate": material_rate, "amount": amt, "is_auto": True,
        })

    # 2. Machine (auto)
    if machine_spec:
        rows, mt, pp, pr = _process_spec(
            machine_spec, form, sheet, item_qty, no_of_colors, material_rate,
            full_sheet_qty, cut_sheet_qty, cut_sheet_area, price_list,
            section="Machine", is_auto=True,
            reel_area=reel_area, reel_length=reel_length, ups=ups,
        )
        cost_rows.extend(rows)
        mat_total += mt; prep_total += pp; prod_total += pr

    # 3. Selected specs — build machine dependency map first
    all_selected_spec_names = [s.get("spec_name", "") for s in selected_specs]
    machine_count_map = {}
    for spec in selected_specs:
        m_name = (spec.get("machine_assignment") or {}).get("machine", "")
        if m_name:
            machine_count_map[m_name] = machine_count_map.get(m_name, 0) + 1

    for spec in selected_specs:
        rows, mt, pp, pr = _process_spec(
            spec, form, sheet, item_qty, no_of_colors, material_rate,
            full_sheet_qty, cut_sheet_qty, cut_sheet_area, price_list,
            section="Spec", is_auto=False,
            reel_area=reel_area, reel_length=reel_length, ups=ups,
            all_selected_spec_names=all_selected_spec_names,
            machine_count_map=machine_count_map,
        )
        cost_rows.extend(rows)
        mat_total += mt; prep_total += pp; prod_total += pr

    grand = mat_total + prep_total + prod_total
    pm    = flt(form.get("profit_margin", 0)) / 100
    uc    = grand / item_qty if item_qty else 0
    cfg   = _get_config()
    sscl  = uc * cfg["sscl_rate"] if form.get("tax_sscl") else 0
    qu    = (uc + sscl) * (1 + pm)
    vat   = qu * cfg["vat_rate"] if form.get("tax_vat") else 0
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
    doc.pricing_type  = form.get("pricing_type", "Offset")
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

    if form.get("pricing_type") == "Flexo":
        for field, key in [
            ("reel_width_mm",     "reel_width_mm"),
            ("product_width_mm",  "product_width_mm"),
            ("product_length_mm", "product_length_mm"),
            ("product_margin_mm", "product_margin_mm"),
            ("product_gap_mm",    "product_gap_mm"),
        ]:
            if hasattr(doc, field):
                setattr(doc, field, flt(form.get(key, 0)))
        for field, key in [
            ("flexo_ups",         "ups"),
            ("flexo_reel_length", "reel_length"),
            ("flexo_reel_area",   "reel_area"),
        ]:
            if hasattr(doc, field):
                setattr(doc, field, flt(sheet.get(key, 0)))

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
        cf = doc.append("cost_facts", {})
        cf.cost_fact      = row.get("cost_fact", "") if not row.get("is_auto") else ""
        cf.cost_group     = row.get("cost_group", "")
        cf.selected_item  = row.get("selected_item", "")
        cf.req_qty        = flt(row.get("req_qty", 0))
        cf.rate           = flt(row.get("rate", 0))
        cf.amount         = flt(row.get("amount", 0))
        cf.attribute_json = json.dumps(row.get("attribute_values", {}))
        if hasattr(cf, "uom"):
            cf.uom = row.get("uom", "")

    if pricing.get("unit_cost"):
        doc.unit_cost     = flt(pricing.get("unit_cost", 0))
    if pricing.get("sell_total"):
        doc.selling_price = flt(pricing.get("sell_total", 0))

    if doc_name and frappe.db.exists("Calculation Breakdown", doc_name):
        doc.save(ignore_permissions=True)
    else:
        doc.insert(ignore_permissions=True)

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
        "pricing_type":  doc.pricing_type or "Offset",
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
            "form": form, "machine_spec": None, "selected_specs": [],
            "calc_result": None}


# ── Helpers ───────────────────────────────────────────────────

def _get_cf_data(name):
    if not name:
        return {}
    try:
        doc = frappe.get_doc("Cost Fact", name)
        return {
            "name":         doc.name,
            "cost_group":   doc.cost_group or "",
            "calculation":  doc.calculation or "",
            "qty_formula":  (getattr(doc, "qty_formula",  None) or "").strip(),
            "rate_formula": (getattr(doc, "rate_formula", None) or "").strip(),
            "min_qty":      flt(doc.min_qty),
            "uom":      getattr(doc, "uom", "") or "",
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


@frappe.whitelist()
def get_inquiry_breakdowns(ref):
    """Fetch breakdown quantities from an Inquiry (Opportunity) by name or custom_subject."""
    if not ref:
        return []
    doc = None
    if frappe.db.exists("Opportunity", ref):
        doc = frappe.get_doc("Opportunity", ref)
    else:
        results = frappe.get_all("Opportunity", filters={"custom_subject": ref}, limit=1)
        if results:
            doc = frappe.get_doc("Opportunity", results[0].name)
    if not doc:
        return []
    out = []
    for row in (doc.custom_breakdown or []):
        qty = flt(getattr(row, "qty", 0))
        if qty > 0:
            out.append({"description": getattr(row, "description", "") or "", "qty": qty})
    return out


def _resolve_pl(price_list=None, customer=None):
    if price_list: return price_list
    if customer:
        pl = frappe.db.get_value("Customer", customer, "default_price_list")
        if pl: return pl
    return frappe.db.get_single_value("Selling Settings", "selling_price_list") or ""


def _get_config():
    """Return Costing Configuration values as a dict with decimal rates."""
    try:
        doc = frappe.get_cached_doc("Costing Configuration")
        rows = sorted(
            [
                (cint(r.max_colors), flt(r.setup_metrage), flt(r.wastage_percent) / 100.0)
                for r in (doc.flexo_wastage or [])
            ],
            key=lambda x: x[0],
        )
        return {
            "sscl_rate":             flt(doc.sscl_rate or 2.5) / 100.0,
            "vat_rate":              flt(doc.vat_rate or 18.0) / 100.0,
            "default_profit_margin": flt(doc.default_profit_margin or 15.0),
            "offset_wastage_pct":    flt(doc.offset_wastage_pct or 5.0) / 100.0,
            "offset_wastage_min":    cint(doc.offset_wastage_min) or 500,
            "flexo_wastage":         rows,
        }
    except Exception:
        return {
            "sscl_rate": 0.025, "vat_rate": 0.18, "default_profit_margin": 15.0,
            "offset_wastage_pct": 0.05, "offset_wastage_min": 500,
            "flexo_wastage": [
                (0, 5, 0.04), (1, 100, 0.06), (2, 100, 0.07),
                (3, 150, 0.08), (4, 150, 0.09), (5, 200, 0.10),
                (6, 200, 0.11), (99, 250, 0.12),
            ],
        }


@frappe.whitelist()
def get_flexo_foils():
    """Return all active Flexo Foil records for the Production Assignment dialog."""
    return frappe.get_all(
        "Flexo Foil",
        filters={"is_active": 1},
        fields=["name", "foil_name", "foil_code", "foil_group", "cost_per_sqm", "min_qty", "min_value"],
        order_by="foil_group asc, foil_name asc",
    )


@frappe.whitelist()
def get_offset_inks():
    """Return all active inks for the Production Assignment dialog."""
    return frappe.get_all(
        "Offset Ink",
        filters={"is_active": 1},
        fields=["ink_name", "price_per_kg", "consumption_per_sqinch", "min_qty", "min_value"],
        order_by="ink_name asc",
    )


def _calc_spec_ink_cost(spec_name, machine_assignment, form, sheet, cycles):
    """Calculate ink costs for all inks assigned to a printing spec.
    Formula: qty_kg = cut_sheet_area × cut_sheet_qty × consumption_per_sqinch × (pct/100) × cycles
    """
    inks = machine_assignment.get("inks", [])
    if not inks:
        return [], 0.0

    cut_sheet_area = flt(form.get("cut_sheet_l", 0)) * (flt(form.get("cut_sheetw", 0)) or flt(form.get("cut_sheet_w", 0)))
    cut_sheet_qty  = sheet.get("cut_sheet_qty", 0)

    rows = []
    total_cost = 0.0

    for ink_row in inks:
        ink_name = (ink_row.get("ink_name") or "").strip()
        ink_pct  = flt(ink_row.get("percentage", 100)) / 100.0
        if not ink_name:
            continue
        try:
            ink_doc = frappe.get_cached_doc("Offset Ink", ink_name)
            consumption  = flt(getattr(ink_doc, "consumption_per_sqinch", 0) or 0)
            price_per_kg = flt(ink_doc.price_per_kg or 0)
            min_qty_v    = flt(getattr(ink_doc, "min_qty",   0) or 0)
            min_val_v    = flt(getattr(ink_doc, "min_value", 0) or 0)
        except Exception:
            continue

        ink_qty  = cut_sheet_area * cut_sheet_qty * consumption * ink_pct * cycles
        ink_cost = round(ink_qty * price_per_kg, 2)

        if min_qty_v and ink_qty < min_qty_v:
            ink_qty  = min_qty_v
            ink_cost = round(ink_qty * price_per_kg, 2)
        if min_val_v and ink_cost < min_val_v:
            ink_cost = flt(min_val_v)

        rows.append({
            "spec_name":          spec_name,
            "cost_fact":          f"{spec_name} - Ink ({ink_name})",
            "cost_group":         "Production",
            "selected_item":      ink_name,
            "selected_item_name": ink_name,
            "attribute_values":   {"ink": ink_name, "percentage": flt(ink_row.get("percentage", 100))},
            "req_qty":            round(ink_qty, 8),
            "rate":               round(price_per_kg, 4),
            "amount":             ink_cost,
            "is_auto":            False,
            "uom":                "KG",
        })
        total_cost += ink_cost

    return rows, total_cost


@frappe.whitelist()
def get_costing_config():
    """Return config values as percentages for frontend use."""
    cfg = _get_config()
    return {
        "sscl_rate":             round(cfg["sscl_rate"] * 100, 4),
        "vat_rate":              round(cfg["vat_rate"] * 100, 4),
        "default_profit_margin": cfg["default_profit_margin"],
    }


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


def _calc_sheet(form, breakdown_qtys=None):
    no_cuts = max(cint(form.get("no_of_cuts")) or 1, 1)
    no_ups  = max(cint(form.get("no_of_ups"))  or 1, 1)
    cfg     = _get_config()
    cup     = max(no_ups // no_cuts, 1)

    qtys = [flt(q) for q in (breakdown_qtys or [])] if breakdown_qtys else [flt(form.get("item_qty") or 0)]
    qtys = [q for q in qtys if q > 0]

    if not qtys:
        return {k: 0 for k in ["cut_sheet_ups", "cut_sheet_qty", "wastage", "req_cut_sheets", "full_sheet_qty"]}

    total_cqty  = 0
    total_waste = 0
    total_rqty  = 0
    total_fqty  = 0

    for qty in qtys:
        cqty  = math.ceil(qty / cup)
        waste = max(math.ceil(cqty * cfg["offset_wastage_pct"]), cfg["offset_wastage_min"])
        rqty  = cqty + waste
        fqty  = math.ceil(rqty / no_cuts)
        total_cqty  += cqty
        total_waste += waste
        total_rqty  += rqty
        total_fqty  += fqty

    return {
        "cut_sheet_ups":  cup,
        "cut_sheet_qty":  total_cqty,
        "wastage":        total_waste,
        "req_cut_sheets": total_rqty,
        "full_sheet_qty": total_fqty,
    }


def _calc_flexo(form):
    reel_width   = flt(form.get("reel_width_mm", 0))
    prod_w       = flt(form.get("product_width_mm", 0))
    prod_l       = flt(form.get("product_length_mm", 0))
    margin       = flt(form.get("product_margin_mm", 4))
    gap          = flt(form.get("product_gap_mm", 3))
    no_of_colors = cint(form.get("no_of_colors", 0))
    item_qty     = flt(form.get("item_qty", 0))

    if not item_qty or not reel_width or not prod_w or not prod_l:
        return {k: 0 for k in [
            "ups", "stickers_per_reel", "reel_length", "reel_area",
            "reel_area_net", "wastage_area", "printable_margin",
            "material_wastage_width", "setup_metrage", "wastage_pct",
        ]}

    printable_margin = 10 if no_of_colors == 0 else 24
    ups = max(int((reel_width - printable_margin) / (prod_w + margin)), 1)
    material_wastage_width = reel_width - (ups * (prod_w + margin) + printable_margin)
    stickers_per_reel = item_qty / ups
    label_pitch       = prod_l + gap
    reel_length_calc  = stickers_per_reel * (label_pitch / 1000)
    # Allow manual reel length override from form (reel_length_m field)
    reel_length_override = flt(form.get("reel_length_m", 0))
    reel_length  = reel_length_override if reel_length_override > 0 else reel_length_calc
    reel_width_m = reel_width / 1000
    reel_area_net = reel_length * reel_width_m

    _cfg = _get_config()
    _wtable = _cfg["flexo_wastage"]
    setup_m = _wtable[-1][1] if _wtable else 250
    wastage_pct = _wtable[-1][2] if _wtable else 0.12
    for colors, sm, wp in _wtable:
        if no_of_colors <= colors:
            setup_m = sm; wastage_pct = wp
            break

    wastage_area = setup_m * reel_width_m + reel_area_net * wastage_pct
    reel_area    = reel_area_net + wastage_area

    return {
        "ups":                    ups,
        "stickers_per_reel":      round(stickers_per_reel, 2),
        "reel_length":            round(reel_length, 4),
        "reel_area_net":          round(reel_area_net, 4),
        "wastage_area":           round(wastage_area, 4),
        "reel_area":              round(reel_area, 4),
        "printable_margin":       printable_margin,
        "material_wastage_width": round(material_wastage_width, 2),
        "setup_metrage":          setup_m,
        "wastage_pct":            wastage_pct,
    }


def _calc_spec_foil_cost(spec_name, machine_assignment, reel_area):
    """Calculate Flexo foil costs for all foils assigned to a Flexo spec.
    Formula per foil:
      qty_sqm = reel_area × (pct/100) × 1.25 (25% wastage)
      cost    = qty_sqm × cost_per_sqm
    Cold Foil Varnish is auto-added for any COLD foils.
    """
    foils = machine_assignment.get("foils", [])
    if not foils or not reel_area:
        return [], 0.0

    rows = []
    total_cost = 0.0
    has_cold = False

    for foil_row in foils:
        foil_key  = (foil_row.get("foil_name") or "").strip()
        foil_grp  = (foil_row.get("foil_group") or "").upper()
        foil_pct  = flt(foil_row.get("percentage", 100)) / 100.0
        if not foil_key:
            continue
        try:
            foil_doc     = frappe.get_cached_doc("Flexo Foil", foil_key)
            cost_per_sqm = flt(foil_doc.cost_per_sqm or 0)
            min_qty_v    = flt(getattr(foil_doc, "min_qty",   0) or 0)
            min_val_v    = flt(getattr(foil_doc, "min_value", 0) or 0)
        except Exception:
            continue

        qty  = reel_area * foil_pct * 1.25   # 25% wastage
        cost = round(qty * cost_per_sqm, 2)
        if min_qty_v and qty  < min_qty_v: qty  = min_qty_v; cost = round(qty * cost_per_sqm, 2)
        if min_val_v and cost < min_val_v: cost = flt(min_val_v)

        rows.append({
            "spec_name":          spec_name,
            "cost_fact":          f"{spec_name} - Foil ({foil_key})",
            "cost_group":         "Material",
            "selected_item":      foil_key,
            "selected_item_name": foil_key,
            "attribute_values":   {"foil": foil_key, "type": foil_grp, "percentage": flt(foil_row.get("percentage", 100))},
            "req_qty":            round(qty, 6),
            "rate":               round(cost_per_sqm, 4),
            "amount":             cost,
            "is_auto":            False,
            "uom":                "M2",
        })
        total_cost += cost
        if foil_grp == "COLD":
            has_cold = True

    # Auto-add Cold Foil Varnish if any COLD foils are present
    if has_cold:
        try:
            ink_doc = frappe.get_cached_doc("Offset Ink", "Cold Foiling Varnish")
            consumption = flt(getattr(ink_doc, "consumption_per_sqm", 0.001) or 0.001)
            price_per_kg = flt(ink_doc.price_per_kg or 10520)
            min_qty_v    = flt(getattr(ink_doc, "min_qty", 0.2) or 0.2)
            varnish_qty  = reel_area * consumption
            if varnish_qty < min_qty_v: varnish_qty = min_qty_v
            varnish_cost = round(varnish_qty * price_per_kg, 2)
            rows.append({
                "spec_name":          spec_name,
                "cost_fact":          f"{spec_name} - Cold Foil Varnish",
                "cost_group":         "Material",
                "selected_item":      "Cold Foiling Varnish",
                "selected_item_name": "Cold Foiling Varnish",
                "attribute_values":   {},
                "req_qty":            round(varnish_qty, 6),
                "rate":               round(price_per_kg, 4),
                "amount":             varnish_cost,
                "is_auto":            True,
                "uom":                "KG",
            })
            total_cost += varnish_cost
        except Exception:
            pass

    return rows, total_cost


def _calc_flexo_ink_cost(spec_name, machine_assignment, reel_area):
    """Calculate Flexo ink costs for assigned inks.
    Formula: qty_kg = consumption_per_sqm × reel_area; cost = qty_kg × price_per_kg
    """
    inks = machine_assignment.get("inks", [])
    if not inks or not reel_area:
        return [], 0.0

    rows = []
    total_cost = 0.0

    for ink_row in inks:
        ink_name = (ink_row.get("ink_name") or "").strip()
        if not ink_name:
            continue
        try:
            ink_doc      = frappe.get_cached_doc("Offset Ink", ink_name)
            consumption  = flt(getattr(ink_doc, "consumption_per_sqm", 0) or 0)
            price_per_kg = flt(ink_doc.price_per_kg or 0)
            min_qty_v    = flt(getattr(ink_doc, "min_qty",   0) or 0)
            min_val_v    = flt(getattr(ink_doc, "min_value", 0) or 0)
        except Exception:
            continue

        qty  = reel_area * consumption
        cost = round(qty * price_per_kg, 2)
        if min_qty_v and qty  < min_qty_v: qty  = min_qty_v; cost = round(qty * price_per_kg, 2)
        if min_val_v and cost < min_val_v: cost = flt(min_val_v)

        rows.append({
            "spec_name":          spec_name,
            "cost_fact":          f"{spec_name} - Ink ({ink_name})",
            "cost_group":         "Material",
            "selected_item":      ink_name,
            "selected_item_name": ink_name,
            "attribute_values":   {"ink": ink_name},
            "req_qty":            round(qty, 8),
            "rate":               round(price_per_kg, 4),
            "amount":             cost,
            "is_auto":            False,
            "uom":                "KG",
        })
        total_cost += cost

    return rows, total_cost


def _process_spec(spec, form, sheet, item_qty, no_of_colors, material_rate,
                  full_sheet_qty, cut_sheet_qty, cut_sheet_area, price_list,
                  section="Spec", is_auto=False,
                  reel_area=0, reel_length=0, ups=0,
                  all_selected_spec_names=None, machine_count_map=None):
    all_rows = []; mat = prep = prod = 0.0

    # Build foil count context from machine_assignment (used in Flexo formulas)
    machine_assignment = spec.get("machine_assignment") or {}
    foils_list = machine_assignment.get("foils", []) or []
    cold_foil_count = sum(1 for f in foils_list if (f.get("foil_group") or "").upper() == "COLD")
    hot_foil_count  = sum(1 for f in foils_list if (f.get("foil_group") or "").upper() == "HOT")
    foil_count      = cold_foil_count + hot_foil_count
    # Machine capacity for UV Machine conditional check
    machine_name_ctx = machine_assignment.get("machine", "")
    machine_capacity_ctx = 8  # default
    for m in spec.get("machines", []):
        if m.get("machine") == machine_name_ctx:
            machine_capacity_ctx = cint(m.get("color_capacity", 8)) or 8
            break
    extra_ctx = {
        "foil_count":       foil_count,
        "cold_foil_count":  cold_foil_count,
        "hot_foil_count":   hot_foil_count,
        "machine_capacity": machine_capacity_ctx,
    }

    for cf_row in spec.get("cost_facts", []):
        row, mt, pp, pr = _build_row(
            cf_row, spec.get("spec_name", ""), form, sheet,
            item_qty, no_of_colors, material_rate,
            full_sheet_qty, cut_sheet_qty, cut_sheet_area, price_list,
            reel_area=reel_area, reel_length=reel_length, ups=ups,
            extra_ctx=extra_ctx,
        )
        row["section"] = section
        row["is_auto"] = is_auto
        all_rows.append(row)
        mat += mt; prep += pp; prod += pr

    # Machine cost block (machine_assignment already resolved above for foil ctx)
    if spec.get("has_machine") and machine_assignment.get("machine"):
        machine_name = machine_assignment["machine"]
        skip_spec    = spec.get("skip_machine_if_spec", "")
        skip = skip_spec and all_selected_spec_names and skip_spec in all_selected_spec_names
        if not skip:
            m_data = next((m for m in spec.get("machines", []) if m["machine"] == machine_name), None)
            if m_data:
                machine_row, machine_cost = _calc_spec_machine_cost(
                    spec.get("spec_name", ""), m_data, machine_assignment,
                    form, sheet, item_qty, no_of_colors,
                    spec.get("units", "Full sheet"), machine_count_map or {},
                )
                machine_row["section"] = section
                all_rows.append(machine_row)
                prod += machine_cost

                # Offset ink cost — only for offset printing machines
                pricing_t = (form.get("pricing_type") or "Offset").strip()
                if pricing_t == "Offset" and cint(m_data.get("is_printing_machine")) and machine_assignment.get("inks"):
                    cycles = max(cint(machine_assignment.get("cycles", 1)), 1)
                    ink_rows, ink_cost = _calc_spec_ink_cost(
                        spec.get("spec_name", ""), machine_assignment, form, sheet, cycles
                    )
                    for ir in ink_rows:
                        ir["section"] = section
                        all_rows.append(ir)
                    prod += ink_cost

                # Flexo foil + ink costs
                if pricing_t == "Flexo":
                    if machine_assignment.get("foils"):
                        foil_rows, foil_cost = _calc_spec_foil_cost(
                            spec.get("spec_name", ""), machine_assignment, reel_area
                        )
                        for fr in foil_rows:
                            fr["section"] = section
                            all_rows.append(fr)
                        mat += foil_cost

                    if machine_assignment.get("inks"):
                        fx_ink_rows, fx_ink_cost = _calc_flexo_ink_cost(
                            spec.get("spec_name", ""), machine_assignment, reel_area
                        )
                        for ir in fx_ink_rows:
                            ir["section"] = section
                            all_rows.append(ir)
                        mat += fx_ink_cost

    return all_rows, mat, prep, prod


class _AttrDict:
    """Wraps attribute_values dict so formulas can use dot notation: attr.length"""
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
    Blocks dangerous keywords and runs with no built-ins.
    Returns float result, or 0.0 on error.
    """
    if not formula or not str(formula).strip():
        return 0.0

    cleaned = str(formula).strip()
    _blocked = ["__", "import", "exec", "eval", "open", "os.",
                "sys.", "getattr", "setattr", "globals", "locals", "builtins"]
    for bad in _blocked:
        if bad in cleaned:
            frappe.log_error(
                title="Cost Fact Formula Security Block",
                message=f"Formula blocked: '{cleaned}' contains '{bad}'"
            )
            return 0.0

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
               cut_sheet_area, price_list='Standard Selling',
               reel_area=0, reel_length=0, ups=0,
               extra_ctx=None):
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

    eval_ctx = {
        "full_sheet_qty":  full_sheet_qty,
        "cut_sheet_qty":   cut_sheet_qty,
        "cut_sheet_area":  cut_sheet_area,
        "no_of_cuts":      cint(form.get("no_of_cuts", 1)),
        "no_of_ups":       cint(form.get("no_of_ups",  1)),
        "cut_sheet_ups":   sheet.get("cut_sheet_ups",  1),
        "reel_area":       reel_area,
        "reel_length":     reel_length,
        "ups":             ups,
        "reel_width_mm":     flt(form.get("reel_width_mm", 0)),
        "product_width_mm":  flt(form.get("product_width_mm", 0)),
        "product_length_mm": flt(form.get("product_length_mm", 0)),
        "product_margin_mm": flt(form.get("product_margin_mm", 0)),
        "product_gap_mm":    flt(form.get("product_gap_mm", 0)),
        "no_of_colors":    no_of_colors,
        "item_qty":        item_qty,
        "item_rate":       item_rate,
        "fix_rate":        fix_rate,
        "min_rate":        min_rate,
        "min_qty":         min_amt,
        "material_rate":   material_rate,
        "attr":            _AttrDict(attr),
        # Flexo foil/ink variables (populated from machine_assignment in _process_spec)
        "foil_count":      0,
        "cold_foil_count": 0,
        "hot_foil_count":  0,
        "plate_count":     cint(form.get("plate_count", 0)),
        "machine_capacity": cint(form.get("machine_capacity", 8)),
    }
    if extra_ctx:
        eval_ctx.update(extra_ctx)
    for k, v in attr.items():
        try:
            eval_ctx[str(k)] = float(v)
        except (TypeError, ValueError):
            eval_ctx[str(k)] = v

    rq = flt(cf_row.get("req_qty", 0))
    if qty_formula and (not rq or is_primary):
        rq = _safe_eval(qty_formula, eval_ctx)
    elif not rq or is_primary:
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

    rate = 0.0
    if rate_formula:
        rate = _safe_eval(rate_formula, eval_ctx)
    elif user_rate:
        rate = user_rate
    elif sel_item:
        rate = item_rate
    elif not cf.get("items"):
        rate = material_rate

    amount = round(rq * rate, 2)
    if min_amt and amount < min_amt:
        amount = flt(min_amt)

    sel_name = item_row["item_name"] if item_row else (
        frappe.db.get_value("Item", sel_item, "item_name") or sel_item if sel_item else ""
    )

    grp = grp_str.lower()
    return {
        "spec_name": spec_name, "cost_fact": cf_name, "cost_group": grp_str,
        "selected_item": sel_item, "selected_item_name": sel_name,
        "attribute_values": attr, "req_qty": round(rq, 4),
        "rate": round(rate, 4), "amount": amount,
        "uom": cf.get("uom", ""),
        "is_auto": False, "section": "Spec",
    }, (amount if grp=="material" else 0), (amount if grp=="preparation" else 0), (amount if grp not in ("material","preparation") else 0)


@frappe.whitelist()
def sync_cost_item_unit_cost(calculation_breakdown):
    """
    Called after calculator saves a Calculation Breakdown.
    Finds all Cost Item Calculation rows linking this CB,
    then updates the parent Cost Item's unit_cost (sum of all linked CBs).
    """
    if not calculation_breakdown:
        return {"updated": 0}

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

    for ci_name in parents:
        try:
            uc = frappe.db.get_value("cost Item", ci_name, "unit_cost")
            if not uc:
                continue
            cs_rows = frappe.db.get_all(
                "Cost Sheet Items",
                filters={"item": ci_name},
                fields=["name", "parent", "qty", "sscl", "vat"],
            )
            _cfg = _get_config()
            _sscl_rate = _cfg["sscl_rate"]
            _vat_rate  = _cfg["vat_rate"]
            try:
                cb_doc  = frappe.get_cached_doc("Calculation Breakdown", calculation_breakdown)
                cb_sscl = cint(cb_doc.tax_sscl)
                cb_vat  = cint(cb_doc.tax_vat)
                for row in cs_rows:
                    frappe.db.set_value("Cost Sheet Items", row["name"], {
                        "sscl": cb_sscl,
                        "vat":  cb_vat,
                    }, update_modified=False)
                    row["sscl"] = cb_sscl
                    row["vat"]  = cb_vat
            except Exception:
                pass
            # Derive per-unit selling price and margin from CB
            try:
                cb_vals = frappe.db.get_value(
                    "Calculation Breakdown", calculation_breakdown,
                    ["selling_price", "item_qty", "profit_margin"],
                    as_dict=True,
                ) or {}
                cb_sell_total   = flt(cb_vals.get("selling_price", 0))
                cb_item_qty     = flt(cb_vals.get("item_qty", 0)) or 1
                cb_profit_margin = flt(cb_vals.get("profit_margin", 0))
                sell_unit_cb    = round(cb_sell_total / cb_item_qty, 4)
            except Exception:
                sell_unit_cb     = 0
                cb_profit_margin = 0

            for row in cs_rows:
                unit      = frappe.utils.flt(uc)
                qty       = frappe.utils.flt(row.get("qty", 0))
                # If CB already has a valid sell_unit (with margin), use it;
                # otherwise fall back to unit_cost + SSCL + VAT only
                if sell_unit_cb and sell_unit_cb > unit:
                    sell_unit = sell_unit_cb
                else:
                    apply_sscl = cint(row.get("sscl", 0))
                    apply_vat  = cint(row.get("vat", 0))
                    sscl_amt   = unit * _sscl_rate if apply_sscl else 0
                    vat_base   = unit + sscl_amt
                    vat_amt    = vat_base * _vat_rate if apply_vat else 0
                    sell_unit  = round(unit + sscl_amt + vat_amt, 4)
                frappe.db.set_value("Cost Sheet Items", row["name"], {
                    "unit_price":         round(unit, 4),
                    "profit_margin":      cb_profit_margin,
                    "ammount":            round(qty * unit, 2),
                    "selling_unit_price": sell_unit,
                    "selling_ammount":    round(qty * sell_unit, 2),
                }, update_modified=False)
        except Exception as e:
            frappe.log_error(title="sync_cost_sheet_items error", message=str(e))

    frappe.db.commit()
    return {"updated": updated}


@frappe.whitelist()
def calculate_qty_break(calculation_breakdown, qty, profit_margin=None, tax_sscl=None, tax_vat=None):
    """
    Re-run calculation with a different item_qty only.
    All other params (material, machine, specs) come from the stored ui_state.
    """
    if not calculation_breakdown:
        return {"error": "No calculation breakdown specified"}

    doc = frappe.get_doc("Calculation Breakdown", calculation_breakdown)
    if not (hasattr(doc, "ui_state") and doc.ui_state):
        return {"error": "No ui_state found on this Calculation Breakdown"}

    try:
        state = json.loads(doc.ui_state)
    except Exception:
        return {"error": "Could not parse ui_state"}

    form = state.get("form", {})
    form["item_qty"] = flt(qty)

    if profit_margin is not None:
        form["profit_margin"] = flt(profit_margin)
    if tax_sscl is not None:
        form["tax_sscl"] = tax_sscl
    if tax_vat is not None:
        form["tax_vat"] = tax_vat

    payload = {
        "form":           form,
        "machine_spec":   state.get("machine_spec"),
        "selected_specs": state.get("selected_specs", []),
    }

    try:
        result  = calculate(json.dumps(payload))
        pricing = result.get("pricing", {})
        return {
            "qty":           flt(qty),
            "unit_cost":     pricing.get("unit_cost", 0),
            "sell_unit":     pricing.get("sell_unit", 0),
            "sell_total":    pricing.get("sell_total", 0),
            "grand_total":   result.get("group_totals", {}).get("grand", 0),
            "profit_margin": form.get("profit_margin", 0),
        }
    except Exception as e:
        frappe.log_error(title="calculate_qty_break error", message=str(e))
        return {"error": str(e)}
