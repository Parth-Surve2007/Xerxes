from pathlib import Path
import sys
import os
import re
class Decision:
    def __init__(self,allowed,reason): self.allowed=allowed; self.reason=reason
class Policy:
    SECRET_NAMES={'.env','.env.local','id_rsa','id_ed25519','credentials.json','secrets.json','xerxes_key.pem'}
    ALLOWED_COMMANDS={'pytest backend/tests -q':[sys.executable,'-m','pytest','backend/tests','-q'],'npm run build':[('npm.cmd' if os.name=='nt' else 'npm'),'run','build'],'python -m pytest backend/tests -q':[sys.executable,'-m','pytest','backend/tests','-q']}
    def __init__(self,root,grants=None): self.root=Path(root).resolve(); self.grants=grants
    def roots(self): return [self.root]+[Path(x['path']).resolve() for x in (self.grants.list() if self.grants else [])]
    def resolve(self,relative):
        if not relative: raise ValueError('path is required')
        candidate=(self.root/relative).resolve()
        if not any(candidate==root or root in candidate.parents for root in self.roots()): raise ValueError('path is outside approved folders')
        if any(part in self.SECRET_NAMES or part.lower().endswith(('.pem','.key')) for part in candidate.parts): raise ValueError('protected credential path denied')
        return candidate
    def check(self,tool,args):
        try:
            if tool in {'list_workspace','search_workspace'}: return Decision(True,'read-only workspace operation')
            if tool=='read_file': self.resolve(args.get('path','')); return Decision(True,'read-only approved path')
            if tool=='apply_patch':
                patch_path=self.resolve(args.get('path',''))
                if patch_path.suffix.lower()=='.pdf': raise ValueError('apply_patch cannot target PDF files')
                if not isinstance(args.get('old_text'),str) or not isinstance(args.get('new_text'),str): raise ValueError('exact old_text and new_text are required')
                if args['old_text']==args['new_text']: raise ValueError('patch makes no change')
                if len(args['old_text'])>200000 or len(args['new_text'])>200000: raise ValueError('patch exceeds size limit')
                return Decision(True,'validated workspace patch; explicit approval required')
            if tool=='create_pdf':
                name=args.get('filename') or 'Welcome_to_Xerxes.pdf'
                if not isinstance(name,str) or Path(name).name!=name or not name.lower().endswith('.pdf') or not re.fullmatch(r'[\w .()\-]+\.pdf',name,re.I): raise ValueError('invalid PDF filename')
                target=self.resolve(args.get('destination','.')+'/'+name)
                if target.exists(): raise ValueError('destination file already exists')
                if not isinstance(args.get('text'),str) or not args['text'].strip(): raise ValueError('PDF text is required')
                return Decision(True,'validated PDF destination; explicit approval required')
            if tool=='run_tests': self.command(args.get('command','')); return Decision(True,'allowlisted non-privileged test command')
            return Decision(False,'tool is not enabled by deterministic policy')
        except (ValueError,FileNotFoundError) as exc: return Decision(False,str(exc))
    def command(self,command):
        if command not in self.ALLOWED_COMMANDS: raise ValueError('command is not allowlisted')
        return self.ALLOWED_COMMANDS[command]
    def list_tree(self, relative='.'):
        base=self.resolve(relative)
        if not base.is_dir(): raise ValueError('folder not found')
        out=[]
        for p in sorted(base.rglob('*')):
            try:
                resolved=p.resolve()
                if not any(resolved==root or root in resolved.parents for root in self.roots()): continue
            except OSError: continue
            if any(x in {'.git','node_modules','dist','.venv','__pycache__','.pytest_cache'} for x in p.parts): continue
            try: display=p.relative_to(self.root).as_posix()
            except ValueError: display=str(p.resolve())
            out.append({'path':display,'kind':'dir' if p.is_dir() else 'file'})
            if len(out)>=500: break
        return out
    def read_file(self,relative):
        p=self.resolve(relative)
        if not p.is_file(): raise ValueError('file not found')
        if p.stat().st_size>512*1024: raise ValueError('file exceeds read limit')
        return {'path':relative,'content':p.read_text(encoding='utf-8',errors='replace')}
    def search(self,query,relative='.',mode='content'):
        if not query or len(query)>100: raise ValueError('query is required and limited to 100 characters')
        if mode not in {'content','filename'}: raise ValueError('search mode must be content or filename')
        if relative and relative not in {'.','./'} and str(relative).casefold()=='desktop':
            desktop=[root for root in self.roots() if root.name.casefold()=='desktop']
            if desktop: relative=str(desktop[0])
        base=self.resolve(relative) if relative not in {'.','./'} else None
        if base is not None and not base.is_dir(): raise ValueError('search folder not found or no longer authorized')
        out=[]; roots=[base] if base else self.roots(); seen=set()
        terms=[term for term in query.lower().split() if term]
        ignored={'node_modules','.git','dist','.venv','__pycache__','.pytest_cache'}
        for root in roots:
            if not root.is_dir(): continue
            for p in root.rglob('*'):
                if any(part.casefold() in ignored for part in p.parts): continue
                try: resolved=p.resolve(); allowed=any(resolved==r or r in resolved.parents for r in self.roots())
                except OSError: continue
                if not allowed or not p.is_file() or resolved in seen or p.suffix.lower() not in {'.py','.js','.jsx','.ts','.tsx','.md','.json','.css','.txt'}: continue
                seen.add(resolved)
                try:
                    filename=p.name.lower()
                    if mode=='filename':
                        if any(term in filename for term in terms):
                            try: display=p.relative_to(self.root).as_posix()
                            except ValueError: display=str(resolved)
                            out.append({'path':display,'match':'filename'})
                        continue
                    for n,line in enumerate(p.read_text(encoding='utf-8',errors='ignore').splitlines(),1):
                        if query.lower() in line.lower() or any(term in line.lower() for term in terms):
                            try: display=p.relative_to(self.root).as_posix()
                            except ValueError: display=str(resolved)
                            out.append({'path':display,'line':n,'text':line[:300]})
                except OSError:
                    continue
                if len(out)>=100: return out
        return out
