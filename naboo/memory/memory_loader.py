"""
Naboo memory loader.

Loads persistent memory files and returns a context string
to inject into the system prompt at session start.
"""

from datetime import datetime, timedelta
from pathlib import Path


MEMORY_DIR = Path(__file__).parent


def load_memory_context(days_back: int = 7) -> str:
    """
    Load Naboo's memory context for system prompt injection.
    
    Returns a string to append to the system prompt containing:
    - Curated long-term memory (MEMORY.md)
    - Family profiles (family/*.md)
    - Recent session summaries (last N days)
    """
    sections = []

    # Long-term memory
    memory_file = MEMORY_DIR / "MEMORY.md"
    if memory_file.exists():
        sections.append("## Naboo's Memory\n" + memory_file.read_text())

    # Family profiles — prefer .local.md (private, gitignored) over base .md
    family_dir = MEMORY_DIR / "family"
    if family_dir.exists():
        seen = set()
        for profile in sorted(family_dir.glob("*.md")):
            name = profile.stem.replace(".local", "")
            if name in seen:
                continue
            # Prefer .local.md (enriched, private) over base .md
            local = family_dir / f"{name}.local.md"
            source = local if local.exists() else profile
            seen.add(name)
            sections.append(f"## About {name.title()}\n" + source.read_text())

    # Recent session summaries
    sessions_dir = MEMORY_DIR / "sessions"
    if sessions_dir.exists():
        recent_summaries = []
        for i in range(days_back):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            session_file = sessions_dir / f"{date}.md"
            if session_file.exists():
                recent_summaries.append(f"### {date}\n" + session_file.read_text())

        if recent_summaries:
            sections.append("## Recent Sessions\n" + "\n\n".join(recent_summaries))

    if not sections:
        return ""

    return "\n\n---\n\n".join(sections)


def append_session_summary(summary: str, date: str | None = None) -> None:
    """
    Append a session summary to today's session log.
    Called at the end of each conversation session.
    """
    sessions_dir = MEMORY_DIR / "sessions"
    sessions_dir.mkdir(exist_ok=True)

    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    session_file = sessions_dir / f"{date}.md"
    timestamp = datetime.now().strftime("%H:%M")

    with open(session_file, "a") as f:
        f.write(f"\n## Session — {timestamp}\n{summary}\n")


def update_family_profile(name: str, update: str) -> None:
    """
    Append new information to a family member's profile.
    Called when Naboo learns something significant about someone.
    """
    family_dir = MEMORY_DIR / "family"
    family_dir.mkdir(exist_ok=True)

    profile_file = family_dir / f"{name.lower()}.md"
    timestamp = datetime.now().strftime("%Y-%m-%d")

    with open(profile_file, "a") as f:
        f.write(f"\n### Learned {timestamp}\n{update}\n")
