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
                "allow_ink_assignment":        cint(getattr(m, "allow_ink_assignment", 0)),
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
        "adds_colors":         cint(getattr(doc, "adds_colors", 0)),
        "allow_foil_assignment": cint(getattr(doc, "allow_foil_assignment", 0)),
        "flexo_wastage_overwrite": cint(getattr(doc, "flexo_wastage_overwrite", 0)),
        "flexo_wastage_pct":     flt(getattr(doc, "flexo_wastage_pct", 0)),
        "parent_spec":         getattr(doc, "parent_spec", "") or "",
        "auto_select":         cint(getattr(doc, "auto_select", 0)),
        "units":               doc.units or "Full sheet",
        "skip_machine_if_spec": doc.skip_machine_if_spec or "",
        "skip_machine_if_machine":  getattr(doc, "skip_machine_if_machine", "") or "",
        "skip_machine_if_printing": cint(getattr(doc, "skip_machine_if_printing", 0)),
        "machines":            machines,
        "cost_facts": [
            {
                "cost_fact":     row.cost_fact,
                "is_primary":    cint(row.is_primary),
                "manual_select": cint(getattr(row, "manual_select", 0)),
                "master":        _get_cf_data(row.cost_fact),
            }
            for row in (doc.cost_facts or [])
        ],
        "allowed_inks": [
            row.ink_name
            for row in (getattr(doc, "allowed_inks", None) or [])
            if row.ink_name
        ],
    }


def _get_unit_qty(units, form, sheet):
    """Convert spec unit type to a calculated quantity.
    Cut sheet / Sqinch use req_cut_sheets (net + wastage) because every sheet — including
    wastage — runs through the press or finishing operation.
    Full sheet is already correct: full_sheet_qty = ceil(req_cut_sheets / no_cuts).
    """
    if units == "Full sheet":
        return sheet.get("full_sheet_qty", 0)
    if units == "Cut sheet":
        return sheet.get("req_cut_sheets", 0) or sheet.get("cut_sheet_qty", 0)
    if units == "Sqinch":
        cut_l = flt(form.get("cut_sheet_l", 0))
        cut_w = flt(form.get("cut_sheetw", 0)) or flt(form.get("cut_sheet_w", 0))
        total_sheets = sheet.get("req_cut_sheets", 0) or sheet.get("cut_sheet_qty", 0)
        return cut_l * cut_w * total_sheets
    if units == "Pcs":
        return flt(form.get("item_qty", 0))
    if units == "Impressions":
        cut_ups = sheet.get("cut_sheet_ups", 1) or 1
        return flt(form.get("item_qty", 0)) / cut_ups
    return 0


