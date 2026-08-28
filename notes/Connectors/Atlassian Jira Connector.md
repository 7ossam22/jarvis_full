# Atlassian Jira Connector

The **Atlassian Jira Connector** connects JARVIS with Jira Cloud REST API v3 to search, inspect, create, update, and transition project tickets directly from voice or chat commands.

## Capabilities

- **JQL & Text Search** (`jira_search_issues`): Queries Jira using Jira Query Language or text search across active sprints, projects, assignees, and priorities.
- **Issue Inspection** (`jira_get_issue`): Retrieves complete issue details including summary, status, priority, description, assignees, reporters, and comments timeline.
- **Ticket Creation** (`jira_create_issue`): Creates new Tasks, Bugs, Stories, or Epics in specified projects with rich Atlassian Document Format (ADF) descriptions.
- **Workflow Status Transitions** (`jira_transition_issue`): Moves issues through workflow stages (e.g. *To Do* ➔ *In Progress* ➔ *Under Review* ➔ *Done*).
- **Comments Thread** (`jira_add_comment`): Adds timestamped discussion comments to any issue.
- **Project Discovery** (`jira_list_projects`): Lists accessible Jira projects and keys.

## Interactive Jira UI Deck

When Jira tools are invoked, JARVIS automatically launches the [[Zen White Glassmorphic UI]] **Interactive Jira Workspace**:
- 4-column Kanban board (**TO DO**, **IN PROGRESS**, **UNDER REVIEW**, **DONE**).
- Real-time issue detail drawer with live status transition buttons and live comment composer.

## Configuration

Configured in `config.json`:
```json
{
  "jira": {
    "domain": "https://omnisync-eg.atlassian.net",
    "email": "hmousa@omnisyncsystems.com",
    "api_token": "ATATT3xFfGF..."
  }
}
```

## Related Systems

- [[Zen White Glassmorphic UI]] for the interactive Kanban board and issue inspector.
- [[Discord Connector]] for sharing Jira tickets in team channels.
- [[Safety Protocol & Guardrails]] for maintaining project integrity.
