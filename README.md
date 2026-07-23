# SIEM Copilot

## Descripción del proyecto

El presente trabajo propone el diseño e implementación de un sistema inteligente de análisis de logs de seguridad basado en técnicas de Inteligencia Artificial, integrado con el SIEM Wazuh.

El objetivo principal es permitir la realización de consultas en lenguaje natural sobre grandes volúmenes de logs, facilitando la labor de los analistas de seguridad. Para ello, se implementará un enfoque basado en Retrieval-Augmented Generation (RAG), donde los logs serán procesados, normalizados y transformados en embeddings mediante modelos locales, almacenados en una base de datos vectorial. A partir de consultas en lenguaje natural, un modelo de lenguaje (LLM) externo interpretará la intención del usuario, generará consultas semánticas y coordinará la recuperación de información relevante.

Adicionalmente, el sistema incorporará técnicas de detección de anomalías no supervisadas para identificar comportamientos sospechosos en los logs, complementando así la búsqueda semántica con análisis de seguridad avanzado.

La solución se diseñará como un microservicio independiente que interactúe con Wazuh, permitiendo su integración en entornos reales sin modificar el núcleo del SIEM.

## Objetivos generales

* Diseñar e implementar un sistema de consulta inteligente sobre logs de seguridad basado en lenguaje natural.
* Integrar técnicas de Inteligencia Artificial (embeddings, LLMs y detección de anomalías) en el análisis de eventos de seguridad.
* Mejorar la eficiencia en la investigación de incidentes mediante herramientas semánticas avanzadas.
* Evaluar la viabilidad de integrar este tipo de soluciones en plataformas SIEM reales.

## Objetivos específicos

* Diseñar un pipeline de procesamiento de logs que incluya normalización y agregación por ventanas temporales y entidades (host, usuario, etc.).
* Implementar generación de embeddings utilizando modelos locales optimizados para texto corto.
* Desarrollar un sistema de almacenamiento y recuperación basado en bases de datos vectoriales combinado con filtros estructurados.
* Integrar un LLM para transformar consultas en lenguaje natural en queries semánticas y estructuradas.
* Implementar mecanismos de detección de anomalías mediante algoritmos no supervisados como Isolation Forest y clustering (HDBSCAN).
* Diseñar un sistema configurable (por ejemplo, mediante YAML) que permita ajustar modelos y comportamiento del sistema.
* Incorporar técnicas de few-shot prompting o “skill tuning” para mejorar la interpretación de consultas por parte del LLM.
* Evaluar el rendimiento del sistema en términos de precisión, utilidad y eficiencia en escenarios de análisis de seguridad.

## Tecnologías

* SIEM: Wazuh
* Lenguaje de programación: Python
* Frameworks de IA: LangChain (opcional)
* Modelos de embeddings locales (ej. sentence-transformers, BGE)
* LLM externo (ej. Claude u otros equivalentes)
* Base de datos vectorial (ej. Qdrant o similar)
* Librerías de Machine Learning: scikit-learn (Isolation Forest), HDBSCAN
* APIs REST para comunicación entre servicios
* Configuración mediante archivos YAML

## Estructura del proyecto

```
siem-copilot/
├── config/config.yaml
├── main.py                          ← CLI con argparse
├── requirements.txt
├── src/
│   ├── models.py                    ← Dataclasses compartidos (RawEvent, NormalizedEvent, EventWindow)
│   ├── pipeline.py                  ← Orquestador general
│   ├── config/settings.py           ← Pydantic v2 para validar el YAML
│   ├── ingestion/
│   │   ├── parsers.py               ← Registro de parsers (dummy extensible)
│   │   └── reader.py                ← Lee archivos → yields RawEvents
│   ├── normalization/normalizer.py  ← Limpieza de ruido + builders de descripción
│   ├── windowing/windower.py        ← Ventanas temporales con solapamiento
│   ├── embedding/embedder.py        ← sentence-transformers, lazy load, batch
│   ├── vectordb/qdrant_store.py     ← Qdrant: colección, upsert, búsqueda
│   └── rag/
│       ├── retriever.py             ← embed query + búsqueda en Qdrant
│       ├── prompt.py                ← construcción del prompt con few-shot
│       ├── llm.py                   ← cliente Anthropic, streaming-ready
│       └── chain.py                 ← orquesta retriever → prompt → llm
└── api/
    ├── main.py                      ← app FastAPI, lifespan, routers
    ├── routers/
    │   ├── query.py                 ← POST /query
    │   └── health.py                ← GET /health
    └── schemas.py                   ← Pydantic request/response models
```

