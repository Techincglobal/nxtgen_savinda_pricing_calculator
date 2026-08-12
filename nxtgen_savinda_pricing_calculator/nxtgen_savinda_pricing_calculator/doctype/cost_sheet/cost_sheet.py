# Copyright (c) 2026, Techincglobal.com and contributors
import frappe
from frappe.model.document import Document
from frappe.utils import flt, cint


class CostSheet(Document):

	def onload(self):
		if self.inquiry:
			self._fetch_inquiry_fields()

	def before_insert(self):
		if self.inquiry:
			self._fetch_inquiry_fields()
			self._copy_inquiry_child_tables()

	def validate(self):
		self._calc_pricing_list_amounts()

	def before_submit(self):
		pass
		# Artwork must be approved before the Cost Sheet can be submitted.
		# if (self.artwork_status or "Pending") != "Approved":
		# 	frappe.throw(
		# 		"Artwork must be <b>Approved</b> before submitting this Cost Sheet "
		# 		"(current status: <b>{0}</b>). Ask an Artwork Approver to approve it.".format(
		# 			self.artwork_status or "Pending"
		# 		)
		# 	)

	def on_submit(self):
		"""Auto-submit all Calculation Breakdowns linked through Cost Items."""
		submitted, skipped = [], []
		for cb_name in _linked_cb_names(self):
			cb = frappe.get_doc("Calculation Breakdown", cb_name)
			if cb.docstatus == 0:
				try:
					cb.submit()
					submitted.append(cb_name)
				except Exception as e:
					frappe.log_error(str(e), f"CB submit failed: {cb_name}")
					skipped.append(cb_name)
			# docstatus 1 (already submitted) → ignore; 2 (cancelled) → ignore

		if submitted:
			frappe.msgprint(
				f"<b>{len(submitted)}</b> Calculation Breakdown(s) submitted automatically.",
				indicator="green", alert=True,
			)
		if skipped:
			frappe.msgprint(
				f"<b>{len(skipped)}</b> Calculation Breakdown(s) could not be submitted: "
				+ ", ".join(skipped),
				indicator="orange",
			)

	def on_cancel(self):
		"""Auto-cancel all submitted Calculation Breakdowns linked through Cost Items."""
		cancelled = []
		for cb_name in _linked_cb_names(self):
			cb = frappe.get_doc("Calculation Breakdown", cb_name)
			if cb.docstatus == 1:
				cb.cancel()
				cancelled.append(cb_name)

		if cancelled:
			frappe.msgprint(
				f"<b>{len(cancelled)}</b> Calculation Breakdown(s) cancelled.",
				indicator="blue", alert=True,
			)

	def after_insert(self):
		"""After amending a Cost Sheet, amend all linked CBs from the original."""
		if not self.amended_from:
			return
		_amend_linked_cbs(self)

	# ── Copy child tables (operations, compliance) from Inquiry ──
	def _copy_inquiry_child_tables(self):
		"""Called only on before_insert. Copies from Inquiry when Cost Sheet tables are empty."""
		try:
			opp = frappe.get_doc("Opportunity", self.inquiry)
		except frappe.DoesNotExistError:
			return

		if not self.get("operations"):
			for row in (opp.get("custom_operations") or []):
				if row.disabled:
					continue
				self.append("operations", {
					"operation": row.operation,
					"remarks":   row.remarks or "",
				})

		if not self.get("compliance"):
			for row in (opp.get("custom_compliance") or []):
				val = row.get("type") or row.get("compliance_type") or ""
				if not val:
					continue
				self.append("compliance", {"type": val})

	# ── Fetch header fields from linked Inquiry ────────────────
	def _fetch_inquiry_fields(self):
		opp = frappe.db.get_value(
			"Opportunity",
			self.inquiry,
			["custom_subject", "customer_name", "custom_colour",
			 "custom_item_group", "custom_tiep"],
			as_dict=True,
		)
		if not opp:
			return
		if opp.get("custom_subject")  and not self.subject:
			self.subject       = opp["custom_subject"]
		if opp.get("customer_name")   and not self.customer_name:
			self.customer_name = opp["customer_name"]
		if opp.get("custom_colour")   and not self.colour:
			self.colour        = cint(opp["custom_colour"])
		if opp.get("custom_item_group") and not self.item_group:
			self.item_group    = opp["custom_item_group"]
		if opp.get("custom_tiep")     and not self.tiep:
			self.tiep          = opp["custom_tiep"]

	# ── Calculate amounts in pricing_list child table ──────────
	def _calc_pricing_list_amounts(self):
		for row in self.pricing_list or []:
			qty        = flt(row.qty)
			unit_price = flt(row.unit_price)
			row.ammount = round(qty * unit_price, 2)
			sell_unit   = flt(row.selling_unit_price)
			row.selling_ammount = round(qty * sell_unit, 2)


