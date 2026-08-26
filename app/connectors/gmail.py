"""app/connectors/gmail.py — Gmail integration & tool definitions (Connector layer).

Provides standard Gmail tools (get_latest_emails, search_emails, send_email)
that interface directly with the Google Gmail REST API using OAuth2 access tokens.
Standard library only — zero pip dependencies.
"""
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def get_gmail_tools():
    """Returns Anthropic API tool definitions for Gmail operations."""
    return [
        {
            "name": "gmail_get_latest_emails",
            "description": "Fetch recent emails from the user's Gmail inbox.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of emails to retrieve (1-10, default 5).",
                        "default": 5
                    }
                }
            }
        },
        {
            "name": "gmail_search_emails",
            "description": "Search Gmail inbox using standard search query syntax (e.g., 'from:boss', 'is:unread', 'subject:report').",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query string."
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of emails to retrieve (default 5).",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "gmail_send_email",
            "description": "Send an email message to a specified recipient.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address."},
                    "subject": {"type": "string", "description": "Email subject line."},
                    "body": {"type": "string", "description": "Email message body."}
                },
                "required": ["to", "subject", "body"]
            }
        }
    ]


def _make_gmail_request(access_token, endpoint, params=None, method="GET", body_data=None):
    url = f"{GMAIL_API_BASE}/{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    data = json.dumps(body_data).encode("utf-8") if body_data else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def execute_gmail_tool(cfg, tool_name, tool_input):
    """Executes a Gmail tool call using configured OAuth token."""
    access_token = cfg.get("gmail.access_token") or os.environ.get("GMAIL_ACCESS_TOKEN")
    if not access_token:
        return {
            "error": "Gmail access token not configured. Please authorize Google account in app config."
        }

    try:
        if tool_name == "gmail_get_latest_emails":
            max_res = min(tool_input.get("max_results", 5), 10)
            res = _make_gmail_request(access_token, "messages", params={"maxResults": max_res, "q": "label:INBOX"})
            messages = res.get("messages", [])
            results = []
            for msg in messages:
                detail = _make_gmail_request(access_token, f"messages/{msg['id']}", params={"format": "full"})
                headers = {h["name"].lower(): h["value"] for h in detail.get("payload", {}).get("headers", [])}
                results.append({
                    "id": msg["id"],
                    "from": headers.get("from", "Unknown"),
                    "subject": headers.get("subject", "(No Subject)"),
                    "date": headers.get("date", ""),
                    "snippet": detail.get("snippet", "")
                })
            return {"emails": results}

        elif tool_name == "gmail_search_emails":
            query = tool_input.get("query", "")
            max_res = min(tool_input.get("max_results", 5), 10)
            res = _make_gmail_request(access_token, "messages", params={"maxResults": max_res, "q": query})
            messages = res.get("messages", [])
            results = []
            for msg in messages:
                detail = _make_gmail_request(access_token, f"messages/{msg['id']}", params={"format": "full"})
                headers = {h["name"].lower(): h["value"] for h in detail.get("payload", {}).get("headers", [])}
                results.append({
                    "id": msg["id"],
                    "from": headers.get("from", "Unknown"),
                    "subject": headers.get("subject", "(No Subject)"),
                    "date": headers.get("date", ""),
                    "snippet": detail.get("snippet", "")
                })
            return {"query": query, "results": results}

        elif tool_name == "gmail_send_email":
            to = tool_input.get("to")
            subject = tool_input.get("subject")
            body = tool_input.get("body")
            raw_email = f"To: {to}\r\nSubject: {subject}\r\n\r\n{body}"
            encoded_message = base64.urlsafe_b64encode(raw_email.encode("utf-8")).decode("utf-8")
            res = _make_gmail_request(access_token, "messages/send", method="POST", body_data={"raw": encoded_message})
            return {"status": "sent", "message_id": res.get("id")}

        else:
            return {"error": f"Unknown Gmail tool: {tool_name}"}

    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        return {"error": f"Gmail API request failed: {e}"}
