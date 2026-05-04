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
# Kimi 官方 API 模型名（2026-04 校验有效）
# kimi-k2.6 - 最新最强
# kimi-k2.5 - 稳定版（也可用）
# kimi-k2-turbo-preview - 高速版 60-100 tokens/s
DEFAULT_MODEL = "kimi-k2.5"
MAX_RETRIES = 3
TIMEOUT = 300  # K2 生成 20 道题较慢，给足时间

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "index.html"
QUIZ_DATA_JSON = REPO_ROOT / "quiz_data.json"
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


def get_recent_errors(days: int = 10, max_items: int = 12) -> list:
    """提取最近 N 天的错题记录（默认 10 天，最多 12 条）"""
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

**重要提示：你必须生成 EXACTLY 20 道题，不多不少！**

## 学生档案
```json
{json.dumps(student, ensure_ascii=False, indent=2)}
```

## 知识点大纲
{kb}

## 近期错题（最近10天，去重后）
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
- type: 题型，必须是以下之一："grammar"(语法选择)、"vocabulary"(词汇)、"cloze"(完形填空)、"reading"(阅读理解)、"fill"(选句子填空)
- knowledge_point: 知识点标签，如 "present_perfect"、"word_meaning"、"cloze_general"、"detail"、"sentence_fill" 等
- difficulty: 难度 1-3，1=基础，2=中等，3=较难
- stem: 题干文本
- options: 选项数组，每个选项以 "A. "、"B. "、"C. "、"D. " 开头
- correct_answer: 正确答案字母，如 "A"、"B"、"C"、"D"
- explanation: 详细讲解，用中文，包含：为什么选这个、为什么不选其他、解题技巧/口诀
- label: 题型中文标签，如 "语法题"、"词汇题"、"完形填空"、"阅读-细节理解"、"短文填空" 等

### 分组题型的特殊要求

**完形填空 (cloze)**：5 题共用一篇短文。
- 需要添加 group: "cloze_1" 和 group_passage: 完整短文
- 每道题的 stem 只显示题号，如 "(8)___"
- ⚠️ **文章词数要求：220-260 词**（对标深圳中考中等难度），主题贴近日常生活/校园/科普，行文流畅自然
- 挖空密度：每 15-20 词一空，考查语境推理、固定搭配、逻辑关系
- 难度分布：3 空为难度 2（中等），2 空为难度 1（基础）

**阅读理解 (reading)**：4 题共用一篇短文。
- 需要添加 group: "reading_1" 和 group_passage: 完整短文
- 4 道题分别覆盖：detail(细节)、inference(推理)、word_guessing(词义猜测)、main_idea(主旨)
- ⚠️ **文章词数要求：260-320 词**（对标深圳中考 B 篇中等难度），题材可为记叙文或说明文
- 文章中如出现超纲词汇需加注释（括号内给中文释义）
- 难度分布：detail 和 word_guessing 为难度 1-2，inference 和 main_idea 为难度 2-3

**选句子填空 (fill)**：4 题共用一篇短文，考查句子衔接与逻辑关系。
- 需要添加 group: "fill_1" 和 group_passage: 完整短文
- 给出 5 个备选句子（如连接词、过渡句、逻辑关系句等），选项标记 A-E
- 4 道题的 correct_answer 分别对应其中 4 个正确句子
- 每道题的 options 数组都包含相同的 5 个选项
- stem 只显示题号，如 "(17)___"
- 重点：选项必须是**完整的句子或连接词**（如 However / Then / In addition / As a result / On the other hand），考查段落衔接和逻辑关系，不能是单词/词组变形
- ⚠️ **文章词数要求：180-220 词**（对标深圳中考中等难度），逻辑结构清晰，有明显的段落衔接信号

### 题目分布（确保总数为 20 题）
- 语法选择：4 题
- 词汇：3 题  
- 完形填空：5 题（共用1篇短文）
- 阅读理解：4 题（共用1篇短文）
- 选句子填空：4 题（共用1篇短文）

**重要：** 请严格确保总题数为 20 题（4+3+5+4+4=20）。

