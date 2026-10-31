import hashlib,json,os,shlex,shutil,subprocess,tarfile,time
from pathlib import Path
root=Path('/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk')
candidate=Path('/tmp/r18-final-candidate')
evidence=root/'docs/plans/ws-sdk-refactor/records/R18-automation'
py='/tmp/r18-envs/py311/bin/python'
results=[]
def run(name,args,cwd=candidate):
    env=os.environ.copy();env['PYTHONPATH']='';env['PIP_INDEX_URL']='https://pypi.tuna.tsinghua.edu.cn/simple'
    with (evidence/'commands-final.log').open('a') as log: log.write(f'cwd={cwd} PYTHONPATH=\'\' PIP_INDEX_URL={env["PIP_INDEX_URL"]} {shlex.join(args)}\n')
    start=time.monotonic()
    with (evidence/f'final-{name}.log').open('w') as log: p=subprocess.run(args,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT)
    result=dict(name=name,command=shlex.join(args),cwd=str(cwd),exit_code=p.returncode,seconds=round(time.monotonic()-start,2));results.append(result);(evidence/'final-results.json').write_text(json.dumps(results,indent=2)+'\n');print(result,flush=True)
    if p.returncode:raise RuntimeError(name)
shutil.copytree(root,candidate,ignore=shutil.ignore_patterns('.git','.venv','node_modules','dist','lib','site','.pytest_dist_cache','__pycache__','.pytest_cache','.mypy_cache','.ruff_cache','.coverage*','R18-automation','R18-performance'))
paths=[p for base in ['src','tests','tools','examples','addon/scripts','.github/workflows'] for p in sorted((candidate/base).rglob('*')) if p.is_file()]
paths += [candidate/name for name in ['pyproject.toml','README.md','README.zh.md','CHANGELOG.md','addon/README.md','addon/README.zh.md']]
hashes={str(p.relative_to(candidate)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
(evidence/'final-snapshot-sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
initial=json.loads((evidence/'snapshot-sha256.json').read_text());(evidence/'snapshot-delta.json').write_text(json.dumps([name for name in initial if initial[name]!=hashes.get(name)],indent=2)+'\n')
run('build',[py,'-m','build','--sdist','--wheel','--outdir',str(candidate/'dist')])
wheel='mcbe_ws_sdk-0.2.1-py3-none-any.whl';sdist='mcbe_ws_sdk-0.2.1.tar.gz'
run('twine',[py,'-m','twine','check',str(candidate/'dist'/wheel),str(candidate/'dist'/sdist)])
run('check-dist',[py,'tools/check_dist.py',str(candidate/'dist')])
with tarfile.open(candidate/'dist'/sdist) as a:
    count=sum(i.isfile() for i in a.getmembers());a.extractall(candidate/'unpacked',filter='data')
run('rebuild-sdist',[py,'-m','build','--wheel','--outdir',str(candidate/'rebuilt'),str(candidate/'unpacked/mcbe_ws_sdk-0.2.1')])
artifacts={str(p.relative_to(candidate)):{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for directory in ['dist','rebuilt'] for p in sorted((candidate/directory).iterdir())}
(evidence/'final-artifacts.json').write_text(json.dumps({'artifacts':artifacts,'sdist_files':count},indent=2)+'\n')
for label in ['dist','rebuilt']:
    target=Path(f'/tmp/r18-envs/final-installed-{label}')
    run(f'venv-{label}',['uv','venv','--python','3.11',str(target)])
    run(f'install-{label}',['uv','pip','install','--python',str(target/'bin/python'),'--index-url','https://pypi.tuna.tsinghua.edu.cn/simple','--constraint','/tmp/r18-dependencies.txt',str(candidate/label/wheel)])
    run(f'installed-contract-{label}',[str(target/'bin/python'),str(candidate/'tools/probe_installed_contract.py')],cwd=Path('/tmp/r18-installed-cwd'))
    run(f'environment-{label}',['uv','pip','freeze','--python',str(target/'bin/python')])
