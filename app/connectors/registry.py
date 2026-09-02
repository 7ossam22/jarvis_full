"""app/connectors/registry.py — the single source of truth for tool
registration, provider access control, schema formatting, and dispatch
(Connector layer).

Before this module, four separate places each rebuilt the tool list and each
repeated the same fragile `if name.startswith("gmail_") … elif …` routing
chain: app/providers/anthropic_provider.py had it twice (API loop and CLI
loop), gemini_provider.py once, lmstudio_provider.py once. Three consequences
this module removes:

- `flutter_run_test` was advertised to every backend by get_browser_tools()
  but only the CLI chain matched a `flutter_` prefix, so on the Anthropic API,
  Gemini and LM Studio it always came back "Tool flutter_run_test not found".
  Routing is now by exact registered name, so a tool that exists is reachable.
- Each provider hand-converted the canonical schema into its own wire format
  (_to_gemini_schema, _to_openai_tools). That conversion now lives here, once.
- There was no way to say "this tool is too dangerous for a hosted model".
  Tool.only_llm is that gate, enforced both when the tool list is built and
  again at dispatch, so a model cannot invoke what it was never offered.

Standard library only — no pydantic, no external schema validation.

Layout:
    Tool                    one registered tool + its access policy
    ToolRegistry            registration, filtering, formatting, dispatch
    registry                the process-wide singleton, pre-loaded with every
                            connector bundle (see _register_builtin_connectors)

Typical provider use:

    from ..connectors.registry import registry

    tools = registry.get_tools_for_provider("anthropic")   # wire-ready specs
    result = registry.dispatch(name, args, "anthropic", cfg)
"""
from __future__ import annotations

import inspect
import sys
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from .web_fetch import WEB_FETCH_SCHEMA, execute_web_fetch
from .web_search import WEB_SEARCH_SCHEMA, execute_web_search

# ---------------------------------------------------------------------------
# Types & provider identifiers
# ---------------------------------------------------------------------------

JSONDict = dict[str, Any]

#: A tool handler is called either as ``handler(arguments)`` or, when it needs
#: the per-request Config, as ``handler(arguments, cfg)``. Which form applies is
#: detected once at registration time (see ToolRegistry._wants_cfg) — callers
#: never have to care.
ToolHandler = Callable[..., JSONDict]

ANTHROPIC = "anthropic"
GEMINI = "gemini"
LMSTUDIO = "lmstudio"
OPENAI = "openai"

#: Providers that run the model on hardware the user controls. A tool marked
#: ``only_llm=True`` is unconditionally available to these and to nobody else
#: unless it names them in ``allowed_providers``.
LOCAL_PROVIDERS: frozenset[str] = frozenset({LMSTUDIO})

#: Every provider name this registry knows how to format schemas for. Anything
#: else raises from get_tools_for_provider rather than silently emitting specs
#: in the wrong shape.
KNOWN_PROVIDERS: frozenset[str] = frozenset({ANTHROPIC, GEMINI, LMSTUDIO, OPENAI})


