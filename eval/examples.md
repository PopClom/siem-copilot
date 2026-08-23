# SIEM Copilot — Examples

This document contains extended examples and sample outputs from SIEM Copilot.

## 1. Anomaly detection

Run:

```bash
python main.py --detect-anomalies
```

A representative output is:

```text
============================================================
  Anomaly Detection Results
============================================================
  Windows analysed : 53
  Anomalies found  : 31 (58.5%)
  Behaviour clusters: 2
  HDBSCAN noise    : 58.5%

  Top anomalous windows:
    [OUTLIER] host=win-dc-8537412.attackrange.local 12:56:55–12:57:05 UTC if_score=1.000 cluster=-1
    [OUTLIER] host=win-dc-8537412.attackrange.local 12:54:05–12:54:15 UTC if_score=0.980 cluster=-1
    [OUTLIER] host=win-dc-8537412.attackrange.local 12:57:00–12:57:10 UTC if_score=0.954 cluster=-1
    [OUTLIER] host=win-dc-8537412.attackrange.local 12:53:30–12:53:40 UTC if_score=0.899 cluster=-1
    [OUTLIER] host=win-dc-8537412.attackrange.local 12:54:10–12:54:20 UTC if_score=0.840 cluster=-1
```

## 2. LLM anomaly summary

The anomaly detector can be followed by an LLM-generated summary.

<details>
<summary><strong>Show full example</strong></summary>

```text
============================================================
  LLM Summary
============================================================
## Anomaly Summary — win-dc-8537412.attackrange.local (12:52–12:57 UTC)

**Scope:** 31 of 53 windows (58.5%) flagged as anomalous, all on a single host (`win-dc-8537412.attackrange.local`), all HDBSCAN noise points (no stable cluster membership) within a tight ~5-minute span (12:52:40–12:57:30 UTC). This clustering in time on one host is itself a strong signal that a single incident/attack chain is driving the anomalies, not random noise.

### Chronological breakdown

- **12:52:40–12:52:55 UTC (Anomalies 6 & 7, if_score 0.81–0.84):**
  Bursts of `driver_loaded` events, a `registry_value_set` on the `System` hive, and `process_create` for `autochk.exe` (`/q /v *`) accessing `smss.exe`. This pattern is consistent with **early boot/session-manager activity** — could be legitimate startup, but the volume (323 events in one 10s window) is highly unusual and worth confirming against a normal baseline for this DC.

- **12:53:30–12:53:40 UTC (Anomaly 4, if_score 0.899, 138 events):**
  Heavy `dns_query` activity from **lsass.exe, dns.exe, svchost.exe**, plus repeated `process_access` into **lsass.exe**. Processes performing DNS queries is atypical, and repeated access to `lsass.exe` is a classic **credential-dumping indicator** (e.g., Mimikatz-style memory access).

- **12:54:05–12:54:20 UTC (Anomalies 2 & 5, if_score 0.98 / 0.84, 97–108 events):**
  `powershell.exe` created, immediately followed by **process_access into lsass.exe**, multiple `dns_query` calls, and repeated `network_connection` events from PowerShell. This is a **high-confidence indicator of credential access + C2/exfil activity via PowerShell** — the combination of PowerShell touching LSASS memory and then making outbound network/DNS calls strongly resembles a credential-dumping-and-exfiltration or beaconing pattern.

- **12:54:40–12:54:50 UTC (Anomaly 8, if_score 0.728):**
  Further isolated `process_access` events against `lsass.exe` — continued probing/access of LSASS after the PowerShell activity.

- **12:56:55–12:57:10 UTC (Anomalies 1 & 3, if_score 0.95–1.00, 32 events each):**
  Repeated `registry_value_set` / `registry_object_create_delete` operations tied to **`WMIADAP.EXE`** (WMI Performance Adapter). This could be legitimate WMI provider re-registration, but given the preceding LSASS/PowerShell activity, it warrants review as possible **persistence via WMI**.

- **12:57:15–12:57:30 UTC (Anomalies 9 & 10, if_score ~0.71–0.72):**
  Single `process_access` events into **`TrustedInstaller.exe`**, a highly privileged servicing process. Access to TrustedInstaller is unusual and often associated with **privilege escalation or tampering with protected system files/permissions**.

### Pattern Assessment

This is a **coherent attack-chain narrative**, not scattered noise:
1. Boot/driver activity (12:52) →
2. DNS/LSASS touching by system processes (12:53) →
3. PowerShell spawned, accesses LSASS, makes network/DNS calls (12:54) →
4. Repeated LSASS access continues (12:54:40) →
5. WMI registry manipulation (12:56:55–12:57:10) →
6. TrustedInstaller process access (12:57:15–12:57:30)

The **repeated lsass.exe process_access events correlated with PowerShell network/DNS activity** is the most security-relevant finding — this sequence is consistent with **credential dumping followed by potential C2 communication**, possibly followed by **WMI-based persistence** and an attempt to interact with **TrustedInstaller** for elevated privileges.

**Recommendation:**
- Prioritize investigating the **12:54:05–12:54:20 UTC PowerShell → lsass.exe → network_connection** sequence as the core of the incident.
- Pull full command-line arguments for the `powershell.exe` process (truncated in this context) and identify the destination IPs/domains from the `dns_query`/`network_connection` events.
- Review the WMIADAP registry changes (12:56:55–12:57:10) for unauthorized WMI subscriptions (persistence).
- Confirm whether the `TrustedInstaller.exe` access (12:57:15–12:57:30) was user/process-initiated or automated (e.g., Windows Update), given its proximity to the suspected credential-access activity.
- Baseline the 12:52:40–12:52:55 driver-load burst against known-good boot sequences for this DC to rule out false positive.
```

