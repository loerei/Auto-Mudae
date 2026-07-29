---
name: conduct-reviewing-loop
description: Conduct an iterative, multi-turn review loop using independent subagents to stress-test plans (Mode A) or validate plan coverage in code implementation via .diff artifacts (Mode B) until PASS status. Use when reviewing plans, validating implementation drafts, auditing code diffs, or executing multi-agent quality reviews.
---

# Conduct Reviewing Loop

Run iterative, independent subagent reviews to stress-test plans (pre-implementation) or validate codebase diffs against approved specifications (post-implementation) against explicit user and system criteria.

## Dual-Mode Architecture & Workflows

```mermaid
flowchart TD
    Start["Start Review Loop (Version = 1)"] --> ModeSelect{"Select Review Mode"}
    
    ModeSelect -->|"Mode A (Pre-Implementation)"| ModeA["Mode A: Design & Plan Audit<br/>(Refine unapproved draft plan/spec)"]
    ModeSelect -->|"Mode B (Post-Implementation)"| ModeB["Mode B: Implementation Validation<br/>(Audit code .diff against approved plan)"]

    ModeA --> SynthA["1. Synthesize Criteria & Task Domain Skills"] --> PrepDraftA["2. Identify/Prepare Target Draft"] --> ReadRef["3. Read REFERENCE.md Templates"] --> SavePromptV["4. Save Prompt to scratch/reviewer_prompt_v1.md"]
    
    ModeB --> SynthB["1. Synthesize Coverage Verification Checklist"] --> GenDiff["2. Generate scratch/patch_changes.diff"] --> ReadRef
    
    SavePromptV --> UserGateV{"4. User Confirms Prompt?<br/>(Keyword: 'Conduct?')"}
    UserGateV -->|"Confirmed ('Conduct?')"| FreezeV["5. Freeze Prompt v1 as Active Prompt"]
    UserGateV -->|"Prompt Feedback Given"| RefinePrompt["Refine Prompt v1"] --> SavePromptV
    FreezeV --> BlindSpawn["6. Spawn Independent Reviewer #N (Using Active Frozen Prompt)"]
    BlindSpawn --> CritEval{"7. Critical Evaluation of Feedback (Main Agent)"}
    
    CritEval -->|"Valid Edits Needed"| CheckExc{"New User Request OR New Spec?"}
    CheckExc -->|"No (Standard Fix)"| ApplyFix["Apply fixes (Mode A: Draft / Mode B: Code)"] --> BranchMode{"Mode?"}
    BranchMode -->|"Mode B"| Regendiff["Re-generate .diff"] --> NextIter["Iteration N = N + 1"] --> BlindSpawn
    BranchMode -->|"Mode A"| NextIter
    CheckExc -->|"Yes (Exception)"| IncVer["Version = Version + 1"] --> SavePromptNew["Save scratch/reviewer_prompt_v<Version>.md"] --> UserGateNew{"User Confirms Prompt?<br/>(Keyword: 'Conduct?')"}
    UserGateNew -->|"Confirmed ('Conduct?')"| FreezeNew["Freeze v<Version> as Active Prompt"] --> ApplyFix
    UserGateNew -->|"Keep Previous"| KeepPrev["Keep Previous Active Prompt"] --> ApplyFix
    
    CritEval -->|"All Points Evaluated Invalid"| JustifyUser["Document Exclusions & Report to User"] --> UserGateFinal{"User Approves Rationale?"}
    UserGateFinal -->|"Approved"| Present["8. Present Verified Final Output"]
    UserGateFinal -->|"User Rejects Rationale"| ApplyFix
    CritEval -->|"STATUS: PASS"| Present
```

### 1. Mode Selection & Review Matrix

| Mode | Target Artifacts | Primary Goal | Reviewer Action | Termination Condition |
| :--- | :--- | :--- | :--- | :--- |
| **Mode A: Design & Plan Audit** *(Pre-Implementation)* | Draft `implementation_plan.md`, PRD, Skill RFC | Discover architectural gaps, edge cases, and design flaws before coding | Output numbered edits to the **draft document** | `STATUS: PASS` or User-Approved Invalid Rationale |
| **Mode B: Implementation Validation** *(Post-Implementation)* | Approved Plan + `.diff` Artifact + Codebase Files (`src/`, `tests/`) | Verify 100% plan coverage, zero regressions, and full spec compliance | Output numbered missing plan items or defects in the **codebase** | `STATUS: PASS` or User-Approved Invalid Rationale |

