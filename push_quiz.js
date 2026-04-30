// push_quiz.js — 已废弃（DEPRECATED）
//
// 原用途：将 english-tutor skill 生成的 today_quiz.json 内联到 index.html 并推送 GitHub Pages。
//
// 替代方案：
//   现在统一使用 scripts/generate_quiz.py 生成题目，它会：
//   1. 调用 Kimi API 生成 20 道题目
//   2. 写入 quiz_data.json（UTF-8-BOM）
//   3. 由 workflow 自动 git commit / push
//
// 如果仍需手动推送，请直接运行：
//   cd C:/Users/basilren/WorkBuddy/english-quiz
//   git add quiz_data.json index.html
//   git commit -m "update quiz"
//   git push
//
// 注意：index.html 现在通过 ArrayBuffer + TextDecoder('utf-8') 加载 quiz_data.json，
// 不再将 JSON 内联到 HTML 中，因此此脚本的替换逻辑已失效。

console.log('⚠️  push_quiz.js 已废弃。请使用 scripts/generate_quiz.py 生成并推送题目。');
process.exit(0);
