import io, json, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import sys
CFG = os.path.join(os.path.expanduser('~'), '.zcode', 'cli', 'config.json')
HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hook.py')
PY = sys.executable

with io.open(CFG, encoding='utf-8') as f:
    cfg = json.load(f)

def h(event):
    return {"type": "process", "command": PY, "args": [HOOK, event], "timeoutMs": 5000}

hooks = {
    "enabled": True,
    "timeoutMs": 5000,
    "events": {
        "SessionStart": [{"hooks": [h("SessionStart")]}],
        "UserPromptSubmit": [{"hooks": [h("UserPromptSubmit")]}],
        "PreToolUse": [{"matcher": "^Agent$", "hooks": [h("AgentStart")]}],
        "PostToolUse": [{"hooks": [h("PostToolUse")]}],
        "PostToolUseFailure": [{"hooks": [h("PostToolUseFailure")]}],
        "Stop": [{"hooks": [h("Stop")]}],
    },
}

cfg["hooks"] = hooks
with io.open(CFG, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)

print("config written")
print(json.dumps(json.load(io.open(CFG, encoding='utf-8')), ensure_ascii=False)[:400])
