# 0004 — Native provider adapters (one file per backend)

- Status: Accepted
- Date: 2026-09-30

## Context

ADR-0003 froze a hardcoded core with a single OpenRouter gateway and removed
the generic provider registry. Real usage then asked for the obvious next
step: choosing between backends (OpenAI, Anthropic, Gemini, local Ollama,
DeepSeek) from inside the app, with per-backend setup (API key, base URL,
model) — without reviving "everything is a plugin".

## Decision

- Each backend gets **one adapter module** under `pico_ai/providers/`
  (`openai.py`, `anthropic.py`, `gemini.py`, `deepseek.py`, `ollama.py`;
  the existing `pico_ai/openrouter.py` stays where it is). Each module only
  converts between that provider's wire format and the app's default
  `StreamEvent` shape (`text` / `thinking` / `tool_call` / `usage`), and
  exposes a `ProviderSpec`: id, display name, setup `FieldSpec`s (key,
  label, default, secret, required, env-var fallback), default model, and a
  `create(config)` factory.
- The registry (`pico_ai/providers/__init__.py`) is a **hardcoded,
  host-owned list** — adding a backend is a new file plus one registry line.
  It is not a plugin API: no dynamic loading, no third-party registration.
- Shared wire logic lives in `providers/_compat.py` (OpenAI
  chat-completions SSE); Anthropic, Gemini, and Ollama each own their
  distinct formats.
- Config: `Settings.provider` (active id) + `Settings.providers` (stored
  per-backend values). Effective value precedence: field default <
  environment variable < stored value. Secrets are stored in plaintext
  `settings.json` — env vars are preferred on shared machines.
- `AgentSession.set_provider(id, values)` swaps the loop's adapter, stores
  values, and resets the model to that backend's stored/default model
  (model ids are backend-specific). The TUI `/provider` flow (picker →
  setup form → switch → persist → status-bar refresh) is the only UI.
- The `openrouter/free` alias resolves only for OpenRouter; other backends
  use their own default model.

## Consequences

- Six backends from one `Provider` protocol; the FSM, tools, session tree,
  and hooks are untouched — adapters slot into the existing seam.
- Anthropic has no list-models endpoint, so its picker list is curated;
  any other id works via `/model <id>`.
- If a seventh backend is needed, copy the smallest adapter file.
