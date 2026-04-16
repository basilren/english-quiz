// push_quiz.js — 将 today_quiz.json 更新到 index.html 并推送 GitHub Pages
// 用法：node push_quiz.js
var fs = require('fs');
var path = require('path');
var { execSync } = require('child_process');

var QUIZ_PATH = 'C:/Users/basilren/.workbuddy/skills/english-tutor/references/today_quiz.json';
var REPO_PATH = 'C:/Users/basilren/WorkBuddy/english-quiz';

try {
  // 1. Read quiz
  var quiz = JSON.parse(fs.readFileSync(QUIZ_PATH, 'utf8'));
  console.log('Quiz date:', quiz.date, 'session:', quiz.session_number, 'questions:', quiz.questions.length);

  // 2. Pull latest
  execSync('git pull', { cwd: REPO_PATH, stdio: 'inherit' });

  // 3. Update index.html - only replace the quizData line, preserve everything after
  var htmlPath = path.join(REPO_PATH, 'index.html');
  var html = fs.readFileSync(htmlPath, 'utf8');
  // Match from "var quizData = {" to the end of the JSON object + ";"
  // Use a more precise pattern: match the line starting with "var quizData"
  var lines = html.split('\n');
  var found = false;
  for (var i = 0; i < lines.length; i++) {
    if (lines[i].trimStart().startsWith('var quizData = {')) {
      lines[i] = 'var quizData = ' + JSON.stringify(quiz) + ';';
      found = true;
      break;
    }
  }
  if (!found) {
    console.error('ERROR: Could not find quizData line in index.html');
    process.exit(1);
  }
  html = lines.join('\n');
  
  // Update day counter
  var dayMatch = html.match(/\u7B2C (\d+) \u5929/);
  var dayNum = dayMatch ? parseInt(dayMatch[1]) + 1 : 1;
  var dateStr = quiz.date.replace(/(\d{4})-(\d{2})-(\d{2})/, '$1\u5E74$2\u6708$3\u65E5');
  html = html.replace(
    /\u7B2C \d+ \u5929 \u00B7 \u7B2C \d+ \u7EC4 \u00B7 \d{4}\u5E74\d{1,2}\u6708\d{1,2}\u65E5/,
    '\u7B2C ' + dayNum + ' \u5929 \u00B7 \u7B2C ' + quiz.session_number + ' \u7EC4 \u00B7 ' + dateStr
  );
  
  fs.writeFileSync(htmlPath, html, 'utf8');
  console.log('Updated index.html, day', dayNum);

  // 4. Git push
  execSync('git add .', { cwd: REPO_PATH, stdio: 'inherit' });
  execSync('git commit -m "Day ' + dayNum + ' (' + quiz.date.slice(5) + '): auto-push quiz"', { cwd: REPO_PATH, stdio: 'inherit' });
  execSync('git push', { cwd: REPO_PATH, stdio: 'inherit' });
  console.log('Pushed to GitHub Pages!');
} catch (e) {
  console.error('Error:', e.message);
  process.exit(1);
}
