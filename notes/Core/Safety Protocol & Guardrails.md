# Safety Protocol & Guardrails

The **Safety Protocol & Guardrails** subsystem enforces strict operational boundaries against destructive or modifying actions on the user's machine and codebase.

## Ground Rule & Security Policy

Destructive actions (such as deleting files, modifying/cutting data, package installation via `apt`/`pip`/`npm`, formatting, or running elevated commands) are **strictly blocked by default**.

## Two-Tier Override Confirmation Workflow

```
[User Request: Destructive Action]
               │
               ▼
       1. Refuse by Default
               │
       (User commands "override")
               │
               ▼
       2. Acknowledge & Prompt for Confirmation
               │
       (User commands "confirm")
               │
               ▼
       3. Execute Action
```

1. **Refuse by Default**:
   - The system refuses any destructive request and informs the user that an explicit override command is required.
2. **Override Request**:
   - When the user issues the command with the `override` keyword, the system acknowledges the override request, pauses execution, and asks for explicit human confirmation.
3. **Explicit Confirmation**:
   - Execution is only unlocked once the user explicitly confirms (e.g. "confirm", "proceed").

## Implementation Layers

- **Code Enforcement Layer** (`app/connectors/system.py`):
  Regex inspection of shell commands (`rm`, `unlink`, `del`, `mv`, `format`, `mkfs`, `dd`, `apt`, `dpkg`, `pip`, `npm`, `sudo`). Hard-blocks execution unless `is_override: true` and `is_confirmed: true`.
- **Persona / Prompt Layer** (`app/persona.py`):
  Directives enforcing refusal, confirmation prompts, and safe tool parameter generation.

## Related Systems

- [[Linux System Controller]] guarded by safe command inspection.
- [[Neural Cortex 3D Graph]] protected against accidental memory wipe.
- [[Atlassian Jira Connector]] and [[Gmail Connector]] ensuring safe data transmission.
