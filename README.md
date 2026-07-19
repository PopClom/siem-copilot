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
```
