"""Exercise DocType variants, field types, and supported field customizations.

This runner performs temporary schema and record writes through the real MCP
stdio transport. It is limited to loopback ``.localhost`` sites and deletes
records, Custom Fields, Property Setters, and DocType metadata through Frappe.
Frappe leaves physical tables after DocType deletion; a separate database
cleanup may drop only verified-empty ``tabMCP Matrix ...`` tables.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


FIELD_TYPES = [
    "Autocomplete", "Attach", "Attach Image", "Barcode", "Button", "Check",
    "Code", "Color", "Column Break", "Currency", "Data", "Date", "Datetime",
    "Duration", "Dynamic Link", "Float", "Geolocation", "Heading",
    "HTML", "HTML Editor", "Icon", "Image", "Int", "JSON", "Link", "Long Text",
    "Markdown Editor", "Password", "Percent", "Phone", "Read Only", "Rating",
    "Fold", "Section Break", "Select", "Signature", "Small Text", "Tab Break", "Table",
    "Table MultiSelect", "Text", "Text Editor", "Time",
]


def result_value(result):
    if result.isError:
        raise RuntimeError(result.content[0].text if result.content else "MCP tool failed")
    structured = result.structuredContent or {}
    if isinstance(structured, dict) and "result" in structured:
        return structured["result"]
    if result.content:
        return json.loads(result.content[0].text)
    return None


async def call(session, tool: str, arguments: dict):
    return result_value(await session.call_tool(tool, arguments))


async def full_schema(session, doctype: str) -> dict:
    """Read every metadata property; the compact default leaves out layout fields and flags."""
    schema = await call(session, "get_doctype_schema", {"doctype": doctype, "detail": "full"})
    return schema["doctype"]


async def create_doctype(session, name: str, fields: list[dict], **flags):
    payload = {"app": "erp_app", "module": "CubaZone", "name": name, "fields": fields, **flags}
    preview = await call(session, "create_doctype", payload)
    if not preview.get("requires_confirmation"):
        raise AssertionError(f"No create preview for {name}")
    created = await call(session, "create_doctype", {**payload, "confirm": True})
    if not created.get("created") or not created.get("native_files_complete"):
        raise AssertionError(f"Incomplete DocType creation for {name}")
    print(f"created DocType variant: {name.rsplit(' ', 1)[-1]}", flush=True)
    return created


async def cleanup_doctype(session, name: str) -> bool:
    async def delete_rows(doctype: str, filter_key: str, filter_value: str) -> bool:
        rows = await call(
            session,
            "list_documents",
            {
                "doctype": doctype,
                "filters": {filter_key: filter_value},
                "fields": ["name"],
                "limit": 100,
            },
        )
        for row in rows:
            preview = await call(
                session, "delete_document", {"doctype": doctype, "name": row["name"]}
            )
            await call(
                session,
                "delete_document",
                {
                    "doctype": doctype,
                    "name": row["name"],
                    "expected_modified": preview.get("modified"),
                    "confirm": True,
                },
            )
        return not await call(
            session,
            "list_documents",
            {
                "doctype": doctype,
                "filters": {filter_key: filter_value},
                "fields": ["name"],
                "limit": 100,
            },
        )

    for attempt in range(4):
        try:
            # Delete dependent customizations and records while their parent
            # metadata still exists; deleting a DocType does not drop its SQL table.
            for dependent, key in (("Custom Field", "dt"), ("Property Setter", "doc_type")):
                if not await delete_rows(dependent, key, name):
                    raise RuntimeError(f"Temporary {dependent} rows remain for {name}")
            rows = await call(
                session,
                "list_documents",
                {"doctype": "DocType", "filters": {"name": name}, "fields": ["name"], "limit": 1},
            )
            if not rows:
                return True
            schema = await full_schema(session, name)
            if schema.get("is_virtual") or schema.get("istable"):
                records = []
            elif schema.get("issingle"):
                try:
                    single = await call(session, "get_document", {"doctype": name, "name": name})
                    records = [{"name": single["name"]}]
                except Exception:
                    records = []
            else:
                records = await call(
                    session,
                    "list_documents",
                    {"doctype": name, "fields": ["name"], "limit": 100},
                )
            for record in records:
                preview = await call(
                    session, "delete_document", {"doctype": name, "name": record["name"]}
                )
                await call(
                    session,
                    "delete_document",
                    {
                        "doctype": name,
                        "name": record["name"],
                        "expected_modified": preview.get("modified"),
                        "confirm": True,
                    },
                )
            preview = await call(session, "delete_document", {"doctype": "DocType", "name": name})
            result = await call(
                session,
                "delete_document",
                {
                    "doctype": "DocType",
                    "name": name,
                    "expected_modified": preview.get("modified"),
                    "confirm": True,
                },
            )
            if result.get("deleted"):
                return True
        except Exception as error:
            print(f"cleanup attempt {attempt + 1} failed for {name}: {type(error).__name__}: {str(error)[:240]}", flush=True)
            await asyncio.sleep(2 + attempt)
    try:
        rows = await call(
            session,
            "list_documents",
            {"doctype": "DocType", "filters": {"name": name}, "fields": ["name"], "limit": 1},
        )
        return not rows
    except Exception:
        return False


def field(fieldname: str, fieldtype: str, label: str | None = None, **kwargs) -> dict:
    return {"fieldname": fieldname, "fieldtype": fieldtype, "label": label or fieldname, **kwargs}


def options_for(fieldtype: str, child_name: str, doctype_name: str) -> dict:
    if fieldtype == "Select":
        return {"options": "Alpha\nBeta", "default": "Alpha"}
    if fieldtype == "Link":
        return {"options": "User"}
    if fieldtype == "Dynamic Link":
        return {"options": "mcp_matrix_link_target"}
    if fieldtype in {"Table", "Table MultiSelect"}:
        return {"options": child_name}
    if fieldtype == "Rating":
        return {"options": "5"}
    if fieldtype == "Code":
        return {"options": "Python"}
    return {}


async def run() -> dict:
    url = os.environ.get("FRAPPE_URL", "")
    site = os.environ.get("FRAPPE_SITE", "")
    hostname = urlparse(url).hostname or ""
    if os.environ.get("FRAPPE_ALLOW_DOCTYPE_MATRIX") != "YES":
        raise RuntimeError("Set FRAPPE_ALLOW_DOCTYPE_MATRIX=YES to opt into temporary schema writes")
    if not (
        hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(".localhost")
    ) or not site.endswith(".localhost"):
        raise RuntimeError("DocType matrix is restricted to loopback .localhost sites")
    if not os.environ.get("FRAPPE_APPS_PATH"):
        raise RuntimeError("Set FRAPPE_APPS_PATH to the shared local Bench apps directory")

    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "frappe_mcp.standalone"],
        env=os.environ.copy(),
        cwd=str(root),
    )
    suffix = uuid.uuid4().hex[:7]
    prefix = f"MCP Matrix {suffix}"
    names = {
        key: f"{prefix} {key.title()}"
        for key in (
            "child", "table", "custom", "site_custom", "single", "tree", "submit",
            "virtual_child", "virtual",
        )
    }
    cleanup_names = list(names.values())
    created_names: list[str] = []
    evidence: dict = {
        "field_types_from_frappe_15_docfield_options": len(FIELD_TYPES),
        "field_type_names": FIELD_TYPES,
        "doctype_variants": {},
        "custom_field_types": {},
        "property_setters": {},
        "record_checks": {},
        "export_rollback": {},
        "cleanup": {},
        "errors": [],
    }

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            try:
                # A regular child target supports both Table and Table MultiSelect.
                child = await create_doctype(
                    session,
                    names["child"],
                    [field("user", "Link", "User", options="User"), field("note", "Data")],
                    is_child_table=True,
                )
                created_names.append(names["child"])
                evidence["doctype_variants"]["child"] = bool(child["native_files_complete"])

                fields = [
                    field("title", "Data", "Title", reqd=1, in_list_view=1, search_index=1),
                    field("mcp_matrix_link_target", "Link", options="DocType"),
                ]
                for index, fieldtype in enumerate(FIELD_TYPES):
                    fname = f"ft_{index:02d}"
                    fields.append(field(fname, fieldtype, **options_for(fieldtype, names["child"], names["table"])))
                fields.extend([
                    field("children", "Table", options=names["child"]),
                    field("selected_users", "Table MultiSelect", options=names["child"]),
                ])
                property_values = {
                    "editable_grid": 1,
                    "quick_entry": 1,
                    "track_changes": 1,
                    "track_seen": 1,
                    "track_views": 1,
                    "allow_rename": 1,
                    "allow_import": 1,
                    "allow_copy": 1,
                    "title_field": "title",
                    "search_fields": "title",
                    "sort_field": "modified",
                    "sort_order": "DESC",
                    "description": "Temporary MCP field matrix",
                    "max_attachments": 2,
                }
                normal = await create_doctype(
                    session,
                    names["table"],
                    fields,
                    properties=property_values,
                    permissions=[
                        {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1}
                    ],
                )
                created_names.append(names["table"])
                schema = await full_schema(session, names["table"])
                schema_field_types = {row.get("fieldtype") for row in schema.get("fields", [])}
                missing_types = sorted(set(FIELD_TYPES) - schema_field_types)
                generated_json_path = Path(normal["files"]["json"])
                generated_json = json.loads(generated_json_path.read_text(encoding="utf-8"))
                generated_field_types = {
                    row.get("fieldtype") for row in generated_json.get("fields", [])
                }
                evidence["native_file_and_database_roundtrip"] = {
                    "generated_json_exists": generated_json_path.is_file(),
                    "json_has_all_43_field_types": set(FIELD_TYPES).issubset(generated_field_types),
                    "json_has_requested_properties": all(
                        generated_json.get(key) == value for key, value in property_values.items()
                    ),
                    "json_has_permission_row": any(
                        row.get("role") == "System Manager" and row.get("create")
                        for row in generated_json.get("permissions", [])
                    ),
                    "database_doctype_row_matches": schema.get("name") == names["table"]
                    and schema.get("module") == "CubaZone"
                    and schema.get("istable") == 0
                    and schema.get("is_submittable") == 0
                    and set(FIELD_TYPES).issubset(schema_field_types),
                }
                evidence["doctype_variants"]["regular"] = {
                    "created_with_native_files": bool(normal["native_files_complete"]),
                    "field_type_count_roundtripped": len(set(FIELD_TYPES) & schema_field_types),
                    "missing_field_types": missing_types,
                    "properties_roundtripped": all(
                        schema.get(key) == value for key, value in property_values.items()
                    ),
                    "permission_rows_roundtripped": any(
                        row.get("role") == "System Manager" and row.get("create")
                        for row in schema.get("permissions", [])
                    ),
                }

                custom_base = await create_doctype(
                    session,
                    names["custom"],
                    [field("title", "Data", "Title")],
                )
                created_names.append(names["custom"])
                evidence["doctype_variants"]["custom_field_matrix_target"] = bool(
                    custom_base["native_files_complete"]
                )

                # Frappe site-custom DocTypes are stored in site metadata, unlike
                # standard DocTypes they do not generate files in an app checkout.
                site_custom_payload = {
                    "app": "erp_app",
                    "module": "CubaZone",
                    "name": names["site_custom"],
                    "fields": [field("title", "Data", reqd=1)],
                    "is_custom": True,
                }
                custom_preview = await call(session, "create_doctype", site_custom_payload)
                custom_created = await call(
                    session, "create_doctype", {**site_custom_payload, "confirm": True}
                )
                custom_schema = await full_schema(session, names["site_custom"])
                evidence["doctype_variants"]["site_custom"] = {
                    "preview_without_source_path": custom_preview.get("destination") is None
                    and custom_preview.get("expected_native_files") == [],
                    "created": bool(custom_created.get("created")),
                    "metadata_custom_flag": bool(custom_schema.get("custom")),
                    "no_generated_files": custom_created.get("files") == {}
                    and custom_created.get("native_files_complete") is None,
                }
                await call(
                    session,
                    "create_document",
                    {
                        "doctype": names["site_custom"],
                        "values": {"title": f"custom-record-{suffix}"},
                        "confirm": True,
                    },
                )
                custom_records = await call(
                    session,
                    "list_documents",
                    {
                        "doctype": names["site_custom"],
                        "fields": ["name", "title"],
                        "limit": 2,
                    },
                )
                evidence["record_checks"]["site_custom"] = any(
                    row.get("title") == f"custom-record-{suffix}" for row in custom_records
                )

                singles = await create_doctype(
                    session,
                    names["single"],
                    [field("setting_value", "Data"), field("enabled", "Check")],
                    is_single=True,
                )
                created_names.append(names["single"])
                evidence["doctype_variants"]["single"] = bool(singles["native_files_complete"])

                tree = await create_doctype(
                    session,
                    names["tree"],
                    [field("title", "Data", reqd=1), field("parent_tree", "Link", options=names["tree"])],
                    is_tree=True,
                    properties={"nsm_parent_field": "parent_tree", "autoname": "field:title"},
                )
                created_names.append(names["tree"])
                evidence["doctype_variants"]["tree"] = bool(tree["native_files_complete"])

                submit = await create_doctype(
                    session,
                    names["submit"],
                    [field("title", "Data", reqd=1), field("allowed_after_submit", "Data", allow_on_submit=1)],
                    is_submittable=True,
                    properties={"autoname": "field:title", "track_changes": 1},
                    permissions=[
                        {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1,
                         "submit": 1, "cancel": 1, "amend": 1}
                    ],
                )
                created_names.append(names["submit"])
                evidence["doctype_variants"]["submittable"] = bool(submit["native_files_complete"])

                # Virtual metadata and matching virtual child definitions are valid,
                # but document CRUD requires an app controller that implements storage.
                vchild = await create_doctype(
                    session,
                    names["virtual_child"],
                    [field("user", "Link", options="User")],
                    is_child_table=True,
                    properties={"is_virtual": 1},
                )
                created_names.append(names["virtual_child"])
                virtual = await create_doctype(
                    session,
                    names["virtual"],
                    [field("title", "Data"), field("children", "Table", options=names["virtual_child"])],
                    properties={"is_virtual": 1},
                )
                created_names.append(names["virtual"])
                virtual_schema = await full_schema(session, names["virtual"])
                evidence["doctype_variants"]["virtual"] = {
                    "parent_created": bool(virtual["native_files_complete"]),
                    "child_created": bool(vchild["native_files_complete"]),
                    "metadata_is_virtual": bool(virtual_schema.get("is_virtual")),
                    "generic_record_crud_tested": False,
                    "crud_limit": "Virtual doctypes need a custom controller/storage implementation.",
                }

                # Exercise actual storage for native record variants.
                single_values = {"setting_value": "single-roundtrip", "enabled": 1}
                await call(session, "create_document", {"doctype": names["single"], "values": single_values, "confirm": True})
                single_read = await call(session, "get_document", {"doctype": names["single"], "name": names["single"], "fields": list(single_values)})
                evidence["record_checks"]["single"] = all(single_read.get(k) == v for k, v in single_values.items())

                value_fields = {
                    "title": f"parent-{suffix}",
                    f"ft_{FIELD_TYPES.index('Select'):02d}": "Alpha",
                    f"ft_{FIELD_TYPES.index('Link'):02d}": "Administrator",
                    "mcp_matrix_link_target": "User",
                    f"ft_{FIELD_TYPES.index('Dynamic Link'):02d}": "Administrator",
                    f"ft_{FIELD_TYPES.index('Check'):02d}": 1,
                    f"ft_{FIELD_TYPES.index('Int'):02d}": 12,
                    "children": [{"user": "Administrator", "note": "child-row-check"}],
                    "selected_users": [{"user": "Administrator", "note": "multi-select-check"}],
                }
                parent_record = await call(session, "create_document", {
                    "doctype": names["table"], "values": value_fields, "confirm": True,
                })
                parent_read = await call(session, "get_document", {
                    "doctype": names["table"], "name": parent_record["name"],
                    "fields": ["title", "children", "selected_users", "mcp_matrix_link_target",
                               f"ft_{FIELD_TYPES.index('Select'):02d}", f"ft_{FIELD_TYPES.index('Link'):02d}",
                               f"ft_{FIELD_TYPES.index('Dynamic Link'):02d}", f"ft_{FIELD_TYPES.index('Int'):02d}"],
                })
                evidence["record_checks"]["select_link_dynamic_link"] = (
                    parent_read.get(f"ft_{FIELD_TYPES.index('Select'):02d}") == "Alpha"
                    and parent_read.get(f"ft_{FIELD_TYPES.index('Link'):02d}") == "Administrator"
                    and parent_read.get("mcp_matrix_link_target") == "User"
                    and parent_read.get(f"ft_{FIELD_TYPES.index('Dynamic Link'):02d}") == "Administrator"
                )
                evidence["record_checks"]["table_and_multiselect_rows"] = (
                    len(parent_read.get("children", [])) == 1
                    and parent_read["children"][0].get("note") == "child-row-check"
                    and len(parent_read.get("selected_users", [])) == 1
                    and parent_read["selected_users"][0].get("user") == "Administrator"
                )

                tree_root = await call(session, "create_document", {"doctype": names["tree"], "values": {"title": f"root-{suffix}"}, "confirm": True})
                tree_leaf = await call(session, "create_document", {"doctype": names["tree"], "values": {"title": f"leaf-{suffix}", "parent_tree": tree_root["name"]}, "confirm": True})
                tree_read = await call(session, "get_document", {"doctype": names["tree"], "name": tree_leaf["name"], "fields": ["title", "parent_tree"]})
                evidence["record_checks"]["tree_parent_link"] = tree_read.get("parent_tree") == tree_root["name"]

                sub_doc = await call(session, "create_document", {"doctype": names["submit"], "values": {"title": f"draft-{suffix}"}, "confirm": True})
                sub_read = await call(session, "get_document", {"doctype": names["submit"], "name": sub_doc["name"], "fields": ["title"]})
                evidence["record_checks"]["submittable_draft_docstatus"] = sub_read.get("docstatus") == 0

                # Apply Custom Fields spanning every fieldtype, including the
                # dependency-bearing field types and both child-table variants.
                cf_specs = []
                cf_target = "mcp_cf_doctype_target"
                cf_specs.append((cf_target, "Link", {"options": "DocType", "insert_after": "append"}))
                for index, fieldtype in enumerate(FIELD_TYPES):
                    if fieldtype in {"Fold", "Section Break"}:
                        continue
                    fname = f"mcp_cf_{index:02d}"
                    if fieldtype == "Dynamic Link":
                        opts = {"options": cf_target}
                    elif fieldtype in {"Table", "Table MultiSelect"}:
                        opts = {"options": names["child"]}
                    else:
                        opts = options_for(fieldtype, names["child"], names["table"])
                    opts["insert_after"] = "append"
                    cf_specs.append((fname, fieldtype, opts))
                custom_results = []
                for fname, fieldtype, opts in cf_specs:
                    custom_field = field(fname, fieldtype, **opts)
                    created_cf = await call(session, "create_custom_field", {
                        "app": "erp_app", "module": "CubaZone", "doctype": names["custom"], "field": custom_field,
                    })
                    if not created_cf.get("requires_confirmation"):
                        raise AssertionError(f"No Custom Field preview for {fieldtype}")
                    created_cf = await call(session, "create_custom_field", {
                        "app": "erp_app", "module": "CubaZone", "doctype": names["custom"], "field": custom_field, "confirm": True,
                    })
                    custom_results.append((fname, fieldtype, bool(created_cf.get("name"))))
                    if len(custom_results) % 10 == 0:
                        print(f"created Custom Field types: {len(custom_results)}", flush=True)
                # Append the boundary first, then place Fold before that boundary.
                section_name = "mcp_cf_section_marker"
                section_payload = field(
                    section_name, "Section Break", "MCP Fold boundary", insert_after="append"
                )
                section_created = await call(session, "create_custom_field", {
                    "app": "erp_app", "module": "CubaZone", "doctype": names["custom"],
                    "field": section_payload, "confirm": True,
                })
                custom_results.append((section_name, "Section Break", bool(section_created.get("name"))))
                fold_name = "mcp_cf_fold_marker"
                fold_created = await call(session, "create_custom_field", {
                    "app": "erp_app", "module": "CubaZone", "doctype": names["custom"],
                    "field": field(
                        fold_name, "Fold", "MCP Fold marker",
                        insert_after=f"mcp_cf_{len(FIELD_TYPES) - 1:02d}",
                    ), "confirm": True,
                })
                custom_results.append((fold_name, "Fold", bool(fold_created.get("name"))))
                custom_field_coverage = sorted({row[1] for row in custom_results})
                custom_schema = await full_schema(session, names["custom"])
                field_order = [row.get("fieldname") for row in custom_schema.get("fields", [])]
                fold_index = field_order.index(fold_name) if fold_name in field_order else -1
                evidence["custom_field_types"] = {
                    "count": len(custom_field_coverage),
                    "created_custom_field_rows": len(custom_results),
                    "all_created": all(row[2] for row in custom_results),
                    "created_types": custom_field_coverage,
                    "fold_immediately_before_section_break": (
                        fold_index >= 0 and fold_index + 1 < len(field_order)
                        and field_order[fold_index + 1] == section_name
                    ),
                }

                # The public MCP setter allowlist, with a distinct target field
                # for each property so each native setter is independent.
                # set_field_property infers property_type; Check values merge as booleans.
                setter_values = {
                    "label": "MCP Updated Label",
                    "hidden": 1,
                    "read_only": 1,
                    "reqd": 1,
                    "default": "defaulted",
                    "in_list_view": 1,
                    "in_standard_filter": 1,
                    "options": "Changed options",
                    "description": "Description from Property Setter",
                    "depends_on": "eval:doc.title",
                    "mandatory_depends_on": "eval:doc.title",
                    "read_only_depends_on": "eval:doc.title",
                    "collapsible": 1,
                    "bold": 1,
                    "width": "160",
                }
                property_results = {}
                for index, (prop, prop_value) in enumerate(setter_values.items()):
                    fname = f"mcp_ps_{index:02d}"
                    cf_payload = field(fname, "Data", f"PS target {prop}")
                    await call(session, "create_custom_field", {
                        "app": "erp_app", "module": "CubaZone", "doctype": names["table"], "field": cf_payload, "confirm": True,
                    })
                    args = {
                        "app": "erp_app", "module": "CubaZone", "doctype": names["table"],
                        "fieldname": fname, "property": prop, "value": prop_value,
                    }
                    preview = await call(session, "set_field_property", args)
                    if not preview.get("requires_confirmation"):
                        raise AssertionError(f"No Property Setter preview for {prop}")
                    saved = await call(session, "set_field_property", {**args, "confirm": True})
                    property_results[prop] = bool(saved.get("name"))
                merged = await full_schema(session, names["table"])
                merged_fields = {row.get("fieldname"): row for row in merged.get("fields", [])}
                mismatches = {}
                for index, (prop, prop_value) in enumerate(setter_values.items()):
                    expected = bool(prop_value) if isinstance(prop_value, int) else prop_value
                    actual = merged_fields.get(f"mcp_ps_{index:02d}", {}).get(prop)
                    if actual != expected:
                        mismatches[prop] = {"expected": expected, "actual": actual}
                property_results["merged_schema_values_match"] = not mismatches
                property_results["merged_schema_mismatches"] = mismatches
                evidence["property_setters"] = property_results

                doc_property_args = {
                    "app": "erp_app", "module": "CubaZone", "doctype": names["table"],
                    "property": "description", "value": "Description set on the DocType itself",
                }
                await call(session, "set_field_property", doc_property_args)
                doc_property_setter = await call(
                    session, "set_field_property", {**doc_property_args, "confirm": True}
                )
                doc_property_schema = await full_schema(session, names["table"])
                evidence["property_setters"]["doctype_level_description"] = (
                    bool(doc_property_setter.get("name"))
                    and doc_property_schema.get("description") == doc_property_args["value"]
                )

                # Apply the Custom Field and Property Setter APIs to other major
                # DocType variants, proving their validators accept each family.
                variant_results = {}
                for key in ("child", "single", "tree", "submit"):
                    fieldname = f"mcp_variant_{key}"
                    created_cf = await call(session, "create_custom_field", {
                        "app": "erp_app", "module": "CubaZone", "doctype": names[key],
                        "field": field(fieldname, "Data", f"Variant {key}", insert_after="append"), "confirm": True,
                    })
                    setter = await call(session, "set_field_property", {
                        "app": "erp_app", "module": "CubaZone", "doctype": names[key],
                        "fieldname": fieldname, "property": "label", "value": f"Updated {key}",
                        "property_type": "Data", "confirm": True,
                    })
                    variant_schema = await full_schema(session, names[key])
                    variant_fields = {row.get("fieldname"): row for row in variant_schema.get("fields", [])}
                    variant_results[key] = bool(
                        created_cf.get("name") and setter.get("name")
                        and variant_fields.get(fieldname, {}).get("label") == f"Updated {key}"
                    )
                evidence["property_setters"]["doctype_variant_roundtrip"] = variant_results

                # Export the test DocTypes' native customization files,
                # then roll back to the exact prior app state.
                export_args = {"app": "erp_app", "module": "CubaZone", "doctypes": created_names}
                export_preview = await call(session, "export_customizations", export_args)
                if export_preview.get("nothing_to_export"):
                    evidence["export_rollback"] = {"nothing_to_export": True}
                else:
                    exported = await call(session, "export_customizations", {
                        **export_args, "confirm": True,
                        "expected_preview": export_preview["preview_id"],
                    })
                    rollback_id = exported.get("rollback_id")
                    rollback_preview = await call(session, "rollback_export", {
                        "app": "erp_app", "module": "CubaZone", "rollback_id": rollback_id,
                    })
                    rolled = await call(session, "rollback_export", {
                        "app": "erp_app", "module": "CubaZone", "rollback_id": rollback_id,
                        "expected_preview": rollback_preview["preview_id"], "confirm": True,
                    })
                    evidence["export_rollback"] = {
                        "previewed": bool(export_preview.get("preview_id")),
                        "exported": bool(rollback_id),
                        "native_custom_files": all(
                            "/custom/" in path or path.endswith(("client_script.json", "hooks.py"))
                            for path in exported.get("files", [])
                        ),
                        "rolled_back": bool(rolled.get("rolled_back")),
                    }
            except Exception as error:
                evidence["errors"].append(f"{type(error).__name__}: {str(error)[:500]}")
            finally:
                for name in reversed(cleanup_names):
                    evidence["cleanup"][name] = await cleanup_doctype(session, name)

    print(json.dumps(evidence, indent=2, sort_keys=True))
    failed_cleanup = [name for name, ok in evidence["cleanup"].items() if not ok]
    if failed_cleanup:
        raise RuntimeError(f"Temporary DocTypes need cleanup: {failed_cleanup}")
    if evidence["errors"]:
        raise RuntimeError("Matrix had errors; inspect the redacted JSON above")
    if evidence["doctype_variants"].get("regular", {}).get("missing_field_types"):
        raise RuntimeError("Some field types did not roundtrip")
    if not all(evidence.get("native_file_and_database_roundtrip", {}).values()):
        raise RuntimeError("Generated DocType JSON or database metadata did not match the request")
    if not evidence["custom_field_types"].get("all_created"):
        raise RuntimeError("Some Custom Field types did not create")
    if evidence["custom_field_types"].get("count") != len(FIELD_TYPES):
        raise RuntimeError("The Custom Field type matrix is incomplete")
    if not evidence["property_setters"].get("merged_schema_values_match"):
        raise RuntimeError("Property Setter values did not merge into schema")
    if not evidence["property_setters"].get("doctype_level_description"):
        raise RuntimeError("DocType-level Property Setter did not roundtrip")
    if not all(evidence["property_setters"].get("doctype_variant_roundtrip", {}).values()):
        raise RuntimeError("Custom Field/Property Setter variant coverage failed")
    if not evidence["export_rollback"].get("rolled_back"):
        raise RuntimeError("Export rollback did not complete")
    return evidence


if __name__ == "__main__":
    asyncio.run(run())