## Cómo ejecutar

```bash
python3 -m venv venv
.\venv\Scripts\Activate.ps1

# Si da error:
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

pip install -r requirements.txt
python main.py

# Drop and recreate collection
python main.py --reingest
```

## Ejecutar API

```bash
# Normal (solo INFO y superiores)
uvicorn api.main:app --reload --port 8000

# Con diagnóstico completo
LOG_LEVEL=DEBUG uvicorn api.main:app --reload --port 8000

# En Windows (cmd)
set LOG_LEVEL=DEBUG
uvicorn api.main:app --reload --port 8000

# Ejemplo de uso
curl -X POST http://localhost:8000/query -H "Content-Type: application/json" -d "{\"question\":\"Were there any lateral movement indicators?\"}"

{"answer":"Based on the provided log windows from **win-dc-8537412.attackrange.local (12:54:55–12:55:25 UTC)**, I do not see any indicators of lateral movement.\n\n**What the logs actually show:**\n- A large volume of Sysmon `process_access` (EventID=10) events involving `TiWorker.exe` (Windows Servicing Stack) and standard system processes (`svchost.exe`, `csrss.exe`, `conhost.exe`) — consistent with routine Windows Update/servicing activity.\n- A series of `process_create` (EventID=1) events for **SplunkUniversalForwarder** components (`splunk-admon.exe`, `splunk-netmon.exe`, `splunk-powershell.exe`, `splunk-regmon.exe`, `splunk-winprintmon.exe`, `btool.exe`, `splunk.exe restart`) — this is normal Splunk Universal Forwarder startup/restart behavior, including its modular input processes and `btool` configuration checks.\n- No network connections (e.g., SMB/445, WinRM/5985, RDP/3389), no `net.exe`/`net1.exe` usage, no remote logon events (Type 3/10), and no credential-dumping tools (e.g., `mimikatz`, `procdump`, `lsass` access) are present in this context.\n\n**Assessment:**\nThis activity pattern is consistent with **benign local host maintenance** — Windows servicing (TiWorker) and Splunk Forwarder restart/reconfiguration — rather than an attack. There are no cross-host connections, remote authentication events, or remote execution artifacts that would indicate lateral movement.\n\n**Caveat:** This context only covers a single host (`win-dc-8537412.attackrange.local`) over a ~30-second window. If lateral movement occurred, it would likely appear in network connection logs, authentication/logon events, or remote process creation on other hosts — none of which are present in this context. I cannot confirm or rule out lateral movement outside this window based on the data provided.","query":"Were there any lateral movement indicators?","chunks_retrieved":8,"chunks_used":2,"latency_ms":11979,"sources":[{"window_id":"splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:54:55+00:00","host":"win-dc-8537412.attackrange.local","window_start":"2020-10-08T12:54:55+00:00","window_end":"2020-10-08T12:55:05+00:00","score":0.6932,"event_count":2},{"window_id":"splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:55:15+00:00","host":"win-dc-8537412.attackrange.local","window_start":"2020-10-08T12:55:15+00:00","window_end":"2020-10-08T12:55:25+00:00","score":0.6859,"event_count":422}]}

curl -X POST http://localhost:8000/query -H "Content-Type: application/json" -d "{\"question\":\"Did you detect any evidence of credential dumping or Active Directory database extraction? If so, summarize the activity?\"}"

# Con HyDE
{"answer":"Based on the provided log windows, I did **not** find direct evidence of credential dumping tools (e.g., `mimikatz`, `procdump`, `ntdsutil.exe`, `vssadmin.exe`, `esentutl.exe`) or Active Directory database extraction (no references to `ntds.dit`, `SYSTEM` hive backup, or shadow-copy creation of the AD database).\n\nHowever, one item is worth flagging for further review:\n\n- **win-dc-8537412.attackrange.local, 12:55:07–12:55:08 UTC:** Multiple Sysmon **process_access (EventID=10)** events show `lsass.exe` being accessed several times in quick succession, interleaved with accesses to `services.exe`, `svchost.exe`, and `csrss.exe`.\n  - `lsass.exe` is the process that holds credential material in memory, so *any* access to it warrants scrutiny — this is the same technique used by tools like Mimikatz or `procdump.exe -ma lsass.exe` to extract credentials.\n  - **Critical caveat:** The log lines in this context only show the **target** image (`lsass.exe`), not the **source/calling process** or the requested access mask (e.g., `0x1010` / `PROCESS_VM_READ` would be highly suspicious, whereas standard service-level access like `0x1000` is normal). Since Sysmon EventID 10 always logs routine, benign LSASS accesses from `services.exe`, `svchost.exe`, and other legitimate system processes (this happens constantly on any Windows host), **this pattern alone is not sufficient to confirm malicious credential dumping.**\n\n- The `C:\\Windows\\System32\\msdtc.exe` process creation at 12:55:07 is the standard Distributed Transaction Coordinator service and is not inherently suspicious in this context.\n\n- The bulk of Window 2 (422 events) is dominated by high-volume, routine Splunk Universal Forwarder activity (`splunk-admon.exe`, `splunk-netmon.exe`, `splunk-powershell.exe`, `splunk-regmon.exe`, `splunk-winprintmon.exe`, `btool.exe`) and Windows servicing stack (`TiWorker.exe`) noise — none of which relates to credential access.\n\n**Assessment:** No confirmed evidence of credential dumping or NTDS.dit extraction. The repeated `lsass.exe` process_access events on the domain controller are a normal artifact of Sysmon EventID 10 logging but should be validated against the **source process name and GrantedAccess mask**, which are not visible in this excerpt.\n\n**Recommendation:** Pull the full EventID 10 records for the 12:55:07–12:55:08 window on `win-dc-8537412.attackrange.local` and inspect the `SourceImage` and `GrantedAccess` fields for any non-standard process (e.g., `procdump.exe`, `rundll32.exe`, `taskmgr.exe`, or unsigned binaries) requesting access to `lsass.exe` with read/memory-dump permissions (commonly `0x1410`, `0x1010`, or `0x1fffff`).","query":"Did you detect any evidence of credential dumping or Active Directory database extraction? If so, summarize the activity?","chunks_retrieved":8,"chunks_used":3,"latency_ms":44988,"hyde_used":false,"hypothetical_doc":null,"sources":[{"window_id":"splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:54:55+00:00","host":"win-dc-8537412.attackrange.local","window_start":"2020-10-08T12:54:55+00:00","window_end":"2020-10-08T12:55:05+00:00","score":0.7416,"event_count":2},{"window_id":"splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:55:15+00:00","host":"win-dc-8537412.attackrange.local","window_start":"2020-10-08T12:55:15+00:00","window_end":"2020-10-08T12:55:25+00:00","score":0.7395,"event_count":422},{"window_id":"splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:55:00+00:00","host":"win-dc-8537412.attackrange.local","window_start":"2020-10-08T12:55:00+00:00","window_end":"2020-10-08T12:55:10+00:00","score":0.7389,"event_count":60}]}

# Sin HyDE
{"answer":"## Yes — Strong Evidence of NTDS.dit Extraction (T1003.003)\n\n**Host:** win-dc-8537412.attackrange.local | **Time window:** 12:54:25–12:54:32 UTC\n\nThis activity shows a clear, multi-step Active Directory credential dumping attack via Volume Shadow Copy, consistent with the **Atomic Red Team T1003.003** technique (explicitly referenced in the logs).\n\n### Attack Sequence\n\n1. **Reconnaissance / Setup (12:54:25–26)**\n   - PowerShell launched with an encoded command that imports a module from `C:\\AtomicRedTeam\\invoke-atomicredteam\\Invoke-AtomicRedTeam...` — confirming this is an Atomic Red Team test execution.\n   - `whoami.exe` and `HOSTNAME.EXE` run to confirm host/user context.\n   - `reg query ... ProductOptions /v ProductType | findstr LanmanNT` executed twice — used to determine if the host is a Domain Controller (LanmanNT = DC check).\n\n2. **Precondition Checks (12:54:26)**\n   - `cmd.exe /c \"if not exist \\\\?\\GLOBALROOT\\Device\\HarddiskVolumeShadowCopy1 (exit /b 1)\"` — checking for existing shadow copies.\n   - Echo command referencing: *\"Run Invoke-AtomicTest T1003.003 -TestName 'Create Volume Shadow Copy with NTDS.dit'\"* — direct confirmation of the MITRE ATT&CK technique being simulated/executed.\n   - `if not exist C:\\Windows\\Temp (exit /b 1)` — staging directory check.\n\n3. **Shadow Copy Creation (12:54:29)**\n   - `vssadmin.exe create shadow /for=C:` — creates a Volume Shadow Copy of the C: drive, bypassing file locks on NTDS.dit.\n   - `VSSVC.exe` spawned to service the request; `spoolsv.exe` issued DNS queries around the same time (possibly incidental).\n\n4. **NTDS.dit and SYSTEM Hive Extraction (12:54:29)**\n   - Critical command:\n     ```\n     cmd.exe /c \"copy \\\\?\\GLOBALROOT\\Device\\HarddiskVolumeShadowCopy1\\Windows\\NTDS\\NTDS.dit C:\\Windows\\Temp\\ntds.dit\n     & copy \\\\?\\GLOBALROOT\\Device\\HarddiskVolumeShadowCopy1\\Windows\\System32\\config\\SYSTEM C:\\Windows\\Temp\\VSC_SYSTEM_HIVE\n     & reg save HKLM\\SYSTEM C:\\Windows\\Temp\\SYSTEM_HIVE\"\n     ```\n   - This copies the **NTDS.dit database** (containing all AD user password hashes) and the **SYSTEM registry hive** (needed to decrypt the boot key/hashes) out of the shadow copy to `C:\\Windows\\Temp\\`.\n   - `reg.exe save HKLM\\SYSTEM C:\\Windows\\Temp\\SYSTEM_HIVE` — redundant SYSTEM hive save via the registry API.\n\n5. **NTDSUtil IFM Extraction (12:54:29)**\n   - `cmd.exe /c \"mkdir C:\\Windows\\Temp\\ntds_T1003 & ntdsutil \"ac i ntds\" \"ifm\" \"create full C:\\Windows\\Temp\\ntds_T1003\" q q\"`\n   - This is the classic **`ntdsutil` \"Install From Media\" (IFM)** method — creates a full copy of the AD database plus registry hives specifically for offline credential extraction (e.g., with tools like `secretsdump.py` or `DSInternals`).\n\n6. **Mass LSASS Access (12:54:29–12:54:32)**\n   - An extremely high volume of `process_access` events targeting **`lsass.exe`** occurs continuously through the end of the window — dozens of handle opens per second, sustained for ~3 seconds. This is highly anomalous and consistent with either:\n     - Credential dumping tools reading LSASS memory (e.g., Mimikatz-style access), or\n     - `ntdsutil`/`vssvc` legitimately interacting with LSASS during the IFM snapshot process — but the sheer volume warrants scrutiny.\n\n7. **Remote Execution Indicators**\n   - `WinrsHost.exe -Embedding` process creation followed by an encoded `powershell.exe` command indicates this activity may have been triggered remotely via **WinRM** (Windows Remote Management), suggesting the attacker/tester had remote administrative access to the DC.\n\n### IOCs Observed\n- `C:\\Windows\\Temp\\ntds.dit`\n- `C:\\Windows\\Temp\\VSC_SYSTEM_HIVE`\n- `C:\\Windows\\Temp\\SYSTEM_HIVE`\n- `C:\\Windows\\Temp\\ntds_T1003\\` (ntdsutil IFM output directory)\n- Command-line references to `Invoke-AtomicTest T1003.003`\n\n### Assessment\nThis is a textbook **NTDS.dit dumping** operation using **two parallel methods** (manual VSS copy + `ntdsutil` IFM) — both designed to exfiltrate the entire Active Directory credential database from a Domain Controller. Combined with the `AtomicRedTeam` module reference, this strongly suggests either an authorized adversary-emulation/red-team exercise or an actual attacker leveraging the same technique.\n\n**Recommendation:**\n- Immediately verify whether this was an authorized red-team/Atomic Red Team test.\n- If not authorized, treat as critical incident: isolate the DC, rotate **all domain credentials** (especially krbtgt), and forensically image `C:\\Windows\\Temp\\ntds.dit`, `SYSTEM_HIVE`, and the `ntds_T1003` folder before remediation.\n- Review W","query":"Did you detect any evidence of credential dumping or Active Directory database extraction? If so, summarize the activity?","chunks_retrieved":8,"chunks_used":1,"latency_ms":36439,"hyde_used":true,"hypothetical_doc":"Host: DC01-CORP | Window: 03:14:02–03:16:47 UTC\n[03:14:02] Sysmon EID1 | ProcessCreate | Image=C:\\Windows\\System32\\cmd.exe | CommandLine=\"cmd.exe /c vssadmin create shadow /for=C:\" | User=CORP\\svc_backup | ParentImage=powershell.exe\n[03:14:09] Sysmon EID1 | ProcessCreate | Image=C:\\Windows\\System32\\vssadmin.exe | CommandLine=\"vssadmin create shadow /for=C:\" | User=CORP\\svc_backup\n[03:14:22] Windows-Security EID4688 | NewProcess=ntdsutil.exe | CommandLine=\"ntdsutil.exe \\\"ac i ntds\\\" \\\"ifm\\\" \\\"create full C:\\Temp\\ntdsdump\\\" q q\" | User=CORP\\svc_backup\n[03:14:58] Sysmon EID11 | FileCreate | TargetFilename=C:\\Temp\\ntdsdump\\Active Directory\\ntds.dit | Image=ntdsutil.exe\n[03:15:10] Sysmon EID11 | FileCreate | TargetFilename=C:\\Temp\\ntdsdump\\registry\\SYSTEM | Image=ntdsutil.exe\n[03:15:44] Sysmon EID3 | NetworkConnect | SourceIP=10.10.5.15 | DestIP=198.51.100.22 | DestPort=445 | Image=svchost.exe | User=CORP\\svc_backup\n[03:16:12] Sysmon EID11 | FileCreate | TargetFilename=C:\\Windows\\Temp\\ntds_exfil.7z | Image=7z.exe | User=CORP\\svc_backup\n[03:16:47] Windows-Security EID4663 | ObjectAccess | Object=C:\\Temp\\ntdsdump\\Active Directory\\ntds.dit | AccessMask=DELETE | User=CORP\\svc_backup","sources":[{"window_id":"splunk_attack_sysmon|win-dc-8537412.attackrange.local|any_user|2020-10-08T12:54:25+00:00","host":"win-dc-8537412.attackrange.local","window_start":"2020-10-08T12:54:25+00:00","window_end":"2020-10-08T12:54:35+00:00","score":0.9112,"event_count":906}]}
```
