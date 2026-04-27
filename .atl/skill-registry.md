# Skill Registry

**Delegator use only.** Any agent that launches sub-agents reads this registry to resolve compact rules, then injects them directly into sub-agent prompts. Sub-agents do NOT read this registry or individual SKILL.md files.

See `_shared/skill-resolver.md` for the full resolution protocol.

## User Skills

| Trigger | Skill | Path |
|---------|-------|------|
| When user asks to create a new skill, add agent instructions, or document patterns for AI | skill-creator | /home/loonbac/.config/opencode/skills/skill-creator/SKILL.md |
| When writing Go tests, using teatest, or adding test coverage | go-testing | /home/loonbac/.config/opencode/skills/go-testing/SKILL.md |
| When user says "judgment day", "judgment-day", "review adversarial", "dual review", "doble review", "juzgar", "que lo juzguen" | judgment-day | /home/loonbac/.config/opencode/skills/judgment-day/SKILL.md |
| When creating a GitHub issue, reporting a bug, or requesting a feature | issue-creation | /home/loonbac/.config/opencode/skills/issue-creation/SKILL.md |
| When creating a pull request, opening a PR, or preparing changes for review | branch-pr | /home/loonbac/.config/opencode/skills/branch-pr/SKILL.md |
| When user says "caveman mode", "talk like caveman", "use caveman", "less tokens", "be brief", or invokes /caveman | caveman | /home/loonbac/.agents/skills/caveman/SKILL.md |

## Compact Rules

Pre-digested rules per skill. Delegators copy matching blocks into sub-agent prompts as `## Project Standards (auto-resolved)`.

### skill-creator
- Create skill when pattern used repeatedly, project-specific conventions differ, complex workflows need step-by-step, or decision trees help
- Don't create when documentation already exists or pattern is trivial
- Structure: `skills/{skill-name}/SKILL.md` + optional `assets/` and `references/`
- Frontmatter required: name, description (includes trigger), license (Apache-2.0), metadata
- DO: start with critical patterns, use tables, keep examples minimal, include Commands section
- DON'T: add Keywords, duplicate content, lengthy explanations, troubleshooting sections, web URLs in references
- After creating, add to AGENTS.md

### go-testing
- Use table-driven tests for multiple test cases with name/input/expected/wantErr
- Test TUI: use teatest.NewTestModel() for full flow, test Model.Update() directly for state changes
- Golden file testing: compare output against saved files in testdata/
- Mock dependencies via interfaces; use t.TempDir() for file operations
- Commands: go test ./..., go test -v, go test -cover, go test -update, go test -short

### judgment-day
- Launch TWO blind judges in parallel (delegate async) — neither knows about the other
- Synthesize verdict: Confirmed (both agree), Suspect A/B (one judge), Contradiction (disagree)
- WARNING classification: real = normal user can trigger; theoretical = requires contrived scenario
- Fix and re-judge: Fix Agent applies confirmed issues, then re-launch both judges
- After 2 fix iterations: ask user to continue or escalate
- Blocking: MUST NOT approve until clean, MUST NOT push before re-judgment, MUST NOT skip rounds
- Skill Resolution: inject Project Standards from registry, check fallback reports

### issue-creation
- Blank issues disabled — MUST use bug_report.yml or feature_request.yml template
- Every issue gets status:needs-review automatically; maintainer must add status:approved
- Questions go to Discussions, not issues
- Bug report required: pre-flight checks, description, steps to reproduce, expected/actual behavior, OS, agent, shell
- Feature request required: pre-flight checks, problem description, proposed solution, affected area

### branch-pr
- Every PR MUST link an approved issue (status:approved label)
- Every PR MUST have exactly one type:* label
- Branch naming: `type/description` matching regex `^(feat|fix|chore|docs|style|refactor|perf|test|build|ci|revert)/[a-z0-9._-]+$`
- PR body MUST include: linked issue (Closes #N), PR type checkbox, summary, changes table, test plan
- Conventional commits: `type(scope): description` matching `^(build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)(\([a-z0-9\._-]+\))?!?: .+`
- No Co-Authored-By trailers; automated checks must pass before merge

### caveman
- Drop: articles (a/an/the), filler (just/really/basically/actually/simply), pleasantries, hedging
- Fragments OK. Short synonyms. Technical terms exact. Code blocks unchanged
- Pattern: `[thing] [action] [reason]. [next step].`
- Default: full. Switch: `/caveman lite|full|ultra`
- Auto-caveman off for: security warnings, irreversible confirmations, multi-step sequences, user asks clarification

## Project Conventions

No project conventions found — empty project directory.

## Next Steps

The orchestrator reads this registry once per session and passes pre-resolved skill paths to sub-agents via their launch prompts.
To update after installing/removing skills, run the skill-registry skill again.
