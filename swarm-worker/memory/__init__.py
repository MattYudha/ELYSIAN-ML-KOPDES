"""
Agent Memory Layer — Elysian Swarm Intelligence
================================================
Pattern: pi-memctx (local-first, durable, inject-before-reasoning)
Format: JSON (bukan Markdown — per instruksi CEO)

Struktur memory:
  - context      : Stack, conventions, environment
  - decisions    : Architecture & technical decisions
  - observations : Durable facts, caveats, patterns
  - runbooks     : Repeatable procedures
  - actions      : Completed work, migrations
  - sessions     : Rich debate snapshots

Usage:
    from memory import AgentMemory
    mem = AgentMemory(agent_name="auditor")
    mem.learn(type="observation", content="Laptop Dell sering markup 20%")
    context = mem.search("laptop dell", limit=3)
"""

from .agent_memory import AgentMemory, SharedMemory
from .pack_manager import PackManager, MemoryNote

__all__ = ["AgentMemory", "SharedMemory", "PackManager", "MemoryNote"]
