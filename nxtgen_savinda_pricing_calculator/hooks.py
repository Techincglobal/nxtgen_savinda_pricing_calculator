app_name = "nxtgen_savinda_pricing_calculator"
app_title = "Nxtgen Savinda Pricing Calculator"
app_publisher = "Techincglobal.com"
app_description = "Pricing Calculator for savinda  printing "
app_email = "info@techincglobal.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "nxtgen_savinda_pricing_calculator",
# 		"logo": "/assets/nxtgen_savinda_pricing_calculator/logo.png",
# 		"title": "Nxtgen Savinda Pricing Calculator",
# 		"route": "/nxtgen_savinda_pricing_calculator",
# 		"has_permission": "nxtgen_savinda_pricing_calculator.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/nxtgen_savinda_pricing_calculator/css/nxtgen_savinda_pricing_calculator.css"
fixtures = [
	{"dt": "Costing Configuration"},
	{"dt": "Item Group", "filters": [["item_group_name", "=", "Costing Rate Items"]]},
	{"dt": "Item",       "filters": [["item_code", "like", "CALC-%"]]},
	{"dt": "Cost Fact"},
	{"dt": "Offset Spec"},
	{"dt": "Print Format", "filters": [["doc_type", "in", ["Cost Sheet", "Calculation Breakdown", "Savinda Quotation"]]]},
]

app_include_js = [
	"https://unpkg.com/vue@3/dist/vue.global.prod.js",
    "/assets/nxtgen_savinda_pricing_calculator/js/offset_calculator.js",
	]

# include js, css files in header of web template
# web_include_css = "/assets/nxtgen_savinda_pricing_calculator/css/nxtgen_savinda_pricing_calculator.css"
# web_include_js = "/assets/nxtgen_savinda_pricing_calculator/js/nxtgen_savinda_pricing_calculator.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "nxtgen_savinda_pricing_calculator/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "nxtgen_savinda_pricing_calculator/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "nxtgen_savinda_pricing_calculator.utils.jinja_methods",
# 	"filters": "nxtgen_savinda_pricing_calculator.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "nxtgen_savinda_pricing_calculator.install.before_install"
# after_install = "nxtgen_savinda_pricing_calculator.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "nxtgen_savinda_pricing_calculator.uninstall.before_uninstall"
# after_uninstall = "nxtgen_savinda_pricing_calculator.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "nxtgen_savinda_pricing_calculator.utils.before_app_install"
# after_app_install = "nxtgen_savinda_pricing_calculator.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "nxtgen_savinda_pricing_calculator.utils.before_app_uninstall"
# after_app_uninstall = "nxtgen_savinda_pricing_calculator.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "nxtgen_savinda_pricing_calculator.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"nxtgen_savinda_pricing_calculator.tasks.all"
# 	],
# 	"daily": [
# 		"nxtgen_savinda_pricing_calculator.tasks.daily"
# 	],
# 	"hourly": [
# 		"nxtgen_savinda_pricing_calculator.tasks.hourly"
# 	],
# 	"weekly": [
# 		"nxtgen_savinda_pricing_calculator.tasks.weekly"
# 	],
# 	"monthly": [
# 		"nxtgen_savinda_pricing_calculator.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "nxtgen_savinda_pricing_calculator.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "nxtgen_savinda_pricing_calculator.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "nxtgen_savinda_pricing_calculator.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["nxtgen_savinda_pricing_calculator.utils.before_request"]
# after_request = ["nxtgen_savinda_pricing_calculator.utils.after_request"]

# Job Events
# ----------
# before_job = ["nxtgen_savinda_pricing_calculator.utils.before_job"]
# after_job = ["nxtgen_savinda_pricing_calculator.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"nxtgen_savinda_pricing_calculator.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

