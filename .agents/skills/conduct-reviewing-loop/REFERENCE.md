# Conduct Reviewing Loop Reference & Prompt Templates

Templates, checklist builders, and dual-mode protocols for stress-testing plans (Mode A) and validating codebase diff implementations (Mode B).

---

## 1. Mode A: Pre-Implementation Design Audit Prompt Template

Use when auditing an unapproved plan, RFC, PRD, or skill draft BEFORE writing code:

```markdown
You are <Domain> Reviewer #<N>. Audit the proposed <Artifact Type> draft.

### Required Reading (MUST read using view_file / jcodemunch):
1. Target Artifact Draft: `<draft_path>`
2. System Guidelines / Rules: `<rule_paths>`
3. Task-Specific Domain Skills: `<task_domain_skill_paths>` (e.g., /write-a-skill, /write-for-ai, /writing-great-skills for skill drafts, /tdd for tests, /design-taste-frontend for UI)

### Synthesized Audit Checklist:
1. **User Requirements**: <User-defined high-level constraints and preferences>
2. **System Guidelines**: <Rules from AGENTS.md, /codebase-design, etc.>
3. **Task-Specific Domain Skill Adherence**: <Adherence to /write-a-skill, /write-for-ai, /tdd, etc.>
4. **Domain & Edge-Case Completeness**: <High-level correctness, safety, or performance checks>

### Output Directive:
Return your evaluation to the parent agent using `send_message` containing:
1. Explicit status (`STATUS: PASS` or `STATUS: REVISIONS NEEDED`)
2. Numbered list of findings/required edits (blocking issues)
3. (Optional) `Suggestions for Improvement (Non-blocking)`: Polish or future considerations that do NOT affect PASS status.

Conclude explicitly with either:
- **STATUS: REVISIONS NEEDED** (with a numbered list of required edits to the draft document), OR
- **STATUS: PASS** (if the draft is 100% complete, edge-case safe, and fully compliant).
```

---

## 2. Mode B: Post-Implementation Coverage Validation Prompt Template

Use when auditing actual code changes against an APPROVED plan:

```markdown
You are Implementation Coverage Validator #<N>. Audit the codebase implementation against the approved Implementation Plan (`<plan_path>`).

### Audit Goal:
Verify that 100% of the features, safety guarantees, edge-case fixes, and schema definitions specified in the approved plan are accurately, completely, and correctly implemented in the real codebase. Do NOT invent new requirements or alter the approved implementation plan.

### Required Reading (MUST read using view_file / jcodemunch):
1. Approved Implementation Plan: `<plan_path>`
2. Diff Artifact: `<diff_path>` (e.g. `scratch/patch_changes.diff`)
3. Key Codebase Implementation Files: `<code_file_paths>`
4. Repository Guidelines: `AGENTS.md`
5. Task-Specific Domain Skills: `<task_domain_skill_paths>`

### Implementation Coverage Verification Checklist:
1. **Plan Feature Coverage**: Does the `.diff` and codebase implement 100% of the specified features in the plan?
2. **Safety & Transactional Guarantees**: Are rollback, directory creation, cleanup, and crash recovery mechanisms fully present?
3. **Edge-Case & Line Handling**: Are empty/new file creation, boundary checks, and line end encodings handled correctly?
4. **Validation & State Consistency**: Are duplicate path checks, cache flags, and state initializations accurate?
5. **Backward Compatibility**: Are legacy wrappers and public API schemas fully preserved?

### Output Directive:
Return your evaluation to the parent agent using `send_message` containing:
1. Explicit status (`STATUS: PASS` or `STATUS: REVISIONS NEEDED`)
2. Numbered list of missing plan implementations or defects in the codebase (blocking issues)
3. (Optional) `Suggestions for Improvement (Non-blocking)`: Polish or future considerations that do NOT affect PASS status.

Conclude explicitly with either:
- **STATUS: REVISIONS NEEDED** (with a numbered list of missing plan implementations or defects in the codebase), OR
- **STATUS: PASS** (if 100% of the plan is fully and accurately implemented in the codebase).
```

---

## 3. Checklist Builders

### Pre-Implementation Plan Audit (Mode A)
- [ ] User goals & constraints explicitly addressed
- [ ] Adherence to task-specific domain skills (<task_domain_skill_paths>) verified
- [ ] No hardcoded env values, magic numbers, or fixed pixel layouts
- [ ] Surgical changes: only touch required files
- [ ] Empirical verification plan included (build, test, lint)
- [ ] Rollback or failure recovery strategy present
- [ ] Boundary validation, path normalization, and payload ambiguity prevented
- [ ] Out-of-Scope / Non-Goals exclusions explicitly recorded

### Post-Implementation Coverage Validation (Mode B)
- [ ] 100% of plan components verified in `.diff` and target files
- [ ] Adherence to task-specific domain skills (<task_domain_skill_paths>) verified
- [ ] Unit tests added covering new edge cases specified in plan
- [ ] Tool schemas (`server.py` / parameter schemas) match plan definitions
- [ ] Optimistic locking, cleanup post-rollback, and startup recovery verified in code
- [ ] Zero unhandled exception paths or hidden `AttributeError` / `NameError` bugs
- [ ] Out-of-Scope / Non-Goals exclusions explicitly recorded
