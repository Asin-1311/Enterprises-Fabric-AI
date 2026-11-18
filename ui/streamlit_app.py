"""Streamlit frontend for Enterprise Fabric AI (CLAUDE.md section 10).

Start the API first, then the UI (two terminals, project root, venv active):
    python -m uvicorn app.api.main:app --port 8000
    streamlit run ui/streamlit_app.py

Features: email + password sign-in (the same accounts as the React UI; creating an
account and resetting a password are in the React UI only), streamed answer with agent
progress, citations with source preview and page, confidence score, superseded-policy
badge, and helpful / not helpful feedback with an optional correction.
The API URL can be changed with the AUDIT_API_URL environment variable.
"""

import json
import os
from collections.abc import Iterable, Iterator

import httpx
import streamlit as st

API = os.environ.get("AUDIT_API_URL", "http://127.0.0.1:8000/api/v1")
STEP_LABELS = {
    "understanding": "Understanding the question",
    "routing": "Choosing sources",
    "retrieving": "Searching the knowledge base",
    "reranking": "Re-ranking results",
    "followup_retrieval": "Searching again with different wording",
    "compressing": "Selecting relevant sentences",
    "synthesizing": "Writing a draft answer",
    "verifying": "Checking every claim against its source",
    "retrying_retrieval": "Confidence too low: searching once more",
}
STATUS_NOTES = {
    "denied": ("info", "Not available for your role or engagement assignments."),
    "not_found": ("info", "Nothing suitable was found in the material you can access."),
    "abstained": ("warning", "Not enough could be verified. Rephrase, or ask your engagement manager."),
}


def iter_sse(lines: Iterable[str]) -> Iterator[tuple[str, dict]]:
    """Parse Server-Sent Event lines into (event, data) pairs."""
    name, data = None, []
    for line in lines:
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif not line.strip():
            if name is not None:
                yield name, json.loads("\n".join(data)) if data else {}
            name, data = None, []
    if name is not None:
        yield name, json.loads("\n".join(data)) if data else {}


def auth_headers() -> dict:
    """The signed-in session's token, as the API expects it."""
    return {"Authorization": f"Bearer {st.session_state['token']}"}


def sign_in_form() -> None:
    """Email + password form; on success the token and user go into the session state."""
    with st.form("sign-in"):
        st.subheader("Sign in")
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in"):
            try:
                r = httpx.post(f"{API}/auth/login", json={"email": email, "password": password},
                               timeout=10)
            except httpx.HTTPError:
                st.error(f"The API is not reachable at {API}. Start it with:\n\n"
                         "`python -m uvicorn app.api.main:app --port 8000`")
                return
            if r.status_code == 200:
                st.session_state["token"] = r.json()["token"]
                st.session_state["user"] = r.json()["user"]
                st.rerun()
            detail = r.json().get("detail") if r.headers.get("content-type", "").startswith(
                "application/json") else None
            st.error(detail if isinstance(detail, str) else "Sign-in failed.")
    st.caption("To create an account or reset a password, use the main (React) interface.")


def sign_out() -> None:
    try:
        httpx.post(f"{API}/auth/logout", headers=auth_headers(), timeout=10)
    except httpx.HTTPError:
        pass
    for key in list(st.session_state):
        del st.session_state[key]
    st.rerun()