class ToolRegistryError(Exception):
    """Raised for registration-time programming errors (duplicate name, bad
    bundle, unknown provider). Never raised by dispatch — see dispatch()."""


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    """One registered tool: its canonical schema, its handler, and the policy
    controlling which model providers may see and invoke it.

    Attributes:
        name: Unique identifier the model calls, e.g. ``"browser_open_url"``.
        description: Natural-language description handed to the model.
        input_schema: Canonical Anthropic-style JSON Schema for the arguments
            (an OpenAPI-subset object schema). Gemini and OpenAI-compatible
            backends use the identical object under a different key, so
            conversion is a rename — see ToolRegistry._format_for_provider.
        handler: Callable invoked by dispatch. See ToolHandler.
        only_llm: When True (the default), the tool is restricted to locally
            hosted models — the providers in LOCAL_PROVIDERS — plus any
            provider explicitly named in allowed_providers. Set False for a
            tool that is safe to expose to a hosted API.
        allowed_providers: Extra providers permitted despite only_llm=True.
            Ignored entirely when only_llm is False (everything is allowed).
        wants_cfg: Whether handler takes ``(arguments, cfg)`` rather than just
            ``(arguments)``. Detected automatically at registration; not
            something callers set by hand.
    """

    name: str
    description: str
    input_schema: JSONDict
    handler: ToolHandler
    only_llm: bool = True
    allowed_providers: list[str] | None = None
    wants_cfg: bool = field(default=False, compare=False)

    def is_allowed_for(self, provider_name: str) -> bool:
        """Whether `provider_name` may both see and invoke this tool.

        The rule, exactly:
          - ``only_llm=False``  -> every provider is allowed.
          - ``only_llm=True``   -> allowed only for a provider in
            LOCAL_PROVIDERS (i.e. ``"lmstudio"``), or one named in
            ``allowed_providers``.
        """
        if not self.only_llm:
            return True
        if provider_name in LOCAL_PROVIDERS:
            return True
        return provider_name in (self.allowed_providers or ())

    def denial_reason(self, provider_name: str) -> str:
        """Human-readable explanation for a refused call. Surfaced to the model
        as the tool result, so it can tell the user why rather than retrying."""
        allowed = sorted(set(LOCAL_PROVIDERS) | set(self.allowed_providers or ()))
        return (
            f"Tool '{self.name}' is restricted to locally hosted models and is not "
            f"available to provider '{provider_name}'. Permitted providers: "
            f"{', '.join(allowed)}."
        )


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Holds every Tool and answers the three questions a provider loop asks:
    which tools may I offer, in what wire shape, and how do I run one.

    Registration happens once at import time (module-level ``registry`` below),
    so the mapping is read-only in practice and needs no locking under
    ThreadingHTTPServer.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # -- introspection ----------------------------------------------------

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def get(self, name: str) -> Tool | None:
        """The registered Tool, or None if `name` is unknown."""
        return self._tools.get(name)

    def names(self) -> list[str]:
        """Every registered tool name, in registration order."""
        return list(self._tools)

    # -- registration -----------------------------------------------------

    @staticmethod
    def _wants_cfg(handler: ToolHandler) -> bool:
        """True when `handler` accepts a second positional parameter (the
        per-request Config). Falls back to False for builtins and C callables
        whose signature cannot be inspected."""
        try:
            params = [
                p for p in inspect.signature(handler).parameters.values()
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ]
        except (TypeError, ValueError):
            return False
        return len(params) >= 2

    def _add(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ToolRegistryError(
                f"Tool '{tool.name}' is already registered. Tool names are the "
                "routing key, so they must be unique across all connectors."
            )
        self._tools[tool.name] = tool
        return tool

    def register(
        self,
        name: str,
        description: str,
        input_schema: JSONDict,
        handler: ToolHandler | None = None,
        only_llm: bool = True,
        allowed_providers: list[str] | None = None,
    ) -> Any:
        """Register one tool.

        Called with a handler, registers immediately and returns the handler.
        Called without one, returns a decorator so a function can register
        itself:

            @registry.register("my_tool", "Does a thing", {...}, only_llm=False)
            def my_tool(arguments: dict) -> dict:
                ...

        Raises:
            ToolRegistryError: if `name` is already registered.
        """

        def _do(fn: ToolHandler) -> ToolHandler:
            self._add(Tool(
                name=name,
                description=description,
                input_schema=input_schema,
                handler=fn,
                only_llm=only_llm,
                allowed_providers=allowed_providers,
                wants_cfg=self._wants_cfg(fn),
            ))
            return fn

        if handler is None:
            return _do
        return _do(handler)

    def register_connector_bundle(
        self,
        tools_getter: Callable[[], list[JSONDict]],
        executor: Callable[..., JSONDict],
        prefix: str | None = None,
        only_llm: bool = True,
        allowed_providers: list[str] | None = None,
    ) -> list[Tool]:
        """Ingest an existing connector module in one call.

        The connectors predate this registry and expose a pair of functions —
        ``get_<name>_tools()`` returning Anthropic-style specs, and
        ``execute_<name>_tool(cfg, tool_name, tool_input)`` dispatching them.
        This adapts that pair without touching the connector: every spec the
        getter returns becomes a Tool whose handler calls the executor with the
        tool's own name bound.

        Args:
            tools_getter: Zero-argument callable returning a list of
                ``{"name", "description", "input_schema"}`` dicts.
            executor: The bundle's ``execute_*_tool(cfg, name, arguments)``.
            prefix: If given, only specs whose name starts with it are ingested.
                Leave None to take everything the getter returns — which is what
                the browser bundle needs, since it also owns ``system_open`` and
                ``flutter_run_test`` alongside its ``browser_*`` tools.
            only_llm: Applied to every tool in the bundle. See Tool.only_llm.
            allowed_providers: Applied to every tool in the bundle.

        Returns:
            The Tool objects registered, in the getter's order.

        Raises:
            ToolRegistryError: on a malformed spec or a duplicate name.
        """
        registered: list[Tool] = []
        for spec in tools_getter():
            try:
                name = spec["name"]
                description = spec["description"]
                input_schema = spec["input_schema"]
            except (KeyError, TypeError) as exc:
                raise ToolRegistryError(
                    f"Malformed tool spec from {getattr(tools_getter, '__name__', tools_getter)!r}: "
                    f"{spec!r} ({exc})"
                ) from exc

            if prefix is not None and not name.startswith(prefix):
                continue

            # `name` and `executor` are bound as defaults so each closure keeps
            # its own tool name rather than the loop's last value.
            def _handler(
                arguments: JSONDict,
                cfg: Any = None,
                _name: str = name,
                _executor: Callable[..., JSONDict] = executor,
            ) -> JSONDict:
                return _executor(cfg, _name, arguments)

            registered.append(self._add(Tool(
                name=name,
                description=description,
                input_schema=input_schema,
                handler=_handler,
                only_llm=only_llm,
                allowed_providers=allowed_providers,
                wants_cfg=True,
            )))
        return registered

    # -- provider-facing schema ------------------------------------------

    @staticmethod
    def _format_for_provider(tool: Tool, provider_name: str) -> JSONDict:
        """One Tool in `provider_name`'s wire format.

        All three shapes carry the same OpenAPI-subset object schema, so this
        is a rename rather than a translation:

            anthropic          {"name", "description", "input_schema"}
            gemini             {"name", "description", "parameters"}
            lmstudio / openai  {"type": "function",
                                "function": {"name", "description", "parameters"}}
        """
        if provider_name == ANTHROPIC:
            return {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
        if provider_name == GEMINI:
            return {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            }
        if provider_name in (LMSTUDIO, OPENAI):
            return {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
        raise ToolRegistryError(f"No schema format known for provider '{provider_name}'.")

    def get_tools_for_provider(self, provider_name: str) -> list[JSONDict]:
        """Every tool `provider_name` is permitted to see, in its wire format.

        Filtering and formatting in one pass, so a provider loop never builds a
        tool list by hand. Gemini callers get the bare function declarations —
        wrap them yourself as ``{"function_declarations": [...]}``, since that
        list sits alongside built-in tools like ``google_search`` in the same
        request.

        Raises:
            ToolRegistryError: if `provider_name` is not in KNOWN_PROVIDERS.
        """
        if provider_name not in KNOWN_PROVIDERS:
            raise ToolRegistryError(
                f"Unknown provider '{provider_name}'. Known: {', '.join(sorted(KNOWN_PROVIDERS))}."
            )
        return [
            self._format_for_provider(tool, provider_name)
            for tool in self._tools.values()
            if tool.is_allowed_for(provider_name)
        ]

    # -- dispatch ---------------------------------------------------------

    def dispatch(
        self,
        tool_name: str,
        arguments: JSONDict,
        provider_name: str,
        cfg: Any = None,
    ) -> JSONDict:
        """Run one tool call and return a JSON-serializable result.

        This never raises. A provider's tool loop runs inside a worker thread
        serving one HTTP request (app/http_server.py:47), and an escaping
        exception there kills the turn with no answer — so every failure comes
        back as ``{"status": "error", "error": ...}`` for the model to read and
        relay, with the traceback logged to stderr for the developer.

        Three ways a call fails before the handler runs:
          - unknown tool name,
          - the provider is not permitted this tool (Tool.only_llm), enforced
            here as well as in get_tools_for_provider so a model that invents
            or replays a name it was never offered still cannot reach it,
          - `arguments` is not a dict.

        Args:
            tool_name: The registered name the model asked for.
            arguments: The model's arguments for it.
            provider_name: The calling provider, e.g. ``"gemini"``.
            cfg: The per-request Config, forwarded to handlers that want it.
                Required by every connector-bundle tool, which reads its
                credentials from it.
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            _telemetry().record("error", f"model asked for unknown tool {tool_name!r}")
            return {
                "status": "error",
                "error": f"Tool '{tool_name}' not found. Available: {', '.join(self.names())}",
            }

        if not tool.is_allowed_for(provider_name):
            reason = tool.denial_reason(provider_name)
            sys.stderr.write(f"[jarvis] blocked {tool_name} for provider {provider_name}\n")
            return {"status": "error", "error": reason}

        if not isinstance(arguments, dict):
            return {
                "status": "error",
                "error": f"Tool '{tool_name}' expects an object of arguments, got {type(arguments).__name__}.",
            }

        # Every provider's tool loop funnels through here, so this one line
        # gives all of them live progress: the turn can now say "reading the
        # form" instead of going silent for two minutes.
        _telemetry().activity(f"{_describe(tool_name, arguments)}…")

        try:
            if tool.wants_cfg:
                return tool.handler(arguments, cfg)
            return tool.handler(arguments)
        except Exception as exc:  # noqa: BLE001 — deliberate: see docstring
            sys.stderr.write(f"[jarvis] tool {tool_name} raised: {exc}\n")
            traceback.print_exc(file=sys.stderr)
            _telemetry().record("error", f"tool {tool_name} raised", exc)
            # Worth SAYING: a tool blowing up mid-turn is the moment the user
            # most needs telling, and it is the moment they are least likely to
            # be watching a panel.
            _telemetry().activity(f"{tool_name} failed: {exc}", notable=True)
            return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# The process-wide registry, pre-loaded with every connector bundle
# ---------------------------------------------------------------------------

#: Phrasing for the live progress line. A raw tool name and a JSON blob is a
#: developer's view; this is the one the user reads while they wait.
_ACTIVITY_VERBS = {
    "web_search": "Searching the web",
    "web_fetch": "Reading",
    "browser_autofill_visit": "Filling the visit forms",
    "browser_fill_form": "Filling the form",
    "browser_screenshot": "Looking at the screen",
    "browser_navigate": "Opening",
    "gmail_list": "Checking your mail",
    "gmail_send": "Sending mail",
    "jira_search": "Searching Jira",
    "discord_send": "Sending on Discord",
}

#: The argument worth showing alongside the verb, per tool.
_ACTIVITY_SUBJECT = ("query", "url", "to", "subject", "label", "text")


def _describe(tool_name, arguments):
    verb = _ACTIVITY_VERBS.get(tool_name) or tool_name.replace("_", " ").capitalize()
    if isinstance(arguments, dict):
        for key in _ACTIVITY_SUBJECT:
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return f"{verb} {value.strip()[:60]}"
    return verb


def _telemetry():
    """Imported lazily: app.telemetry must not become a hard dependency of the
    connector layer, which is also driven from tests and the CLI."""
    from .. import telemetry
    return telemetry


registry = ToolRegistry()


def _register_builtin_connectors(reg: ToolRegistry) -> None:
    """Ingest the five existing connector bundles.

    Access policy, per bundle:

      browser, system   only_llm=True, with Anthropic and Gemini named
                        explicitly in LOCAL_ONLY_ALLOWED_PROVIDERS. These drive
                        the user's real desktop — a visible browser, shell
                        commands, volume, screenshots, app launching — so they
                        stay gated rather than open, but the two cloud backends
                        in use here are trusted with them: the Novatek
                        automation flows in app/persona.py have to run on
                        whichever provider answers, and Gemini's 500-round tool
                        loop exists specifically to drive them.
      gmail, discord,   only_llm=False — these reach an external API the user
      jira              has already granted a scoped token for, and the hosted
                        models are the ones with the reasoning to use them well.

    Keeping only_llm=True with an explicit allowlist, rather than flipping to
    only_llm=False, is deliberate: a provider added later is denied desktop
    control by default and has to be named here on purpose. To lock these tools
    back down to locally hosted models, set LOCAL_ONLY_ALLOWED_PROVIDERS to
    None — nothing else needs to change.
    """
    from .browser import get_browser_tools, execute_browser_tool
    from .discord import get_discord_tools, execute_discord_tool
    from .gmail import get_gmail_tools, execute_gmail_tool
    from .jira import get_jira_tools, execute_jira_tool
    from .system import get_system_tools, execute_system_tool

    # Set the list of allowed providers for the desktop control tools,
    # If LOCAL_ONLY_ALLOWED_PROVIDERS: list[str] | None = None that means no restriction
    LOCAL_ONLY_ALLOWED_PROVIDERS: list[str] | None = [ANTHROPIC, GEMINI]

    # Desktop control — gated, with the trusted cloud providers named above.
    # No prefix filter on the browser bundle: it also owns `system_open` and
    # `flutter_run_test`, both of which were unreachable under the old
    # prefix-matching routing.
    reg.register_connector_bundle(
        get_browser_tools, execute_browser_tool,
        only_llm=True, allowed_providers=LOCAL_ONLY_ALLOWED_PROVIDERS,
    )
    reg.register_connector_bundle(
        get_system_tools, execute_system_tool,
        only_llm=True, allowed_providers=LOCAL_ONLY_ALLOWED_PROVIDERS,
    )

    # Scoped external APIs — available to every backend.
    for getter, executor in (
        (get_gmail_tools, execute_gmail_tool),
        (get_discord_tools, execute_discord_tool),
        (get_jira_tools, execute_jira_tool),
    ):
        reg.register_connector_bundle(getter, executor, only_llm=False)


_register_builtin_connectors(registry)


# ---------------------------------------------------------------------------
# Standalone tools
# ---------------------------------------------------------------------------

# Web search, for the backends that have none of their own — which is the gap
# this tool was written to fill. Anthropic and Gemini each run a real search
# index server-side (`web_search` and `google_search`, declared in their own
# provider modules), and those are markedly better than scraping DuckDuckGo
# Lite: a real index, real ranking, no bot-blocking. A local LM Studio model
# has nothing, so it gets this.
#
# Hence only_llm=True with no extra providers: not because search is dangerous,
# but because on a hosted backend it would be the *worse* of two options. On
# Anthropic it would also collide outright — its built-in tool is named
# `web_search` too, and two tools of one name in a request is an API error.
#
# To hand every provider the same scraped search instead (identical behaviour
# everywhere, at a quality cost), set only_llm=False here and delete the
# native declarations in anthropic_provider.py and gemini_provider.py.
registry.register(
    WEB_SEARCH_SCHEMA["name"],
    WEB_SEARCH_SCHEMA["description"],
    WEB_SEARCH_SCHEMA["input_schema"],
    execute_web_search,
    only_llm=True,
    allowed_providers=None,
)

# web_fetch reads one page the model picked, usually a URL web_search just
# returned — snippets name a source but rarely carry the actual figure asked
# for. Open to every provider on the same reasoning: it is read-only and
# reaches nothing on this machine. That last part is enforced, not assumed —
# app/connectors/web_fetch.py refuses non-http(s) schemes and any address that
# is not globally routable, on the original URL and on every redirect hop, so
# it cannot be turned into a way to read localhost:4701 (the browser daemon),
# the cloud metadata endpoint, or file:///etc/passwd.
registry.register(
    WEB_FETCH_SCHEMA["name"],
    WEB_FETCH_SCHEMA["description"],
    WEB_FETCH_SCHEMA["input_schema"],
    execute_web_fetch,
    only_llm=False,
    allowed_providers=[ANTHROPIC, GEMINI, LMSTUDIO],
)
