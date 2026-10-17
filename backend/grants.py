import json, os, tempfile, threading, uuid
from pathlib import Path
from datetime import datetime, timezone

class GrantStore:
    def __init__(self, path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True); self.lock=threading.RLock()
    def _read(self):
        try: return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else []
        except (OSError,ValueError): return []
    def list(self):
        with self.lock: return list(self._read())
    def add(self, folder):
        root=Path(folder).resolve(strict=True)
        if not root.is_dir() or root.parent==root: raise ValueError('folder must be an accessible non-root directory')
        with self.lock:
            items=self._read(); existing={x['path'].casefold() for x in items}
            if str(root).casefold() not in existing: items.append({'id':str(uuid.uuid4()),'path':str(root),'created_at':datetime.now(timezone.utc).isoformat()}); self._write(items)
            return next(x for x in items if x['path'].casefold()==str(root).casefold())
    def revoke(self, grant_id):
        with self.lock:
            items=self._read(); kept=[x for x in items if x.get('id')!=grant_id]
            if len(kept)==len(items): raise KeyError(grant_id)
            self._write(kept)
    def _write(self, items):
        fd,tmp=tempfile.mkstemp(prefix='.grants-',dir=str(self.path.parent),text=True)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(items,f); f.flush(); os.fsync(f.fileno())
            os.replace(tmp,self.path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
