var fs = require('fs');
var html = fs.readFileSync('index.html', 'utf8');
var m = html.match(/var quizData = (\{.*?\});/s);
if (!m) { console.log('NO MATCH'); process.exit(1); }
try {
  var d = JSON.parse(m[1]);
  console.log('OK: date=' + d.date + ' questions=' + d.questions.length);
  // Check each question
  d.questions.forEach(function(q, i) {
    if (!q.stem) console.log('Q' + (i+1) + ': MISSING STEM');
    if (!q.options || q.options.length < 2) console.log('Q' + (i+1) + ': MISSING OPTIONS');
    if (!q.correct_answer) console.log('Q' + (i+1) + ': MISSING ANSWER');
  });
} catch (e) {
  console.log('PARSE ERROR:', e.message);
  var pos = parseInt((e.message.match(/position (\d+)/) || [])[1] || 0);
  if (pos) console.log('NEAR:', m[1].substring(Math.max(0, pos - 100), pos + 100));
}
