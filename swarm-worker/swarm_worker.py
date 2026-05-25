import os
import sys
import json
import time
import hashlib
import psycopg2
from psycopg2.extras import RealDictCursor
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
# Nemesis Ground Truth Database (PostgreSQL Instance 2 - Dedicated)
# ------------------------------------------------------------------------------
NEMESIS_DB_HOST = os.getenv("NEMESIS_DB_HOST", "localhost")
NEMESIS_DB_PORT = int(os.getenv("NEMESIS_DB_PORT", 5432))
NEMESIS_DB_USER = os.getenv("NEMESIS_DB_USER", "postgres")
NEMESIS_DB_PASSWORD = os.getenv("NEMESIS_DB_PASSWORD", "postgres")
NEMESIS_DB_NAME = os.getenv("NEMESIS_DB_NAME", "nemesis_db")
NEMESIS_DB_SSLMODE = os.getenv("NEMESIS_DB_SSLMODE", "disable")

_nemesis_conn = None


def _get_nemesis_conn():
    """Lazy-init PostgreSQL connection to Nemesis DB."""
    global _nemesis_conn
    if _nemesis_conn is None:
        try:
            _nemesis_conn = psycopg2.connect(
                host=NEMESIS_DB_HOST,
                port=NEMESIS_DB_PORT,
                user=NEMESIS_DB_USER,
                password=NEMESIS_DB_PASSWORD,
                dbname=NEMESIS_DB_NAME,
                sslmode=NEMESIS_DB_SSLMODE
            )
            _nemesis_conn.autocommit = True
            print(f"[Init] Connected to Dedicated Nemesis PostgreSQL DB: {NEMESIS_DB_HOST}:{NEMESIS_DB_PORT}/{NEMESIS_DB_NAME}")
        except Exception as e:
            print(f"[Init] WARNING: Could not connect to Nemesis PostgreSQL: {e}")
            _nemesis_conn = None
    return _nemesis_conn


def query_nemesis_price(item_name: str, location: str = None) -> dict:
    """
    Query historical price data from Nemesis for a given item keyword.
    Returns avg/min/max prices and sample count.
    Supports fallback to the first word of the item_name if no samples are found.
    """
    conn = _get_nemesis_conn()
    if conn is None:
        return {}

    def run_query(term: str) -> dict:
        keyword = f"%{term}%"
        params = [keyword]

        sql = """
            SELECT
                COUNT(*) as sample_count,
                AVG(budget_amount) as avg_price,
                MIN(budget_amount) as min_price,
                MAX(budget_amount) as max_price,
                AVG(waste_potential_score) as avg_waste
            FROM procurement
            WHERE package_name ILIKE %s OR work_description ILIKE %s
        """
        params.append(keyword)

        if location:
            sql += " AND location ILIKE %s"
            params.append(f"%{location}%")

        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(sql, params)
            row = cursor.fetchone()
            cursor.close()
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

    # 1. Try full search term
    result = run_query(item_name)
    if result.get("sample_count", 0) > 0:
        return result

    # 2. Try first word fallback if multiple words exist
    words = [w for w in item_name.split() if len(w) > 1]
    if len(words) > 1:
        fallback_term = words[0]
        print(f"[Nemesis Fallback] No samples for '{item_name}', trying '{fallback_term}'...")
        fallback_result = run_query(fallback_term)
        return fallback_result

    return result



