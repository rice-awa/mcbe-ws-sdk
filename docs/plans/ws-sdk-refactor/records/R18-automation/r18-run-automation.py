import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import threading
import time

ROOT = Path('/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk')
CANDIDATE = Path('/tmp/r18-candidate')
EVIDENCE = ROOT / 'docs/plans/ws-sdk-refactor/records/R18-automation'
EVIDENCE.mkdir(exist_ok=True)
ENVS = Path('/tmp/r18-envs')
RESULTS = []
LOCK = threading.Lock()
STOP = threading.Event()

def heartbeat():
    while not STOP.wait(240):
        subprocess.run(['orca','orchestration','send','--from','term_8f504cb6-fea9-424f-a5ba-ddaae2379f0e','--type','heartbeat','--subject','R18 automation active','--task-id','task_721f99998937','--dispatch-id','ctx_542e5bed2aae','--phase','reviewing'], check=False, capture_output=True)

threading.Thread(target=heartbeat, daemon=True).start()

def run(name, args, cwd=CANDIDATE, env=None):
    actual_env = os.environ.copy()
    actual_env['PYTHONPATH'] = ''
    actual_env['PIP_INDEX_URL'] = 'https://pypi.tuna.tsinghua.edu.cn/simple'
    if env:
        actual_env.update(env)
    command = shlex.join(args)
    with (EVIDENCE/'commands.log').open('a') as commands:
        commands.write(f'{time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())} cwd={cwd} PYTHONPATH=\'\' PIP_INDEX_URL={actual_env["PIP_INDEX_URL"]} ' + ' '.join(f'{k}={shlex.quote(v)}' for k,v in (env or {}).items()) + ' ' + command+'\n')
    start=time.monotonic()
    with (EVIDENCE/f'{name}.log').open('w') as log:
        result=subprocess.run(args,cwd=cwd,env=actual_env,stdout=log,stderr=subprocess.STDOUT)
    record={'name':name,'command':command,'cwd':str(cwd),'env':env or {},'exit_code':result.returncode,'seconds':round(time.monotonic()-start,2)}
    with LOCK:
        RESULTS.append(record)
        (EVIDENCE/'results.json').write_text(json.dumps(RESULTS,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(record),flush=True)
    return result.returncode==0

try:
    if CANDIDATE.exists():
        raise RuntimeError('candidate directory already exists; preserve previous evidence')
    shutil.copytree(ROOT,CANDIDATE,ignore=shutil.ignore_patterns('.git','.venv','node_modules','dist','lib','site','.pytest_dist_cache','__pycache__','.pytest_cache','.mypy_cache','.ruff_cache','.coverage*','R18-automation','R18-performance'))
    (CANDIDATE/'addon/node_modules').symlink_to(ROOT/'addon/node_modules',target_is_directory=True)
    hashes={str(p.relative_to(CANDIDATE)):hashlib.sha256(p.read_bytes()).hexdigest() for base in ['src','tests','tools','.github/workflows'] for p in sorted((CANDIDATE/base).rglob('*')) if p.is_file()}
    hashes['pyproject.toml']=hashlib.sha256((CANDIDATE/'pyproject.toml').read_bytes()).hexdigest()
    (EVIDENCE/'snapshot-sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
    for version in ['311','312','313','314']:
        py=str(ENVS/f'py{version}/bin/python')
        run(f'environment-py{version}',[py,'-c','import sys,platform; from importlib.metadata import distributions; print(sys.version); print(platform.platform()); print("\\n".join(sorted(f"{d.metadata[\"Name\"]}=={d.version}" for d in distributions())))'])
    py=str(ENVS/'py311/bin/python')
    for name,args in [
        ('v1-generated',[py,'tools/generate_protocol_assets.py','--check']),
        ('protocol-names',[py,'tools/check_protocol_names.py']),
        ('v2-generated',[py,'tools/generate_v2_assets.py','--check']),
        ('ruff',[py,'-m','ruff','check','--no-cache','src','tests','examples','tools/check_dist.py','tools/probe_installed_contract.py']),
        ('mypy',[py,'-m','mypy','--no-incremental','src']),
        ('build',[py,'-m','build','--sdist','--wheel','--outdir',str(CANDIDATE/'dist')]),
        ('twine',[py,'-m','twine','check',str(CANDIDATE/'dist/mcbe_ws_sdk-0.2.1-py3-none-any.whl'),str(CANDIDATE/'dist/mcbe_ws_sdk-0.2.1.tar.gz')]),
        ('check-dist',[py,'tools/check_dist.py',str(CANDIDATE/'dist')]),
    ]:
        if not run(name,args):
            raise RuntimeError(f'{name} failed; inspect log before continuing')
    import tarfile
    unpacked=CANDIDATE/'unpacked'
    unpacked.mkdir()
    with tarfile.open(CANDIDATE/'dist/mcbe_ws_sdk-0.2.1.tar.gz') as archive:
        archive.extractall(unpacked,filter='data')
    if not run('rebuild-sdist',[py,'-m','build','--wheel','--outdir',str(CANDIDATE/'rebuilt'),str(unpacked/'mcbe_ws_sdk-0.2.1')]):
        raise RuntimeError('sdist rebuild failed')
    artifacts={str(p.relative_to(CANDIDATE)):{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for directory in ['dist','rebuilt'] for p in sorted((CANDIDATE/directory).iterdir())}
    (EVIDENCE/'artifacts.json').write_text(json.dumps(artifacts,indent=2)+'\n')
    installed_cwd=Path('/tmp/r18-installed-cwd')
    installed_cwd.mkdir(exist_ok=True)
    for label in ['dist','rebuilt']:
        target=ENVS/f'installed-{label}'
        if not run(f'venv-{label}',['uv','venv','--python','3.11',str(target)]):
            raise RuntimeError('venv creation failed')
        if not run(f'install-{label}',['uv','pip','install','--python',str(target/'bin/python'),'--index-url','https://pypi.tuna.tsinghua.edu.cn/simple','--constraint','/tmp/r18-dependencies.txt',str(CANDIDATE/label/'mcbe_ws_sdk-0.2.1-py3-none-any.whl')]):
            raise RuntimeError('wheel install failed')
        if not run(f'installed-contract-{label}',[str(target/'bin/python'),str(CANDIDATE/'tools/probe_installed_contract.py')],cwd=installed_cwd):
            raise RuntimeError('installed wheel probe failed')
        run(f'installed-environment-{label}',['uv','pip','freeze','--python',str(target/'bin/python')],cwd=installed_cwd)
    from concurrent.futures import ThreadPoolExecutor
    def full_suite(version):
        matrix=Path('/tmp/r18-matrix')/f'py{version}'
        shutil.copytree(CANDIDATE,matrix,ignore=shutil.ignore_patterns('node_modules','dist','rebuilt','unpacked','.pytest_dist_cache','__pycache__','.mypy_cache','.ruff_cache','.coverage*'))
        (matrix/'addon/node_modules').symlink_to(ROOT/'addon/node_modules',target_is_directory=True)
        return run(f'pytest-py{version}',[str(ENVS/f'py{version}/bin/python'),'-m','pytest','-p','no:cacheprovider','--cov=mcbe_ws_sdk','--cov-report=term-missing','-q'],cwd=matrix,env={'MCBE_REQUIRE_CROSS_LANGUAGE':'1','COVERAGE_FILE':str(EVIDENCE/f'.coverage-py{version}')})
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(full_suite,['311','312','313','314']))
    # Match the dedicated CI compatibility gate, rather than multiplying the full suite.
    requirements=Path('/tmp/r18-dependencies-ws.txt')
    requirements.write_text('\n'.join(line for line in Path('/tmp/r18-dependencies.txt').read_text().splitlines() if not line.startswith('websockets=='))+'\n')
    for version in ['12','14','16']:
        target=ENVS/f'ws{version}'
        run(f'venv-ws{version}',['uv','venv','--python','3.11',str(target)])
        if not run(f'install-ws{version}',['uv','pip','install','--python',str(target/'bin/python'),'--index-url','https://pypi.tuna.tsinghua.edu.cn/simple','-r',str(requirements),f'websockets~={version}.0']):
            continue
        run(f'environment-ws{version}',['uv','pip','freeze','--python',str(target/'bin/python')])
        run(f'pytest-ws{version}',[str(target/'bin/python'),'-m','pytest','-p','no:cacheprovider','tests/unit/test_server_facade.py','tests/unit/test_delivery.py','tests/unit/test_server_entry.py','-q'],env={'COVERAGE_FILE':str(EVIDENCE/f'.coverage-ws{version}'),'MCBE_REQUIRE_CROSS_LANGUAGE':'1'})
finally:
    STOP.set()
