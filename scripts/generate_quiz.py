#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动出题脚本 - 调用 Kimi K2.5 API 生成每日英语练习题

用法:
    export KIMI_API_KEY="sk-xxxxxxxx"
    python scripts/generate_quiz.py

环境变量:
    KIMI_API_KEY - Kimi API Key (必填)
    KIMI_MODEL   - 模型名称, 默认 kimi-k2.5
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# ── 配置 ──────────────────────────────────────────
API_BASE = "https://api.moonshot.cn/v1"
DEFAULT_MODEL = "kimi-k2.5"
MAX_RETRIES = 3
TIMEOUT = 120

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "index.html"
DATA_JSON = REPO_ROOT / "data" / "results.json"
PROFILE_JSON = REPO_ROOT / "scripts" / "student_profile.json"
KB_MD = REPO_ROOT / "scripts" / "knowledge_base.md"

# 北京时区
BJ_TZ = timezone(timedelta(hours=8))


def log(msg: str):
    now = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_text(path: Path) -> str:
    if not path.exists():
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def get_recent_errors(days: int = 14, max_items: int = 15) -> list:
    """提取最近 N 天的错题记录"""
    data = load_json(DATA_JSON)
    errors = data.get("errors", [])
    if not errors:
        return []

    # 按日期排序，取最近几天的
    cutoff = (datetime.now(BJ_TZ) - timedelta(days=days)).strftime("%Y-%m-%d")
    recent = [e for e in errors if e.get("date", "") >= cutoff]

    # 去重：同一知识点只保留最近一条
    seen = set()
    deduped = []
    for e in reversed(recent):
        kp = e.get("knowledge_point", "")
        if kp and kp not in seen:
            seen.add(kp)
            deduped.append(e)
        elif len(deduped) < max_items // 2:
            deduped.append(e)
        if len(deduped) >= max_items:
            break
    return list(reversed(deduped))


def get_next_session_number(today: str) -> int:
    """获取今天的下一个 session 编号"""
    data = load_json(DATA_JSON)
    sessions = data.get("sessions", [])
    today_sessions = [s["session"] for s in sessions if s.get("date") == today]
    return max(today_sessions) + 1 if today_sessions else 1