### 其他要求
1. 整体难度对标**深圳中考中等水平**（约 70% 中等题 + 20% 基础题 + 10% 较难题）
2. **语法选择题**：4 题中 1 题难度 1（基础），2 题难度 2（中等），1 题难度 3（较难）
3. **词汇题**：3 题中 1 题难度 1，2 题难度 2
4. 优先覆盖学生的 weak_points 和近期错题涉及的知识点
5. 完形和阅读的文章主题可以从学生 preferred_topics 中选择
6. 讲解要详细，用中文，包含：为什么选这个、为什么不选其他、解题技巧/口诀
7. 选项要有一定干扰性，不能太明显
8. 所有文本使用 UTF-8 编码，不要有特殊不可见字符

### 最后提醒
1. **必须生成恰好 20 道题**（4语法 + 3词汇 + 5完形 + 4阅读 + 4选句子填空）
2. 每道题必须有完整的字段
3. 完形、阅读、选句子填空必须共用短文

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
        "temperature": 1,
        "max_tokens": 12000,
        "stream": True,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log(f"调用 Kimi API (尝试 {attempt}/{MAX_RETRIES}, stream 模式)...")
            resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT, stream=True)
            if resp.status_code != 200:
                log(f"API 返回 {resp.status_code}: {resp.text[:500]}")
            resp.raise_for_status()

            # 强制使用 UTF-8 解码，防止 requests 因响应头缺失 charset 而错误使用 latin-1，
            # 导致中文被双重编码（UTF-8 字节被当作 latin-1 解码后再存为 UTF-8）。
            resp.encoding = "utf-8"

            # 流式累积 content
            content_parts = []
            chunk_count = 0
            last_log_time = time.time()
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        piece = delta.get("content", "")
                        if piece:
                            content_parts.append(piece)
                            chunk_count += 1
                            # 每 10 秒打一次进度
                            if time.time() - last_log_time >= 10:
                                total_chars = sum(len(x) for x in content_parts)
                                log(f"  已接收 {chunk_count} 块, {total_chars} 字符...")
                                last_log_time = time.time()
                    except json.JSONDecodeError:
                        continue

            full_content = "".join(content_parts)
            log(f"流式接收完成，共 {len(full_content)} 字符")
            return parse_quiz_json(full_content)
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
    original = text.strip()
    log(f"原始响应长度: {len(original)} 字符")
    # 保存原始响应到临时文件以便调试
    debug_file = REPO_ROOT / "scripts" / "debug_raw_response.txt"
    with open(debug_file, "w", encoding="utf-8") as f:
        f.write(original)
    log(f"原始响应已保存至: {debug_file}")

    # 去掉可能的 markdown 代码块标记
    text = original
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

    # 尝试解析，如果失败，尝试修复常见的 JSON 格式错误
    for attempt in range(2):
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            log(f"JSON 解析失败 (尝试 {attempt+1}/2): {e}")
            if attempt == 0:
                # 第一次尝试失败，尝试修复常见的格式问题
                # 1. 修复尾随逗号（对象或数组最后一个元素后的逗号）
                text = re.sub(r',\s*}', '}', text)
                text = re.sub(r',\s*]', ']', text)
                # 2. 修复缺失逗号：在 } 后跟 { 或 " 前缺少逗号？这里简单处理，可能不通用
                # 3. 修复未转义的控制字符
                # 由于不确定具体错误，我们记录修复后的文本并继续
                debug_file_fixed = REPO_ROOT / "scripts" / "debug_fixed_response.txt"
                with open(debug_file_fixed, "w", encoding="utf-8") as f:
                    f.write(text)
                log(f"修复后响应已保存至: {debug_file_fixed}")
            else:
                # 第二次尝试仍然失败，抛出异常并附带更多上下文
                log(f"最终解析失败，原始响应前500字符: {original[:500]}")
                raise


def validate_quiz(quiz: dict) -> bool:
    """验证生成的题目格式是否正确"""
    required_top = {"date", "session_number", "questions"}
    if not required_top.issubset(quiz.keys()):
        log(f"验证失败: 缺少顶层字段，实际有 {set(quiz.keys())}")
        return False

    questions = quiz.get("questions", [])
    if len(questions) != 20:
        log(f"验证失败: 题目数量不是 20，实际 {len(questions)}")
        # 输出每道题的 id 和类型，便于调试
        for i, q in enumerate(questions):
            log(f"  第{i+1}: id={q.get('id', 'N/A')}, type={q.get('type', 'N/A')}")
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

    # 检查 id 是否从 1 到 20 连续（允许不连续但警告）
    ids = [q.get("id") for q in questions]
    if sorted(ids) != list(range(1, 21)):
        log(f"警告: id 不连续或重复: {ids}")
    else:
        log("id 连续 ✓")

    log("题目格式验证通过 ✓")
    return True


