# GoodLooks LangChain Recommendation Agent Plan

This document describes how to replace the rule-based recommendation logic in `goodlooks recommend --id <task_id>` with a LangChain-powered agent while keeping the current CLI UX stable.

## Current State (Baseline)

- `recommend` command loads the task by ID and prints a panel.
- Recommendation logic is deterministic and keyword-based in `recommendation_for_task()`.
- Output rendering is handled by `recommendation_to_text()`.

## Target State

Keep the same command:

```bash
goodlooks recommend --id 2
```

But generate recommendations through a LangChain agent that returns structured output:

- `summary`
- `first_action`
- `estimated_time_minutes`
- `steps` (ordered list)
- `risks_or_blockers` (list)

## Design Goals

- Preserve fast CLI feel (sensible timeout + fallback).
- Keep output schema stable so UI rendering code remains mostly unchanged.
- Make behavior predictable with guardrails and validation.
- Support offline/local fallback to current heuristic recommender.

## Dependencies

Add these packages:

- `langchain`
- `langchain-openai` (or provider-specific package if you choose Anthropic/Groq/etc.)
- `pydantic` (already likely available transitively, but keep explicit if desired)

Example install:

```bash
python3 -m pip install langchain langchain-openai pydantic
```

Then update `pyproject.toml` dependencies.

## Configuration

Use env vars for model settings:

- `GOODLOOKS_RECOMMENDER_BACKEND` (`langchain` | `heuristic`, default `langchain`)
- `GOODLOOKS_LLM_MODEL` (e.g. `gpt-4o-mini`)
- `OPENAI_API_KEY` (or equivalent provider key)
- `GOODLOOKS_RECOMMENDER_TIMEOUT_SEC` (default `8`)

Behavior:

- If backend is `heuristic`, always use current logic.
- If backend is `langchain` but API key/model call fails or times out, fallback to heuristic.

## Proposed Module Layout

Create a new module:

- `cli/recommender_agent.py`

Suggested contents:

- `Recommendation` (Pydantic model for strict schema)
- `build_recommender_chain()` (lazy initialization)
- `generate_recommendation_with_langchain(task: dict[str, Any]) -> Recommendation`
- `safe_generate_recommendation(task) -> dict[str, Any]` (timeout + fallback adapter)

Keep `recommendation_to_text()` in `goodlooks.py` initially to minimize churn.

## Structured Output Schema

Use strict schema validation to prevent malformed model responses.

```python
class Recommendation(BaseModel):
    summary: str
    first_action: str
    estimated_time_minutes: int = Field(ge=1, le=240)
    steps: list[str] = Field(min_length=2, max_length=8)
    risks_or_blockers: list[str] = Field(default_factory=list, max_length=6)
```

Post-processing guardrails:

- Trim whitespace.
- Remove empty steps.
- Clamp `estimated_time_minutes` to `[1, 240]`.
- If `first_action` is empty, use `steps[0]`.

## Prompt Strategy

Use a system prompt that enforces pragmatic, actionable output:

- You are a productivity coach for short task execution.
- Prefer immediate concrete next actions.
- Recommendations must be realistic for a single person.
- Account for urgency (`low`, `normal`, `high`) and completion state.
- If task is already done, recommend brief review/closure steps.
- Return only valid structured data matching schema.

User input to model:

- Task ID
- Task title
- Urgency
- Done status
- Created time (optional context)

## LangChain Implementation Approach

Use LangChain structured output (preferred) so parsing is native and typed:

1. Initialize chat model (temperature around `0.2`).
2. Bind structured output to `Recommendation`.
3. Invoke with task context.
4. Validate; on failure, fallback to heuristic recommender.

No external tools are required in V1 of this agent. Keep it single-call and fast.

## CLI Integration Plan

In `cli/goodlooks.py`:

1. Keep `recommend` command signature unchanged.
2. Replace direct call to `recommendation_for_task(task)` with:
   - `safe_generate_recommendation(task)` from new module.
3. Keep rendering through `recommendation_to_text(task, rec_dict)` to avoid output changes.

This limits the first migration to a single call site.

## Fallback and Reliability

Fallback conditions:

- Missing API key
- Provider/network error
- Timeout
- Invalid schema response

Fallback action:

- Use existing `recommendation_for_task(task)` logic.
- Optionally print a dim one-line note such as:
  - `Using local recommender fallback.`

## Testing Plan

### Unit tests

- Schema validation rejects malformed output.
- Post-processing normalizes invalid edge values.
- Fallback path triggers on synthetic timeout/error.

### Integration tests

- `goodlooks recommend --id <id>` still prints expected sections.
- Completed task path remains sensible.
- High urgency tasks include immediate first action.

### Manual test checklist

- With API key configured: recommendations vary by task and urgency.
- Without API key: command still works via heuristic fallback.
- Airplane mode/network cut: command degrades gracefully.

## Rollout Plan

1. Add new module + schema + langchain call.
2. Wire command to new `safe_generate_recommendation()`.
3. Keep heuristic logic as fallback (do not delete yet).
4. Add docs section to `README.md` for required env vars.
5. After stable usage, optionally:
   - remove duplicated heuristic branches, or
   - keep heuristic mode as a permanent offline feature.

## Future Enhancements (Optional)

- Add memory/context from recently completed tasks for better suggestions.
- Add a `--style` flag (concise, detailed, aggressive).
- Add `goodlooks recommend --all` to generate prioritized plans for all pending tasks.
- Add cost controls via model routing (`mini` vs `full` models).

