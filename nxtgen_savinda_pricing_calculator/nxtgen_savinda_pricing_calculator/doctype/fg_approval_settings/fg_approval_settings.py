from frappe.model.document import Document


class FGApprovalSettings(Document):
	def on_update(self):
		from nxtgen_savinda_pricing_calculator.api.fg_approval import ensure_review_permissions
		ensure_review_permissions(self)