</details>

## 3. Querying the API

Start the API with:

```bash
uvicorn api.main:app --reload --port 8000
```

### Example: lateral movement

Request:

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"Were there any lateral movement indicators?"}'
```

Example response:

```json
{
  "answer": "Based on the provided log windows from **win-dc-8537412.attackrange.local (12:54:55–12:55:25 UTC)**, I do not see any indicators of lateral movement.\n\n**What the logs actually show:**\n- A large volume of Sysmon `process_access` (EventID=10) events involving `TiWorker.exe` (Windows Servicing Stack) and standard system processes (`svchost.exe`, `csrss.exe`, `conhost.exe`) — consistent with routine Windows Update/servicing activity.\n- A series of `process_create` (EventID=1) events for **SplunkUniversalForwarder** components (`splunk-admon.exe`, `splunk-netmon.exe`, `splunk-powershell.exe`, `splunk-regmon.exe`, `splunk-winprintmon.exe`, `btool.exe`, `splunk.exe restart`) — this is normal Splunk Universal Forwarder startup/restart behavior, including its modular input processes and `btool` configuration checks.\n- No network connections (e.g., SMB/445, WinRM/5985, RDP/3389), no `net.exe`/`net1.exe` usage, no remote logon events (Type 3/10), and no credential-dumping tools (e.g., `mimikatz`, `procdump`, `lsass` access) are present in this context.\n\n**Assessment:**\nThis activity pattern is consistent with **benign local host maintenance** — Windows servicing (TiWorker) and Splunk Forwarder restart/reconfiguration — rather than an attack. There are no cross-host connections, remote authentication events, or remote execution artifacts that would indicate lateral movement.\n\n**Caveat:** This context only covers a single host (`win-dc-8537412.attackrange.local`) over a ~30-second window. If lateral movement occurred, it would likely appear in network connection logs, authentication/logon events, or remote process creation on other hosts — none of which are present in this context. I cannot confirm or rule out lateral movement outside this window based on the data provided.",
  "query": "Were there any lateral movement indicators?",
  "chunks_retrieved": 8,
  "chunks_used": 2,
  "latency_ms": 11979,
  "sources": [
    {
      "window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:54:55+00:00",
      "host": "win-dc-8537412.attackrange.local",
      "window_start": "2020-10-08T12:54:55+00:00",
      "window_end": "2020-10-08T12:55:05+00:00",
      "score": 0.6932,
      "event_count": 2
    },
    {
      "window_id":"splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:55:15+00:00",
      "host":"win-dc-8537412.attackrange.local",
      "window_start":"2020-10-08T12:55:15+00:00",
      "window_end":"2020-10-08T12:55:25+00:00",
      "score":0.6859,
      "event_count":422
    }
  ]
}
```

## 4. Credential dumping / Active Directory extraction query

Request:

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"Did you detect any evidence of credential dumping or Active Directory database extraction? If so, summarize the activity?"}'
```

### Without HyDE