def query_standard_price(item_name: str) -> dict:
    """
    Query official standard price limit from POJK/SHSR.
    Supports ILIKE and fallback to the first word of item_name if no match is found.
    """
    conn = _get_nemesis_conn()
    if conn is None:
        return {}

    def run_query(term: str) -> dict:
        keyword = f"%{term}%"
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT item_name, item_category, max_price, min_specs
                FROM standard_price
                WHERE item_name ILIKE %s OR item_category ILIKE %s
                LIMIT 1
            """, [keyword, keyword])
            row = cursor.fetchone()
            cursor.close()
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

    # 1. Try full search term
    result = run_query(item_name)
    if result:
        return result

    # 2. Try first word fallback if multiple words exist
    words = [w for w in item_name.split() if len(w) > 1]
    if len(words) > 1:
        fallback_term = words[0]
        print(f"[Standard Price Fallback] No match for '{item_name}', trying '{fallback_term}'...")
        fallback_result = run_query(fallback_term)
        return fallback_result

    return result



OPENVIKING_API_URL = os.getenv("OPENVIKING_API_URL", "http://localhost:1929")


def query_local_regulations(item_name: str) -> list:
    """
    Query OpenViking RAG engine for local regulations matching the item description.
    """
    if not OPENVIKING_API_URL:
        return []

    url = f"{OPENVIKING_API_URL}/api/v1/search/find"
    payload = {
        "query": item_name,
        "top_k": 2
    }
    try:
        resp = requests.post(url, json=payload, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            chunks = []
            for r in results:
                text = r.get("content") or r.get("text") or ""
                if text:
                    chunks.append(text)
            return chunks
        else:
            print(f"[OpenViking Warning] Returned status code {resp.status_code}")
            return []
    except Exception as e:
        print(f"[OpenViking Error] Failed to query local regulations: {e}")
        return []


def run_agent(system_prompt: str, user_prompt: str, max_tokens: int = 300) -> tuple[str, int, int]:
    """Utility to run a single agent completion."""
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,  # Lower temperature to reduce hallucinations/looping
            max_tokens=max_tokens,
            frequency_penalty=1.0,  # Strongly penalize repeated tokens (prevent "agaragaragar...")
            presence_penalty=0.5,   # Encourage diversity of vocabulary
        )
        content = response.choices[0].message.content
        import re
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        
        prompt_tokens = 0
        completion_tokens = 0
        if response.usage:
            prompt_tokens = response.usage.prompt_tokens or 0
            completion_tokens = response.usage.completion_tokens or 0
            
        return content, prompt_tokens, completion_tokens
    except Exception as e:
        print(f"[LLM Error] {e}")
        return f"Error connecting to LLM: {str(e)}", 0, 0


def publish_progress(redis_conn, task_id, status, step, agent=None, message=None, progress=0):
    payload = {
        "task_id": task_id,
        "status": status,
        "step": step,
        "agent": agent,
        "message": message,
        "progress": progress,
        "timestamp": int(time.time() * 1000)
    }
    try:
        redis_conn.publish("swarm:events", json.dumps(payload))
    except Exception as e:
        print(f"[Redis Publish Error] {e}")


def run_swarm_evaluation(r, item: dict, task_id: str = "") -> tuple[str, list, str, float, int, int]:
    """
    Executes the multi-agent swarm evaluation for a single budget item.

    Agents:
      1. Auditor   — Compares price against Standar Harga Regional
      2. Compliance — Evaluates legal justification / regulatory exceptions
      3. Manager   — Decides final FLAGGED | CLEARED verdict as JSON

    Memory: pi-memctx pattern — inject relevant context before reasoning,
            learn durable memory after debate.

    Returns: (status, agent_logs, manager_conclusion, max_price, total_prompt, total_completion)
    """
    item_json = json.dumps(item, ensure_ascii=False)
    item_name = item.get('name', '')
    item_location = item.get('metadata', {}).get('region', '') if item.get('metadata') else ''

    publish_progress(r, task_id, "PROCESSING", f"Querying regional prices and POJK reference for: {item_name}...", agent="System", progress=15)

    total_prompt = 0
    total_completion = 0

    # ------------------------------------------------------------------
    # 1. Agent Analis (Auditor) — with Nemesis + Memory Context
    # ------------------------------------------------------------------
    item_price = item.get('unit_price', item.get('requested_price', 0))

    # Query Nemesis DB for historical prices
    nemesis_data = query_nemesis_price(item_name, item_location)
    standard_data = query_standard_price(item_name)

    publish_progress(r, task_id, "PROCESSING", f"Retrieved POJK standard and Nemesis historical samples for {item_name}.", agent="System", progress=25)

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

    override_context = ""
    override = item.get("override")
    if override:
        override_context = f"""

