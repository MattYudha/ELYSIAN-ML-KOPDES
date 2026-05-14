import os
import sys
import json
import time
import hashlib
import sqlite3
import requests
import redis
from openai import OpenAI
from dotenv import load_dotenv

# Load env from the same directory as this script
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

# ------------------------------------------------------------------------------
# Agent Memory Layer (pi-memctx pattern, JSON format)
# ------------------------------------------------------------------------------
try:
    from memory import AgentMemory, SharedMemory
    _MEMORY_ENABLED = True
except ImportError:
    _MEMORY_ENABLED = False
    print("[Init] WARNING: memory module not found. Running without agent memory.")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

REDIS_QUEUE = "swarm:tasks"

# Initialize OpenAI-compatible client
# Supports any OpenAI-compatible endpoint (e.g., OpenAI, Aliyun Qwen, etc.)
client = OpenAI(
    api_key=os.getenv("LLM_API_KEY", "dummy_key"),
    base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
)
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "gpt-4o-mini")

# Load Standar Harga Referensi (POJK) as context for the Auditor agent
_STANDAR_HARGA_PATH = os.path.join(os.path.dirname(__file__), "DUMMY_POJK_Standar_Harga.md")
STANDAR_HARGA_CONTEXT = ""
if os.path.exists(_STANDAR_HARGA_PATH):
    with open(_STANDAR_HARGA_PATH, "r", encoding="utf-8") as f:
        STANDAR_HARGA_CONTEXT = f.read()
    print(f"[Init] Loaded Standar Harga context ({len(STANDAR_HARGA_CONTEXT)} chars)")
else:
    print("[Init] WARNING: DUMMY_POJK_Standar_Harga.md not found. Auditor will use LLM base knowledge only.")

# ------------------------------------------------------------------------------
# Nemesis Ground Truth Database (SQLite)
# ------------------------------------------------------------------------------
_NEMESIS_DB_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "nemesis-groundtruth", "data", "nemesis.db"
)

_nemesis_conn = None


def _get_nemesis_conn():
    """Lazy-init SQLite connection to Nemesis DB."""
    global _nemesis_conn
    if _nemesis_conn is None:
        if os.path.exists(_NEMESIS_DB_PATH):
            _nemesis_conn = sqlite3.connect(_NEMESIS_DB_PATH, check_same_thread=False)
            _nemesis_conn.row_factory = sqlite3.Row
            print(f"[Init] Connected to Nemesis DB: {_NEMESIS_DB_PATH}")
        else:
            print(f"[Init] WARNING: Nemesis DB not found at {_NEMESIS_DB_PATH}")
    return _nemesis_conn


def query_nemesis_price(item_name: str, location: str = None) -> dict:
    """
    Query historical price data from Nemesis for a given item keyword.
    Returns avg/min/max prices and sample count.
    """
    conn = _get_nemesis_conn()
    if conn is None:
        return {}

    keyword = f"%{item_name}%"
    params = [keyword]

    sql = """
        SELECT
            COUNT(*) as sample_count,
            AVG(budget_amount) as avg_price,
            MIN(budget_amount) as min_price,
            MAX(budget_amount) as max_price,
            AVG(waste_potential_score) as avg_waste
        FROM procurement
        WHERE package_name LIKE ? OR work_description LIKE ?
    """
    params.append(keyword)

    if location:
        sql += " AND location LIKE ?"
        params.append(f"%{location}%")

    try:
        cursor = conn.execute(sql, params)
        row = cursor.fetchone()
        return {
            "sample_count": row["sample_count"] or 0,
            "avg_price": int(row["avg_price"] or 0),
            "min_price": int(row["min_price"] or 0),
            "max_price": int(row["max_price"] or 0),
            "avg_waste": round(row["avg_waste"] or 0, 2),
        }
    except Exception as e:
        print(f"[Nemesis Query Error] {e}")
        return {}


