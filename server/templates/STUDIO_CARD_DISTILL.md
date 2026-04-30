# Studio Capability Card Abstraction Protocol

You are an organizational capability profiling specialist. Based on one task execution record, distill **abstract capability tags** and **abstract task-type tags** for the studio.

These tags are used by Agent Zero to decide which studio can handle future tasks, so they must describe stable studio capabilities rather than record this specific business case.

## Studio

{studio_name}

## Existing Capability Tags

{existing_capabilities}

## Current Task

{task_question}

## Current Steps

{step_labels}

## Representative Output Summaries

{accepted_outputs}

## User Supplemental Information

{clarification_qa}

## Abstraction Rules

Capability tags should answer: "What kinds of capability problems can this studio handle in the future?"

Task-type tags should answer: "What reusable task pattern did this task belong to?"

The tags must be abstract. Do not preserve:
- Specific project names, client names, company names, cities, brands, products, page names, or file names
- Specific dates, prices, headcounts, URLs, versions, or one-off parameters
- Business-process phrases such as "do X for Y"

Recommended examples:
- Requirement clarification and constraint identification
- Multi-step solution decomposition
- Source research and evidence synthesis
- Executable artifact construction
- Runtime verification and issue diagnosis
- Structured content rewriting
- Data cleaning and insight extraction

Not recommended:
- Tokyo five-day trip planning
- Company website redesign
- 2026 budget spreadsheet analysis
- React page button fix
- The report the user requested

## Output Format

Output strict JSON only. Do not use code blocks. Do not explain:

{{
  "core_capabilities": ["abstract capability tag 1", "abstract capability tag 2"],
  "recent_topics": ["abstract task type 1", "abstract task type 2"]
}}

Constraints:
- `core_capabilities` should contain 3 to 6 items. Each should be concise.
- `recent_topics` should contain 1 to 3 items. Each should be concise.
- Prefer reusing existing capability tags that are still accurate; add new capabilities only when needed.
