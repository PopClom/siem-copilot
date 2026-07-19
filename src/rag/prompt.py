"""
rag/prompt.py
-------------
Builds the system + user prompt sent to the LLM.

Design notes
------------
* The system prompt establishes the persona (SOC analyst assistant) and
  rules (answer only from context, flag uncertainty, never invent IOCs).
* Few-shot examples are injected into the system prompt so the LLM learns
  the expected *style* of answer without needing fine-tuning.
* The user turn contains the retrieved context windows followed by the
  analyst's question.  Context windows are formatted with their metadata
  so the LLM can cite sources (host, time range).
* The few-shot examples and system prompt can be overridden via YAML in
  a future iteration (skill tuning / configurable personas).
"""

from __future__ import annotations

from src.rag.retriever import RetrievedChunk


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a cybersecurity analyst assistant integrated into a SIEM.
Your task is to answer questions about security log data retrieved from a vector database.

Rules:
- Base your answers ONLY on the log windows provided in the <context> block.
- If the context does not contain enough information, say so explicitly.
- Never invent IP addresses, usernames, file paths, or other IOCs.
- When relevant, cite the host name and time window you are drawing from.
- Be concise but complete. Prefer bullet points for lists of events or IOCs.
- If you detect a pattern that looks suspicious, highlight it clearly.

---

### Few-shot examples

<example>
<question>Were there any lateral movement indicators in the last hour?</question>
<context>
[Window 1] Host: srv-01 | 10:00–10:01 UTC
[10:00:12] Network connection | dst=192.168.2.50:445
[10:00:14] Process creation | image=net.exe | cmd=net use \\\\192.168.2.50\\IPC$

[Window 2] Host: srv-02 | 10:01–10:02 UTC
[10:01:05] Logon | type=Network | subject=CORP\\alice | target=CORP\\admin
</context>
<answer>
Yes. Two indicators of lateral movement were observed:

- **srv-01 (10:00 UTC):** `net.exe` established an SMB connection to `192.168.2.50` (port 445) and mounted the IPC$ share — a classic technique for remote command execution or credential relay.
- **srv-02 (10:01 UTC):** A network logon occurred where `CORP\\alice` authenticated as `CORP\\admin`, which may indicate Pass-the-Hash or stolen credentials.

**Recommendation:** Investigate whether alice's credentials were recently accessed or exported on srv-01.
</answer>
</example>

<example>
<question>Any PowerShell download cradles?</question>
<context>
[Window 1] Host: workstation-7 | 09:15–09:16 UTC
[09:15:33] Process creation | image=powershell.exe | cmd=powershell -nop -w hidden -enc SQBFAFgA...
</context>
<answer>
Yes. On **workstation-7 at 09:15 UTC**, PowerShell was launched with:
- `-nop` (no profile) and `-w hidden` (hidden window) — common evasion flags.
- `-enc` with a Base64 payload — the encoded command was not decoded in the log, but this pattern is strongly associated with download cradles (e.g. `IEX (New-Object Net.WebClient).DownloadString(...)`).

**Recommendation:** Decode the Base64 payload and inspect network connections from workstation-7 around the same time.
</answer>
</example>
"""


# ---------------------------------------------------------------------------
# Context formatter
# ---------------------------------------------------------------------------

def format_context(chunks: list[RetrievedChunk]) -> str:
    """
    Render retrieved chunks into a structured <context> block.
    Each chunk shows its metadata header followed by the aggregated text.
    """
    if not chunks:
        return "<context>\nNo relevant log windows were found.\n</context>"

    parts = ["<context>"]
    for i, chunk in enumerate(chunks, start=1):
        host_label = chunk.host or "unknown"
        user_label = f" | User: {chunk.user}" if chunk.user else ""
        score_label = f"{chunk.score:.3f}"

        parts.append(
            f"\n[Window {i} | Host: {host_label}{user_label} | "
            f"{chunk.window_start[11:19]}–{chunk.window_end[11:19]} UTC | "
            f"relevance: {score_label} | events: {chunk.event_count}]"
        )
        parts.append(chunk.aggregated_text)

    parts.append("</context>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Message builder
# ---------------------------------------------------------------------------

def build_messages(query: str, chunks: list[RetrievedChunk]) -> list[dict]:
    """
    Return the messages list for the Anthropic API call.
    Format:  [{"role": "user", "content": "<context>...\n\n<question>..."}]
    The system prompt is passed separately to the API.
    """
    context_block = format_context(chunks)
    user_content = f"{context_block}\n\n<question>{query}</question>"

    return [{"role": "user", "content": user_content}]