[PENGATURAN KOREKSI MANUSIA (RLHF OVERRIDE)]
Catatan perbaikan manual sebelumnya untuk barang ini:
- Keputusan awal: {override.get('original_verdict')}
- Keputusan yang dikoreksi: {override.get('new_verdict')}
- Alasan koreksi: {override.get('justification')}

Instruksi Penting: Kamu HARUS mengikuti koreksi manual di atas sebagai acuan utama dalam evaluasimu.
"""

    auditor_system = f"""
Kamu adalah Agen Analis (Auditor Anggaran) Pemerintah Daerah.
Tugasmu adalah memeriksa apakah harga barang yang diajukan wajar atau kemahalan (markup) dibanding Standar Harga Resmi (POJK) dan riwayat pembelian.

Aturan Penulisan:
1. Gunakan Bahasa Indonesia yang santun, sederhana, dan mudah dimengerti orang awam. Jangan gunakan istilah teknis IT atau istilah asing.
2. JANGAN gunakan format markdown seperti heading (##), list (-), atau sub-judul tebal. Tulis langsung dalam 2-3 kalimat paragraf biasa.
3. Sebutkan dengan jelas: harga yang diajukan, batas maksimal resmi, dan selisihnya.
4. Nyatakan kesimpulan akhir secara langsung: Kemahalan (Markup) atau Wajar.
5. Jika ada data historis pengadaan serupa dari Nemesis (SIRUP), sebutkan secara singkat perbandingan harga historisnya (misalnya rata-rata harga pengadaan serupa) untuk memperkuat hasil analisismu.
6. PENTING: Tulis respon Anda dengan sangat singkat dan padat (maksimal 3 kalimat). DILARANG KERAS mengulang-ulang kata secara beruntun (seperti 'agar agar agar...' atau 'yang yang yang...'). Tulis kalimat secara mengalir, ringkas, dan langsung ke inti informasi.
7. DILARANG KERAS menulis dalam bahasa Mandarin, menggunakan huruf/aksara China (中文/汉字), atau tanda baca Mandarin. Seluruh respon Anda wajib ditulis 100% dalam Bahasa Indonesia yang baik dan benar.
{standar_harga_section}
{memory_context}
{override_context}
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

    publish_progress(r, task_id, "PROCESSING", f"Analis (Auditor) sedang membandingkan harga regional untuk {item_name}...", agent="Auditor", progress=35)
    auditor_response, p1, c1 = run_agent(auditor_system, auditor_prompt)
    total_prompt += p1
    total_completion += c1
    publish_progress(r, task_id, "PROCESSING", f"Auditor selesai mengevaluasi {item_name}.", agent="Auditor", message=auditor_response, progress=50)

    # ------------------------------------------------------------------
    # 2. Agent Pengawas (Compliance)
    # ------------------------------------------------------------------
    compliance_system = """
Kamu adalah Agen Pengawas (Hukum & Kepatuhan) Pemerintah Daerah.
Tugasmu adalah memeriksa apakah pengajuan harga yang melebihi batas ini diizinkan oleh aturan hukum (misalnya ada alasan khusus yang sah).

Aturan Penulisan:
1. Gunakan Bahasa Indonesia yang sederhana dan langsung pada intinya. Hindari pasal-pasal hukum yang rumit agar mudah dipahami orang non-IT/awam.
2. JANGAN gunakan format markdown, heading, list, atau sub-judul tebal. Tulis langsung dalam 2-3 kalimat paragraf biasa.
3. Nyatakan apakah pengajuan ini memiliki dokumen pendukung/alasan sah atau tidak. Jika tidak, sebutkan bahwa pengajuan ditolak karena melanggar batas harga resmi.
4. PENTING: Tulis respon Anda dengan sangat singkat dan padat (maksimal 3 kalimat). DILARANG KERAS mengulang-ulang kata secara beruntun (seperti 'agar agar agar...' atau 'yang yang yang...'). Tulis kalimat secara mengalir, ringkas, dan langsung ke inti informasi.
5. DILARANG KERAS menulis dalam bahasa Mandarin, menggunakan huruf/aksara China (中文/汉字), atau tanda baca Mandarin. Seluruh respon Anda wajib ditulis 100% dalam Bahasa Indonesia yang baik dan benar.
"""

    # Query OpenViking RAG for local regulations context
    regulations = query_local_regulations(item_name)
    regulations_context = ""
    if regulations:
        regulations_context = "\n\nKonteks Regulasi Lokal Terkait (RAG):\n" + "\n---\n".join(regulations)

    compliance_user = (
        f"Item yang diajukan:\n{item_json}\n\n"
        f"Temuan Auditor:\n{auditor_response}\n\n"
        f"{regulations_context}\n\n"
        f"Berikan evaluasi kepatuhanmu berdasarkan temuan auditor dan konteks regulasi lokal yang tersedia:"
    )
    print(f"  -> [Compliance] Reviewing: {item.get('name')}")
    publish_progress(r, task_id, "PROCESSING", f"Pengawas (Compliance) sedang meninjau aspek kepatuhan legal untuk {item_name}...", agent="Pengawas", progress=65)
    compliance_response, p2, c2 = run_agent(compliance_system, compliance_user)
    total_prompt += p2
    total_completion += c2
    publish_progress(r, task_id, "PROCESSING", f"Compliance selesai meninjau {item_name}.", agent="Pengawas", message=compliance_response, progress=80)

    # ------------------------------------------------------------------
    # 3. Agent Manajer (Decision Maker)
    # ------------------------------------------------------------------
    manager_system = """
Kamu adalah Agen Manajer (Kepala Swarm).
Tugasmu memberikan keputusan akhir yang sangat singkat, padat, dan jelas berdasarkan laporan Auditor dan Pengawas.

Aturan Penulisan:
1. Gunakan Bahasa Indonesia yang sangat sederhana dan langsung ke kesimpulan untuk pengguna non-IT.
2. Kamu HARUS merespon HANYA dalam format JSON valid berikut:
{
    "status": "FLAGGED" | "CLEARED",
    "manager_conclusion": "Tulis kesimpulan akhir di sini dalam 1-2 kalimat sederhana tanpa format markdown."
}
3. Gunakan "FLAGGED" jika harga kemahalan dan melanggar batas resmi. Gunakan "CLEARED" jika harga dinilai wajar.
4. PENTING: Kesimpulan akhir (manager_conclusion) harus ditulis dengan sangat singkat (maksimal 2 kalimat). DILARANG KERAS mengulang-ulang kata secara beruntun (seperti 'agar agar agar...' atau 'yang yang yang...').
5. DILARANG KERAS menulis dalam bahasa Mandarin, menggunakan huruf/aksara China (中文/汉字), atau tanda baca Mandarin. Seluruh respon Anda (terutama isi manager_conclusion) wajib ditulis 100% dalam Bahasa Indonesia yang baik dan benar.
"""

    manager_user = (
        f"Item:\n{item_json}\n\n"
        f"Auditor:\n{auditor_response}\n\n"
        f"Pengawas:\n{compliance_response}\n\n"
        f"Berikan kesimpulan JSON:"
    )
    print(f"  -> [Manager] Concluding: {item.get('name')}")
    publish_progress(r, task_id, "PROCESSING", f"Kepala Manajer membuat konsensus akhir untuk {item_name}...", agent="Manager", progress=88)
    manager_response_raw, p3, c3 = run_agent(manager_system, manager_user)
    total_prompt += p3
    total_completion += c3

    # Parse manager JSON — with robust regex & fallback parsing
    status = "CLEARED"
    manager_conclusion = ""
    clean_json = manager_response_raw.replace("```json", "").replace("```", "").strip()
    try:
        manager_data = json.loads(clean_json)
        status = manager_data.get("status", "CLEARED")
        manager_conclusion = manager_data.get("manager_conclusion", "")
    except Exception as e:
        print(f"[JSON Error] Failed to parse manager response: {e}")
        
    # If json.loads failed or returned empty conclusion, try regex extraction
    if not manager_conclusion:
        import re
        status_match = re.search(r'"status"\s*:\s*"([^"]+)"', clean_json)
        conclusion_match = re.search(r'"manager_conclusion"\s*:\s*"([^"]+)"', clean_json)
        if status_match:
            status = status_match.group(1)
        else:
            status = "FLAGGED" if "FLAGGED" in manager_response_raw.upper() else "CLEARED"
            
        if conclusion_match:
            manager_conclusion = conclusion_match.group(1)
        else:
            # Clean up the raw response text
            manager_conclusion = manager_response_raw

    # Trim and make sure it has no raw JSON styling
    manager_conclusion = manager_conclusion.strip()
    if manager_conclusion.startswith("{") and manager_conclusion.endswith("}"):
        import re
        match = re.search(r'"manager_conclusion"\s*:\s*"([^"]+)"', manager_conclusion)
        if match:
            manager_conclusion = match.group(1)

    publish_progress(r, task_id, "PROCESSING", f"Manager selesai memproses {item_name}: {status}.", agent="Manager", message=manager_conclusion, progress=95)

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

    max_price = standard_data.get("max_price", 0) if standard_data else 0
    return status, agent_logs, manager_conclusion, max_price, total_prompt, total_completion


def process_task(r, task_data: dict) -> None:
    """Process the swarm task by orchestrating the agents."""
    task_id = task_data.get("task_id")
    webhook_url = task_data.get("webhook_url")
    items = task_data.get("items", [])

    print(f"\n[Worker] Processing task {task_id} with {len(items)} items")
    publish_progress(r, task_id, "PROCESSING", "Inisialisasi Swarm Review Pipeline. Menghubungkan ke agen...", agent="System", progress=5)

    results = []
    markup_count = 0
    total_task_prompt = 0
    total_task_completion = 0

    for idx, item in enumerate(items):
        item_name = item.get('name', f"Item {idx+1}")
        publish_progress(r, task_id, "PROCESSING", f"Memulai analisis item {idx+1} dari {len(items)}: {item_name}...", agent="System", progress=10)
        status, agent_logs, manager_conclusion, max_price, p_tokens, c_tokens = run_swarm_evaluation(r, item, task_id)
        total_task_prompt += p_tokens
        total_task_completion += c_tokens

        if status == "FLAGGED":
            markup_count += 1

        item_location = item.get('metadata', {}).get('region', '') if item.get('metadata') else ''
        results.append({
            "item_id": item.get("item_id"),
            "name": item_name,
            "qty": item.get("qty", 1),
            "requested_price": item.get("unit_price", item.get("requested_price", 0)),
            "max_price": max_price,
            "region": item_location,
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
    rationale_hash = "0x" + hashlib.sha256(rationale_text.encode()).hexdigest()
    
    consensus_text = f"{task_id}:{markup_count}:{len(items)}"
    consensus_hash = "0x" + hashlib.sha256(consensus_text.encode()).hexdigest()

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
        "token_usage": {
            "prompt_tokens": total_task_prompt,
            "completion_tokens": total_task_completion,
            "model": MODEL_NAME,
        }
    }

    # Send webhook back to Elysian Go backend
    if webhook_url:
        print(f"[Worker] Sending webhook to {webhook_url}")
        print(f"[Worker] Hashes: rationale={rationale_hash}, consensus={consensus_hash}")
        publish_progress(r, task_id, "PROCESSING", "Mengirimkan hasil konsensus ke core ledger backend...", agent="System", progress=98)
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
                process_task(r, task_data)
        except json.JSONDecodeError:
            print(f"[Worker] Failed to decode task JSON: {task_json}")
        except Exception as e:
            print(f"[Worker] Error processing task: {str(e)}")
            time.sleep(1)


if __name__ == "__main__":
    main()
