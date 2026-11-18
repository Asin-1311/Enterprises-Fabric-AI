"""Interactive console: ask Enterprise Fabric AI questions and read the answers.

Run from the project root, in Command Prompt:
    .venv\\Scripts\\activate.bat
    python scripts\\ask.py
or in PowerShell:
    .venv\\Scripts\\Activate.ps1
    python scripts\\ask.py

Options:
    --user S01    start as this synthetic user (otherwise you are asked)
    --stream      show the DRAFT answer as it is generated (the verified answer, with
                  any unverifiable statements removed, is printed after it)

Inside the console:
    type a question and press Enter
    /user S02     switch user            /users   list users and their engagements
    /help         show commands          /quit    exit (or Ctrl+C)

Every question makes real LLM calls through OpenRouter (roughly $0.004 each at M4).
Close the folder watcher first: local Qdrant allows only one program at a time.
"""

import argparse
import logging
import os
import sys
import time
import warnings
from pathlib import Path

# Make "python scripts\ask.py" work as well as "python -m scripts.ask".
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Use the local model cache without contacting Hugging Face when both models are already
# downloaded (they are after the first indexing run). Avoids a network check and a warning.
_HF_HUB = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
if all((_HF_HUB / f"models--BAAI--{m}").exists() for m in ("bge-small-en-v1.5", "bge-reranker-base")):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

