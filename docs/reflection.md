# Reflection: LinkedIn Content Automation

## 1. Design Decisions

**Sequential agents, not full AutoGen.** Four prompts run in one request. Roles stay separate. Deployment stays simple on Ubuntu. Trade-off: no agent debate or tool use.

**Critic agent owns confidence.** Separate scoring avoids self-grading bias. Temperature is low for stable scores.

**Deterministic forbidden-topic check.** Code scans the draft after the critic runs. Matches cap confidence at 0.30. A model alone could miss a banned topic.

**JSON mode for every agent.** Structured output keeps parsing simple. Service validates each reply and returns 502 on bad JSON.

**Dry run by default.** `dryRun` starts true. No post reaches LinkedIn until a human flips the flag.

**Approval Gate logic.** Live posting needs both confidence at or above threshold and `dryRun` false. Everything else goes to Slack.

**CSV log.** No external credentials needed. Every route writes one row, including errors.

**Ubuntu as canonical runtime.** Course environment uses Node.js, n8n, and FastAPI on Ubuntu. HTTP node targets `http://127.0.0.1:8000/linkedin`.

## 2. Challenges

- LLM output shape varies. Validation and clamping handle it.
- Confidence scores drift by model and prompt. Threshold needs tuning from logged runs.
- LinkedIn OAuth needs developer app approval. Live path stays prepared but untested until credentials exist.
- Set node cannot avoid expressions entirely. Hashtag join uses one small expression.
- Slack webhook must be replaced in Brand Config before dry-run E2E succeeds.

## 3. Trade-offs

| Choice | Gain | Cost |
|--------|------|------|
| `gpt-4o-mini` | Low cost, fast | Lower writing quality |
| Sequential calls | Simple, debuggable | Four round trips, slower |
| Slack webhook | Easy setup | No interactive approve button |
| CSV log | Zero setup | No querying, no concurrency safety |
| No service auth | Faster build | Must stay on private network |

## 4. Validation evidence (Ubuntu / WSL)

- `GET /health` returned `ok` with `mock_llm: true` during smoke runs.
- `POST /linkedin` returned ideas, draft, confidence, hashtags, meta.
- Invalid body `{}` returned HTTP 400 `validation_error`.
- `scripts/e2e_dry_run_sim.py` confirmed Approval Gate dry-run routes to `slack_review`, low confidence routes to Slack, and live mode routes to `linkedin_live`.
- Artifacts: `docs/screenshots/health.json`, `linkedin_response.json`, `approval_gate_dry_run.json`, `linkedin_run_log.csv`.
- n8n workflow JSON parses cleanly. Import on the course VM after `n8n start`, then replace Slack webhook and capture UI screenshots.

## 5. Next Steps

- Add Slack approve button that triggers LinkedIn post.
- Add API key header on `/linkedin`.
- Store history in a database.
- Add brand-voice examples to prompts.
- Capture n8n canvas and Slack webhook PNGs on the graded Ubuntu VM.
