# 🤖 Elysian Rebirth — ML / MiroFish

> **Cognitive Swarm Engine untuk Infrastruktur Audit Finansial Otonom**

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/>
  <img src="https://img.shields.io/badge/Flask-2.0+-000000?style=for-the-badge&logo=flask&logoColor=white" alt="Flask"/>
  <img src="https://img.shields.io/badge/OpenAI-Compatible-412991?style=for-the-badge&logo=openai&logoColor=white" alt="OpenAI"/>
  <img src="https://img.shields.io/badge/Redis-Queue-DC382D?style=for-the-badge&logo=redis&logoColor=white" alt="Redis"/>
  <img src="https://img.shields.io/badge/Swarm-Intelligence-FF6B6B?style=for-the-badge" alt="Swarm"/>
</p>

---

## 🎯 Apa itu MiroFish?

**MiroFish** adalah mesin simulasi Multi-Agent (Auditor, Compliance, Manager) yang saling berdebat untuk mencapai konsensus audit. Bagian dari ekosistem **Elysian Rebirth**.

**Fungsi Utama:**
- 🕵️ **Auditor Agent** — Analisis harga vs Standar Harga Regional
- ⚖️ **Compliance Agent** — Review legalitas vs Perda/POJK
- 👔 **Manager Agent** — Konsensus akhir (FLAGGED / CLEARED)
- 🔗 **Hash Generation** — SHA-256 untuk blockchain audit trail

---

## 🏗️ Architecture v3.0

```
┌─────────────────────────────────────────────────────────────────┐
│  MiroFish — Cognitive Layer                                     │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │ Redis Queue  │───►│ Swarm Worker │───►│ Go Backend   │      │
│  │ · BRPOP      │    │ · Debate     │    │ · Callback   │      │
│  │   tasks      │    │ · Hash Gen   │    │ · Blockchain │      │
│  └──────────────┘    └──────┬───────┘    └──────────────┘      │
│                             │                                    │
│              ┌──────────────┼──────────────┐                   │
│              ▼              ▼              ▼                   │
│         ┌────────┐    ┌────────┐    ┌────────┐               │
│         │Auditor │◄──►│Compli- │◄──►│Manager │               │
│         │Agent   │    │ance    │    │Agent   │               │
│         └────────┘    └────────┘    └────────┘               │
│              │              │              │                   │
│              ▼              ▼              ▼                   │
│         ┌────────┐    ┌────────┐    ┌────────┐               │
│         │Nemesis │    │OpenVik-│    │Shared  │               │
│         │· SIRUP│    │ing     │    │Memory  │               │
│         │· Harga│    │· Perda │    │· Learn │               │
│         └────────┘    └────────┘    └────────┘               │
└─────────────────────────────────────────────────────────────────┘
```

### Swarm Debate Flow:
```
Redis BRPOP swarm:tasks
    ↓
Auditor Agent (Price Checking)
    ↓ Query Nemesis DB
    → Output: MARKUP / WAJAR
    
Compliance Agent (Legal Review)
    ↓ Query OpenViking RAG
    → Output: Justifikasi regulasi
    
Manager Agent (Consensus)
    ↓ Baca temuan Auditor + Compliance
    → Output: JSON {"status": "FLAGGED"|"CLEARED"}
    
Generate SHA-256 Hashes
    ↓
Callback ke Go Backend
    ↓
Go push ke Blockchain Sepolia
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| **Runtime** | Python 3.11+ |
| **Framework** | Flask (development) / FastAPI (production) |
| **LLM Client** | OpenAI-compatible API |
| **Redis** | redis-py (queue + pub/sub) |
| **Memory** | pi-memctx pattern (JSON format) |
| **Database** | SQLite (Nemesis SIRUP data) |

---

## 📁 Project Structure

```
ml/
├── backend/                    # MiroFish Flask API
│   ├── app/
│   │   ├── api/               # REST endpoints
│   │   │   ├── graph.py
│   │   │   ├── report.py
│   │   │   └── simulation.py
│   │   ├── models/            # Data models
│   │   ├── services/          # Business logic
│   │   │   ├── simulation_manager.py
│   │   │   ├── report_agent.py
│   │   │   └── zep_tools.py
│   │   └── utils/             # Utilities
│   │       ├── llm_client.py
│   │       └── logger.py
│   ├── scripts/               # Standalone scripts
│   │   └── swarm_worker.py    # 🔥 Redis queue worker
│   ├── pyproject.toml
│   └── requirements.txt
│
└── swarm-worker/              # 🔥 Swarm Engine (Production)
    ├── swarm_worker.py        # Main worker (BRPOP → Debate → Callback)
    ├── memory/                # Agent Memory Layer
    │   ├── agent_memory.py    # Per-agent memory
    │   ├── pack_manager.py    # Pack management
    │   └── packs/             # Memory packs
    │       ├── auditor/       # Auditor agent memory
    │       ├── compliance/    # Compliance agent memory
    │       └── manager/       # Manager agent memory
    ├── .env.example
    └── requirements.txt
