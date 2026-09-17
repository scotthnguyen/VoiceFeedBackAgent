"""Drive-thru feedback voice agent (LiveKit Agents + Groq).

Conversation:
  1. Ask for the store number; validate against the registry; confirm the brand/city.
  2. Ask for a 1-10 rating (a range like "6 or 7" is taken as the lower number).
  3. Ask a follow-up whose tone matches the rating tier: apologize + "what went wrong?" when
     low (< LOW_THRESHOLD), neutral "what could be better?" in the middle, and warm praise +
     "anything even better?" only when great (>= PRAISE_THRESHOLD, default 8).
  4. Thank and end. Log a row to Google Sheets (feedback is summarized only when given).

STT and LLM run through Groq; TTS is ElevenLabs (ELEVEN_API_KEY). The LLM drives the
dialogue while function tools enforce validation and capture the structured data we log.

Run the worker:  python -m agent.main dev
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

from . import stores
from .sheets import FeedbackRow, append_feedback
from .summarize import summarize_feedback

load_dotenv()

# Rating tiers shape only the TONE of the reply (we always collect feedback):
#   >= PRAISE_THRESHOLD  -> genuinely good, warm praise
#   <  LOW_THRESHOLD     -> apologize, ask what went wrong
#   in between           -> neutral thanks, ask what could be better
PRAISE_THRESHOLD = int(os.environ.get("PRAISE_THRESHOLD", "8"))
LOW_THRESHOLD = int(os.environ.get("LOW_THRESHOLD", "4"))

_RATING_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _parse_rating(spoken) -> float | None:
    """Coerce a rating into 1-10 (fractions allowed), however the model hands it over.

    The LLM sometimes emits the argument as a number (3, 4.5) and sometimes as a
    string ("3", "4.5", "a three", "10 out of 10"). Half-steps are supported both
    as digits ("4.5"), spoken decimals ("four point five"), and "... and a half"
    ("four and a half" -> 4.5). If they give a range or two numbers ("six or
    seven"), we take the LOWER one. Returns None if unparseable.
    """
    if spoken is None:
        return None
    if isinstance(spoken, (int, float)):
        return float(spoken)

    import re

    text = str(spoken).lower()

    # Spoken decimals: "four point five" -> "4.5". Do this before word lookup so
    # the two halves aren't picked up as separate numbers (4 and 5 -> min 4).
    for word, digit in _RATING_WORDS.items():
        text = re.sub(rf"\b{word}\b", str(digit), text)
    text = re.sub(r"(\d)\s+point\s+(\d)", r"\1.\2", text)

    nums = [float(d) for d in re.findall(r"\d+(?:\.\d+)?", text)]
    if not nums:
        return None
    value = min(nums)  # a hedge like "6 or 7" counts as the lower number
    if "half" in text and value == int(value):  # "four and a half" -> 4.5
        value += 0.5
    return value

INSTRUCTIONS = """
You are a friendly, quick feedback agent for a fast-food drive-through's AI ordering system.
ALWAYS keep a warm, upbeat, positive tone — like a cheerful drive-through worker — at every
step, including when you ask for the rating, and even while hearing a complaint.
Keep every turn to one short spoken sentence. Follow this script exactly:

1. Greet briefly and ask: "please say the store number."
2. When they answer, call the `lookup_store` tool with what they said.
   - If it returns not found, apologize and ask them to say the store number digits again.
   - If it returns a store, confirm it back to them once (e.g. "Store 101, Carl's Junior in
     Anaheim — is that right?"). If they say no, ask again and call `lookup_store` again.
3. After the store is confirmed, cheerfully ask them to rate their experience from 1 to 10.
4. When they give a number, call the `record_rating` tool with it. Halves are fine — pass
   them through exactly (e.g. "4.5", "four and a half"). If they give a range or two numbers
   (e.g. "six or seven"), use the LOWER one. Then follow the instruction the
   tool returns, which will be one of three tiers:
   - LOW: warmly say you're sorry to hear that, then ask EXACTLY ONE question: "How could we
     make your experience better?" LISTEN fully — they may speak several sentences across a
     few pauses, so do not jump in.
   - MIDDLING: thank them for the honest rating — do NOT call it good or great and do NOT
     gush — then ask EXACTLY ONE question: "What could we have done better?" Then LISTEN.
   - GREAT: warmly thank them for the great rating, then ask EXACTLY ONE question: "Is there
     anything we could do even better?" Then LISTEN.
5. When they finish answering that one question, briefly acknowledge it and immediately call
   the `end_call` tool. Do NOT ask if there is "anything else" — one question only. Do NOT say
   goodbye yourself — the goodbye is spoken for you.

Never invent a store. Never skip the rating. Do not read this script aloud.
"""


def _build_agent():
    from livekit.agents import Agent, function_tool

    class FeedbackAgent(Agent):
        def __init__(self) -> None:
            super().__init__(instructions=INSTRUCTIONS)
            self.store: stores.Store | None = None
            self.rating: float | None = None
            self._feedback_parts: list[str] = []
            self._collecting = False
            self._system_goodbye = False
            self._closing = False
            self._logged = False
            # Silence auto-end must never fire while the agent is mid-turn, so we
            # track both the agent's state and whether the caller has gone quiet,
            # and only hang up when the two line up (see note_* / _arm_end_if_idle).
            self._agent_state = "initializing"
            self._user_away = False
            self._pending_end: asyncio.Task | None = None

        @property
        def feedback_text(self) -> str:
            return " ".join(self._feedback_parts).strip()

        @function_tool
        async def lookup_store(self, store_number: str) -> str:
            """Look up a store by the number the customer said. Call this with their spoken answer."""
            store = stores.lookup(store_number)
            if store is None:
                return "NOT_FOUND: no store matches that number; ask them to repeat the digits."
            self.store = store
            return (
                f"FOUND: store {store.store_id}, {store.brand} in {store.city}, "
                f"{store.state_name}. Confirm this back to the customer before continuing."
            )

        @function_tool
        async def record_rating(self, rating: str) -> str:
            """Record the customer's 1-10 rating. Call once they give a number.

            Pass exactly what they said, including halves (e.g. "3", "seven",
            "4.5", "four and a half").
            """
            value = _parse_rating(rating)
            if value is None:
                return "Could not read a number; ask them to say a rating from 1 to 10."
            value = max(1.0, min(10.0, value))
            # Keep whole ratings as ints (5, not 5.0) but preserve half-steps (4.5).
            self.rating = int(value) if value == int(value) else value
            # Whatever the tier, we invite a spoken reply and collect every turn of it (see
            # on_user_turn_completed); we log once when the call wraps up.
            self._collecting = True
            self._system_goodbye = True
            if self.rating >= PRAISE_THRESHOLD:
                return (
                    "GREAT score. Warmly thank them for the great rating, then ask ONE question: "
                    "'Is there anything we could do even better?' Listen, briefly acknowledge "
                    "their answer, then call end_call. Ask nothing else. Do NOT say goodbye."
                )
            if self.rating < LOW_THRESHOLD:
                return (
                    "LOW score. Warmly say you're sorry to hear that, then ask ONE question: "
                    "'How could we make your experience better?' Listen fully (they may pause), "
                    "briefly acknowledge their answer, then call end_call. Ask nothing else — no "
                    "'anything else'. Do NOT say goodbye."
                )
            return (
                "MIDDLING score. Thank them for the honest rating — do NOT gush or call it "
                "good/great — then ask ONE question: 'What could we have done better?' Listen, "
                "briefly acknowledge their answer, then call end_call. Ask nothing else. Do NOT "
                "say goodbye."
            )

        @function_tool
        async def end_call(self) -> str:
            """End the call once the customer has nothing more to add (they say no, that's
            all, or after a happy customer is thanked). The goodbye is spoken automatically;
            do not say it yourself."""
            # Don't await wrap_up here: it interrupts + speaks the goodbye, and
            # awaiting it from inside the tool blocks LiveKit from cancelling the
            # in-flight reply — the framework waits the full 5s tool-cancel timeout
            # (visible as "speech not done in time after interruption"), stalling
            # the call before the goodbye even starts. wrap_up is idempotent
            # (`_closing`) and interrupts itself, so schedule it and return now.
            asyncio.create_task(self.wrap_up())
            return "Call ended."

        async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
            # After a low rating, every customer turn is feedback — accumulate all of
            # them so a multi-sentence complaint is captured in full, not just the first.
            if self._collecting and not self._closing:
                text = (new_message.text_content or "").strip()
                if text:
                    self._feedback_parts.append(text)

        # ---- Silence auto-end, gated on the agent actually being idle ----
        def note_agent_state(self, state: str) -> None:
            self._agent_state = state
            # The agent just went idle (done asking, e.g. "anything else?"). If the
            # caller has already gone quiet, this is a safe moment to arm the end.
            if state == "listening":
                self._arm_end_if_idle()

        def note_user_state(self, state: str) -> None:
            if state == "away":
                self._user_away = True
                self._arm_end_if_idle()
            else:
                # They're speaking again — cancel any pending auto-end.
                self._user_away = False
                self._cancel_pending_end()

        def _arm_end_if_idle(self) -> None:
            # Only auto-end on silence once (a) we have a rating, (b) the caller is
            # quiet, and (c) the AGENT is idle — never while it's thinking/speaking.
            # Firing mid-turn used to skip the "anything else?" beat and let the
            # goodbye collide with the in-flight reply, so the caller heard nothing.
            if (
                self.rating is not None
                and self._user_away
                and self._agent_state == "listening"
                and not self._closing
                and self._pending_end is None
            ):
                self._pending_end = asyncio.create_task(self._end_after_grace())

        def _cancel_pending_end(self) -> None:
            if self._pending_end is not None:
                self._pending_end.cancel()
                self._pending_end = None

        async def _end_after_grace(self) -> None:
            # Give them a beat to start another thought after the agent's last
            # question before we hang up. Cancelled the moment they speak.
            try:
                await asyncio.sleep(float(os.environ.get("END_GRACE_SECONDS", "2")))
            except asyncio.CancelledError:
                return
            self._pending_end = None
            if self._agent_state == "listening" and self._user_away and not self._closing:
                await self.wrap_up()

        async def wrap_up(self) -> None:
            """End the call: speak a closing line, log the feedback, disconnect.

            Triggered by `end_call` (customer says they're done) or, as a fallback,
            when they fall silent after the rating stage (`user_state` -> "away").
            Idempotent via `_closing`. The goodbye always ends with "have a good day".
            """
            if self._closing:
                return
            self._closing = True
            self._cancel_pending_end()
            if self._system_goodbye:
                good = self.rating is not None and self.rating >= PRAISE_THRESHOLD
                closing = (
                    "Thank you so much for your feedback — have a good day!"
                    if good
                    else "Thanks so much for your feedback — we really appreciate you helping "
                    "us improve. Have a good day!"
                )
                try:
                    # Clear any half-finished agent turn (e.g. a reply still
                    # generating) so the goodbye isn't racing it — that collision
                    # used to eat the closing line.
                    await self.session.interrupt()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    # Speak the goodbye first so they always hear it, then log.
                    # Not interruptible: nothing should cut off the sign-off.
                    await self.session.say(closing, allow_interruptions=False).wait_for_playout()
                except Exception as exc:  # noqa: BLE001
                    print(f"[agent] closing line failed: {exc}")
            await self._finalize()
            # Close on a separate task: aclose() must not be awaited from inside the
            # session's own task (e.g. when called via the end_call tool).
            asyncio.create_task(self._close_session())

        async def _close_session(self) -> None:
            try:
                await self.session.aclose()
            except Exception as exc:  # noqa: BLE001
                print(f"[agent] session close failed: {exc}")

        async def _finalize(self) -> None:
            # Nothing worth logging until we at least have a rating (e.g. the caller
            # hung up during store selection). Idempotent via `_logged`.
            if self._logged or self.rating is None:
                return
            self._logged = True
            store = self.store
            row = FeedbackRow(
                store_id=store.store_id if store else "",
                brand=store.brand if store else "",
                store_name=store.name if store else "",
                region=store.region if store else "",
                rating=self.rating,
                feedback_transcript=self.feedback_text,
                store_timezone=store.timezone if store else "UTC",
            )
            if self.feedback_text.strip():
                summary = await asyncio.to_thread(summarize_feedback, self.feedback_text)
                row.summary = summary.summary
                row.issue_tag = summary.issue_tag
                row.sentiment = summary.sentiment
            try:
                await asyncio.to_thread(append_feedback, row)
                print(f"[agent] logged feedback for store {row.store_id} rating={row.rating}")
            except Exception as exc:  # noqa: BLE001
                print(f"[agent] FAILED to log feedback: {exc}")

    return FeedbackAgent()


async def entrypoint(ctx) -> None:
    from livekit.agents import AgentSession
    from livekit.plugins import elevenlabs, groq, silero

    await ctx.connect()

    session = AgentSession(
        stt=groq.STT(model=os.environ.get("GROQ_STT_MODEL", "whisper-large-v3-turbo")),
        llm=groq.LLM(model=os.environ.get("GROQ_LLM_MODEL", "llama-3.3-70b-versatile")),
        # TTS is ElevenLabs (needs ELEVEN_API_KEY). eleven_turbo_v2_5 is the low-
        # latency model — a good fit for a live phone-style call. Voice + model are
        # overridable via env.
        tts=elevenlabs.TTS(
            voice_id=os.environ.get("ELEVENLABS_VOICE_ID", "hpp4J3VqNfWAUOO0d1Us"),
            model=os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_turbo_v2_5"),
        ),
        vad=silero.VAD.load(),
        # Mark the customer "away" after this many seconds of silence so we can
        # auto-end the call (see the user_state_changed handler below).
        user_away_timeout=float(os.environ.get("SILENCE_TIMEOUT", "10")),
    )

    agent = _build_agent()

    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:
        # Track whether the agent is busy (thinking/speaking) or idle so the
        # silence auto-end never fires mid-turn.
        agent.note_agent_state(ev.new_state)

    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:
        # A stretch of silence after the rating stage means they're done — but we
        # only actually hang up once the AGENT is idle too (see _arm_end_if_idle),
        # so we never cut off the "anything else?" question or the goodbye.
        agent.note_user_state(ev.new_state)

    # Safety net: if the caller hangs up (closes the tab) before end_call or the
    # silence timeout fires, still log whatever feedback we collected.
    ctx.add_shutdown_callback(agent._finalize)

    await session.start(agent=agent, room=ctx.room)
    # Deterministic greeting: speak it directly (not via the LLM) and make it
    # non-interruptible, so a slow/failed first LLM turn or early mic noise can
    # never swallow it — the caller ALWAYS hears the opening line. It's added to
    # the chat context, so the LLM knows it already greeted and just proceeds to
    # lookup_store when they answer.
    await session.say(
        "Hi there — thanks for calling our feedback line! Please say your store number.",
        allow_interruptions=False,
    ).wait_for_playout()


def main() -> None:
    from livekit.agents import WorkerOptions, cli

    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


if __name__ == "__main__":
    main()