def query_standard_price(item_name: str) -> dict:
    """
    Query official standard price limit from POJK/SHSR.
    """
    conn = _get_nemesis_conn()
    if conn is None:
        return {}

    keyword = f"%{item_name}%"

    try:
        cursor = conn.execute("""
            SELECT item_name, item_category, max_price, min_specs
            FROM standard_price
            WHERE item_name LIKE ? OR item_category LIKE ?
            LIMIT 1
        """, [keyword, keyword])
        row = cursor.fetchone()
        if row:
            return {
                "item_name": row["item_name"],
                "category": row["item_category"],
                "max_price": row["max_price"],
                "min_specs": row["min_specs"],
            }
        return {}
    except Exception as e:
        print(f"[Standard Price Query Error] {e}")
        return {}


def run_agent(system_prompt: str, user_prompt: str) -> str:
    """Utility to run a single agent completion."""
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2,  # Low temperature for analytical tasks
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"[LLM Error] {e}")
        return f"Error connecting to LLM: {str(e)}"


def run_swarm_evaluation(item: dict, task_id: str = "") -> tuple[str, list, str]:
    """
    Executes the multi-agent swarm evaluation for a single budget item.

    Agents:
      1. Auditor   — Compares price against Standar Harga Regional
      2. Compliance — Evaluates legal justification / regulatory exceptions
      3. Manager   — Decides final FLAGGED | CLEARED verdict as JSON

    Memory: pi-memctx pattern — inject relevant context before reasoning,
            learn durable memory after debate.

    Returns: (status, agent_logs, manager_conclusion)
    """
    item_json = json.dumps(item, ensure_ascii=False)
    item_name = item.get('name', '')
    item_location = item.get('metadata', {}).get('region', '') if item.get('metadata') else ''

    # ------------------------------------------------------------------
    # 1. Agent Analis (Auditor) — with Nemesis + Memory Context
    # ------------------------------------------------------------------
    item_price = item.get('unit_price', 0)

    # Query Nemesis DB for historical prices
    nemesis_data = query_nemesis_price(item_name, item_location)
    standard_data = query_standard_price(item_name)

    # Query Agent Memory (pi-memctx pattern: inject before reasoning)
    memory_context = ""
    if _MEMORY_ENABLED:
        auditor_mem = AgentMemory("auditor")
        memory_context = auditor_mem.get_system_prompt_context(item_name, item_location)
        if memory_context:
            print(f"      Memory: {auditor_mem.status()['total_notes']} notes loaded")

    nemesis_section = ""
    if nemesis_data.get("sample_count", 0) > 0:
        nemesis_section = f"""

Data historis pengadaan serupa dari Nemesis (SIRUP):
- Jumlah sampel: {nemesis_data['sample_count']} transaksi
- Rata-rata harga: Rp {nemesis_data['avg_price']:,.0f}
- Harga terendah: Rp {nemesis_data['min_price']:,.0f}
- Harga tertinggi: Rp {nemesis_data['max_price']:,.0f}
- Rata-rata potensi pemborosan: {nemesis_data['avg_waste']}
"""

    standard_section = ""
    if standard_data:
        standard_section = f"""

Standar Harga Resmi (POJK/SHSR):
- Item: {standard_data['item_name']}
- Kategori: {standard_data['category']}
- Batas Maksimal: Rp {standard_data['max_price']:,.0f}
- Spesifikasi Minimum: {standard_data['min_specs']}
"""

    standar_harga_section = (
        f"\n\nBerikut adalah Standar Harga Regional yang menjadi acuanmu:\n{STANDAR_HARGA_CONTEXT}"
        if STANDAR_HARGA_CONTEXT else ""
    )

    auditor_system = f"""
Kamu adalah Agen Analis (Auditor Anggaran) di Pemerintah Daerah.
Tugasmu adalah membandingkan item anggaran yang diajukan dengan data historis pengadaan (Nemesis) dan Standar Harga Regional.

Aturan:
1. Jika harga diajukan > batas standar harga resmi (POJK), nyatakan MARKUP beserta persentase dan selisih.
2. Jika harga diajukan > rata-rata historis Nemesis lebih dari 30%, nyatakan MARKUP.
3. Jika ada justifikasi teknis yang kuat (spesifikasi di atas minimum), nyatakan WAJAR dengan catatan.
4. Selalu sebutkan angka perbandingannya (harga diajukan vs standar vs historis).
5. Jawab singkat (maksimal 3 kalimat).{standar_harga_section}
{memory_context}
"""

    auditor_prompt = f"""Evaluasi item ini:
{item_json}
{nemesis_section}{standard_section}
"""

    print(f"  -> [Auditor] Evaluating: {item_name}")
    if nemesis_data:
        print(f"      Nemesis: {nemesis_data['sample_count']} samples, avg=Rp {nemesis_data['avg_price']:,.0f}")
    if standard_data:
        print(f"      Standard: max=Rp {standard_data['max_price']:,.0f}")

    auditor_response = run_agent(auditor_system, auditor_prompt)

    # ------------------------------------------------------------------
    # 2. Agent Pengawas (Compliance)
    # ------------------------------------------------------------------
    compliance_system = """
    Kamu adalah Agen Pengawas (Compliance/Legal) di Pemerintah Daerah.
    Tugasmu adalah meninjau temuan dari Agen Analis dan mengevaluasi apakah
    ada justifikasi atau pasal regulasi yang mengizinkan pengecualian harga
    (misalnya spesifikasi khusus untuk departemen tertentu).
    Jawab secara legal dan kepatuhan (maksimal 3 kalimat).
    """

    compliance_user = (
        f"Item yang diajukan:\n{item_json}\n\n"
        f"Temuan Auditor:\n{auditor_response}\n\n"
        f"Berikan evaluasi kepatuhanmu:"
    )
    print(f"  -> [Compliance] Reviewing: {item.get('name')}")
    compliance_response = run_agent(compliance_system, compliance_user)

    # ------------------------------------------------------------------
    # 3. Agent Manajer (Decision Maker)
    # ------------------------------------------------------------------
    manager_system = """
    Kamu adalah Agen Manajer (Kepala Review).
    Tugasmu adalah membaca temuan Auditor dan Pengawas, lalu memberikan kesimpulan akhir.
    Kamu HARUS merespon hanya dalam format JSON valid berikut:
    {
        "status": "FLAGGED" | "CLEARED",
        "manager_conclusion": "Kesimpulan singkat maksimal 2 kalimat"
    }
    Gunakan "FLAGGED" jika ada indikasi kuat markup yang tidak memiliki justifikasi legal.
    Gunakan "CLEARED" jika harga wajar atau ada justifikasi legal yang kuat.
    """

    manager_user = (
        f"Item:\n{item_json}\n\n"
        f"Auditor:\n{auditor_response}\n\n"
        f"Pengawas:\n{compliance_response}\n\n"
        f"Berikan kesimpulan JSON:"
    )
    print(f"  -> [Manager] Concluding: {item.get('name')}")
    manager_response_raw = run_agent(manager_system, manager_user)

    # Parse manager JSON — with graceful fallback
    status = "CLEARED"
    manager_conclusion = "Tidak dapat mem-parsing kesimpulan Manajer."
    try:
        clean_json = manager_response_raw.replace("```json", "").replace("```", "").strip()
        manager_data = json.loads(clean_json)
        status = manager_data.get("status", "CLEARED")
        manager_conclusion = manager_data.get("manager_conclusion", manager_response_raw)
    except Exception as e:
        print(f"[JSON Error] Failed to parse manager response: {e}")
        status = "FLAGGED" if "FLAGGED" in manager_response_raw.upper() else "CLEARED"
        manager_conclusion = manager_response_raw

    agent_logs = [
        {"agent": "Auditor", "action": "Price Checking", "message": auditor_response},
        {"agent": "Pengawas", "action": "Compliance Review", "message": compliance_response},
    ]

    # ------------------------------------------------------------------
    # 4. Memory Learning (pi-memctx pattern: learn after turn)
    # ------------------------------------------------------------------
    if _MEMORY_ENABLED and task_id:
        shared_mem = SharedMemory()
        learned = shared_mem.learn_all(
            task_id=task_id,
            item_name=item_name,
            auditor_result=auditor_response,
            compliance_result=compliance_response,
            manager_verdict=manager_conclusion,
            status=status,
        )
        total_notes = sum(len(v) for v in learned.values())
        print(f"      Memory: learned {total_notes} notes across agents")

    return status, agent_logs, manager_conclusion


