app_name = "eu_einvoice"
app_title = "European e-Invoice"
app_publisher = "ALYF GmbH"
app_description = "Create and import european e-invoices with ERPNext"
app_email = "hallo@alyf.de"
app_license = "gpl-3.0"

# Apps
# ------------------

required_apps = ["frappe/erpnext"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "eu_einvoice",
# 		"logo": "/assets/eu_einvoice/logo.png",
# 		"title": "European e-Invoice",
# 		"route": "/eu_einvoice",
# 		"has_permission": "eu_einvoice.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/eu_einvoice/css/eu_einvoice.css"
# app_include_js = "/assets/eu_einvoice/js/eu_einvoice.js"

# include js, css files in header of web template
# web_include_css = "/assets/eu_einvoice/css/eu_einvoice.css"
# web_include_js = "/assets/eu_einvoice/js/eu_einvoice.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "eu_einvoice/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
	"Purchase Order": "european_e_invoice/custom/purchase_order.js",
	"Sales Invoice": "european_e_invoice/custom/sales_invoice.js",
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "eu_einvoice/public/icons.svg"

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
# 	"methods": "eu_einvoice.utils.jinja_methods",
# 	"filters": "eu_einvoice.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "eu_einvoice.install.before_install"
after_install = "eu_einvoice.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "eu_einvoice.uninstall.before_uninstall"
# after_uninstall = "eu_einvoice.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "eu_einvoice.utils.before_app_install"
# after_app_install = "eu_einvoice.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "eu_einvoice.utils.before_app_uninstall"
# after_app_uninstall = "eu_einvoice.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "eu_einvoice.notifications.get_notification_config"

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

doc_events = {
	"Sales Invoice": {
		"validate": "eu_einvoice.european_e_invoice.custom.sales_invoice.validate_doc",
	}
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"eu_einvoice.tasks.all"
# 	],
# 	"daily": [
# 		"eu_einvoice.tasks.daily"
# 	],
# 	"hourly": [
# 		"eu_einvoice.tasks.hourly"
# 	],
# 	"weekly": [
# 		"eu_einvoice.tasks.weekly"
# 	],
# 	"monthly": [
# 		"eu_einvoice.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "eu_einvoice.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "eu_einvoice.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "eu_einvoice.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["eu_einvoice.utils.before_request"]
# after_request = ["eu_einvoice.utils.after_request"]

# Job Events
# ----------
# before_job = ["eu_einvoice.utils.before_job"]
# after_job = ["eu_einvoice.utils.after_job"]

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
# 	"eu_einvoice.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }
