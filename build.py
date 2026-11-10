"""Build both independent, single-file Windows executables and release ZIPs."""
from pathlib import Path
import hashlib
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parent
def main():
    if sys.platform!='win32':raise SystemExit('Windows에서 실행해 주세요.')
    for script,name,guide in [('patcher.py','LocalRealm-Korean-Patcher','Patcher.txt'),('reviewer.py','LocalRealm-Translation-Reviewer','Reviewer.txt')]:
        subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--windowed','--noupx','--icon','NONE',
                        '--name',name,'--distpath',str(ROOT/'dist'),'--workpath',str(ROOT/'build'/name),
                        '--specpath',str(ROOT/'build'),'--add-data',str(ROOT/'resources')+';resources',str(ROOT/'src'/script)],cwd=ROOT,check=True)
        exe=ROOT/'dist'/(name+'.exe')
        with zipfile.ZipFile(ROOT/'dist'/(name+'-v1.0.0-win-x64.zip'),'w',zipfile.ZIP_DEFLATED) as z:
            z.write(exe,exe.name);z.write(ROOT/'resources'/guide,'사용안내.txt')
            z.write(ROOT/'THIRD_PARTY_NOTICES.md','THIRD_PARTY_NOTICES.md')
            for f in sorted((ROOT/'resources/third_party').rglob('*')):
                if f.is_file():z.write(f,'third_party/'+str(f.relative_to(ROOT/'resources/third_party')))
    lines=[]
    for f in sorted((ROOT/'dist').glob('*')):
        if f.suffix in ('.exe','.zip'):lines.append(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+f.name)
    (ROOT/'dist/SHA256SUMS.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('두 EXE와 릴리스 ZIP 생성 완료:',ROOT/'dist')
if __name__=='__main__':main()
