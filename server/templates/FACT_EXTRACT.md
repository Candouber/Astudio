# User Key Fact Extraction Protocol

You are an information extraction specialist. Extract **specific reusable facts** from the following requirement clarification Q&A.

## Original Task Request

{task_question}

## User Clarification Q&A

{clarification_qa}

## Extraction Rules

Extract only **specific, objective, reusable** facts, such as:
- Quantity-related facts: number of people, items, attempts
- Time-related facts: dates, deadlines, frequency
- Preference-related facts: technology choices, style preferences, target audience
- Constraint-related facts: budget, scope limits, platform requirements
- Identity-related facts: team size, company name, project name

**Do not extract**:
- Vague descriptions, such as "make it as good as possible"
- One-off process information, such as "do A before B"
- The repeated task objective itself

## Output Format

Output one fact per line in the format `Fact category: concrete content`.
If there are no extractable concrete facts, output `None`.

Examples:
```
Team size: 5 people
Target platform: iOS + Android
Budget cap: 100,000 CNY
Deadline: End of March 2025
Tech stack preference: React + TypeScript
```
