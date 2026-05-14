"""
Agent Memory — Swarm Intelligence Context Injection
====================================================
Pattern: pi-memctx (inject relevant context before reasoning)
Format: JSON (per instruksi CEO)

Cara kerja:
  1. Sebelum agent reasoning, search memory relevan
  2. Inject ke system prompt sebagai "context window"
  3. Setelah debate, extract & save durable memory
  4. Cross-agent: Manager bisa lihat memory Auditor + Compliance
"""

import os
import json
from typing import List, Dict, Optional
from datetime import datetime

from .pack_manager import PackManager, MemoryNote


class AgentMemory:
    """
    Memory layer untuk satu agent di swarm.
    Terinspirasi pi-memctx Memory Gateway — tapi JSON format.
    """

    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.pack = PackManager(agent_name)
        self._manifest = None

    def search(self, query: str, mode: str = "keyword", limit: int = 3) -> str:
        """
        Cari memory relevan dan format sebagai context string.
        Ini yang di-inject ke system prompt sebelum reasoning.
        """
        notes = self.pack.search(query, mode=mode, limit=limit)
        if not notes:
            return ""

        lines = [f"\n[Memory Context — {self.agent_name}]"]
        for note in notes:
            lines.append(f"  • {note.title} (relevance: {note.score:.2f})")
            # Truncate content untuk hemat token
            content = note.content[:200] + "..." if len(note.content) > 200 else note.content
            lines.append(f"    {content}")
            if note.links:
                lines.append(f"    refs: {', '.join(note.links)}")

        return "\n".join(lines)

    def learn(self, note_type: str, title: str, content: str,
              tags: List[str] = None, source: str = "",
              links: List[str] = None) -> str:
        """
        Simpan durable memory setelah turn/debate.
        Terinspirasi pi-memctx auto-learn after turn.
        """
        note = MemoryNote(
            type=note_type,
            title=title,
            content=content,
            tags=tags or [],
            source=source,
            created_at=datetime.now().isoformat(),
            links=links or [],
        )
        filepath = self.pack.save(note)
        return filepath

    def learn_decision(self, title: str, content: str,
                       tags: List[str] = None, source: str = "") -> str:
        """Shortcut: simpan decision ke collection."""
        return self.learn("decision", title, content, tags, source)

    def learn_observation(self, title: str, content: str,
                          tags: List[str] = None, source: str = "") -> str:
        """Shortcut: simpan observation ke collection."""
        return self.learn("observation", title, content, tags, source)

    def learn_from_debate(self, task_id: str, item_name: str,
                          auditor_result: str, compliance_result: str,
                          manager_verdict: str, status: str):
        """
        Auto-extract memory dari hasil debate swarm.
        Simpan ke multiple note types (observation + decision).
        """
        memories_saved = []

        # 1. Observation: pattern yang ditemukan
        if status == "FLAGGED":
            obs_title = f"Markup pattern: {item_name[:30]}"
            obs_content = (
                f"Item '{item_name}' di-flag. "
                f"Auditor: {auditor_result[:100]}. "
                f"Verdict: {manager_verdict[:100]}"
            )
            path = self.learn_observation(
                title=obs_title,
                content=obs_content,
                tags=["markup", "pattern", item_name.split()[0].lower()],
                source=task_id,
            )
            memories_saved.append(("observation", path))

        # 2. Decision: kesimpulan manager (COLLECTION DECISION)
        dec_title = f"Verdict {status}: {item_name[:30]}"
        dec_content = manager_verdict
        path = self.learn_decision(
            title=dec_title,
            content=dec_content,
            tags=["verdict", status.lower(), item_name.split()[0].lower()],
            source=task_id,
        )
        memories_saved.append(("decision", path))

        # 3. Session snapshot (rich persistence)
        session_title = f"Debate {task_id[:8]}"
        session_content = json.dumps({
            "item": item_name,
            "auditor": auditor_result,
            "compliance": compliance_result,
            "manager": manager_verdict,
            "status": status,
        }, ensure_ascii=False)
        path = self.learn(
            "session", session_title, session_content,
            ["session", task_id[:8]], task_id,
        )
        memories_saved.append(("session", path))

        return memories_saved

    def get_system_prompt_context(self, item_name: str = "",
                                   location: str = "") -> str:
        """
        Generate context block untuk ditambahkan ke system prompt.
        Ini yang membuat agent "tidak cold start".
        """
        contexts = []

        # Search item-specific memory
        if item_name:
            ctx = self.search(item_name, limit=2)
            if ctx:
                contexts.append(ctx)

        # Search location-specific memory
        if location:
            ctx = self.search(location, limit=1)
            if ctx:
                contexts.append(ctx)

        # Search general patterns
        ctx = self.search("pattern markup flagged", limit=1)
        if ctx:
            contexts.append(ctx)

        return "\n".join(contexts) if contexts else ""

    def status(self) -> dict:
        """Memory status overlay — mirip pi-memctx status bar."""
        manifest = self.pack.get_manifest()
        return {
            "agent": self.agent_name,
            "memory_ready": manifest["total_notes"] > 0,
            "total_notes": manifest["total_notes"],
            "by_type": manifest["by_type"],
            "pack_dir": manifest["pack_dir"],
        }


class SharedMemory:
    """
    Cross-agent memory sharing.
    Manager bisa akses memory Auditor + Compliance.
    """

    def __init__(self):
        self.auditor_mem = AgentMemory("auditor")
        self.compliance_mem = AgentMemory("compliance")
        self.manager_mem = AgentMemory("manager")

    def get_all_context(self, item_name: str = "") -> str:
        """Aggregate context dari semua agent."""
        parts = []
        for mem in [self.auditor_mem, self.compliance_mem, self.manager_mem]:
            ctx = mem.search(item_name, limit=2)
            if ctx:
                parts.append(ctx)
        return "\n".join(parts)

    def learn_all(self, task_id: str, item_name: str,
                  auditor_result: str, compliance_result: str,
                  manager_verdict: str, status: str):
        """Save memory ke semua agent packs."""
        results = {}
        for mem in [self.auditor_mem, self.compliance_mem, self.manager_mem]:
            saved = mem.learn_from_debate(
                task_id, item_name,
                auditor_result, compliance_result,
                manager_verdict, status
            )
            results[mem.agent_name] = saved
        return results
