const assert = require('assert');
const {EventEmitter} = require('events');
const {waitForChildExit} = require('./dev-supervisor.cjs');

class FakeChild extends EventEmitter {
  constructor({pid=1234, killed=false, exitCode=null}={}) { super(); this.pid=pid; this.killed=killed; this.exitCode=exitCode; }
  finish(code=0) { this.exitCode=code; this.killed=true; this.emit('exit',code,null); }
}

async function run() {
  const child=new FakeChild(); let fallback=0;
  const first=waitForChildExit(child,30,()=>fallback++);
  const second=waitForChildExit(child,30,()=>fallback++);
  child.finish(0);
  assert.strictEqual(await first,'exited'); assert.strictEqual(await second,'exited'); assert.strictEqual(fallback,0);

  const startupFailure=new FakeChild({killed:true,exitCode:1});
  assert.strictEqual(await waitForChildExit(startupFailure,10,()=>fallback++),'already-exited');

  const unexpected=new FakeChild(); let unexpectedFallback=0;
  const pending=waitForChildExit(unexpected,30,()=>unexpectedFallback++); unexpected.finish(1);
  assert.strictEqual(await pending,'exited'); assert.strictEqual(unexpectedFallback,0);

  const timed=new FakeChild(); let timedFallback=0;
  assert.strictEqual(await waitForChildExit(timed,5,()=>timedFallback++),'timeout'); assert.strictEqual(timedFallback,1);

  const stale=new FakeChild({killed:true,exitCode:0});
  assert.strictEqual(await waitForChildExit(stale,5,()=>{throw Error('stale fallback')}),'already-exited');
  console.log('supervisor lifecycle fake-child tests passed');
}
run().catch(error=>{console.error(error);process.exitCode=1});