def update_quiz_data(quiz: dict) -> None:
    """将 quiz 数据写入 quiz_data.json（index.html 通过 XHR 加载）"""
    # 清洗 quiz：去掉所有字符串中的裸回车
    def _sanitize(obj):
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize(x) for x in obj]
        if isinstance(obj, str):
            return obj.replace("\r\n", "\n").replace("\r", "\n")
        return obj

    safe_quiz = _sanitize(quiz)

    # 写入外部 JSON 文件，使用 utf-8-sig（带 BOM），给浏览器/编辑器明确的 UTF-8 信号
    with open(QUIZ_DATA_JSON, "w", encoding="utf-8-sig") as f:
        json.dump(safe_quiz, f, ensure_ascii=False, indent=2)

    log(f"已更新 {QUIZ_DATA_JSON} ({len(json.dumps(safe_quiz, ensure_ascii=False))} chars, UTF-8-BOM)")


def git_commit(today: str, session: int) -> None:
    """git add / commit / push"""
    import subprocess

    cmds = [
        ["git", "add", "quiz_data.json"],
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


def prune_old_errors(days: int = 10) -> int:
    """清理 results.json 中超过 N 天的错题记录，返回清理条数。

    仅清理 errors 数组，sessions（成绩统计）保留全部用于长期趋势分析。
    """
    data = load_json(DATA_JSON)
    errors = data.get("errors", [])
    if not errors:
        return 0

    cutoff = (datetime.now(BJ_TZ) - timedelta(days=days)).strftime("%Y-%m-%d")
    kept = [e for e in errors if e.get("date", "") >= cutoff]
    removed = len(errors) - len(kept)

    if removed > 0:
        data["errors"] = kept
        with open(DATA_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return removed


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

    # 清理 10 天前的旧错题（保持 prompt 精简 + 文件不膨胀）
    removed = prune_old_errors(days=10)
    if removed > 0:
        log(f"已清理 {removed} 条超过 10 天的旧错题")

    errors = get_recent_errors()
    log(f"加载到 {len(errors)} 条近期错题")

    # 构建 prompt 并调用 API（最多重试 3 次）
    max_attempts = 3
    quiz = None
    for attempt in range(1, max_attempts + 1):
        log(f"=== 生成尝试 {attempt}/{max_attempts} ===")
        prompt = build_prompt(student, kb, errors, today, session)
        log("Prompt 长度: {} chars".format(len(prompt)))
        try:
            quiz = call_kimi(prompt, api_key, model)
        except Exception as e:
            log(f"API 调用失败: {e}")
            if attempt == max_attempts:
                raise
            continue
        
        # 验证
        if validate_quiz(quiz):
            log(f"第 {attempt} 次尝试验证通过 ✓")
            break
        else:
            log(f"第 {attempt} 次尝试验证失败")
            if attempt == max_attempts:
                log("所有重试均失败，终止")
                sys.exit(1)
            # 等待片刻后重试
            wait_sec = 2 ** attempt  # 指数退避
            log(f"等待 {wait_sec} 秒后重试...")
            time.sleep(wait_sec)
    
    # 确保 quiz 不为 None（理论上不会）
    if quiz is None:
        log("严重错误: 未生成有效 quiz")
        sys.exit(1)

    # 确保日期和 session 正确
    quiz["date"] = today
    quiz["session_number"] = session

    # 更新 quiz_data.json
    update_quiz_data(quiz)

    # git 提交交给外层 workflow 统一处理（避免 identity 未配置等问题）
    # 本地手动运行时，如需自动推送可取消下面两行注释
    # git_commit(today, session)

    log("✅ 每日出题完成！（commit/push 由 workflow 处理）")


if __name__ == "__main__":
    main()