# ── Artwork approval ────────────────────────────────────────────

ARTWORK_APPROVER_ROLE = "Artwork Approver"


def _check_artwork_approver():
	roles = frappe.get_roles(frappe.session.user)
	if ARTWORK_APPROVER_ROLE not in roles and "System Manager" not in roles:
		frappe.throw("Only users with the '" + ARTWORK_APPROVER_ROLE + "' role can approve or reject artwork.")


def _set_artwork_status(cost_sheet, status, remarks=None):
	_check_artwork_approver()
	cs = frappe.get_doc("Cost Sheet", cost_sheet)
	cs.artwork_status = status
	cs.artwork_approved_by = frappe.session.user
	cs.artwork_approved_on = frappe.utils.now()
	if remarks is not None:
		cs.artwork_remarks = remarks
	cs.flags.ignore_permissions = True
	cs.save(ignore_permissions=True)
	return {"ok": True, "status": status}


@frappe.whitelist()
def approve_artwork(cost_sheet, remarks=None):
	return _set_artwork_status(cost_sheet, "Approved", remarks)


@frappe.whitelist()
def reject_artwork(cost_sheet, remarks=None):
	return _set_artwork_status(cost_sheet, "Rejected", remarks)


# ── Module-level helpers ────────────────────────────────────────

def _linked_cb_names(cost_sheet_doc):
	"""Return a deduplicated list of all CB names linked via Cost Items."""
	seen = set()
	for row in (cost_sheet_doc.pricing_list or []):
		if not row.item:
			continue
		try:
			ci = frappe.get_doc("cost Item", row.item)
		except frappe.DoesNotExistError:
			continue
		for calc in (ci.calculations or []):
			if calc.calculation_breakdown and calc.calculation_breakdown not in seen:
				seen.add(calc.calculation_breakdown)
				yield calc.calculation_breakdown


def _amend_linked_cbs(new_doc):
	"""
	For each cancelled CB in the original Cost Sheet, create an amendment
	and re-link it on the shared Cost Item so the new Cost Sheet picks it up.
	"""
	try:
		old_doc = frappe.get_doc("Cost Sheet", new_doc.amended_from)
	except frappe.DoesNotExistError:
		return

	amended_count = 0

	for row in (old_doc.pricing_list or []):
		if not row.item:
			continue
		try:
			ci = frappe.get_doc("cost Item", row.item)
		except frappe.DoesNotExistError:
			continue

		ci_dirty = False
		for calc in ci.calculations:
			old_cb_name = calc.calculation_breakdown
			if not old_cb_name:
				continue

			try:
				old_cb = frappe.get_doc("Calculation Breakdown", old_cb_name)
			except frappe.DoesNotExistError:
				continue

			if old_cb.docstatus != 2:
				continue  # only amend cancelled CBs

			# Create an amendment of the cancelled CB
			new_cb = frappe.copy_doc(old_cb)
			new_cb.docstatus   = 0
			new_cb.amended_from = old_cb_name
			# Clear submit-only fields so it opens as a clean draft
			new_cb.insert(ignore_permissions=True)

			# Point this Cost Item calculation row to the new CB
			calc.calculation_breakdown = new_cb.name
			calc.unit_cost = flt(old_cb.unit_cost)
			ci_dirty = True
			amended_count += 1

		if ci_dirty:
			ci.save(ignore_permissions=True)

	if amended_count:
		frappe.msgprint(
			f"<b>{amended_count}</b> Calculation Breakdown(s) amended and ready to edit. "
			"Open each item's calculation panel to review and re-submit.",
			indicator="blue",
		)
