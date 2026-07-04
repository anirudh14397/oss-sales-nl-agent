# Commit style

This is a portfolio repo — the commit history is part of what a reviewer sees,
so treat it as a narrative, not a dump.

## Guidelines
- One logical change per commit (a model, a guardrail, a UI piece — not "wip")
- Message format: `<area>: <what changed>` e.g. `dbt: add net revenue metric definition`
- Avoid `fix typo`, `wip`, `updates` as final commit messages — squash those before merging
- When Claude Code makes a multi-file change, review the diff and split into
  logical commits rather than accepting one giant commit, where practical

## Suggested milestone commits (rough order)
1. `data: add synthetic messy Contoso generator`
2. `dbt: add staging models`
3. `dbt: add star schema marts, handle customer dedup + region hierarchy change`
4. `dbt: define semantic layer metrics`
5. `agent: add metrics client + guardrails`
6. `agent: add orchestration loop with ambiguity detection`
7. `app: add Streamlit chat UI`
8. `eval: add question set + runner`
9. `ci: run eval suite on every PR`
10. `docs: add failure case writeup`
