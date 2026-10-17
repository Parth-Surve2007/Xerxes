const {spawn} = require('child_process');
const electron = require('electron');
const env = {...process.env};
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(electron, ['.'], {stdio:['pipe','inherit','inherit'], env});
let shuttingDown = false;
function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  if (!child.killed) child.stdin.write('XERXES_SHUTDOWN\n');
  setTimeout(() => { if (!child.killed) child.kill(signal); process.exit(0); }, 1500);
}
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
child.on('exit', (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
