# SIEM Copilot

> Master's thesis project in cybersecurity: an AI-assisted system for semantic analysis of security logs.

## Overview

SIEM Copilot is an intelligent security-log analysis system designed to help security analysts investigate large volumes of events using natural-language queries.

The project integrates **Retrieval-Augmented Generation (RAG)** with security-log processing and unsupervised anomaly detection. Logs are processed, normalized, aggregated into time windows, transformed into embeddings using local models, and stored in a vector database. A language model (LLM) interprets natural-language questions, coordinates semantic retrieval, and generates the final response.

The system is designed as an independent microservice that can interact with a SIEM without modifying its core.

### Key capabilities

- Natural-language querying over security logs.
- RAG-based semantic retrieval.
- Local embedding generation.
- Structured filtering combined with vector search.
- LLM-based query interpretation and response generation.
- Unsupervised anomaly detection using **Isolation Forest** and **HDBSCAN**.
- Few-shot prompting / skill tuning for query interpretation.
- Configurable models and system behavior through YAML.
- REST API for integration with other services.

## Project goals

### General objectives

- Design and implement an intelligent natural-language query system for security logs.
- Integrate AI techniques such as embeddings, LLMs, and anomaly detection into security-event analysis.
- Improve incident-investigation efficiency through semantic analysis.
- Evaluate the feasibility of integrating these capabilities into real-world SIEM platforms.

### Specific objectives

- Design a log-processing pipeline including normalization and aggregation by time windows and entities such as hosts and users.
- Generate embeddings using local models optimized for short text.
- Implement vector-based storage and retrieval combined with structured filters.
- Integrate an LLM to transform natural-language questions into semantic and structured queries.
- Implement unsupervised anomaly detection using Isolation Forest and HDBSCAN.
- Provide YAML-based configuration for models and system behavior.
- Incorporate few-shot prompting or skill tuning to improve LLM query interpretation.
- Evaluate the system in terms of accuracy, usefulness, and efficiency in security-analysis scenarios.

## Technology stack

| Component | Technology |
| --- | --- |
| Language | Python |
| Embeddings | Local BGE models |
| LLM | Claude |
| Vector database | Qdrant |
| Anomaly detection | scikit-learn / Isolation Forest |
| Clustering | HDBSCAN |
| API | FastAPI / REST |
| Configuration | YAML / Pydantic v2 |

## Project structure

```text
siem-copilot/
├── config/
│   └── config.yaml
├── main.py                         # CLI entry point
├── requirements.txt
├── src/
│   ├── models.py                   # Shared dataclasses
│   ├── pipeline.py                 # Main pipeline orchestrator
│   ├── config/
│   │   └── settings.py             # YAML validation with Pydantic v2
│   ├── ingestion/
│   │   ├── parsers/
│   │   │   ├── registry.py         # Parser registry
│   │   │   └── sysmon.py           # Sysmon parser
│   │   └── reader.py               # Files -> RawEvents
│   ├── normalization/
│   │   └── normalizer.py           # Noise removal and descriptions
│   ├── windowing/
│   │   └── windower.py             # Overlapping time windows
│   ├── embedding/
│   │   └── embedder.py             # Sentence-transformers, lazy loading, batching
│   ├── vectordb/
│   │   └── qdrant_store.py         # Qdrant collection, upsert, search
│   ├── rag/
│   │   ├── retriever.py            # Query embedding and Qdrant retrieval
│   │   ├── prompt.py               # Few-shot prompt construction
│   │   ├── llm.py                  # Anthropic client
│   │   └── chain.py                # Retriever -> prompt -> LLM
│   └── anomaly/
│       ├── detector.py             # Isolation Forest + HDBSCAN
│       └── reporter.py             # Summary for the LLM
└── api/
    ├── main.py                     # FastAPI application
    ├── routers/
    │   ├── query.py                # POST /query
    │   ├── anomalies.py            # GET /anomalies
    │   └── health.py               # GET /health
    ├── schemas.py                  # Pydantic request/response models
    └── static/
        └── chat.html               # Chat frontend
```

## Getting started

### 1. Create a virtual environment

**Linux / macOS**

```bash
python3 -m venv venv
source venv/bin/activate
```

**Windows PowerShell**

```powershell
python3 -m venv venv
.\venv\Scripts\Activate.ps1
```

If PowerShell blocks script execution, run:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### 2. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Run the CLI

Start the main pipeline with:

```bash
python main.py
```

To drop and recreate the vector-database collection and re-ingest the data:

```bash
python main.py --reingest
```

To run anomaly detection:

```bash
python main.py --detect-anomalies
```

For anomaly detection without generating the LLM summary:

```bash
python main.py --detect-anomalies --since 24h --no-llm-summary
```

### 4. Run the API

Start the FastAPI application with:

```bash
uvicorn api.main:app --reload --port 8000
```

For verbose diagnostics:

```bash
LOG_LEVEL=DEBUG uvicorn api.main:app --reload --port 8000
```

On Windows Command Prompt:

```cmd
set LOG_LEVEL=DEBUG
uvicorn api.main:app --reload --port 8000
```

The API exposes the following endpoints:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/query` | Submit a natural-language security query |
| `GET` | `/anomalies` | Retrieve anomaly information |
| `GET` | `/health` | Check service health |

## Examples

The repository contains several potentially long examples, so the full examples are kept separately to keep this README focused on setup and project documentation.

See **[Examples](eval/examples.md)** for:

- Anomaly-detection output.
- LLM-generated anomaly summaries.
- RAG queries through the API.
- Queries with and without HyDE.
- Example API responses.

A minimal API request looks like this:

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"Were there any lateral movement indicators?"}'
```

## Architecture

At a high level, the system follows this flow:

```text
Security logs
     │
     ▼
Ingestion
     │
     ▼
Normalization
     │
     ▼
Windowing / aggregation
     │
     ▼
Embedding generation
     │
     ├──────────────► Anomaly detection
     │
     ▼
Qdrant vector database
     │
     ▼
Natural-language query
     │
     ▼
Retriever + structured filters
     │
     ▼
Few-shot prompt / LLM
     │
     ▼
Security analysis response
```

## Configuration

System behavior is configured through YAML, with settings validated using Pydantic v2.

The main configuration file is:

```text
config/config.yaml
```

Model selection and other runtime behavior should be adjusted there according to the current project configuration.

## Development notes

This repository contains the implementation developed as part of a Master's thesis in cybersecurity. The project is intended to evaluate the practical usefulness of combining semantic retrieval, LLMs, and unsupervised anomaly detection for security-log investigation.

For reproducible experiments and detailed outputs, see **[eval/examples.md](eval/examples.md)**.
