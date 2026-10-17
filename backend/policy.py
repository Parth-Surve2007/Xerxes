from pathlib import Path
import sys
import os
import re
import logging
logger=logging.getLogger(__name__)
class Decision:
    def __init__(self,allowed,reason): self.allowed=allowed; self.reason=reason
class Policy:
    SECRET_NAMES={'.env','.env.local','id_rsa','id_ed25519','credentials.json','secrets.json','xerxes_key.pem'}
    ALLOWED_COMMANDS={'pytest backend/tests -q':[sys.executable,'-m','pytest','backend/tests','-q'],'npm run build':[('npm.cmd' if os.name=='nt' else 'npm'),'run','build'],'python -m pytest backend/tests -q':[sys.executable,'-m','pytest','backend/tests','-q']}
    def __init__(self,root,grants=None): self.root=Path(root).resolve(); self.grants=grants
    def roots(self): return [self.root]+[Path(x['path']).resolve() for x in (self.grants.list() if self.grants else [])]
    def resolve(self,relative):
        if not relative: raise ValueError('path is required')
        relative_text=str(relative).replace('\\','/')
        if relative_text.casefold()=='desktop' or relative_text.casefold().startswith('desktop/'):
            desktop=[root for root in self.roots() if root.name.casefold()=='desktop']
            if not desktop: raise ValueError('Desktop is not an approved folder')
            relative=str(desktop[0]/relative_text.split('/',1)[1]) if '/' in relative_text else str(desktop[0])
        candidate=(self.root/relative).resolve()
        if not any(candidate==root or root in candidate.parents for root in self.roots()): raise ValueError('path is outside approved folders')
        if any(part in self.SECRET_NAMES or part.lower().endswith(('.pem','.key')) for part in candidate.parts): raise ValueError('protected credential path denied')
        return candidate
    def normalize_pdf_args(self,args):
        source=dict(args or {})
        def choose(canonical,*aliases):
            value=source.get(canonical)
            if value is not None: return value
            for alias in aliases:
                if source.get(alias) is not None: return source[alias]
            return None
        name=choose('filename','file_name','name') or 'Welcome_to_Xerxes.pdf'
        destination=choose('destination','location','folder','directory','path') or '.'
        if isinstance(destination,str) and destination.casefold()=='desktop': destination='desktop'
        text=choose('text','content')
        normalized={'filename':name,'destination':destination}
        if text is not None: normalized['text']=text
        if source.get('title') is not None: normalized['title']=source['title']
        return normalized
    def pdf_target(self,args):
        normalized=self.normalize_pdf_args(args)
        name=normalized['filename']
        if not isinstance(name,str) or Path(name).name!=name or not name.lower().endswith('.pdf') or not re.fullmatch(r'[\w .()\-]+\.pdf',name,re.I): raise ValueError('invalid PDF filename')
        destination=self.resolve(normalized['destination'])
        if not destination.is_dir(): raise ValueError('PDF destination folder not found or no longer authorized')
        return normalized,destination/name
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
                normalized,target=self.pdf_target(args)
                if target.exists(): raise ValueError('destination file already exists')
                if not isinstance(normalized.get('text'),str) or not normalized['text'].strip(): raise ValueError('PDF text is required')
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
        terms=[term for term in re.split(r'[\s,]+',query.casefold()) if term]
        ignored={'node_modules','.git','dist','.venv','__pycache__','.pytest_cache'}
        inaccessible_roots=excluded=unauthorized=traversal_errors=0
        for root in roots:
            if not root.is_dir(): inaccessible_roots+=1; continue
            for p in root.rglob('*'):
                if any(part.casefold() in ignored for part in p.parts): excluded+=1; continue
                try: resolved=p.resolve(); allowed=any(resolved==r or r in resolved.parents for r in self.roots())
                except OSError: traversal_errors+=1; continue
                if not allowed: unauthorized+=1; continue
                if not p.is_file() or resolved in seen: continue
                if p.suffix.lower() not in {'.py','.js','.jsx','.ts','.tsx','.md','.json','.css','.txt'}: excluded+=1; continue
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
                    traversal_errors+=1; continue
                if len(out)>=100:
                    logger.info('search_workspace mode=%s roots=%d inaccessible_roots=%d matches=%d excluded=%d unauthorized=%d traversal_errors=%d capped=true',mode,len(roots),inaccessible_roots,len(out),excluded,unauthorized,traversal_errors)
                    return out
        logger.info('search_workspace mode=%s roots=%d inaccessible_roots=%d matches=%d excluded=%d unauthorized=%d traversal_errors=%d',mode,len(roots),inaccessible_roots,len(out),excluded,unauthorized,traversal_errors)
        return out