def process_task(task_data: dict) -> None:
    """Process the swarm task by orchestrating the agents."""
    task_id = task_data.get("task_id")
    webhook_url = task_data.get("webhook_url")
    items = task_data.get("items", [])

    print(f"\n[Worker] Processing task {task_id} with {len(items)} items")

    results = []
    markup_count = 0

    for item in items:
        status, agent_logs, manager_conclusion = run_swarm_evaluation(item, task_id)

        if status == "FLAGGED":
            markup_count += 1

        results.append({
            "item_id": item.get("item_id"),
            "status": status,
            "agent_logs": agent_logs,
            "manager_conclusion": manager_conclusion,
        })

    summary = (
        f"Swarm Review Selesai. "
        f"Ditemukan {markup_count} indikasi markup dari total {len(items)} item."
    )

    # ------------------------------------------------------------------
    # Generate Hashes for Blockchain Audit Trail
    # ------------------------------------------------------------------
    rationale_text = "\n".join([
        f"{r['item_id']}:{r['status']}:{r['manager_conclusion']}" 
        for r in results
    ])
    rationale_hash = "0x" + hashlib.sha256(rationale_text.encode()).hexdigest()[:40]
    
    consensus_text = f"{task_id}:{markup_count}:{len(items)}"
    consensus_hash = "0x" + hashlib.sha256(consensus_text.encode()).hexdigest()[:40]

    response_payload = {
        "task_id": task_id,
        "status": "COMPLETED",
        "summary": summary,
        "hashes": {
            "rationale_hash": rationale_hash,
            "consensus_hash": consensus_hash,
        },
        "blockchain": {
            "tx_hash": None,
            "network": "sepolia",
            "status": "PENDING_COMMIT",
        },
        "results": results,
    }

    # Send webhook back to Elysian Go backend
    if webhook_url:
        print(f"[Worker] Sending webhook to {webhook_url}")
        print(f"[Worker] Hashes: rationale={rationale_hash}, consensus={consensus_hash}")
        try:
            resp = requests.post(webhook_url, json=response_payload, timeout=10)
            resp.raise_for_status()
            print(f"[Worker] Webhook sent successfully: {resp.status_code}")
        except Exception as e:
            print(f"[Worker] Failed to send webhook: {str(e)}")
    else:
        print("[Worker] No webhook URL provided, skipping callback.")


def main() -> None:
    print(f"[Elysian Swarm Worker] Connecting to Redis at {REDIS_HOST}:{REDIS_PORT} db={REDIS_DB}")

    if os.getenv("LLM_API_KEY") in [None, "dummy_key", ""]:
        print("[WARNING] LLM_API_KEY is not set. API calls will fail unless using a mock endpoint.")

    try:
        r = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD,
            decode_responses=True,
        )
        r.ping()
        print(f"[Worker] Connected to Redis. Listening on queue: {REDIS_QUEUE}")
    except Exception as e:
        print(f"[Worker] Failed to connect to Redis: {str(e)}")
        sys.exit(1)

    while True:
        try:
            result = r.brpop(REDIS_QUEUE, timeout=0)
            if result:
                _queue_name, task_json = result
                task_data = json.loads(task_json)
                process_task(task_data)
        except json.JSONDecodeError:
            print(f"[Worker] Failed to decode task JSON: {task_json}")
        except Exception as e:
            print(f"[Worker] Error processing task: {str(e)}")
            time.sleep(1)


if __name__ == "__main__":
    main()
