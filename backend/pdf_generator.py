from pathlib import Path
import re

def create_pdf(path, text, title=None):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    if not isinstance(text,str) or not text.strip(): raise ValueError('PDF text is required')
    if len(text)>1_000_000: raise ValueError('PDF text exceeds size limit')
    if path.exists(): raise FileExistsError('destination file already exists')
    font='Helvetica'
    for candidate in [Path('C:/Windows/Fonts/arial.ttf'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]:
        if candidate.is_file():
            name='XerxesUnicode'; pdfmetrics.registerFont(TTFont(name,str(candidate))); font=name; break
    path.parent.mkdir(parents=True,exist_ok=True)
    c=canvas.Canvas(str(path),pagesize=letter); c.setTitle(title or path.stem); width,height=letter
    c.setFont(font,12); y=height-54
    for line in text.splitlines() or ['']:
        for start in range(0,max(1,len(line)),95):
            if y<48: c.showPage(); c.setFont(font,12); y=height-54
            c.drawString(54,y,line[start:start+95]); y-=16
    c.save()
    if not path.is_file() or path.stat().st_size<100 or path.read_bytes()[:5]!=b'%PDF-': raise RuntimeError('generated PDF validation failed')
    return {'path':str(path),'bytes':path.stat().st_size,'verified':True}