def render_answer(final: dict, user_id: str) -> None:
    """Confidence, badges, citations and feedback for a completed answer."""
    status = final["status"]
    if status in STATUS_NOTES:
        kind, note = STATUS_NOTES[status]
        getattr(st, kind)(note)
    cols = st.columns(3)
    if final.get("confidence") is not None:
        cols[0].metric("Confidence", f"{final['confidence']:.0%}",
                       help=f"{final['claims_supported']} of {final['claims_factual']} factual "
                            "claims verified against their cited source")
    cols[1].metric("Time", f"{final['seconds_to_answer']:.1f} s")
    cols[2].metric("Status", status.replace("_", " "))
    if final.get("superseded_involved"):
        st.warning("Superseded policy involved: the current version is cited, and the "
                   "earlier version is marked below.", icon="🔁")
    if final.get("removed_claims"):
        st.caption(f"{final['removed_claims']} draft statement(s) could not be verified and were removed.")
    if final.get("injection_flagged_chunks"):
        st.caption(f"{final['injection_flagged_chunks']} retrieved passage(s) contained "
                   "instruction-like text; it was treated as data and logged.")

    if final["citations"]:
        st.markdown("**Sources**")
        for c in final["citations"]:
            pages = ""
            if c["page_start"]:
                pages = f", p.{c['page_start']}" + (
                    f"-{c['page_end']}" if c["page_end"] != c["page_start"] else "")
            section = f" · §{c['section']} {c['section_title'] or ''}" if c["section"] else ""
            label = f"[{c['chunk_id']}] {c['title']}{section}{pages}"
            if c["superseded"]:
                label += "  ·  SUPERSEDED VERSION"
            with st.expander(label):
                st.write(c["preview"])

    if status in ("answered", "abstained", "not_found"):
        key = f"fb-{final['answer_id']}"
        if st.session_state.get(key):
            st.caption("Thank you, your feedback was recorded.")
            return
        with st.form(key):
            rating = st.radio("Was this answer helpful?", ["helpful", "not_helpful"],
                              format_func=lambda r: "Helpful" if r == "helpful" else "Not helpful",
                              horizontal=True)
            correction = st.text_area("Correction (optional)", height=70)
            if st.form_submit_button("Send feedback"):
                r = httpx.post(f"{API}/feedback", timeout=10, headers=auth_headers(), json={
                    "answer_id": final["answer_id"], "rating": rating,
                    "correction": correction or None})
                if r.status_code == 200:
                    st.session_state[key] = True
                    st.rerun()
                else:
                    st.error(f"Feedback not saved ({r.status_code}).")


def ask_streaming(question: str) -> dict | None:
    """POST /query and render progress and the verified answer as they arrive."""
    status_box = st.status("Thinking…", expanded=True)
    answer_box = st.empty()
    text, final = "", None
    try:
        with httpx.stream("POST", f"{API}/query", json={"text": question}, headers=auth_headers(),
                          timeout=httpx.Timeout(10, read=300)) as resp:
            if resp.status_code == 401:
                status_box.update(label="Your session has ended. Sign in again.", state="error")
                st.session_state.pop("token", None)
                return None
            if resp.status_code != 200:
                status_box.update(label=f"Request failed ({resp.status_code})", state="error")
                return None
            for event, data in iter_sse(resp.iter_lines()):
                if event == "progress":
                    status_box.write(STEP_LABELS.get(data.get("step"), data.get("step")))
                elif event == "token":
                    text += data["text"]
                    answer_box.markdown(text)
                elif event == "final":
                    final = data
                elif event == "error":
                    status_box.update(label="Error", state="error")
                    st.error(data.get("message", "unknown error"))
                    return None
    except httpx.HTTPError as exc:
        status_box.update(label="Could not reach the API", state="error")
        st.error(f"{type(exc).__name__}: {exc}")
        return None
    status_box.update(label="Done", state="complete", expanded=False)
    if final is not None:
        answer_box.markdown(final["text"])
    return final


def main() -> None:
    st.set_page_config(page_title="Enterprise Fabric AI", page_icon="📚", layout="wide")
    st.title("Enterprise Fabric AI")
    st.caption("SYNTHETIC demonstration for an internship project: fictional firm, clients "
               "and people. Answers support, and do not replace, professional judgement.")

    if "token" not in st.session_state:
        sign_in_form()
        st.stop()

    user = st.session_state["user"]
    user_id = user["user_id"]
    with st.sidebar:
        st.header("Signed in as")
        st.markdown(f"**{user['name']}**  \n{user.get('email') or user_id}")
        st.markdown(f"**Role:** {user['role']}")
        st.markdown("**Engagements:** " + (", ".join(user["engagements"]) or "none"))
        st.divider()
        st.caption("Try: sign in as different users and ask about a client file to see access control.")
        if st.button("Clear conversation"):
            st.session_state.pop(f"history-{user_id}", None)
            st.rerun()
        if st.button("Sign out"):
            sign_out()

    history: list[dict] = st.session_state.setdefault(f"history-{user_id}", [])
    for turn in history:
        with st.chat_message("user"):
            st.markdown(turn["question"])
        with st.chat_message("assistant"):
            st.markdown(turn["final"]["text"])
            render_answer(turn["final"], user_id)

    question = st.chat_input("Ask about SAs, firm policies, checklists, deadlines or your engagements")
    if question:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            final = ask_streaming(question)
            if final is not None:
                history.append({"question": question, "final": final})
                render_answer(final, user_id)


if __name__ == "__main__":
    main()