```

---

## 🔥 Swarm Worker (swarm_worker.py)

### Core Functions:

| Function | Description |
|----------|-------------|
| `process_task(task_json)` | Main entry point |
| `run_debate(items, task_id)` | Orchestrate 3-agent debate |
| `generate_hashes(results, task_id)` | Create SHA-256 hashes |
| `send_callback(payload)` | POST to Go backend |

### Hash Generation:
```python
def generate_hashes(results, task_id):
    # rationale_hash: SHA-256 of debate logs
    rationale_text = "\n".join([f"{r['item_id']}:{r['status']}:{log_text}" ...])
    rationale_hash = "0x" + hashlib.sha256(rationale_text.encode()).hexdigest()[:40]
    
    # consensus_hash: SHA-256 of verdict summary
    consensus_text = f"{task_id}:{flagged_count}:{total_count}"
    consensus_hash = "0x" + hashlib.sha256(consensus_text.encode()).hexdigest()[:40]
    
    return rationale_hash, consensus_hash
```

---

## 🧠 Agent Memory Layer

**Pattern:** pi-memctx (JSON format, not Markdown per CEO directive)

### Note Types:
| Type | Folder | Purpose |
|------|--------|---------|
| context | `20-context/` | Shared context |
| actions | `40-actions/` | Action logs |
| decisions | `50-decisions/` | Verdict decisions |
| observations | `60-observations/` | Debate patterns |
| runbooks | `70-runbooks/` | Procedures |
| sessions | `80-sessions/` | Session logs |

### Cross-Agent SharedMemory:
```python
class SharedMemory:
    def get_all_context(self, query: str) -> str:
        # Aggregate memory from all 3 agents
        
    def learn_all(self, task_id, item, auditor_response, 
                  compliance_response, verdict, status):
        # All agents learn from debate
```

---

## 🚀 Quick Start

### Swarm Worker:
```bash
cd swarm-worker

# 1. Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Setup environment
cp .env.example .env
# Edit .env:
# REDIS_HOST=localhost
# REDIS_PORT=6379
# LLM_API_KEY=your_key
# LLM_BASE_URL=https://api.openai.com/v1
# NEMESIS_DB_HOST=localhost
# NEMESIS_DB_PORT=5432
# NEMESIS_DB_USER=elysian
# NEMESIS_DB_PASSWORD=elysian123
# NEMESIS_DB_NAME=nemesis_db

# 4. Run worker
python swarm_worker.py
```

### Nemesis Ground Truth DB Integration:
Swarm worker terintegrasi langsung dengan database `nemesis_db` (PostgreSQL) untuk mencari batas harga standard (`standard_price`) dan data statistik pengadaan historis (`procurement`). Fitur pencarian meliputi:
- **Case-Insensitive (`ILIKE`)**: Pencarian kata kunci mengabaikan perbedaan huruf kapital.
- **First-Word Fallback**: Jika kata kunci lengkap (misalnya `"Laptop IT"`) tidak memiliki data histori (0 sampel), query secara otomatis akan mencoba ulang dengan kata pertama saja (`"Laptop"`) untuk keandalan maksimal.


### MiroFish API:
```bash
cd backend

# 1. Install dependencies
pip install -r requirements.txt

# 2. Run Flask server
python run.py

# 3. API available at
http://localhost:5001
```

---

## 📡 Redis Communication

### Queue: `swarm:tasks`
- **Go:** `LPUSH swarm:tasks <json_payload>`
- **Python:** `BRPOP swarm:tasks 0`

### Pub/Sub: `swarm:events`
- **Python:** `PUBLISH swarm:events <results_json>`
- **Go:** `SUBSCRIBE swarm:events`

### Payload Format (Go → Python):
```json
{
  "task_id": "uuid",
  "document_id": "uuid",
  "document_type": "RAPBD",
  "webhook_url": "http://host.docker.internal:7777/api/v1/swarm/callback",
  "items": [...],
  "metadata": {"region": "Purbalingga", "year": 2026}
}
```

### Callback Format (Python → Go):
```json
{
  "task_id": "uuid",
  "status": "COMPLETED",
  "summary": "Ditemukan 2 indikasi markup",
  "hashes": {
    "rationale_hash": "0xabc...",
    "consensus_hash": "0xdef..."
  },
  "blockchain": {
    "tx_hash": null,
    "network": "sepolia",
    "status": "PENDING_COMMIT"
  },
  "results": [...]
}
```

---

## 🏛️ Elysian Ecosystem

| Repo | Role | Stack |
|------|------|-------|
| [Frontend](https://github.com/MattYudha/Frontend-Elysian-Rebirth) | Next.js 14 UI | TypeScript + Tailwind |
| [Backend](https://github.com/MattYudha/Backend-Elysian-) | Go API Server | Go + Gin + PostgreSQL |
| [ML](https://github.com/MattYudha/ML-ELYSIAN) | Python Swarm | Flask + OpenAI |
| [Trust Layer](https://github.com/MattYudha/Backend-Elysian-/tree/main/trust-layer) | Smart Contract | Solidity + Hardhat |

---

> **Versi:** 3.0.0 (Blockchain-Integrated)  
> **Tanggal:** Mei 2026  
> **Pemilik:** Matt (Team Elysian)