def build_prompt(student: dict, kb: str, errors: list, today: str, session: int) -> str:
    """构建给大模型的出题 prompt"""

    error_text = ""
    if errors:
        error_lines = []
        for i, e in enumerate(errors[-10:], 1):
            error_lines.append(
                f"{i}. [{e.get('label', e.get('type', ''))}] "
                f"{e.get('stem', '')[:80]}... "
                f"错选: {e.get('student_answer', '?')} | "
                f"正解: {e.get('correct_answer', '?')}"
            )
        error_text = "\n".join(error_lines)
    else:
        error_text = "暂无近期错题记录。"

    prompt = f"""你是一位资深的初中英语教师，正在为初二学生 Bosco 生成每日英语练习题。

## 学生档案
```json
{json.dumps(student, ensure_ascii=False, indent=2)}
```

## 知识点大纲
{kb}

## 近期错题（最近14天，去重后）
{error_text}

## 出题要求

请生成 **20 道**初二英语练习题，格式必须严格符合下面的 JSON Schema。

### 题目结构要求

总体结构：
{{
  "date": "{today}",
  "session_number": {session},
  "questions": [
    // 20 道题，id 从 1 到 20
  ]
}}

每道题的字段：
- id: 整数，1-20
- type: 题型，必须是以下之一："grammar"(语法选择)、"vocabulary"(词汇)、"cloze"(完形填空)、"reading"(阅读理解)、"fill"(短文填空)
- knowledge_point: 知识点标签，如 "present_perfect"、"word_meaning"、"cloze_general"、"detail"、"sentence_fill" 等
- difficulty: 难度 1-3，1=基础，2=中等，3=较难
- stem: 题干文本
- options: 选项数组，每个选项以 "A. "、"B. "、"C. "、"D. " 开头
- correct_answer: 正确答案字母，如 "A"、"B"、"C"、"D"
- explanation: 详细讲解，用中文，包含：为什么选这个、为什么不选其他、解题技巧/口诀
- label: 题型中文标签，如 "语法题"、"词汇题"、"完形填空"、"阅读-细节理解"、"短文填空" 等

### 分组题型的特殊要求

**完形填空 (cloze)**：4-5 题共用一篇短文。
- 需要添加 group: "cloze_1" 和 group_passage: 完整短文
- 每道题的 stem 只显示题号，如 "(8)___"

**阅读理解 (reading)**：4 题共用一篇短文。
- 需要添加 group: "reading_1" 和 group_passage: 完整短文
- 4 道题分别覆盖：detail(细节)、inference(推理)、word_guessing(词义猜测)、main_idea(主旨)

**短文填空 (fill)**：4 题共用一篇短文，从 5 个选项中选 4 个填入。
- 需要添加 group: "fill_1" 和 group_passage: 完整短文
- 5 个选项为 A-E，4 道题的 correct_answer 分别对应其中 4 个
- 每道题的 options 数组都包含相同的 5 个选项
- stem 只显示题号，如 "(17)___"

### 题目分布
- 语法选择：3-4 题
- 词汇：2-3 题
- 完形填空：4-5 题（共用1篇）
- 阅读理解：4 题（共用1篇）
- 短文填空：4 题（共用1篇）

### 其他要求
1. 题目要贴合初二中考难度，避免过难或过简单
2. 优先覆盖学生的 weak_points 和近期错题涉及的知识点
3. 完形和阅读的文章主题可以从学生 preferred_topics 中选择
4. 讲解要详细，用中文，适合学生自学理解
5. 选项要有一定干扰性，不能太明显
6. 所有文本使用 UTF-8 编码，不要有特殊不可见字符

### 输出格式
只输出纯 JSON，不要任何 markdown 代码块标记（不要 ```json），不要任何解释性文字，直接输出合法的 JSON 字符串。
"""
    return prompt