```json
{
  "answer": "Based on the provided log windows, I did **not** find direct evidence of credential dumping tools (e.g., `mimikatz`, `procdump`, `ntdsutil.exe`, `vssadmin.exe`, `esentutl.exe`) or Active Directory database extraction (no references to `ntds.dit`, `SYSTEM` hive backup, or shadow-copy creation of the AD database).\n\nHowever, one item is worth flagging for further review:\n\n- **win-dc-8537412.attackrange.local, 12:55:07–12:55:08 UTC:** Multiple Sysmon **process_access (EventID=10)** events show `lsass.exe` being accessed several times in quick succession, interleaved with accesses to `services.exe`, `svchost.exe`, and `csrss.exe`.\n  - `lsass.exe` is the process that holds credential material in memory, so *any* access to it warrants scrutiny — this is the same technique used by tools like Mimikatz or `procdump.exe -ma lsass.exe` to extract credentials.\n  - **Critical caveat:** The log lines in this context only show the **target** image (`lsass.exe`), not the **source/calling process** or the requested access mask (e.g., `0x1010` / `PROCESS_VM_READ` would be highly suspicious, whereas standard service-level access like `0x1000` is normal). Since Sysmon EventID 10 always logs routine, benign LSASS accesses from `services.exe`, `svchost.exe`, and other legitimate system processes (this happens constantly on any Windows host), **this pattern alone is not sufficient to confirm malicious credential dumping.**\n\n- The `C:\\Windows\\System32\\msdtc.exe` process creation at 12:55:07 is the standard Distributed Transaction Coordinator service and is not inherently suspicious in this context.\n\n- The bulk of Window 2 (422 events) is dominated by high-volume, routine Splunk Universal Forwarder activity (`splunk-admon.exe`, `splunk-netmon.exe`, `splunk-powershell.exe`, `splunk-regmon.exe`, `splunk-winprintmon.exe`, `btool.exe`) and Windows servicing stack (`TiWorker.exe`) noise — none of which relates to credential access.\n\n**Assessment:** No confirmed evidence of credential dumping or NTDS.dit extraction. The repeated `lsass.exe` process_access events on the domain controller are a normal artifact of Sysmon EventID 10 logging but should be validated against the **source process name and GrantedAccess mask**, which are not visible in this excerpt.\n\n**Recommendation:** Pull the full EventID 10 records for the 12:55:07–12:55:08 window on `win-dc-8537412.attackrange.local` and inspect the `SourceImage` and `GrantedAccess` fields for any non-standard process (e.g., `procdump.exe`, `rundll32.exe`, `taskmgr.exe`, or unsigned binaries) requesting access to `lsass.exe` with read/memory-dump permissions (commonly `0x1410`, `0x1010`, or `0x1fffff`).",
  "query": "Did you detect any evidence of credential dumping or Active Directory database extraction? If so, summarize the activity?",
  "chunks_retrieved": 8,
  "chunks_used": 3,
  "latency_ms": 44988,
  "hyde_used": false,
  "hypothetical_doc": null,
  "sources": [
    {
      "window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:54:55+00:00",
      "host": "win-dc-8537412.attackrange.local",
      "window_start": "2020-10-08T12:54:55+00:00",
      "window_end": "2020-10-08T12:55:05+00:00",
      "score": 0.7416,
      "event_count": 2
    },
    {
      "window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:55:15+00:00",
      "host": "win-dc-8537412.attackrange.local",
      "window_start": "2020-10-08T12:55:15+00:00",
      "window_end": "2020-10-08T12:55:25+00:00",
      "score": 0.7395,
      "event_count": 422
    },
    {
      "window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:55:00+00:00",
      "host": "win-dc-8537412.attackrange.local",
      "window_start": "2020-10-08T12:55:00+00:00",
      "window_end": "2020-10-08T12:55:10+00:00",
      "score": 0.7389,
      "event_count": 60
    }
  ]
}
```

### With HyDE

