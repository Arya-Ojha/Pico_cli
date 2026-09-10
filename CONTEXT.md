# Context

The domain vocabulary for **pico**, a Python CLI coding agent inspired by Pi's modular, plugin-driven architecture.

## Glossary

- **Agent** — the autonomous coding agent itself. It acts on its own, without asking for approval at each step (informally, *yolo mode*).
- **Session** — one persisted coding session, represented as a tree of nodes.
- **Node** — an immutable, append-only unit of event data in a session. Each node carries an id, a pointer to its parent, a timestamp, and a payload. Once written, a node is never edited or deleted — only built upon.
- **Payload** — the content of a node: a **user** message, an **assistant** message, a **tool request**, a **tool result**, or a **compaction summary**.
- **Branch** — a timeline: the sequence of nodes from the root to a leaf. A session can hold many parallel branches.
- **Fork** — rewinding to an earlier node and starting a new branch from it (for example, after a change that broke the codebase).
- **Turn** — one user message plus the agent's full response to it, including any tool requests it makes.
- **Tool** — a capability the agent can invoke. The core tools are **read**, **write**, **edit**, **grep**, and **bash**.
- **Todo tool** — the agent-facing `todo` tool (actions `add` / `update` / `list` / `clear`) backed by a shared in-memory **todo list**; statuses are `pending`, `in_progress`, `completed`.
- **Tool request** — the agent asking to run a tool.
- **Tool result** — the output returned by running a tool.
- **Compaction** — summarising older context so the session fits within the model's context window.
- **Context window** — the token budget of the model in use.
- **Reserve tokens** — the portion of the context window held back for the model's own response.
- **Provider** — an LLM backend. Every provider is reached through a single gateway and exposed as one unified **AI call**.
- **AI call** — the unified request/response shape used to talk to any provider.
- **Headless** — running the agent programmatically (as a library) with no terminal UI.
- **Extension** (also **plugin**) — a modular capability registered into the agent: a tool, a provider, or a UI widget. Extensions are loaded from a plugins directory or registered explicitly.