def call_kimi(prompt: str, api_key: str, model: str) -> dict:
    """调用 Kimi API，返回解析后的 JSON"""
    url = f"{API_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个专业的初中英语出题系统。你必须严格输出合法的 JSON，不要包含任何 markdown 代码块标记或其他说明文字。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 8000,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log(f"调用 Kimi API (尝试 {attempt}/{MAX_RETRIES})...")
            resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return parse_quiz_json(content)
        except requests.exceptions.RequestException as e:
            log(f"API 请求失败: {e}")
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt
                log(f"等待 {wait} 秒后重试...")
                time.sleep(wait)
            else:
                raise
        except (KeyError, IndexError) as e:
            log(f"API 响应解析失败: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(2)
            else:
                raise

    raise RuntimeError("所有重试均失败")


def parse_quiz_json(text: str) -> dict:
    """从模型输出中提取并解析 JSON"""
    # 去掉可能的 markdown 代码块标记
    text = text.strip()
    if text.startswith("```"):
        # 去掉开头的 ```json 或 ```
        text = re.sub(r"^```(?:json)?\s*", "", text)
        # 去掉结尾的 ```
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    # 有时模型输出会在 JSON 前后加一些文字，尝试提取 JSON 部分
    # 找第一个 { 和最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]

    return json.loads(text)


def validate_quiz(quiz: dict) -> bool:
    """验证生成的题目格式是否正确"""
    required_top = {"date", "session_number", "questions"}
    if not required_top.issubset(quiz.keys()):
        log(f"验证失败: 缺少顶层字段，实际有 {set(quiz.keys())}")
        return False

    questions = quiz.get("questions", [])
    if len(questions) != 20:
        log(f"验证失败: 题目数量不是 20，实际 {len(questions)}")
        return False

    required_q = {"id", "type", "knowledge_point", "difficulty", "stem", "options", "correct_answer", "explanation", "label"}
    for i, q in enumerate(questions):
        if not required_q.issubset(q.keys()):
            log(f"验证失败: 第 {i+1} 题缺少字段，实际有 {set(q.keys())}")
            return False
        if len(q.get("options", [])) != 4 and q.get("type") != "fill":
            log(f"验证失败: 第 {i+1} 题选项数量不是 4")
            return False
        if q.get("type") == "fill" and len(q.get("options", [])) != 5:
            log(f"验证失败: 第 {i+1} 题(fill)选项数量不是 5")
            return False

    log("题目格式验证通过 ✓")
    return True


def update_index_html(quiz: dict) -> None:
    """更新 index.html 中的 quizData"""
    if not INDEX_HTML.exists():
        raise FileNotFoundError(f"找不到 {INDEX_HTML}")

    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        content = f.read()

    # 将 quiz 对象序列化为单行 JSON（避免换行破坏 HTML）
    quiz_json = json.dumps(quiz, ensure_ascii=False, separators=(",", ":"))

    # 替换 var quizData = {...}; 这一行
    # 匹配 var quizData = 后面直到分号的内容
    pattern = r'var\s+quizData\s*=\s*\{[\s\S]*?\};'
    replacement = f'var quizData = {quiz_json};'

    new_content, count = re.subn(pattern, replacement, content, count=1)
    if count == 0:
        # 如果没匹配到，尝试更宽松的匹配
        pattern2 = r'var\s+quizData\s*=\s*\{[^}]*\}[^;]*;'
        new_content, count = re.subn(pattern2, replacement, content, count=1)

    if count == 0:
        raise RuntimeError("无法在 index.html 中找到 var quizData 的定义")

    with open(INDEX_HTML, "w", encoding="utf-8") as f:
        f.write(new_content)

    log(f"已更新 {INDEX_HTML}")


def git_commit(today: str, session: int) -> None:
    """git add / commit / push"""
    import subprocess

    cmds = [
        ["git", "add", "index.html"],
        ["git", "commit", "-m", f"auto: daily quiz {today} session-{session}"],
        ["git", "push", "origin", "main"],
    ]

    for cmd in cmds:
        log(f"执行: {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if result.returncode != 0:
            # commit 可能因为没有变化而失败，忽略
            if cmd[1] == "commit" and "nothing to commit" in result.stdout.lower():
                log("没有变更需要提交")
                continue
            if cmd[1] == "commit" and "nothing added" in result.stdout.lower():
                log("没有变更需要提交")
                continue
            err = result.stderr or result.stdout
            raise RuntimeError(f"命令失败 ({' '.join(cmd)}): {err}")
        if result.stdout:
            log(result.stdout.strip())


def main():
    api_key = os.environ.get("KIMI_API_KEY", "")
    if not api_key:
        log("错误: 环境变量 KIMI_API_KEY 未设置")
        sys.exit(1)

    model = os.environ.get("KIMI_MODEL", DEFAULT_MODEL)
    log(f"使用模型: {model}")

    today = datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    session = get_next_session_number(today)
    log(f"生成日期: {today}, session: {session}")

    # 加载上下文
    student = load_json(PROFILE_JSON)
    kb = load_text(KB_MD)
    errors = get_recent_errors()
    log(f"加载到 {len(errors)} 条近期错题")

    # 构建 prompt 并调用 API
    prompt = build_prompt(student, kb, errors, today, session)
    log("Prompt 长度: {} chars".format(len(prompt)))

    quiz = call_kimi(prompt, api_key, model)

    # 验证
    if not validate_quiz(quiz):
        log("题目验证失败，终止")
        sys.exit(1)

    # 确保日期和 session 正确
    quiz["date"] = today
    quiz["session_number"] = session

    # 更新 index.html
    update_index_html(quiz)

    # git 提交
    git_commit(today, session)

    log("✅ 每日出题完成并推送!")


if __name__ == "__main__":
    main()
