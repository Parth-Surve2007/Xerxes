const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const release = path.join(root, 'release');
const application = path.join(root, 'application');
const candidates = fs.readdirSync(release)
  .filter(name => /^Xerxes-.*\.exe$/i.test(name) && !name.endsWith('.blockmap'))
  .map(name => path.join(release, name))
  .filter(file => fs.statSync(file).isFile())
  .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);

if (!candidates.length) throw new Error(`No Xerxes executable found in ${release}`);
fs.mkdirSync(application, {recursive: true});
const target = path.join(application, 'Xerxes.exe');
try { fs.rmSync(target, {force: true}); } catch (error) {
  throw new Error(`Close Xerxes before rebuilding application/Xerxes.exe: ${error.message}`);
}
fs.copyFileSync(candidates[0], target);
console.log(`Application updated: ${target}`);
