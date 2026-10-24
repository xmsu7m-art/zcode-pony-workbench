# -*- coding: utf-8 -*-
"""Emit a realistic event burst through the REAL hook.py (same binary the hook
runner invokes) so the dashboard can be seen switching to live mode."""
import json, subprocess, sys, time, io
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
PY = sys.executable
HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hook.py')
SID = 'demo-burst'

def emit(name, payload, wait):
    payload['session_id'] = SID
    payload['hook_event_name'] = name
    p = subprocess.run([PY, HOOK, name], input=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                       capture_output=True, timeout=20)
    print(name, p.returncode)
    time.sleep(wait)

emit('UserPromptSubmit', {'prompt': '家里路由器断网了,帮我排查并写一份报告'}, 3)
emit('PostToolUse', {'tool_name': 'Bash', 'tool_input': {'description': '扫描局域网网段 192.168.6.0/24'}}, 3)
emit('PostToolUseFailure', {'tool_name': 'Bash', 'tool_input': {'description': '端口探测'}, 'tool_response': {'error': '连接超时,改用简化探测'}}, 3)
emit('PreToolUse', {'tool_name': 'Agent', 'tool_input': {'subagent_type': 'general-purpose', 'description': '并行探测 4 台设备'}}, 3)
emit('PostToolUse', {'tool_name': 'Read', 'tool_input': {'file_path': 'output/探测结果.md'}}, 3)
emit('PostToolUse', {'tool_name': 'Agent', 'tool_input': {'subagent_type': 'general-purpose'}, 'tool_response': '4 台设备均无常见端口开放,符合移动设备特征'}, 3)
emit('PostToolUse', {'tool_name': 'Write', 'tool_input': {'file_path': 'output/局域网网络排查报告.md'}}, 3)
# clearly-labeled demo finisher: token total injected directly (real sessions get it from the transcript)
import os
line = json.dumps({'ev': 'turn_end', 'agent': 'main', 'sid': SID, 'tokens': 132400,
                   'text': '(演示注入的 token 总量)', 'ts': int(time.time() * 1000)}, ensure_ascii=False) + '\n'
fd = os.open(r'C:\Users\Administrator\.zcode\workbench\events.jsonl', os.O_APPEND | os.O_CREAT | os.O_WRONLY)
os.write(fd, line.encode('utf-8')); os.close(fd)
print('done')
