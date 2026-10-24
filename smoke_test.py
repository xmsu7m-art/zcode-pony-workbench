import json
import os, subprocess, sys, io
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
PY = sys.executable
HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hook.py')

cases = [
    ("SessionStart", {"session_id": "s1", "hook_event_name": "SessionStart", "source": "startup"}),
    ("UserPromptSubmit", {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": "把小马工作台接到真实会话数据"}),
    ("PreToolUse", {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Agent",
                    "tool_input": {"subagent_type": "Explore", "description": "扫描日志里的状态机"}}),
    ("PostToolUse", {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                     "tool_input": {"command": "dir", "description": "枚举安装目录"}, "tool_response": "ok"}),
    ("PostToolUseFailure", {"session_id": "s1", "hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                            "tool_input": {"command": "ping"}, "tool_response": {"error": "超时"}}),
    ("PostToolUse", {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Agent",
                     "tool_input": {"subagent_type": "Explore"}, "tool_response": "命中动画状态机"}),
    ("Stop", {"session_id": "s1", "hook_event_name": "Stop", "transcript_path": "", "last_message": "收工"}),
    ("Stop", None),
]

for name, payload in cases:
    inp = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else b"{bad json"
    p = subprocess.run([PY, HOOK, name], input=inp, capture_output=True, timeout=20)
    print(f"{name}: exit={p.returncode} stdout={p.stdout[:60]!r} stderr={p.stderr[:80]!r}")

print("--- events.jsonl ---")
with io.open(r'C:\Users\Administrator\.zcode\workbench\events.jsonl', encoding='utf-8') as f:
    for line in f:
        print(line.rstrip()[:200])
