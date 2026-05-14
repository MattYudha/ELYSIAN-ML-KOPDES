# Elysian Swarm Worker

Komponen Python ringan yang menjalankan **Multi-Agent Swarm Intelligence** untuk mendeteksi markup anggaran pada dokumen RAPBD Pemerintah Daerah.

Merupakan bagian dari ekosistem **Elysian Rebirth** — terinspirasi dari arsitektur multi-agent MiroFish.

---

## Arsitektur

```
Frontend (Next.js)
    │  Klik "Swarm Review"
    ▼
Go Backend (Gin)
    │  LPUSH → swarm:tasks
    ▼
Redis Queue
    │  BRPOP
    ▼
swarm_worker.py  ◄─── THIS COMPONENT
    │  3 Agent berdebat (Auditor → Compliance → Manager)
    │  HTTP POST Webhook
    ▼
Go Backend (Gin) → Redis Pub/Sub → SSE → Frontend
```

## Tiga Agent

| Agent          | Peran                                                         |
| -------------- | ------------------------------------------------------------- |
| **Auditor**    | Membandingkan harga item dengan Standar Harga Regional (POJK) |
| **Compliance** | Mengevaluasi justifikasi legal / pengecualian regulasi        |
| **Manager**    | Memutuskan `FLAGGED` atau `CLEARED` dalam format JSON         |

---

## Cara Menjalankan

### 1. Setup environment

```bash
cd swarm-worker/
cp .env.example .env
# Edit .env dan isi LLM_API_KEY, REDIS_HOST, dll.
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
# atau dengan uv:
uv pip install -r requirements.txt
```

### 3. Jalankan worker

```bash
python swarm_worker.py
```

Worker akan menunggu task dari Redis queue `swarm:tasks` secara blocking.

---

## Environment Variables

| Variable         | Default                     | Keterangan                 |
| ---------------- | --------------------------- | -------------------------- |
| `REDIS_HOST`     | `localhost`                 | Host Redis                 |
| `REDIS_PORT`     | `6379`                      | Port Redis                 |
| `REDIS_PASSWORD` | _(kosong)_                  | Password Redis (opsional)  |
| `LLM_API_KEY`    | _(wajib diisi)_             | API Key LLM                |
| `LLM_BASE_URL`   | `https://api.openai.com/v1` | Endpoint OpenAI-compatible |
| `LLM_MODEL_NAME` | `gpt-4o-mini`               | Model yang digunakan       |

---

## Referensi Standar Harga

File `DUMMY_POJK_Standar_Harga.md` berisi data harga referensi yang otomatis di-inject ke dalam prompt Agent Auditor. Untuk produksi, ganti dengan data e-Katalog resmi.
