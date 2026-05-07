from database import save_memory, get_recent_memories, get_memory_count as _db_count


def record_action(category, summary, device_name=None, triggered_by=None):
    try:
        save_memory(category, summary, device_name, triggered_by)
    except Exception as e:
        print(f"[Memory] Failed to save: {e}")


def get_context_for_llm(limit=5):
    try:
        memories = get_recent_memories(limit)
        if not memories:
            return ""
        lines = ["Recent infrastructure actions:"]
        for m in memories:
            line = f"- [{m['timestamp'][:10]}] {m['summary']}"
            if m['device_name']:
                line += f" ({m['device_name']})"
            lines.append(line)
        return "\n".join(lines)
    except Exception:
        return ""


def get_memory_count():
    return _db_count()