### 2. Mode B `.diff` Artifact Workflow

For **Mode B (Post-Implementation Validation)**:
1. **Generate `.diff` Artifact**: Run `git diff origin/<default-branch>` (or target base branch, capturing both committed and working tree edits) and save the untruncated patch to `<appDataDir>\brain\<conversation-id>\scratch\patch_changes.diff`.
2. **Provide 3-Way Context**: Supply the subagent reviewer with:
   - Approved `implementation_plan.md` path.
   - `.diff` artifact path (`scratch/patch_changes.diff`).
   - Core codebase implementation files (`src/`, `tests/`).
3. **Iterative `.diff` Regeneration**: In Mode B, after applying codebase fixes in iteration $N$, the Main Agent MUST re-generate `scratch/patch_changes.diff` before spawning Reviewer $N+1$.
4. **Immutable Plan Principle**: In Mode B, the approved plan is treated as immutable. Reviewers must NOT request edits to the plan; any discrepancy between plan and code must be resolved by fixing the **codebase**.

### 3. Reviewer Loop Protocol (Blind Protocol & Approval Gate)

> [!IMPORTANT]
> **Prompt Persistence & Approval Gate Protocol**:
> 1. **Synthesize Task Domain Skills & Read REFERENCE.md**: Call `view_file` on `REFERENCE.md` to retrieve prompt templates. Always identify and include task-specific domain skills (e.g., `/write-a-skill`, `/write-for-ai`, `/writing-great-skills` when auditing skills; `/tdd` for tests; `/design-taste-frontend` for UI) under `Required Reading` in the reviewer prompt.
> 2. **Save Prompt to File**: Save every reviewer prompt as a markdown file inside `<appDataDir>\brain\<conversation-id>\scratch\reviewer_prompt_v1.md`.
> 3. **Initial User Approval Gate (Disambiguated)**: Present `scratch/reviewer_prompt_v1.md` to the user and **AWAIT EXPLICIT KEYWORD "Conduct?"** (or *"Conduct review"*) before spawning Reviewer #1. *Do NOT ask using "Approve" or "Proceed" for prompt authorization to prevent lower-tier models from confusing prompt confirmation with direct Tier 3 plan/source approval.*
> 4. **Immutable Active Prompt Reuse**: Freeze the approved prompt as Active Prompt ($P_{active}$) and reuse it 100% identically for subsequent reviewers (#2, #3... #N), changing only the Reviewer ID.
> 5. **Prompt Revision Exception (v1 $\rightarrow$ vN)**: Prompt updates (`reviewer_prompt_v<Version>.md`) are permitted ONLY if triggered by explicit user instructions, a newly discovered High-Level Specification, or user-approved exclusions/non-goals (Mode B), all of which require prior user approval.
> 6. **Preventing Blind Reviewer Deadlocks**: If reviewer suggestions are evaluated as invalid/YAGNI by the Main Agent and approved by the User, the rejected items MUST be recorded under an explicit **Out-of-Scope / Non-Goals** section in the document (Mode A) or added as non-goals in `reviewer_prompt_v<Version>.md` (Mode B) so subsequent blind reviewers do not re-raise them.

> [!WARNING]
> **Critical Evaluation Rule (Main Agent Gatekeeper)**: ALWAYS evaluate reviewer feedback critically against YAGNI, empirical codebase facts, and repository rules (`AGENTS.md`). Do NOT blindly apply over-engineered or hallucinated reviewer suggestions. Items listed under `Suggestions for Improvement (Optional / Non-blocking)` do NOT affect `STATUS: PASS` and are non-blocking considerations that must never trigger unnecessary revision iterations.

---

## Output Templates & Checklists

See [REFERENCE.md](REFERENCE.md) for Reviewer Prompt Templates (Mode A & Mode B) and Checklist Builders.
