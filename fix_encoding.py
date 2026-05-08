#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚠️  此脚本已废弃（DEPRECATED）—— 不再需要手动运行！

原用途：修复双重 UTF-8 编码导致的乱码（即 UTF-8 字节被错误当作 latin-1 解码后再保存）。

根本原因已修复：
1. generate_quiz.py 中已强制设置 resp.encoding = 'utf-8'，防止 requests 库因响应头缺失 charset
   而错误使用 latin-1 解码，从源头杜绝双重编码。
2. generate_quiz.py 写入 quiz_data.json 时已改用 utf-8-sig（带 BOM），给浏览器明确信号。
3. index.html 加载 JSON 时已改用 ArrayBuffer + TextDecoder('utf-8')，强制 UTF-8 解码，
   不受浏览器自动编码检测影响。

如果历史原因需要此脚本，保留如下代码，但强烈建议不要对已经是正确 UTF-8 的文件运行，
否则会产生新的乱码。
"""

import json

with open('quiz_data.json', 'r', encoding='utf-8') as f:
    content = f.read()

# 仅当文件确实包含双重 UTF-8 编码的 latin-1 字符时才有效
fixed = content.encode('latin-1').decode('utf-8')

with open('quiz_data.json', 'w', encoding='utf-8') as f:
    f.write(fixed)

# 验证
with open('quiz_data.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print('Date:', data['date'])
print('Q1 stem:', data['questions'][0]['stem'][:60])
print('Q1 expl:', data['questions'][0]['explanation'][:60])
print('Fix applied successfully.')
print('\n⚠️  提醒：此脚本已废弃，上述操作仅作兼容保留。')
