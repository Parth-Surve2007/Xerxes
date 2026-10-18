from pathlib import Path
import sys
import os
import re
import logging
import hashlib
import os
logger=logging.getLogger(__name__)
class Decision:
    def __init__(self,allowed,reason): self.allowed=allowed; self.reason=reason
class Policy:
    SECRET_NAMES={'.env','.env.local','id_rsa','id_ed25519','credentials.json','secrets.json','xerxes_key.pem'}
    ALLOWED_COMMANDS={'pytest backend/tests -q':[sys.executable,'-m','pytest','backend/tests','-q'],'npm run build':[('npm.cmd' if os.name=='nt' else 'npm'),'run','build'],'python -m pytest backend/tests -q':[sys.executable,'-m','pytest','backend/tests','-q']}
    EXCLUDED_DIRS={'.git','node_modules','dist','build','intermediates','.venv','venv','__pycache__','.pytest_cache','.mypy_cache','.ruff_cache','.tox','.next','coverage'}
    TEXT_EXTENSIONS={'.py','.js','.jsx','.ts','.tsx','.md','.json','.css','.txt'}
    def __init__(self,root,grants=None): self.root=Path(root).resolve(); self.grants=grants
    def roots(self): return [self.root]+[Path(x['path']).resolve() for x in (self.grants.list() if self.grants else [])]
    def desktop_root(self):
        desktops=[root for root in self.roots() if root.name.casefold()=='desktop']
        if not desktops: raise ValueError('Desktop is not an approved folder')
        return desktops[0]
    def is_desktop_root(self, value):
        try:
            return self.resolve(value)==self.desktop_root()
        except (ValueError, OSError):
            return False
    def is_desktop_path(self, value):
        try:
            candidate=self.resolve(value)
            desktop=self.desktop_root()
            return candidate==desktop or desktop in candidate.parents
        except (ValueError, OSError):
            return False
    def resolve(self,relative):
        if not relative: raise ValueError('path is required')
        relative_text=str(relative).replace('\\','/')
        if relative_text.casefold()=='desktop' or relative_text.casefold().startswith('desktop/'):
            desktop=self.desktop_root()
            relative=str(desktop/relative_text.split('/',1)[1]) if '/' in relative_text else str(desktop)
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
            if tool=='create_file': self.file_target(args); return Decision(True,'validated new file destination; explicit approval required')
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
        ignored={name.casefold() for name in self.EXCLUDED_DIRS}
        for current, directories, filenames in os.walk(base, topdown=True):
            directories[:]=[name for name in directories if name.casefold() not in ignored]
            for name in sorted(directories+filenames, key=str.casefold):
                p=Path(current)/name
                try: resolved=p.resolve()
                except OSError: continue
                if not any(resolved==root or root in resolved.parents for root in self.roots()): continue
                try: display=p.relative_to(self.root).as_posix()
                except ValueError: display=str(resolved)
                out.append({'path':display,'kind':'dir' if p.is_dir() else 'file'})
                if len(out)>=500: return out
        return out
    def read_file(self,relative):
        p=self.resolve(relative)
        if not p.is_file(): raise ValueError('file not found')
        if p.stat().st_size>512*1024: raise ValueError('file exceeds read limit')
        return {'path':relative,'content':p.read_text(encoding='utf-8',errors='replace')}
    def file_target(self,args):
        path=args.get('path','')
        if not isinstance(path,str) or not path.strip(): raise ValueError('file path is required')
        target=self.resolve(path)
        if target.exists(): raise ValueError('destination file already exists')
        if not target.name or target.name in {'.','..'}: raise ValueError('invalid destination filename')
        if not isinstance(args.get('content'),str): raise ValueError('file content is required')
        if len(args['content'])>512*1024: raise ValueError('file content exceeds size limit')
        if not target.parent.is_dir(): raise ValueError('destination folder not found or no longer authorized')
        return target
    def search(self,query,relative='.',mode='content'):
        if not query or len(query)>100: raise ValueError('query is required and limited to 100 characters')
        if mode not in {'content','filename'}: raise ValueError('search mode must be content or filename')
        if relative and relative not in {'.','./'} and str(relative).casefold()=='desktop':
            desktop=[root for root in self.roots() if root.name.casefold()=='desktop']
            if desktop: relative=str(desktop[0])
        base=self.resolve(relative) if relative not in {'.','./'} else None
        if base is not None and not base.is_dir(): raise ValueError('search folder not found or no longer authorized')
        out=[]; roots=[base] if base else self.roots(); seen=set()
        terms=[term.casefold() for term in re.findall(r'[\w.-]+',query,flags=re.UNICODE) if term]
        ignored={name.casefold() for name in self.EXCLUDED_DIRS}
        inaccessible_roots=excluded=unauthorized=traversal_errors=0
        for root in roots:
            if not root.is_dir(): inaccessible_roots+=1; continue
            for current, directories, filenames in os.walk(root, topdown=True):
                directories[:]=[name for name in directories if name.casefold() not in ignored]
                for filename in filenames:
                    p=Path(current)/filename
                    try: resolved=p.resolve(); allowed=any(resolved==r or r in resolved.parents for r in self.roots())
                    except OSError: traversal_errors+=1; continue
                    if not allowed: unauthorized+=1; continue
                    if resolved in seen: continue
                    if p.suffix.lower() not in self.TEXT_EXTENSIONS: excluded+=1; continue
                    seen.add(resolved)
                    try:
                        filename=p.name.casefold()
                        if mode=='filename':
                            exact=filename in terms or p.stem.casefold() in terms
                            partial=any(term in filename for term in terms)
                            if partial:
                                try: display=p.relative_to(self.root).as_posix()
                                except ValueError: display=str(resolved)
                                out.append({'id':'search-'+hashlib.sha1(str(resolved).casefold().encode()).hexdigest()[:12],'path':display,'match':'filename','match_type':'exact' if exact else 'substring','score':2 if exact else 1})
                            continue
                        if p.stat().st_size>512*1024: excluded+=1; continue
                        for n,line in enumerate(p.read_text(encoding='utf-8',errors='ignore').splitlines(),1):
                            if query.casefold() in line.casefold() or any(term in line.casefold() for term in terms):
                                try: display=p.relative_to(self.root).as_posix()
                                except ValueError: display=str(resolved)
                                out.append({'id':'search-'+hashlib.sha1(f'{resolved}:{n}'.casefold().encode()).hexdigest()[:12],'path':display,'line':n,'text':line[:300],'match':'content'})
                    except OSError:
                        traversal_errors+=1; continue
                    if len(out)>=100:
                        logger.info('search_workspace mode=%s roots=%d inaccessible_roots=%d matches=%d excluded=%d unauthorized=%d traversal_errors=%d capped=true',mode,len(roots),inaccessible_roots,len(out),excluded,unauthorized,traversal_errors)
                        return out
        if mode=='filename': out.sort(key=lambda item:(-item.get('score',0),item['path'].casefold()))
        logger.info('search_workspace mode=%s roots=%d inaccessible_roots=%d matches=%d excluded=%d unauthorized=%d traversal_errors=%d',mode,len(roots),inaccessible_roots,len(out),excluded,unauthorized,traversal_errors)
        return out
