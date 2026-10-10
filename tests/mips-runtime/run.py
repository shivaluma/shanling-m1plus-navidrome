#!/usr/bin/env python3
"""Run isolated MIPS service and original-userland plugin probes."""
import json
import hashlib
import pathlib
import subprocess
import sys
ROOT=pathlib.Path(__file__).resolve().parents[2]
HERE=pathlib.Path(__file__).resolve().parent
subprocess.run(['docker','build','-t','m1plus-runtime-probe:local','-f',str(HERE/'Dockerfile'),str(ROOT)],check=True)
container=['docker','run','--rm','--network','none','--ulimit','core=0','-v',str(ROOT)+':/work','m1plus-runtime-probe:local']
subprocess.run(container+['python3','/work/tests/mips-runtime/probe.py'],check=True)
subprocess.run([sys.executable,str(HERE/'build_native_probe.py')],check=True)
result=subprocess.run(container+['qemu-mipsel','-cpu','P5600','-L','/work/tests/mips-runtime/sysroot','-E','LD_LIBRARY_PATH=/work/tests/mips-runtime/sysroot/lib:/work/tests/mips-runtime/sysroot/usr/lib','/work/tests/mips-runtime/native-probe','/work/dist/libnavidrome_ui.so'],capture_output=True,text=True,timeout=20)
(HERE/'native-run.log').write_text(result.stdout+result.stderr)
if result.returncode!=0 or 'PASS original MIPS libc/loader dlopen plugin' not in result.stdout or 'required firmware symbol missing: main_loop' not in result.stderr:
    raise SystemExit('Native MIPS plugin load/fail-closed probe failed; see native-run.log')
preload=subprocess.run(container+['qemu-mipsel','-cpu','P5600','-L','/work/tests/mips-runtime/sysroot','-E','LD_LIBRARY_PATH=/work/tests/mips-runtime/sysroot/lib:/work/tests/mips-runtime/sysroot/usr/lib','-E','LD_PRELOAD=/work/dist/libnavidrome_ui.so','-E','ND_EXPECT_PRELOAD_UNSET=1','/work/tests/mips-runtime/native-probe','/work/dist/libnavidrome_ui.so'],capture_output=True,text=True,timeout=20)
(HERE/'native-preload-run.log').write_text(preload.stdout+preload.stderr)
if preload.returncode!=0 or 'PASS startup interception reaches real main and clears LD_PRELOAD' not in preload.stdout or 'PASS original MIPS libc/loader dlopen plugin' not in preload.stdout:
    raise SystemExit('MIPS startup interception test failed; see native-preload-run.log')
report=json.loads((HERE/'results.json').read_text())
report['native_plugin_probe']={'plugin_sha256':hashlib.sha256((ROOT/'dist/libnavidrome_ui.so').read_bytes()).hexdigest(),'startup_interception_passed':True,'preload_exit_status':preload.returncode,'passed':True,'exit_status':result.returncode,'probe_scope':'RTLD_NOW loading with original libraries and missing-GUI-symbol fail-closed path; custom crt uses _exit, no normal teardown test',**json.loads((HERE/'native-build.json').read_text())}
(HERE/'results.json').write_text(json.dumps(report,indent=2)+'\n')
print(result.stdout.strip())
