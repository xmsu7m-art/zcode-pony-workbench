# -*- coding: utf-8 -*-
"""ZCode workbench event collector.

Registered as a `process` hook for UserPromptSubmit / PreToolUse(Agent) /
PostToolUse / PostToolUseFailure / Stop / SessionStart. Reads the hook JSON
payload from stdin, appends one event line to events.jsonl, prints nothing,
always exits 0. Never blocks the session: any failure is swallowed.
"""
import io
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
EVENTS = os.path.join(BASE, "events.jsonl")
STATE = os.path.join(BASE, "state.json")
MAX_TEXT = 110
MAX_EVENTS_BYTES = 4 * 1024 * 1024


def clean(s, limit=MAX_TEXT):
    if s is None:
        return ""
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s[:limit]


def append_event(obj):
    obj["ts"] = int(time.time() * 1000)
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    fd = os.open(EVENTS, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def trim_events():
    try:
        if os.path.getsize(EVENTS) > MAX_EVENTS_BYTES:
            with io.open(EVENTS, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            keep = lines[-2000:]
            tmp = EVENTS + ".tmp"
            with io.open(tmp, "w", encoding="utf-8") as f:
                f.writelines(keep)
            os.replace(tmp, EVENTS)
    except Exception:
        pass


def load_state():
    try:
        with io.open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    try:
        with io.open(STATE, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:
        pass


def token_total(transcript_path, session_id):
    """Incrementally sum input+output tokens from the session transcript JSONL."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return None
    st = load_state()
    ent = st.get(session_id) or {"off": 0, "total": 0}
    off, total = int(ent.get("off", 0)), int(ent.get("total", 0))
    try:
        size = os.path.getsize(transcript_path)
        if off > size:  # transcript replaced (new session on same id?) — restart
            off, total = 0, 0
        with io.open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            if off:
                f.seek(off)
            while True:
                line = f.readline()
                if not line:
                    break
                off = f.tell()
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                u = (d.get("message") or {}).get("usage") or {}
                if u:
                    total += int(u.get("input_tokens", 0) or 0)
                    total += int(u.get("output_tokens", 0) or 0)
    except Exception:
        return total or None
    st[session_id] = {"off": off, "total": total}
    # keep state file small
    if len(st) > 40:
        try:
            oldest = sorted(st.items(), key=lambda kv: kv[1].get("off", 0))[: len(st) - 20]
            for k, _ in oldest:
                st.pop(k, None)
        except Exception:
            pass
    save_state(st)
    return total


def main():
    evname = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        raw = sys.stdin.read() or "{}"
        data = json.loads(raw)
    except Exception:
        data = {}
    sid = clean(data.get("session_id", ""), 60) or "s"
    tool = clean(data.get("tool_name", ""), 40)
    tin = data.get("tool_input") or {}
    resp = data.get("tool_response")
    try:
        if evname == "SessionStart":
            append_event({"ev": "session", "agent": "main", "text": clean(data.get("source", "startup"), 40)})
        elif evname == "UserPromptSubmit":
            append_event({"ev": "prompt", "agent": "main", "sid": sid, "text": clean(data.get("prompt", ""))})
        elif evname == "PreToolUse" and tool in ("Agent", "Task"):
            stype = clean((tin or {}).get("subagent_type", "subagent"), 40) or "subagent"
            desc = clean((tin or {}).get("description", ""), 60)
            append_event({"ev": "agent_start", "agent": stype, "sid": sid, "text": desc})
        elif evname == "PostToolUse":
            if tool in ("Agent", "Task"):
                stype = clean((tin or {}).get("subagent_type", "subagent"), 40) or "subagent"
                preview = ""
                if isinstance(resp, str):
                    preview = resp
                elif isinstance(resp, dict):
                    preview = resp.get("content") or resp.get("text") or ""
                append_event({"ev": "agent_end", "agent": stype, "sid": sid,
                              "text": clean(preview, 80) or "子代理返回"})
            else:
                append_event({"ev": "tool", "agent": "main", "sid": sid, "tool": tool, "status": "ok",
                              "text": clean(str(tin.get("description") or tin.get("file_path")
                                            or tin.get("command") or tin.get("skill") or tin.get("pattern")
                                            or tool), 90)})
        elif evname == "PostToolUseFailure":
            err = ""
            if isinstance(resp, dict):
                err = resp.get("error") or ""
            elif resp is not None:
                err = str(resp)
            append_event({"ev": "tool", "agent": "main", "sid": sid, "tool": tool, "status": "fail",
                          "text": clean(err or "调用失败", 90)})
        elif evname == "Stop":
            total = token_total(data.get("transcript_path"), sid)
            append_event({"ev": "turn_end", "agent": "main", "sid": sid,
                          "tokens": total, "text": clean(data.get("last_message", "") or "", 60)})
        trim_events()
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
