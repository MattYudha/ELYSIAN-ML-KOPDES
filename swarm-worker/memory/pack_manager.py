"""
Pack Manager — JSON-native memory storage
==========================================
Pattern dari pi-memctx: local-first, no database server, inspectable.
Tapi format JSON (bukan Markdown) — per instruksi CEO.

Struktur folder:
  memory/packs/{agent_name}/
    00-system/
      memory-manifest.json
      resource-map.json
    20-context/
      {topic}.json
    40-actions/
      {timestamp}-{slug}.json
    50-decisions/
      {seq}-{slug}.json
    60-observations/
      {slug}.json
    70-runbooks/
      {slug}.json
    80-sessions/
      {task_id}.json
"""

import os
import json
import glob
import re
from datetime import datetime
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, asdict

MEMORY_DIR = os.path.join(os.path.dirname(__file__), "packs")


@dataclass
class MemoryNote:
    """Satu unit memory — terinspirasi pi-memctx note types."""
    type: str           # context | decision | observation | runbook | action | session
    title: str
    content: str
    tags: List[str]
    source: str         # task_id, agent, atau manual
    created_at: str
    links: List[str]    # cross-reference ke memory lain
    score: float = 0.0  # relevance score (diisi saat search)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryNote":
        return cls(**d)


class PackManager:
    """
    Kelola memory pack per agent.
    Sama seperti pi-memctx pack, tapi JSON format.
    """

    DIR_MAP = {
        "context": "20-context",
        "action": "40-actions",
        "decision": "50-decisions",
        "observation": "60-observations",
        "runbook": "70-runbooks",
        "session": "80-sessions",
    }

    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.pack_dir = os.path.join(MEMORY_DIR, agent_name)
        self._ensure_dirs()

    def _ensure_dirs(self):
        """Buat struktur folder pack jika belum ada."""
        for subdir in ["00-system", "20-context", "40-actions", "50-decisions",
                       "60-observations", "70-runbooks", "80-sessions"]:
            os.makedirs(os.path.join(self.pack_dir, subdir), exist_ok=True)

    def _filepath(self, note_type: str, slug: str) -> str:
        """Generate path untuk memory note."""
        directory = self.DIR_MAP.get(note_type, "60-observations")
        if note_type == "action":
            slug = f"{datetime.now().strftime('%Y%m%d')}-{slug}"
        elif note_type == "decision":
            seq = self._next_decision_seq()
            slug = f"{seq:03d}-{slug}"
        return os.path.join(self.pack_dir, directory, f"{slug}.json")

    def _next_decision_seq(self) -> int:
        """Auto-increment decision number."""
        pattern = os.path.join(self.pack_dir, "50-decisions", "*.json")
        files = glob.glob(pattern)
        if not files:
            return 1
        nums = []
        for f in files:
            name = os.path.basename(f)
            m = re.match(r"(\d+)-", name)
            if m:
                nums.append(int(m.group(1)))
        return max(nums) + 1 if nums else 1

    def save(self, note: MemoryNote) -> str:
        """Simpan memory note ke JSON file."""
        slug = re.sub(r"[^a-z0-9-]", "-", note.title.lower())[:50]
        filepath = self._filepath(note.type, slug)

        # Handle duplicate filename
        base, ext = os.path.splitext(filepath)
        counter = 1
        while os.path.exists(filepath):
            filepath = f"{base}-{counter}{ext}"
            counter += 1

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(note.to_dict(), f, ensure_ascii=False, indent=2)

        return filepath

    def load_all(self) -> List[MemoryNote]:
        """Load semua memory notes untuk agent ini."""
        notes = []
        pattern = os.path.join(self.pack_dir, "**", "*.json")
        for filepath in glob.glob(pattern, recursive=True):
            if "00-system" in filepath:
                continue
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                notes.append(MemoryNote.from_dict(data))
            except (json.JSONDecodeError, KeyError):
                continue
        return notes

    def search(self, query: str, mode: str = "keyword", limit: int = 5) -> List[MemoryNote]:
        """
        Cari memory relevan.
        mode: keyword | semantic (simple tf-idf fallback)
        """
        query_lower = query.lower()
        notes = self.load_all()
        scored = []

        for note in notes:
            score = 0.0
            text = f"{note.title} {note.content} {' '.join(note.tags)}".lower()

            if mode == "keyword":
                # Simple keyword matching
                keywords = query_lower.split()
                matches = sum(1 for kw in keywords if kw in text)
                score = matches / len(keywords) if keywords else 0
            else:
                # Simple semantic: exact phrase + partial word match
                if query_lower in text:
                    score = 1.0
                else:
                    keywords = query_lower.split()
                    matches = sum(1 for kw in keywords if kw in text)
                    score = matches / (len(keywords) * 2) if keywords else 0

            if score > 0:
                note.score = score
                scored.append(note)

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:limit]

    def get_manifest(self) -> dict:
        """Memory manifest — summary dari pack ini."""
        notes = self.load_all()
        by_type = {}
        for n in notes:
            by_type[n.type] = by_type.get(n.type, 0) + 1

        return {
            "agent": self.agent_name,
            "total_notes": len(notes),
            "by_type": by_type,
            "pack_dir": self.pack_dir,
            "last_updated": datetime.now().isoformat(),
        }
