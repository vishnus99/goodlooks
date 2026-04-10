# GoodLooks — V3 plan

Builds on `V2_PLAN.md`: keep the static wallpaper snapshot, and add an **interactive companion surface** where users can click a task and see **agent-recommended completion steps**.

## Goals (V3)

- **Interactive tasks:** Users can click a task in a UI surface (not in wallpaper image) to open details.
- **Agent recommendations:** For a selected task, generate concise, actionable steps to complete it.
- **Low-friction UX:** Keep current CLI flow; add interactive features as an optional layer.
- **Local-first:** Continue using local task storage and local rendering where possible.
- **Safe fallback:** If agent recommendation fails, task operations still work normally.

## Constraint recap

- macOS wallpaper is a static image and cannot support per-item click targets.
- Interactive behavior must come from a separate UI surface.

## Proposed UX model

Use a **dual-surface design**:

1. **Wallpaper (existing):** read-only visual overview.
2. **Interactive board (new):** clickable task list with recommendation panel.

Primary entry points:

- `goodlooks board` to open the interactive board.
- Optional future: menu bar shortcut to launch/focus board.

## Recommended implementation approach

### Surface

- Start with a **local web board** served by a tiny local process (or static file + local API).
- Open in default browser automatically from CLI command.
- Keep UI minimal: left pane task list, right pane recommendation details.

Why this first:

- Fastest path to interactive clicks.
- Easy iteration and styling.
- Portable and testable compared to native app first.

### Data flow

1. Load tasks from existing `tasks.json`.
2. Render list (same sorting/filtering semantics as CLI).
3. On task click, call a recommendation endpoint/function with:
   - task title
   - urgency
   - done status
   - optional user context (time available, energy, deadline)
4. Return structured steps and display in panel.
5. Optionally persist cached recommendation by task id + hash(title, urgency).

### Recommendation schema

Return a stable JSON shape:

- `summary`: one-line intent.
- `steps`: ordered list of concrete actions.
- `estimated_time_minutes`: coarse estimate.
- `first_action`: next immediate step (one sentence).
- `risks_or_blockers`: optional list.

### CLI/API contract (proposed)

- `goodlooks board` → starts board session.
- `goodlooks recommend --id <task_id>` → prints recommendation in terminal.
- Internal function: `recommend_task(task: dict) -> dict`.

This allows:

- UI reuse of recommendation logic.
- CLI-only fallback for headless usage.

## Architecture slices

1. **Core recommendation layer**
   - Add recommendation module and schema validation.
   - Add mock provider first; wire real provider behind interface.

2. **CLI command surface**
   - Add `recommend` command for one task.
   - Add optional cache file in app config directory.

3. **Interactive board**
   - Add `board` command.
   - Render task list and recommendation panel.
   - Add filters (`pending`, `all`, `done`).

4. **Quality + resilience**
   - Timeouts and retry policy for recommendation calls.
   - Graceful UI error states.
   - Basic analytics/logging (local only) for failures.

## Agent integration strategy

Provider abstraction:

- `RecommendationProvider` interface with `get_steps(task, context) -> Recommendation`.

Initial providers:

- `StubProvider` for development/testing.
- `ExternalAgentProvider` for real agent-backed suggestions.

Guardrails:

- Enforce max tokens/length in prompt-response path.
- Strip unsafe shell instructions from suggestions unless explicitly allowed.
- Keep recommendations advisory (no auto-execution).

## Caching and performance

- Cache by task fingerprint: `(title, urgency, done)` hash.
- TTL-based cache invalidation (for example, 24h).
- “Regenerate” action in UI to bypass cache.

## Security and privacy

- Default local-only mode where possible.
- If remote agent is used:
  - Document what task content is sent.
  - Add explicit opt-in env/config toggle.
  - Redact obvious secrets from task text before sending.

## Testing plan

- Unit tests:
  - Recommendation schema parsing/validation.
  - Cache hit/miss behavior.
  - CLI `recommend --id` happy/failure paths.
- Integration tests:
  - Board loads tasks and handles click-to-recommend flow.
  - Provider timeout and fallback messaging.
- Manual tests:
  - Large task lists.
  - Long task titles.
  - Offline/no-provider mode.

## Out of scope for V3

- Clickable elements directly inside wallpaper image.
- Full native macOS app rewrite.
- Multi-user sync or cloud task sharing.

## Definition of done (V3)

- `goodlooks board` opens interactive task board.
- Clicking a task shows agent-recommended steps within 2-5 seconds (with timeout handling).
- `goodlooks recommend --id` works in terminal.
- Wallpaper remains functional and unchanged as read-only snapshot.
- Docs updated with setup, provider config, and troubleshooting.
