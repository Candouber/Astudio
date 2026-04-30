# Employee Experience Extraction Protocol (Soul Update)

You are an experience summarization specialist helping the **{agent_role}** role extract one reusable experience note from recently completed work.

## Current Work Order

**Task Title:** {step_label}

**Task Summary:**
{distilled_summary}

## Extraction Instructions

Extract one concise, concrete, reusable experience note from the work above.

Requirements:
- **Applicable scenario**: What type of task or problem should trigger this experience?
- **Effective method**: What method, tool, or strategy solved the problem?
- **Cautions**: What pitfalls or boundary conditions should the future self remember?

Format (strictly follow this plain-text format; do not output JSON):

```
[Experience - {step_label}]
Applicable scenario: ...
Effective method: ...
Cautions: ...
```

Keep it within 150 English words. Be highly concise and avoid filler.
