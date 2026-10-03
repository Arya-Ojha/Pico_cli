# 01 — Assistant stream timing (`duration_ms`)

**What to build:** measure provider-stream wall-time in `AgentLoop.stream()`
(`packages/pico_core/src/pico_core/fsm.py`) and persist it as
`duration_ms: float | None = None` on `AssistantPayload`
(`packages/pico_core/src/pico_core/session.py`). `None` means "unknown"
(old sessions, unmeasurable runs) and renders as `—`.

**Blocked by:** nothing.

**Status:** ready-for-agent

- [ ] `AssistantPayload` gains `duration_ms: float | None = None`; old JSONL session files still validate and load.
- [ ] The loop records a monotonic start before `provider.stream(request)` and stamps the appended `AssistantPayload` with elapsed ms (both tool and non-tool turns).
- [ ] Interrupted/error streams still append with a measured (partial) duration or `None` — never crash timing.
- [ ] Fake-provider tests assert a non-negative `duration_ms` on the appended assistant node.
