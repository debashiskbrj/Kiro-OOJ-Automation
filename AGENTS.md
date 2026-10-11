# AGENTS

<!-- neurotrace-start -->
## NeuroTrace Workflow

- Check NeuroTrace is available with `neurotrace_getDatabaseStatus`.
- If necessary, review pending tasks with `neurotrace_listThoughts` using type_filter "task" to understand the current backlog and priorities.
- When you already know the current file or module, use `neurotrace_getMemoriesByFile` first for file-scoped context.
- When the problem is fuzzy or unfamiliar, use `neurotrace_semanticSearch` first to discover relevant context.
- Use `neurotrace_searchThoughts` next to refine with exact terms, names, or IDs once you have concrete keywords.
- If broader context is needed, use `neurotrace_listThoughts` without filters to review recent entries.
- When you find a relevant memory and need connected context, use `neurotrace_suggestRelated` or `neurotrace_getGraphData` to expand the investigation.
- Base plans, code changes, and debugging steps on the relevant NeuroTrace context you find.
- Only save durable, high-signal memories: important decisions, non-obvious findings, root causes, concrete follow-up tasks, or unresolved hypotheses.
- Do not save routine progress updates, trivial code changes, temporary debugging notes, or facts already obvious from the code.
- When saving a memory tied to code, include file_path and line whenever possible; include snippet when it helps locate the relevant block.
- If the file no longer exists or the memory is historical, mark it clearly in the memory text or tags.
- Before creating a new memory, prefer updating or linking an existing related one if it already covers the same point.
<!-- neurotrace-end -->
