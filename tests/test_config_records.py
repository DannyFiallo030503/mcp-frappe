"""Workflows, Notifications, and the shared configuration-record save path."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from frappe_mcp import standalone

ROLES = {"Employee", "Leave Approver", "HR Manager"}
STATES = {"Draft", "Approved"}
ACTIONS = {"Approve"}
MODIFIED = "2026-10-20 10:00:00.000001"


def _in_filter(call) -> list[str]:
    return json.loads(call.params["filters"])[0][2]


def _named(known: set[str]):
    return lambda call: [{"name": name} for name in _in_filter(call) if name in known]


def _matches(row: dict[str, Any], filters: Any) -> bool:
    if isinstance(filters, dict):
        return all(row.get(key) == value for key, value in filters.items())
    for field, operator, value in filters:
        if operator == "=" and row.get(field) != value:
            return False
        if operator == "!=" and row.get(field) == value:
            return False
        if operator == "in" and row.get(field) not in value:
            return False
    return True


class Records:
    """Records of one DocType answering list reads (any filter shape) and reads by name."""

    def __init__(self, site, doctype: str) -> None:
        self.site, self.doctype, self.rows = site, doctype, {}
        site.add("GET", standalone._resource(doctype), self._list)

    def _list(self, call) -> list[dict[str, Any]]:
        filters = json.loads(call.params.get("filters") or "{}")
        return [
            {"name": row["name"], "is_active": row.get("is_active")}
            for row in self.rows.values()
            if _matches(row, filters)
        ]

    def put(self, row: dict[str, Any]) -> dict[str, Any]:
        self.rows[row["name"]] = row
        self.site.add("GET", standalone._resource(self.doctype, row["name"]), row)
        return row


@pytest.fixture
def site(fake_site):
    fake_site.add_doctype(
        "Leave Request",
        [
            {"fieldname": "employee", "fieldtype": "Link", "options": "Employee"},
            {"fieldname": "approver", "fieldtype": "Link", "options": "User"},
            {"fieldname": "email", "fieldtype": "Data", "options": "Email"},
            {"fieldname": "status", "fieldtype": "Select", "options": "Open\nApproved"},
            {"fieldname": "from_date", "fieldtype": "Date"},
            {"fieldname": "total", "fieldtype": "Currency"},
            {"fieldname": "details", "fieldtype": "Section Break"},
            {"fieldname": "days", "fieldtype": "Table", "options": "Leave Day"},
        ],
        is_submittable=1,
    )
    fake_site.add_doctype("Leave Day", [{"fieldname": "day", "fieldtype": "Date"}], istable=1)
    fake_site.add_doctype("Note", [{"fieldname": "title", "fieldtype": "Data"}])
    fake_site.add("GET", standalone._resource("Role"), _named(ROLES))
    fake_site.add("GET", standalone._resource("Workflow State"), _named(STATES))
    fake_site.add("GET", standalone._resource("Workflow Action Master"), _named(ACTIONS))
    fake_site.add("GET", "/api/method/frappe.client.get_count", 0)
    fake_site.add("POST", standalone._resource("Workflow State"), {"name": "x"})
    fake_site.add("POST", standalone._resource("Workflow Action Master"), {"name": "x"})
    fake_site.add("POST", standalone._resource("Workflow"), {"name": "Leave Approval",
                                                             "modified": MODIFIED})
    fake_site.add("POST", standalone._resource("Notification"), {"name": "n",
                                                                 "modified": MODIFIED})
    fake_site.workflows = Records(fake_site, "Workflow")
    fake_site.notifications = Records(fake_site, "Notification")
    return fake_site


STATES_IN = [
    {"state": "Draft", "doc_status": 0, "allow_edit": "Employee"},
    {"state": "Pending", "doc_status": 0, "allow_edit": "Leave Approver"},
    {"state": "Approved", "doc_status": 1, "allow_edit": "Leave Approver",
     "update_field": "status", "update_value": "Approved"},
    {"state": "Rejected", "doc_status": 0, "allow_edit": "Leave Approver"},
]
TRANSITIONS_IN = [
    {"state": "Draft", "action": "Submit for Approval", "next_state": "Pending",
     "allowed": "Employee"},
    {"state": "Pending", "action": "Approve", "next_state": "Approved",
     "allowed": "Leave Approver", "allow_self_approval": False},
    {"state": "Pending", "action": "Reject", "next_state": "Rejected",
     "allowed": "Leave Approver"},
    {"state": "Rejected", "action": "Submit for Approval", "next_state": "Pending",
     "allowed": "Employee"},
]


def _workflow(**kwargs) -> dict[str, Any]:
    args = {
        "doctype": "Leave Request",
        "name": "Leave Approval",
        "states": STATES_IN,
        "transitions": TRANSITIONS_IN,
        **kwargs,
    }
    return standalone.upsert_workflow(**args)


def _existing_workflow(site, **overrides) -> dict[str, Any]:
    return site.workflows.put(
        {
            "name": "Leave Approval",
            "workflow_name": "Leave Approval",
            "document_type": "Leave Request",
            "is_active": 1,
            "send_email_alert": 0,
            "workflow_state_field": "workflow_state",
            "modified": MODIFIED,
            "states": [
                {"name": "s1", "idx": 1, "parent": "Leave Approval", "state": "Draft",
                 "doc_status": "0", "allow_edit": "Employee", "message": "Fill it in"},
                {"name": "s2", "idx": 2, "parent": "Leave Approval", "state": "Pending",
                 "doc_status": "0", "allow_edit": "HR Manager"},
                {"name": "s3", "idx": 3, "state": "Approved", "doc_status": "1",
                 "allow_edit": "Leave Approver"},
            ],
            "transitions": [
                {"name": "t1", "state": "Draft", "action": "Submit for Approval",
                 "next_state": "Pending", "allowed": "Employee", "allow_self_approval": 1,
                 "condition": "doc.total > 0"},
                {"name": "t2", "state": "Pending", "action": "Approve", "next_state": "Approved",
                 "allowed": "Leave Approver", "allow_self_approval": 1},
            ],
            **overrides,
        }
    )


# Workflows


def test_workflow_preview_lists_masters_state_field_and_writes_nothing(site) -> None:
    preview = _workflow()

    assert preview["preview"] is True
    assert preview["exists"] is False
    assert preview["creates"] == {
        "Workflow State": ["Pending", "Rejected"],
        "Workflow Action Master": ["Reject", "Submit for Approval"],
    }
    assert preview["state_field"]["exists"] is False
    assert preview["state_field"]["custom_field"]["options"] == "Workflow State"
    assert preview["deactivates"] == []
    assert preview["transitions"][1]["allow_self_approval"] == 0
    assert preview["states"][2]["doc_status"] == "1"
    assert not site.calls_to("POST") and not site.calls_to("PUT")


def test_workflow_confirm_creates_masters_then_the_workflow(site) -> None:
    standalone._meta_bundle("Leave Request")

    result = _workflow(confirm=True)

    posts = [(call.path, call.json["data"]) for call in site.calls_to("POST")]
    assert [path for path, _ in posts] == [
        standalone._resource("Workflow State"),
        standalone._resource("Workflow State"),
        standalone._resource("Workflow Action Master"),
        standalone._resource("Workflow Action Master"),
        standalone._resource("Workflow"),
    ]
    assert posts[0][1] == {"doctype": "Workflow State", "workflow_state_name": "Pending"}
    workflow = posts[-1][1]
    assert workflow["workflow_name"] == "Leave Approval"
    assert workflow["is_active"] == 1
    assert [row["state"] for row in workflow["states"]] == [
        "Draft", "Pending", "Approved", "Rejected"
    ]
    assert all("condition" not in row for row in workflow["transitions"])
    assert result["created"] is True
    assert result["masters_created"]["Workflow Action Master"] == ["Reject", "Submit for Approval"]
    assert "Leave Request" not in standalone._meta_cache


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"states": [{**STATES_IN[0], "allow_edit": "Nobody"}, *STATES_IN[1:]]},
         "Unknown roles on the target site: Nobody"),
        ({"transitions": [{**TRANSITIONS_IN[0], "next_state": "Archived"}]},
         "next_state 'Archived' is not one of the listed states"),
        ({"doctype": "Note",
          "states": [{"state": "Draft", "allow_edit": "Employee"},
                     {"state": "Approved", "doc_status": 1, "allow_edit": "Employee"}],
          "transitions": [{"state": "Draft", "action": "Approve", "next_state": "Approved",
                           "allowed": "Employee"}]},
         "doc_status 1 needs a submittable DocType; Note is not submittable"),
        ({"transitions": [{**TRANSITIONS_IN[0], "condition": "doc.total > 0"}]},
         "condition is a Python expression"),
        ({"states": [{**STATES_IN[0], "evaluate_as_expression": 1}, *STATES_IN[1:]]},
         "evaluate_as_expression"),
        ({"transitions": TRANSITIONS_IN[:2]}, "reaches: Rejected"),
        ({"states": [STATES_IN[2], *STATES_IN[:2], STATES_IN[3]]},
         "give it doc_status 0"),
        ({"transitions": [*TRANSITIONS_IN,
                          {"state": "Approved", "action": "Reopen", "next_state": "Draft",
                           "allowed": "HR Manager"}]},
         "cannot go back to draft"),
        ({"states": [{**STATES_IN[2], "update_field": "days"}]}, "update_field 'days'"),
        ({"states": [{**STATES_IN[2], "update_field": ["status"]}]}, "update_field"),
        ({"workflow_state_field": "from_date"}, "must be a Link, Data, or Select field"),
        ({"transitions": [*TRANSITIONS_IN, TRANSITIONS_IN[0]]}, "listed twice"),
    ],
)
def test_workflow_refuses_invalid_input(site, change, message) -> None:
    with pytest.raises(ValueError, match=message.replace("(", r"\(").replace(")", r"\)")):
        _workflow(**change)
    assert not site.calls_to("POST")


def test_workflow_warns_about_draft_states_with_no_way_out(site) -> None:
    preview = _workflow(transitions=TRANSITIONS_IN[:3])

    assert preview["warnings"] == [
        "Draft state 'Rejected' has no transition out of it; documents there are stuck"
    ]


def test_workflow_warns_about_records_the_state_will_be_set_on(site) -> None:
    site.add("GET", "/api/method/frappe.client.get_count", 7,
             params={"filters": standalone._filters([])})

    preview = _workflow()

    assert any(warning.startswith("7 existing Leave Request records") for warning in
               preview["warnings"])


def test_workflow_preview_names_the_workflows_it_deactivates(site) -> None:
    site.workflows.put({"name": "Old Flow", "document_type": "Leave Request", "is_active": 1})
    site.workflows.put({"name": "Inactive", "document_type": "Leave Request", "is_active": 0})

    assert _workflow()["deactivates"] == ["Old Flow"]
    assert _workflow(is_active=False)["deactivates"] == []


def test_workflow_update_shows_a_diff_and_needs_the_modified_token(site) -> None:
    _existing_workflow(site)

    preview = _workflow()

    assert preview["exists"] is True
    assert preview["modified"] == MODIFIED
    changes = preview["changes"]
    assert changes["states"]["added"] == ["Rejected"]
    assert changes["states"]["changed"]["Pending"] == {
        "allow_edit": {"from": "HR Manager", "to": "Leave Approver"}
    }
    assert changes["states"]["changed"]["Approved"]["update_field"] == {
        "from": None, "to": "status"
    }
    assert changes["transitions"]["changed"] == [
        {"state": "Pending", "action": "Approve", "allowed": "Leave Approver",
         "changes": {"allow_self_approval": {"from": 1, "to": 0}}}
    ]
    assert len(changes["transitions"]["added"]) == 2
    assert any("keeps the Python condition" in warning for warning in preview["warnings"])
    with pytest.raises(ValueError, match="Document changed since preview"):
        _workflow(confirm=True)
    with pytest.raises(ValueError, match="Document changed since preview"):
        _workflow(confirm=True, expected_modified="2026-01-01 00:00:00")
    assert not site.calls_to("PUT") and not site.calls_to("POST")


def test_workflow_update_puts_modified_and_keeps_rows_it_does_not_manage(site) -> None:
    _existing_workflow(site)
    site.add("PUT", standalone._resource("Workflow", "Leave Approval"),
             {"name": "Leave Approval", "modified": "2026-10-21 09:00:00"})

    result = _workflow(confirm=True, expected_modified=MODIFIED)

    (put,) = site.calls_to("PUT")
    data = put.json["data"]
    assert data["modified"] == MODIFIED
    draft, pending = data["states"][:2]
    assert draft == {"name": "s1", "state": "Draft", "doc_status": "0", "allow_edit": "Employee",
                     "update_field": None, "update_value": None, "message": "Fill it in"}
    assert pending["name"] == "s2" and pending["allow_edit"] == "Leave Approver"
    assert "name" not in data["states"][3]
    assert data["transitions"][0]["name"] == "t1"
    assert data["transitions"][0]["condition"] == "doc.total > 0"
    assert result["created"] is False
    assert result["modified"] == "2026-10-21 09:00:00"


def test_workflow_name_of_another_doctype_is_refused(site) -> None:
    _existing_workflow(site, document_type="Note")

    with pytest.raises(ValueError, match="belongs to Note"):
        _workflow()


def test_get_workflow_returns_the_active_one(site) -> None:
    _existing_workflow(site)
    site.workflows.put({"name": "Inactive", "document_type": "Leave Request", "is_active": 0})

    result = standalone.get_workflow("Leave Request")

    workflow = result["workflow"]
    assert workflow["name"] == "Leave Approval"
    assert workflow["states"][0] == {"state": "Draft", "doc_status": "0", "allow_edit": "Employee"}
    assert workflow["transitions"][0]["has_condition"] is True
    assert "condition" not in workflow["transitions"][0]
    assert result["inactive"] == ["Inactive"]


def test_get_workflow_returns_none_without_an_active_workflow(site) -> None:
    assert standalone.get_workflow("Note") == {"doctype": "Note", "workflow": None,
                                                "inactive": []}


# _save_config_record


def test_save_config_record_update_sends_modified_and_maps_a_stale_save(site) -> None:
    site.add("PUT", standalone._resource("Notification", "n"),
             standalone.FrappeError("changed", "TimestampMismatchError"))

    with pytest.raises(ValueError, match="Document changed since preview"):
        standalone._save_config_record("Notification", {"subject": "x"}, "n", MODIFIED)

    assert site.calls_to("PUT")[0].json["data"] == {"subject": "x", "modified": MODIFIED}


def test_save_config_record_clears_the_metadata_cache(site) -> None:
    standalone._meta_bundle("Leave Request")
    standalone._meta_bundle("Note")

    standalone._save_config_record("Workflow", {"workflow_name": "w"}, "w",
                                   forget=("Leave Request",))

    assert "Leave Request" not in standalone._meta_cache
    assert "Note" in standalone._meta_cache


def _reload_during(site, method: str, path: str, monkeypatch) -> None:
    site.add(method, path, httpx.ReadError("worker reloaded"))
    monkeypatch.setattr(standalone, "_wait_for_server_ready", lambda: None)


def test_save_config_record_reconciles_a_reload_by_re_reading(site, monkeypatch) -> None:
    _reload_during(site, "POST", standalone._resource("Notification"), monkeypatch)
    values = {"name": "n", "subject": "Hi", "recipients": [{"receiver_by_role": "Employee"}]}
    site.notifications.put({**values, "modified": MODIFIED,
                            "recipients": [{"name": "r1", "receiver_by_role": "Employee"}]})

    saved, reconciled = standalone._save_config_record("Notification", values, "n")

    assert reconciled is True
    assert saved["modified"] == MODIFIED
    assert len(site.calls_to("POST")) == 1


def test_save_config_record_fails_when_the_reload_lost_the_change(site, monkeypatch) -> None:
    path = standalone._resource("Notification", "n")
    _reload_during(site, "PUT", path, monkeypatch)
    site.notifications.put({"name": "n", "subject": "Old", "modified": MODIFIED})

    with pytest.raises(RuntimeError, match="does not hold the change"):
        standalone._save_config_record("Notification", {"subject": "New"}, "n", MODIFIED)
    assert len(site.calls_to("PUT")) == 1


def test_save_config_record_needs_modified_to_move_on_update(site, monkeypatch) -> None:
    _reload_during(site, "PUT", standalone._resource("Notification", "n"), monkeypatch)
    site.notifications.put({"name": "n", "subject": "New", "modified": MODIFIED})

    with pytest.raises(RuntimeError):
        standalone._save_config_record("Notification", {"subject": "New"}, "n", MODIFIED)


# Template rule

FIELDS = {"title", "total", "name", "owner"}


@pytest.mark.parametrize(
    "text",
    [
        "Plain text, no placeholders",
        "Leave {{ doc.name }} by {{doc.owner}}",
        "Total: {{ doc.total | round(2) }} ({{ doc.total | flt(2) }})",
        "{{ doc.title | upper | truncate(20) }} {{ doc.total|round(0, 'floor') }}",
        "{{ doc.title | default(\"n/a\") | e }}",
        "Line one\n<b>{{ doc.title }}</b> 100% done }}",
    ],
)
def test_template_rule_accepts_placeholders_and_safe_filters(text) -> None:
    standalone._assert_template(text, "message", FIELDS)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{% if doc.total %}big{% endif %}", "statements"),
        ("{# note #}", "comments"),
        ("{{ doc.missing }}", "unknown field in placeholder: missing"),
        ("{{ frappe.db.sql('x') }}", "unsupported placeholder"),
        ("{{ doc.title.__class__ }}", "unsupported placeholder"),
        ("{{ doc.get('title') }}", "unsupported placeholder"),
        ("{{ doc['title'] }}", "unsupported placeholder"),
        ("{{- doc.title }}", "unsupported placeholder"),
        ("{{ doc.title ~ doc.name }}", "unsupported placeholder"),
        ("{{ doc.title | attr('x') }}", "filter .attr"),
        ("{{ doc.title | format('x') }}", "filter .format"),
        ("{{ doc.title | upper(1) }}", "filter upper takes no arguments"),
        ("{{ doc.title | truncate('5') }}", "filter truncate takes int"),
        ("{{ doc.title | default(doc.name) }}", "literals"),
        ("{{ doc.title | default('{{x}}') }}", "is not allowed"),
        ("{{ doc.total | round(2, 'up') }}", "filter round takes int, str"),
        ("{{ doc.total | round(1 + 1) }}", "literals"),
        ("Hello {{ doc.title", "unclosed"),
        ("", "non-empty"),
    ],
)
def test_template_rule_refuses_other_jinja(text, message) -> None:
    with pytest.raises(ValueError, match=message):
        standalone._assert_template(text, "message", FIELDS)


def test_template_fields_skip_layout_and_tables(site) -> None:
    fields = standalone._template_fields(standalone._meta_bundle("Leave Request")["doctype"])

    assert {"employee", "total", "name", "modified"} <= fields
    assert "details" not in fields and "days" not in fields


# Notifications


def _notification(**kwargs) -> dict[str, Any]:
    args = {
        "name": "Leave Submitted",
        "document_type": "Leave Request",
        "event": "Submit",
        "channel": "Email",
        "recipients": [{"receiver_by_document_field": "approver"}],
        "subject": "Leave {{ doc.name }} submitted",
        "message": "{{ doc.employee }} asks for leave from {{ doc.from_date }}.",
        **kwargs,
    }
    return standalone.upsert_notification(**args)


def test_notification_preview_shows_the_record_and_writes_nothing(site) -> None:
    preview = _notification(filters={"status": "Open"})

    record = preview["record"]
    assert record["event"] == "Submit"
    assert record["condition_type"] == "Filters"
    assert json.loads(record["filters"]) == {"status": "Open"}
    assert record["condition"] is None
    assert record["recipients"] == [
        {"receiver_by_document_field": "approver", "receiver_by_role": None, "cc": None,
         "bcc": None}
    ]
    assert preview["exists"] is False
    assert "app_files" not in preview
    assert not site.calls_to("POST")


def test_notification_confirm_posts_the_name_and_record(site) -> None:
    result = _notification(confirm=True)

    (post,) = site.calls_to("POST")
    data = post.json["data"]
    assert data["name"] == "Leave Submitted"
    assert data["doctype"] == "Notification"
    assert data["condition_type"] == "Python" and data["condition"] is None
    assert data["filters"] is None
    assert "notification_message" not in data
    assert result["created"] is True


def test_system_notification_sets_the_alert_text(site) -> None:
    _notification(channel="System Notification", confirm=True)

    data = site.calls_to("POST")[0].json["data"]
    assert data["notification_message"] == data["message"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"event": "Custom"}, "Unsupported event 'Custom'"),
        ({"event": "Method"}, "Unsupported event 'Method'"),
        ({"channel": "Slack"}, "Unsupported channel 'Slack'"),
        ({"channel": "SMS"}, "Unsupported channel 'SMS'"),
        ({"document_type": "Note"}, "Note is not submittable"),
        ({"document_type": "Leave Day", "event": "New"}, "child table"),
        ({"event": "Value Change"}, "value_changed must name a field"),
        ({"event": "Value Change", "value_changed": "details"}, "value_changed must name"),
        ({"event": "Value Change", "value_changed": ["status"]}, "value_changed must name"),
        ({"value_changed": "status"}, "Value Change event only"),
        ({"event": "Days Before", "date_changed": "status", "days_in_advance": 2},
         "Date or Datetime"),
        ({"event": "Days After", "date_changed": "from_date", "days_in_advance": 0},
         "positive whole number"),
        ({"event": "Days After", "date_changed": "from_date"}, "positive whole number"),
        ({"date_changed": "from_date"}, "Days Before/After only"),
        ({"subject": "{% for x in y %}"}, "statements"),
        ({"message": "{{ doc.nonexistent }}"}, "unknown field"),
        ({"subject": "x" * 141}, "longer than 140"),
        ({"filters": {"missing": 1}}, "Unknown field for Leave Request: missing"),
        ({"filters": [["Leave Day", "day", "=", "2026-01-01"]]}, "fields of the DocType itself"),
        ({"recipients": [{"receiver_by_role": "Employee", "condition": "doc.total > 1"}]},
         "condition is a Python expression"),
        ({"recipients": [{"receiver_by_document_field": "status"}]}, "Link to User"),
        ({"recipients": [{"cc": "{{ doc.email }}"}]}, "not a plain email address"),
        ({"recipients": [{"cc": "a@example.com"}], "channel": "System Notification"},
         "Email channel only"),
        ({"recipients": [{"receiver_by_role": "Nobody"}]}, "Unknown roles"),
        ({"recipients": [{}]}, "set receiver_by_document_field"),
        ({"recipients": []}, "recipients must list"),
        ({"module": "HR"}, "set is_standard"),
        ({"is_standard": True}, "needs module"),
    ],
)
def test_notification_refuses_invalid_input(site, change, message) -> None:
    with pytest.raises(ValueError, match=message):
        _notification(**change)
    assert not site.calls_to("POST")


def test_notification_accepts_each_supported_event(site) -> None:
    for event, extra in (
        ("New", {}),
        ("Save", {}),
        ("Submit", {}),
        ("Cancel", {}),
        ("Value Change", {"value_changed": "status"}),
        ("Days Before", {"date_changed": "from_date", "days_in_advance": 3}),
        ("Days After", {"date_changed": "from_date", "days_in_advance": 1}),
    ):
        record = _notification(event=event, **extra)["record"]
        assert record["event"] == event
        assert record["days_in_advance"] == extra.get("days_in_advance", 0)


def test_notification_recipient_kinds(site) -> None:
    rows = _notification(
        recipients=[
            {"receiver_by_document_field": "owner"},
            {"receiver_by_document_field": "email", "receiver_by_role": "HR Manager"},
            {"cc": "a@example.com, b@example.com", "bcc": "c@example.com"},
        ]
    )["record"]["recipients"]

    assert rows[2]["cc"] == "a@example.com\nb@example.com"
    assert rows[1]["receiver_by_role"] == "HR Manager"


def _module(site, app: str, custom: int = 0) -> None:
    site.add(
        "GET",
        standalone._resource("Module Def"),
        [{"name": "HR", "app_name": app, "custom": custom}],
        params={"filters": standalone._filters({"name": "HR"})},
    )


def test_standard_notification_previews_frappes_files(site, tmp_path, monkeypatch) -> None:
    module = tmp_path / "apps" / "example_app" / "example_app" / "hr"
    module.mkdir(parents=True)
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(tmp_path / "apps"))
    _module(site, "example_app")

    preview = _notification(is_standard=True, module="HR")

    files = preview["app_files"]
    folder = module.resolve() / "notification" / "leave_submitted"
    assert files["local_checkout"] is True
    assert files["files"][:2] == [str(folder / "leave_submitted.json"),
                                  str(folder / "leave_submitted.md")]
    assert preview["requires_developer_mode"] is True
    assert preview["record"]["is_standard"] == 1 and preview["record"]["module"] == "HR"


@pytest.mark.parametrize(("app", "custom", "message"), [
    ("erpnext", 0, "protected app"),
    ("example_app", 1, "custom Module Def"),
])
def test_standard_notification_refuses_unwritable_modules(site, app, custom, message) -> None:
    _module(site, app, custom)

    with pytest.raises(ValueError, match=message):
        _notification(is_standard=True, module="HR")


def test_notification_update_shows_changes_and_needs_the_modified_token(site) -> None:
    site.notifications.put({
        "name": "Leave Submitted", "document_type": "Leave Request", "event": "Submit",
        "channel": "Email", "enabled": 1, "subject": "Old subject",
        "message": "{{ doc.employee }} asks for leave from {{ doc.from_date }}.",
        "condition": "doc.total > 5", "condition_type": "Python", "modified": MODIFIED,
        "recipients": [{"name": "r1", "receiver_by_role": "HR Manager", "condition": "1"}],
    })

    preview = _notification()

    assert preview["exists"] is True and preview["modified"] == MODIFIED
    assert preview["changes"]["subject"] == {"from": "Old subject",
                                             "to": "Leave {{ doc.name }} submitted"}
    assert preview["changes"]["condition"] == {"from": "doc.total > 5", "to": None}
    assert "recipients" in preview["changes"]
    assert "message" not in preview["changes"]
    assert preview["warnings"] == [
        "Removes the Python condition set outside this server",
        "Removes recipient conditions set outside this server",
    ]
    with pytest.raises(ValueError, match="Document changed since preview"):
        _notification(confirm=True)

    site.add("PUT", standalone._resource("Notification", "Leave Submitted"),
             {"name": "Leave Submitted", "modified": "2026-10-21 09:00:00"})
    result = _notification(confirm=True, expected_modified=MODIFIED)

    (put,) = site.calls_to("PUT")
    assert put.json["data"]["modified"] == MODIFIED
    assert "name" not in put.json["data"]
    assert result["created"] is False


def test_list_notifications_filters_and_bounds(site) -> None:
    site.add("GET", standalone._resource("Notification"),
             [{"name": f"N{index}"} for index in range(3)])

    result = standalone.list_notifications(document_type="Leave Request", limit=2)

    assert result == {"notifications": [{"name": "N0"}, {"name": "N1"}], "truncated": True}
    params = site.calls_to("GET", standalone._resource("Notification"))[-1].params
    assert json.loads(params["filters"]) == {"document_type": "Leave Request"}
    assert params["limit_page_length"] == 3
    with pytest.raises(ValueError):
        standalone.list_notifications(limit=0)
