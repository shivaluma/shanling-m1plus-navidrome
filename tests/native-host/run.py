#!/usr/bin/env python3
"""Compile/run actual native C plugin with mock exported toolkit and HTTP fixture."""
import http.server
import json
import os
import platform
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import urllib.parse

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent.parent

def main():
    with tempfile.TemporaryDirectory(prefix="nd-native-host-") as temp:
        work=Path(temp);state=work/"state";state.write_text("normal")
        requests=[]
        artists=[{"id":f"ar{i}","name":"宇多田 Test" if i==0 else f"Artist {i}","album_count":2} for i in range(6)]
        albums=[{"id":f"al{i}","name":"Requiem café" if i==0 else f"Album {i}","artist":"宇多田 Test","artist_id":"ar0","song_count":6} for i in range(6)]
        tracks=[{"id":f"s{i}","title":f"Track {i}","album":"Requiem café","artist":"宇多田 Test","track":i+1,"disc":2,"duration":120} for i in range(6)]
        class Fixture(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def respond(self,value,status=200):
                data=json.dumps(value,ensure_ascii=False).encode() if not isinstance(value,bytes) else value
                self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(data)));self.end_headers()
                try:self.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError):pass
            def do_GET(self):
                u=urllib.parse.urlsplit(self.path);q=urllib.parse.parse_qs(u.query);requests.append((u.path,q));mode=state.read_text()
                if mode=="delay":Path(str(state)+".inflight").touch();time.sleep(1)
                if mode=="malformed":self.respond(b"{broken");return
                if mode=="schema":self.respond({"artists":"invalid shape"});return
                if mode=="error":self.respond({"error":{"message":"fixture failure"}},503);return
                if mode=="empty":self.respond({"artists":[]});return
                if u.path=="/v1/artists":self.respond({"artists":artists})
                elif u.path=="/v1/artist":self.respond({"artist":artists[0],"albums":albums[:2]})
                elif u.path=="/v1/albums":
                    o=int(q.get("offset",["0"])[0]);n=int(q.get("size",["4"])[0]);self.respond({"albums":albums[o:o+n],"offset":o,"size":n})
                elif u.path=="/v1/album":self.respond({"album":albums[0],"tracks":tracks})
                elif u.path=="/v1/search":
                    assert q.get("q")==["宇多田 & café"],"native UTF-8 query was corrupted"
                    o=int(q.get("offset",["0"])[0]);n=int(q.get("size",["4"])[0]);self.respond({"artists":artists[o:o+n],"albums":albums[o:o+n],"tracks":tracks[o:o+n],"offset":o,"size":n})
                elif u.path=="/v1/playlists":self.respond({"playlists":[{"id":"pl","name":"Fixture Mix","song_count":3}]})
                elif u.path=="/v1/playlist":self.respond({"playlist":{"id":"pl","name":"Fixture Mix"},"tracks":tracks[:3]})
                else:self.respond({},404)
            def do_POST(self):
                data=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))));requests.append((self.path,{"id":[data["id"]]}))
                if state.read_text()=="delay_prepare":Path(str(state)+".inflight").touch();time.sleep(1)
                if state.read_text()=="badurl":self.respond({"url":"https://user:secret@foreign.invalid/track"});return
                token=("%032x" % (int(data["id"][1:])+1))
                self.respond({"url":"http://127.0.0.1:17831/stream/"+token})
        http.server.ThreadingHTTPServer.allow_reuse_address=True
        server=http.server.ThreadingHTTPServer(("127.0.0.1",17831),Fixture)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        sanitizers=os.environ.get("ND_SANITIZERS","undefined")
        common=["clang","-g","-O1","-fsanitize="+sanitizers,"-fno-omit-frame-pointer","-Wno-deprecated-declarations"]
        darwin=platform.system()=="Darwin"
        plugin=work/("native-host.dylib" if darwin else "native-host.so");harness=work/"harness"
        subprocess.run(common+(["-dynamiclib"] if darwin else ["-shared","-fPIC"])+["-DND_HOST_TEST","-fvisibility=hidden",str(ROOT/"native/nd_ui.c"),str(ROOT/"native/startup.c"),str(ROOT/"vendor/cJSON.c"),"-lcurl","-lpthread","-o",str(plugin)],check=True)
        subprocess.run(common+[str(HERE/"harness.c")]+(["-Wl,-export_dynamic"] if darwin else ["-rdynamic","-ldl"])+["-lpthread","-o",str(harness)],check=True)
        env=dict(os.environ,ASAN_OPTIONS="detect_leaks=0:abort_on_error=1",UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
        try:
            result=subprocess.run([str(harness),str(plugin),str(state),str(HERE/"host-mock.svg")],env=env,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=45)
            (HERE/"results.txt").write_text("Sanitizers: "+sanitizers+"\n"+result.stdout+result.stderr)
            (HERE/("results-"+platform.system().lower()+".txt")).write_text("Sanitizers: "+sanitizers+"\n"+result.stdout+result.stderr)
            print(result.stdout,end="")
            if result.stderr:print(result.stderr,end="")
            assert any(p=="/v1/search" and q.get("offset")==["4"] for p,q in requests)
            assert any(p=="/v1/prepare" for p,q in requests)
            (HERE/"requests.json").write_text(json.dumps(requests,ensure_ascii=False,indent=2)+"\n")
        except subprocess.CalledProcessError as err:
            print(err.stdout or "",end="");print(err.stderr or "",end="");raise
        except subprocess.TimeoutExpired as err:
            print((err.stdout or b"").decode(errors="replace"),end="");print((err.stderr or b"").decode(errors="replace"),end="");raise
        finally:server.shutdown();server.server_close()

if __name__=="__main__":main()
