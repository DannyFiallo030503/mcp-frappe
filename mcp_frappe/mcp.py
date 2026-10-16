from frappe_mcp import MCP

mcp = MCP(name="frappe-mcp")


@mcp.register()
def handle_mcp():
    """Serve MCP requests over Frappe's authenticated Streamable HTTP endpoint."""
    import frappe

    # Frappe builds this reverse map from cached app module lists during site
    # initialization. After an app or Module Def is installed, the cache can
    # be stale for subsequent HTTP requests and native document controllers
    # then fail with ``Module ... not found``. Refresh installed modules from
    # the authoritative Module Def records for each MCP request.
    if frappe.local.module_app is None:
        frappe.local.module_app = {}
    installed_apps = frappe.get_installed_apps()
    for row in frappe.get_all(
        "Module Def",
        fields=["name", "app_name"],
        filters={"app_name": ["in", installed_apps]},
    ):
        frappe.local.module_app[frappe.scrub(row.name)] = row.app_name

    import mcp_frappe.tools.customizations  # noqa: F401
    import mcp_frappe.tools.discovery  # noqa: F401
    import mcp_frappe.tools.documents  # noqa: F401
    import mcp_frappe.tools.advanced  # noqa: F401
