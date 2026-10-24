@echo off
rem ZCode 小马工作台:widget.py 会自动拉起 server.py 并打开右侧停靠侧边栏。
rem 开机自启(Run 键 ZCodeWorkbench)已指向 widget.py;手动双击本文件亦可。
rem 移除自启:删除注册表 HKCU\Software\Microsoft\Windows\CurrentVersion\Run 下的 ZCodeWorkbench 项。
start "" pythonw.exe "%~dp0widget.py"
