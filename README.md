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
```

## Ejecutar API

```bash
uvicorn api.main:app --reload --port 8000

curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Were there any lateral movement indicators?"}'
```
