import json,os,re,shlex,signal,subprocess,time,shutil
from pathlib import Path
root=Path('/home/riceawa/Desktop/code/MCBE-AI-Agent/mcbe-ws-sdk')
e=root/'docs/plans/ws-sdk-refactor/records/R18-automation'
orphans=[]
for p in Path('/proc').iterdir():
    if not p.name.isdigit():continue
    try:
        cwd=str((p/'cwd').resolve());args=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode()
        if cwd.startswith('/tmp/r18-matrix/') and args.startswith('node '):
            orphans.append({'pid':int(p.name),'cwd':cwd,'args':args});os.kill(int(p.name),signal.SIGTERM)
    except (OSError,UnicodeError):pass
print('Remaining orphan nodes:',orphans,flush=True)
results=[]
for v in ['311','312','313','314']:
    failures=re.findall(r'^FAILED (\S+)',(e/f'pytest-py{v}.log').read_text(),re.M)
    shutil.copy2(e/f'.coverage-py{v}',e/f'.coverage-appended-py{v}')
    args=[f'/tmp/r18-envs/py{v}/bin/python','-m','pytest','-p','no:cacheprovider','--cov=mcbe_ws_sdk','--cov-append','--cov-report=term-missing','-q',*failures]
    env=os.environ.copy();env.update(PYTHONPATH='',MCBE_REQUIRE_CROSS_LANGUAGE='1',COVERAGE_FILE=str(e/f'.coverage-appended-py{v}'))
    with (e/'commands-retry-cov.log').open('a') as log:log.write(f'cwd=/tmp/r18-matrix/py{v} PYTHONPATH=\'\' MCBE_REQUIRE_CROSS_LANGUAGE=1 COVERAGE_FILE={env["COVERAGE_FILE"]} {shlex.join(args)}\n')
    start=time.monotonic()
    with (e/f'retry-cov-py{v}.log').open('w') as log:p=subprocess.run(args,cwd=f'/tmp/r18-matrix/py{v}',env=env,stdout=log,stderr=subprocess.STDOUT)
    item=dict(python=v,failed_nodes=failures,exit_code=p.returncode,seconds=round(time.monotonic()-start,2));results.append(item);(e/'retry-cov-results.json').write_text(json.dumps(results,indent=2)+'\n');print(item,flush=True)
