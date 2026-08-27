"""app/connectors/jira.py — Atlassian Jira integration & tool definitions (Connector layer).

Provides standard Jira tools (jira_search_issues, jira_get_issue, jira_create_issue,
jira_update_issue, jira_transition_issue, jira_add_comment, jira_list_projects)
interfacing with the Jira Cloud REST API (v3). Standard library only — zero pip dependencies.
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class JiraAPIError(Exception):
    def __init__(self, code, detail):
        self.code = code
        self.detail = detail
        super().__init__(f"HTTP {code}: {detail}")


def _to_adf(text):
    """Converts plain text or markdown paragraphs to Atlassian Document Format (ADF)."""
    if not text:
        return None
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    content = []
    for p in paragraphs:
        content.append({
            "type": "paragraph",
            "content": [{"type": "text", "text": p.strip()}]
        })
    if not content:
        content.append({
            "type": "paragraph",
            "content": [{"type": "text", "text": text}]
        })
    return {
        "type": "doc",
        "version": 1,
        "content": content
    }


def _from_adf(node):
    """Extracts clean plain text from Atlassian Document Format (ADF) nodes."""
    if not node:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        sub = [_from_adf(c) for c in node.get("content", [])]
        if node.get("type") in ("paragraph", "heading"):
            return "".join(sub) + "\n"
        return "".join(sub)
    if isinstance(node, list):
        return "".join(_from_adf(c) for c in node)
    return str(node)


def get_jira_tools():
    """Returns tool definitions for Jira Cloud operations."""
    return [
        {
            "name": "jira_search_issues",
            "description": (
                "Search Jira issues using Jira Query Language (JQL) or keyword search "
                "(e.g. 'project = PROJ AND status = \"To Do\"', 'assignee = currentUser()', or text query)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "jql": {
                        "type": "string",
                        "description": "JQL query string (e.g. 'project = MYPROJ AND status != Done order by created DESC').",
                    },
                    "query": {
                        "type": "string",
                        "description": "Plain text search query to find in issue summary/description (optional shorthand if JQL is not used).",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of issues to return (default 10, max 50).",
                        "default": 10,
                    },
                },
            },
        },
        {
            "name": "jira_get_issue",
            "description": "Retrieve complete details, description, status, assignee, and comments of a specific Jira issue by its key (e.g. 'PROJ-123').",
            "input_schema": {
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "The Jira issue key (e.g. 'PROJ-123').",
                    },
                },
                "required": ["issue_key"],
            },
        },
        {
            "name": "jira_create_issue",
            "description": "Create a new Jira issue, task, bug, or story.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_key": {
                        "type": "string",
                        "description": "The Jira project key (e.g. 'PROJ', 'DEV', 'ENG').",
                    },
                    "summary": {
                        "type": "string",
                        "description": "The issue title or short summary.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Detailed description of the issue.",
                    },
                    "issue_type": {
                        "type": "string",
                        "description": "Issue type name, e.g. 'Task', 'Bug', 'Story', 'Epic' (default 'Task').",
                        "default": "Task",
                    },
                    "priority": {
                        "type": "string",
                        "description": "Optional priority name (e.g. 'Highest', 'High', 'Medium', 'Low', 'Lowest').",
                    },
                },
                "required": ["project_key", "summary"],
            },
        },
        {
            "name": "jira_update_issue",
            "description": "Update fields of an existing Jira issue (summary, description, or priority).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "The Jira issue key to update (e.g. 'PROJ-123').",
                    },
                    "summary": {
                        "type": "string",
                        "description": "New summary/title for the issue.",
                    },
                    "description": {
                        "type": "string",
                        "description": "New description for the issue.",
                    },
                    "priority": {
                        "type": "string",
                        "description": "New priority name (e.g. 'High', 'Medium', 'Low').",
                    },
                },
                "required": ["issue_key"],
            },
        },
        {
            "name": "jira_transition_issue",
            "description": (
                "Move a Jira issue to a new status workflow state (e.g. 'In Progress', 'Done', 'In Review', 'Closed'). "
                "If transition_name or transition_id is omitted, returns the list of available valid status transitions."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "The Jira issue key (e.g. 'PROJ-123').",
                    },
                    "transition_name": {
                        "type": "string",
                        "description": "Target status name to transition into (e.g. 'Done', 'In Progress', 'Under Review').",
                    },
                    "transition_id": {
                        "type": "string",
                        "description": "Specific transition ID (optional if transition_name is provided).",
                    },
                },
                "required": ["issue_key"],
            },
        },
        {
            "name": "jira_add_comment",
            "description": "Add a comment to an existing Jira issue.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "The Jira issue key (e.g. 'PROJ-123').",
                    },
                    "comment": {
                        "type": "string",
                        "description": "The comment text to post.",
                    },
                },
                "required": ["issue_key", "comment"],
            },
        },
        {
            "name": "jira_list_projects",
            "description": "List all Jira projects accessible with the current credentials.",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
        },
    ]


def _get_jira_credentials(cfg):
    domain = cfg.get("jira.domain") or cfg.get("jira.base_url") or os.environ.get("JIRA_DOMAIN")
    email = cfg.get("jira.email") or cfg.get("jira.user") or os.environ.get("JIRA_EMAIL")
    token = cfg.get("jira.api_token") or cfg.get("jira.token") or os.environ.get("JIRA_API_TOKEN")

    if not domain or not email or not token:
        return None, None, None

    domain = domain.strip().rstrip("/")
    if not domain.startswith(("http://", "https://")):
        domain = "https://" + domain

    return domain, email.strip(), token.strip()


def _make_jira_request(domain, email, token, endpoint, params=None, method="GET", body_data=None):
    url = f"{domain}/rest/api/3/{endpoint.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    auth_str = f"{email}:{token}"
    auth_b64 = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")

    headers = {
        "Authorization": f"Basic {auth_b64}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "JarvisJiraConnector/1.0",
    }

    data = json.dumps(body_data).encode("utf-8") if body_data is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {"status": "ok"}
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8")[:400]
            err_json = json.loads(detail)
            err_messages = err_json.get("errorMessages") or []
            errors = err_json.get("errors") or {}
            if err_messages:
                detail = "; ".join(err_messages)
            elif errors:
                detail = "; ".join(f"{k}: {v}" for k, v in errors.items())
        except Exception:
            detail = e.reason
        raise JiraAPIError(e.code, detail)


def execute_jira_tool(cfg, tool_name, tool_input):
    """Executes a Jira Cloud tool call using configured domain, email, and API token."""
    domain, email, token = _get_jira_credentials(cfg)
    if not domain or not email or not token:
        return {
            "error": (
                "Jira credentials not configured. Please set 'jira.domain' (e.g. 'https://your-domain.atlassian.net'), "
                "'jira.email', and 'jira.api_token' in config.json."
            )
        }

    try:
        if tool_name == "jira_search_issues":
            jql = tool_input.get("jql")
            query = tool_input.get("query")
            if not jql:
                if query:
                    jql = f'text ~ "{query}" ORDER BY created DESC'
                else:
                    jql = "order by created DESC"

            limit = min(max(1, int(tool_input.get("max_results", 10))), 50)
            params = {
                "jql": jql,
                "maxResults": limit,
                "fields": "summary,status,priority,assignee,reporter,issuetype,created,updated",
            }
            try:
                res = _make_jira_request(domain, email, token, "search/jql", params=params)
            except JiraAPIError:
                res = _make_jira_request(domain, email, token, "search", params=params)

            issues = []
            for item in res.get("issues", []):
                fields = item.get("fields", {})
                issues.append({
                    "key": item.get("key"),
                    "summary": fields.get("summary", ""),
                    "status": (fields.get("status") or {}).get("name", "Unknown"),
                    "type": (fields.get("issuetype") or {}).get("name", "Task"),
                    "priority": (fields.get("priority") or {}).get("name", "Normal"),
                    "assignee": (fields.get("assignee") or {}).get("displayName", "Unassigned"),
                    "reporter": (fields.get("reporter") or {}).get("displayName", "Unknown"),
                    "created": fields.get("created", ""),
                    "updated": fields.get("updated", ""),
                    "url": f"{domain}/browse/{item.get('key')}",
                })
            return {"total": res.get("total", len(issues)), "issues": issues}

        elif tool_name == "jira_get_issue":
            issue_key = (tool_input.get("issue_key") or "").strip().upper()
            if not issue_key:
                return {"error": "issue_key is required"}

            res = _make_jira_request(domain, email, token, f"issue/{issue_key}")
            fields = res.get("fields", {})
            description_text = _from_adf(fields.get("description")).strip()

            comments = []
            for c in (fields.get("comment", {}).get("comments") or []):
                comments.append({
                    "author": (c.get("author") or {}).get("displayName", "Unknown"),
                    "created": c.get("created", ""),
                    "body": _from_adf(c.get("body")).strip(),
                })

            return {
                "key": res.get("key"),
                "url": f"{domain}/browse/{res.get('key')}",
                "summary": fields.get("summary", ""),
                "status": (fields.get("status") or {}).get("name", "Unknown"),
                "type": (fields.get("issuetype") or {}).get("name", "Task"),
                "priority": (fields.get("priority") or {}).get("name", "Normal"),
                "assignee": (fields.get("assignee") or {}).get("displayName", "Unassigned"),
                "reporter": (fields.get("reporter") or {}).get("displayName", "Unknown"),
                "created": fields.get("created", ""),
                "updated": fields.get("updated", ""),
                "description": description_text,
                "comments": comments[-5:],
            }

        elif tool_name == "jira_create_issue":
            project_key = (tool_input.get("project_key") or "").strip().upper()
            summary = (tool_input.get("summary") or "").strip()
            description = tool_input.get("description")
            issue_type = tool_input.get("issue_type") or "Task"
            priority = tool_input.get("priority")

            if not project_key or not summary:
                return {"error": "project_key and summary are required"}

            payload_fields = {
                "project": {"key": project_key},
                "summary": summary,
                "issuetype": {"name": issue_type},
            }
            if description:
                payload_fields["description"] = _to_adf(description)
            if priority:
                payload_fields["priority"] = {"name": priority}

            res = _make_jira_request(domain, email, token, "issue", method="POST", body_data={"fields": payload_fields})
            created_key = res.get("key")
            return {
                "status": "created",
                "key": created_key,
                "id": res.get("id"),
                "url": f"{domain}/browse/{created_key}",
                "summary": summary,
            }

        elif tool_name == "jira_update_issue":
            issue_key = (tool_input.get("issue_key") or "").strip().upper()
            if not issue_key:
                return {"error": "issue_key is required"}

            update_fields = {}
            if "summary" in tool_input:
                update_fields["summary"] = tool_input["summary"]
            if "description" in tool_input:
                update_fields["description"] = _to_adf(tool_input["description"])
            if "priority" in tool_input:
                update_fields["priority"] = {"name": tool_input["priority"]}

            if not update_fields:
                return {"error": "No fields provided to update"}

            _make_jira_request(domain, email, token, f"issue/{issue_key}", method="PUT", body_data={"fields": update_fields})
            return {"status": "updated", "key": issue_key, "url": f"{domain}/browse/{issue_key}"}

        elif tool_name == "jira_transition_issue":
            issue_key = (tool_input.get("issue_key") or "").strip().upper()
            transition_name = (tool_input.get("transition_name") or "").strip().lower()
            transition_id = tool_input.get("transition_id")

            if not issue_key:
                return {"error": "issue_key is required"}

            # Fetch available transitions
            trans_res = _make_jira_request(domain, email, token, f"issue/{issue_key}/transitions")
            available = trans_res.get("transitions", [])

            if not transition_name and not transition_id:
                return {
                    "issue_key": issue_key,
                    "available_transitions": [
                        {"id": t.get("id"), "name": t.get("name"), "to_status": (t.get("to") or {}).get("name")}
                        for t in available
                    ]
                }

            # Match transition by name or ID
            target_id = None
            target_name = None
            for t in available:
                if transition_id and str(t.get("id")) == str(transition_id):
                    target_id = t.get("id")
                    target_name = t.get("name")
                    break
                if transition_name and (t.get("name", "").lower() == transition_name or (t.get("to") or {}).get("name", "").lower() == transition_name):
                    target_id = t.get("id")
                    target_name = t.get("name")
                    break

            if not target_id:
                names = [t.get("name") for t in available]
                return {
                    "error": f"Transition '{transition_name or transition_id}' not found for issue {issue_key}.",
                    "available_transitions": names
                }

            _make_jira_request(domain, email, token, f"issue/{issue_key}/transitions", method="POST", body_data={"transition": {"id": target_id}})
            return {
                "status": "transitioned",
                "issue_key": issue_key,
                "transition": target_name,
                "url": f"{domain}/browse/{issue_key}"
            }

        elif tool_name == "jira_add_comment":
            issue_key = (tool_input.get("issue_key") or "").strip().upper()
            comment = (tool_input.get("comment") or "").strip()

            if not issue_key or not comment:
                return {"error": "issue_key and comment are required"}

            res = _make_jira_request(
                domain, email, token, f"issue/{issue_key}/comment",
                method="POST", body_data={"body": _to_adf(comment)}
            )
            return {
                "status": "comment_added",
                "issue_key": issue_key,
                "comment_id": res.get("id"),
                "url": f"{domain}/browse/{issue_key}"
            }

        elif tool_name == "jira_list_projects":
            res = _make_jira_request(domain, email, token, "project")
            projects = []
            for p in res:
                projects.append({
                    "key": p.get("key"),
                    "name": p.get("name"),
                    "id": p.get("id"),
                    "type": p.get("projectTypeKey"),
                })
            return {"projects": projects}

        else:
            return {"error": f"Unknown Jira tool: {tool_name}"}

    except JiraAPIError as e:
        print(f"[jarvis] Jira API error on {tool_name}: {e}", file=sys.stderr)
        friendly = {
            401: "Jira rejected credentials (401 Unauthorized) — please check jira.email and jira.api_token in config.json.",
            403: "Access forbidden (403) — your Jira account lacks permission for this action/project.",
            404: f"Not found (404) — the requested Jira project or issue was not found.",
        }.get(e.code, f"Jira API error: {e.detail}")
        return {"error": friendly}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"[jarvis] Jira connection error on {tool_name}: {e}", file=sys.stderr)
        return {"error": f"Jira connection failed: {e}"}
