---
description: "Recall previous session context from sui-memory database"
allowed-tools: ["Bash(uv run*)"]
---

Retrieve and internalize the previous session context from sui-memory. Do NOT print all memories verbatim. Summarize what was being worked on in 1-3 lines and report it concisely.

```!
uv run --project $SUI_MEMORY_PATH python $SUI_MEMORY_PATH/recall_dump.py
```

Based on the output above, briefly summarize: what project was being worked on, what was the last task, and any pending items. Keep it to 2-3 lines maximum.
