# Gmail Connector

The **Gmail Connector** integrates JARVIS with Google Workspace and Gmail REST APIs using OAuth2 authentication tokens without external dependencies.

## Capabilities

- **Fetch Latest Emails** (`gmail_get_latest_emails`): Retrieves recent messages from the inbox, extracting the sender, subject line, date, snippet, and message body.
- **Search Inbox** (`gmail_search_emails`): Performs targeted email queries using standard Gmail syntax (e.g. `is:unread`, `from:boss@example.com`, `subject:urgent`, `after:2026/01/01`).
- **Send Emails** (`gmail_send_email`): Composes and transmits emails directly to recipients with customizable subject and body content.

## Configuration

Configured in `config.json`:
```json
{
  "gmail": {
    "access_token": "ya29.a0AdMD6..."
  }
}
```

## Related Systems

- [[Discord Connector]] for sharing email summaries across team channels.
- [[Atlassian Jira Connector]] for converting incoming email issues into Jira tickets.
- [[Safety Protocol & Guardrails]] protecting user data and email communications.