```json
{
  "answer": "## Yes — Strong Evidence of NTDS.dit Extraction (T1003.003)\n\n**Host:** win-dc-8537412.attackrange.local | **Time window:** 12:54:25–12:54:32 UTC\n\nThis activity shows a clear, multi-step Active Directory credential dumping attack via Volume Shadow Copy, consistent with the **Atomic Red Team T1003.003** technique (explicitly referenced in the logs).\n\n### Attack Sequence\n\n1. **Reconnaissance / Setup (12:54:25–26)**\n   - PowerShell launched with an encoded command that imports a module from `C:\\AtomicRedTeam\\invoke-atomicredteam\\Invoke-AtomicRedTeam...` — confirming this is an Atomic Red Team test execution.\n   - `whoami.exe` and `HOSTNAME.EXE` run to confirm host/user context.\n   - `reg query ... ProductOptions /v ProductType | findstr LanmanNT` executed twice — used to determine if the host is a Domain Controller (LanmanNT = DC check).\n\n2. **Precondition Checks (12:54:26)**\n   - `cmd.exe /c \"if not exist \\\\?\\GLOBALROOT\\Device\\HarddiskVolumeShadowCopy1 (exit /b 1)\"` — checking for existing shadow copies.\n   - Echo command referencing: *\"Run Invoke-AtomicTest T1003.003 -TestName 'Create Volume Shadow Copy with NTDS.dit'\"* — direct confirmation of the MITRE ATT&CK technique being simulated/executed.\n   - `if not exist C:\\Windows\\Temp (exit /b 1)` — staging directory check.\n\n3. **Shadow Copy Creation (12:54:29)**\n   - `vssadmin.exe create shadow /for=C:` — creates a Volume Shadow Copy of the C: drive, bypassing file locks on NTDS.dit.\n   - `VSSVC.exe` spawned to service the request; `spoolsv.exe` issued DNS queries around the same time (possibly incidental).\n\n4. **NTDS.dit and SYSTEM Hive Extraction (12:54:29)**\n   - Critical command:\n     ```\n     cmd.exe /c \"copy \\\\?\\GLOBALROOT\\Device\\HarddiskVolumeShadowCopy1\\Windows\\NTDS\\NTDS.dit C:\\Windows\\Temp\\ntds.dit\n     & copy \\\\?\\GLOBALROOT\\Device\\HarddiskVolumeShadowCopy1\\Windows\\System32\\config\\SYSTEM C:\\Windows\\Temp\\VSC_SYSTEM_HIVE\n     & reg save HKLM\\SYSTEM C:\\Windows\\Temp\\SYSTEM_HIVE\"\n     ```\n   - This copies the **NTDS.dit database** (containing all AD user password hashes) and the **SYSTEM registry hive** (needed to decrypt the boot key/hashes) out of the shadow copy to `C:\\Windows\\Temp\\`.\n   - `reg.exe save HKLM\\SYSTEM C:\\Windows\\Temp\\SYSTEM_HIVE` — redundant SYSTEM hive save via the registry API.\n\n5. **NTDSUtil IFM Extraction (12:54:29)**\n   - `cmd.exe /c \"mkdir C:\\Windows\\Temp\\ntds_T1003 & ntdsutil \"ac i ntds\" \"ifm\" \"create full C:\\Windows\\Temp\\ntds_T1003\" q q\"`\n   - This is the classic **`ntdsutil` \"Install From Media\" (IFM)** method — creates a full copy of the AD database plus registry hives specifically for offline credential extraction (e.g., with tools like `secretsdump.py` or `DSInternals`).\n\n6. **Mass LSASS Access (12:54:29–12:54:32)**\n   - An extremely high volume of `process_access` events targeting **`lsass.exe`** occurs continuously through the end of the window — dozens of handle opens per second, sustained for ~3 seconds. This is highly anomalous and consistent with either:\n     - Credential dumping tools reading LSASS memory (e.g., Mimikatz-style access), or\n     - `ntdsutil`/`vssvc` legitimately interacting with LSASS during the IFM snapshot process — but the sheer volume warrants scrutiny.\n\n7. **Remote Execution Indicators**\n   - `WinrsHost.exe -Embedding` process creation followed by an encoded `powershell.exe` command indicates this activity may have been triggered remotely via **WinRM** (Windows Remote Management), suggesting the attacker/tester had remote administrative access to the DC.\n\n### IOCs Observed\n- `C:\\Windows\\Temp\\ntds.dit`\n- `C:\\Windows\\Temp\\VSC_SYSTEM_HIVE`\n- `C:\\Windows\\Temp\\SYSTEM_HIVE`\n- `C:\\Windows\\Temp\\ntds_T1003\\` (ntdsutil IFM output directory)\n- Command-line references to `Invoke-AtomicTest T1003.003`\n\n### Assessment\nThis is a textbook **NTDS.dit dumping** operation using **two parallel methods** (manual VSS copy + `ntdsutil` IFM) — both designed to exfiltrate the entire Active Directory credential database from a Domain Controller. Combined with the `AtomicRedTeam` module reference, this strongly suggests either an authorized adversary-emulation/red-team exercise or an actual attacker leveraging the same technique.\n\n**Recommendation:**\n- Immediately verify whether this was an authorized red-team/Atomic Red Team test.\n- If not authorized, treat as critical incident: isolate the DC, rotate **all domain credentials** (especially krbtgt), and forensically image `C:\\Windows\\Temp\\ntds.dit`, `SYSTEM_HIVE`, and the `ntds_T1003` folder before remediation.\n- Review W",
  "query": "Did you detect any evidence of credential dumping or Active Directory database extraction? If so, summarize the activity?",
  "chunks_retrieved": 8,
  "chunks_used": 1,
  "latency_ms": 36439,
  "hyde_used": true,
  "hypothetical_doc": "Host: DC01-CORP | Window: 03:14:02–03:16:47 UTC\n[03:14:02] Sysmon EID1 | ProcessCreate | Image=C:\\Windows\\System32\\cmd.exe | CommandLine=\"cmd.exe /c vssadmin create shadow /for=C:\" | User=CORP\\svc_backup | ParentImage=powershell.exe\n[03:14:09] Sysmon EID1 | ProcessCreate | Image=C:\\Windows\\System32\\vssadmin.exe | CommandLine=\"vssadmin create shadow /for=C:\" | User=CORP\\svc_backup\n[03:14:22] Windows-Security EID4688 | NewProcess=ntdsutil.exe | CommandLine=\"ntdsutil.exe \\\"ac i ntds\\\" \\\"ifm\\\" \\\"create full C:\\Temp\\ntdsdump\\\" q q\" | User=CORP\\svc_backup\n[03:14:58] Sysmon EID11 | FileCreate | TargetFilename=C:\\Temp\\ntdsdump\\Active Directory\\ntds.dit | Image=ntdsutil.exe\n[03:15:10] Sysmon EID11 | FileCreate | TargetFilename=C:\\Temp\\ntdsdump\\registry\\SYSTEM | Image=ntdsutil.exe\n[03:15:44] Sysmon EID3 | NetworkConnect | SourceIP=10.10.5.15 | DestIP=198.51.100.22 | DestPort=445 | Image=svchost.exe | User=CORP\\svc_backup\n[03:16:12] Sysmon EID11 | FileCreate | TargetFilename=C:\\Windows\\Temp\\ntds_exfil.7z | Image=7z.exe | User=CORP\\svc_backup\n[03:16:47] Windows-Security EID4663 | ObjectAccess | Object=C:\\Temp\\ntdsdump\\Active Directory\\ntds.dit | AccessMask=DELETE | User=CORP\\svc_backup",
  "sources": [
    {
      "window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:54:25+00:00",
      "host": "win-dc-8537412.attackrange.local",
      "window_start": "2020-10-08T12:54:25+00:00",
      "window_end": "2020-10-08T12:54:35+00:00",
      "score": 0.9112,
      "event_count": 906
    }
  ]
}
```

### Smaller windows, higher top-k, sanitized logs

Configuration:

- `time-window = 5s`.
- `expand-context = false`.
- `top-k = 20`.
- `use-hyde = true`.
- Sanitized logs.

<details>
<summary><strong>Show full response</strong></summary>

```json
{
  "answer": "## Yes — Strong Evidence of Active Directory Database (NTDS.dit) Extraction\n\nClear indicators of a credential dumping / NTDS extraction attack chain were found on **`win-dc-8537412.attackrange.local`** (a domain controller) between **12:52:10 – 12:57:25 UTC**. This aligns closely with **MITRE ATT&CK T1003.003 (OS Credential Dumping: NTDS)**.\n\n### Key Evidence\n\n**1. Direct reference to NTDS.dit and registry hive artifacts (12:54:39 UTC)**\n```\ncmd.exe /c \"del \"C:\\Windows\\Temp\\ntds.dit\" >nul 2> nul & del \"C:\\Windows\\Temp\\VSC_SYSTEM_HIVE\" >nul 2> nul & del \"C:\\Windows\\Temp\\SYSTEM_HIVE\" >nul 2> nul\"\ncmd.exe /c \"rmdir /q /s C:\\Windows\\Temp\\ntds_logs >nul 2>&1\"\n```\n- This is a **cleanup/anti-forensics step** — deleting the extracted `ntds.dit` (the AD database file containing all domain password hashes), the exported `SYSTEM` registry hive (needed to decrypt the database), and associated log files from a staging location (`C:\\Windows\\Temp`). This staging path and hive-naming convention is consistent with tools like **secretsdump.py**, **ntdsutil**, or **Invoke-NinjaCopy/DSInternals**.\n\n**2. Volume Shadow Copy service activity (12:54:31–12:54:32 UTC)**\n```\nSysmon process_access | C:\\Windows\\system32\\vssvc.exe\n```\n- `vssvc.exe` was accessed immediately before the ntds.dit cleanup — VSS is the standard mechanism used (via `ntdsutil \"ac i ntds\" \"ifm\"` or similar) to create a shadow copy of the live AD database so it can be copied out while in use.\n\n**3. Massive, repeated LSASS process_access events**\n- Dozens of `Sysmon EventID=10 (process_access)` hits against `C:\\Windows\\system32\\lsass.exe` throughout the window (e.g., 12:52:14, 12:53:00, 12:53:26, 12:53:30, 12:54:04, 12:54:24, 12:54:30–32, 12:54:37, 12:54:39, 12:54:43, 12:55:51). The sheer volume and repetition (dozens of accesses per second at times) is atypical of normal system behavior and consistent with credential-harvesting tooling repeatedly querying LSASS memory.\n- Multiple `dns_query` events sourced from `lsass.exe` (12:53:30, 12:53:34) are anomalous — LSASS does not normally initiate DNS lookups, suggesting injected/hooked code.\n\n**4. Supporting attacker tradecraft in the same window**\n- Heavy use of **encoded PowerShell** (`-EncodedCommand`, `-NoProfile -NonInteractive -ExecutionPolicy Unrestricted`) launched via `WinrsHost.exe` (WinRM) — indicating remote execution.\n- On-the-fly C# compilation (`csc.exe` / `cvtres.exe`) — a common technique for loading custom .NET-based credential-dumping or in-memory tooling to evade signature-based AV.\n- Recon commands: `whoami.exe`, `HOSTNAME.EXE`.\n- References to `AtomicRedTeam` in a decoded command fragment — suggesting this may be an **atomic test/attack simulation** (this host is `attackrange.local`, consistent with a purple-team/detection-lab environment, e.g., Splunk Attack Range).\n\n### Summary Timeline (win-dc-8537412.attackrange.local)\n| Time (UTC) | Activity |\n|---|---|\n| 12:52:14 | Encoded PowerShell execution, LSASS access, C# compilation |\n| 12:53:00–12:53:34 | Repeated LSASS access + anomalous LSASS DNS queries; WinRM remote shell activity |\n| 12:54:24–12:54:32 | More encoded PowerShell, `vssvc.exe` access (shadow copy) |\n| 12:54:37–12:54:39 | LSASS access, `whoami`/`hostname` recon, **deletion of `ntds.dit`, SYSTEM hive, and ntds_logs from `C:\\Windows\\Temp`** |\n| 12:55:xx | Continued svchost/lsass access, Splunk forwarder activity (likely monitoring, not attacker) |\n\n### Assessment\nThis is a high-confidence detection of **NTDS.dit extraction followed by anti-forensic cleanup** — the attacker (or emulated adversary) likely used a VSS-based method to copy the AD database and SYSTEM hive to `C:\\Windows\\Temp`, then deleted the artifacts after presumably exfiltrating or processing them elsewhere. Combined with repeated LSASS touches, this indicates both **NTDS-based** and potentially **LSASS-memory-based** credential harvesting occurred on this domain controller.\n\n**Recommendation:**\n- Treat all domain credentials as potentially compromised — plan for a domain-wide password/krbtgt reset (twice, per Microsoft guidance).\n- Hunt for the process that created `C:\\Windows\\Temp\\ntds.dit` (search slightly before 12:54:39 for `ntdsutil.exe`, `esentutl.exe`, `vssadmin.exe`, or PowerShell IFM/DSInternals cmdlets — not captured in the retrieved windows).\n- Check for outbound file transfers from the DC around 12:53–12:55 UTC to identify exfiltration destination.\n- Verify whether this is expected Atomic Red Team / Attack Range testing activity or an actual incident, given the `attackrange.local` domain suffix.",
  "query": "Did you detect any evidence of credential dumping or Active Directory database extraction? If so, summarize the activity?",
  "tool_used": "semantic_search",
  "chunks_retrieved": 20,
  "chunks_used": 20,
  "neighbours_added": 0,
  "hyde_used": true,
  "hypothetical_doc": "Host: DC01-CORPAD | Window: 02:14:05–02:16:47 UTC\n[02:14:05] Sysmon E",
  "latency_ms": 39569,
  "input_tokens": 109331,
  "output_tokens": 2154,
  "sources": [
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:55:00+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:55:00+00:00", "window_end": "2020-10-08T12:55:05+00:00", "score": 0.9101, "event_count": 2},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:55:50+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:55:50+00:00", "window_end": "2020-10-08T12:55:55+00:00", "score": 0.9085, "event_count": 4},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:55:02+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:55:02+00:00", "window_end": "2020-10-08T12:55:07+00:00", "score": 0.9082, "event_count": 2},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:57:20+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:57:20+00:00", "window_end": "2020-10-08T12:57:25+00:00", "score": 0.908, "event_count": 1},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:55:48+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:55:48+00:00", "window_end": "2020-10-08T12:55:53+00:00", "score": 0.9079, "event_count": 4},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:57:18+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:57:18+00:00", "window_end": "2020-10-08T12:57:23+00:00", "score": 0.9061, "event_count": 1},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:53:24+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:53:24+00:00", "window_end": "2020-10-08T12:53:29+00:00", "score": 0.9051, "event_count": 3},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:53:22+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:53:22+00:00", "window_end": "2020-10-08T12:53:27+00:00", "score": 0.905, "event_count": 3},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:52:18+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:52:18+00:00", "window_end": "2020-10-08T12:52:23+00:00", "score": 0.9019, "event_count": 4},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:54:40+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:54:40+00:00", "window_end": "2020-10-08T12:54:45+00:00", "score": 0.9018, "event_count": 2},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:54:58+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:54:58+00:00", "window_end": "2020-10-08T12:55:03+00:00", "score": 0.9018, "event_count": 1},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:54:42+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:54:42+00:00", "window_end": "2020-10-08T12:54:47+00:00", "score": 0.9001, "event_count": 2},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:55:10+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:55:10+00:00", "window_end": "2020-10-08T12:55:15+00:00", "score": 0.8928, "event_count": 59},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:55:12+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:55:12+00:00", "window_end": "2020-10-08T12:55:17+00:00", "score": 0.8924, "event_count": 87},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:54:34+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:54:34+00:00", "window_end": "2020-10-08T12:54:39+00:00", "score": 0.8914, "event_count": 89},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:53:26+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:53:26+00:00", "window_end": "2020-10-08T12:53:31+00:00", "score": 0.8883, "event_count": 79},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:52:10+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:52:10+00:00", "window_end": "2020-10-08T12:52:15+00:00", "score": 0.8865, "event_count": 63},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:53:28+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:53:28+00:00", "window_end": "2020-10-08T12:53:33+00:00", "score": 0.885, "event_count": 76},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:53:30+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:53:30+00:00", "window_end": "2020-10-08T12:53:35+00:00", "score": 0.8849, "event_count": 84},
    {"window_id": "splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:54:16+00:00", "host": "win-dc-8537412.attackrange.local", "window_start": "2020-10-08T12:54:16+00:00", "window_end": "2020-10-08T12:54:21+00:00", "score": 0.8827, "event_count": 73}
  ]
}
```

</details>

## 5. Retrieval and anomaly-detection configuration comparison

These examples compare different retrieval and anomaly-detection configurations.

### 5.1 High Isolation threshold with HDBSCAN disabled

Query:

```text
Were there anomalies in the last 10 minutes?
```

Example response:

```json
{
  "answer": "Ran anomaly detection over the last 10 minutes of log data (112 windows analyzed, 3 flagged as outliers — ~2.7%, all as unclustered \"noise\"). All three findings are on the same host:\n\n**Host: win-dc-8537412.attackrange.local**\n\n- **Anomaly 1 (12:52:44–12:52:49 UTC, if_score=1.000 — highest confidence outlier)**\n  A dense burst (78 events) of Sysmon `driver_loaded` (EventID 6) and `process_create` (EventID 1) events tied to core Windows boot processes: `autochk.exe`, `smss.exe`, `csrss.exe`, `wininit.exe`, `winlogon.exe`, plus registry writes to the `System` hive.\n\n- **Anomaly 2 (12:52:42–12:52:47 UTC, if_score=0.838)**\n  A tight cluster of 4 `driver_loaded` events, overlapping the same timeframe as Anomaly 1.\n\n- **Anomaly 3 (12:56:58–12:57:03 UTC, if_score=0.816)**\n  38 events of repeated `registry_value_set` / `registry_object_create_delete` (EventID 13/12) all referencing `WMIADAP.EXE` under `C:\\Windows\\system32\\wbem\\`.\n\n**Assessment:**\nThese patterns strongly resemble a **system boot/restart sequence** — the process chain (`smss → csrss → wininit → winlogon`) and driver-loading burst are consistent with a Windows startup, and `WMIADAP.EXE` registry churn is typical of the built-in WMI Performance Adapter maintenance task that runs periodically. They were flagged as anomalous primarily due to **unusually high event volume/density in a short window** (78 events in 5 seconds) rather than by matching a known attack signature — hence they landed as HDBSCAN \"noise\" rather than in a normal-behavior cluster.\n\n**Recommendation:**\n- Confirm whether win-dc-8537412 underwent a scheduled reboot or patching cycle around 12:52–12:57 UTC. If so, this is very likely benign.\n- If no reboot/maintenance was scheduled, treat the driver-load burst as worth a closer look — verify the loaded driver names/hashes for anything unsigned or unexpected, since malware persistence and rootkits can also trigger dense `driver_loaded` bursts.\n- No indicators of lateral movement, credential access, or suspicious command lines were present in these windows.\n\nLet me know if you'd like me to pull the full event details (driver names, hashes) for the 12:52:44 window to rule out malicious driver loading.",
  "query": "Were there anomalies in the last 10 minutes?",
  "tool_used": "detect_anomalies",
  "chunks_retrieved": 0,
  "chunks_used": 0,
  "neighbours_added": 0,
  "hyde_used": false,
  "hypothetical_doc": null,
  "latency_ms": 44845,
  "input_tokens": 9569,
  "output_tokens": 1141,
  "sources": []
}
```

### 5.3 Lower Isolation threshold with HDBSCAN enabled

The same query was evaluated with a lower Isolation Forest threshold and HDBSCAN enabled.

Example response:

```json
{
  "answer": "Yes — anomaly detection flagged **8 outlier windows (7.1% of all activity)** in the last 10 minutes, and they are unusual enough that HDBSCAN couldn't assign any of them to a normal-behavior cluster (100% \"noise\"). All 8 come from a single host: **win-dc-8537412.attackrange.local**, between **12:52–12:57 UTC**. The most concerning cluster is a PowerShell process touching LSASS combined with network beaconing.\n\n### 🔴 Most suspicious: PowerShell + LSASS access + beaconing (12:54:02–12:54:13 UTC)\n- `powershell.exe` performed a `process_access` (EventID=10) against `C:\\Windows\\system32\\lsass.exe` — this is the classic signature of **credential dumping** (e.g., Mimikatz-style memory reads of LSASS to harvest credentials).\n- The same PowerShell process simultaneously generated a burst of:\n  - `dns_query` (EventID=22) events\n  - `network_connection` (EventID=3) events\n  - `file_create` (EventID=11) events — dozens of file creations under the PowerShell binary path\n- This pattern (LSASS access + DNS/network activity + file drops, all from PowerShell) is a strong indicator of **credential theft followed by C2 communication or payload staging**.\n- **This is the highest-priority item to investigate.**\n\n### 🟠 System boot / driver load storm (12:52:44–12:52:49 UTC, if_score=1.000 — top outlier)\n- A dense burst of `driver_loaded` (EventID=6) events, registry writes to `System`, and the classic Windows boot process chain: `autochk.exe` → `smss.exe` → `csrss.exe` → `wininit.exe` → `winlogon.exe`.\n- This looks like a **system reboot/restart event** on the DC. While boot sequences are normal, an unscheduled reboot of a domain controller right before credential-access activity is worth correlating — it could indicate a deliberate restart to load a malicious driver, clear state, or as part of an attack chain.\n\n### 🟡 WMIADAP.EXE registry churn (12:56:58–12:57:09 UTC, multiple overlapping windows)\n- Very high volume of `registry_value_set` (EventID=13) and `registry_object_create_delete` (EventID=12) events, all tied to `WMIADAP.EXE` (WMI Performance Adapter).\n- This is normally a benign periodic WMI class-refresh process, but the sheer repetition/volume flagged it as anomalous. Could be legitimate maintenance, but worth confirming timing wasn't triggered by attacker-driven WMI activity (e.g., WMI persistence or lateral movement staging).\n\n### Summary / Recommendation\n- **Priority 1:** Investigate the PowerShell process (PID/command line) that accessed `lsass.exe` at 12:54 UTC on `win-dc-8537412.attackrange.local`. Pull the full command line, parent process, and destination IPs/domains from the DNS/network events to determine if this is credential dumping + exfiltration/C2.\n- **Priority 2:** Correlate the 12:52 reboot with any scheduled maintenance windows — if unscheduled, treat as suspicious given the subsequent LSASS access.\n- **Priority 3:** Confirm WMIADAP.EXE activity at 12:57 is routine (it commonly runs periodically) and not an artifact of WMI-based attacker activity.\n\nWould you like me to pull the full process tree / command-line details for the PowerShell process at 12:54 UTC to identify the parent process and any encoded arguments?",
  "query": "Were there anomalies in the last 10 minutes?",
  "tool_used": "detect_anomalies",
  "chunks_retrieved": 0,
  "chunks_used": 0,
  "neighbours_added": 0,
  "hyde_used": false,
  "hypothetical_doc": null,
  "latency_ms": 27882,
  "input_tokens": 20820,
  "output_tokens": 1733,
  "sources": []
}
```