def _calc_spec_machine_cost(spec_name, m_data, machine_assignment, form, sheet,
                             item_qty, no_of_colors, units, machine_count_map,
                             reel_area=0):
    """Calculate machine cost for one spec.
    Returns (list_of_rows, total_cost).
    Flexo printing machines return two rows: Printing Setup + Printing Run (Excel formula).
    All other machines return a single row.
    """
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
    pricing_t    = (form.get("pricing_type") or "Offset").strip()

    # Foil counts from this machine's own assignment (used in Flexo printing formula)
    foils_list      = machine_assignment.get("foils", []) or []
    cold_foil_count = sum(1 for f in foils_list if (f.get("foil_group") or "").upper() == "COLD")
    hot_foil_count  = sum(1 for f in foils_list if (f.get("foil_group") or "").upper() == "HOT")
    foil_count      = cold_foil_count + hot_foil_count

    # ── Flexo printing machine: Excel formula (Setup + Run as separate rows) ──────────────
    if pricing_t == "Flexo" and is_printing and not qty_formula:
        run_div    = 600 if cold_foil_count > 0 else 750
        setup_hrs  = no_of_colors / 3.0
        run_hrs    = (reel_area / run_div) if (run_div and reel_area) else 0.0
        setup_cost = round(setup_hrs * cost_ph, 2)
        run_cost   = round(run_hrs   * cost_ph, 2)
        total_cost = setup_cost + run_cost
        rows = [
            {
                "spec_name":          spec_name,
                "cost_fact":          f"{spec_name} - Printing Setup",
                "cost_group":         "Production",
                "selected_item":      machine_name,
                "selected_item_name": machine_name,
                "attribute_values":   {"machine": machine_name, "setup_hrs": round(setup_hrs, 4)},
                "req_qty":            round(setup_hrs, 4),
                "rate":               round(cost_ph, 4),
                "amount":             setup_cost,
                "is_auto":            False,
            },
            {
                "spec_name":          spec_name,
                "cost_fact":          f"{spec_name} - Printing Run",
                "cost_group":         "Production",
                "selected_item":      machine_name,
                "selected_item_name": machine_name,
                "attribute_values":   {"machine": machine_name, "run_hrs": round(run_hrs, 4), "divisor": run_div},
                "req_qty":            round(run_hrs, 4),
                "rate":               round(cost_ph, 4),
                "amount":             run_cost,
                "is_auto":            False,
            },
        ]
        return rows, total_cost
    # ─────────────────────────────────────────────────────────────────────────────────────

    if is_printing:
        # Offset printing press: throughput in cut sheets/hr (req_cut_sheets includes wastage)
        unit_qty   = sheet.get("req_cut_sheets", 0) or sheet.get("cut_sheet_qty", 0)
        pass_count = math.ceil(no_of_colors / color_cap) if color_cap else 1
    else:
        unit_qty    = _get_unit_qty(units, form, sheet)
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

    formula_ctx = {
        "total_hrs":             total_hrs,
        "makeready_hrs":         makeready_hrs,
        "production_hrs":        production_hrs,
        "pass_count":            pass_count,
        "unit_qty":              unit_qty,
        "cycles":                cycles,
        "machine_cost_per_hour": cost_ph,
        "no_of_colors":          no_of_colors,
        "item_qty":              item_qty,
        "full_sheet_qty":        sheet.get("full_sheet_qty",  0),
        "cut_sheet_qty":         sheet.get("cut_sheet_qty",   0),
        "req_cut_sheets":        sheet.get("req_cut_sheets",  sheet.get("cut_sheet_qty", 0)),
        "cut_sheet_ups":         sheet.get("cut_sheet_ups",   1),
        "cut_sheet_area":        flt(form.get("cut_sheet_l", 0)) * (flt(form.get("cut_sheetw", 0)) or flt(form.get("cut_sheet_w", 0))),
        "reel_area":             reel_area or sheet.get("reel_area", 0),
        "reel_length":           sheet.get("reel_length", 0),
        "no_of_cuts":            cint(form.get("no_of_cuts", 1)),
        "no_of_ups":             cint(form.get("no_of_ups",  1)),
        "foil_count":            foil_count,
        "cold_foil_count":       cold_foil_count,
        "hot_foil_count":        hot_foil_count,
    }

    req_qty      = _safe_eval(qty_formula,  formula_ctx) if qty_formula  else total_hrs
    rate         = _safe_eval(rate_formula, formula_ctx) if rate_formula else cost_ph
    machine_cost = round(req_qty * rate, 2)

    row = {
        "spec_name":          spec_name,
        "cost_fact":          f"{spec_name} - Machine",
        "cost_group":         "Production",
        "selected_item":      machine_name,
        "selected_item_name": machine_name,
        "attribute_values": {
            "machine":        machine_name,
            "cycles":         cycles,
            "total_hrs":      round(total_hrs, 4),
            "makeready_hrs":  round(makeready_hrs, 4),
            "production_hrs": round(production_hrs, 4),
            "pass_count":     pass_count,
            "req_qty":        round(req_qty, 4),
            "rate":           round(rate, 4),
        },
        "req_qty": round(req_qty, 4),
        "rate":    round(rate, 4),
        "amount":  machine_cost,
        "is_auto": False,
    }
    return [row], machine_cost


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
    material_rate = flt(form.get("material_rate") or 0)

    # Effective colors = base No of Colors + colors added by selected specs (adds_colors).
    # Write it back to the form so ALL downstream reads (sheet wastage, printing formula,
    # plates) use the total. Base specs default adds_colors=0 → no change to existing calcs.
    base_colors   = cint(form.get("no_of_colors") or 0)
    added_colors  = sum(cint(s.get("adds_colors", 0)) for s in (selected_specs or []))
    no_of_colors  = base_colors + added_colors
    form["no_of_colors"] = no_of_colors

    # Flexo Wastage Overwrite: any selected/printing spec can tick "Wastage
    # Overwrite" and set a %. When one or more do, the HIGHEST % is used as the
    # effective Flexo wastage — replacing BOTH the reel material wastage (from
    # the Costing Configuration flexo table) AND the default 25% foil wastage.
    # When none are ticked, the config table % / 25% foil default apply.
    flexo_wastage_override = None
    if pricing_type == "Flexo":
        _ovr = [
            flt(s.get("flexo_wastage_pct", 0))
            for s in ((selected_specs or []) + ([machine_spec] if machine_spec else []))
            if cint(s.get("flexo_wastage_overwrite"))
        ]
        if _ovr:
            flexo_wastage_override = max(_ovr)
    foil_wastage_frac = (flt(flexo_wastage_override) / 100.0) if flexo_wastage_override is not None else 0.25

    # Manual overrides typed into the right-panel requirement cards. Only the wastage figure
    # is editable; changing it cascades to the dependent totals (full sheet qty / total reel
    # area) so the whole cost recalculates from the edited wastage.
    sheet_overrides = payload.get("sheet_overrides") or {}

    # Route sheet calculation by pricing type
    if pricing_type == "Flexo":
        sheet = _calc_flexo(form, wastage_override_pct=flexo_wastage_override)
        _wa = sheet_overrides.get("wastage_area")
        if _wa not in (None, ""):
            sheet["wastage_area"] = flt(_wa)
            sheet["reel_area"] = round(flt(sheet.get("reel_area_net", 0)) + flt(_wa), 4)
        reel_area      = sheet.get("reel_area", 0)
        reel_length    = sheet.get("reel_length", 0)
        ups            = sheet.get("ups", 0)
        full_sheet_qty = 0
        cut_sheet_qty  = 0
        cut_sheet_area = 0
    else:
        sheet = _calc_sheet(form, breakdown_qtys or None)
        _w = sheet_overrides.get("wastage")
        if _w not in (None, ""):
            _no_cuts = max(cint(form.get("no_of_cuts")) or 1, 1)
            sheet["wastage"] = flt(_w)
            sheet["req_cut_sheets"] = flt(sheet.get("cut_sheet_qty", 0)) + flt(_w)
            sheet["full_sheet_qty"] = math.ceil(sheet["req_cut_sheets"] / _no_cuts) if sheet["req_cut_sheets"] else 0
        full_sheet_qty = sheet.get("full_sheet_qty", 0)
        cut_sheet_qty  = sheet.get("cut_sheet_qty",  0)
        cut_sheet_area = flt(form.get("cut_sheet_l", 0)) * flt(form.get("cut_sheet_w", 0))
        reel_area   = 0
        reel_length = 0
        ups         = 0

    cost_rows = []
    mat_total = prep_total = prod_total = out_total = 0.0

    # 1. Material (auto)
    auto_qty      = reel_area if pricing_type == "Flexo" else full_sheet_qty
    # Offset base material is bought in whole sheets → always round the qty UP to
    # the next integer. Flexo material is a continuous reel area (m²) → keep as-is.
    mat_qty       = math.ceil(auto_qty) if (pricing_type != "Flexo" and auto_qty) else auto_qty
    material_type = (form.get("material_type") or "Existing").strip()
    label         = "Paper / Board" if pricing_type == "Offset" else "Reel Material"
    if material_type == "Custom":
        custom_name = (form.get("custom_material_name") or "").strip()
        if custom_name and material_rate and auto_qty:
            amt = round(mat_qty * material_rate, 2)
            mat_total += amt
            cost_rows.append({
                "section": "Material", "spec_name": "Base Material",
                "cost_fact": label, "cost_group": "Material",
                "selected_item": "", "selected_item_name": custom_name,
                "attribute_values": {}, "req_qty": round(mat_qty, 4),
                "rate": material_rate, "amount": amt, "is_auto": True,
            })
    elif form.get("base_material") and material_rate and auto_qty:
        iname = frappe.db.get_value("Item", form["base_material"], "item_name") or form["base_material"]
        amt   = round(mat_qty * material_rate, 2)
        mat_total += amt
        cost_rows.append({
            "section": "Material", "spec_name": "Base Material",
            "cost_fact": label, "cost_group": "Material",
            "selected_item": form["base_material"], "selected_item_name": iname,
            "attribute_values": {}, "req_qty": round(mat_qty, 4),
            "rate": material_rate, "amount": amt, "is_auto": True,
        })

    # 1b. Plates (Flexo) — plate count = effective colors, unless the user set it
    # manually (plate_count_manual); manual value is never overwritten by color changes.
    if pricing_type == "Flexo":
        plate_price = flt(form.get("plate_price") or 0)
        if form.get("plate_count_manual"):
            plate_count = cint(form.get("plate_count") or 0)
        else:
            plate_count = no_of_colors
        if plate_price and plate_count:
            plate_cost = round(plate_count * plate_price, 2)
            prep_total += plate_cost
            cost_rows.append({
                "section": "Preparation", "spec_name": "Plates",
                "cost_fact": "Plates", "cost_group": "Preparation",
                "selected_item": "",
                "selected_item_name": f"Plates ({plate_count} colors)",
                "attribute_values": {"plate_count": plate_count, "plate_price": plate_price},
                "req_qty": plate_count, "rate": plate_price,
                "amount": plate_cost, "is_auto": True,
            })

    # 2. Machine (auto) — flexo_ctx not yet computed here; will pass empty dict (machine spec is Offset-only)
    if machine_spec:
        rows, mt, pp, pr = _process_spec(
            machine_spec, form, sheet, item_qty, no_of_colors, material_rate,
            full_sheet_qty, cut_sheet_qty, cut_sheet_area, price_list,
            section="Machine", is_auto=True,
            reel_area=reel_area, reel_length=reel_length, ups=ups,
            foil_wastage_frac=foil_wastage_frac,
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

    # Flexo: pre-compute global foil counts + printing machine rate so ALL specs can use them
    flexo_ctx = {}
    if pricing_type == "Flexo":
        g_foil = g_cold = g_hot = 0
        g_print_rate = 0.0
        g_print_cap  = 8
        for s in selected_specs:
            ma = s.get("machine_assignment") or {}
            # Foils may live on a dedicated foil spec (no machine) — count from ANY spec so
            # the printing formula / UV-machine check still sees the total foil count.
            for f in (ma.get("foils") or []):
                grp = (f.get("foil_group") or "").upper()
                g_foil += 1
                if grp == "COLD": g_cold += 1
                elif grp == "HOT": g_hot += 1
            if s.get("has_machine"):
                mname = ma.get("machine", "")
                for m in (s.get("machines") or []):
                    if m.get("machine") == mname and cint(m.get("is_printing_machine")):
                        g_print_rate = flt(m.get("machine_cost_per_hour", 0))
                        g_print_cap  = cint(m.get("color_capacity", 8)) or 8
        flexo_ctx = {
            "foil_count":            g_foil,
            "cold_foil_count":       g_cold,
            "hot_foil_count":        g_hot,
            "printing_machine_rate": g_print_rate,
            "machine_capacity":      g_print_cap,
            "uv_machine_applies":    1 if (g_foil + no_of_colors) > g_print_cap else 0,
        }

    for spec in selected_specs:
        rows, mt, pp, pr = _process_spec(
            spec, form, sheet, item_qty, no_of_colors, material_rate,
            full_sheet_qty, cut_sheet_qty, cut_sheet_area, price_list,
            section="Spec", is_auto=False,
            reel_area=reel_area, reel_length=reel_length, ups=ups,
            all_selected_spec_names=all_selected_spec_names,
            machine_count_map=machine_count_map,
            global_extra_ctx=flexo_ctx,
            foil_wastage_frac=foil_wastage_frac,
        )
        # Outsource-group specs get their own section + cost bucket in the breakdown.
        if (spec.get("group") or "") == "Outsource":
            for r in rows:
                r["cost_group"] = "Outsource"
            out_total += (mt + pp + pr)
        else:
            mat_total += mt; prep_total += pp; prod_total += pr
        cost_rows.extend(rows)

    # NOTE: Extra Production Cost is NOT a breakdown line. It is applied after all
    # costs (incl. manual) as a % of the total Production cost, and shown in the
    # pricing summary as: Net Cost → Extra Production Cost → Total Cost.

    # Manual cost items (user-entered ad-hoc lines: name + qty + rate + cost group)
    for mc in (form.get("manual_costs") or []):
        mc_name = (mc.get("name") or "").strip()
        mc_qty  = flt(mc.get("qty"))
        mc_rate = flt(mc.get("rate"))
        mc_amt  = round(mc_qty * mc_rate, 2)
        if not mc_name or not mc_amt:
            continue
        mc_group = (mc.get("cost_group") or "Production").strip()
        gl = mc_group.lower()
        if gl == "material":
            mat_total += mc_amt
        elif gl == "preparation":
            prep_total += mc_amt
        elif gl == "outsource":
            mc_group = "Outsource"
            out_total += mc_amt
        else:
            mc_group = "Production"
            prod_total += mc_amt
        cost_rows.append({
            "section":            "Manual",
            "spec_name":          mc_name,
            "cost_fact":          mc_name,
            "cost_group":         mc_group,
            "selected_item":      "",
            "selected_item_name": mc_name,
            "attribute_values":   {"manual": 1},
            "req_qty":            round(mc_qty, 4),
            "rate":               round(mc_rate, 4),
            "amount":             mc_amt,
            "is_auto":            False,
        })

    # ── Authoritative group totals — derived from the DISPLAYED cost rows ──────
    # The internal accumulators (mat_total/prod_total/…) can disagree with the
    # rows shown in the breakdown: e.g. offset ink cost is added to the
    # production accumulator (prod_total) while the ink rows themselves carry
    # cost_group="Material". That made group_totals.production ~2x the sum of
    # its own rows, so the Extra Production Cost was computed on an inflated
    # base. We therefore compute each group total as the sum of the amounts of
    # the rows in that group — exactly what the user sees — and base the extra
    # on that. The grand total is unchanged (a cost only moves between groups,
    # it is never double counted): sum(all rows) == old net_total.
    cfg = _get_config()
    # Use the DISPLAYED (rounded) required qty in the ACTUAL costing: round each row's
    # req_qty to the configured decimals, then recompute simple qty×rate amounts from the
    # rounded qty so the shown qty and amount agree and feed the group totals. Composite /
    # min-floored rows (whose amount is not simply qty×rate) keep their amount — only their
    # displayed qty is rounded.
    qd = cfg.get("req_qty_decimals", 2)
    for r in cost_rows:
        if "req_qty" not in r:
            continue
        _precise = flt(r["req_qty"])
        _rate    = flt(r.get("rate", 0))
        _orig    = flt(r.get("amount", 0))
        r["req_qty"] = round(_precise, qd)
        # Recompute amount from the ROUNDED qty for SIMPLE qty×rate rows. A row is "simple"
        # when its amount matches qty×rate at the row's stored precision (within a small
        # relative tolerance that absorbs 4-6 decimal storage rounding). Min-floored / composite
        # rows (e.g. foils clamped to a minimum charge) have amount ≠ qty×rate → keep as-is.
        if _rate and "amount" in r:
            _simple = round(_precise * _rate, 2)
            if abs(_orig - _simple) <= max(0.5, 0.005 * abs(_orig)):
                r["amount"] = round(flt(r["req_qty"]) * _rate, 2)

    def _group_sum(g):
        return round(
            sum(flt(r.get("amount", 0)) for r in cost_rows
                if (r.get("cost_group") or "").strip().lower() == g),
            2,
        )

    mat_g  = _group_sum("material")
    prep_g = _group_sum("preparation")
    prod_g = _group_sum("production")
    out_g  = _group_sum("outsource")

    # Net cost (all groups, before the extra-production add-on).
    net_total      = round(mat_g + prep_g + prod_g + out_g, 2)
    extra_prod_pct = flt(form.get("extra_prod_cost_pct", 0))
    extra_prod_amt = round(prod_g * extra_prod_pct / 100, 2) if (extra_prod_pct and prod_g > 0) else 0.0
    grand = round(net_total + extra_prod_amt, 2)
    pm    = flt(form.get("profit_margin", 0)) / 100
    uc    = grand / item_qty if item_qty else 0

    sscl  = uc * cfg["sscl_rate"] if form.get("tax_sscl") else 0
    if pricing_type == "Flexo":
        # Flexo: profit margin is a % of the SELLING price → sell = cost / (1 - margin).
        qu = (uc + sscl) / (1 - pm) if pm < 1 else (uc + sscl)
    else:
        # Offset: unchanged — markup on cost → sell = cost * (1 + margin).
        qu = (uc + sscl) * (1 + pm)
    vat   = qu * cfg["vat_rate"] if form.get("tax_vat") else 0
    su    = qu + vat
    mc    = (((qu * item_qty) - (prep_g + mat_g)) / (qu * item_qty) * 100) if qu * item_qty else 0

    return {
        "sheet":       sheet,
        "cost_rows":   cost_rows,
        "group_totals": {
            "material":    round(mat_g,  2),
            "preparation": round(prep_g, 2),
            "production":  round(prod_g, 2),
            "outsource":   round(out_g,  2),
            "net":         round(net_total, 2),
            "extra_production": round(extra_prod_amt, 2),
            "grand":       round(grand, 2),
        },
        "pricing": {
            "item_qty":    item_qty,
            "net_cost":    round(net_total, 2),
            "extra_prod_pct": round(extra_prod_pct, 4),
            "extra_prod_amt": round(extra_prod_amt, 2),
            "total_cost":  round(grand, 2),
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
def get_finishings():
    """Finishing master records for the calculator's finishing multi-select. These are
    display-only labels printed on the quotation (they do NOT affect cost). Returns the
    active finishings by name (Finishing.name == finishing_operation)."""
    return frappe.get_all("Finishing", filters={"disabled": 0}, pluck="name", order_by="name asc")


@frappe.whitelist()
def save_costing(payload):
    if isinstance(payload, str):
        payload = json.loads(payload)

    form           = payload.get("form", {})
    machine_spec   = payload.get("machine_spec")
    selected_specs = payload.get("selected_specs", [])
    calc_result    = payload.get("calc_result", {})
    doc_name       = payload.get("doc_name", "")
    finishing      = payload.get("finishing", []) or []
    sheet_overrides = payload.get("sheet_overrides") or {}

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
    mat_type = (form.get("material_type") or "Existing").strip()
    if hasattr(doc, "material_type"):
        doc.material_type = mat_type
    if mat_type == "Custom":
        doc.base_material = ""
        if hasattr(doc, "custom_material_name"):
            doc.custom_material_name = (form.get("custom_material_name") or "").strip()
    else:
        doc.base_material = form.get("base_material", "")
        if hasattr(doc, "custom_material_name"):
            doc.custom_material_name = ""
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
        ("profit_margin",      flt(form.get("profit_margin", 0))),
        ("extra_prod_cost_pct", flt(form.get("extra_prod_cost_pct", 0))),
        ("tax_sscl",           1 if form.get("tax_sscl") else 0),
        ("tax_vat",            1 if form.get("tax_vat")  else 0),
        ("unit_cost",          flt(pricing.get("unit_cost", 0))),
        ("selling_price",      flt(pricing.get("sell_total", 0))),
    ]:
        if hasattr(doc, f):
            setattr(doc, f, v)

    if hasattr(doc, "ui_state"):
        doc.ui_state = json.dumps({
            "form":           form,
            "machine_spec":   machine_spec,
            "selected_specs": selected_specs,
            "calc_result":    calc_result,
            "finishing":      finishing,
            "sheet_overrides": sheet_overrides,
        })

    # Finishing multi-select — display-only labels for the quotation (no cost impact).
    # Reset + re-append the chosen Finishing master records; skip any that no longer exist.
    if hasattr(doc, "finishing"):
        doc.set("finishing", [])
        _seen_fin = set()
        for fn in finishing:
            fn = (fn or "").strip()
            if fn and fn not in _seen_fin and frappe.db.exists("Finishing", fn):
                doc.append("finishing", {"finishing": fn})
                _seen_fin.add(fn)

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
            # Finishing selection: prefer the stored child table (authoritative), else ui_state.
            fin_rows = [r.finishing for r in (doc.get("finishing") or []) if r.finishing]
            if fin_rows or "finishing" not in state:
                state["finishing"] = fin_rows
            return state
        except Exception:
            pass
    form = {
        "pricing_type":  doc.pricing_type or "Offset",
        "customer_name": doc.customer_name or "",
        "ref":           doc.ref or "",
        "price_list":    doc.price_list or "",
        "carton_size":   doc.carton_size or "",
        "material_type":          (getattr(doc, "material_type", None) or "Existing"),
        "base_material":          doc.base_material or "",
        "custom_material_name":   (getattr(doc, "custom_material_name", None) or ""),
        "material_rate":          flt(doc.material_rate),
        "full_sheet_l":  flt(doc.full_sheet_l),
        "full_sheet_w":  flt(doc.full_sheet_w),
        "cut_sheet_l":   flt(doc.cut_sheet_l),
        "cut_sheet_w":   flt(doc.cut_sheetw),
        "no_of_cuts":    cint(doc.no_of_cuts),
        "no_of_ups":     cint(doc.no_of_ups),
        "no_of_colors":  cint(doc.no_of_colors),
        "item_qty":      flt(doc.item_qty),
        "profit_margin":       flt(getattr(doc, "profit_margin", 0)),
        "extra_prod_cost_pct": flt(getattr(doc, "extra_prod_cost_pct", 0)),
        "tax_sscl":            cint(getattr(doc, "tax_sscl", 0)),
        "tax_vat":             cint(getattr(doc, "tax_vat",  0)),
    }
    return {"doc_name": doc.name, "status": doc.docstatus,
            "form": form, "machine_spec": None, "selected_specs": [],
            "calc_result": None,
            "finishing": [r.finishing for r in (doc.get("finishing") or []) if r.finishing]}


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
                {
                    "attribute_name": r.attribute_name,
                    "lable":          r.lable,
                    "type":           r.type or "Number",
                    "default_value":  getattr(r, "default_value", None),
                }
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
            "req_qty_decimals":      (cint(doc.req_qty_decimals) if getattr(doc, "req_qty_decimals", None) not in (None, "") else 2),
            "flexo_wastage":         rows,
        }
    except Exception:
        return {
            "sscl_rate": 0.025, "vat_rate": 0.18, "default_profit_margin": 15.0,
            "offset_wastage_pct": 0.05, "offset_wastage_min": 500,
            "req_qty_decimals": 2,
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
        fields=["ink_name", "price_per_kg", "consumption_per_sqm", "consumption_per_sqinch", "min_qty", "min_value"],
        order_by="ink_name asc",
    )


def _calc_spec_ink_cost(spec_name, machine_assignment, form, sheet, cycles):
    """Calculate ink costs for all inks assigned to a printing spec.
    Formula: qty_kg = cut_sheet_area × req_cut_sheets × consumption_per_sqinch × (pct/100) × cycles
    Uses req_cut_sheets (net + wastage) because ink is consumed on every sheet run through the press.
    """
    inks = machine_assignment.get("inks", [])
    if not inks:
        return [], 0.0

    cut_sheet_area = flt(form.get("cut_sheet_l", 0)) * (flt(form.get("cut_sheetw", 0)) or flt(form.get("cut_sheet_w", 0)))
    cut_sheet_qty  = sheet.get("req_cut_sheets", 0) or sheet.get("cut_sheet_qty", 0)

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
            "cost_group":         "Material",
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


@frappe.whitelist()
def resolve_common_material_name(raw_material=None, base_material=None, calculation_breakdown=None):
    """Customer-facing common material name held on the Boards and Papers master.

    Used to HIDE the real material on the customer quotation. Resolution order:
      1. raw_material treated as a Boards and Papers record (e.g. cost Item.material).
      2. base_material (an ERPNext Item) → its item_name → Boards and Papers (name == item).
      3. base_material code → Boards and Papers directly.
    Returns the common_name, or "" — NEVER the real material/item name.
    """
    if not frappe.db.has_column("Boards and Papers", "item"):
        return ""

    def _bp_common(name):
        if not name:
            return ""
        cn = frappe.db.get_value("Boards and Papers", name, "item")
        if cn:
            return cn
        return frappe.db.get_value("Boards and Papers", {"item": name}, "common_name") or ""
    def _get_bp_item(name,cost_breakdown=None):
        cn =frappe.db.get_value("Item", name, "custom_board_and_paper_group")
        if not name:
            return ""
        if cn:
            return cn
        if cost_breakdown:
            inquery=frappe.db.get_value("Calculation Breakdown", cost_breakdown, "ref")
            if  inquery:
                common_name=frappe.db.get_value("Opportunity",inquery,"custom_board")
                if common_name:
                    return common_name
                else:
                    return ""
                # for row in inquery_doc.custom_breakdown:
                #     if row.description==name:
                #         return row.common_name

        return frappe.db.get_value("Boards and Papers", {"item": name}, "name") or ""
    raw_material  = (raw_material or "").strip()
    base_material = (base_material or "").strip()

    cn = _bp_common(raw_material)
    if cn:
        return cn
    if base_material:
        iname = frappe.db.get_value("Item", base_material, "item_name") or base_material
        cn = _bp_common(iname) or _get_bp_item(base_material, calculation_breakdown)
        if cn:
            return cn
    return ""


@frappe.whitelist()
def download_cost_breakdown_xlsx(calculation_breakdown=None, cost_item=None):
    """Stream the Product Costing Summary of a Calculation Breakdown as an Excel (.xlsx)
    download. Accepts a CB name directly, or a cost Item (whose first linked CB is used)."""
    from frappe.utils.xlsxutils import make_xlsx
    from nxtgen_savinda_pricing_calculator.nxtgen_savinda_pricing_calculator.utils.jinja import (
        get_cb_print_data,
    )

    cb = calculation_breakdown
    if not cb and cost_item:
        cb = frappe.db.get_value(
            "Cost Item Calculation", {"parent": cost_item},
            "calculation_breakdown", order_by="idx asc",
        )
    if not cb or not frappe.db.exists("Calculation Breakdown", cb):
        frappe.throw("No Calculation Breakdown found to export.")

    d = get_cb_print_data(cb)
    f = d.get("form", {}) or {}
    is_flexo = d.get("is_flexo")

    def q(v):
        v = flt(v)
        return int(v) if v == int(v) else round(v, 4)

    rows = []
    rows.append(["PRODUCT COSTING SUMMARY (%s)" % ("FLEXO" if is_flexo else "OFFSET")])
    rows.append(["Sales Inquiry", d["doc"].ref or ""])
    rows.append(["Customer", d["doc"].customer_name or ""])
    rows.append(["Inquiry Breakdown", d.get("breakdown_name", "")])
    rows.append(["Breakdown Quantity", q(d.get("item_qty", 0))])
    rows.append(["Created At", "%s by %s" % (d.get("created_date"), d.get("created_by"))])
    rows.append(["Updated At", "%s by %s" % (d.get("updated_date"), d.get("updated_by"))])
    rows.append([])

    rows.append(["PRODUCT SPECS"])
    if is_flexo:
        rows.append(["Sticker Size (L x W)", "%s x %s mm" % (int(flt(f.get("product_length_mm"))), int(flt(f.get("product_width_mm"))))])
        rows.append(["Material Width", int(flt(f.get("reel_width_mm")))])
        rows.append(["Ups", int(flt(d["sheet"].get("ups")))])
        rows.append(["Colors", d["doc"].no_of_colors or 0])
    else:
        if d["doc"].carton_size:
            rows.append(["Carton Size", d["doc"].carton_size])
        rows.append(["Full Sheet Size", "%s x %s INCH" % (int(d.get("full_sheet_l") or 0), int(d.get("full_sheet_w") or 0))])
        rows.append(["Cut Sheet Size", "%s x %s INCH" % (int(d.get("cut_sheet_l") or 0), int(d.get("cut_sheet_w") or 0))])
        rows.append(["Cut Sheet Ups", int(flt(d["sheet"].get("cut_sheet_ups")))])
        rows.append(["Full Sheet Ups", int(flt(f.get("no_of_ups") or d["doc"].no_of_ups or 0))])
        rows.append(["Cuts", d["doc"].no_of_cuts or 0])
        rows.append(["Colors", d["doc"].no_of_colors or 0])
    rows.append([])

    rows.append(["COST BREAKDOWN"])
    rows.append(["DESCRIPTION", "ESTIMATED QTY", "UNIT PRICE (LKR)", "AMOUNT (LKR)"])
    gt = d.get("group_totals", {}) or {}
    groups = [
        ("Preparation Costs", d.get("prep_rows", []), gt.get("preparation", 0)),
        ("Material Costs", d.get("mat_rows", []), gt.get("material", 0)),
        ("Over Head Costs" if is_flexo else "Production Costs", d.get("prod_rows", []), gt.get("production", 0)),
    ]
    for label, grp_rows, total in groups:
        if not grp_rows:
            continue
        rows.append([label, "", "", round(flt(total), 2)])
        for r in grp_rows:
            rows.append([
                r.get("_desc", ""),
                q(r.get("req_qty", 0)),
                round(flt(r.get("rate", 0)), 2) if r.get("rate") else "",
                round(flt(r.get("amount", 0)), 2),
            ])
    rows.append([])

    rows.append(["SUMMARY"])
    rows.append(["Order Quantity", q(d.get("item_qty", 0))])
    rows.append(["Net Cost", round(flt(d.get("net_cost", 0)), 2)])
    if flt(d.get("extra_prod_amt", 0)):
        rows.append(["Extra Production Cost (%s%% of Production)" % q(d.get("extra_prod_pct", 0)),
                     round(flt(d.get("extra_prod_amt", 0)), 2)])
    rows.append(["Total Cost/Value", round(flt(d.get("grand_total", 0)), 2)])
    rows.append(["Unit Cost", round(flt(d.get("unit_cost", 0)), 4)])
    rows.append(["SSCL (2.5%)", round(flt(d.get("sscl_per_unit", 0)), 4)])
    rows.append(["Profit Margin", "%s %%" % q(d.get("profit_margin", 0))])
    rows.append(["Quoted Price", round(flt(d.get("sell_unit", 0)), 4)])
    rows.append(["VAT (18%)", round(flt(d.get("vat_per_unit", 0)), 4)])
    rows.append(["Final Value", round(flt(d.get("final_value", 0)), 4)])
    rows.append(["Material Contribution", "%s %%" % round(flt(d.get("mat_contrib", 0)), 2)])
    rows.append([])
    rows.append(["Remarks", f.get("remarks", "") or ""])

    xlsx = make_xlsx(rows, "Costing Summary")
    frappe.response["filename"] = "Costing - %s.xlsx" % cb
    frappe.response["filecontent"] = xlsx.getvalue()
    frappe.response["type"] = "binary"


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


def _calc_flexo(form, wastage_override_pct=None):
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

    # A ticked "Wastage Overwrite" spec replaces the table wastage % (highest
    # wins, resolved by the caller). Setup metrage (a fixed length) is kept.
    if wastage_override_pct is not None:
        wastage_pct = flt(wastage_override_pct) / 100.0

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


def _calc_spec_foil_cost(spec_name, machine_assignment, printable_area, wastage_frac=0.25):
    """Calculate Flexo foil costs for all foils assigned to a Flexo spec.
    Foil is costed on the NET PRINTABLE reel area (reel_area_net) — NOT the total
    reel area — so the reel setup/wastage add-on is not double counted; only the
    foil's own wastage_frac applies on top.
    Formula per foil:
      qty_sqm = printable_area × (pct/100) × (1 + wastage_frac)
      cost    = qty_sqm × cost_per_sqm
    wastage_frac defaults to 0.25 (25%) but is replaced by the Wastage Overwrite
    value (as a fraction) when a spec enables it. Cold Foil Varnish is
    auto-added for any COLD foils.
    """
    foils = machine_assignment.get("foils", [])
    if not foils or not printable_area:
        return [], 0.0

    rows = []
    total_cost = 0.0
    has_cold = False

    for foil_row in foils:
        # foil_key is the Flexo Foil DOC NAME (e.g. "COLD-Green"); foil_name is the display
        # label (e.g. "Green"), which is NOT unique across COLD/HOT — always look up by key.
        foil_key  = (foil_row.get("foil_key") or foil_row.get("foil_name") or "").strip()
        foil_disp = (foil_row.get("foil_name") or foil_key).strip()
        foil_grp  = (foil_row.get("foil_group") or "").upper()
        foil_pct  = flt(foil_row.get("percentage", 100)) / 100.0
        if not foil_key:
            continue
        if not frappe.db.exists("Flexo Foil", foil_key):
            continue
        foil_doc     = frappe.get_cached_doc("Flexo Foil", foil_key)
        cost_per_sqm = flt(foil_doc.cost_per_sqm or 0)
        min_qty_v    = flt(getattr(foil_doc, "min_qty",   0) or 0)
        min_val_v    = flt(getattr(foil_doc, "min_value", 0) or 0)

        qty  = printable_area * foil_pct * (1 + flt(wastage_frac))   # net printable area × foil wastage (default 25%)
        cost = round(qty * cost_per_sqm, 2)
        if min_qty_v and qty  < min_qty_v: qty  = min_qty_v; cost = round(qty * cost_per_sqm, 2)
        if min_val_v and cost < min_val_v: cost = flt(min_val_v)

        rows.append({
            "spec_name":          spec_name,
            "cost_fact":          f"{spec_name} - Foil ({foil_disp})",
            "cost_group":         "Material",
            "selected_item":      foil_key,
            "selected_item_name": foil_disp,
            "attribute_values":   {"foil": foil_disp, "type": foil_grp, "percentage": flt(foil_row.get("percentage", 100))},
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
            varnish_qty  = printable_area * consumption
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
    Formula: qty_kg = reel_area × (grams_per_sqm / 1000); cost = qty_kg × price_per_kg
    Flexo ink consumption is entered directly in grams per m² in the Production
    Assignment dialog (defaulted from the ink master's consumption_per_sqm × 1000),
    NOT as a percentage like Offset.
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

        # grams per m²: use the dialog value if given, else default from the ink
        # master (consumption_per_sqm is stored in kg/m² → ×1000 for g/m²).
        g_per_sqm = ink_row.get("grams_per_sqm")
        if g_per_sqm in (None, ""):
            g_per_sqm = consumption * 1000.0
        g_per_sqm = flt(g_per_sqm)

        qty  = reel_area * (g_per_sqm / 1000.0)
        cost = round(qty * price_per_kg, 2)
        if min_qty_v and qty  < min_qty_v: qty  = min_qty_v; cost = round(qty * price_per_kg, 2)
        if min_val_v and cost < min_val_v: cost = flt(min_val_v)

        rows.append({
            "spec_name":          spec_name,
            "cost_fact":          f"{spec_name} - Ink ({ink_name})",
            "cost_group":         "Material",
            "selected_item":      ink_name,
            "selected_item_name": ink_name,
            "attribute_values":   {"ink": ink_name, "grams_per_sqm": g_per_sqm},
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
                  all_selected_spec_names=None, machine_count_map=None,
                  global_extra_ctx=None, foil_wastage_frac=0.25):
    all_rows = []; mat = prep = prod = 0.0

    # Build foil count context from machine_assignment (used in Flexo formulas)
    machine_assignment = spec.get("machine_assignment") or {}

    # ── Manual Process override ────────────────────────────────────────────────
    if machine_assignment.get("manual_process"):
        manual_unit      = (machine_assignment.get("manual_unit") or "Fixed Amount").strip()
        manual_unit_cost = flt(machine_assignment.get("manual_unit_cost", 0))
        if manual_unit_cost:
            if manual_unit == "Per Item":
                req_qty = flt(item_qty)
            elif manual_unit == "Per Cut Sheet":
                req_qty = flt(cut_sheet_qty) or flt(sheet.get("cut_sheet_qty", 0))
            elif manual_unit == "Per Full Sheet":
                req_qty = flt(full_sheet_qty) or flt(sheet.get("full_sheet_qty", 0))
            else:  # Fixed Amount
                req_qty = 1.0
            amt = round(req_qty * manual_unit_cost, 2)
            all_rows.append({
                "section":            section,
                "spec_name":          spec.get("spec_name", ""),
                "cost_fact":          spec.get("spec_name", ""),
                "cost_group":         "Production",
                "selected_item":      "",
                "selected_item_name": f"Manual — {manual_unit}",
                "attribute_values":   {"unit": manual_unit},
                "req_qty":            round(req_qty, 4),
                "rate":               round(manual_unit_cost, 4),
                "amount":             amt,
                "is_auto":            is_auto,
            })
            prod += amt
        return all_rows, mat, prep, prod
    # ──────────────────────────────────────────────────────────────────────────
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
    # Merge global Flexo context (e.g. printing_machine_rate, uv_machine_applies, global foil counts)
    # Global values fill in any key not already set by this spec's own machine assignment
    if global_extra_ctx:
        for k, v in global_extra_ctx.items():
            if k not in extra_ctx:
                extra_ctx[k] = v

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
        # Skip decision: the calculator sends an explicit skip_machine flag (its
        # "Skip machine cost" checkbox, which auto-ticks per the spec's skip rules and
        # can be un-ticked to force the cost). When present it wins. Otherwise fall back
        # to the legacy server-side "skip if spec selected" rule (unchanged behaviour).
        explicit_skip = machine_assignment.get("skip_machine")
        if explicit_skip is not None:
            skip = bool(explicit_skip)
        else:
            skip_spec = spec.get("skip_machine_if_spec", "")
            skip = skip_spec and all_selected_spec_names and skip_spec in all_selected_spec_names
        m_data = next((m for m in spec.get("machines", []) if m["machine"] == machine_name), None)
        if m_data:
            # Machine (hourly) cost — REMOVED when "skip machine cost" is ticked.
            if not skip:
                machine_rows, machine_cost = _calc_spec_machine_cost(
                    spec.get("spec_name", ""), m_data, machine_assignment,
                    form, sheet, item_qty, no_of_colors,
                    spec.get("units", "Full sheet"), machine_count_map or {},
                    reel_area=reel_area,
                )
                for mr in machine_rows:
                    mr["section"] = section
                    all_rows.append(mr)
                prod += machine_cost

            # Ink / foil MATERIAL cost — ALWAYS added (the ink/foil is consumed even
            # when the machine cost is skipped, e.g. the operation runs inline).
            pricing_t = (form.get("pricing_type") or "Offset").strip()
            show_inks = cint(m_data.get("is_printing_machine")) or cint(m_data.get("allow_ink_assignment", 0))
            if pricing_t == "Offset" and show_inks and machine_assignment.get("inks"):
                cycles = max(cint(machine_assignment.get("cycles", 1)), 1)
                ink_rows, ink_cost = _calc_spec_ink_cost(
                    spec.get("spec_name", ""), machine_assignment, form, sheet, cycles
                )
                for ir in ink_rows:
                    ir["section"] = section
                    all_rows.append(ir)
                prod += ink_cost

            # Flexo ink costs (foils are handled by a dedicated foil spec below)
            if pricing_t == "Flexo" and machine_assignment.get("inks"):
                fx_ink_rows, fx_ink_cost = _calc_flexo_ink_cost(
                    spec.get("spec_name", ""), machine_assignment, reel_area
                )
                for ir in fx_ink_rows:
                    ir["section"] = section
                    all_rows.append(ir)
                mat += fx_ink_cost

    # Flexo foils — allocated on a dedicated foil spec (Allow Foil Assignment), usually a
    # child of the Print spec. Computed independently of any machine so the foil cost is its
    # own line/section under its parent.
    if (form.get("pricing_type") or "Offset").strip() == "Flexo" \
            and spec.get("allow_foil_assignment") and machine_assignment.get("foils"):
        # Foil is costed on the NET printable reel area (before the setup/wastage
        # add-on); fall back to total reel_area only if net is unavailable.
        foil_printable_area = flt(sheet.get("reel_area_net", 0)) or reel_area
        foil_rows, foil_cost = _calc_spec_foil_cost(
            spec.get("spec_name", ""), machine_assignment, foil_printable_area, foil_wastage_frac
        )
        for fr in foil_rows:
            fr["section"] = section
            all_rows.append(fr)
        mat += foil_cost

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
        "full_sheet_qty":   full_sheet_qty,
        "cut_sheet_qty":    cut_sheet_qty,
        "req_cut_sheets":   sheet.get("req_cut_sheets", cut_sheet_qty),
        "cut_sheet_area":   cut_sheet_area,
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


@frappe.whitelist()
def get_cost_sheet_calculations(cost_sheet):
    """
    Return all distinct Calculation Breakdowns already created in a Cost Sheet
    (via its Cost Items). Used by the "Copy Calculation" picker so a user can
    reuse an existing calculation for another item at a different qty.
    """
    if not cost_sheet:
        return []
    try:
        cs = frappe.get_doc("Cost Sheet", cost_sheet)
    except frappe.DoesNotExistError:
        return []

    out, seen = [], set()
    for row in (cs.pricing_list or []):
        if not row.item:
            continue
        try:
            ci = frappe.get_doc("cost Item", row.item)
        except frappe.DoesNotExistError:
            continue
        for calc in (ci.calculations or []):
            cb = calc.calculation_breakdown
            if not cb or cb in seen:
                continue
            seen.add(cb)
            cbd = frappe.db.get_value(
                "Calculation Breakdown", cb,
                ["item_qty", "unit_cost", "pricing_type"], as_dict=True,
            ) or {}
            out.append({
                "cb":           cb,
                "source_item":  ci.cost_item_name or ci.name,
                "item_qty":     flt(cbd.get("item_qty")),
                "unit_cost":    flt(cbd.get("unit_cost")),
                "pricing_type": cbd.get("pricing_type") or "Offset",
                "description":  calc.description or "",
            })
    return out


@frappe.whitelist()
def copy_calculation(source_cb, target_cost_item, new_qty=None, description=None):
    """
    Duplicate an existing Calculation Breakdown at a (optionally) different qty
    WITHOUT re-entering any specs. Loads the source's saved ui_state, overrides
    item_qty, recalculates, saves a NEW Calculation Breakdown, and links it to
    the target Cost Item. Single-item copy → no sheet split.
    """
    if not source_cb or not target_cost_item:
        return {"error": "source_cb and target_cost_item are required"}

    try:
        src = frappe.get_doc("Calculation Breakdown", source_cb)
    except frappe.DoesNotExistError:
        return {"error": f"Source calculation {source_cb} not found"}

    if not (hasattr(src, "ui_state") and src.ui_state):
        return {"error": "Source calculation has no saved state to copy"}

    try:
        state = json.loads(src.ui_state)
    except Exception:
        return {"error": "Could not parse source calculation state"}

    # Carry the finishing multi-select from the SOURCE breakdown (not the inquiry) — the
    # child table is authoritative, so this works for old CBs too.
    src_finishing = [r.finishing for r in (src.get("finishing") or []) if r.finishing]

    form = state.get("form", {})
    if new_qty is not None and flt(new_qty) > 0:
        form["item_qty"] = flt(new_qty)
    # Single-item copy → no breakdown split (recompute as one quantity)
    form["breakdown_qtys"] = []
    state["form"] = form

    payload = {
        "form":           form,
        "machine_spec":   state.get("machine_spec"),
        "selected_specs": state.get("selected_specs", []),
    }

    try:
        result = calculate(json.dumps(payload))
    except Exception as e:
        frappe.log_error(title="copy_calculation calculate error", message=str(e))
        return {"error": str(e)}

    # Save as a NEW Calculation Breakdown (doc_name empty → insert)
    save_payload = {
        "form":           form,
        "machine_spec":   state.get("machine_spec"),
        "selected_specs": state.get("selected_specs", []),
        "calc_result":    result,
        "finishing":      src_finishing,
        "doc_name":       "",
    }
    try:
        saved  = save_costing(json.dumps(save_payload))
        new_cb = saved.get("doc_name")
    except Exception as e:
        frappe.log_error(title="copy_calculation save error", message=str(e))
        return {"error": str(e)}

    if not new_cb:
        return {"error": "Failed to create copied calculation"}

    pricing = result.get("pricing", {})
    unit_cost = flt(pricing.get("unit_cost", 0))

    # Link the new CB to the target Cost Item
    try:
        ci = frappe.get_doc("cost Item", target_cost_item)
        ci.append("calculations", {
            "calculation_breakdown": new_cb,
            "description":           description or "",
            "unit_cost":             unit_cost,
            "amount":                0,
        })
        ci.save(ignore_permissions=True)
    except Exception as e:
        frappe.log_error(title="copy_calculation link error", message=str(e))
        return {"error": str(e)}

    # Sync unit cost up to Cost Item + Cost Sheet rows
    try:
        sync_cost_item_unit_cost(new_cb)
    except Exception:
        pass

    frappe.db.commit()
    return {
        "new_cb":    new_cb,
        "unit_cost": unit_cost,
        "item_qty":  flt(form.get("item_qty")),
    }


@frappe.whitelist()
def duplicate_cost_item(source_cost_item, new_name=None, new_qty=None):
    """Duplicate a cost Item into a NEW one that carries its OWN cloned Calculation
    Breakdown(s). The caller adds the returned item as a new Cost Sheet row.

    Lets sales quote a with/without-spec variant of a product: the copy starts identical,
    then the user edits specs on the copy in the calculator without touching the original.
    Reuses copy_calculation() to clone each breakdown so the copy's costs are independent.

    When `new_qty` is given, each cloned breakdown is recomputed at that quantity (and the
    copy's item_qty is set to it) — used by add_qty_variant() for quantity-break pricing.
    """
    if not source_cost_item:
        return {"error": "source_cost_item is required"}
    if not frappe.db.exists("cost Item", source_cost_item):
        return {"error": f"Cost Item {source_cost_item} not found"}

    src = frappe.get_doc("cost Item", source_cost_item)

    # New cost Item: copy every field, then drop the linked calculations (we clone fresh
    # breakdowns below so editing the copy never affects the original).
    new_ci = frappe.copy_doc(src)
    new_ci.cost_item_name = (new_name or f"{src.cost_item_name} (Copy)").strip()
    if new_qty is not None and flt(new_qty) > 0:
        new_ci.item_qty = flt(new_qty)
    new_ci.set("calculations", [])
    new_ci.insert(ignore_permissions=True)

    new_cbs = []
    for calc in src.calculations:
        if not calc.calculation_breakdown:
            continue
        res = copy_calculation(calc.calculation_breakdown, new_ci.name, new_qty=new_qty, description=calc.description)
        if isinstance(res, dict) and res.get("new_cb"):
            new_cbs.append(res["new_cb"])

    new_ci.reload()
    frappe.db.commit()
    return {
        "new_cost_item":      new_ci.name,
        "new_cost_item_name": new_ci.cost_item_name,
        "unit_cost":          flt(new_ci.unit_cost),
        "new_cbs":            new_cbs,
    }


@frappe.whitelist()
def add_qty_variant(source_cost_item, new_qty):
    """Create a QUANTITY variant of a cost Item: a fresh cost Item with the SAME name and
    specs, but its breakdown(s) recomputed at `new_qty`. The caller adds it as a new Cost
    Sheet row. Because it keeps the SAME cost_item_name, the quotation groups it under the
    same item and prints it as an extra qty/price line (item details shown once)."""
    if not source_cost_item or not frappe.db.exists("cost Item", source_cost_item):
        return {"error": "Source cost item not found"}
    if not new_qty or flt(new_qty) <= 0:
        return {"error": "New quantity must be greater than 0"}
    src_name = frappe.db.get_value("cost Item", source_cost_item, "cost_item_name")
    return duplicate_cost_item(source_cost_item, new_name=src_name, new_qty=new_qty)