# Quieter start-up: hide model-loading progress bars and library chatter.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")  # huggingface_hub resets its logger on import
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
warnings.filterwarnings("ignore")
for noisy in ("httpx", "huggingface_hub", "sentence_transformers", "transformers"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

# Windows consoles may not be UTF-8; avoid crashes on characters such as ₹ and §.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

# Prices per million tokens (input, output) as listed on OpenRouter on 2026-11-07.
# Used only for a rough per-answer cost ESTIMATE shown in the console.
PRICES = {
    "anthropic/claude-haiku-5.5": (0.10, 0.50),
    "anthropic/claude-sonnet-5.5": (2.00, 10.00),
    "anthropic/claude-opus-5.5": (4.00, 20.00),
}
STEP_LABELS = {
    "understanding": "understanding the question",
    "routing": "choosing sources",
    "retrieving": "searching",
    "reranking": "re-ranking",
    "followup_retrieval": "searching again with different wording",
    "compressing": "selecting relevant sentences",
    "synthesizing": "writing the answer",
    "verifying": "checking every claim against its cited source",
    "retrying_retrieval": "confidence too low: searching again once",
}
HELP = """Commands:
  <question>     ask a question
  /user <ID>     switch to another synthetic user (e.g. /user M01)
  /users         list users, roles and engagements
  /help          show this help
  /quit          exit"""


def list_users(sessions) -> dict[str, str]:
    """Print the synthetic users; return {user_id: role}."""
    from sqlalchemy import select

    from app.db.models import Engagement, EngagementMember, User

    with sessions() as s:
        users = s.scalars(select(User).order_by(User.user_id)).all()
        members = s.execute(select(EngagementMember.user_id, Engagement.client_name)
                            .join(Engagement, Engagement.engagement_id == EngagementMember.engagement_id)).all()
        clients: dict[str, list[str]] = {}
        for uid, client in members:
            clients.setdefault(uid, []).append(client)
        print("\n  ID   Name             Role      Engagements")
        for u in users:
            engs = "all (partner)" if u.role == "partner" else (", ".join(clients.get(u.user_id, [])) or "none")
            print(f"  {u.user_id:<4} {u.name:<16} {u.role:<9} {engs}")
        print()
        return {u.user_id: u.role for u in users}


def usage_summary(sessions, query_id: int) -> str:
    """Tokens per model and an estimated cost, from this query's trace rows."""
    from sqlalchemy import select

    from app.db.models import Trace

    totals: dict[str, list[int]] = {}
    with sessions() as s:
        for t in s.scalars(select(Trace).where(Trace.query_id == query_id)):
            if t.model:
                acc = totals.setdefault(t.model, [0, 0])
                acc[0] += t.tokens_in or 0
                acc[1] += t.tokens_out or 0
    if not totals:
        return "no LLM tokens used"
    cost = sum(PRICES.get(m, (0, 0))[0] * i / 1e6 + PRICES.get(m, (0, 0))[1] * o / 1e6
               for m, (i, o) in totals.items())
    parts = [f"{m.split('/')[-1]} {i}+{o}" for m, (i, o) in totals.items()]
    return f"tokens (in+out): {', '.join(parts)}  |  est. cost ${cost:.4f}"


def print_result(result, seconds: float, usage: str, streamed: bool) -> None:
    if not streamed or result.status != "answered":
        print("\n" + result.text)
    if result.citations:
        print("\nSources:")
        for c in result.citations:
            section = f" §{c.section} {c.section_title or ''}".rstrip() if c.section else ""
            pages = ""
            if c.page_start:
                pages = f", p.{c.page_start}" + (f"-{c.page_end}" if c.page_end != c.page_start else "")
            flag = "  [SUPERSEDED VERSION]" if c.superseded else ""
            print(f"  [{c.chunk_id}] {c.title}{section}{pages}{flag}")
    notes = []
    if result.superseded_involved:
        notes.append("a superseded policy/circular was involved; the current version is cited")
    if result.injection_hits:
        notes.append(f"suspicious instructions found in {len(result.injection_hits)} retrieved "
                     "chunk(s) were treated as data and logged")
    if result.removed_claims:
        notes.append(f"{len(result.removed_claims)} statement(s) in the draft could not be "
                     "verified against their sources and were removed")
    for n in notes:
        print(f"  Note: {n}")
    conf = ""
    if result.confidence is not None:
        conf = (f" | confidence {result.confidence:.0%} "
                f"({result.claims_supported}/{result.claims_factual} claims supported)")
    print(f"\n[status: {result.status}{conf} | {seconds:.1f}s | {usage}]")
    print("-" * 78)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask Enterprise Fabric AI questions.")
    parser.add_argument("--user", help="synthetic user id to start as, e.g. S01")
    parser.add_argument("--stream", action="store_true", help="show the answer as it is written")
    args = parser.parse_args()

    print("Enterprise Fabric AI - interactive console (synthetic demo data)")
    print("Loading models and indexes (first start can take 20-40 seconds)...", flush=True)
    from app.agents.graph import build_assistant
    from app.config import get_settings
    from app.retrieval.acl import UnknownUserError

    from app.retrieval.dense import QdrantInUseError

    try:
        assistant = build_assistant(get_settings())
    except QdrantInUseError as exc:
        print(f"\n{exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\nCould not start: {type(exc).__name__}: {exc}")
        return 1
    sessions = assistant.deps.sessions

    roles = list_users(sessions)
    user = (args.user or "").upper()
    while user not in roles:
        try:
            user = input("Choose a user ID (e.g. S01): ").lstrip("﻿").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
    print(f"You are {user} ({roles[user]}). Type a question, or /help.\n")

    while True:
        try:
            line = input(f"{user}> ").lstrip("﻿").strip()  # BOM from piped input
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return 0
        if not line:
            continue
        if line.lower() in ("/quit", "/exit", "quit", "exit"):
            print("Bye.")
            return 0
        if line.lower() == "/help":
            print(HELP)
            continue
        if line.lower() == "/users":
            list_users(sessions)
            continue
        if line.lower().startswith("/user"):
            new = line[5:].strip().upper()
            if new in roles:
                user = new
                print(f"Now asking as {user} ({roles[user]}).")
            else:
                print(f"Unknown user '{new}'. Type /users to see the list.")
            continue

        def on_event(step: str, _payload: dict) -> None:
            if step in STEP_LABELS:
                print(f"  ... {STEP_LABELS[step]}", flush=True)

        streamed = False

        def on_token(text: str) -> None:
            nonlocal streamed
            if not streamed:
                print()
                streamed = True
            print(text, end="", flush=True)

        start = time.perf_counter()
        try:
            result = assistant.ask(line, user, on_event=on_event,
                                   on_token=on_token if args.stream else None)
        except UnknownUserError:
            print(f"User {user} is not in the database.")
            continue
        except KeyboardInterrupt:
            print("\n(cancelled)")
            continue
        except Exception as exc:  # noqa: BLE001 - keep the console alive
            print(f"\nSomething went wrong: {type(exc).__name__}: {exc}")
            continue
        if streamed:
            print("\n\n(the text above was the draft; the verified answer follows)")
        print_result(result, time.perf_counter() - start, usage_summary(sessions, result.query_id),
                     streamed=False)


if __name__ == "__main__":
    raise SystemExit(main())
